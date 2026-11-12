"""Caller PM session check for ``task advance`` and ``task drive`` (#1124).

The caller's PM session key comes only from the caller: an explicit
``--pm-session`` value, else the runtime's own session in the environment
(``CLAUDE_CODE_SESSION_ID`` for Claude Code, ``CODEX_THREAD_ID`` for Codex).
Each value is mapped with ``active_pm_inbox.pm_session_key``. The Task record
is never used to decide who the caller is; it is read only afterwards, to
check that its ``pm_session_id`` equals the caller key.

Refusal codes:

- ``PM_CALLER_SESSION_MISSING``: no explicit value and no environment value.
- ``PM_CALLER_SESSION_INVALID``: a supplied value that ``pm_session_key`` rejects.
- ``PM_CALLER_SESSION_AMBIGUOUS``: both environment values are set and no
  explicit value picks one.
- ``PM_CALLER_SESSION_CONFLICT``: the explicit value differs from every set
  environment value.
- ``PM_CALLER_SESSION_FENCED``: the caller's own native session has a #1126
  transfer fence; unreadable fences also refuse.
- ``TASK_RECORD_UNAVAILABLE``: the Task record cannot be read.
- ``PM_CALLER_NOT_TASK_OWNER``: the record has no ``pm_session_id`` or it is
  another session's.

An empty environment variable counts as unset. Only the ``task advance``,
``task drive`` and ``task remote-test`` CLI adapters use this check (#1230 A1).
Worker result callbacks (``task complete``, ``claude-result``), ``abandon``,
``delegate``, ``bind``, ``review-dispatch``, ``repair`` and the internal calls
that ``advance`` makes, including its remote test, are not checked here.

Known limit: fenced sessions are now refused, but the check is still not taken
under the adoption lock, so a Task adopted between the check and the call is
caught at the next guarded call.
"""

import os

from . import active_pm_inbox
from .errors import LLMToolError

ENVIRONMENT_KEYS = ('CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID')

SESSION_MISSING = 'PM_CALLER_SESSION_MISSING'
SESSION_INVALID = 'PM_CALLER_SESSION_INVALID'
SESSION_AMBIGUOUS = 'PM_CALLER_SESSION_AMBIGUOUS'
SESSION_CONFLICT = 'PM_CALLER_SESSION_CONFLICT'
SESSION_FENCED = 'PM_CALLER_SESSION_FENCED'
NOT_TASK_OWNER = 'PM_CALLER_NOT_TASK_OWNER'
TASK_RECORD_UNAVAILABLE = 'TASK_RECORD_UNAVAILABLE'


class PMCallerRefused(LLMToolError):
    """The caller is not established as the Task's PM; ``code`` says why."""

    def __init__(self, code):
        super().__init__('PM caller refused: ' + code)
        self.code = code
        self.task_store_error_code = code


def _key(value):
    try:
        return active_pm_inbox.pm_session_key(value)
    except LLMToolError:
        raise PMCallerRefused(SESSION_INVALID) from None


def resolve_caller_session(explicit=None, environ=None):
    """Return the caller's PM session key from ``explicit`` or the environment."""
    env = os.environ if environ is None else environ
    environment_values = []
    for name in ENVIRONMENT_KEYS:
        value = env.get(name)
        if value is not None and value != '':
            environment_values.append(value)
    if explicit is None and not environment_values:
        raise PMCallerRefused(SESSION_MISSING)
    explicit_key = None if explicit is None else _key(explicit)
    environment_keys = [_key(value) for value in environment_values]
    if explicit_key is not None:
        if environment_keys and explicit_key not in environment_keys:
            raise PMCallerRefused(SESSION_CONFLICT)
        return explicit_key
    if len(environment_keys) > 1:
        raise PMCallerRefused(SESSION_AMBIGUOUS)
    return environment_keys[0]


def owner_refusal_code(store, task_id, caller_key):
    """Return None when the stored Task belongs to ``caller_key``, else a code."""
    try:
        record = store.get(task_id)
    except Exception:
        return TASK_RECORD_UNAVAILABLE
    if type(record) is not dict:
        return TASK_RECORD_UNAVAILABLE
    owner = record.get('pm_session_id')
    if type(owner) is not str or not owner or type(caller_key) is not str or \
            owner != caller_key:
        return NOT_TASK_OWNER
    return None


def fence_refusal_code(caller_key, state_root=None):
    """Return SESSION_FENCED when caller_key is fenced, else None."""
    if not isinstance(caller_key, str) or not caller_key.startswith('pm-'):
        return None
    native = caller_key[3:]
    if active_pm_inbox.PM_NATIVE_SESSION_ID.fullmatch(native) is None:
        return None
    from . import pm_runtime
    if pm_runtime.read_session_fence(native, state_root) is not None:
        return SESSION_FENCED
    return None


def check_caller(store, task_id, explicit=None, environ=None, state_root=None):
    """Resolve the caller and require it to own the Task; return the caller key."""
    caller_key = resolve_caller_session(explicit, environ)
    code = fence_refusal_code(caller_key, state_root=state_root)
    if code is not None:
        raise PMCallerRefused(code)
    code = owner_refusal_code(store, task_id, caller_key)
    if code is not None:
        raise PMCallerRefused(code)
    return caller_key


def refusal(task_id, code):
    """The one machine-readable result printed for a refused caller."""
    return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
            'next': 'REFUSED', 'codes': [code], 'acked': []}


def guarded(call, store, task_id, caller_key, state_root=None):
    """Wrap ``call`` so every invocation re-checks Task ownership first.

    A refused invocation does not call ``call`` and raises ``PMCallerRefused``.
    Raising, rather than returning a refusal result, stops the whole drive at
    once: ``task_drive.drive`` does not treat a label-helper result as
    terminal, and a refusal on the last allowed step would otherwise be
    reported as ``MAX_STEPS`` with an earlier step's codes.
    """
    def invoke(**kwargs):
        code = fence_refusal_code(caller_key, state_root=state_root)
        if code is not None:
            raise PMCallerRefused(code)
        code = owner_refusal_code(store, task_id, caller_key)
        if code is not None:
            raise PMCallerRefused(code)
        return call(**kwargs)
    return invoke
