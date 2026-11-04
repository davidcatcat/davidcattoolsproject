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
        # Private content binding: these fields carry the canonical digest of
        # manifest entries (path, blob SHA-256, size), not Git HEAD or the
        # full manifest SHA-256. Delivery and checks use the same encoding.
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


def _part_binding(base, part, count, generation):
    values = dict(base)
    suffix = ':generation:{}:part:{}:{}'.format(generation, part, count)
    values['head'] = base['head'] + suffix
    values['manifest_sha256'] = base['manifest_sha256'] + suffix
    return values


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
    if pending['part'] != pending['count']:
        return
    for number in range(1, pending['count'] + 1):
        marker = _part_binding(base, number, pending['count'], generation)
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
    generation = payload.get('source') if event_name == 'SessionStart' else 'subagent-start'
    if type(generation) is not str or not generation:
        generation = 'unknown-start'
    if part == 1:
        # A new first part invalidates a previous complete receipt and any
        # same-generation observations, so later parts must belong to this run.
        bindings = (base,) + tuple(
            _part_binding(base, number, len(contexts), generation)
            for number in range(1, len(contexts) + 1))
        for binding in bindings:
            try:
                result = skill_ledger.check(
                    *(binding[key] for key in (
                        'task_id', 'run_id', 'session_id', 'agent_id', 'role',
                        'provider', 'model', 'repo', 'head', 'manifest_sha256')),
                    now=datetime.now(timezone.utc),
                )
                if result.get('status') == 'VERIFIED' and result.get('receipt_id'):
                    skill_ledger.revoke(result['receipt_id'], 'DELIVERY_RESTARTED')
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
        for words in _commands(command):
            name = os.path.basename(words[0])
            args = words[1:]
            if name == 'git' and args and args[0] == 'push':
                gates.append('publish')
            elif name == 'gh':
                is_merge, _ = _gh_subcommand(args, 'merge')
                is_comment, comment_args = _gh_subcommand(args, 'comment')
                is_review, review_args = _gh_subcommand(args, 'review')
                body_args = comment_args if is_comment else review_args
                has_body_file = any(
                    item in ('--body-file', '-F') or
                    item.startswith('--body-file=') or
                    (item.startswith('-F') and len(item) > 2)
                    for item in body_args
                )
                if is_merge:
                    gates.append('merge')
                elif ((is_comment or is_review) and
                      ('review_verdict' in command.lower() or has_body_file)):
                    gates.append('review')
            elif name == 'llmtool' and args[:2] == ['task', 'delegate']:
                gates.append('dispatch')
            elif name == 'llmtool' and args[:2] in (
                    ['task', 'complete'], ['task', 'advance']):
                gates.append('complete')
    except (ValueError, TypeError):
        return ['COMMAND_UNPARSEABLE']
    return gates


def _protected(command):
    return bool(_protected_gates(command))


def _commands(command, depth=0):
    """Yield executable command word lists, including supported wrappers."""
    if depth > 8:
        raise ValueError('COMMAND_UNPARSEABLE')
    for words in _shell_segments(command):
        if not words:
            continue
        executable = os.path.basename(words[0])
        if executable == 'env':
            index = 1
            while index < len(words):
                item = words[index]
                if item == '--':
                    index += 1
                    break
                if item.startswith('-'):
                    index += 2 if item in ('-u', '--unset', '-C', '--chdir') else 1
                elif re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', item):
                    index += 1
                else:
                    break
            if index < len(words):
                yield from _commands(shlex.join(words[index:]), depth + 1)
        elif executable in ('bash', 'sh'):
            index = 1
            while index < len(words):
                option = words[index]
                if option == '--':
                    index += 1
                    break
                if option.startswith('-') and len(option) > 1:
                    index += 1
                    if 'c' in option[1:]:
                        if index >= len(words):
                            raise ValueError('COMMAND_UNPARSEABLE')
                        yield from _commands(words[index], depth + 1)
                        break
                    continue
                break
        elif executable == 'sudo':
            index = 1
            while index < len(words) and words[index].startswith('-'):
                index += 2 if words[index] in ('-u', '-g', '-h', '-p', '-C') else 1
            if index < len(words):
                yield from _commands(shlex.join(words[index:]), depth + 1)
        else:
            yield words


def _gh_subcommand(args, verb):
    """Find a gh PR verb while accepting global options with attached values."""
    index = 0
    while index < len(args):
        value = args[index]
        if value == 'pr' and index + 1 < len(args):
            return args[index + 1] == verb, args[index + 2:]
        if value in ('-R', '--repo', '-c', '--config', '-H', '--hostname'):
            index += 2
        elif (value.startswith('--repo=') or value.startswith('--config=') or
              value.startswith('--hostname=') or
              (value.startswith('-R') and len(value) > 2)):
            index += 1
        elif value.startswith('-'):
            index += 1
        else:
            break
    return False, []


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
        response = _deny(
            payload.get('hook_event_name') or payload.get('hookEventName')
            if type(locals().get('payload')) is dict else 'PreToolUse',
            reason,
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
