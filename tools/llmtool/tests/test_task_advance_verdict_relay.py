import functools
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llmtool import review_transcript, task_advance
from test_review_transcript import REVIEWER, SESSION, TASK, verdict
from test_task_advance import HEAD, PR, base_record, d6


class VerdictRelayTest(unittest.TestCase):
    def advance(self, *texts, state='done'):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        os.makedirs(os.path.join(home, 'projects', '-tasks-' + REVIEWER))
        with open(os.path.join(home, 'projects', '-tasks-' + REVIEWER,
                               SESSION + '.jsonl'), 'w') as handle:
            for text in texts:
                handle.write(json.dumps({'type': 'assistant', 'message': {
                    'content': [{'type': 'text', 'text': text}]}}) + '\n')
        rows = json.dumps([{'sessionId': SESSION, 'state': state}]).encode()
        relay = functools.partial(
            review_transcript.unposted_verdict, config_dir=home, claude='claude',
            run=lambda *_a, **_k: subprocess.CompletedProcess([], 0, rows, b''))
        record = base_record(**dict(TASK, developer=dict(
            TASK['developer'], state='DEVELOPER_RESPONDED')), review_dispatch_claim=[{
                'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD, 'version': 1,
                'state': 'DISPATCHED', 'reviewer_task': REVIEWER, 'native_session_id': SESSION}])
        self.posted, self.reads = [], []

        def review_result(**_kwargs):
            self.reads.append(1)
            return {'state': 'REVIEW_PASS' if self.posted else 'NOT_READY'}

        def gh(_root, args):
            self.posted.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        store = type('Store', (), {'get': lambda _self, _task_id: record})()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            return task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17,
                record_review_result_call=review_result, relay_verdict_call=relay,
                publish_gh_runner=gh, github_get=lambda _url: [])

    def test_ended_session_with_bound_verdict_is_posted_unedited(self):
        result = self.advance('Review done.\n' + verdict() + '\n')
        self.assertEqual(self.posted, [['pr', 'comment', '17', '--body', verdict()]])
        self.assertEqual((len(self.reads), result['step'], result['codes']),
                         (2, 'RECORD_REVIEW_RESULT', []))
        self.assertEqual(result['result']['state'], 'REVIEW_PASS')

    def test_binding_mismatch_is_refused(self):
        result = self.advance(verdict(task='9' * 32))
        self.assertEqual((self.posted, result['step'], result['codes']), ([], 'REVIEW_NOT_POSTED',
            ['REVIEWER_VERDICT_UNPOSTED', 'REVIEWER_VERDICT_BINDING_MISMATCH']))

    def test_ended_session_without_verdict_returns_code(self):
        result = self.advance('I ran out of time.')
        self.assertEqual((self.posted, result['step'], result['codes']), ([], 'REVIEW_NOT_POSTED',
            ['REVIEWER_VERDICT_UNPOSTED', 'REVIEWER_VERDICT_MISSING']))

    def test_running_session_does_nothing(self):
        result = self.advance(verdict(), state=None)
        self.assertEqual((self.posted, len(self.reads), result['step'], result['codes']),
                         ([], 1, 'RECORD_REVIEW_RESULT', []))
        self.assertEqual(result['result']['state'], 'NOT_READY')
