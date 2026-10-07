#!/usr/bin/env python3
"""Mac development handoff helper. Standard library only; never part of llmtool."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO = Path('/Users/davidsun/git/davidcataiporject/davidcattoolsproject')
IDENTITY = 'davidcatcat/davidcattoolsproject'
TASK_DIR = 'automation/ai-loop/'


class Stop(RuntimeError):
    pass


def command(argv, cwd, timeout=120, stdin=None, stdout_path=None, stderr_path=None):
    """Bound each child group, including cancellation. No shell interpolation."""
    handles = []
    try:
        for path in (stdout_path, stderr_path):
            handles.append(open(path, 'wb') if path else subprocess.PIPE)
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                stdout=handles[0], stderr=handles[1],
                                start_new_session=True)
        started = time.monotonic()
        payload = None if stdin is None else stdin.encode('utf-8')
        try:
            while True:
                try:
                    out, err = proc.communicate(payload, timeout=min(20, max(0.1, timeout - (time.monotonic() - started))))
                    return proc.returncode, (out or b'').decode('utf-8', 'replace'), (err or b'').decode('utf-8', 'replace')
                except subprocess.TimeoutExpired:
                    payload = None
                    if time.monotonic() - started >= timeout:
                        raise Stop('command timed out: ' + argv[0])
                    print('[Running] ' + argv[0] + ' ...', flush=True)
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
    required = {'task_id', 'base_sha', 'branch', 'allowed_files', 'tests', 'max_repairs', 'timeout_seconds', 'ci_workflow'}
    if set(metadata) != required or not re.fullmatch(r'[A-Za-z0-9._-]+', metadata['task_id']):
        raise Stop('invalid task contract')
    if not re.fullmatch(r'[0-9a-f]{40}', metadata['base_sha']) or not metadata['branch'].startswith('feat/'):
        raise Stop('invalid base/branch')
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
    if not isinstance(metadata['tests'], list) or not metadata['tests']:
        raise Stop('task needs deterministic tests')
    for argv in metadata['tests']:
        if not isinstance(argv, list) or not argv or any(not isinstance(a, str) for a in argv):
            raise Stop('invalid test argv')
    if not isinstance(metadata['ci_workflow'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.yml', metadata['ci_workflow']):
        raise Stop('invalid CI workflow')
    return metadata, text


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def codex(root, logs, prompt, round_no, timeout):
    output = logs / ('codex-%d.json' % round_no)
    spec = logs / 'codex-schema.json'
    save_json(spec, schema('developer'))
    print('[Codex] Implementation / repair %d' % round_no, flush=True)
    run(['codex', '-a', 'never', 'exec', '--sandbox', 'workspace-write',
         '--output-schema', str(spec), '--output-last-message', str(output), '-'],
        root, timeout=timeout, stdin=prompt,
        stdout_path=logs / ('codex-%d.stdout.log' % round_no),
        stderr_path=logs / ('codex-%d.stderr.log' % round_no))
    return validate_result(json.loads(output.read_text()), 'developer')


def claude(root, logs, prompt, round_no, timeout, head):
    output = logs / ('claude-%d.raw.json' % round_no)
    print('[Claude] Independent read-only review %d' % round_no, flush=True)
    run(['claude', '-p', '--output-format', 'json', '--json-schema', json.dumps(schema('review')),
         '--max-turns', '30', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read', 'Glob', 'Grep',
         '--permission-mode', 'dontAsk', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
         '--settings', '{"disableAllHooks":true}', '--no-session-persistence'],
        root, timeout=timeout, stdin=prompt, stdout_path=output,
        stderr_path=logs / ('claude-%d.stderr.log' % round_no))
    result = parse_claude(json.loads(output.read_text()), head)
    save_json(logs / ('claude-%d.json' % round_no), result)
    return result


def tests(root, logs, task, round_no):
    results = []
    for i, argv in enumerate(task['tests']):
        print('[Tests] ' + ' '.join(argv), flush=True)
        rc, _, _ = command(argv, root, timeout=task['timeout_seconds'],
                            stdout_path=logs / ('tests-%d-%d.stdout.log' % (round_no, i)),
                            stderr_path=logs / ('tests-%d-%d.stderr.log' % (round_no, i)))
        results.append({'argv': argv, 'exit_code': rc})
    save_json(logs / ('tests-%d.json' % round_no), results)
    return all(r['exit_code'] == 0 for r in results)


def wait_ci(root, head, workflow):
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        raw = run(['gh', 'run', 'list', '--repo', IDENTITY, '--commit', head,
                   '--workflow', workflow, '--event', 'push', '--json',
                   'headSha,status,conclusion,url', '--limit', '10'], root)
        matches = [r for r in json.loads(raw) if r['headSha'] == head]
        if matches and all(r['status'] == 'completed' for r in matches):
            if any(r['conclusion'] != 'success' for r in matches):
                raise Stop('exact-head CI did not pass: ' + head)
            return matches
        print('[CI] Waiting for exact HEAD ' + head[:12], flush=True)
        time.sleep(20)
    raise Stop('CI timed out; no PASS inferred from missing checks')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--control-sha', required=True)
    parser.add_argument('--check', action='store_true', help='fetch/check only; no models or code edits')
    args = parser.parse_args()
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
        task, text = load_task(root, args.control_sha)
        for executable in ('codex', 'claude', 'gh', 'make'):
            if not shutil.which(executable):
                raise Stop(executable + ' is not installed/in PATH')
        if not (root / '.venv/bin/python').is_file():
            raise Stop('canonical .venv/bin/python is missing; use the existing llmtool environment')
        # Git/GitHub authentication fails before spending model tokens.
        run(['gh', 'auth', 'status'], root)
        git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
        if git(root, 'rev-parse', 'origin/main') != task['base_sha']:
            raise Stop('main changed since task was prepared; task.md must be refreshed')
        if git(root, 'diff', '--name-only', 'HEAD') or git(root, 'diff', '--cached', '--name-only'):
            raise Stop('tracked work is dirty; existing files preserved')
        original = untracked(root)
        collision = set(original).intersection(task['allowed_files'])
        if collision:
            raise Stop('untracked task-file collision; existing files preserved')
        completed = state / (task['task_id'] + '.completed.json')
        if completed.exists():
            print('Already completed. ' + completed.read_text(), flush=True)
            return
        branch, head = git(root, 'branch', '--show-current'), git(root, 'rev-parse', 'HEAD')
        existing = command(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/' + task['branch']], root)[0] == 0
        if existing:
            raise Stop('task branch already exists; no automatic rerun/rewrite: ' + task['branch'])
        if command(['git', 'merge-base', '--is-ancestor', head, task['base_sha']], root)[0]:
            raise Stop('current branch has unmerged work; no automatic checkout over unrelated work')
        print('[Task] ' + task['task_id'] + '\n' + text, flush=True)
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
            f.write('\n/.ai-loop/\n')
        (logs / 'task.md').write_text(text + '\n', encoding='utf-8')
        save_json(logs / 'task.json', task)
        save_json(logs / 'provenance.json', {'root': str(root), 'start_branch': branch, 'base': head,
            'control_sha': args.control_sha, 'run_id': run_id,
            'codex': run(['codex', '--version'], root), 'claude': run(['claude', '--version'], root)})
        git(root, 'switch', '-c', task['branch'], task['base_sha'])
        branch = task['branch']
        head = git(root, 'rev-parse', 'HEAD')
        prompt = ('Execute exactly this task. Read AGENTS.md and skills/implement-feature/SKILL.md. '
                  'Do not stage, commit, push, merge, create worktrees, change task scope, or start another slice. '
                  'Only edit allowed_files in task.json. The outer runner owns Git and deterministic tests. '
                  'If architecture/scope/authorization is unclear, return NEEDS_DECISION. '
                  'Your summary will be committed to Git: do not include secrets, tokens, raw provider output or private logs. '
                  'Return JSON matching the provided schema.\nTASK:\n' + text + '\nCONTRACT:\n' + json.dumps(task))
        history = []
        try:
            for round_no in range(task['max_repairs'] + 1):
                result = codex(root, logs, prompt, round_no, task['timeout_seconds'])
                touched = check_scope(root, task, original, branch, head)
                if result['status'] != 'PASS':
                    raise Stop('Codex reported ' + result['status'] + ': ' + result['summary'])
                passed = tests(root, logs, task, round_no)
                touched = check_scope(root, task, original, branch, head)
                if not passed:
                    if round_no == task['max_repairs']:
                        raise Stop('deterministic tests still fail after repair bound')
                    prompt += '\nRepair only the failed tests. Read local test logs under ' + str(logs)
                    continue
                if touched:
                    git(root, 'diff', '--check')
                    git(root, 'add', '--', *touched)
                    git(root, 'commit', '-m', 'fix(llmtool): ' + task['task_id'] + ' round ' + str(round_no))
                    head = git(root, 'rev-parse', 'HEAD')
                if check_scope(root, task, original, branch, head):
                    raise Stop('commit hooks left uncommitted source; tests/review provenance is unsafe')
                if head == task['base_sha']:
                    raise Stop('no implementation changes; task not completed')
                patch = git(root, 'diff', '--no-ext-diff', '--no-textconv', task['base_sha'], head)
                review_prompt = ('Independently review the ENTIRE cumulative diff against task/AGENTS.md/CLAUDE.md '
                    'and skills/review-change/SKILL.md. You are read-only; tests were run by the outer runner. '
                    'Do not run commands or edit files. Return NEEDS_DECISION for scope/architecture conflict. '
                    'Use PASS only when no blocking findings. Evidence must cite concrete file/test behavior. '
                    'Do not convert optional improvements into blockers. This is ordinary code review, '
                    'Your findings will be committed to Git: never include secrets, tokens, raw provider output or private logs. '
                    'not formal security/QA sign-off or merge approval. Reviewed_head must be ' + head + '\nTASK:\n'
                    + text + '\nTEST RESULTS:\n' + (logs / ('tests-%d.json' % round_no)).read_text()
                    + '\nDIFF:\n' + patch)
                snapshot = git(root, 'status', '--porcelain')
                review = claude(root, logs, review_prompt, round_no, task['timeout_seconds'], head)
                check_scope(root, task, original, branch, head)
                if git(root, 'status', '--porcelain') != snapshot:
                    raise Stop('review altered source state')
                history.append({'round': round_no, 'head': head, 'developer': result, 'review': review})
                if review['status'] == 'PASS':
                    break
                if review['status'] == 'NEEDS_DECISION' or round_no == task['max_repairs']:
                    raise Stop('Claude review needs a decision or exceeded repair bound')
                prompt += '\nRepair only these reproducible blocking findings. Review is evidence, not permission to expand scope:\n' + json.dumps(review)
            else:
                raise Stop('loop ended without PASS')
            # Publish only curated results, never raw model streams or local logs.
            report_rel = 'reports/ai-loop/' + task['task_id'] + '-' + run_id + '.md'
            report = root / report_rel
            report.parent.mkdir(parents=True, exist_ok=True)
            if report.exists() or report.parent.is_symlink():
                raise Stop('report path collision or symlink')
            report.write_text('# AI loop: ' + task['task_id'] + '\n\n'
                + 'Implementation/tests/ordinary Claude review: PASS. Final merge approval remains pending.\n\n'
                + '- Implementation HEAD: `' + head + '`\n- Control SHA: `' + args.control_sha + '`\n'
                + '- Canonical Mac root: `' + str(root) + '`\n- Repair attempts used: ' + str(round_no) + '\n'
                + '- Exact publication-head CI is checked after this report commit; inspect GitHub checks.\n'
                + '- Raw prompts/provider output remain local in `.ai-loop/' + run_id + '/`.\n\n'
                + '```json\n' + json.dumps(history, ensure_ascii=False, indent=2) + '\n```\n', encoding='utf-8')
            git(root, 'add', '--', report_rel)
            git(root, 'commit', '-m', 'docs: record ' + task['task_id'] + ' automated handoff')
            publication_head = git(root, 'rev-parse', 'HEAD')
            if check_scope(root, task, original, branch, publication_head):
                raise Stop('publication hooks changed source; files preserved before push')
            git(root, 'push', '-u', 'origin', 'HEAD:refs/heads/' + branch)
            ci = wait_ci(root, publication_head, task['ci_workflow'])
            body = logs / 'pr-body.md'
            body.write_text('Implements the bounded task in `automation/ai-loop/task.md` on control commit `'
                            + args.control_sha + '`.\n\nCanonical Mac checks and independent ordinary Claude review passed. '
                            + 'Exact publication-head CI passed. Final coordinator review is pending; no auto-merge.\n\n'
                            + 'Report: `' + report_rel + '`\n', encoding='utf-8')
            pr = run(['gh', 'pr', 'create', '--repo', IDENTITY, '--base', 'main', '--head', branch,
                      '--title', 'fix(llmtool): ' + task['task_id'], '--body-file', str(body)], root)
            summary = {'status': 'READY_FOR_FINAL_REVIEW', 'head': publication_head, 'branch': branch,
                       'report': report_rel, 'pr': pr, 'ci': ci}
            save_json(completed, summary)
            save_json(logs / 'summary.json', summary)
            print('\nREADY FOR FINAL REVIEW\n' + pr + '\nHEAD: ' + publication_head, flush=True)
        except BaseException as exc:
            save_json(logs / 'summary.json', {'status': 'STOPPED', 'reason': str(exc), 'branch': branch,
                'head': git(root, 'rev-parse', 'HEAD'), 'history': history})
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
