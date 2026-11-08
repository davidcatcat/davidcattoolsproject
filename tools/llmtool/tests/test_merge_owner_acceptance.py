"""task advance --merge --owner-acceptance URL: a recorded Owner acceptance.

A PM relay ANSWERED comment in this repository that names the exact PR and
full head SHA, created after the head commit, lets a PR whose files need Owner
acceptance merge. Every other merge gate stays in place.
"""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from llmtool import task_advance
from llmtool import task_submission_cli
from llmtool.cli import parser_for_cli


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
MAIN_SHA = 'e' * 40
SLUG = 'example-owner/example-repo'
COMMENT_ID = 9001
URL = f'https://github.com/{SLUG}/issues/42#issuecomment-{COMMENT_ID}'
PR_URL = f'https://github.com/{SLUG}/pull/17#issuecomment-{COMMENT_ID}'
MARKER = '<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->'
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
TUI_FILES = ['tools/llmtool/llmtool/chat_tui.py']
CODE_FILES = ['src/module.py']
AUTHOR_TIME = '2026-11-08T08:00:00Z'
COMMIT_TIME = '2026-11-08T09:00:00Z'
LATER = '2026-11-08T10:00:00Z'
COMMENT_PATH = f'issues/comments/{COMMENT_ID}'
COMMIT_PATH = f'commits/{HEAD}'


def run(name, conclusion='success'):
    return {'name': name, 'head_sha': HEAD, 'status': 'completed', 'conclusion': conclusion}


GREEN = [run(LINUX), run(MACOS)]


def body(*, pr='#17', head=HEAD, first=MARKER, newline='\n'):
    return f'{first}{newline}Owner accepts PR {pr} at head {head}, relayed by PM.'


def comment(text=None, *, created=LATER, updated=None, html_url=URL):
    return {'id': COMMENT_ID, 'body': body() if text is None else text,
            'html_url': html_url, 'created_at': created,
            'updated_at': created if updated is None else updated}


def head_commit(*, author=AUTHOR_TIME, committer=COMMIT_TIME):
    return {'sha': HEAD, 'commit': {'author': {'date': author},
                                    'committer': {'date': committer}}}


def open_pr():
    return {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
            'mergeable': True, 'labels': [{'name': 'mac-ci'}]}


def fake_github(*, files=TUI_FILES, runs=GREEN, comment_value=None, commit_value=None):
    """Return an in-memory github_get and the paths it was asked for.

    A value that is an exception instance is raised instead of returned.
    """
    reads = []
    pr = open_pr()
    answers = {
        f'commits/{HEAD}/check-runs?per_page=100': {
            'total_count': len(runs),
            'check_runs': [dict(item, id=index + 1) for index, item in enumerate(runs)]},
        'pulls/17/files?per_page=100': [{'filename': item} for item in files],
        'pulls/17': pr,
        'branches/main': {'commit': {'sha': MAIN_SHA}},
        f'compare/{MAIN_SHA}...{HEAD}': {'status': 'ahead'},
        COMMENT_PATH: comment() if comment_value is None else comment_value,
        COMMIT_PATH: head_commit() if commit_value is None else commit_value,
    }

    def github_get(path):
        reads.append(path)
        if path not in answers:
            raise AssertionError(f'unexpected read: {path}')
        value = answers[path]
        if isinstance(value, Exception):
            raise value
        return value

    return github_get, reads


