"""Advance one durable Task by one step: publish, EC2 remote test, review, repair, completion, merge or closure."""

import hashlib
import contextlib
from datetime import datetime, timedelta, timezone
import io
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from argparse import Namespace

from . import active_pm_inbox, github_events, remote_test, review_dispatch, review_repair
from . import github_identity, review_result, sensitive_paths, task_completion
from . import mac_full_suite, task_submission_cli
from .ai_github_exec import AiIdentityError, verified_ai_env
from .errors import LLMToolError
from .task_store import TaskStore, _json_bytes

# Outcomes that remote_test.py records as UNAVAILABLE: nothing ran on EC2, so
# the next advance step may retry the same head. A hosted run is read back by
# its nonce on the next step and never dispatched twice.
_REMOTE_TEST_RETRY_CODES = frozenset((
    'SSH_UNAVAILABLE', 'SSH_FAILED', 'REMOTE_BUSY', 'REMOTE_LOCK_UNSAFE',
    'HOSTED_PENDING', 'HOSTED_QUEUE_FULL', 'HOSTED_DISPATCH_UNCONFIRMED'))


class _PublishRefusal(Exception):
    def __init__(self, code, operation=None):
        self.code = code
        # Set only for a refusal after this publish's own gh pr create (#1139).
        self.operation = operation


_ACTIVITY_MARKER = '<!-- activity: type=task actor=PM target=PR status=TRACKING -->'
_PR_URL = re.compile(
    r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100})'
    r'/pull/([1-9][0-9]{0,6})')
_HEX40 = re.compile(r'[0-9a-f]{40}')


_REMOTE_GIT_COMMANDS = frozenset(('push', 'fetch', 'pull', 'ls-remote', 'clone'))


def _publish_run_git(root, args):
    cmd = ['git', *args]
    if args and args[0] in _REMOTE_GIT_COMMANDS:
        try:
            env = verified_ai_env()
        except AiIdentityError as exc:
            print(f'llmtool: GITHUB_AI_IDENTITY_REFUSED {exc.code}', file=sys.stderr)
            return subprocess.CompletedProcess(cmd, 2, stdout=b'', stderr=b'')
        return subprocess.run(cmd, cwd=root, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              check=False, timeout=30, env=env)
    return subprocess.run(cmd, cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=False, timeout=30)


def _publish_run_gh(root, args):
    cmd = ['gh', *args]
    try:
        env = verified_ai_env()
    except AiIdentityError as exc:
        print(f'llmtool: GITHUB_AI_IDENTITY_REFUSED {exc.code}', file=sys.stderr)
        return subprocess.CompletedProcess(cmd, 2, stdout=b'', stderr=b'')
    return subprocess.run(cmd, cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=False, timeout=30, env=env)


def _publish_pause(seconds):
    time.sleep(seconds)


def _publish_output(result):
    if type(getattr(result, 'stdout', None)) is bytes:
        try:
            return result.stdout.decode('utf-8')
        except UnicodeDecodeError:
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
    if type(getattr(result, 'stdout', None)) is str:
        return result.stdout
    raise _PublishRefusal('PUBLISH_COMMAND_FAILED')


def _publish_command(runner, root, args, *, code='PUBLISH_COMMAND_FAILED'):
    try:
        result = runner(root, args)
    except Exception:
        raise _PublishRefusal(code) from None
    if getattr(result, 'returncode', 1) != 0:
        raise _PublishRefusal(code)
    return _publish_output(result)


def _publication_text(record, executor_task, branch):
    """Build publication title/body from the screened PM intake when readable."""
    task_id = record.get('task_id') if type(record) is dict else None
    old_title = f'llmtool Task {task_id}'
    old_body = f'Automated publication for llmtool Task {task_id}.'
    response = record.get('response') if type(record) is dict else None
    assignment = record.get('pm_assignment') if type(record) is dict else None
    summary = None
    try:
        response = json.loads(response)
        if type(response) is dict and type(response.get('summary')) is str:
            summary = response['summary'].strip()
    except (TypeError, ValueError):
        pass
    title = re.sub(r'\s+', ' ', summary).strip()[:120] if summary else old_title
    if not summary:
        return title, old_body

    try:
        if type(assignment) is not dict:
            return title, old_body
        goal = assignment.get('goal')
        acceptance = assignment.get('acceptance')
        if (type(goal) is not str or not goal.strip() or type(acceptance) is not list or
                any(type(item) is not str or not item.strip() for item in acceptance) or
                type(task_id) is not str or type(executor_task) is not str or
                type(branch) is not str):
            return title, old_body
        body = '\n'.join([
            summary, '', goal.strip(), '', 'Acceptance:',
            *[f'- {item.strip()}' for item in acceptance], '',
            f'Task ID: {task_id}', f'D6 executor: {executor_task}',
            f'Branch: {branch}',
        ])
        return title, body
    except (TypeError, ValueError, IndexError):
        return title, old_body


def _find_published_pr(gh_runner, root, branch, expected_head):
    """Find one PR for an exact published D6 head, without changing GitHub."""
    try:
        output = _publish_command(gh_runner, root,
            ['pr', 'list', '--head', branch, '--state', 'all',
             '--json', 'number,headRefOid'], code='PUBLISH_GITHUB_FAILED')
        pulls = json.loads(output)
    except Exception:
        return None
    if type(pulls) is not list:
        return None
    matches = [pull for pull in pulls if type(pull) is dict and
               pull.get('headRefOid') == expected_head]
    if len(matches) != 1:
        return None
    number = matches[0].get('number')
    return number if type(number) is int and number > 0 else None


def _open_pr_present(gh_runner, root, branch):
    """Return whether the branch already has an open PR; any doubt refuses."""
    output = _publish_command(gh_runner, root,
        ['pr', 'list', '--head', branch, '--state', 'open',
         '--json', 'number,headRefOid'], code='PUBLISH_GITHUB_FAILED')
    try:
        pulls = json.loads(output)
    except (ValueError, TypeError):
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED') from None
    if type(pulls) is not list:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    return bool(pulls)


def _require_published_head(git_runner, root, branch, head):
    """Refuse a PR unless the cleared head is still the local and pushed tip."""
    local = _publish_command(git_runner, root, ['rev-parse', '--verify', 'HEAD']).strip()
    remote = _publish_command(git_runner, root,
        ['ls-remote', '--heads', 'origin', f'refs/heads/{branch}']).split()
    if local != head or not remote or remote[0] != head:
        raise _PublishRefusal('PUBLISH_HEAD_MOVED')


def _read_open_prs(gh_runner, root, branch):
    output = _publish_command(gh_runner, root,
        ['pr', 'list', '--head', branch, '--state', 'open',
         '--json', 'number,headRefOid'], code='PUBLISH_GITHUB_FAILED')
    try:
        return json.loads(output)
    except (ValueError, TypeError):
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED') from None


def _gh_succeeded(gh_runner, root, args):
    try:
        return getattr(gh_runner(root, args), 'returncode', 1) == 0
    except Exception:
        return False


def _creation_evidence(output):
    """Return (url, repository, number) from gh pr create's last non-empty line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    match = _PR_URL.fullmatch(lines[-1]) if lines else None
    if match is None:
        return None
    return match.group(0), match.group(1), int(match.group(2))


def _listed_pr(pulls, created):
    """Return (number, head) for the PR this call follows in one listing.

    With creation evidence only the created number is followed; it may be
    absent (number None). Without it, the listing's first PR is read as today.
    """
    if type(pulls) is not list or not pulls:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    if created is None:
        pull = pulls[0]
    else:
        pull = next((item for item in pulls if type(item) is dict and
                     type(item.get('number')) is int and
                     item['number'] == created[2]), None)
        if pull is None:
            return None, None
    number = pull.get('number') if type(pull) is dict else None
    head = pull.get('headRefOid') if type(pull) is dict else None
    if type(number) is not int or number < 1 or type(head) is not str:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    return number, head


def _gh_json_value(gh_runner, root, args, key):
    try:
        value = json.loads(_publish_command(gh_runner, root, args,
                                            code='PUBLISH_GITHUB_FAILED'))
    except Exception:
        return None
    value = value.get(key) if type(value) is dict else None
    return value if type(value) is str and value else None


def _recheck_created_pr(gh_runner, root, branch, created, title, body):
    """Read the created PR once; return (recheck, observed head or None)."""
    _, repository, number = created
    try:
        view = json.loads(_publish_command(gh_runner, root,
            ['pr', 'view', str(number), '--repo', repository, '--json',
             'number,url,headRefName,headRefOid,isCrossRepository,state,author,title,body'],
            code='PUBLISH_GITHUB_FAILED'))
    except Exception:
        return 'FAIL:view', None
    if type(view) is not dict:
        return 'FAIL:view', None
    observed = view.get('headRefOid')
    observed = observed if type(observed) is str and _HEX40.fullmatch(observed) else None
    url = view.get('url')
    match = _PR_URL.fullmatch(url) if type(url) is str else None
    author = view.get('author')
    login = author.get('login') if type(author) is dict else None
    if type(view.get('number')) is not int or view['number'] != number:
        return 'FAIL:number', observed
    if match is None or match.group(1) != repository or int(match.group(2)) != number:
        return 'FAIL:url', observed
    if _gh_json_value(gh_runner, root, ['repo', 'view', '--json', 'nameWithOwner'],
                      'nameWithOwner') != repository:
        return 'FAIL:nameWithOwner', observed
    if view.get('headRefName') != branch:
        return 'FAIL:headRefName', observed
    if view.get('isCrossRepository') is not False:
        return 'FAIL:isCrossRepository', observed
    if view.get('state') != 'OPEN':
        return 'FAIL:state', observed
    if (type(login) is not str or not login or
            _gh_json_value(gh_runner, root, ['api', 'user'], 'login') != login):
        return 'FAIL:author', observed
    if view.get('title') != title:
        return 'FAIL:title', observed
    if view.get('body') != body:
        return 'FAIL:body', observed
    if observed is None:
        return 'FAIL:headRefOid', observed
    return 'PASS', observed


def _publish_created_pr(gh_runner, root, branch, task_id, expected_head, title, body):
    """Open one PR; only a verified PR this call created may be closed (#1139)."""
    try:
        result = gh_runner(root, ['pr', 'create', '--head', branch,
                                  '--base', 'main', '--title', title, '--body', body])
    except Exception:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED') from None
    if getattr(result, 'returncode', 1) != 0:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    try:
        created = _creation_evidence(_publish_output(result))
    except _PublishRefusal:
        created = None
    operation = {
        'task_id': task_id if type(task_id) is str else None,
        'repository': created[1] if created else None,
        'branch': branch, 'pr': created[2] if created else None,
        'creation_url': created[0] if created else None,
        'expected_head': expected_head, 'observed_head': None,
        'recheck': 'FAIL:creation_url' if created is None else 'FAIL:list',
        'close_attempted': False, 'record_comment': 'NOT_POSTED',
        'outcome': 'NOT_VERIFIED_NOT_CLOSED',
        'next_action': 'inspect the branch PRs; this publish closed nothing',
    }
    for attempt in range(4):
        if attempt:
            _publish_pause(2)
        try:
            number, head = _listed_pr(_read_open_prs(gh_runner, root, branch), created)
        except _PublishRefusal as exc:
            operation['recheck'] = 'FAIL:list'
            raise _PublishRefusal(exc.code, operation) from None
        if head is not None:
            operation['observed_head'] = head if _HEX40.fullmatch(head) else None
        if head == expected_head:
            return number
    if created is None:
        # No creation evidence: nothing may be closed, so refuse as today.
        operation['recheck'] = 'FAIL:creation_url'
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED', operation)
    _, repository, number = created
    recheck, observed = _recheck_created_pr(gh_runner, root, branch, created, title, body)
    operation['recheck'] = recheck
    if observed is not None:
        operation['observed_head'] = observed
    if recheck == 'PASS' and observed == expected_head:
        return number
    if recheck != 'PASS':
        operation.update(outcome='LEFT_OPEN', next_action=(
            f'inspect PR #{number}; it is still open and a later publish treats it as existing'))
        raise _PublishRefusal('PUBLISH_MOVED_PR_OPEN', operation)
    operation['close_attempted'] = True
    if _gh_succeeded(gh_runner, root, ['pr', 'close', str(number), '--repo', repository]):
        code = 'PUBLISH_HEAD_MOVED'
        operation.update(outcome='CLOSED', next_action=(
            'publish again; the branch stays pushed and a later publish may open a new PR'))
    else:
        code = 'PUBLISH_MOVED_PR_OPEN'
        operation.update(outcome='CLOSE_FAILED_LEFT_OPEN', next_action=(
            f'inspect PR #{number}; it is still open and a later publish treats it as existing'))
    posted = dict(operation, record_comment='POSTED')
    record = _ACTIVITY_MARKER + '\n' + json.dumps(posted, sort_keys=True,
                                                  separators=(',', ':'))
    operation['record_comment'] = ('POSTED' if _gh_succeeded(gh_runner, root,
        ['pr', 'comment', str(number), '--repo', repository, '--body', record])
        else 'FAILED')
    raise _PublishRefusal(code, operation)


def _publish_pr(gh_runner, root, branch, task_id, expected_head, title, body):
    pulls = _read_open_prs(gh_runner, root, branch)
    if type(pulls) is not list:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    if not pulls:
        return _publish_created_pr(gh_runner, root, branch, task_id, expected_head,
                                   title, body)
    # A PR open before this call is never closed; a head mismatch refuses.
    for attempt in range(4):
        if type(pulls) is not list or not pulls:
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
        pull = pulls[0]
        number = pull.get('number') if type(pull) is dict else None
        head = pull.get('headRefOid') if type(pull) is dict else None
        if type(number) is not int or number < 1 or type(head) is not str:
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
        if head == expected_head:
            return number
        if attempt == 3:
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
        _publish_pause(2)
        output = _publish_command(gh_runner, root,
            ['pr', 'list', '--head', branch, '--state', 'open',
             '--json', 'number,headRefOid'], code='PUBLISH_GITHUB_FAILED')
        try:
            pulls = json.loads(output)
        except (ValueError, TypeError):
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED') from None


def _merge_refusal(code):
    return {'step': 'MERGE_REFUSED', 'codes': [code], 'result': None}


def _has_pr_label(pr, label):
    labels = pr.get('labels', []) if type(pr) is dict else None
    if type(labels) is not list:
        return None
    for item in labels:
        name = item.get('name') if type(item) is dict else item
        if type(name) is not str:
            return None
        if name == label:
            return True
    return False


def _owner_acceptance_required(files):
    if type(files) is not list:
        return True
    for item in files:
        path = item.get('filename') if type(item) is dict else None
        if type(path) is not str:
            return True
        if (re.fullmatch(r'tools/llmtool/llmtool/chat_tui[^/]*\.py', path) or
                re.fullmatch(r'tools/llmtool/llmtool/tui_[^/]*\.py', path) or
                re.fullmatch(r'tools/llmtool/tests/test_chat_tui[^/]*', path) or
                re.fullmatch(r'tools/llmtool/tests/test_tui_[^/]*', path) or
                path == 'tools/llmtool/tests/tui_pty_driver.py'):
            return True
    return False


def _merge_checks(record, number, head, pr, github_get, *, allow_missing_macos=False,
                  owner_acceptance=None, acceptance_out=None):
    """Check every exact-head merge gate; return None or one refusal code.

    acceptance_out, when given, is a dict that gets the carried-acceptance
    evidence on a carry pass (#1237). Without it carry mode never runs.
    """
    try:
        checks = github_get(f'commits/{head}/check-runs?per_page=100')
        files = github_get(f'pulls/{number}/files?per_page=100')
    except Exception:
        return 'MERGE_CHECKS_UNAVAILABLE'
    if (type(checks) is not dict or type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] != len(checks['check_runs']) or
            len(checks['check_runs']) > 100 or type(files) is not list or len(files) >= 100):
        return 'MERGE_CHECKS_UNAVAILABLE'
    check_runs = checks['check_runs']
    if not check_runs:
        return 'MERGE_CHECKS_MISSING'
    superseded = {}
    for item in check_runs:
        if (type(item) is dict and type(item.get('id')) is int and type(item.get('name')) is str and
                item.get('head_sha') == head and item.get('status') == 'completed' and
                item.get('conclusion') == 'success'):
            superseded[item['name']] = max(item['id'], superseded.get(item['name'], 0))
    # An older run is excused only by a newer SUCCESS of the same check name (#1433).
    if any(type(item) is not dict or type(item.get('name')) is not str or
           item.get('head_sha') != head or
           (not (type(item.get('id')) is int and item['id'] < superseded.get(item['name'], 0)) and
            (item.get('status') != 'completed' or
             item.get('conclusion') not in ('success', 'skipped'))) for item in check_runs):
        return 'MERGE_CHECKS_NOT_GREEN'
    if pr.get('state') not in (None, 'open') or pr.get('merged') is True:
        return 'MERGE_PR_NOT_OPEN'
    if type(pr.get('mergeable')) is not bool or pr.get('mergeable') is not True:
        return 'MERGE_PR_NOT_MERGEABLE'
    paths = [item.get('filename') for item in files if type(item) is dict]
    if len(paths) != len(files) or any(type(path) is not str for path in paths):
        return 'MERGE_CHECKS_UNAVAILABLE'
    docs_only = task_completion._docs_only_ci(paths)
    if docs_only:
        required_checks = ['check (docs)']
    else:
        required_checks = ['check (Linux 3.14)']
    requires_macos = not docs_only
    if requires_macos:
        required_checks.append('check (macOS 3.13)')
    names = {item['name'] for item in check_runs}
    missing_macos = requires_macos and 'check (macOS 3.13)' not in names
    other_missing = any(name not in names for name in required_checks
                        if name != 'check (macOS 3.13)')
    if other_missing or (missing_macos and not allow_missing_macos):
        return 'MERGE_REQUIRED_CHECK_MISSING'
    # #1182: a skipped required check never counts as SUCCESS. Every run is
    # completed here, so a required name without a success run was skipped.
    # The macOS check keeps its own skipped and mac-ci rules below.
    if any(not any(item['name'] == name and item.get('conclusion') == 'success'
                   for item in check_runs)
           for name in required_checks if name != 'check (macOS 3.13)'):
        return 'MERGE_REQUIRED_CHECK_SKIPPED'
    if owner_acceptance is not None:
        carry = {} if type(acceptance_out) is dict else None
        acceptance_code = _owner_acceptance_code(owner_acceptance, number, head, github_get,
                                                 carry_out=carry)
        if acceptance_code:
            return acceptance_code
        if carry:
            # The carry passed; record it with every exact-head H1 check run.
            acceptance_out['original'] = owner_acceptance
            acceptance_out.update(carry)
            acceptance_out['h1_checks'] = sorted(
                f"{item['name']}={item.get('conclusion')}" for item in check_runs)
    elif _owner_acceptance_required(files):
        return 'MERGE_OWNER_ACCEPTANCE_MANUAL'
    if not docs_only:
        mac_runs = [item for item in check_runs if item.get('name') == 'check (macOS 3.13)']
        # GitHub can retain the earlier skipped run after mac-ci starts a new
        # run on the same head. Any successful exact-head macOS run is enough;
        # failures and in-progress runs have already been refused above.
        macos_not_green = not any(item.get('conclusion') == 'success' for item in mac_runs)
        if macos_not_green and mac_runs:
            if allow_missing_macos:
                return 'MERGE_MACOS_CHECK_SKIPPED'
            return 'MERGE_MACOS_CHECK_REQUIRED'
    if missing_macos:
        return ('MERGE_MACOS_CHECK_MISSING' if allow_missing_macos else
                'MERGE_REQUIRED_CHECK_MISSING')
    return None


