"""#1230 refusal-code pin: completion event BUSY and UNAVAILABLE codes of the active PM inbox."""
import unittest
from unittest.mock import patch

from llmtool import active_pm_inbox as inbox
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

TASK = '1'*32

BINDING = {'event_id': 'e'*32, 'run_id': 'run-1', 'session_id': 'ses_Test', 'source': 'REVIEWER',
           'source_sha': 'a'*40, 'payload_sha256': 'f'*64}


def coded(code):
    error = LLMToolError('synthetic')
    if code is not None:
        error.task_store_error_code = code
    return error


class FakeStore:
    def __init__(self, code):
        self.code, self.calls = code, 0

    def get(self, task_id):
        return {'task_id': task_id, 'completion_events': []}

    def record_completion_event(self, task_id, **binding):
        self.calls += 1
        raise coded(self.code)


class CompletionEventCodeTests(unittest.TestCase):
    def setUp(self):
        for target, name, value in ((task_submission_cli, '_active_pm_handoff', {'packet': {}}),
                                    (inbox, '_handoff_event', BINDING)):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_three_lost_sequence_races_are_busy(self):
        for race in ('COMPLETION_EVENT_CONFLICT', 'COMPLETION_EVENT_SEQUENCE_INVALID'):
            with self.subTest(race=race):
                store = FakeStore(race)
                with self.assertRaises(LLMToolError) as caught:
                    inbox.record_ready_event(store, TASK)
                self.assertEqual(caught.exception.task_store_error_code, 'COMPLETION_EVENT_BUSY')
                self.assertEqual(store.calls, 3)

    def test_other_store_codes_are_not_retried(self):
        store = FakeStore('COMPLETION_EVENT_LIMIT')
        with self.assertRaises(LLMToolError) as caught:
            inbox.record_ready_event(store, TASK)
        self.assertEqual(caught.exception.task_store_error_code, 'COMPLETION_EVENT_LIMIT')
        self.assertEqual(store.calls, 1)

    def test_inbox_reports_uncoded_failure_as_unavailable(self):
        record = {'task_id': TASK, 'completion_events': []}
        for code, expected in ((None, 'COMPLETION_EVENT_UNAVAILABLE'),
                               ('COMPLETION_EVENT_BUSY', 'COMPLETION_EVENT_BUSY')):
            with self.subTest(code=code), \
                    patch.object(inbox, '_session_records', return_value=([record], 0)), \
                    patch.object(inbox, '_developer_assignments', return_value=([], [], None)), \
                    patch.object(inbox, 'record_ready_event', side_effect=coded(code)):
                view = inbox.read_inbox(FakeStore(None), 'pm-test')
            self.assertEqual(view['status'], 'ATTENTION')
            self.assertEqual(view['blocked'], [{'task_id': TASK, 'code': expected,
                                                'unacknowledged_event_count': 0,
                                                'action': 'RETRY_INBOX'}])
            self.assertEqual(view['events'], [])


if __name__ == '__main__':
    unittest.main()
