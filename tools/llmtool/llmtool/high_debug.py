"""One-shot read-only Codex diagnosis for a registered Executor D6."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

from .errors import LLMToolError
from . import task_completion, worker_dispatch
from .test_output_excerpts import _emitted_sensitive

REPORT_SECTIONS = ('Identity and source', 'Symptom and reproduction', 'Root Cause',
                   'Exactly what to modify', 'Verification', 'Risks and handoff',
                   'Outcome/cost')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_SESSION_READ_LIMIT = 2 * 1024 * 1024


def _thread_id_from_events(events_raw):
    thread_id = None
    for line in events_raw.decode('utf-8', 'replace').splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if type(event) is dict and event.get('type') == 'thread.started':
            candidate = event.get('thread_id')
            if type(candidate) is str and candidate:
                if thread_id is not None and thread_id != candidate:
                    return None
                thread_id = candidate
    return thread_id


def _usage_from_events(events_raw):
    last_usage = None
    for line in events_raw.decode('utf-8', 'replace').splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if type(event) is dict and event.get('type') == 'turn.completed':
            last_usage = event.get('usage')
    if type(last_usage) is not dict:
        return 'UNKNOWN'
    fields = {
        'input': last_usage.get('input_tokens'),
        'cached': last_usage.get('cached_input_tokens'),
        'output': last_usage.get('output_tokens'),
        'reasoning': last_usage.get('reasoning_output_tokens'),
    }
    if any(type(value) is not int or value < 0 for value in fields.values()):
        return 'UNKNOWN'
    return fields


def _session_observed(thread_id, sessions_dir=None):
    """Read only model/effort metadata from bounded, regular rollout files."""
    if type(thread_id) is not str or not thread_id:
        return 'UNKNOWN', 'UNKNOWN'
    root = Path(sessions_dir) if sessions_dir is not None else Path.home() / '.codex' / 'sessions'
    try:
        root_stat = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
            return 'UNKNOWN', 'UNKNOWN'
    except OSError:
        return 'UNKNOWN', 'UNKNOWN'

    models, efforts = set(), set()
    matched = False
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs
                       if not (Path(current) / name).is_symlink()]
            for name in files:
                if not (name.startswith('rollout-') and
                        name.endswith('-' + thread_id + '.jsonl')):
                    continue
                path = Path(current) / name
                try:
                    if path.is_symlink():
                        return 'UNKNOWN', 'UNKNOWN'
                    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    try:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_size > _SESSION_READ_LIMIT:
                            return 'UNKNOWN', 'UNKNOWN'
                        chunks = []
                        remaining = _SESSION_READ_LIMIT + 1
                        while remaining:
                            chunk = os.read(fd, min(65536, remaining))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            remaining -= len(chunk)
                        raw = b''.join(chunks)
                    finally:
                        os.close(fd)
                except OSError:
                    return 'UNKNOWN', 'UNKNOWN'
                if len(raw) > _SESSION_READ_LIMIT:
                    return 'UNKNOWN', 'UNKNOWN'
                matched = True
                for line in raw.decode('utf-8', 'replace').splitlines():
                    try:
                        record = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if type(record) is not dict:
                        continue
                    containers = [record]
                    if type(record.get('payload')) is dict:
                        containers.append(record['payload'])
                    for container in containers:
                        model = container.get('model')
                        effort = container.get('effort', container.get('reasoning_effort'))
                        if type(model) is str:
                            models.add(model)
                        if type(effort) is str:
                            efforts.add(effort)
    except OSError:
        return 'UNKNOWN', 'UNKNOWN'
    if not matched:
        return 'UNKNOWN', 'UNKNOWN'
    return (next(iter(models)) if len(models) == 1 else 'UNKNOWN',
            next(iter(efforts)) if len(efforts) == 1 else 'UNKNOWN')


class _AttemptFailed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _refuse(code):
    error = LLMToolError('High-debug diagnosis refused; details omitted')
    error.high_debug_code = code
    raise error from None


def _create_d6(*, task_id, base_sha, repo_root, executor_task):
    lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    result = subprocess.run([sys.executable, '-B', str(lifecycle), '--task-id', task_id,
        '--agent-id', 'codex-high-debug-gpt-6.1-sol', '--session-ref', 'pending-' + task_id,
        '--lease-ref', f'high-debug {executor_task} at {base_sha}', '--branch',
        'llmtool/high-debug-' + task_id, '--base-sha', base_sha, '--repo-root', str(repo_root)],
        cwd=repo_root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, timeout=180)
    marker = ('root=',)
    for field in result.stdout.decode('utf-8', 'replace').split():
        if field.startswith(marker[0]): return field[5:]
    _refuse('HIGH_DEBUG_D6_CREATE_FAILED')


# The prompt carries untrusted evidence (for example CI logs) and live web
# search is on, so the child gets the same isolation as the Codex Developer
# route: an environment allowlist, no user config or rules, no inherited shell
# environment and no extra features. Session rollouts stay on (no
# --ephemeral) because the observed model and effort are read from them.
_CODEX_SETTINGS = (
    'model_reasoning_effort=high', 'web_search="live"',
    'features.multi_agent=false', 'features.hooks=false', 'features.apps=false',
    'features.memories=false', 'features.remote_plugin=false',
    'features.code_mode.enabled=false', 'shell_environment_policy.inherit="none"',
    'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"',
    'allow_login_shell=false',
)


def _run_codex(*, prompt, cwd, output_path):
    from . import codex_direct_developer
    argv = ['codex', 'exec', '--ignore-user-config', '--ignore-rules',
            '-m', 'gpt-6.1-sol']
    for setting in _CODEX_SETTINGS + (
            'shell_environment_policy.set.PATH=' +
            json.dumps(codex_direct_developer._command_path(cwd)),):
        argv.extend(('-c', setting))
    argv.extend(('-s', 'read-only', '--json', '-o', output_path, '-'))
    with tempfile.TemporaryDirectory(prefix='llmtool-high-debug-tmp-') as tmpdir:
        return subprocess.run(argv, input=prompt.encode('utf-8'), cwd=cwd,
            env=codex_direct_developer._environment(tmpdir), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=1800)


def _d6_taken(repo_root, common_dir, d6):
    """True when a D6 id is already used (its branch or registry record exists).

    A failed create can leave either behind, so the next attempt skips that
    id. An unanswerable probe counts as taken.
    """
    try:
        branch = subprocess.run(['git', '-C', str(repo_root), 'show-ref', '--verify',
                                 '--quiet', 'refs/heads/llmtool/high-debug-' + d6],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False, timeout=60)
        if branch.returncode != 1:
            return True
        record = Path(common_dir) / 'llmtool-worktrees' / (d6 + '.json')
        return record.exists() or record.is_symlink()
    except Exception:
        return True


_D6_NAME_PROBES = 16


def _failed_entry(*, family, failed_corrections, early_reason, head, d6, code,
                  observed, effort, usage, recorded_at):
    """The high_debug entry recording one Codex attempt that did not finish."""
    return {'family': family, 'failed_corrections': failed_corrections,
            'early_reason': early_reason, 'examined_sha': head, 'd6': d6,
            'model_requested': 'gpt-6.1-sol', 'model_observed': observed,
            'effort_observed': effort, 'status': 'RUN_FAILED',
            'report_sha256': hashlib.sha256(code.encode()).hexdigest(),
            'report': code, 'usage': usage, 'recorded_at': recorded_at}


def _d6_name(executor_task, attempt, head):
    """A D6 id unique per attempt that fits the lifecycle's 96-character id."""
    prefix = executor_task
    if len(prefix) > 72:
        digest = hashlib.sha256(executor_task.encode('utf-8')).hexdigest()[:8]
        prefix = executor_task[:63].rstrip('-') + '-' + digest
    return f'{prefix}-hd{attempt}-{head[:7]}'


