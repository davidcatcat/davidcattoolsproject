"""#2199: the merge gate refuses while a FAIL verdict at the exact head is unresolved."""
import unittest

from llmtool import task_advance


HEAD = 'a' * 40
OLD_HEAD = 'b' * 40
MAIN_SHA = 'e' * 40


def verdict(word, version, head=HEAD):
    return {'body': f'<!-- activity: type=review_verdict -->\n'
                    f'Verdict: {word} | Version: v{version} | HEAD: {head}\n'}


class Store:
    def __init__(self):
        self.attempt = None

    def compare_and_update(self, _task_id, *, expected, merge_attempt):
        if self.attempt != expected.get('merge_attempt'):
            raise RuntimeError('claim race')
        self.attempt = dict(merge_attempt)


class MergeUnresolvedHeadFailTests(unittest.TestCase):
    def merge(self, comments):
        """Run one merge with a bound formal PASS v1; comments feeds the comment read."""
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}
        pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
              'mergeable': True, 'labels': [{'name': 'mac-ci'}], 'body': ''}
        reads, calls = [], []

        def github_get(path):
            reads.append(path)
            if path == 'branches/main':
                return {'commit': {'sha': MAIN_SHA}}
            if path == f'compare/{MAIN_SHA}...{HEAD}':
                return {'status': 'ahead'}
            if path.startswith('commits/'):
                return {'total_count': 2, 'check_runs': [
                    {'head_sha': HEAD, 'name': name, 'status': 'completed',
                     'conclusion': 'success'}
                    for name in ('check (Linux 3.14)', 'check (macOS 3.13)')]}
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            if path.startswith('issues/17/comments?per_page=100'):
                return comments(path)
            if path.startswith('pulls/'):
                return pr
            return []

        def gh_runner(_root, args):
            calls.append(args)
            return type('R', (), {'returncode': 0})()

        result = task_advance._merge_once(
            store=Store(), record=record, number=17, head=HEAD, pr=pr,
            events=[event], github_get=github_get, gh_runner=gh_runner, root='/repo')
        return result, reads, calls

    def assert_refused(self, outcome, code):
        result, _reads, calls = outcome
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], [code])
        self.assertEqual(calls, [])

    def assert_merged(self, outcome):
        result, reads, calls = outcome
        self.assertEqual(result['step'], 'MERGED')
        self.assertIn('issues/17/comments?per_page=100', reads)
        self.assertEqual(sum(call[1] == 'merge' for call in calls), 1)

    def test_fail_v91_then_pass_v1_at_head_refuses(self):
        # PRs #2194 #2197: a Codex security FAIL v91, then a formal PASS v1.
        comments = [verdict('FAIL', 91), verdict('PASS', 1)]
        self.assert_refused(self.merge(lambda _path: comments),
                            'MERGE_UNRESOLVED_REVIEW_FAIL')

    def test_fail_v91_then_pass_v92_at_head_merges(self):
        comments = [verdict('FAIL', 91), verdict('PASS', 92)]
        self.assert_merged(self.merge(lambda _path: comments))

    def test_pass_of_the_same_version_does_not_resolve_a_fail(self):
        comments = [verdict('FAIL', 91), verdict('PASS', 91)]
        self.assert_refused(self.merge(lambda _path: comments),
                            'MERGE_UNRESOLVED_REVIEW_FAIL')

    def test_highest_fail_needs_a_higher_pass(self):
        comments = [verdict('FAIL', 91), verdict('PASS', 92), verdict('FAIL', 93)]
        self.assert_refused(self.merge(lambda _path: comments),
                            'MERGE_UNRESOLVED_REVIEW_FAIL')

    def test_pass_for_another_head_does_not_resolve_a_fail(self):
        comments = [verdict('FAIL', 91), verdict('PASS', 92, head=OLD_HEAD)]
        self.assert_refused(self.merge(lambda _path: comments),
                            'MERGE_UNRESOLVED_REVIEW_FAIL')

    def test_fail_at_an_older_head_is_ignored(self):
        comments = [verdict('FAIL', 91, head=OLD_HEAD), verdict('PASS', 1)]
        self.assert_merged(self.merge(lambda _path: comments))

    def test_fail_naming_a_longer_hex_run_is_ignored(self):
        comments = [{'body': f'Verdict: FAIL | Version: v91 | HEAD: {HEAD}0\n'}]
        self.assert_merged(self.merge(lambda _path: comments))

    def test_fail_line_with_markup_still_refuses(self):
        comments = [{'body': f'> **Verdict: FAIL | Version: v91 | HEAD: {HEAD}**\r\n'},
                    verdict('PASS', 1)]
        self.assert_refused(self.merge(lambda _path: comments),
                            'MERGE_UNRESOLVED_REVIEW_FAIL')

    def test_quoted_pass_line_does_not_resolve_a_fail(self):
        comments = [verdict('FAIL', 91),
                    {'body': f'> Verdict: PASS | Version: v92 | HEAD: {HEAD}\n'}]
        self.assert_refused(self.merge(lambda _path: comments),
                            'MERGE_UNRESOLVED_REVIEW_FAIL')

    def test_no_verdict_comments_merges(self):
        self.assert_merged(self.merge(lambda _path: [{'body': 'ordinary note'},
                                                     {'body': None}]))

    def test_unreadable_comments_refuse(self):
        def raises(_path):
            raise RuntimeError('read failed')

        for label, reader in (
                ('read error', raises),
                ('not a list', lambda _path: {'message': 'Not Found'}),
                ('oversized page', lambda _path: [{'body': ''}] * 101),
                ('non-dict comment', lambda _path: ['text']),
                ('non-string body', lambda _path: [{'body': 7}])):
            with self.subTest(label):
                self.assert_refused(self.merge(reader),
                                    'MERGE_REVIEW_COMMENTS_UNAVAILABLE')

    def test_full_first_page_reads_the_next_page(self):
        pages = {1: [{'body': 'ordinary note'}] * 100,
                 2: [verdict('FAIL', 91), verdict('PASS', 1)]}

        def paged(path):
            page = int(path.rsplit('&page=', 1)[1]) if '&page=' in path else 1
            return pages[page]

        outcome = self.merge(paged)
        self.assert_refused(outcome, 'MERGE_UNRESOLVED_REVIEW_FAIL')
        self.assertIn('issues/17/comments?per_page=100&page=2', outcome[1])

    def test_failed_page_after_a_full_page_refuses(self):
        def paged(path):
            if '&page=' in path:
                raise RuntimeError('read failed')
            return [{'body': 'ordinary note'}] * 100

        self.assert_refused(self.merge(paged), 'MERGE_REVIEW_COMMENTS_UNAVAILABLE')

    def test_every_page_full_is_an_incomplete_read(self):
        self.assert_refused(self.merge(lambda _path: [{'body': ''}] * 100),
                            'MERGE_REVIEW_COMMENTS_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