def _merge_main_gate(head, github_get, pr):
    """Owner decision #1317 (loose merge rule): a head behind main may merge
    when GitHub reports it mergeable; exact-head review and CI evidence stay
    required; any doubt fails closed."""
    try:
        branch = github_get('branches/main')
        commit = branch.get('commit') if type(branch) is dict else None
        main_sha = commit.get('sha') if type(commit) is dict else None
        if type(main_sha) is not str or re.fullmatch(r'[0-9a-f]{40}', main_sha) is None:
            return 'MERGE_CHECKS_UNAVAILABLE', None
        # The GitHub read path admits compare only between two exact SHAs.
        compare = github_get(f'compare/{main_sha}...{head}')
    except Exception:
        return 'MERGE_CHECKS_UNAVAILABLE', None
    status = compare.get('status') if type(compare) is dict else None
    if status in ('ahead', 'identical'):
        return None, 0
    if status in ('behind', 'diverged'):
        # Owner decision #1317 (loose merge rule): a head behind main may merge
        # when GitHub reports it mergeable; exact-head review and CI evidence stay
        # required; any doubt fails closed.
        behind_by = compare.get('behind_by') if type(compare) is dict else None
        if type(behind_by) is not int or isinstance(behind_by, bool) or behind_by < 1:
            return 'MERGE_CHECKS_UNAVAILABLE', None
        mergeable_state = pr.get('mergeable_state') if type(pr) is dict else None
        if mergeable_state == 'dirty':
            return 'MERGE_PR_CONFLICT', None
        if type(mergeable_state) is not str or mergeable_state == 'unknown':
            return 'MERGE_MERGEABLE_UNKNOWN', None
        if mergeable_state in ('clean', 'behind', 'unstable', 'has_hooks'):
            return None, behind_by
        return 'MERGE_PR_NOT_MERGEABLE', None
    return 'MERGE_CHECKS_UNAVAILABLE', None


def _review_ci_gate(number, head, github_get):
    """Require the newest exact-head Linux CI check to pass before review."""
    try:
        checks = github_get(f'commits/{head}/check-runs?per_page=100')
        files = github_get(f'pulls/{number}/files?per_page=100')
    except Exception:
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    if (type(checks) is not dict or type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] < len(checks['check_runs']) or
            checks['total_count'] < 0 or len(checks['check_runs']) > 100 or
            type(files) is not list or len(files) >= 100):
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    paths = []
    for item in files:
        path = item.get('filename') if type(item) is dict else None
        if type(path) is not str:
            return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
        paths.append(path)
    docs_only = task_completion._docs_only_ci(paths)
    required_name = 'check (docs)' if docs_only else 'check (Linux 3.14)'
    runs = checks['check_runs']
    if any(type(item) is not dict for item in runs):
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    candidates = [item for item in runs if item.get('head_sha') == head and
                  item.get('name') == required_name]
    for item in candidates:
        if (type(item.get('id')) is not int or item['id'] < 1 or
                type(item.get('status')) is not str):
            return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    if not candidates:
        return 'WAITING_CI', 'LINUX_CI_PENDING'
    latest = max(candidates, key=lambda item: item['id'])
    if latest['status'] != 'completed':
        return 'WAITING_CI', 'LINUX_CI_PENDING'
    if type(latest.get('conclusion')) is not str:
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    if latest['conclusion'] == 'success':
        return None, None
    return 'CI_FAILED', 'LINUX_CI_FAILED'


def _remote_test_receipt_summary(record, head):
    receipts = record.get('remote_test_receipts', []) if type(record) is dict else []
    if type(receipts) is not list:
        return None
    for receipt in reversed(receipts):
        if type(receipt) is not dict or receipt.get('source_sha') != head:
            continue
        digest = receipt.get('receipt_digest')
        modules = receipt.get('modules')
        elapsed = receipt.get('elapsed_s')
        if (type(digest) is not str or re.fullmatch(r'[0-9a-f]{64}', digest) is None or
                receipt.get('result') not in ('PASS', 'FAIL', 'TIMEOUT', 'SHA_MISMATCH') or
                type(modules) is not list or not modules or
                any(type(module) is not str or
                    re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', module) is None
                    for module in modules) or
                type(receipt.get('ran_total')) is not int or receipt['ran_total'] < 0 or
                type(elapsed) not in (int, float) or not math.isfinite(elapsed) or
                elapsed < 0):
            return None
        digest_input = dict(receipt)
        digest_input.pop('receipt_digest', None)
        try:
            if hashlib.sha256(_json_bytes(digest_input)).hexdigest() != digest:
                return None
        except Exception:
            return None
        actions = receipt.get('actions')
        hosted_run_id = (actions.get('run_id')
                         if type(actions) is dict and type(actions.get('run_id')) is int and actions['run_id'] > 0
                         else None)
        return {
            'result': receipt.get('result'),
            'modules': receipt.get('modules'),
            'ran_total': receipt.get('ran_total'),
            'elapsed_s': receipt.get('elapsed_s'),
            'receipt_digest': digest[:8],
            'hosted_run_id': hosted_run_id,
        }
    return None


def _post_remote_test_comment(gh_runner, root, number, task_id, d6, head, summary):
    modules = ','.join(summary['modules'])
    hosted_run_id = summary.get('hosted_run_id')
    prefix = (f"remote-test (GitHub-hosted run {hosted_run_id}), result="
              if hosted_run_id is not None
              else "EC2 remote-test, result=")
    body = ('<!-- activity: type=task actor=PM target=PR status=TRACKING -->\n'
            f"{prefix}{summary['result']}, modules={modules}, "
            f"tests run={summary['ran_total']}, seconds={summary['elapsed_s']}, "
            f"receipt={summary['receipt_digest']}, Task={task_id}, D6={d6.get('task_id')}, head={head}")
    response = gh_runner(root, ['pr', 'comment', str(number), '--body', body])
    if getattr(response, 'returncode', 1) != 0:
        raise _PublishRefusal('REMOTE_TEST_COMMENT_FAILED')


def _post_review_verdict_comment(gh_runner, root, number, review_text):
    response = gh_runner(root, ['pr', 'comment', str(number), '--body', review_text])
    if getattr(response, 'returncode', 1) != 0:
        raise _PublishRefusal('REVIEW_VERDICT_COMMENT_FAILED')


def _merge_pass_claim(record, number, head, events):
    candidates = []
    for claim in _claims(record):
        if (type(claim) is not dict or claim.get('pr') != number or
                claim.get('head_sha') != head or
                claim.get('state') not in ('COMPLETED', 'DISPATCHED') or
                type(claim.get('version')) is not int):
            continue
        result = claim.get('review_result')
        outcome = next((event.get('outcome') or event.get('state') for event in events
                        if type(event) is dict and event.get('source') == 'REVIEWER' and
                        event.get('source_sha') == head and
                        event.get('version') == claim['version']), None)
        if outcome is None:
            continue
        if claim.get('state') == 'COMPLETED':
            if type(result) is not dict:
                continue
            verdict = result.get('verdict')
        else:
            # Claude Reviewer dispatches remain DISPATCHED and keep the
            # classified verdict only in the matching REVIEWER completion event.
            verdict = 'PASS' if outcome == 'REVIEW_PASS' else None
        candidates.append((claim['version'], verdict, outcome))
    if not candidates:
        return None
    passes = [item[0] for item in candidates
              if item[1] == 'PASS' and item[2] == 'REVIEW_PASS']
    if not passes:
        return 'MERGE_REVIEW_NOT_PASS'
    top = max(passes)
    # Any newer claim on this PR and head, in any state and with or without a
    # REVIEWER event, vetoes the PASS; so does a claim with no integer version.
    for claim in _claims(record):
        if (type(claim) is not dict or claim.get('pr') != number or
                claim.get('head_sha') != head):
            continue
        version = claim.get('version')
        if type(version) is not int or version > top:
            return 'MERGE_NEWER_REVIEW_EXISTS'
    if any(item[0] == top and (item[1] != 'PASS' or item[2] != 'REVIEW_PASS')
           for item in candidates):
        return 'MERGE_REVIEW_NOT_PASS'
    if any(type(event) is dict and event.get('source') == 'REVIEWER' and
           event.get('source_sha') == head and type(event.get('version')) is int and
           event['version'] > top for event in events):
        return 'MERGE_NEWER_REVIEW_EXISTS'
    return top


# A recorded Owner acceptance is a PM relay comment in this repository.
_OWNER_ACCEPTANCE_MARKER = ('<!-- activity: type=pm_progress actor=PM target=Owner '
                            'status=ANSWERED -->')
_OWNER_ACCEPTANCE_URL = re.compile(
    r'https://github\.com/([^/#?\s]+/[^/#?\s]+)/(?:issues|pull)/([1-9][0-9]*)'
    r'#issuecomment-([1-9][0-9]*)')
_UTC_TIME = re.compile(
    r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})'
    r'(?:\.([0-9]{1,6}))?(?:Z|\+00:00)')


