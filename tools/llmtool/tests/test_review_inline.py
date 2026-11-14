import unittest

from llmtool.review_inline import MAX_INLINE_FINDINGS, MAX_INLINE_TEXT, inline_findings

CHANGED = ['tools/llmtool/llmtool/a.py', 'docs/b.md']


class InlineFindingsTests(unittest.TestCase):
    def test_selects_findings_on_changed_files_in_order(self):
        body = ('Verdict: FAIL | Version: v1 | HEAD: ' + '0' * 40 + '\n'
                'Finding: F1 [High] tools/llmtool/llmtool/a.py:12 off by one\n'
                '  Finding: F2 [Low] docs/b.md:3-5 wording\n')
        self.assertEqual(inline_findings(body, CHANGED), [
            {'path': 'tools/llmtool/llmtool/a.py', 'line': 12,
             'body': 'Advisory copy of formal verdict finding F1 [High]: off by one'},
            {'path': 'docs/b.md', 'line': 3,
             'body': 'Advisory copy of formal verdict finding F2 [Low]: wording'}])

    def test_skips_unsafe_unchanged_malformed_and_repeated(self):
        lines = ['Finding: F1 [High] other.py:1 not changed',
                 'Finding: F2 [High] /tools/llmtool/llmtool/a.py:1 absolute',
                 'Finding: F3 [High] docs/../docs/b.md:1 parent',
                 'Finding: F4 [Major] docs/b.md:1 unknown severity',
                 'Finding: F5 [Low] docs/b.md:0 line zero',
                 'Finding: F6 [Low] docs/b.md no line',
                 'Finding: F7 [Low] docs/b.md:2 kept',
                 'Finding: F7 [Low] docs/b.md:9 repeated id',
                 'Note: F8 [Low] docs/b.md:4 not a finding line']
        self.assertEqual([item['line'] for item in inline_findings('\n'.join(lines), CHANGED + ['other'])], [2])

    def test_bounds_count_and_text(self):
        body = '\n'.join('Finding: F%d [Advisory] docs/b.md:%d %s' % (n, n, 'x' * 2000)
                         for n in range(1, MAX_INLINE_FINDINGS + 5))
        found = inline_findings(body, CHANGED)
        self.assertEqual(len(found), MAX_INLINE_FINDINGS)
        self.assertTrue(all(len(item['body']) == MAX_INLINE_TEXT for item in found))

    def test_non_text_input_gives_nothing(self):
        self.assertEqual(inline_findings(None, CHANGED), [])
        self.assertEqual(inline_findings('Finding: F1 [High] docs/b.md:1 x', [None, 3]), [])


from llmtool.review_inline import post_inline_copies  # noqa: E402

HEAD = 'a' * 40
VERDICT = ('Finding: F1 [High] tools/llmtool/llmtool/a.py:12 off by one\n'
           'Finding: F2 [Low] docs/b.md:3 wording\n')


