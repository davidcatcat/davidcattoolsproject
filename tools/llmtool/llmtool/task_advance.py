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

from . import active_pm_inbox, github_events, review_dispatch, review_repair
from . import github_identity, review_result, sensitive_paths, task_completion
from . import lineage_counts, mac_full_suite, repair_policy, review_transcript
from . import remote_test, start_failure, task_submission_cli
from . import guidance_refresh
from .ai_github_exec import AiIdentityError, verified_ai_env
from .errors import LLMToolError
from .task_store import TaskStore, _json_bytes, external_action_refusal

# PR #1794 v91 F1 (I4 allowlist): the merge refusal for each lineage refusal.
_MERGE_LINEAGE_CODES = {'TASK_LINEAGE_PENDING': 'TASK_LINEAGE_PENDING',
                        'TASK_LINEAGE_PARKED': 'MERGE_ROOT_PARKED',
                        'TASK_LINEAGE_ENDED': 'MERGE_LINEAGE_ENDED',
                        'TASK_LINEAGE_UNKNOWN': 'MERGE_LINEAGE_UNKNOWN'}

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


def _require_published_head(git_runner, root, branch, head):
    """Refuse a PR unless the candidate head is still the local and pushed tip."""
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
    try:
        # PR #1789 codex-reviewer v1 F1 (I4): the recovery close is an external
        # mutation; under advance the runner re-checks the Task right before it.
        closed = _gh_succeeded(gh_runner, root,
                               ['pr', 'close', str(number), '--repo', repository])
    except _ExternalActionRefused as refused:
        # The Task's lineage is no longer absent or OPEN (PENDING, PARKED, ended or
        # unknown; PR #1794 I4 allowlist): leave the PR open, post
        # nothing more, and return the refusal with this call's operation record.
        operation.update(close_attempted=False, outcome='CLOSE_REFUSED_LEFT_OPEN',
                         next_action=(f'inspect PR #{number}; it is still open because the '
                                      f'Task took no further external action ({refused.code})'))
        raise _PublishRefusal(refused.code, operation) from None
    if closed:
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


def _publish_pr(gh_runner, root, branch, task_id, expected_head, title, body, *, bound_pr=None):
    pulls = _read_open_prs(gh_runner, root, branch)
    if type(pulls) is not list:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    if not pulls:
        if bound_pr is not None:
            raise _PublishRefusal('PUBLISH_BOUND_PR_UNAVAILABLE')
        return _publish_created_pr(gh_runner, root, branch, task_id, expected_head,
                                   title, body)
    # A PR open before this call is never closed; a head mismatch refuses.
    for attempt in range(4):
        if type(pulls) is not list or len(pulls) != 1:
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
        pull = pulls[0]
        number = pull.get('number') if type(pull) is dict else None
        head = pull.get('headRefOid') if type(pull) is dict else None
        if type(number) is not int or number < 1 or type(head) is not str:
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
        if bound_pr is not None and number != bound_pr:
            raise _PublishRefusal('PUBLISH_BOUND_PR_MISMATCH')
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
    has_tui_test = False
    has_non_tui_test = False
    for item in files:
        if type(item) is not dict:
            return True
        path = item.get('filename')
        if type(path) is not str:
            return True
        if 'previous_filename' in item:
            prev = item['previous_filename']
            if type(prev) is not str:
                return True
        else:
            prev = None
        # Owner O3 (#1943): TUI test files alone need no Owner acceptance; product TUI code does.
        if (re.fullmatch(r'tools/llmtool/llmtool/chat_tui[^/]*\.py', path) or
                re.fullmatch(r'tools/llmtool/llmtool/tui_[^/]*\.py', path) or
                path == 'tools/llmtool/tests/tui_pty_driver.py'):
            return True
        if prev is not None and (
                re.fullmatch(r'tools/llmtool/llmtool/chat_tui[^/]*\.py', prev) or
                re.fullmatch(r'tools/llmtool/llmtool/tui_[^/]*\.py', prev) or
                prev == 'tools/llmtool/tests/tui_pty_driver.py'):
            return True
        is_path_tui_test = bool(
            re.fullmatch(r'tools/llmtool/tests/test_chat_tui[^/]*', path) or
            re.fullmatch(r'tools/llmtool/tests/test_tui_[^/]*', path))
        if prev is not None:
            is_prev_tui_test = bool(
                re.fullmatch(r'tools/llmtool/tests/test_chat_tui[^/]*', prev) or
                re.fullmatch(r'tools/llmtool/tests/test_tui_[^/]*', prev))
            if is_path_tui_test or is_prev_tui_test:
                has_tui_test = True
            if not (is_path_tui_test and is_prev_tui_test):
                has_non_tui_test = True
        else:
            if is_path_tui_test:
                has_tui_test = True
            else:
                has_non_tui_test = True
    if has_tui_test and has_non_tui_test:
        return True
    return False


# Mac workflow pull_request paths (#1518); a test pins them to the yml file.
MAC_CI_PR_PATHS = (
    'tools/llmtool/**', '!tools/llmtool/*.md', 'scripts/llmtool_monitor.py',
    'scripts/test_llmtool_monitor.py', 'scripts/test_llmtool_monitor_github_reads.py',
    '.github/workflows/llmtool-mac-ci.yml', 'tools/mac-runner/**', 'tools/safe-git/**')


def _mac_ci_runs(paths):
    def hit(path):
        last = False
        for pat in MAC_CI_PR_PATHS:
            rx = re.escape(pat.lstrip('!')).replace(re.escape('**'), '.*')
            rx = rx.replace(re.escape('*'), '[^/]*')
            last = (not pat.startswith('!')) if re.fullmatch(rx, path) else last
        return last
    return any(hit(path) for path in paths)


# #1502 WP-G gate 1: GitHub lists a PR's files 100 per page and at most 3000
# in total. Every page is read; a short page ends the list. A full last page
# (3000 files) cannot prove the list is complete, so the gate fails closed with
# PR_FILES_TOO_MANY instead of treating a partial list as the whole PR.
_PR_FILES_PER_PAGE = 100
_PR_FILES_MAX_PAGES = 30
PR_FILES_TOO_MANY = 'PR_FILES_TOO_MANY'
_PR_FILES_UNAVAILABLE = 'PR_FILES_UNAVAILABLE'


def _pr_files(number, github_get):
    """Return (files, None) for every changed file, or (None, code).

    Page 1 keeps the historical read path; page N > 1 adds ``&page=N``. At
    most 30 reads, so it never loops. A read error or a malformed page gives
    PR_FILES_UNAVAILABLE; a full page 30 gives PR_FILES_TOO_MANY.
    """
    files = []
    for page in range(1, _PR_FILES_MAX_PAGES + 1):
        path = f'pulls/{number}/files?per_page={_PR_FILES_PER_PAGE}'
        if page > 1:
            path += f'&page={page}'
        try:
            batch = github_get(path)
        except Exception:
            return None, _PR_FILES_UNAVAILABLE
        if type(batch) is not list or len(batch) > _PR_FILES_PER_PAGE:
            return None, _PR_FILES_UNAVAILABLE
        files.extend(batch)
        if len(batch) < _PR_FILES_PER_PAGE:
            return files, None
    return None, PR_FILES_TOO_MANY


def _pr_changed_test_modules(number, head, github_get):
    """#2491: the test modules the PR adds or changes at exactly head.

    Return (modules, None) only when the PR is at head and its complete file
    list is readable; otherwise (frozenset(), explicit_code). It is the same
    fail-closed file reader _pr_docs_only uses (#2473); this is the one copy of
    the exact-head decision, owned by remote_test.
    """
    return remote_test._pr_test_modules(number, head, github_get)


# #1502 WP-G gate 2 (sec v93): llmtool-linux-ci.yml's 'changes' job sets four
# independent flags (code, docs, gateway, ai_loop) with linux_flags() from
# tools/llmtool/scripts/ci_path_classes.py, and each enabled flag runs its own
# check. The gates load that same classifier (never a local copy) and require
# the union of the checks: a code PR that also changes docs, the gateway or
# ai-loop still needs those checks. Never a check that cannot appear.
_AI_LOOP_CHECK = 'ai-loop helper tests (Python 3.14)'
_GATEWAY_CHECK = 'gateway tests (Linux 3.14)'
# In linux_flags() order: code, docs, gateway, ai_loop.
_LINUX_FLAG_CHECKS = ('check (Linux 3.14)', 'check (docs)', _GATEWAY_CHECK, _AI_LOOP_CHECK)
_CI_PATH_CLASSES = []


def _ci_path_classes():
    """The canonical CI path classifier module, loaded once from scripts/."""
    if not _CI_PATH_CLASSES:
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'ci_path_classes.py'
        spec = importlib.util.spec_from_file_location('_llmtool_ci_path_classes', path)
        if spec is None or spec.loader is None:
            raise ImportError('ci_path_classes.py unavailable')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CI_PATH_CLASSES.append(module)
    return _CI_PATH_CLASSES[0]


def _required_linux_checks(paths, old_paths=()):
    """The exact-head Linux workflow check names a PR with these paths needs.

    CI diffs with --no-renames, so a rename's old path is classified exactly
    like a changed path: both sides of a rename add their checks. Returns
    None (fail closed) when the paths cannot be classified the way CI does.
    """
    if not paths:
        return ['check (Linux 3.14)']
    names = list(paths) + list(old_paths)
    if any(type(name) is not str or '\0' in name for name in names):
        return None
    try:
        data = b''.join(name.encode('utf-8', 'surrogateescape') + b'\0' for name in names)
        flags = _ci_path_classes().linux_flags(data)
    except Exception:
        return None
    if type(flags) is not tuple or len(flags) != len(_LINUX_FLAG_CHECKS):
        return None
    required = [name for flag, name in zip(flags, _LINUX_FLAG_CHECKS) if flag is True]
    return required or None


def _merge_checks(record, number, head, pr, github_get, *, allow_missing_macos=False,
                  owner_acceptance=None, acceptance_out=None, skip_owner_acceptance=False):
    """Check every exact-head merge gate; return None or one refusal code.

    acceptance_out, when given, is a dict that gets the carried-acceptance
    evidence on a carry pass (#1237). Without it carry mode never runs.
    """
    try:
        checks = github_get(f'commits/{head}/check-runs?per_page=100')
    except Exception:
        return 'MERGE_CHECKS_UNAVAILABLE'
    files, files_code = _pr_files(number, github_get)
    if files_code == PR_FILES_TOO_MANY:
        return 'MERGE_PR_FILES_TOO_MANY'
    if (files_code is not None or type(checks) is not dict or
            type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] != len(checks['check_runs']) or
            len(checks['check_runs']) > 100):
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
    old_paths = [item.get('previous_filename') for item in files
                 if type(item) is dict and item.get('previous_filename') is not None]
    if any(type(path) is not str for path in old_paths):
        return 'MERGE_CHECKS_UNAVAILABLE'
    required_checks = _required_linux_checks(paths, old_paths)
    if required_checks is None:
        return 'MERGE_CHECKS_UNAVAILABLE'
    docs_only = required_checks == ['check (docs)']
    requires_macos = not docs_only and _mac_ci_runs(paths + old_paths)
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
    elif not skip_owner_acceptance and _owner_acceptance_required(files):
        return 'MERGE_OWNER_ACCEPTANCE_MANUAL'
    if requires_macos:
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


def _review_ci_gate(number, head, github_get, latest_out=None):
    """Require every required exact-head Linux workflow check to pass before review.

    latest_out, when a dict, gets the newest completed non-success run so a
    CI_FAILED result can be routed (#1502 WP-D D11). A PR whose file list
    cannot be read completely (more than GitHub's 3000-file limit) returns
    STOPPED with PR_FILES_TOO_MANY instead of waiting forever (#1502 WP-G).
    """
    try:
        checks = github_get(f'commits/{head}/check-runs?per_page=100')
    except Exception:
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    files, files_code = _pr_files(number, github_get)
    if files_code == PR_FILES_TOO_MANY:
        return 'STOPPED', PR_FILES_TOO_MANY
    if (files_code is not None or type(checks) is not dict or
            type(checks.get('check_runs')) is not list or
            type(checks.get('total_count')) is not int or
            checks['total_count'] < len(checks['check_runs']) or
            checks['total_count'] < 0 or len(checks['check_runs']) > 100):
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    paths, old_paths = [], []
    for item in files:
        path = item.get('filename') if type(item) is dict else None
        if type(path) is not str:
            return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
        paths.append(path)
        old = item.get('previous_filename')
        if old is not None:
            if type(old) is not str:
                return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
            old_paths.append(old)
    runs = checks['check_runs']
    if any(type(item) is not dict for item in runs):
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    required_checks = _required_linux_checks(paths, old_paths)
    if required_checks is None:
        return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
    # Any required Linux-workflow check failure routes to CI_FAILED; the Mac
    # check requires the post-PASS mac-ci label and stays merge-gated only.
    failed = None
    pending = False
    for required_name in required_checks:
        candidates = [item for item in runs if item.get('head_sha') == head and
                      item.get('name') == required_name]
        for item in candidates:
            if (type(item.get('id')) is not int or item['id'] < 1 or
                    type(item.get('status')) is not str):
                return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
        if not candidates:
            pending = True
            continue
        latest = max(candidates, key=lambda item: item['id'])
        if latest['status'] != 'completed':
            pending = True
            continue
        if type(latest.get('conclusion')) is not str:
            return 'WAITING_CI', 'LINUX_CI_UNAVAILABLE'
        # #2337: CI repair path handles any failed required check
        if latest['conclusion'] != 'success':
            failed = latest
            break
    if failed is not None:
        if type(latest_out) is dict:
            latest_out.update(failed)
        return 'CI_FAILED', 'LINUX_CI_FAILED'
    if pending:
        return 'WAITING_CI', 'LINUX_CI_PENDING'
    return None, None