def _has_report_section(report, heading):
    for line in report.splitlines():
        line = line.strip()
        if (line == '## ' + heading or line == '**' + heading + '**' or
                line == '**' + heading + ':**' or line == '**' + heading + '**:'):
            return True
    return False


_STATUS_WORDS = ('RCA_CONFIRMED', 'RCA_UNCONFIRMED', 'BLOCKED')


def _outcome_status(report):
    """The status word on its own line in the Outcome/cost section, else REPORT_INVALID."""
    in_outcome = False
    found = []
    for line in report.splitlines():
        stripped = line.strip()
        if _has_report_section(stripped, 'Outcome/cost'):
            in_outcome = True
            continue
        if in_outcome and (stripped.startswith('## ') or
                           any(_has_report_section(stripped, h) for h in REPORT_SECTIONS)):
            in_outcome = False
        word = stripped.strip('*').strip()
        if in_outcome and word in _STATUS_WORDS:
            found.append(word)
    return found[0] if len(found) == 1 else 'REPORT_INVALID'


def run_high_debug(*, store, task_id, executor_task, pr_number, family,
                   failed_corrections=None, early_reason=None, evidence=None,
                   runner=None, create_d6=None, repo_root=None,
                   read_registration=None, sessions_dir=None):
    task = store.get(task_id)
    if type(task) is not dict or task.get('task_id') != task_id:
        _refuse('HIGH_DEBUG_TASK_INVALID')
    if (type(executor_task) is not str or
            re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}', executor_task) is None or
            type(pr_number) is not int or not 1 <= pr_number <= 9999999 or
            type(family) is not str or re.fullmatch(r'[a-z0-9._-]{1,64}', family) is None):
        _refuse('HIGH_DEBUG_ARGUMENT_INVALID')
    if ((type(failed_corrections) is int and failed_corrections >= 2 and early_reason is None) or
        (failed_corrections is None and type(early_reason) is str and 1 <= len(early_reason) <= 300)):
        pass
    else:
        _refuse('HIGH_DEBUG_ARGUMENT_INVALID')
    if failed_corrections is not None:
        claims = task.get('review_repair_claims', [])
        if type(claims) is not list:
            _refuse('HIGH_DEBUG_ARGUMENT_INVALID')
        family_claims = [claim for claim in claims
                         if type(claim) is dict and
                         claim.get('family', f"pr-{claim.get('pr')}") == family]
        if len(family_claims) < failed_corrections:
            _refuse('HIGH_DEBUG_ARGUMENT_INVALID')
    if evidence is not None:
        try:
            with open(evidence, 'rb') as evidence_stream:
                raw = evidence_stream.read(8193)
            if len(raw) > 8192: _refuse('HIGH_DEBUG_EVIDENCE_INVALID')
            evidence_text = raw.decode('utf-8')
            if _emitted_sensitive(evidence_text): _refuse('HIGH_DEBUG_EVIDENCE_SENSITIVE')
        except LLMToolError: raise
        except Exception: _refuse('HIGH_DEBUG_EVIDENCE_INVALID')
    else: evidence_text = ''
    policy = worker_dispatch.evaluate('codex-developer', None, task_id)
    if not policy.allowed:
        _refuse('HIGH_DEBUG_DISPATCH_REFUSED')
    completion = task.get('completion')
    ev = completion.get('evidence') if type(completion) is dict else None
    if type(ev) is not dict or completion.get('executor_task_id') != executor_task or type(ev.get('common_dir')) is not str:
        _refuse('HIGH_DEBUG_D6_UNAVAILABLE')
    try:
        reader = read_registration or task_completion.read_d6_registration
        registration = reader(executor_task, ev['common_dir'],
            allowed_states=task_completion._D6_EVIDENCE_STATES)
        head = registration.result_sha
        snap = task_completion.collect_git_snapshot(registration.root)
    except Exception:
        _refuse('HIGH_DEBUG_D6_UNAVAILABLE')
    if not isinstance(head, str) or _HEX40.fullmatch(head) is None or snap.head != head:
        _refuse('HIGH_DEBUG_HEAD_MISMATCH')
    root = str(registration.root)
    entries = task.get('high_debug', [])
    if type(entries) is not list or len(entries) >= 8: _refuse('HIGH_DEBUG_LIMIT')
    repo_root = repo_root or str(Path(registration.common_dir).parent)
    # Attempts are numbered by recorded entries; ids left behind by an earlier
    # failed create are skipped, so a broken create costs no entry.
    for attempt in range(len(entries) + 1, len(entries) + 1 + _D6_NAME_PROBES):
        d6 = _d6_name(executor_task, attempt, head)
        if not _d6_taken(repo_root, registration.common_dir, d6):
            break
    else:
        _refuse('HIGH_DEBUG_D6_CREATE_FAILED')

    def failed(code, observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN'):
        # A Codex attempt that started but did not finish is recorded: it
        # counts toward the entry limit and keeps its D6 traceable. Recording
        # is best effort: the original refusal code is what the caller sees.
        try:
            store.append_high_debug(task_id, _failed_entry(
                family=family, failed_corrections=failed_corrections,
                early_reason=early_reason, head=head, d6=d6, code=code,
                observed=observed, effort=effort, usage=usage,
                recorded_at=store._completion_now()))
        except Exception:
            pass
        _refuse(code)

    create = create_d6 or _create_d6
    try: diagnosis_root = create(task_id=d6, base_sha=head, repo_root=repo_root, executor_task=executor_task)
    except Exception: _refuse('HIGH_DEBUG_D6_CREATE_FAILED')
    reason = f'failed corrections: {failed_corrections}' if failed_corrections is not None else f'early reason: {early_reason}'
    prompt = (f'Task: {task_id}\nPR: {pr_number}\nFamily: {family}\n{reason}\nExamined SHA: {head}\n'
              f'Evidence:\n{evidence_text}\n\nRequired report sections, each exactly as its own line:\n'
              + '\n'.join('## ' + section for section in REPORT_SECTIONS) + '\n'
              'Include exactly one of RCA_CONFIRMED, RCA_UNCONFIRMED, BLOCKED. Diagnose only; do not edit files.')
    report = ''; observed = 'UNKNOWN'; effort = 'UNKNOWN'; usage = 'UNKNOWN'
    failure = None
    output_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix='llmtool-high-debug-', delete=False) as stream:
            output_path = stream.name
        result = (runner or _run_codex)(prompt=prompt, cwd=diagnosis_root or d6, output_path=output_path)
        if result.returncode != 0: raise _AttemptFailed('HIGH_DEBUG_RUN_FAILED')
        events_raw = result.stdout
        if len(events_raw) > 1024 * 1024 or len(result.stderr) > 65536:
            raise _AttemptFailed('HIGH_DEBUG_RUN_FAILED')
        raw = Path(output_path).read_bytes()
        thread_id = _thread_id_from_events(events_raw)
        observed, effort = _session_observed(thread_id, sessions_dir)
        usage = _usage_from_events(events_raw)
        report = raw.decode('utf-8')
        status_result = subprocess.run(['git', 'status', '--porcelain'], cwd=diagnosis_root or d6,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=60)
        if status_result.returncode != 0 or status_result.stdout:
            raise _AttemptFailed('HIGH_DEBUG_WROTE_FILES')
    except _AttemptFailed as error: failure = error.code
    except Exception: failure = 'HIGH_DEBUG_RUN_FAILED'
    finally:
        try: os.unlink(output_path)
        except Exception: pass
    if failure is not None: failed(failure, observed, effort, usage)
    if len(report.encode('utf-8')) > 16384 or _emitted_sensitive(report):
        failed('HIGH_DEBUG_REPORT_INVALID', observed, effort, usage)
    status=_outcome_status(report)
    if any(not _has_report_section(report, h) for h in REPORT_SECTIONS): status='REPORT_INVALID'
    entry={'family':family, 'failed_corrections':failed_corrections, 'early_reason':early_reason,
       'examined_sha':head,'d6':d6,'model_requested':'gpt-6.1-sol','model_observed':observed,
       'effort_observed':effort,'status':status,'report_sha256':hashlib.sha256(report.encode()).hexdigest(),
       'report':report,'usage':usage,'recorded_at':store._completion_now()}
    store.append_high_debug(task_id, entry)
    payload=entry['report_sha256'].encode()
    event_id=hashlib.sha256(('HIGH_DEBUG\0'+task_id+'\0'+head+'\0'+entry['report_sha256']).encode()).hexdigest()[:32]
    result=store.record_completion_event(task_id,event_id=event_id,run_id=task['run_id'],
       session_id='high-debug-'+d6,source='HIGH_DEBUG',source_sha=head,sequence=len(entries),
       payload_sha256=hashlib.sha256(payload).hexdigest())
    print(json.dumps({'state':'RECORDED','status':status,'d6':d6,'report_sha256':entry['report_sha256'],'event_id':event_id},sort_keys=True))
