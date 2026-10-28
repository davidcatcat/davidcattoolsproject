"""One-shot, cross-family formal Reviewer dispatch for a completed Task."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .errors import LLMToolError
from . import task_completion, worker_dispatch


def _refuse(code, message='Reviewer dispatch refused; details omitted'):
    error = LLMToolError(message)
    error.review_dispatch_code = code
    raise error from None


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _policy(task, reviewer_key):
    developer = task.get('developer') if type(task) is dict else None
    assignment = (developer.get('owner_assignment_ref')
                  if type(developer) is dict else None)
    return worker_dispatch.evaluate(reviewer_key, assignment, task.get('task_id'))


def _resolve_github_login():
    """Resolve the authenticated GitHub account without exposing credentials."""
    gh = shutil.which('gh', path=task_completion._GH_PATH)
    if gh is None:
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    env = {'PATH': task_completion._GH_PATH, 'HOME': os.path.expanduser('~'),
           'LC_ALL': 'C', 'GH_PROMPT_DISABLED': '1',
           'GH_NO_UPDATE_NOTIFIER': '1', 'NO_COLOR': '1'}
    if os.environ.get('GH_CONFIG_DIR'):
        env['GH_CONFIG_DIR'] = os.environ['GH_CONFIG_DIR']
    try:
        result = subprocess.run(
            [gh, 'api', 'user', '--jq', '.login'], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    try:
        login = result.stdout.decode('utf-8').strip()
    except UnicodeDecodeError:
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    if (result.returncode or re.fullmatch(r'[A-Za-z0-9-]{1,39}', login) is None):
        _refuse('REVIEWER_GITHUB_LOGIN_UNAVAILABLE')
    return login


def _preflight(store, task_id, executor_task, pr_number, version, *, task=None):
    if task is None:
        task = store.get(task_id)
    binding = task.get('task_binding')
    try:
        task_completion.validate_task_binding(
            stored_binding=binding, task_id=task.get('task_id'), run_id=task.get('run_id'),
            contract_digest=binding.get('contract_digest') if type(binding) is dict else None)
    except LLMToolError:
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    completion = task.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    if (type(evidence) is not dict or completion.get('executor_task_id') != executor_task or
            type(evidence.get('common_dir')) is not str):
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    try:
        executor = task_completion.read_d6_registration(
            executor_task, evidence['common_dir'], allowed_states=task_completion._D6_EVIDENCE_STATES)
        head = executor.result_sha
        snap = task_completion.collect_git_snapshot(executor.root)
        if not snap.clean:
            _refuse('REVIEW_PROMPT_EXECUTOR_DIRTY')
        recorded = evidence.get('result')
        if (type(head) is not str or re.fullmatch(r'[0-9a-f]{40}', head) is None or
                type(recorded) is dict and recorded.get('result_head') not in (None, head) or
                snap.head != head or snap.root != executor.root or
                snap.common_dir != executor.common_dir or snap.branch != executor.branch):
            _refuse('REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
    except LLMToolError as exc:
        if getattr(exc, 'review_dispatch_code', None):
            raise
        _refuse('REVIEW_PROMPT_D6_UNAVAILABLE')
    except Exception:
        _refuse('REVIEW_PROMPT_D6_UNAVAILABLE')
    try:
        pull = task_completion._github_get(f'pulls/{pr_number}')
    except LLMToolError:
        _refuse('REVIEW_PROMPT_PR_UNAVAILABLE')
    if (type(pull) is not dict or type(pull.get('number')) is not int or
            pull['number'] != pr_number or type(pull.get('head')) is not dict or
            pull['head'].get('sha') != head):
        _refuse('REVIEW_PROMPT_PR_HEAD_MISMATCH')
    base = pull.get('base')
    if (type(base) is not dict or type(base.get('sha')) is not str or
            re.fullmatch(r'[0-9a-f]{40}', base['sha']) is None):
        _refuse('REVIEW_PROMPT_PR_UNAVAILABLE')
    if type(version) is not int or not 1 <= version <= 9999:
        _refuse('REVIEW_PROMPT_BINDING_MISSING')
    developer = task.get('developer')
    family = developer.get('provider_profile') if type(developer) is dict else None
    if family in ('codex', 'opencode', 'company-gw'):
        reviewer_key = 'claude-reviewer'
    elif family == 'claude':
        _refuse('REVIEWER_ROUTE_UNIMPLEMENTED')
    else:
        _refuse('REVIEWER_FAMILY_UNSUPPORTED')
    return task, executor, head, base['sha'], reviewer_key


def _create_d6(*, task_id, base_sha, repo_root, executor_task, github_login):
    lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    lease = f'read-only formal review of executor task {executor_task} at {base_sha}'
    args = [sys.executable, '-B', str(lifecycle), '--task-id', task_id,
            '--agent-id', 'claude-reviewer', '--session-ref', 'pending-' + task_id,
            '--lease-ref', lease, '--branch', 'llmtool/reviewer-' + task_id,
            '--base-sha', base_sha, '--github-login', github_login,
            '--repo-root', str(repo_root)]
    result = subprocess.run(args, cwd=str(repo_root), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, timeout=180)
    if result.returncode:
        raise RuntimeError('REVIEWER_D6_CREATE_FAILED')


def _dispatch(*, task_id, prompt, repo_root, env):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'claude_bg_dispatch.py'
    args = [sys.executable, '-B', str(script), 'dispatch', '--new', '--name',
            'claude-reviewer', '--task-id', task_id, '--prompt', prompt,
            '--repo-root', str(repo_root)]
    result = subprocess.run(args, cwd=str(repo_root), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=660)
    try:
        receipt = json.loads(result.stdout.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError('REVIEWER_DISPATCH_UNKNOWN') from None
    if result.returncode or type(receipt) is not dict or receipt.get('result') != 'DELIVERED':
        raise RuntimeError('REVIEWER_DISPATCH_UNKNOWN')
    session_id = receipt.get('actual_session_id')
    if type(session_id) is not str or not session_id:
        raise RuntimeError('REVIEWER_DISPATCH_UNKNOWN')
    return session_id


def _bind_d6(task_id, session_id, repo_root):
    lifecycle = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
    result = subprocess.run(
        [sys.executable, '-B', str(lifecycle), '--bind-native-session-task-id', task_id,
         '--native-session-id', session_id, '--repo-root', str(repo_root)],
        cwd=str(repo_root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=120)
    if result.returncode:
        raise RuntimeError('REVIEWER_SESSION_BIND_FAILED')


def dispatch_review(*, store, task_id, executor_task, pr_number, version=1,
                    create_d6=None, dispatch_session=None, bind_session=None,
                    prompt_renderer=None):
    """Claim once, create a read-only Reviewer D6 and bind its dispatched session."""
    task = store.get(task_id)
    _, executor, head, _base, reviewer_key = _preflight(
        store, task_id, executor_task, pr_number, version, task=task)
    stored_claims = task.get('review_dispatch_claim')
    claims = ([stored_claims] if type(stored_claims) is dict else
              list(stored_claims) if type(stored_claims) is list else [])
    identity = (pr_number, head, version)
    prior = next((claim for claim in claims if
                  (claim.get('pr'), claim.get('head_sha'), claim.get('version')) == identity), None)
    if prior is not None:
        if prior.get('executor_task') != executor_task:
            _refuse('REVIEW_DISPATCH_CLAIM_MISMATCH')
        if prior['state'] == 'CLAIMED':
            unknown = dict(prior, state='UNKNOWN', updated_at=_now())
            updated_claims = [unknown if claim is prior else claim for claim in claims]
            try:
                saved = store.compare_and_update(
                    task_id, expected={'review_dispatch_claim': stored_claims},
                    review_dispatch_claim=updated_claims)
                return unknown
            except Exception:
                _refuse('REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        return prior
    policy = _policy(task, reviewer_key)
    if not policy.allowed:
        _refuse(policy.reason_code)
    config_dir = os.environ.get('CLAUDE_CONFIG_DIR')
    account_mode = ('EXPLICIT_CLAUDE_CONFIG_DIR' if config_dir else 'DEFAULT_SECOND_ACCOUNT')
    reviewer_task = 'reviewer-' + os.urandom(10).hex()
    claim = {'executor_task': executor_task, 'pr': pr_number, 'head_sha': head,
             'version': version, 'state': 'CLAIMED', 'reviewer_task': reviewer_task,
             'native_session_id': None, 'account_mode': account_mode,
             'claimed_at': _now(), 'updated_at': _now()}
    try:
        store.compare_and_update(task_id, expected={'record_sha256': task['record_sha256']},
                                 review_dispatch_claim=claims + [claim])
    except Exception:
        _refuse('REVIEW_DISPATCH_CLAIM_UNAVAILABLE')

    repo_root = str(Path(executor.common_dir).parent)
    create = create_d6 or _create_d6
    send = dispatch_session or _dispatch
    bind = bind_session or _bind_d6
    session_id = None
    try:
        # Authorization is deliberately re-read immediately before each side effect.
        gate = _policy(task, reviewer_key)
        if not gate.allowed:
            _refuse(gate.reason_code)
        github_login = _resolve_github_login()
        create(task_id=reviewer_task, base_sha=head, repo_root=repo_root,
               executor_task=executor_task, github_login=github_login)
        if prompt_renderer is None:
            from . import task_submission_cli
            args = type('ReviewArgs', (), {'task_id': task_id, 'executor_task': executor_task,
                    'reviewer_task': reviewer_task, 'pr': pr_number, 'version': version})()
            output = io.StringIO()
            with redirect_stdout(output):
                task_submission_cli.run_task_review_prompt(args, store=store)
            prompt = output.getvalue()
        else:
            prompt = prompt_renderer(task_id, executor_task, reviewer_task, pr_number, version)
        # Detect a moved PR/head before starting the Reviewer session.
        _, _, current_head, _, _ = _preflight(store, task_id, executor_task, pr_number, version)
        if current_head != head:
            _refuse('REVIEW_PROMPT_PR_HEAD_MISMATCH')
        gate = _policy(task, reviewer_key)
        if not gate.allowed:
            _refuse(gate.reason_code)
        env = os.environ.copy()
        if not config_dir:
            env.pop('CLAUDE_CONFIG_DIR', None)
        session_id = send(task_id=reviewer_task, prompt=prompt,
                          repo_root=repo_root, env=env)
        bind(task_id=reviewer_task, session_id=session_id, repo_root=repo_root)
        dispatched = dict(claim, state='DISPATCHED', reviewer_task=reviewer_task,
                          native_session_id=session_id, updated_at=_now())
        updated_claims = [dispatched if item is claim else item for item in claims + [claim]]
        store.compare_and_update(task_id, expected={'review_dispatch_claim': claims + [claim]},
                                 review_dispatch_claim=updated_claims)
        return dispatched
    except Exception as exc:
        unknown = dict(claim, state='UNKNOWN', updated_at=_now())
        if session_id is not None:
            unknown['native_session_id'] = session_id
        try:
            claimed = claims + [claim]
            updated_claims = [unknown if item is claim else item for item in claimed]
            store.compare_and_update(task_id, expected={'review_dispatch_claim': claimed},
                                     review_dispatch_claim=updated_claims)
        except Exception:
            pass
        if getattr(exc, 'review_dispatch_code', None):
            raise
        _refuse('REVIEW_DISPATCH_OUTCOME_UNKNOWN')