# #1502 WP-D D11: required exact-head CI checks a CI repair may answer.
_LINUX_CHECK = 'check (Linux 3.14)'
_MAC_CHECK = 'check (macOS 3.13)'
# These conclusions say the CI environment stopped the check: wait uncounted,
# never re-run or re-request CI (main PM open-item 1).
_CI_ENV_CONCLUSIONS = ('cancelled', 'startup_failure')


def _ci_excerpt(run):
    """Bounded untrusted excerpt from the check run output title and summary only."""
    from . import task_submission_cli
    output = run.get('output')
    parts = []
    if type(output) is dict:
        for key in ('title', 'summary'):
            value = output.get(key)
            if type(value) is str and value:
                parts.append(value)
    lines = '\n'.join(parts).splitlines()[:review_repair.CI_EXCERPT_MAX_LINES]
    raw = '\n'.join(lines).encode('utf-8', errors='replace')
    excerpt = raw[:review_repair.CI_EXCERPT_MAX_BYTES].decode('utf-8', errors='ignore')
    if not excerpt.strip():
        return ''
    return task_submission_cli._screen_text(excerpt, review_repair.CI_EXCERPT_MAX_BYTES + 1024)


def _read_check_run(head, name, run_id, github_get):
    """Read one check run by id; None unless it is this completed exact-head check."""
    if type(run_id) is not int or run_id < 1:
        return None
    try:
        run = github_get(f'check-runs/{run_id}')
    except Exception:
        return None
    if (type(run) is not dict or type(run.get('id')) is not int or run['id'] != run_id or
            run.get('head_sha') != head or run.get('name') != name or
            run.get('status') != 'completed' or type(run.get('conclusion')) is not str):
        return None
    return run


def _newest_check_run(head, name, github_get):
    """Read the newest exact-head run of one check by id, or None."""
    try:
        checks = github_get(f'commits/{head}/check-runs?per_page=100')
    except Exception:
        return None
    runs = checks.get('check_runs') if type(checks) is dict else None
    if type(runs) is not list:
        return None
    candidates = [item for item in runs if type(item) is dict and
                  item.get('head_sha') == head and item.get('name') == name and
                  type(item.get('id')) is int and item['id'] >= 1]
    if not candidates:
        return None
    newest = max(candidates, key=lambda item: item['id'])
    if newest.get('status') != 'completed':
        return None
    return _read_check_run(head, name, newest['id'], github_get)


def _test_fail_count(record, number, store=None):
    """EC2 and CI repairs used, counted as review_repair counts them.

    #1502 WP-D L1 (I2, F1): a Task alone in its chain (no continues and no
    successor) counts its repairs on this PR; a Task in a chain counts every
    EC2 and CI repair of every chain Task, predecessors and successors and its
    own, on any PR, as review_repair does under the chain lock. A chain link or
    store scan in doubt (or no store) gives repair_policy.UNKNOWN, never 0.
    """
    return lineage_counts.lineage_counts(
        store, record, base_family=f'pr-{number}', include_successors=True)['test_fails']


def _live_repair_claim(record, head):
    """True when this Task holds a live repair claim on head (review_repair replays it)."""
    claims = record.get('review_repair_claims', [])
    return type(claims) is list and any(
        type(item) is dict and item.get('head_sha') == head and
        item.get('state') in ('CLAIMED', 'DISPATCHED', 'UNKNOWN') for item in claims)


def _test_family_used_up(record, number, head, store=None):
    """True when policy test_fail_max stops a new EC2 or CI repair on this head.

    A live claim on the head replays in review_repair, so it is left to that
    path. A policy that cannot be read is left to review_repair's own refusal.
    """
    if _live_repair_claim(record, head):
        return False
    try:
        policy = repair_policy.load_policy()
    except Exception:
        return False
    return repair_policy.test_failure_action(
        test_fail_count=_test_fail_count(record, number, store), policy=policy) == 'STOPPED'


_MAC_DETAILS_URL_RE = re.compile(r'/actions/runs/([0-9]+)/job/([0-9]+)\Z')
_CANCEL_CONCLUSIONS = frozenset({'cancelled', 'startup_failure'})
_ALLOWED_JOB_CONCLUSIONS = frozenset({
    'success', 'failure', 'cancelled', 'skipped',
    'timed_out', 'startup_failure', 'neutral', 'action_required'
})


def _mac_capacity_cancel(run, head, github_get, number=None):
    """Classify a Mac CI failure as infrastructure capacity cancellation (#1786).

    Accepted cancellation is interrupted CI evidence, not verified capacity
    causation. Return ('WAITING_ENV', ['CI_ENV_MAC_CAPACITY_CANCELLED'],
    evidence) when positive sibling-job evidence shows select or shards were
    cancelled with no failed test job; otherwise return None (fail closed to
    today's routing).
    """
    if type(run) is not dict or run.get('name') != _MAC_CHECK:
        return None
    if type(run.get('id')) is not int or run['id'] < 1:
        return None
    details_url = run.get('details_url')
    if type(details_url) is not str:
        return None
    match = _MAC_DETAILS_URL_RE.search(details_url)
    if not match:
        return None
    try:
        run_id = int(match.group(1))
        job_id = int(match.group(2))
    except (ValueError, TypeError):
        return None
    if run_id < 1 or job_id < 1:
        return None
    if type(number) is not int or number < 1:
        return None

    # Trust 1: files, code = _pr_files(number, github_get); code not None -> None.
    # Every entry must be a dict with a str 'filename'; if any 'filename' or str
    # 'previous_filename' starts with '.github/' -> None.
    files, code = _pr_files(number, github_get)
    if code is not None or type(files) is not list:
        return None
    for entry in files:
        if type(entry) is not dict:
            return None
        filename = entry.get('filename')
        if type(filename) is not str or filename.startswith('.github/'):
            return None
        previous_filename = entry.get('previous_filename')
        if type(previous_filename) is str and previous_filename.startswith('.github/'):
            return None

    # Trust 2: github_get(f'actions/runs/{run_id}') in try/except Exception -> must be
    # a dict with head_sha == head and path == '.github/workflows/llmtool-mac-ci.yml';
    # anything else -> None.
    try:
        workflow_run = github_get(f'actions/runs/{run_id}')
    except Exception:
        return None
    if type(workflow_run) is not dict:
        return None
    if workflow_run.get('head_sha') != head or workflow_run.get('path') != '.github/workflows/llmtool-mac-ci.yml':
        return None

    try:
        payload = github_get(f'actions/runs/{run_id}/jobs?per_page=100')
    except Exception:
        return None
    if type(payload) is not dict:
        return None
    jobs = payload.get('jobs')
    if type(jobs) is not list:
        return None
    total_count = payload.get('total_count')
    if type(total_count) is not int or total_count != len(jobs) or total_count > 100 or total_count < 0:
        return None
    for job in jobs:
        if type(job) is not dict:
            return None
        name = job.get('name')
        conclusion = job.get('conclusion')
        head_sha = job.get('head_sha')
        if type(name) is not str or type(conclusion) is not str:
            return None
        if head_sha != head:
            return None
        if conclusion not in _ALLOWED_JOB_CONCLUSIONS:
            return None

    select_jobs = [j for j in jobs if j.get('name') == 'select']
    if len(select_jobs) != 1:
        return None
    select_job = select_jobs[0]

    mac_jobs = [j for j in jobs if j.get('name') == _MAC_CHECK]
    if len(mac_jobs) != 1:
        return None
    mac_job = mac_jobs[0]
    # Trust 3: the job id parsed from details_url must equal the int 'id' of the single
    # job named _MAC_CHECK in the jobs list; mismatch or missing -> None.
    mac_job_id = mac_job.get('id')
    if type(mac_job_id) is not int or mac_job_id != job_id:
        return None
    if mac_job.get('conclusion') != 'failure':
        return None

    steps = mac_job.get('steps')
    if type(steps) is not list:
        return None
    if any(type(s) is not dict for s in steps):
        return None
    failed_steps = [s for s in steps if s.get('conclusion') == 'failure']
    if len(failed_steps) != 1 or failed_steps[0].get('name') != 'Shard results':
        return None

    failed_jobs = [j for j in jobs if j.get('conclusion') == 'failure']
    if len(failed_jobs) != 1 or failed_jobs[0] is not mac_job:
        return None

    select_conclusion = select_job.get('conclusion')
    shards_jobs = [j for j in jobs if j.get('name', '').startswith('shards')]

    if select_conclusion == 'timed_out' or any(j.get('conclusion') == 'timed_out' for j in shards_jobs):
        return None

    is_capacity = False
    if select_conclusion in _CANCEL_CONCLUSIONS:
        is_capacity = True
    elif select_conclusion == 'success':
        if shards_jobs and all(
            j.get('conclusion') in (_CANCEL_CONCLUSIONS | {'skipped'})
            for j in shards_jobs
        ) and any(
            j.get('conclusion') in _CANCEL_CONCLUSIONS
            for j in shards_jobs
        ):
            is_capacity = True

    if not is_capacity:
        return None

    jobs_summary = {job['name'][:100]: job['conclusion'] for job in jobs[:100]}
    evidence = {
        'check': run['name'],
        'check_run_id': run['id'],
        'head': head,
        'workflow_run_id': run_id,
        'jobs': jobs_summary,
    }
    return ('WAITING_ENV', ['CI_ENV_MAC_CAPACITY_CANCELLED'], evidence)


def _route_ci_failure(*, store, record, task_id, executor_task, number, d6, related,
                      run, fail_code, github_get, repair_review_call):
    """#1502 WP-D D11: route one completed exact-head required check that did not pass.

    Return (step, codes, result), or None when the conclusion keeps today's
    CI_FAILED result (neutral, skipped, stale, action_required). A failure goes
    back to the original Developer through review_repair on the shared EC2/CI
    count; an ENV conclusion waits uncounted and nothing re-runs CI.
    """
    head = d6['result_head']
    conclusion = run['conclusion']
    if conclusion in _CI_ENV_CONCLUSIONS:
        return ('WAITING_ENV', ['CI_ENV_' + conclusion.upper()],
                {'check': run['name'], 'check_run_id': run['id'], 'head': head})
    if conclusion not in ('failure', 'timed_out'):
        return None
    if conclusion == 'failure' and run.get('name') == _MAC_CHECK:
        mac_capacity = _mac_capacity_cancel(run, head, github_get, number)
        if mac_capacity is not None:
            return mac_capacity
    action = _remote_fail_action(related, head)
    if action == 'REPAIR_OUTCOME_UNKNOWN':
        unknown = _unknown_repair_detail(related, head)
        if unknown is not None:
            return action, [action, unknown['reason']], unknown
        return action, [action], None
    if action == 'WAITING_REPAIR':
        return action, [action], None
    if action == 'DEVELOPER_START_FAILED':
        # #2188: the PM decides; no automatic retry and no new repair claim.
        return action, [action], _start_failed_repair(related, head)
    if action == 'DEVELOPER_ATTENTION':
        return action, ['REPAIR_TASK_RECORD_UNAVAILABLE'], None
    if action == 'PUBLISH':
        # The repair answered but the head did not move: nothing to publish.
        return 'REPAIR_RESULT_NOT_PUBLISHED', ['REPAIR_RESULT_NOT_PUBLISHED'], None
    retry_claim_id = None
    if action == 'REPAIR_RETRY':
        retry = next((item for item in reversed(related.get('repair_claims', []))
                      if type(item) is dict and item.get('head_sha') == head and
                      item.get('state') == 'DISPATCHED'), None)
        retry_claim_id = retry.get('repair_task_id') if type(retry) is dict else None
        if type(retry_claim_id) is not str:
            return 'CI_FAILED', [fail_code, 'REPAIR_CLAIM_UNAVAILABLE'], None
    elif _test_family_used_up(record, number, head, store):
        return 'STOPPED', ['TEST_FAIL_LIMIT_REACHED'], None
    if type(d6.get('root')) is not str:
        return 'REFUSED', ['D6_BINDING_UNAVAILABLE'], None
    profile = (record.get('developer') or {}).get('provider_profile')
    if profile in ('codex', 'claude') and shutil.which(profile) is None:
        return 'RUNTIME_NOT_ON_PATH', ['RUNTIME_NOT_ON_PATH'], None
    ci_failure = {'head': head, 'check_name': run['name'], 'check_run_id': run['id'],
                  'conclusion': 'failure', 'excerpt': _ci_excerpt(run)}
    invoke = repair_review_call or review_repair.repair_review
    try:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            value = invoke(store=store, task_id=task_id, executor_task=executor_task,
                           pr_number=number, auto_review=False, github_get=github_get,
                           retry_claim_id=retry_claim_id, ci_failure=ci_failure,
                           auto=True)
        emitted = captured.getvalue().strip()
        try:
            result = json.loads(emitted) if emitted else value
        except (TypeError, ValueError):
            result = value
    except Exception as exc:
        code = _refusal(exc, 'review_repair_code')
        if code == 'REPAIR_LIMIT_REACHED':
            return 'STOPPED', ['TEST_FAIL_LIMIT_REACHED'], None
        return 'CI_FAILED', [fail_code, code], None
    # No codes: task drive goes on to WAITING_REPAIR like the EC2 repair path.
    return 'CI_FAILED', [], result


