"""#2324: a Claude run that ends CLAUDE_NO_CHANGES keeps the Developer's final reply.

The reply is stored as developer.reported.summary (Developer-reported, unverified),
screened for credentials and bounded to the TaskStore reported-object limit.
Test data rule (Owner #763 A): no credential-shaped strings here; the screen
refusal tests inject the screen function and still check the real behaviour.
"""

import inspect
import json
import unittest
from unittest.mock import patch

from llmtool import task_store
from llmtool import task_submission_cli as cli


SPEC_REPLY = ('SPEC_MISMATCH: the stored reason field is limited to 200 characters.\n'
              'Evidence: tools/llmtool/llmtool/task_store.py:449 checks the length.\n'
              'No file was changed.')


def _cli_result(**changes):
    raw = {'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 14,
           'duration_ms': 49000, 'session_id': '00000000-0000-4000-8000-000000000000',
           'permission_denials': [], 'result': SPEC_REPLY}
    raw.update(changes)
    return raw


def _reported_size(reported):
    return len(json.dumps(reported, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':')).encode('ascii'))


def _blocked_developer():
    """The key set run_task_claude_delegate stores for a CLAUDE_NO_CHANGES run."""
    return {
        'assignment_id': 'a' * 32, 'selection_source': 'PM', 'state': 'BLOCKED',
        'provider_profile': 'claude', 'provider_type': 'claude-cli', 'model': 'claude-test',
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID', 'session_id': 'dev-' + 'b' * 32,
        'source_sha': 'c' * 40, 'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['a.py'], 'write_paths': ['a.py'], 'acceptance': ['one'],
        'context_sha256': 'd' * 64, 'egress_basis': 'basis', 'input_bytes': 1,
        'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': 'CLAUDE_NO_CHANGES',
        'execution': {'status': 'BLOCKED'}, 'created_at': 'now', 'owner_assignment_ref': None,
        'executor_task_id': 'e' * 32, 'timeout_seconds': 600, 'direct_result_callback': None,
        'no_changes_diagnosis': {'reason': 'UNKNOWN', 'write_attempted': 'UNKNOWN',
                                 'num_turns': 14},
        'claude_failure_evidence': {'subtype': 'SUCCESS', 'num_turns': 14},
    }


class ClaudeNoChangeReplyTests(unittest.TestCase):
    def test_reply_is_kept_whole_when_it_fits(self):
        self.assertEqual(cli._claude_no_change_reply(_cli_result()), {'summary': SPEC_REPLY})

    def test_reply_keeps_its_lines_and_is_stripped(self):
        reported = cli._claude_no_change_reply(_cli_result(result='\n  ' + SPEC_REPLY + '  \n'))
        self.assertEqual(reported, {'summary': SPEC_REPLY})
        self.assertIn('\n', reported['summary'])

    def test_no_reply_gives_no_report(self):
        for raw in (_cli_result(result=None), _cli_result(result=''),
                    _cli_result(result=' \n\t '), _cli_result(result=['text']),
                    {k: v for k, v in _cli_result().items() if k != 'result'}, None, 'text'):
            with self.subTest(raw=raw):
                self.assertIsNone(cli._claude_no_change_reply(raw))

    def test_long_reply_is_cut_to_the_store_limit_and_marked(self):
        text = ''.join('evidence line %d of a long answer\n' % i for i in range(2000))
        reported = cli._claude_no_change_reply(_cli_result(result=text))
        summary = reported['summary']
        self.assertTrue(summary.endswith(cli.CLAUDE_REPLY_TRUNCATED))
        self.assertTrue(text.startswith(summary[:-len(cli.CLAUDE_REPLY_TRUNCATED)]))
        self.assertLessEqual(_reported_size(reported), cli.CLAUDE_REPLY_MAX_REPORTED_BYTES)
        # The cut keeps as much as fits, not a token amount.
        self.assertGreater(_reported_size(reported), cli.CLAUDE_REPLY_MAX_REPORTED_BYTES - 64)

    def test_long_non_ascii_reply_is_bounded_in_ascii_json(self):
        reported = cli._claude_no_change_reply(_cli_result(result='é' * 5000))
        self.assertTrue(reported['summary'].endswith(cli.CLAUDE_REPLY_TRUNCATED))
        self.assertLessEqual(_reported_size(reported), cli.CLAUDE_REPLY_MAX_REPORTED_BYTES)

    def test_screen_sees_the_whole_reply_before_any_cut(self):
        text = 'start of answer\n' + 'x' * 20000
        real = cli._emitted_sensitive
        with patch.object(cli, '_emitted_sensitive', side_effect=real) as screen:
            reported = cli._claude_no_change_reply(_cli_result(result=text))
        screened = [call.args[0] for call in screen.call_args_list]
        self.assertEqual(screened[0], text)
        self.assertIn(reported['summary'], screened)

    def test_screen_refusal_of_the_reply_keeps_only_the_fixed_text(self):
        with patch.object(cli, '_emitted_sensitive', return_value=True):
            reported = cli._claude_no_change_reply(_cli_result())
        self.assertEqual(reported, {'summary': cli.CLAUDE_REPLY_WITHHELD})
        self.assertNotIn('SPEC_MISMATCH', reported['summary'])

    def test_screen_refusal_of_the_cut_reply_keeps_only_the_fixed_text(self):
        text = 'start of answer\n' + 'y' * 20000
        refuse_cut = lambda value: value.endswith(cli.CLAUDE_REPLY_TRUNCATED)
        with patch.object(cli, '_emitted_sensitive', side_effect=refuse_cut):
            reported = cli._claude_no_change_reply(_cli_result(result=text))
        self.assertEqual(reported, {'summary': cli.CLAUDE_REPLY_WITHHELD})

    def test_control_or_unencodable_text_is_withheld_not_reworded(self):
        for text in ('answer\x07 done', 'answer ​ done', 'answer \ud800 done'):
            with self.subTest(text=repr(text)):
                self.assertEqual(cli._claude_no_change_reply(_cli_result(result=text)),
                                 {'summary': cli.CLAUDE_REPLY_WITHHELD})


