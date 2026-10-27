import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch
from llmtool.errors import LLMToolError


TASK = 'a' * 32
HEAD = 'b' * 40


class DurableFakeStore:
    """Small file-backed TaskStore seam for restart/idempotence tests."""
    def __init__(self, path):
        self.path = Path(path)
        self.data = (json.loads(self.path.read_text()) if self.path.exists() else
                     {'record_sha256': 'c' * 64, 'review_dispatch_claim': None})

    def get(self, task_id):
        assert task_id == TASK
        return dict(self.data)

    def compare_and_update(self, task_id, *, expected, **changes):
        assert task_id == TASK
        if any(self.data.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        self.data.update(changes)
        self.path.write_text(json.dumps(self.data))
        return dict(self.data)


class ReviewDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DurableFakeStore(Path(self.temp.name) / 'task.json')
        self.executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        self.preflight = (TASK, self.executor, HEAD, 'd' * 40, 'claude-reviewer')
        self.created, self.sent, self.bound = [], [], []

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *, create=None, preflight=None, policy=True):
        create = create or (lambda **kw: self.created.append(kw))
        send = lambda **kw: (self.sent.append(kw) or 'native-session')
        bind = lambda **kw: self.bound.append(kw)
        gate = SimpleNamespace(allowed=policy, reason_code='PAUSED')
        with patch.object(review_dispatch, '_preflight', side_effect=preflight or [self.preflight, self.preflight]), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate', return_value=gate):
            return review_dispatch.dispatch_review(
                store=self.store, task_id=TASK, executor_task='executor-d6',
                pr_number=42, version=1, create_d6=create, dispatch_session=send,
                bind_session=bind, prompt_renderer=lambda *_: 'bound prompt')

    def test_happy_path_claims_creates_dispatches_and_binds(self):
        result = self.invoke()
        self.assertEqual(result['state'], 'DISPATCHED')
        self.assertEqual(result['reviewer_task'][:9], 'reviewer-')
        self.assertEqual(result['native_session_id'], 'native-session')
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.bound), 1)

    def test_replay_after_restart_returns_claim_without_redispatch(self):
        first = self.invoke()
        restarted = DurableFakeStore(self.store.path)
        second = self.invoke_store(restarted)
        self.assertEqual(second, first)
        self.assertEqual(len(self.sent), 1)

    def invoke_store(self, store):
        original = self.store
        self.store = store
        try:
            return self.invoke()
        finally:
            self.store = original

    def test_crash_after_claim_becomes_unknown_and_is_not_retried(self):
        with self.assertRaises(LLMToolError):
            self.invoke(create=lambda **_: (_ for _ in ()).throw(RuntimeError('crash')))
        self.assertEqual(self.store.get(TASK)['review_dispatch_claim']['state'], 'UNKNOWN')
        before = len(self.sent)
        result = self.invoke_store(DurableFakeStore(self.store.path))
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(len(self.sent), before)

    def test_head_moved_before_session_dispatch_is_refused(self):
        moved = (TASK, self.executor, 'e' * 40, 'd' * 40, 'claude-reviewer')
        with self.assertRaises(LLMToolError):
            self.invoke(preflight=[self.preflight, moved])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.get(TASK)['review_dispatch_claim']['state'], 'UNKNOWN')

    def test_codex_reviewer_route_is_blocked(self):
        with self.assertRaises(LLMToolError) as raised:
            self.invoke(preflight=LLMToolError('REVIEWER_ROUTE_UNIMPLEMENTED'))
        self.assertEqual(self.created, [])
        self.assertEqual(self.sent, [])

    def test_paused_worker_policy_refuses_before_claim_or_side_effect(self):
        with self.assertRaises(LLMToolError):
            self.invoke(policy=False)
        self.assertIsNone(self.store.get(TASK)['review_dispatch_claim'])
        self.assertEqual(self.created, [])

    def test_wrong_family_is_never_selected(self):
        with patch.object(review_dispatch, '_preflight', side_effect=LLMToolError('unsupported')):
            with self.assertRaises(LLMToolError):
                review_dispatch.dispatch_review(
                    store=self.store, task_id=TASK, executor_task='executor-d6',
                    pr_number=42, create_d6=lambda **kw: self.created.append(kw))
        self.assertEqual(self.created, [])
        self.assertIsNone(self.store.get(TASK)['review_dispatch_claim'])


if __name__ == '__main__':
    unittest.main()
