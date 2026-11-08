"""#763: a Reviewer D6 that cannot be created leaves a named UNKNOWN claim."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch, task_advance, task_store
from llmtool.errors import LLMToolError

import review_trust_fixture


HEAD = 'b' * 40
PR = 42
EXECUTOR = 'executor-d6'
SECRET_OUT = 'lifecycle-stdout-must-not-be-kept'
SECRET_ERR = 'lifecycle-stderr-must-not-be-kept'
CREATE_REASONS = ('REVIEWER_D6_CREATE_FAILED', 'REVIEWER_D6_CREATE_TIMEOUT')


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _real_store(root):
    task_id, run_id = 'a' * 32, 'c' * 32
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': task_id,
        'goal': 'test reviewer D6 create failure claims',
        'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response',
                      'description': 'Record the selected PM response or a truthful unavailable outcome.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [],
        'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    request = 'test reviewer D6 create failure claims'
    record = {
        'schema_version': 2, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-11-07T00:00:00Z',
        'updated_at': '2026-11-07T00:00:00Z', 'task_contract': contract,
        'task_binding': vars(parsed.binding(run_id)),
        'request': request,
        'request_sha256': hashlib.sha256(request.encode('utf-8')).hexdigest(),
        'response': None, 'usage': None,
        'error_code': None, 'developer': None, 'pm_assignment': None,
        'completion': None,
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store, task_id


def _completed(returncode):
    return SimpleNamespace(returncode=returncode, stdout=SECRET_OUT.encode(),
                           stderr=SECRET_ERR.encode())


class CreateD6RefusalTests(unittest.TestCase):
    """_create_d6 refuses with fixed names and keeps no lifecycle output."""

    def call(self, **run_kwargs):
        with patch.object(review_dispatch.subprocess, 'run', **run_kwargs) as run:
            with self.assertRaises(LLMToolError) as raised:
                review_dispatch._create_d6(
                    task_id='reviewer-0123456789abcdef0123', base_sha=HEAD,
                    repo_root='/repo', executor_task=EXECUTOR,
                    github_login='reviewer-user', reviewer_key='codex-reviewer')
        run.assert_called_once()
        return raised.exception

    def assert_refusal(self, exc, name):
        self.assertEqual(exc.review_dispatch_code, name)
        self.assertEqual(exc.review_failure_reason, name)
        self.assertIsNone(exc.__cause__)
        self.assertTrue(exc.__suppress_context__)
        for text in (str(exc), repr(exc), repr(vars(exc))):
            self.assertNotIn(SECRET_OUT, text)
            self.assertNotIn(SECRET_ERR, text)

    def test_non_zero_exit_refuses_as_create_failed(self):
        exc = self.call(return_value=_completed(3))
        self.assert_refusal(exc, 'REVIEWER_D6_CREATE_FAILED')

    def test_timeout_refuses_as_create_timeout(self):
        timeout = subprocess.TimeoutExpired(
            ['lifecycle'], 180, output=SECRET_OUT.encode(), stderr=SECRET_ERR.encode())
        exc = self.call(side_effect=timeout)
        self.assert_refusal(exc, 'REVIEWER_D6_CREATE_TIMEOUT')

    def test_os_error_starting_script_refuses_as_create_failed(self):
        exc = self.call(side_effect=OSError(SECRET_ERR))
        self.assert_refusal(exc, 'REVIEWER_D6_CREATE_FAILED')

    def test_success_returns_without_refusal(self):
        with patch.object(review_dispatch.subprocess, 'run',
                          return_value=_completed(0)) as run:
            self.assertIsNone(review_dispatch._create_d6(
                task_id='reviewer-0123456789abcdef0123', base_sha=HEAD,
                repo_root='/repo', executor_task=EXECUTOR,
                github_login='reviewer-user'))
        self.assertEqual(run.call_args.kwargs.get('timeout'), 180)

    def test_create_reasons_join_the_fixed_set(self):
        for name in CREATE_REASONS:
            self.assertIn(name, review_dispatch.REVIEW_FAILURE_REASONS)
        self.assertEqual(review_dispatch._D6_CREATE_FAILURE_REASONS,
                         frozenset(CREATE_REASONS))
        self.assertIn('REVIEW_CLASSIFIER_REJECTED', review_dispatch.REVIEW_FAILURE_REASONS)


class ReviewD6CreateFailureClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name) / 'home'
        home.mkdir()
        env_patch = patch.dict(os.environ, {'HOME': str(home)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.store, self.task_id = _real_store(Path(self.temp.name) / 'store')
        self.codex_calls, self.bind_calls, self.lifecycle_calls = [], [], []

    def dispatch(self, *, version=1, create_d6=None, run_kwargs=None):
        executor = SimpleNamespace(root='/repo/executor', common_dir='/repo/.git')
        preflight = (self.task_id, executor, HEAD, 'e' * 40, 'codex-reviewer')
        role = {'source': 'roles', 'config_dir': None, 'provider': 'codex',
                'model': None, 'effort': None}
        real_run = subprocess.run

        def lifecycle_run(args, *rest, **kwargs):
            # Only the lifecycle script is faked; anything else runs unchanged.
            if not any(str(arg).endswith('d6_worktree_lifecycle.py') for arg in args):
                return real_run(args, *rest, **kwargs)
            self.lifecycle_calls.append(list(args))
            if 'side_effect' in run_kwargs:
                raise run_kwargs['side_effect']
            return run_kwargs['return_value']

        run_patch = (patch.object(review_dispatch.subprocess, 'run',
                                  side_effect=lifecycle_run)
                     if run_kwargs is not None else patch.object(
                         review_dispatch, '_create_d6',
                         side_effect=AssertionError('default create must not run')))
        with patch.object(review_dispatch, '_preflight', return_value=preflight), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch.role_config, 'resolve_role',
                             return_value=role), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             side_effect=AssertionError('reviewer D6 must not be read')), \
                run_patch:
            with self.assertRaises(LLMToolError) as raised:
                review_dispatch.dispatch_review(
                    store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                    pr_number=PR, version=version, create_d6=create_d6,
                    dispatch_session=lambda **_: self.fail('Claude dispatch called'),
                    bind_session=lambda **kw: self.bind_calls.append(kw),
                    prompt_renderer=lambda *_: 'prompt',
                    codex_runner=lambda **kw: self.codex_calls.append(kw))
        return raised.exception

    def claims(self):
        return self.store.get(self.task_id)['review_dispatch_claim']

    def decide(self):
        record = dict(self.store.get(self.task_id),
                      developer={'state': 'DEVELOPER_RESPONDED'},
                      completion={'executor_task_id': EXECUTOR})
        d6 = {'result_head': HEAD, 'recorded_result_head': HEAD, 'dirty': False}
        pr = {'number': PR, 'head': {'sha': HEAD}, 'merged': False}
        with patch.object(task_advance, '_remote_test_status',
                          return_value=('PASS', None, None)):
            return task_advance.decide(record, d6, pr, {})

    def assert_no_output_kept(self, exc):
        stored = json.dumps(self.store.get(self.task_id))
        for text in (SECRET_OUT, SECRET_ERR):
            self.assertNotIn(text, stored)
            self.assertNotIn(text, str(exc))

    def test_non_zero_exit_and_timeout_leave_named_unknown_claims(self):
        cases = (
            ('REVIEWER_D6_CREATE_FAILED', {'return_value': _completed(1)}),
            ('REVIEWER_D6_CREATE_TIMEOUT', {'side_effect': subprocess.TimeoutExpired(
                ['lifecycle'], 180, output=SECRET_OUT.encode(),
                stderr=SECRET_ERR.encode())}),
        )
        for index, (name, run_kwargs) in enumerate(cases):
            with self.subTest(reason=name):
                self.store, self.task_id = _real_store(
                    Path(self.temp.name) / f'case-{index}')
                exc = self.dispatch(run_kwargs=run_kwargs)
                self.assertEqual(exc.review_dispatch_code, name)
                self.assertEqual(exc.review_failure_reason, name)
                [claim] = self.claims()
                self.assertEqual(claim['state'], 'UNKNOWN')
                self.assertEqual(claim['failure_reason'], name)
                self.assertEqual((claim['pr'], claim['head_sha'], claim['version']),
                                 (PR, HEAD, 1))
                self.assertIsNone(claim['native_session_id'])
                self.assert_no_output_kept(exc)
                # A reopened store re-validates the saved claim from disk.
                reopened = task_store.TaskStore(
                    (Path(self.temp.name) / f'case-{index}').resolve())
                self.assertEqual(reopened.get(self.task_id)['review_dispatch_claim'],
                                 [claim])
        self.assertEqual(len(self.lifecycle_calls), 2)
        self.assertEqual(self.codex_calls, [])
        self.assertEqual(self.bind_calls, [])

    def test_first_create_failure_redispatches_and_second_on_head_stops(self):
        exc = self.dispatch(run_kwargs={'return_value': _completed(1)})
        self.assertEqual(exc.review_failure_reason, 'REVIEWER_D6_CREATE_FAILED')
        [first] = self.claims()
        self.assertEqual(self.decide(), 'REVIEW_REDISPATCH')

        timeout = subprocess.TimeoutExpired(['lifecycle'], 180)
        exc = self.dispatch(version=2, run_kwargs={'side_effect': timeout})
        self.assertEqual(exc.review_failure_reason, 'REVIEWER_D6_CREATE_TIMEOUT')
        claims = self.claims()
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0], first)
        self.assertEqual(claims[1]['state'], 'UNKNOWN')
        self.assertEqual(claims[1]['failure_reason'], 'REVIEWER_D6_CREATE_TIMEOUT')
        self.assertEqual(claims[1]['version'], 2)
        self.assertEqual(self.decide(), 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(len(self.lifecycle_calls), 2)
        self.assertEqual(self.codex_calls, [])

    def test_injected_create_adapter_refusal_is_saved_with_its_reason(self):
        def create(**_):
            review_dispatch._refuse('REVIEWER_D6_CREATE_FAILED',
                                    reason='REVIEWER_D6_CREATE_FAILED')
        self.dispatch(create_d6=create)
        [claim] = self.claims()
        self.assertEqual(claim['failure_reason'], 'REVIEWER_D6_CREATE_FAILED')
        self.assertEqual(self.decide(), 'REVIEW_REDISPATCH')
        self.assertEqual(self.codex_calls, [])

    def test_failure_without_reason_still_reads_outcome_unknown(self):
        def create(**_):
            raise RuntimeError('unnamed create failure')
        exc = self.dispatch(create_d6=create)
        self.assertEqual(exc.review_dispatch_code, 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        [claim] = self.claims()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('failure_reason', claim)
        self.assertEqual(self.decide(), 'REVIEW_OUTCOME_UNKNOWN')
        self.assertEqual(self.codex_calls, [])

    def test_reason_outside_the_set_is_dropped(self):
        def create(**_):
            review_dispatch._refuse('REVIEWER_D6_CREATE_FAILED',
                                    reason='NOT_A_KNOWN_REASON')
        self.dispatch(create_d6=create)
        [claim] = self.claims()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('failure_reason', claim)
        self.assertEqual(self.decide(), 'REVIEW_OUTCOME_UNKNOWN')
        self.assertEqual(self.codex_calls, [])


class StoreValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def claimed(self, store, task_id):
        stamp = '2026-11-07T00:00:00Z'
        claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD,
                 'version': 1, 'state': 'CLAIMED',
                 'reviewer_task': 'reviewer-0123456789abcdef0123',
                 'native_session_id': None, 'account_mode': 'ROLES',
                 'claimed_at': stamp, 'updated_at': stamp}
        store.compare_and_update(
            task_id, expected={'record_sha256': store.get(task_id)['record_sha256']},
            review_dispatch_claim=[claim])
        return claim

    def test_validator_accepts_each_create_failure_reason(self):
        for index, name in enumerate(CREATE_REASONS):
            with self.subTest(reason=name):
                root = Path(self.temp.name) / str(index)
                store, task_id = _real_store(root)
                claim = self.claimed(store, task_id)
                unknown = dict(claim, state='UNKNOWN', failure_reason=name,
                               updated_at='2026-11-07T00:01:00Z')
                store.compare_and_update(
                    task_id, expected={'review_dispatch_claim': [claim]},
                    review_dispatch_claim=[unknown])
                reopened = task_store.TaskStore(root.resolve())
                self.assertEqual(reopened.get(task_id)['review_dispatch_claim'], [unknown])

    def test_validator_still_rejects_an_unknown_reason(self):
        store, task_id = _real_store(Path(self.temp.name) / 'bad')
        claim = self.claimed(store, task_id)
        unknown = dict(claim, state='UNKNOWN', failure_reason='REVIEWER_D6_LOCKED',
                       updated_at='2026-11-07T00:01:00Z')
        with self.assertRaises(LLMToolError) as raised:
            store.compare_and_update(
                task_id, expected={'review_dispatch_claim': [claim]},
                review_dispatch_claim=[unknown])
        self.assertEqual(getattr(raised.exception, 'task_store_error_code', None),
                         'REVIEW_DISPATCH_CLAIM_INVALID')
        self.assertEqual(store.get(task_id)['review_dispatch_claim'], [claim])


if __name__ == '__main__':
    unittest.main()
