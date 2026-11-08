"""Merge one recorded merge train and close its member Tasks (#1182 T4b2).

merge_train merges a BUILT (or UNKNOWN) train record once through the same
exact-head squash merge route as ``task advance --merge``. Before it claims the
merge it rechecks authenticated main, the open train PR at the recorded head,
the train delta, every member's bound PASS and the train-head CI, then reads
authenticated main once more right before the claim. A record left in
MERGE_CLAIMED (its UNKNOWN or MERGED write failed) is settled from one fresh PR
read without a new merge: MERGED when the train merged at its head, else UNKNOWN.
The provenance comment is posted only after MERGED is durably recorded.
close_train then closes the member Tasks in record order through ``task advance``.

merge_train holds an exclusive non-blocking per-train operation lock (a
dot-file beside the record) for the whole call; a held lock refuses
TRAIN_OPERATION_BUSY, so a live claim is never recovered as abandoned.

Each call does one step and fails closed. Both return
``{'step': STEP, 'codes': [...], 'result': ...}``. A refusal is step
``MERGE_REFUSED`` (merge_train) or ``CLOSE_REFUSED`` (close_train) with one code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import os
import re
import stat

from . import merge_train as _train_build
from . import merge_train_store, task_advance, task_completion

_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')

TRAIN_NOT_MERGEABLE = 'TRAIN_NOT_MERGEABLE'
TRAIN_MAIN_MOVED = 'TRAIN_MAIN_MOVED'
TRAIN_MAIN_UNAVAILABLE = 'TRAIN_MAIN_UNAVAILABLE'
TRAIN_PR_STALE = 'TRAIN_PR_STALE'
TRAIN_DELTA_UNVERIFIED = 'TRAIN_DELTA_UNVERIFIED'
TRAIN_MEMBER_CHANGED = 'TRAIN_MEMBER_CHANGED'
TRAIN_CLAIM_CONFLICT = 'TRAIN_CLAIM_CONFLICT'
TRAIN_MERGE_OUTCOME_UNKNOWN = 'TRAIN_MERGE_OUTCOME_UNKNOWN'
TRAIN_RESULT_RECORD_FAILED = 'TRAIN_RESULT_RECORD_FAILED'
TRAIN_PROVENANCE_COMMENT_FAILED = 'TRAIN_PROVENANCE_COMMENT_FAILED'
TRAIN_NOT_MERGED = 'TRAIN_NOT_MERGED'
TRAIN_MEMBERS_OPEN = 'TRAIN_MEMBERS_OPEN'
TRAIN_OPERATION_BUSY = 'TRAIN_OPERATION_BUSY'
TRAIN_MEMBER_PR_CLOSE_FAILED = 'TRAIN_MEMBER_PR_CLOSE_FAILED'

# The two train-head macOS codes that _merge_once answers with the mac-ci label.
_MACOS_LABEL_CODES = frozenset(('MERGE_MACOS_CHECK_MISSING', 'MERGE_MACOS_CHECK_SKIPPED'))


def _result(step, codes=(), result=None):
    return {'step': step, 'codes': list(codes), 'result': result}


def _refused(code, result=None):
    return _result('MERGE_REFUSED', [code], result)


def _close_refused(code, result=None):
    return _result('CLOSE_REFUSED', [code], result)


def _store_code(exc):
    code = getattr(exc, 'merge_train_code', None)
    if type(code) is str and _CODE.fullmatch(code):
        return code
    return merge_train_store.TRAIN_RECORD_UNAVAILABLE


def _operation_lock_name(train_id):
    """Per-train merge operation lock; distinct from the store's ``.<id>.lock``.

    A train_id has no dot, so this name never equals another train's store lock,
    and the leading dot keeps record listing from treating it as a record.
    """
    return '.' + train_id + '.merge-op.lock'


def _acquire_operation_lock(train_id, train_root):
    """Return (fd, None) holding the exclusive operation lock, or (None, code)."""
    try:
        merge_train_store._record_name(train_id)
        directory = merge_train_store._open_directory(train_root)
    except Exception as exc:
        return None, _store_code(exc)
    try:
        fd = os.open(_operation_lock_name(train_id),
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                     dir_fd=directory)
    except OSError:
        return None, merge_train_store.TRAIN_RECORD_INVALID
    finally:
        os.close(directory)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1):
            os.close(fd)
            return None, merge_train_store.TRAIN_RECORD_INVALID
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None, TRAIN_OPERATION_BUSY
    except OSError:
        os.close(fd)
        return None, merge_train_store.TRAIN_RECORD_UNAVAILABLE
    return fd, None


def _release_operation_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_record(train_id, train_root):
    """Return (record, None) or (None, code); the store validates shape and digest."""
    try:
        return merge_train_store.get(train_id, root=train_root), None
    except Exception as exc:
        return None, _store_code(exc)


def _read_pr(github_get, number):
    """Return one fresh PR read for ``number``, or None on any doubt."""
    try:
        value = github_get(f'pulls/{number}')
    except Exception:
        return None
    if type(value) is not dict or value.get('number', number) != number:
        return None
    return value


def _pr_head(pr):
    head = pr.get('head') if type(pr) is dict else None
    return head.get('sha') if type(head) is dict else None


def _open_at(pr, head):
    return (type(pr) is dict and pr.get('state') == 'open' and
            pr.get('merged') is False and _pr_head(pr) == head)


def _merged_at(pr, head):
    """Return the merge commit when ``pr`` is merged at exactly ``head``, else None."""
    if type(pr) is not dict or pr.get('merged') is not True or _pr_head(pr) != head:
        return None
    sha = pr.get('merge_commit_sha')
    return sha if type(sha) is str and _HEX40.fullmatch(sha) else None


def _utc_now_text(previous=None):
    now = datetime.now(timezone.utc)
    text = now.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    if text == previous:
        # Keep a retry distinct even if the clock has not advanced.
        text = (now + timedelta(microseconds=1)).isoformat(
            timespec='microseconds').replace('+00:00', 'Z')
    return text


def _member_unchanged(member, store, github_get):
    """Return True only when the member Task still binds its recorded PASS."""
    try:
        record = store.get(member['task_id'])
        if type(record) is not dict or record.get('task_id') != member['task_id']:
            return False
        if record.get('merge_attempt') is not None:
            return False
        if not _open_at(_read_pr(github_get, member['pr']), member['head']):
            return False
        # Events exactly as task_advance.advance builds them for _merge_once.
        events = record.get('completion_events', [])
        try:
            events = list(events) + task_advance._review_outcomes(
                record, task_advance._claims(record), github_get)
        except Exception:
            pass
        version = task_advance._merge_pass_claim(record, member['pr'], member['head'], events)
    except Exception:
        return False
    return type(version) is int and version == member['review_version']


def _provenance(record, merge_sha):
    lines = [f'llmtool merge train provenance: train={record["train_id"]} '
             f'head={record["train_head"]} merge={merge_sha} '
             'checks=exact-head-success-or-skipped']
    for member in record['members']:
        lines.append(f'- task={member["task_id"]} pr={member["pr"]} head={member["head"]} '
                     f'review_version={member["review_version"]}')
    return '\n'.join(lines)


def _finish_merged(record, expected_sha256, attempt, merge_sha, gh_runner, root, train_root):
    """Record a merge seen at the exact train head, then post one provenance comment.

    The comment follows only a durable MERGED write. When both writes fail the
    record stays MERGE_CLAIMED or UNKNOWN and the next call settles it from a
    fresh PR read without a new merge, posting the comment then.
    """
    outcome = {'train_id': record['train_id'], 'pr': record['train_pr'],
               'head': record['train_head'], 'merge_sha': merge_sha}
    changes = {'state': 'MERGED', 'merge_attempt': dict(attempt, state='MERGED'),
               'merge_sha': merge_sha}
    # The remote merge already happened. One bounded record retry cannot repeat it.
    for _ in range(2):
        try:
            merge_train_store.compare_and_update(
                record['train_id'], expected_sha256, root=train_root, **changes)
            break
        except Exception:
            continue
    else:
        return _result('MERGED', [TRAIN_RESULT_RECORD_FAILED], outcome)
    codes = []
    try:
        comment = gh_runner(root, ['pr', 'comment', str(record['train_pr']),
                                   '--body', _provenance(record, merge_sha)])
        if getattr(comment, 'returncode', 1) != 0:
            codes.append(TRAIN_PROVENANCE_COMMENT_FAILED)
    except Exception:
        codes.append(TRAIN_PROVENANCE_COMMENT_FAILED)
    return _result('MERGED', codes, outcome)


def _resolve_unknown(record, github_get, gh_runner, root, train_root):
    """Settle an UNKNOWN train whose PR a fresh read shows merged; else None.

    Without this, a merge that landed after an ambiguous outcome would move
    main and the retry would supersede a train that is in fact merged.
    """
    pr = _read_pr(github_get, record['train_pr'])
    if type(pr) is not dict or pr.get('merged') is not True:
        return None
    merge_sha = _merged_at(pr, record['train_head'])
    if merge_sha is None:
        return _refused(TRAIN_PR_STALE)
    attempt = record['merge_attempt']
    if (type(attempt) is not dict or attempt.get('pr') != record['train_pr'] or
            attempt.get('head_sha') != record['train_head']):
        return _refused(TRAIN_NOT_MERGEABLE)
    return _finish_merged(record, record['record_sha256'], attempt, merge_sha,
                          gh_runner, root, train_root)


def _record_unknown(train_id, expected_sha256, attempt, train_root):
    """Move a claimed train to UNKNOWN with one bounded retry; return True when written."""
    for _ in range(2):
        try:
            merge_train_store.compare_and_update(
                train_id, expected_sha256, root=train_root,
                state='UNKNOWN', merge_attempt=dict(attempt, state='UNKNOWN'))
            return True
        except Exception:
            continue
    return False


def _recover_claimed(record, github_get, gh_runner, root, train_root):
    """Settle a MERGE_CLAIMED train whose UNKNOWN write failed; never merges.

    A fresh read showing the train merged at train_head records MERGED. Any
    other read moves the record to UNKNOWN, so a later call merges only after
    the full recheck.
    """
    train_pr, train_head = record['train_pr'], record['train_head']
    attempt = record['merge_attempt']
    if (type(attempt) is not dict or attempt.get('pr') != train_pr or
            attempt.get('head_sha') != train_head):
        return _refused(TRAIN_NOT_MERGEABLE)
    merge_sha = _merged_at(_read_pr(github_get, train_pr), train_head)
    if merge_sha is not None:
        return _finish_merged(record, record['record_sha256'], attempt, merge_sha,
                              gh_runner, root, train_root)
    _record_unknown(record['train_id'], record['record_sha256'], attempt, train_root)
    return _refused(TRAIN_MERGE_OUTCOME_UNKNOWN,
                    {'train_id': record['train_id'], 'pr': train_pr, 'head': train_head})


def _main_moved(record, train_root):
    """Return None when authenticated main still equals main_sha, else the step.

    A moved main supersedes the record; a failed read refuses without a write.
    """
    try:
        main_sha = task_completion.read_authenticated_main_sha()
    except Exception:
        return _refused(TRAIN_MAIN_UNAVAILABLE)
    if main_sha == record['main_sha']:
        return None
    train_id = record['train_id']
    moved = {'train_id': train_id, 'main_sha': record['main_sha'], 'current_main': main_sha}
    try:
        merge_train_store.compare_and_update(
            train_id, record['record_sha256'], root=train_root, state='SUPERSEDED')
    except Exception as exc:
        return _result('MERGE_REFUSED', [TRAIN_MAIN_MOVED, _store_code(exc)], moved)
    return _result('SUPERSEDED', [TRAIN_MAIN_MOVED], moved)


def merge_train(train_id, *, store, github_get, gh_runner, root, train_root=None):
    """Merge one recorded train once at its exact head; one step, fail closed.

    The whole call runs under the per-train operation lock; a held lock
    refuses TRAIN_OPERATION_BUSY before any read, write or command.
    """
    lock_fd, code = _acquire_operation_lock(train_id, train_root)
    if lock_fd is None:
        return _refused(code)
    try:
        return _merge_locked(train_id, store, github_get, gh_runner, root, train_root)
    finally:
        _release_operation_lock(lock_fd)


def _merge_locked(train_id, store, github_get, gh_runner, root, train_root):
    record, code = _read_record(train_id, train_root)
    if record is None:
        return _refused(code)
    train_pr, train_head = record['train_pr'], record['train_head']
    # BUILT and UNKNOWN may lead to a new merge; MERGE_CLAIMED is recovery
    # only, under the operation lock, never a new merge. A record in any other
    # state, or one without train_pr, refuses TRAIN_NOT_MERGEABLE.
    if record['state'] not in ('BUILT', 'MERGE_CLAIMED', 'UNKNOWN') or train_pr is None:
        return _refused(TRAIN_NOT_MERGEABLE)
    if record['state'] == 'MERGE_CLAIMED':
        # Recovery only, and only under the operation lock, so no live call
        # holds this claim: this call never runs pr merge.
        return _recover_claimed(record, github_get, gh_runner, root, train_root)
    if record['state'] == 'UNKNOWN':
        resolved = _resolve_unknown(record, github_get, gh_runner, root, train_root)
        if resolved is not None:
            return resolved

    moved = _main_moved(record, train_root)
    if moved is not None:
        return moved

    pr = _read_pr(github_get, train_pr)
    if not _open_at(pr, train_head) or pr.get('mergeable') is not True:
        return _refused(TRAIN_PR_STALE)

    heads = [member['head'] for member in record['members']]
    if _train_build.train_delta_verified(root, record['main_sha'], heads, train_head) is not True:
        return _refused(TRAIN_DELTA_UNVERIFIED)

    for member in record['members']:
        if not _member_unchanged(member, store, github_get):
            return _refused(TRAIN_MEMBER_CHANGED, {'task_id': member['task_id']})

    # Train-head CI only; member-head checks are never read here.
    code = task_advance._merge_checks({}, train_pr, train_head, pr, github_get,
                                      allow_missing_macos=True)
    if code in _MACOS_LABEL_CODES:
        has_mac_ci = task_advance._has_pr_label(pr, 'mac-ci')
        if has_mac_ci is None:
            return _refused('MERGE_LABELS_UNAVAILABLE')
        if not has_mac_ci:
            try:
                labeled = gh_runner(root, ['pr', 'edit', str(train_pr), '--add-label', 'mac-ci'])
            except Exception:
                labeled = None
            if labeled is None or getattr(labeled, 'returncode', 1) != 0:
                return _refused('MERGE_MAC_CI_LABEL_FAILED')
            return _result('WAITING_CI', [], {'train_id': train_id, 'pr': train_pr,
                                              'head': train_head})
        code = ('MERGE_MACOS_CHECK_REQUIRED' if code == 'MERGE_MACOS_CHECK_SKIPPED' else
                'MERGE_REQUIRED_CHECK_MISSING')
    if code:
        return _refused(code)

    # Main may have moved during the gate reads; recheck right before the claim.
    moved = _main_moved(record, train_root)
    if moved is not None:
        return moved

    prior = record['merge_attempt']
    attempt = {'pr': train_pr, 'head_sha': train_head, 'state': 'CLAIMED',
               'claimed_at': _utc_now_text(prior.get('claimed_at')
                                           if type(prior) is dict else None)}
    try:
        claimed = merge_train_store.compare_and_update(
            train_id, record['record_sha256'], root=train_root,
            state='MERGE_CLAIMED', merge_attempt=attempt)
    except Exception:
        return _refused(TRAIN_CLAIM_CONFLICT)

    try:
        gh_runner(root, ['pr', 'merge', str(train_pr), '--squash',
                         '--match-head-commit', train_head])
    except Exception:
        pass
    # The merge commit comes only from a fresh exact-head read, so a reported
    # success and an ambiguous failure resolve the same way.
    # MERGED needs the verified merge_commit_sha from that fresh read. An exit-0
    # merge the read does not yet confirm stays UNKNOWN; a later call settles it
    # through _resolve_unknown from a new read, without a second merge command.
    merge_sha = _merged_at(_read_pr(github_get, train_pr), train_head)
    if merge_sha is None:
        # If both writes fail the record stays MERGE_CLAIMED; the next call
        # settles it through _recover_claimed without a new merge.
        _record_unknown(train_id, claimed['record_sha256'], attempt, train_root)
        return _refused(TRAIN_MERGE_OUTCOME_UNKNOWN,
                        {'train_id': train_id, 'pr': train_pr, 'head': train_head})
    return _finish_merged(claimed, claimed['record_sha256'], attempt, merge_sha,
                          gh_runner, root, train_root)


def _member_pr_comment(record):
    """The one fixed pointer comment left on a member PR that is closed."""
    return (f'Closed by llmtool merge train {record["train_id"]}: this change was merged '
            f'in train PR #{record["train_pr"]} as squash commit {record["merge_sha"]}.')


def _close_member_prs(record, github_get, gh_runner, root):
    """Close each member PR that is still open and not merged; return (closed, failed).

    One fresh read per PR. A PR already closed or merged is left alone. A
    failed or malformed read, or a failed close, is listed in ``failed``.
    Each open PR gets one close command, never a retry.
    """
    closed, failed = [], []
    comment = _member_pr_comment(record)
    for member in record['members']:
        number = member['pr']
        pr = _read_pr(github_get, number)
        if (type(pr) is not dict or type(pr.get('state')) is not str or
                type(pr.get('merged')) is not bool):
            failed.append(number)
            continue
        if pr['state'] != 'open' or pr['merged']:
            continue
        try:
            outcome = gh_runner(root, ['pr', 'close', str(number), '--comment', comment])
        except Exception:
            outcome = None
        if outcome is None or getattr(outcome, 'returncode', 1) != 0:
            failed.append(number)
        else:
            closed.append(number)
    return closed, failed


def close_train(train_id, *, store, advance_call, github_get, gh_runner, root,
                train_root=None):
    """Close every member Task of a MERGED train in record order; one step.

    ``store`` is the TaskStore the caller binds into ``advance_call``; this
    function reads no Task record itself.

    Once every member Task is CLOSED and the record is written CLOSED, each
    member PR is read once through ``github_get``. A PR still open and not
    merged is closed once through ``gh_runner`` (``gh pr close N --comment``)
    with one fixed comment naming the train id, the train PR and the squash
    SHA. A PR already closed or merged is left alone. A failed read or close
    keeps step CLOSED (the record stays CLOSED) and adds
    TRAIN_MEMBER_PR_CLOSE_FAILED. The result lists ``closed_prs`` and
    ``failed_prs``. A refused or WAITING_MEMBERS close never touches a PR.
    """
    record, code = _read_record(train_id, train_root)
    if record is None:
        return _close_refused(code)
    if record['state'] != 'MERGED':
        return _close_refused(TRAIN_NOT_MERGED)
    open_tasks = []
    for member in record['members']:
        try:
            outcome = advance_call(task_id=member['task_id'], pr_number=member['pr'],
                                   integration_pr=record['train_pr'])
        except Exception:
            outcome = None
        if type(outcome) is not dict or outcome.get('step') != 'CLOSED':
            open_tasks.append(member['task_id'])
    if open_tasks:
        return _result('WAITING_MEMBERS', [TRAIN_MEMBERS_OPEN],
                       {'train_id': train_id, 'open': open_tasks})
    try:
        merge_train_store.compare_and_update(
            train_id, record['record_sha256'], root=train_root, state='CLOSED')
    except Exception as exc:
        return _close_refused(_store_code(exc))
    closed, failed = _close_member_prs(record, github_get, gh_runner, root)
    return _result('CLOSED', [TRAIN_MEMBER_PR_CLOSE_FAILED] if failed else [],
                   {'train_id': train_id,
                    'tasks': [member['task_id'] for member in record['members']],
                    'closed_prs': closed, 'failed_prs': failed})
