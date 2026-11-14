"""#1816 worker quota availability: quota_gate, record_outcome and their wiring.

Every test that can reach quota_gate or worker_dispatch.evaluate injects a
synthetic policy file and repository config (_use_policy), so no test reads
the real worker-dispatch.json and results match on any machine or checkout.
No credential-shaped data is used: digests are computed at run time.
"""

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from llmtool import (pm_caller, review_repair, task_submission_cli,
                     worker_availability, worker_dispatch)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, WORKER_KEYS, WORKER_LAYERS, _digest, new_id

_SLUG = 'davidcatcat/davidcattoolsproject'
_EVIDENCE_URL = 'https://github.com/' + _SLUG + '/issues/120'
_FIXTURE_DIGEST = hashlib.sha256(b'synthetic quota fixture').hexdigest()


def _stamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _developer(profile='opencode', state='DEVELOPER_RESPONDED', error_code=None, **extra):
    value = {'assignment_id': new_id(), 'provider_profile': profile,
             'state': state, 'error_code': error_code}
    value.update(extra)
    return value


def _opencode_attempted(error_code='OPENCODE_STEPS_EXHAUSTED', **execution):
    """The record task_submission_cli persists for an attempted OpenCode adapter
    error: DEVELOPER_OUTCOME_UNKNOWN with execution status UNKNOWN."""
    value = {'status': 'UNKNOWN', 'native_session_id': 'ses_' + new_id(),
             'process_exit_code': 0,
             'stdout_sha256': hashlib.sha256(b'synthetic opencode output').hexdigest(),
             'elapsed_ms': 2400, 'cleanup_status': 'CONFIRMED',
             'usage_source': 'UNKNOWN', 'source_bytes': None,
             'source_file_count': None, 'source_manifest_sha256': None}
    value.update(execution)
    return _developer(state='DEVELOPER_OUTCOME_UNKNOWN', error_code=error_code,
                      provider_type='opencode-cli', model='synthetic-model',
                      max_tokens=1024, execution=value)


class _Store(TaskStore):
    """A real TaskStore for worker observations; Task records live in memory."""

    def __init__(self, root):
        super().__init__(root)
        self.records = {}
        self.writes = []

    def get(self, task_id):
        if task_id not in self.records:
            raise LLMToolError('task not found')
        return copy.deepcopy(self.records[task_id])

    def update(self, task_id, **changes):
        self.writes.append(('update', task_id))

    def compare_and_update(self, task_id, **changes):
        self.writes.append(('compare_and_update', task_id))