def _receipt_not_executed(receipt):
    """Return True only when receipt is a FAIL with ran_total 0 and empty failures list."""
    if type(receipt) is not dict:
        return False
    return (receipt.get('result') == 'FAIL' and
            type(receipt.get('ran_total')) is int and
            receipt['ran_total'] == 0 and
            receipt.get('failures') == [])


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
        if receipt['result'] == 'PASS' and not remote_test.pass_receipt_covers(
                receipt, remote_test.remote_required_modules(record)):
            return None
        summary_result = ('NOT_EXECUTED'
                          if _receipt_not_executed(receipt)
                          else receipt.get('result'))
        return {
            'result': summary_result,
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


# #1705: dispatch_review hands the verdict to this caller to post only in these
# states. Every other verdict_post state means dispatch_review already posted
# or verified the comment, another poster holds the claim, or the text is
# withheld, so posting here would create a second claim-bound comment.
_CALLER_VERDICT_POST_STATES = frozenset(('CALLER_POSTING', 'RECEIPT_UNAVAILABLE'))


def _caller_posts_verdict(result):
    """True only when this caller, not dispatch_review, must post the verdict."""
    if 'verdict_post' not in result:
        # A result without verdict_post predates the #1705 post receipt.
        return True
    verdict_post = result.get('verdict_post')
    return (type(verdict_post) is dict and
            verdict_post.get('state') in _CALLER_VERDICT_POST_STATES)


def _merge_pass_claim(record, number, head, events):
    candidates = []
    for claim in _claims(record):
        if (type(claim) is not dict or claim.get('pr') != number or
                claim.get('head_sha') != head or
                review_result.review_kind(claim) != 'formal' or
                claim.get('state') not in ('COMPLETED', 'DISPATCHED') or
                type(claim.get('version')) is not int):
            continue
        result = claim.get('review_result')
        outcome = next((event.get('outcome') or event.get('state') for event in events
                        if type(event) is dict and event.get('source') == 'REVIEWER' and
                        event.get('source_sha') == head and
                        review_result.review_kind(event) == 'formal' and
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
    # Any newer formal claim on this PR and head, in any state and with or without
    # a REVIEWER event, vetoes the PASS; so does a claim with no integer version.
    for claim in _claims(record):
        if (type(claim) is not dict or claim.get('pr') != number or
                claim.get('head_sha') != head or
                review_result.review_kind(claim) != 'formal'):
            continue
        version = claim.get('version')
        if type(version) is not int or version > top:
            return 'MERGE_NEWER_REVIEW_EXISTS'
    if any(item[0] == top and (item[1] != 'PASS' or item[2] != 'REVIEW_PASS')
           for item in candidates):
        return 'MERGE_REVIEW_NOT_PASS'
    if any(type(event) is dict and event.get('source') == 'REVIEWER' and
           event.get('source_sha') == head and
           review_result.review_kind(event) == 'formal' and
           type(event.get('version')) is int and
           event['version'] > top for event in events):
        return 'MERGE_NEWER_REVIEW_EXISTS'
    return top


def _merge_security_claim(record, number, head, events):
    """Refuse when any security verdict at (number, head) is not PASS, failing closed."""
    try:
        if type(record) is not dict:
            return 'MERGE_SECURITY_REVIEW_UNKNOWN'
        raw_claims = record.get('review_dispatch_claim')
        if raw_claims is None:
            claims = []
        elif type(raw_claims) is dict:
            claims = [raw_claims]
        elif type(raw_claims) is list:
            claims = raw_claims
        else:
            return 'MERGE_SECURITY_REVIEW_UNKNOWN'
        if events is None:
            event_list = []
        elif type(events) is list:
            event_list = events
        else:
            return 'MERGE_SECURITY_REVIEW_UNKNOWN'
        for claim in claims:
            if type(claim) is not dict:
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            if claim.get('pr') != number or claim.get('head_sha') != head:
                continue
            kind = review_result.review_kind(claim)
            if kind is None:
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            if kind != 'security':
                continue
            if claim.get('state') != 'COMPLETED':
                return 'MERGE_SECURITY_REVIEW_NOT_PASS'
            result = claim.get('review_result')
            if type(result) is not dict:
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            if 'head' in result and result.get('head') != head:
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            verdict = result.get('verdict')
            if verdict == 'PASS':
                continue
            if verdict in ('FIX', 'FAIL', 'BLOCKED'):
                return 'MERGE_SECURITY_REVIEW_NOT_PASS'
            return 'MERGE_SECURITY_REVIEW_UNKNOWN'
        for event in event_list:
            if type(event) is not dict:
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            if event.get('source') != 'REVIEWER' or event.get('source_sha') != head:
                continue
            kind = review_result.review_kind(event)
            if kind is None:
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            session_id = event.get('session_id')
            if type(session_id) is str and session_id.startswith('secreview-') and kind != 'security':
                return 'MERGE_SECURITY_REVIEW_UNKNOWN'
            if kind != 'security':
                continue
            outcome = event.get('outcome') or event.get('state')
            if outcome == 'REVIEW_PASS':
                continue
            if outcome in ('REVIEW_FAIL', 'REVIEW_BLOCKED'):
                return 'MERGE_SECURITY_REVIEW_NOT_PASS'
            return 'MERGE_SECURITY_REVIEW_UNKNOWN'
        return None
    except Exception:
        return 'MERGE_SECURITY_REVIEW_UNKNOWN'


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
    lineage_refusal = external_action_refusal(record)
    if lineage_refusal is not None:
        # #1502 WP-D D10: a split (PARKED) ROOT is never merged, nor is an ended
        # Task. PR #1794 v91 F1: only no lineage or OPEN merges (I4 allowlist).
        return _merge_refusal(_MERGE_LINEAGE_CODES.get(lineage_refusal,
                                                       'MERGE_LINEAGE_UNKNOWN'))
    if any(type(claim) is dict and claim.get('head_sha') == head and
           claim.get('pr') != number for claim in _claims(record)):
        return _merge_refusal('MERGE_CONFLICTING_PR')
    if owner_exception is not None and pr.get('merged') is True:
        prior = record.get('merge_attempt')
        if type(prior) is dict:
            return _reconcile_exception_merge(store, record, number, head, pr, prior,
                                              owner_exception, github_get)
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
    security_code = _merge_security_claim(record, number, head, events)
    if security_code is not None:
        return _merge_refusal(security_code)
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
    except Exception as exc:
        # #1502 D10c2 (security v91): the store re-reads the lineage under the Task
        # lock in this same write; a ROOT parked since `record` was read is refused.
        # #1781 (I4): so is a split reservation that landed since then.
        code = getattr(exc, 'task_store_error_code', None)
        if code in ('TASK_STORE_INVALID', 'TASK_STORE_ARTIFACT_SENSITIVE'):
            # #1362: the stored record fails strict re-validation on this write
            # (for example a legacy docs-only test entry). That is not a race.
            return _merge_refusal('MERGE_RECORD_INVALID')
        return _merge_refusal(code if code in ('MERGE_ROOT_PARKED', 'MERGE_LINEAGE_ENDED',
                                               'TASK_LINEAGE_PENDING')
                              else 'MERGE_CLAIM_CONFLICT')
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


def _reconcile_exception_merge(store, record, number, head, pr, prior, owner_exception,
                              github_get):
    """Reconcile an owner-exception merge (#1519); no merge, label or comment is sent."""
    if prior.get('pr') != number or prior.get('head_sha') != head:
        return _merge_refusal('MERGE_CONFLICTING_PR')
    if prior.get('state') != 'CLAIMED':
        return _merge_refusal('MERGE_ALREADY_CLAIMED')
    pr_head = pr.get('head') if type(pr) is dict else None
    if (type(pr_head) is not dict or pr_head.get('sha') != head or
            pr.get('state') != 'closed' or pr.get('merged') is not True):
        return _merge_refusal('MERGE_STALE_HEAD')
    from . import owner_exception as owner_exception_mod
    v_code, v_evidence = owner_exception_mod.verify(
        owner_exception, number, github_get, allow_merged=True,
    )
    if v_code is not None:
        return _merge_refusal(v_code)
    if type(v_evidence) is not dict or v_evidence.get('head') != head:
        return _merge_refusal('OWNER_EXCEPTION_SHA_MISMATCH')
    review_exception = {
        'type': owner_exception_mod.EXCEPTION_TYPE,
        'url': owner_exception,
        'evidence': v_evidence,
    }
    claim = dict(prior, state='MERGED')
    try:
        store.compare_and_update(
            record['task_id'],
            expected={'merge_attempt': prior},
            merge_attempt=claim,
            review_exception=review_exception,
        )
    except Exception:
        return _merge_refusal('MERGE_RESULT_RECORD_FAILED')
    record['merge_attempt'] = claim
    record['review_exception'] = review_exception
    return {
        'step': 'MERGED',
        'codes': ['MERGE_RECONCILED'],
        'result': {
            'pr': number,
            'head': head,
            'review_exception': review_exception,
            'reconciled': True,
        },
    }


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
    # Owner #1485: no FULL request per merge; `llmtool ci full-mac-due` decides.
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
    # #1576: Owner acceptance gates the merge only, never the Mac run that precedes it.
    code = _merge_checks(record, number, head, pr, get, allow_missing_macos=True,
                         skip_owner_acceptance=True)
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
            # #1781 (I4): `task drive` calls this outside advance; re-check here too.
            _external_action_guard(store, task_id)()
        except _ExternalActionRefused as refused:
            return _merge_refusal(refused.code)
        try:
            labeled = runner(d6.get('root') or cwd or os.getcwd(),
                             ['pr', 'edit', str(number), '--add-label', 'mac-ci'])
        except Exception:
            labeled = None
        if labeled is None or getattr(labeled, 'returncode', 1) != 0:
            return _merge_refusal('MERGE_MAC_CI_LABEL_FAILED')
    return {'step': 'WAITING_CI', 'codes': [], 'result': {'pr': number, 'head': head}}


def _record_d6_result(*, task_id, canonical_root, root, write_paths=None):
    lifecycle_path = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    spec = importlib.util.spec_from_file_location('_llmtool_d6_worktree_lifecycle', lifecycle_path)
    if spec is None or spec.loader is None:
        raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.record_result(task_id=task_id, canonical_root=canonical_root, root=root,
                                 write_paths=write_paths)


def _publish_recorded_main_pin(record):
    """Return the Task's recorded authenticated main sha, or None (#2367 F1).

    Task completion stores this pin in the Task record (outside the D6): the
    main tip read from the trusted GitHub ref, bound to this Task, run,
    contract and Developer source. Main is published history, so that commit
    stays a published base after main moves on. A pin that does not validate
    is not used.
    """
    completion = record.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    pin = evidence.get('trusted_main_pin') if type(evidence) is dict else None
    developer = record.get('developer')
    source_sha = developer.get('source_sha') if type(developer) is dict else None
    binding = record.get('task_binding')
    if (type(pin) is not dict or type(source_sha) is not str or
            type(record.get('task_id')) is not str or type(record.get('run_id')) is not str or
            type(binding) is not dict or type(binding.get('contract_digest')) is not str):
        return None
    try:
        pin = task_submission_cli._validate_trusted_main_pin(pin, record, source_sha)
    except Exception:
        return None
    return pin['sha']


def _publish_base_commits(git_runner, root, remote_head, recorded_main=None, *,
                          materialize=None):
    """Return the published commits that bound what HEAD adds (#2367 A-sec).

    Only published commits count: this branch's head and the main tip from
    the remote's own ls-remote answer, and the Task's recorded authenticated
    main pin (#2367 F1: a new branch whose main moved to a tip this D6 does
    not hold), each when that commit is present locally. Local refs are
    never a base, because a D6 writer that can commit can also move them.
    ``materialize`` may make the recorded pin's commit local (a direct-route
    result verifies its pin before its first publish); it is never used for
    the unauthenticated remote tip. With no base the added commits are
    unknown, so publish fails closed.
    """
    bases = []

    def present(sha):
        try:
            result = git_runner(root, ['cat-file', '-e', f'{sha}^{{commit}}'])
        except Exception:
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
        return getattr(result, 'returncode', 1) == 0

    def add_if_present(sha, *, fetch_ok=False):
        if sha in bases:
            return
        is_present = present(sha)
        if not is_present and fetch_ok and materialize is not None:
            try:
                materialize(sha)
            except Exception:
                pass
            is_present = present(sha)
        if is_present:
            bases.append(sha)

    if remote_head:
        add_if_present(remote_head)

    listed = _publish_command(git_runner, root,
        ['ls-remote', '--heads', 'origin', 'refs/heads/main'])
    for line in listed.splitlines():
        parts = line.split()
        if len(parts) != 2 or _HEX40.fullmatch(parts[0]) is None:
            raise _PublishRefusal('PUBLISH_REMOTE_UNAVAILABLE')
        if parts[1] != 'refs/heads/main':
            continue
        add_if_present(parts[0])
    if type(recorded_main) is str and _HEX40.fullmatch(recorded_main) is not None:
        add_if_present(recorded_main, fetch_ok=True)
    if not bases:
        raise _PublishRefusal('PUBLISH_BINDING_UNAVAILABLE')
    return bases


def _publish_task_scope_bases(git_runner, root, d6, bases, write_paths):
    """#2534: bases for the per-commit scope walk, or None to keep the full walk.

    The D6's registered base_sha (written by the integrator lifecycle, never
    by the Developer) may start the walk only when it is an ancestor of HEAD,
    the remote main tip is local, and the net change base_sha still makes
    against its merge-base with that tip touches no path outside write_paths.
    Any doubt returns None, so every commit is judged one by one as before.
    """
    base = d6.get('base_sha') if type(d6) is dict else None
    if type(base) is not str or _HEX40.fullmatch(base) is None or base in bases:
        return None
    try:
        listed = _publish_command(git_runner, root,
            ['ls-remote', '--heads', 'origin', 'refs/heads/main'])
        tips = [line.split()[0] for line in listed.splitlines()
                if len(line.split()) == 2 and line.split()[1] == 'refs/heads/main' and
                _HEX40.fullmatch(line.split()[0]) is not None]
        if len(tips) != 1 or tips[0] not in bases:
            return None
        main_tip = tips[0]
        for args in (['cat-file', '-e', f'{base}^{{commit}}'],
                     ['merge-base', '--is-ancestor', base, 'HEAD']):
            if getattr(git_runner(root, args), 'returncode', 1) != 0:
                return None
        fork = _publish_command(git_runner, root,
            ['--no-replace-objects', 'merge-base', base, main_tip]).strip()
        if _HEX40.fullmatch(fork) is None:
            return None
        net = _publish_command(git_runner, root,
            ['--no-replace-objects', 'diff', '--no-renames', '--name-only', '-z',
             fork, base])
    except Exception:
        return None
    if any(path not in write_paths for path in net.split('\0') if path):
        return None
    return list(bases) + [base]


def _publish_added_commits(git_runner, root, bases):
    """Return (commit, parents) for every commit HEAD adds over bases.

    Grafts are refused and replace refs ignored, so the walk sees what push
    sends.
    """
    try:
        # A graft file could rewrite the parents the walk reads.
        task_completion._reject_legacy_grafts(root)
    except Exception:
        raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
    listed = _publish_command(git_runner, root,
        ['--no-replace-objects', 'rev-list', '--parents', 'HEAD', '--not', *bases])
    commits = []
    for line in listed.splitlines():
        shas = line.split()
        if not shas or any(_HEX40.fullmatch(sha) is None for sha in shas):
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED')
        commits.append((shas[0], shas[1:]))
    return commits


def _publish_commit_paths(git_runner, root, commit, parents):
    """Return the paths one commit introduces.

    Each commit is read on its own, so a path added and later removed still
    counts. A one-parent commit introduces each path it changes. A merge
    introduces the paths that differ from every parent; any other path holds
    one parent's version, and that parent is either another added commit or
    already published. A parentless commit introduces its whole tree.
    """
    if not parents:
        raw = _publish_command(git_runner, root,
            ['--no-replace-objects', 'ls-tree', '-r', '--name-only', '-z', commit])
        return {path for path in raw.split('\0') if path}
    introduced = None
    for parent in parents:
        raw = _publish_command(git_runner, root,
            ['--no-replace-objects', 'diff', '--no-renames', '--name-only', '-z',
             parent, commit])
        changed = {path for path in raw.split('\0') if path}
        introduced = changed if introduced is None else introduced & changed
    return introduced


# Fixed diff options, so local diff config (external drivers, textconv,
# prefixes, colour) cannot change or hide what the screen reads. --text
# shows binary content as text; output that is not UTF-8 refuses.
_PUBLISH_SCREEN_DIFF = ('--no-renames', '--no-ext-diff', '--no-textconv', '--no-color',
                        '--text', '--src-prefix=a/', '--dst-prefix=b/')


def _publish_blob_reader(git_runner, root, revision):
    """Pre-image reader for the diff screen, like task_submission_cli._revision_sources."""
    def read(path):
        if revision is None:
            raise LLMToolError('PUBLISH_SOURCE_UNAVAILABLE')
        try:
            return _publish_command(git_runner, root,
                ['--no-replace-objects', 'cat-file', 'blob', f'{revision}:{path}'])
        except _PublishRefusal:
            raise LLMToolError('PUBLISH_SOURCE_UNAVAILABLE') from None
    return read


def _publish_commit_diff(git_runner, root, commit, parents):
    """Return (unified diff of what one commit introduces, its pre-image revision)."""
    if not parents:
        return _publish_command(git_runner, root,
            ['--no-replace-objects', 'diff-tree', '-r', '-p', '--root', '--no-commit-id',
             *_PUBLISH_SCREEN_DIFF, commit]), None
    if len(parents) == 1:
        return _publish_command(git_runner, root,
            ['--no-replace-objects', 'diff', *_PUBLISH_SCREEN_DIFF, parents[0], commit]), parents[0]
    # A merge: only the paths it introduces, so a file taken unchanged from
    # one parent (for example published main) is not screened as the Task's
    # own. An introduced path is diffed against the first parent, so lines it
    # takes from the other parent are screened too (conservative).
    introduced = sorted(_publish_commit_paths(git_runner, root, commit, parents))
    if not introduced:
        return '', parents[0]
    return _publish_command(git_runner, root,
        ['--literal-pathspecs', '--no-replace-objects', 'diff', *_PUBLISH_SCREEN_DIFF,
         parents[0], commit, '--', *introduced]), parents[0]


def _publish_screen_commits(git_runner, root, commits):
    """#2367 A-rca: screen the added content of each commit before record or push.

    This is the Developer-output sensitive screen task_submission_cli applies
    to a Developer's uncommitted Task diff (developer_diff_sensitive over the
    added diff lines). A hit refuses with its code, DEVELOPER_OUTPUT_SENSITIVE;
    a screen that cannot run refuses too. Only the code is reported, never
    the matched text.
    """
    for commit, parents in commits:
        diff, revision = _publish_commit_diff(git_runner, root, commit, parents)
        if not diff:
            continue
        try:
            hit = task_submission_cli.developer_diff_sensitive(
                diff, task_submission_cli._added_diff_content(diff),
                task_submission_cli._patch_paths,
                _publish_blob_reader(git_runner, root, revision))
        except Exception:
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED') from None
        if hit:
            raise _PublishRefusal('DEVELOPER_OUTPUT_SENSITIVE')


def _publish_task(record, d6, executor_task, *, git_runner=None,
                  gh_runner=None, record_result_call=None):
    """Commit scoped D6 changes, record the result, push and open or reuse a PR."""
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

    # #2367 A-sec/A-rca: commits already on HEAD (an earlier publish or the
    # D6 writer) pass the same write-scope check and Developer-output
    # sensitive screen as uncommitted changes, before this publish commits,
    # records or pushes anything.
    recorded_main = _publish_recorded_main_pin(record)

    # A direct-route result records its authenticated pin before this publish.
    # If that pin's commit was not materialized then (the capture is best
    # effort), fetch only this validated pin now; other records and the
    # unauthenticated remote tip keep today's local-presence rule.
    def materialize_main(sha):
        return task_completion.ensure_main_commit(root, sha)

    materialize = None
    if (recorded_main is not None and
            type(developer.get('direct_result_callback')) is dict):
        materialize = materialize_main
    bases = _publish_base_commits(git_runner, root, remote_head, recorded_main,
                                  materialize=materialize)
    committed = _publish_added_commits(git_runner, root, bases)
    # #2534: commits the D6 already held when the integrator registered it
    # (base_sha) are branch history, not this Task's work. Judge them by the
    # net content they still differ from main by; judge every later commit
    # one by one as before. Without a usable registered base, all commits are
    # judged one by one (fail closed).
    task_bases = _publish_task_scope_bases(git_runner, root, d6, bases, write_paths)
    scoped = (committed if task_bases is None else
              _publish_added_commits(git_runner, root, task_bases))
    task_paths = set(changed)
    for commit, parents in scoped:
        commit_paths = _publish_commit_paths(git_runner, root, commit, parents)
        if any(path not in write_paths for path in commit_paths):
            raise _PublishRefusal('PUBLISH_OUT_OF_SCOPE')
        task_paths.update(commit_paths)
    _publish_screen_commits(git_runner, root, committed)

    task_commits = list(scoped)
    if changed:
        _publish_command(git_runner, root, ['add', '--', *changed])
        staged = _publish_command(git_runner, root,
            ['diff', '--cached', '--no-renames', '--name-only', '-z', 'HEAD'])
        staged_paths = [path for path in staged.split('\0') if path]
        if not staged_paths or any(path not in write_paths for path in staged_paths):
            raise _PublishRefusal('PUBLISH_OUT_OF_SCOPE')
        title, _ = _publication_text(record, executor_task, branch)
        previous_head = local_head
        _publish_command(git_runner, root,
            ['commit', '-m', title, '-m', f'llmtool Task {task_id}'])
        local_head = _publish_command(git_runner, root,
            ['rev-parse', '--verify', 'HEAD']).strip()
        # The commit this publish just made gets the same screen before it is
        # recorded or pushed: it must be the one commit over the old head.
        own = _publish_added_commits(git_runner, root, [previous_head])
        if len(own) != 1 or own[0][0] != local_head or own[0][1] != [previous_head]:
            raise _PublishRefusal('PUBLISH_COMMAND_FAILED')
        _publish_screen_commits(git_runner, root, own)
        task_commits = own + task_commits

    if any(path in guidance_refresh.GUIDANCE_FILES for path in task_paths):
        title, _ = _publication_text(record, executor_task, branch)
        try:
            refreshed = guidance_refresh.refresh_guidance_hashes(
                root=root,
                head=local_head,
                write_paths=write_paths,
                task_commits=task_commits,
                git_runner=git_runner,
                commit_message=(f'{title} (refresh guidance hash)',
                                f'llmtool Task {task_id}'),
            )
        except guidance_refresh.GuidanceRefreshError as exc:
            raise _PublishRefusal(exc.code) from None
        if refreshed['refreshed']:
            local_head = refreshed['head']
    # #2367 (M1, #1317): record the Developer result head as committed and push
    # it as it is. PUBLISH never merges main into the head; a head behind main
    # or conflicting with it is handled at the merge gate (BEHIND/dirty path).
    # A head committed by an earlier interrupted publish is recorded here too.
    if changed or d6.get('recorded_result_head') != local_head:
        try:
            record_result_call = _record_d6_result if record_result_call is None else record_result_call
            recorded = record_result_call(task_id=executor_task, canonical_root=canonical_root,
                                          root=root, write_paths=write_paths)
        except Exception:
            raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED') from None
        if recorded not in (None, local_head):
            raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED')

    if remote_head != local_head:
        _publish_command(git_runner, root,
            ['push', 'origin', f'HEAD:refs/heads/{branch}'],
            code='PUBLISH_PUSH_FAILED')
    _require_published_head(git_runner, root, branch, local_head)
    title, body = _publication_text(record, executor_task, branch)
    number = _publish_pr(gh_runner, root, branch, task_id, local_head, title, body,
                         bound_pr=_pr_number(record, None))
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


# #2486: fixed codes for a refused review whose one bounded re-run is not allowed.
REVIEW_RERUN_RECEIPT_INVALID = 'REVIEW_RERUN_RECEIPT_INVALID'
REVIEW_RERUN_ALREADY_DONE = 'REVIEW_RERUN_ALREADY_DONE'
REVIEW_RERUN_HEAD_CHANGED = 'REVIEW_RERUN_HEAD_CHANGED'
REVIEW_RERUN_CONTRACT_CHANGED = 'REVIEW_RERUN_CONTRACT_CHANGED'
REVIEW_RERUN_REVIEWER_LIVE = 'REVIEW_RERUN_REVIEWER_LIVE'
REVIEW_RERUN_RECORD_FAILED = 'REVIEW_RERUN_RECORD_FAILED'


def _reviewer_process_live(claim):
    """True when a reviewer attempt may still be running; doubt refuses.

    A COMPLETED claim has ended. A stored fixed-word refusal (UNKNOWN with a
    failure_reason) ended with nothing posted, so it is only a doubt when it
    also carries a bound native session id. Every other state, including
    CLAIMED, DISPATCHED and UNKNOWN without a refusal, may still be running.
    """
    if type(claim) is not dict:
        return True
    state = claim.get('state')
    if state == 'COMPLETED':
        return False
    if (state == 'UNKNOWN' and
            _failure_reason(claim.get('failure_reason')) is not None):
        return claim.get('native_session_id') is not None
    return True


def _review_refusal_rerun(record, claim, d6, pr, store, github_get):
    """Check one bounded automatic re-run of a stored refusal.

    Return (next_version, None) when it is allowed, (None, code) for a fixed
    refusal, or (None, None) when the claim is not a stored refusal.
    """
    if (type(claim) is not dict or claim.get('state') != 'UNKNOWN' or
            _failure_reason(claim.get('failure_reason')) is None):
        return None, None
    receipt = review_dispatch.read_refusal_receipt(
        store=store, task_id=record.get('task_id'), claim=claim)
    if receipt is None:
        return None, REVIEW_RERUN_RECEIPT_INVALID
    if 'rerun' in receipt or receipt.get('automatic_rerun') is True:
        return None, REVIEW_RERUN_ALREADY_DONE
    head = claim.get('head_sha')
    result_head = d6.get('result_head') if type(d6) is dict else None
    pr_head = pr.get('head') if type(pr) is dict else None
    if (type(head) is not str or head != result_head or
            type(pr_head) is not dict or pr_head.get('sha') != head):
        return None, REVIEW_RERUN_HEAD_CHANGED
    binding = record.get('task_binding')
    contract_digest = binding.get('contract_digest') if type(binding) is dict else None
    if (type(contract_digest) is not str or
            receipt.get('contract_digest') != contract_digest):
        return None, REVIEW_RERUN_CONTRACT_CHANGED
    # #2486 security v91: any attempt for this PR and head may still be running,
    # not only the newest refused claim. One live attempt is doubt; doubt refuses.
    live_attempts = [item for item in _claims(record)
                     if type(item) is dict and item.get('pr') == claim.get('pr') and
                     item.get('head_sha') == head]
    if any(_reviewer_process_live(item) for item in live_attempts):
        return None, REVIEW_RERUN_REVIEWER_LIVE
    if not _review_verdict_absent(claim, github_get):
        return None, None
    versions = [item.get('version') for item in _claims(record)
                if type(item) is dict and type(item.get('version')) is int and
                not isinstance(item.get('version'), bool)]
    return max(versions, default=0) + 1, None


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


def _remote_test_status(record, d6, required_modules=None):
    """Return exact-head remote-test state, bounded excerpt, and validated receipt."""
    head = d6.get('result_head') if type(d6) is dict else None
    completion = record.get('completion') if type(record) is dict else None
    executor_task = completion.get('executor_task_id') if type(completion) is dict else None
    if type(head) is not str or type(executor_task) is not str:
        return None, None, None
    required = (remote_test.remote_required_modules(record)
                if required_modules is None else tuple(required_modules))
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
            if remote_test.pass_receipt_covers(receipt, required):
                return 'PASS', None, receipt
            break
        if receipt['result'] in ('TIMEOUT', 'SHA_MISMATCH'):
            return receipt['result'], None, receipt
        if receipt['result'] == 'FAIL':
            if _receipt_not_executed(receipt):
                return 'NOT_EXECUTED', None, receipt
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
        if (outcome.get('result') == 'UNAVAILABLE' and
                outcome.get('code') in _UNREACHABLE_OUTCOME_CODES):
            return 'UNREACHABLE', None, None
        if (outcome.get('result') == 'ADMISSION_DENIED' and
                outcome.get('code') == 'MODULE_NOT_ADMITTED' and
                outcome.get('modules') == [] and not required):
            return 'ADMISSION_DENIED', None, None
    return None, None, None


# #2602 item k: an EC2 run that could not reach the runner is retried once on
# the same commit, then waited on with an Owner notice, never counted as a repair.
_UNREACHABLE_OUTCOME_CODES = frozenset(('SSH_UNAVAILABLE', 'SSH_FAILED'))


def _remote_test_unreachable_count(record, d6):
    """Count exact-head EC2 outcomes where the runner could not be reached."""
    head = d6.get('result_head') if type(d6) is dict else None
    completion = record.get('completion') if type(record) is dict else None
    executor_task = completion.get('executor_task_id') if type(completion) is dict else None
    if type(head) is not str or type(executor_task) is not str:
        return 0
    outcomes = record.get('remote_test_outcomes', [])
    if type(outcomes) is not list:
        return 0
    return sum(1 for outcome in outcomes
               if type(outcome) is dict and
               outcome.get('executor_task') == executor_task and
               outcome.get('sha') == head and
               outcome.get('result') == 'UNAVAILABLE' and
               outcome.get('code') in _UNREACHABLE_OUTCOME_CODES)


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


def _remote_fail_action(related, result_head):
    """Return the repair action for an exact-head EC2 FAIL."""
    repairs = related.get('repair_claims', []) if type(related) is dict else []
    current_repair = next((item for item in reversed(repairs)
                           if type(item) is dict and item.get('head_sha') == result_head), None)
    if current_repair is not None and current_repair.get('state') == 'DISPATCHED':
        state = current_repair.get('repair_state')
        if state == 'START_FAILED':
            return 'DEVELOPER_START_FAILED'
        if state in ('UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED'):
            return 'REPAIR_RETRY'
        if state == 'RECOVERABLE_DIRECT':
            return 'REPAIR_REVIEW'
        if state in ('RUNNING', 'PENDING'):
            return 'WAITING_REPAIR'
        if state == 'UNKNOWN':
            # #2325: a launched run that provably ended with no change is retried.
            return ('REPAIR_RETRY' if _unknown_retry_allowed(current_repair)
                    else 'REPAIR_OUTCOME_UNKNOWN')
        if state == 'RECORD_UNAVAILABLE':
            return 'DEVELOPER_ATTENTION'
        if state == 'RESPONDED':
            return 'PUBLISH'
    return 'REPAIR_REVIEW'


def _start_failed_repair(related, head):
    """Name the repair Task of the DISPATCHED start-failed claim at head, or None.

    #2188 step DEVELOPER_START_FAILED: the PM runs `task retry-start` on this
    Task or chooses another route. Nothing here retries or writes.
    """
    repairs = related.get('repair_claims', []) if type(related) is dict else []
    if type(repairs) is not list:
        return None
    current = next((item for item in reversed(repairs)
                    if type(item) is dict and item.get('head_sha') == head), None)
    if (type(current) is not dict or current.get('state') != 'DISPATCHED' or
            current.get('repair_state') != 'START_FAILED'):
        return None
    repair_task = current.get('repair_task_id')
    if type(repair_task) is not str or re.fullmatch(r'[0-9a-f]{32}', repair_task) is None:
        return None
    return {'repair_task_id': repair_task}


def derive_repair_state(repair_record):
    """Normalize a repair Task record into the state used by the advance loop.

    START_FAILED (#2188 section 2 checks 1-5, record only): the Claude delegate
    stopped before its provider process started. The PM decides whether to run
    `task retry-start`; advance never retries it and never spends a repair on it.
    """
    if type(repair_record) is not dict:
        return 'UNKNOWN'
    developer = repair_record.get('developer')
    state = developer.get('state') if type(developer) is dict else None
    # This failure updates only the developer field, so check it before the
    # top-level state, which may still read PM_PENDING.
    if state == 'BLOCKED' and developer.get('error_code') == 'CLAUDE_NO_CHANGES':
        return 'BLOCKED'
    if state == 'BLOCKED' and start_failure.record_checks(repair_record).start_failure:
        return 'START_FAILED'
    from . import direct_reassignment
    if direct_reassignment.replacement_candidate(developer):
        return 'RECOVERABLE_DIRECT'
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


def unknown_repair_reason(repair_record, claim, d6, pr):
    """#2325: why a launched repair with an unknown outcome may not be retried.

    Returns None only when every fact shows the run ended and left nothing
    behind: the repair Developer state is DEVELOPER_OUTCOME_UNKNOWN, its
    execution cleanup is CONFIRMED, the D6 is clean and both its observed and
    recorded result heads are the claim head, and the PR head (when known) is
    the claim head. Anything else returns the fixed reason code, and the claim
    stays REPAIR_OUTCOME_UNKNOWN. A repair Task that never reached a Developer
    is never retried here (#2492 item 1 = B; its recovery is separate).
    """
    developer = repair_record.get('developer') if type(repair_record) is dict else None
    if type(developer) is not dict or developer.get('state') != 'DEVELOPER_OUTCOME_UNKNOWN':
        return 'REPAIR_NOT_LAUNCHED_OR_STATE_CHANGED'
    execution = developer.get('execution')
    if type(execution) is not dict or execution.get('cleanup_status') != 'CONFIRMED':
        return 'REPAIR_CLEANUP_NOT_CONFIRMED'
    head = claim.get('head_sha') if type(claim) is dict else None
    if (type(head) is not str or type(d6) is not dict or d6.get('dirty') is not False or
            d6.get('result_head') != head or d6.get('recorded_result_head') != head):
        return 'REPAIR_D6_DIRTY_OR_MOVED'
    if pr is not None:
        pr_head = pr.get('head') if type(pr) is dict else None
        if type(pr_head) is not dict or pr_head.get('sha') != head:
            return 'REPAIR_PR_HEAD_MOVED'
    return None


def _unknown_retry_allowed(claim):
    """True only when the related claim carries a checked None unknown_reason."""
    return (type(claim) is dict and 'unknown_reason' in claim and
            claim['unknown_reason'] is None)


def _unknown_repair_detail(related, head):
    """#2325: the reason and the next PM command for a REPAIR_OUTCOME_UNKNOWN stop."""
    repairs = related.get('repair_claims', []) if type(related) is dict else []
    current = next((item for item in reversed(repairs if type(repairs) is list else [])
                    if type(item) is dict and item.get('head_sha') == head), None)
    if (type(current) is not dict or current.get('state') != 'DISPATCHED' or
            current.get('repair_state') != 'UNKNOWN'):
        return None
    reason = current.get('unknown_reason') or 'REPAIR_OUTCOME_CHECK_FAILED'
    repair_task = current.get('repair_task_id')
    if type(repair_task) is not str or re.fullmatch(r'[0-9a-f]{32}', repair_task) is None:
        return {'reason': reason}
    return {'reason': reason, 'repair_task_id': repair_task,
            'next_command': f'llmtool task brief {repair_task}'}


def _pr_open_for_head(pr, number, head):
    """Return True only when pr is an open, unmerged PR for number at head."""
    pr_head = pr.get('head') if type(pr) is dict else None
    return (type(pr_head) is dict and pr_head.get('sha') == head and
            pr.get('number') == number and
            pr.get('state') == 'open' and pr.get('merged') is False)


# GitHub's pull request files statuses; a rename or copy must also name its old
# path, or the file list cannot prove every changed path is documentation.
_PR_FILE_STATUSES = frozenset(('added', 'removed', 'modified', 'renamed',
                               'copied', 'changed', 'unchanged'))
_PR_RENAME_STATUSES = frozenset(('renamed', 'copied'))


def _pr_docs_only(number, head, github_get):
    """#2473: True only when GitHub proves every changed path of the PR is documentation.

    The complete file list is read for this PR number, both paths of every
    rename are classified with the CI path classifier, the list must match the
    PR's own file count, and the PR must be open and unmerged at the exact head
    both before and after the read. Any doubt returns False, which keeps the EC2
    decision.
    """
    try:
        if type(number) is not int or type(head) is not str or _HEX40.fullmatch(head) is None:
            return False
        before = github_get(f'pulls/{number}')
        if not _pr_open_for_head(before, number, head):
            return False
        count = before.get('changed_files')
        files, files_code = _pr_files(number, github_get)
        if files_code is not None or type(files) is not list or not files:
            return False
        if type(count) is not int or isinstance(count, bool) or count != len(files):
            return False
        names, seen = [], set()
        for item in files:
            if type(item) is not dict:
                return False
            path, status = item.get('filename'), item.get('status')
            if (type(path) is not str or not path or path in seen or
                    type(status) is not str or status not in _PR_FILE_STATUSES):
                return False
            seen.add(path)
            names.append(path)
            previous = item.get('previous_filename')
            if status in _PR_RENAME_STATUSES and type(previous) is not str:
                return False
            if previous is not None:
                if (type(previous) is not str or not previous or previous in seen):
                    return False
                seen.add(previous)
                names.append(previous)
        if any('\0' in name for name in names):
            return False
        classify = _ci_path_classes().classify_path
        if any(classify(name) != 'docs' for name in names):
            return False
        after = github_get(f'pulls/{number}')
        if not _pr_open_for_head(after, number, head):
            return False
        after_count = after.get('changed_files')
        return (type(after_count) is int and not isinstance(after_count, bool) and
                after_count == len(files))
    except Exception:
        return False


# #2473: steps the pre-review EC2 record can produce; a docs-only PR re-decides them.
_EC2_DERIVED_STEPS = frozenset((
    'RUN_REMOTE_TEST', 'REPAIR_REVIEW', 'NEEDS_ATTENTION', 'WAITING_OWNER',
    'WAITING_REPAIR', 'REPAIR_RETRY', 'DEVELOPER_START_FAILED',
    'REPAIR_OUTCOME_UNKNOWN'))


def _remote_test_waiting_result(task_id, number, d6, remote_status):
    """Codes and result for a repeated timeout or unreachable-runner wait."""
    reason = ('REMOTE_TEST_UNREACHABLE' if remote_status == 'UNREACHABLE'
              else 'REMOTE_TEST_TIMEOUT')
    head = d6.get('result_head') if type(d6) is dict else None
    return [reason, 'OWNER_NOTICE'], {
        'owner_notice': {'task_id': task_id, 'pr': number, 'head': head,
                         'reason': reason}}


def _pre_review_action(record, d6, pr, related, result_head, docs_only=False):
    """Return the gate action before a Reviewer may start on this head.

    docs_only (#2473) is set only after _pr_docs_only proved the PR changes
    documentation alone: no EC2 result is required, and the review path's
    exact-head check (docs) gate still runs before any Reviewer is dispatched.
    """
    remote_status, _, _ = (None, None, None) if docs_only else _remote_test_status(record, d6)
    # #2602 item k: the first repeat reruns on this commit; the second waits
    # for the Owner. Neither a timeout nor an unreachable runner is a repair.
    if remote_status == 'TIMEOUT':
        if _remote_test_timeout_count(record, d6) == 1:
            return 'RUN_REMOTE_TEST'
        return 'WAITING_OWNER'
    if remote_status == 'UNREACHABLE':
        if _remote_test_unreachable_count(record, d6) == 1:
            return 'RUN_REMOTE_TEST'
        return 'WAITING_OWNER'
    if remote_status == 'SHA_MISMATCH':
        return 'NEEDS_ATTENTION'
    if remote_status == 'NOT_EXECUTED':
        return 'NEEDS_ATTENTION'
    if remote_status == 'FAIL':
        return _remote_fail_action(related, result_head)
    if type(pr) is dict and pr.get('merged') is True:
        # The code is already merged; a Reviewer or EC2 run must never start for it.
        return 'PR_MERGED_NO_REVIEW'
    if not docs_only and remote_status not in ('PASS', 'ADMISSION_DENIED'):
        return 'RUN_REMOTE_TEST'
    return 'DISPATCH_REVIEW'


def decide(record, d6, pr, related, docs_only=False):
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
    chosen_pr = pr.get('number') if type(pr) is dict else None
    pr_head = pr.get('head') if type(pr) is dict else None
    if (type(chosen_pr) is not int or not 1 <= chosen_pr <= 9999999 or
            type(pr_head) is not dict or pr_head.get('sha') != result_head or
            (pr.get('state') == 'closed' and pr.get('merged') is not True)):
        return 'PUBLISH'
    published = record.get('published_pr')
    if (type(published) is dict and published.get('pr') == chosen_pr and
            published.get('head_sha') != result_head):
        return 'PUBLISH'

    matching = [claim for claim in claims if type(claim) is dict and
                claim.get('pr') == chosen_pr and claim.get('head_sha') == result_head]
    if not matching:
        return _pre_review_action(record, d6, pr, related, result_head, docs_only)
    claim = max(matching, key=lambda item: item.get('version', 0))
    if claim.get('state') == 'UNKNOWN' and _failure_reason(claim.get('failure_reason')):
        # #2486: advance may re-run the newest stored refusal once even when
        # older claims exist on the head; decide() itself only names the stop.
        if sum(type(item) is dict and item.get('head_sha') == result_head
               for item in claims) > 1:
            return 'REVIEW_UNUSABLE_STOPPED'
        action = _pre_review_action(record, d6, pr, related, result_head, docs_only)
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
                    current_repair.get('repair_state') == 'START_FAILED'):
                return 'DEVELOPER_START_FAILED'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') in (
                        'UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED')):
                return 'REPAIR_RETRY'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') in ('RUNNING', 'PENDING')):
                return 'WAITING_REPAIR'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'UNKNOWN'):
                # #2325: same rule as the EC2 path.
                return ('REPAIR_RETRY' if _unknown_retry_allowed(current_repair)
                        else 'REPAIR_OUTCOME_UNKNOWN')
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


