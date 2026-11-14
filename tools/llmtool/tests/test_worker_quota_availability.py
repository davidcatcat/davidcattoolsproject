"""#1816 part 2: worker quota layer (classify, observe, delegate gate).

Fixtures are scrubbed shapes of real Developer records: ids, digests and counts
replaced with fixed values. No credential-shaped value appears (Owner #763 A).
Every policy decision uses worker_policy_fixture, never the real
worker-dispatch.json.
"""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from llmtool.errors import LLMToolError  # noqa: E402
from llmtool import review_repair  # noqa: E402
from llmtool import task_store  # noqa: E402
from llmtool import task_submission_cli as cli  # noqa: E402
from llmtool import worker_availability as wa  # noqa: E402
from llmtool import worker_dispatch  # noqa: E402
import worker_policy_fixture  # noqa: E402

TASK = '1' * 32
HEAD = 'a' * 40
CLI_USAGE = {'basis': 'CLI_USAGE', 'accepted': True}
STEP_USAGE = {'basis': 'STEP_USAGE', 'accepted': True}
AGY_STATUS = {'basis': 'AGY_STATUS', 'accepted': True}
GATEWAY_USAGE = {'basis': 'GATEWAY_USAGE', 'accepted': True}
QUOTA = {'condition': 'QUOTA_EXHAUSTED'}
RATE = {'condition': 'RATE_LIMITED'}
TOKENS = {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150}
ZERO = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
REPORTED = 'CLI_REPORTED'
NO_EXECUTION = 'NO_EXECUTION'
REPO = {'slug': 'davidcatcat/davidcattoolsproject', 'dispatch_issue': 289,
        'dispatch_evidence_pr': 1}


def _developer(profile, code=None, evidence=None, usage=None, usage_source='UNKNOWN',
               state='BLOCKED', status='BLOCKED'):
    """A scrubbed Developer record; evidence NO_EXECUTION means no execution at all."""
    execution = None
    if evidence != NO_EXECUTION:
        execution = {'status': status, 'native_session_id': None, 'process_exit_code': 0,
                     'stdout_sha256': 'e' * 64, 'elapsed_ms': 1500,
                     'cleanup_status': 'CONFIRMED', 'usage_source': usage_source,
                     'source_bytes': None, 'source_file_count': None,
                     'source_manifest_sha256': None}
        if evidence is not None:
            execution['provider_evidence'] = dict(evidence)
    return {'assignment_id': '2' * 32, 'provider_profile': profile, 'state': state,
            'error_code': code, 'usage': None if usage is None else dict(usage),
            'artifact': None, 'execution': execution}


def _record(developer):
    return {'task_id': TASK, 'state': 'PM_RESPONDED', 'developer': developer}


