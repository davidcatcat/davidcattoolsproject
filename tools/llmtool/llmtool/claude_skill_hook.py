"""Claude Code hook for required skill delivery and protected Bash checks.

The hook cannot block a script file that invokes a protected command, shell
variable indirection, quote splitting, Claude's disableAllHooks setting, or
Claude's --safe-mode option. An agent's own Bash can also run this module with
synthetic stdin, so same-user forgery remains possible. These measured
bypasses remain out of reach. Delivery markers use a short freshness window
because separate hook processes have no shared delivery-run identifier; a
failed redelivery is detected only when its part 1 arrives and revokes the
prior receipt. One session satisfies one gate family. A gate mismatch reports
WRONG_HEAD. Receipts bind a content digest, not the delivered Git commit.
Partial-part markers use OBSERVED_READ because the ledger has no partial
delivery evidence level; only a complete set creates HOOK_DELIVERED.
"""

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import fcntl
from datetime import datetime, timedelta, timezone

from . import skill_ledger
from .skill_manifest import build_manifest, read_manifest_files

MAX_CONTEXT_CHARS = 9000
_CHUNK_RESERVE = 200
_AGENT_ROLES = {
    'pm': 'pm',
    'developer': 'developer',
    'reviewer': 'reviewer',
    'high-debug': 'high-debug',
}
_PENDING_DELIVERY = None
_PART_FRESH_SECONDS = 30
_DELIVERY_GENERATIONS = ('startup', 'resume', 'compact', 'subagent-start', 'delivery')


def _mode():
    return 'enforce' if os.environ.get('LLMTOOL_HOOK_MODE') == 'enforce' else 'observe'


def _emit(value):
    try:
        sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')
        sys.stdout.flush()
        return True
    except (OSError, ValueError):
        _log('HOOK_OUTPUT_FAILED')
        return False


def _deny(event_name, reason):
    if event_name != 'PreToolUse':
        return {'hookSpecificOutput': {
            'hookEventName': event_name,
            'error': str(reason),
        }}
    return {
        'hookSpecificOutput': {
            'hookEventName': event_name,
            'permissionDecision': 'deny',
            'permissionDecisionReason': str(reason),
        }
    }


def _log(code):
    print('claude_skill_hook: ' + code, file=sys.stderr)


def _role(payload, event_name):
    if event_name == 'SubagentStart' or (payload.get('agent_id') and
                                          payload.get('agent_type')):
        agent_type = payload.get('agent_type')
        if type(agent_type) is str:
            return _AGENT_ROLES.get(agent_type.strip().lower(), 'UNVERIFIED')
        return 'UNVERIFIED'
    value = os.environ.get('LLMTOOL_ROLE', 'pm')
    return value if value in ('pm', 'developer', 'reviewer', 'high-debug') else 'UNVERIFIED'


def _commit(repo):
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ValueError('REPOSITORY_UNAVAILABLE')
    head = result.stdout.decode('ascii').strip()
    if re.fullmatch(r'[0-9a-fA-F]{40}', head) is None:
        raise ValueError('REPOSITORY_UNAVAILABLE')
    return head.lower()


def _repo_root(repo):
    result = subprocess.run(
        ['git', 'rev-parse', '--show-toplevel'], cwd=repo,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=False, timeout=10,
    )
    if result.returncode != 0:
        raise ValueError('REPOSITORY_UNAVAILABLE')
    return os.path.realpath(result.stdout.decode('utf-8').strip())


def _manifest_context(repo, head, role, gate):
    manifest = build_manifest(repo, head, role, gate, provider='claude')
    files = read_manifest_files(repo, manifest)
    full_text = ''.join(
        '\n--- ' + path + ' ---\n' + content
        for path, content in files
    )
    digest = manifest['manifest_sha256']
    size = MAX_CONTEXT_CHARS - _CHUNK_RESERVE

    def split_utf16(value, maximum):
        chunks, current, units = [], [], 0
        for character in value:
            character_units = 2 if ord(character) > 0xffff else 1
            if current and units + character_units > maximum:
                chunks.append(''.join(current))
                current, units = [], 0
            current.append(character)
            units += character_units
        if current or not chunks:
            chunks.append(''.join(current))
        return chunks

    chunks = split_utf16(full_text, size)
    count = len(chunks)
    contexts = []
    for number, chunk in enumerate(chunks, 1):
        label = ('Required skill text: part {0} of {1}; '
                 'manifest_sha256={2}\n').format(number, count, digest)
        context = label + chunk
        if len(context.encode('utf-16-le')) // 2 > MAX_CONTEXT_CHARS:
            raise ValueError('CONTEXT_TOO_LARGE')
        contexts.append(context)
    return manifest, contexts


