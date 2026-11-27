"""#1230: a replaced Task refuses advance and formal review dispatch.

A Task that already has a newer continuation (a PM spec correction) must not be
advanced or reviewed against its old contract. Both entry points refuse with the
fixed code TASK_CONTINUATION_SUPERSEDED and name the latest continuation Task.
The latest continuation itself, and a Task with no continuation, are unchanged.
"""

import json
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, task_advance
from llmtool.errors import LLMToolError

ROOT = '1' * 32
NEXT = '2' * 32
LATEST = '3' * 32
PLAIN = '4' * 32

REFUSAL = 'TASK_CONTINUATION_SUPERSEDED'


class DictStore:
    """Read-only Task store fake keyed by Task ID."""

    def __init__(self, *records):
        self.records = {record['task_id']: record for record in records}

    def get(self, task_id):
        if task_id not in self.records:
            raise KeyError(task_id)
        return json.loads(json.dumps(self.records[task_id]))

    def task_ids(self):
        return sorted(self.records)


def record(task_id, continues=None):
    value = {'task_id': task_id, 'review_dispatch_claim': [],
             'review_repair_claims': [], 'completion_events': []}
    if continues is not None:
        value['continues'] = continues
    return value


def chain():
    """ROOT <- NEXT <- LATEST, plus PLAIN with no continuation."""
    return DictStore(record(ROOT), record(NEXT, ROOT), record(LATEST, NEXT),
                     record(PLAIN))


class LatestContinuationTests(unittest.TestCase):
    def test_the_deepest_continuation_is_named(self):
        store = chain()
        self.assertEqual(review_dispatch.latest_continuation(store, ROOT), LATEST)
        self.assertEqual(review_dispatch.latest_continuation(store, NEXT), LATEST)

    def test_a_latest_or_unlinked_task_has_no_continuation(self):
        store = chain()
        self.assertIsNone(review_dispatch.latest_continuation(store, LATEST))
        self.assertIsNone(review_dispatch.latest_continuation(store, PLAIN))


class AdvanceContinuationRefusalTests(unittest.TestCase):
    def test_root_with_a_newer_continuation_refuses_and_names_latest(self):
        store = chain()
        before = store.get(ROOT)
        result = task_advance.advance(task_id=ROOT, store=store)
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], [REFUSAL])
        self.assertEqual(result['result']['latest_continuation'], LATEST)
        self.assertIn(LATEST, result['result']['message'])
        self.assertEqual(store.get(ROOT), before)

    def test_middle_task_with_a_newer_continuation_refuses(self):
        result = task_advance.advance(task_id=NEXT, store=chain())
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], [REFUSAL])
        self.assertEqual(result['result']['latest_continuation'], LATEST)

    def test_latest_continuation_keeps_the_existing_behaviour(self):
        result = task_advance.advance(task_id=LATEST, store=chain())
        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertNotIn(REFUSAL, result['codes'])

    def test_a_task_without_a_continuation_keeps_the_existing_behaviour(self):
        result = task_advance.advance(task_id=PLAIN, store=chain())
        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertNotIn(REFUSAL, result['codes'])


class ReviewDispatchContinuationRefusalTests(unittest.TestCase):
    def dispatch(self, store, task_id, **kwargs):
        return review_dispatch.dispatch_review(
            store=store, task_id=task_id, executor_task='executor-1',
            pr_number=17, version=1, **kwargs)

    def test_root_refuses_before_preflight_and_names_latest(self):
        store = chain()
        before = store.get(ROOT)
        with patch.object(review_dispatch, '_preflight',
                          side_effect=AssertionError('preflight must not run')):
            with self.assertRaises(LLMToolError) as caught:
                self.dispatch(store, ROOT)
        error = caught.exception
        self.assertEqual(error.review_dispatch_code, REFUSAL)
        self.assertIn(LATEST, str(error))
        self.assertEqual(store.get(ROOT), before)

    def test_middle_task_refuses_before_preflight(self):
        with patch.object(review_dispatch, '_preflight',
                          side_effect=AssertionError('preflight must not run')):
            with self.assertRaises(LLMToolError) as caught:
                self.dispatch(chain(), NEXT)
        self.assertEqual(caught.exception.review_dispatch_code, REFUSAL)
        self.assertIn(LATEST, str(caught.exception))

    def test_latest_and_unlinked_tasks_reach_preflight(self):
        for task_id in (LATEST, PLAIN):
            with self.subTest(task_id=task_id):
                with patch.object(review_dispatch, '_preflight',
                                  side_effect=RuntimeError('preflight reached')):
                    with self.assertRaises(RuntimeError) as caught:
                        self.dispatch(chain(), task_id)
                self.assertEqual(str(caught.exception), 'preflight reached')


if __name__ == '__main__':
    unittest.main()
