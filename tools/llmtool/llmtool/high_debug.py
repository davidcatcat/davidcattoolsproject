"""One-shot read-only Codex diagnosis for a registered Executor D6."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from .errors import LLMToolError
from . import task_completion, task_store, worker_dispatch
from .test_output_excerpts import (
    _SENSITIVE, _emitted_sensitive, _sensitive_jwt, _sensitive_url,
)

REPORT_SECTIONS = ('Identity and source', 'Symptom and reproduction', 'Root Cause',
                   'Exactly what to modify', 'Verification', 'Risks and handoff',
                   'Outcome/cost')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_SESSION_READ_LIMIT = 2 * 1024 * 1024
_REPORT_LIMIT = 16384
_EVIDENCE_LIMIT = 8192
_RECORD_TOKEN = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
_REFUSAL_CODES = frozenset((
    'HIGH_DEBUG_RUN_FAILED', 'HIGH_DEBUG_WROTE_FILES',
    'HIGH_DEBUG_REPORT_INVALID'))
_SCREEN_HIT_LIMIT = 32
_SCREEN_RULES = (
    ('private_key', re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----', re.I)),
    ('cloud_key', re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', re.I)),
    ('provider_key', re.compile(r'\bsk-[A-Za-z0-9_-]{12,}', re.I)),
    ('git_host_key', re.compile(r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}', re.I)),
    ('chat_key', re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{20,}', re.I)),
    ('auth_scheme', re.compile(r'\b(?:bearer|basic)\s+\S+', re.I)),
    ('keyword_assign', re.compile(
        r'["\']?\b(?:api[_-]?key|password|passwd|token|secret|access[_-]?token|'
        r'client[_-]?secret|aws_secret_access_key)["\']?\s*[:=]', re.I)),
)
_SCREEN_NAME = re.compile(r'[a-z_]+\Z')
_SCREEN_RECORD_LIMIT = 16384
_SCREEN_FAMILY = re.compile(r'[a-z0-9._-]{1,64}\Z')
_SCREEN_TASK_ID = re.compile(r'[a-f0-9]{32}\Z')
_SCREEN_REF = re.compile(r'https://github\.com/[^/\s]+/[^/\s]+/issues/[0-9]+#issuecomment-[0-9]+\Z')


def _record_body(entry, task_id, refusal_code=None):
    """Build a monitor-only comment using recorded metadata, never report text."""
    status = entry.get('status')
    marker_status = 'WAITING' if status in ('RCA_CONFIRMED', 'RCA_UNCONFIRMED') else 'BLOCKED'
    observed = entry.get('model_observed')
    effort = entry.get('effort_observed')
    observed = observed if type(observed) is str and _RECORD_TOKEN.fullmatch(observed) else 'UNKNOWN'
    effort = effort if type(effort) is str and _RECORD_TOKEN.fullmatch(effort) else 'UNKNOWN'
    usage = entry.get('usage')
    if (type(usage) is dict and
            all(type(usage.get(key)) is int and usage[key] >= 0
                for key in ('input', 'cached', 'output', 'reasoning'))):
        usage_text = ','.join(f'{key}={usage[key]}'
                              for key in ('input', 'cached', 'output', 'reasoning'))
    else:
        usage_text = 'UNKNOWN'
    lines = [
        f'<!-- activity: type=developer_progress actor=Codex target=PM status={marker_status} -->',
        f'Task: {task_id}',
        f'D6: {entry.get("d6", "UNKNOWN")}',
        f'Family: {entry.get("family", "UNKNOWN")}',
        f'Examined SHA: {entry.get("examined_sha", "UNKNOWN")}',
        f'Requested model: {entry.get("model_requested", "UNKNOWN")}',
        f'Observed model: {observed}',
        f'Effort: {effort}',
        f'Status: {status if type(status) is str else "UNKNOWN"}',
        f'Report SHA-256: {entry.get("report_sha256", "UNKNOWN")}',
        f'Usage: {usage_text}',
    ]
    if entry.get('status') == 'RUN_FAILED':
        code = refusal_code if refusal_code in _REFUSAL_CODES else 'HIGH_DEBUG_RUN_FAILED'
        lines.append(f'Refusal code: {code}')
    return '\n'.join(lines)


def _post_github_record(pr_number, body, repo_root):
    """Post one screened metadata comment with the trusted gh environment."""
    gh = shutil.which('gh', path=task_completion._GH_PATH)
    if gh is None:
        return False
    slug = task_completion._trusted_slug()
    if slug is None:
        return False
    env = {'PATH': task_completion._GH_PATH, 'HOME': os.path.expanduser('~'),
           'LC_ALL': 'C', 'GH_PROMPT_DISABLED': '1',
           'GH_NO_UPDATE_NOTIFIER': '1', 'NO_COLOR': '1'}
    if os.environ.get('GH_CONFIG_DIR'):
        env['GH_CONFIG_DIR'] = os.environ['GH_CONFIG_DIR']
    result = subprocess.run(
        [gh, 'pr', 'comment', str(pr_number), '--repo', slug, '--body-file', '-'],
        cwd=repo_root, env=env, input=body.encode('utf-8'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
        check=False, timeout=30)
    return result.returncode == 0


def _write_unposted(entry, pr_number, task_id, reason, body=None):
    """Best-effort private recovery record for the PM."""
    try:
        directory = Path(task_store.default_root()).parent / 'high-debug-unposted'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        record = {'task_id': task_id, 'd6': entry.get('d6'),
                  'pr': pr_number, 'reason': reason}
        if reason == 'POST_FAILED':
            record['body'] = body
        fd, temporary = tempfile.mkstemp(prefix='.unposted-', dir=str(directory))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(record, stream, sort_keys=True)
                stream.write('\n')
            os.replace(temporary, directory / (str(entry.get('d6')) + '.json'))
            os.chmod(directory / (str(entry.get('d6')) + '.json'), 0o600)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except Exception:
        pass


def _post_record(entry, pr_number, repo_root, task_id, refusal_code=None):
    """Return whether a comment was posted; never affect the diagnostic result."""
    try:
        body = _record_body(entry, task_id, refusal_code)
        if _emitted_sensitive(body):
            _write_unposted(entry, pr_number, task_id, 'SCREEN_REJECTED')
            return False
        try:
            posted = _post_github_record(pr_number, body, repo_root)
        except Exception:
            posted = False
        if posted:
            return True
        _write_unposted(entry, pr_number, task_id, 'POST_FAILED', body)
    except Exception:
        # Even metadata formatting and local persistence are best effort.
        return False
    return False


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


def _refuse(code, detail=None):
    error = LLMToolError('High-debug diagnosis refused; details omitted')
    error.high_debug_code = code
    if detail is not None:
        error.high_debug_detail = detail
    raise error from None


def _screen_rule(match_text):
    for name, pattern in _SCREEN_RULES:
        if pattern.search(match_text):
            return name
    return 'unclassified'


def _screen_hits(text):
    """Return bounded, text-free locations from the real output screen."""
    located = set()
    ascii_text = text.encode('ascii', 'backslashreplace').decode('ascii')
    for value in (text, ascii_text):
        for match in _SENSITIVE.finditer(value):
            line = value.count('\n', 0, match.start()) + 1
            located.add((_screen_rule(match.group(0)), line))
    for line, value in enumerate(text.split('\n'), 1):
        if _sensitive_jwt(value):
            located.add(('jwt', line))
        if _sensitive_url(value):
            located.add(('url_userinfo', line))
    return sorted(located)[:_SCREEN_HIT_LIMIT]


def _screen_path(task_id, family):
    return Path(task_store.default_root()).parent / 'high-debug-screen' / (task_id + '-' + family + '.json')


def _valid_screen_timestamp(value):
    if type(value) is not str:
        return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    except ValueError:
        return False


def _write_screen_record(task_id, family, code, source, hits, byte_count):
    directory = _screen_path(task_id, family).parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_info = directory.lstat()
    if not stat.S_ISDIR(directory_info.st_mode) or directory.is_symlink():
        raise OSError('invalid screen record directory')
    os.chmod(directory, 0o700)
    record = {'code': code, 'source': source, 'hits': [list(hit) for hit in hits],
              'bytes': byte_count, 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'disposition': None}
    fd, temporary = tempfile.mkstemp(prefix='.screen-', dir=str(directory))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write('\n')
        target = _screen_path(task_id, family)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except Exception:
        try: os.close(fd)
        except OSError: pass
        try: os.unlink(temporary)
        except OSError: pass
        raise


def _read_screen_record(task_id, family):
    path = _screen_path(task_id, family)
    try:
        parent_info = path.parent.lstat()
        if path.parent.is_symlink() or not stat.S_ISDIR(parent_info.st_mode):
            _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > _SCREEN_RECORD_LIMIT:
            _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > _SCREEN_RECORD_LIMIT:
                _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
            raw = os.read(fd, _SCREEN_RECORD_LIMIT + 1)
        finally:
            os.close(fd)
        if len(raw) > _SCREEN_RECORD_LIMIT:
            _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
        record = json.loads(raw.decode('utf-8'))
        if (type(record) is not dict or set(record) !=
                {'code', 'source', 'hits', 'bytes', 'recorded_at', 'disposition'} or
                record.get('source') not in ('EVIDENCE', 'REPORT') or
                type(record.get('hits')) is not list or not record['hits'] or
                len(record['hits']) > _SCREEN_HIT_LIMIT or
                type(record.get('bytes')) is not int or record['bytes'] < 0 or
                not _valid_screen_timestamp(record.get('recorded_at'))):
            _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
        hits = []
        for hit in record['hits']:
            if (type(hit) is not list or len(hit) != 2 or type(hit[0]) is not str or
                    _SCREEN_NAME.fullmatch(hit[0]) is None or type(hit[1]) is not int or hit[1] < 0):
                _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
            hits.append((hit[0], hit[1]))
        if hits != sorted(set(hits)):
            _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
        expected_code = ('HIGH_DEBUG_EVIDENCE_SENSITIVE' if record['source'] == 'EVIDENCE'
                         else 'HIGH_DEBUG_REPORT_INVALID')
        disposition = record['disposition']
        if record['code'] != expected_code or (disposition is not None and
                (type(disposition) is not dict or set(disposition) !=
                 {'classification', 'ref', 'recorded_at'} or
                 disposition.get('classification') not in
                 ('FALSE_POSITIVE', 'SYNTHETIC_TEST_DATA', 'REAL_VALUE_REMOVED') or
                 type(disposition.get('ref')) is not str or
                 _SCREEN_REF.fullmatch(disposition['ref']) is None or
                 not _valid_screen_timestamp(disposition.get('recorded_at')))):
            _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
        return record
    except LLMToolError:
        raise
    except Exception:
        _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')


def _screen_detail(hits, prefix=''):
    pairs = list(hits)
    while True:
        omitted = len(hits) - len(pairs)
        suffix = (' rules=' + ','.join(hit[0] for hit in pairs) +
                  ' lines=' + ','.join(str(hit[1]) for hit in pairs))
        if omitted:
            suffix += ' more=' + str(omitted)
        detail = prefix + suffix
        if len(detail) <= 128:
            return detail
        if pairs:
            pairs.pop()
            continue
        return (prefix + ' more=' + str(len(hits)))[:128]


def record_screen_disposition(task_id, family, hits, classification, ref):
    if (type(task_id) is not str or _SCREEN_TASK_ID.fullmatch(task_id) is None or
            type(family) is not str or _SCREEN_FAMILY.fullmatch(family) is None):
        _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')
    path = _screen_path(task_id, family)
    if not path.exists() and not path.is_symlink():
        _refuse('HIGH_DEBUG_SCREEN_NO_REFUSAL')
    record = _read_screen_record(task_id, family)
    if record['disposition'] is not None:
        _refuse('HIGH_DEBUG_SCREEN_NO_REFUSAL')
    if type(hits) not in (list, tuple):
        _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')
    supplied = []
    for hit in hits:
        if (type(hit) not in (list, tuple) or len(hit) != 2 or
                type(hit[0]) is not str or _SCREEN_NAME.fullmatch(hit[0]) is None or
                type(hit[1]) is not int or hit[1] < 0):
            _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')
        supplied.append((hit[0], hit[1]))
    supplied = sorted(set(supplied))
    if supplied != [tuple(hit) for hit in record['hits']]:
        _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_MISMATCH')
    if (classification not in ('FALSE_POSITIVE', 'SYNTHETIC_TEST_DATA', 'REAL_VALUE_REMOVED') or
            type(ref) is not str or _SCREEN_REF.fullmatch(ref) is None):
        _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')
    record['disposition'] = {'classification': classification, 'ref': ref,
                             'recorded_at': datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = path.parent.lstat()
    if path.parent.is_symlink() or not stat.S_ISDIR(parent_info.st_mode):
        _refuse('HIGH_DEBUG_SCREEN_RECORD_INVALID')
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.screen-', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try: os.close(fd)
        except OSError: pass
        try: os.unlink(temporary)
        except OSError: pass
        raise


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
                  report=None,
                  observed, effort, usage, recorded_at):
    """The high_debug entry recording one Codex attempt that did not finish."""
    stored_report = code if report is None else report
    return {'family': family, 'failed_corrections': failed_corrections,
            'early_reason': early_reason, 'examined_sha': head, 'd6': d6,
            'model_requested': 'gpt-6.1-sol', 'model_observed': observed,
            'effort_observed': effort, 'status': 'RUN_FAILED',
            'report_sha256': hashlib.sha256(stored_report.encode('utf-8')).hexdigest(),
            'report': stored_report, 'usage': usage, 'recorded_at': recorded_at}


def _report_rejection(code, checks, byte_count, report, hits=()):
    """Return a bounded diagnostic report, or fixed lines if its head is unsafe."""
    fixed_lines = (code, 'check: ' + checks, 'bytes: ' + str(byte_count),
                   'limit: ' + str(_REPORT_LIMIT))
    fixed = '\n'.join(fixed_lines)
    if 'SENSITIVE' in checks:
        return fixed + ''.join(f'\nhit: {rule} line={line}' for rule, line in hits)
    marker = '\nreport truncated\n'
    available = _REPORT_LIMIT - len((fixed + marker).encode('utf-8'))
    if available < 0:
        return fixed
    head = report.encode('utf-8')[:available].decode('utf-8', 'ignore')
    stored = fixed + marker + head
    if _emitted_sensitive(stored):
        return fixed
    return stored


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
_STATUS_TOKEN = re.compile(r'(?<!\w)(?:' + '|'.join(_STATUS_WORDS) + r')(?!\w)')
_STATUS_LINE = re.compile(
    r'^(?:\*\*(?P<bold>' + '|'.join(_STATUS_WORDS) + r')\*\*'
    r'|(?P<plain>' + '|'.join(_STATUS_WORDS) + r'))'
    r'(?P<trailing>(?:(?:\s+—|\s+-|\s*:)\s+\S.*)?)$'
)


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
        if in_outcome:
            match = _STATUS_LINE.fullmatch(stripped)
            if match:
                if _STATUS_TOKEN.search(match.group('trailing') or ''):
                    return 'REPORT_INVALID'
                found.append(match.group('bold') or match.group('plain'))
    return found[0] if len(found) == 1 else 'REPORT_INVALID'


def run_high_debug(*, store, task_id, executor_task, pr_number, family,
                   failed_corrections=None, early_reason=None, evidence=None,
                   runner=None, create_d6=None, repo_root=None,
                   read_registration=None, sessions_dir=None):
    task = store.get(task_id)
    if type(task) is not dict or task.get('task_id') != task_id:
        _refuse('HIGH_DEBUG_TASK_INVALID')
    if (type(task_id) is not str or _SCREEN_TASK_ID.fullmatch(task_id) is None or
            type(executor_task) is not str or
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
    screen_path = _screen_path(task_id, family)
    if screen_path.exists() or screen_path.is_symlink():
        screen_record = _read_screen_record(task_id, family)
        if screen_record['disposition'] is None:
            _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED',
                    _screen_detail([tuple(hit) for hit in screen_record['hits']]))
    if evidence is not None:
        try:
            with open(evidence, 'rb') as evidence_stream:
                evidence_bytes = os.fstat(evidence_stream.fileno()).st_size
                if evidence_bytes > _EVIDENCE_LIMIT:
                    _refuse('HIGH_DEBUG_EVIDENCE_INVALID',
                            f'bytes={evidence_bytes} limit={_EVIDENCE_LIMIT}')
                raw = evidence_stream.read(_EVIDENCE_LIMIT + 1)
            if len(raw) > _EVIDENCE_LIMIT:
                _refuse('HIGH_DEBUG_EVIDENCE_INVALID',
                        f'bytes={evidence_bytes} limit={_EVIDENCE_LIMIT}')
            evidence_text = raw.decode('utf-8')
            if _emitted_sensitive(evidence_text):
                hits = _screen_hits(evidence_text) or [('unlocated', 0)]
                try:
                    _write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                         'EVIDENCE', hits, len(raw))
                    detail = _screen_detail(hits)
                except Exception:
                    detail = _screen_detail(hits, 'screen_record=UNRECORDED')
                _refuse('HIGH_DEBUG_EVIDENCE_SENSITIVE', detail)
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

    def failed(code, observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN', report=None,
               detail=None, screen_hits=None):
        # A Codex attempt that started but did not finish is recorded: it
        # counts toward the entry limit and keeps its D6 traceable. Recording
        # is best effort: the original refusal code is what the caller sees.
        try:
            recorded_at = store._completion_now()
        except Exception:
            recorded_at = None
        entry = _failed_entry(
                family=family, failed_corrections=failed_corrections,
                early_reason=early_reason, head=head, d6=d6, code=code, report=report,
                observed=observed, effort=effort, usage=usage,
                recorded_at=recorded_at)
        try:
            store.append_high_debug(task_id, entry)
        except Exception:
            pass
        posted = _post_record(entry, pr_number, repo_root, task_id, code)
        if not posted:
            detail = (detail + ' ' if detail else '') + 'github_record=UNPOSTED'
        if screen_hits is not None:
            detail = _screen_detail(screen_hits, detail or '')
        _refuse(code, detail)

    create = create_d6 or _create_d6
    try: diagnosis_root = create(task_id=d6, base_sha=head, repo_root=repo_root, executor_task=executor_task)
    except Exception: _refuse('HIGH_DEBUG_D6_CREATE_FAILED')
    reason = f'failed corrections: {failed_corrections}' if failed_corrections is not None else f'early reason: {early_reason}'
    prompt = (f'Task: {task_id}\nPR: {pr_number}\nFamily: {family}\n{reason}\nExamined SHA: {head}\n'
              f'Evidence:\n{evidence_text}\n\nRequired report sections, each exactly as its own line:\n'
              + '\n'.join('## ' + section for section in REPORT_SECTIONS) + '\n'
              'The Outcome/cost section must start with exactly one status word alone on its own line: '
              'RCA_CONFIRMED, RCA_UNCONFIRMED or BLOCKED. Put cost and other details on following lines. '
              'Diagnose only; do not edit files.')
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
    report_bytes = report.encode('utf-8')
    oversized = len(report_bytes) > _REPORT_LIMIT
    sensitive = _emitted_sensitive(report)
    if oversized or sensitive:
        checks = ','.join(check for check, matched in
                          (('SIZE', oversized), ('SENSITIVE', sensitive)) if matched)
        hits = (_screen_hits(report) or [('unlocated', 0)]) if sensitive else []
        stored_report = _report_rejection('HIGH_DEBUG_REPORT_INVALID', checks,
                                          len(report_bytes), report, hits)
        detail = f'check={checks} bytes={len(report_bytes)} limit={_REPORT_LIMIT}'
        if sensitive:
            try:
                _write_screen_record(task_id, family, 'HIGH_DEBUG_REPORT_INVALID',
                                     'REPORT', hits, len(report_bytes))
            except Exception:
                detail += ' screen_record=UNRECORDED'
        failed('HIGH_DEBUG_REPORT_INVALID', observed, effort, usage,
               report=stored_report, detail=detail, screen_hits=hits if sensitive else None)
    status=_outcome_status(report)
    if any(not _has_report_section(report, h) for h in REPORT_SECTIONS): status='REPORT_INVALID'
    entry={'family':family, 'failed_corrections':failed_corrections, 'early_reason':early_reason,
       'examined_sha':head,'d6':d6,'model_requested':'gpt-6.1-sol','model_observed':observed,
       'effort_observed':effort,'status':status,'report_sha256':hashlib.sha256(report.encode()).hexdigest(),
       'report':report,'usage':usage,'recorded_at':store._completion_now()}
    store.append_high_debug(task_id, entry)
    posted = _post_record(entry, pr_number, repo_root, task_id)
    payload=entry['report_sha256'].encode()
    event_id=hashlib.sha256(('HIGH_DEBUG\0'+task_id+'\0'+head+'\0'+entry['report_sha256']).encode()).hexdigest()[:32]
    result=store.record_completion_event(task_id,event_id=event_id,run_id=task['run_id'],
       session_id='high-debug-'+d6,source='HIGH_DEBUG',source_sha=head,sequence=len(entries),
       payload_sha256=hashlib.sha256(payload).hexdigest())
    print(json.dumps({'state':'RECORDED','status':status,'d6':d6,
        'report_sha256':entry['report_sha256'],'event_id':event_id,
        'github_record':'POSTED' if posted else 'UNPOSTED'},sort_keys=True))


def _screen_disposition_main(argv=None):
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            raise ValueError

    parser = Parser(prog='python -m llmtool.high_debug', add_help=False)
    parser.add_argument('command', choices=('screen-disposition',))
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--family', required=True)
    parser.add_argument('--hit', action='append', required=True)
    parser.add_argument('--classification', required=True)
    parser.add_argument('--ref', required=True)
    try:
        args = parser.parse_args(argv)
        hits = []
        for value in args.hit:
            match = re.fullmatch(r'([a-z_]+):([0-9]+)', value)
            if match is None:
                _refuse('HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')
            hits.append((match.group(1), int(match.group(2))))
        record_screen_disposition(args.task_id, args.family, hits,
                                  args.classification, args.ref)
        print(json.dumps({'state': 'RECORDED'}, sort_keys=True))
        return 0
    except LLMToolError as error:
        print(getattr(error, 'high_debug_code', 'HIGH_DEBUG_SCREEN_DISPOSITION_INVALID'),
              file=sys.stderr)
        return 1
    except Exception:
        print('HIGH_DEBUG_SCREEN_DISPOSITION_INVALID', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(_screen_disposition_main())
