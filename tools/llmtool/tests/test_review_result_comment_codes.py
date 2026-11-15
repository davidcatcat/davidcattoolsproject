import tempfile
import unittest

from llmtool import review_result
from llmtool.errors import LLMToolError

try:
    from test_review_result import _store, _comment, FamilyStore, TASK, EXECUTOR, PR, CONTRACT
except ImportError:
    try:
        from tests.test_review_result import _store, _comment, FamilyStore, TASK, EXECUTOR, PR, CONTRACT
    except ImportError:
        from tools.llmtool.tests.test_review_result import _store, _comment, FamilyStore, TASK, EXECUTOR, PR, CONTRACT


def run(comments_fn, store=None):
    if store is None:
        td = tempfile.TemporaryDirectory()
        store = FamilyStore(_store(td.name), 'codex')
        store._td = td
    elif not hasattr(store, 'get'):
        root = store.name if hasattr(store, 'name') else store
        store = FamilyStore(_store(root), 'codex')
    run.last_store = store

    digest = store.get(TASK)['task_binding']['contract_digest']

    def github_get(path):
        raw = comments_fn() if callable(comments_fn) else comments_fn
        if not isinstance(raw, list):
            return raw
        prepared = []
        for comment in raw:
            if isinstance(comment, dict):
                c = dict(comment)
                body = c.get('body')
                if isinstance(body, str):
                    c['body'] = body.replace(CONTRACT, digest)
                prepared.append(c)
            else:
                prepared.append(comment)
        return prepared

    try:
        return review_result.record_review_result(
            store=store,
            task_id=TASK,
            executor_task=EXECUTOR,
            pr_number=PR,
            github_get=github_get,
        )
    except LLMToolError as exc:
        exc.store = store
        raise


class ReviewResultCommentCodesTests(unittest.TestCase):
    def test_control_returns_review_pass(self):
        res = run(lambda: [_comment()])
        self.assertEqual(res.get('state'), 'REVIEW_PASS')

    def test_review_result_comments_invalid_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FamilyStore(_store(tmp), 'codex')
            with self.assertRaises(LLMToolError) as raised:
                run(lambda: {}, store=store)
            self.assertEqual(
                raised.exception.review_dispatch_code,
                'REVIEW_RESULT_COMMENTS_INVALID',
            )
            self.assertEqual(store.get(TASK).get('completion_events') or [], [])

    def test_review_result_comments_invalid_too_many(self):
        comments = [
            {'id': i, 'body': 'x', 'created_at': 't', 'updated_at': 't'}
            for i in range(100)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = FamilyStore(_store(tmp), 'codex')
            with self.assertRaises(LLMToolError) as raised:
                run(lambda: comments, store=store)
            self.assertEqual(
                raised.exception.review_dispatch_code,
                'REVIEW_RESULT_COMMENTS_INVALID',
            )
            self.assertEqual(store.get(TASK).get('completion_events') or [], [])

    def test_review_result_comments_invalid_list_holding_int(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FamilyStore(_store(tmp), 'codex')
            with self.assertRaises(LLMToolError) as raised:
                run(lambda: [1], store=store)
            self.assertEqual(
                raised.exception.review_dispatch_code,
                'REVIEW_RESULT_COMMENTS_INVALID',
            )
            self.assertEqual(store.get(TASK).get('completion_events') or [], [])

    def test_review_result_invalid_non_int_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FamilyStore(_store(tmp), 'codex')
            with self.assertRaises(LLMToolError) as raised:
                run(lambda: [dict(_comment(), id='x')], store=store)
            self.assertEqual(
                raised.exception.review_dispatch_code,
                'REVIEW_RESULT_INVALID',
            )
            self.assertEqual(store.get(TASK).get('completion_events') or [], [])

    def test_review_result_invalid_negative_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FamilyStore(_store(tmp), 'codex')
            with self.assertRaises(LLMToolError) as raised:
                run(lambda: [dict(_comment(), id=-1)], store=store)
            self.assertEqual(
                raised.exception.review_dispatch_code,
                'REVIEW_RESULT_INVALID',
            )
            self.assertEqual(store.get(TASK).get('completion_events') or [], [])

    def test_review_result_invalid_empty_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FamilyStore(_store(tmp), 'codex')
            with self.assertRaises(LLMToolError) as raised:
                run(
                    lambda: [dict(_comment(), created_at='', updated_at='')],
                    store=store,
                )
            self.assertEqual(
                raised.exception.review_dispatch_code,
                'REVIEW_RESULT_INVALID',
            )
            self.assertEqual(store.get(TASK).get('completion_events') or [], [])


if __name__ == '__main__':
    unittest.main()
