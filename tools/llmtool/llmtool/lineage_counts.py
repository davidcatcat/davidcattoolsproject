"""#1502 WP-D L1: chain-wide repair counts across Task continuations.

A Task may continue one earlier Task through its write-once 'continues' field,
set only by `task record-pm --continues TASK`. The chain of a Task is the Task,
its predecessor, that Task's predecessor and so on: at most MAX_CHAIN Tasks.
Review repairs, failed review HEADs and test failures are summed over the
chain, so a continuation never resets a limit.

Fail closed (spec section 7): an unreadable or malformed predecessor, a cycle,
a fork (one Task continued by two records; with successors counted, anywhere
in the chain) or a chain over MAX_CHAIN makes every count
repair_policy.UNKNOWN, which the policy treats as the limit. A count is never
0 by doubt.

Atomicity (phase 2): record-pm runs check_continues and the create under one
store-wide continues lock, so a predecessor gets at most one successor. A
review repair reads the chain counts and writes its claim under the chain lock
of the chain's oldest Task (chain_key), so two repairs in one chain cannot
both spend the last repair. A lock that cannot be taken refuses; nothing is
checked or written without it.

Phase 2 users: a Developer delegate counts runs over every D6 of its chain
view (delegate_chain) and writes its run claim under those chain locks
(acquire_chain_locks); AI Debug admission counts Debugs over the chain
(chain_debug_used); a delegate whose D6 base is another chain's recorded
result HEAD refuses CONTINUATION_LINK_REQUIRED (check_continuation_link).
"""

import contextlib
import re
import threading

from .errors import LLMToolError
from .repair_policy import UNKNOWN

MAX_CHAIN = 16
# A delegate's chain view joins the chain of the delegated Task and the chains
# of the Tasks already on its D6: at most this many Tasks, else in doubt.
MAX_DELEGATE_MEMBERS = 2 * MAX_CHAIN
# The continuation link scan reads at most this many Task records, else in doubt.
LINK_SCAN_MAX = 20000
_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX40 = re.compile(r'[0-9a-f]{40}')
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


def successor_records(store, task, members):
    """The Tasks that continue task, nearest first, or None when any read is in doubt.

    members holds the IDs already in the chain (the Task and its predecessors);
    their records are not read again. Every member, the Task's predecessors
    included, must have at most one successor: each predecessor's one successor
    is the member before it, so any other record continuing a predecessor is a
    fork (review F1 v2). A fork anywhere in the chain (at a predecessor, the
    Task or a later Task), a cycle or a chain over MAX_CHAIN is in doubt, from
    whichever chain Task the walk starts.
    """
    try:
        task_ids = list(store.task_ids())
    except Exception:
        return None
    own_id = task.get('task_id')
    by_link = {}
    for other_id in task_ids:
        if other_id in members:
            continue
        try:
            other = store.get(other_id)
        except Exception:
            return None
        if type(other) is not dict:
            return None
        link = other.get('continues')
        if type(link) is str:
            if link in members and link != own_id:
                # An off-path successor of a predecessor: the chain forks.
                return None
            by_link.setdefault(link, {})[other.get('task_id')] = other
    successors, seen, current = [], set(members), task.get('task_id')
    while current in by_link:
        found = list(by_link[current].values())
        if len(found) != 1:
            return None
        record = found[0]
        if (not _hex32(record.get('task_id')) or record['task_id'] in seen or
                len(seen) + 1 > MAX_CHAIN):
            return None
        successors.append(record)
        seen.add(record['task_id'])
        current = record['task_id']
    return successors


def chain_key(store, task):
    """The oldest Task ID of task's continuation chain: the chain lock key.

    A Task without continues is its own key. A chain in doubt also gives the
    Task's own ID; its counts are then UNKNOWN, and a caller that recomputes
    the key under the lock refuses when it no longer matches.
    """
    records = chain_records(store, task)
    if not records:
        return task.get('task_id') if type(task) is dict else None
    return records[-1].get('task_id')


