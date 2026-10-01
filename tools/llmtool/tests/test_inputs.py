import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from llmtool.errors import LLMToolError
from llmtool.inputs import read_input
from llmtool.cli import main


class InputTests(unittest.TestCase):
    def test_explicit_text_never_inspects_stdin(self):
        stdin = Mock()
        self.assertEqual(read_input("explicit", None, stdin), "explicit")
        stdin.assert_not_called()

    def test_explicit_file_never_inspects_stdin(self):
        stdin = Mock()
        with open(__file__, encoding="utf-8") as source:
            expected = source.read()
        self.assertEqual(read_input(None, __file__, stdin), expected)
        stdin.assert_not_called()

    def test_stdin_only_path_reads_non_tty_and_whitespace_is_empty(self):
        stdin = Mock()
        stdin.isatty.return_value = False
        stdin.buffer.read.return_value = b" supplied \n"
        self.assertEqual(read_input(None, None, stdin), " supplied \n")
        stdin.buffer.read.assert_called_once()
        stdin.buffer.read.return_value = b" \n\t"
        with self.assertRaisesRegex(LLMToolError, "input is empty"):
            read_input(None, None, stdin)

    def test_closed_stdin_errors_are_sanitized(self):
        class Closed:
            def isatty(self):
                raise ValueError("private details")
        with self.assertRaisesRegex(LLMToolError, "cannot read input") as caught:
            read_input(None, None, Closed())
        self.assertNotIn("private", str(caught.exception))

    def test_closed_stdin_cli_contract_has_no_traceback_or_stdout(self):
        class Closed:
            def isatty(self):
                raise RuntimeError("private closed-stream details")
        out, err = io.StringIO(), io.StringIO()
        with patch("llmtool.cli.resolve_config", return_value={}), \
                patch("sys.stdin", Closed()), redirect_stdout(out), redirect_stderr(err):
            status = main(["ask"])
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("llmtool: cannot read input", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertNotIn("private", err.getvalue())


if __name__ == "__main__":
    unittest.main()
