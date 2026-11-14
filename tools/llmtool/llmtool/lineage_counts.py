"""#1502 WP-D L1: chain-wide repair counts across Task continuations.

A Task may continue one earlier Task through its write-once 'continues' field,
set only by `task record-pm --continues TASK`. The chain of a Task is the Task,
its predecessor, that Task's predecessor and so on: at most MAX_CHAIN Tasks.
Review repairs, failed review HEADs and test failures are summed over the
chain, so a continuation never resets a limit.

Fail closed (spec section 7): an unreadable or malformed predecessor, a cycle
or a chain over MAX_CHAIN makes every count repair_policy.UNKNOWN, which the
policy treats as the limit. A count is never 0 by doubt.
"""

import re

from .errors import LLMToolError
from .repair_policy import UNKNOWN

MAX_CHAIN = 16
_HEX32 = re.compile(r'[0-9a-f]{32}')
_EC2_SUFFIX = '-ec2'   # review_repair.EC2_FAMILY_SUFFIX


def _hex32(value):
    return type(value) is str and _HEX32.fullmatch(value) is not None


def _refuse(code):
    error = LLMToolError(code)
    error.continues_code = code
    raise error from None


def chain_records(store, task):
    """[task, predecessor, ...] in chain order, or None when any link is in doubt."""
    if type(task) is not dict:
        return None
    # The Task's own ID is not checked here: a Task without continues counts as before.
    records, seen, current = [task], {task.get('task_id')}, task
    while 'continues' in current:
        previous = current['continues']
        if (not _hex32(previous) or previous in seen or len(records) >= MAX_CHAIN or
                store is None):
            return None
        try:
            record = store.get(previous)
        except Exception:
            return None
        if type(record) is not dict or record.get('task_id') != previous:
            return None
        records.append(record)
        seen.add(previous)
        current = record
    return records


def _unknown():
    return {'chain': UNKNOWN, 'review_repairs': UNKNOWN, 'review_heads': UNKNOWN,
            'test_fails': UNKNOWN}


def lineage_counts(store, task, *, base_family):
    """Chain-wide counts for one Task; each value is known or UNKNOWN.

    review_repairs: review repair claims. The Task's own claims are counted as
      before (the stored base_family, legacy EC2 claims included); each
      predecessor adds every review-family claim (family not '-ec2'), any PR.
    review_heads: {head: 'FAIL'} for those claims, one entry per HEAD.
    test_fails: EC2 and CI repair claims. The Task's own '<base>-ec2' claims and
      legacy EC2 claims in base_family; each predecessor adds every '-ec2' claim
      and every legacy EC2 claim, any PR.
    chain: the Task IDs of the chain, the Task first.
    """
    from .review_repair import ec2_claim_kind
    if type(base_family) is not str:
        return _unknown()
    records = chain_records(store, task)
    if records is None:
        return _unknown()
    review, tests, heads, heads_known = 0, 0, {}, True
    for index, record in enumerate(records):
        claims = record.get('review_repair_claims', [])
        if type(claims) is not list or any(type(item) is not dict for item in claims):
            return _unknown()
        for claim in claims:
            family = claim.get('family', f"pr-{claim.get('pr')}")
            legacy = ec2_claim_kind(claim) == 'LEGACY'
            if index == 0:
                is_review = family == base_family
                is_test = (family == base_family + _EC2_SUFFIX or
                           (family == base_family and legacy))
            elif type(family) is not str:
                return _unknown()
            else:
                is_review = not family.endswith(_EC2_SUFFIX)
                is_test = family.endswith(_EC2_SUFFIX) or legacy
            if is_review:
                review += 1
                head = claim.get('head_sha')
                if type(head) is str:
                    heads[head] = 'FAIL'
                else:
                    heads_known = False
            if is_test:
                tests += 1
    return {'chain': [record.get('task_id') for record in records],
            'review_repairs': review,
            'review_heads': heads if heads_known else UNKNOWN,
            'test_fails': tests}


def check_continues(store, task_id, predecessor):
    """Refuse, with a fixed code, a --continues link that breaks the chain shape.

    Refused: a malformed or unknown Task, the Task itself, a predecessor that is
    a split child or REPLAN of another lineage, a predecessor that already has a
    successor, a cycle and a chain over MAX_CHAIN Tasks. Any read doubt refuses.
    """
    if not _hex32(predecessor):
        _refuse('CONTINUES_TASK_INVALID')
    if predecessor == task_id:
        _refuse('CONTINUES_SELF')
    try:
        task_ids = list(store.task_ids())
    except Exception:
        _refuse('CONTINUES_STORE_UNAVAILABLE')
    if predecessor not in task_ids:
        _refuse('CONTINUES_TASK_UNKNOWN')
    try:
        record = store.get(predecessor)
    except Exception:
        _refuse('CONTINUES_CHAIN_INVALID')
    if type(record) is not dict or record.get('task_id') != predecessor:
        _refuse('CONTINUES_CHAIN_INVALID')
    lineage = record.get('lineage')
    if type(lineage) is dict and lineage.get('kind') in ('child', 'replan'):
        _refuse('CONTINUES_LINEAGE_CONFLICT')
    if 'lineage' in record and type(lineage) is not dict:
        _refuse('CONTINUES_CHAIN_INVALID')
    for other_id in task_ids:
        if other_id == predecessor:
            continue
        try:
            other = store.get(other_id)
        except Exception:
            _refuse('CONTINUES_STORE_UNAVAILABLE')
        if type(other) is not dict:
            _refuse('CONTINUES_STORE_UNAVAILABLE')
        if other.get('continues') == predecessor:
            _refuse('CONTINUES_PREDECESSOR_HAS_SUCCESSOR')
    # The new Task plus the predecessor's chain: no cycle, at most MAX_CHAIN Tasks.
    seen, current = {task_id, predecessor}, record
    length = 2
    while 'continues' in current:
        previous = current['continues']
        if not _hex32(previous):
            _refuse('CONTINUES_CHAIN_INVALID')
        if previous in seen:
            _refuse('CONTINUES_CYCLE')
        length += 1
        if length > MAX_CHAIN:
            _refuse('CONTINUES_CHAIN_TOO_LONG')
        try:
            current = store.get(previous)
        except Exception:
            _refuse('CONTINUES_CHAIN_INVALID')
        if type(current) is not dict or current.get('task_id') != previous:
            _refuse('CONTINUES_CHAIN_INVALID')
        seen.add(previous)
    if length > MAX_CHAIN:
        _refuse('CONTINUES_CHAIN_TOO_LONG')
    return None
