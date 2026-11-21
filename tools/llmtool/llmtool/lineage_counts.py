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

Chain views (phase 2 R2; pure, no store writes, no callers yet): store_records
reads one bounded store snapshot; chain_in finds a Task's chain and key in it;
d6_task_ids and delegate_chain gather the chains of a delegated Task and its
D6 Tasks for the chain-wide Developer repair cap; recorded_result_heads and
check_continuation_link refuse CONTINUATION_LINK_REQUIRED when a base SHA is a
result head of a Task outside the chain; acquire_chain_locks takes several
chain locks in sorted order. Doubt returns None or refuses.
"""

import contextlib
import re
import threading

from .errors import LLMToolError
from .repair_policy import UNKNOWN

MAX_CHAIN = 16
MAX_DELEGATE_MEMBERS = 2 * MAX_CHAIN
STORE_SCAN_MAX = 20000
_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX40 = re.compile(r'[0-9a-f]{40}')
_EC2_SUFFIX = '-ec2'   # review_repair.EC2_FAMILY_SUFFIX


def _hex32(value):
    return type(value) is str and _HEX32.fullmatch(value) is not None


def _hex40(value):
    return type(value) is str and _HEX40.fullmatch(value) is not None


def _refuse(code, successor=None):
    error = LLMToolError(code)
    error.continues_code = code
    if successor is not None:
        error.continues_successor = successor
    raise error from None


# #2188 section 6: fields that a successor left by a delegate refused before its
# first write still has empty. 'claims' covers both claim lists.
_EMPTY_SUCCESSOR_FIELDS = ('developer', 'completion', 'lineage',
                           'review_repair_claims', 'review_dispatch_claim')


def _empty_successor(record, task_id):
    """task_id when record is an empty successor, else None (fail closed).

    Empty: a 32-hex task_id equal to the scanned ID, and no developer,
    completion, claims or lineage (each absent, None or an empty list). Such a
    Task counts nothing; the PM re-delegates it instead of forking the chain.
    """
    if not _hex32(task_id) or record.get('task_id') != task_id:
        return None
    for field in _EMPTY_SUCCESSOR_FIELDS:
        value = record.get(field)
        if value is not None and not (type(value) is list and not value):
            return None
    return task_id


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


def debug_lineage_key(store, task):
    """Return the continuation root of the original work."""
    records = chain_records(store, task)
    if records is None:
        return None
    oldest = records[-1]
    line = oldest.get('lineage', {})
    if type(line) is not dict:
        return None
    if line.get('kind') in ('child', 'replan'):
        root_id = line.get('lineage_id')
        try:
            root = store.get(root_id)
        except Exception:
            return None
        if (type(root) is not dict or root.get('task_id') != root_id or
                root.get('lineage', {}).get('kind') != 'root'):
            return None
        oldest = root
    roots = chain_records(store, oldest)
    return roots[-1].get('task_id') if roots else None


def debug_lineage_members(store, task):
    """Return the original work members from a complete, unambiguous snapshot."""
    if type(task) is not dict:
        return None
    records = store_records(store)
    key = debug_lineage_key(store, task)
    if (records is None or key is None or task.get('task_id') not in records or
            any('lineage' in record and type(record['lineage']) is not dict
                for record in records.values())):
        return None
    root_chain = chain_in(records, key)
    if root_chain is None:
        return None
    members = set(root_chain[0])
    for root_id in root_chain[0]:
        line = records[root_id].get('lineage', {})
        if type(line) is not dict:
            return None
        related = {other_id for other_id, other in records.items()
                   if type(other.get('lineage')) is dict and
                   other['lineage'].get('kind') in ('child', 'replan') and
                   other['lineage'].get('lineage_id') == root_id}
        for split in line.get('splits', ()):
            if type(split) is not dict or type(split.get('children')) is not list:
                return None
            if not set(split['children']) <= related:
                return None
        for member in related:
            chain = chain_in(records, member)
            if chain is None:
                return None
            members.update(chain[0])
    if task['task_id'] not in members:
        return None
    return {member: records[member] for member in sorted(members)}


def chain_debug_used(store, task, entries, is_used):
    """Count reservations and used Debugs once across the original work."""
    if type(entries) is not list:
        return UNKNOWN, UNKNOWN
    members = debug_lineage_members(store, task)
    if members is None:
        return UNKNOWN, UNKNOWN
    used = 0
    for member, record in members.items():
        items = entries if member == task['task_id'] else record.get('high_debug', [])
        if type(items) is not list:
            return UNKNOWN, UNKNOWN
        used += sum(1 for item in items if is_used(item))
    return used, used


def check_continues(store, task_id, predecessor):
    """Refuse, with a fixed code, a --continues link that breaks the chain shape.

    Refused: a malformed or unknown Task, the Task itself, a predecessor that is
    a split child or REPLAN of another lineage, a predecessor that already has a
    successor, a cycle and a chain over MAX_CHAIN Tasks. Any read doubt refuses.
    A CONTINUES_PREDECESSOR_HAS_SUCCESSOR refusal sets continues_successor to
    the successor's ID only when that successor is empty (#2188 section 6).
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
            # #2188 section 6: an empty successor is named so the PM can
            # re-delegate it; any other successor is not named.
            _refuse('CONTINUES_PREDECESSOR_HAS_SUCCESSOR',
                    successor=_empty_successor(other, other_id))
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
    """Take the chain locks of keys in sorted order; return one release callable.

    Sorted, de-duplicated order means two callers with overlapping key sets
    cannot deadlock. The release is idempotent and releases in reverse order.
    On any acquire failure the locks already taken are released and the
    failure is re-raised. A non-list, an empty list, or an empty or non-str
    key raises LINEAGE_LOCK_INVALID before any lock is taken.
    """
    if (type(keys) is not list or not keys or
            any(type(key) is not str or not key for key in keys)):
        raise LLMToolError('LINEAGE_LOCK_INVALID')
    held, guard = [], threading.Lock()

    def release():
        with guard:
            taken = held[::-1]
            held.clear()
        first = None
        for undo in taken:
            try:
                undo()
            except Exception as error:   # release every lock before reporting
                if first is None:
                    first = error
        if first is not None:
            raise first

    try:
        for key in sorted(set(keys)):
            held.append(acquire_chain_lock(store, key))
    except BaseException:
        try:
            release()
        except Exception:
            pass   # the acquire failure is the error the caller sees
        raise
    return release


