"""Advance one durable Task by exactly one review or closure action."""

import hashlib
import contextlib
import io
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from argparse import Namespace

from . import active_pm_inbox, github_events, review_dispatch, review_repair, review_result
from . import task_completion, task_submission_cli
from .errors import LLMToolError
from .task_store import TaskStore


class _PublishRefusal(Exception):
    def __init__(self, code):
        self.code = code


def _publish_run_git(root, args):
    return subprocess.run(['git', *args], cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=False, timeout=30)


def _publish_run_gh(root, args):
    return subprocess.run(['gh', *args], cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=False, timeout=30)


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


def _publish_pr(gh_runner, root, branch, task_id, expected_head):
    output = _publish_command(gh_runner, root,
        ['pr', 'list', '--head', branch, '--state', 'open',
         '--json', 'number,headRefOid'], code='PUBLISH_GITHUB_FAILED')
    try:
        pulls = json.loads(output)
    except (ValueError, TypeError):
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED') from None
    if type(pulls) is not list:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    if not pulls:
        _publish_command(gh_runner, root,
            ['pr', 'create', '--head', branch,
             '--title', f'llmtool Task {task_id}',
             '--body', f'Automated publication for llmtool Task {task_id}.'],
            code='PUBLISH_GITHUB_FAILED')
        output = _publish_command(gh_runner, root,
            ['pr', 'list', '--head', branch, '--state', 'open',
             '--json', 'number,headRefOid'], code='PUBLISH_GITHUB_FAILED')
        try:
            pulls = json.loads(output)
        except (ValueError, TypeError):
            raise _PublishRefusal('PUBLISH_GITHUB_FAILED') from None
    if type(pulls) is not list or not pulls:
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    pull = pulls[0]
    number = pull.get('number') if type(pull) is dict else None
    head = pull.get('headRefOid') if type(pull) is dict else None
    if (type(number) is not int or number < 1 or type(head) is not str or
            head != expected_head):
        raise _PublishRefusal('PUBLISH_GITHUB_FAILED')
    return number


def _merge_refusal(code):
    return {'step': 'MERGE_REFUSED', 'codes': [code], 'result': None}


def _owner_accepted(pr):
    labels = pr.get('labels', []) if type(pr) is dict else []
    tui = any(type(item) is dict and type(item.get('name')) is str and
              'tui' in item['name'].lower() for item in labels) if type(labels) is list else False
    body = pr.get('body') if type(pr) is dict else None
    return (not tui or (type(body) is str and
            '<!-- llmtool-owner-accepted -->' in body))


def _merge_checks(record, number, head, pr, github_get):
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
    if any(type(item) is not dict or type(item.get('name')) is not str or
           item.get('head_sha') != head or
           item.get('status') != 'completed' or
           item.get('conclusion') not in ('success', 'skipped') for item in check_runs):
        return 'MERGE_CHECKS_NOT_GREEN'
    if pr.get('state') not in (None, 'open') or pr.get('merged') is True:
        return 'MERGE_PR_NOT_OPEN'
    if type(pr.get('mergeable')) is not bool or pr.get('mergeable') is not True:
        return 'MERGE_PR_NOT_MERGEABLE'
    paths = [item.get('filename') for item in files if type(item) is dict]
    if len(paths) != len(files) or any(type(path) is not str for path in paths):
        return 'MERGE_CHECKS_UNAVAILABLE'
    docs_or_tests = all(path.endswith('.md') or '/tests/' in '/' + path or
                        path.startswith('tests/') or
                        re.search(r'(^|/)test_[^/]+\.py$', path) for path in paths)
    mac_runs = [item for item in check_runs if
                any(token in item.get('name', '').lower() for token in ('macos', 'mac os'))]
    if not docs_or_tests and (not mac_runs or any(item.get('conclusion') not in
                                                   ('success', 'skipped') for item in mac_runs)):
        return 'MERGE_MACOS_CHECK_REQUIRED'
    return None


