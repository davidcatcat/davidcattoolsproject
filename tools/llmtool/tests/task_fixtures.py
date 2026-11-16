"""Shared TaskStore Task fixtures carrying a developer record (#1502).

Rule: never pass a developer record to ``TaskStore.create`` in tests. Use
``create_with_developer``. It creates the Task without a developer, then sets
the developer with ``store.compare_and_update(task_id, expected={'developer':
None}, developer=...)``, which is the supported path through the dispatch gate
and the developer transition checks. ``test_task_fixtures`` shows that a
developer passed straight to ``create`` is refused.

The records copy ``TaskStoreTests._codex_dispatch_task`` and
``_developer_assignment`` in ``test_task_store.py``. Each provider gets the
smallest field set the store accepts for a ``DEVELOPER_PENDING`` record.
Nothing here calls a provider. Install the shared test policy
(``worker_policy_fixture``) and trust (``review_trust_fixture``) first.
"""

import hashlib

from llmtool.task_store import new_id
from llmtool.task_submission_cli import _record

PROVIDERS = ('codex', 'claude', 'agy', 'opencode', 'gw-agent')


def pm_responded_task(route='codex'):
    """A PM_RESPONDED v2 Task with a LOW-risk pm_assignment and no developer."""
    request = 'Bounded non-sensitive Codex proposal task.'
    task_id, run_id = new_id(), new_id()
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    task = _record(
        task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
        state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
        backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
    task.update(
        pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                          'base_url': None},
        response='PM returned a bounded task.', error_code=None,
        pm_assignment={'schema_version': 1, 'route': route, 'risk': 'LOW',
                       'goal': 'Produce a proposal diff.',
                       'acceptance': ['the proposal is recorded']},
    )
    return task


def developer_assignment(provider='codex', **changes):
    """A DEVELOPER_PENDING developer record for ``provider``; ``changes`` override.

    Provider rules (task_store._validate):
    - opencode, agy and gw-agent need CLI_REPORTED_SESSION_ID.
    - opencode also carries execution=None and a token cap, so a later
      DEVELOPER_DISPATCHING step is valid.
    - agy needs the owner_assignment_ref key. None is accepted because the
      live gate decides.
    - claude is the record-only shape (NO_NATIVE_SESSION_ID_EXPOSED,
      provider_type None) with an owner_assignment_ref of None.
    """
    if provider not in PROVIDERS:
        raise ValueError('unknown fixture provider')
    developer = {
        'assignment_id': new_id(), 'selection_source': 'PM',
        'state': 'DEVELOPER_PENDING', 'provider_profile': provider,
        'provider_type': None, 'model': None,
        'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
        'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the proposal is recorded'],
        'context_sha256': 'b' * 64,
        'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
        'max_tokens': None, 'artifact_format': 'unified_diff',
        'artifact': None, 'artifact_sha256': None, 'usage': None,
        'error_code': None, 'created_at': '2026-10-23T00:00:00Z',
    }
    if provider in ('opencode', 'agy', 'gw-agent'):
        developer['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
    if provider == 'opencode':
        developer.update(execution=None, max_tokens=128)
    if provider in ('agy', 'claude'):
        developer['owner_assignment_ref'] = None
    developer.update(changes)
    return developer


def create_with_developer(store, provider='codex', **changes):
    """Create a Task without a developer, then add one by compare_and_update.

    The Task's pm_assignment route is ``provider``. ``changes`` override
    fields of the developer record. Returns the saved record.
    """
    task = pm_responded_task(route=provider)
    store.create(task)
    return store.compare_and_update(
        task['task_id'], expected={'developer': None},
        developer=developer_assignment(provider, **changes))
