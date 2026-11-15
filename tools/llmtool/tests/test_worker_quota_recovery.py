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
STEP_USAGE = {'basis': 'STEP_USAGE', 'accepted': True}
REPO = {'slug': 'davidcatcat/davidcattoolsproject', 'dispatch_issue': 289,
        'dispatch_evidence_pr': 1}
VALID_RELAY_URL = (
    'https://github.com/davidcatcat/davidcattoolsproject/issues/2129#issuecomment-6894474079'
)


def _make_comment(
    *,
    issue=2129,
    comment_id=6894474079,
    worker='opencode-developer',
    marker='<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->',
    created_at=None,
    updated_at=None,
    body_extra='',
):
    body = marker + '\n' + worker + body_extra
    created = created_at if created_at is not None else wa._stamp(
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2))
    updated = updated_at or created
    return {
        'id': comment_id,
        'html_url': f'https://github.com/davidcatcat/davidcattoolsproject/issues/{issue}#issuecomment-{comment_id}',
        'issue_url': f'https://api.github.com/repos/davidcatcat/davidcattoolsproject/issues/{issue}',
        'created_at': created,
        'updated_at': updated,
        'body': body,
    }


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
        fail_time = self.now - timedelta(minutes=10)
        comment_time = self.now - timedelta(minutes=2)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail_time)
        self.assertTrue(wa.fresh_quota_fail(self.store, 'opencode-developer'))
        self.assertEqual(self.quota('opencode-developer')['status'], 'FAIL')

        fake_comment = _make_comment(
            created_at=wa._stamp(comment_time),
            updated_at=wa._stamp(comment_time),
        )
        fake_github_get = mock.Mock(return_value=fake_comment)

        result = wa.record_owner_recovery(
            self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
            github_get=fake_github_get)

        fake_github_get.assert_called_once_with('issues/comments/6894474079')
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
        for bad_worker in ('codex-developer', 'company-gw', 'codex-reviewer', 'unknown-worker', 'other', '', None):
            with self.subTest(bad_worker=bad_worker):
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(self.store, bad_worker, VALID_RELAY_URL)
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_WORKER_INVALID')
                self.assertFalse(self.sidecar().exists())

    def test_refusal_no_fail_asserts_unchanged(self):
        # 1. Store has no observation (UNKNOWN)
        self.assertEqual(self.quota('opencode-developer')['status'], 'UNKNOWN')
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now)
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_NO_FAIL')
        self.assertEqual(self.quota('opencode-developer')['status'], 'UNKNOWN')

        # 2. Store has PASS
        self.observe('opencode', None, STEP_USAGE, now=self.now - timedelta(minutes=15))
        self.assertEqual(self.quota('opencode-developer')['status'], 'PASS')
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now)
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_NO_FAIL')
        self.assertEqual(self.quota('opencode-developer')['status'], 'PASS')

        # 3. Store has expired FAIL (reads UNKNOWN)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=35))
        self.assertEqual(self.quota('opencode-developer')['status'], 'UNKNOWN')
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now)
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_NO_FAIL')
        self.assertEqual(self.quota('opencode-developer')['status'], 'UNKNOWN')

    def test_refusal_reader_raises_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        fake_get = mock.Mock(side_effect=RuntimeError('network error'))
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=fake_get)
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_UNAVAILABLE')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_reader_non_dict_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        for bad_val in ([], 'string', None, 123, True):
            with self.subTest(bad_val=bad_val):
                fake_get = mock.Mock(return_value=bad_val)
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(
                        self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                        github_get=fake_get)
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_UNAVAILABLE')
                self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_missing_field_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        comment_time = wa._stamp(self.now - timedelta(minutes=2))
        for field in ('body', 'html_url', 'issue_url', 'created_at', 'updated_at'):
            with self.subTest(missing_field=field):
                c = _make_comment(created_at=comment_time, updated_at=comment_time)
                del c[field]
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(
                        self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                        github_get=mock.Mock(return_value=c))
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_UNAVAILABLE')
                self.assertEqual(self.quota('opencode-developer'), before_obs)
            with self.subTest(non_str_field=field):
                c = _make_comment(created_at=comment_time, updated_at=comment_time)
                c[field] = 123
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(
                        self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                        github_get=mock.Mock(return_value=c))
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_UNAVAILABLE')
                self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_html_url_mismatch_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        c = _make_comment(
            comment_id=9999999,
            created_at=wa._stamp(self.now - timedelta(minutes=2)),
            updated_at=wa._stamp(self.now - timedelta(minutes=2)),
        )
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_REF_INVALID')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_issue_url_mismatch_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        c = _make_comment(
            created_at=wa._stamp(self.now - timedelta(minutes=2)),
            updated_at=wa._stamp(self.now - timedelta(minutes=2)),
        )
        c['issue_url'] = 'https://api.github.com/repos/davidcatcat/davidcattoolsproject/issues/9999'
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_REF_INVALID')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_marker_missing_or_altered_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        comment_time = wa._stamp(self.now - timedelta(minutes=2))

        # Missing marker
        c = _make_comment(marker='', created_at=comment_time, updated_at=comment_time)
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_MARKER')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

        # Altered marker (PENDING instead of ANSWERED)
        altered_1 = '<!-- activity: type=pm_progress actor=PM target=Owner status=PENDING -->'
        c = _make_comment(marker=altered_1, created_at=comment_time, updated_at=comment_time)
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_MARKER')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

        # Altered marker (Codex instead of PM)
        altered_2 = '<!-- activity: type=pm_progress actor=Codex target=Owner status=ANSWERED -->'
        c = _make_comment(marker=altered_2, created_at=comment_time, updated_at=comment_time)
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_MARKER')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_wrong_worker_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        c = _make_comment(
            worker='claude-developer',
            created_at=wa._stamp(self.now - timedelta(minutes=2)),
            updated_at=wa._stamp(self.now - timedelta(minutes=2)),
        )
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_WORKER_MISSING')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_partial_key_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        comment_time = wa._stamp(self.now - timedelta(minutes=2))
        for extra in ('-extra', '_tail', '-worker'):
            with self.subTest(extra=extra):
                c = _make_comment(
                    worker='opencode-developer' + extra,
                    created_at=comment_time,
                    updated_at=comment_time,
                )
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(
                        self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                        github_get=mock.Mock(return_value=c))
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_WORKER_MISSING')
                self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_edited_comment_asserts_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        c = _make_comment(
            created_at=wa._stamp(self.now - timedelta(minutes=5)),
            updated_at=wa._stamp(self.now - timedelta(minutes=2)),
        )
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_EDITED')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_created_at_equal_or_before_fail_asserts_stale_and_unchanged(self):
        fail_time = self.now - timedelta(minutes=5)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail_time)
        before_obs = self.quota('opencode-developer')

        # Equal
        c_equal = _make_comment(
            created_at=wa._stamp(fail_time),
            updated_at=wa._stamp(fail_time),
        )
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c_equal))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_STALE')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

        # Before
        c_before = _make_comment(
            created_at=wa._stamp(fail_time - timedelta(seconds=1)),
            updated_at=wa._stamp(fail_time - timedelta(seconds=1)),
        )
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=c_before))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_STALE')
        self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_malformed_timestamp_asserts_unavailable_and_unchanged(self):
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=self.now - timedelta(minutes=10))
        before_obs = self.quota('opencode-developer')
        bad_timestamps = [
            'not-a-timestamp',
            '2026-11-15T12:00:00+00:00',
            '2026-11-15T12:00:00.000Z',
            '2026-02-29T12:00:00Z',
            '2026-13-01T12:00:00Z',
        ]
        for bad_ts in bad_timestamps:
            with self.subTest(bad_ts=bad_ts):
                c = _make_comment(created_at=bad_ts, updated_at=bad_ts)
                with self.assertRaises(LLMToolError) as caught:
                    wa.record_owner_recovery(
                        self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                        github_get=mock.Mock(return_value=c))
                self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_UNAVAILABLE')
                self.assertEqual(self.quota('opencode-developer'), before_obs)

    def test_refusal_same_comment_reused_after_newer_fail_asserts_stale(self):
        fail1_time = self.now - timedelta(minutes=20)
        comment_time = self.now - timedelta(minutes=15)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail1_time)

        comment = _make_comment(
            created_at=wa._stamp(comment_time),
            updated_at=wa._stamp(comment_time),
        )
        # First recovery succeeds
        wa.record_owner_recovery(
            self.store, 'opencode-developer', VALID_RELAY_URL,
            now=self.now - timedelta(minutes=14),
            github_get=mock.Mock(return_value=comment))
        self.assertEqual(self.quota('opencode-developer')['status'], 'PASS')

        # Newer FAIL occurs
        fail2_time = self.now - timedelta(minutes=10)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail2_time)
        self.assertEqual(self.quota('opencode-developer')['status'], 'FAIL')
        fail2_obs = self.quota('opencode-developer')

        # Reusing the same comment (created at comment_time < fail2_time) refuses STALE
        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=mock.Mock(return_value=comment))
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_STALE')
        self.assertEqual(self.quota('opencode-developer'), fail2_obs)

    def test_refusal_concurrent_newer_fail_during_fetch_asserts_changed_and_survives(self):
        fail1_time = self.now - timedelta(minutes=20)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail1_time)
        newer_fail_time = self.now - timedelta(minutes=5)

        def fake_github_get(path):
            # Concurrently inject a newer FAIL during fetch
            self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=newer_fail_time)
            return _make_comment(
                created_at=wa._stamp(self.now - timedelta(minutes=10)),
                updated_at=wa._stamp(self.now - timedelta(minutes=10)),
            )

        with self.assertRaises(LLMToolError) as caught:
            wa.record_owner_recovery(
                self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
                github_get=fake_github_get)
        self.assertEqual(str(caught.exception), 'WORKER_QUOTA_RECOVERY_CHANGED')

        # The newer FAIL survives in the store
        entry = self.quota('opencode-developer')
        self.assertEqual(entry['status'], 'FAIL')
        self.assertEqual(entry['observed_at'], wa._stamp(newer_fail_time))


