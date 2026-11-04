"""Claude Code hook for required-skill indexes and protected Bash checks.

The hook verifies that each required file was observed through a Read tool in
the same session and agent and still has the same content hash. This is
evidence that the text reached that context, not that it was understood. A
Read whose working-tree hash differs from the manifest hash records no row, so
the gate reports NOT_READ; a file changed after a valid Read reports
STALE_BLOB. The
N3 protected-word matcher is deliberately lexical and cannot block commands
hidden in scripts, shell indirection, disabled hooks, or Claude safe mode. If
both a compaction ledger append and its failure-marker write fail, an already
readable ledger may retain older evidence; that rare gap cannot be recorded.
After a failed compaction marker, re-reads remain COMPACTED; start a new
session to recover.
Same-user processes can forge hook input or ledger data. A Read event is
evidence the hook saw the unchanged file contents at hook time, not that the
text was understood; a file change between hashing and later use remains a
race. Expired or inactive evidence is ignored. N4/N5 are deferred and block
enforce mode; enforce mode blocks protected commands when required Read
evidence is absent, stale, or invalidated by compaction. Observe mode remains
the default.
"""

import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import fcntl
from datetime import datetime, timedelta, timezone

from . import skill_ledger
from .skill_manifest import build_manifest

MAX_CONTEXT_CHARS = 9000
_AGENT_ROLES = {
    'pm': 'pm', 'developer': 'developer', 'reviewer': 'reviewer',
    'high-debug': 'high-debug',
}
_failed_compactions = set()


def _mode():
    return 'enforce' if os.environ.get('LLMTOOL_HOOK_MODE') == 'enforce' else 'observe'


def _log(code):
    print('claude_skill_hook: ' + code, file=sys.stderr)


def _deny(event_name, reason):
    if event_name != 'PreToolUse':
        return {'hookSpecificOutput': {'hookEventName': event_name, 'error': str(reason)}}
    return {'hookSpecificOutput': {
        'hookEventName': event_name, 'permissionDecision': 'deny',
        'permissionDecisionReason': str(reason),
    }}


def _role(payload, event_name):
    if event_name == 'SubagentStart' or (payload.get('agent_id') and payload.get('agent_type')):
        agent_type = payload.get('agent_type')
        return _AGENT_ROLES.get(agent_type.strip().lower(), 'UNVERIFIED') if type(agent_type) is str else 'UNVERIFIED'
    value = os.environ.get('LLMTOOL_ROLE', 'pm')
    return value if value in _AGENT_ROLES else 'UNVERIFIED'


def _repo_root(repo):
    result = subprocess.run(
        ['git', 'rev-parse', '--show-toplevel'], cwd=repo, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10,
    )
    if result.returncode:
        raise ValueError('REPOSITORY_UNAVAILABLE')
    return os.path.realpath(result.stdout.decode('utf-8').strip())


def _commit(repo):
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10,
    )
    head = result.stdout.decode('ascii', 'ignore').strip()
    if result.returncode or re.fullmatch(r'[0-9a-f]{40}', head) is None:
        raise ValueError('REPOSITORY_UNAVAILABLE')
    return head


def _manifest_context(repo, head, role, gate):
    manifest = build_manifest(repo, head, role, gate, provider='claude')
    paths = tuple(entry['path'] for entry in manifest['entries'])
    by_path = {entry['path']: entry['sha256'] for entry in manifest['entries']}
    lines = [
        'Required skills index',
        'role: ' + role,
        'gate: ' + gate,
        'manifest_sha256: ' + manifest['manifest_sha256'],
        'Read every listed file.',
    ]
    lines.extend(path + ' sha256=' + by_path[path] for path in paths)
    context = '\n'.join(lines)
    if len(context) > MAX_CONTEXT_CHARS:
        raise ValueError('CONTEXT_TOO_LARGE')
    return manifest, context


