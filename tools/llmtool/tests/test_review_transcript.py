import json
import os
import shutil
import subprocess
import tempfile
import unittest

from llmtool import review_transcript

HEAD = 'a' * 40
SESSION = '01234567-89ab-cdef-0123-456789abcdef'
REVIEWER = 'reviewer-' + 'b' * 20
TASK = {'task_id': '1' * 32, 'run_id': '2' * 32, 'task_binding': {'contract_digest': 'c' * 64},
        'developer': {'provider_profile': 'agy', 'model': 'gemini-3.8-flash-medium'}}


def verdict(task='1' * 32):
    return ('<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\nFORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={task} run={"2" * 32} contract={"c" * 64} head={HEAD}')


class UnpostedVerdictTest(unittest.TestCase):
    def relay(self, *texts, state='done'):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        os.makedirs(os.path.join(home, 'projects', '-tasks-' + REVIEWER))
        with open(os.path.join(home, 'projects', '-tasks-' + REVIEWER,
                               SESSION + '.jsonl'), 'w') as handle:
            for text in texts:
                handle.write(json.dumps({'type': 'assistant', 'message': {
                    'content': [{'type': 'text', 'text': text}]}}) + '\n')
        rows = json.dumps([{'sessionId': SESSION, 'state': state}]).encode()
        return review_transcript.unposted_verdict(
            {'native_session_id': SESSION, 'reviewer_task': REVIEWER, 'head_sha': HEAD,
             'version': 1}, TASK, config_dir=home, claude='claude',
            run=lambda *_a, **_k: subprocess.CompletedProcess([], 0, rows, b''))

    def test_ended_session_returns_block_unedited(self):
        self.assertEqual(self.relay('Done.\n' + verdict() + '\n'),
                         ('ENDED', None, verdict()))

    def test_mismatch_or_two_blocks_are_refused(self):
        self.assertEqual(self.relay(verdict('9' * 32)),
                         ('ENDED', 'REVIEWER_VERDICT_BINDING_MISMATCH', None))
        self.assertEqual(self.relay(verdict(), verdict())[1], 'REVIEWER_VERDICT_AMBIGUOUS')

    def test_ended_session_without_verdict_returns_code(self):
        self.assertEqual(self.relay('No verdict.'),
                         ('ENDED', 'REVIEWER_VERDICT_MISSING', None))

    def test_running_session_does_nothing(self):
        self.assertEqual(self.relay(verdict(), state=None), ('RUNNING', None, None))
