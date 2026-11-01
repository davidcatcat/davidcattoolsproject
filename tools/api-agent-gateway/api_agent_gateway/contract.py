"""CLI/JSON contract: task.json, profiles, result.json, statuses, exit codes."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from .audit import canonical, is_int, sha256_hex
from .confine import ConfinementError, Scope

SCHEMA_VERSION = 1

DONE = 'DONE'
BLOCKED = 'BLOCKED'
BUDGET_EXCEEDED = 'BUDGET_EXCEEDED'
MODEL_ERROR = 'MODEL_ERROR'
POLICY_REFUSED = 'POLICY_REFUSED'

EXIT_CODES = {DONE: 0, BLOCKED: 3, MODEL_ERROR: 4, POLICY_REFUSED: 5, BUDGET_EXCEEDED: 6}
EXIT_USAGE = 2
EXIT_INTERNAL = 1

BUDGET_LIMITS = {
    'max_turns': (1, 500),
    'max_input_tokens': (1, 50_000_000),
    'max_output_tokens': (1, 5_000_000),
    'max_wall_seconds': (1, 86_400),
    'max_tool_result_bytes': (256, 1_048_576),
    'max_sent_bytes': (1024, 512 * 1024 * 1024),
}
TASK_KEYS = frozenset((
    'schema_version', 'task_id', 'source_sha', 'goal', 'acceptance', 'read_paths',
    'write_paths', 'egress_basis', 'data_class', 'budgets', 'findings',
))
SCOPE_KEYS = ('read_paths', 'write_paths', 'egress_basis', 'data_class', 'budgets')
MAX_SESSION_EXPIRY_HOURS = 72
MAX_JSON_FILE = 1024 * 1024
_TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
_SHA = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
_DATA_CLASS = re.compile(r'[a-z][a-z0-9_-]{0,63}')
_ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]{0,127}')
_RESERVED_BODY = frozenset(('model', 'messages', 'tools', 'tool_choice', 'max_tokens', 'stream',
                            'functions', 'function_call', 'n'))


class Refusal(Exception):
    """End the invocation with a status and a distinct code."""

    def __init__(self, status: str, code: str, message: str = ''):
        super().__init__(f'{status}:{code}')
        self.status = status
        self.code = code
        self.message = message or code


def read_json_file(path: str, what: str) -> tuple[Any, bytes]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
    except OSError as exc:
        raise Refusal(POLICY_REFUSED, f'{what}_UNREADABLE') from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_JSON_FILE:
            raise Refusal(POLICY_REFUSED, f'{what}_UNREADABLE')
        data = os.read(fd, MAX_JSON_FILE + 1)
    finally:
        os.close(fd)
    try:
        return json.loads(data.decode('utf-8')), data
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refusal(POLICY_REFUSED, f'{what}_INVALID') from exc


def _strs(value: Any, *, max_items: int, max_len: int, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items or (not allow_empty and not value):
        raise ValueError
    for item in value:
        if not isinstance(item, str) or not item or len(item) > max_len:
            raise ValueError
    return list(value)


@dataclass
class Task:
    task_id: str
    source_sha: str
    goal: str
    acceptance: list[str]
    read_paths: list[str]
    write_paths: list[str]
    egress_basis: str
    data_class: str
    budgets: dict[str, int]
    findings: list[str]
    sha256: str
    scope: Scope = field(repr=False)

    def scope_fields(self) -> dict[str, Any]:
        return {
            'read_paths': sorted(set(self.read_paths)),
            'write_paths': sorted(set(self.write_paths)),
            'egress_basis': self.egress_basis,
            'data_class': self.data_class,
            'budgets': dict(sorted(self.budgets.items())),
        }

    def scope_digest(self) -> str:
        return sha256_hex(canonical(self.scope_fields()))


def parse_task(obj: Any, raw: bytes) -> Task:
    def bad(code: str = 'TASK_INVALID') -> Refusal:
        return Refusal(POLICY_REFUSED, code)

    if not isinstance(obj, dict) or not is_int(obj.get('schema_version'), SCHEMA_VERSION):
        raise bad()
    if set(obj) - TASK_KEYS:
        raise bad()
    basis = obj.get('egress_basis')
    if not isinstance(basis, str) or not basis.strip() or len(basis) > 2000:
        raise bad('EGRESS_BASIS_MISSING')
    data_class = obj.get('data_class')
    if not isinstance(data_class, str) or not _DATA_CLASS.fullmatch(data_class):
        raise bad('DATA_CLASS_MISSING')
    try:
        task_id = obj['task_id']
        source_sha = obj['source_sha']
        goal = obj['goal']
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError
        if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
            raise ValueError
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 20000:
            raise ValueError
        acceptance = _strs(obj.get('acceptance', []), max_items=50, max_len=4000)
        read_paths = _strs(obj['read_paths'], max_items=500, max_len=1024, allow_empty=False)
        write_paths = _strs(obj.get('write_paths', []), max_items=500, max_len=1024)
        findings = _strs(obj.get('findings', []), max_items=50, max_len=8000)
        budgets_in = obj['budgets']
        if not isinstance(budgets_in, dict) or set(budgets_in) != set(BUDGET_LIMITS):
            raise ValueError
        budgets = {}
        for key, (low, high) in BUDGET_LIMITS.items():
            value = budgets_in[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError
            budgets[key] = value
    except (KeyError, ValueError, TypeError) as exc:
        raise bad() from exc
    try:
        scope = Scope(read_paths, write_paths)
    except ConfinementError as exc:
        raise bad('TASK_PATH_INVALID') from exc
    return Task(task_id, source_sha, goal, acceptance, read_paths, write_paths, basis,
                data_class, budgets, findings, sha256_hex(raw), scope)


def _loopback(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class Profile:
    name: str
    adapter: str
    base_url: str
    model: str
    auth_env: Optional[str]
    context_cap_tokens: int
    max_output_tokens: int
    allowed_data_classes: list[str]
    session_expiry_hours: int
    timeout_seconds: int
    tool_choice: str
    request_extra: dict
    sha256: str


def parse_profile(profiles: Any, name: str) -> Profile:
    def bad(code: str = 'PROFILE_INVALID') -> Refusal:
        return Refusal(POLICY_REFUSED, code)

    if not isinstance(profiles, dict) or not is_int(profiles.get('schema_version'), SCHEMA_VERSION):
        raise bad()
    table = profiles.get('profiles')
    if not isinstance(table, dict) or not isinstance(table.get(name), dict):
        raise bad('PROFILE_NOT_FOUND')
    p = table[name]
    try:
        adapter = p['adapter']
        if adapter not in ('openai-chat-tools', 'text-tools'):
            raise ValueError
        base_url = p['base_url']
        parts = urlsplit(base_url)
        if (parts.scheme not in ('https', 'http') or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError
        if parts.scheme == 'http' and not _loopback(parts.hostname):
            raise ValueError
        model = p['model']
        if not isinstance(model, str) or not model or len(model) > 200:
            raise ValueError
        auth_env = p.get('auth_env')
        if auth_env is not None and (not isinstance(auth_env, str) or not _ENV_NAME.fullmatch(auth_env)):
            raise ValueError
        ctx = p['context_cap_tokens']
        out = p['max_output_tokens']
        if type(ctx) is not int or type(out) is not int or not (256 <= ctx <= 10_000_000) or not (1 <= out < ctx):
            raise ValueError
        classes = _strs(p['allowed_data_classes'], max_items=50, max_len=64, allow_empty=False)
        expiry = p.get('session_expiry_hours', MAX_SESSION_EXPIRY_HOURS)
        if type(expiry) is not int or not 1 <= expiry <= MAX_SESSION_EXPIRY_HOURS:
            raise bad('PROFILE_EXPIRY_INVALID')
        timeout = p.get('timeout_seconds', 120)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError
        tool_choice = p.get('tool_choice', 'auto')
        if tool_choice not in ('auto', 'required'):
            raise ValueError
        extra = p.get('request_extra', {})
        if not isinstance(extra, dict) or set(extra) & _RESERVED_BODY or len(canonical(extra)) > 4096:
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise bad() from exc
    if adapter == 'text-tools':
        raise bad('ADAPTER_NOT_IMPLEMENTED')
    return Profile(name, adapter, base_url.rstrip('/'), model, auth_env, ctx, out, classes, expiry,
                   timeout, tool_choice, extra, sha256_hex(canonical(p)))


def write_result(path: str, result: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open(path, flags, 0o600)
    try:
        data = json.dumps(result, indent=2, sort_keys=True).encode('utf-8') + b'\n'
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def outside(path: str, root: str) -> bool:
    """True if ``path`` (possibly not yet existing) resolves outside ``root``."""
    real_root = os.path.realpath(root)
    real = os.path.realpath(os.path.dirname(os.path.abspath(path)) or '.')
    return os.path.commonpath([real, real_root]) != real_root
