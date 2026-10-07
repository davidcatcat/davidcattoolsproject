#!/usr/bin/env python3
"""Mac development handoff helper. Standard library only; never part of llmtool."""
import argparse
import fcntl
import hashlib
import io
import json
import os
import platform
from pathlib import Path
import re
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone

REPO = Path('/Users/davidsun/git/davidcataiporject/davidcattoolsproject')
IDENTITY = 'davidcatcat/davidcattoolsproject'
TASK_DIR = 'automation/ai-loop/'
MAX_MODEL_TIMEOUT = 3 * 60 * 60
DIAGNOSTICS_BRANCH = 'automation/ai-loop-diagnostics'


class Stop(RuntimeError):
    pass


def duration(seconds):
    seconds = max(0, int(seconds))
    return '%02d:%02d:%02d' % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def progress_line(label, elapsed, timeout, activity=None):
    status = {True: '日誌有更新', False: '暫無新增日誌；等待結果',
              None: '等待命令完成'}[activity]
    return label + '｜已執行 ' + duration(elapsed) + '／上限 ' + duration(timeout) + '｜' + status


def command(argv, cwd, timeout=120, stdin=None, stdout_path=None, stderr_path=None, progress=None):
    """Bound each child group, including cancellation. No shell interpolation."""
    handles = []
    try:
        for path in (stdout_path, stderr_path):
            handles.append(open(path, 'wb') if path else subprocess.PIPE)
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                stdout=handles[0], stderr=handles[1],
                                start_new_session=True)
        started = time.monotonic()
        last_log_bytes = 0
        payload = None if stdin is None else stdin.encode('utf-8')
        try:
            while True:
                try:
                    out, err = proc.communicate(payload, timeout=min(20, max(0.1, timeout - (time.monotonic() - started))))
                    return proc.returncode, (out or b'').decode('utf-8', 'replace'), (err or b'').decode('utf-8', 'replace')
                except subprocess.TimeoutExpired:
                    payload = None
                    elapsed = time.monotonic() - started
                    if elapsed >= timeout:
                        raise Stop('command timed out: ' + argv[0])
                    activity = None
                    paths = [p for p in (stdout_path, stderr_path) if p]
                    if paths:
                        try:
                            log_bytes = sum(Path(p).stat().st_size for p in paths)
                            activity = log_bytes != last_log_bytes
                            last_log_bytes = log_bytes
                        except OSError:
                            pass  # Progress metadata is not a completion/error gate.
                    label = progress or ('[執行] 等待 ' + Path(argv[0]).name + ' 完成')
                    print(progress_line(label, elapsed, timeout, activity), flush=True)
        finally:
            # Never signal a remembered PGID after communicate() reaped its leader.
            # This bounds the CLI invocation; it is not the llmtool containment harness.
            try:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=3)
                    except ProcessLookupError:
                        pass
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=3)
                    except PermissionError:
                        print('STOP: child cleanup permission failure', file=sys.stderr)
            finally:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
    finally:
        for handle in handles:
            if handle != subprocess.PIPE:
                handle.close()


def run(argv, cwd, **kwargs):
    rc, out, _ = command(argv, cwd, **kwargs)
    if rc:
        raise Stop('command failed (exit %s): %s; see local logs' % (rc, argv[0]))
    return out.strip()


def git(root, *args):
    return run(['git', *args], root)


