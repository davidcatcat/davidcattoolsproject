"""#2602 item v: coded pre-launch refusals on the repair-dispatch path.

Reproduces the PR #2648 / #2653 shape where task advance routes an EC2
repair 1/5 for an AGY Task while the checkout's origin/main is behind main:
_developer_card_prefix -> build_role_cards refuses with a SkillManifestError
before any provider launch is possible. Verifies:
1. task advance reports the definite refusal code (not LLMTOOLERROR).
2. The repair claim stays CLAIMED (not UNKNOWN) and the repair Task has no
   Developer.
3. The next task advance continues the same repair Task without --recover-claim
   and without counting a second attempt.
4. An uncoded exception after a possible launch still records UNKNOWN and is
   never replayed.
"""
import contextlib
import dataclasses
import hashlib
import json
import tempfile
import types
import unittest
from unittest import mock

import review_trust_fixture
import worker_policy_fixture
from llmtool import (active_pm_inbox, review_repair, skill_ledger,
                     skill_manifest, source_freshness, task_advance,
                     task_submission_cli)
from llmtool.task_store import TaskStore
from test_review_repair import (CONTRACT, EXECUTOR, HEAD, PR, RUN, TASK,
                                TempTaskStore, _receipt, _record)


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class _RepairStore(TempTaskStore):
    """TempTaskStore with the direct-dispatch CAS methods used by AGY delegate."""

    def create(self, record):
        return self.put(record)

    def completion_d6_task_ids(self, executor_task_id):
        return [
            tid for tid in self.task_ids()
            if (self.get(tid).get('completion') or {}).get('executor_task_id') == executor_task_id
        ]

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected,
                                   **changes):
        return self.compare_and_update(task_id, expected=expected, **changes)

    def prepare_direct_dispatch(self, task_id, *, expected_developer, developer):
        return self.compare_and_update(
            task_id, expected={'developer': expected_developer},
            developer=developer)


@dataclasses.dataclass
class _Registration:
    task_id: str
    root: str
    common_dir: str
    branch: str
    source_sha: str
    result_sha: str
    trusted_agent_id: str
    session_ref: str
    native_session_id: object = None
    state: str = 'ACTIVE'


def _ec2_agy_record():
    record = _record()
    record['schema_version'] = 2
    record['state'] = 'PM_RESPONDED'
    record['developer']['created_at'] = '2026-10-27T00:00:00Z'
    record['developer']['read_paths'] = ['tools/llmtool/llmtool/review_repair.py']
    record['response'] = json.dumps({
        'schema_version': 1,
        'summary': 'AGY repair fixture.',
        'developer_assignment': {
            'route': 'agy', 'risk': 'LOW',
            'goal': 'Repair the reported issue.',
            'acceptance': ['Repair the reported issue.'],
        },
    })
    record['task_binding'] = {
        'task_id': TASK, 'run_id': RUN, 'contract_digest': CONTRACT,
    }
    record['published_pr'] = {'pr': PR, 'head_sha': HEAD}
    record['review_dispatch_claim'] = []
    record['completion_events'] = []
    record['remote_test_receipts'] = [_receipt(HEAD)]
    record['record_sha256'] = hashlib.sha256(json.dumps(
        {key: value for key, value in record.items() if key != 'record_sha256'},
        sort_keys=True).encode()).hexdigest()
    return record


class RepairDispatchCodedRefusalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = _RepairStore(self.temp.name, _ec2_agy_record())
        worker_policy_fixture.install(self)

    def _d6(self):
        return {
            'root': '/registered/d6', 'common_dir': '/tmp/git',
            'task_id': EXECUTOR, 'branch': 'feat/task',
            'result_head': HEAD, 'recorded_result_head': HEAD,
            'dirty': False, 'state': 'CLEAN',
        }

    def _registration(self):
        return _Registration(
            task_id=EXECUTOR, root='/registered/d6', common_dir='/tmp/git',
            branch='feat/task', source_sha=HEAD, result_sha=HEAD,
            trusted_agent_id='agy-developer', session_ref='s-1',
            native_session_id=None)

    def _snapshot(self):
        return types.SimpleNamespace(
            head=HEAD, root='/registered/d6', common_dir='/tmp/git',
            branch='feat/task', clean=True)

    def _github_get(self, path):
        if path == f'pulls/{PR}':
            return {'number': PR, 'state': 'open', 'merged': False,
                    'head': {'sha': HEAD}}
        if path == f'git/trees/{HEAD}?recursive=1':
            return {'truncated': False, 'tree': [
                {'path': 'tools/llmtool/tests/test_remote.py', 'type': 'blob'}]}
        return []

    def test_stale_main_card_refusal_reports_code_and_continues_without_recover_claim(self):
        reg = self._registration()
        snap = self._snapshot()
        route_cfg = {
            'routes': {
                'agy': {
                    'executable': 'agy', 'denied_dirs': [],
                    'default_model': 'gemini-test', 'default_effort': 'medium',
                    'max_prompt_bytes': 1024 * 1024, 'max_diff_bytes': 1024 * 1024,
                    'default_timeout': 600, 'max_timeout': 1200,
                },
            },
        }
        cards = {
            'schema_version': 1, 'commit': 'e' * 40, 'role': 'developer',
            'route': 'agy', 'prefix_size': 10, 'cards_sha256': '0' * 64,
            'cards': [{'path': '.claude/skills/role-cards/common-core.md',
                       'sha256': hashlib.sha256(b'core').hexdigest(),
                       'size': 4, 'text': 'core'}],
        }
        boundary = types.SimpleNamespace(
            prepare=lambda *_a, **_kw: None,
            verify=lambda *_a, **_kw: {'verified': True, 'policy_sha256': '0' * 64})
        route_result = types.SimpleNamespace(
            session_id='agy-conv-12345678', model='gemini-test', exit_code=0,
            stdout_sha256='1' * 64, elapsed_ms=10, cleanup_status='CONFIRMED',
            usage={'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
            summary='Fixed.', stderr_excerpt='', provider_evidence=None)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                task_advance, '_d6_snapshot', return_value=self._d6()))
            stack.enter_context(mock.patch.object(
                review_repair, '_registered_d6', return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                review_repair.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                task_submission_cli.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                active_pm_inbox, 'observe_claude_session', return_value='/repo'))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_delegate_d6_binding',
                return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                task_submission_cli, 'load_route_config', return_value=route_cfg))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_agy_developer_profile',
                return_value=('gemini-test', 'medium')))
            stack.enter_context(mock.patch.object(
                task_submission_cli.shutil, 'which', return_value='/usr/bin/agy'))
            stack.enter_context(mock.patch.object(
                skill_ledger, 'pin_dispatch', return_value='e' * 40))
            stack.enter_context(mock.patch.object(
                task_submission_cli.task_completion, 'ensure_main_commit',
                return_value=False))
            stack.enter_context(mock.patch.object(
                source_freshness, '_fetch_origin_main', return_value=None))
            stack.enter_context(mock.patch.object(
                skill_manifest, 'build_role_cards',
                side_effect=skill_manifest.SkillManifestError(
                    skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN)))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_scope_advice_items', return_value=[]))

            first = task_advance.advance(
                task_id=TASK, store=self.store, pr_number=PR,
                github_get=self._github_get)

            self.assertEqual(first['step'], 'REPAIR_REVIEW')
            self.assertEqual(first['codes'], ['SKILL_MANIFEST_COMMIT_NOT_ON_MAIN'])
            claims = self.store.get(TASK)['review_repair_claims']
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]['state'], 'CLAIMED')
            self.assertEqual(claims[0]['attempt'], 1)
            repair_id = claims[0]['repair_task_id']
            self.assertIsNone(self.store.get(repair_id).get('developer'))

            # Once origin/main catches up, the next task advance continues the
            # same repair Task without --recover-claim and without recounting.
            bound_session = {'done': False}

            def fake_bind_session(_reg, _session_id):
                bound_session['done'] = True
                return 'BOUND'

            def fake_read_d6(*_a, **_kw):
                if bound_session['done']:
                    return dataclasses.replace(reg, native_session_id=route_result.session_id)
                return reg

            stack.enter_context(mock.patch.object(
                skill_manifest, 'build_role_cards', return_value=cards))
            stack.enter_context(mock.patch.object(
                skill_manifest, 'role_card_prefix', return_value='core'))
            stack.enter_context(mock.patch.object(
                task_submission_cli, 'for_platform', return_value=boundary))
            stack.enter_context(mock.patch.object(
                task_submission_cli, 'run_route', return_value=route_result))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_record_skill_delivery', return_value=None))
            stack.enter_context(mock.patch.object(
                task_submission_cli.task_completion, 'read_d6_registration',
                side_effect=fake_read_d6))
            stack.enter_context(mock.patch.object(
                task_submission_cli.task_completion, 'collect_git_snapshot',
                return_value=snap))
            stack.enter_context(mock.patch.object(
                task_submission_cli.task_completion, 'bind_executor_task',
                return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                task_submission_cli.task_completion, '_worktree_diff',
                return_value=(['tools/llmtool/llmtool/review_repair.py'],
                              b'diff --git a/x b/x\n')))
            stack.enter_context(mock.patch.object(
                task_submission_cli, 'developer_diff_sensitive',
                return_value=False))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_bind_codex_native_session',
                side_effect=fake_bind_session))
            stack.enter_context(mock.patch.object(
                task_submission_cli, 'run_task_direct_result_event',
                return_value=0))

            second = task_advance.advance(
                task_id=TASK, store=self.store, pr_number=PR,
                github_get=self._github_get)

            self.assertEqual(second['step'], 'REPAIR_REVIEW')
            self.assertEqual(second['codes'], [])
            self.assertTrue(second['result']['replayed'])
            self.assertEqual(second['result']['repair_task_id'], repair_id)
            claims_after = self.store.get(TASK)['review_repair_claims']
            self.assertEqual(len(claims_after), 1)
            self.assertEqual(claims_after[0]['repair_task_id'], repair_id)
            self.assertEqual(claims_after[0]['attempt'], 1)
            self.assertEqual(claims_after[0]['state'], 'DISPATCHED')

    def test_prelaunch_scope_refusal_reports_code_and_stays_claimed(self):
        bad_record = _ec2_agy_record()
        bad_record['developer']['read_paths'] = ['tools/llmtool/llmtool']
        store = _RepairStore(self.temp.name, bad_record)
        reg = self._registration()
        snap = self._snapshot()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                task_advance, '_d6_snapshot', return_value=self._d6()))
            stack.enter_context(mock.patch.object(
                review_repair, '_registered_d6', return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                review_repair.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                task_submission_cli.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                active_pm_inbox, 'observe_claude_session', return_value='/repo'))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_delegate_d6_binding',
                return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_scope_advice_items', return_value=[]))

            result = task_advance.advance(
                task_id=TASK, store=store, pr_number=PR,
                github_get=self._github_get)

            self.assertEqual(result['step'], 'REPAIR_REVIEW')
            self.assertEqual(result['codes'], ['WRITE_PATH_NOT_IN_READ_PATHS'])
            claims = store.get(TASK)['review_repair_claims']
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]['state'], 'CLAIMED')
            self.assertIsNone(store.get(claims[0]['repair_task_id']).get('developer'))

    def test_prelaunch_screen_text_refusal_reports_code_and_stays_claimed(self):
        reg = self._registration()
        snap = self._snapshot()
        bad_record = _ec2_agy_record()
        bad_record['developer']['egress_basis'] = 'invalid\x00basis'
        store = _RepairStore(self.temp.name, bad_record)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                task_advance, '_d6_snapshot', return_value=self._d6()))
            stack.enter_context(mock.patch.object(
                review_repair, '_registered_d6', return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                review_repair.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                task_submission_cli.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                active_pm_inbox, 'observe_claude_session', return_value='/repo'))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_delegate_d6_binding',
                return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                task_submission_cli, '_scope_advice_items', return_value=[]))

            first = task_advance.advance(
                task_id=TASK, store=store, pr_number=PR,
                github_get=self._github_get)

            self.assertEqual(first['step'], 'REPAIR_REVIEW')
            self.assertEqual(first['codes'], ['DELEGATE_TEXT_INVALID'])
            claims = store.get(TASK)['review_repair_claims']
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]['state'], 'CLAIMED')
            self.assertEqual(claims[0]['attempt'], 1)
            repair_id = claims[0]['repair_task_id']
            self.assertIsNone(store.get(repair_id).get('developer'))

            # Acceptance text screening in _validate_acceptance_scope also stays coded and CLAIMED.
            bad_acceptance = _ec2_agy_record()
            with tempfile.TemporaryDirectory() as acc_dir:
                store_acc = _RepairStore(acc_dir, bad_acceptance)
                real_record_repair = review_repair._record_repair_task

                def record_with_invalid_acceptance(**kw):
                    saved = real_record_repair(**kw)
                    saved['pm_assignment']['acceptance'] = ['invalid\x00acceptance']
                    store_acc.put(saved)
                    return saved

                with mock.patch.object(
                    review_repair, '_record_repair_task',
                    side_effect=record_with_invalid_acceptance,
                ):
                    acc_res = task_advance.advance(
                        task_id=TASK, store=store_acc, pr_number=PR,
                        github_get=self._github_get)
                self.assertEqual(acc_res['step'], 'REPAIR_REVIEW')
                self.assertEqual(acc_res['codes'], ['DELEGATE_TEXT_INVALID'])
                acc_claims = store_acc.get(TASK)['review_repair_claims']
                self.assertEqual(len(acc_claims), 1)
                self.assertEqual(acc_claims[0]['state'], 'CLAIMED')
                self.assertIsNone(store_acc.get(acc_claims[0]['repair_task_id']).get('developer'))

    def test_uncoded_exception_after_possible_launch_still_records_unknown(self):
        reg = self._registration()
        snap = self._snapshot()
        calls = []

        def fail_after_possible_launch(**request):
            calls.append(request['task']['task_id'])
            raise RuntimeError('post-launch adapter failure')

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                task_advance, '_d6_snapshot', return_value=self._d6()))
            stack.enter_context(mock.patch.object(
                review_repair, '_registered_d6', return_value=(reg, snap)))
            stack.enter_context(mock.patch.object(
                review_repair.worker_dispatch, 'evaluate',
                return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                active_pm_inbox, 'observe_claude_session', return_value='/repo'))

            first = task_advance.advance(
                task_id=TASK, store=self.store, pr_number=PR,
                github_get=self._github_get,
                repair_review_call=lambda **kw: review_repair.repair_review(
                    **kw, dispatch_adapter=fail_after_possible_launch))
            self.assertEqual(first['step'], 'REPAIR_REVIEW')
            self.assertEqual(first['codes'], ['RUNTIMEERROR'])
            claims = self.store.get(TASK)['review_repair_claims']
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]['state'], 'UNKNOWN')

            # A subsequent advance does not replay an UNKNOWN claim.
            second = task_advance.advance(
                task_id=TASK, store=self.store, pr_number=PR,
                github_get=self._github_get,
                repair_review_call=lambda **kw: review_repair.repair_review(
                    **kw, dispatch_adapter=fail_after_possible_launch))
            self.assertEqual(len(calls), 1)
            self.assertFalse(second['result']['replayed'])
            self.assertEqual(self.store.get(TASK)['review_repair_claims'][0]['state'],
                             'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