class _Fixture(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.store = _Store(self.tmp / 'store')

    def _use_policy(self, states=None):
        """Inject a synthetic Owner policy; every worker ALLOWED unless named."""
        states = dict(states or {})
        workers = {}
        for worker in sorted(worker_dispatch.WORKERS):
            state = states.get(worker, 'ALLOWED')
            if worker == 'gw-agent':
                entry = ({'dispatch': 'ALLOWED', 'decided_by': 'Owner',
                          'evidence': worker_dispatch._GW_AGENT_OWNER_REF}
                         if state == 'ALLOWED' else
                         {'dispatch': 'PAUSED', 'decided_by': 'PM',
                          'evidence': worker_dispatch._GW_AGENT_PAUSED_REF})
            else:
                entry = {'dispatch': state, 'decided_by': 'Owner', 'evidence': _EVIDENCE_URL}
            entry.update(decided_at='2026-11-01', reason='Synthetic test policy.')
            workers[worker] = entry
        self.policy_path = self.tmp / 'worker-dispatch-fixture.json'
        self.policy_path.write_text(json.dumps({'schema_version': 1, 'workers': workers}))
        for patcher in (
                mock.patch.object(worker_dispatch, '_POLICY_PATH', self.policy_path),
                mock.patch.object(worker_dispatch, 'repository_config', return_value={
                    'slug': _SLUG, 'dispatch_issue': 120, 'dispatch_evidence_pr': 121})):
            patcher.start()
            self.addCleanup(patcher.stop)
        return self.policy_path

    def _observe(self, worker, status, *, observed_at, fresh_until, **reset):
        self.store.record_worker_observation(
            worker_key=worker, layer='quota', status=status,
            observed_at=_stamp(observed_at), fresh_until=_stamp(fresh_until),
            source='TEST_FIXTURE', evidence_sha256=_FIXTURE_DIGEST, **reset)

    def _fresh_fail(self, worker):
        now = _utcnow()
        self._observe(worker, 'FAIL', observed_at=now - timedelta(minutes=5),
                      fresh_until=now + timedelta(hours=5))

    def _status(self, worker):
        return worker_availability.quota_status(self.store, worker)['status']


class TaskStoreQuotaLayerTests(_Fixture):

    def test_layer_and_gw_agent_are_known(self):
        self.assertIn('quota', WORKER_LAYERS)
        self.assertIn('gw-agent', WORKER_KEYS)
        view = self.store.get_worker_observations()
        self.assertEqual(view['gw-agent']['quota']['status'], 'UNKNOWN')

    def test_provider_reported_reset_is_stored_and_shown(self):
        now = _utcnow()
        reset = now + timedelta(hours=2)
        self._observe('opencode-developer', 'FAIL', observed_at=now, fresh_until=reset,
                      reset_at=_stamp(reset), reset_source='PROVIDER_REPORTED')
        shown = worker_availability.quota_status(self.store, 'opencode-developer')
        self.assertEqual(shown['status'], 'FAIL')
        self.assertEqual(shown['reset_at'], _stamp(reset))
        self.assertEqual(shown['reset_source'], 'PROVIDER_REPORTED')

    def test_reset_fields_validate_strictly(self):
        now = _utcnow()
        reset = now + timedelta(hours=2)
        bad = (
            {'layer': 'runtime', 'status': 'FAIL', 'reset_source': 'PROVIDER_REPORTED'},
            {'layer': 'quota', 'status': 'PASS', 'reset_source': 'PROVIDER_REPORTED'},
            {'layer': 'quota', 'status': 'FAIL', 'reset_source': 'ESTIMATED'},
            {'layer': 'quota', 'status': 'FAIL', 'reset_source': None},
        )
        for case in bad:
            with self.subTest(case=case), self.assertRaises(LLMToolError):
                self.store.record_worker_observation(
                    worker_key='agy', layer=case['layer'], status=case['status'],
                    observed_at=_stamp(now), fresh_until=_stamp(reset),
                    source='TEST_FIXTURE', evidence_sha256=_FIXTURE_DIGEST,
                    reset_at=_stamp(reset), reset_source=case['reset_source'])
        with self.assertRaises(LLMToolError):   # reset_at must be fresh_until
            self.store.record_worker_observation(
                worker_key='agy', layer='quota', status='FAIL',
                observed_at=_stamp(now), fresh_until=_stamp(reset),
                source='TEST_FIXTURE', evidence_sha256=_FIXTURE_DIGEST,
                reset_at=_stamp(reset + timedelta(hours=1)),
                reset_source='PROVIDER_REPORTED')
        self.assertEqual(self._status('agy'), 'UNKNOWN')

    def test_old_sidecar_stays_valid(self):
        now = _utcnow()
        item = {'worker_key': 'agy', 'layer': 'runtime', 'status': 'PASS',
                'observed_at': _stamp(now), 'fresh_until': _stamp(now + timedelta(hours=1)),
                'source': 'TEST_FIXTURE', 'evidence_sha256': _FIXTURE_DIGEST}
        record = {'schema_version': 1, 'observations': [item]}
        record['record_sha256'] = _digest(record)
        self.assertIs(TaskStore._validate_worker_availability(record), record)
        extra = dict(item, reset_at=_stamp(now + timedelta(hours=1)),
                     reset_source='PROVIDER_REPORTED')
        record = {'schema_version': 1, 'observations': [extra]}
        record['record_sha256'] = _digest(record)
        with self.assertRaises(LLMToolError):
            TaskStore._validate_worker_availability(record)


class OutcomeClassificationTests(_Fixture):

    def test_only_quota_and_rate_limit_codes_write_fail(self):
        for code in ('DEVELOPER_OPENCODE_QUOTA_EXHAUSTED', 'DEVELOPER_AGY_QUOTA_EXHAUSTED',
                     'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429',
                     'DEVELOPER_GW_AGENT_HTTP_429', 'DEVELOPER_CLAUDE_RATE_LIMITED'):
            with self.subTest(code=code):
                developer = _developer(state='DEVELOPER_UNAVAILABLE', error_code=code)
                self.assertEqual(worker_availability.classify_outcome(developer), 'FAIL')

    def test_non_quota_failures_never_write_fail(self):
        cases = (
            _developer(state='DEVELOPER_OUTCOME_UNKNOWN', error_code='DEVELOPER_OPENCODE_TIMEOUT'),
            _developer(state='DEVELOPER_OUTCOME_UNKNOWN', error_code='DEVELOPER_RESULT_UNKNOWN'),
            _developer(state='DEVELOPER_UNAVAILABLE', error_code='OWNER_PAUSED'),
            _developer(state='DEVELOPER_UNAVAILABLE', error_code='WORKER_DISPATCH_TRANSITION_NOT_FOUND'),
            _developer(state='DEVELOPER_UNAVAILABLE', error_code='DEVELOPER_OPENCODE_EXIT_NONZERO'),
            _developer(state='DEVELOPER_OUTPUT_REJECTED', error_code='DEVELOPER_OUTPUT_APPLY_FAILED'),
            _developer(state='DEVELOPER_RESPONDED', error_code=None,
                       test_result={'state': 'FAIL'}),
        )
        for developer in cases:
            with self.subTest(code=developer['error_code']):
                self.assertNotEqual(worker_availability.classify_outcome(developer), 'FAIL')
                worker_availability.record_outcome(
                    self.store, 'opencode-developer', developer, task_id=new_id())
                self.assertNotEqual(self._status('opencode-developer'), 'FAIL')

    def test_timeouts_and_refusals_record_nothing(self):
        for developer in (
                _developer(state='DEVELOPER_OUTCOME_UNKNOWN', error_code='DEVELOPER_OPENCODE_TIMEOUT'),
                _developer(state='DEVELOPER_UNAVAILABLE', error_code='OWNER_PAUSED')):
            self.assertIsNone(worker_availability.record_outcome(
                self.store, 'opencode-developer', developer, task_id=new_id()))
        self.assertEqual(self._status('opencode-developer'), 'UNKNOWN')

    def test_evidenced_non_quota_outcome_writes_pass(self):
        developer = _developer(
            state='DEVELOPER_UNAVAILABLE', error_code='DEVELOPER_OPENCODE_MODEL_MISMATCH',
            execution={'status': 'DONE', 'process_exit_code': 0})
        self.assertEqual(worker_availability.record_outcome(
            self.store, 'opencode-developer', developer, task_id=new_id()), 'PASS')
        self.assertEqual(self._status('opencode-developer'), 'PASS')
        rejected = _developer(state='DEVELOPER_OUTPUT_REJECTED',
                              error_code='DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertEqual(worker_availability.record_outcome(
            self.store, 'agy', rejected, task_id=new_id()), 'PASS')

    @staticmethod
    def _claude_blocked(error_code, **execution):
        """The record task_submission_cli persists when a Claude run returned
        and the host then blocked its result (e.g. CLAUDE_NO_CHANGES)."""
        value = {'status': 'BLOCKED', 'native_session_id': new_id(),
                 'process_exit_code': 0,
                 'stdout_sha256': hashlib.sha256(b'synthetic stdout').hexdigest(),
                 'elapsed_ms': 1200, 'cleanup_status': 'CONFIRMED',
                 'usage_source': 'UNKNOWN', 'source_bytes': None,
                 'source_file_count': None, 'source_manifest_sha256': None}
        value.update(execution)
        return _developer(profile='claude', state='BLOCKED', error_code=error_code,
                          provider_type='claude-cli', artifact=None, artifact_sha256=None,
                          execution=value, usage=None)

    def test_claude_no_changes_record_writes_pass(self):
        for code in ('CLAUDE_NO_CHANGES', 'CLAUDE_WRITE_SCOPE_VIOLATION',
                     'CLAUDE_OUTPUT_TOO_LARGE'):
            with self.subTest(code=code):
                developer = self._claude_blocked(code)
                self.assertEqual(worker_availability.classify_outcome(developer), 'PASS')
        task_id = new_id()
        self.store.records[task_id] = {'task_id': task_id, 'developer': None}
        before = worker_availability.developer_snapshot(self.store, task_id)
        self.store.records[task_id]['developer'] = self._claude_blocked('CLAUDE_NO_CHANGES')
        self.assertEqual(worker_availability.record_task_outcome(self.store, task_id, before),
                         'PASS')
        self.assertEqual(self._status('claude-developer'), 'PASS')

    def test_blocked_without_response_evidence_records_nothing(self):
        cases = (
            # Prelaunch refusal: no execution was ever observed.
            _developer(profile='claude', state='BLOCKED', error_code='CLAUDE_DISPATCH_DENIED',
                       execution=None),
            _developer(profile='claude', state='BLOCKED', error_code='CLAUDE_D6_NOT_REGISTERED'),
            # BLOCKED without a returned session or output digest.
            self._claude_blocked('CLAUDE_NO_CHANGES', native_session_id=None),
            self._claude_blocked('CLAUDE_NO_CHANGES', stdout_sha256=None),
            self._claude_blocked('CLAUDE_NO_CHANGES', process_exit_code=None),
            # Timeouts and interruptions never count, whatever their fields say.
            self._claude_blocked('CLAUDE_TIMEOUT'),
            self._claude_blocked('CODEX_INTERRUPTED'),
            # A timeout status is not a provider result.
            _developer(state='DEVELOPER_OUTCOME_UNKNOWN', error_code='CODEX_TIMEOUT',
                       execution={'status': 'TIMEOUT', 'process_exit_code': 0}),
        )
        for developer in cases:
            with self.subTest(code=developer['error_code']):
                self.assertIsNone(worker_availability.classify_outcome(developer))
                self.assertIsNone(worker_availability.record_outcome(
                    self.store, 'claude-developer', developer, task_id=new_id()))
        self.assertEqual(self._status('claude-developer'), 'UNKNOWN')

    def test_blocked_quota_code_still_writes_fail(self):
        developer = self._claude_blocked('DEVELOPER_CLAUDE_RATE_LIMITED')
        self.assertEqual(worker_availability.classify_outcome(developer), 'FAIL')

    def test_opencode_steps_exhausted_record_writes_pass(self):
        # The persisted shape: Task outcome UNKNOWN, provider completed.
        developer = _opencode_attempted()
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(worker_availability.classify_outcome(developer), 'PASS')
        task_id = new_id()
        self.store.records[task_id] = {'task_id': task_id, 'developer': None}
        before = worker_availability.developer_snapshot(self.store, task_id)
        self.store.records[task_id]['developer'] = developer
        self.assertEqual(worker_availability.record_task_outcome(self.store, task_id, before),
                         'PASS')
        self.assertEqual(self._status('opencode-developer'), 'PASS')
        # The Developer outcome itself stays UNKNOWN; only quota reads PASS.
        self.assertEqual(self.store.records[task_id]['developer']['state'],
                         'DEVELOPER_OUTCOME_UNKNOWN')

    def test_unknown_without_completion_evidence_records_nothing(self):
        cases = (
            # Missing or malformed response evidence.
            _opencode_attempted(native_session_id=None),
            _opencode_attempted(native_session_id=''),
            _opencode_attempted(stdout_sha256=None),
            _opencode_attempted(stdout_sha256='not-a-digest'),
            # Nonzero or unknown exit.
            _opencode_attempted(process_exit_code=1),
            _opencode_attempted(process_exit_code=None),
            # Cleanup not confirmed.
            _opencode_attempted(cleanup_status='UNKNOWN'),
            # Timeout, interruption and other incomplete runs, even with output.
            _opencode_attempted('OPENCODE_TIMEOUT'),
            _opencode_attempted('DEVELOPER_INTERRUPTED'),
            _opencode_attempted('OPENCODE_ADAPTER_UNKNOWN'),
            _opencode_attempted('OPENCODE_EVENT_STREAM_INVALID'),
            # A timeout status is never a completion.
            _opencode_attempted(status='TIMEOUT'),
            # Prelaunch refusal: not attempted, no execution.
            _developer(state='DEVELOPER_UNAVAILABLE', error_code='OPENCODE_STEPS_EXHAUSTED'),
        )
        for developer in cases:
            with self.subTest(code=developer['error_code'],
                              execution=developer.get('execution')):
                self.assertIsNone(worker_availability.classify_outcome(developer))
                self.assertIsNone(worker_availability.record_outcome(
                    self.store, 'opencode-developer', developer, task_id=new_id()))
        self.assertEqual(self._status('opencode-developer'), 'UNKNOWN')

    def test_unknown_quota_code_still_writes_fail(self):
        for developer in (_opencode_attempted('DEVELOPER_OPENCODE_QUOTA_EXHAUSTED'),
                          _opencode_attempted('DEVELOPER_OPENCODE_QUOTA_EXHAUSTED',
                                              process_exit_code=1, cleanup_status='UNKNOWN')):
            with self.subTest(execution=developer['execution']):
                self.assertEqual(worker_availability.classify_outcome(developer), 'FAIL')

    def test_fail_freshness_is_six_hours_without_trusted_reset(self):
        developer = _developer(state='DEVELOPER_UNAVAILABLE',
                               error_code='DEVELOPER_OPENCODE_QUOTA_EXHAUSTED')
        worker_availability.record_outcome(self.store, 'opencode-developer', developer,
                                           task_id=new_id(), reset_at='soon')
        shown = worker_availability.quota_status(self.store, 'opencode-developer')
        observed = datetime.strptime(shown['observed_at'], '%Y-%m-%dT%H:%M:%SZ')
        fresh = datetime.strptime(shown['fresh_until'], '%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual(fresh - observed, timedelta(hours=6))
        self.assertNotIn('reset_at', shown)

    def test_fail_freshness_uses_trusted_reset(self):
        reset = _stamp(_utcnow() + timedelta(hours=2))
        developer = _developer(state='DEVELOPER_UNAVAILABLE',
                               error_code='DEVELOPER_AGY_QUOTA_EXHAUSTED')
        worker_availability.record_outcome(self.store, 'agy', developer,
                                           task_id=new_id(), reset_at=reset)
        shown = worker_availability.quota_status(self.store, 'agy')
        self.assertEqual((shown['fresh_until'], shown['reset_at'], shown['reset_source']),
                         (reset, reset, 'PROVIDER_REPORTED'))


class QuotaGateTests(_Fixture):

    def setUp(self):
        super().setUp()
        self._use_policy({'codex-developer': 'PAUSED'})

    def test_absent_reads_unknown_and_does_not_block(self):
        gate = worker_availability.quota_gate(self.store, 'opencode-developer')
        self.assertFalse(gate.refused)
        self.assertEqual(gate.quota['status'], 'UNKNOWN')

    def test_expired_is_unknown_never_pass(self):
        now = _utcnow()
        for status in ('PASS', 'FAIL'):
            with self.subTest(status=status):
                self._observe('opencode-developer', status,
                              observed_at=now - timedelta(hours=8),
                              fresh_until=now - timedelta(hours=2))
                gate = worker_availability.quota_gate(self.store, 'opencode-developer')
                self.assertEqual(gate.quota['status'], 'UNKNOWN')
                self.assertFalse(gate.refused)

    def test_fresh_fail_refuses_allowed_worker(self):
        self._fresh_fail('opencode-developer')
        gate = worker_availability.quota_gate(self.store, 'opencode-developer')
        self.assertTrue(gate.refused)
        self.assertEqual(gate.reason_code, 'WORKER_QUOTA_UNAVAILABLE')
        self.assertEqual(gate.dispatch_decision, 'ALLOWED')

    def test_paused_worker_is_left_to_policy(self):
        self._fresh_fail('codex-developer')
        gate = worker_availability.quota_gate(self.store, 'codex-developer')
        self.assertFalse(gate.refused)
        self.assertEqual((gate.dispatch_decision, gate.dispatch_reason_code),
                         ('BLOCKED', 'OWNER_PAUSED'))

    def test_gw_agent_stored_and_gated(self):
        developer = _developer(profile='gw-agent', state='DEVELOPER_UNAVAILABLE',
                               error_code='DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429')
        self.assertEqual(worker_availability.record_outcome(
            self.store, 'gw-agent', developer, task_id=new_id()), 'FAIL')
        self.assertEqual(self.store.get_worker_observations()['gw-agent']['quota']['status'],
                         'FAIL')
        gate = worker_availability.quota_gate(self.store, 'gw-agent')
        self.assertTrue(gate.refused)
        self.assertEqual(gate.reason_code, 'WORKER_QUOTA_UNAVAILABLE')

    def test_unreadable_observations_fail_closed_after_policy(self):
        with mock.patch.object(self.store, 'get_worker_observations',
                               side_effect=LLMToolError('unreadable')):
            gate = worker_availability.quota_gate(self.store, 'opencode-developer')
            self.assertEqual(gate.reason_code, 'WORKER_QUOTA_UNREADABLE')
            paused = worker_availability.quota_gate(self.store, 'codex-developer')
            self.assertFalse(paused.refused)


class OwnerCaseTests(_Fixture):
    """The four Owner cases through the ``task delegate`` CLI entry."""

    def setUp(self):
        super().setUp()
        self._use_policy({'codex-developer': 'PAUSED'})
        patcher = mock.patch.object(pm_caller, 'check_caller', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.task_id = new_id()
        self.store.records[self.task_id] = {'task_id': self.task_id, 'developer': None}

    def _args(self, provider='opencode', **extra):
        values = dict(task_id=self.task_id, provider=provider, pm_session=None,
                      owner_ref=None, executor_task='d6-fixture', background=False,
                      mode='direct', timeout=None, acceptance=['Add one line.'])
        values.update(extra)
        return SimpleNamespace(**values)

    def _run(self, args, delegate):
        output = io.StringIO()
        with mock.patch.object(task_submission_cli, 'run_task_delegate',
                               side_effect=delegate) as fake, \
                contextlib.redirect_stdout(output):
            result = task_submission_cli.run_task_delegate_checked(args, store=self.store)
        return result, fake, output.getvalue()

    def _files(self):
        return sorted(os.listdir(self.store.root)) if self.store.root.exists() else []

    def test_allowed_and_available_dispatches(self):
        result, fake, _ = self._run(self._args(), lambda args, store: 0)
        self.assertEqual(result, 0)
        fake.assert_called_once()

    def test_allowed_and_fresh_fail_is_refused_before_any_write(self):
        self._fresh_fail('opencode-developer')
        policy_before = self.policy_path.read_bytes()
        policy_stat = os.stat(self.policy_path)
        records_before = copy.deepcopy(self.store.records)
        files_before = self._files()
        result, fake, printed = self._run(self._args(), lambda args, store: 0)
        self.assertEqual(result, 1)
        fake.assert_not_called()
        view = json.loads(printed)
        self.assertEqual(view['error_code'], 'WORKER_QUOTA_UNAVAILABLE')
        self.assertEqual(view['worker_dispatch']['decision'], 'ALLOWED')
        self.assertEqual(view['quota']['status'], 'FAIL')
        self.assertNotIn('reset_at', view['quota'])
        # No Task, developer record, D6 or policy write; no count is used.
        self.assertEqual(self.store.records, records_before)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self._files(), files_before)
        self.assertEqual(self.policy_path.read_bytes(), policy_before)
        self.assertEqual(os.stat(self.policy_path).st_mtime_ns, policy_stat.st_mtime_ns)
        self.assertTrue(worker_dispatch.evaluate('opencode-developer').allowed)

    def test_paused_is_refused_by_policy_first(self):
        # The real delegate runs: the Owner switch refuses it before any write,
        # with the quota available (absent) and with a fresh quota FAIL.
        for fail in (False, True):
            with self.subTest(fresh_fail=fail):
                if fail:
                    self._fresh_fail('codex-developer')
                args = self._args(provider='codex', background=True)
                with contextlib.redirect_stdout(io.StringIO()), \
                        self.assertRaisesRegex(LLMToolError, 'OWNER_PAUSED'):
                    task_submission_cli.run_task_delegate_checked(args, store=self.store)
                self.assertEqual(self.store.writes, [])

    def test_recovered_and_allowed_dispatches(self):
        self._fresh_fail('claude-developer')
        result, fake, _ = self._run(self._args(provider='claude'), lambda args, store: 0)
        self.assertEqual(result, 1)
        fake.assert_not_called()
        worker_availability.record_outcome(
            self.store, 'claude-developer', _developer(profile='claude'), task_id=self.task_id)
        result, fake, _ = self._run(self._args(provider='claude'), lambda args, store: 0)
        self.assertEqual(result, 0)
        fake.assert_called_once()

    def test_run_outcome_is_recorded(self):
        def quota_run(args, store):
            store.records[args.task_id]['developer'] = _developer(
                state='DEVELOPER_UNAVAILABLE', error_code='DEVELOPER_OPENCODE_QUOTA_EXHAUSTED')
            return 1
        self._run(self._args(), quota_run)
        self.assertEqual(self._status('opencode-developer'), 'FAIL')

    def test_steps_exhausted_run_records_pass(self):
        def steps_run(args, store):
            store.records[args.task_id]['developer'] = _opencode_attempted()
            return 1
        result, _, _ = self._run(self._args(), steps_run)
        self.assertEqual(result, 1)
        self.assertEqual(self._status('opencode-developer'), 'PASS')

    def test_refused_run_records_nothing(self):
        stale = _developer(state='DEVELOPER_UNAVAILABLE',
                           error_code='DEVELOPER_OPENCODE_QUOTA_EXHAUSTED')
        self.store.records[self.task_id]['developer'] = stale

        def refused(args, store):
            raise LLMToolError('OWNER_PAUSED')
        with self.assertRaises(LLMToolError):
            self._run(self._args(), refused)
        self.assertEqual(self._status('opencode-developer'), 'UNKNOWN')

    def test_timeout_run_never_writes_fail(self):
        def timed_out(args, store):
            store.records[args.task_id]['developer'] = _developer(
                state='DEVELOPER_OUTCOME_UNKNOWN', error_code='DEVELOPER_OPENCODE_TIMEOUT')
            return 1
        self._run(self._args(), timed_out)
        self.assertEqual(self._status('opencode-developer'), 'UNKNOWN')


class BackgroundCompletionTests(_Fixture):

    def setUp(self):
        super().setUp()
        self._use_policy()
        self.task_id = new_id()
        self.store.records[self.task_id] = {'task_id': self.task_id, 'developer': None}

    def _child(self, developer):
        def finish(args, store, d6_binding):
            store.records[args.task_id]['developer'] = developer
            return 0
        before = worker_availability.developer_snapshot(self.store, self.task_id)
        with mock.patch.object(task_submission_cli, 'run_task_opencode_delegate',
                               side_effect=finish):
            return task_submission_cli._background_delegate_body(
                SimpleNamespace(task_id=self.task_id), provider='opencode',
                store=self.store, d6_binding=None, quota_before=before)

    def test_detached_completion_writes_pass(self):
        self.assertEqual(self._child(_developer()), 0)
        self.assertEqual(self._status('opencode-developer'), 'PASS')

    def test_detached_quota_writes_fail(self):
        self._child(_developer(state='DEVELOPER_UNAVAILABLE',
                               error_code='DEVELOPER_OPENCODE_QUOTA_EXHAUSTED'))
        self.assertEqual(self._status('opencode-developer'), 'FAIL')

    def test_detached_steps_exhausted_writes_pass(self):
        self._child(_opencode_attempted())
        self.assertEqual(self._status('opencode-developer'), 'PASS')

    def test_detached_incomplete_unknown_records_nothing(self):
        self._child(_opencode_attempted('OPENCODE_ADAPTER_UNKNOWN'))
        self.assertEqual(self._status('opencode-developer'), 'UNKNOWN')


class ReviewRepairGateTests(_Fixture):

    def setUp(self):
        super().setUp()
        self._use_policy()
        self.task_id = new_id()
        self.head = hashlib.sha1(b'synthetic head').hexdigest()
        self.store.records[self.task_id] = {
            'task_id': self.task_id, 'record_sha256': _FIXTURE_DIGEST,
            'developer': _developer(owner_assignment_ref=None),
            'completion': {'executor_task_id': 'd6-fixture'},
        }
        for patcher in (
                mock.patch.object(review_repair, '_failed_event',
                                  return_value=({'failed_head': self.head, 'version': 1}, None)),
                mock.patch.object(review_repair, '_current_d6_head', return_value=self.head)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _repair(self, **extra):
        return review_repair.repair_review(
            store=self.store, task_id=self.task_id, executor_task='d6-fixture',
            pr_number=12, **extra)

    def test_fresh_fail_refuses_before_claim_or_delegate(self):
        self._fresh_fail('opencode-developer')
        recorder, delegate = mock.Mock(), mock.Mock()
        with self.assertRaises(LLMToolError) as caught:
            self._repair(repair_task_recorder=recorder, delegate_callable=delegate)
        self.assertEqual(caught.exception.review_repair_code, 'WORKER_QUOTA_UNAVAILABLE')
        recorder.assert_not_called()
        delegate.assert_not_called()
        self.assertEqual(self.store.writes, [])

    def test_available_passes_the_gate(self):
        with mock.patch.object(self.store, 'task_ids', side_effect=RuntimeError):
            with self.assertRaises(LLMToolError) as caught:
                self._repair()
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_COUNT_UNAVAILABLE')

    def test_repair_delegate_outcome_is_recorded(self):
        repair_id = new_id()
        self.store.records[repair_id] = {'task_id': repair_id, 'developer': None}

        def delegate(args, store):
            store.records[args.task_id]['developer'] = _developer(
                state='DEVELOPER_UNAVAILABLE', error_code='DEVELOPER_OPENCODE_QUOTA_EXHAUSTED')
            return 1
        registration = SimpleNamespace(root=str(self.tmp))
        with mock.patch.object(review_repair, '_registered_d6',
                               return_value=(registration, None)):
            review_repair._delegate_repair(
                task={'task_id': repair_id, 'pm_assignment': {'acceptance': ['Add one line.']}},
                developer=_developer(read_paths=['a.py'], write_paths=['a.py']),
                packet={'task': self.task_id}, executor_task='d6-fixture',
                delegate=delegate, store=self.store)
        self.assertEqual(self._status('opencode-developer'), 'FAIL')


if __name__ == '__main__':
    unittest.main()
