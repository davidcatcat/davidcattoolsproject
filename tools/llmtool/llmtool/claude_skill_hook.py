"""Claude Code hook for required skill delivery and protected Bash checks.

The hook cannot block a script file that invokes a protected command, shell
variable indirection, quote splitting, Claude's disableAllHooks setting, or
Claude's --safe-mode option. An agent's own Bash can also run this module with
synthetic stdin, so same-user forgery remains possible. These measured
bypasses remain out of reach.
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

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


def _mode():
    return 'enforce' if os.environ.get('LLMTOOL_HOOK_MODE') == 'enforce' else 'observe'


def _emit(value):
    sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')


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


def _binding(payload, role, repo, content_digest, manifest):
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
        'head': content_digest,
        'manifest_sha256': content_digest,
    }


def _record(payload, role, repo, content_digest, manifest, event_name):
    values = _binding(payload, role, repo, content_digest, manifest)
    values['evidence_level'] = 'HOOK_DELIVERED'
    if any(type(value) is not str or not value for value in values.values()):
        _log('DELIVERY_BINDING_MISSING')
        return
    try:
        skill_ledger.record(values)
    except Exception:
        _log('LEDGER_UNREADABLE')


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


def _part_binding(base, part, count):
    values = dict(base)
    values['head'] = '{}:part:{}:{}'.format(base['head'], part, count)
    values['manifest_sha256'] = '{}:part:{}:{}'.format(
        base['manifest_sha256'], part, count,
    )
    return values


def _finalize_delivery():
    global _PENDING_DELIVERY
    pending = _PENDING_DELIVERY
    _PENDING_DELIVERY = None
    if pending is None:
        return
    base = pending['base']
    part_values = _part_binding(base, pending['part'], pending['count'])
    part_values['evidence_level'] = 'OBSERVED_READ'
    try:
        skill_ledger.record(part_values)
    except Exception:
        _log('LEDGER_UNREADABLE')
        return
    if pending['part'] != pending['count']:
        return
    for number in range(1, pending['count'] + 1):
        marker = _part_binding(base, number, pending['count'])
        try:
            result = skill_ledger.check(
                marker['task_id'], marker['run_id'], marker['session_id'],
                marker['agent_id'], marker['role'], marker['provider'],
                marker['model'], marker['repo'], marker['head'],
                marker['manifest_sha256'], now=datetime.now(timezone.utc),
            )
        except Exception:
            _log('LEDGER_UNREADABLE')
            return
        if result.get('status') != 'VERIFIED':
            return
    _record(pending['payload'], pending['role'], pending['repo'],
            pending['content_digest'], pending['manifest'], pending['event_name'])


def _delivery(payload, event_name, part):
    role = _role(payload, event_name)
    mode = _mode()
    if role == 'UNVERIFIED':
        _log('UNVERIFIED_ROLE')
        return _deny(event_name, 'UNVERIFIED_ROLE') if mode == 'enforce' else {}
    repo = payload.get('cwd') or os.getcwd()
    try:
        repo = os.path.realpath(repo)
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
    base = _binding(payload, role, repo, content_digest, manifest)
    global _PENDING_DELIVERY
    _PENDING_DELIVERY = {
        'base': base, 'part': part, 'count': len(contexts),
        'payload': payload, 'role': role, 'repo': repo,
        'content_digest': content_digest, 'manifest': manifest,
        'event_name': event_name,
    }
    return {'hookSpecificOutput': {
        'hookEventName': event_name,
        'additionalContext': contexts[part - 1],
    }}


def _shell_segments(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()`,')
    lexer.whitespace_split = True
    lexer.commenters = ''
    tokens = list(lexer)
    segments, current = [], []
    for token in tokens:
        if token and all(char in ';&|()`,' for char in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _unwrap_words(words, depth=0):
    if depth > 8:
        raise ValueError('COMMAND_UNPARSEABLE')
    result = []
    index = 0
    while index < len(words):
        word = words[index]
        base = os.path.basename(word)
        if base == 'env':
            index += 1
            while index < len(words):
                item = words[index]
                if item == '--':
                    index += 1
                    break
                if item.startswith('-'):
                    index += 2 if item in ('-u', '--unset', '-C', '--chdir') else 1
                    continue
                if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', item):
                    index += 1
                    continue
                break
            continue
        if base == 'sudo':
            index += 1
            while index < len(words) and words[index].startswith('-'):
                option = words[index]
                index += 2 if option in ('-u', '-g', '-h', '-p', '-C') else 1
            continue
        if base in ('bash', 'sh'):
            index += 1
            while index < len(words):
                option = words[index]
                if option == '--':
                    index += 1
                    break
                if option.startswith('-') and len(option) > 1:
                    if 'c' in option[1:]:
                        index += 1
                        if index >= len(words):
                            raise ValueError('COMMAND_UNPARSEABLE')
                        result.extend(_flatten_command(words[index], depth + 1))
                        index += 1
                        break
                    index += 1
                    continue
                break
            continue
        result.append(word)
        index += 1
    return result


def _flatten_command(command, depth=0):
    if depth > 8:
        raise ValueError('COMMAND_UNPARSEABLE')
    flattened = []
    for words in _shell_segments(command):
        flattened.extend(_unwrap_words(words, depth))
    return flattened


def _protected_gates(command):
    gates = []
    try:
        for words in _shell_segments(command):
            words = _unwrap_words(words)
            names = [os.path.basename(word) for word in words]
            for index, name in enumerate(names):
                rest = names[index:]
                if name == 'git' and 'push' in rest[1:]:
                    gates.append('publish')
                elif name == 'gh':
                    args = rest[1:]
                    while args and args[0] in ('-R', '--repo'):
                        args = args[2:]
                    while args and args[0].startswith('-'):
                        args = args[1:]
                    if len(args) >= 2 and args[0] == 'pr' and args[1] == 'merge':
                        gates.append('merge')
                    elif (len(args) >= 2 and args[0] == 'pr' and
                          args[1] in ('comment', 'review') and
                          ('review_verdict' in command.lower() or
                           any(value in ('--body-file', '-F') for value in args))):
                        gates.append('review')
                elif (name == 'llmtool' and len(rest) >= 3 and
                      rest[1] == 'task' and rest[2] == 'delegate'):
                    gates.append('dispatch')
                elif (name == 'llmtool' and len(rest) >= 3 and
                      rest[1] == 'task' and rest[2] == 'complete'):
                    gates.append('complete')
                elif (name == 'llmtool' and len(rest) >= 3 and
                      rest[1] == 'task' and rest[2] == 'advance'):
                    gates.append('complete')
    except (ValueError, TypeError):
        return ['COMMAND_UNPARSEABLE']
    return gates


def _protected(command):
    return bool(_protected_gates(command))


def _check(payload, gate):
    role = _role(payload, 'PreToolUse')
    if role == 'UNVERIFIED':
        return {'status': 'UNVERIFIED', 'reason': 'UNVERIFIED_ROLE'}
    repo = os.path.realpath(payload.get('cwd') or os.getcwd())
    try:
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
        arguments = list(sys.argv[1:] if argv is None else argv)
        if len(arguments) > 2 or (arguments and arguments[0] != '--part'):
            raise ValueError('PART_OUT_OF_RANGE')
        if arguments:
            if len(arguments) != 2 or not arguments[1].isdigit():
                raise ValueError('PART_OUT_OF_RANGE')
            part = int(arguments[1])
    except Exception:
        _log('PART_OUT_OF_RANGE')
        _emit({'error': 'PART_OUT_OF_RANGE'})
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        response = handle(payload, part)
    except Exception:
        _log('INPUT_INVALID')
        response = _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}
    _emit(response)
    _finalize_delivery()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
