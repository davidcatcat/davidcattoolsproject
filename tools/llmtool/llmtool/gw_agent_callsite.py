"""Callsite context manager for gateway-agent sessions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .developer_routes import RouteError
from .developer_routes.gw_agent import _scope_digest, read_session_binding
from .gw_agent_wiring import (
    WiringRefused,
    check_resumed_session,
    check_scope as _wiring_check_scope,
    open_locked_home,
    plan_run,
    record_first_run,
)


class SessionCallSite:
    """Context manager binding a task run to a private gateway session home and lock."""

    def __init__(
        self,
        store_root: Any,
        *,
        executor_task_id: str,
        task_id: str,
        repair_excerpt: Optional[str] = None,
        data_class: Optional[str] = None,
    ) -> None:
        self.store_root = store_root
        self.executor_task_id = executor_task_id
        self.task_id = task_id
        self.repair_excerpt = repair_excerpt
        self._init_data_class = data_class
        self._home: Any = None
        self._lock: Any = None
        self._plan: Optional[Dict[str, Any]] = None

    def __enter__(self) -> SessionCallSite:
        home, lock = open_locked_home(self.store_root, self.executor_task_id)
        self._home = home
        self._lock = lock
        try:
            self._plan = plan_run(
                home,
                executor_task_id=self.executor_task_id,
                task_id=self.task_id,
                repair_excerpt=self.repair_excerpt,
                data_class=self._init_data_class,
            )
        except BaseException:
            self._release_lock()
            raise
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self._release_lock()
        return False

    def _release_lock(self) -> None:
        """Release session lock if held; safe to call repeatedly."""
        if self._lock is not None:
            lock = self._lock
            self._lock = None
            lock.release()

    @property
    def gateway_task_id(self) -> str:
        """Gateway task ID from the run plan."""
        if self._plan is None:
            raise RuntimeError('SessionCallSite not entered')
        return self._plan['gateway_task_id']

    @property
    def findings(self) -> List[str]:
        """Findings list from the run plan."""
        if self._plan is None:
            raise RuntimeError('SessionCallSite not entered')
        return list(self._plan['findings'])

    @property
    def data_class(self) -> str:
        """Data class from the run plan."""
        if self._plan is None:
            raise RuntimeError('SessionCallSite not entered')
        return self._plan['data_class']

    @property
    def resume_session(self) -> Optional[str]:
        """Session ID to resume, or None on a first run."""
        if self._plan is None:
            raise RuntimeError('SessionCallSite not entered')
        return self._plan['resume_session']

    @property
    def is_repair(self) -> bool:
        """True if this is a repair run, False on first run."""
        if self._plan is not None:
            return self._plan['resume_session'] is not None
        return self.repair_excerpt is not None

    def route_fields(self) -> Dict[str, Any]:
        """Return session_home and session_id fields for developer route call."""
        if self._home is None:
            raise RuntimeError('SessionCallSite not entered')
        return {
            'session_home': str(self._home),
            'session_id': self.resume_session,
        }

    def check_scope(self, task_input: Dict[str, Any]) -> None:
        """Verify scope digest match on repair run; do nothing on first run."""
        if not self.is_repair:
            return
        if self._home is None or self._plan is None:
            raise RuntimeError('SessionCallSite not entered')
        try:
            binding = read_session_binding(self._home, executor_task_id=self.executor_task_id)
        except RouteError as exc:
            raise WiringRefused(exc.code) from exc
        except Exception as exc:
            raise WiringRefused('SESSION_UNAVAILABLE') from exc

        bound_scope_digest = binding.get('scope_digest')
        current_scope_digest = _scope_digest(task_input)
        _wiring_check_scope(self._plan, bound_scope_digest, current_scope_digest)

    def after_result(self, result_session_id: str, task_input: Dict[str, Any]) -> bool:
        """Validate resumed session, record first run binding, and return write status."""
        if self._home is None or self._plan is None:
            raise RuntimeError('SessionCallSite not entered')
        check_resumed_session(self._plan, result_session_id)
        return record_first_run(
            self._home,
            self._plan,
            executor_task_id=self.executor_task_id,
            result_session_id=result_session_id,
            scope_digest=_scope_digest(task_input),
        )
