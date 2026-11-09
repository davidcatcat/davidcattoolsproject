"""Tests for gateway agent session wiring helpers."""

import os
from pathlib import Path
import stat
import tempfile
import unittest

from llmtool.developer_routes.gw_agent import (
    session_binding_exists,
    write_session_binding,
)
from llmtool.gw_agent_wiring import (
    WiringRefused,
    check_resumed_session,
    check_scope,
    open_locked_home,
    plan_run,
    record_first_run,
)


class GwAgentWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.store_root = self.base / 'store' / 'task-store'
        self.store_root.parent.mkdir(parents=True, mode=0o700)
        self.valid_root_id = 'task-root-synthetic-01'
        self.valid_executor_id = 'task-exec-synthetic-01'
        self.valid_task_id = 'task-synthetic-01'
        self.valid_session_id = '0123456789abcdef0123456789abcdef'
        self.valid_data_class = 'synthetic'
        self.valid_scope_digest = 'a' * 64

    def test_wiring_refused_code_normalization(self):
        err1 = WiringRefused('GW_AGENT_SESSION_LOCKED')
        self.assertEqual(err1.code, 'DEVELOPER_GW_AGENT_SESSION_LOCKED')
        self.assertEqual(str(err1), 'DEVELOPER_GW_AGENT_SESSION_LOCKED')

        err2 = WiringRefused('SESSION_EXISTS')
        self.assertEqual(err2.code, 'DEVELOPER_GW_AGENT_SESSION_EXISTS')

        err3 = WiringRefused('DEVELOPER_GW_AGENT_SESSION_EXISTS')
        self.assertEqual(err3.code, 'DEVELOPER_GW_AGENT_SESSION_EXISTS')

        err4 = WiringRefused('BINDING_FAILED')
        self.assertEqual(err4.code, 'DEVELOPER_GW_AGENT_SESSION_BINDING_FAILED')

        long_code = WiringRefused('GW_AGENT_' + 'X' * 100)
        self.assertLessEqual(len(long_code.code), 64)

    def test_lock_busy_refuses_locked(self):
        home, lock1 = open_locked_home(self.store_root, self.valid_executor_id)
        self.assertTrue(home.is_dir())

        with self.assertRaises(WiringRefused) as cm:
            open_locked_home(self.store_root, self.valid_executor_id)
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_LOCKED')

        # Release is safe to call twice.
        lock1.release()
        lock1.release()

        # Lock can be acquired again after release.
        home2, lock2 = open_locked_home(self.store_root, self.valid_executor_id)
        self.assertEqual(home2, home)
        lock2.release()

    def test_first_run_existing_binding_refuses_exists(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        write_session_binding(
            home,
            root_task_id=self.valid_root_id,
            executor_task_id=self.valid_executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )

        with self.assertRaises(WiringRefused) as cm:
            plan_run(
                home,
                executor_task_id=self.valid_executor_id,
                task_id=self.valid_task_id,
                repair_excerpt=None,
                data_class=self.valid_data_class,
            )
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_EXISTS')

    def test_first_run_plan_fields(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        plan = plan_run(
            home,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        expected = {
            'gateway_task_id': self.valid_task_id,
            'resume_session': None,
            'findings': [],
            'data_class': self.valid_data_class,
        }
        self.assertEqual(plan, expected)

        # Non-empty string data_class is required.
        with self.assertRaises(WiringRefused) as cm:
            plan_run(
                home,
                executor_task_id=self.valid_executor_id,
                task_id=self.valid_task_id,
                repair_excerpt=None,
                data_class='',
            )
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_record_first_run_writes_binding_and_rejects_bad_id(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        plan = plan_run(
            home,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )

        # Bad session IDs write nothing and return False.
        bad_ids = [
            'short',
            '0123456789abcdef' * 3,
            '0123456789abcdef0123456789ABCDEF',
            'not-hex-characters-here-01234567',
            '',
        ]
        for bad_id in bad_ids:
            with self.subTest(bad_id=bad_id):
                written = record_first_run(
                    home,
                    plan,
                    executor_task_id=self.valid_executor_id,
                    result_session_id=bad_id,
                    scope_digest=self.valid_scope_digest,
                )
                self.assertFalse(written)
                self.assertFalse(session_binding_exists(home))

        # Valid 32 lowercase hex session ID writes 0600 binding and returns True.
        written = record_first_run(
            home,
            plan,
            executor_task_id=self.valid_executor_id,
            result_session_id=self.valid_session_id,
            scope_digest=self.valid_scope_digest,
        )
        self.assertTrue(written)
        self.assertTrue(session_binding_exists(home))

        binding_path = home / 'binding.json'
        info = binding_path.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

        # Repair run writes nothing and returns False.
        repair_plan = {
            'gateway_task_id': self.valid_root_id,
            'resume_session': self.valid_session_id,
            'findings': [],
            'data_class': self.valid_data_class,
        }
        self.assertFalse(
            record_first_run(
                home,
                repair_plan,
                executor_task_id=self.valid_executor_id,
                result_session_id=self.valid_session_id,
                scope_digest=self.valid_scope_digest,
            )
        )

    def test_repair_plan_uses_root_task_id_and_bound_session(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        write_session_binding(
            home,
            root_task_id=self.valid_root_id,
            executor_task_id=self.valid_executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )

        repair_text = 'synthetic defect description'
        plan = plan_run(
            home,
            executor_task_id=self.valid_executor_id,
            task_id='task-repair-synthetic-02',
            repair_excerpt=repair_text,
            data_class=None,
        )
        self.assertEqual(plan['gateway_task_id'], self.valid_root_id)
        self.assertEqual(plan['resume_session'], self.valid_session_id)
        self.assertEqual(plan['findings'], [repair_text])
        self.assertEqual(plan['data_class'], self.valid_data_class)

        # Explicitly matching data_class is accepted.
        plan_matched = plan_run(
            home,
            executor_task_id=self.valid_executor_id,
            task_id='task-repair-synthetic-02',
            repair_excerpt=repair_text,
            data_class=self.valid_data_class,
        )
        self.assertEqual(plan_matched['data_class'], self.valid_data_class)

    def test_missing_binding_refuses(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        with self.assertRaises(WiringRefused) as cm:
            plan_run(
                home,
                executor_task_id=self.valid_executor_id,
                task_id=self.valid_task_id,
                repair_excerpt='synthetic defect',
                data_class=None,
            )
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_data_class_mismatch_refuses(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        write_session_binding(
            home,
            root_task_id=self.valid_root_id,
            executor_task_id=self.valid_executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )

        with self.assertRaises(WiringRefused) as cm:
            plan_run(
                home,
                executor_task_id=self.valid_executor_id,
                task_id=self.valid_task_id,
                repair_excerpt='synthetic defect',
                data_class='other-synthetic-class',
            )
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_scope_change_refuses(self):
        first_plan = {
            'gateway_task_id': self.valid_task_id,
            'resume_session': None,
            'findings': [],
            'data_class': self.valid_data_class,
        }
        # First run does nothing on scope mismatch.
        check_scope(first_plan, 'a' * 64, 'b' * 64)

        repair_plan = {
            'gateway_task_id': self.valid_root_id,
            'resume_session': self.valid_session_id,
            'findings': [],
            'data_class': self.valid_data_class,
        }
        # Matching scope does nothing.
        check_scope(repair_plan, 'a' * 64, 'a' * 64)

        # Mismatched scope on repair run raises SESSION_UNAVAILABLE.
        with self.assertRaises(WiringRefused) as cm:
            check_scope(repair_plan, 'a' * 64, 'b' * 64)
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_resumed_id_mismatch_refuses(self):
        first_plan = {
            'gateway_task_id': self.valid_task_id,
            'resume_session': None,
            'findings': [],
            'data_class': self.valid_data_class,
        }
        # First run does nothing on session mismatch.
        check_resumed_session(first_plan, 'other-session-id')

        repair_plan = {
            'gateway_task_id': self.valid_root_id,
            'resume_session': self.valid_session_id,
            'findings': [],
            'data_class': self.valid_data_class,
        }
        # Matching session does nothing.
        check_resumed_session(repair_plan, self.valid_session_id)

        # Mismatched session on repair run raises SESSION_UNAVAILABLE.
        with self.assertRaises(WiringRefused) as cm:
            check_resumed_session(repair_plan, 'other-session-id')
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_blank_excerpt_gives_no_findings(self):
        home, lock = open_locked_home(self.store_root, self.valid_executor_id)
        self.addCleanup(lock.release)

        write_session_binding(
            home,
            root_task_id=self.valid_root_id,
            executor_task_id=self.valid_executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )

        plan_empty = plan_run(
            home,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt='',
            data_class=None,
        )
        self.assertEqual(plan_empty['findings'], [])

        plan_whitespace = plan_run(
            home,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt='   \t\n  ',
            data_class=None,
        )
        self.assertEqual(plan_whitespace['findings'], [])


if __name__ == '__main__':
    unittest.main()
