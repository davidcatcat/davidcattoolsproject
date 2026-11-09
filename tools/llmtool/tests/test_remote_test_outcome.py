"""Tests for GitHub-hosted remote-test outcome mapping, actions block and command digest."""

import hashlib
import json
import unittest

from llmtool import remote_test


_SHA = '0123456789abcdef0123456789abcdef01234567'
_TREE = 'abcdef0123456789abcdef0123456789abcdef01'
_REF = 'main'
_NONCE = 'fedcba9876543210fedcba9876543210'
_MODULES = ('test_mod',)


def _make_receipt_bytes(
    result='PASS',
    exit_code=0,
    sha=_SHA,
    modules=_MODULES,
    fetch_head=_SHA,
    tree=_TREE,
    failures=(),
    final=('test_mod ... OK',),
    ran_total=1,
):
    if result == 'PASS':
        exit_code = 0
        fetch_head = sha
        failures = []
        if ran_total <= 0:
            ran_total = 1
    elif exit_code == 0:
        exit_code = 1
    receipt = {
        'result': result,
        'exit_code': exit_code,
        'source_sha': sha,
        'tree': tree,
        'fetch_head': fetch_head,
        'modules': list(modules),
        'identity': {'uid': 1000},
        'started_at': '2026-11-09T10:00:00Z',
        'ended_at': '2026-11-09T10:01:00Z',
        'elapsed_s': 60.0,
        'ran_total': ran_total,
        'final': list(final),
        'failures': list(failures),
        'log_bytes': 100,
        'log_sha256': 'a' * 64,
        'remote_log_path': '/tmp/llmtool-remote-log.abcdef/remote-test.log',
    }
    return json.dumps(receipt, separators=(',', ':'), sort_keys=True).encode('utf-8')


class HostedActionsBlockTests(unittest.TestCase):
    def setUp(self):
        self.run = {
            'id': 101,
            'run_attempt': 1,
            'path': remote_test._HOSTED_WORKFLOW_PATH,
            'head_sha': _SHA,
        }
        self.job = {
            'id': 202,
            'check_run_url': 'https://api.github.com/repos/davidcatcat/davidcattoolsproject/check-runs/303',
            'labels': list(remote_test._HOSTED_LABELS),
            'runner_group_name': remote_test._HOSTED_RUNNER_GROUP,
        }

    def test_valid_actions_block_with_check_run_url(self):
        block = remote_test._hosted_actions_block(self.run, self.job, _NONCE)
        self.assertEqual(block, {
            'run_id': 101,
            'run_attempt': 1,
            'job_id': 202,
            'check_run_id': 303,
            'workflow_path': remote_test._HOSTED_WORKFLOW_PATH,
            'workflow_sha': _SHA,
            'labels': ['ubuntu-24.04'],
            'runner_group': 'GitHub Actions',
            'nonce': _NONCE,
        })

    def test_valid_actions_block_with_direct_check_run_id_and_runner_group(self):
        job = {
            'id': 202,
            'check_run_id': 303,
            'labels': ('ubuntu-24.04',),
            'runner_group': 'GitHub Actions',
        }
        block = remote_test._hosted_actions_block(self.run, job, _NONCE)
        self.assertEqual(block, {
            'run_id': 101,
            'run_attempt': 1,
            'job_id': 202,
            'check_run_id': 303,
            'workflow_path': remote_test._HOSTED_WORKFLOW_PATH,
            'workflow_sha': _SHA,
            'labels': ['ubuntu-24.04'],
            'runner_group': 'GitHub Actions',
            'nonce': _NONCE,
        })

    def test_malformed_run_fields(self):
        for bad_run in (None, 'not a dict', 123, []):
            with self.subTest(bad_run=bad_run):
                self.assertIsNone(remote_test._hosted_actions_block(bad_run, self.job, _NONCE))

        # bad run id
        for bad_id in (None, 0, -1, '101', 1.5):
            bad = dict(self.run, id=bad_id)
            self.assertIsNone(remote_test._hosted_actions_block(bad, self.job, _NONCE))

        # bad run attempt
        for bad_attempt in (None, 0, -1, '1', 1.2):
            bad = dict(self.run, run_attempt=bad_attempt)
            self.assertIsNone(remote_test._hosted_actions_block(bad, self.job, _NONCE))

        # bad workflow path
        for bad_path in (None, '', 123, []):
            bad = dict(self.run, path=bad_path)
            self.assertIsNone(remote_test._hosted_actions_block(bad, self.job, _NONCE))

        # bad workflow sha
        for bad_sha in (None, '', 'not_a_sha', _SHA[:-1], _SHA + 'a', _SHA.upper()):
            bad = dict(self.run, head_sha=bad_sha)
            self.assertIsNone(remote_test._hosted_actions_block(bad, self.job, _NONCE))

    def test_malformed_job_fields(self):
        for bad_job in (None, 'not a dict', 456, []):
            with self.subTest(bad_job=bad_job):
                self.assertIsNone(remote_test._hosted_actions_block(self.run, bad_job, _NONCE))

        # bad job id
        for bad_id in (None, 0, -1, '202', 2.5):
            bad = dict(self.job, id=bad_id)
            self.assertIsNone(remote_test._hosted_actions_block(self.run, bad, _NONCE))

        # bad check run id and url
        for bad_url in (None, '', 'invalid-url', 'https://api.github.com/repos/x/y/check-runs/0'):
            bad = {'id': 202, 'check_run_url': bad_url, 'labels': ['ubuntu-24.04'], 'runner_group_name': 'GitHub Actions'}
            self.assertIsNone(remote_test._hosted_actions_block(self.run, bad, _NONCE))

        # bad labels
        for bad_labels in (None, [], [''], [123], 'ubuntu-24.04', 123):
            bad = dict(self.job, labels=bad_labels)
            self.assertIsNone(remote_test._hosted_actions_block(self.run, bad, _NONCE))

        # bad runner group
        for bad_group in (None, '', 123, []):
            bad = {'id': 202, 'check_run_id': 303, 'labels': ['ubuntu-24.04'], 'runner_group_name': bad_group}
            self.assertIsNone(remote_test._hosted_actions_block(self.run, bad, _NONCE))

    def test_malformed_nonce(self):
        for bad_nonce in (None, '', 'short', _NONCE[:-1], _NONCE + 'a', _NONCE.upper(), 123):
            with self.subTest(bad_nonce=bad_nonce):
                self.assertIsNone(remote_test._hosted_actions_block(self.run, self.job, bad_nonce))