def _utc_time(value):
    """Parse an ISO 8601 UTC time; return None when it does not parse."""
    match = _UTC_TIME.fullmatch(value) if type(value) is str else None
    if match is None:
        return None
    fraction = (match.group(7) or '').ljust(6, '0')
    try:
        return datetime(*(int(part) for part in match.groups()[:6]), int(fraction),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


def _comment_after_commit_code(sha, created_at, github_get):
    """Require the comment to be created strictly after the commit sha.

    Return None, MERGE_OWNER_ACCEPTANCE_UNAVAILABLE or MERGE_OWNER_ACCEPTANCE_STALE.
    """
    try:
        commit = github_get(f'commits/{sha}')
    except Exception:
        return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
    detail = commit.get('commit') if type(commit) is dict else None
    commit_times = []
    for role in ('author', 'committer'):
        person = detail.get(role) if type(detail) is dict else None
        moment = _utc_time(person.get('date') if type(person) is dict else None)
        if moment is None:
            return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
        commit_times.append(moment)
    created = _utc_time(created_at)
    if created is None:
        return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
    # The acceptance must come strictly after the commit.
    if created <= max(commit_times):
        return 'MERGE_OWNER_ACCEPTANCE_STALE'
    return None


# Carry checks, in the order they run and are reported (#1237).
_CARRY_CHECKS = ('descendant', 'non_test', 'new_file', 'test_deleted',
                 'assertion_removed', 'skip_added')
_CARRY_TEST_HELPERS = frozenset(
    f'tools/llmtool/tests/{name}' for name in
    ('tui_pty_driver.py', 'notification_fixture.py', 'review_trust_fixture.py'))
_CARRY_TEST_DEF = re.compile(r'\bdef\s+(test\w*)')
_CARRY_REMOVED_BAD = re.compile(r'assert|skip|expectedfailure', re.IGNORECASE)
_CARRY_ADDED_BAD = re.compile(r'skip|expectedfailure', re.IGNORECASE)


def _carry_test_file(path):
    """Return whether path is a plain test file a carry may touch."""
    if type(path) is not str:
        return False
    if (re.fullmatch(r'(?:tools/llmtool/tests|scripts)/test_[^/]*\.py', path) is None and
            path not in _CARRY_TEST_HELPERS):
        return False
    try:
        # Any doubt about the rule list counts as sensitive.
        return sensitive_paths.is_sensitive(path) is False
    except Exception:
        return False


def _carry_compare(base, target, github_get):
    """Read one compare; return (answer, entries) or None when unknown."""
    try:
        answer = github_get(f'compare/{base}...{target}?per_page=100')
    except Exception:
        return None
    files = answer.get('files') if type(answer) is dict else None
    if type(files) is not list or len(files) >= 100:
        return None
    for entry in files:
        if (type(entry) is not dict or type(entry.get('filename')) is not str or
                type(entry.get('status')) is not str):
            return None
        if 'previous_filename' in entry and type(entry['previous_filename']) is not str:
            return None
    return answer, files


def _carry_paths(entries):
    paths = set()
    for entry in entries:
        paths.add(entry['filename'])
        if 'previous_filename' in entry:
            paths.add(entry['previous_filename'])
    return paths


def _carry_patch_lines(patch):
    """Split a patch into (removed, added) line texts; '@@' lines are skipped."""
    removed, added = [], []
    for line in patch.split('\n'):
        if line.startswith('@@'):
            continue
        if line.startswith('-'):
            removed.append(line[1:])
        elif line.startswith('+'):
            added.append(line[1:])
    return removed, added


def _acceptance_carry_code(h0, head, created_at, github_get, carry_out):
    """Check that the acceptance at old head h0 may carry to head (#1237).

    Return None and fill carry_out when the PR's own changes from h0 to head
    only edit test files already in the PR, else one refusal code.
    """
    time_code = _comment_after_commit_code(h0, created_at, github_get)
    if time_code:
        return time_code
    try:
        branch = github_get('branches/main')
    except Exception:
        return 'MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN'
    commit = branch.get('commit') if type(branch) is dict else None
    main_sha = commit.get('sha') if type(commit) is dict else None
    if type(main_sha) is not str or _HEX40.fullmatch(main_sha) is None:
        return 'MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN'
    reads = [_carry_compare(main_sha, h0, github_get),
             _carry_compare(main_sha, head, github_get),
             _carry_compare(h0, head, github_get)]
    if any(item is None for item in reads):
        return 'MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN'
    (main_h0, s0_entries), (_main_h1, s1_entries), (h0_h1, d_entries) = reads
    if main_h0.get('status') not in ('ahead', 'diverged'):
        return 'MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN'
    base = h0_h1.get('merge_base_commit')
    if (h0_h1.get('status') != 'ahead' or type(base) is not dict or
            base.get('sha') != h0):
        return 'MERGE_ACCEPTANCE_CARRY_NOT_DESCENDANT'
    s0 = _carry_paths(s0_entries)
    s1 = _carry_paths(s1_entries)
    # A D entry that touches no PR path came only from merging main.
    own = sorted((entry for entry in d_entries
                  if entry['filename'] in s0 or entry['filename'] in s1 or
                  entry.get('previous_filename') in s0 or
                  entry.get('previous_filename') in s1),
                 key=lambda entry: entry['filename'])
    own_paths = sorted(_carry_paths(own) | (s1 - s0))
    if any(not _carry_test_file(path) for path in own_paths):
        return 'MERGE_ACCEPTANCE_CARRY_NON_TEST'
    if any(path not in s0 for path in own_paths):
        return 'MERGE_ACCEPTANCE_CARRY_NEW_FILE'
    if any(entry['status'] == 'removed' for entry in own):
        return 'MERGE_ACCEPTANCE_CARRY_TEST_DELETED'
    if any(entry['status'] != 'modified' or type(entry.get('patch')) is not str or
           type(entry.get('additions')) is not int or
           type(entry.get('deletions')) is not int for entry in own):
        return 'MERGE_ACCEPTANCE_CARRY_DIFF_UNKNOWN'
    patches = [_carry_patch_lines(entry['patch']) for entry in own]
    for removed, added in patches:
        kept = {match.group(1) for line in added
                for match in [_CARRY_TEST_DEF.search(line)] if match}
        if any(match.group(1) not in kept for line in removed
               for match in [_CARRY_TEST_DEF.search(line)] if match):
            return 'MERGE_ACCEPTANCE_CARRY_TEST_DELETED'
    # Fail closed: a moved or reworded assertion line also refuses.
    if any(_CARRY_REMOVED_BAD.search(line) for removed, _added in patches
           for line in removed):
        return 'MERGE_ACCEPTANCE_CARRY_ASSERTION_REMOVED'
    if any(_CARRY_ADDED_BAD.search(line) for _removed, added in patches
           for line in added):
        return 'MERGE_ACCEPTANCE_CARRY_SKIP_ADDED'
    carry_out.update({
        'h0': h0,
        'h1': head,
        'files': [{'path': entry['filename'], 'additions': entry['additions'],
                   'deletions': entry['deletions']} for entry in own],
        'checks': {name: 'PASS' for name in _CARRY_CHECKS},
    })
    return None


def _owner_acceptance_code(url, number, head, github_get, *, carry_out=None):
    """Check one Owner acceptance comment for this PR and head.

    Return None when it is valid, else one refusal code. Checks run in order:
    reference, read, html_url, edited, author, marker, PR, head, time.
    When the body does not name head but names exactly one other full SHA
    and carry_out is a dict, carry mode replaces the head step (#1237); on a
    carry pass carry_out gets the evidence.
    """
    match = _OWNER_ACCEPTANCE_URL.fullmatch(url) if type(url) is str else None
    if match is None:
        return 'MERGE_OWNER_ACCEPTANCE_REF_INVALID'
    try:
        slug = task_completion._trusted_slug()
    except Exception:
        return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
    if type(slug) is not str or match.group(1) != slug:
        return 'MERGE_OWNER_ACCEPTANCE_REF_INVALID'
    try:
        comment = github_get(f'issues/comments/{match.group(3)}')
    except Exception:
        return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
    if type(comment) is not dict or any(
            type(comment.get(key)) is not str
            for key in ('body', 'html_url', 'created_at', 'updated_at')):
        return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
    user = comment.get('user')
    if type(user) is not dict or type(user.get('login')) is not str:
        return 'MERGE_OWNER_ACCEPTANCE_UNAVAILABLE'
    if comment['html_url'] != url:
        return 'MERGE_OWNER_ACCEPTANCE_REF_INVALID'
    if comment['updated_at'] != comment['created_at']:
        return 'MERGE_OWNER_ACCEPTANCE_EDITED'
    # Only a person on a configured PM relay posting login counts; never a bot.
    if (user.get('type') != 'User' or user['login'] not in
            (github_identity.owner_login(), github_identity.ai_login())):
        return 'MERGE_OWNER_ACCEPTANCE_AUTHOR'
    body = comment['body']
    first_line = body.split('\n', 1)[0]
    if first_line.endswith('\r'):
        first_line = first_line[:-1]
    if first_line != _OWNER_ACCEPTANCE_MARKER:
        return 'MERGE_OWNER_ACCEPTANCE_MARKER'
    # '#17' must not match '#170' or 'x#17'.
    if type(number) is not int or not any(
            found.start() == 0 or not body[found.start() - 1].isalnum()
            for found in re.finditer(rf'#{number}(?!\d)', body)):
        return 'MERGE_OWNER_ACCEPTANCE_PR_MISMATCH'
    if type(head) is not str or _HEX40.fullmatch(head) is None:
        return 'MERGE_OWNER_ACCEPTANCE_HEAD_MISMATCH'
    # The full lowercase head, not part of a longer hex run.
    if re.search(rf'(?<![0-9a-fA-F]){head}(?![0-9a-fA-F])', body) is not None:
        return _comment_after_commit_code(head, comment['created_at'], github_get)
    # Without a place for its evidence, carry mode does not run.
    if type(carry_out) is not dict:
        return 'MERGE_OWNER_ACCEPTANCE_HEAD_MISMATCH'
    named = set(re.findall(r'(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])', body))
    if not named:
        return 'MERGE_OWNER_ACCEPTANCE_HEAD_MISMATCH'
    if len(named) > 1:
        return 'MERGE_ACCEPTANCE_CARRY_HEAD_AMBIGUOUS'
    return _acceptance_carry_code(named.pop(), head, comment['created_at'], github_get,
                                  carry_out)


def _merge_once(*, store, record, number, head, pr, events, github_get,
                gh_runner, root, owner_acceptance=None, owner_exception=None,
                sleep_fn=None, sleep=None):
    _sleep = time.sleep if sleep_fn is None and sleep is None else (sleep_fn if sleep_fn is not None else sleep)
    if type(number) is not int or number < 1:
        return _merge_refusal('MERGE_PR_UNAVAILABLE')
    if type(pr) is not dict or pr.get('number', number) != number:
        return _merge_refusal('MERGE_PR_UNAVAILABLE')
    pr_head = pr.get('head') if type(pr) is dict else None
    if type(pr_head) is not dict or pr_head.get('sha') != head:
        return _merge_refusal('MERGE_STALE_HEAD')
    if any(type(claim) is dict and claim.get('head_sha') == head and
           claim.get('pr') != number for claim in _claims(record)):
        return _merge_refusal('MERGE_CONFLICTING_PR')
    if pr.get('mergeable') is None:
        # GitHub may still be computing mergeability.
        for _ in range(3):
            _sleep(5)
            try:
                pr = github_get(f'pulls/{number}')
            except Exception:
                return _merge_refusal('MERGE_PR_UNAVAILABLE')
            if type(pr) is not dict:
                return _merge_refusal('MERGE_PR_UNAVAILABLE')
            fresh_head = pr.get('head')
            if (type(fresh_head) is not dict or fresh_head.get('sha') != head or
                    pr.get('state') not in (None, 'open') or
                    pr.get('merged') is True):
                break
            if pr.get('mergeable') is not None:
                break
    review_exception = None
    if owner_exception is not None:
        from . import owner_exception as owner_exception_mod
        v_code, v_evidence = owner_exception_mod.verify(
            owner_exception, number, github_get,
        )
        if v_code is not None:
            return _merge_refusal(v_code)
        review_version = None
        review_exception = {
            'type': owner_exception_mod.EXCEPTION_TYPE,
            'url': owner_exception,
            'evidence': v_evidence,
        }
    else:
        review_version = _merge_pass_claim(record, number, head, events)
        if type(review_version) is not int:
            return _merge_refusal(review_version or 'MERGE_REVIEW_NOT_PASS')
    acceptance_out = {}
    code = _merge_checks(record, number, head, pr, github_get,
                         allow_missing_macos=True, owner_acceptance=owner_acceptance,
                         acceptance_out=acceptance_out)
    acceptance_carry = acceptance_out or None
    if code and code not in ('MERGE_MACOS_CHECK_MISSING', 'MERGE_MACOS_CHECK_SKIPPED'):
        if code == 'MERGE_CHECKS_NOT_GREEN':
            # Preserve the merge refusal while identifying an exact-head
            # pending Mac check. Completed failures remain ordinary refusals.
            try:
                checks = github_get(f'commits/{head}/check-runs?per_page=100')
            except Exception:
                checks = None
            check_runs = checks.get('check_runs') if type(checks) is dict else None
            mac_pending = (type(check_runs) is list and any(
                type(item) is dict and item.get('head_sha') == head and
                item.get('name') == 'check (macOS 3.13)' and
                item.get('status') != 'completed' for item in check_runs))
            all_other_checks_green = (type(check_runs) is list and all(
                type(item) is dict and item.get('head_sha') == head and
                ((item.get('name') == 'check (macOS 3.13)' and
                  item.get('status') != 'completed') or
                 (item.get('status') == 'completed' and
                  item.get('conclusion') in ('success', 'skipped')))
                for item in check_runs))
            if mac_pending and all_other_checks_green:
                return {'step': 'MERGE_REFUSED', 'codes': [code],
                        'result': {'macos_check_pending': True}}
        return _merge_refusal(code)
    try:
        current_pr = github_get(f'pulls/{number}')
    except Exception:
        return _merge_refusal('MERGE_PR_UNAVAILABLE')
    # Re-fetch when GitHub is still computing mergeability.
    for _ in range(3):
        if type(current_pr) is not dict:
            break
        current_head = current_pr.get('head')
        if (type(current_head) is not dict or current_head.get('sha') != head or
                current_pr.get('number', number) != number):
            break
        if current_pr.get('state') not in (None, 'open') or current_pr.get('merged') is True:
            break
        if current_pr.get('mergeable') is not None:
            break
        _sleep(5)
        try:
            current_pr = github_get(f'pulls/{number}')
        except Exception:
            return _merge_refusal('MERGE_PR_UNAVAILABLE')
    current_head = current_pr.get('head') if type(current_pr) is dict else None
    if (type(current_head) is not dict or current_head.get('sha') != head or
            current_pr.get('number', number) != number):
        return _merge_refusal('MERGE_STALE_HEAD')
    if current_pr.get('state') not in (None, 'open') or current_pr.get('merged') is True:
        return _merge_refusal('MERGE_PR_NOT_OPEN')
    if current_pr.get('mergeable') is not True:
        return _merge_refusal('MERGE_PR_NOT_MERGEABLE')
    main_code, behind_by = _merge_main_gate(head, github_get, current_pr)
    if main_code:
        return _merge_refusal(main_code)
    if code in ('MERGE_MACOS_CHECK_MISSING', 'MERGE_MACOS_CHECK_SKIPPED'):
        has_mac_ci = _has_pr_label(current_pr, 'mac-ci')
        if has_mac_ci is None:
            return _merge_refusal('MERGE_LABELS_UNAVAILABLE')
        if not has_mac_ci:
            try:
                labeled = gh_runner(root, ['pr', 'edit', str(number), '--add-label', 'mac-ci'])
            except Exception:
                labeled = None
            if labeled is None or getattr(labeled, 'returncode', 1) != 0:
                return _merge_refusal('MERGE_MAC_CI_LABEL_FAILED')
            return {'step': 'WAITING_CI', 'codes': [], 'result': {'pr': number, 'head': head}}
        code = ('MERGE_MACOS_CHECK_REQUIRED' if code == 'MERGE_MACOS_CHECK_SKIPPED' else
                'MERGE_REQUIRED_CHECK_MISSING')
    if code:
        return _merge_refusal(code)
    prior = record.get('merge_attempt')
    if prior is not None:
        if prior.get('pr') != number or prior.get('head_sha') != head:
            return _merge_refusal('MERGE_CONFLICTING_PR')
        if prior.get('state') != 'UNKNOWN':
            return _merge_refusal('MERGE_ALREADY_CLAIMED')
        if (current_pr.get('state') != 'open' or current_pr.get('merged') is not False):
            return _merge_refusal('MERGE_ALREADY_CLAIMED')
    claimed_at_time = datetime.now(timezone.utc)
    claimed_at = claimed_at_time.isoformat(timespec='microseconds').replace('+00:00', 'Z')
    if prior is not None and claimed_at == prior.get('claimed_at'):
        # Keep a retry distinct even if the clock has not advanced.
        claimed_at = (claimed_at_time + timedelta(microseconds=1)).isoformat(
            timespec='microseconds').replace('+00:00', 'Z')
    claim = {'pr': number, 'head_sha': head, 'state': 'CLAIMED',
             'claimed_at': claimed_at}
    try:
        expected = {'record_sha256': record['record_sha256']}
        if prior is not None:
            expected['merge_attempt'] = prior
        store.compare_and_update(record['task_id'], expected=expected, merge_attempt=claim)
    except Exception:
        return _merge_refusal('MERGE_CLAIM_CONFLICT')
    args = ['pr', 'merge', str(number), '--squash', '--match-head-commit', head]
    try:
        result = gh_runner(root, args)
    except Exception:
        result = None
    if result is None or getattr(result, 'returncode', 1) != 0:
        # The command outcome can be ambiguous. A fresh exact-head merged read
        # resolves it as success; every other read preserves UNKNOWN.
        try:
            resolved_pr = github_get(f'pulls/{number}')
        except Exception:
            resolved_pr = None
        resolved_head = (resolved_pr.get('head')
                         if type(resolved_pr) is dict else None)
        merged = (type(resolved_pr) is dict and
                  resolved_pr.get('number', number) == number and
                  type(resolved_head) is dict and resolved_head.get('sha') == head and
                  resolved_pr.get('merged') is True)
        if merged:
            claim['state'] = 'MERGED'
            warning_codes = []
            update_kwargs = {'merge_attempt': claim}
            if review_exception is not None:
                update_kwargs['review_exception'] = review_exception
                record['review_exception'] = review_exception
            try:
                store.compare_and_update(record['task_id'], expected={'merge_attempt':
                    dict(claim, state='CLAIMED')}, **update_kwargs)
            except Exception:
                try:
                    store.compare_and_update(record['task_id'], expected={'merge_attempt':
                        dict(claim, state='CLAIMED')}, **update_kwargs)
                except Exception:
                    warning_codes.append('MERGE_RESULT_RECORD_FAILED')
            outcome = _merge_success(record, number, head, review_version, gh_runner, root,
                                     warning_codes, owner_acceptance=owner_acceptance,
                                     acceptance_carry=acceptance_carry,
                                     review_exception=review_exception,
                                     github_get=github_get)
            outcome['result']['main_behind_by'] = behind_by
            return outcome
        claim['state'] = 'UNKNOWN'
        try:
            store.compare_and_update(record['task_id'], expected={'merge_attempt':
                dict(claim, state='CLAIMED')}, merge_attempt=claim)
        except Exception:
            pass
        return _merge_refusal('MERGE_OUTCOME_UNKNOWN')
    claim['state'] = 'MERGED'
    warning_codes = []
    update_kwargs = {'merge_attempt': claim}
    if review_exception is not None:
        update_kwargs['review_exception'] = review_exception
        record['review_exception'] = review_exception
    try:
        store.compare_and_update(record['task_id'], expected={'merge_attempt':
            dict(claim, state='CLAIMED')}, **update_kwargs)
    except Exception:
        # The remote merge succeeded. A bounded state-record retry cannot
        # repeat the merge and avoids leaving a transient failure as CLAIMED.
        try:
            store.compare_and_update(record['task_id'], expected={'merge_attempt':
                dict(claim, state='CLAIMED')}, **update_kwargs)
        except Exception:
            pass
        warning_codes.append('MERGE_RESULT_RECORD_FAILED')
    outcome = _merge_success(record, number, head, review_version, gh_runner, root,
                             warning_codes, owner_acceptance=owner_acceptance,
                             acceptance_carry=acceptance_carry,
                             review_exception=review_exception,
                             github_get=github_get)
    outcome['result']['main_behind_by'] = behind_by
    return outcome


def _merge_success(record, number, head, review_version, gh_runner, root, warning_codes,
                   *, owner_acceptance=None, acceptance_carry=None, review_exception=None,
                   github_get=None):
    body = (f'llmtool task advance merge provenance: task={record["task_id"]} '
            f'head={head} review_version={review_version} checks=exact-head-success-or-skipped')
    if owner_acceptance is not None:
        body += f' owner_acceptance={owner_acceptance}'
    if review_exception is not None:
        body += f' review_exception={review_exception["type"]}'
    if acceptance_carry:
        acceptance_carry = dict(acceptance_carry, h1_review_version=review_version)
        files = ','.join(f"{item['path']}(+{item['additions']}/-{item['deletions']})"
                         for item in acceptance_carry['files'])
        checks = ','.join(f'{name}:{value}'
                          for name, value in acceptance_carry['checks'].items())
        body += (f" owner_acceptance_carry: original={acceptance_carry['original']} "
                 f"h0={acceptance_carry['h0']} h1={acceptance_carry['h1']} "
                 f'files={files} checks={checks} h1_review_version={review_version} '
                 f"h1_checks={','.join(acceptance_carry['h1_checks'])}")
    try:
        comment = gh_runner(root, ['pr', 'comment', str(number), '--body', body])
        if getattr(comment, 'returncode', 1) != 0:
            warning_codes.append('MERGE_PROVENANCE_COMMENT_FAILED')
    except Exception:
        warning_codes.append('MERGE_PROVENANCE_COMMENT_FAILED')
    suite_code = mac_full_suite.request_full_suite(gh_runner, root, github_get)
    if suite_code not in (
        mac_full_suite.MAC_FULL_SUITE_DISPATCHED,
        mac_full_suite.MAC_FULL_SUITE_ALREADY_QUEUED,
    ):
        warning_codes.append(suite_code)
    result = {'pr': number, 'head': head}
    if owner_acceptance is not None:
        result['owner_acceptance'] = owner_acceptance
    if acceptance_carry:
        result['owner_acceptance_carry'] = acceptance_carry
    if review_exception is not None:
        result['review_exception'] = review_exception
    return {'step': 'MERGED', 'codes': warning_codes, 'result': result}


def label_mac_ci_for_skipped_check(*, task_id, store=None, pr_number=None,
                                   github_get=None, merge_gh_runner=None, cwd=None):
    """Label an exact-head skipped macOS check without entering the merge path.

    Return None when the available evidence is not specifically a skipped
    macOS check. A successful or already-present label returns WAITING_CI.
    """
    store = TaskStore() if store is None else store
    try:
        record = store.get(task_id)
    except Exception:
        return None
    if type(record) is not dict:
        return None
    executor_task = (record.get('completion') or {}).get('executor_task_id')
    d6 = _d6_snapshot(record, executor_task) if type(executor_task) is str else None
    if (type(d6) is not dict or type(d6.get('result_head')) is not str or
            d6.get('recorded_result_head') != d6.get('result_head') or
            d6.get('dirty') is True or d6.get('state') == 'DIRTY'):
        return None
    number = _pr_number(record, pr_number)
    if number is None:
        return None
    get = task_completion._github_get if github_get is None else github_get
    head = d6['result_head']
    try:
        pr = _pr_snapshot(number, get)
    except Exception:
        return None
    if (type(pr) is not dict or pr.get('number', number) != number or
            type(pr.get('head')) is not dict or pr['head'].get('sha') != head or
            pr.get('state') not in (None, 'open') or pr.get('merged') is True or
            pr.get('mergeable') is not True):
        return None
    code = _merge_checks(record, number, head, pr, get, allow_missing_macos=True)
    if code != 'MERGE_MACOS_CHECK_SKIPPED':
        return None
    try:
        current_pr = get(f'pulls/{number}')
    except Exception:
        return None
    if (type(current_pr) is not dict or current_pr.get('number', number) != number or
            type(current_pr.get('head')) is not dict or
            current_pr['head'].get('sha') != head or
            current_pr.get('state') not in (None, 'open') or
            current_pr.get('merged') is True or current_pr.get('mergeable') is not True):
        return None
    has_mac_ci = _has_pr_label(current_pr, 'mac-ci')
    if has_mac_ci is None:
        return None
    if not has_mac_ci:
        runner = _publish_run_gh if merge_gh_runner is None else merge_gh_runner
        try:
            labeled = runner(d6.get('root') or cwd or os.getcwd(),
                             ['pr', 'edit', str(number), '--add-label', 'mac-ci'])
        except Exception:
            labeled = None
        if labeled is None or getattr(labeled, 'returncode', 1) != 0:
            return _merge_refusal('MERGE_MAC_CI_LABEL_FAILED')
    return {'step': 'WAITING_CI', 'codes': [], 'result': {'pr': number, 'head': head}}


def _record_d6_result(*, task_id, canonical_root, root):
    lifecycle_path = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    spec = importlib.util.spec_from_file_location('_llmtool_d6_worktree_lifecycle', lifecycle_path)
    if spec is None or spec.loader is None:
        raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.record_result(task_id=task_id, canonical_root=canonical_root, root=root)


def _publish_task(record, d6, executor_task, *, pre_pr_gate, git_runner=None,
                  gh_runner=None, record_result_call=None):
    """Commit scoped D6 changes, record the result and push the branch.

    With no open PR, pre_pr_gate(head, branch) decides whether one may open: a
    returned hold is passed back with pr=None. An open PR already follows the
    branch, so it keeps the PR-ref remote test step.
    """
    git_runner = _publish_run_git if git_runner is None else git_runner
    gh_runner = _publish_run_gh if gh_runner is None else gh_runner
    task_id = record.get('task_id')
    root = d6.get('root') if type(d6) is dict else None
    branch = d6.get('branch') if type(d6) is dict else None
    common_dir = d6.get('common_dir') if type(d6) is dict else None
    developer = record.get('developer')
    write_paths = developer.get('write_paths') if type(developer) is dict else None
    if (type(task_id) is not str or type(root) is not str or type(branch) is not str or
            type(common_dir) is not str or type(executor_task) is not str or
            d6.get('task_id') != executor_task or
            type(write_paths) is not list or not write_paths or
            any(type(path) is not str or not path or path.startswith('/') or
                '\\' in path or any(part in ('', '.', '..') for part in path.split('/'))
                for path in write_paths)):
        raise _PublishRefusal('PUBLISH_BINDING_UNAVAILABLE')
    canonical_root = str(Path(common_dir).parent)
    actual_root = _publish_command(git_runner, root, ['rev-parse', '--show-toplevel'])
    actual_branch = _publish_command(git_runner, root, ['branch', '--show-current'])
    if os.path.realpath(actual_root.strip()) != os.path.realpath(root) or actual_branch.strip() != branch:
        raise _PublishRefusal('PUBLISH_D6_MISMATCH')
    if branch in ('main', 'master', 'default'):
        raise _PublishRefusal('PUBLISH_PROTECTED_BRANCH')

    ignored = _publish_command(git_runner, root,
        ['ls-files', '--others', '--ignored', '--exclude-standard', '-z'])
    ignored_paths = [path for path in ignored.split('\0') if path]
    if any((path == '__pycache__' or '/__pycache__/' in '/' + path + '/' or
            path.endswith('.pyc')) and
           not path.startswith('tools/llmtool/.venv/')
           for path in ignored_paths):
        raise _PublishRefusal('PUBLISH_IGNORED_STRAY')

    tracked = _publish_command(git_runner, root,
        ['diff', '--no-renames', '--name-only', '-z', 'HEAD'])
    untracked = _publish_command(git_runner, root,
        ['ls-files', '--others', '--exclude-standard', '-z'])
    changed = sorted({path for raw in (tracked, untracked)
                      for path in raw.split('\0') if path})
    if any(path not in write_paths for path in changed):
        raise _PublishRefusal('PUBLISH_OUT_OF_SCOPE')

    remote_sha = _publish_command(git_runner, root,
        ['ls-remote', '--heads', 'origin', f'refs/heads/{branch}']).strip()
    local_head = _publish_command(git_runner, root, ['rev-parse', '--verify', 'HEAD']).strip()
    remote_head = None
    if remote_sha:
        remote_parts = remote_sha.split()
        if len(remote_parts) != 2 or not re.fullmatch(r'[0-9a-f]{40}', remote_parts[0]):
            raise _PublishRefusal('PUBLISH_REMOTE_UNAVAILABLE')
        remote_head = remote_parts[0]
        try:
            ancestor = git_runner(root, ['merge-base', '--is-ancestor', remote_head, 'HEAD'])
        except Exception:
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
        if getattr(ancestor, 'returncode', 1) != 0:
            raise _PublishRefusal('PUBLISH_NON_FAST_FORWARD')

    if changed:
        _publish_command(git_runner, root, ['add', '--', *changed])
        staged = _publish_command(git_runner, root,
            ['diff', '--cached', '--no-renames', '--name-only', '-z', 'HEAD'])
        staged_paths = [path for path in staged.split('\0') if path]
        if not staged_paths or any(path not in write_paths for path in staged_paths):
            raise _PublishRefusal('PUBLISH_OUT_OF_SCOPE')
        title, _ = _publication_text(record, executor_task, branch)
        _publish_command(git_runner, root,
            ['commit', '-m', title, '-m', f'llmtool Task {task_id}'])
        local_head = _publish_command(git_runner, root,
            ['rev-parse', '--verify', 'HEAD']).strip()
    # Bring the publication head up to date with main before recording it or
    # pushing it, so the published PR and its checks bind the merged result.
    _publish_command(git_runner, root, ['fetch', 'origin', 'main'])
    try:
        main_is_ancestor = git_runner(root, ['merge-base', '--is-ancestor', 'origin/main', 'HEAD'])
    except Exception:
        raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
    ancestry_status = getattr(main_is_ancestor, 'returncode', 1)
    if ancestry_status not in (0, 1):
        raise _PublishRefusal('PUBLISH_COMMAND_FAILED')
    if ancestry_status == 1:
        try:
            merged = git_runner(root, ['merge', '--no-edit', 'origin/main'])
        except Exception:
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
        if getattr(merged, 'returncode', 1) != 0:
            try:
                conflicts = git_runner(root, ['ls-files', '--unmerged', '-z'])
            except Exception:
                conflicts = None
            is_conflict = (getattr(conflicts, 'returncode', 1) == 0 and
                           type(getattr(conflicts, 'stdout', None)) in (bytes, str) and
                           bool(conflicts.stdout))
            try:
                git_runner(root, ['merge', '--abort'])
            except Exception:
                pass
            raise _PublishRefusal('PUBLISH_MERGE_CONFLICT' if is_conflict else
                                  'PUBLISH_COMMAND_FAILED')
        local_head = _publish_command(git_runner, root,
            ['rev-parse', '--verify', 'HEAD']).strip()
    if changed or d6.get('recorded_result_head') != local_head:
        try:
            record_result_call = _record_d6_result if record_result_call is None else record_result_call
            recorded = record_result_call(task_id=executor_task, canonical_root=canonical_root,
                                          root=root)
        except Exception:
            raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED') from None
        if recorded not in (None, local_head):
            raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED')

    if remote_head != local_head:
        _publish_command(git_runner, root,
            ['push', 'origin', f'HEAD:refs/heads/{branch}'],
            code='PUBLISH_PUSH_FAILED')
    if not _open_pr_present(gh_runner, root, branch):
        hold = pre_pr_gate(local_head, branch)
        if hold is not None:
            return dict(hold, pr=None, head=local_head)
        # The EC2 run can take minutes, and gh opens the PR from the branch tip.
        _require_published_head(git_runner, root, branch, local_head)
    title, body = _publication_text(record, executor_task, branch)
    number = _publish_pr(gh_runner, root, branch, task_id, local_head, title, body)
    return {'pr': number, 'head': local_head}


def _publish_refusal_result(store, task_id, refusal):
    """Return a publish refusal; one after this call's create keeps its record."""
    result = {'task_id': task_id, 'codes': [refusal.code]}
    operation = refusal.operation
    if type(operation) is not dict:
        return result
    operation = dict(operation)
    result['publish_operation'] = operation
    number = operation.get('pr')
    if refusal.code == 'PUBLISH_MOVED_PR_OPEN' and type(number) is int:
        result['open_pr'] = {'number': number, 'error': (
            f'PUBLISH_MOVED_PR_OPEN: PR #{number} is still open; this publish did not '
            'close it and a later publish treats it as existing.')}
    try:
        store.record_publish_operation(task_id, operation)
    except Exception:
        result['codes'].append('PUBLISH_OPERATION_RECORD_FAILED')
    return result


def _claims(record):
    value = record.get('review_dispatch_claim')
    if type(value) is dict:
        return [value]
    return value if type(value) is list else []


def _events(related):
    if type(related) is dict:
        value = related.get('events', [])
    else:
        value = related
    return value if type(value) is list else []


def _failure_reason(value):
    """Return a fixed unusable-review reason; any other value names nothing."""
    if type(value) is str and value in review_dispatch.REVIEW_FAILURE_REASONS:
        return value
    return None


def _well_formed_review_refusal(refusal):
    """Return refusal when it has failure_reason str, exactly one of excerpt or
    label str, and optional receipt path str or None; any other value returns None."""
    if type(refusal) is not dict:
        return None
    failure = refusal.get('failure_reason')
    if type(failure) is not str or not failure:
        return None
    excerpt = refusal.get('excerpt')
    label = refusal.get('label')
    has_excerpt = type(excerpt) is str
    has_label = type(label) is str
    if has_excerpt == has_label:
        return None
    if 'excerpt' in refusal and excerpt is not None and not has_excerpt:
        return None
    if 'label' in refusal and label is not None and not has_label:
        return None
    if 'receipt_path' in refusal:
        receipt_path = refusal['receipt_path']
        if receipt_path is not None and type(receipt_path) is not str:
            return None
    if 'receipt' in refusal:
        receipt = refusal['receipt']
        if receipt is not None and type(receipt) is not str:
            return None
    allowed = {'failure_reason', 'excerpt', 'label', 'receipt_path', 'receipt'}
    if any(key not in allowed for key in refusal):
        return None
    return refusal


def _head_review_claim(record, number, head):
    """Return the highest-version claim for one PR and head, if any."""
    matching = [claim for claim in _claims(record) if type(claim) is dict and
                claim.get('pr') == number and claim.get('head_sha') == head]
    return max(matching, key=lambda item: item.get('version', 0), default=None)


def _may_be_review_verdict(body, head, version):
    """Treat anything shaped like a verdict for this head and version as posted."""
    if head not in body or not any(mark in body for mark in (
            'Verdict:', 'type=review_verdict', 'LLMTOOL-REVIEW-BINDING')):
        return False
    versions = re.findall(r'Version: v(\d+)', body)
    return not versions or str(version) in versions


def _review_verdict_absent(claim, github_get):
    """Return True only when a complete comment read shows no verdict for the claim."""
    try:
        comments = github_get(f"issues/{claim.get('pr')}/comments?per_page=100")
    except Exception:
        return False
    # A full page may hide later comments, so it cannot prove absence.
    if type(comments) is not list or len(comments) >= 100:
        return False
    for comment in comments:
        body = comment.get('body') if type(comment) is dict else None
        if type(body) is not str or _may_be_review_verdict(
                body, claim.get('head_sha'), claim.get('version')):
            return False
    return True


def _remote_test_status(record, d6):
    """Return exact-head remote-test state, bounded excerpt, and validated receipt."""
    head = d6.get('result_head') if type(d6) is dict else None
    completion = record.get('completion') if type(record) is dict else None
    executor_task = completion.get('executor_task_id') if type(completion) is dict else None
    if type(head) is not str or type(executor_task) is not str:
        return None, None, None
    receipts = record.get('remote_test_receipts', [])
    if type(receipts) is not list:
        receipts = []
    for receipt in reversed(receipts):
        if (type(receipt) is not dict or
                receipt.get('task_id') != record.get('task_id') or
                receipt.get('run_id') != record.get('run_id') or
                receipt.get('executor_task') != executor_task or
                receipt.get('source_sha') != head or
                receipt.get('result') not in ('PASS', 'FAIL', 'TIMEOUT', 'SHA_MISMATCH') or
                type(receipt.get('modules')) is not list or not receipt['modules']):
            continue
        digest_input = dict(receipt)
        claimed = digest_input.pop('receipt_digest', None)
        digest = hashlib.sha256(_json_bytes(digest_input)).hexdigest()
        if type(claimed) is not str or claimed != digest:
            continue
        if receipt['result'] == 'PASS':
            return 'PASS', None, receipt
        if receipt['result'] in ('TIMEOUT', 'SHA_MISMATCH'):
            return receipt['result'], None, receipt
        if receipt['result'] == 'FAIL':
            excerpt = receipt.get('failure_excerpt')
            try:
                valid_excerpt = (type(excerpt) is str and
                    len(excerpt.encode('utf-8')) <= 4096 and
                    len(excerpt.splitlines()) <= 40)
            except UnicodeError:
                valid_excerpt = False
            if valid_excerpt:
                return 'FAIL', excerpt, receipt
            return 'FAIL', '', receipt
    outcomes = record.get('remote_test_outcomes', [])
    if type(outcomes) is not list:
        outcomes = []
    for outcome in reversed(outcomes):
        if (type(outcome) is not dict or outcome.get('executor_task') != executor_task or
                outcome.get('sha') != head):
            continue
        if outcome.get('result') == 'TIMEOUT':
            return 'TIMEOUT', None, None
        if (outcome.get('result') == 'ADMISSION_DENIED' and
                outcome.get('code') == 'MODULE_NOT_ADMITTED' and
                outcome.get('modules') == []):
            return 'ADMISSION_DENIED', None, None
    return None, None, None


def _remote_test_timeout_count(record, d6):
    """Count exact-head EC2 TIMEOUT results for this Task's executor."""
    head = d6.get('result_head') if type(d6) is dict else None
    completion = record.get('completion') if type(record) is dict else None
    executor_task = completion.get('executor_task_id') if type(completion) is dict else None
    if type(head) is not str or type(executor_task) is not str:
        return 0
    count = 0
    receipts = record.get('remote_test_receipts', [])
    if type(receipts) is list:
        for receipt in receipts:
            if (type(receipt) is not dict or
                    receipt.get('task_id') != record.get('task_id') or
                    receipt.get('run_id') != record.get('run_id') or
                    receipt.get('executor_task') != executor_task or
                    receipt.get('source_sha') != head or
                    receipt.get('result') != 'TIMEOUT' or
                    type(receipt.get('modules')) is not list or not receipt['modules']):
                continue
            digest_input = dict(receipt)
            claimed = digest_input.pop('receipt_digest', None)
            try:
                digest = hashlib.sha256(_json_bytes(digest_input)).hexdigest()
            except Exception:
                continue
            if type(claimed) is not str or claimed != digest:
                continue
            count += 1
    outcomes = record.get('remote_test_outcomes', [])
    if type(outcomes) is list:
        for outcome in outcomes:
            if (type(outcome) is not dict or
                    outcome.get('executor_task') != executor_task or
                    outcome.get('sha') != head or
                    outcome.get('result') != 'TIMEOUT'):
                continue
            count += 1
    return count


def _run_remote_test(store, task_id, executor_task, head, ref, pr):
    """Run one remote test through the CLI adapter and return its emitted result."""
    args = Namespace(task_id=task_id, executor_task=executor_task, sha=head, ref=ref,
                     module=[], pr=pr)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        value = task_submission_cli.run_task_remote_test(args, store=store)
    try:
        return json.loads(captured.getvalue().strip())
    except (TypeError, ValueError):
        return value if type(value) is dict else {}


def _remote_test_hold(step, codes, *, task_id, executor_task, head, record=None,
                      next_step=None):
    result = {'task_id': task_id, 'executor_task': executor_task, 'head': head}
    summary = _remote_test_receipt_summary(record, head)
    if summary is not None:
        result['remote_test'] = summary
    return {'step': step, 'next': next_step or step, 'codes': codes, 'result': result}


def _pre_pr_remote_gate(record, store, executor_task, head, branch):
    """Decide whether a pushed branch head with no PR may open one.

    Return (status, hold). A None hold lets the PR open. A None status with it
    means no test module is declared, so the PR-ref step records that as today.
    """
    if not remote_test._task_path_modules(record):
        return None, None
    task_id = record.get('task_id')
    binding = {'result_head': head}
    identity = {'task_id': task_id, 'executor_task': executor_task, 'head': head}
    remote_result = None
    try:
        fresh = store.get(task_id)
        status = _remote_test_status(fresh, binding)[0]
        if status is None or (status == 'TIMEOUT' and _remote_test_timeout_count(fresh, binding) == 1):
            remote_result = _run_remote_test(store, task_id, executor_task, head,
                                             f'refs/heads/{branch}', None)
            fresh = store.get(task_id)
            status = _remote_test_status(fresh, binding)[0]
    except Exception as exc:
        return None, _remote_test_hold(
            'REMOTE_TEST_REFUSED',
            ['PUBLISH_REMOTE_TEST_REFUSED', _refusal(exc, 'remote_test_code')], **identity)
    if status in ('PASS', 'ADMISSION_DENIED'):
        return status, None
    if status == 'FAIL':
        return status, _remote_test_hold('RUN_REMOTE_TEST', ['REMOTE_TEST_FAIL'],
                                         record=fresh, next_step='REPAIR_REVIEW', **identity)
    if status in ('TIMEOUT', 'SHA_MISMATCH'):
        return status, _remote_test_hold('NEEDS_ATTENTION', ['REMOTE_TEST_' + status],
                                         record=fresh, **identity)
    code = remote_result.get('code') if type(remote_result) is dict else None
    code = code if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code) else None
    detail = [code] if code else []
    if code in _REMOTE_TEST_RETRY_CODES:
        return None, _remote_test_hold('WAITING_REMOTE_TEST',
                                       ['REMOTE_TEST_UNAVAILABLE', *detail], **identity)
    return None, _remote_test_hold('REMOTE_TEST_REFUSED',
                                   ['PUBLISH_REMOTE_TEST_REFUSED', *detail], **identity)


