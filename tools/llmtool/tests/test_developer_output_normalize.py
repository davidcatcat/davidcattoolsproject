import unittest

from llmtool.developer_output_normalize import normalize_developer_output
from llmtool.developer_output_normalize import reanchor_developer_output
from llmtool.task_submission_cli import (_developer_normalized_output,
                                         _developer_output_rejection)


class DeveloperOutputNormalizeTests(unittest.TestCase):
    PATH = 'tools/llmtool/example.py'
    HEADER = (f'diff --git a/{PATH} b/{PATH}\n'
              f'--- a/{PATH}\n+++ b/{PATH}\n')

    def test_salvages_only_the_four_documented_shapes(self):
        cases = (
            ('```diff\n' + self.HEADER + '@@ -1,1 +1,1 @@\n-old\n+new\n```',
             {'OUTER_FENCE_STRIPPED'}),
            (f'--- a/{self.PATH}\n+++ b/{self.PATH}\n@@ -1 +1 @@\n-old\n+new\n',
             {'DIFF_HEADER_ADDED'}),
            (self.HEADER + '@@ -1,3 +1,3 @@\n keep\n\n-old\n+new\n',
             {'BARE_BLANK_CONTEXT_SPACED'}),
            (self.HEADER + '@@ -1,4 +1,4 @@\n keep\n-old\n+new\n keep\n',
             {'HUNK_COUNTS_RECOUNTED'}),
        )
        for raw, expected_tokens in cases:
            with self.subTest(expected_tokens=expected_tokens):
                normalized, tokens = normalize_developer_output(raw, [self.PATH])
                self.assertTrue(expected_tokens.issubset(set(tokens)))
                self.assertIsNone(_developer_output_rejection(normalized, [self.PATH]))

    def test_strictly_valid_omitted_hunk_counts_round_trip_byte_for_byte(self):
        valid = (self.HEADER + '@@ -1 +1 @@\n-old\n+new\n')
        artifact, tokens, rejection = _developer_normalized_output(valid, [self.PATH])
        self.assertIsNone(rejection)
        self.assertEqual(artifact, valid)
        self.assertEqual(tokens, ())

    def test_opencode_strict_mode_does_not_normalize(self):
        missing_header = (f'--- a/{self.PATH}\n+++ b/{self.PATH}\n'
                          '@@ -1,9 +1,9 @@\n-old\n+new\n')
        self.assertEqual(
            _developer_output_rejection(missing_header, [self.PATH]),
            'DEVELOPER_OUTPUT_INVALID_ENVELOPE')
        self.assertIsNone(_developer_output_rejection(
            missing_header, [self.PATH], normalize=True))

    def test_fence_refuses_edge_prose_and_prose_inside(self):
        diff = self.HEADER + '@@ -1 +1 @@\n-old\n+new\n'
        prose_edges = 'Here is the change:\n```diff\n' + diff + '```\nDone.'
        unchanged, tokens = normalize_developer_output(prose_edges, [self.PATH])
        self.assertEqual(unchanged, prose_edges)
        self.assertEqual(tokens, ())
        self.assertEqual(_developer_output_rejection(prose_edges, [self.PATH]),
                         'DEVELOPER_OUTPUT_INVALID_ENVELOPE')
        normalized, tokens = normalize_developer_output(
            '\n```diff\n\n' + diff + '\n\n```\n', [self.PATH])
        self.assertIn('OUTER_FENCE_STRIPPED', tokens)
        self.assertIsNone(_developer_output_rejection(normalized, [self.PATH]))
        inside, _ = normalize_developer_output('```diff\nintro\n' + diff + '```',
                                               [self.PATH])
        self.assertEqual(inside, '```diff\nintro\n' + diff + '```')

    def test_header_recovery_refuses_a_second_file_header_pair(self):
        headerless_two_file = (
            '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n'
            '--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-p\n+q\n')
        normalized, tokens = normalize_developer_output(headerless_two_file, ['a.py'])
        self.assertEqual(normalized, headerless_two_file)
        self.assertEqual(tokens, ())
        self.assertEqual(_developer_output_rejection(headerless_two_file, ['a.py']),
                         'DEVELOPER_OUTPUT_INVALID_ENVELOPE')

        section_then_headerless_pair = (
            'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
            '@@ -1 +1 @@\n-x\n+y\n'
            '--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-p\n+q\n')
        normalized, tokens = normalize_developer_output(section_then_headerless_pair, ['a.py'])
        self.assertEqual(normalized, section_then_headerless_pair)
        self.assertEqual(tokens, ())
        self.assertEqual(_developer_output_rejection(section_then_headerless_pair, ['a.py']),
                         'DEVELOPER_OUTPUT_INVALID_DIFF')

    def test_nonstandard_line_separators_and_carriage_returns_are_not_normalized(self):
        separator_in_added_line = (self.HEADER + '@@ -1 +1 @@\n-old\n+new\u2028extra\n')
        crlf = (self.HEADER + '@@ -1 +1 @@\n-old\n+new\n').replace('\n', '\r\n')
        for raw in (separator_in_added_line, crlf):
            with self.subTest(raw=repr(raw[-30:])):
                normalized, tokens = normalize_developer_output(raw, [self.PATH])
                self.assertEqual(normalized, raw)
                self.assertEqual(tokens, ())

    def test_recounts_hunks_in_each_in_scope_file_section(self):
        second = 'tools/llmtool/second.py'
        raw = (self.HEADER + '@@ -1,8 +1,8 @@\n-old\n+new\n' +
               f'diff --git a/{second} b/{second}\n--- a/{second}\n+++ b/{second}\n'
               '@@ -1,3 +1,3 @@\n-old\n+new\n')
        normalized, tokens = normalize_developer_output(raw, [self.PATH, second])
        self.assertEqual(tokens, ('HUNK_COUNTS_RECOUNTED',))
        self.assertEqual(normalized.count('@@ -1,1 +1,1 @@'), 2)
        self.assertIsNone(_developer_output_rejection(normalized, [self.PATH, second]))

    def test_unsafe_shapes_are_returned_unchanged(self):
        diff = self.HEADER + '@@ -1 +1 @@\n-old\n+new\n'
        cases = (
            '```diff\n' + diff + '```\n```diff\n' + diff + '```',
            'prose\n' + diff,
            diff.replace('--- a/', 'old mode 100644\nnew mode 100755\n--- a/', 1),
            diff.replace(self.PATH, 'outside.py'),
            f'diff --git a/{self.PATH} b/renamed.py\nrename from {self.PATH}\nrename to renamed.py\n',
            f'diff --git a/{self.PATH} b/{self.PATH}\nGIT binary patch\nliteral 1\n',
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                normalized, tokens = normalize_developer_output(raw, [self.PATH])
                self.assertEqual(normalized, raw)
                self.assertEqual(tokens, ())

    def test_source_anchor_repairs_pm_observed_context_shape(self):
        source = '\n'.join(f'line {number}' for number in range(1, 45)) + '\n'
        raw = (self.HEADER + '@@ -33,4 +33,8 @@\n'
               ' line 33\n line 34\n line 35\n'
               '+added one\n+added two\n+added three\n+added four\n')
        normalized, _ = normalize_developer_output(raw, [self.PATH])
        anchored, tokens = reanchor_developer_output(
            normalized, [self.PATH], {self.PATH: source})
        self.assertEqual(tokens, ('HUNK_REANCHORED',))
        self.assertIn('@@ -33,6 +33,10 @@', anchored)
        self.assertIn('+added four', anchored)
        self.assertIsNone(_developer_output_rejection(anchored, [self.PATH]))

    def test_source_anchor_accepts_already_anchored_patch(self):
        raw = (self.HEADER + '@@ -1 +1 @@\n-old\n+new\n')
        anchored, tokens, rejection = _developer_normalized_output(
            raw, [self.PATH], source_files={self.PATH: 'old\n'})
        self.assertEqual(anchored, raw)
        self.assertEqual(tokens, ('HUNK_COUNTS_RECOUNTED', 'HUNK_REANCHORED'))
        self.assertIsNone(rejection)

    def test_source_anchor_rejects_invented_or_ambiguous_old_side(self):
        invented = (self.HEADER + '@@ -1 +1 @@\n-invented\n+replacement\n')
        ambiguous = (self.HEADER + '@@ -1 +1 @@\n-same\n+replacement\n')
        for raw, source in ((invented, 'real line\n'),
                            (ambiguous, 'same\nsame\n')):
            with self.subTest(source=source):
                anchored, tokens = reanchor_developer_output(
                    raw, [self.PATH], {self.PATH: source})
                self.assertEqual(anchored, raw)
                self.assertEqual(tokens, ())
                self.assertEqual(_developer_normalized_output(
                    raw, [self.PATH], source_files={self.PATH: source})[2],
                    'DEVELOPER_OUTPUT_INVALID_DIFF')


if __name__ == '__main__':
    unittest.main()
