import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

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

    def test_fenced_verdict_with_trailing_prose_posts_only_verdict_lines(self):
        self.assertEqual(self.relay('Post this:\n\n```\n' + verdict() + '\n```\n\nNot run: CI.'),
                         ('ENDED', None, verdict()))
        self.assertEqual(self.relay(verdict() + '\nNot run: CI.')[2], verdict())

    def test_extra_lines_inside_the_verdict_are_refused(self):
        lines = verdict().split('\n')
        self.assertEqual(self.relay('\n'.join(lines[:2] + ['Internal note.'] + lines[2:])),
                         ('ENDED', 'REVIEWER_VERDICT_NOT_EXACT', None))

    def test_ended_session_without_verdict_returns_code(self):
        self.assertEqual(self.relay('No verdict.'),
                         ('ENDED', 'REVIEWER_VERDICT_MISSING', None))

    def test_running_session_does_nothing(self):
        self.assertEqual(self.relay(verdict(), state=None), ('RUNNING', None, None))

    def test_unreadable_reviewer_evidence_names_its_code(self):
        # #1281 refusal-code pin: each reason a finished verdict cannot be read has one code.
        claim = {'native_session_id': SESSION, 'reviewer_task': REVIEWER, 'head_sha': HEAD,
                 'version': 1}
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        empty = lambda *_a, **_k: subprocess.CompletedProcess([], 0, b'[]', b'')
        self.assertEqual(review_transcript.unposted_verdict(
            claim, TASK, config_dir=home, claude='claude', run=empty),
            ('UNKNOWN', 'REVIEWER_SESSION_UNKNOWN', None))
        rows = json.dumps([{'sessionId': SESSION, 'state': 'stopped'}]).encode()
        ended = lambda *_a, **_k: subprocess.CompletedProcess([], 0, rows, b'')
        self.assertEqual(review_transcript.unposted_verdict(
            claim, TASK, config_dir=home, claude='claude', run=ended),
            ('ENDED', 'REVIEWER_TRANSCRIPT_UNAVAILABLE', None))
        role = {'account': 'claude2', 'config_dir': home}
        with mock.patch.object(review_transcript.role_config, 'resolve_role', return_value=role):
            self.assertEqual(review_transcript.unposted_verdict(
                dict(claim, account='other'), TASK, claude='claude', run=ended),
                ('UNKNOWN', 'REVIEWER_ACCOUNT_CHANGED', None))
        self.assertEqual(review_transcript.unposted_verdict(
            dict(claim, native_session_id='not-a-uuid'), TASK, config_dir=home, run=ended),
            ('NOT_APPLICABLE', None, None))

    def test_screened_verdict_is_withheld(self):
        from llmtool import task_submission_cli
        with mock.patch.object(task_submission_cli, '_screen_text',
                               side_effect=review_transcript.LLMToolError('synthetic')):
            self.assertEqual(self.relay(verdict()),
                             ('ENDED', 'REVIEWER_VERDICT_WITHHELD', None))


def _entries_file(home, entries):
    os.makedirs(os.path.join(home, 'projects', '-tasks-' + REVIEWER), exist_ok=True)
    with open(os.path.join(home, 'projects', '-tasks-' + REVIEWER,
                           SESSION + '.jsonl'), 'w') as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + '\n')


def assistant(*content, stop='end_turn'):
    return {'type': 'assistant', 'message': {'stop_reason': stop, 'content': list(content)}}


def text(value):
    return {'type': 'text', 'text': value}


class BlockedBackgroundReviewerTest(unittest.TestCase):
    """#2552: a background reviewer that answered sits in agents state 'blocked'."""

    def relay(self, entries, state='blocked'):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        _entries_file(home, entries)
        rows = json.dumps([{'sessionId': SESSION, 'kind': 'background',
                            'state': state, 'status': 'idle'}]).encode()
        return review_transcript.unposted_verdict(
            {'native_session_id': SESSION, 'reviewer_task': REVIEWER, 'head_sha': HEAD,
             'version': 1}, TASK, config_dir=home, claude='claude',
            run=lambda *_a, **_k: subprocess.CompletedProcess([], 0, rows, b''))

    def test_finished_turn_with_bound_pass_is_collected(self):
        # The real #2552 shape: a thinking entry, then the verdict, both end_turn.
        entries = [assistant({'type': 'thinking', 'thinking': '...'}),
                   assistant(text('Review done.\n' + verdict()))]
        self.assertEqual(self.relay(entries), ('ENDED', None, verdict()))

    def test_finished_turn_after_completed_tool_use_is_collected(self):
        entries = [assistant({'type': 'tool_use', 'id': 't1', 'name': 'Read'}, stop='tool_use'),
                   {'type': 'user', 'message': {'content': [
                       {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]}},
                   assistant(text(verdict()))]
        self.assertEqual(self.relay(entries), ('ENDED', None, verdict()))

    def test_finished_turn_without_verdict_is_a_definite_code(self):
        self.assertEqual(self.relay([assistant(text('No verdict.'))]),
                         ('ENDED', 'REVIEWER_VERDICT_MISSING', None))

    def test_pending_tool_approval_is_never_ended(self):
        # Waiting on a permission: the verdict text earlier must not be collected.
        entries = [assistant(text(verdict())),
                   assistant({'type': 'tool_use', 'id': 't2', 'name': 'Bash'}, stop='tool_use')]
        self.assertEqual(self.relay(entries), ('BLOCKED', 'REVIEWER_WAITING_APPROVAL', None))

    def test_blocked_without_end_turn_proof_is_waiting(self):
        self.assertEqual(self.relay([{'type': 'assistant', 'message': {
            'content': [{'type': 'text', 'text': verdict()}]}}]),
            ('BLOCKED', 'REVIEWER_WAITING_APPROVAL', None))

    def test_blocked_with_mismatched_binding_is_refused(self):
        self.assertEqual(self.relay([assistant(text(verdict('9' * 32)))]),
                         ('ENDED', 'REVIEWER_VERDICT_BINDING_MISMATCH', None))

    def test_other_live_states_stay_running(self):
        self.assertEqual(self.relay([assistant(text(verdict()))], state='running'),
                         ('RUNNING', None, None))