def _record_cleared_pr(store, task_id, number, head, d6, gh_runner, root):
    """Bind a PR opened after EC2 cleared its head and report that result on it.

    The remote test ran before the PR existed, so neither of these happened
    during that run. Both are warnings: the PR is already open.
    """
    codes = []
    try:
        store.record_published_pr(task_id, number, head)
    except Exception:
        codes.append('PUBLISH_PR_RECORD_FAILED')
    try:
        summary = _remote_test_receipt_summary(store.get(task_id), head)
        if summary is not None:
            _post_remote_test_comment(gh_runner, root, number, task_id, d6, head, summary)
    except Exception:
        codes.append('REMOTE_TEST_COMMENT_FAILED')
    return codes


def _remote_fail_action(related, result_head):
    """Return the repair action for an exact-head EC2 FAIL."""
    repairs = related.get('repair_claims', []) if type(related) is dict else []
    current_repair = next((item for item in reversed(repairs)
                           if type(item) is dict and item.get('head_sha') == result_head), None)
    if current_repair is not None and current_repair.get('state') == 'DISPATCHED':
        state = current_repair.get('repair_state')
        if state in ('UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED'):
            return 'REPAIR_RETRY'
        if state in ('RUNNING', 'PENDING'):
            return 'WAITING_REPAIR'
        if state == 'UNKNOWN':
            return 'REPAIR_OUTCOME_UNKNOWN'
        if state == 'RECORD_UNAVAILABLE':
            return 'DEVELOPER_ATTENTION'
        if state == 'RESPONDED':
            return 'PUBLISH'
    return 'REPAIR_REVIEW'