# Design table 1: (label, profile, code, evidence, usage, usage_source, expected).
TABLE = (
    ('claude CLI_USAGE', 'claude', None, CLI_USAGE, TOKENS, REPORTED, wa.AVAILABLE),
    ('claude parsed 429', 'claude', 'CLAUDE_QUOTA_EXHAUSTED', QUOTA, None, 'UNKNOWN',
     wa.QUOTA_EXHAUSTED),
    ('claude quota code, no evidence (RCA row)', 'claude', 'CLAUDE_QUOTA_EXHAUSTED', None,
     None, 'UNKNOWN', wa.QUOTA_EXHAUSTED),
    ('claude nonzero exit', 'claude', 'CLAUDE_EXIT_NONZERO', None, None, 'UNKNOWN', None),
    ('claude legacy usage', 'claude', None, None, TOKENS, REPORTED, wa.AVAILABLE),
    ('claude legacy zero usage', 'claude', None, None, ZERO, REPORTED, None),
    ('claude Owner refusal, no execution', 'claude', 'CLAUDE_DISPATCH_DENIED',
     NO_EXECUTION, None, None, None),
    ('claude other route code', 'claude', 'OPENCODE_FREE_LIMIT', None, None, 'UNKNOWN',
     None),
    ('opencode STEP_USAGE', 'opencode', None, STEP_USAGE, TOKENS, REPORTED, wa.AVAILABLE),
    ('opencode free limit', 'opencode', 'OPENCODE_FREE_LIMIT', QUOTA, None, 'UNKNOWN',
     wa.QUOTA_EXHAUSTED),
    ('opencode free limit, signal exit', 'opencode', 'OPENCODE_FREE_LIMIT', None, None,
     'UNKNOWN', wa.QUOTA_EXHAUSTED),
    ('opencode rate limit', 'opencode', 'OPENCODE_RATE_LIMITED', RATE, None, 'UNKNOWN',
     wa.RATE_LIMITED),
    ('opencode rate limit, signal exit', 'opencode', 'OPENCODE_RATE_LIMITED', None, None,
     'UNKNOWN', wa.RATE_LIMITED),
    ('opencode provider failed', 'opencode', 'OPENCODE_PROVIDER_FAILED', None, None,
     'UNKNOWN', None),
    ('opencode legacy usage', 'opencode', None, None, TOKENS, REPORTED, wa.AVAILABLE),
    ('agy AGY_STATUS', 'agy', None, AGY_STATUS, TOKENS, REPORTED, wa.AVAILABLE),
    ('agy quota', 'agy', 'AGY_QUOTA_EXHAUSTED', QUOTA, None, 'UNKNOWN', wa.QUOTA_EXHAUSTED),
    ('agy legacy DONE', 'agy', None, None, TOKENS, REPORTED, None),
    ('gw-agent GATEWAY_USAGE', 'gw-agent', None, GATEWAY_USAGE, TOKENS, REPORTED,
     wa.AVAILABLE),
    ('gw-agent HTTP 429', 'gw-agent', 'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429', QUOTA,
     None, 'UNKNOWN', wa.QUOTA_EXHAUSTED),
    ('gw-agent HTTP 429, signal exit', 'gw-agent',
     'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429', None, None, 'UNKNOWN',
     wa.QUOTA_EXHAUSTED),
    ('gw-agent quota', 'gw-agent', 'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED', None, None,
     'UNKNOWN', wa.QUOTA_EXHAUSTED),
    ('gw-agent HTTP 500', 'gw-agent', 'DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_500', None, None,
     'UNKNOWN', None),
    ('gw-agent legacy usage', 'gw-agent', None, None, TOKENS, REPORTED, wa.AVAILABLE),
    ('codex legacy usage', 'codex', None, None, TOKENS, REPORTED, None),
    ('codex provider failed', 'codex', 'CODEX_PROVIDER_FAILED', None, None, 'UNKNOWN', None),
    ('company-gw legacy usage', 'company-gw', None, None, TOKENS, REPORTED, None),
)

# Pre-execution (no model turn, no evidence) and post-execution pairs.
PAIRS = (
    ('claude', 'CLAUDE_NO_CHANGES', CLI_USAGE, TOKENS),
    ('opencode', 'OPENCODE_STEPS_EXHAUSTED', STEP_USAGE, None),
    ('opencode', 'OPENCODE_D6_SESSION_REFUSED', STEP_USAGE, None),
)


def _row_record(row):
    _label, profile, code, evidence, usage, source, _expected = row
    return _record(_developer(profile, code, evidence, usage, source))