class PostInlineCopiesTests(unittest.TestCase):
    def run_post(self, existing=(), poster=None, body=VERDICT, head=HEAD):
        posted = []
        def post(pr, payload):
            posted.append((pr, payload))
        result = post_inline_copies(
            7, head, body, file_lister=lambda pr: list(CHANGED),
            comment_lister=lambda pr: list(existing), poster=poster or post)
        return result, posted

    def test_posts_each_finding_once_on_the_head_line(self):
        result, posted = self.run_post()
        self.assertEqual(result, {'posted': 2, 'skipped': 0, 'failed': 0})
        self.assertEqual(posted[0], (7, {
            'body': '<!-- llmtool-inline-copy head=%s id=F1 -->\n'
                    'Advisory copy of formal verdict finding F1 [High]: off by one' % HEAD,
            'commit_id': HEAD, 'path': 'tools/llmtool/llmtool/a.py', 'line': 12, 'side': 'RIGHT'}))
        self.assertEqual(posted[1][1]['path'], 'docs/b.md')

    def test_existing_copy_on_same_head_is_skipped_other_head_is_not(self):
        existing = [{'body': '<!-- llmtool-inline-copy head=%s id=F1 -->\nold' % HEAD},
                    {'body': '<!-- llmtool-inline-copy head=%s id=F2 -->\nold' % ('b' * 40)}, 'junk']
        result, posted = self.run_post(existing=existing)
        self.assertEqual(result, {'posted': 1, 'skipped': 1, 'failed': 0})
        self.assertEqual([item[1]['path'] for item in posted], ['docs/b.md'])

    def test_failures_are_counted_never_raised(self):
        def broken(pr, payload):
            raise OSError('synthetic post failure')
        self.assertEqual(self.run_post(poster=broken)[0], {'posted': 0, 'skipped': 0, 'failed': 2})
        def lister(pr):
            raise OSError('synthetic list failure')
        result = post_inline_copies(7, HEAD, VERDICT, file_lister=lister,
                                    comment_lister=lambda pr: [], poster=broken)
        self.assertEqual(result, {'posted': 0, 'skipped': 0, 'failed': 1})

    def test_bad_pr_or_head_posts_nothing(self):
        for pr, head in ((0, HEAD), ('7', HEAD), (7, 'A' * 40), (7, 'a' * 39)):
            posted = []
            result = post_inline_copies(pr, head, VERDICT, file_lister=lambda p: list(CHANGED),
                                        comment_lister=lambda p: [],
                                        poster=lambda p, payload: posted.append(payload))
            self.assertEqual((result['failed'], posted), (1, []))

    def test_rerun_with_its_own_copies_listed_posts_nothing(self):
        first, posted = self.run_post()
        self.assertEqual(first['posted'], 2)
        existing = [{'body': payload['body']} for _pr, payload in posted]
        result, again = self.run_post(existing=existing)
        self.assertEqual(result, {'posted': 0, 'skipped': 2, 'failed': 0})
        self.assertEqual(again, [])

    def test_marker_sits_on_its_own_first_line(self):
        _result, posted = self.run_post()
        for _pr, payload in posted:
            first_line, rest = payload['body'].split('\n', 1)
            self.assertRegex(first_line, r'\A<!-- llmtool-inline-copy head=%s id=F[12] -->\Z' % HEAD)
            self.assertTrue(rest.startswith('Advisory copy of formal verdict finding '))

    def test_comment_lister_failure_is_counted_and_posts_nothing(self):
        posted = []
        def lister(pr):
            raise OSError('synthetic list failure')
        result = post_inline_copies(7, HEAD, VERDICT, file_lister=lambda pr: list(CHANGED),
                                    comment_lister=lister,
                                    poster=lambda pr, payload: posted.append(payload))
        self.assertEqual(result, {'posted': 0, 'skipped': 0, 'failed': 1})
        self.assertEqual(posted, [])

    def test_one_poster_failure_does_not_stop_the_next_copy(self):
        calls = []
        def flaky(pr, payload):
            calls.append(payload['path'])
            if len(calls) == 1:
                raise RuntimeError('synthetic post failure')
        result, _posted = self.run_post(poster=flaky)
        self.assertEqual(result, {'posted': 1, 'skipped': 0, 'failed': 1})
        self.assertEqual(calls, ['tools/llmtool/llmtool/a.py', 'docs/b.md'])

    def test_more_bad_inputs_post_nothing_and_counts_are_plain_ints(self):
        for pr, head in ((-1, HEAD), (True, HEAD), (7.0, HEAD), (None, HEAD),
                         (7, None), (7, 'g' * 40), (7, 'a' * 41), (7, ' ' + 'a' * 40)):
            calls = []
            def track(name):
                def call(*args):
                    calls.append(name)
                    return []
                return call
            result = post_inline_copies(pr, head, VERDICT, file_lister=track('files'),
                                        comment_lister=track('comments'), poster=track('post'))
            self.assertEqual(result, {'posted': 0, 'skipped': 0, 'failed': 1})
            self.assertEqual(calls, [])
            self.assertEqual({type(value) for value in result.values()}, {int})

    def test_counts_are_plain_ints_after_posting(self):
        result, _posted = self.run_post()
        self.assertEqual(sorted(result), ['failed', 'posted', 'skipped'])
        self.assertEqual({type(value) for value in result.values()}, {int})

    def test_module_imports_no_network_or_subprocess_code(self):
        import ast
        import llmtool.review_inline as module
        with open(module.__file__, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        self.assertEqual(imported, {'re'})
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(names & {'__import__', 'open', 'exec', 'eval'})


if __name__ == '__main__':
    unittest.main()