def store_records(store):
    """{task_id: record} for every stored Task, or None when the scan is in doubt.

    store.task_ids() is read once, then store.get() once per ID. None when the
    store is None, a read raises, a record is not a dict, an ID is not a
    non-empty str or repeats, or the store lists more than STORE_SCAN_MAX IDs.
    """
    if store is None:
        return None
    try:
        task_ids = list(store.task_ids())
    except Exception:
        return None
    if len(task_ids) > STORE_SCAN_MAX:
        return None
    records = {}
    for task_id in task_ids:
        if type(task_id) is not str or not task_id or task_id in records:
            return None
        try:
            record = store.get(task_id)
        except Exception:
            return None
        if type(record) is not dict:
            return None
        records[task_id] = record
    return records


def chain_in(records, task_id):
    """(chain_ids, key) for task_id in a store_records snapshot, or None in doubt.

    chain_ids: the Task, its predecessors nearest first, then its successors
    nearest first (lineage_counts include_successors order). key: the oldest
    Task ID, as chain_key gives. None for a missing record, a malformed or self
    link, a cycle, a chain record other than the Task whose task_id differs
    from its key, a fork anywhere (a chain Task continued by two records) or a
    chain over MAX_CHAIN Tasks.
    """
    if type(records) is not dict or type(task_id) is not str or not task_id:
        return None
    task = records.get(task_id)
    if type(task) is not dict:
        return None
    children = {}
    for other_id, other in records.items():
        if type(other) is not dict:
            return None
        link = other.get('continues')
        if type(link) is str:
            children.setdefault(link, []).append(other_id)
    chain, seen, current = [task_id], {task_id}, task
    while 'continues' in current:
        previous = current['continues']
        if not _hex32(previous) or previous in seen or len(chain) >= MAX_CHAIN:
            return None
        record = records.get(previous)
        if type(record) is not dict or record.get('task_id') != previous:
            return None
        # The member before it is its one successor; any other is a fork.
        if len(children.get(previous, ())) != 1:
            return None
        chain.append(previous)
        seen.add(previous)
        current = record
    key = chain[-1]
    current = task_id
    while current in children:
        found = children[current]
        if len(found) != 1:
            return None
        successor = found[0]
        if (not _hex32(successor) or records[successor].get('task_id') != successor or
                successor in seen or len(chain) >= MAX_CHAIN):
            return None
        chain.append(successor)
        seen.add(successor)
        current = successor
    return chain, key