# #1831 P1: pre-launch refusals that mean the default Reviewer route is unavailable.
_REVIEWER_ROUTE_UNAVAILABLE = ('ROLE_MODEL_NOT_ALLOWED', 'REVIEWER_ACCOUNT_UNCONFIGURED',
                               'REVIEWER_NOT_ALLOWED')


def _reviewer_fallback(record, exc):
    """#1831 P1: return (alternate Reviewer or None, code) for a route refusal.

    None when the refusal is not a route-unavailable code. The alternate is the
    other formal Reviewer key, and only when review_dispatch's own family and
    policy check admits it; dispatch_review(reviewer=alt) checks it again.
    """
    code = getattr(exc, 'review_dispatch_code', None)
    if code not in _REVIEWER_ROUTE_UNAVAILABLE:
        return None
    family = task_completion._reviewer_author_family(record.get('developer'))
    alternate = 'claude-reviewer' if family == 'claude' else 'codex-reviewer'
    try:
        review_dispatch._select_reviewer(record, alternate)
    except Exception:
        return None, code
    return alternate, code


def _blocked_fallback_reviewer(record, head):
    """#1831 P2: the other eligible Reviewer while the head has one claim, else None."""
    if sum(type(item) is dict and item.get('head_sha') == head
           for item in _claims(record)) != 1:
        return None
    family = task_completion._reviewer_author_family(record.get('developer'))
    alternate = 'claude-reviewer' if family == 'claude' else 'codex-reviewer'
    try:
        review_dispatch._select_reviewer(record, alternate)
    except Exception:
        return None
    return alternate


