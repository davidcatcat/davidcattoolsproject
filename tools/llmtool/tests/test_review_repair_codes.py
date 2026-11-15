import tempfile
import unittest
from unittest.mock import patch

from llmtool import review_repair
from llmtool.errors import LLMToolError

import test_review_repair as trr
import worker_policy_fixture


def setUpModule():
    trr.setUpModule()


def tearDownModule():
    if hasattr(run, 'last_temp') and run.last_temp is not None:
        try:
            run.last_temp.cleanup()
        except Exception:
            pass
    trr.tearDownModule()


class _NoneGetStoreWrapper:
    """Store wrapper whose get() returns None while other attributes delegate."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._inner, name)


def run(get=lambda _p: [trr._comment()], store_wrap=None, **kwargs):
    td = tempfile.TemporaryDirectory()
    inner = trr.TempTaskStore(td.name, trr._record())
    run.last_temp = td
    run.last_inner_store = inner

    store = store_wrap(inner) if store_wrap is not None else inner
    run.last_store = store

    decision = type('Decision', (), {'allowed': True})()

    def default_dispatch_adapter(**request):
        return {'status': 'DISPATCHED', 'head': trr.HEAD}

    def default_repair_task_recorder(**request):
        return {
            'task_id': request['repair_task_id'],
            'pm_assignment': {
                'acceptance': list(request['developer']['acceptance']) + ['bounded repair item'],
            },
        }

    dispatch_adapter = kwargs.get('dispatch_adapter', default_dispatch_adapter)
    repair_task_recorder = kwargs.get('repair_task_recorder', default_repair_task_recorder)
    current_head = kwargs.get('current_head', lambda task, executor: trr.HEAD)

    with patch.object(review_repair.worker_dispatch, 'evaluate', return_value=decision), \
         patch.object(review_repair.shutil, 'which', return_value='/usr/bin/codex'):
        return review_repair.repair_review(
            store=store,
            task_id=trr.TASK,
            executor_task=trr.EXECUTOR,
            pr_number=trr.PR,
            github_get=get,
            dispatch_adapter=dispatch_adapter,
            current_head=current_head,
            repair_task_recorder=repair_task_recorder,
        )


class ReviewRepairCodesTests(unittest.TestCase):
    def setUp(self):
        worker_policy_fixture.install(self)

    def test_control(self):
        result = run()
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get('next_action'), 'review-dispatch --version 2')

    def test_repair_task_invalid(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(store_wrap=_NoneGetStoreWrapper)
        self.assertEqual(ctx.exception.review_repair_code, 'REPAIR_TASK_INVALID')
        self.assertIsNone(run.last_inner_store.get(trr.TASK).get('review_repair_claims'))

    def test_review_result_comments_invalid_dict(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(get=lambda _p: {})
        self.assertEqual(ctx.exception.review_repair_code, 'REVIEW_RESULT_COMMENTS_INVALID')
        self.assertIsNone(run.last_store.get(trr.TASK).get('review_repair_claims'))
        self.assertIsNone(run.last_inner_store.get(trr.TASK).get('review_repair_claims'))

    def test_review_result_comments_invalid_100_comments(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(get=lambda _p: [{'id': i} for i in range(100)])
        self.assertEqual(ctx.exception.review_repair_code, 'REVIEW_RESULT_COMMENTS_INVALID')
        self.assertIsNone(run.last_store.get(trr.TASK).get('review_repair_claims'))
        self.assertIsNone(run.last_inner_store.get(trr.TASK).get('review_repair_claims'))

    def test_review_result_comments_invalid_non_dict_element(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(get=lambda _p: [1])
        self.assertEqual(ctx.exception.review_repair_code, 'REVIEW_RESULT_COMMENTS_INVALID')
        self.assertIsNone(run.last_store.get(trr.TASK).get('review_repair_claims'))
        self.assertIsNone(run.last_inner_store.get(trr.TASK).get('review_repair_claims'))

    def test_review_result_comments_invalid(self):
        cases = [
            {},
            [{'id': i} for i in range(100)],
            [1],
        ]
        for comments_payload in cases:
            with self.subTest(comments_payload=comments_payload):
                with self.assertRaises(LLMToolError) as ctx:
                    run(get=lambda _p: comments_payload)
                self.assertEqual(ctx.exception.review_repair_code, 'REVIEW_RESULT_COMMENTS_INVALID')
                self.assertIsNone(run.last_store.get(trr.TASK).get('review_repair_claims'))
                self.assertIsNone(run.last_inner_store.get(trr.TASK).get('review_repair_claims'))


if __name__ == '__main__':
    unittest.main()
