"""OpenCode D6 session bind retry tests (#1817).

Verifies that OpenCode runs get the same single bounded retry of the D6
lifecycle bind helper on D6_BIND_NATIVE_SESSION_HELPER_UNAVAILABLE as
gw-agent (#1895):
- unavailable then BOUND retries once and succeeds;
- unavailable then ALREADY_SET is accepted only when read-back matches;
- unavailable twice fails without further retry;
- non-retryable codes fail immediately without retry;
- gw-agent behavior remains unchanged.
"""

import copy
import dataclasses
import hashlib
import io
import json
import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import opencode_direct_developer
from llmtool import task_completion
from llmtool import task_submission_cli as submission_cli
from llmtool.opencode_direct_developer import DirectResult
from llmtool.task_submission_cli import (
    _D6SessionRefused,
    _GW_AGENT_SESSION,
    _bind_direct_native_session,
)


_TASK_ID = 'a' * 32
_RUN_ID = 'b' * 32
_HEAD = 'd' * 40
_OPENCODE_SESSION = 'ses_abc123'
_OTHER_OPENCODE_SESSION = 'ses_other456'
_GW_AGENT_SESSION_ID = '01234567-89ab-cdef-0123-456789abcdef'
_OTHER_GW_AGENT_SESSION_ID = 'fedcba98-7654-3210-fedc-ba9876543210'

_UNAVAILABLE = 'D6_BIND_NATIVE_SESSION_HELPER_UNAVAILABLE'
_ALREADY_SET = 'D6_BIND_NATIVE_SESSION_ALREADY_SET'
_REFUSED = 'D6_BIND_NATIVE_SESSION_REFUSED'
_UNTRUSTED = 'D6_BIND_NATIVE_SESSION_HELPER_UNTRUSTED'
_INVALID_INPUT = 'D6_BIND_NATIVE_SESSION_INPUT_INVALID'


@dataclasses.dataclass
class _FakeRegistration:
    task_id: str = 'executor-d6'
    common_dir: str = '/repo/.git'
    root: str = '/registered/root'
    branch: str = 'task-branch'
    trusted_agent_id: str = 'opencode-developer'
    session_ref: str = 'session-ref'
    native_session_id: str = None
    source_sha: str = _HEAD
    result_sha: str = _HEAD
    env2_approved_tooling_sha: str = None
    env2_pass_marker: str = None


def _make_opencode_developer(session=_OPENCODE_SESSION):
    execution = {
        'status': 'SUCCEEDED',
        'native_session_id': session,
        'process_exit_code': 0,
        'stdout_sha256': '0' * 64,
        'elapsed_ms': 100,
        'cleanup_status': 'CONFIRMED',
        'usage_source': 'UNKNOWN',
        'source_bytes': 100,
        'source_file_count': 1,
        'source_manifest_sha256': '1' * 64,
    }
    return {
        'provider_profile': 'opencode',
        'state': 'DEVELOPER_RESPONDED',
        'usage': None,
        'execution': execution,
        'direct_result_callback': {
            'evidence': {
                'executor_native_session_id': None,
                'executor_native_session': 'UNKNOWN',
            }
        },
    }


def _make_gw_agent_developer(session=_GW_AGENT_SESSION_ID):
    execution = {
        'status': 'DONE',
        'native_session_id': session,
        'process_exit_code': 0,
        'stdout_sha256': '0' * 64,
        'elapsed_ms': 100,
        'cleanup_status': 'CONFIRMED',
        'usage_source': 'UNKNOWN',
        'source_bytes': None,
        'source_file_count': None,
        'source_manifest_sha256': None,
    }
    return {
        'provider_profile': 'gw-agent',
        'state': 'DEVELOPER_RESPONDED',
        'usage': None,
        'execution': execution,
        'direct_result_callback': {
            'evidence': {
                'executor_native_session_id': None,
                'executor_native_session': 'UNKNOWN',
            }
        },
    }


def _make_completion():
    return {
        'evidence': {
            'executor_native_session_id': None,
            'executor_native_session': 'UNKNOWN',
        }
    }