class OwnerPausePrecedenceAndCliTest(_StoreCase):
    def test_paused_still_wins_and_policy_unchanged(self):
        worker_policy_fixture.update(self.policy_path, 'opencode-developer', 'PAUSED')
        policy_bytes_before = self.policy_path.read_bytes()

        fail_time = self.now - timedelta(minutes=10)
        comment_time = self.now - timedelta(minutes=2)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail_time)

        fake_comment = _make_comment(
            created_at=wa._stamp(comment_time),
            updated_at=wa._stamp(comment_time),
        )
        fake_github_get = mock.Mock(return_value=fake_comment)

        result = wa.record_owner_recovery(
            self.store, 'opencode-developer', VALID_RELAY_URL, now=self.now,
            github_get=fake_github_get)

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
        fail_time = self.now - timedelta(minutes=10)
        comment_time = self.now - timedelta(minutes=2)
        self.observe('opencode', 'OPENCODE_FREE_LIMIT', QUOTA, now=fail_time)

        fake_comment = _make_comment(
            created_at=wa._stamp(comment_time),
            updated_at=wa._stamp(comment_time),
        )

        # 0 return and one JSON line output on success
        args = SimpleNamespace(worker='opencode-developer', owner_relay=VALID_RELAY_URL)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch('llmtool.task_completion._github_get', return_value=fake_comment), \
                mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
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