def _binding(payload, role, repo, content_digest):
    agent_id = payload.get('agent_id')
    if not agent_id:
        agent_id = 'main'
    model = payload.get('model') or os.environ.get('LLMTOOL_MODEL')
    return {
        'task_id': os.environ.get('LLMTOOL_TASK_ID'),
        'run_id': os.environ.get('LLMTOOL_RUN_ID'),
        'session_id': payload.get('session_id'),
        'agent_id': agent_id,
        'role': role,
        'provider': 'claude',
        'model': model,
        'repo': repo,
        # Private content binding: these fields carry the canonical digest of
        # manifest entries (path, blob SHA-256, size), not Git HEAD or the
        # full manifest SHA-256. Delivery and checks use the same encoding.
        'head': content_digest,
        'manifest_sha256': content_digest,
    }


def _content_digest(manifest):
    entries = manifest.get('entries')
    if type(entries) is not list:
        raise ValueError('MANIFEST_UNAVAILABLE')
    selected = []
    for entry in entries:
        if (type(entry) is not dict or type(entry.get('path')) is not str or
                type(entry.get('sha256')) is not str or
                type(entry.get('size')) is not int):
            raise ValueError('MANIFEST_UNAVAILABLE')
        selected.append({key: entry[key] for key in ('path', 'sha256', 'size')})
    canonical = json.dumps(selected, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _part_binding(base, part, count, generation):
    values = dict(base)
    suffix = ':generation:{}:part:{}:{}'.format(generation, part, count)
    values['head'] = base['head'] + suffix
    values['manifest_sha256'] = base['manifest_sha256'] + suffix
    return values


def _revoke_delivery_receipts(base, count):
    """Revoke every older complete receipt and partial marker for this session."""
    path = skill_ledger._path()
    lock_fd = skill_ledger._locked(path)
    try:
        records = skill_ledger._read(path, recover_torn=True)
        if (not skill_ledger._chain(records) or
                not skill_ledger._recover_anchor(path, records)):
            raise ValueError('LEDGER_UNREADABLE')
        revoked = {item.get('revokes_receipt_id') for item in records
                   if item.get('entry_type') == 'REVOCATION'}
        bindings = [base]
        bindings.extend(
            _part_binding(base, number, count, generation)
            for generation in _DELIVERY_GENERATIONS
            for number in range(1, count + 1)
        )
        for target in list(records):
            if (target.get('entry_type') == 'REVOCATION' or
                    target.get('receipt_id') in revoked or
                    target.get('evidence_level') not in
                    ('HOOK_DELIVERED', 'OBSERVED_READ')):
                continue
            if not any(all(target.get(key) == binding[key]
                           for key in skill_ledger._BINDING_FIELDS)
                       for binding in bindings):
                continue
            event = dict(target)
            event.update(
                entry_type='REVOCATION',
                revokes_receipt_id=target['receipt_id'],
                revocation_reason='DELIVERY_RESTARTED',
                status='REVOKED', receipt_id=secrets.token_hex(16),
                created_at=skill_ledger._now_text(),
            )
            skill_ledger._append_locked(path, records, event)
            records.append(event)
            revoked.add(target['receipt_id'])
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _finalize_delivery():
    global _PENDING_DELIVERY
    pending = _PENDING_DELIVERY
    _PENDING_DELIVERY = None
    if pending is None:
        return
    base = pending['base']
    generation = pending['generation']
    part_values = _part_binding(base, pending['part'], pending['count'], generation)
    part_values['evidence_level'] = 'OBSERVED_READ'
    try:
        skill_ledger.record(part_values)
    except Exception:
        _log('LEDGER_UNREADABLE')
        return
    _finalize_if_complete(pending)


def _finalize_if_complete(pending):
    """Append one complete receipt while holding the ledger's exclusive lock."""
    ledger = skill_ledger
    path = ledger._path()
    try:
        lock_fd = ledger._locked(path)
    except Exception:
        _log('LEDGER_UNREADABLE')
        return
    try:
        records = ledger._read(path, recover_torn=True)
        if not ledger._chain(records) or not ledger._recover_anchor(path, records):
            _log('LEDGER_UNREADABLE')
            return
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - _PART_FRESH_SECONDS
        revoked = {item.get('revokes_receipt_id') for item in records
                   if item.get('entry_type') == 'REVOCATION'}
        candidates = {}
        for item in records:
            if (item.get('evidence_level') != 'OBSERVED_READ' or
                    item.get('entry_type') == 'REVOCATION' or
                    item.get('receipt_id') in revoked):
                continue
            created = skill_ledger._stamp(item.get('created_at'))
            if created is None or created.timestamp() < cutoff:
                continue
            for number in range(1, pending['count'] + 1):
                marker = _part_binding(pending['base'], number,
                                       pending['count'], pending['generation'])
                if all(item.get(key) == marker[key]
                       for key in skill_ledger._BINDING_FIELDS):
                    candidates[number] = created
        if len(candidates) != pending['count']:
            return
        times = [value.timestamp() for value in candidates.values()]
        if max(times) - min(times) > _PART_FRESH_SECONDS:
            return
        base = pending['base']
        # Another concurrent finalizer may have completed this same marker set.
        for item in records:
            if (item.get('evidence_level') == 'HOOK_DELIVERED' and
                    all(item.get(key) == base[key]
                        for key in skill_ledger._BINDING_FIELDS) and
                    item.get('receipt_id') not in revoked):
                return
        value = {key: base[key] for key in skill_ledger._BINDING_FIELDS}
        value.update({
            'schema_version': skill_ledger.SCHEMA_VERSION,
            'receipt_id': secrets.token_hex(16),
            'evidence_level': 'HOOK_DELIVERED', 'status': 'ACTIVE',
            'created_at': now.isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'expires_at': (now + timedelta(seconds=86400))
                          .isoformat(timespec='seconds').replace('+00:00', 'Z'),
        })
        ledger._append_locked(path, records, value)
    except Exception:
        _log('LEDGER_UNREADABLE')
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _delivery(payload, event_name, part):
    role = _role(payload, event_name)
    mode = _mode()
    if role == 'UNVERIFIED':
        _log('UNVERIFIED_ROLE')
        return _deny(event_name, 'UNVERIFIED_ROLE') if mode == 'enforce' else {}
    repo = payload.get('cwd') or os.getcwd()
    try:
        repo = _repo_root(os.path.realpath(repo))
        head = _commit(repo)
        gate = os.environ.get('LLMTOOL_GATE', 'develop')
        manifest, contexts = _manifest_context(repo, head, role, gate)
        content_digest = _content_digest(manifest)
    except Exception as exc:
        reason = getattr(exc, 'code', None) or 'MANIFEST_UNAVAILABLE'
        _log(reason)
        return _deny(event_name, reason) if mode == 'enforce' else {}
    if part < 1 or part > len(contexts):
        _log('PART_OUT_OF_RANGE')
        return _deny(event_name, 'PART_OUT_OF_RANGE')
    if event_name == 'SubagentStart' and not payload.get('agent_id'):
        _log('DELIVERY_BINDING_MISSING')
        return {'hookSpecificOutput': {
            'hookEventName': event_name,
            'additionalContext': contexts[part - 1],
        }}
    base = _binding(payload, role, repo, content_digest)
    generation = (payload.get('source') if event_name == 'SessionStart'
                  else 'subagent-start')
    if generation not in _DELIVERY_GENERATIONS:
        generation = 'delivery'
    if part == 1:
        try:
            _revoke_delivery_receipts(base, len(contexts))
        except Exception:
            _log('LEDGER_UNREADABLE')
            if mode == 'enforce':
                return _deny(event_name, 'LEDGER_UNREADABLE')
    global _PENDING_DELIVERY
    _PENDING_DELIVERY = {
        'base': base, 'part': part, 'count': len(contexts),
        'payload': payload, 'role': role, 'repo': repo,
        'content_digest': content_digest, 'manifest': manifest,
        'generation': generation,
        'event_name': event_name,
    }
    return {'hookSpecificOutput': {
        'hookEventName': event_name,
        'additionalContext': contexts[part - 1],
    }}


def _protected_gates(command):
    # This is intentionally a conservative lexical search, not a shell parser.
    # It catches commands nested in wrappers, quotes, substitutions and -c args.
    gates = []
    for segment in re.split(r'[\n;&|(){}]+', command):
        tokens = re.findall(r'[A-Za-z0-9_.-]+', segment.lower())
        if not tokens:
            continue
        if tokens[0] in ('echo', 'printf') and '$(' not in segment and '`' not in segment:
            continue
        if 'git' in tokens and 'push' in tokens:
            gates.append('publish')
        if 'gh' in tokens:
            if 'pr' in tokens and 'merge' in tokens:
                gates.append('merge')
            if 'pr' in tokens and ('comment' in tokens or 'review' in tokens):
                gates.append('review')
            if 'issue' in tokens and 'comment' in tokens:
                gates.append('review')
        if ('task' in tokens and 'delegate' in tokens and
                ('llmtool' in tokens or ('-m' in tokens and 'llmtool' in tokens))):
            gates.append('dispatch')
        if ('task' in tokens and ('complete' in tokens or 'advance' in tokens) and
                ('llmtool' in tokens or ('-m' in tokens and 'llmtool' in tokens))):
            gates.append('complete')
    # Preserve order while avoiding redundant checks from nested shell forms.
    return list(dict.fromkeys(gates))


def _protected(command):
    return bool(_protected_gates(command))


def _check(payload, gate):
    role = _role(payload, 'PreToolUse')
    if role == 'UNVERIFIED':
        return {'status': 'UNVERIFIED', 'reason': 'UNVERIFIED_ROLE'}
    repo = os.path.realpath(payload.get('cwd') or os.getcwd())
    try:
        repo = _repo_root(repo)
        head = _commit(repo)
        manifest, _ = _manifest_context(
            repo, head, role, gate,
        )
        content_digest = _content_digest(manifest)
    except Exception as exc:
        return {'status': 'UNVERIFIED',
                'reason': getattr(exc, 'code', None) or 'MANIFEST_UNAVAILABLE'}
    agent_id = payload.get('agent_id') or 'main'
    model = payload.get('model') or os.environ.get('LLMTOOL_MODEL')
    values = [os.environ.get('LLMTOOL_TASK_ID'), os.environ.get('LLMTOOL_RUN_ID'),
              payload.get('session_id'), agent_id, role, 'claude', model, repo,
              content_digest, content_digest]
    if any(type(value) is not str or not value for value in values):
        return {'status': 'UNVERIFIED', 'reason': 'BINDING_MISSING'}
    try:
        return skill_ledger.check(*values, now=datetime.now(timezone.utc))
    except Exception:
        return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}