def _refusal(exc, attribute):
    code = getattr(exc, attribute, None)
    if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code):
        return code
    return 'TASK_ADVANCE_REFUSED'


def _pr_number(record, requested):
    if requested is not None:
        return requested
    published = record.get('published_pr')
    number = published.get('pr') if type(published) is dict else None
    if type(number) is int and 1 <= number <= 9999999:
        return number
    claims = _claims(record)
    return claims[-1].get('pr') if claims and type(claims[-1]) is dict else None


def _d6_snapshot(record, executor_task, allow_cwd_fallback=False, cwd=None):
    completion = record.get('completion') or {}
    evidence = completion.get('evidence') or {}
    common_dir = evidence.get('common_dir')
    if type(common_dir) is not str and allow_cwd_fallback:
        try:
            cmd = ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir']
            proc = subprocess.run(
                cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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
            # #2534: the integrator-registered D6 start (registry key base_sha,
            # D6Registration.source_sha); publish scope uses it.
            'base_sha': registration.source_sha,
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
        if type(claim) is not dict or result_module.review_kind(claim) != 'formal':
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
        # #1132 item 1: as in decide(), only the newest claim at this PR and head
        # counts. A newer claim without a PASS (open, failed or blocked) keeps an
        # older PASS from closing the Task; a newer PASS returned above.
        if any(type(other) is dict and other.get('pr') == number and
               other.get('head_sha') == head and type(other.get('version')) is int and
               other['version'] > version for other in claims):
            return None

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


def _stored_merge_replay(record, number, head, pr):
    """The replay result for an already merged Task, or None.

    Only when the stored merge_attempt is MERGED for exactly this PR and head
    and the PR snapshot GitHub returned is merged at that same head; anything
    else returns None and the normal merge gate decides (fail closed).
    """
    attempt = record.get('merge_attempt') if type(record) is dict else None
    if (type(attempt) is not dict or attempt.get('state') != 'MERGED' or
            type(number) is not int or attempt.get('pr') != number or
            type(head) is not str or attempt.get('head_sha') != head):
        return None
    pr_head = pr.get('head') if type(pr) is dict else None
    if (type(pr) is not dict or pr.get('number', number) != number or
            pr.get('merged') is not True or type(pr_head) is not dict or
            pr_head.get('sha') != head):
        return None
    return {'step': 'MERGED', 'codes': [],
            'result': {'pr': number, 'head': head, 'replay': True}}


def _lineage_after_merge(result, store, task_id, record, *, merged_now=False):
    """#1502 WP-D D10f2: close a merged lineage Task once; the merge result never changes.

    Runs only after the merge_attempt is MERGED: a merge this call made
    (merged_now) or a stored MERGED attempt (replay). A Task without lineage
    makes no call. lineage_close_merged closes an OPEN member with no Owner
    notice and rolls up its PARKED ROOT; on a CLOSED member it only retries a
    failed roll-up, so a replay never closes twice. Its result, None or
    LINEAGE_CLOSE_FAILED, is reported under result['lineage'].
    """
    if type(record) is not dict or type(record.get('lineage')) is not dict:
        return result
    attempt = record.get('merge_attempt')
    if not merged_now and (type(attempt) is not dict or attempt.get('state') != 'MERGED'):
        return result
    try:
        closed = task_submission_cli.lineage_close_merged(store, task_id)
    except Exception:
        closed = {'code': 'LINEAGE_CLOSE_FAILED', 'cause': 'UNKNOWN'}
    result['lineage'] = closed
    if type(closed) is dict and closed.get('code') == 'LINEAGE_CLOSE_FAILED':
        result.setdefault('codes', []).append('LINEAGE_CLOSE_FAILED')
    return result


def _advance_refused(task_id, code):
    return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
            'next': 'REFUSED', 'codes': [code], 'acked': []}