def _unknown():
    return {'chain': UNKNOWN, 'review_repairs': UNKNOWN, 'review_heads': UNKNOWN,
            'test_fails': UNKNOWN}


def lineage_counts(store, task, *, base_family, include_successors=False):
    """Chain-wide counts for one Task; each value is known or UNKNOWN.

    review_repairs: review repair claims. Every Task of a chain, the Task
      itself included, adds every review-family claim (family not '-ec2',
      legacy EC2 claims included), any PR.
    review_heads: {head: 'FAIL'} for those claims, one entry per HEAD.
    test_fails: EC2 and CI repair claims. Every Task of a chain, the Task
      itself included, adds every '-ec2' claim and every legacy EC2 claim, any
      PR.
    chain: the Task IDs of the chain, the Task first.

    A Task alone in its chain (no continues and, when include_successors, no
    successor) counts as before: only its claims in the stored base_family
    (review) and '<base>-ec2' plus legacy EC2 claims in base_family (tests).

    include_successors (review_repair under the chain lock, and
    task_advance._test_fail_count so both agree): every Task that
    continues this one is counted like a predecessor and listed after the
    predecessors, so a repair on an earlier Task of the chain sees the claims
    of later ones. A store scan in doubt, or a fork anywhere in the chain
    (a predecessor, the Task or a later Task continued by two records), makes
    every count UNKNOWN: an ambiguous chain is never counted from one branch.
    """
    from .review_repair import ec2_claim_kind
    if type(base_family) is not str:
        return _unknown()
    records = chain_records(store, task)
    if records is None:
        return _unknown()
    if include_successors:
        if store is None:
            return _unknown()
        later = successor_records(store, task,
                                  {record.get('task_id') for record in records})
        if later is None or len(records) + len(later) > MAX_CHAIN:
            return _unknown()
        records = records + later
    # Family-local counting only for a Task alone in its chain; in a chain every
    # Task's claims count on any PR, the Task's own included (#1502 F1).
    local = len(records) == 1
    review, tests, heads, heads_known = 0, 0, {}, True
    for record in records:
        claims = record.get('review_repair_claims', [])
        if type(claims) is not list or any(type(item) is not dict for item in claims):
            return _unknown()
        for claim in claims:
            family = claim.get('family', f"pr-{claim.get('pr')}")
            legacy = ec2_claim_kind(claim) == 'LEGACY'
            if local:
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


def chain_review_repairs(store, task):
    """Every review repair claim of task's whole chain: a count or UNKNOWN.

    The auto review repair limit (v92 F1, CWE-841): every chain Task (the Task,
    its predecessors and its successors) adds every claim whose family is not
    '-ec2', on any PR and in any PR family, legacy EC2 claims included. A Task
    alone in its chain counts all its families too. '-ec2' claims are test
    failures, not review repairs. Any read, link or claim in doubt is UNKNOWN,
    which the caller treats as used. Run it under the chain lock.
    """
    if type(task) is not dict:
        return UNKNOWN
    records = chain_records(store, task)
    if records is None or store is None:
        return UNKNOWN
    later = successor_records(store, task, {record.get('task_id') for record in records})
    if later is None or len(records) + len(later) > MAX_CHAIN:
        return UNKNOWN
    count = 0
    for record in records + later:
        claims = record.get('review_repair_claims', [])
        if type(claims) is not list:
            return UNKNOWN
        for claim in claims:
            if type(claim) is not dict:
                return UNKNOWN
            family = claim.get('family', f"pr-{claim.get('pr')}")
            if type(family) is not str:
                return UNKNOWN
            if not family.endswith(_EC2_SUFFIX):
                count += 1
    return count


