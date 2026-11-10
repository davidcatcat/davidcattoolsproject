"""Wiring helpers for the gateway agent developer route."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .developer_routes import RouteError
from .developer_routes.gw_agent import (
    GwAgentSessionLock,
    open_session_home,
    read_session_binding,
    session_binding_exists,
    write_session_binding,
)

_HEX_32 = re.compile(r'^[0-9a-f]{32}\Z')


def _normalize_refusal_code(code: str) -> str:
    """Return refusal code prefixed with DEVELOPER_GW_AGENT_ up to 64 chars."""
    if not isinstance(code, str):
        code = str(code)
    if code in ('BINDING_FAILED', 'GW_AGENT_BINDING_FAILED', 'SESSION_BINDING_FAILED'):
        return 'DEVELOPER_GW_AGENT_SESSION_BINDING_FAILED'
    prefix = 'DEVELOPER_GW_AGENT_'
    if code.startswith(prefix):
        suffix = code[len(prefix):]
    elif code.startswith('GW_AGENT_'):
        suffix = code[len('GW_AGENT_'):]
    else:
        suffix = code
    return (prefix + suffix)[:64]


class WiringRefused(Exception):
    """Raised when gw-agent session wiring refuses an operation."""

    def __init__(self, code: str):
        self.code = _normalize_refusal_code(code)
        super().__init__(self.code)


def open_locked_home(store_root, executor_task_id) -> Tuple[Any, GwAgentSessionLock]:
    """Open session home and acquire session lock, returning (home, lock)."""
    try:
        home = open_session_home(store_root, executor_task_id)
        lock = GwAgentSessionLock(home)
        lock.acquire()
        return home, lock
    except RouteError as exc:
        raise WiringRefused(exc.code) from exc
    except Exception as exc:
        raise WiringRefused('SESSION_HOME_UNSAFE') from exc


def _plan_resumed_run(
    binding: Dict[str, Any],
    *,
    repair_excerpt: str,
    data_class: Optional[str],
) -> Dict[str, Any]:
    """Build a run plan that resumes an existing session binding."""
    root_task_id = binding.get('root_task_id')
    session_id = binding.get('session_id')
    bound_data_class = binding.get('data_class')

    if data_class is None:
        effective_data_class = bound_data_class
    elif data_class == bound_data_class:
        effective_data_class = bound_data_class
    else:
        raise WiringRefused('SESSION_UNAVAILABLE')

    if repair_excerpt.strip():
        findings = [repair_excerpt]
    else:
        findings = []

    return {
        'gateway_task_id': root_task_id,
        'resume_session': session_id,
        'findings': findings,
        'data_class': effective_data_class,
    }


def plan_run(
    home,
    *,
    executor_task_id: str,
    task_id: str,
    repair_excerpt: Optional[str],
    data_class: Optional[str],
) -> Dict[str, Any]:
    """Plan gateway-agent run parameters for a first run or repair run."""
    if repair_excerpt is None:
        if session_binding_exists(home):
            try:
                binding = read_session_binding(home, executor_task_id=executor_task_id)
                if not isinstance(binding, dict):
                    raise ValueError('invalid binding structure')
                root_task_id = binding.get('root_task_id')
            except (RouteError, Exception) as exc:
                raise WiringRefused('SESSION_UNAVAILABLE') from exc

            if root_task_id == task_id:
                raise WiringRefused('SESSION_EXISTS')

            return _plan_resumed_run(binding, repair_excerpt='', data_class=data_class)

        if not isinstance(data_class, str) or not data_class:
            raise WiringRefused('SESSION_UNAVAILABLE')
        return {
            'gateway_task_id': task_id,
            'resume_session': None,
            'findings': [],
            'data_class': data_class,
        }
    elif isinstance(repair_excerpt, str):
        try:
            binding = read_session_binding(home, executor_task_id=executor_task_id)
            if not isinstance(binding, dict):
                raise ValueError('invalid binding structure')
        except (RouteError, Exception) as exc:
            raise WiringRefused('SESSION_UNAVAILABLE') from exc

        return _plan_resumed_run(binding, repair_excerpt=repair_excerpt, data_class=data_class)
    else:
        raise WiringRefused('SESSION_UNAVAILABLE')


def check_scope(plan: Dict[str, Any], binding_scope_digest: str, scope_digest: str) -> None:
    """Verify scope digest match on repair run; do nothing on first run."""
    if plan.get('resume_session') is None:
        return
    if binding_scope_digest != scope_digest:
        raise WiringRefused('SESSION_UNAVAILABLE')


def check_resumed_session(plan: Dict[str, Any], result_session_id: str) -> None:
    """Verify result session matches resumed session on repair run; do nothing on first run."""
    resume_session = plan.get('resume_session')
    if resume_session is None:
        return
    if result_session_id != resume_session:
        raise WiringRefused('SESSION_UNAVAILABLE')


def record_first_run(
    home,
    plan: Dict[str, Any],
    *,
    executor_task_id: str,
    result_session_id: str,
    scope_digest: str,
) -> bool:
    """Record initial session binding for first run; return True if written, False otherwise."""
    if plan.get('resume_session') is not None:
        return False

    if not isinstance(result_session_id, str) or _HEX_32.fullmatch(result_session_id) is None:
        return False

    try:
        write_session_binding(
            home,
            root_task_id=plan['gateway_task_id'],
            executor_task_id=executor_task_id,
            session_id=result_session_id,
            data_class=plan['data_class'],
            scope_digest=scope_digest,
        )
        return True
    except RouteError as exc:
        raise WiringRefused(exc.code) from exc
    except Exception as exc:
        raise WiringRefused('SESSION_BINDING_FAILED') from exc