class ClassifyTest(unittest.TestCase):
    def test_design_table(self):
        for row in TABLE:
            with self.subTest(row=row[0]):
                self.assertEqual(wa.classify(_row_record(row)), row[-1])

    def test_pre_and_post_execution_pairs(self):
        for profile, code, evidence, usage in PAIRS:
            with self.subTest(code=code):
                pre = _record(_developer(profile, code, None, None))
                post = _record(_developer(profile, code, evidence, usage,
                                          REPORTED if usage else 'UNKNOWN'))
                self.assertIsNone(wa.classify(pre))
                self.assertEqual(wa.classify(post), wa.AVAILABLE)

    def test_never_reads_state_or_outcome(self):
        for row in TABLE:
            for state in ('DEVELOPER_RESPONDED', 'DEVELOPER_OUTCOME_UNKNOWN', 'BLOCKED'):
                for status in ('DONE', 'UNKNOWN', 'TIMEOUT'):
                    record = _row_record(row)
                    record['state'] = 'REVIEW_FAILED'
                    record['developer'].update(state=state, artifact='diff')
                    if record['developer']['execution'] is not None:
                        record['developer']['execution']['status'] = status
                    with self.subTest(row=row[0], state=state, status=status):
                        self.assertEqual(wa.classify(record), row[-1])

    def test_code_family_lookalikes_and_bad_shapes_give_none(self):
        for code in ('CLAUDE_QUOTA_EXHAUSTED_RETRY', 'X_QUOTA_EXHAUSTED', 'QUOTA_EXHAUSTED'):
            self.assertIsNone(wa.classify(_record(_developer('claude', code))))
        for evidence in ({'condition': ['QUOTA_EXHAUSTED']}, {'condition': 'TIMEOUT'},
                         STEP_USAGE, {'basis': 'CLI_USAGE', 'accepted': 1}):
            self.assertIsNone(wa.classify(_record(_developer('claude', None, evidence,
                                                             TOKENS, REPORTED))))
        for record in (None, {}, {'developer': None}, {'developer': {'execution': {}}}):
            self.assertIsNone(wa.classify(record))
            self.assertIsNone(wa.worker_key(record))


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / 'tasks'
        os.mkdir(self.root, 0o700)
        os.chmod(self.root, 0o700)
        self.store = task_store.TaskStore(self.root)
        self.policy_path = worker_policy_fixture.install(self)
        patcher = mock.patch.object(worker_dispatch, 'repository_config', return_value=REPO)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)

    def quota(self, worker):
        return self.store.get_worker_observations()[worker][wa.QUOTA_LAYER]

    def observe(self, profile, code=None, evidence=None, now=None, **kwargs):
        record = _record(_developer(profile, code, evidence, TOKENS if evidence else None,
                                    REPORTED if evidence else 'UNKNOWN'))
        return wa.observe(self.store, record, now=now or self.now, **kwargs)

    def sidecar(self):
        return self.root / task_store.WORKER_AVAILABILITY_NAME


class ObserveTest(_StoreCase):
    def test_status_per_result_and_six_hour_freshness(self):
        rows = (('claude', 'CLAUDE_QUOTA_EXHAUSTED', QUOTA, 'claude-developer', 'FAIL'),
                ('opencode', 'OPENCODE_RATE_LIMITED', RATE, 'opencode-developer', 'FAIL'),
                ('agy', None, AGY_STATUS, 'agy', 'PASS'),
                ('gw-agent', 'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429', QUOTA,
                 'gw-agent', 'FAIL'))
        for profile, code, evidence, worker, status in rows:
            with self.subTest(worker=worker):
                self.assertIsNotNone(self.observe(profile, code, evidence))
                entry = self.quota(worker)
                self.assertEqual((entry['status'], entry['source']),
                                 (status, 'LOCAL_OBSERVATION'))
                self.assertEqual(entry['observed_at'], wa._stamp(self.now))
                self.assertEqual(entry['fresh_until'], wa._stamp(self.now + wa.FRESH_FOR))
                self.assertEqual(wa.fresh_quota_fail(self.store, worker), status == 'FAIL')

    def test_none_and_no_write_workers_write_nothing(self):
        cases = (('claude', 'CLAUDE_EXIT_NONZERO', None), ('agy', None, None),
                 ('codex', 'CODEX_PROVIDER_FAILED', QUOTA), ('company-gw', None, QUOTA))
        for profile, code, evidence in cases:
            with self.subTest(profile=profile):
                self.assertIsNone(self.observe(profile, code, evidence))
                self.assertFalse(self.sidecar().exists())
        for worker in wa.NO_WRITE_WORKERS:
            self.assertEqual(self.quota(worker)['status'], 'UNKNOWN')

    def test_reset_at_only_when_provider_reported_for_a_fail(self):
        reset = self.now + timedelta(hours=2)
        self.observe('claude', 'CLAUDE_QUOTA_EXHAUSTED', QUOTA, reset_at=wa._stamp(reset))
        self.assertEqual(self.quota('claude-developer')['fresh_until'], wa._stamp(reset))
        six = wa._stamp(self.now + wa.FRESH_FOR)
        for reset_at in (None, 'soon', wa._stamp(self.now - timedelta(hours=1)),
                         self.now + timedelta(days=31)):
            with self.subTest(reset_at=reset_at):
                self.observe('claude', 'CLAUDE_QUOTA_EXHAUSTED', QUOTA, reset_at=reset_at)
                self.assertEqual(self.quota('claude-developer')['fresh_until'], six)
        self.observe('agy', None, AGY_STATUS, reset_at=reset)
        self.assertEqual(self.quota('agy')['fresh_until'], six)

    def test_expired_fail_reads_unknown(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA,
                     now=self.now - timedelta(hours=7))
        self.assertEqual(self.quota('opencode-developer')['status'], 'UNKNOWN')
        self.assertFalse(wa.fresh_quota_fail(self.store, 'opencode-developer'))

    def test_unreadable_or_missing_reader_never_refuses(self):
        self.assertFalse(wa.fresh_quota_fail(SimpleNamespace(), 'agy'))
        broken = SimpleNamespace(get_worker_observations=mock.Mock(
            side_effect=LLMToolError('WORKER_AVAILABILITY_INVALID')))
        self.assertFalse(wa.fresh_quota_fail(broken, 'agy'))


