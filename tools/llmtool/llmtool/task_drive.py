"""Foreground driver for durable llmtool Task advancement."""

import time

from . import github_quota
from . import task_advance


# These names and result shapes are emitted by task_advance.advance. Review
# polling uses RECORD_REVIEW_RESULT with result.state NOT_READY. A ready PR
# carries WAITING_INTEGRATION until merged, but that is a stop/merge decision,
# not a reason to poll here.
_WAITING_STEPS = {'WAITING_CI', 'WAITING_EXECUTOR', 'WAITING_REPAIR'}
_UNKNOWN = {
    'UNKNOWN', 'OUTCOME_UNKNOWN', 'REVIEW_OUTCOME_UNKNOWN',
    'REPAIR_OUTCOME_UNKNOWN', 'MERGE_OUTCOME_UNKNOWN',
}
_TERMINAL = {'CLOSED', 'CI_FAILED', 'MERGED', 'MERGE_REFUSED', 'REFUSED'}
_MAX_PAUSE_SECONDS = 60


def _quota_stop(task_id, read_quota, last_result, completed):
    low = github_quota.low(read_quota())
    if low is None:
        return None
    return {
        'task_id': task_id,
        'step': 'WAITING_GITHUB_QUOTA',
        'result': {
            'resource': low['resource'],
            'remaining': low['remaining'],
            'reset': low['reset'],
            'last_result': last_result,
        },
        'codes': ['GITHUB_QUOTA_LOW'],
        'steps': completed,
    }