def derive_repair_state(repair_record):
    """Normalize a repair Task record into the state used by the advance loop."""
    if type(repair_record) is not dict:
        return 'UNKNOWN'
    developer = repair_record.get('developer')
    state = developer.get('state') if type(developer) is dict else None
    # This failure updates only the developer field, so check it before the
    # top-level state, which may still read PM_PENDING.
    if state == 'BLOCKED' and developer.get('error_code') == 'CLAUDE_NO_CHANGES':
        return 'BLOCKED'
    if state == 'DEVELOPER_PENDING' or repair_record.get('state') == 'PM_PENDING':
        return 'PENDING'
    if state == 'DEVELOPER_DISPATCHING':
        return 'RUNNING'
    if state == 'DEVELOPER_UNAVAILABLE':
        return 'UNAVAILABLE'
    if state == 'DEVELOPER_OUTPUT_REJECTED':
        return 'OUTPUT_REJECTED'
    if state == 'DEVELOPER_RESPONDED':
        return 'RESPONDED'
    if state == 'DEVELOPER_OUTCOME_UNKNOWN':
        execution = developer.get('execution') if type(developer) is dict else None
        if (developer.get('error_code') == 'CODEX_BLOCKED' or
                (type(execution) is dict and execution.get('status') == 'BLOCKED')):
            return 'BLOCKED'
        return 'UNKNOWN'
    return 'UNKNOWN'


