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

    def test_fenced_verdict_ends_at_its_closing_fence(self):
        self.assertEqual(self.relay('Post this:\n\n```\n' + verdict() + '\n```\n\nNot run: CI.'),
                         ('ENDED', None, verdict()))
        # #2536: an unfenced verdict keeps everything after its binding line.
        self.assertEqual(self.relay(verdict() + '\nNot run: CI.')[2],
                         verdict() + '\nNot run: CI.')

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
        # #2602 item i: a claim that records its reviewer account reads that
        # account's config directory through role resolution with a one-shot
        # account override; a claim without a recorded account keeps the
        # reviewer role resolved at collection time; a recorded account the
        # role configuration refuses is refused and nothing else is read.
        x = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, x)
        os.makedirs(os.path.join(x, 'projects', '-tasks-' + REVIEWER))
        with open(os.path.join(x, 'projects', '-tasks-' + REVIEWER,
                               SESSION + '.jsonl'), 'w') as handle:
            handle.write(json.dumps({'type': 'assistant', 'message': {
                'content': [{'type': 'text', 'text': verdict()}]}}) + '\n')
        role = {'account': 'claude2', 'config_dir': x}
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               return_value=role) as resolve:
            self.assertEqual(review_transcript.unposted_verdict(
                dict(claim, account='claude2'), TASK, claude='claude', run=ended),
                ('ENDED', None, verdict()))
            self.assertEqual(resolve.call_args,
                             mock.call('reviewer', cli={'account': 'claude2'}))
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               return_value={'config_dir': home}) as resolve:
            self.assertEqual(review_transcript.unposted_verdict(
                claim, TASK, claude='claude', run=ended),
                ('ENDED', 'REVIEWER_TRANSCRIPT_UNAVAILABLE', None))
            self.assertEqual(resolve.call_args, mock.call('reviewer'))
        refused = review_transcript.LLMToolError('synthetic')
        refused.role_config_code = 'ROLE_CONFIG_OVERRIDE_INVALID'
        run = mock.Mock()
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               side_effect=refused) as resolve:
            self.assertEqual(review_transcript.unposted_verdict(
                dict(claim, account='other'), TASK, run=run),
                ('UNKNOWN', 'ROLE_CONFIG_OVERRIDE_INVALID', None))
            self.assertEqual(resolve.call_args,
                             mock.call('reviewer', cli={'account': 'other'}))
        run.assert_not_called()
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               side_effect=review_transcript.LLMToolError('synthetic')):
            self.assertEqual(review_transcript.unposted_verdict(
                dict(claim, account='other'), TASK, claude='claude', run=ended),
                ('UNKNOWN', 'REVIEWER_ACCOUNT_UNCONFIGURED', None))
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


BINDING = (f'LLMTOOL-REVIEW-BINDING v1 task={"1" * 32} run={"2" * 32} '
           f'contract={"c" * 64} head={HEAD}')


def fail_verdict(*body):
    return '\n'.join([
        '<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->',
        f'Verdict: FAIL | Version: v1 | HEAD: {HEAD}', BINDING, *body])


