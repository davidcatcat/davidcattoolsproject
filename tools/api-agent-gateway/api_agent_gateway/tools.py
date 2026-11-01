"""The complete v1 tool set: list_dir, read_file, grep, apply_edit, finish."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .confine import ConfinementError, ConfinementViolation, Workspace

TOOL_SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'list_dir', 'description': 'List a directory inside the declared scope.',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': {'path': {'type': 'string', 'description': 'Relative path; "" for the root.'}},
                       'required': ['path']}}},
    {'type': 'function', 'function': {
        'name': 'read_file', 'description': 'Read a line range of a text file in the read set.',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': {'path': {'type': 'string'},
                                      'start_line': {'type': 'integer', 'minimum': 1},
                                      'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': 2000}},
                       'required': ['path']}}},
    {'type': 'function', 'function': {
        'name': 'grep', 'description': 'Find a literal string in files of the read set (bounded).',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': {'pattern': {'type': 'string'}, 'path': {'type': 'string'},
                                      'ignore_case': {'type': 'boolean'},
                                      'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 200}},
                       'required': ['pattern']}}},
    {'type': 'function', 'function': {
        'name': 'apply_edit',
        'description': 'Replace exactly one occurrence of old with new, or create a new file (create=true).',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': {'path': {'type': 'string'}, 'old': {'type': 'string'},
                                      'new': {'type': 'string'}, 'create': {'type': 'boolean'}},
                       'required': ['path', 'new']}}},
    {'type': 'function', 'function': {
        'name': 'finish', 'description': 'End the task with a short summary.',
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': {'summary': {'type': 'string'},
                                      'status': {'type': 'string', 'enum': ['done', 'blocked']}},
                       'required': ['summary']}}},
]

_ARGS = {
    'list_dir': {'path': str},
    'read_file': {'path': str, 'start_line': int, 'max_lines': int},
    'grep': {'pattern': str, 'path': str, 'ignore_case': bool, 'max_results': int},
    'apply_edit': {'path': str, 'old': str, 'new': str, 'create': bool},
    'finish': {'summary': str, 'status': str},
}
_REQUIRED = {'list_dir': ('path',), 'read_file': ('path',), 'grep': ('pattern',),
             'apply_edit': ('path', 'new'), 'finish': ('summary',)}
MAX_ARGUMENT_BYTES = 2 * 1024 * 1024 + 4096


@dataclass
class ToolOutcome:
    content: str
    tool: str
    ok: bool
    code: Optional[str] = None
    path: Optional[str] = None
    changed_path: Optional[str] = None
    finish: Optional[dict] = None
    extra: dict = field(default_factory=dict)


def _error(tool: str, code: str, message: str, path: Optional[str] = None) -> ToolOutcome:
    return ToolOutcome(json.dumps({'error': code, 'message': message}), tool, False, code, path)


def _parse_args(name: str, raw: str) -> dict:
    if len(raw.encode('utf-8')) > MAX_ARGUMENT_BYTES:
        raise ValueError('arguments too large')
    args = json.loads(raw) if raw.strip() else {}
    if not isinstance(args, dict):
        raise ValueError('arguments must be an object')
    spec = _ARGS[name]
    for key, value in args.items():
        if key not in spec:
            raise ValueError(f'unknown argument {key!r}')
        expected = spec[key]
        if expected is int and (type(value) is not int):
            raise ValueError(f'{key} must be an integer')
        if expected is not int and not isinstance(value, expected):
            raise ValueError(f'{key} has the wrong type')
    for key in _REQUIRED[name]:
        if key not in args:
            raise ValueError(f'missing argument {key!r}')
    return args


class ToolExecutor:
    def __init__(self, workspace: Workspace, max_result_bytes: int):
        self.ws = workspace
        self.max_result_bytes = max_result_bytes

    def _fit(self, payload: dict) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text.encode('utf-8')) > self.max_result_bytes:
            raise ConfinementError('RESULT_TOO_LARGE',
                                   f'result exceeds {self.max_result_bytes} bytes; request less')
        return text

    def execute(self, name: str, raw_args: str) -> ToolOutcome:
        if name not in _ARGS:
            return _error('unknown', 'UNKNOWN_TOOL', 'no such tool')
        try:
            args = _parse_args(name, raw_args)
        except (ValueError, RecursionError) as exc:
            return _error(name, 'BAD_ARGUMENTS', str(exc)[:200])
        path = args.get('path')
        try:
            return getattr(self, '_' + name)(args)
        except ConfinementViolation:
            raise
        except ConfinementError as exc:
            return _error(name, exc.code, exc.message, path if isinstance(path, str) else None)

    def _list_dir(self, args: dict) -> ToolOutcome:
        rel, entries, truncated = self.ws.list_dir(args['path'])
        return ToolOutcome(self._fit({'path': rel, 'entries': entries, 'truncated': truncated}),
                           'list_dir', True, path=rel)

    def _read_file(self, args: dict) -> ToolOutcome:
        rel, text = self.ws.read_text(args['path'])
        lines = text.splitlines(keepends=True)
        start = max(1, args.get('start_line', 1))
        count = min(max(1, args.get('max_lines', 400)), 2000)
        chunk = lines[start - 1:start - 1 + count]
        # Shrink to the byte cap rather than fail when the default range is big.
        overhead = 256 + len(rel)
        while chunk and len(''.join(chunk).encode('utf-8')) + overhead > self.max_result_bytes:
            chunk = chunk[:max(1, len(chunk) // 2)] if len(chunk) > 1 else []
        end = start - 1 + len(chunk)
        payload = {'path': rel, 'start_line': start, 'end_line': end, 'total_lines': len(lines),
                   'truncated': end < len(lines), 'content': ''.join(chunk)}
        if not chunk and start <= len(lines):
            raise ConfinementError('RESULT_TOO_LARGE', 'a single line exceeds the result cap')
        return ToolOutcome(self._fit(payload), 'read_file', True, path=rel)

    def _grep(self, args: dict) -> ToolOutcome:
        pattern = args['pattern']
        if not pattern or len(pattern) > 500:
            raise ConfinementError('BAD_ARGUMENTS', 'pattern must be 1-500 characters')
        limit = min(max(1, args.get('max_results', 50)), 200)
        results, truncated = self.ws.grep(pattern, args.get('path', ''),
                                          ignore_case=args.get('ignore_case', False), max_results=limit)
        while results and len(json.dumps({'matches': results}).encode('utf-8')) + 128 > self.max_result_bytes:
            results = results[:len(results) // 2]
            truncated = True
        return ToolOutcome(self._fit({'matches': results, 'truncated': truncated}), 'grep', True,
                           path=args.get('path', ''))

    def _apply_edit(self, args: dict) -> ToolOutcome:
        rel = self.ws.apply_edit(args['path'], args.get('old'), args['new'], args.get('create', False))
        return ToolOutcome(json.dumps({'ok': True, 'path': rel}), 'apply_edit', True, path=rel, changed_path=rel)

    def _finish(self, args: dict) -> ToolOutcome:
        status = args.get('status', 'done')
        if status not in ('done', 'blocked'):
            raise ConfinementError('BAD_ARGUMENTS', 'status must be done or blocked')
        return ToolOutcome(json.dumps({'ok': True}), 'finish', True,
                           finish={'summary': args['summary'][:4000], 'status': status})
