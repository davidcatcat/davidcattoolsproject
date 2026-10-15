"""S1a explicit regular-file reads and output only. No runtime observation."""

import json
import os
import stat
import sys
from pathlib import Path

from .action_contract_cli import _identity, _input_spelling
from .errors import LLMToolError
from .task_lifecycle import (FILE_LIMIT, OUTPUT_LIMIT, invalid, inspect_task,
                             parse_events, parse_expectations)
from .task_verification import parse_task_contract


def add_task_parser(sub):
    task = sub.add_parser('task', help='Inspect caller-supplied task records offline; no authority')
    actions = task.add_subparsers(dest='action', required=True)
    inspect = actions.add_parser('inspect', help='Validate/replay independent pins and events; never execute')
    for option in ('contract', 'expect', 'events', 'observed-at'):
        inspect.add_argument('--' + option, required=True)
    inspect.add_argument('--json', action='store_true', required=True,
                         help='Structural result only; runtime is always NOT_VERIFIED')
    bundle = actions.add_parser('review-bundle', help='Prepare private local evidence; no review execution')
    for option in ('contract','expect','events','base','evidence','output'):
        bundle.add_argument('--'+option, required=True)
    bundle.add_argument('--json', action='store_true', required=True,
                        help='PREPARED means data only; runtime is NOT_VERIFIED')


def read_task_file(filename):
    """No-follow physical path, held directory FDs, bounded stable double-read."""
    if os.name != 'posix' or not all(hasattr(os, name) for name in
                                   ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')):
        invalid()
    _input_spelling(filename)
    path = Path(os.path.abspath(filename))
    _input_spelling(str(path))
    if path.parts[1] in ('dev', 'proc', 'sys'):
        invalid()
    directories = []
    edges = []
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directories.append(os.open(path.anchor, flags))
        for part in path.parent.parts[1:]:
            parent = directories[-1]
            child = os.open(part, flags, dir_fd=parent)
            directories.append(child)
            info = os.fstat(child)
            edges.append((parent, part, (info.st_dev, info.st_ino, info.st_mode)))
        directory = directories[-1]
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                             dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > FILE_LIMIT:
            invalid()
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            raw = stream.read(FILE_LIMIT + 1)
            stream.seek(0)
            repeated = stream.read(FILE_LIMIT + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if (len(raw) > FILE_LIMIT or raw != repeated or _identity(before) != _identity(after) or
                _identity(before) != _identity(current)):
            invalid()
        for parent, part, expected in edges:
            info = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_mode) != expected:
                invalid()
        return raw.decode('utf-8'), (before.st_dev, before.st_ino)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory in reversed(directories):
            os.close(directory)


def run_task_inspect(args):
    output = None
    result = 2
    try:
        # Validate the independent caller-selected envelope before touching events.
        expected_text, expected_inode = read_task_file(args.expect)
        expect = parse_expectations(expected_text)
        contract_text, _ = read_task_file(args.contract)
        contract = parse_task_contract(contract_text)
        event_text, event_inode = read_task_file(args.events)
        if expected_inode == event_inode:
            invalid('TASK_EXPECT_EVENTS_ALIAS')
        snapshot = inspect_task(contract, expect, parse_events(event_text), args.observed_at)
        output = snapshot.to_dict()
        result = snapshot.exit_code
    except (LLMToolError, OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        code = getattr(exc, 'lifecycle_code', 'TASK_INPUT_INVALID')
    except Exception:
        code = 'TASK_INTERNAL_ERROR'
    if output is None:
        output = {'schema_version': 1, 'reported_state': None,
                  'structural_validation': 'INVALID', 'runtime_evidence_status': 'NOT_VERIFIED',
                  'authorized': False, 'code': code, 'missing_evidence': []}
        print('llmtool: ' + code + '; details omitted', file=sys.stderr)
    try:
        rendered = json.dumps(output, sort_keys=True, ensure_ascii=True,
                              separators=(',', ':'), allow_nan=False)
        if len(rendered.encode('utf-8')) + 1 > OUTPUT_LIMIT:
            print('llmtool: TASK_OUTPUT_INVALID; details omitted', file=sys.stderr)
            return 2
        sys.stdout.write(rendered + '\n')
        sys.stdout.flush()
    except BrokenPipeError:
        # No implicit /dev/null write: preserve the original non-PASS exit.
        return result
    return result
