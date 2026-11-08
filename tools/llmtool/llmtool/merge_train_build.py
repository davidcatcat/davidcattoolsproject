"""Build one merge train for PM-owned, eligible member Tasks (#1182 T3b1).

``build_train`` checks that the caller owns every member Task, reads main from
GitHub, checks each member with the existing eligibility gates, builds the
clean merge-only candidate on that main, records the train, pushes the
candidate to ``llmtool/train-<train_id>`` without force and opens one train PR.

Order and refusals:

1. Member list shape (no reads): ``TRAIN_MEMBERS_INVALID``.
2. Caller owns every member, before any other read: the pm_caller code.
3. Authenticated main: ``TRAIN_MAIN_UNAVAILABLE``.
4. Eligibility: an ineligible member is excluded with its code; an
   unexpected check error or result refuses ``TRAIN_MEMBER_CHECK_FAILED``.
5. Candidate: a conflicting or unavailable member is excluded; fewer than two
   merged refuses with the build code. Nothing is recorded or pushed.
6. Record (state BUILT, train_pr None): the train store code.
7. Push without force: ``TRAIN_PUSH_FAILED``; the record stays BUILT.
8. PR create, then exactly one open PR at the train head:
   ``TRAIN_PR_CREATE_FAILED``; the record stays BUILT with train_pr None.
9. compare_and_update sets train_pr: the train store code on failure.

Results carry only ids, SHAs, PR numbers and codes; never command output,
record file content or member text.
"""

import json
import re
import secrets
from datetime import datetime, timezone

from . import (merge_train, merge_train_eligibility, merge_train_store, pm_caller,
               task_completion)

STEP_BUILT = 'BUILT'
STEP_REFUSED = 'REFUSED'

TRAIN_MEMBERS_INVALID = merge_train.TRAIN_MEMBERS_INVALID
TRAIN_MAIN_UNAVAILABLE = 'TRAIN_MAIN_UNAVAILABLE'
TRAIN_MEMBER_CHECK_FAILED = 'TRAIN_MEMBER_CHECK_FAILED'
TRAIN_CLOCK_INVALID = 'TRAIN_CLOCK_INVALID'
TRAIN_PUSH_FAILED = 'TRAIN_PUSH_FAILED'
TRAIN_PR_CREATE_FAILED = 'TRAIN_PR_CREATE_FAILED'

MIN_MEMBERS = 2
MAX_MEMBERS = 6
BRANCH_PREFIX = 'llmtool/train-'

_TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
_SHA40 = re.compile(r'[0-9a-f]{40}\Z')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')


def _refused(code, result=None):
    return {'step': STEP_REFUSED, 'codes': [code], 'result': result}


def _sha40(value):
    return type(value) is str and _SHA40.fullmatch(value) is not None


def _code(value):
    return type(value) is str and _CODE.fullmatch(value) is not None


def _pr(value):
    return type(value) is int and 1 <= value <= merge_train_store.MAX_PR


def _member_pairs(members):
    """Return [(task_id, pr), ...] for a valid member list, else None."""
    if type(members) not in (list, tuple) or not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        return None
    pairs = []
    for member in members:
        if type(member) not in (list, tuple) or len(member) != 2:
            return None
        task_id, pr = member
        if type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None or not _pr(pr):
            return None
        pairs.append((task_id, pr))
    if (len({task_id for task_id, _ in pairs}) != len(pairs) or
            len({pr for _, pr in pairs}) != len(pairs)):
        return None
    return pairs


def _utc_moment(now):
    """Return an aware UTC datetime for ``now`` (default: the clock), else None."""
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return None
    return now.astimezone(timezone.utc)


def _member_outcome(outcome, task_id, pr):
    """Return the member dict, an exclusion code (str), or None on any doubt."""
    if type(outcome) is not dict:
        return None
    if outcome.get('eligible') is False:
        code = outcome.get('code')
        return code if _code(code) else None
    if outcome.get('eligible') is not True:
        return None
    member = outcome.get('member')
    if (type(member) is not dict or set(member) != merge_train_store.MEMBER_FIELDS or
            member['task_id'] != task_id or member['pr'] != pr or
            not _sha40(member['head'])):
        return None
    return dict(member)


def _store_code(error):
    code = getattr(error, 'merge_train_code', None)
    return code if _code(code) else merge_train_store.TRAIN_RECORD_UNAVAILABLE


def _succeeded(runner, root, args):
    try:
        result = runner(root, args)
    except Exception:
        return None
    if getattr(result, 'returncode', 1) != 0:
        return None
    return result


def _output(result):
    stdout = getattr(result, 'stdout', None)
    if type(stdout) is bytes:
        try:
            return stdout.decode('utf-8')
        except UnicodeDecodeError:
            return None
    return stdout if type(stdout) is str else None


def _open_train_pr(gh_runner, root, branch, head):
    """Return the one open PR number on ``branch`` at ``head``, else None."""
    result = _succeeded(gh_runner, root, ['pr', 'list', '--head', branch, '--state', 'open',
                                          '--json', 'number,headRefOid'])
    text = None if result is None else _output(result)
    if text is None:
        return None
    try:
        pulls = json.loads(text)
    except ValueError:
        return None
    if type(pulls) is not list or len(pulls) != 1 or type(pulls[0]) is not dict:
        return None
    number = pulls[0].get('number')
    if not _pr(number) or pulls[0].get('headRefOid') != head:
        return None
    return number


