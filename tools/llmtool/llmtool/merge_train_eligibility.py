"""Decide whether one Task and PR may join a merge train (#1182 T3a).

``member_check`` reads the Task record, the PR and the train store fresh and
reuses the existing ``task advance`` merge gates. It returns the member fields a
merge-train record stores, or one refusal code; the first failed check decides.
It never writes to GitHub, Git, the TaskStore or the train store.
"""

import re

from . import merge_train_store, sensitive_paths, task_advance, task_completion

TRAIN_MEMBER_RECORD_UNAVAILABLE = 'TRAIN_MEMBER_RECORD_UNAVAILABLE'
TRAIN_MEMBER_NOT_READY = 'TRAIN_MEMBER_NOT_READY'
TRAIN_MEMBER_PR_UNAVAILABLE = 'TRAIN_MEMBER_PR_UNAVAILABLE'
TRAIN_MEMBER_PR_NOT_OPEN = 'TRAIN_MEMBER_PR_NOT_OPEN'
TRAIN_MEMBER_MERGE_CLAIMED = 'TRAIN_MEMBER_MERGE_CLAIMED'
TRAIN_MEMBER_REVIEW_NOT_PASS = 'TRAIN_MEMBER_REVIEW_NOT_PASS'
TRAIN_MEMBER_RULE_FILE = 'TRAIN_MEMBER_RULE_FILE'
TRAIN_MEMBER_REMOTE_TEST_MISSING = 'TRAIN_MEMBER_REMOTE_TEST_MISSING'
TRAIN_MEMBER_IN_LIVE_TRAIN = 'TRAIN_MEMBER_IN_LIVE_TRAIN'
TRAIN_STORE_UNAVAILABLE = 'TRAIN_STORE_UNAVAILABLE'
# An exact-head success run whose id is not a positive int: the check data is
# malformed, reported with the existing merge-gate code.
_CHECKS_UNAVAILABLE = 'MERGE_CHECKS_UNAVAILABLE'

LIVE_TRAIN_STATES = frozenset(('BUILT', 'MERGE_CLAIMED', 'UNKNOWN'))
READY_STATE = 'PM_EVIDENCE_READY'
LINUX_CHECK = 'check (Linux 3.14)'
MACOS_CHECK = 'check (macOS 3.13)'
MAX_PR_FILES = 100

_TASK_ID = re.compile(r'[0-9a-f]{32}')
_SHA40 = re.compile(r'[0-9a-f]{40}')
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _refused(code):
    return {'eligible': False, 'code': code, 'member': None}


class _Snapshot:
    """Read each GitHub path at most once, so every gate judges one snapshot.

    A failed read is not cached and propagates to the caller's gate.
    """

    def __init__(self, github_get):
        self._get = github_get
        self._values = {}

    def __call__(self, path):
        if type(path) is not str:
            return self._get(path)
        if path not in self._values:
            self._values[path] = self._get(path)
        return self._values[path]


def _pr_snapshot(pr, github_get):
    """Return (pr dict, head sha) for a well-formed PR read, else None.

    Well-formed means: ``number`` is the int ``pr``, ``state`` is a string,
    ``merged`` is a bool and ``head.sha`` is a 40-hex SHA. A missing or
    malformed field is unavailable, decided before the open check.
    """
    try:
        value = github_get(f'pulls/{pr}')
    except Exception:
        return None
    if type(value) is not dict:
        return None
    number = value.get('number')
    if type(number) is not int or number != pr:
        return None
    if type(value.get('state')) is not str or type(value.get('merged')) is not bool:
        return None
    head = value.get('head')
    sha = head.get('sha') if type(head) is dict else None
    if type(sha) is not str or _SHA40.fullmatch(sha) is None:
        return None
    return value, sha


def _review_version(record, pr, head, github_get):
    """Return the PASS review version (int) or a refusal code (str)."""
    events = list(record.get('completion_events') or [])
    try:
        events += task_advance._review_outcomes(record, task_advance._claims(record),
                                                github_get)
    except Exception:
        # As in `task advance`: persisted REVIEWER events still decide. A
        # missing outcome never raises a PASS, since any newer claim vetoes it.
        pass
    try:
        version = task_advance._merge_pass_claim(record, pr, head, events)
    except Exception:
        return TRAIN_MEMBER_REVIEW_NOT_PASS
    if type(version) is int and version >= 1:
        return version
    if type(version) is str and version:
        return version
    return TRAIN_MEMBER_REVIEW_NOT_PASS


def _newest_success_id(check_runs, name, head):
    """Return the newest exact-head success run id, None, or False when malformed."""
    ids = [item.get('id') for item in check_runs
           if type(item) is dict and item.get('name') == name and
           item.get('head_sha') == head and item.get('conclusion') == 'success']
    if any(type(run_id) is not int or run_id < 1 for run_id in ids):
        return False
    return max(ids, default=None)


def _ci_gate(record, pr, head, pull, github_get):
    """Return (code, None) or (None, (linux_run_id, macos_run_id))."""
    try:
        code = task_advance._merge_checks(record, pr, head, pull, github_get)
    except Exception:
        return _CHECKS_UNAVAILABLE, None
    if code is not None:
        return (code if type(code) is str and code else _CHECKS_UNAVAILABLE), None
    try:
        checks = github_get(f'commits/{head}/check-runs?per_page=100')
    except Exception:
        return _CHECKS_UNAVAILABLE, None
    check_runs = checks.get('check_runs') if type(checks) is dict else None
    if type(check_runs) is not list:
        return _CHECKS_UNAVAILABLE, None
    linux = _newest_success_id(check_runs, LINUX_CHECK, head)
    macos = _newest_success_id(check_runs, MACOS_CHECK, head)
    if linux is False or macos is False:
        return _CHECKS_UNAVAILABLE, None
    return None, (linux, macos)