def _binding(payload, role, repo):
    model = payload.get('model') or os.environ.get('LLMTOOL_MODEL') or ''
    return {
        'task_id': os.environ.get('LLMTOOL_TASK_ID', ''),
        'run_id': os.environ.get('LLMTOOL_RUN_ID', ''),
        'session_id': payload.get('session_id') or '',
        'agent_id': payload.get('agent_id') or 'main',
        'role': role, 'provider': 'claude', 'model': model, 'repo': repo,
        'head': '', 'manifest_sha256': '',
    }


def _append_event(binding, event_type, **fields):
    ledger = skill_ledger
    path = ledger._path()
    fd = ledger._locked(path)
    try:
        records = ledger._read(path, recover_torn=True)
        if not ledger._chain(records) or not ledger._recover_anchor(path, records):
            raise ValueError('LEDGER_UNREADABLE')
        now = datetime.now(timezone.utc)
        entry = dict(binding)
        entry.update({
            'schema_version': ledger.SCHEMA_VERSION,
            'receipt_id': secrets.token_hex(16),
            'evidence_level': ('OBSERVED_READ' if event_type == 'OBSERVED_READ'
                               else 'BOUNDARY'), 'status': 'ACTIVE',
            'created_at': now.isoformat(timespec='microseconds').replace('+00:00', 'Z'),
            'expires_at': (now + timedelta(days=1)).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
            'event_type': event_type,
        })
        entry.update(fields)
        ledger._append_locked(path, records, entry)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _safe_repo_file(repo, supplied):
    if type(supplied) is not str or not supplied:
        return None
    repo = os.path.realpath(repo)
    target = supplied if os.path.isabs(supplied) else os.path.join(repo, supplied)
    target = os.path.abspath(target)
    try:
        # Git reports a canonical root (for example /private/var/... on macOS),
        # while Claude may report the same file through an alias such as /var/....
        # Find the spelling of the repo root in the supplied path, then validate
        # every in-repository component before opening the canonical path.
        cursor = target
        suffix = []
        while os.path.realpath(cursor) != repo:
            parent, name = os.path.split(cursor)
            if parent == cursor or not name:
                return None
            suffix.insert(0, name)
            cursor = parent
        relative = os.path.join(*suffix) if suffix else ''
        canonical_target = os.path.join(repo, relative)
        if (os.path.realpath(target) != canonical_target or
                os.path.commonpath((repo, canonical_target)) != repo):
            return None
        cursor = repo
        for component in suffix:
            if component in ('', '.'):
                continue
            cursor = os.path.join(cursor, component)
            info = os.lstat(cursor)
            if stat.S_ISLNK(info.st_mode):
                return None
        fd = os.open(canonical_target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return None
            digest = hashlib.sha256()
            line_count = 0
            last_byte = b''
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                digest.update(chunk)
                line_count += chunk.count(b'\n')
                last_byte = chunk[-1:]
            if info.st_size and last_byte != b'\n':
                line_count += 1
            return relative.replace(os.sep, '/'), digest.hexdigest(), line_count
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return None


def _identity(record, binding, gate):
    return (all(record.get(key) == binding.get(key) for key in
                ('session_id', 'agent_id', 'role', 'provider', 'repo')) and
            record.get('gate') == gate)


def _record_read(payload, role):
    repo = _repo_root(os.path.realpath(payload.get('cwd') or os.getcwd()))
    gate = os.environ.get('LLMTOOL_GATE', 'develop')
    manifest = build_manifest(repo, _commit(repo), role, gate, provider='claude')
    required = {entry['path']: entry['sha256'] for entry in manifest['entries']}
    tool_input = payload.get('tool_input')
    requested = tool_input.get('file_path') if type(tool_input) is dict else None
    resolved = _safe_repo_file(repo, requested)
    if resolved is None:
        return
    path, digest, line_count = resolved
    if path not in required or digest != required[path]:
        return
    tool_input = payload.get('tool_input') or {}
    # Claude versions may encode the first line as offset 0 or 1.
    offset = tool_input.get('offset', 1)
    limit = tool_input.get('limit')
    if (type(offset) is not int or offset not in (0, 1) or
            (limit is not None and (type(limit) is not int or limit < line_count))):
        return
    binding = _binding(payload, role, repo)
    if not binding['session_id']:
        return
    _append_event(binding, 'OBSERVED_READ', gate=gate,
                  path=path, blob_sha256=digest)


def _compaction_marker(identity):
    payload = json.dumps(identity, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _mark_compaction_failed(identity):
    _failed_compactions.add(identity)
    marker = str(skill_ledger._path()) + '.compaction-failed'
    digest = (_compaction_marker(identity) + '\n').encode('ascii')
    fd = os.open(marker, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise OSError('unsafe compaction marker')
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, digest)
        os.fsync(fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _compaction_failed(identity):
    if identity in _failed_compactions:
        return True
    marker = str(skill_ledger._path()) + '.compaction-failed'
    try:
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 1024 * 1024:
            return True
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return (_compaction_marker(identity) + '\n').encode('ascii') in b''.join(chunks).splitlines(keepends=True)
    except OSError:
        return True
    finally:
        os.close(fd)


def _record_compaction(payload, role):
    repo = _repo_root(os.path.realpath(payload.get('cwd') or os.getcwd()))
    gate = os.environ.get('LLMTOOL_GATE', 'develop')
    binding = _binding(payload, role, repo)
    if not binding['session_id']:
        return
    identity = (binding['session_id'], binding['agent_id'], binding['repo'])
    try:
        _append_event(binding, 'COMPACTION', gate=gate,
                      source=payload.get('source'))
    except Exception:
        _mark_compaction_failed(identity)
        raise


def _check(payload, gate):
    role = _role(payload, 'PreToolUse')
    if role == 'UNVERIFIED':
        return {'status': 'UNVERIFIED', 'reason': 'UNVERIFIED_ROLE'}
    repo = _repo_root(os.path.realpath(payload.get('cwd') or os.getcwd()))
    manifest, _ = _manifest_context(repo, _commit(repo), role, gate)
    binding = _binding(payload, role, repo)
    if not binding['session_id']:
        return {'status': 'UNVERIFIED', 'reason': 'NOT_READ'}
    if _compaction_failed((binding['session_id'], binding['agent_id'], binding['repo'])):
        return {'status': 'UNVERIFIED', 'reason': 'COMPACTED'}
    ledger = skill_ledger
    fd = ledger._locked(ledger._path())
    try:
        records = ledger._read(ledger._path(), recover_torn=True)
        # A missing ledger or a genuinely empty ledger without an anchor is
        # the normal initial state: there are simply no Read observations yet.
        if not records and not os.path.lexists(str(ledger._anchor_path(ledger._path()))):
            return {'status': 'UNVERIFIED', 'reason': 'NOT_READ'}
        if not ledger._chain(records) or not ledger._recover_anchor(ledger._path(), records):
            return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    relevant = [r for r in records if r.get('event_type') in ('OBSERVED_READ', 'COMPACTION')
                and _identity(r, binding, gate)]
    boundaries = [r for r in relevant if r.get('event_type') == 'COMPACTION']
    latest_boundary = max((skill_ledger._stamp(r.get('created_at')) for r in boundaries),
                          default=None)
    latest_boundary = latest_boundary or datetime.min.replace(tzinfo=timezone.utc)
    required = {}
    for entry in manifest['entries']:
        current = _safe_repo_file(repo, entry['path'])
        current_hash = current[1] if current else None
        required[entry['path']] = current_hash
    saw_stale = False
    saw_compacted = False
    fresh_by_path = {}
    for path, current_hash in required.items():
        rows = [r for r in relevant if r.get('event_type') == 'OBSERVED_READ' and r.get('path') == path]
        fresh = [r for r in rows if (skill_ledger._stamp(r.get('created_at')) or
                                     datetime.min.replace(tzinfo=timezone.utc)) > latest_boundary
                 and r.get('status') == 'ACTIVE'
                 and (skill_ledger._stamp(r.get('expires_at')) or
                      datetime.min.replace(tzinfo=timezone.utc)) > datetime.now(timezone.utc)]
        fresh_by_path[path] = fresh
        if not fresh:
            if rows and boundaries:
                saw_compacted = True
            continue
        if not any(r.get('blob_sha256') == current_hash for r in fresh):
            saw_stale = True
    if saw_compacted:
        reason = 'COMPACTED'
    elif saw_stale:
        reason = 'STALE_BLOB'
    elif any(not any(r.get('blob_sha256') == digest for r in fresh_by_path[path])
             for path, digest in required.items()):
        reason = 'NOT_READ'
    else:
        return {'status': 'VERIFIED', 'reason': None}
    return {'status': 'UNVERIFIED', 'reason': reason}


def _protected_gates(command):
    # Keep shell syntax handling lexical: split command boundaries, then
    # inspect each command without combining quoted arguments from neighbors.
    gates = []
    wrappers = {'command', 'exec', 'env', 'sudo', 'time', 'nohup', '!',
                'if', 'then', 'while', 'do'}

    def parse(segment):
        try:
            return shlex.split(segment, posix=True)
        except ValueError:
            # An unmatched quote should not hide a recognizable leading
            # command (for example a shell comment containing an apostrophe).
            # This fallback deliberately applies the same word rules after
            # removing quote characters; it does not infer commands in prose.
            return re.sub(r'["\']', '', segment).split()

    def strip_value_options(words, value_options):
        result = []
        index = 0
        while index < len(words):
            word = words[index]
            if word in value_options:
                index += 2
                continue
            if any(word.startswith(option + '=') for option in value_options):
                index += 1
                continue
            result.append(word)
            index += 1
        return result

    def scan(segment, depth=0):
        if depth > 8:
            return
        for part in re.split(r'[\n;&|(){}]+', segment):
            words = parse(part)
            if not words:
                continue
            # Shell control words and command wrappers may precede the real
            # executable. env/sudo accept option arguments before it.
            while words:
                while words and words[0] in ('if', 'then', 'while', 'do', '!'):
                    words.pop(0)
                if not words or words[0] not in wrappers:
                    break
                wrapper = words.pop(0)
                if wrapper == 'env':
                    words = strip_value_options(words, {'-u', '--unset', '-C', '--chdir'})
                    while words and (words[0].startswith('-') or
                                     re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', words[0])):
                        words.pop(0)
                elif wrapper == 'sudo':
                    words = strip_value_options(words, {'-u', '--user', '-g', '--group',
                                                        '-h', '--host', '-p', '--prompt',
                                                        '-C', '--close-from', '-T', '--command-timeout'})
                    while words and words[0].startswith('-'):
                        words.pop(0)
                elif wrapper == 'time':
                    while words and words[0].startswith('-'):
                        words.pop(0)
                else:
                    while words and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', words[0]):
                        words.pop(0)
            if not words or words[0] in ('echo', 'printf'):
                continue
            executable = os.path.basename(words[0])
            args = words[1:]
            if executable in ('bash', 'sh', 'zsh'):
                try:
                    c_index = args.index('-c')
                except ValueError:
                    c_index = -1
                if c_index >= 0 and c_index + 1 < len(args):
                    scan(args[c_index + 1], depth + 1)
                continue
            if executable == 'git':
                args = strip_value_options(args, {'-c', '-C', '--git-dir', '--work-tree',
                                                  '--namespace', '--exec-path'})
                if args and args[0] == 'push':
                    gates.append('publish')
            elif executable == 'gh':
                args = strip_value_options(args, {'-R', '--repo'})
                if args[:2] == ['pr', 'merge']:
                    gates.append('merge')
                if len(args) >= 2 and args[0] == 'pr' and args[1] in ('comment', 'review'):
                    gates.append('review')
                if args[:2] == ['issue', 'comment']:
                    gates.append('review')
            tool_args = None
            if executable in ('llmtool', 'llmtool.exe'):
                tool_args = [arg for arg in args if arg != '--json']
            elif re.fullmatch(r'python(?:3(?:\.\d+)?)?(?:\.exe)?', executable):
                python_args = list(args)
                while python_args and python_args[0] in ('-B', '-I'):
                    python_args.pop(0)
                while python_args and python_args[0] == '-X':
                    python_args = python_args[2:]
                while python_args and python_args[0].startswith('-X'):
                    python_args.pop(0)
                if len(python_args) >= 2 and python_args[0] == '-m' and python_args[1] in (
                        'llmtool', 'llmtool.cli'):
                    tool_args = python_args[2:]
            if tool_args and len(tool_args) >= 2 and tool_args[0] == 'task':
                if tool_args[1] == 'delegate':
                    gates.append('dispatch')
                if tool_args[1] in ('complete', 'advance'):
                    gates.append('complete')

    scan(command)
    return list(dict.fromkeys(gates))


def handle(payload):
    if type(payload) is not dict:
        return _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}
    event = payload.get('hook_event_name') or payload.get('hookEventName')
    if event is None and payload.get('tool_name') == 'Bash':
        _log('INPUT_INVALID')
        return _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}
    if event in ('SessionStart', 'SubagentStart'):
        source = payload.get('source')
        if event == 'SessionStart' and source not in ('startup', 'resume', 'compact'):
            _log('INPUT_INVALID')
            return _deny(event, 'INPUT_INVALID') if _mode() == 'enforce' else {}
        role = _role(payload, event)
        if role == 'UNVERIFIED':
            _log('UNVERIFIED_ROLE')
            return _deny(event, 'UNVERIFIED_ROLE') if _mode() == 'enforce' else {}
        try:
            repo = _repo_root(os.path.realpath(payload.get('cwd') or os.getcwd()))
            manifest, context = _manifest_context(
                repo, _commit(repo), role, os.environ.get('LLMTOOL_GATE', 'develop'))
            if event == 'SessionStart' and source in ('compact', 'resume'):
                _record_compaction(payload, role)
        except Exception as exc:
            reason = getattr(exc, 'code', None) or str(exc) or 'MANIFEST_UNAVAILABLE'
            _log(reason)
            return _deny(event, reason) if _mode() == 'enforce' else {}
        return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': context}}
    if event == 'PostToolUse' and payload.get('tool_name') == 'Read':
        role = _role(payload, event)
        if role == 'UNVERIFIED':
            _log('UNVERIFIED_ROLE')
            return {}
        try:
            _record_read(payload, role)
        except Exception:
            _log('LEDGER_UNREADABLE')
        return {}
    if event == 'PreToolUse' and payload.get('tool_name') == 'Bash':
        tool_input = payload.get('tool_input')
        command = tool_input.get('command') if type(tool_input) is dict else None
        if type(command) is not str:
            _log('BASH_INPUT_INVALID')
            return _deny(event, 'BASH_INPUT_INVALID') if _mode() == 'enforce' else {}
        for gate in _protected_gates(command):
            try:
                result = _check(payload, gate)
            except Exception:
                result = {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
            if result.get('status') != 'VERIFIED':
                reason = result.get('reason') or 'NOT_READ'
                _log(reason)
                if _mode() == 'enforce':
                    return _deny(event, reason)
        return {}
    return {}


def main(argv=None):
    # Extra arguments are intentionally ignored; multipart delivery is gone.
    del argv
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = None
    try:
        response = handle(payload)
    except Exception:
        _log('HOOK_FAILURE')
        response = _deny('PreToolUse', 'HOOK_FAILURE') if _mode() == 'enforce' else {}
    try:
        sys.stdout.write(json.dumps(response, separators=(',', ':')) + '\n')
        sys.stdout.flush()
    except (OSError, ValueError):
        _log('HOOK_OUTPUT_FAILED')


if __name__ == '__main__':
    main()