def digest(path):
    if path.is_symlink():
        return 'symlink:' + os.readlink(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def untracked(root):
    names = git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
    return {name: digest(root / name) for name in names if name and not name.startswith('.ai-loop/')}


def changed(root):
    paths = git(root, 'diff', '--name-only', '-z', 'HEAD').split('\0')
    paths += git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
    return sorted(set(p for p in paths if p))


def check_scope(root, task, original, branch, head):
    if git(root, 'branch', '--show-current') != branch or git(root, 'rev-parse', 'HEAD') != head:
        raise Stop('agent changed branch or committed; only the runner may write Git')
    if git(root, 'diff', '--cached', '--name-only'):
        raise Stop('agent changed the index')
    now = untracked(root)
    if any(now.get(p) != h for p, h in original.items()):
        raise Stop('pre-existing untracked work changed')
    touched = [p for p in changed(root) if p not in original]
    if any(p not in task['allowed_files'] for p in touched):
        raise Stop('changes exceeded task allowed_files; files preserved')
    return touched


def schema(role):
    fields = {'status': {'type': 'string', 'enum': ['PASS', 'FAIL', 'NEEDS_DECISION']},
              'summary': {'type': 'string'}}
    if role == 'review':
        fields.update({'reviewed_head': {'type': 'string'}, 'findings': {
            'type': 'array', 'items': {'type': 'object', 'properties': {
                'severity': {'type': 'string', 'enum': ['Critical', 'High', 'Medium', 'Low']},
                'blocking': {'type': 'boolean'}, 'file': {'type': 'string'},
                'detail': {'type': 'string'}, 'evidence': {'type': 'string'}},
                'required': ['severity', 'blocking', 'file', 'detail', 'evidence'],
                'additionalProperties': False}}})
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def validate_result(value, role, head=None):
    fields = schema(role)['properties']
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Stop('missing/invalid structured ' + role + ' result')
    if value['status'] not in ('PASS', 'FAIL', 'NEEDS_DECISION') or not isinstance(value['summary'], str):
        raise Stop('invalid result status/summary')
    if role == 'review':
        if value['reviewed_head'] != head or not isinstance(value['findings'], list):
            raise Stop('review revision mismatch or invalid findings')
        for finding in value['findings']:
            keys = {'severity', 'blocking', 'file', 'detail', 'evidence'}
            if not isinstance(finding, dict) or set(finding) != keys:
                raise Stop('invalid review finding')
            if type(finding['blocking']) is not bool or finding['severity'] not in ('Critical', 'High', 'Medium', 'Low'):
                raise Stop('invalid finding severity/blocking')
            if any(not isinstance(finding[k], str) or not finding[k].strip() for k in ('file', 'detail', 'evidence')):
                raise Stop('finding lacks file or evidence')
        blocked = any(f['blocking'] or f['severity'] in ('Critical', 'High') for f in value['findings'])
        if value['status'] == 'PASS' and blocked:
            raise Stop('review claimed PASS with blocking findings')
        if value['status'] == 'FAIL' and not blocked:
            raise Stop('FAIL without actionable blocking findings needs a decision')
    return value


def parse_claude(raw, head):
    if not isinstance(raw, dict) or raw.get('is_error') is not False or raw.get('subtype') != 'success':
        raise Stop('Claude run did not complete successfully (auth/quota/turn limit/format)')
    return validate_result(raw.get('structured_output'), 'review', head)


def load_task(root, control_sha):
    metadata = json.loads(git(root, 'show', control_sha + ':' + TASK_DIR + 'task.json'))
    text = git(root, 'show', control_sha + ':' + TASK_DIR + 'task.md')
    required = {'task_id', 'mode', 'pr_number', 'expected_head', 'base_sha', 'branch',
                'allowed_files', 'related_tests', 'full_tests', 'default_test_mode',
                'max_repairs', 'timeout_seconds', 'ci_workflow'}
    optional = {'required_files', 'mandatory_tests', 'mac_evidence', 'require_local_full',
                'pr_title', 'acceptance_report', 'publish_decision_checkpoint', 'initial_quota_recovery',
                'initial_timeout_recovery', 'codex_timeout_seconds', 'claude_timeout_seconds',
                'acceptance_failure_recovery'}
    if not required.issubset(metadata) or set(metadata) - required - optional or not re.fullmatch(r'[A-Za-z0-9._-]+', metadata['task_id']):
        raise Stop('invalid task contract')
    if not re.fullmatch(r'[0-9a-f]{40}', metadata['base_sha']) or not metadata['branch'].startswith('feat/'):
        raise Stop('invalid base/branch')
    if metadata['mode'] == 'review_existing_pr':
        if type(metadata['pr_number']) is not int or metadata['pr_number'] <= 0:
            raise Stop('review task must target an existing PR')
    elif metadata['mode'] == 'implement_new_pr':
        if metadata['pr_number'] is not None or metadata['expected_head'] != metadata['base_sha']:
            raise Stop('new implementation must start at its pinned base without an existing PR')
        if not isinstance(metadata.get('pr_title'), str) or not metadata['pr_title'].strip():
            raise Stop('new implementation needs a PR title')
    else:
        raise Stop('unsupported task mode')
    if not re.fullmatch(r'[0-9a-f]{40}', metadata['expected_head']):
        raise Stop('invalid expected PR HEAD')
    git(root, 'check-ref-format', '--branch', metadata['branch'])
    if not isinstance(metadata['allowed_files'], list) or not metadata['allowed_files']:
        raise Stop('task needs explicit allowed_files')
    for path in metadata['allowed_files']:
        if not isinstance(path, str) or path.startswith(('/', '.git/')) or '..' in Path(path).parts:
            raise Stop('invalid allowed file')
    if type(metadata['max_repairs']) is not int or not 0 <= metadata['max_repairs'] <= 2:
        raise Stop('invalid repair bound')
    if type(metadata['timeout_seconds']) is not int or not 60 <= metadata['timeout_seconds'] <= 3600:
        raise Stop('invalid timeout')
    for key in ('codex_timeout_seconds', 'claude_timeout_seconds'):
        if key in metadata and (type(metadata[key]) is not int or not 60 <= metadata[key] <= MAX_MODEL_TIMEOUT):
            raise Stop('invalid model timeout: ' + key)
    if metadata['default_test_mode'] not in ('related', 'full'):
        raise Stop('invalid default test mode')
    for suite in ('related_tests', 'full_tests', 'mandatory_tests'):
        if suite == 'mandatory_tests' and suite not in metadata:
            continue
        if not isinstance(metadata[suite], list) or not metadata[suite]:
            raise Stop('task needs explicit related and full test commands')
        for argv in metadata[suite]:
            if not isinstance(argv, list) or not argv or any(not isinstance(a, str) for a in argv):
                raise Stop('invalid test argv')
    if not isinstance(metadata['ci_workflow'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.yml', metadata['ci_workflow']):
        raise Stop('invalid CI workflow')
    if 'require_local_full' in metadata and type(metadata['require_local_full']) is not bool:
        raise Stop('invalid local full requirement')
    if 'required_files' in metadata and (not isinstance(metadata['required_files'], list)
            or not metadata['required_files'] or any(p not in metadata['allowed_files'] for p in metadata['required_files'])):
        raise Stop('required files must be explicit allowed files')
    if 'acceptance_report' in metadata and (metadata['acceptance_report'] not in metadata['allowed_files']
            or not metadata['acceptance_report'].startswith('reports/') or not metadata['acceptance_report'].endswith('.md')):
        raise Stop('invalid acceptance report path')
    if 'publish_decision_checkpoint' in metadata:
        if type(metadata['publish_decision_checkpoint']) is not bool:
            raise Stop('invalid decision checkpoint publication setting')
        if metadata['publish_decision_checkpoint'] and (metadata['mode'] != 'implement_new_pr'
                or not metadata.get('acceptance_report')):
            raise Stop('decision checkpoint publication requires a new task and explicit report')
    if 'mac_evidence' in metadata:
        if metadata['mac_evidence'] != 'mac-containment' or not metadata.get('mandatory_tests'):
            raise Stop('invalid Mac evidence gate')
        if not metadata.get('require_local_full') or not metadata.get('acceptance_report'):
            raise Stop('Mac containment task must retain full local checks and report')
    if sum(k in metadata for k in ('initial_quota_recovery', 'initial_timeout_recovery',
                                   'acceptance_failure_recovery')) > 1:
        raise Stop('only one explicit recovery can be active')
    for recovery_key in ('initial_quota_recovery', 'initial_timeout_recovery'):
        if recovery_key not in metadata:
            continue
        recovery = metadata[recovery_key]
        if (metadata['mode'] != 'implement_new_pr' or not isinstance(recovery, dict)
                or set(recovery) != {'run_id', 'control_sha'}
                or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', recovery.get('run_id', ''))
                or not re.fullmatch(r'[0-9a-f]{40}', recovery.get('control_sha', ''))):
            raise Stop('invalid initial recovery contract')
    if 'acceptance_failure_recovery' in metadata:
        recovery = metadata['acceptance_failure_recovery']
        keys = {'run_id', 'control_sha', 'expected_head', 'summary_sha256', 'evidence_sha256'}
        if (metadata['mode'] != 'implement_new_pr' or not metadata.get('mac_evidence')
                or metadata['max_repairs'] < 1 or not isinstance(recovery, dict)
                or set(recovery) != keys
                or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', recovery.get('run_id', ''))
                or any(not isinstance(recovery.get(k), str)
                       or not re.fullmatch(r'[0-9a-f]{40}', recovery[k])
                       for k in ('control_sha', 'expected_head'))
                or any(not isinstance(recovery.get(k), str)
                       or not re.fullmatch(r'[0-9a-f]{64}', recovery[k])
                       for k in ('summary_sha256', 'evidence_sha256'))):
            raise Stop('invalid acceptance failure recovery contract')
    return metadata, text


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def publish_decision_checkpoint(root, logs, task, result, original, head, control_sha, round_no):
    """Publish only an initial report-only decision; never local source/index/logs."""
    if not task.get('publish_decision_checkpoint') or task['mode'] != 'implement_new_pr' or round_no != 0:
        return None
    if result['status'] != 'NEEDS_DECISION':
        return None
    report_rel = task['acceptance_report']
    if head != task['base_sha'] or check_scope(root, task, original, task['branch'], head) != [report_rel]:
        raise Stop('decision publication requires unchanged base and report-only work')
    report = root / report_rel
    if (not report.is_file() or any(p.is_symlink() for p in [report, *report.parents])
            or report.stat().st_size > 128 * 1024):
        raise Stop('decision report is missing, oversized or a symlink')
    raw = report.read_bytes()
    if len(raw) > 128 * 1024:
        raise Stop('decision report grew beyond its publication bound')
    body = raw.decode('utf-8')
    if '\0' in body or 'NEEDS_DECISION' not in body or len(result['summary'].encode('utf-8')) > 8192:
        raise Stop('invalid bounded decision report')
    run_id = logs.name
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', run_id):
        raise Stop('invalid checkpoint run identity')
    path = 'reports/ai-loop/checkpoints/' + task['task_id'] + '-' + run_id + '.md'
    content = ('# AI-loop decision checkpoint: ' + task['task_id'] + '\n\n'
        + 'Status: **NEEDS_DECISION / STOPPED**. This is an unverified feasibility report, '
        + 'not an implemented candidate, review PASS or merge-ready handoff.\n\n'
        + '- Run: `' + run_id + '`\n- Pinned control: `' + control_sha + '`\n'
        + '- Canonical Mac branch: `' + task['branch'] + '`\n- Unchanged starting HEAD: `' + head + '`\n'
        + '- Source report: `' + report_rel + '`\n- Source report SHA-256: `' + hashlib.sha256(raw).hexdigest() + '`\n'
        + '- Runner tests, containment probes and independent review: NOT RUN.\n'
        + '- Implementation and feature PR: not published by this checkpoint.\n'
        + '- No decision approval or automatic resume. Preserve local work for coordinator recovery.\n\n'
        + '## Codex decision summary (not independently verified)\n\n' + result['summary']
        + '\n\n## Codex-authored report (not independently verified)\n\n' + body)
    api = 'repos/' + IDENTITY + '/git/'
    ref = json.loads(run(['gh', 'api', api + 'ref/heads/automation/ai-loop'], root))
    if ref['object']['sha'] != control_sha:
        raise Stop('control branch changed; decision remains local for coordinator recovery')
    tree = json.loads(run(['gh', 'api', api + 'trees/' + control_sha + '?recursive=1'], root))
    if tree.get('truncated') is not False or any(e.get('path') == path for e in tree['tree']):
        raise Stop('cannot prove checkpoint path is new')

    def write_api(method, endpoint, payload, name):
        request = logs / ('checkpoint-' + name + '-request.json')
        save_json(request, payload)
        return json.loads(run(['gh', 'api', '--method', method, endpoint, '--input', str(request)], root))

    tree_reply = write_api('POST', api + 'trees', {'base_tree': control_sha, 'tree': [
        {'path': path, 'mode': '100644', 'type': 'blob', 'content': content}]}, 'tree')
    tree_sha = tree_reply['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', tree_sha):
        raise Stop('invalid checkpoint tree identity')
    commit = write_api('POST', api + 'commits', {'message': 'docs: record stopped ' + task['task_id'],
        'parents': [control_sha], 'tree': tree_sha}, 'commit')['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise Stop('invalid checkpoint commit identity')
    # Check again before publication. force=False rejects concurrent divergent updates.
    latest = json.loads(run(['gh', 'api', api + 'ref/heads/automation/ai-loop'], root))
    if latest['object']['sha'] != control_sha:
        raise Stop('control branch changed before checkpoint publication')
    if report.read_bytes() != raw:
        raise Stop('decision report changed during publication')
    if check_scope(root, task, original, task['branch'], head) != [report_rel]:
        raise Stop('decision work changed during publication')
    ref_reply = write_api('PATCH', api + 'refs/heads/automation/ai-loop', {'sha': commit, 'force': False}, 'ref')
    if ref_reply.get('object', {}).get('sha') != commit:
        raise Stop('checkpoint branch update is unconfirmed')
    published = {'status': 'NEEDS_DECISION', 'control_sha': commit, 'path': path,
                 'url': 'https://github.com/' + IDENTITY + '/blob/' + commit + '/' + path,
                 'source_report_sha256': hashlib.sha256(raw).hexdigest()}
    save_json(logs / 'published-checkpoint.json', published)
    remote = json.loads(run(['gh', 'api', 'repos/' + IDENTITY + '/contents/' + path + '?ref=' + commit], root))
    encoded = content.encode('utf-8')
    expected_blob = hashlib.sha1(b'blob ' + str(len(encoded)).encode('ascii') + b'\0' + encoded).hexdigest()
    if remote.get('type') != 'file' or remote.get('sha') != expected_blob:
        raise Stop('checkpoint remote content verification failed; inspect published-checkpoint.json')
    print('[Checkpoint] NEEDS_DECISION published: ' + published['url'], flush=True)
    return published


def codex(root, logs, prompt, round_no, timeout):
    output = logs / ('codex-%d.json' % round_no)
    spec = logs / 'codex-schema.json'
    save_json(spec, schema('developer'))
    label = '[Codex／實作修正] 依 task.md 產生或補完候選程式' if round_no == 0 else (
        '[Codex／修正第%d輪] 處理測試失敗或審查阻擋項目' % round_no)
    print(label + '；單次上限 ' + duration(timeout), flush=True)
    run(['codex', '-a', 'never', 'exec', '--sandbox', 'workspace-write',
         '--output-schema', str(spec), '--output-last-message', str(output), '-'],
        root, timeout=timeout, stdin=prompt, progress=label,
        stdout_path=logs / ('codex-%d.stdout.log' % round_no),
        stderr_path=logs / ('codex-%d.stderr.log' % round_no))
    return validate_result(json.loads(output.read_text()), 'developer')


def claude(root, logs, prompt, round_no, timeout, head):
    output = logs / ('claude-%d.raw.json' % round_no)
    label = ('[Claude／最終審查]' if round_no >= 100 else '[Claude／獨立審查]') + (
        ' 檢查需求、完整程式差異與測試證據（唯讀）')
    print(label + '；單次上限 ' + duration(timeout), flush=True)
    run(['claude', '-p', '--output-format', 'json', '--json-schema', json.dumps(schema('review')),
         '--max-turns', '30', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read', 'Glob', 'Grep',
         '--permission-mode', 'dontAsk', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
         '--settings', '{"disableAllHooks":true}', '--no-session-persistence'],
        root, timeout=timeout, stdin=prompt, progress=label, stdout_path=output,
        stderr_path=logs / ('claude-%d.stderr.log' % round_no))
    result = parse_claude(json.loads(output.read_text()), head)
    save_json(logs / ('claude-%d.json' % round_no), result)
    return result


def test_commands(task, suite):
    if suite == 'mandatory-final':
        return task['mandatory_tests']
    return task['full_tests' if suite in ('full', 'full-final') else 'related_tests']


def test_result_file(logs, round_no, suite):
    return logs / ('tests-%d-%s.json' % (round_no, suite))


def test_purpose(argv, suite):
    purposes = {'test_git_containment.py': 'supervisor／fake-child containment 測試',
                'test_git_mutation.py': 'Git 前置檢查與錯誤清理回歸測試',
                'test_git_intel.py': '唯讀 Git 資訊與程序清理測試',
                'test_action_contract.py': 'Action／Approval 資料契約測試'}
    for name, purpose in purposes.items():
        if name in argv:
            return purpose
    if suite == 'mandatory-final':
        return 'Mac containment 驗收（訊號、identity、parent SIGKILL probes）'
    if 'syntax' in argv:
        return 'Python 語法檢查'
    if 'check' in argv:
        return 'Python 全套檢查（' + ('Mac 系統 Python' if 'PYTHON=/usr/bin/python3' in argv else 'venv Python') + '）'
    return '執行任務指定的 ' + suite + ' 測試'


def tests(root, logs, task, round_no, suite='related'):
    results = []
    commands = test_commands(task, suite)
    for i, argv in enumerate(commands):
        argv = [a.replace('{mac_evidence}', str(mac_evidence_path(logs, round_no))) for a in argv]
        label = '[Tests/%s] %d/%d' % (suite, i + 1, len(commands))
        print(label + ' ' + ' '.join(argv), flush=True)
        purpose = '[測試 %d/%d] ' % (i + 1, len(commands)) + test_purpose(argv, suite)
        print(purpose, flush=True)
        started = time.monotonic()
        rc, _, _ = command(argv, root, timeout=task['timeout_seconds'], progress=purpose,
                            stdout_path=logs / ('tests-%d-%s-%d.stdout.log' % (round_no, suite, i)),
                            stderr_path=logs / ('tests-%d-%s-%d.stderr.log' % (round_no, suite, i)))
        elapsed = round(time.monotonic() - started, 1)
        results.append({'suite': suite, 'argv': argv, 'exit_code': rc, 'seconds': elapsed})
        print(label + (' PASS' if rc == 0 else ' FAIL') + ' (%ss)' % elapsed, flush=True)
        if rc:
            break
    save_json(test_result_file(logs, round_no, suite), results)
    return all(r['exit_code'] == 0 for r in results)


def runtime_bytes(values):
    """Report-only evidence changes do not invalidate frozen source/test bytes."""
    return {p: h for p, h in values.items() if not p.startswith('reports/')}


def mac_evidence_path(logs, round_no):
    return logs / ('mac-containment-%d.json' % round_no)


MAC_SCENARIOS = ('fd_inheritance', 'sigint', 'sigterm', 'sighup', 'hung_supervisor',
    'leader_exit_descendant', 'identity_reuse', 'macos_start_time', 'kqueue_non_reaping',
    'protocol_framing', 'code_runtime_binding', 'public_import_boundary',
    'parent_sigkill_plain', 'parent_sigkill_popen')


def validate_mac_evidence(path, tested, python_version):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 128 * 1024:
        raise Stop('missing/unsafe/oversized Mac containment evidence')
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise Stop('duplicate key in Mac evidence')
            value[key] = item
        return value
    def invalid_constant(value):
        raise Stop('non-finite number in Mac evidence')
    value = json.loads(path.read_text(), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    keys = {'schema_version', 'platform', 'os_version', 'python_version', 'tested_sha256', 'scenarios'}
    if not isinstance(value, dict) or set(value) != keys or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise Stop('invalid Mac containment evidence schema')
    if (sys.platform != 'darwin' or value['platform'] != 'darwin'
            or value['os_version'] != platform.mac_ver()[0]
            or not value['os_version'] or value['python_version'] != python_version
            or value['tested_sha256'] != runtime_bytes(tested)):
        raise Stop('Mac evidence environment/revision mismatch; no containment PASS')
    scenarios = value['scenarios']
    if not isinstance(scenarios, dict) or set(scenarios) != set(MAC_SCENARIOS):
        raise Stop('missing required Mac containment scenarios')
    for name, row in scenarios.items():
        if (not isinstance(row, dict) or set(row) != {'status', 'checks', 'skipped', 'repetitions'}
                or row['status'] != 'PASS' or type(row['checks']) is not int or row['checks'] < 1
                or type(row['skipped']) is not int or row['skipped'] != 0
                or not isinstance(row['repetitions'], list)):
            raise Stop('Mac scenario failed/skipped/incomplete: ' + name)
        if name.startswith('parent_sigkill_'):
            if not 20 <= len(row['repetitions']) <= 100 or row['checks'] < len(row['repetitions']):
                raise Stop('parent SIGKILL needs >=20 repetitions per variant')
            for repetition in row['repetitions']:
                fields = {'leader_pid', 'pgid', 'descendant_pids', 'latency_seconds',
                    'identity_verified', 'leader_absent', 'descendants_absent', 'group_absent', 'containment_confirmed'}
                if (not isinstance(repetition, dict) or set(repetition) != fields
                        or any(type(repetition[k]) is not int or repetition[k] <= 0 for k in ('leader_pid', 'pgid'))
                        or not isinstance(repetition['descendant_pids'], list) or not repetition['descendant_pids']
                        or any(type(p) is not int or p <= 0 for p in repetition['descendant_pids'])
                        or type(repetition['latency_seconds']) not in (int, float)
                        or not 0 <= repetition['latency_seconds'] <= 2
                        or any(repetition[k] is not True for k in fields - {'leader_pid', 'pgid', 'descendant_pids', 'latency_seconds'})):
                    raise Stop('parent SIGKILL latency/identity/absence proof failed')
        elif row['repetitions']:
            raise Stop('unexpected repetition fields in scenario: ' + name)
    return value


def current_is_integrated(root, base):
    """Accept normal ancestry or an exactly published, merged squash-equivalent tree."""
    current = git(root, 'rev-parse', 'HEAD')
    rc, _, _ = command(['git', 'merge-base', '--is-ancestor', current, base], root)
    if rc == 0:
        return True
    if rc != 1:
        raise Stop('could not establish current branch ancestry')
    # Squash merges replace commit IDs. Equal trees alone must not approve local
    # unpublished/empty commits: require the exact merged PR head and merge ancestry.
    if git(root, 'rev-parse', current + '^{tree}') != git(root, 'rev-parse', base + '^{tree}'):
        return False
    branch = git(root, 'branch', '--show-current')
    if not branch:
        return False
    pulls = json.loads(run(['gh', 'api', 'repos/' + IDENTITY
        + '/pulls?state=closed&head=davidcatcat:' + branch], root))
    for pr in pulls:
        merge = pr.get('merge_commit_sha')
        if (pr.get('state') != 'closed' or not pr.get('merged_at')
                or not isinstance(merge, str) or not re.fullmatch(r'[0-9a-f]{40}', merge)
                or pr.get('head', {}).get('sha') != current
                or pr.get('head', {}).get('ref') != branch
                or (pr.get('head', {}).get('repo') or {}).get('full_name') != IDENTITY
                or pr.get('base', {}).get('ref') != 'main'
                or (pr.get('base', {}).get('repo') or {}).get('full_name') != IDENTITY):
            continue
        rc, _, _ = command(['git', 'merge-base', '--is-ancestor', merge, base], root)
        if rc == 0:
            print('[Preflight] Current HEAD matches a merged PR and the pinned main tree (squash merge).', flush=True)
            return True
        if rc != 1:
            raise Stop('could not verify the merged PR commit in pinned main')
    return False


def prepare_new_branch(root, task, check_only=False):
    """One fresh branch from pinned main; collisions/unpublished work stop."""
    if json.loads(run(['gh', 'api', 'repos/' + IDENTITY + '/pulls?state=all&head=davidcatcat:' + task['branch']], root)):
        raise Stop('this task branch already has a PR; refresh/recover instead of duplicating it')
    if run(['git', 'ls-remote', '--heads', 'origin', 'refs/heads/' + task['branch']], root):
        raise Stop('new task remote branch already exists; files preserved')
    rc, _, _ = command(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/' + task['branch']], root)
    if rc == 0:
        raise Stop('new task local branch already exists; files preserved for recovery')
    if rc != 1:
        raise Stop('could not establish local branch absence')
    if not current_is_integrated(root, task['base_sha']):
        raise Stop('current branch has unrelated/unpublished commits; files preserved')
    if not check_only:
        git(root, 'switch', '--no-overwrite-ignore', '-c', task['branch'], 'origin/main')
        if git(root, 'rev-parse', 'HEAD') != task['base_sha']:
            raise Stop('new branch did not reach pinned main')


def ensure_no_active_runner_codex(root):
    """Identify this repo's ai-loop exec calls, not every Codex app/terminal."""
    rc, out, _ = command(['/usr/bin/pgrep', '-f', r'(^|/)codex([ .-]|$)'], root)
    if rc == 1:
        return
    pids = out.split()
    if rc != 0 or not pids or len(pids) > 128 or any(not re.fullmatch(r'[1-9][0-9]*', p) for p in pids):
        raise Stop('could not enumerate Codex processes for recovery')
    rc, rows, err = command(['/bin/ps', '-ww', '-p', ','.join(pids), '-o', 'pid=,args='], root)
    if rc == 1 and not rows.strip() and not err.strip():
        return  # All enumerated processes exited before ps inspected them.
    if rc != 0 or len(rows.encode('utf-8')) > 128 * 1024:
        raise Stop('could not inspect full Codex process arguments')
    prefix = str(root / '.ai-loop') + '/'
    for row in rows.splitlines():
        fields = row.strip().split(None, 1)
        if len(fields) != 2 or fields[0] not in pids:
            raise Stop('invalid Codex process inspection result')
        try:
            argv = shlex.split(fields[1])
        except ValueError:
            raise Stop('ambiguous Codex process arguments; no recovery started')
        # Our runner passes explicit absolute schema/output paths through exec.
        # App servers, code-mode hosts and plain interactive terminals lack this
        # binding and must not be inferred to be the timed-out runner's writer.
        if 'exec' not in argv or not any(a.startswith(prefix) or
                ('=' in a and a.split('=', 1)[1].startswith(prefix)) for a in argv):
            continue
        def option(name):
            values = [a.split('=', 1)[1] for a in argv if a.startswith(name + '=')]
            for i, a in enumerate(argv):
                if a == name and i + 1 < len(argv):
                    values.append(argv[i + 1])
            return values[0] if len(values) == 1 else None
        schema_path, output_path = option('--output-schema'), option('--output-last-message')
        if not schema_path or not output_path:
            raise Stop('possible active ai-loop Codex process has ambiguous output binding; PID ' + fields[0])
        relative = schema_path[len(prefix):] if schema_path.startswith(prefix) else ''
        if (not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+/codex-schema\.json', relative)
                or Path(output_path).parent != Path(schema_path).parent
                or not re.fullmatch(r'codex-[0-9]+\.json', Path(output_path).name)):
            raise Stop('possible active ai-loop Codex process has invalid output binding; PID ' + fields[0])
        raise Stop('ai-loop Codex exec is still active for this repository; PID ' + fields[0])


def prepare_initial_resume(root, task, run_id, current_untracked, tracked_paths=()):
    """Explicit recovery of one pinned, unpublished initial Codex interruption."""
    timeout_recovery = 'initial_timeout_recovery' in task
    policy = task.get('initial_timeout_recovery') or task.get('initial_quota_recovery')
    if not policy or run_id != policy['run_id']:
        raise Stop('resume run is not authorized by the current task contract')
    if any(p not in task['allowed_files'] for p in tracked_paths):
        raise Stop('unrelated tracked changes prevent recovery')
    if (git(root, 'branch', '--show-current') != task['branch']
            or git(root, 'rev-parse', 'HEAD') != task['base_sha']):
        raise Stop('resume requires the existing task branch at its pinned base')
    if json.loads(run(['gh', 'api', 'repos/' + IDENTITY + '/pulls?state=all&head=davidcatcat:' + task['branch']], root)):
        raise Stop('resume branch already has a PR; publication recovery is separate')
    if run(['git', 'ls-remote', '--heads', 'origin', 'refs/heads/' + task['branch']], root):
        raise Stop('resume branch is already published; files preserved')
    old_logs = root / '.ai-loop' / run_id
    def read_record(name):
        path = old_logs / name
        if any(p.is_symlink() for p in (root / '.ai-loop', old_logs, path)) or not path.is_file():
            raise Stop('resume evidence is missing or a symlink: ' + name)
        if path.stat().st_size > 1024 * 1024:
            raise Stop('resume JSON/text record exceeds its bound')
        return path.read_text(encoding='utf-8')
    old_task, old_text = load_task(root, policy['control_sha'])
    recovery_keys = {'initial_quota_recovery', 'initial_timeout_recovery', 'codex_timeout_seconds', 'claude_timeout_seconds'}
    current_contract = {k: v for k, v in task.items() if k not in recovery_keys}
    old_contract = {k: v for k, v in old_task.items() if k not in recovery_keys}
    if (old_contract != current_contract or json.loads(read_record('task.json')) != old_task
            or read_record('task.md').strip() != old_text.strip()):
        raise Stop('resume contract differs from the pinned interrupted task')
    if timeout_recovery and not (old_task.get('codex_timeout_seconds', old_task['timeout_seconds'])
            < task.get('codex_timeout_seconds', task['timeout_seconds']) <= MAX_MODEL_TIMEOUT):
        raise Stop('timeout recovery requires an explicitly increased bounded Codex timeout')
    provenance = json.loads(read_record('provenance.json'))
    summary = json.loads(read_record('summary.json'))
    if (provenance.get('root') != str(root) or provenance.get('run_id') != run_id
            or provenance.get('control_sha') != policy['control_sha']
            or provenance.get('base') != task['base_sha']
            or summary.get('branch') != task['branch'] or summary.get('head') != task['base_sha']
            or summary.get('status') != 'STOPPED' or summary.get('history') != []
            or summary.get('decision_checkpoint') is not None
            or summary.get('reason') != ('command timed out: codex' if timeout_recovery else
                'command failed (exit 1): codex; see local logs')):
        raise Stop('resume is limited to the contracted initial Codex interruption before tests/commits')
    stderr = old_logs / 'codex-0.stderr.log'
    if stderr.is_symlink() or not stderr.is_file():
        raise Stop('resume Codex log is missing/unsafe')
    with stderr.open('rb') as stream:
        stream.seek(max(0, stderr.stat().st_size - 16384))
        tail = stream.read(16384).decode('utf-8', 'replace')
    if not timeout_recovery and 'ERROR: You’ve hit your usage limit.' not in tail:
        raise Stop('resume needs the recorded Codex usage-limit error')
    if timeout_recovery:
        previous_recovery = json.loads(read_record('recovery.json'))
        protected = previous_recovery.get('preserved_untracked')
        if not isinstance(protected, dict) or not protected:
            raise Stop('timeout recovery requires the previous unrelated-file inventory')
        if any(current_untracked.get(p) != h for p, h in protected.items()):
            raise Stop('unrelated files changed since the previous recovery; files preserved')
        if set(current_untracked) - set(protected) - set(task['allowed_files']):
            raise Stop('new out-of-scope untracked files appeared during the interrupted run')
        ensure_no_active_runner_codex(root)
    adopted = {p: h for p, h in current_untracked.items() if p in task['allowed_files']}
    for name in tracked_paths:
        path = root / name
        if not path.is_file():
            raise Stop('deleted/non-file tracked task path prevents recovery')
        adopted[name] = digest(path)
    if not adopted:
        raise Stop('no partial task files to resume')
    total = 0
    for name in adopted:
        path = root / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise Stop('partial task path is missing/unsafe: ' + name)
        total += path.stat().st_size
        if path.stat().st_size > 2 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise Stop('partial task files exceed recovery snapshot bounds')
    # Older runner did not save an initial unrelated-file inventory. Bind and
    # preserve the current unrelated bytes, without claiming historical integrity.
    original = {p: h for p, h in current_untracked.items() if p not in adopted}
    return original, {'from_run': run_id, 'old_control_sha': policy['control_sha'],
        'partial_files': adopted, 'preserved_untracked': original,
        'prior_unrelated_integrity': 'UNVERIFIED', 'inherited_validation': 'NONE'}


def ensure_failed_probe_pids_absent(root, evidence):
    """Conservative read-only gate. A snapshot never authorizes a PID signal."""
    owned = evidence.get('failure', {}).get('owned_processes')
    if not isinstance(owned, list) or not owned or len(owned) > 128:
        raise Stop('failed Mac evidence lacks a bounded ownership inventory')
    pids = set()
    for row in owned:
        if (not isinstance(row, dict) or type(row.get('pid')) is not int
                or not 1 < row['pid'] < 2**31):
            raise Stop('invalid failed probe PID inventory')
        pids.add(row['pid'])
        for key in ('host_identity', 'supervisor'):
            item = row.get(key)
            if (not isinstance(item, dict) or type(item.get('pid')) is not int
                    or not 1 < item['pid'] < 2**31):
                raise Stop('invalid failed probe identity inventory')
            pids.add(item['pid'])
    rc, out, err = command(['/bin/ps', '-ww', '-p', ','.join(map(str, sorted(pids))),
                            '-o', 'pid='], root)
    if rc == 1 and not out.strip() and not err.strip():
        return  # Recovery prerequisite only, never native containment PASS.
    if rc == 0:
        observed = out.split()
        if observed and all(re.fullmatch(r'[1-9][0-9]*', p)
                            and int(p) in pids for p in observed) and not err.strip():
            raise Stop('舊 Mac probe PID 仍存在：' + ', '.join(observed)
                       + '。不會用舊 PID 清除；請先核對程序狀態，由協調者確認如何處理測試 parent。')
    raise Stop('cannot prove old probe PIDs absent; no model or new probe started')


def prepare_acceptance_resume(root, task, run_id, current_untracked, tracked_paths=()):
    """One explicitly assessed native failure at an exact unpublished commit."""
    policy = task.get('acceptance_failure_recovery')
    if not policy or policy['run_id'] != run_id:
        raise Stop('native failure recovery is not authorized for this run')
    if tracked_paths or git(root, 'diff', '--cached', '--name-only'):
        raise Stop('native failure recovery requires unchanged tracked/index state')
    if (git(root, 'branch', '--show-current') != task['branch']
            or git(root, 'rev-parse', 'HEAD') != policy['expected_head']):
        raise Stop('native recovery requires the exact stopped branch/commit')
    if json.loads(run(['gh', 'api', 'repos/' + IDENTITY
            + '/pulls?state=all&head=davidcatcat:' + task['branch']], root)):
        raise Stop('native recovery branch already has a PR')
    if run(['git', 'ls-remote', '--heads', 'origin', 'refs/heads/' + task['branch']], root):
        raise Stop('native recovery branch is already published')
    if set(current_untracked).intersection(task['allowed_files']):
        raise Stop('untracked candidate collision in committed native recovery')
    cumulative = git(root, 'diff', '--name-only', '-z', task['base_sha'], policy['expected_head'])
    if any(p and p not in task['allowed_files'] for p in cumulative.split('\0')):
        raise Stop('native recovery cumulative diff exceeds task scope')
    rc, _, _ = command(['git', 'merge-base', '--is-ancestor', task['base_sha'],
                         policy['expected_head']], root)
    if rc != 0:
        raise Stop('native recovery candidate is not descended from pinned main')
    old_logs = root / '.ai-loop' / run_id
    def record(name, expected=None):
        path = old_logs / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise Stop('native recovery evidence missing/unsafe: ' + name)
        if path.stat().st_size > 1024 * 1024:
            raise Stop('native recovery evidence oversized: ' + name)
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024 or (expected and hashlib.sha256(raw).hexdigest() != expected):
            raise Stop('native recovery evidence changed/oversized: ' + name)
        return raw, json.loads(raw)
    old_task, old_text = load_task(root, policy['control_sha'])
    variable = {'initial_quota_recovery', 'initial_timeout_recovery', 'acceptance_failure_recovery',
                'codex_timeout_seconds', 'claude_timeout_seconds'}
    if {k: v for k, v in task.items() if k not in variable} != {
            k: v for k, v in old_task.items() if k not in variable}:
        raise Stop('native recovery altered scope, tests or acceptance gates')
    if record('task.json')[1] != old_task:
        raise Stop('stopped task JSON differs from its Git-pinned contract')
    old_md = old_logs / 'task.md'
    if (any(p.is_symlink() for p in (old_md, *old_md.parents)) or not old_md.is_file()
            or old_md.stat().st_size > 1024 * 1024
            or old_md.read_text(encoding='utf-8').strip() != old_text.strip()):
        raise Stop('stopped task text differs from its Git-pinned contract')
    provenance = record('provenance.json')[1]
    summary_raw, summary = record('summary.json', policy['summary_sha256'])
    mac_raw, mac = record('mac-containment-0.json', policy['evidence_sha256'])
    history = summary.get('history')
    if (provenance.get('root') != str(root) or provenance.get('run_id') != run_id
            or provenance.get('base') != task['base_sha']
            or provenance.get('control_sha') != policy['control_sha']
            or summary.get('branch') != task['branch']
            or summary.get('head') != policy['expected_head']
            or summary.get('status') != 'STOPPED'
            or summary.get('reason') != 'required Mac acceptance failed; probes/design need coordinator assessment, no automatic retry'
            or not isinstance(history, list) or len(history) != 1
            or history[0].get('head') != policy['expected_head']
            or history[0].get('mandatory', {}).get('status') != 'FAIL'
            or mac.get('platform') != 'darwin'
            or mac.get('failure', {}).get('scenario') != 'sigint'
            or mac.get('failure', {}).get('status') != 'FAIL'):
        raise Stop('stopped native failure does not match coordinator assessment')
    tested = history[0].get('tested_sha256')
    if (not isinstance(tested, dict) or set(tested) != set(task['allowed_files'])
            or mac.get('tested_sha256') != runtime_bytes(tested)):
        raise Stop('failed native source hashes do not bind the complete candidate')
    total = 0
    for name, expected in tested.items():
        path = root / name
        if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
                or path.stat().st_size > 2 * 1024 * 1024 or digest(path) != expected):
            raise Stop('native recovery candidate changed/unsafe: ' + name)
        total += path.stat().st_size
    if total > 16 * 1024 * 1024:
        raise Stop('native recovery candidate snapshot exceeds bound')
    # Unrelated files may have been added by David since the stopped run. Bind
    # current bytes, without claiming historical preservation we cannot prove.
    ensure_no_active_runner_codex(root)
    ensure_failed_probe_pids_absent(root, mac)
    return current_untracked, {'from_run': run_id, 'kind': 'acceptance_failure',
        'old_control_sha': policy['control_sha'], 'starting_head': policy['expected_head'],
        'partial_files': tested, 'preserved_untracked': current_untracked,
        'prior_unrelated_integrity': 'UNVERIFIED', 'inherited_validation': 'NONE',
        'evidence_files': {'summary.json': hashlib.sha256(summary_raw).hexdigest(),
                           'mac-containment-0.json': hashlib.sha256(mac_raw).hexdigest()}}


def snapshot_recovery(root, logs, recovery):
    """Retain the partial candidate before another provider can change it."""
    for name, expected in recovery['partial_files'].items():
        source = root / name
        if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_file():
            raise Stop('partial task path changed before recovery snapshot')
        data = source.read_bytes()
        if source.is_symlink() or hashlib.sha256(data).hexdigest() != expected:
            raise Stop('partial task changed before recovery snapshot')
        target = logs / 'recovery-inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(data)
    for name, expected in recovery.get('evidence_files', {}).items():
        source = root / '.ai-loop' / recovery['from_run'] / name
        if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_file():
            raise Stop('native failure evidence changed before snapshot')
        data = source.read_bytes()
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise Stop('native failure evidence changed before snapshot')
        target = logs / 'recovery-evidence' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(data)
    save_json(logs / 'recovery.json', recovery)


def claim_initial_resume(state, recovery, run_id, control_sha):
    """A recovery authorization is one-use, even if the next provider stops."""
    marker = state / ('resume-' + recovery['from_run'] + '.json')
    try:
        with marker.open('x', encoding='utf-8') as output:
            json.dump({'from_run': recovery['from_run'], 'new_run': run_id,
                       'control_sha': control_sha}, output, indent=2)
    except FileExistsError:
        raise Stop('this recovery was already attempted; coordinator recovery required')


def export_diagnostics(root, logs, task):
    """Visible curated ZIP; never export provider streams or authentication files."""
    folder = root / 'ai-loop-diagnostics'
    if any(p.is_symlink() for p in (folder, *folder.parents)):
        raise Stop('diagnostics export directory is unsafe')
    folder.mkdir(exist_ok=True, mode=0o700)
    destination = folder / (logs.name + '.zip')
    if destination.exists() or destination.is_symlink():
        raise Stop('diagnostics ZIP already exists; preserved')
    entries = [(root / name, name) for name in task['allowed_files']]
    for name in ('summary.json', 'task.json', 'provenance.json', 'recovery.json', 'coordinator-assessment.md'):
        entries.append((logs / name, 'evidence/' + name))
    for path in logs.glob('mac-containment-*.json'):
        entries.append((path, 'evidence/' + path.name))
    for path in logs.glob('tests-*-mandatory-final-*.log'):
        entries.append((path, 'evidence/' + path.name))
    for path in (logs / 'recovery-evidence').glob('*.json'):
        entries.append((path, 'previous-failure/' + path.name))
    selected, hashes, total = [], {}, 0
    for source, name in entries:
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise Stop('diagnostics input is unsafe: ' + name)
        if not source.is_file():
            continue
        size = source.stat().st_size
        total += size
        if size > 2 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise Stop('diagnostics input exceeds export bound')
        raw = source.read_bytes()
        if len(raw) != size:
            raise Stop('diagnostics input changed during export')
        hashes[name] = hashlib.sha256(raw).hexdigest()
        selected.append((name, raw))
    with destination.open('xb') as output:
        os.chmod(destination, 0o600)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in selected:
                archive.writestr(name, raw)
            archive.writestr('captured-sha256.json', json.dumps(hashes, indent=2) + '\n')
    return destination


def diagnostic_payload(root, logs):
    """Publish only frozen, curated evidence; provider streams/source stay local."""
    run_id = logs.name
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', run_id):
        raise Stop('invalid diagnostic run identity')
    if logs != root / '.ai-loop' / run_id:
        raise Stop('diagnostics must use the canonical run store')
    archive_path = root / 'ai-loop-diagnostics' / (run_id + '.zip')
    if (any(p.is_symlink() for p in (logs, *logs.parents, archive_path, *archive_path.parents))
            or not logs.is_dir() or not archive_path.is_file()
            or archive_path.stat().st_size > 16 * 1024 * 1024):
        raise Stop('frozen diagnostic ZIP missing/unsafe/oversized; preserve local evidence')
    raw_zip = archive_path.read_bytes()
    if len(raw_zip) > 16 * 1024 * 1024:
        raise Stop('diagnostic archive grew beyond publication bound')
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        rows = archive.infolist()
        names = [row.filename for row in rows]
        if (len(rows) > 256 or len(names) != len(set(names))
                or sum(row.file_size for row in rows) > 16 * 1024 * 1024
                or any(row.is_dir() or row.file_size > 2 * 1024 * 1024
                       or Path(row.filename).is_absolute() or '..' in Path(row.filename).parts
                       or (row.external_attr >> 16) & 0o170000 == 0o120000 for row in rows)):
            raise Stop('invalid bounded diagnostic archive')
        captured = {name: archive.read(name) for name in names}
    hashes = json.loads(captured.pop('captured-sha256.json'))
    if (not isinstance(hashes, dict) or set(hashes) != set(captured)
            or any(hashlib.sha256(raw).hexdigest() != hashes[name] for name, raw in captured.items())):
        raise Stop('frozen diagnostic capture hash mismatch')
    summary = json.loads(captured['evidence/summary.json'])
    provenance = json.loads(captured['evidence/provenance.json'])
    contract = json.loads(captured['evidence/task.json'])
    control_sha = provenance.get('control_sha')
    if (not isinstance(control_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', control_sha)
            or provenance.get('root') != str(root) or provenance.get('run_id') != run_id
            or summary.get('status') not in ('STOPPED', 'READY_FOR_FINAL_REVIEW')
            or not re.fullmatch(r'[0-9a-f]{40}', summary.get('head', ''))):
        raise Stop('diagnostic provenance/status invalid')
    pinned, _ = load_task(root, control_sha)
    if contract != pinned or summary.get('branch') != pinned['branch']:
        raise Stop('diagnostic contract differs from its original Git pin')

    def encoded(value):
        return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

    # Do not forward free-form Codex/Claude summaries/findings, exception reason,
    # API error output, arbitrary history keys, prompts or environment values.
    public_summary = {key: summary[key] for key in ('status', 'head', 'branch')}
    public_summary.update(run_id=run_id, task_id=pinned['task_id'],
                          source_summary_sha256=hashes['evidence/summary.json'])
    public_summary['rounds'] = []
    for item in summary.get('history', []):
        row = {key: item[key] for key in ('round', 'head', 'tests', 'tested_sha256') if key in item}
        for key in ('developer', 'review', 'final_full', 'mandatory'):
            if isinstance(item.get(key), dict):
                row[key] = {field: item[key][field] for field in ('status', 'reviewed_head')
                            if field in item[key]}
        public_summary['rounds'].append(row)
    public_provenance = {key: provenance[key] for key in ('root', 'run_id', 'control_sha', 'base',
        'start_head', 'start_branch', 'local_test_mode', 'venv_python', 'system_python') if key in provenance}
    selected = {'summary.json': encoded(public_summary), 'provenance.json': encoded(public_provenance),
        'source-hashes.json': encoded({'zip_sha256': hashlib.sha256(raw_zip).hexdigest(),
            'captured_sha256': hashes, 'source_files_published': False})}
    report = pinned.get('acceptance_report')
    if report and report in pinned['allowed_files'] and report in captured:
        selected['validation.md'] = captured[report]
    native = []
    for name, raw in captured.items():
        if re.fullmatch(r'evidence/mac-containment-[0-9]+\.json', name):
            data = json.loads(raw)
            if data.get('platform') != 'darwin' or data.get('schema_version') != 1:
                raise Stop('invalid native diagnostic evidence')
            selected[Path(name).name] = raw
            native.append(data)
        elif (pinned.get('mac_evidence') == 'mac-containment'
              and re.fullmatch(r'evidence/tests-[0-9]+-mandatory-final-[0-9]+\.(stdout|stderr)\.log', name)):
            selected[Path(name).name] = raw
    content = ('# AI-loop diagnostic handoff\n\nStatus: **' + summary['status']
        + '**. Evidence only; no containment/security approval, feature publication or automatic recovery.\n\n'
        + '- Run: `' + run_id + '`\n- Task: `' + pinned['task_id'] + '`\n'
        + '- Candidate HEAD: `' + summary['head'] + '`\n- Original control: `' + control_sha + '`\n'
        + '- Source ZIP SHA-256: `' + hashlib.sha256(raw_zip).hexdigest() + '`\n'
        + '- Source code and raw Codex/Claude/authentication logs remain on the Mac.\n'
        + '- [Projected runner summary](summary.json); [provenance](provenance.json); '
          '[capture hashes](source-hashes.json).\n')
    for data in native:
        failure = data.get('failure', {})
        content += '\nNative result: `' + str(failure.get('scenario', 'complete')) + '` / `'
        content += str(failure.get('status', 'no failure recorded')) + '`.\n'
        for name, result in sorted(data.get('scenarios', {}).items()):
            content += '- `' + name + '`: `' + str(result.get('status')) + '`\n'
    content += '\n## Published evidence files\n\n'
    for name in sorted(selected):
        content += '- [' + name + '](' + name + ')\n'
    selected['index.md'] = content.encode('utf-8')
    if (sum(len(raw) for raw in selected.values()) > 512 * 1024
            or any(len(raw) > 128 * 1024 or b'\0' in raw for raw in selected.values())):
        raise Stop('curated Git diagnostics exceed publication bound')
    for raw in selected.values():
        raw.decode('utf-8')
    if archive_path.read_bytes() != raw_zip:
        raise Stop('diagnostic archive changed during capture')
    return selected


def diagnostic_api(root, logs, method, endpoint, payload=None, allow_missing=False):
    """Structured gh requests; never place report text/credentials in shell args."""
    argv = ['gh', 'api', '--method', method, endpoint]
    request = None
    try:
        if payload is not None:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=logs,
                    prefix='diagnostic-request-', suffix='.json', delete=False) as output:
                request = Path(output.name)
                json.dump(payload, output, ensure_ascii=False)
            argv += ['--input', str(request)]
        rc, raw, _ = command(argv, root, progress='[診斷/Git] 發布篩選後的報告')
        if len(raw.encode('utf-8')) > 4 * 1024 * 1024:
            raise Stop('diagnostic API response exceeds bound')
        reply = json.loads(raw)
        if rc:
            if allow_missing and reply.get('status') == '404' and reply.get('message') == 'Not Found':
                return None
            if allow_missing and reply.get('status') == 404 and reply.get('message') == 'Not Found':
                return None
            raise Stop('diagnostic Git API failed; local evidence preserved')
        return reply
    finally:
        if request is not None:
            request.unlink()


def publish_diagnostics(root, logs):
    """Append an immutable report batch to a separate, reports-only branch."""
    if not shutil.which('gh'):
        raise Stop('gh is required to publish diagnostics')
    files = diagnostic_payload(root, logs)
    receipt = logs / 'published-diagnostics.json'
    if receipt.is_symlink():
        raise Stop('diagnostic receipt is unsafe; preserved')
    if receipt.exists():
        previous_receipt = json.loads(receipt.read_bytes())
        if previous_receipt.get('status') != 'PUBLISHED' or previous_receipt.get('run_id') != logs.name:
            raise Stop('existing diagnostic receipt is unrelated; preserved')
    prefix = 'reports/ai-loop/diagnostics/' + logs.name + '/'
    expected = {prefix + name: hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii')
                + b'\0' + raw).hexdigest() for name, raw in files.items()}
    api = 'repos/' + IDENTITY + '/git/'
    ref_path = api + 'ref/heads/' + DIAGNOSTICS_BRANCH
    read = lambda endpoint, missing=False: diagnostic_api(root, logs, 'GET', endpoint, allow_missing=missing)
    ref = read(ref_path, True)
    previous = ref['object']['sha'] if ref else None
    if previous is not None and not re.fullmatch(r'[0-9a-f]{40}', previous):
        raise Stop('invalid diagnostic branch identity')

    def tree_rows(sha):
        value = read(api + 'trees/' + sha + '?recursive=1')
        if value.get('truncated') is not False:
            raise Stop('cannot inspect complete diagnostic tree')
        return {row['path']: (row['sha'], row['mode'], row['type'])
                for row in value['tree'] if row.get('type') != 'tree'}

    old_rows, base_tree = {}, None
    if previous:
        base_tree = read(api + 'commits/' + previous)['tree']['sha']
        old_rows = tree_rows(base_tree)
    existing = {path: row for path, row in old_rows.items() if path.startswith(prefix)}
    desired = {path: (sha, '100644', 'blob') for path, sha in expected.items()}
    if existing and existing != desired:
        raise Stop('diagnostic run already differs remotely; never overwrite it')
    commit = previous
    if not existing:
        payload = {'tree': [{'path': prefix + name, 'mode': '100644', 'type': 'blob',
                            'content': raw.decode('utf-8')} for name, raw in files.items()]}
        if base_tree:
            payload['base_tree'] = base_tree
        tree = diagnostic_api(root, logs, 'POST', api + 'trees', payload)['sha']
        if tree_rows(tree) != {**old_rows, **desired}:
            raise Stop('diagnostic tree changed outside the report batch')
        commit = diagnostic_api(root, logs, 'POST', api + 'commits', {
            'message': 'docs: publish AI-loop diagnostics ' + logs.name,
            'tree': tree, 'parents': [previous] if previous else []})['sha']
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise Stop('invalid diagnostic commit identity')
        latest = read(ref_path, True)
        if (latest['object']['sha'] if latest else None) != previous:
            raise Stop('diagnostic branch moved; no automatic upload retry')
        if previous:
            diagnostic_api(root, logs, 'PATCH', api + 'refs/heads/' + DIAGNOSTICS_BRANCH,
                           {'sha': commit, 'force': False})
        else:
            diagnostic_api(root, logs, 'POST', api + 'refs',
                           {'ref': 'refs/heads/' + DIAGNOSTICS_BRANCH, 'sha': commit})
        if read(ref_path)['object']['sha'] != commit:
            raise Stop('diagnostic publication unconfirmed')
        verified = tree_rows(read(api + 'commits/' + commit)['tree']['sha'])
        if verified != {**old_rows, **desired}:
            raise Stop('published diagnostic content verification failed')
    url = 'https://github.com/' + IDENTITY + '/blob/' + commit + '/' + prefix + 'index.md'
    save_json(receipt, {'status': 'PUBLISHED', 'run_id': logs.name,
        'branch': DIAGNOSTICS_BRANCH, 'commit': commit, 'url': url,
        'published_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}})
    print('[診斷/Git 已發布] ' + url, flush=True)
    return url


def finish_diagnostics(root, logs, task):
    """Upload problems must not replace the original test/model STOP."""
    try:
        exported = export_diagnostics(root, logs, task)
        print('[診斷 ZIP／Finder 可見] ' + str(exported), file=sys.stderr, flush=True)
    except (Stop, OSError, ValueError) as error:
        print('[診斷匯出未完成] ' + str(error), file=sys.stderr, flush=True)
        return
    try:
        publish_diagnostics(root, logs)
    except (Stop, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print('[診斷/Git 未發布] ' + str(error) + '\n本機 ZIP 已保留。補傳：bash scripts/ai-loop.sh '
              '--publish-diagnostics ' + logs.name, file=sys.stderr, flush=True)


def create_draft_pr(root, task, logs, publication_head, report_rel):
    if git(root, 'rev-parse', 'origin/main') != task['base_sha']:
        raise Stop('base changed before PR creation')
    body = logs / 'new-pr-body.md'
    body.write_text('Implements only the authorized task: ' + task['task_id']
        + '.\n\nValidation and limitations: `' + task.get('acceptance_report', report_rel)
        + '`. Automated evidence: `' + report_rel + '`.\n\nPublication HEAD: `'
        + publication_head + '`. Ordinary Claude review passed. Exact-head CI is pending. '
        + 'Independent security review and coordinator acceptance remain pending. '
        + 'No real product Git ADD, public mutation path, merge or next slice.\n', encoding='utf-8')
    return run(['gh', 'pr', 'create', '--repo', IDENTITY, '--base', 'main', '--head', task['branch'],
                '--draft', '--title', task['pr_title'], '--body-file', str(body)], root)


def record_acceptance(root, task, head, history, mac):
    """Append runner evidence without replacing the developer's historical report."""
    report = root / task['acceptance_report']
    if not report.is_file() or report.is_symlink():
        raise Stop('required acceptance report missing/unsafe')
    report.write_text(report.read_text() + '\n\n## Outer runner final verification\n\n'
        + '- Tested implementation HEAD: `' + head + '`; subsequent report update changes evidence only.\n'
        + '- Required local full canonical checks: PASS.\n'
        + '- A11b.2.2.1 containment harness: PASS (implementation/required Mac checks only).\n'
        + '- Real product Git ADD executed: NO; public mutation path: NONE.\n'
        + '- A11b.2.2.2 authorized: NO.\n'
        + '- Ready for independent security review: YES; security/merge approval remains pending.\n\n'
        + 'The runner executed the configured commands and validated the new Mac evidence '
        + 'against the frozen source/test hashes. Exit zero and JSON alone are not a proof '
        + 'of test design correctness: independent source/security review remains required.\n\n'
        + '```json\n' + json.dumps({'rounds': history, 'mac_evidence': mac}, ensure_ascii=False, indent=2)
        + '\n```\n', encoding='utf-8')


def read_pr(root, task, expected_head):
    pr = json.loads(run(['gh', 'api', 'repos/' + IDENTITY + '/pulls/' + str(task['pr_number'])], root))
    if (pr.get('state') != 'open' or pr.get('merged') is not False
            or pr['base']['ref'] != 'main' or pr['base']['sha'] != task['base_sha']
            or pr['base']['repo']['full_name'] != IDENTITY
            or pr['head']['repo']['full_name'] != IDENTITY
            or pr['head']['ref'] != task['branch'] or pr['head']['sha'] != expected_head):
        raise Stop('PR state/base/branch/HEAD changed; refresh the task before continuing')
    return pr


def prepare_existing_branch(root, task, check_only=False):
    """Reuse the PR branch; never recreate it from main or overwrite local commits."""
    branch, remote = task['branch'], 'origin/' + task['branch']
    git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/' + branch + ':refs/remotes/origin/' + branch)
    if git(root, 'rev-parse', remote) != task['expected_head']:
        raise Stop('remote PR branch moved since task preparation')
    current = git(root, 'rev-parse', 'HEAD')
    if command(['git', 'merge-base', '--is-ancestor', current, task['expected_head']], root)[0]:
        raise Stop('current branch has unrelated/unpublished work; files preserved')
    exists = command(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/' + branch], root)[0] == 0
    if exists:
        local = git(root, 'rev-parse', branch)
        if command(['git', 'merge-base', '--is-ancestor', local, task['expected_head']], root)[0]:
            raise Stop('local PR branch has unpublished/divergent commits; files preserved')
    if check_only:
        return
    if exists:
        git(root, 'switch', '--no-overwrite-ignore', branch)
        git(root, 'merge', '--ff-only', '--no-overwrite-ignore', remote)
    else:
        git(root, 'switch', '--no-overwrite-ignore', '-c', branch, remote)
    if git(root, 'rev-parse', 'HEAD') != task['expected_head']:
        raise Stop('checkout did not reach the pinned PR HEAD')


def wait_ci(root, head, workflow):
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        raw = run(['gh', 'run', 'list', '--repo', IDENTITY, '--commit', head,
                   '--workflow', workflow, '--json',
                   'headSha,status,conclusion,url', '--limit', '10'], root)
        matches = [r for r in json.loads(raw) if r['headSha'] == head]
        if matches and all(r['status'] == 'completed' for r in matches):
            if any(r['conclusion'] != 'success' for r in matches):
                raise Stop('exact-head CI did not pass: ' + head)
            return matches
        print('[CI] Waiting for exact HEAD ' + head[:12], flush=True)
        time.sleep(20)
    raise Stop('CI timed out; no PASS inferred from missing checks')


def install_launcher(root, control_sha):
    """Install the pinned launcher without switching branches or starting a task."""
    rc, source, _ = command(['git', 'show', control_sha + ':scripts/ai-loop.sh'], root)
    if rc:
        raise Stop('cannot read the pinned launcher from Git')
    encoded = source.encode('utf-8')
    if not source.startswith('#!/usr/bin/env bash\n') or '\0' in source or len(encoded) > 64 * 1024:
        raise Stop('invalid bounded launcher')
    rc, _, _ = command(['bash', '-n'], root, stdin=source)
    if rc:
        raise Stop('pinned launcher failed Bash syntax validation')
    directory = root / 'scripts'
    target = directory / 'ai-loop.sh'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()) or target.is_symlink():
        raise Stop('launcher install path must not be a symlink or non-directory')
    directory.mkdir(exist_ok=True)
    if target.exists():
        if not target.is_file() or target.read_bytes() != encoded:
            raise Stop('scripts/ai-loop.sh already differs; existing file preserved')
        print('[Install] Existing scripts/ai-loop.sh already matches pinned Git.', flush=True)
    else:
        # Exclusive creation also protects against a concurrent file appearing.
        with target.open('xb') as output:
            output.write(encoded)
            os.fchmod(output.fileno(), 0o700)
        print('[Install] Installed ' + str(target), flush=True)
    print('INSTALL ONLY COMPLETE. No task, model, test or branch switch performed.\n'
          'Future task entry point: bash scripts/ai-loop.sh\n'
          'Current stopped task still requires coordinator recovery.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--control-sha', required=True)
    parser.add_argument('--check', action='store_true', help='fetch/check only; no models or code edits')
    parser.add_argument('--resume', metavar='RUN_ID', help='resume one explicitly contracted Codex/native acceptance stop')
    parser.add_argument('--publish-diagnostics', metavar='RUN_ID',
                        help='upload a frozen diagnostic report only; no models/tests/task recovery')
    parser.add_argument('--install-launcher', action='store_true',
                        help='install scripts/ai-loop.sh only; do not start or resume the task')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--test-mode', choices=('related', 'full'), help='override task test mode')
    mode.add_argument('--related', dest='test_mode', action='store_const', const='related', help='local related tests; full suite in CI')
    mode.add_argument('--full', dest='test_mode', action='store_const', const='full', help='also require local full suite on final tested bytes')
    args = parser.parse_args()
    if args.publish_diagnostics and (args.install_launcher or args.check or args.test_mode or args.resume):
        parser.error('--publish-diagnostics cannot be combined with task/install/test options')
    if args.install_launcher and (args.check or args.test_mode or args.resume):
        parser.error('--install-launcher cannot be combined with task/check/test options')
    root = REPO.resolve()
    if Path.cwd().resolve() != root or git(root, 'rev-parse', '--show-toplevel') != str(root):
        raise Stop('must run in the canonical Mac repository')
    if not re.fullmatch(r'[0-9a-f]{40}', args.control_sha):
        raise Stop('invalid control SHA')
    remote = git(root, 'remote', 'get-url', 'origin')
    if remote not in ('git@github.com:' + IDENTITY + '.git', 'https://github.com/' + IDENTITY + '.git',
                       'https://github.com/' + IDENTITY, 'ssh://git@github.com/' + IDENTITY + '.git'):
        raise Stop('wrong repository identity')
    state = Path(git(root, 'rev-parse', '--absolute-git-dir')) / 'ai-loop'
    state.mkdir(exist_ok=True, mode=0o700)
    with (state / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('another ai-loop is already running')
        if args.install_launcher:
            install_launcher(root, args.control_sha)
            return
        if args.publish_diagnostics:
            if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', args.publish_diagnostics):
                raise Stop('invalid diagnostic run identity')
            publish_diagnostics(root, root / '.ai-loop' / args.publish_diagnostics)
            print('DIAGNOSTICS ONLY COMPLETE. No task/model/test/branch/index change or recovery claim.', flush=True)
            return
        task, text = load_task(root, args.control_sha)
        new_task = task['mode'] == 'implement_new_pr'
        test_mode = args.test_mode or task['default_test_mode']
        for executable in ('codex', 'claude', 'gh', 'make'):
            if not shutil.which(executable):
                raise Stop(executable + ' is not installed/in PATH')
        if not (root / 'tools/llmtool/.venv/bin/python').is_file():
            raise Stop('tools/llmtool/.venv/bin/python is missing; use the existing llmtool environment')
        if not Path('/usr/bin/python3').is_file():
            raise Stop('the Mac system Python required by this task is missing')
        # Git/GitHub authentication fails before spending model tokens.
        run(['gh', 'auth', 'status'], root)
        git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
        if git(root, 'rev-parse', 'origin/main') != task['base_sha']:
            raise Stop('main changed since task was prepared; task.md must be refreshed')
        tracked_paths = git(root, 'diff', '--name-only', 'HEAD').splitlines()
        if (git(root, 'diff', '--cached', '--name-only') or (tracked_paths and
                (not args.resume or any(p not in task['allowed_files'] for p in tracked_paths)))):
            raise Stop('tracked work is dirty; existing files preserved')
        original = untracked(root)
        recovery = None
        if args.resume:
            prepare_resume = (prepare_acceptance_resume if task.get('acceptance_failure_recovery')
                              else prepare_initial_resume)
            original, recovery = prepare_resume(root, task, args.resume, original, tracked_paths)
            marker = state / ('resume-' + recovery['from_run'] + '.json')
            if marker.exists() or marker.is_symlink():
                raise Stop('this recovery was already attempted; coordinator recovery required')
        collision = set(original).intersection(task['allowed_files'])
        if collision:
            raise Stop('untracked task-file collision; existing files preserved')
        completed = state / (task['task_id'] + '.completed.json')
        if completed.exists():
            print('Already completed. ' + completed.read_text(), flush=True)
            return
        branch, head = git(root, 'branch', '--show-current'), git(root, 'rev-parse', 'HEAD')
        pr_info = None
        if recovery:
            print('[Resume] Preserving candidate from ' + args.resume + '; all validation runs afresh.', flush=True)
        elif new_task:
            prepare_new_branch(root, task, check_only=args.check)
        else:
            pr_info = read_pr(root, task, task['expected_head'])
            prepare_existing_branch(root, task, check_only=args.check)
        print('[Task] ' + task['task_id'] + (' / new Draft PR' if new_task else ' / PR #' + str(task['pr_number'])), flush=True)
        print('[Mode] local ' + test_mode + ' tests; full canonical checks remain required in CI', flush=True)
        if task.get('require_local_full'):
            print('[Required] Final local full checks and Mac containment probes cannot be skipped by --related.', flush=True)
        if args.check:
            print('PRECHECK PASS. No models started; no source changes.', flush=True)
            return
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
        logs = root / '.ai-loop' / run_id
        if (root / '.ai-loop').is_symlink():
            raise Stop('.ai-loop must not be a symlink')
        logs.mkdir(parents=True, mode=0o700)
        exclude = Path(git(root, 'rev-parse', '--git-path', 'info/exclude'))
        with exclude.open('a') as f:
            f.write('\n/.ai-loop/\n/ai-loop-diagnostics/' + run_id + '.zip\n')
        (logs / 'task.md').write_text(text + '\n', encoding='utf-8')
        save_json(logs / 'task.json', task)
        if recovery:
            snapshot_recovery(root, logs, recovery)
            if recovery.get('kind') == 'acceptance_failure':
                assessment = 'reports/ai-loop/checkpoints/A11b.2.2.1-sigint-failure-assessment-20261007.md'
                (logs / 'coordinator-assessment.md').write_text(
                    git(root, 'show', args.control_sha + ':' + assessment) + '\n', encoding='utf-8')
        save_json(logs / 'provenance.json', {'root': str(root), 'start_branch': branch, 'start_head': head,
            'base': task['base_sha'], 'pr_number': task['pr_number'], 'expected_head': task['expected_head'],
            'control_sha': args.control_sha, 'run_id': run_id, 'local_test_mode': test_mode,
            'codex_timeout_seconds': task.get('codex_timeout_seconds', task['timeout_seconds']),
            'claude_timeout_seconds': task.get('claude_timeout_seconds', task['timeout_seconds']),
            'codex': run(['codex', '--version'], root), 'claude': run(['claude', '--version'], root),
            'venv_python': run(['tools/llmtool/.venv/bin/python', '--version'], root),
            'system_python': run(['/usr/bin/python3', '--version'], root)})
        branch = task['branch']
        head = git(root, 'rev-parse', 'HEAD')
        if recovery:
            check_scope(root, task, original, branch, head)
            claim_initial_resume(state, recovery, run_id, args.control_sha)
        prompt = (('Implement the newly authorized task, then repair only within its scope. ' if new_task
                   else 'Repair only the existing PR implementation within this task. ')
                  + 'Read AGENTS.md and skills/implement-feature/SKILL.md. '
                  'Do not stage, commit, push, merge, create worktrees, change task scope, or start another slice. '
                  'Only edit allowed_files in task.json. The outer runner owns Git and deterministic tests; '
                  'do not run expensive suites/probes yourself. Write the initial report with pending runner checks, '
                  'not fabricated PASS; final truthful evidence will be appended by the runner. '
                  'If architecture/scope/authorization is unclear, return NEEDS_DECISION. '
                  'Your summary will be committed to Git: do not include secrets, tokens, raw provider output or private logs. '
                  'Return JSON matching the provided schema.\nTASK:\n' + text + '\nCONTRACT:\n' + json.dumps(task))
        history = []
        repair_reason = ('Continue the existing partial implementation after an interrupted initial Codex call. '
                         'Read the existing allowed files before editing; do not restart from scratch. '
                         'Preserve historical report evidence and label all current gates pending. '
                         'No previous tests/review are inherited. Recovery input hashes and retained copies: '
                         + str(logs / 'recovery.json')) if recovery else (
                         'Implement the initial candidate. This is implementation, not a repair attempt.' if new_task else None)
        native_recovery = recovery is not None and recovery.get('kind') == 'acceptance_failure'
        if native_recovery:
            repair_reason = ('Repair the existing committed candidate after the coordinator-assessed SIGINT Mac failure. '
                'Preserve the original failure. Read recovery.json, recovery-evidence/summary.json and '
                'recovery-evidence/mac-containment-0.json under ' + str(logs) + '. '
                'First preserve primary errors and bounded diagnostic evidence. Zombie/SID handling is a '
                'suspected cause, not an established runtime fact. Do not remove identity checks, guess SID, '
                'reimplement the feature or rerun old probes. All validation starts afresh on changed bytes.')
        full_tested_bytes = None
        mandatory_tested_bytes = None
        mac = None
        checkpoint = None
        checkpoint_error = None
        try:
            initial_paths = git(root, 'diff', '--name-only', '-z', task['base_sha'], head).split('\0')
            if any(p and p not in task['allowed_files'] for p in initial_paths):
                raise Stop('existing PR cumulative diff exceeds the pinned task scope')
            last_round = task['max_repairs'] - 1 if native_recovery else task['max_repairs']
            for round_no in range(last_round + 1):
                result = None
                if repair_reason is not None:
                    result = codex(root, logs, prompt + '\nREPAIR EVIDENCE:\n' + repair_reason,
                                   round_no, task.get('codex_timeout_seconds', task['timeout_seconds']))
                    check_scope(root, task, original, branch, head)
                    if result['status'] != 'PASS':
                        if result['status'] == 'NEEDS_DECISION':
                            try:
                                checkpoint = publish_decision_checkpoint(root, logs, task, result,
                                    original, head, args.control_sha, round_no)
                            except (Stop, OSError, ValueError, KeyError, TypeError) as publication_error:
                                checkpoint_error = str(publication_error)
                                print('[Checkpoint] Publication not confirmed; local evidence preserved. '
                                      + checkpoint_error, file=sys.stderr, flush=True)
                        raise Stop('Codex reported ' + result['status'] + ': ' + result['summary'])
                if any(not (root / p).is_file() or (root / p).is_symlink() for p in task.get('required_files', [])):
                    raise Stop('required implementation/test/report artifacts missing; no empty-suite PASS')
                tested_bytes = {p: digest(root / p) for p in task['allowed_files'] if (root / p).exists()}
                if native_recovery and round_no == 0 and runtime_bytes(tested_bytes) == runtime_bytes(recovery['partial_files']):
                    raise Stop('native recovery requires changed source/test bytes; no blind probe retry')
                suite = 'full' if test_mode == 'full' and round_no == 0 else 'related'
                passed = tests(root, logs, task, round_no, suite)
                test_results = json.loads(test_result_file(logs, round_no, suite).read_text())
                touched = check_scope(root, task, original, branch, head)
                after_tests = {p: digest(root / p) for p in tested_bytes if (root / p).exists()}
                if after_tests != tested_bytes:
                    raise Stop('tests changed the implementation bytes; provenance is invalid')
                if not passed:
                    history.append({'round': round_no, 'head': head, 'developer': result,
                                    'tests': 'FAIL', 'test_results': test_results,
                                    'review': None, 'tested_sha256': tested_bytes})
                    if round_no == last_round:
                        raise Stop('deterministic tests still fail after repair bound')
                    repair_reason = 'Repair only failures in the deterministic tests. Read test logs under ' + str(logs)
                    continue
                if suite == 'full':
                    full_tested_bytes = runtime_bytes(tested_bytes)
                if touched:
                    git(root, 'diff', '--check')
                    git(root, 'add', '--', *touched)
                    git(root, 'commit', '-m', 'fix(llmtool): ' + task['task_id'] + ' round ' + str(round_no))
                    head = git(root, 'rev-parse', 'HEAD')
                if check_scope(root, task, original, branch, head):
                    raise Stop('commit hooks left uncommitted source; tests/review provenance is unsafe')
                committed_bytes = {p: digest(root / p) for p in tested_bytes if (root / p).exists()}
                if committed_bytes != tested_bytes:
                    raise Stop('commit hooks changed the tested bytes; no review PASS can be inferred')
                patch = git(root, 'diff', '--no-ext-diff', '--no-textconv', task['base_sha'], head)
                review_prompt = ('Independently review the ENTIRE cumulative diff against task/AGENTS.md/CLAUDE.md '
                    'and skills/review-change/SKILL.md. You are read-only; tests were run by the outer runner. '
                    'Do not run commands or edit files. Return NEEDS_DECISION for scope/architecture conflict. '
                    'Use PASS only when no blocking findings. Evidence must cite concrete file/test behavior. '
                    'Do not convert optional improvements into blockers. This is ordinary code review, '
                    'Your findings will be committed to Git: never include secrets, tokens, raw provider output or private logs. '
                    'not formal security/QA sign-off or merge approval. Reviewed_head must be ' + head + '\nTASK:\n'
                    + text + '\nLOCAL TEST MODE: ' + test_mode + '\nTEST RESULTS:\n'
                    + ('Required final Mac/full gates will run after this preliminary source review; '
                       'review PASS is not yet acceptance/merge approval.\n' if new_task else '')
                    + test_result_file(logs, round_no, suite).read_text()
                    + '\nDIFF:\n' + patch)
                snapshot = git(root, 'status', '--porcelain')
                review = claude(root, logs, review_prompt, round_no, task.get('claude_timeout_seconds', task['timeout_seconds']), head)
                check_scope(root, task, original, branch, head)
                if git(root, 'status', '--porcelain') != snapshot:
                    raise Stop('review altered source state')
                history.append({'round': round_no, 'head': head, 'developer': result, 'tests': 'PASS',
                                'test_results': test_results, 'tested_sha256': tested_bytes, 'review': review})
                if review['status'] == 'PASS':
                    if (test_mode == 'full' or task.get('require_local_full')) and full_tested_bytes != runtime_bytes(committed_bytes):
                        final_passed = tests(root, logs, task, round_no, 'full-final')
                        final_results = json.loads(test_result_file(logs, round_no, 'full-final').read_text())
                        history[-1]['final_full'] = {'status': 'PASS' if final_passed else 'FAIL', 'results': final_results}
                        if check_scope(root, task, original, branch, head):
                            raise Stop('final full tests altered source state')
                        after_full = {p: digest(root / p) for p in committed_bytes if (root / p).exists()}
                        if after_full != committed_bytes:
                            raise Stop('final full tests changed the reviewed bytes')
                        if not final_passed:
                            if round_no == last_round:
                                raise Stop('final full tests still fail after repair bound')
                            repair_reason = 'Repair only final-full test failures. Read logs under ' + str(logs)
                            continue
                        full_tested_bytes = runtime_bytes(committed_bytes)
                    if task.get('mandatory_tests') and mandatory_tested_bytes != runtime_bytes(committed_bytes):
                        evidence_path = mac_evidence_path(logs, round_no)
                        if evidence_path.exists():
                            raise Stop('Mac evidence path collision; old evidence is not a new run')
                        mandatory_passed = tests(root, logs, task, round_no, 'mandatory-final')
                        mandatory_results = json.loads(test_result_file(logs, round_no, 'mandatory-final').read_text())
                        history[-1]['mandatory'] = {'status': 'PASS' if mandatory_passed else 'FAIL', 'results': mandatory_results}
                        if check_scope(root, task, original, branch, head):
                            raise Stop('required Mac checks altered source state')
                        if {p: digest(root / p) for p in committed_bytes if (root / p).exists()} != committed_bytes:
                            raise Stop('required Mac checks changed reviewed bytes')
                        if not mandatory_passed:
                            raise Stop('required Mac acceptance failed; probes/design need coordinator assessment, no automatic retry')
                        if task.get('mac_evidence'):
                            mac = validate_mac_evidence(evidence_path, committed_bytes,
                                json.loads((logs / 'provenance.json').read_text())['venv_python'].removeprefix('Python '))
                        mandatory_tested_bytes = runtime_bytes(committed_bytes)
                    if task.get('acceptance_report'):
                        record_acceptance(root, task, head, history, mac)
                        if check_scope(root, task, original, branch, head) != [task['acceptance_report']]:
                            raise Stop('acceptance evidence update exceeded report scope')
                        git(root, 'diff', '--check')
                        git(root, 'add', '--', task['acceptance_report'])
                        git(root, 'commit', '-m', 'docs: record required Mac containment verification')
                        head = git(root, 'rev-parse', 'HEAD')
                        if check_scope(root, task, original, branch, head):
                            raise Stop('acceptance commit hooks changed files')
                        now_bytes = {p: digest(root / p) for p in committed_bytes if (root / p).exists()}
                        if runtime_bytes(now_bytes) != runtime_bytes(committed_bytes):
                            raise Stop('acceptance commit changed verified implementation')
                        committed_bytes = now_bytes
                        snapshot = git(root, 'status', '--porcelain')
                        final_review = claude(root, logs, review_prompt + '\nFINAL REQUIRED VERIFICATION:\n'
                            + json.dumps({'rounds': history, 'mac': mac})
                            + '\nRe-review the final cumulative diff including the appended truthful report. '
                            + 'Reviewed_head must now be ' + head + '\nFINAL DIFF:\n'
                            + git(root, 'diff', '--no-ext-diff', '--no-textconv', task['base_sha'], head),
                            round_no + 100, task.get('claude_timeout_seconds', task['timeout_seconds']), head)
                        check_scope(root, task, original, branch, head)
                        if git(root, 'status', '--porcelain') != snapshot:
                            raise Stop('final review altered files')
                        history[-1]['final_review'] = final_review
                        history[-1]['final_review_head'] = head
                        if final_review['status'] != 'PASS':
                            if final_review['status'] == 'NEEDS_DECISION' or round_no == last_round:
                                raise Stop('final review needs a decision or exceeded repair bound')
                            repair_reason = 'Repair only final review blockers:\n' + json.dumps(final_review)
                            continue
                    break
                if review['status'] == 'NEEDS_DECISION' or round_no == last_round:
                    raise Stop('Claude review needs a decision or exceeded repair bound')
                repair_reason = ('Repair only these reproducible blocking findings. '
                                 'Review is evidence, not permission to expand scope:\n' + json.dumps(review))
            else:
                raise Stop('loop ended without PASS')
            # Publish only curated results, never raw model streams or local logs.
            report_rel = 'reports/ai-loop/' + task['task_id'] + '-' + run_id + '.md'
            report = root / report_rel
            report.parent.mkdir(parents=True, exist_ok=True)
            if report.exists() or report.parent.is_symlink():
                raise Stop('report path collision or symlink')
            report.write_text('# AI loop: ' + task['task_id'] + '\n\n'
                + 'Local selected tests/ordinary Claude review: PASS. Final merge approval remains pending.\n\n'
                + '- Local test mode: `' + test_mode + '`\n'
                + '- Local full canonical suite: ' + ('PASS' if full_tested_bytes == runtime_bytes(committed_bytes) else 'NOT RUN; required full suite remains in exact-head CI') + '\n'
                + '- Required Mac containment: ' + ('PASS; independent security review pending' if mac else 'not a gate of this task') + '\n'
                + '- Task mode: `' + task['mode'] + '`\n- Starting HEAD: `' + task['expected_head'] + '`\n'
                + '- Implementation HEAD: `' + head + '`\n- Control SHA: `' + args.control_sha + '`\n'
                + '- Canonical Mac root: `' + str(root) + '`\n- Repair attempts used: ' + str(round_no) + '\n'
                + '- Exact publication-head CI is checked after this report commit; inspect GitHub checks.\n'
                + '- Raw prompts/provider output remain local in `.ai-loop/' + run_id + '/`.\n\n'
                + '## Execution provenance\n\n```json\n' + (logs / 'provenance.json').read_text() + '```\n\n'
                + '```json\n' + json.dumps(history, ensure_ascii=False, indent=2) + '\n```\n', encoding='utf-8')
            git(root, 'add', '--', report_rel)
            git(root, 'commit', '-m', 'docs: record ' + task['task_id'] + ' automated handoff')
            publication_head = git(root, 'rev-parse', 'HEAD')
            if check_scope(root, task, original, branch, publication_head):
                raise Stop('publication hooks changed source; files preserved before push')
            publication_bytes = {p: digest(root / p) for p in task['allowed_files'] if (root / p).exists()}
            if publication_bytes != committed_bytes:
                raise Stop('publication hooks changed reviewed/tested bytes; no push or inferred PASS')
            git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
            if git(root, 'rev-parse', 'origin/main') != task['base_sha']:
                raise Stop('main changed before publication; refresh/recover without discarding work')
            if new_task:
                if run(['git', 'ls-remote', '--heads', 'origin', 'refs/heads/' + branch], root):
                    raise Stop('new branch was concurrently published; files preserved')
                if json.loads(run(['gh', 'api', 'repos/' + IDENTITY + '/pulls?state=all&head=davidcatcat:' + branch], root)):
                    raise Stop('a PR already exists for the new task; no duplicate publication')
            else:
                read_pr(root, task, task['expected_head'])
            git(root, 'push', '-u', 'origin', 'HEAD:refs/heads/' + branch)
            if new_task:
                create_draft_pr(root, task, logs, publication_head, report_rel)
                pulls = json.loads(run(['gh', 'api', 'repos/' + IDENTITY + '/pulls?state=open&head=davidcatcat:' + branch], root))
                if len(pulls) != 1 or pulls[0].get('draft') is not True:
                    raise Stop('could not establish unique new Draft PR; published work preserved')
                task['pr_number'] = pulls[0]['number']
                read_pr(root, task, publication_head)
            ci = wait_ci(root, publication_head, task['ci_workflow'])
            pr_info = read_pr(root, task, publication_head)
            body = logs / 'pr-body.md'
            body.write_text((pr_info.get('body') or '') + '\n\n## Automated independent review handoff\n\n'
                            + 'Review/repair contract: `automation/ai-loop/task.md` on control commit `'
                            + args.control_sha + '`.\n\nLocal ' + test_mode + ' checks and independent ordinary Claude review passed. '
                            + 'Exact publication-head CI passed. Final coordinator review is pending; no auto-merge.\n\n'
                            + 'Report: `' + report_rel + '`\n', encoding='utf-8')
            run(['gh', 'pr', 'edit', str(task['pr_number']), '--repo', IDENTITY, '--body-file', str(body)], root)
            pr = pr_info['html_url']
            summary = {'status': 'READY_FOR_FINAL_REVIEW', 'head': publication_head, 'branch': branch,
                       'report': report_rel, 'pr': pr, 'ci': ci, 'local_test_mode': test_mode,
                       'local_full': 'PASS' if full_tested_bytes == runtime_bytes(committed_bytes) else 'NOT_RUN',
                       'mac_containment': 'PASS' if mac else 'NOT_APPLICABLE',
                       'independent_security_review': 'PENDING' if new_task else 'not established by ordinary review'}
            save_json(completed, summary)
            save_json(logs / 'summary.json', summary)
            print('\nREADY FOR FINAL REVIEW\n' + pr + '\nHEAD: ' + publication_head, flush=True)
        except BaseException as exc:
            save_json(logs / 'summary.json', {'status': 'STOPPED', 'reason': str(exc), 'branch': branch,
                'head': git(root, 'rev-parse', 'HEAD'), 'history': history,
                'decision_checkpoint': checkpoint, 'checkpoint_publication_error': checkpoint_error})
            finish_diagnostics(root, logs, task)
            print('STOPPED. Work preserved. Logs: ' + str(logs), file=sys.stderr, flush=True)
            raise


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, TypeError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('STOP: cancelled; existing work preserved', file=sys.stderr)
        sys.exit(130)
