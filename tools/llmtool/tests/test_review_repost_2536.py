"""#2536 part 2: audited repost of a Claude FAIL that was relayed without its findings."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from llmtool import lineage_counts, review_result, review_transcript, task_submission_cli
from test_review_transcript import (BINDING, HEAD, REVIEWER, SESSION, TASK, fail_verdict,
                                    verdict)
import test_review_result as rr

CLAIM = {'native_session_id': SESSION, 'reviewer_task': REVIEWER, 'head_sha': HEAD,
         'version': 1}
FULL = fail_verdict('', 'Finding: F1 [High] a.py:1 synthetic.', '',
                    'Finding: F2 [Medium] a.py:2 synthetic.', '', 'Disposition: FIX.')
TRUNCATED = '\n'.join(FULL.split('\n')[:3])


def _home(test, *texts):
    home = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, home)
    folder = os.path.join(home, 'projects', '-tasks-' + REVIEWER)
    os.makedirs(folder)
    with open(os.path.join(folder, SESSION + '.jsonl'), 'w') as handle:
        for text in texts:
            handle.write(json.dumps({'type': 'assistant', 'message': {
                'content': [{'type': 'text', 'text': text}]}}) + '\n')
    return home


def _comment(body, comment_id=7):
    stamp = '2026-11-22T00:00:00Z'
    return {'id': comment_id, 'body': body, 'created_at': stamp, 'updated_at': stamp}


class TruncatedFailRepostTest(unittest.TestCase):
    def repost(self, transcript, comments):
        return review_transcript.truncated_fail_repost(
            CLAIM, TASK, comments, config_dir=_home(self, transcript))

    def test_exact_truncated_header_is_replaced_by_the_transcript_verdict(self):
        self.assertEqual(self.repost('Done.\n\n' + FULL, [_comment(TRUNCATED + '\n')]),
                         (None, FULL, 7))

    def test_posted_text_that_differs_is_refused(self):
        edited = TRUNCATED.replace('FAIL |', 'FAIL  |')
        self.assertEqual(self.repost(FULL, [_comment(TRUNCATED + '\nExtra.')])[0],
                         'REPOST_NOT_TRUNCATED_PREFIX')
        self.assertEqual(self.repost(FULL, [_comment(edited)])[0],
                         'REPOST_POSTED_VERDICT_NOT_SINGLE')
        self.assertEqual(self.repost(FULL, [_comment(FULL)])[0],
                         'REPOST_NOT_TRUNCATED_PREFIX')

    def test_two_verdict_comments_for_the_version_are_refused(self):
        self.assertEqual(self.repost(FULL, [_comment(TRUNCATED), _comment(FULL, 8)])[0],
                         'REPOST_POSTED_VERDICT_NOT_SINGLE')
        self.assertEqual(self.repost(FULL, [])[0], 'REPOST_POSTED_VERDICT_NOT_SINGLE')

    def test_pass_is_always_refused(self):
        self.assertEqual(self.repost(verdict() + '\n\nNotes.', [_comment(verdict())])[0],
                         'REPOST_ONLY_FAIL')

    def test_binding_mismatch_is_refused(self):
        other = FULL.replace(BINDING, BINDING.replace('1' * 32, '9' * 32))
        self.assertEqual(self.repost(other, [_comment(TRUNCATED)])[0],
                         'REVIEWER_VERDICT_BINDING_MISMATCH')

    def test_non_claude_claim_is_refused(self):
        self.assertEqual(review_transcript.truncated_fail_repost(
            dict(CLAIM, native_session_id=None), TASK, [], config_dir='/nonexistent')[0],
            'REPOST_NOT_CLAUDE_REVIEWER')


class RepostCliTest(unittest.TestCase):
    def run_cli(self, comments):
        home = _home(self, FULL)
        record = dict(TASK, review_dispatch_claim=[dict(CLAIM, executor_task='executor-1',
                                                        pr=17, state='DISPATCHED')])
        store = SimpleNamespace(get=lambda _task_id: record)
        posted = []

        def gh(_root, args):
            posted.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        args = SimpleNamespace(task_id=TASK['task_id'], executor_task='executor-1', pr=17,
                               version=1, repost_truncated_fail=True)
        role = {'account': None, 'config_dir': home}
        with mock.patch.object(review_transcript.role_config, 'resolve_role',
                               return_value=role), \
                mock.patch.object(task_submission_cli, '_print') as printed:
            code = task_submission_cli.run_task_review_result(
                args, store=store, github_get=lambda _url: comments, gh_runner=gh)
        return code, posted, printed.call_args[0][0]

    def test_cli_posts_verdict_unedited_then_an_audit_note(self):
        code, posted, out = self.run_cli([_comment(TRUNCATED, 11)])
        self.assertEqual(code, 0)
        self.assertEqual(posted[0], ['pr', 'comment', '17', '--body', FULL])
        self.assertIn('comment 11', posted[1][4])
        self.assertEqual((out['state'], out['replaced_comment_id']), ('REPOSTED', 11))

    def test_cli_refusal_posts_nothing(self):
        code, posted, out = self.run_cli([_comment(FULL, 11)])
        self.assertEqual((code, posted, out['state'], out['code']),
                         (1, [], 'REFUSED', 'REPOST_NOT_TRUNCATED_PREFIX'))


class NextAdvanceRecordsFindingsTest(unittest.TestCase):
    """After the repost, the next record uses the newest comment: a FAIL with findings."""

    def test_newer_full_fail_is_recorded_and_repair_counts_do_not_grow(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = rr.FamilyStore(rr._store(temp.name), 'opencode')
        digest = store.get(rr.TASK)['task_binding']['contract_digest']
        header = ('<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n'
                  f'Verdict: FAIL | Version: v1 | HEAD: {rr.HEAD}\n'
                  f'LLMTOOL-REVIEW-BINDING v1 task={rr.TASK} run={rr.RUN} '
                  f'contract={digest} head={rr.HEAD}')
        full = header + ('\n\nFinding: F1 [High] a.py:1 synthetic.\n'
                         'Finding: F2 [Medium] a.py:2 synthetic.')

        def record(comments):
            return review_result.record_review_result(
                store=store, task_id=rr.TASK, executor_task=rr.EXECUTOR, pr_number=rr.PR,
                github_get=lambda _path: comments)
        before = lineage_counts.lineage_counts(store.store, store.get(rr.TASK),
                                               base_family='pr-42')
        first = record([_comment(header, 7)])
        second = record([_comment(header, 7), _comment(full, 8)])
        after = lineage_counts.lineage_counts(store.store, store.get(rr.TASK),
                                              base_family='pr-42')
        self.assertEqual((first['state'], first['comment_id']), ('REVIEW_FAIL', 7))
        self.assertEqual((second['state'], second['comment_id'], second['created']),
                         ('REVIEW_FAIL', 8, True))
        events = store.get(rr.TASK)['completion_events']
        self.assertEqual(len(events), 2)
        self.assertEqual(review_result.blocking_findings(full), ['F1', 'F2'])
        self.assertEqual(before['review_repairs'], after['review_repairs'])


if __name__ == '__main__':
    unittest.main()