def d6_task_ids(records, executor_task):
    """Sorted IDs whose completion.executor_task_id is executor_task, or None in doubt.

    None when records is not a dict, executor_task is not a non-empty str, an
    ID is not a str, a record is not a dict, or any completion is neither None
    nor a dict.
    """
    if type(records) is not dict or type(executor_task) is not str or not executor_task:
        return None
    found = []
    for task_id, record in records.items():
        if type(task_id) is not str or type(record) is not dict:
            return None
        completion = record.get('completion')
        if completion is None:
            continue
        if type(completion) is not dict:
            return None
        if completion.get('executor_task_id') == executor_task:
            found.append(task_id)
    return sorted(found)


def delegate_chain(records, seed_ids):
    """The chains of a delegated Task and its D6 Tasks, or None in doubt.

    seed_ids: the delegated Task first, then the D6 Tasks. Returns
    {'members': {id: record}, 'keys': sorted unique chain keys, 'linked':
    sorted IDs in chains longer than one Task}. Two seeds in one chain count
    it once. None when seed_ids is not a non-empty list, any seed's chain is in
    doubt, two chains share a Task, or members exceed MAX_DELEGATE_MEMBERS.
    """
    if type(records) is not dict or type(seed_ids) is not list or not seed_ids:
        return None
    chains, owner = {}, {}
    for seed in seed_ids:
        found = chain_in(records, seed)
        if found is None:
            return None
        chain_ids, key = found
        if key in chains:
            if set(chain_ids) != chains[key]:
                return None
            continue
        for member in chain_ids:
            if member in owner:
                return None   # one Task in two chains
            owner[member] = key
        chains[key] = set(chain_ids)
        if len(owner) > MAX_DELEGATE_MEMBERS:
            return None
    linked = sorted(member for ids in chains.values() if len(ids) > 1 for member in ids)
    return {'members': {member: records[member] for member in owner},
            'keys': sorted(chains), 'linked': linked}


def recorded_result_heads(record):
    """The 40-hex result heads a Task recorded, other than its source SHA.

    Reads completion.evidence.result_head and completion.evidence.result
    .result_head; a head equal to evidence.source_sha is not a result. set()
    when completion or evidence is None. None when the record, completion,
    evidence or result has a wrong type, or a head is neither None nor a str.
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
    result = evidence.get('result')
    if result is not None and type(result) is not dict:
        return None
    candidates = [evidence.get('result_head')]
    if result is not None:
        candidates.append(result.get('result_head'))
    source = evidence.get('source_sha')
    heads = set()
    for head in candidates:
        if head is None:
            continue
        if type(head) is not str:
            return None
        if _hex40(head) and head != source:
            heads.add(head)
    return heads


def _link_refuse(reason):
    error = LLMToolError('CONTINUATION_LINK_REQUIRED')
    error.continuation_link_reason = reason
    raise error from None


def check_continuation_link(records, member_ids, base_sha):
    """Refuse a base SHA that is the result of a Task outside the chain.

    Raises LLMToolError('CONTINUATION_LINK_REQUIRED') with
    continuation_link_reason 'RESULT_HEAD_OUTSIDE_CHAIN' when base_sha is a
    recorded result head of a Task not in member_ids, or 'SCAN_IN_DOUBT' when
    base_sha is not 40-hex, records is not a dict, member_ids is not a
    collection of IDs, or a non-member's heads are in doubt. No override.
    Returns None otherwise.
    """
    if not _hex40(base_sha) or type(records) is not dict:
        _link_refuse('SCAN_IN_DOUBT')
    if type(member_ids) not in (set, frozenset, list, tuple, dict):
        _link_refuse('SCAN_IN_DOUBT')
    try:
        members = set(member_ids)
    except TypeError:
        _link_refuse('SCAN_IN_DOUBT')
    outside = doubt = False
    for task_id, record in records.items():
        if task_id in members:
            continue
        heads = recorded_result_heads(record)
        if heads is None:
            doubt = True
        elif base_sha in heads:
            outside = True
    if outside:
        _link_refuse('RESULT_HEAD_OUTSIDE_CHAIN')
    if doubt:
        _link_refuse('SCAN_IN_DOUBT')
    return None