def _merge_pass_claim(record, number, head, events):
    candidates = []
    for claim in _claims(record):
        if (type(claim) is not dict or claim.get('pr') != number or
                claim.get('head_sha') != head or claim.get('state') != 'COMPLETED' or
                type(claim.get('version')) is not int):
            continue
        result = claim.get('review_result')
        if type(result) is not dict:
            continue
        outcome = next((event.get('outcome') or event.get('state') for event in events
                        if type(event) is dict and event.get('source') == 'REVIEWER' and
                        event.get('source_sha') == head and
                        event.get('version') == claim['version']), None)
        if outcome is None:
            continue
        candidates.append((claim['version'], result.get('verdict'), outcome))
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item[0])
    if latest[1] != 'PASS' or latest[2] != 'REVIEW_PASS':
        return 'MERGE_REVIEW_NOT_PASS'
    if any(type(event) is dict and event.get('source') == 'REVIEWER' and
           event.get('source_sha') == head and type(event.get('version')) is int and
           event['version'] > latest[0] for event in events):
        return 'MERGE_NEWER_REVIEW_EXISTS'
    return latest[0]


def _merge_once(*, store, record, number, head, pr, events, github_get,
                gh_runner, root):
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
    review_version = _merge_pass_claim(record, number, head, events)
    if type(review_version) is not int:
        return _merge_refusal(review_version or 'MERGE_REVIEW_NOT_PASS')
    if not _owner_accepted(pr):
        return _merge_refusal('MERGE_OWNER_ACCEPTANCE_REQUIRED')
    code = _merge_checks(record, number, head, pr, github_get)
    if code:
        return _merge_refusal(code)
    try:
        current_pr = github_get(f'pulls/{number}')
    except Exception:
        return _merge_refusal('MERGE_PR_UNAVAILABLE')
    current_head = current_pr.get('head') if type(current_pr) is dict else None
    if (type(current_head) is not dict or current_head.get('sha') != head or
            current_pr.get('number', number) != number):
        return _merge_refusal('MERGE_STALE_HEAD')
    if not _owner_accepted(current_pr):
        return _merge_refusal('MERGE_OWNER_ACCEPTANCE_REQUIRED')
    if current_pr.get('state') not in (None, 'open') or current_pr.get('merged') is True:
        return _merge_refusal('MERGE_PR_NOT_OPEN')
    if current_pr.get('mergeable') is not True:
        return _merge_refusal('MERGE_PR_NOT_MERGEABLE')
    prior = record.get('merge_attempt')
    if prior is not None:
        if prior.get('pr') != number or prior.get('head_sha') != head:
            return _merge_refusal('MERGE_CONFLICTING_PR')
        return _merge_refusal('MERGE_ALREADY_CLAIMED')
    claim = {'pr': number, 'head_sha': head, 'state': 'CLAIMED',
             'claimed_at': TaskStore._completion_now()}
    try:
        store.compare_and_update(record['task_id'],
            expected={'record_sha256': record['record_sha256']}, merge_attempt=claim)
    except Exception:
        return _merge_refusal('MERGE_CLAIM_CONFLICT')
    args = ['pr', 'merge', str(number), '--squash', '--match-head-commit', head]
    try:
        result = gh_runner(root, args)
    except Exception:
        result = None
    if result is None or getattr(result, 'returncode', 1) != 0:
        claim['state'] = 'UNKNOWN'
        try:
            store.compare_and_update(record['task_id'], expected={'merge_attempt':
                dict(claim, state='CLAIMED')}, merge_attempt=claim)
        except Exception:
            pass
        return _merge_refusal('MERGE_OUTCOME_UNKNOWN')
    claim['state'] = 'MERGED'
    try:
        store.compare_and_update(record['task_id'], expected={'merge_attempt':
            dict(claim, state='CLAIMED')}, merge_attempt=claim)
    except Exception:
        return _merge_refusal('MERGE_RESULT_RECORD_FAILED')
    body = (f'llmtool task advance merge provenance: task={record["task_id"]} '
            f'head={head} review_version={review_version} checks=exact-head-success-or-skipped')
    try:
        comment = gh_runner(root, ['pr', 'comment', str(number), '--body', body])
        if getattr(comment, 'returncode', 1) != 0:
            return _merge_refusal('MERGE_PROVENANCE_COMMENT_FAILED')
    except Exception:
        return _merge_refusal('MERGE_PROVENANCE_COMMENT_FAILED')
    return {'step': 'MERGED', 'codes': [], 'result': {'pr': number, 'head': head}}