class ClaudeNoChangeRecordTests(unittest.TestCase):
    def test_reply_is_attached_as_reported_summary(self):
        developer = _blocked_developer()
        cli._claude_attach_no_change_reply(developer, _cli_result())
        self.assertEqual(developer['reported'], {'summary': SPEC_REPLY})

    def test_no_reply_leaves_the_record_unchanged(self):
        developer = _blocked_developer()
        before = json.loads(json.dumps(developer))
        cli._claude_attach_no_change_reply(developer, _cli_result(result=''))
        self.assertEqual(developer, before)
        self.assertNotIn('reported', developer)

    def test_withheld_reply_is_attached_and_accepted_by_the_store_check(self):
        developer = _blocked_developer()
        with patch.object(cli, '_emitted_sensitive', return_value=True):
            cli._claude_attach_no_change_reply(developer, _cli_result())
        self.assertEqual(developer['reported'], {'summary': cli.CLAUDE_REPLY_WITHHELD})

    def test_failure_or_store_refusal_never_blocks_the_record(self):
        for target, kwargs in (('_claude_no_change_reply', {'side_effect': RuntimeError}),
                               ('_valid_reported_text', {'return_value': False})):
            with self.subTest(target=target):
                developer = _blocked_developer()
                before = json.loads(json.dumps(developer))
                with patch.object(cli, target, **kwargs):
                    cli._claude_attach_no_change_reply(developer, _cli_result())
                self.assertEqual(developer, before)

    def test_record_with_reply_has_a_store_accepted_shape_and_reported_value(self):
        developer = _blocked_developer()
        cli._claude_attach_no_change_reply(developer, _cli_result(
            result=''.join('line %d\n' % i for i in range(3000))))
        keys = frozenset(developer) - task_store._DEVELOPER_OPTIONAL_FIELDS
        self.assertIn(keys, task_store._DEVELOPER_RECORD_SHAPES)
        reported = developer['reported']
        self.assertTrue(set(reported) <= task_store._DEVELOPER_REPORTED_FIELDS)
        self.assertTrue(task_store._valid_reported_text(reported['summary'], 8 * 1024))
        self.assertLessEqual(_reported_size(reported), 8 * 1024)

    def test_store_limit_matches_the_reply_bound(self):
        # task_store bounds the reported object at 8 KiB of ASCII JSON.
        self.assertEqual(cli.CLAUDE_REPLY_MAX_REPORTED_BYTES, 8 * 1024)

    def test_delegate_attaches_the_reply_only_on_the_no_change_branch(self):
        source = inspect.getsource(cli.run_task_claude_delegate)
        branch = source.index("error_code == 'CLAUDE_NO_CHANGES'")
        attach = source.index('_claude_attach_no_change_reply(developer, parsed_cli_result)')
        self.assertLess(branch, attach)
        self.assertEqual(source.count('_claude_attach_no_change_reply('), 1)
        self.assertLess(attach, source.index("store.update(task['task_id'], developer=developer)\n"
                                             "        _print(_task_view"))


if __name__ == '__main__':
    unittest.main()