def chain_members(store, task):
    """(records, key) for task's whole chain, or None when anything is in doubt.

    records: the Task, its predecessors (nearest first), then its successors
    (nearest first), at most MAX_CHAIN. key: the chain's oldest Task ID, the
    chain lock key. A fork anywhere in the chain, a cycle, a read in doubt or
    no store is None.
    """
    if type(task) is not dict or store is None:
        return None
    records = chain_records(store, task)
    if not records:
        return None
    later = successor_records(store, task, {record.get('task_id') for record in records})
    if later is None or len(records) + len(later) > MAX_CHAIN:
        return None
    key = records[-1].get('task_id')
    if type(key) is not str or not key:
        return None
    return records + later, key


def delegate_chain(store, seeds):
    """The chain view a Developer delegate counts and locks, or None in doubt.

    seeds: the delegated Task first, then every Task whose stored completion
    names the delegated D6 (a PM follow-up or repair Task on a chain D6 joins
    that D6's chain). members: {task_id: record} of every Task of every seed's
    chain (predecessors and successors), at most MAX_DELEGATE_MEMBERS. keys:
    the sorted chain keys of those chains. Any seed or chain in doubt, or two
    chains that share a Task, is None.
    """
    if type(seeds) is not list or not seeds:
        return None
    members, keys = {}, set()
    for seed in seeds:
        if type(seed) is not dict:
            return None
        seed_id = seed.get('task_id')
        if type(seed_id) is not str or not seed_id:
            return None
        if seed_id in members:
            continue
        found = chain_members(store, seed)
        if found is None:
            return None
        records, key = found
        for record in records:
            record_id = record.get('task_id')
            if type(record_id) is not str or not record_id or record_id in members:
                return None
            members[record_id] = record
        keys.add(key)
        if len(members) > MAX_DELEGATE_MEMBERS:
            return None
    return {'members': members, 'keys': sorted(keys)}


def chain_debug_used(store, task, entries, used_statuses):
    """AI Debugs used over task's whole chain: a count or UNKNOWN.

    entries is the Task's own fresh high_debug list; every other chain Task
    (predecessors and successors) adds each of its stored entries that is not
    a dict or whose status is in used_statuses. A chain, a list or a read in
    doubt is UNKNOWN, which admission treats as the limit.
    """
    if type(task) is not dict or type(entries) is not list:
        return UNKNOWN
    found = chain_members(store, task)
    if found is None:
        return UNKNOWN
    records, _key = found
    own_id = task.get('task_id')
    used = 0
    for record in records:
        listed = entries if record.get('task_id') == own_id else record.get('high_debug', [])
        if type(listed) is not list:
            return UNKNOWN
        used += sum(1 for entry in listed
                    if type(entry) is not dict or entry.get('status') in used_statuses)
    return used


def recorded_result_heads(record):
    """The result HEADs a Task record holds, or None when the record is malformed.

    A result HEAD is a 40-hex completion.evidence.result.result_head or
    completion.evidence.result_head that differs from the evidence source_sha:
    a HEAD equal to its own source is no result (a pending claim stores its
    source there).
    """
    if type(record) is not dict:
        return None
    completion = record.get('completion')
    if completion is None:
        return set()
    if type(completion) is not dict:
        return None
    evidence = completion.get('evidence')
    if evidence is None:
        return set()
    if type(evidence) is not dict:
        return None
    source = evidence.get('source_sha')
    result = evidence.get('result')
    if result is not None and type(result) is not dict:
        return None
    heads = set()
    for head in (evidence.get('result_head'),
                 result.get('result_head') if type(result) is dict else None):
        if head is None:
            continue
        if type(head) is not str:
            return None
        if _HEX40.fullmatch(head) is not None and head != source:
            heads.add(head)
    return heads


def _refuse_link(reason):
    error = LLMToolError('CONTINUATION_LINK_REQUIRED')
    error.continuation_link_code = 'CONTINUATION_LINK_REQUIRED'
    error.continuation_link_reason = reason
    raise error from None


