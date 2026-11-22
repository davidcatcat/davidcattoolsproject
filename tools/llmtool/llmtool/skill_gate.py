"""#1502 WP-F minimal gate: missing card-delivery evidence blocks completion.

Scope (Advisor ruling on #1502; Owner "你接手" 2026-11-23): only the protected
completion transitions (``task complete`` and the direct result event, which
the delegate auto-callbacks and the Reviewer resume also pass through) read
the ledger. The dispatch-claim, Reviewer-claim and Debug-entry gates and the
prepare/confirm/commit producer of the full design
(docs/design/wpf-delivery-evidence-gate-1502.md) stay not planned.

The gate is keyed on the run's own ledger state, never on a wall clock:
- Claude / AGY / OpenCode Developer run that llmtool pinned for card delivery
  (a DISPATCH_PIN row exists for this Task, run and route): it must also have
  a non-revoked CONTROLLER_DELIVERED receipt bound to that pin, else refused.
  A pinned run is one that llmtool dispatched with role cards, so the gate
  never applies to older records, to runs that never reached the pin, or to
  runs llmtool did not launch.
- A run without a pin (older records, ``task claude-bind``, Codex, gw-agent)
  is not gated here: no cards were sent, so there is nothing to verify.
- An unreadable or broken ledger refuses (fail closed) for card routes.
Every refusal is a definite SKILL_DELIVERY_* code with the next step; the
remedy is a new Developer run, because delivery evidence cannot be written
by hand.
"""

from . import skill_ledger
from .errors import LLMToolError

CARD_ROUTES = {'agy': 'agy', 'claude': 'claude-developer',
               'opencode': 'opencode-developer'}

_NEXT = ('; next: dispatch a new Developer run for this work (delivery evidence '
         'cannot be added afterwards)')


def _refuse(code, task):
    raise LLMToolError('%s: task=%s run=%s%s' % (code, task.get('task_id'),
                                                 task.get('run_id'), _NEXT))


def check_completion(task):
    """Raise SKILL_DELIVERY_* unless the Task's Developer result may complete.

    Returns the class that passed: NO_DEVELOPER, NOT_CARD_ROUTE, NOT_PINNED
    or VERIFIED.
    """
    developer = task.get('developer') if type(task) is dict else None
    if type(developer) is not dict:
        return 'NO_DEVELOPER'
    route = CARD_ROUTES.get(developer.get('provider_profile'))
    if route is None:
        return 'NOT_CARD_ROUTE'
    task_id, run_id = task.get('task_id'), task.get('run_id')
    if not all(type(value) is str and value for value in (task_id, run_id)):
        return 'NOT_PINNED'  # no pin can be keyed without a Task and run id
    status = skill_ledger.run_delivery_status(task_id, run_id, route)
    if status.get('status') == skill_ledger.STATUS_VERIFIED:
        return 'VERIFIED'
    if status.get('status') == 'NO_PIN':
        return 'NOT_PINNED'
    _refuse(status.get('reason') or skill_ledger.DELIVERY_RECEIPT_MISSING, task)