class HostedCommandDigestTests(unittest.TestCase):
    def test_command_digest_matches_compact_sorted_json_sha256(self):
        workflow_path = remote_test._HOSTED_WORKFLOW_PATH
        workflow_sha = _SHA
        ref = 'refs/pull/42/head'
        modules = ['test_alpha', 'test_beta']

        expected_dict = {
            'modules': 'test_alpha test_beta',
            'nonce': _NONCE,
            'ref': ref,
            'sha': _SHA,
            'workflow_path': workflow_path,
            'workflow_sha': workflow_sha,
        }
        expected_bytes = json.dumps(expected_dict, separators=(',', ':'), sort_keys=True).encode('utf-8')
        expected_digest = hashlib.sha256(expected_bytes).hexdigest()

        # Positional arguments with sequence modules
        digest1 = remote_test._hosted_command_digest(
            workflow_path, workflow_sha, _SHA, ref, modules, _NONCE,
        )
        self.assertEqual(digest1, expected_digest)

        # Positional arguments with string modules
        digest2 = remote_test._hosted_command_digest(
            workflow_path, workflow_sha, _SHA, ref, 'test_alpha test_beta', _NONCE,
        )
        self.assertEqual(digest2, expected_digest)

        # Inputs dictionary
        inputs_dict = {
            'sha': _SHA,
            'ref': ref,
            'modules': modules,
            'nonce': _NONCE,
        }
        digest3 = remote_test._hosted_command_digest(workflow_path, workflow_sha, inputs_dict)
        self.assertEqual(digest3, expected_digest)

        # Keyword arguments
        digest4 = remote_test._hosted_command_digest(
            workflow_path, workflow_sha, sha=_SHA, ref=ref, modules='test_alpha test_beta', nonce=_NONCE,
        )
        self.assertEqual(digest4, expected_digest)

        # Keyword with inputs dict
        digest5 = remote_test._hosted_command_digest(
            workflow_path, workflow_sha, inputs=inputs_dict,
        )
        self.assertEqual(digest5, expected_digest)