# #1781 (security v91, I4): split vs advance. Every external action of one
# advance step re-reads the Task under its update lock right before it runs
# (TaskStore.check_external_action). PR #1794 v91 F1 (CWE-362): the check is an
# allowlist (task_store.external_action_refusal): only a Task with no lineage or
# with its own lineage OPEN passes; PENDING, PARKED, ended and unknown states are
# refused. A split's ROOT PARKED commit takes the ROOT's advance lease
# (task_store.commit_split_root), so no park lands while an advance of the ROOT
# runs, and an advance started after it refuses before any action. Reads, and the record comment of an action this call already took, are never
# refused, so a refusal never leaves a half-done action unrecorded. Every other
# GitHub mutation is re-checked, including the publish-recovery `pr close`
# (PR #1789 codex-reviewer v1 F1): a refused close leaves the PR open and returns
# the refusal (_publish_created_pr). A lineage end takes the advance lease
# exclusively (task_store.end_lineage), so no end lands while an advance
# holding that lease runs.
_EXTERNAL_GIT = frozenset(('add', 'commit', 'merge', 'push'))
_READ_OR_CLEANUP_GH = (['pr', 'list'], ['pr', 'view'], ['api', 'user'],
                       # The merge claim, a locked compare-and-set that the store
                       # refuses for a PENDING or ended Task, is the re-check of
                       # the merge.
                       ['pr', 'merge'])
_RECORD_COMMENT_PREFIXES = (_ACTIVITY_MARKER + '\n',
                            'llmtool task advance merge provenance: ')


