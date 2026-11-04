"""Focused tests for the local skill evidence ledger."""

import errno
import fcntl
import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from llmtool import skill_ledger as ledger


def _forked_writer(path, index, results):
    ledger._path = lambda: path
    try:
        ledger.record({
            'task_id': 'task-a', 'run_id': 'fork-' + str(index),
            'session_id': 'session-a', 'agent_id': 'agent-a',
            'role': 'Developer', 'provider': 'local', 'model': 'model-a',
            'repo': 'repo-a', 'head': 'head-a', 'manifest_sha256': 'manifest-a',
            'evidence_level': 'OBSERVED_READ',
        })
        results.put(None)
    except Exception as exc:
        results.put(type(exc).__name__)


class SkillLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.jsonl'
        self.path.parent.chmod(0o700)
        self.path_patch = mock.patch.object(ledger, '_path', return_value=self.path)
        self.path_mock = self.path_patch.start()
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
                self.path.chmod(0o600)
                self.record(run_id='run-c')
                rows = self.path.read_text().splitlines()

    def test_malformed_json_and_noncanonical_values_return_chain_code(self):
        self.record()
        # The stored end marker remains intact, so verification reaches parsing.
        self.path.write_text('{"schema_version":1,"prev_sha256":"' +
                             '0' * 64 + '","entry_sha256":"x","value":NaN}\n')
        self.path.chmod(0o600)
        self.assertEqual(ledger.verify_chain(), (False, 'CHAIN_BROKEN'))
        self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')
        self.path.write_text('{"schema_version":1,"prev_sha256":"' +
                             '0' * 64 + '","entry_sha256":"x","value":"\\ud800"}\n')
        self.path.chmod(0o600)
        self.assertEqual(ledger.verify_chain(), (False, 'CHAIN_BROKEN'))
        self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')

    def test_invalid_evidence_types_and_utf8_are_refused_before_filesystem_access(self):
        for evidence_level in ([], {}):
            with self.subTest(evidence_level=evidence_level):
                with mock.patch.object(ledger, '_path', side_effect=AssertionError(
                        'filesystem path accessed')):
                    with self.assertRaises(ledger.SkillLedgerError) as caught:
                        ledger.record({**self.entry, 'evidence_level': evidence_level})
                self.assertEqual(caught.exception.skill_ledger_code,
                                 'INVALID_EVIDENCE_LEVEL')
        with mock.patch.object(ledger, '_path', side_effect=AssertionError(
                'filesystem path accessed')):
            with self.assertRaises(ledger.SkillLedgerError) as caught:
                ledger.record({**self.entry, 'head': '\ud800'})
        self.assertEqual(caught.exception.skill_ledger_code, 'INVALID_ENTRY')

    def test_wrong_head_selection_prefers_matching_run_in_either_order(self):
        for matching_first in (True, False):
            with self.subTest(matching_first=matching_first):
                for suffix in ('', '.head', '.lock'):
                    target = Path(str(self.path) + suffix)
                    if target.exists() or target.is_symlink():
                        target.unlink()
                matching = {'run_id': 'run-match', 'head': 'old-head'}
                other = {'run_id': 'run-other', 'head': 'requested-head'}
                ordered = (matching, other) if matching_first else (other, matching)
                for row in ordered:
                    self.record(**row)
                result = self.check(run_id='run-match', head='requested-head')
                self.assertEqual(result['reason'], 'WRONG_HEAD')

    def test_removing_revocation_tail_breaks_chain(self):
        receipt = self.record()
        ledger.revoke(receipt, 'withdrawn')
        rows = self.path.read_text().splitlines()
        self.path.write_text(rows[0] + '\n')
        self.path.chmod(0o600)
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

    def test_ledger_file_mode_is_private(self):
        self.record()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_hard_linked_ledger_is_refused(self):
        self.record()
        os.link(self.path, self.path.parent / 'ledger-link')
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')
        self.assertEqual(ledger.verify_chain(), (False, 'LEDGER_UNREADABLE'))
        with self.assertRaises(ledger.SkillLedgerError) as caught:
            self.record(run_id='other')
        self.assertEqual(caught.exception.skill_ledger_code, 'LEDGER_UNREADABLE')

    def test_existing_non_private_ledger_is_refused_by_public_operations(self):
        self.record()
        self.path.chmod(0o644)
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')
        self.assertEqual(ledger.verify_chain(), (False, 'LEDGER_UNREADABLE'))
        with self.assertRaises(ledger.SkillLedgerError) as caught:
            self.record(run_id='other')
        self.assertEqual(caught.exception.skill_ledger_code, 'LEDGER_UNREADABLE')

    def test_fifo_ledger_is_refused(self):
        os.mkfifo(self.path, 0o600)
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_torn_last_line_is_refused(self):
        self.record()
        with self.path.open('ab') as stream:
            stream.write(b'{"torn":')
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_valid_final_json_without_newline_is_refused(self):
        self.record()
        raw = self.path.read_bytes()
        self.assertTrue(raw.endswith(b'\n'))
        self.path.write_bytes(raw[:-1])
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_lock_file_mode_is_private(self):
        self.record()
        lock = Path(str(self.path) + '.lock')
        self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)
        lock.chmod(0o644)
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_lock_symlink_is_refused(self):
        target = self.path.parent / 'lock-target'
        target.write_text('')
        target.chmod(0o600)
        Path(str(self.path) + '.lock').symlink_to(target)
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_parent_directory_mode_is_private(self):
        self.path.parent.chmod(0o755)
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_parent_directory_symlink_is_refused(self):
        real_parent = self.path.parent / 'real-parent'
        real_parent.mkdir(mode=0o700)
        link_parent = self.path.parent / 'link-parent'
        link_parent.symlink_to(real_parent, target_is_directory=True)
        self.path = link_parent / 'ledger.jsonl'
        self.path_mock.return_value = self.path
        self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

    def test_missing_binding_fields_are_refused(self):
        for field in ('task_id', 'run_id', 'session_id', 'agent_id', 'role',
                      'provider', 'model', 'repo', 'head', 'manifest_sha256'):
            with self.subTest(field=field):
                value = dict(self.entry)
                del value[field]
                with self.assertRaises(ledger.SkillLedgerError) as caught:
                    ledger.record(value)
                self.assertEqual(caught.exception.skill_ledger_code, 'INVALID_ENTRY')

    def test_invalid_and_oversized_ttl_are_refused(self):
        for ttl in (0, -1, 1.5, 10**100, False, True):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ledger.SkillLedgerError) as caught:
                    ledger.record(dict(self.entry), ttl_seconds=ttl)
                self.assertEqual(caught.exception.skill_ledger_code, 'INVALID_ENTRY')

    def test_anchor_file_mode_is_private(self):
        self.record()
        anchor = Path(str(self.path) + '.head')
        self.assertEqual(os.stat(anchor).st_mode & 0o777, 0o600)
        anchor.chmod(0o644)
        self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')

    def test_anchor_binding_mismatch_is_refused(self):
        self.record()
        anchor = Path(str(self.path) + '.head')
        anchor.write_text(json.dumps({'count': 99, 'last_sha256': 'f' * 64}))
        anchor.chmod(0o600)
        self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')

    def test_anchor_symlink_hard_link_fifo_and_malformed_are_refused(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'malformed'):
            with self.subTest(kind=kind):
                self.record()
                anchor = Path(str(self.path) + '.head')
                if kind == 'symlink':
                    anchor_contents = anchor.read_bytes()
                    anchor.unlink()
                    target = self.path.parent / 'anchor-target'
                    target.write_bytes(anchor_contents)
                    target.chmod(0o600)
                    anchor.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(anchor, self.path.parent / 'anchor-link')
                elif kind == 'fifo':
                    anchor.unlink()
                    os.mkfifo(anchor, 0o600)
                else:
                    anchor.write_text('{}')
                    anchor.chmod(0o600)
                self.assertEqual(self.check()['reason'], 'CHAIN_BROKEN')
                for candidate in self.path.parent.iterdir():
                    if candidate != self.path:
                        if candidate.is_dir() and not candidate.is_symlink():
                            continue
                        candidate.unlink()
                self.path.unlink(missing_ok=True)

    def test_non_regular_anchor_is_rejected_before_anchor_json_parsing(self):
        self.record()
        anchor = Path(str(self.path) + '.head')
        anchor.unlink()
        os.mkfifo(anchor, 0o600)
        with mock.patch.object(ledger.json, 'loads', wraps=json.loads) as loads:
            self.assertFalse(ledger._read_anchor(self.path))
        loads.assert_not_called()

    def test_recovery_io_failures_are_reported_as_unreadable(self):
        self.record()
        records = ledger._read(self.path)
        pending = {'count': len(records) + 1, 'last_sha256': 'f' * 64}
        ledger._write_anchor(self.path, records, pending)
        with mock.patch.object(ledger, '_write_anchor', side_effect=ledger.SkillLedgerError(
                'LEDGER_UNREADABLE')):
            self.assertEqual(self.check()['reason'], 'LEDGER_UNREADABLE')

        ledger._write_anchor(self.path, records, pending)
        with mock.patch.object(ledger, '_write_anchor', side_effect=ledger.SkillLedgerError(
                'LEDGER_UNREADABLE')):
            self.assertEqual(ledger.verify_chain(), (False, 'LEDGER_UNREADABLE'))

    def test_check_recovers_anchor_while_holding_exclusive_lock(self):
        self.record()
        original_recover = ledger._recover_anchor
        lock = Path(str(self.path) + '.lock')

        def assert_locked(path, records):
            probe_fd = os.open(lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe_fd)
            return original_recover(path, records)

        with mock.patch.object(ledger, '_recover_anchor', side_effect=assert_locked) as recover:
            self.assertEqual(self.check()['status'], 'VERIFIED')
        recover.assert_called_once()

    def test_interrupted_anchor_replace_recovers_committed_receipt(self):
        self.record()
        original_replace = os.replace
        calls = 0

        def fail_final_replace(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.ENOSPC, 'full')
            return original_replace(source, target)

        with mock.patch.object(ledger.os, 'replace', side_effect=fail_final_replace):
            with self.assertRaises(ledger.SkillLedgerError):
                self.record(run_id='run-b')
        self.assertEqual(self.check(run_id='run-a')['status'], 'VERIFIED')
        self.assertEqual(self.check(run_id='run-b')['status'], 'VERIFIED')
        self.record(run_id='after-recovery')
        self.assertTrue(ledger.verify_chain()[0])

    def test_interrupted_anchor_fsync_recovers_committed_receipt(self):
        self.record()
        original_fsync = os.fsync
        original_write_anchor = ledger._write_anchor
        in_final_anchor_phase = False
        final_anchor_fsync_reached = False

        def track_anchor_phase(path, records, intent=None):
            nonlocal in_final_anchor_phase
            previous_phase = in_final_anchor_phase
            in_final_anchor_phase = intent is None
            try:
                return original_write_anchor(path, records, intent)
            finally:
                in_final_anchor_phase = previous_phase

        def fail_final_anchor_fsync(fd):
            nonlocal final_anchor_fsync_reached
            if in_final_anchor_phase and not final_anchor_fsync_reached:
                final_anchor_fsync_reached = True
                raise OSError(errno.ENOSPC, 'full')
            return original_fsync(fd)

        with mock.patch.object(ledger, '_write_anchor', side_effect=track_anchor_phase), \
                mock.patch.object(ledger.os, 'fsync', side_effect=fail_final_anchor_fsync):
            with self.assertRaises(ledger.SkillLedgerError):
                self.record(run_id='run-b')
        self.assertTrue(final_anchor_fsync_reached)
        self.assertEqual(self.check(run_id='run-a')['status'], 'VERIFIED')
        self.assertEqual(self.check(run_id='run-b')['status'], 'VERIFIED')
        self.record(run_id='after-recovery')
        self.assertTrue(ledger.verify_chain()[0])

    def test_forked_process_writers_keep_a_valid_chain(self):
        context = multiprocessing.get_context('fork')
        results = context.Queue()
        workers = [context.Process(target=_forked_writer,
                                   args=(self.path, index, results))
                   for index in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertTrue(all(not worker.is_alive() and worker.exitcode == 0
                            for worker in workers))
        self.assertEqual([results.get(timeout=2) for _ in workers], [None] * 8)
        self.assertEqual(len(self.path.read_text().splitlines()), 8)
        self.assertTrue(ledger.verify_chain()[0])

    def test_extreme_now_is_not_misclassified_as_ledger_damage(self):
        self.record()
        self.assertEqual(self.check(now='999999-01-01T00:00:00+00:00')['reason'],
                         'INVALID_NOW')

    def test_verify_chain_parent_refusal_returns_ledger_code(self):
        self.path.parent.chmod(0o755)
        self.assertEqual(ledger.verify_chain(), (False, 'LEDGER_UNREADABLE'))


if __name__ == '__main__':
    unittest.main()