def _record_d6_result(*, task_id, canonical_root, root):
    lifecycle_path = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    spec = importlib.util.spec_from_file_location('_llmtool_d6_worktree_lifecycle', lifecycle_path)
    if spec is None or spec.loader is None:
        raise _PublishRefusal('PUBLISH_RESULT_RECORD_FAILED')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.record_result(task_id=task_id, canonical_root=canonical_root, root=root)


def _publish_task(record, d6, executor_task, *, git_runner=None, gh_runner=None,
                  record_result_call=None):
    """Commit scoped D6 changes, record the result, push, and ensure an open PR."""
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
    if any(path == '__pycache__' or '/__pycache__/' in '/' + path + '/' or
           path.endswith('.pyc') for path in ignored_paths):
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
        _publish_command(git_runner, root,
            ['commit', '-m', f'llmtool Task {task_id}'])
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
    number = _publish_pr(gh_runner, root, branch, task_id, local_head)
    return {'pr': number, 'head': local_head}


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
        digest = hashlib.sha256(json.dumps(
            digest_input, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
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


def derive_repair_state(repair_record):
    """Normalize a repair Task record into the state used by the advance loop."""
    if type(repair_record) is not dict:
        return 'UNKNOWN'
    developer = repair_record.get('developer')
    state = developer.get('state') if type(developer) is dict else None
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
    if type(chosen_pr) is not int or chosen_pr < 1:
        return 'PUBLISH'
    pr_head = pr.get('head') if type(pr) is dict else None
    if type(pr_head) is not dict or pr_head.get('sha') != result_head:
        return 'PUBLISH'

    matching = [claim for claim in claims if type(claim) is dict and
                claim.get('pr') == chosen_pr and claim.get('head_sha') == result_head]
    if not matching:
        remote_status, _, _ = _remote_test_status(record, d6)
        if remote_status in ('TIMEOUT', 'SHA_MISMATCH'):
            return 'NEEDS_ATTENTION'
        if remote_status == 'FAIL':
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
        if remote_status not in ('PASS', 'ADMISSION_DENIED'):
            return 'RUN_REMOTE_TEST'
        return 'DISPATCH_REVIEW'
    claim = max(matching, key=lambda item: item.get('version', 0))
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
            'state': 'CLEAN' if clean else 'DIRTY'}


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
                                 'version': version, 'outcome': latest[2]})
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


def _merged_review_target(record, claims, events, pr, integration_pr, github_get):
    """Return a verified merged PR tuple for a completed review, if present."""
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
            if type(compare) is dict and compare.get('status') in ('ahead', 'identical'):
                return integration_pr, merge_sha
        except Exception:
            continue
    return None


