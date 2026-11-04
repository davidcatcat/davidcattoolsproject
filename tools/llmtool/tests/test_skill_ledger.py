"""Focused tests for the local skill evidence ledger."""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from llmtool import skill_ledger as ledger


class SkillLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.jsonl'
        self.path.parent.chmod(0o700)
        self.path_patch = mock.patch.object(ledger, '_path', return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.entry = {
            'task_id': 'task-a', 'run_id': 'run-a', 'session_id': 'session-a',
            'agent_id': 'agent-a', 'role': 'Developer', 'provider': 'local',
            'model': 'model-a', 'repo': 'repo-a', 'head': 'head-a',
            'manifest_sha256': 'manifest-a', 'evidence_level': 'OBSERVED_READ',
        }

    def record(self, **changes):
        ttl_seconds = changes.pop('ttl_seconds', 86400)
        value = dict(self.entry)
        value.update(changes)
        return ledger.record(value, ttl_seconds=ttl_seconds)

    def check(self, **changes):
        args = dict(task_id='task-a', run_id='run-a', session_id='session-a',
                    agent_id='agent-a', role='Developer', provider='local',
                    model='model-a', repo='repo-a', head='head-a',
                    manifest_sha256='manifest-a')
        args.update(changes)
        return ledger.check(**args)

    def test_records_private_hash_chained_line_and_verifies(self):
        receipt = self.record()
        self.assertEqual(self.check()['status'], 'VERIFIED')
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        row = json.loads(self.path.read_text().strip())
        self.assertEqual(row['receipt_id'], receipt)
        self.assertEqual(row['schema_version'], 1)
        self.assertEqual(row['evidence_level'], 'OBSERVED_READ')
        self.assertEqual(row['status'], 'ACTIVE')
        self.assertEqual(row['prev_sha256'], '0' * 64)
        self.assertEqual(len(row['entry_sha256']), 64)
        self.assertTrue(row['created_at'])
        self.assertTrue(row['expires_at'])
        for field in ('task_id', 'run_id', 'session_id', 'agent_id', 'role',
                      'provider', 'model', 'repo', 'head', 'manifest_sha256'):
            self.assertEqual(row[field], self.entry[field])
        self.assertTrue(ledger.verify_chain()[0])

    def test_unknown_evidence_level_is_refused(self):
        with self.assertRaises(ledger.SkillLedgerError) as caught:
            self.record(evidence_level='PASS')
        self.assertEqual(caught.exception.skill_ledger_code, 'INVALID_EVIDENCE_LEVEL')

    def test_expiration_revocation_and_not_found_reasons(self):
        self.record(ttl_seconds=1)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual(self.check(now=future)['reason'], 'EXPIRED')
        receipt = json.loads(self.path.read_text().splitlines()[0])['receipt_id']
        ledger.revoke(receipt, 'withdrawn')
        self.assertEqual(self.check()['reason'], 'REVOKED')
        self.assertEqual(self.check(task_id='missing')['reason'], 'WRONG_TASK')
        self.assertEqual(ledger.check('x', 'y', 'z', 'q', 'r', 'p', 'm', 'repo',
                                      'h', 'digest')['reason'], 'WRONG_TASK')

    def test_not_found_on_empty_ledger_and_unreadable_ledger(self):
        self.assertEqual(self.check()['reason'], 'NOT_FOUND')
        target = self.path.parent / 'target'
        target.write_text('')
        self.path.symlink_to(target)
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_binding_mismatches_and_identity_replay(self):
        self.record()
        for field, reason in (('task_id', 'WRONG_TASK'), ('run_id', 'WRONG_RUN'),
                              ('session_id', 'NOT_FOUND'),
                              ('agent_id', 'WRONG_AGENT'), ('role', 'WRONG_ROLE'),
                              ('provider', 'WRONG_PROVIDER'), ('model', 'WRONG_MODEL'),
                              ('repo', 'WRONG_REPO'),
                              ('head', 'WRONG_HEAD'),
                              ('manifest_sha256', 'MANIFEST_CHANGED')):
            with self.subTest(field=field):
                self.assertEqual(self.check(**{field: 'other'})['reason'], reason)
        self.assertEqual(self.check(agent_id='child-agent')['reason'], 'WRONG_AGENT')
        self.assertEqual(self.check(role='Reviewer')['reason'], 'WRONG_ROLE')

    def test_receipt_cannot_be_replayed_by_another_task(self):
        self.record()
        self.assertEqual(self.check(task_id='task-b')['reason'], 'WRONG_TASK')

    def test_parent_child_and_developer_reviewer_receipts_are_distinct(self):
        self.record()
        self.assertEqual(self.check(agent_id='child-agent')['reason'], 'WRONG_AGENT')
        self.assertEqual(self.check(role='Reviewer')['reason'], 'WRONG_ROLE')

    def test_none_and_empty_binding_values_never_match(self):
        self.record()
        for field in ('task_id', 'run_id', 'session_id', 'agent_id', 'role',
                      'provider', 'model', 'repo', 'head', 'manifest_sha256'):
            for value in (None, ''):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.check(**{field: value})['status'], 'UNVERIFIED')

    def test_head_anchor_detects_tail_removal_empty_and_deleted_ledger(self):
        self.record()
        self.record(run_id='run-b')
        rows = self.path.read_text().splitlines()
        for damaged in ('\n'.join(rows[:-1]) + '\n', '', None):
            with self.subTest(damaged=damaged):
                if damaged is None:
                    self.path.unlink()
                else:
                    self.path.write_text(damaged)
                self.assertEqual(ledger.verify_chain(), (False, 'CHAIN_BROKEN'))
                self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')
                # Restore both files for the next independent tampering case.
                self.path.write_text('\n'.join(rows) + '\n')
                self.record(run_id='run-c')
                rows = self.path.read_text().splitlines()

    def test_malformed_json_and_noncanonical_values_return_chain_code(self):
        self.record()
        # The stored end marker remains intact, so verification reaches parsing.
        self.path.write_text('{"value": NaN}\n')
        self.assertEqual(ledger.verify_chain(), (False, 'CHAIN_BROKEN'))
        self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')
        self.path.write_text('{"value":"\\ud800"}\n')
        self.assertEqual(ledger.verify_chain(), (False, 'CHAIN_BROKEN'))
        self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')

    def test_skill_revision_change_invalidates_receipt(self):
        self.record()
        self.assertEqual(self.check(manifest_sha256='manifest-revised')['reason'],
                         'MANIFEST_CHANGED')

    def test_unverified_evidence_never_verifies(self):
        self.record(evidence_level='UNVERIFIED')
        self.assertEqual(self.check()['status'], 'UNVERIFIED')

    def test_chain_detects_edit_removal_and_reordering(self):
        self.record()
        self.record(run_id='run-b')
        original = self.path.read_text().splitlines()
        for changed in (original[::-1], original[1:], [original[0].replace('head-a', 'head-x'),
                                                        original[1]]):
            self.path.write_text('\n'.join(changed) + '\n')
            self.assertEqual(ledger.verify_chain(), (False, 'CHAIN_BROKEN'))
            self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')

    def test_symlink_ledger_is_refused(self):
        target = self.path.parent / 'target'
        target.write_text('')
        self.path.symlink_to(target)
        with self.assertRaises(ledger.SkillLedgerError) as caught:
            self.record()
        self.assertEqual(caught.exception.skill_ledger_code, 'LEDGER_UNREADABLE')

    def test_parallel_writers_keep_a_valid_chain(self):
        errors = []

        def writer(index):
            try:
                self.record(run_id='run-' + str(index))
            except Exception as exc:  # surfaced after all workers join
                errors.append(exc)

        workers = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.path.read_text().splitlines()), 12)
        self.assertTrue(ledger.verify_chain()[0])


if __name__ == '__main__':
    unittest.main()
