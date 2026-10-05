"""Offline contract CLI: explicit bounded input reads, no approval authority."""

import json
import os
import stat
import sys
from pathlib import Path

from .action_contract import (JSON_LIMIT, _path, parse_action, parse_approval,
    parse_repository_state, validate_approval, proposal_audit, approval_audit)
from .errors import LLMToolError


def add_action_contract_parsers(sub):
    action = sub.add_parser('action', help='Validate typed proposals offline; never execute')
    actions = action.add_subparsers(dest='action', required=True)
    validate = actions.add_parser('validate', help='Validate explicit version-1 action JSON')
    validate.add_argument('--file', required=True)
    validate.add_argument('--json', action='store_true')
    approval = sub.add_parser('approval', help='Check receipt bindings; never authorize execution')
    actions = approval.add_subparsers(dest='action', required=True)
    validate = actions.add_parser('validate', help='Validate supplied synthetic bindings only')
    validate.add_argument('--action', dest='action_file', required=True)
    validate.add_argument('--approval', dest='approval_file', required=True)
    validate.add_argument('--state', required=True, help='explicit synthetic repository-state JSON')
    validate.add_argument('--nonce', required=True, help='expected 64 lowercase hex nonce; synthetic input')
    validate.add_argument('--now', required=True, type=int, help='explicit synthetic epoch seconds')
    validate.add_argument('--consumed-nonce', action='append', default=[],
                          help='synthetic consumed nonce; no replay ledger')
    validate.add_argument('--json', action='store_true')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def read_contract_file(filename):
    """POSIX physical explicit .json files; never resolve links or discover inputs."""
    if os.name != 'posix' or not all(hasattr(os, name) for name in
                                   ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')):
        raise LLMToolError('action input unsupported platform')
    # Validate original spelling BEFORE abspath can erase a traversal component.
    _path(filename, absolute=filename.startswith('/'))
    path = Path(os.path.abspath(filename))
    _path(str(path), absolute=True)
    if (not path.name.endswith('.json') or
            path.parts[1] in ('dev', 'proc', 'sys')):
        raise LLMToolError('action input requires an explicit regular JSON file')
    directory = descriptor = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(path.anchor, flags)
        for part in path.parent.parts[1:]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                             dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > JSON_LIMIT:
            raise LLMToolError('action input requires a bounded single-link regular file')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            raw = stream.read(JSON_LIMIT + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if (len(raw) > JSON_LIMIT or _identity(before) != _identity(after) or
                _identity(before) != _identity(current)):
            raise LLMToolError('action input changed or exceeded its bound')
        return raw.decode('utf-8')
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def run_action_contract(args):
    """Every successful CLI binding result remains NOT_AUTHORIZED."""
    try:
        if args.command == 'action':
            action = parse_action(read_contract_file(args.file))
            output = {'version': 1, 'status': 'PROPOSED', 'authorized': False,
                      'action_digest': action.digest, 'kind': action.kind.value,
                      'audit': proposal_audit(action).to_dict()}
            line = 'PROPOSED action_sha256=' + action.digest + ' authorized=false'
            result = 0
        else:
            action = parse_action(read_contract_file(args.action_file))
            approval = parse_approval(read_contract_file(args.approval_file))
            state = parse_repository_state(read_contract_file(args.state))
            validation = validate_approval(action, approval, current_state=state,
                expected_nonce=args.nonce, now=args.now, consumed_nonces=args.consumed_nonce)
            output = {'version': 1, 'status': 'NOT_AUTHORIZED',
                      'state_checked': 'SUPPLIED_ONLY', **validation.to_dict(),
                      'audit': approval_audit(action, approval, validation).to_dict()}
            line = ('NOT_AUTHORIZED bindings=' + ('PASS' if validation.bindings_valid else 'FAIL') +
                    ' code=' + validation.code.value + ' authorized=false state=SUPPLIED_ONLY')
            result = 0 if validation.bindings_valid else 1
        # Build the entire output first. No raw action fields, paths or nonce display.
        rendered = json.dumps(output, sort_keys=True, ensure_ascii=True,
                              separators=(',', ':'), allow_nan=False) if args.json else line
        sys.stdout.write(rendered + '\n')
        return result
    except LLMToolError:
        print('llmtool: ACTION_CONTRACT_INVALID; details omitted', file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except (OSError, UnicodeError, ValueError):
        print('llmtool: ACTION_INPUT_INVALID; details omitted', file=sys.stderr)
        return 1
    except Exception:
        print('llmtool: ACTION_INTERNAL_ERROR; details omitted', file=sys.stderr)
        return 1