def _pre_review_action(record, d6, pr, related, result_head):
    """Return the gate action before a Reviewer may start on this head."""
    remote_status, _, _ = _remote_test_status(record, d6)
    if remote_status == 'TIMEOUT':
        if _remote_test_timeout_count(record, d6) == 1:
            return 'RUN_REMOTE_TEST'
        return 'NEEDS_ATTENTION'
    if remote_status == 'SHA_MISMATCH':
        return 'NEEDS_ATTENTION'
    if remote_status == 'FAIL':
        return _remote_fail_action(related, result_head)
    if remote_status not in ('PASS', 'ADMISSION_DENIED'):
        return 'RUN_REMOTE_TEST'
    if type(pr) is dict and pr.get('merged') is True:
        # The code is already merged; a Reviewer must never start for it.
        return 'PR_MERGED_NO_REVIEW'
    return 'DISPATCH_REVIEW'


def decide(record, d6, pr, related):
    """Return the next action name using only supplied snapshots."""
    if type(record) is not dict:
        return 'DEVELOPER_ATTENTION'
    developer = record.get('developer')
    if type(developer) is not dict or developer.get('state') in (
            'DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_UNAVAILABLE',
            'DEVELOPER_OUTPUT_REJECTED'):
        return 'DEVELOPER_ATTENTION'
    if developer.get('state') != 'DEVELOPER_RESPONDED':
        return 'DEVELOPER_ATTENTION'
    if type(d6) is not dict or d6.get('dirty') is True or d6.get('state') == 'DIRTY':
        return 'PUBLISH'
    result_head = d6.get('result_head')
    if (type(result_head) is not str or re.fullmatch(r'[0-9a-f]{40}', result_head) is None or
            d6.get('recorded_result_head') != result_head):
        return 'PUBLISH'
    claims = _claims(record)
    chosen_pr = (pr.get('number') if type(pr) is dict else None)
    if chosen_pr is None and claims:
        chosen_pr = claims[-1].get('pr') if type(claims[-1]) is dict else None
    pr_head = pr.get('head') if type(pr) is dict else None
    if (type(chosen_pr) is not int or chosen_pr < 1 or type(pr_head) is not dict or
            pr_head.get('sha') != result_head):
        # EC2 runs on the branch before any PR, so a stopped head stays here
        # with no PR. Every other state re-enters the idempotent publish step.
        remote_status, _, _ = _remote_test_status(record, d6)
        if remote_status == 'TIMEOUT':
            if _remote_test_timeout_count(record, d6) == 1:
                return 'PUBLISH'
            return 'NEEDS_ATTENTION'
        if remote_status == 'SHA_MISMATCH':
            return 'NEEDS_ATTENTION'
        if remote_status == 'FAIL':
            return _remote_fail_action(related, result_head)
        return 'PUBLISH'

    matching = [claim for claim in claims if type(claim) is dict and
                claim.get('pr') == chosen_pr and claim.get('head_sha') == result_head]
    if not matching:
        return _pre_review_action(record, d6, pr, related, result_head)
    claim = max(matching, key=lambda item: item.get('version', 0))
    if claim.get('state') == 'UNKNOWN' and _failure_reason(claim.get('failure_reason')):
        # One head gets one redispatch: any earlier claim on it has spent that.
        if sum(type(item) is dict and item.get('head_sha') == result_head
               for item in claims) > 1:
            return 'REVIEW_UNUSABLE_STOPPED'
        action = _pre_review_action(record, d6, pr, related, result_head)
        return 'REVIEW_REDISPATCH' if action == 'DISPATCH_REVIEW' else action
    if claim.get('state') in ('CLAIMED', 'UNKNOWN'):
        return 'REVIEW_OUTCOME_UNKNOWN'
    events = [event for event in _events(related) if type(event) is dict and
              event.get('source') == 'REVIEWER' and event.get('source_sha') == result_head and
              event.get('version', claim.get('version')) == claim.get('version')]
    if claim.get('state') in ('DISPATCHED', 'COMPLETED') and not events:
        return 'RECORD_REVIEW_RESULT'
    if events:
        event = events[-1]
        outcome = event.get('outcome') or event.get('state')
        if outcome == 'REVIEW_BLOCKED':
            return 'REVIEWER_ATTENTION'
        if outcome == 'REVIEW_FAIL':
            repairs = (related.get('repair_claims', []) if type(related) is dict else [])
            current_repair = next((item for item in reversed(repairs)
                                   if type(item) is dict and item.get('head_sha') == result_head), None)
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') in (
                        'UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED')):
                return 'REPAIR_RETRY'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') in ('RUNNING', 'PENDING')):
                return 'WAITING_REPAIR'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'UNKNOWN'):
                return 'REPAIR_OUTCOME_UNKNOWN'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'RECORD_UNAVAILABLE'):
                return 'DEVELOPER_ATTENTION'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'RESPONDED'):
                return 'PUBLISH'
            # Only a COMPLETED claim's outcome carries a count. Without a code
            # finding a Developer has nothing to repair, so the PM decides.
            findings = event.get('findings_count')
            if claim.get('state') == 'COMPLETED' and type(findings) is int and findings == 0:
                return 'REVIEW_NO_CODE_FINDING'
            return 'REPAIR_REVIEW'
        if outcome == 'REVIEW_PASS':
            completion = record.get('completion')
            evidence = completion.get('evidence') if type(completion) is dict else None
            review_ci = evidence.get('review_pr_ci') if type(evidence) is dict else None
            result = evidence.get('result') if type(evidence) is dict else None
            if (type(completion) is dict and completion.get('state') == 'PM_EVIDENCE_READY' and
                    type(review_ci) is dict and type(result) is dict and
                    result.get('result_head') == result_head):
                return 'READY_FOR_INTEGRATION'
            return 'RUN_TASK_COMPLETE'
    return 'REVIEW_OUTCOME_UNKNOWN'


def _refusal(exc, attribute):
    code = getattr(exc, attribute, None)
    if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code):
        return code
    return 'TASK_ADVANCE_REFUSED'


def _pr_number(record, requested):
    if requested is not None:
        return requested
    claims = _claims(record)
    return claims[-1].get('pr') if claims and type(claims[-1]) is dict else None


def _d6_snapshot(record, executor_task):
    completion = record.get('completion') or {}
    evidence = completion.get('evidence') or {}
    common_dir = evidence.get('common_dir')
    if type(common_dir) is not str:
        try:
            cmd = ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir']
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, check=False, timeout=10)
            if proc.returncode == 0 and proc.stdout.strip():
                common_dir = proc.stdout.strip()
        except Exception:
            pass
    if type(common_dir) is not str:
        return {'state': 'D6_BINDING_UNAVAILABLE', 'code': 'D6_BINDING_UNAVAILABLE'}
    try:
        registration = task_completion.read_d6_registration(
            executor_task, common_dir,
            allowed_states=task_completion._D6_EVIDENCE_STATES)
        snapshot = task_completion.collect_git_snapshot(registration.root)
    except Exception as exc:
        return {'state': 'D6_BINDING_UNAVAILABLE',
                'code': _refusal(exc, 'completion_code')}
    clean = bool(getattr(snapshot, 'clean', False))
    recorded = registration.result_sha
    return {'root': registration.root, 'common_dir': registration.common_dir,
            'task_id': registration.task_id,
            'branch': registration.branch, 'result_head': snapshot.head,
            'recorded_result_head': recorded,
            'registered_result_head': registration.result_sha, 'dirty': not clean,
            'state': 'CLEAN' if clean else 'DIRTY',
            'registration_state': getattr(registration, 'state', None),
            'trusted_agent_id': getattr(registration, 'trusted_agent_id', getattr(registration, 'agent_id', None))}


def _pr_snapshot(number, github_get):
    if type(number) is not int or not 1 <= number <= 9999999:
        return None
    value = github_get(f'pulls/{number}')
    return value if type(value) is dict else None


def _ack_consumed_events(result, record, store, result_head):
    """Best-effort ACK of completion events consumed by this advance decision."""
    acked = []
    skipped = False
    closed = result.get('step') == 'CLOSED'
    try:
        latest_record = store.get(record.get('task_id'))
        if type(latest_record) is not dict:
            latest_record = record
    except Exception:
        latest_record = record
    events = latest_record.get('completion_events', [])
    if type(events) is list:
        for event in events:
            if type(event) is not dict or event.get('state') == 'ACKED':
                continue
            source_sha = event.get('source_sha')
            eligible = closed or (
                type(source_sha) is str and
                re.fullmatch(r'[0-9a-f]{40}', source_sha) is not None and
                type(result_head) is str and source_sha != result_head)
            if not eligible:
                continue
            session_id = latest_record.get('pm_session_id')
            if type(session_id) is not str:
                skipped = True
                continue
            try:
                active_pm_inbox.ack(store, event.get('event_id'), session_id)
                acked.append(event['event_id'])
            except Exception:
                skipped = True
    result['acked'] = acked
    if skipped and 'ACK_SKIPPED' not in result.get('codes', []):
        result.setdefault('codes', []).append('ACK_SKIPPED')
    return result


def _review_outcomes(record, claims, github_get):
    """Read verdicts bound to claims; completion events intentionally hold hashes only."""
    from . import review_result as result_module
    outcomes = []
    task = record
    for claim in claims:
        if type(claim) is not dict:
            continue
        number, version, head = claim.get('pr'), claim.get('version'), claim.get('head_sha')
        events = [event for event in record.get('completion_events', [])
                  if type(event) is dict and event.get('source') == 'REVIEWER' and
                  event.get('source_sha') == head]
        if claim.get('state') == 'COMPLETED':
            try:
                latest, event_id = result_module._completed_claim_result(
                    task, claim, version=version)
            except Exception:
                continue
            if any(event.get('event_id') == event_id for event in events):
                outcomes.append({'source': 'REVIEWER', 'source_sha': head,
                                 'version': version, 'outcome': latest[2],
                                 'findings_count': latest[3]})
            continue
        if claim.get('state') != 'DISPATCHED':
            continue
        if not events:
            continue
        comments = github_get(f'issues/{number}/comments?per_page=100')
        if type(comments) is not list or len(comments) >= 100:
            continue
        latest = None
        for comment in comments:
            try:
                value = result_module._comment_verdict(comment, head=head,
                    version=version, task=task)
            except Exception:
                value = None
            if value is not None and (latest is None or value[:2] > latest[:2]):
                latest = value
        if latest is not None:
            import hashlib
            import json
            identity = {'task': record['task_id'], 'run': record['run_id'],
                        'head': head, 'version': version,
                        'outcome': latest[2], 'comment_id': latest[1]}
            event_id = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]
            if any(event.get('event_id') == event_id for event in events):
                outcomes.append({'source': 'REVIEWER', 'source_sha': head,
                                 'version': version, 'outcome': latest[2]})
        else:
            import hashlib
            import json
            identity = {'task': record['task_id'], 'run': record['run_id'],
                        'head': head, 'version': version,
                        'outcome': 'REVIEW_BLOCKED', 'comment_id': None}
            event_id = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]
            if any(event.get('event_id') == event_id for event in events):
                outcomes.append({'source': 'REVIEWER', 'source_sha': head,
                                 'version': version, 'outcome': 'REVIEW_BLOCKED'})
    return outcomes


def _integration_delta_verified(root, reviewed_head, integration_head, merge_sha, number,
                                git_runner=None):
    """Return whether a merged integration head adds only clean merges of main.

    The trusted anchor is the first parent of the integration PR's merge
    commit: main as it stood before that PR merged. Main after the merge is
    never used, because it may contain the PR's own commits. A missing commit
    allows one fetch of main and the PR head; any failure is not verified.
    """
    if (type(reviewed_head) is str and integration_head == reviewed_head and
            _HEX40.fullmatch(reviewed_head)):
        return True
    run = _publish_run_git if git_runner is None else git_runner
    root = root or os.getcwd()
    try:
        if (any(type(sha) is not str or _HEX40.fullmatch(sha) is None
                for sha in (reviewed_head, integration_head, merge_sha)) or
                type(number) is not int or not 1 <= number <= 9999999):
            return False

        def present(sha):
            try:
                result = run(root, ['cat-file', '-e', f'{sha}^{{commit}}'])
            except Exception:
                return False
            return getattr(result, 'returncode', 1) == 0

        def anchor():
            if not all(present(sha) for sha in (reviewed_head, integration_head, merge_sha)):
                return None
            try:
                trusted = _publish_command(
                    run, root, ['rev-parse', '--verify', f'{merge_sha}^1']).strip()
            except _PublishRefusal:
                return None
            return trusted if _HEX40.fullmatch(trusted) and present(trusted) else None

        trusted = anchor()
        if trusted is None:
            _publish_command(run, root, ['fetch', 'origin', 'main',
                                         f'refs/pull/{number}/head'])
            trusted = anchor()
            if trusted is None:
                return False
        # A graft file could rewrite the parents that the walk trusts.
        task_completion._reject_legacy_grafts(root)
        return task_completion._developer_tip(
            root, reviewed_head, integration_head, trusted) == reviewed_head
    except Exception:
        return False