class FullVerdictTest(unittest.TestCase):
    """#2536: the findings and evidence after the binding line are part of the verdict."""

    relay = UnpostedVerdictTest.relay

    def test_fail_with_findings_after_binding_is_kept_whole(self):
        # Shape of PR #2365 v4: prose, blank, header, then text and four findings.
        body = ['', 'Scope reviewed: the note.', '',
                'Finding: F1 [High] docs/a.md:1 synthetic one.', 'Evidence line.', '',
                'Finding: F2 [High] docs/a.md:2 synthetic two.', '',
                'Finding: F3 [Medium] docs/a.md:3 synthetic three.', '',
                'Finding: F4 [Low] docs/a.md:4 synthetic four.', '', 'Disposition: FIX.']
        expected = fail_verdict(*body)
        self.assertEqual(self.relay('I reviewed the change.\n\n' + expected + '\n'),
                         ('ENDED', None, expected))

    def test_prose_verdict_line_before_the_marker_is_not_part_of_it(self):
        # Shape of PR #2553 v1: a summary line starting "Verdict:" before the marker.
        expected = fail_verdict('', 'Finding: F1 [High] llmtool/x.py:1 synthetic.', '', 'Why.')
        self.assertEqual(self.relay('Verdict: FAIL, one High finding.\n\n' + expected),
                         ('ENDED', None, expected))

    def test_pass_with_accept_before_binding_keeps_its_notes(self):
        # Shape of PR #2540 v1: marker, Verdict, ACCEPT, binding, then the review notes.
        lines = verdict().split('\n')
        expected = '\n'.join(lines[:2] + [lines[2], lines[3], '', 'Checked: synthetic notes.'])
        self.assertEqual(self.relay('Done.\n\n' + expected), ('ENDED', None, expected))

    def test_fail_without_findings_is_never_posted(self):
        self.assertEqual(self.relay(fail_verdict('', 'Looks wrong.')),
                         ('ENDED', 'REVIEWER_VERDICT_NO_FINDINGS', None))

    def test_text_between_header_lines_or_a_second_binding_is_refused(self):
        lines = fail_verdict().split('\n')
        moved = '\n'.join(lines[:2] + ['Note.', lines[2], 'Finding: F1 [High] a:1 x'])
        self.assertEqual(self.relay(moved)[1], 'REVIEWER_VERDICT_NOT_EXACT')
        twice = fail_verdict('Finding: F1 [High] a:1 x', BINDING)
        self.assertEqual(self.relay(twice)[1], 'REVIEWER_VERDICT_NOT_EXACT')
        other = fail_verdict('Finding: F1 [High] a:1 x', BINDING.replace('1' * 32, '9' * 32))
        self.assertEqual(self.relay(other)[1], 'REVIEWER_VERDICT_NOT_EXACT')

    def test_fenced_fail_keeps_findings_up_to_the_closing_fence(self):
        expected = fail_verdict('', 'Finding: F1 [High] a.py:1 synthetic.')
        self.assertEqual(self.relay('Post:\n```\n' + expected + '\n```\nAfter the fence.'),
                         ('ENDED', None, expected))

    def test_truncated_fail_reads_the_recorded_account_directory(self):
        # #2602 item i: the FAIL repost path reads the session and transcript
        # from the config directory of the account recorded on the claim.
        expected = fail_verdict('', 'Finding: F1 [High] a.py:1 synthetic.')
        posted = review_transcript._legacy_header(expected)
        x = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, x)
        os.makedirs(os.path.join(x, 'projects', '-tasks-' + REVIEWER))
        with open(os.path.join(x, 'projects', '-tasks-' + REVIEWER,
                               SESSION + '.jsonl'), 'w') as handle:
            handle.write(json.dumps({'type': 'assistant', 'message': {
                'content': [{'type': 'text', 'text': expected}]}}) + '\n')
        claim = {'native_session_id': SESSION, 'reviewer_task': REVIEWER,
                 'head_sha': HEAD, 'version': 1}
        comments = [{'id': 1, 'body': posted}]
        role = {'account': 'claude2', 'config_dir': x}
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               return_value=role) as resolve:
            self.assertEqual(review_transcript.truncated_fail_repost(
                dict(claim, account='claude2'), TASK, comments),
                (None, expected, 1))
            self.assertEqual(resolve.call_args,
                             mock.call('reviewer', cli={'account': 'claude2'}))
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               return_value={'config_dir': x}) as resolve:
            self.assertEqual(review_transcript.truncated_fail_repost(claim, TASK, comments),
                             (None, expected, 1))
            self.assertEqual(resolve.call_args, mock.call('reviewer'))
        refused = review_transcript.LLMToolError('synthetic')
        refused.role_config_code = 'ROLE_CONFIG_OVERRIDE_INVALID'
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               side_effect=refused) as resolve:
            self.assertEqual(review_transcript.truncated_fail_repost(
                dict(claim, account='other'), TASK, comments),
                ('ROLE_CONFIG_OVERRIDE_INVALID', None, None))
            self.assertEqual(resolve.call_args,
                             mock.call('reviewer', cli={'account': 'other'}))