def _pr_text(train_id, main_sha, train_head, members):
    """Fixed PR title and body; only ids, SHAs and PR numbers."""
    title = 'llmtool merge train ' + train_id
    lines = [
        'llmtool merge train ' + train_id,
        '',
        'Main: ' + main_sha,
        'Train head: ' + train_head,
        '',
        'Members, in merge order:',
    ]
    lines += ['- PR #%d head %s Task %s' % (member['pr'], member['head'], member['task_id'])
              for member in members]
    return title, '\n'.join(lines) + '\n'


def build_train(members, *, store, caller_key, github_get, git_runner, gh_runner, root,
                train_root=None, now=None):
    """Build, record, push and open one merge train for ``members``.

    ``members`` is 2 to 6 ``(task_id, pr)`` pairs in merge order. Returns
    ``{'step': 'BUILT' | 'REFUSED', 'codes': [...], 'result': ...}``.
    """
    pairs = _member_pairs(members)
    if pairs is None:
        return _refused(TRAIN_MEMBERS_INVALID)
    moment = _utc_moment(now)
    if moment is None:
        return _refused(TRAIN_CLOCK_INVALID)

    # 1. The caller owns every member, before any other read.
    for task_id, _pr_number in pairs:
        code = pm_caller.owner_refusal_code(store, task_id, caller_key)
        if code is not None:
            return _refused(code)

    # 2. Authenticated main.
    try:
        main_sha = task_completion.read_authenticated_main_sha()
    except Exception:
        return _refused(TRAIN_MAIN_UNAVAILABLE)
    if not _sha40(main_sha):
        return _refused(TRAIN_MAIN_UNAVAILABLE)

    # 3. Eligibility, in the given order.
    checked, excluded = [], []
    for task_id, pr in pairs:
        try:
            outcome = merge_train_eligibility.member_check(store, task_id, pr, github_get,
                                                           train_root)
        except Exception:
            return _refused(TRAIN_MEMBER_CHECK_FAILED)
        verdict = _member_outcome(outcome, task_id, pr)
        if verdict is None:
            return _refused(TRAIN_MEMBER_CHECK_FAILED)
        if type(verdict) is str:
            excluded.append({'task_id': task_id, 'pr': pr, 'code': verdict})
        else:
            checked.append(verdict)

    # 4. Candidate on authenticated main.
    by_task = {member['task_id']: member for member in checked}
    try:
        build = merge_train.build_candidate(
            root, main_sha, [(member['task_id'], member['head']) for member in checked])
    except Exception:
        return _refused(merge_train.TRAIN_BUILD_FAILED)
    if (type(build) is not dict or type(build.get('merged')) is not list or
            type(build.get('excluded')) is not list or type(build.get('codes')) is not list):
        return _refused(merge_train.TRAIN_BUILD_FAILED)
    for item in build['excluded']:
        member = by_task.get(item.get('key')) if type(item) is dict else None
        if member is None or not _code(item.get('code')):
            return _refused(merge_train.TRAIN_BUILD_FAILED)
        excluded.append({'task_id': member['task_id'], 'pr': member['pr'],
                         'code': item['code']})
    train_head = build.get('head')
    if train_head is None:
        codes = build['codes']
        code = codes[0] if codes and _code(codes[0]) else merge_train.TRAIN_BUILD_FAILED
        return _refused(code, {'excluded': excluded})
    if (not _sha40(train_head) or len(build['merged']) < MIN_MEMBERS or
            any(key not in by_task for key in build['merged']) or
            len(set(build['merged'])) != len(build['merged'])):
        return _refused(merge_train.TRAIN_BUILD_FAILED)
    merged = [by_task[key] for key in build['merged']]

    # 5. Record, state BUILT and no PR yet.
    train_id = 't' + moment.strftime('%Y%m%d%H%M%S') + '-' + secrets.token_hex(2)
    branch = BRANCH_PREFIX + train_id
    record = {
        'schema_version': merge_train_store.SCHEMA_VERSION,
        'train_id': train_id,
        'main_sha': main_sha,
        'members': merged,
        'excluded': excluded,
        'train_branch': branch,
        'train_head': train_head,
        'train_pr': None,
        'state': 'BUILT',
        'merge_attempt': None,
        'merge_sha': None,
        'created_at': moment.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    try:
        stored = merge_train_store.create(record, root=train_root)
    except Exception as error:
        return _refused(_store_code(error))
    kept = {'train_id': train_id}

    # 6. Push the candidate to its own new branch; never force.
    if _succeeded(git_runner, root,
                  ['push', 'origin', train_head + ':refs/heads/' + branch]) is None:
        return _refused(TRAIN_PUSH_FAILED, kept)

    # 7. Open one train PR and confirm it is the only open PR at the train head.
    title, body = _pr_text(train_id, main_sha, train_head, merged)
    if _succeeded(gh_runner, root, ['pr', 'create', '--base', 'main', '--head', branch,
                                    '--title', title, '--body', body]) is None:
        return _refused(TRAIN_PR_CREATE_FAILED, kept)
    train_pr = _open_train_pr(gh_runner, root, branch, train_head)
    if train_pr is None:
        return _refused(TRAIN_PR_CREATE_FAILED, kept)

    # 8. Record the PR.
    try:
        merge_train_store.compare_and_update(train_id, stored['record_sha256'],
                                             root=train_root, train_pr=train_pr)
    except Exception as error:
        return _refused(_store_code(error), {'train_id': train_id, 'train_pr': train_pr})

    return {'step': STEP_BUILT, 'codes': [], 'result': {
        'train_id': train_id,
        'train_pr': train_pr,
        'train_head': train_head,
        'main_sha': main_sha,
        'members': [{'task_id': member['task_id'], 'pr': member['pr'],
                     'head': member['head']} for member in merged],
        'excluded': excluded,
    }}