def handle(payload, part=1):
    """Return a Claude Code hook response for one decoded JSON payload."""
    if type(payload) is not dict:
        return _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}
    event_name = payload.get('hook_event_name') or payload.get('hookEventName')
    if event_name in ('SessionStart', 'SubagentStart'):
        source = payload.get('source')
        if event_name != 'SessionStart' or source in ('startup', 'resume', 'compact'):
            if type(part) is not int or part < 1:
                return _deny(event_name, 'PART_OUT_OF_RANGE')
            return _delivery(payload, event_name, part)
        _log('INPUT_INVALID')
        return _deny(event_name, 'INPUT_INVALID') if _mode() == 'enforce' else {}
    if event_name == 'PreToolUse' and payload.get('tool_name') == 'Bash':
        tool_input = payload.get('tool_input')
        command = tool_input.get('command') if type(tool_input) is dict else None
        if type(command) is not str:
            reason = 'BASH_INPUT_INVALID'
            if _mode() == 'enforce':
                return _deny(event_name, reason)
            _log(reason)
            return {}
        gates = _protected_gates(command)
        for gate in gates:
            if gate == 'COMMAND_UNPARSEABLE':
                reason = 'COMMAND_UNPARSEABLE'
                if _mode() == 'enforce':
                    return _deny(event_name, reason)
                _log(reason)
                return {}
            result = _check(payload, gate)
            if result.get('status') != 'VERIFIED':
                reason = result.get('reason') or 'UNVERIFIED'
                if _mode() == 'enforce':
                    return _deny(event_name, reason)
                _log(reason)
        return {}
    if (event_name == 'PreToolUse' and
            type(payload.get('tool_name')) is not str):
        reason = 'BASH_INPUT_INVALID'
        if _mode() == 'enforce':
            return _deny(event_name, reason)
        _log(reason)
        return {}
    if event_name == 'PreToolUse':
        return {}
    _log('INPUT_INVALID')
    return _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}