class OpenCodeBindRetryDirectTests(unittest.TestCase):
    """Direct unit tests for _bind_direct_native_session retry semantics."""

    def _run_bind(self, developer, session_format, helper_answers, *,
                  read_back_session=None):
        registration = _FakeRegistration()
        completion = _make_completion()
        calls = []
        answers = list(helper_answers)

        def fake_helper(_registration, session_val):
            calls.append(session_val)
            if not answers:
                raise AssertionError('helper called more times than expected')
            return answers.pop(0)

        session = developer['execution']['native_session_id']
        expected_read_back = (session if read_back_session is None
                              else read_back_session)

        def fake_read(_task_id, _common_dir):
            return dataclasses.replace(registration, native_session_id=expected_read_back)

        with patch.object(submission_cli, '_bind_codex_native_session', side_effect=fake_helper), \
             patch.object(task_completion, 'read_d6_registration', side_effect=fake_read):
            result = _bind_direct_native_session(
                registration, developer, completion, session_format)

        return result, calls, completion, developer

    def test_opencode_unavailable_then_bound_retries_and_succeeds(self):
        developer = _make_opencode_developer()
        result, calls, completion, developer = self._run_bind(
            developer, opencode_direct_developer._SESSION,
            [_UNAVAILABLE, 'BOUND'])

        self.assertTrue(result)
        self.assertEqual(calls, [_OPENCODE_SESSION, _OPENCODE_SESSION])
        self.assertEqual(completion['evidence']['executor_native_session_id'], _OPENCODE_SESSION)
        self.assertEqual(completion['evidence']['executor_native_session'], 'VERIFIED')
        callback_ev = developer['direct_result_callback']['evidence']
        self.assertEqual(callback_ev['executor_native_session_id'], _OPENCODE_SESSION)
        self.assertEqual(callback_ev['executor_native_session'], 'VERIFIED')

    def test_opencode_unavailable_then_already_set_with_matching_read_back_succeeds(self):
        developer = _make_opencode_developer()
        result, calls, completion, developer = self._run_bind(
            developer, opencode_direct_developer._SESSION,
            [_UNAVAILABLE, _ALREADY_SET],
            read_back_session=_OPENCODE_SESSION)

        self.assertTrue(result)
        self.assertEqual(calls, [_OPENCODE_SESSION, _OPENCODE_SESSION])
        self.assertEqual(completion['evidence']['executor_native_session_id'], _OPENCODE_SESSION)
        self.assertEqual(completion['evidence']['executor_native_session'], 'VERIFIED')
        callback_ev = developer['direct_result_callback']['evidence']
        self.assertEqual(callback_ev['executor_native_session_id'], _OPENCODE_SESSION)
        self.assertEqual(callback_ev['executor_native_session'], 'VERIFIED')

    def test_opencode_unavailable_then_already_set_with_non_matching_read_back_refused(self):
        developer = _make_opencode_developer()
        with self.assertRaises(_D6SessionRefused):
            self._run_bind(
                developer, opencode_direct_developer._SESSION,
                [_UNAVAILABLE, _ALREADY_SET],
                read_back_session=_OTHER_OPENCODE_SESSION)

    def test_opencode_unavailable_twice_refuses_without_third_retry(self):
        developer = _make_opencode_developer()
        with self.assertRaises(_D6SessionRefused) as ctx:
            self._run_bind(
                developer, opencode_direct_developer._SESSION,
                [_UNAVAILABLE, _UNAVAILABLE, 'BOUND'])

        self.assertEqual(ctx.exception.code, _UNAVAILABLE)

    def test_opencode_non_retryable_codes_fail_immediately_without_retry(self):
        for code in (_REFUSED, _UNTRUSTED, _INVALID_INPUT):
            with self.subTest(code=code):
                developer = _make_opencode_developer()
                with self.assertRaises(_D6SessionRefused) as ctx:
                    self._run_bind(
                        developer, opencode_direct_developer._SESSION,
                        [code, 'BOUND'])

                self.assertEqual(ctx.exception.code, code)

    def test_gw_agent_unchanged(self):
        developer = _make_gw_agent_developer()
        result, calls, completion, developer = self._run_bind(
            developer, _GW_AGENT_SESSION,
            [_UNAVAILABLE, 'BOUND'])
        self.assertTrue(result)
        self.assertEqual(calls, [_GW_AGENT_SESSION_ID, _GW_AGENT_SESSION_ID])
        self.assertEqual(completion['evidence']['executor_native_session_id'], _GW_AGENT_SESSION_ID)
        self.assertEqual(completion['evidence']['executor_native_session'], 'VERIFIED')

        developer = _make_gw_agent_developer()
        result, calls, completion, developer = self._run_bind(
            developer, _GW_AGENT_SESSION,
            [_UNAVAILABLE, _ALREADY_SET],
            read_back_session=_GW_AGENT_SESSION_ID)
        self.assertTrue(result)
        self.assertEqual(calls, [_GW_AGENT_SESSION_ID, _GW_AGENT_SESSION_ID])

        developer = _make_gw_agent_developer()
        with self.assertRaises(_D6SessionRefused):
            self._run_bind(
                developer, _GW_AGENT_SESSION,
                [_UNAVAILABLE, _ALREADY_SET],
                read_back_session=_OTHER_GW_AGENT_SESSION_ID)

        developer = _make_gw_agent_developer()
        with self.assertRaises(_D6SessionRefused) as ctx:
            self._run_bind(
                developer, _GW_AGENT_SESSION,
                [_UNAVAILABLE, _UNAVAILABLE, 'BOUND'])
        self.assertEqual(ctx.exception.code, _UNAVAILABLE)

        developer = _make_gw_agent_developer()
        with self.assertRaises(_D6SessionRefused) as ctx:
            self._run_bind(
                developer, _GW_AGENT_SESSION,
                [_REFUSED, 'BOUND'])
        self.assertEqual(ctx.exception.code, _REFUSED)