class _ExternalActionRefused(BaseException):
    """The per-action re-check refused; it ends the advance step with its code.

    A BaseException, so no step's ``except Exception`` turns it into another
    code or goes on to the action it stopped.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _external_action_guard(store, task_id):
    """Return guard(): re-check the Task under its update lock, else raise."""
    check = getattr(store, 'check_external_action', None)

    def guard():
        if not callable(check):
            return  # an injected test double; TaskStore always has the check
        try:
            check(task_id)
        except Exception as exc:
            code = getattr(exc, 'task_store_error_code', None)
            raise _ExternalActionRefused(
                code if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code)
                else 'TASK_RECORD_UNAVAILABLE') from None
    return guard


def _external_command(args, *, gh):
    """True for a git or gh command that starts an external action for the Task."""
    if type(args) not in (list, tuple) or not args or any(type(a) is not str for a in args):
        return True
    args = list(args)
    if not gh:
        return args[0] in _EXTERNAL_GIT and args != ['merge', '--abort']
    if args[:2] in _READ_OR_CLEANUP_GH:
        return False
    if args[:2] == ['pr', 'comment'] and '--body' in args[:-1]:
        body = args[args.index('--body') + 1]
        if body.startswith(_RECORD_COMMENT_PREFIXES):
            return False
    return True


def _guarded_runner(guard, runner, *, gh):
    def run(root, args, *rest, **kwargs):
        if _external_command(args, gh=gh):
            guard()
        return runner(root, args, *rest, **kwargs)
    return run


def _guarded_call(guard, call):
    def run(*args, **kwargs):
        guard()
        return call(*args, **kwargs)
    return run


def advance(*, task_id, store=None, merge=False, owner_acceptance=None,
            owner_exception=None, executor_task=None, dispatch_review_call=None,
            repair_review_call=None, publish_git_runner=None, publish_gh_runner=None,
            merge_gh_runner=None, **options):
    """Perform one selected action and return a bounded JSON-ready summary.

    #1781 (security v91): the step runs holding the Task's advance lease, so no
    split reserves the Task while it runs, and every external action re-checks
    the Task under its update lock right before it runs.
    """
    if owner_acceptance is not None and not merge:
        # An Owner acceptance is only for a merge; refuse before anything runs.
        return _advance_refused(task_id, 'OWNER_ACCEPTANCE_REQUIRES_MERGE')
    if executor_task is not None and (not merge or owner_exception is None):
        return _advance_refused(task_id, 'OWNER_EXCEPTION_EXECUTOR_REQUIRES_EXCEPTION')
    if owner_exception is not None and not merge:
        return _advance_refused(task_id, 'OWNER_EXCEPTION_REQUIRES_MERGE')
    store = TaskStore() if store is None else store
    with contextlib.ExitStack() as held:
        lease = getattr(store, 'advance_lease', None)
        if callable(lease):
            try:
                held.enter_context(lease(task_id))
            except Exception as exc:
                code = getattr(exc, 'task_store_error_code', None)
                return _advance_refused(task_id, 'TASK_ADVANCE_LEASE_BUSY'
                                        if code == 'TASK_ADVANCE_LEASE_BUSY'
                                        else 'TASK_RECORD_UNAVAILABLE')
        guard = _external_action_guard(store, task_id)
        try:
            return _advance_step(
                task_id=task_id, store=store, merge=merge, guard=guard,
                owner_acceptance=owner_acceptance, owner_exception=owner_exception,
                executor_task=executor_task,
                dispatch_review_call=_guarded_call(
                    guard, dispatch_review_call or review_dispatch.dispatch_review),
                repair_review_call=_guarded_call(
                    guard, repair_review_call or review_repair.repair_review),
                publish_git_runner=_guarded_runner(
                    guard, publish_git_runner or _publish_run_git, gh=False),
                publish_gh_runner=_guarded_runner(
                    guard, publish_gh_runner or _publish_run_gh, gh=True),
                merge_gh_runner=_guarded_runner(
                    guard, merge_gh_runner or _publish_run_gh, gh=True),
                # The label helper keeps the caller's own runner: it re-checks the
                # Task itself (I4) right before its `pr edit --add-label mac-ci`.
                label_gh_runner=merge_gh_runner,
                **options)
        except _ExternalActionRefused as refused:
            return _advance_refused(task_id, refused.code)


def _advance_step(*, task_id, store, guard, pr_number=None, integration_pr=None,
                  merge=False, reviewer_session_done=False, dispatch_review_call,
                  record_review_result_call=None, repair_review_call, relay_verdict_call=None,
                  run_task_complete_call=None, record_github_event_call=None,
                  github_get=None, cwd=None, publish_git_runner, publish_gh_runner,
                  record_result_call=None, merge_gh_runner, label_gh_runner=None,
                  owner_acceptance=None, owner_exception=None, executor_task=None,
                  sleep_fn=None, sleep=None):
    """One advance step; the caller holds the advance lease and passes guarded runners."""
    try:
        record = store.get(task_id)
    except Exception:
        return _advance_refused(task_id, 'TASK_RECORD_UNAVAILABLE')
    lineage = record.get('lineage') if isinstance(record, dict) else None
    lineage_refusal = external_action_refusal(record)
    if lineage_refusal == 'TASK_LINEAGE_PENDING':
        # #1781 (I4): a split reservation made before this advance took its lease;
        # refuse before any D6, PR, review, claim or GitHub read.
        if merge:
            return dict(_merge_refusal('TASK_LINEAGE_PENDING'), task_id=task_id,
                        next='MERGE_REFUSED', acked=[])
        return _advance_refused(task_id, 'TASK_LINEAGE_PENDING')
    if merge and lineage_refusal is not None:
        # #1502 WP-D D10: refuse before any D6, PR, review or claim read.
        code = _MERGE_LINEAGE_CODES.get(lineage_refusal, 'MERGE_LINEAGE_UNKNOWN')
        refused = dict(_merge_refusal(code), task_id=task_id, next='MERGE_REFUSED', acked=[])
        if type(lineage) is dict and lineage.get('state') == 'CLOSED':
            # D10f2 replay: an already merged, CLOSED member retries a failed roll-up.
            _lineage_after_merge(refused, store, task_id, record)
        return refused
    if lineage_refusal is not None and not (
            type(lineage) is dict and lineage.get('state') == 'CLOSED'):
        # PR #1789 v91 F1 (CWE-841): a DROPPED, STOPPED or FAILED_FINAL Task takes
        # no further step; PR #1794 v91 F1 (I4 allowlist): nor does a PARKED ROOT
        # (TASK_LINEAGE_PARKED) or a Task in any unknown lineage state
        # (TASK_LINEAGE_UNKNOWN). Refused before any D6, PR, review or GitHub
        # read. A CLOSED Task keeps its read-only record path; the per-action
        # guard refuses its every external action.
        return _advance_refused(task_id, lineage_refusal)
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
    # #1817 (Codex v995 on PR #1514, CWE-863): an owner-exception merge always
    # checks the executor identity, whether or not --executor-task was given.
    exception_merge = bool(merge and owner_exception is not None)
    executor_task = active_executor
    number = _pr_number(record, pr_number)
    get = task_completion._github_get if github_get is None else github_get
    if using_executor_task:
        d6 = _d6_snapshot(record, executor_task, allow_cwd_fallback=True, cwd=cwd)
    else:
        d6 = _d6_snapshot(record, executor_task) if type(executor_task) is str else None
    recovered_pr = False
    if (number is None and type(d6) is dict and d6.get('dirty') is False and
            type(d6.get('root')) is str and d6.get('root') and
            type(d6.get('branch')) is str and d6.get('branch') and
            type(d6.get('result_head')) is str and
            d6.get('recorded_result_head') == d6.get('result_head')):
        number = _find_published_pr(
            _publish_run_gh if publish_gh_runner is None else publish_gh_runner,
            d6['root'], d6['branch'], d6['result_head'])
        recovered_pr = number is not None
    try:
        pr = _pr_snapshot(number, get) if number is not None else None
        if type(pr) is dict and pr.get('number') != number:
            pr = None
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
        extra = {}
        if repair_state == 'UNKNOWN' and claim.get('state') == 'DISPATCHED':
            try:
                extra['unknown_reason'] = unknown_repair_reason(repair_record, claim, d6, pr)
            except Exception:
                extra['unknown_reason'] = 'REPAIR_OUTCOME_CHECK_FAILED'
        repair_claims.append(dict(claim, repair_state=repair_state, **extra))
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
            closed = _lineage_after_merge(
                {'task_id': task_id, 'step': 'CLOSED', 'result': None,
                 'next': 'CLOSED', 'codes': []}, store, task_id, record)
            return _ack_consumed_events(
                closed, record, store,
                d6.get('result_head') if type(d6) is dict else None)
        except Exception as exc:
            return _ack_consumed_events(
                {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                 'next': 'REFUSED', 'codes': [_refusal(exc, 'completion_code') if
                     getattr(exc, 'completion_code', None) else
                     _refusal(exc, 'task_store_error_code')]}, record, store,
                d6.get('result_head') if type(d6) is dict else None)
    if merge:
        if exception_merge:
            if type(executor_task) is not str or not executor_task:
                # #1817: no completion executor and no --executor-task; the
                # executor identity cannot be established, so nothing merges.
                return _ack_consumed_events(
                    _merge_refusal('OWNER_EXCEPTION_EXECUTOR_UNKNOWN'),
                    record, store, None)
            if (type(d6) is not dict or d6.get('state') == 'D6_BINDING_UNAVAILABLE' or
                    d6.get('task_id') != executor_task or
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
        replay = _stored_merge_replay(record, number, d6['result_head'], pr)
        if replay is not None:
            # A stored MERGED attempt whose PR GitHub reports merged at that head:
            # nothing merges again; only the lineage close is replayed (D10f2), so
            # a close that failed after the merge (e.g. LINEAGE_LOCK_BUSY) can be
            # retried. lineage_close_merged is idempotent.
            replay.update(task_id=task_id, next=replay['step'])
            _lineage_after_merge(replay, store, task_id, record)
            return _ack_consumed_events(replay, record, store, d6['result_head'])
        merge_result = _merge_once(store=store, record=record, number=number,
            head=d6['result_head'], pr=pr, events=related['events'], github_get=get,
            gh_runner=_publish_run_gh if merge_gh_runner is None else merge_gh_runner,
            root=d6.get('root') or cwd or os.getcwd(), owner_acceptance=owner_acceptance,
            owner_exception=owner_exception,
            sleep_fn=sleep_fn, sleep=sleep)
        if (owner_exception is None and type(number) is int and
                merge_result.get('step') == 'MERGE_REFUSED' and
                merge_result.get('codes') == ['MERGE_CHECKS_NOT_GREEN']):
            # #1502 WP-D D11: a failed exact-head Mac check after PASS goes to repair.
            mac_run = _newest_check_run(d6['result_head'], _MAC_CHECK, get)
            routed = None if mac_run is None else _route_ci_failure(
                store=store, record=record, task_id=task_id,
                executor_task=executor_task, number=number, d6=d6, related=related,
                run=mac_run, fail_code='MAC_CI_FAILED', github_get=get,
                repair_review_call=repair_review_call)
            if routed is not None:
                merge_result = {'step': routed[0], 'codes': list(routed[1]),
                                'result': routed[2]}
        merge_result.update(task_id=task_id, next=merge_result['step'])
        if merge_result.get('step') == 'MERGED':
            _lineage_after_merge(merge_result, store, task_id, record, merged_now=True)
        return _ack_consumed_events(merge_result, record, store, d6['result_head'])
    if type(d6) is dict and d6.get('state') == 'D6_BINDING_UNAVAILABLE':
        code = d6.get('code') if type(d6.get('code')) is str else 'D6_BINDING_UNAVAILABLE'
        return _ack_consumed_events(
            {'task_id': task_id, 'step': 'D6_BINDING_UNAVAILABLE', 'result': None,
             'next': 'D6_BINDING_UNAVAILABLE', 'codes': [code]}, record, store, None)
    step = decide(record, d6, pr, related)
    docs_only = False
    if step in _EC2_DERIVED_STEPS and _pr_docs_only(number, d6['result_head'], get):
        # #2473: a docs-only PR needs no EC2 result, whatever the EC2 record
        # says (none, FAIL, TIMEOUT, SHA_MISMATCH). The check (docs) gate below
        # still runs before any Reviewer is dispatched. A head with a Reviewer
        # claim decides identically with docs_only, so those steps are unchanged.
        step = decide(record, d6, pr, related, docs_only=True)
        docs_only = True
    # A crash after GitHub creates the PR can leave the Task without its binding.
    # Only an exact-head open PR recovered from this D6 branch may fill it.
    if (recovered_pr and step not in ('PUBLISH', 'DEVELOPER_ATTENTION') and
            pr.get('state') == 'open'):
        try:
            store.record_published_pr(task_id, number, d6['result_head'])
        except Exception:
            return {'task_id': task_id, 'step': 'REFUSED', 'codes': ['PUBLISH_PR_RECORD_FAILED'],
                    'pr': number, 'head': d6['result_head']}
    result, codes, refusal_dict = None, [], None
    redispatch_reason = None
    rerun_version = None
    rerun_claim = None
    rerun_contract = None
    rerun_refusal_code = None
    if step in ('REVIEW_REDISPATCH', 'REVIEW_UNUSABLE_STOPPED'):
        # #2486: a stored refusal may be re-run exactly once even when older
        # claims exist on the head. The receipt proves the refusal and records
        # the re-run, so it can never repeat.
        candidate = _head_review_claim(record, number, d6['result_head'])
        rerun_version, rerun_refusal_code = _review_refusal_rerun(
            record, candidate, d6, pr, store, get)
        if rerun_version is not None:
            step = 'REVIEW_REDISPATCH'
            binding = record.get('task_binding')
            rerun_claim = candidate
            rerun_contract = (binding.get('contract_digest')
                              if type(binding) is dict else None)
            redispatch_reason = _failure_reason(candidate.get('failure_reason'))
        elif rerun_refusal_code is not None:
            step = 'REVIEW_UNUSABLE_STOPPED'
    if step == 'REVIEW_REDISPATCH':
        unusable = _head_review_claim(record, number, d6['result_head'])
        if rerun_version is not None:
            step = 'DISPATCH_REVIEW'
        elif unusable is not None and _review_verdict_absent(unusable, get):
            step = 'DISPATCH_REVIEW'
            redispatch_reason = _failure_reason(unusable.get('failure_reason'))
        else:
            step = 'REVIEW_OUTCOME_UNKNOWN'
    blocked_reviewer = None
    if step == 'REVIEWER_ATTENTION' and not reviewer_session_done:
        # #1831 P2: one redispatch of version N+1 with the other eligible Reviewer.
        blocked_reviewer = _blocked_fallback_reviewer(record, d6['result_head'])
        if blocked_reviewer is not None:
            step = 'DISPATCH_REVIEW'
            codes.extend(('REVIEW_BLOCKED', 'REVIEWER_FALLBACK'))
    changed_modules = frozenset()
    changed_gate = False
    if step == 'DISPATCH_REVIEW' and not docs_only and type(number) is int:
        # #2491: the pre-review EC2 gate covers the Task's declared test
        # modules plus every runnable test module the PR adds or changes at its
        # exact head. A PR whose changed-file data is missing, unreadable,
        # incomplete or not for the exact head is not satisfied: no review is
        # dispatched and the explicit reader code is reported. Readable data
        # that adds a module is decided after the CI gate, so a CI failure
        # keeps its existing repair or stop path.
        changed_modules, files_code = _pr_changed_test_modules(
            number, d6['result_head'], get)
        if files_code is not None:
            step = 'EC2_GATE_UNSATISFIED'
            codes.append(files_code)
        elif changed_modules:
            changed_gate = True
    if step == 'DISPATCH_REVIEW' and type(number) is int:
        gate_latest = {}
        gate_step, ci_code = _review_ci_gate(number, d6['result_head'], get,
                                             latest_out=gate_latest)
        if gate_step is not None:
            step = gate_step
            if ci_code is not None:
                codes.append(ci_code)
        failed_name = gate_latest.get('name')
        if (step == 'CI_FAILED' and failed_name in _LINUX_FLAG_CHECKS and
                gate_latest.get('conclusion') in ('failure', 'timed_out', *_CI_ENV_CONCLUSIONS)):
            # #1502 WP-D D11, #2337: route the newest exact-head required check run before review.
            check_run = _read_check_run(d6['result_head'], failed_name,
                                        gate_latest.get('id'), get)
            if check_run is None:
                codes.append('CI_CHECK_RUN_UNAVAILABLE')
            else:
                routed = _route_ci_failure(
                    store=store, record=record, task_id=task_id,
                    executor_task=executor_task, number=number, d6=d6, related=related,
                    run=check_run, fail_code='LINUX_CI_FAILED', github_get=get,
                    repair_review_call=repair_review_call)
                if routed is not None:
                    step, codes, result = routed[0], list(routed[1]), routed[2]
    if changed_gate and step == 'DISPATCH_REVIEW':
        declared = remote_test.remote_required_modules(record)
        required = remote_test.remote_required_modules(
            record, changed_modules=changed_modules)
        if set(required) - set(declared):
            remote_status, _, _ = _remote_test_status(
                record, d6, required_modules=required)
            if remote_status != 'PASS':
                step = 'RUN_REMOTE_TEST'
    if step == 'RUN_REMOTE_TEST':
        guard()  # #1781: an EC2 run is an external action
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
        rerun_recorded = True
        if rerun_version is not None:
            # Record the one re-run before starting it. A crash after this point
            # cannot repeat it for the same refusal.
            rerun_recorded = review_dispatch.mark_refusal_rerun(
                store=store, task_id=task_id, claim=rerun_claim,
                contract_digest=rerun_contract, rerun_version=rerun_version)
        if not rerun_recorded:
            step = 'REVIEW_UNUSABLE_STOPPED'
            codes.append(REVIEW_RERUN_RECORD_FAILED)
        else:
            claims = _claims(record)
            versions = [c.get('version', 0) for c in claims if type(c) is dict and
                        type(c.get('version')) is int]
            version = (rerun_version if rerun_version is not None
                       else max(versions, default=0) + 1)
            invoke = dispatch_review_call or review_dispatch.dispatch_review
            if redispatch_reason is not None:
                codes.extend(('REVIEW_REDISPATCHED', redispatch_reason))
            rerun_kwargs = ({} if rerun_version is None else {'refusal_rerun': True})
            try:
                try:
                    result = invoke(store=store, task_id=task_id, executor_task=executor_task,
                                    pr_number=number, version=version,
                                    **({} if blocked_reviewer is None else
                                       {'reviewer': blocked_reviewer}),
                                    **rerun_kwargs)
                except Exception as exc:
                    # A P2 redispatch already uses the alternate: no P1 fallback on top.
                    fallback = None if blocked_reviewer else _reviewer_fallback(record, exc)
                    if fallback is None:
                        raise
                    # #1831 P1: one fallback per step, through dispatch_review only.
                    alternate, original = fallback
                    codes.append(original)
                    if alternate is not None:
                        codes.append('REVIEWER_FALLBACK')
                        try:
                            result = invoke(store=store, task_id=task_id,
                                            executor_task=executor_task, pr_number=number,
                                            version=version, reviewer=alternate,
                                            **rerun_kwargs)
                        except Exception as alt_exc:
                            if (getattr(alt_exc, 'review_dispatch_code', None) not in
                                    _REVIEWER_ROUTE_UNAVAILABLE):
                                # Security v91 F1: any other refusal of the alternate keeps
                                # the existing handling (failure reason, refusal receipt).
                                raise
                            codes.append(_refusal(alt_exc, 'review_dispatch_code'))
                            alternate = None
                    if alternate is None:
                        step = 'REVIEWER_UNAVAILABLE'
                        codes.append('REVIEWER_UNAVAILABLE')
            except Exception as exc:
                codes.append(_refusal(exc, 'review_dispatch_code'))
                failure = _failure_reason(getattr(exc, 'review_failure_reason', None))
                if failure is not None:
                    codes.append(failure)
                refusal_dict = _well_formed_review_refusal(getattr(exc, 'review_refusal', None))
        if type(result) is dict:
            review_text = result.get('review_text')
            if (result.get('state') == 'COMPLETED' and
                    type(review_text) is str and review_text and
                    _caller_posts_verdict(result)):
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
            # First check for a posted comment without creating a
            # REVIEW_BLOCKED event for a missing verdict.
            review_args = dict(store=store, task_id=task_id, executor_task=executor_task,
                               pr_number=number, version=claim['version'],
                               reviewer_session_done=False, github_get=get)
            result = invoke(**review_args)
            relay_codes, relayed = [], False
            if (type(result) is dict and result.get('state') == 'NOT_READY' and
                    claim.get('state') == 'DISPATCHED'):
                # #1568: a read-only Claude Reviewer cannot post its own verdict.
                # Post its one claim-bound transcript block unedited, then re-read.
                _, relay_code, block = (relay_verdict_call or
                                        review_transcript.unposted_verdict)(claim, record)
                if relay_code is not None:
                    relay_codes = ['REVIEWER_VERDICT_UNPOSTED', relay_code]
                elif block is not None:
                    try:
                        _post_review_verdict_comment(
                            _publish_run_gh if publish_gh_runner is None else publish_gh_runner,
                            d6.get('root') or cwd or os.getcwd(), number, block)
                    except _PublishRefusal:
                        relay_codes = ['REVIEWER_VERDICT_UNPOSTED',
                                       'REVIEW_VERDICT_COMMENT_FAILED']
                    else:
                        relayed, result = True, invoke(**review_args)
            if relay_codes:
                step = 'REVIEW_NOT_POSTED'
                codes.extend(relay_codes)
            elif relayed and (type(result) is not dict or result.get('state') == 'NOT_READY'):
                # Posted but not read back yet: stop instead of posting twice.
                step = 'REVIEW_OUTCOME_UNKNOWN'
                codes.append('REVIEWER_VERDICT_RELAY_UNCONFIRMED')
            elif type(result) is dict and result.get('state') == 'REVIEW_BLOCKED':
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
                remote_status, _, failure_receipt = _remote_test_status(record, d6)
                if (step == 'REPAIR_REVIEW' and remote_status == 'FAIL' and
                        _test_family_used_up(record, number, d6['result_head'], store)):
                    # #1502 WP-D D11: the shared EC2/CI count is used up.
                    step, codes = 'STOPPED', ['TEST_FAIL_LIMIT_REACHED']
                if step in ('REPAIR_REVIEW', 'REPAIR_RETRY'):
                    invoke = repair_review_call or review_repair.repair_review
                    try:
                        captured = io.StringIO()
                        repair_args = dict(store=store, task_id=task_id,
                            executor_task=executor_task, pr_number=number,
                            auto_review=False, github_get=get,
                            retry_claim_id=(repair_claim.get('repair_task_id')
                                            if step == 'REPAIR_RETRY' and
                                            type(repair_claim) is dict else None),
                            # Auto: review_repair applies the chain-wide auto
                            # limit under its chain lock; advance never pre-checks it.
                            auto=True)
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
                        code = getattr(exc, 'task_store_error_code', None) or _refusal(
                            exc, 'review_repair_code')
                        if code == 'DIRECT_DISPATCH_LEASE_BUSY':
                            step, codes = 'WAITING_REPAIR', [code]
                        elif code == 'REPAIR_LIMIT_REACHED' and remote_status == 'FAIL':
                            step, codes = 'STOPPED', ['TEST_FAIL_LIMIT_REACHED']
                        elif code == 'REVIEW_REPAIR_PM_REQUIRED':
                            step, codes = 'STOPPED', ['REVIEW_REPAIR_PM_REQUIRED']
                        else:
                            codes.append(code)
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
                        github_get=get, merge_gh_runner=label_gh_runner, cwd=cwd)
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
        if step == 'CI_FAILED':
            # #1502 WP-D D11: after PASS, route the newest exact-head Mac run.
            mac_run = _newest_check_run(d6['result_head'], _MAC_CHECK, get)
            routed = None if mac_run is None else _route_ci_failure(
                store=store, record=record, task_id=task_id,
                executor_task=executor_task, number=number, d6=d6, related=related,
                run=mac_run, fail_code='MAC_CI_FAILED', github_get=get,
                repair_review_call=repair_review_call)
            if routed is not None:
                step, codes, result = routed[0], list(routed[1]), routed[2]
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
        try:
            result = _publish_task(record, d6, executor_task,
                git_runner=publish_git_runner, gh_runner=publish_gh_runner,
                record_result_call=record_result_call)
        except _PublishRefusal as exc:
            return _publish_refusal_result(store, task_id, exc)
        except Exception:
            return {'task_id': task_id, 'codes': ['PUBLISH_FAILED']}
        try:
            store.record_published_pr(task_id, result['pr'], result['head'])
        except Exception:
            return dict(result, task_id=task_id, step='REFUSED',
                        codes=['PUBLISH_PR_RECORD_FAILED'])
        return dict(result, task_id=task_id, codes=codes)
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
        if step == 'REPAIR_OUTCOME_UNKNOWN':
            unknown = _unknown_repair_detail(
                related, d6.get('result_head') if type(d6) is dict else None)
            if unknown is not None:
                codes.append(unknown['reason'])
                result = unknown
    elif step == 'DEVELOPER_START_FAILED':
        # #2188 section 7 item 3: stop for the PM. No automatic retry, no new
        # repair claim; the PM decides on `task retry-start`.
        codes.append(step)
        result = _start_failed_repair(
            related, d6.get('result_head') if type(d6) is dict else None)
    elif step == 'WAITING_OWNER':
        remote_status, _, _ = _remote_test_status(record, d6)
        waiting_codes, result = _remote_test_waiting_result(
            task_id, number, d6, remote_status)
        codes.extend(waiting_codes)
    elif step == 'NEEDS_ATTENTION':
        remote_status, _, _ = _remote_test_status(record, d6)
        if remote_status == 'NOT_EXECUTED':
            codes.append('TEST_REMOTE_MODULE_NOT_EXECUTED')
        elif remote_status in ('TIMEOUT', 'SHA_MISMATCH'):
            codes.append('REMOTE_TEST_' + remote_status)
        else:
            codes.append('NEEDS_ATTENTION')
    elif step == 'PR_MERGED_NO_REVIEW':
        codes.append('PR_MERGED_NO_REVIEW')
    elif step in ('REVIEW_OUTCOME_UNKNOWN', 'REVIEW_UNUSABLE_STOPPED'):
        codes.append(step)
        if rerun_refusal_code is not None:
            codes.append(rerun_refusal_code)
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