def _train_member_merged(train, task_id, number, head, integration, integration_pr,
                         root=None, git_runner=None):
    """Return whether a merged merge train proves this Task's reviewed head merged.

    Needs the record's member entry for this Task, PR and reviewed head, the
    integration PR merged at the record's train head and merge commit, a
    one-parent merge commit on the record's main with the train head's tree,
    and the train delta proof. A missing commit allows one fetch of main and
    the integration PR head; any doubt is not merged.
    """
    from . import merge_train
    try:
        if type(train) is not dict or train.get('state') not in ('MERGED', 'CLOSED'):
            return False
        if type(integration_pr) is not int or train.get('train_pr') != integration_pr:
            return False
        members = train.get('members')
        if type(members) is not list or any(type(item) is not dict for item in members):
            return False
        if type(task_id) is not str or type(number) is not int or not any(
                item.get('task_id') == task_id and item.get('pr') == number and
                item.get('head') == head for item in members):
            return False
        main_sha, train_head = train.get('main_sha'), train.get('train_head')
        merge_sha = train.get('merge_sha')
        heads = [item.get('head') for item in members]
        if any(type(sha) is not str or _HEX40.fullmatch(sha) is None
               for sha in (main_sha, train_head, merge_sha, head, *heads)):
            return False
        integration_head = integration.get('head') if type(integration) is dict else None
        if (type(integration) is not dict or integration.get('merged') is not True or
                integration.get('number', integration_pr) != integration_pr or
                type(integration_head) is not dict or
                integration_head.get('sha') != train_head or
                integration.get('merge_commit_sha') != merge_sha):
            return False
        run = _publish_run_git if git_runner is None else git_runner
        root = root or os.getcwd()

        def present():
            for sha in (merge_sha, main_sha, train_head, *heads):
                try:
                    result = run(root, ['cat-file', '-e', f'{sha}^{{commit}}'])
                except Exception:
                    return False
                if getattr(result, 'returncode', 1) != 0:
                    return False
            return True

        if not present():
            _publish_command(run, root, ['fetch', 'origin', 'main',
                                         f'refs/pull/{integration_pr}/head'])
            if not present():
                return False
        # A graft file could rewrite the parents read below.
        task_completion._reject_legacy_grafts(root)
        merge_tree, merge_parents = merge_train._commit_header(root, merge_sha)
        train_tree, _train_parents = merge_train._commit_header(root, train_head)
        if merge_parents != [main_sha] or merge_tree != train_tree:
            return False
        return merge_train.train_delta_verified(root, main_sha, heads, train_head) is True
    except Exception:
        return False