def check_continuation_link(store, member_ids, base_sha):
    """Refuse CONTINUATION_LINK_REQUIRED when a D6 builds on another chain's result.

    base_sha is the delegated D6's registered base. When it equals a recorded
    result HEAD of any Task outside member_ids (the delegate's chain view), the
    work continues that Task without a continues link: refused. A Task that
    continues it (record-pm --continues) has it in its chain and passes. The
    scan reads at most LINK_SCAN_MAX records; a malformed base, a scan over the
    bound or any read or record in doubt refuses too. There is no override.
    """
    if type(base_sha) is not str or _HEX40.fullmatch(base_sha) is None:
        _refuse_link('SCAN_IN_DOUBT')
    if not isinstance(member_ids, (set, frozenset, dict, list, tuple)):
        _refuse_link('SCAN_IN_DOUBT')
    try:
        task_ids = list(store.task_ids())
    except Exception:
        _refuse_link('SCAN_IN_DOUBT')
    if len(task_ids) > LINK_SCAN_MAX:
        _refuse_link('SCAN_IN_DOUBT')
    for task_id in task_ids:
        if task_id in member_ids:
            continue
        try:
            record = store.get(task_id)
        except Exception:
            _refuse_link('SCAN_IN_DOUBT')
        heads = recorded_result_heads(record)
        if heads is None:
            _refuse_link('SCAN_IN_DOUBT')
        if base_sha in heads:
            _refuse_link('RESULT_HEAD_OUTSIDE_CHAIN')
    return None


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


# Process-local locks stand in for the file locks only for stores that are not
# a TaskStore (in-memory test stores, which no other process can share). A
# TaskStore always takes its flock sidecar.
_LOCAL_GUARD = threading.Lock()
_LOCAL_LOCKS = {}


@contextlib.contextmanager
def _local_lock(name):
    from .task_store import LINEAGE_LOCK_TIMEOUT_SECONDS
    with _LOCAL_GUARD:
        lock = _LOCAL_LOCKS.setdefault(name, threading.Lock())
    if not lock.acquire(timeout=LINEAGE_LOCK_TIMEOUT_SECONDS):
        raise LLMToolError('LINEAGE_LOCK_BUSY')
    try:
        yield
    finally:
        lock.release()


def _store_lock(store, kind, key=None):
    from .task_store import TaskStore
    if isinstance(store, TaskStore):
        return store.continues_lock() if kind == 'continues' else store.chain_lock(key)
    return _local_lock((kind, key))


@contextlib.contextmanager
def continues_lock(store):
    """Hold the store-wide continues lock across check_continues and the create.

    A lock that cannot be taken refuses CONTINUES_LOCK_UNAVAILABLE before any
    check or write; the lock is released on every path.
    """
    stack = contextlib.ExitStack()
    try:
        stack.enter_context(_store_lock(store, 'continues'))
    except Exception:
        _refuse('CONTINUES_LOCK_UNAVAILABLE')
    with stack:
        yield


def acquire_chain_lock(store, key):
    """Take the exclusive chain lock keyed by the chain's oldest Task ID.

    Returns a release callable (safe to call more than once). Raises when the
    lock cannot be taken within its bounded wait; the caller refuses.
    """
    if not isinstance(key, str) or not key:
        raise LLMToolError('LINEAGE_LOCK_INVALID')
    stack = contextlib.ExitStack()
    stack.enter_context(_store_lock(store, 'chain', key))
    return stack.close


def acquire_chain_locks(store, keys):
    """Take the chain locks of every key, in sorted order; return one release callable.

    Sorted order keeps two holders of overlapping key sets from deadlocking.
    When any lock cannot be taken, the ones already taken are released and the
    error is raised; the caller refuses. The release callable releases them
    in reverse order and is safe to call more than once.
    """
    if (type(keys) is not list or not keys or
            any(not isinstance(key, str) or not key for key in keys)):
        raise LLMToolError('LINEAGE_LOCK_INVALID')
    releases = []

    def release():
        while releases:
            releases.pop()()
    try:
        for key in sorted(set(keys)):
            releases.append(acquire_chain_lock(store, key))
    except BaseException:
        release()
        raise
    return release
