"""One-shot, cross-family formal Reviewer dispatch for a completed Task."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from .errors import LLMToolError
from . import github_identity, role_config, task_completion, worker_dispatch


_CODEX_REVIEW_MODEL = 'gpt-6-luna'
_CODEX_REVIEW_EFFORT = 'high'
_CODEX_REVIEW_TIMEOUT = 600
_CODEX_REVIEW_MAX_OUTPUT = 1024 * 1024

# Fixed names of the check that refused a finished Codex run's output. They are
# the only failure text a claim stores: never review text, model output or a path.
# A claim holds one only when the read-only run ended and nothing was posted for it.
# The Reviewer D6 create names mark a run that never started, so nothing was posted.
_D6_CREATE_FAILURE_REASONS = frozenset((
    'REVIEWER_D6_CREATE_FAILED', 'REVIEWER_D6_CREATE_TIMEOUT'))
REVIEW_FAILURE_REASONS = _D6_CREATE_FAILURE_REASONS | frozenset((
    'OUTPUT_UNREADABLE', 'OUTPUT_NOT_JSONL', 'THREAD_ID_INVALID', 'MESSAGE_NOT_TEXT',
    'NO_FINAL_MESSAGE', 'NO_THREAD_ID', 'VERDICT_NOT_JSON', 'VERDICT_WRONG_FIELDS',
    'VERDICT_BINDING_MISMATCH', 'VERDICT_STATUS_INVALID', 'VERDICT_TEXT_INVALID',
    'REVIEW_HEADER_MISMATCH', 'REVIEW_BINDING_LINE_MISSING',
    'REVIEW_CLASSIFIER_REJECTED',
    # The review prompt was refused before any Reviewer session started.
    'REVIEW_PROMPT_REFUSED'))
REVIEW_OUTPUT_WITHHELD = 'REVIEW_OUTPUT_WITHHELD'
REVIEW_OUTPUT_ABSENT = 'REVIEW_OUTPUT_ABSENT'
_REVIEW_REFUSALS_DIR = 'review-refusals'
_RECEIPT_NAME_RE = re.compile(r'^[0-9a-zA-Z._-]+-[0-9a-zA-Z._-]+-[0-9]+\.json\Z')
_RECEIPT_PRUNE_LIMIT = 64


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _refuse(code, message='Reviewer dispatch refused; details omitted', *, reason=None,
            mismatch_keys=None, excerpt=None):
    error = LLMToolError(message)
    error.review_dispatch_code = code
    if reason is not None:
        error.review_failure_reason = reason
    if mismatch_keys is not None:
        # Fixed JSON key names only; never a returned or expected value.
        error.review_binding_mismatch_keys = tuple(mismatch_keys)
    if excerpt is not None:
        error.review_excerpt = excerpt
    raise error from None


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _policy(task, reviewer_key):
    developer = task.get('developer') if type(task) is dict else None
    assignment = (developer.get('owner_assignment_ref')
                  if type(developer) is dict else None)
    return worker_dispatch.evaluate(reviewer_key, assignment, task.get('task_id'))


def _select_reviewer(task, requested=None):
    """Choose the default or explicit Reviewer and enforce family separation."""
    developer = task.get('developer') if type(task) is dict else None
    family = task_completion._reviewer_author_family(developer)
    if family is None:
        _refuse('REVIEWER_FAMILY_UNSUPPORTED')
    reviewer_key = requested
    if reviewer_key is None:
        reviewer_key = 'codex-reviewer' if family == 'claude' else 'claude-reviewer'
    if reviewer_key not in ('claude-reviewer', 'codex-reviewer'):
        _refuse('REVIEWER_INVALID')
    reviewer_family = 'claude' if reviewer_key == 'claude-reviewer' else 'codex'
    if reviewer_family == family:
        _refuse('REVIEWER_SAME_FAMILY')
    policy = _policy(task, reviewer_key)
    if not policy.allowed:
        _refuse('REVIEWER_NOT_ALLOWED')
    return reviewer_key


def _resolve_github_login():
    """Resolve the authenticated GitHub account without exposing credentials."""
    gh = shutil.which('gh', path=task_completion._GH_PATH)
    if gh is None:
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    home = os.environ.get('HOME') or os.path.expanduser('~')
    env = {'PATH': task_completion._GH_PATH, 'HOME': home,
           'LC_ALL': 'C', 'GH_PROMPT_DISABLED': '1',
           'GH_NO_UPDATE_NOTIFIER': '1', 'NO_COLOR': '1',
           'GH_CONFIG_DIR': github_identity.ai_gh_config_dir(home)}
    try:
        result = subprocess.run(
            [gh, 'api', 'user', '--jq', '.login'], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    try:
        login = result.stdout.decode('utf-8').strip()
    except UnicodeDecodeError:
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    if (result.returncode or re.fullmatch(r'[A-Za-z0-9-]{1,39}', login) is None):
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    if login != github_identity.ai_login():
        _refuse('REVIEWER_GITHUB_LOGIN_MISMATCH')
    return login


def _preflight(store, task_id, executor_task, pr_number, version, *, task=None,
               reviewer=None):
    if task is None:
        task = store.get(task_id)
    if (type(task.get('developer')) is dict and
            task['developer'].get('provider_profile') == 'claude' and
            task.get('abandonment') is not None):
        _refuse('CLAUDE_TASK_ABANDONED')
    # Reviewer family/dispatch refusals precede binding, D6, and PR checks;
    # dispatch-policy details are intentionally normalized to REVIEWER_NOT_ALLOWED.
    reviewer_key = _select_reviewer(task, reviewer)
    binding = task.get('task_binding')
    try:
        task_completion.validate_task_binding(
            stored_binding=binding, task_id=task.get('task_id'), run_id=task.get('run_id'),
            contract_digest=binding.get('contract_digest') if type(binding) is dict else None)
    except LLMToolError:
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    completion = task.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    if (type(evidence) is not dict or completion.get('executor_task_id') != executor_task or
            type(evidence.get('common_dir')) is not str):
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    try:
        executor = task_completion.read_d6_registration(
            executor_task, evidence['common_dir'], allowed_states=task_completion._D6_EVIDENCE_STATES)
        head = executor.result_sha
        snap = task_completion.collect_git_snapshot(executor.root)
        if not snap.clean:
            _refuse('REVIEW_PROMPT_EXECUTOR_DIRTY')
        recorded = evidence.get('result')
        if (type(head) is not str or re.fullmatch(r'[0-9a-f]{40}', head) is None or
                snap.head != head or snap.root != executor.root or
                snap.common_dir != executor.common_dir or snap.branch != executor.branch):
            _refuse('REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        # task_submission_cli loads this module lazily and owns the repair chain.
        from .task_submission_cli import result_head_admitted
        if type(recorded) is dict and not result_head_admitted(
                task, store, evidence, executor_task=executor_task, root=executor.root,
                recorded_head=recorded.get('result_head'), head=head):
            _refuse('REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
    except LLMToolError as exc:
        if getattr(exc, 'review_dispatch_code', None):
            raise
        _refuse('REVIEW_PROMPT_D6_UNAVAILABLE')
    except Exception:
        _refuse('REVIEW_PROMPT_D6_UNAVAILABLE')
    try:
        pull = task_completion._github_get(f'pulls/{pr_number}')
    except LLMToolError:
        _refuse('REVIEW_PROMPT_PR_UNAVAILABLE')
    if (type(pull) is not dict or type(pull.get('number')) is not int or
            pull['number'] != pr_number or type(pull.get('head')) is not dict or
            pull['head'].get('sha') != head):
        _refuse('REVIEW_PROMPT_PR_HEAD_MISMATCH')
    base = pull.get('base')
    if (type(base) is not dict or type(base.get('sha')) is not str or
            re.fullmatch(r'[0-9a-f]{40}', base['sha']) is None):
        _refuse('REVIEW_PROMPT_PR_UNAVAILABLE')
    if type(version) is not int or not 1 <= version <= 9999:
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    return task, executor, head, base['sha'], reviewer_key


def _create_d6(*, task_id, base_sha, repo_root, executor_task, github_login,
               reviewer_key='claude-reviewer'):
    lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    lease = f'read-only formal review of executor task {executor_task} at {base_sha}'
    args = [sys.executable, '-B', str(lifecycle), '--task-id', task_id,
            '--agent-id', reviewer_key, '--session-ref', 'pending-' + task_id,
            '--lease-ref', lease, '--branch', 'llmtool/reviewer-' + task_id,
            '--base-sha', base_sha, '--github-login', github_login,
            '--repo-root', str(repo_root)]
    # Lifecycle output is never kept or raised; only the fixed name is.
    try:
        result = subprocess.run(args, cwd=str(repo_root), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, timeout=180)
    except subprocess.TimeoutExpired:
        _refuse('REVIEWER_D6_CREATE_TIMEOUT', reason='REVIEWER_D6_CREATE_TIMEOUT')
    except OSError:
        _refuse('REVIEWER_D6_CREATE_FAILED', reason='REVIEWER_D6_CREATE_FAILED')
    if result.returncode:
        _refuse('REVIEWER_D6_CREATE_FAILED', reason='REVIEWER_D6_CREATE_FAILED')


def _codex_argv(executable, reviewer_root, *, model=_CODEX_REVIEW_MODEL,
                effort=_CODEX_REVIEW_EFFORT):
    return [executable, '--no-daemon', '--ask-for-approval', 'never', 'exec',
            '--ignore-user-config', '--ignore-rules', '--ephemeral',
            '--strict-config', '--color', 'never', '--json', '--model',
            model, '-c', 'model_reasoning_effort=' + effort,
            '-s', 'read-only', '--cd', str(reviewer_root), '-']


def _parse_codex_review_inner(raw, *, task_id, run_id, contract_digest, head, version=1):
    """Accept one exact, binding-complete Codex review object from JSONL."""
    if type(raw) is not bytes or len(raw) > _CODEX_REVIEW_MAX_OUTPUT:
        _refuse('REVIEW_NOT_POSTED', reason='OUTPUT_UNREADABLE')
    final = None
    thread_id = None
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line, object_pairs_hook=_unique_json_object)
            if type(event) is dict and event.get('type') == 'thread.started':
                current = event.get('thread_id')
                if (thread_id is not None or type(current) is not str or
                        re.fullmatch(r'[0-9a-fA-F-]{16,64}', current) is None):
                    _refuse('REVIEW_NOT_POSTED', reason='THREAD_ID_INVALID')
                thread_id = current
            elif type(event) is dict and event.get('type') == 'item.completed':
                item = event.get('item')
                if type(item) is dict and item.get('type') == 'agent_message':
                    if type(item.get('text')) is not str:
                        _refuse('REVIEW_NOT_POSTED', reason='MESSAGE_NOT_TEXT')
                    final = item['text']
    except LLMToolError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        _refuse('REVIEW_NOT_POSTED', reason='OUTPUT_NOT_JSONL')
    if final is None:
        _refuse('REVIEW_NOT_POSTED', reason='NO_FINAL_MESSAGE')
    if thread_id is None:
        _refuse('REVIEW_NOT_POSTED', reason='NO_THREAD_ID')
    try:
        verdict = json.loads(final, object_pairs_hook=_unique_json_object)
    except (ValueError, TypeError, RecursionError):
        _refuse('REVIEW_NOT_POSTED', reason='VERDICT_NOT_JSON')
    # The contract digest is bound only by the exact binding line in the review
    # text. An older reply may still carry contract_digest; its value is ignored.
    expected = {'task_id', 'run_id', 'head', 'verdict', 'review'}
    if type(verdict) is not dict or set(verdict) - {'contract_digest'} != expected:
        _refuse('REVIEW_NOT_POSTED', reason='VERDICT_WRONG_FIELDS')
    mismatched = [key for key, value in
                  (('task_id', task_id), ('run_id', run_id), ('head', head))
                  if verdict[key] != value]
    if mismatched:
        _refuse('REVIEW_NOT_POSTED',
                'Reviewer dispatch refused: JSON binding keys differ: ' + ', '.join(mismatched),
                reason='VERDICT_BINDING_MISMATCH', mismatch_keys=mismatched)
    if (type(verdict.get('verdict')) is not str or
            verdict['verdict'] not in ('PASS', 'FIX', 'FAIL', 'BLOCKED')):
        _refuse('REVIEW_NOT_POSTED', reason='VERDICT_STATUS_INVALID')
    if (type(verdict.get('review')) is not str or
            len(verdict['review'].encode('utf-8', 'replace')) > 65536):
        _refuse('REVIEW_NOT_POSTED', reason='VERDICT_TEXT_INVALID')
    lines = [line for line in verdict['review'].splitlines() if line.strip()]
    status = verdict['verdict']
    displayed_status = 'PASS' if status == 'PASS' else 'FAIL'
    marker_status = 'PASS' if status == 'PASS' else 'FIX'
    expected_marker = (f'<!-- activity: type=review_verdict actor=Codex target=PM '
                       f'status={marker_status} -->')
    expected_status = (f'Verdict: {displayed_status} | Version: v{version} | HEAD: {head}')
    binding_line = (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
                    f'contract={contract_digest} head={head}')
    if len(lines) < 3 or lines[0] != expected_marker or lines[1] != expected_status:
        _refuse('REVIEW_NOT_POSTED', reason='REVIEW_HEADER_MISMATCH')
    if binding_line not in lines:
        _refuse('REVIEW_NOT_POSTED', reason='REVIEW_BINDING_LINE_MISSING')
    # Use the persisted-result classifier as the authority for the body shape.
    # This keeps direct Codex results and later comment classification in parity.
    from . import review_result
    classified = review_result._comment_verdict(
        {'id': 0, 'body': verdict['review'],
         'created_at': '2000-01-01T00:00:00Z',
         'updated_at': '2000-01-01T00:00:00Z'},
        head=head, version=version,
        task={'task_id': task_id, 'run_id': run_id,
              'task_binding': {'contract_digest': contract_digest},
              'developer': {'provider_profile': 'claude'}})
    expected_outcome = 'REVIEW_PASS' if status == 'PASS' else 'REVIEW_FAIL'
    if classified is None or classified[2] != expected_outcome:
        _refuse('REVIEW_NOT_POSTED', reason='REVIEW_CLASSIFIER_REJECTED')
    return {'thread_id': thread_id, 'review_text': verdict['review'], 'verdict': status}


def _codex_final_message(raw):
    """Return the last complete Codex agent message when its JSONL is readable."""
    if type(raw) is not bytes or len(raw) > _CODEX_REVIEW_MAX_OUTPUT:
        return None
    final = None
    try:
        for line in raw.decode('utf-8').splitlines():
            if not line:
                continue
            event = json.loads(line, object_pairs_hook=_unique_json_object)
            item = event.get('item') if type(event) is dict else None
            if (type(event) is dict and event.get('type') == 'item.completed' and
                    type(item) is dict and item.get('type') == 'agent_message' and
                    type(item.get('text')) is str):
                final = item['text']
    except (UnicodeError, ValueError, RecursionError):
        return final
    return final


def _parse_codex_review(raw, *, task_id, run_id, contract_digest, head, version=1):
    """Parse a bound review and attach only a screened excerpt to refusals."""
    try:
        return _parse_codex_review_inner(
            raw, task_id=task_id, run_id=run_id,
            contract_digest=contract_digest, head=head, version=version)
    except LLMToolError as exc:
        if getattr(exc, 'review_dispatch_code', None) != 'REVIEW_NOT_POSTED':
            raise
        reason = getattr(exc, 'review_failure_reason', None)
        if reason == 'VERDICT_BINDING_MISMATCH':
            # The reply holds the returned values, so no excerpt is added.
            raise
        final = _codex_final_message(raw)
        if final is None or not final.strip():
            _refuse('REVIEW_NOT_POSTED', reason=reason, excerpt=REVIEW_OUTPUT_ABSENT)
        from .task_submission_cli import _screen_text
        try:
            _screen_text(final, _CODEX_REVIEW_MAX_OUTPUT)
        except LLMToolError:
            _refuse('REVIEW_NOT_POSTED', reason=reason, excerpt=REVIEW_OUTPUT_WITHHELD)
        cut = '\n'.join(final.splitlines()[:40]).encode('utf-8')[:2048].decode('utf-8', 'ignore')
        try:
            excerpt = _screen_text(cut, 2048)
        except LLMToolError:
            _refuse('REVIEW_NOT_POSTED', reason=reason, excerpt=REVIEW_OUTPUT_WITHHELD)
        _refuse('REVIEW_NOT_POSTED', 'Reviewer dispatch refused: ' + excerpt,
                reason=reason, excerpt=excerpt)


def _review_return(claim, result):
    """Return a Codex verdict with review text screened at the CLI boundary."""
    output = dict(claim)
    output.pop('review_result', None)
    if result.get('verdict') == 'BLOCKED' and result.get('review_text') == '':
        output['verdict'] = 'BLOCKED'
        output['review_dispatch_code'] = 'REVIEW_TEXT_WITHHELD'
        return output
    output['verdict'] = result['verdict']
    try:
        from .task_submission_cli import _screen_text
        output['review_text'] = _screen_text(result['review_text'], 65536)
    except LLMToolError:
        output['verdict'] = 'BLOCKED'
        output['review_dispatch_code'] = 'REVIEW_TEXT_WITHHELD'
    return output


_CODEX_QUOTA_FILE = Path('.local') / 'state' / 'llmtool' / 'codex-quota.json'
_CODEX_QUOTA_MAX_SIZE = 4096


def _codex_quota_path():
    home = os.environ.get('HOME') or os.path.expanduser('~')
    return Path(home) / _CODEX_QUOTA_FILE


def _extract_codex_quota(raw_stdout):
    """Scan codex exec JSON event output for the newest rate_limits object."""
    if type(raw_stdout) is not bytes:
        return None
    newest = None
    seen = False
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_json_object)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        candidate = None
        if 'rate_limits' in event:
            candidate = event['rate_limits']
        elif event.get('type') == 'rate_limits':
            candidate = event
        elif isinstance(event.get('item'), dict):
            item = event['item']
            if 'rate_limits' in item:
                candidate = item['rate_limits']
            elif item.get('type') == 'rate_limits':
                candidate = item
        if candidate is not None:
            newest = candidate
            seen = True
    if not seen or not isinstance(newest, dict):
        return None

    cleaned = {}
    for section in ('primary', 'secondary'):
        sub = newest.get(section)
        if isinstance(sub, dict):
            clean_sub = {}
            for key in ('used_percent', 'window_minutes', 'resets_at'):
                val = sub.get(key)
                if (type(val) in (int, float) and
                        not isinstance(val, bool) and
                        math.isfinite(val)):
                    clean_sub[key] = val
            if clean_sub:
                cleaned[section] = clean_sub

    if not cleaned:
        return None

    record = {
        'schema_version': 1,
        'read_time': int(datetime.now(timezone.utc).timestamp()),
    }
    record.update(cleaned)
    return record


def _write_codex_quota(quota_data):
    """Write cleaned codex quota figures atomically to ~/.local/state/llmtool/codex-quota.json."""
    if not isinstance(quota_data, dict):
        return
    quota_path = _codex_quota_path()
    parent = quota_path.parent
    try:
        if os.path.islink(quota_path):
            return
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.path.islink(parent):
            return
        parent_stat = parent.stat()
        if not stat.S_ISDIR(parent_stat.st_mode):
            return
        if hasattr(os, 'getuid') and parent_stat.st_uid != os.getuid():
            return
        if parent.parent.exists() and os.path.islink(parent.parent):
            return
        if parent.parent.parent.exists() and os.path.islink(parent.parent.parent):
            return

        payload = (json.dumps(quota_data, sort_keys=True) + '\n').encode('utf-8')
        if len(payload) > _CODEX_QUOTA_MAX_SIZE:
            return

        temp_name = f'.codex-quota.json.tmp.{os.urandom(8).hex()}'
        temp_path = parent / temp_name
        fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        try:
            os.chmod(temp_path, 0o600)
            if os.path.islink(quota_path):
                return
            os.replace(str(temp_path), str(quota_path))
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    except Exception:
        return


def _record_codex_quota(raw_stdout):
    try:
        quota = _extract_codex_quota(raw_stdout)
        if quota is not None:
            _write_codex_quota(quota)
    except Exception:
        pass


def _dispatch_codex(*, reviewer_root, prompt, task, head, version=1,
                    model=_CODEX_REVIEW_MODEL, effort=_CODEX_REVIEW_EFFORT,
                    config_dir=None):
    executable = shutil.which('codex')
    if executable is None:
        _refuse('REVIEWER_RUNTIME_UNAVAILABLE')
    try:
        env = {'PATH': os.pathsep.join((str(Path(executable).parent), '/usr/bin', '/bin')),
               'HOME': os.path.expanduser('~'), 'LC_ALL': 'C', 'NO_COLOR': '1',
               **({'CODEX_HOME': config_dir} if config_dir is not None else {})}
        home = env.get('HOME') or os.path.expanduser('~')
        env = github_identity.ai_github_env(env, home)
        result = subprocess.run(
            _codex_argv(executable, reviewer_root, model=model, effort=effort),
            cwd=str(reviewer_root),
            input=prompt.encode('utf-8'), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=_CODEX_REVIEW_TIMEOUT)
    except subprocess.TimeoutExpired:
        _refuse('REVIEWER_TIMEOUT')
    except OSError:
        _refuse('REVIEWER_RUNTIME_UNAVAILABLE')
    if result.returncode:
        raw_stderr = result.stderr[:65536] if type(result.stderr) is bytes else b''
        excerpt = raw_stderr.decode('utf-8', 'replace')
        if excerpt.strip():
            try:
                from .task_submission_cli import _screen_text
                excerpt = _screen_text(excerpt, 65536)
            except LLMToolError:
                excerpt = ''
        message = ('Codex Reviewer exited unsuccessfully; details omitted' if not excerpt else
                   'Codex Reviewer exited unsuccessfully: ' + excerpt)
        _refuse('REVIEW_NOT_POSTED', message)
    binding = task.get('task_binding')
    if type(binding) is not dict:
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    _record_codex_quota(result.stdout)
    return _parse_codex_review(
        result.stdout, task_id=task.get('task_id'), run_id=task.get('run_id'),
        contract_digest=binding.get('contract_digest'),
        head=head, version=version)


def _dispatch(*, task_id, prompt, repo_root, env):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'claude_bg_dispatch.py'
    args = [sys.executable, '-B', str(script), 'dispatch', '--new', '--name',
            'claude-reviewer', '--task-id', task_id, '--prompt', prompt,
            '--repo-root', str(repo_root)]
    result = subprocess.run(args, cwd=str(repo_root), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=660)
    try:
        receipt = json.loads(result.stdout.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError('REVIEWER_DISPATCH_UNKNOWN') from None
    if result.returncode or type(receipt) is not dict or receipt.get('result') != 'DELIVERED':
        raise RuntimeError('REVIEWER_DISPATCH_UNKNOWN')
    session_id = receipt.get('actual_session_id')
    if type(session_id) is not str or not session_id:
        raise RuntimeError('REVIEWER_DISPATCH_UNKNOWN')
    return session_id


def _bind_d6(task_id, session_id, repo_root):
    lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    result = subprocess.run(
        [sys.executable, '-B', str(lifecycle), '--bind-native-session-task-id', task_id,
         '--native-session-id', session_id, '--repo-root', str(repo_root)],
        cwd=str(repo_root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=120)
    if result.returncode:
        raise RuntimeError('REVIEWER_SESSION_BIND_FAILED')


def _prune_receipts(directory, limit=_RECEIPT_PRUNE_LIMIT):
    entries = []
    for entry in os.scandir(directory):
        try:
            if entry.is_file() and not entry.is_symlink() and _RECEIPT_NAME_RE.fullmatch(entry.name):
                stat_info = entry.stat()
                entries.append((stat_info.st_mtime_ns, entry.name, Path(entry.path)))
        except OSError:
            continue
    if len(entries) <= limit:
        return
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _, _, path in entries[limit:]:
        try:
            path.unlink()
        except OSError:
            pass


def _write_refusal_receipt(*, store, task_id, pr_number, head, version,
                           reviewer_task, reason, excerpt_key, excerpt_val):
    root = getattr(store, 'root', None)
    if not isinstance(root, Path):
        return None
    receipt_dir = root.parent / _REVIEW_REFUSALS_DIR
    if receipt_dir.is_symlink():
        return None
    from . import high_debug
    high_debug._private_directory(receipt_dir)
    receipt_path = receipt_dir / f'{task_id}-{head}-{version}.json'
    record = {
        'task_id': task_id,
        'pr': pr_number,
        'head': head,
        'version': version,
        'reviewer_task': reviewer_task,
        'failure_reason': reason,
        'recorded_at': _now(),
        'not_a_verdict': True,
        excerpt_key: excerpt_val,
    }
    data = (json.dumps(record, sort_keys=True) + '\n').encode('utf-8')
    high_debug._create_private(receipt_path, data)
    _prune_receipts(receipt_dir, _RECEIPT_PRUNE_LIMIT)
    return receipt_path


def dispatch_review(*, store, task_id, executor_task, pr_number, version=1,
                    create_d6=None, dispatch_session=None, bind_session=None,
                    prompt_renderer=None, codex_runner=None, reviewer=None):
    """Claim once, create a read-only Reviewer D6 and bind its dispatched session."""
    task = store.get(task_id)
    _, executor, head, _base, reviewer_key = _preflight(
        store, task_id, executor_task, pr_number, version, task=task,
        reviewer=reviewer)
    # Authorization must be checked before a durable claim is created. The
    # policy is re-read again immediately before each external side effect.
    gate = _policy(task, reviewer_key)
    if not gate.allowed:
        _refuse('REVIEWER_NOT_ALLOWED')
    stored_claims = task.get('review_dispatch_claim')
    claims = ([stored_claims] if type(stored_claims) is dict else
              list(stored_claims) if type(stored_claims) is list else [])
    identity = (pr_number, head, version)
    prior = next((claim for claim in claims if
                  (claim.get('pr'), claim.get('head_sha'), claim.get('version')) == identity), None)
    if prior is not None:
        if prior.get('executor_task') != executor_task:
            _refuse('REVIEW_DISPATCH_CLAIM_MISMATCH')
        if prior.get('state') == 'COMPLETED':
            result = prior.get('review_result')
            return _review_return(prior, result)
        if prior['state'] == 'CLAIMED':
            unknown = dict(prior, state='UNKNOWN', updated_at=_now())
            updated_claims = [unknown if claim is prior else claim for claim in claims]
            try:
                saved = store.compare_and_update(
                    task_id, expected={'review_dispatch_claim': stored_claims},
                    review_dispatch_claim=updated_claims)
                return unknown
            except Exception:
                _refuse('REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        return prior
    # Refuse when a stored claim already used this version on a different head.
    used = next((claim for claim in claims if
                 type(claim) is dict and
                 claim.get('executor_task') == executor_task and
                 claim.get('pr') == pr_number and
                 claim.get('version') == version and
                 claim.get('head_sha') != head), None)
    if used is not None:
        pr_versions = [
            claim['version'] for claim in claims
            if type(claim) is dict and
            claim.get('executor_task') == executor_task and
            claim.get('pr') == pr_number and
            type(claim.get('version')) is int
        ]
        next_version = max(pr_versions) + 1
        _refuse(
            'REVIEW_DISPATCH_VERSION_USED',
            f'Review version {version} is already used for PR {pr_number} '
            f'on another head; use version {next_version}.')
    reviewer_role = role_config.resolve_role(
        'reviewer' if reviewer_key == 'claude-reviewer' else 'codex-reviewer')
    config_dir = reviewer_role['config_dir'] if reviewer_role is not None else None
    if (reviewer_key == 'claude-reviewer' and
            (reviewer_role['source'] == 'builtin' or config_dir is None)):
        _refuse('REVIEWER_ACCOUNT_UNCONFIGURED')
    if reviewer_key == 'codex-reviewer':
        if reviewer_role.get('provider') not in (None, 'codex'):
            _refuse('REVIEWER_CONFIG_INVALID')
        codex_model = reviewer_role.get('model') or _CODEX_REVIEW_MODEL
        codex_effort = reviewer_role.get('effort') or _CODEX_REVIEW_EFFORT
        from .codex_direct_developer import validate_profile
        try:
            validate_profile(codex_model, codex_effort)
        except LLMToolError:
            _refuse('REVIEWER_CONFIG_INVALID')
        account_mode = ('ROLES' if reviewer_role['source'] == 'roles' else 'CODEX_CLI')
    else:
        codex_model, codex_effort = _CODEX_REVIEW_MODEL, _CODEX_REVIEW_EFFORT
        account_mode = {
            'roles': 'ROLES', 'env': 'EXPLICIT', 'cli': 'EXPLICIT',
            'pm_runtime': 'LEGACY', 'builtin': 'BUILTIN_DEFAULT',
        }.get(reviewer_role['source'], 'BUILTIN_DEFAULT')
    reviewer_task = 'reviewer-' + os.urandom(10).hex()
    claim = {'executor_task': executor_task, 'pr': pr_number, 'head_sha': head,
             'version': version, 'state': 'CLAIMED', 'reviewer_task': reviewer_task,
             'native_session_id': None, 'account_mode': account_mode,
             'claimed_at': _now(), 'updated_at': _now()}
    if reviewer_role.get('account') is not None:
        claim['account'] = reviewer_role['account']
    try:
        store.compare_and_update(task_id, expected={'record_sha256': task['record_sha256']},
                                 review_dispatch_claim=claims + [claim])
    except Exception:
        _refuse('REVIEW_DISPATCH_CLAIM_UNAVAILABLE')

    repo_root = str(Path(executor.common_dir).parent)
    create = create_d6 or _create_d6
    send = dispatch_session or _dispatch
    bind = bind_session or _bind_d6
    session_id = None
    try:
        # Authorization is deliberately re-read immediately before each side effect.
        gate = _policy(task, reviewer_key)
        if not gate.allowed:
            _refuse(gate.reason_code)
        github_login = _resolve_github_login()
        create(task_id=reviewer_task, base_sha=head, repo_root=repo_root,
               executor_task=executor_task, github_login=github_login,
               reviewer_key=reviewer_key)
        try:
            if prompt_renderer is None:
                from . import task_submission_cli
                args = type('ReviewArgs', (), {'task_id': task_id, 'executor_task': executor_task,
                        'reviewer_task': reviewer_task, 'pr': pr_number, 'version': version})()
                output = io.StringIO()
                with redirect_stdout(output):
                    task_submission_cli.run_task_review_prompt(args, store=store)
                prompt = output.getvalue()
            else:
                prompt = prompt_renderer(task_id, executor_task, reviewer_task, pr_number,
                                         version)
        except LLMToolError as exc:
            # No Reviewer session has started, so the claim may name this fixed
            # reason. Only an upper-case code is kept; the message never is.
            code = next((value for value in (getattr(exc, 'completion_code', None),
                                             getattr(exc, 'review_dispatch_code', None))
                         if type(value) is str and
                         re.fullmatch(r'[A-Z][A-Z0-9_]*', value) is not None),
                        'REVIEW_PROMPT_REFUSED')
            _refuse(code, reason='REVIEW_PROMPT_REFUSED')
        # Detect a moved PR/head before starting the Reviewer session.
        _, _, current_head, _, _ = _preflight(
            store, task_id, executor_task, pr_number, version, reviewer=reviewer_key)
        if current_head != head:
            _refuse('REVIEW_PROMPT_PR_HEAD_MISMATCH')
        gate = _policy(task, reviewer_key)
        if not gate.allowed:
            _refuse(gate.reason_code)
        env = os.environ.copy()
        if config_dir:
            env['CLAUDE_CONFIG_DIR'] = config_dir
        else:
            env.pop('CLAUDE_CONFIG_DIR', None)
        home = env.get('HOME') or os.path.expanduser('~')
        env = github_identity.ai_github_env(env, home)
        if reviewer_key == 'codex-reviewer':
            reviewer = task_completion.read_d6_registration(
                reviewer_task, executor.common_dir,
                allowed_states=task_completion._D6_EVIDENCE_STATES)
            run_codex = codex_runner or _dispatch_codex
            codex_result = run_codex(reviewer_root=reviewer.root, prompt=prompt,
                                     task=task, head=head, version=version,
                                     model=codex_model, effort=codex_effort,
                                     config_dir=config_dir)
            if type(codex_result) is not dict:
                _refuse('REVIEW_NOT_POSTED')
            session_id = codex_result.get('thread_id')
            if type(session_id) is not str or not session_id:
                _refuse('REVIEW_NOT_POSTED')
            bind(task_id=reviewer_task, session_id=session_id, repo_root=repo_root)
        else:
            session_id = send(task_id=reviewer_task, prompt=prompt,
                              repo_root=repo_root, env=env)
            bind(task_id=reviewer_task, session_id=session_id, repo_root=repo_root)
        result_state = 'COMPLETED' if reviewer_key == 'codex-reviewer' else 'DISPATCHED'
        dispatched = dict(claim, state=result_state, reviewer_task=reviewer_task,
                          native_session_id=session_id, updated_at=_now())
        if result_state == 'COMPLETED':
            binding = task.get('task_binding')
            try:
                from .task_submission_cli import _screen_text
                review_text = _screen_text(codex_result['review_text'], 65536)
                verdict = codex_result['verdict']
            except LLMToolError:
                # The claim schema has no separate withheld state. BLOCKED with
                # empty text is its durable, non-PASS withheld representation.
                review_text = ''
                verdict = 'BLOCKED'
            dispatched['review_result'] = {
                'task_id': task_id, 'run_id': task.get('run_id'),
                'contract_digest': binding.get('contract_digest'), 'head': head,
                'verdict': verdict, 'review_text': review_text,
            }
        updated_claims = [dispatched if item is claim else item for item in claims + [claim]]
        store.compare_and_update(task_id, expected={'review_dispatch_claim': claims + [claim]},
                                 review_dispatch_claim=updated_claims)
        if reviewer_key == 'codex-reviewer':
            return _review_return(dispatched, codex_result)
        return dispatched
    except Exception as exc:
        unknown = dict(claim, state='UNKNOWN', updated_at=_now())
        if session_id is not None:
            unknown['native_session_id'] = session_id
        reason = getattr(exc, 'review_failure_reason', None)
        if reason in REVIEW_FAILURE_REASONS:
            unknown['failure_reason'] = reason
        mismatch = getattr(exc, 'review_binding_mismatch_keys', None)
        if reason == 'VERDICT_BINDING_MISMATCH' and type(mismatch) is tuple:
            # Keep only the fixed JSON key names; never a returned or expected value.
            keys = [key for key in ('task_id', 'run_id', 'head') if key in mismatch]
            if keys:
                unknown['binding_mismatch_keys'] = keys
        claim_updated = False
        try:
            claimed = claims + [claim]
            updated_claims = [unknown if item is claim else item for item in claimed]
            store.compare_and_update(task_id, expected={'review_dispatch_claim': claimed},
                                     review_dispatch_claim=updated_claims)
            claim_updated = True
        except Exception:
            pass
        if claim_updated and reason in REVIEW_FAILURE_REASONS:
            raw_excerpt = getattr(exc, 'review_excerpt', None)
            if raw_excerpt in (REVIEW_OUTPUT_WITHHELD, REVIEW_OUTPUT_ABSENT):
                excerpt_key = 'label'
                excerpt_val = raw_excerpt
            elif raw_excerpt is None or not str(raw_excerpt).strip():
                excerpt_key = 'label'
                excerpt_val = REVIEW_OUTPUT_ABSENT
            else:
                excerpt_key = 'excerpt'
                excerpt_val = raw_excerpt
            refusal = {
                'failure_reason': reason,
                excerpt_key: excerpt_val,
                'receipt_path': None,
            }
            try:
                receipt_path = _write_refusal_receipt(
                    store=store, task_id=task_id, pr_number=pr_number, head=head,
                    version=version, reviewer_task=reviewer_task, reason=reason,
                    excerpt_key=excerpt_key, excerpt_val=excerpt_val)
                refusal['receipt_path'] = receipt_path
            except Exception:
                pass
            exc.review_refusal = refusal
        if getattr(exc, 'review_dispatch_code', None):
            raise
        outcome_error = LLMToolError('Reviewer dispatch refused; details omitted')
        outcome_error.review_dispatch_code = 'REVIEW_DISPATCH_OUTCOME_UNKNOWN'
        if hasattr(exc, 'review_refusal'):
            outcome_error.review_refusal = exc.review_refusal
        raise outcome_error from None
