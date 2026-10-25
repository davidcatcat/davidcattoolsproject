import unittest

from llmtool.developer_output_normalize import normalize_developer_output
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
        self.assertIsNone(_developer_output_rejection(missing_header, [self.PATH]))
        self.assertEqual(
            _developer_output_rejection(missing_header, [self.PATH], normalize=False),
            'DEVELOPER_OUTPUT_INVALID_ENVELOPE')

    def test_fence_can_have_edge_prose_but_not_prose_inside(self):
        diff = self.HEADER + '@@ -1 +1 @@\n-old\n+new\n'
        normalized, tokens = normalize_developer_output(
            'Here is the change:\n```diff\n' + diff + '```\nDone.', [self.PATH])
        self.assertIn('OUTER_FENCE_STRIPPED', tokens)
        self.assertIsNone(_developer_output_rejection(normalized, [self.PATH]))
        inside, _ = normalize_developer_output('```diff\nintro\n' + diff + '```',
                                               [self.PATH])
        self.assertEqual(inside, '```diff\nintro\n' + diff + '```')

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


if __name__ == '__main__':
    unittest.main()
