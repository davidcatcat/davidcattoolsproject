"""#2129: worker quota recovery tests (freshness boundary, owner recovery, CLI wiring).

Uses scrubbed synthetic records and worker_policy_fixture; never touches real
worker-dispatch.json and uses no credential-shaped values (Owner #763 A).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
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

from llmtool.cli import run_worker_quota_recovered
from llmtool.errors import LLMToolError
from llmtool import task_store
from llmtool import worker_availability as wa
from llmtool import worker_dispatch
import worker_policy_fixture

TASK = '1' * 32
TOKENS = {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150}
REPORTED = 'CLI_REPORTED'
QUOTA = {'condition': 'QUOTA_EXHAUSTED'}
REPO = {'slug': 'davidcatcat/davidcattoolsproject', 'dispatch_issue': 289,
        'dispatch_evidence_pr': 1}
VALID_RELAY_URL = (
    'https://github.com/davidcatcat/davidcattoolsproject/issues/2129#issuecomment-6894474079'
)


def _developer(profile, code=None, evidence=None, usage=None, usage_source='UNKNOWN',
               state='BLOCKED', status='BLOCKED'):
    execution = None
    if evidence is not None:
        execution = {
            'status': status, 'native_session_id': None, 'process_exit_code': 0,
            'stdout_sha256': 'e' * 64, 'elapsed_ms': 1500,
            'cleanup_status': 'CONFIRMED', 'usage_source': usage_source,
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None,
            'provider_evidence': dict(evidence),
        }
    return {
        'assignment_id': '2' * 32, 'provider_profile': profile, 'state': state,
        'error_code': code, 'usage': None if usage is None else dict(usage),
        'artifact': None, 'execution': execution,
    }


def _record(developer):
    return {'task_id': TASK, 'state': 'PM_RESPONDED', 'developer': developer}


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


class QuotaFreshnessWindowTest(_StoreCase):
    def test_thirty_minute_freshness_boundary(self):
        # 31 minutes ago: expired, reads UNKNOWN, fresh_quota_fail is False, does not refuse.
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA,
                     now=self.now - timedelta(minutes=31))
        self.assertEqual(self.quota('opencode-developer')['status'], 'UNKNOWN')
        self.assertFalse(wa.fresh_quota_fail(self.store, 'opencode-developer'))
        self.assertIsNone(wa.quota_refusal(self.store, 'opencode-developer'))

        # 20 minutes ago: still fresh, reads FAIL, fresh_quota_fail is True, still refuses.
        self.observe('claude', 'CLAUDE_QUOTA_EXHAUSTED', QUOTA,
                     now=self.now - timedelta(minutes=20))
        self.assertEqual(self.quota('claude-developer')['status'], 'FAIL')
        self.assertTrue(wa.fresh_quota_fail(self.store, 'claude-developer'))
        self.assertEqual(wa.quota_refusal(self.store, 'claude-developer'), wa.REFUSAL_CODE)


class RecordOwnerRecoveryTest(_StoreCase):
    def test_recovery_after_fresh_fail_clears_refusal(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA,
                     now=self.now - timedelta(minutes=5))
        self.assertTrue(wa.fresh_quota_fail(self.store, 'opencode-developer'))
        self.assertEqual(self.quota('opencode-developer')['status'], 'FAIL')

        result = wa.record_owner_recovery(
            self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now)

        self.assertFalse(wa.fresh_quota_fail(self.store, 'opencode-developer'))
        entry = self.quota('opencode-developer')
        self.assertEqual(entry['status'], 'PASS')
        self.assertEqual(entry['source'], 'AUTHORIZED_OPERATION')
        expected_sha = hashlib.sha256(VALID_RELAY_URL.encode('utf-8')).hexdigest()
        self.assertEqual(entry['evidence_sha256'], expected_sha)
        self.assertEqual(entry['observed_at'], wa._stamp(self.now))
        self.assertEqual(entry['fresh_until'], wa._stamp(self.now + wa.FRESH_FOR))

        self.assertEqual(result['worker'], 'opencode-developer')
        self.assertEqual(result['owner_relay_url'], VALID_RELAY_URL)
        self.assertEqual(result['evidence_sha256'], expected_sha)
        self.assertEqual(result['before']['status'], 'FAIL')
        self.assertEqual(result['after']['status'], 'PASS')

    def test_invalid_url_refuses_and_writes_nothing(self):
        bad_urls = [
            None,
            '',
            'https://github.com/davidcatcat/davidcattoolsproject/issues/2129',
            'https://github.com/otheruser/davidcattoolsproject/issues/2129#issuecomment-6894474079',
            'https://github.com/davidcatcat/otherrepo/issues/2129#issuecomment-6894474079',
            'https://example.com/issues/2129#issuecomment-6894474079',
            'https://github.com/davidcatcat/davidcattoolsproject/issues/0#issuecomment-1',
            'https://github.com/davidcatcat/davidcattoolsproject/issues/1#issuecomment-0',
            'not-a-url',
        ]
        for bad_url in bad_urls:
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(self.store, 'opencode-developer', bad_url)
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_REF_INVALID')
                self.assertFalse(self.sidecar().exists())

    def test_invalid_worker_refuses_and_writes_nothing(self):
        for bad_worker in ('codex-developer', 'company-gw', 'unknown-worker', 'other', '', None):
            with self.subTest(bad_worker=bad_worker):
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(self.store, bad_worker, VALID_RELAY_URL)
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_WORKER_INVALID')
                self.assertFalse(self.sidecar().exists())


class OwnerPausePrecedenceAndCliTest(_StoreCase):
    def test_paused_still_wins_and_policy_unchanged(self):
        worker_policy_fixture.update(self.policy_path, 'opencode-developer', 'PAUSED')
        policy_bytes_before = self.policy_path.read_bytes()

        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now)
        result = wa.record_owner_recovery(
            self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now)

        self.assertEqual(result['after']['status'], 'PASS')
        self.assertEqual(self.quota('opencode-developer')['status'], 'PASS')

        # quota_refusal returns None because evaluate() denies due to OWNER_PAUSED
        self.assertIsNone(wa.quota_refusal(self.store, 'opencode-developer'))
        decision = worker_dispatch.evaluate('opencode-developer')
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_PAUSED')

        # worker-dispatch.json bytes are completely unchanged
        self.assertEqual(self.policy_path.read_bytes(), policy_bytes_before)

    def test_cli_handler_success_and_error(self):
        # 0 return and one JSON line output on success
        args = SimpleNamespace(worker='opencode-developer', owner_relay=VALID_RELAY_URL)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
            code = run_worker_quota_recovered(args, store=self.store)

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), '')
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data['worker'], 'opencode-developer')
        self.assertEqual(data['owner_relay_url'], VALID_RELAY_URL)
        self.assertEqual(data['after']['status'], 'PASS')

        # 2 return and code printed to stderr on bad URL
        bad_args = SimpleNamespace(worker='opencode-developer', owner_relay='bad-url')
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
            bad_code = run_worker_quota_recovered(bad_args, store=self.store)

        self.assertEqual(bad_code, 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue().strip(), 'WORKER_QUOTA_RECOVERY_REF_INVALID')


if __name__ == '__main__':
    unittest.main()