class _FakeStore:
    """Minimal TaskStore fake tracking writes for delegate testing."""

    def __init__(self, task):
        self.task = copy.deepcopy(task)
        self.writes = []

    def task_ids(self):
        return [self.task['task_id']]

    def get(self, _task_id):
        return copy.deepcopy(self.task)

    def _write(self, name, updates):
        self.writes.append((name, copy.deepcopy(updates)))
        self.task.update(copy.deepcopy(updates))
        return self.get(self.task['task_id'])

    def d6_bind_compare_and_update(self, _task_id, *, executor_task_id, expected, **updates):
        return self._write('d6_bind_compare_and_update', updates)

    def prepare_direct_dispatch(self, task_id, *, expected_developer, developer):
        return self.compare_and_update(task_id, expected={'developer': expected_developer},
                                       developer=dict(developer, dispatch_boundary='PREPARED'))

    def compare_and_update(self, _task_id, *, expected, **updates):
        return self._write('compare_and_update', updates)

    def update(self, _task_id, **updates):
        return self._write('update', updates)


class OpenCodeDelegateBindRetryTests(unittest.TestCase):
    """Integration unit tests through run_task_opencode_delegate."""

    def _base_task(self):
        return {
            'schema_version': 2,
            'task_id': _TASK_ID,
            'run_id': _RUN_ID,
            'state': 'PM_RESPONDED',
            'request': 'Focused task.',
            'response': 'Route to opencode.',
            'developer': None,
            'completion': None,
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {
                'route': 'opencode',
                'risk': 'LOW',
                'goal': 'Update sample.',
                'acceptance': ['change is present'],
            },
        }

    def _run_delegate(self, helper_answers, *, read_back_session=None):
        task = self._base_task()
        store = _FakeStore(task)
        registration = _FakeRegistration()
        snapshot = SimpleNamespace(
            root=registration.root,
            common_dir=registration.common_dir,
            branch=registration.branch,
            head=_HEAD,
        )
        args = Namespace(
            task_id=_TASK_ID,
            provider='opencode',
            selection_source='PM',
            executor_task='executor-d6',
            read_path=['src.py'],
            write_path=['src.py'],
            acceptance=['change is present'],
            egress_basis='Synthetic test context.',
            context_stdin=False,
            timeout=None,
            repair_packet=None,
        )
        provider_result = DirectResult(
            0, _OPENCODE_SESSION, 'Updated fixture.', None, 'UNKNOWN', '0' * 64, 32,
            'CONFIRMED', 'opencode v2.0.32', 10, 1, '1' * 64,
        )
        helper_calls = []
        answers = list(helper_answers)

        def fake_helper(_registration, session_val):
            helper_calls.append(session_val)
            if not answers:
                raise AssertionError('helper called more times than expected')
            return answers.pop(0)

        expected_read_back = (_OPENCODE_SESSION if read_back_session is None
                              else read_back_session)

        def fake_read(_task_id, _common_dir):
            return dataclasses.replace(registration, native_session_id=expected_read_back)

        patches = {
            'profile': patch.object(submission_cli, '_opencode_developer_profile',
                                    return_value=('opencode/sample-model-free', 12)),
            'card_prefix': patch.object(submission_cli, '_developer_card_prefix',
                                        return_value=('prefix\n', '2' * 64)),
            'delivery': patch.object(submission_cli, '_record_skill_delivery'),
            'bootstrap': patch.object(submission_cli, '_bootstrap_registered_d6'),
            'refresh': patch.object(submission_cli, '_refresh_d6_binding_after_bootstrap',
                                    return_value=registration),
            'collect': patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                                    return_value=snapshot),
            'diff': patch.object(submission_cli.task_completion, '_worktree_diff',
                                 return_value=(['src.py'], b'diff --git a/src.py b/src.py\n')),
            'rejection': patch.object(submission_cli, '_developer_output_rejection',
                                      return_value=None),
            'paths': patch.object(submission_cli, '_assignment_paths',
                                  return_value=(['src.py'], ['src.py'])),
            'event': patch.object(submission_cli, 'run_task_direct_result_event',
                                  return_value=0),
            'view': patch.object(submission_cli, '_task_view', return_value={}),
            'provider': patch.object(submission_cli.opencode_direct_developer, 'run',
                                     return_value=provider_result),
            'helper': patch.object(submission_cli, '_bind_codex_native_session',
                                   side_effect=fake_helper),
            'read_reg': patch.object(task_completion, 'read_d6_registration',
                                     side_effect=fake_read),
        }

        with patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(submission_cli, '_delegate_chain_gate',
                          side_effect=lambda _a, _s, _b, call: call()):
            with contextlib_exit_stack(patches):
                status = submission_cli.run_task_opencode_delegate(
                    args, store=store, d6_binding=(registration, snapshot))

        return status, helper_calls, store.task


