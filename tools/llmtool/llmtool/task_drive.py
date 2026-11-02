"""Foreground driver for durable llmtool Task advancement."""

import time

from . import task_advance


# These are the waiting steps emitted by task_advance.advance. In particular,
# review polling is represented by RECORD_REVIEW_RESULT with result.state
# NOT_READY rather than a dedicated WAITING_REVIEW step.
_WAITING = {
    'WAITING_CI', 'WAITING_EXECUTOR', 'WAITING_REPAIR', 'WAITING_INTEGRATION',
}
_UNKNOWN = {
    'UNKNOWN', 'OUTCOME_UNKNOWN', 'REVIEW_OUTCOME_UNKNOWN',
    'REPAIR_OUTCOME_UNKNOWN', 'MERGE_OUTCOME_UNKNOWN',
}
_TERMINAL = {'CLOSED', 'MERGED', 'MERGE_REFUSED', 'REFUSED'}


def drive(*, task_id, store=None, merge=False, max_steps=100, poll_seconds=5,
          advance_call=None, sleep=None, **adapters):
    """Drive foreground steps; stop for attention if the initial Developer is unfinished."""
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError('max_steps must be a positive integer')
    if type(poll_seconds) not in (int, float) or poll_seconds < 0:
        raise ValueError('poll_seconds must be non-negative')
    invoke = task_advance.advance if advance_call is None else advance_call
    pause = time.sleep if sleep is None else sleep
    completed = 0
    attempts = 0

    # Polls consume the bounded call budget so a permanently pending Task
    # cannot spin forever, but they do not count as completed workflow steps.
    while attempts < max_steps:
        result = invoke(task_id=task_id, store=store,
                        merge=False, **adapters)
        attempts += 1
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
            completed += 1
            continue
        if step is None and codes:
            return dict(result, step='REFUSED', steps=completed)
        if type(step) is not str:
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
        waiting = (step in _WAITING or
                   (step == 'RECORD_REVIEW_RESULT' and
                    type(result.get('result')) is dict and
                    result['result'].get('state') == 'NOT_READY'))
        if waiting:
            if attempts >= max_steps:
                break
            pause(poll_seconds)
            continue
        completed += 1
        if step in _TERMINAL or step in _UNKNOWN or step.endswith('_UNKNOWN'):
            return dict(result, steps=completed)
        if step == 'READY_FOR_INTEGRATION':
            if not merge:
                return dict(result, steps=completed)
            if attempts >= max_steps:
                break
            # The merge flag selects only this explicit final step. Calling
            # advance(merge=True) earlier would skip the preceding workflow.
            merged = invoke(task_id=task_id, store=store, merge=True, **adapters)
            attempts += 1
            completed += 1
            if type(merged) is not dict:
                return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                        'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
            return dict(merged, steps=completed)
        if step == 'MERGE_OWNER_ACCEPTANCE_MANUAL' or 'MERGE_OWNER_ACCEPTANCE_MANUAL' in codes:
            return dict(result, steps=completed)
        if codes and step not in _WAITING:
            return dict(result, steps=completed)
        if step in ('REVIEWER_ATTENTION', 'DEVELOPER_ATTENTION', 'NEEDS_ATTENTION',
                    'REVIEW_NOT_POSTED', 'NEEDS_CWD', 'RUNTIME_NOT_ON_PATH'):
            return dict(result, steps=completed)

    return {'task_id': task_id, 'step': 'MAX_STEPS', 'result': None,
            'codes': [], 'steps': completed}