class HostedOutcomeMappingTests(unittest.TestCase):
    def setUp(self):
        self.pass_raw = _make_receipt_bytes('PASS')
        self.fail_raw = _make_receipt_bytes('FAIL', exit_code=1, failures=['test_mod'], final=['FAIL: test_mod'])
        self.sha_mismatch_raw = _make_receipt_bytes(
            'SHA_MISMATCH', exit_code=41, fetch_head='1' * 40, failures=['test_mod'], final=['FAIL: test_mod'],
        )
        self.timeout_raw = _make_receipt_bytes('TIMEOUT', exit_code=124, failures=['test_mod'], final=['TIMEOUT'])

    def test_success_with_pass_yields_receipt(self):
        run = {'status': 'completed', 'conclusion': 'success'}
        job = {'status': 'completed', 'conclusion': 'success', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        kind, receipt = remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES)
        self.assertEqual(kind, 'RECEIPT')
        self.assertEqual(receipt['result'], 'PASS')

    def test_failure_with_fail_yields_receipt(self):
        run = {'status': 'completed', 'conclusion': 'failure'}
        job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        kind, receipt = remote_test._hosted_outcome(run, job, self.fail_raw, sha=_SHA, modules=_MODULES)
        self.assertEqual(kind, 'RECEIPT')
        self.assertEqual(receipt['result'], 'FAIL')

    def test_failure_with_sha_mismatch_yields_receipt(self):
        run = {'status': 'completed', 'conclusion': 'failure'}
        job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        kind, receipt = remote_test._hosted_outcome(run, job, self.sha_mismatch_raw, sha=_SHA, modules=_MODULES)
        self.assertEqual(kind, 'RECEIPT')
        self.assertEqual(receipt['result'], 'SHA_MISMATCH')

    def test_failure_with_timeout_yields_receipt(self):
        run = {'status': 'completed', 'conclusion': 'failure'}
        job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        kind, receipt = remote_test._hosted_outcome(run, job, self.timeout_raw, sha=_SHA, modules=_MODULES)
        self.assertEqual(kind, 'RECEIPT')
        self.assertEqual(receipt['result'], 'TIMEOUT')

    def test_timed_out_with_timeout_yields_receipt(self):
        run = {'status': 'completed', 'conclusion': 'timed_out'}
        job = {'status': 'completed', 'conclusion': 'timed_out', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        kind, receipt = remote_test._hosted_outcome(run, job, self.timeout_raw, sha=_SHA, modules=_MODULES)
        self.assertEqual(kind, 'RECEIPT')
        self.assertEqual(receipt['result'], 'TIMEOUT')

    def test_only_success_and_success_with_pass_yields_pass(self):
        # run success, job failure, receipt PASS -> conflict
        run = {'status': 'completed', 'conclusion': 'success'}
        job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        self.assertEqual(
            remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'),
        )

        # run failure, job success, receipt PASS -> conflict
        run = {'status': 'completed', 'conclusion': 'failure'}
        job = {'status': 'completed', 'conclusion': 'success', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        self.assertEqual(
            remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'),
        )

        # run failure, job failure, receipt PASS -> conflict
        run = {'status': 'completed', 'conclusion': 'failure'}
        job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        self.assertEqual(
            remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'),
        )

    def test_pending_statuses_yield_hosted_pending(self):
        pending_statuses = ('queued', 'in_progress', 'waiting', 'requested', 'pending')
        for status in pending_statuses:
            with self.subTest(status_on_run=status):
                run = {'status': status}
                job = {'status': 'completed', 'conclusion': 'success', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
                self.assertEqual(
                    remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
                    ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING'),
                )
            with self.subTest(status_on_job=status):
                run = {'status': 'completed', 'conclusion': 'success'}
                job = {'status': status, 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
                self.assertEqual(
                    remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
                    ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING'),
                )

    def test_timed_out_with_no_valid_receipt_yields_hosted_timeout(self):
        run = {'status': 'completed', 'conclusion': 'timed_out'}
        job = {'status': 'completed', 'conclusion': 'timed_out', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}

        # None raw
        self.assertEqual(
            remote_test._hosted_outcome(run, job, None, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'TIMEOUT', 'HOSTED_TIMEOUT'),
        )

        # Invalid JSON bytes
        self.assertEqual(
            remote_test._hosted_outcome(run, job, b'corrupted bytes', sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'TIMEOUT', 'HOSTED_TIMEOUT'),
        )

        # Empty bytes
        self.assertEqual(
            remote_test._hosted_outcome(run, job, b'', sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'TIMEOUT', 'HOSTED_TIMEOUT'),
        )

    def test_conclusions_yielding_hosted_run_capitalized(self):
        conclusions = ('cancelled', 'skipped', 'neutral', 'stale', 'action_required', 'startup_failure')
        for c in conclusions:
            with self.subTest(run_conclusion=c):
                run = {'status': 'completed', 'conclusion': c}
                job = {'status': 'completed', 'conclusion': c, 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
                expected_code = f'HOSTED_RUN_{c.upper()}'
                self.assertEqual(
                    remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
                    ('OUTCOME', 'UNKNOWN', expected_code),
                )
            with self.subTest(job_conclusion=c):
                run = {'status': 'completed', 'conclusion': 'failure'}
                job = {'status': 'completed', 'conclusion': c, 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
                expected_code = f'HOSTED_RUN_{c.upper()}'
                self.assertEqual(
                    remote_test._hosted_outcome(run, job, self.pass_raw, sha=_SHA, modules=_MODULES),
                    ('OUTCOME', 'UNKNOWN', expected_code),
                )

    def test_missing_or_invalid_artifact_yields_receipt_invalid(self):
        valid_job = {'status': 'completed', 'conclusion': 'success', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        success_run = {'status': 'completed', 'conclusion': 'success'}
        failure_run = {'status': 'completed', 'conclusion': 'failure'}
        failure_job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}

        for run, job in ((success_run, valid_job), (failure_run, failure_job)):
            # Missing raw (None)
            self.assertEqual(
                remote_test._hosted_outcome(run, job, None, sha=_SHA, modules=_MODULES),
                ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID'),
            )
            # Empty raw
            self.assertEqual(
                remote_test._hosted_outcome(run, job, b'', sha=_SHA, modules=_MODULES),
                ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID'),
            )
            # Invalid JSON
            self.assertEqual(
                remote_test._hosted_outcome(run, job, b'not json', sha=_SHA, modules=_MODULES),
                ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID'),
            )
            # JSON missing required receipt fields
            self.assertEqual(
                remote_test._hosted_outcome(run, job, b'{"result":"PASS"}', sha=_SHA, modules=_MODULES),
                ('OUTCOME', 'UNKNOWN', 'RECEIPT_INVALID'),
            )

    def test_conclusion_and_receipt_result_conflicts(self):
        valid_job = {'status': 'completed', 'conclusion': 'success', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        success_run = {'status': 'completed', 'conclusion': 'success'}
        failure_run = {'status': 'completed', 'conclusion': 'failure'}
        failure_job = {'status': 'completed', 'conclusion': 'failure', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        timed_out_run = {'status': 'completed', 'conclusion': 'timed_out'}
        timed_out_job = {'status': 'completed', 'conclusion': 'timed_out', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}

        # success with FAIL, TIMEOUT, SHA_MISMATCH
        for raw in (self.fail_raw, self.timeout_raw, self.sha_mismatch_raw):
            self.assertEqual(
                remote_test._hosted_outcome(success_run, valid_job, raw, sha=_SHA, modules=_MODULES),
                ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'),
            )

        # failure with PASS
        self.assertEqual(
            remote_test._hosted_outcome(failure_run, failure_job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'),
        )

        # timed_out with PASS, FAIL, SHA_MISMATCH
        for raw in (self.pass_raw, self.fail_raw, self.sha_mismatch_raw):
            self.assertEqual(
                remote_test._hosted_outcome(timed_out_run, timed_out_job, raw, sha=_SHA, modules=_MODULES),
                ('OUTCOME', 'UNKNOWN', 'RECEIPT_CONFLICT'),
            )

    def test_untrusted_job_yields_hosted_runner_untrusted(self):
        run = {'status': 'completed', 'conclusion': 'success'}

        # String marker from _hosted_job
        self.assertEqual(
            remote_test._hosted_outcome(run, 'HOSTED_RUNNER_UNTRUSTED', self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED'),
        )

        # Untrusted labels in job dict
        untrusted_labels_job = {
            'status': 'completed',
            'conclusion': 'success',
            'labels': ['other-runner'],
            'runner_group_name': remote_test._HOSTED_RUNNER_GROUP,
        }
        self.assertEqual(
            remote_test._hosted_outcome(run, untrusted_labels_job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED'),
        )

        # Untrusted runner group in job dict
        untrusted_group_job = {
            'status': 'completed',
            'conclusion': 'success',
            'labels': list(remote_test._HOSTED_LABELS),
            'runner_group_name': 'Self-Hosted Group',
        }
        self.assertEqual(
            remote_test._hosted_outcome(run, untrusted_group_job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUNNER_UNTRUSTED'),
        )

    def test_anything_else_yields_hosted_run_unknown(self):
        valid_job = {'status': 'completed', 'conclusion': 'success', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        success_run = {'status': 'completed', 'conclusion': 'success'}

        # Non-dict run
        self.assertEqual(
            remote_test._hosted_outcome(None, valid_job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN'),
        )
        self.assertEqual(
            remote_test._hosted_outcome('UNREADABLE', valid_job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN'),
        )

        # Non-dict job (other than HOSTED_RUNNER_UNTRUSTED)
        self.assertEqual(
            remote_test._hosted_outcome(success_run, 'HOSTED_JOB_MISSING', self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN'),
        )
        self.assertEqual(
            remote_test._hosted_outcome(success_run, None, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN'),
        )

        # Unknown conclusion
        unknown_run = {'status': 'completed', 'conclusion': 'unexpected_conclusion'}
        unknown_job = {'status': 'completed', 'conclusion': 'unexpected_conclusion', 'labels': list(remote_test._HOSTED_LABELS), 'runner_group_name': remote_test._HOSTED_RUNNER_GROUP}
        self.assertEqual(
            remote_test._hosted_outcome(unknown_run, unknown_job, self.pass_raw, sha=_SHA, modules=_MODULES),
            ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_UNKNOWN'),
        )


if __name__ == '__main__':
    unittest.main()