class OwnerAcceptanceTestCase(unittest.TestCase):
    def setUp(self):
        slug = patch.object(task_advance.task_completion, '_trusted_slug', return_value=SLUG)
        slug.start()
        self.addCleanup(slug.stop)

    def checks(self, owner_acceptance=None, *, allow_missing_macos=False, **fake):
        github_get, reads = fake_github(**fake)
        kwargs = {'allow_missing_macos': allow_missing_macos}
        if owner_acceptance is not None:
            kwargs['owner_acceptance'] = owner_acceptance
        code = task_advance._merge_checks({}, 17, HEAD, open_pr(), github_get, **kwargs)
        return code, reads

    def merge_once(self, owner_acceptance=None, *, review_pass=True, **fake):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS' if review_pass else 'FAIL'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS' if review_pass else 'REVIEW_FAIL'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}
        updates, calls = [], []

        class Store:
            def compare_and_update(self, *args, **kwargs):
                updates.append((args, kwargs))

        def gh_runner(_root, args):
            calls.append(list(args))
            return type('R', (), {'returncode': 0})()

        github_get, reads = fake_github(**fake)
        kwargs = {} if owner_acceptance is None else {'owner_acceptance': owner_acceptance}
        result = task_advance._merge_once(store=Store(), record=record, number=17,
            head=HEAD, pr=open_pr(), events=[event], github_get=github_get,
            gh_runner=gh_runner, root='/repo', **kwargs)
        return result, calls, reads

    def assert_code(self, expected, owner_acceptance=URL, **fake):
        code, reads = self.checks(owner_acceptance, **fake)
        self.assertEqual(code, expected)
        return reads


class AcceptTests(OwnerAcceptanceTestCase):
    def test_valid_acceptance_lets_a_tui_pr_pass_the_checks(self):
        code, reads = self.checks(URL)
        self.assertIsNone(code)
        self.assertIn(COMMENT_PATH, reads)
        self.assertIn(COMMIT_PATH, reads)

    def test_valid_acceptance_merges_with_the_url_in_provenance(self):
        result, calls, _reads = self.merge_once(URL)
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result'],
                         {'pr': 17, 'head': HEAD, 'owner_acceptance': URL})
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)
        provenance = [call for call in calls if call[:2] == ['pr', 'comment']]
        self.assertEqual(len(provenance), 1)
        self.assertTrue(provenance[0][-1].endswith(' owner_acceptance=' + URL))

    def test_pull_request_comment_url_and_crlf_marker_line_are_accepted(self):
        self.assertIsNone(self.checks(
            PR_URL, comment_value=comment(html_url=PR_URL))[0])
        self.assertIsNone(self.checks(
            URL, comment_value=comment(body(newline='\r\n')))[0])

    def test_pr_and_head_may_sit_next_to_punctuation(self):
        text = f'{MARKER}\nAccepted (#17), head={HEAD}.'
        self.assertIsNone(self.checks(URL, comment_value=comment(text))[0])

    def test_fractional_and_offset_utc_times_parse(self):
        self.assertIsNone(self.checks(URL, comment_value=comment(
            created='2026-11-08T09:00:00.000001+00:00'))[0])


class UnchangedWithoutOptionTests(OwnerAcceptanceTestCase):
    def test_missing_option_keeps_the_manual_refusal(self):
        code, reads = self.checks()
        self.assertEqual(code, 'MERGE_OWNER_ACCEPTANCE_MANUAL')
        self.assertNotIn(COMMENT_PATH, reads)
        self.assertNotIn(COMMIT_PATH, reads)
        result, calls, _reads = self.merge_once()
        self.assertEqual(result, {'step': 'MERGE_REFUSED',
                                  'codes': ['MERGE_OWNER_ACCEPTANCE_MANUAL'],
                                  'result': None})
        self.assertEqual(calls, [])

    def test_merge_without_option_keeps_provenance_and_result(self):
        result, calls, reads = self.merge_once(files=CODE_FILES)
        self.assertEqual(result, {'step': 'MERGED', 'codes': [],
                                  'result': {'pr': 17, 'head': HEAD}})
        provenance = [call for call in calls if call[:2] == ['pr', 'comment']]
        self.assertEqual(provenance[0][-1], (
            f'llmtool task advance merge provenance: task={"1" * 32} '
            f'head={HEAD} review_version=1 checks=exact-head-success-or-skipped'))
        self.assertNotIn(COMMENT_PATH, reads)

    def test_option_is_checked_even_when_files_do_not_need_it(self):
        self.assertIsNone(self.checks(URL, files=CODE_FILES)[0])
        self.assert_code('MERGE_OWNER_ACCEPTANCE_MARKER', files=CODE_FILES,
                         comment_value=comment(body(first='plain text')))