def advance(*, task_id, store=None, pr_number=None, integration_pr=None, merge=False,
            reviewer_session_done=False, dispatch_review_call=None,
            record_review_result_call=None, repair_review_call=None,
            run_task_complete_call=None, record_github_event_call=None,
            github_get=None, cwd=None, publish_git_runner=None,
            publish_gh_runner=None, record_result_call=None, merge_gh_runner=None):
    """Perform one selected action and return a bounded JSON-ready summary."""
    store = TaskStore() if store is None else store
    try:
        record = store.get(task_id)
    except Exception:
        return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                'next': 'REFUSED', 'codes': ['TASK_RECORD_UNAVAILABLE'], 'acked': []}
    executor_task = (record.get('completion') or {}).get('executor_task_id')
    number = _pr_number(record, pr_number)
    get = task_completion._github_get if github_get is None else github_get
    d6 = _d6_snapshot(record, executor_task) if type(executor_task) is str else None
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
    merged = _merged_review_target(record, _claims(record), related['events'], pr,
                                    integration_pr, get)
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
            root=d6.get('root') or cwd or os.getcwd())
        merge_result.update(task_id=task_id, next=merge_result['step'])
        return _ack_consumed_events(merge_result, record, store, d6['result_head'])
    if type(d6) is dict and d6.get('state') == 'D6_BINDING_UNAVAILABLE':
        code = d6.get('code') if type(d6.get('code')) is str else 'D6_BINDING_UNAVAILABLE'
        return _ack_consumed_events(
            {'task_id': task_id, 'step': 'D6_BINDING_UNAVAILABLE', 'result': None,
             'next': 'D6_BINDING_UNAVAILABLE', 'codes': [code]}, record, store, None)
    step = decide(record, d6, pr, related)
    result, codes = None, []
    if step == 'RUN_REMOTE_TEST':
        invoke = task_submission_cli.run_task_remote_test
        args = Namespace(task_id=task_id, executor_task=executor_task,
                         sha=d6['result_head'], ref=f'refs/pull/{number}/head',
                         module=[], pr=number)
        try:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                value = invoke(args, store=store)
            try:
                remote_result = json.loads(captured.getvalue().strip())
            except (TypeError, ValueError):
                remote_result = value if type(value) is dict else {}
            code = remote_result.get('code') if type(remote_result) is dict else None
            if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code):
                codes.append(code)
            result = {'task_id': task_id, 'executor_task': executor_task,
                      'head': d6['result_head']}
        except Exception as exc:
            codes.append(_refusal(exc, 'remote_test_code'))
    elif step == 'DISPATCH_REVIEW':
        claims = _claims(record)
        versions = [c.get('version', 0) for c in claims if type(c) is dict and
                    type(c.get('version')) is int]
        version = max(versions, default=0) + 1
        invoke = dispatch_review_call or review_dispatch.dispatch_review
        try:
            result = invoke(store=store, task_id=task_id, executor_task=executor_task,
                            pr_number=number, version=version)
        except Exception as exc:
            codes.append(_refusal(exc, 'review_dispatch_code'))
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
                step = 'WAITING_CI'
            elif missing:
                codes.extend(missing)
                step = 'WAITING_EXECUTOR'
        except Exception as exc:
            codes.append(_refusal(exc, 'completion_code'))
    elif step == 'READY_FOR_INTEGRATION':
        try:
            integration = _pr_snapshot(integration_pr, get) if integration_pr is not None else pr
            merged = type(integration) is dict and integration.get('merged') is True
            merge_sha = integration.get('merge_commit_sha') if merged else None
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
            if merged and type(merge_sha) is str and re.fullmatch(r'[0-9a-f]{40}', merge_sha):
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
            return {'task_id': task_id, 'codes': [], 'pr': result['pr'],
                    'head': result['head']}
        except _PublishRefusal as exc:
            return {'task_id': task_id, 'codes': [exc.code]}
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
    elif step in ('REVIEW_OUTCOME_UNKNOWN', 'WAITING_REPAIR',
                  'NEEDS_CWD', 'RUNTIME_NOT_ON_PATH'):
        codes.append(step)
    next_action = ('resume the reviewer to post its verdict' if step == 'REVIEW_NOT_POSTED'
                   else step)
    return _ack_consumed_events(
        {'task_id': task_id, 'step': step, 'result': result,
         'next': next_action, 'codes': codes}, record, store,
        d6.get('result_head') if type(d6) is dict else None)