class DelegateGateTest(_StoreCase):
    def run_checked(self, provider='opencode', order=None):
        args = SimpleNamespace(task_id=TASK, provider=provider, pm_session=None,
                               owner_ref=None, executor_task='d6-task')
        reader = self.store.get_worker_observations
        with mock.patch('llmtool.pm_caller.check_caller',
                        side_effect=lambda *a: order is not None and order.append('caller')), \
                mock.patch.object(self.store, 'get_worker_observations',
                                  side_effect=lambda: (order is not None and
                                                       order.append('quota'), reader())[1]), \
                mock.patch.object(cli, 'run_task_delegate', return_value=0) as delegate:
            try:
                return cli.run_task_delegate_checked(args, store=self.store), delegate
            except LLMToolError as error:
                return error, delegate

    def assert_no_task_write(self):
        self.assertEqual([path.name for path in self.root.iterdir()
                          if path.suffix == '.json' and not path.name.startswith('.')], [])

    def test_fresh_fail_refuses_after_caller_check_before_any_write(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA)
        policy = self.policy_path.read_bytes()
        order = []
        result, delegate = self.run_checked(order=order)
        self.assertIsInstance(result, LLMToolError)
        self.assertTrue(str(result).startswith('WORKER_QUOTA_UNAVAILABLE'))
        self.assertEqual(order, ['caller', 'quota'])
        delegate.assert_not_called()
        self.assert_no_task_write()
        self.assertEqual(self.policy_path.read_bytes(), policy)

    def test_owner_pause_comes_first_and_stays_paused(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA)
        worker_policy_fixture.update(self.policy_path, 'opencode-developer', 'PAUSED')
        policy = self.policy_path.read_bytes()
        result, delegate = self.run_checked()
        # The route reports its own Owner refusal; quota never masks it.
        self.assertEqual(result, 0)
        delegate.assert_called_once()
        self.assertEqual(self.policy_path.read_bytes(), policy)
        self.assertEqual(worker_policy_fixture.read(self.policy_path)['workers']
                         ['opencode-developer']['dispatch'], 'PAUSED')

    def test_pass_unknown_expired_and_other_worker_dispatch(self):
        # Cumulative on one store: UNKNOWN, another worker's FAIL, PASS, expired FAIL.
        cases = ((None, None, None, 0), ('agy', 'AGY_QUOTA_EXHAUSTED', QUOTA, 0),
                 ('opencode', None, STEP_USAGE, 0),
                 ('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, 7))
        for profile, code, evidence, hours_ago in cases:
            with self.subTest(profile=profile, code=code, hours_ago=hours_ago):
                if profile is not None:
                    self.observe(profile, code, evidence,
                                 now=self.now - timedelta(hours=hours_ago))
                result, delegate = self.run_checked('opencode')
                self.assertEqual(result, 0)
                delegate.assert_called_once()

    def test_review_repair_refuses_after_owner_policy(self):
        task = {'task_id': TASK, 'developer': {
            'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'opencode',
            'owner_assignment_ref': None}, 'completion': {'executor_task_id': 'd6-task'}}
        cases = (('FAIL', 'ALLOWED', 'WORKER_QUOTA_UNAVAILABLE'),
                 ('FAIL', 'PAUSED', 'OWNER_PAUSED'),
                 ('UNKNOWN', 'ALLOWED', 'REPAIR_COUNT_UNAVAILABLE'))
        for status, dispatch, code in cases:
            worker_policy_fixture.update(self.policy_path, 'opencode-developer', dispatch)
            store = SimpleNamespace(
                get=lambda task_id: copy.deepcopy(task),
                get_worker_observations=lambda: {
                    'opencode-developer': {wa.QUOTA_LAYER: {'status': status}}})
            with self.subTest(status=status, dispatch=dispatch), \
                    mock.patch.object(review_repair, '_failed_event',
                                      return_value=({'failed_head': HEAD}, None)), \
                    mock.patch.object(review_repair, '_current_d6_head', return_value=HEAD), \
                    mock.patch.object(review_repair, 'dispute_open_on', return_value=False):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair.repair_review(store=store, task_id=TASK,
                                                executor_task='d6-task', pr_number=1)
                self.assertEqual(caught.exception.review_repair_code, code)


class _FakeStore:
    def __init__(self, developer):
        self.record = _record(developer)
        self.observations = []

    def get(self, task_id):
        return copy.deepcopy(self.record)

    def record_worker_observation(self, **observation):
        self.observations.append(observation)


QUOTA_DEV = _developer('opencode', 'OPENCODE_FREE_LIMIT', QUOTA)


class OutcomeRecordingTest(unittest.TestCase):
    def delegate(self, store, effect):
        args = SimpleNamespace(task_id=TASK, provider='opencode', mode='proposal',
                               background=False, timeout=None, acceptance=['Edit x.txt.'])
        with mock.patch.object(cli, '_validate_acceptance_scope'), \
                mock.patch.object(cli, '_check_delegate_repair_cap'), \
                mock.patch.object(cli, '_delegate_timeout'), \
                mock.patch.object(cli, 'run_task_opencode_delegate', side_effect=effect):
            return cli.run_task_delegate(args, store=store)

    def write(self, store, developer, result=1):
        def effect(args, *, store=store):
            store.record['developer'] = copy.deepcopy(developer)
            if isinstance(result, BaseException):
                raise result
            return result
        return effect

    def test_foreground_outcome_is_recorded_once(self):
        store = _FakeStore(None)
        self.assertEqual(self.delegate(store, self.write(store, QUOTA_DEV)), 1)
        self.assertEqual([(item['worker_key'], item['layer'], item['status'])
                          for item in store.observations],
                         [('opencode-developer', 'quota', 'FAIL')])

    def test_unchanged_developer_is_not_rerecorded(self):
        store = _FakeStore(QUOTA_DEV)
        self.assertEqual(self.delegate(store, lambda args, store: 1), 1)
        self.assertEqual(store.observations, [])

    def test_raised_delegate_is_recorded_and_reraised(self):
        store = _FakeStore(None)
        with self.assertRaises(RuntimeError):
            self.delegate(store, self.write(store, QUOTA_DEV, RuntimeError('fixture')))
        self.assertEqual(len(store.observations), 1)

    def test_observation_failure_never_changes_the_result(self):
        store = _FakeStore(None)
        store.record_worker_observation = mock.Mock(side_effect=LLMToolError('busy'))
        self.assertEqual(self.delegate(store, self.write(store, QUOTA_DEV, 0)), 0)

    def test_detached_opencode_child_records(self):
        store = _FakeStore(None)
        sent = []
        patches = [mock.patch.object(cli.os, name, value) for name, value in (
            ('fork', mock.Mock(return_value=0)), ('setsid', mock.Mock()),
            ('open', mock.Mock(return_value=99)), ('dup2', mock.Mock()),
            ('close', mock.Mock()), ('pipe', mock.Mock(return_value=(97, 98))),
            ('_exit', mock.Mock(side_effect=SystemExit)))]
        patches += [mock.patch.object(cli, '_background_send',
                                      side_effect=lambda fd, value: sent.append(value)),
                    mock.patch.object(cli, 'run_task_opencode_delegate',
                                      side_effect=lambda args, store, d6_binding:
                                      self.write(store, QUOTA_DEV)(args))]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with self.assertRaises(SystemExit):
                cli._run_background_delegate(SimpleNamespace(task_id=TASK),
                                             provider='opencode', store=store,
                                             d6_binding=None)
        self.assertEqual(sent[-1]['kind'], 'finished')
        self.assertEqual([item['status'] for item in store.observations], ['FAIL'])


if __name__ == '__main__':
    unittest.main()