def contextlib_exit_stack(patches):
    import contextlib
    stack = contextlib.ExitStack()
    for patcher in patches.values():
        stack.enter_context(patcher)
    return stack


class OpenCodeDelegateCases(OpenCodeDelegateBindRetryTests):

    def test_delegate_unavailable_then_bound_succeeds(self):
        status, calls, task = self._run_delegate([_UNAVAILABLE, 'BOUND'])
        self.assertEqual(status, 0)
        self.assertEqual(calls, [_OPENCODE_SESSION, _OPENCODE_SESSION])
        developer = task['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(developer['error_code'])
        self.assertEqual(task['completion']['evidence']['executor_native_session_id'],
                         _OPENCODE_SESSION)
        self.assertEqual(task['completion']['evidence']['executor_native_session'],
                         'VERIFIED')

    def test_delegate_unavailable_then_already_set_matching_succeeds(self):
        status, calls, task = self._run_delegate(
            [_UNAVAILABLE, _ALREADY_SET], read_back_session=_OPENCODE_SESSION)
        self.assertEqual(status, 0)
        self.assertEqual(calls, [_OPENCODE_SESSION, _OPENCODE_SESSION])
        developer = task['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(task['completion']['evidence']['executor_native_session_id'],
                         _OPENCODE_SESSION)

    def test_delegate_unavailable_then_already_set_non_matching_refused(self):
        status, calls, task = self._run_delegate(
            [_UNAVAILABLE, _ALREADY_SET], read_back_session=_OTHER_OPENCODE_SESSION)
        self.assertEqual(status, 1)
        self.assertEqual(calls, [_OPENCODE_SESSION, _OPENCODE_SESSION])
        developer = task['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'OPENCODE_D6_SESSION_REFUSED')

    def test_delegate_unavailable_twice_refused(self):
        status, calls, task = self._run_delegate(
            [_UNAVAILABLE, _UNAVAILABLE, 'BOUND'])
        self.assertEqual(status, 1)
        self.assertEqual(calls, [_OPENCODE_SESSION, _OPENCODE_SESSION])
        developer = task['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'OPENCODE_D6_SESSION_REFUSED')

    def test_delegate_non_retryable_code_refused(self):
        for code in (_REFUSED, _UNTRUSTED, _INVALID_INPUT):
            with self.subTest(code=code):
                status, calls, task = self._run_delegate([code, 'BOUND'])
                self.assertEqual(status, 1)
                self.assertEqual(calls, [_OPENCODE_SESSION])
                developer = task['developer']
                self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(developer['error_code'], 'OPENCODE_D6_SESSION_REFUSED')


if __name__ == '__main__':
    unittest.main()
