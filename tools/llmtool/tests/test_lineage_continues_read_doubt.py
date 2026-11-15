"""#1502 WP-D refusal-code pin: check_continues fails closed when a Task store read is in doubt."""
import unittest

from llmtool import lineage_counts
from llmtool.errors import LLMToolError

NEW, PRED, OTHER = '1'*32, '2'*32, '3'*32


class FakeStore:
    """task_ids() lists only ``records``; get() also serves ``hidden`` Tasks."""
    def __init__(self, records, *, hidden=None, ids_error=False, broken=()):
        self.records, self.hidden = records, hidden or {}
        self.ids_error, self.broken = ids_error, set(broken)

    def task_ids(self):
        if self.ids_error:
            raise OSError('synthetic')
        return list(self.records)

    def get(self, task_id):
        if task_id in self.broken:
            raise OSError('synthetic')
        if task_id in self.records:
            return self.records[task_id]
        return self.hidden[task_id]


class ContinuesReadDoubtTests(unittest.TestCase):
    def code(self, store):
        with self.assertRaises(LLMToolError) as caught:
            lineage_counts.check_continues(store, NEW, PRED)
        return caught.exception.continues_code

    def test_valid_predecessor_is_accepted(self):
        store = FakeStore({PRED: {'task_id': PRED}, OTHER: {'task_id': OTHER}})
        self.assertIsNone(lineage_counts.check_continues(store, NEW, PRED))

    def test_store_read_doubt_is_store_unavailable(self):
        pred = {PRED: {'task_id': PRED}}
        for label, store in (
                ('task ids unreadable', FakeStore(pred, ids_error=True)),
                ('other Task unreadable', FakeStore(dict(pred, **{OTHER: {'task_id': OTHER}}), broken=(OTHER,))),
                ('other Task not a dict', FakeStore(dict(pred, **{OTHER: ['not', 'a', 'dict']})))):
            with self.subTest(label=label):
                self.assertEqual(self.code(store), 'CONTINUES_STORE_UNAVAILABLE')

    def test_chain_read_doubt_is_chain_invalid(self):
        linked = {'task_id': PRED, 'continues': OTHER}
        for label, store in (
                ('predecessor unreadable', FakeStore({PRED: {'task_id': PRED}}, broken=(PRED,))),
                ('predecessor not a dict', FakeStore({PRED: None})),
                ('predecessor wrong task id', FakeStore({PRED: {'task_id': OTHER}})),
                ('lineage not a dict', FakeStore({PRED: {'task_id': PRED, 'lineage': 'child'}})),
                ('malformed continues', FakeStore({PRED: {'task_id': PRED, 'continues': 'x'*32}})),
                ('earlier Task missing', FakeStore({PRED: linked})),
                ('earlier Task wrong task id', FakeStore({PRED: linked}, hidden={OTHER: {'task_id': PRED}}))):
            with self.subTest(label=label):
                self.assertEqual(self.code(store), 'CONTINUES_CHAIN_INVALID')


if __name__ == '__main__':
    unittest.main()