def main(argv=None):
    global _PENDING_DELIVERY
    _PENDING_DELIVERY = None
    part = 1
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        arguments = list(sys.argv[1:] if argv is None else argv)
        if len(arguments) > 2 or (arguments and arguments[0] != '--part'):
            raise ValueError('PART_OUT_OF_RANGE')
        if arguments:
            if len(arguments) != 2 or not arguments[1].isdigit():
                raise ValueError('PART_OUT_OF_RANGE')
            part = int(arguments[1])
        response = handle(payload, part)
    except ValueError as exc:
        reason = str(exc) if str(exc) == 'PART_OUT_OF_RANGE' else 'INPUT_INVALID'
        _log(reason)
        event_name = None
        if type(locals().get('payload')) is dict:
            event_name = payload.get('hook_event_name') or payload.get('hookEventName')
        if event_name not in ('SessionStart', 'SubagentStart', 'PreToolUse'):
            event_name = 'PreToolUse'
        response = _deny(
            event_name, reason,
        ) if (_mode() == 'enforce' or reason == 'PART_OUT_OF_RANGE') else {}
    except Exception:
        _log('INPUT_INVALID')
        event_name = None
        if type(locals().get('payload')) is dict:
            event_name = payload.get('hook_event_name') or payload.get('hookEventName')
        if event_name not in ('SessionStart', 'SubagentStart', 'PreToolUse'):
            event_name = 'PreToolUse'
        response = _deny(event_name, 'INPUT_INVALID') if _mode() == 'enforce' else {}
    if _emit(response):
        _finalize_delivery()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