def _rule_file_code(files):
    """Refuse a PR whose file list touches a rule file; an unreadable list is unavailable."""
    if type(files) is not list or len(files) >= MAX_PR_FILES:
        return TRAIN_MEMBER_PR_UNAVAILABLE
    for item in files:
        filename = item.get('filename') if type(item) is dict else None
        if type(filename) is not str or not filename:
            return TRAIN_MEMBER_PR_UNAVAILABLE
    # An unreadable sensitive-paths list comes back as the SENSITIVE_LIST_UNREADABLE
    # match and refuses TRAIN_MEMBER_RULE_FILE on purpose: pm-continuation.md
    # "Rule-file PRs" says to treat the PR as a rule-file PR when the list cannot
    # be read. Only an unreadable PR file list is TRAIN_MEMBER_PR_UNAVAILABLE.
    if sensitive_paths.sensitive_matches(files):
        return TRAIN_MEMBER_RULE_FILE
    return None


def _receipt_digest(record, head):
    """Return the full digest of the newest receipt for ``head``, or None."""
    receipts = record.get('remote_test_receipts')
    if type(receipts) is not list:
        return None
    for receipt in reversed(receipts):
        if type(receipt) is dict and receipt.get('source_sha') == head:
            digest = receipt.get('receipt_digest')
            if type(digest) is str and _SHA256.fullmatch(digest) is not None:
                return digest
            return None
    return None


def _live_train_code(task_id, pr, train_root):
    try:
        records = merge_train_store.list_records(train_root)
    except Exception:
        return TRAIN_STORE_UNAVAILABLE
    for train in records:
        if train['state'] not in LIVE_TRAIN_STATES:
            continue
        for member in train['members']:
            if member['task_id'] == task_id or member['pr'] == pr:
                return TRAIN_MEMBER_IN_LIVE_TRAIN
    return None


def member_check(store, task_id, pr, github_get, train_root=None):
    """Return whether Task ``task_id`` with PR ``pr`` may join a merge train.

    Returns ``{'eligible': True, 'code': None, 'member': {...}}`` with exactly
    the merge_train_store member fields, or ``{'eligible': False, 'code': CODE,
    'member': None}``. ``train_root`` is the TaskStore root whose sibling
    ``merge-trains`` directory holds the train records.
    """
    get = _Snapshot(task_completion._github_get if github_get is None else github_get)

    # 1. Task record.
    if type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None:
        return _refused(TRAIN_MEMBER_RECORD_UNAVAILABLE)
    try:
        record = store.get(task_id)
    except Exception:
        return _refused(TRAIN_MEMBER_RECORD_UNAVAILABLE)
    if type(record) is not dict or record.get('task_id') != task_id:
        return _refused(TRAIN_MEMBER_RECORD_UNAVAILABLE)

    # 2. Completion state and executor.
    completion = record.get('completion')
    executor = completion.get('executor_task_id') if type(completion) is dict else None
    if (type(completion) is not dict or completion.get('state') != READY_STATE or
            type(executor) is not str or
            merge_train_store._EXECUTOR_TASK.fullmatch(executor) is None):
        return _refused(TRAIN_MEMBER_NOT_READY)

    # 3. Fresh PR read.
    if type(pr) is not int or not 1 <= pr <= merge_train_store.MAX_PR:
        return _refused(TRAIN_MEMBER_PR_UNAVAILABLE)
    snapshot = _pr_snapshot(pr, get)
    if snapshot is None:
        return _refused(TRAIN_MEMBER_PR_UNAVAILABLE)
    pull, head = snapshot

    # 4. PR open and not merged.
    if pull.get('state') != 'open' or pull.get('merged') is True:
        return _refused(TRAIN_MEMBER_PR_NOT_OPEN)

    # 5. No merge already claimed for this Task.
    if record.get('merge_attempt') is not None:
        return _refused(TRAIN_MEMBER_MERGE_CLAIMED)

    # 6. Formal review PASS bound to this PR and head.
    review_version = _review_version(record, pr, head, get)
    if type(review_version) is not int:
        return _refused(review_version)

    # 7. Exact-head CI under every routed rule (allow_missing_macos=False).
    code, run_ids = _ci_gate(record, pr, head, pull, get)
    if code is not None:
        return _refused(code)
    linux_run_id, macos_run_id = run_ids

    # 8. Rule files never ride a train.
    try:
        files = get(f'pulls/{pr}/files?per_page=100')
    except Exception:
        return _refused(TRAIN_MEMBER_PR_UNAVAILABLE)
    code = _rule_file_code(files)
    if code is not None:
        return _refused(code)

    # 9. EC2 remote-test PASS for this head.
    summary = task_advance._remote_test_receipt_summary(record, head)
    digest = _receipt_digest(record, head)
    if (type(summary) is not dict or summary.get('result') != 'PASS' or digest is None or
            summary.get('receipt_digest') != digest[:8]):
        return _refused(TRAIN_MEMBER_REMOTE_TEST_MISSING)

    # 10. Not already in a live train.
    code = _live_train_code(task_id, pr, train_root)
    if code is not None:
        return _refused(code)

    return {'eligible': True, 'code': None, 'member': {
        'task_id': task_id,
        'pr': pr,
        'head': head,
        'executor_task': executor,
        'review_version': review_version,
        'linux_check_run_id': linux_run_id,
        'macos_check_run_id': macos_run_id,
        'remote_test_receipt_digest': digest,
    }}
