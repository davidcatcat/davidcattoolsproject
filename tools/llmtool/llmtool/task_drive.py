"""Foreground driver for durable llmtool Task advancement."""

import time

from . import task_advance


_WAITING = {
    'WAITING_CI', 'WAITING_EXECUTOR', 'WAITING_REVIEW', 'WAITING_REPAIR',
    'WAITING_INTEGRATION', 'DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
    'REVIEW_PENDING',
}
_UNKNOWN = {
    'UNKNOWN', 'OUTCOME_UNKNOWN', 'REVIEW_OUTCOME_UNKNOWN',
    'REPAIR_OUTCOME_UNKNOWN', 'MERGE_OUTCOME_UNKNOWN',
}
_TERMINAL = {'CLOSED', 'MERGED', 'MERGE_REFUSED', 'REFUSED'}


def drive(*, task_id, store=None, merge=False, max_steps=100, poll_seconds=5,
          advance_call=None, sleep=None, **adapters):
    """Apply at most ``max_steps`` existing advance actions in the foreground."""
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError('max_steps must be a positive integer')
    if type(poll_seconds) not in (int, float) or poll_seconds < 0:
        raise ValueError('poll_seconds must be non-negative')
    invoke = task_advance.advance if advance_call is None else advance_call
    pause = time.sleep if sleep is None else sleep
    completed = 0

    while completed < max_steps:
        result = invoke(task_id=task_id, store=store,
                        merge=False, **adapters)
        completed += 1
        if type(result) is not dict:
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}

        step = result.get('step')
        codes = result.get('codes', [])
        if type(codes) is not list or any(type(code) is not str for code in codes):
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}

        # Publication's one-step adapter returns {pr, head} without a step.
        if step is None and type(result.get('pr')) is int and type(result.get('head')) is str:
            continue
        if step is None and codes:
            return dict(result, step='REFUSED', steps=completed)
        if type(step) is not str:
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
        if step in _TERMINAL or step in _UNKNOWN or step.endswith('_UNKNOWN'):
            return dict(result, steps=completed)
        if step == 'READY_FOR_INTEGRATION':
            if not merge:
                return dict(result, steps=completed)
            if completed >= max_steps:
                break
            # The merge flag selects only this explicit final step. Calling
            # advance(merge=True) earlier would skip the preceding workflow.
            merged = invoke(task_id=task_id, store=store, merge=True, **adapters)
            completed += 1
            if type(merged) is not dict:
                return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                        'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
            return dict(merged, steps=completed)
        if step == 'MERGE_OWNER_ACCEPTANCE_MANUAL' or 'MERGE_OWNER_ACCEPTANCE_MANUAL' in codes:
            return dict(result, steps=completed)
        if codes and step not in _WAITING:
            return dict(result, steps=completed)
        if step in _WAITING or any(code in _WAITING for code in codes):
            if completed >= max_steps:
                break
            pause(poll_seconds)
            continue
        if step in ('REVIEWER_ATTENTION', 'DEVELOPER_ATTENTION', 'NEEDS_ATTENTION',
                    'REVIEW_NOT_POSTED', 'NEEDS_CWD', 'RUNTIME_NOT_ON_PATH'):
            return dict(result, steps=completed)

    return {'task_id': task_id, 'step': 'MAX_STEPS', 'result': None,
            'codes': [], 'steps': completed}