class OtherGatesStillApplyTests(OwnerAcceptanceTestCase):
    def test_earlier_check_gates_keep_their_codes(self):
        reads = self.assert_code('MERGE_REQUIRED_CHECK_SKIPPED',
                                 runs=[run(LINUX, 'skipped'), run(MACOS)])
        self.assertNotIn(COMMENT_PATH, reads)
        self.assert_code('MERGE_CHECKS_NOT_GREEN', runs=[run(LINUX, 'failure'), run(MACOS)])

    def test_later_macos_gate_still_applies(self):
        self.assert_code('MERGE_MACOS_CHECK_REQUIRED',
                         runs=[run(LINUX), run(MACOS, 'skipped')])

    def test_review_pass_is_still_required(self):
        result, calls, reads = self.merge_once(URL, review_pass=False)
        self.assertEqual(result['codes'], ['MERGE_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])
        self.assertEqual(reads, [])


class RefusalTests(OwnerAcceptanceTestCase):
    def test_wrong_pr_number(self):
        for pr in ('#170', '#1', 'PR17', 'PR#17', '1#17'):
            with self.subTest(pr=pr):
                self.assert_code('MERGE_OWNER_ACCEPTANCE_PR_MISMATCH',
                                 comment_value=comment(body(pr=pr)))

    def test_wrong_or_short_head(self):
        for head in (OTHER_HEAD, HEAD[:12], HEAD.upper(), 'c' + HEAD, HEAD + 'F'):
            with self.subTest(head=head):
                reads = self.assert_code('MERGE_OWNER_ACCEPTANCE_HEAD_MISMATCH',
                                         comment_value=comment(body(head=head)))
                self.assertNotIn(COMMIT_PATH, reads)

    def test_wrong_marker(self):
        firsts = (
            MARKER.replace('status=ANSWERED', 'status=FIX'),
            MARKER.replace('actor=PM', 'actor=Owner'),
            MARKER.replace('target=Owner', 'target=PR'),
            ' ' + MARKER,
            MARKER + ' ',
        )
        for first in firsts:
            with self.subTest(first=first):
                self.assert_code('MERGE_OWNER_ACCEPTANCE_MARKER',
                                 comment_value=comment(body(first=first)))
        # The marker on line 2 is not accepted.
        self.assert_code('MERGE_OWNER_ACCEPTANCE_MARKER',
                         comment_value=comment('Relayed answer\n' + body()))

    def test_comment_not_after_the_head_commit(self):
        for created in (COMMIT_TIME, AUTHOR_TIME, '2026-11-08T08:59:59Z'):
            with self.subTest(created=created):
                self.assert_code('MERGE_OWNER_ACCEPTANCE_STALE',
                                 comment_value=comment(created=created))
        # The later of the author and committer dates counts.
        self.assert_code('MERGE_OWNER_ACCEPTANCE_STALE',
                         comment_value=comment(created='2026-11-08T09:30:00Z'),
                         commit_value=head_commit(author=LATER))

    def test_edited_comment(self):
        self.assert_code('MERGE_OWNER_ACCEPTANCE_EDITED',
                         comment_value=comment(updated='2026-11-08T11:00:00Z'))
        # Edited is checked before the marker.
        self.assert_code('MERGE_OWNER_ACCEPTANCE_EDITED', comment_value=comment(
            body(first='plain text'), updated='2026-11-08T11:00:00Z'))

    def test_other_repo_or_malformed_reference_is_refused_without_a_read(self):
        bad = (
            f'https://github.com/example-owner/other-repo/issues/42#issuecomment-{COMMENT_ID}',
            f'http://github.com/{SLUG}/issues/42#issuecomment-{COMMENT_ID}',
            f'https://github.com/{SLUG}/issues/042#issuecomment-{COMMENT_ID}',
            f'https://github.com/{SLUG}/issues/42#issuecomment-0{COMMENT_ID}',
            f'https://github.com/{SLUG}/issues/42',
            f'https://github.com/{SLUG}/discussions/42#issuecomment-{COMMENT_ID}',
            f'https://github.com/{SLUG}/issues/42#issuecomment-{COMMENT_ID}/',
            URL + '\n',
            '',
        )
        for url in bad:
            with self.subTest(url=url):
                reads = self.assert_code('MERGE_OWNER_ACCEPTANCE_REF_INVALID', url)
                self.assertNotIn(COMMENT_PATH, reads)
                self.assertFalse(any(path.startswith('issues/comments/') for path in reads))

    def test_html_url_must_match_exactly(self):
        self.assert_code('MERGE_OWNER_ACCEPTANCE_REF_INVALID',
                         comment_value=comment(html_url=PR_URL))

    def test_unreadable_comment(self):
        missing_body = comment()
        del missing_body['body']
        values = (RuntimeError('read failed'), ['not', 'a', 'dict'], missing_body,
                  dict(comment(), updated_at=None), dict(comment(), html_url=7))
        for value in values:
            with self.subTest(value=value):
                reads = self.assert_code('MERGE_OWNER_ACCEPTANCE_UNAVAILABLE',
                                         comment_value=value)
                self.assertNotIn(COMMIT_PATH, reads)

    def test_unreadable_head_commit_or_bad_time(self):
        for value in (RuntimeError('read failed'), {'sha': HEAD},
                      head_commit(committer='yesterday'),
                      head_commit(author='2026-11-08T08:00:00+02:00'),
                      head_commit(committer='2026-13-08T09:00:00Z')):
            with self.subTest(value=value):
                self.assert_code('MERGE_OWNER_ACCEPTANCE_UNAVAILABLE', commit_value=value)
        self.assert_code('MERGE_OWNER_ACCEPTANCE_UNAVAILABLE',
                         comment_value=comment(created='2026-11-08 10:00'))


class CliTests(unittest.TestCase):
    NATIVE = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'

    def _run(self, argv):
        args = parser_for_cli().parse_args(argv + ['--pm-session', self.NATIVE])
        output = io.StringIO()
        with patch('llmtool.task_advance.advance',
                   return_value={'codes': []}) as advance, \
                patch('llmtool.pm_caller.check_caller', return_value='pm-caller') as caller, \
                patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': '', 'CODEX_THREAD_ID': ''}), \
                redirect_stdout(output):
            status = task_submission_cli.run_task_advance(args, store=object())
        return status, output.getvalue(), advance, caller

    def test_option_alone_refuses_and_runs_nothing(self):
        status, output, advance, caller = self._run(
            ['task', 'advance', '1' * 32, '--owner-acceptance', URL])
        self.assertEqual(status, 1)
        self.assertIn('"OWNER_ACCEPTANCE_REQUIRES_MERGE"', output)
        advance.assert_not_called()
        caller.assert_not_called()

    def test_option_with_merge_reaches_advance(self):
        status, _output, advance, _caller = self._run(
            ['task', 'advance', '1' * 32, '--merge', '--owner-acceptance', URL])
        self.assertEqual(status, 0)
        self.assertTrue(advance.call_args.kwargs['merge'])
        self.assertEqual(advance.call_args.kwargs['owner_acceptance'], URL)

    def test_without_option_advance_call_is_unchanged(self):
        _status, _output, advance, _caller = self._run(
            ['task', 'advance', '1' * 32, '--merge'])
        self.assertNotIn('owner_acceptance', advance.call_args.kwargs)

    def test_advance_without_merge_refuses_before_reading_the_task(self):
        store = Mock()
        result = task_advance.advance(task_id='1' * 32, store=store, owner_acceptance=URL)
        self.assertEqual(result['codes'], ['OWNER_ACCEPTANCE_REQUIRES_MERGE'])
        store.get.assert_not_called()

    def test_drive_has_no_owner_acceptance_option(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser_for_cli().parse_args(
                ['task', 'drive', '1' * 32, '--owner-acceptance', URL])


if __name__ == '__main__':
    unittest.main()
