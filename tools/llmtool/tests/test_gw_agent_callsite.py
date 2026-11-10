"""Tests for gateway agent session callsite context manager."""

from pathlib import Path
import tempfile
import unittest

from llmtool.developer_routes.gw_agent import session_binding_exists
from llmtool.gw_agent_callsite import SessionCallSite
from llmtool.gw_agent_wiring import WiringRefused


class GwAgentCallSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.store_root = self.base / 'store' / 'task-store'
        self.store_root.parent.mkdir(parents=True, mode=0o700)

        self.valid_executor_id = 'task-exec-synthetic-01'
        self.valid_task_id = 'task-synthetic-01'
        self.valid_session_id = '0123456789abcdef0123456789abcdef'
        self.valid_data_class = 'synthetic'
        self.valid_task_input = {
            'read_paths': ['file_a.py'],
            'write_paths': ['file_b.py'],
            'egress_basis': 'synthetic-egress',
            'data_class': 'synthetic',
            'budgets': {'max_turns': 5},
        }

    def test_first_run_plan_and_route_fields(self):
        site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with site:
            self.assertFalse(site.is_repair)
            self.assertIsNone(site.resume_session)
            self.assertEqual(site.gateway_task_id, self.valid_task_id)
            self.assertEqual(site.findings, [])
            self.assertEqual(site.data_class, self.valid_data_class)
            self.assertEqual(
                site.route_fields(),
                {'session_home': str(site._home), 'session_id': None},
            )
            # check_scope is a no-op on first run.
            site.check_scope(self.valid_task_input)

    def test_binding_written_while_lock_held_and_concurrent_refuses_locked(self):
        site1 = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with site1:
            site2 = SessionCallSite(
                self.store_root,
                executor_task_id=self.valid_executor_id,
                task_id='task-synthetic-02',
                repair_excerpt=None,
                data_class=self.valid_data_class,
            )
            with self.assertRaises(WiringRefused) as cm:
                with site2:
                    pass
            self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_LOCKED')

            written = site1.after_result(self.valid_session_id, self.valid_task_input)
            self.assertTrue(written)
            self.assertTrue(session_binding_exists(site1._home))

    def test_lock_released_after_with_block_and_after_exception(self):
        # Lock released on normal exit.
        exec_id_normal = 'task-exec-synthetic-norm'
        site_norm1 = SessionCallSite(
            self.store_root,
            executor_task_id=exec_id_normal,
            task_id='task-synthetic-norm1',
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with site_norm1:
            pass

        site_norm2 = SessionCallSite(
            self.store_root,
            executor_task_id=exec_id_normal,
            task_id='task-synthetic-norm2',
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with site_norm2:
            self.assertFalse(site_norm2.is_repair)

        # Lock released on exception; exception is not swallowed.
        exec_id_exc = 'task-exec-synthetic-exc'
        site_exc = SessionCallSite(
            self.store_root,
            executor_task_id=exec_id_exc,
            task_id='task-synthetic-exc1',
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with self.assertRaises(ValueError) as cm:
            with site_exc:
                raise ValueError('synthetic error')
        self.assertEqual(str(cm.exception), 'synthetic error')

        site_exc_after = SessionCallSite(
            self.store_root,
            executor_task_id=exec_id_exc,
            task_id='task-synthetic-exc2',
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with site_exc_after:
            self.assertFalse(site_exc_after.is_repair)

    def test_second_first_run_refuses_session_exists(self):
        first_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with first_site:
            first_site.after_result(self.valid_session_id, self.valid_task_input)

        same_task_second_run = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with self.assertRaises(WiringRefused) as cm:
            with same_task_second_run:
                pass
        self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_EXISTS')

        other_task_run = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id='task-synthetic-03',
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with other_task_run:
            self.assertEqual(other_task_run.resume_session, self.valid_session_id)
            self.assertTrue(other_task_run.is_repair)

        # Lock was released; repair run can acquire it.
        repair_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id='task-synthetic-repair-check',
            repair_excerpt='synthetic defect',
            data_class=self.valid_data_class,
        )
        with repair_site:
            self.assertTrue(repair_site.is_repair)

    def test_repair_run_resumes_bound_session(self):
        first_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with first_site:
            first_site.after_result(self.valid_session_id, self.valid_task_input)

        repair_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id='task-synthetic-repair',
            repair_excerpt='synthetic defect excerpt',
            data_class=self.valid_data_class,
        )
        with repair_site:
            self.assertTrue(repair_site.is_repair)
            self.assertEqual(repair_site.resume_session, self.valid_session_id)
            self.assertEqual(repair_site.gateway_task_id, self.valid_task_id)
            self.assertEqual(repair_site.findings, ['synthetic defect excerpt'])
            self.assertEqual(repair_site.data_class, self.valid_data_class)
            self.assertEqual(
                repair_site.route_fields(),
                {'session_home': str(repair_site._home), 'session_id': self.valid_session_id},
            )
            repair_site.check_scope(self.valid_task_input)
            written = repair_site.after_result(self.valid_session_id, self.valid_task_input)
            self.assertFalse(written)

    def test_scope_change_refuses_session_unavailable(self):
        first_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with first_site:
            first_site.after_result(self.valid_session_id, self.valid_task_input)

        repair_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id='task-synthetic-repair',
            repair_excerpt='synthetic defect',
            data_class=self.valid_data_class,
        )
        with repair_site:
            altered_input = dict(self.valid_task_input)
            altered_input['read_paths'] = ['other_synthetic_file.py']
            with self.assertRaises(WiringRefused) as cm:
                repair_site.check_scope(altered_input)
            self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_resumed_id_mismatch_refuses_session_unavailable(self):
        first_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with first_site:
            first_site.after_result(self.valid_session_id, self.valid_task_input)

        repair_site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id='task-synthetic-repair',
            repair_excerpt='synthetic defect',
            data_class=self.valid_data_class,
        )
        with repair_site:
            mismatched_session_id = 'fedcba9876543210fedcba9876543210'
            with self.assertRaises(WiringRefused) as cm:
                repair_site.after_result(mismatched_session_id, self.valid_task_input)
            self.assertEqual(cm.exception.code, 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE')

    def test_not_entered_properties_raise_runtime_error(self):
        site = SessionCallSite(
            self.store_root,
            executor_task_id=self.valid_executor_id,
            task_id=self.valid_task_id,
            repair_excerpt=None,
            data_class=self.valid_data_class,
        )
        with self.assertRaises(RuntimeError):
            _ = site.gateway_task_id
        with self.assertRaises(RuntimeError):
            _ = site.findings
        with self.assertRaises(RuntimeError):
            _ = site.data_class
        with self.assertRaises(RuntimeError):
            _ = site.resume_session
        with self.assertRaises(RuntimeError):
            site.route_fields()
        with self.assertRaises(RuntimeError):
            site.after_result(self.valid_session_id, self.valid_task_input)


if __name__ == '__main__':
    unittest.main()
