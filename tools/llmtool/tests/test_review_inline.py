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


if __name__ == '__main__':
    unittest.main()