def drive(*, task_id, store=None, merge=False, max_steps=100, poll_seconds=5,
          advance_call=None, label_mac_ci_call=None,
          quota_read=github_quota.read, sleep=None, **adapters):
    """Drive foreground steps; stop without polling if the initial Developer is unfinished."""
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError('max_steps must be a positive integer')
    if type(poll_seconds) not in (int, float) or poll_seconds < 0:
        raise ValueError('poll_seconds must be non-negative')
    invoke = task_advance.advance if advance_call is None else advance_call
    label_mac_ci = (task_advance.label_mac_ci_for_skipped_check
                    if label_mac_ci_call is None else label_mac_ci_call)
    read_quota = github_quota.read if quota_read is None else quota_read
    pause = time.sleep if sleep is None else sleep
    max_pause = max(poll_seconds, _MAX_PAUSE_SECONDS)
    next_pause = poll_seconds
    # Keep an explicitly supplied PR authoritative for the whole drive. When
    # publication discovers the PR, carry it into every subsequent advance.
    pr_number = adapters.get('pr_number')
    completed = 0
    attempts = 0
    mac_ci_pending = False
    completion_mac_label_attempted = False
    last_result = None

    # Polls consume the bounded call budget so a permanently pending Task
    # cannot spin forever, but they do not count as completed workflow steps.
    while attempts < max_steps:
        if mac_ci_pending:
            advance_adapters = dict(adapters)
            advance_adapters['pr_number'] = pr_number
            merged = invoke(task_id=task_id, store=store, merge=True,
                            **advance_adapters)
            attempts += 1
            completed += 1
            if type(merged) is not dict:
                return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                        'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
            codes = merged.get('codes', [])
            if type(codes) is not list or any(type(code) is not str for code in codes):
                return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                        'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
            if type(merged.get('step')) is not str:
                return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                        'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
            last_result = merged
            if merged.get('step') == 'WAITING_CI':
                if attempts >= max_steps:
                    break
                stopped = _quota_stop(task_id, read_quota, last_result, completed)
                if stopped is not None:
                    return stopped
                pause(next_pause)
                next_pause = min(next_pause * 2, max_pause)
                continue
            # MERGE_REFUSED is a terminal decision for this drive invocation,
            # including when an exact-head Mac check is still pending.
            return dict(merged, steps=completed)

        advance_adapters = dict(adapters)
        advance_adapters['pr_number'] = pr_number
        result = invoke(task_id=task_id, store=store,
                        merge=False, **advance_adapters)
        attempts += 1
        if type(result) is not dict:
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
        last_result = result

        step = result.get('step')
        codes = result.get('codes', [])
        if type(codes) is not list or any(type(code) is not str for code in codes):
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}

        # Publication's one-step adapter returns {pr, head} without a step.
        if step is None and type(result.get('pr')) is int and type(result.get('head')) is str:
            if pr_number is None:
                pr_number = result['pr']
            completed += 1
            next_pause = poll_seconds
            continue
        if step is None and codes:
            return dict(result, step='REFUSED', steps=completed)
        if type(step) is not str:
            return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                    'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
        # Completion can stop on invalid Mac job evidence before the workflow
        # reaches READY_FOR_INTEGRATION. The label-only helper verifies the
        # exact-head skipped check and cannot enter the merge path.
        if (merge and step == 'WAITING_CI' and
                'CI_WORKFLOW_JOBS_INVALID' in codes):
            stopped = _quota_stop(task_id, read_quota, last_result, completed)
            if stopped is not None:
                return stopped
            if not completion_mac_label_attempted:
                label_adapters = {key: adapters[key] for key in
                                  ('github_get', 'merge_gh_runner', 'cwd')
                                  if key in adapters}
                label_result = label_mac_ci(task_id=task_id, store=store,
                    pr_number=pr_number, **label_adapters)
                if type(label_result) is dict:
                    if label_result.get('step') == 'MERGE_REFUSED':
                        return dict(label_result, task_id=task_id,
                                    steps=completed + 1)
                    if label_result.get('step') == 'WAITING_CI':
                        completion_mac_label_attempted = True
            if attempts >= max_steps:
                break
            pause(next_pause)
            next_pause = min(next_pause * 2, max_pause)
            continue
        waiting = (
            step in _WAITING_STEPS or
            (step == 'RECORD_REVIEW_RESULT' and
             type(result.get('result')) is dict and
             result['result'].get('state') == 'NOT_READY')
        )
        if waiting:
            if attempts >= max_steps:
                break
            stopped = _quota_stop(task_id, read_quota, last_result, completed)
            if stopped is not None:
                return stopped
            pause(next_pause)
            next_pause = min(next_pause * 2, max_pause)
            continue
        completed += 1
        next_pause = poll_seconds
        if step in _TERMINAL or step in _UNKNOWN or step.endswith('_UNKNOWN'):
            return dict(result, steps=completed)
        if step == 'READY_FOR_INTEGRATION':
            if not merge:
                return dict(result, steps=completed)
            if attempts >= max_steps:
                break
            # The merge flag selects only this explicit final step. Calling
            # advance(merge=True) earlier would skip the preceding workflow.
            advance_adapters = dict(adapters)
            advance_adapters['pr_number'] = pr_number
            merged = invoke(task_id=task_id, store=store, merge=True,
                            **advance_adapters)
            attempts += 1
            completed += 1
            if type(merged) is not dict:
                return {'task_id': task_id, 'step': 'UNKNOWN', 'result': None,
                        'codes': ['ADVANCE_RESULT_INVALID'], 'steps': completed}
            # A pending exact-head Mac check keeps the existing merge path
            # active; READY_FOR_INTEGRATION alone cannot retry the merge.
            if merged.get('step') == 'WAITING_CI':
                last_result = merged
                mac_ci_pending = True
                if attempts >= max_steps:
                    break
                stopped = _quota_stop(task_id, read_quota, last_result, completed)
                if stopped is not None:
                    return stopped
                pause(next_pause)
                next_pause = min(next_pause * 2, max_pause)
                continue
            return dict(merged, steps=completed)
        if step == 'MERGE_OWNER_ACCEPTANCE_MANUAL' or 'MERGE_OWNER_ACCEPTANCE_MANUAL' in codes:
            return dict(result, steps=completed)
        if codes and step not in _WAITING_STEPS:
            return dict(result, steps=completed)
        if step in ('REVIEWER_ATTENTION', 'DEVELOPER_ATTENTION', 'NEEDS_ATTENTION',
                    'REVIEW_NOT_POSTED', 'NEEDS_CWD', 'RUNTIME_NOT_ON_PATH'):
            return dict(result, steps=completed)

    return {'task_id': task_id, 'step': 'MAX_STEPS',
            'result': last_result.get('result') if type(last_result) is dict else None,
            'codes': list(last_result.get('codes', []))
                     if type(last_result) is dict and type(last_result.get('codes')) is list
                     else [], 'steps': completed}
