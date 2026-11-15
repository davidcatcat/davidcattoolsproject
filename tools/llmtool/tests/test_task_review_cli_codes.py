"""#1230 refusal-code pin: task review CLI alias, input and internal error codes."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from llmtool import task_review_cli as cli

ARGS = SimpleNamespace(expect='e', contract='c', acceptance_contract='a', events='v', json=True)


class TaskReviewCliCodeTests(unittest.TestCase):
    def run_cli(self, **read):
        out = io.StringIO()
        with mock.patch.object(cli, 'read_task_file', **read), mock.patch('sys.stdout', out):
            code = cli.run_task_review(ARGS)
        return code, json.loads(out.getvalue())

    def test_failures_name_one_code_and_never_authorize(self):
        for label, read, expected in (
                ('same file four times', dict(return_value=('{}', 7)), 'REVIEW_INPUT_ALIAS'),
                ('uncoded input error', dict(side_effect=ValueError('private detail')), 'REVIEW_INPUT_INVALID'),
                ('unexpected crash', dict(side_effect=RuntimeError('private detail')), 'REVIEW_INTERNAL_ERROR')):
            with self.subTest(label=label):
                code, result = self.run_cli(**read)
                self.assertEqual(code, 2)
                self.assertEqual((result['status'], result['code']), ('BLOCKED', expected))
                self.assertIs(result['authorized'], False)
                self.assertNotIn('private detail', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