def _merged_review_target(record, claims, events, pr, integration_pr, github_get, *,
                          root=None, git_runner=None):
    """Return a verified merged PR tuple for a completed review, if present.

    When integration_pr is a recorded merge train, only the train proof
    decides; a store refusal closes nothing.
    """
    train = None
    if integration_pr is not None:
        from . import merge_train_store
        try:
            train = merge_train_store.find_by_pr(integration_pr)
        except Exception:
            return None
    completion = record.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    result = evidence.get('result') if type(evidence) is dict else None
    review_ci = evidence.get('review_pr_ci') if type(evidence) is dict else None
    if (type(completion) is not dict or completion.get('state') != 'PM_EVIDENCE_READY' or
            type(result) is not dict or type(review_ci) is not dict):
        return None
    for claim in reversed(claims):
        if type(claim) is not dict or claim.get('state') not in ('DISPATCHED', 'COMPLETED'):
            continue
        head = claim.get('head_sha')
        number = claim.get('pr')
        version = claim.get('version')
        if (type(head) is not str or re.fullmatch(r'[0-9a-f]{40}', head) is None or
                result.get('result_head') != head or
                type(number) is not int or type(version) is not int):
            continue
        if not any(type(event) is dict and event.get('source') == 'REVIEWER' and
                   event.get('source_sha') == head and event.get('version') == version and
                   (event.get('outcome') or event.get('state')) == 'REVIEW_PASS'
                   for event in events):
            continue

        if train is not None:
            try:
                integration = _pr_snapshot(integration_pr, github_get)
            except Exception:
                continue
            if _train_member_merged(train, record.get('task_id'), number, head, integration,
                                    integration_pr, root, git_runner):
                return integration_pr, train['merge_sha']
            continue

        if type(pr) is dict and pr.get('number', number) == number and pr.get('merged') is True:
            pr_head = pr.get('head')
            merge_sha = pr.get('merge_commit_sha')
            if (type(pr_head) is dict and pr_head.get('sha') == head and
                    type(merge_sha) is str and re.fullmatch(r'[0-9a-f]{40}', merge_sha)):
                return number, merge_sha

        if integration_pr is None:
            continue
        try:
            integration = _pr_snapshot(integration_pr, github_get)
            integration_head = integration.get('head') if type(integration) is dict else None
            merge_sha = integration.get('merge_commit_sha') if type(integration) is dict else None
            if (type(integration) is not dict or integration.get('merged') is not True or
                    type(integration_head) is not dict or
                    type(integration_head.get('sha')) is not str or
                    type(merge_sha) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', merge_sha) is None):
                continue
            integration_sha = integration_head.get('sha')
            if type(integration_sha) is not str or re.fullmatch(r'[0-9a-f]{40}', integration_sha) is None:
                continue
            compare = github_get(f'compare/{head}...{integration_sha}')
            if (type(compare) is dict and compare.get('status') in ('ahead', 'identical') and
                    _integration_delta_verified(root, head, integration_sha, merge_sha,
                                                integration_pr, git_runner)):
                return integration_pr, merge_sha
        except Exception:
            continue
    return None


def _closed_train_member_head(record, task_id, store):
    """Return matching member head if record has a verified train-closed merge event."""
    if type(record) is not dict or type(task_id) is not str:
        return None
    events = record.get('completion_events')
    if type(events) is not list or not events:
        return None
    task_head = None
    completion = record.get('completion')
    if type(completion) is dict:
        evidence = completion.get('evidence')
        if type(evidence) is dict:
            result = evidence.get('result')
            if type(result) is dict:
                candidate = result.get('result_head')
                if type(candidate) is str and _HEX40.fullmatch(candidate):
                    task_head = candidate
    if task_head is None:
        executor_task = (completion or {}).get('executor_task_id')
        if type(executor_task) is str:
            try:
                d6 = _d6_snapshot(record, executor_task)
                if type(d6) is dict:
                    candidate = d6.get('result_head')
                    if type(candidate) is str and _HEX40.fullmatch(candidate):
                        task_head = candidate
            except Exception:
                pass
    if task_head is None:
        return None
    from . import merge_train_store
    for event in reversed(events):
        if type(event) is not dict:
            continue
        if event.get('source') != 'GITHUB' or event.get('session_id') != 'github-task-advance':
            continue
        if event.get('marker_type') != 'merge' or event.get('marker_status') != 'MERGED':
            continue
        pr = event.get('issue_number')
        if type(pr) is not int or not 1 <= pr <= 9999999:
            continue
        merge_sha = event.get('merge_sha') or event.get('source_sha')
        if type(merge_sha) is not str or not _HEX40.fullmatch(merge_sha):
            continue
        try:
            train = merge_train_store.find_by_pr(
                pr, root=getattr(store, 'root', getattr(store, '_root', None)))
        except Exception:
            # A store read error never yields CLOSED.
            continue
        if type(train) is not dict or train.get('state') not in ('MERGED', 'CLOSED'):
            continue
        if train.get('train_pr') != pr:
            continue
        if train.get('merge_sha') != merge_sha:
            continue
        members = train.get('members')
        if type(members) is not list:
            continue
        if any(type(m) is dict and m.get('task_id') == task_id and m.get('head') == task_head
               for m in members):
            return task_head
    return None


def advance(*, task_id, store=None, pr_number=None, integration_pr=None, merge=False,
            reviewer_session_done=False, dispatch_review_call=None,
            record_review_result_call=None, repair_review_call=None,
            run_task_complete_call=None, record_github_event_call=None,
            github_get=None, cwd=None, publish_git_runner=None,
            publish_gh_runner=None, record_result_call=None, merge_gh_runner=None,
            owner_acceptance=None, owner_exception=None, executor_task=None, sleep_fn=None, sleep=None):
    """Perform one selected action and return a bounded JSON-ready summary."""
    if owner_acceptance is not None and not merge:
        # An Owner acceptance is only for a merge; refuse before anything runs.
        return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                'next': 'REFUSED', 'codes': ['OWNER_ACCEPTANCE_REQUIRES_MERGE'],
                'acked': []}
    if executor_task is not None and (not merge or owner_exception is None):
        return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                'next': 'REFUSED', 'codes': ['OWNER_EXCEPTION_EXECUTOR_REQUIRES_EXCEPTION'],
                'acked': []}
    if owner_exception is not None and not merge:
        return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                'next': 'REFUSED', 'codes': ['OWNER_EXCEPTION_REQUIRES_MERGE'],
                'acked': []}
    store = TaskStore() if store is None else store
    try:
        record = store.get(task_id)
    except Exception:
        return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                'next': 'REFUSED', 'codes': ['TASK_RECORD_UNAVAILABLE'], 'acked': []}
    if integration_pr is None:
        closed_head = _closed_train_member_head(record, task_id, store)
        if closed_head is not None:
            return _ack_consumed_events(
                {'task_id': task_id, 'step': 'CLOSED', 'result': None,
                 'next': 'CLOSED', 'codes': []}, record, store, closed_head)
    completion_executor = (record.get('completion') or {}).get('executor_task_id')
    if executor_task is not None and type(completion_executor) is str and executor_task != completion_executor:
        return _ack_consumed_events(_merge_refusal('OWNER_EXCEPTION_EXECUTOR_MISMATCH'),
                                    record, store, None)
    active_executor = completion_executor or executor_task
    using_executor_task = bool(owner_exception is not None and type(executor_task) is str)
    executor_task = active_executor
    number = _pr_number(record, pr_number)
    get = task_completion._github_get if github_get is None else github_get
    d6 = _d6_snapshot(record, executor_task) if type(executor_task) is str else None
    if (number is None and type(d6) is dict and d6.get('dirty') is False and
            type(d6.get('root')) is str and d6.get('root') and
            type(d6.get('branch')) is str and d6.get('branch') and
            type(d6.get('result_head')) is str and
            d6.get('recorded_result_head') == d6.get('result_head')):
        number = _find_published_pr(
            _publish_run_gh if publish_gh_runner is None else publish_gh_runner,
            d6['root'], d6['branch'], d6['result_head'])
    try:
        pr = _pr_snapshot(number, get) if number is not None else None
    except Exception:
        pr = None
    related = {'events': record.get('completion_events', []),
               'repair_claims': record.get('review_repair_claims', [])}
    repair_claims = []
    for claim in related['repair_claims']:
        if type(claim) is not dict:
            continue
        repair_record = None
        try:
            repair_record = store.get(claim['repair_task_id'])
            repair_state = (derive_repair_state(repair_record)
                            if type(repair_record) is dict else 'RECORD_UNAVAILABLE')
        except Exception:
            repair_state = 'RECORD_UNAVAILABLE'
        repair_claims.append(dict(claim, repair_state=repair_state))
    related['repair_claims'] = repair_claims
    try:
        review_outcomes = _review_outcomes(record, _claims(record), get)
        related['events'] = list(related['events']) + review_outcomes
    except Exception:
        # Advance may proceed from persisted reviewer events when GitHub is unavailable.
        pass
    delta_root = (d6.get('root') if type(d6) is dict else None) or cwd or os.getcwd()
    merged = _merged_review_target(record, _claims(record), related['events'], pr,
                                    integration_pr, get, root=delta_root,
                                    git_runner=publish_git_runner)
    if merged is not None:
        merge_number, merge_sha = merged
        import hashlib
        event_id = hashlib.sha256(
            f'GITHUB\0merge\0{merge_number}\0{merge_sha}'.encode()).hexdigest()[:32]
        invoke = record_github_event_call or github_events._record
        try:
            invoke(store, record, 'github-task-advance', event_id,
                   merge_number, None, 'merge', 'MERGED', merge_sha)
            return _ack_consumed_events(
                {'task_id': task_id, 'step': 'CLOSED', 'result': None,
                 'next': 'CLOSED', 'codes': []}, record, store,
                d6.get('result_head') if type(d6) is dict else None)
        except Exception as exc:
            return _ack_consumed_events(
                {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                 'next': 'REFUSED', 'codes': [_refusal(exc, 'completion_code') if
                     getattr(exc, 'completion_code', None) else
                     _refusal(exc, 'task_store_error_code')]}, record, store,
                d6.get('result_head') if type(d6) is dict else None)
    if merge:
        if using_executor_task:
            if (type(d6) is not dict or d6.get('state') == 'D6_BINDING_UNAVAILABLE' or
                    d6.get('trusted_agent_id') != 'pm-deterministic' or
                    not (d6.get('registration_state') == 'ACTIVE' or d6.get('state') == 'ACTIVE') or
                    d6.get('dirty') is True or d6.get('state') == 'DIRTY' or
                    type(d6.get('result_head')) is not str or
                    d6.get('recorded_result_head') != d6.get('result_head')):
                return _ack_consumed_events(
                    _merge_refusal('OWNER_EXCEPTION_EXECUTOR_INVALID'),
                    record, store,
                    d6.get('result_head') if type(d6) is dict and type(d6.get('result_head')) is str else None)
        if type(d6) is not dict or type(d6.get('result_head')) is not str:
            return _ack_consumed_events(_merge_refusal('MERGE_D6_UNAVAILABLE'),
                                        record, store, None)
        if d6.get('dirty') is True or d6.get('state') == 'DIRTY':
            return _ack_consumed_events(_merge_refusal('MERGE_D6_NOT_CLEAN'),
                                        record, store, d6['result_head'])
        if d6.get('recorded_result_head') != d6['result_head']:
            return _ack_consumed_events(_merge_refusal('MERGE_STALE_HEAD'),
                                        record, store, d6['result_head'])
        merge_result = _merge_once(store=store, record=record, number=number,
            head=d6['result_head'], pr=pr, events=related['events'], github_get=get,
            gh_runner=_publish_run_gh if merge_gh_runner is None else merge_gh_runner,
            root=d6.get('root') or cwd or os.getcwd(), owner_acceptance=owner_acceptance,
            owner_exception=owner_exception,
            sleep_fn=sleep_fn, sleep=sleep)
        merge_result.update(task_id=task_id, next=merge_result['step'])
        return _ack_consumed_events(merge_result, record, store, d6['result_head'])
    if type(d6) is dict and d6.get('state') == 'D6_BINDING_UNAVAILABLE':
        code = d6.get('code') if type(d6.get('code')) is str else 'D6_BINDING_UNAVAILABLE'
        return _ack_consumed_events(
            {'task_id': task_id, 'step': 'D6_BINDING_UNAVAILABLE', 'result': None,
             'next': 'D6_BINDING_UNAVAILABLE', 'codes': [code]}, record, store, None)
    step = decide(record, d6, pr, related)
    result, codes, refusal_dict = None, [], None
    redispatch_reason = None
    if step == 'REVIEW_REDISPATCH':
        unusable = _head_review_claim(record, number, d6['result_head'])
        if unusable is not None and _review_verdict_absent(unusable, get):
            step = 'DISPATCH_REVIEW'
            redispatch_reason = _failure_reason(unusable.get('failure_reason'))
        else:
            step = 'REVIEW_OUTCOME_UNKNOWN'
    if step == 'RUN_REMOTE_TEST':
        try:
            remote_result = _run_remote_test(store, task_id, executor_task, d6['result_head'],
                                             f'refs/pull/{number}/head', number)
            code = remote_result.get('code') if type(remote_result) is dict else None
            if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code):
                codes.append(code)
            result = {'task_id': task_id, 'executor_task': executor_task,
                      'head': d6['result_head']}
            try:
                fresh_record = store.get(task_id)
            except Exception:
                fresh_record = None
            summary = _remote_test_receipt_summary(fresh_record, d6['result_head'])
            if summary is not None:
                result['remote_test'] = summary
                if number is not None:
                    try:
                        _post_remote_test_comment(
                            _publish_run_gh if publish_gh_runner is None else publish_gh_runner,
                            d6.get('root') or cwd or os.getcwd(), number, task_id,
                            d6, d6['result_head'], summary)
                    except Exception:
                        codes.append('REMOTE_TEST_COMMENT_FAILED')
        except Exception as exc:
            codes.append(_refusal(exc, 'remote_test_code'))
    elif step == 'DISPATCH_REVIEW':
        gate_step, ci_code = _review_ci_gate(number, d6['result_head'], get)
        if gate_step is not None:
            step = gate_step
            if ci_code is not None:
                codes.append(ci_code)
    if step == 'DISPATCH_REVIEW':
        claims = _claims(record)
        versions = [c.get('version', 0) for c in claims if type(c) is dict and
                    type(c.get('version')) is int]
        version = max(versions, default=0) + 1
        invoke = dispatch_review_call or review_dispatch.dispatch_review
        if redispatch_reason is not None:
            codes.extend(('REVIEW_REDISPATCHED', redispatch_reason))
        try:
            result = invoke(store=store, task_id=task_id, executor_task=executor_task,
                            pr_number=number, version=version)
        except Exception as exc:
            codes.append(_refusal(exc, 'review_dispatch_code'))
            failure = _failure_reason(getattr(exc, 'review_failure_reason', None))
            if failure is not None:
                codes.append(failure)
            refusal_dict = _well_formed_review_refusal(getattr(exc, 'review_refusal', None))
        if type(result) is dict:
            review_text = result.get('review_text')
            if (result.get('state') == 'COMPLETED' and
                    type(review_text) is str and review_text):
                try:
                    _post_review_verdict_comment(
                        _publish_run_gh if publish_gh_runner is None else publish_gh_runner,
                        d6.get('root') or cwd or os.getcwd(), number, review_text)
                except Exception:
                    codes.append('REVIEW_VERDICT_COMMENT_FAILED')
            if 'review_text' in result:
                result = dict(result)
                result.pop('review_text', None)
    elif step == 'RECORD_REVIEW_RESULT':
        claim = next(c for c in reversed(_claims(record)) if type(c) is dict and
                     c.get('pr') == number and c.get('head_sha') == d6['result_head'])
        invoke = record_review_result_call or review_result.record_review_result
        try:
            result = invoke(store=store, task_id=task_id, executor_task=executor_task,
                            pr_number=number, version=claim['version'],
                            # First check for a posted comment without creating a
                            # REVIEW_BLOCKED event for a missing verdict.
                            reviewer_session_done=False,
                            github_get=get)
            if type(result) is dict and result.get('state') == 'REVIEW_BLOCKED':
                step = 'REVIEWER_ATTENTION'
                codes.append('REVIEWER_ATTENTION')
            elif (type(result) is dict and result.get('state') == 'NOT_READY' and
                  reviewer_session_done):
                step = 'REVIEW_NOT_POSTED'
                codes.append('REVIEW_VERDICT_NOT_POSTED')
                result = {'reviewer_task': claim.get('reviewer_task'),
                          'reviewer_session_id': claim.get('native_session_id'),
                          'pr': claim.get('pr'), 'head_sha': claim.get('head_sha'),
                          'version': claim.get('version')}
        except Exception as exc:
            codes.append(_refusal(exc, 'review_dispatch_code'))
    elif step in ('REPAIR_REVIEW', 'REPAIR_RETRY'):
        # The repair dispatcher binds the provider cwd to the registered D6 root.
        # It also revalidates the registry and head before creating a repair claim.
        if type(d6) is not dict or type(d6.get('root')) is not str:
            step, codes = 'REFUSED', ['D6_BINDING_UNAVAILABLE']
        else:
            profile = (record.get('developer') or {}).get('provider_profile')
            if profile in ('codex', 'claude') and shutil.which(profile) is None:
                step, codes = 'RUNTIME_NOT_ON_PATH', ['RUNTIME_NOT_ON_PATH']
            else:
                if step == 'REPAIR_RETRY':
                    repair_claim = next((item for item in reversed(record.get('review_repair_claims', []))
                                         if type(item) is dict and item.get('head_sha') == d6['result_head'] and
                                         item.get('state') == 'DISPATCHED'), None)
                    if repair_claim is None:
                        step, codes = 'REFUSED', ['REPAIR_CLAIM_UNAVAILABLE']
                if step in ('REPAIR_REVIEW', 'REPAIR_RETRY'):
                    invoke = repair_review_call or review_repair.repair_review
                    try:
                        captured = io.StringIO()
                        repair_args = dict(store=store, task_id=task_id,
                            executor_task=executor_task, pr_number=number,
                            auto_review=False, github_get=get,
                            retry_claim_id=(repair_claim.get('repair_task_id')
                                            if step == 'REPAIR_RETRY' and
                                            type(repair_claim) is dict else None))
                        remote_status, _, failure_receipt = _remote_test_status(record, d6)
                        if remote_status == 'FAIL':
                            repair_args['remote_test_receipt'] = failure_receipt
                        with contextlib.redirect_stdout(captured):
                            result_value = invoke(**repair_args)
                        emitted = captured.getvalue().strip()
                        try:
                            result = json.loads(emitted) if emitted else result_value
                        except (TypeError, ValueError):
                            result = result_value
                    except Exception as exc:
                        codes.append(_refusal(exc, 'review_repair_code'))
    elif step == 'RUN_TASK_COMPLETE':
        claim = next(c for c in reversed(_claims(record)) if type(c) is dict and
                     c.get('pr') == number and c.get('head_sha') == d6['result_head'])
        invoke = run_task_complete_call or task_submission_cli.run_task_complete
        try:
            args = Namespace(task_id=task_id, executor_task=executor_task,
                             reviewer_task=claim.get('reviewer_task'), pr=number)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                return_code = invoke(args, store=store)
            emitted = captured.getvalue().strip()
            try:
                result = json.loads(emitted) if emitted else {'code': return_code}
            except (TypeError, ValueError):
                result = {'code': return_code}
            fresh = store.get(task_id)
            completion = fresh.get('completion') or {}
            missing = completion.get('missing_gates', [])
            if 'CI' in ' '.join(missing) or any(str(item).startswith(('CI_', 'PR_', 'REQUIRED_CI')) for item in missing):
                codes.extend(item for item in missing if str(item).startswith(('CI_', 'PR_', 'REQUIRED_CI')))
                step = 'CI_FAILED' if 'REQUIRED_CI_FAILED' in missing else 'WAITING_CI'
            elif missing:
                codes.extend(missing)
                step = 'WAITING_EXECUTOR'
            if 'CI_WORKFLOW_JOBS_INVALID' in missing:
                try:
                    label_result = label_mac_ci_for_skipped_check(
                        task_id=task_id, store=store, pr_number=number,
                        github_get=get, merge_gh_runner=merge_gh_runner, cwd=cwd)
                    if type(label_result) is dict:
                        if label_result.get('step') == 'WAITING_CI':
                            step = 'WAITING_CI'
                            codes.append('MAC_CI_LABEL_READY')
                        elif label_result.get('step') == 'MERGE_REFUSED':
                            step = 'WAITING_CI'
                            label_codes = label_result.get('codes', [])
                            if type(label_codes) is list:
                                codes.extend(label_codes)
                except Exception:
                    step = 'WAITING_CI'
                    codes.append('MAC_CI_LABEL_FAILED')
        except Exception as exc:
            codes.append(_refusal(exc, 'completion_code'))
    elif step == 'READY_FOR_INTEGRATION':
        try:
            integration = _pr_snapshot(integration_pr, get) if integration_pr is not None else pr
            merged = type(integration) is dict and integration.get('merged') is True
            merge_sha = integration.get('merge_commit_sha') if merged else None
            delta_unverified = False
            if merged:
                integration_head = integration.get('head')
                integration_sha = integration_head.get('sha') if type(integration_head) is dict else None
                if integration_pr is None:
                    merged = integration_sha == d6['result_head']
                else:
                    compare = (get(f'compare/{d6["result_head"]}...{integration_sha}')
                               if type(integration_sha) is str and
                               re.fullmatch(r'[0-9a-f]{40}', integration_sha) else None)
                    merged = type(compare) is dict and compare.get('status') in ('ahead', 'identical')
                    if merged and not _integration_delta_verified(
                            delta_root, d6['result_head'], integration_sha, merge_sha,
                            integration_pr, publish_git_runner):
                        merged, delta_unverified = False, True
            if delta_unverified:
                codes.append('INTEGRATION_DELTA_UNVERIFIED')
            elif merged and type(merge_sha) is str and re.fullmatch(r'[0-9a-f]{40}', merge_sha):
                import hashlib
                event_id = hashlib.sha256(
                    f'GITHUB\0merge\0{integration_pr or number}\0{merge_sha}'.encode()).hexdigest()[:32]
                invoke = record_github_event_call or github_events._record
                invoke(store, record, 'github-task-advance', event_id,
                       integration_pr or number, None, 'merge', 'MERGED', merge_sha)
                step = 'CLOSED'
            else:
                codes.append('WAITING_INTEGRATION')
        except Exception as exc:
            step, codes = 'REFUSED', [_refusal(exc, 'completion_code') if
                getattr(exc, 'completion_code', None) else
                _refusal(exc, 'task_store_error_code')]
    elif step == 'REVIEWER_ATTENTION':
        # A prior result check may already have persisted the synthetic
        # REVIEW_BLOCKED event for a completed session with no posted verdict.
        # Keep that recoverable state actionable on later advance calls.
        claim = next((c for c in reversed(_claims(record)) if type(c) is dict and
                      c.get('pr') == number and c.get('head_sha') == d6['result_head']), None)
        missing_verdict = (reviewer_session_done and claim is not None and any(
            type(event) is dict and event.get('source') == 'REVIEWER' and
            event.get('source_sha') == claim.get('head_sha') and
            event.get('version') == claim.get('version') and
            (event.get('outcome') or event.get('state')) == 'REVIEW_BLOCKED'
            for event in related['events']))
        if missing_verdict:
            step = 'REVIEW_NOT_POSTED'
            codes.append('REVIEW_VERDICT_NOT_POSTED')
            result = {'reviewer_task': claim.get('reviewer_task'),
                      'reviewer_session_id': claim.get('native_session_id'),
                      'pr': claim.get('pr'), 'head_sha': claim.get('head_sha'),
                      'version': claim.get('version')}
        else:
            codes.append('REVIEWER_ATTENTION')
    elif step == 'REVIEW_NOT_POSTED':
        pass
    elif step == 'PUBLISH':
        cleared = []

        def pre_pr_gate(head, branch):
            status, hold = _pre_pr_remote_gate(record, store, executor_task, head, branch)
            if hold is None and status is not None:
                cleared.append(head)
            return hold

        try:
            result = _publish_task(record, d6, executor_task, pre_pr_gate=pre_pr_gate,
                git_runner=publish_git_runner, gh_runner=publish_gh_runner,
                record_result_call=record_result_call)
            if result['pr'] is None:
                return dict(result, task_id=task_id)
            if cleared:
                codes = _record_cleared_pr(
                    store, task_id, result['pr'], result['head'], d6,
                    _publish_run_gh if publish_gh_runner is None else publish_gh_runner,
                    d6.get('root') or cwd or os.getcwd())
            return {'task_id': task_id, 'codes': codes, 'pr': result['pr'],
                    'head': result['head']}
        except _PublishRefusal as exc:
            return _publish_refusal_result(store, task_id, exc)
        except Exception:
            return {'task_id': task_id, 'codes': ['PUBLISH_FAILED']}
    elif step == 'DEVELOPER_ATTENTION':
        active_repair = next((item for item in reversed(related['repair_claims'])
                              if type(item) is dict and item.get('head_sha') ==
                              (d6.get('result_head') if type(d6) is dict else None) and
                              item.get('state') == 'DISPATCHED'), None)
        if type(active_repair) is dict and active_repair.get('repair_state') == 'RECORD_UNAVAILABLE':
            codes.append('REPAIR_TASK_RECORD_UNAVAILABLE')
        else:
            codes.append('DEVELOPER_ATTENTION')
    elif step in ('REPAIR_OUTCOME_UNKNOWN', 'REPAIR_RESULT_NOT_PUBLISHED'):
        codes.append(step)
    elif step == 'NEEDS_ATTENTION':
        remote_status, _, _ = _remote_test_status(record, d6)
        codes.append('REMOTE_TEST_' + remote_status if remote_status in
                     ('TIMEOUT', 'SHA_MISMATCH') else 'NEEDS_ATTENTION')
    elif step == 'PR_MERGED_NO_REVIEW':
        codes.append('PR_MERGED_NO_REVIEW')
    elif step in ('REVIEW_OUTCOME_UNKNOWN', 'REVIEW_UNUSABLE_STOPPED'):
        codes.append(step)
        claim = _head_review_claim(record, number, d6['result_head'])
        failure = _failure_reason(claim.get('failure_reason')) if claim else None
        if failure is not None:
            codes.append(failure)
    elif step in ('WAITING_REPAIR', 'NEEDS_CWD', 'RUNTIME_NOT_ON_PATH',
                  'REVIEW_NO_CODE_FINDING'):
        codes.append(step)
    next_action =('resume the reviewer to post its verdict' if step == 'REVIEW_NOT_POSTED'
                   else step)
    advance_result = {'task_id': task_id, 'step': step, 'result': result,
                      'next': next_action, 'codes': codes}
    if refusal_dict is not None:
        advance_result['review_refusal'] = refusal_dict
    return _ack_consumed_events(
        advance_result, record, store,
        d6.get('result_head') if type(d6) is dict else None)
