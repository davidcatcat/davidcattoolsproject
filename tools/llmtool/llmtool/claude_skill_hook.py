"""Claude Code hook for required skill delivery and protected Bash checks.

The hook cannot block a script file that invokes a protected command, shell
variable indirection, quote splitting, Claude's disableAllHooks setting, or
Claude's --safe-mode option. These measured bypasses remain out of reach.
"""

import argparse
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


def _mode():
    return 'enforce' if os.environ.get('LLMTOOL_HOOK_MODE') == 'enforce' else 'observe'


def _emit(value):
    sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')


def _deny(event_name, reason):
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
    chunks = [full_text[index:index + size]
              for index in range(0, len(full_text), size)] or ['']
    count = len(chunks)
    contexts = []
    for number, chunk in enumerate(chunks, 1):
        label = ('Required skill text: part {0} of {1}; '
                 'manifest_sha256={2}\n').format(number, count, digest)
        context = label + chunk
        if len(context) > MAX_CONTEXT_CHARS:
            raise ValueError('CONTEXT_TOO_LARGE')
        contexts.append(context)
    return manifest, contexts


def _record(payload, role, repo, head, manifest, event_name):
    agent_id = payload.get('agent_id')
    if not agent_id:
        if event_name == 'SubagentStart':
            _log('DELIVERY_BINDING_MISSING')
            return
        agent_id = 'main'
    model = payload.get('model') or os.environ.get('LLMTOOL_MODEL')
    values = {
        'task_id': os.environ.get('LLMTOOL_TASK_ID'),
        'run_id': os.environ.get('LLMTOOL_RUN_ID'),
        'session_id': payload.get('session_id'),
        'agent_id': agent_id,
        'role': role,
        'provider': 'claude',
        'model': model,
        'repo': repo,
        'head': head,
        'manifest_sha256': manifest['manifest_sha256'],
        'evidence_level': 'HOOK_DELIVERED',
    }
    if any(type(value) is not str or not value for value in values.values()):
        _log('DELIVERY_BINDING_MISSING')
        return
    try:
        skill_ledger.record(values)
    except Exception as exc:
        _log(getattr(exc, 'skill_ledger_code', 'LEDGER_UNREADABLE'))


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
    except Exception as exc:
        reason = getattr(exc, 'code', None) or str(exc) or 'MANIFEST_UNAVAILABLE'
        _log(reason)
        return _deny(event_name, reason) if mode == 'enforce' else {}
    if part < 1 or part > len(contexts):
        _log('PART_OUT_OF_RANGE')
        return _deny(event_name, 'PART_OUT_OF_RANGE') if mode == 'enforce' else {}
    if part == len(contexts):
        _record(payload, role, repo, head, manifest, event_name)
    return {'hookSpecificOutput': {
        'hookEventName': event_name,
        'additionalContext': contexts[part - 1],
    }}


def _unwrap(command):
    """Unwrap common env and bash -c forms without evaluating shell text."""
    current = command
    for _ in range(8):
        try:
            tokens = shlex.split(current, posix=True)
        except ValueError:
            return []
        if not tokens:
            return []
        executable = os.path.basename(tokens[0])
        if executable == 'env':
            index = 1
            while index < len(tokens):
                token = tokens[index]
                if token == '--':
                    index += 1
                    break
                if token.startswith('-'):
                    index += 2 if token in ('-u', '--unset', '-C', '--chdir') else 1
                    continue
                if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', token):
                    index += 1
                    continue
                break
            if index >= len(tokens):
                return []
            current = shlex.join(tokens[index:])
            continue
        if executable in ('bash', 'sh'):
            try:
                index = tokens.index('-c')
            except ValueError:
                return tokens
            if index + 1 >= len(tokens):
                return []
            current = tokens[index + 1]
            continue
        return tokens
    return []


def _protected(command):
    tokens = _unwrap(command)
    if not tokens:
        return False
    names = [os.path.basename(token) for token in tokens]
    for index, name in enumerate(names):
        rest = names[index:]
        if name == 'git' and 'push' in rest[1:]:
            return True
        if (name == 'gh' and len(rest) >= 3 and rest[1] == 'pr' and
                rest[2] == 'merge'):
            return True
        if (name == 'gh' and len(rest) >= 3 and rest[1] == 'pr' and
                rest[2] in ('comment', 'review') and
                'review_verdict' in command.lower()):
            return True
        if (name == 'llmtool' and len(rest) >= 3 and rest[1] == 'task' and
                rest[2] in ('delegate', 'complete', 'advance')):
            return True
    return False


def _check(payload):
    role = _role(payload, 'PreToolUse')
    if role == 'UNVERIFIED':
        return {'status': 'UNVERIFIED', 'reason': 'UNVERIFIED_ROLE'}
    repo = os.path.realpath(payload.get('cwd') or os.getcwd())
    try:
        head = _commit(repo)
        manifest, _ = _manifest_context(
            repo, head, role, os.environ.get('LLMTOOL_GATE', 'develop'),
        )
    except Exception as exc:
        return {'status': 'UNVERIFIED',
                'reason': getattr(exc, 'code', None) or 'MANIFEST_UNAVAILABLE'}
    agent_id = payload.get('agent_id') or 'main'
    model = payload.get('model') or os.environ.get('LLMTOOL_MODEL')
    values = [os.environ.get('LLMTOOL_TASK_ID'), os.environ.get('LLMTOOL_RUN_ID'),
              payload.get('session_id'), agent_id, role, 'claude', model, repo,
              head, manifest['manifest_sha256']]
    if any(type(value) is not str or not value for value in values):
        return {'status': 'UNVERIFIED', 'reason': 'BINDING_MISSING'}
    try:
        return skill_ledger.check(*values, now=datetime.now(timezone.utc))
    except Exception:
        return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}


def handle(payload, part=1):
    """Return a Claude Code hook response for one decoded JSON payload."""
    if type(payload) is not dict:
        raise ValueError('INPUT_INVALID')
    event_name = payload.get('hook_event_name') or payload.get('hookEventName')
    if event_name in ('SessionStart', 'SubagentStart'):
        source = payload.get('source')
        if event_name != 'SessionStart' or source in ('startup', 'resume', 'compact'):
            return _delivery(payload, event_name, part)
        _log('INPUT_INVALID')
        return _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}
    if (event_name == 'PreToolUse' and payload.get('tool_name') == 'Bash' and
            type(payload.get('tool_input')) is dict):
        command = payload['tool_input'].get('command')
        if type(command) is str and _protected(command):
            result = _check(payload)
            if result.get('status') == 'VERIFIED':
                return {}
            reason = result.get('reason') or 'UNVERIFIED'
            if _mode() == 'enforce':
                return _deny(event_name, reason)
            _log(reason)
        return {}
    if event_name == 'PreToolUse':
        return {}
    _log('INPUT_INVALID')
    return _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--part', type=int, default=1)
    try:
        args = parser.parse_args(argv)
        raw = sys.stdin.read()
        payload = json.loads(raw)
        response = handle(payload, args.part)
    except Exception:
        _log('INPUT_INVALID')
        response = _deny('PreToolUse', 'INPUT_INVALID') if _mode() == 'enforce' else {}
    _emit(response)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
