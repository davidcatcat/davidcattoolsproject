"""#289: the optional formal Reviewer order in policy.json.

Real TaskStore records are used for the reviewer selection checks; the shared
worker policy fixture is installed, and repository_config is patched with the
same evidence values as the temporary worker policy so no live Owner file is
read.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool import repair_policy, review_dispatch, task_advance, task_store, worker_dispatch
from llmtool.errors import LLMToolError

import worker_policy_fixture


HEAD = 'b' * 40
_ABSENT = object()
REPOSITORY = {'slug': 'owner/repo', 'dispatch_issue': 120, 'dispatch_evidence_pr': 1}
WORKER_EVIDENCE = 'https://github.com/owner/repo/issues/120#issuecomment-1'
BASE_POLICY = {
    'schema_version': 1,
    'repair_max': 5,
    'parent_debug_max': 3,
    'group_debug_max': 3,
    'child_max': 3,
    'replan_max': 1,
    'test_fail_max': 5,
    'review_fail_max': 6,
    'waiting_notify_after_hours': 24,
}


def _real_store(root):
    task_id, run_id = 'a' * 32, 'b' * 32
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': task_id,
        'goal': 'test formal reviewer order selection',
        'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response',
                      'description': 'Record the selected PM response or a truthful unavailable outcome.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [],
        'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    record = {
        'schema_version': 2, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-10-27T00:00:00Z',
        'updated_at': '2026-10-27T00:00:00Z', 'task_contract': contract,
        'task_binding': vars(parsed.binding(run_id)),
        'request': 'test formal reviewer order selection',
        'request_sha256': hashlib.sha256(
            b'test formal reviewer order selection').hexdigest(),
        'response': None, 'usage': None,
        'error_code': None, 'developer': None, 'pm_assignment': None,
        'completion': None,
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store, task_id


class ReviewerOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        worker_policy_fixture.install(self)
        patcher = patch.object(worker_dispatch, 'repository_config',
                               return_value=REPOSITORY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store, self.task_id = _real_store(Path(self.temp.name) / 'store')

    def _task(self, profile, model=None):
        developer = {'provider_profile': profile}
        if model is not None:
            developer['model'] = model
        return dict(self.store.get(self.task_id), developer=developer)

    def _use_policy(self, order=_ABSENT):
        data = dict(BASE_POLICY)
        if order is not _ABSENT:
            data['formal_reviewer_order'] = order
        path = Path(self.temp.name) / 'policy.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        patcher = patch.object(repair_policy, 'policy_path', return_value=path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def _use_worker_policy(self, paused=()):
        workers = {}
        for key in sorted(worker_dispatch.WORKERS):
            if key == 'gw-agent':
                workers[key] = {
                    'dispatch': 'PAUSED', 'decided_by': 'PM',
                    'decided_at': '2026-10-01', 'reason': 'test fixture',
                    'evidence': 'https://github.com/davidcatcat/davidcattoolsproject/issues/120',
                }
            else:
                workers[key] = {
                    'dispatch': 'PAUSED' if key in paused else 'ALLOWED',
                    'decided_by': 'Owner', 'decided_at': '2026-10-01',
                    'reason': 'test fixture', 'evidence': WORKER_EVIDENCE,
                }
        path = Path(self.temp.name) / 'worker-dispatch.json'
        path.write_text(json.dumps({'schema_version': 1, 'workers': workers}),
                        encoding='utf-8')
        patcher = patch.object(worker_dispatch, '_POLICY_PATH', path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def test_order_absent_gives_the_old_result(self):
        self._use_policy()
        self._use_worker_policy()
        self.assertEqual(review_dispatch._select_reviewer(
            self._task('agy', 'gemini-2.5-pro')), 'claude-reviewer')
        self.assertEqual(review_dispatch._select_reviewer(
            self._task('claude')), 'codex-reviewer')

    def test_codex_first_order_with_agy_author_gives_codex(self):
        self._use_policy(['codex-reviewer', 'claude-reviewer'])
        self._use_worker_policy()
        self.assertEqual(review_dispatch._select_reviewer(
            self._task('agy', 'gemini-2.5-pro')), 'codex-reviewer')

    def test_paused_codex_reviewer_falls_back_to_claude(self):
        self._use_policy(['codex-reviewer', 'claude-reviewer'])
        self._use_worker_policy(paused=('codex-reviewer',))
        self.assertEqual(review_dispatch._select_reviewer(
            self._task('agy', 'gemini-2.5-pro')), 'claude-reviewer')

    def test_claude_author_gets_codex(self):
        self._use_policy(['codex-reviewer', 'claude-reviewer'])
        self._use_worker_policy()
        self.assertEqual(review_dispatch._select_reviewer(
            self._task('claude')), 'codex-reviewer')

    def test_advance_fallback_picks_the_other_key(self):
        self._use_policy(['codex-reviewer', 'claude-reviewer'])
        self._use_worker_policy()
        task = self._task('agy', 'gemini-2.5-pro')
        self.assertEqual(review_dispatch._select_reviewer(task), 'codex-reviewer')
        error = LLMToolError('route unavailable')
        error.review_dispatch_code = 'REVIEWER_NOT_ALLOWED'
        self.assertEqual(task_advance._reviewer_fallback(task, error),
                         ('claude-reviewer', 'REVIEWER_NOT_ALLOWED'))
        claimed = dict(task, review_dispatch_claim=[{
            'pr': 42, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}])
        self.assertEqual(task_advance._blocked_fallback_reviewer(claimed, HEAD),
                         'claude-reviewer')

    def test_invalid_order_is_refused_at_load(self):
        for value in ('codex-reviewer', [], ['codex-reviewer', 'codex-reviewer'],
                      ['gemini-reviewer'], ['codex-reviewer', 1], None):
            with self.subTest(value=value):
                self._use_policy(value)
                with self.assertRaises(LLMToolError):
                    repair_policy.load_policy()

    def test_valid_order_loads(self):
        self._use_policy(['codex-reviewer', 'claude-reviewer'])
        self.assertEqual(repair_policy.load_policy()['formal_reviewer_order'],
                         ['codex-reviewer', 'claude-reviewer'])
        self.assertEqual(repair_policy.formal_reviewer_order(),
                         ['codex-reviewer', 'claude-reviewer'])


if __name__ == '__main__':
    unittest.main()
