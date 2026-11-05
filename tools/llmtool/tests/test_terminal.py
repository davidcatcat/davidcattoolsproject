import io
import unittest

from llmtool.terminal import human_output, terminal_safe_text


class TTY(io.StringIO):
    def isatty(self):
        return True


class TerminalRenderingTests(unittest.TestCase):
    def test_escapes_terminal_control_sequences_and_bidi(self):
        source = "ok\x1b[2J\x07\x1b]52;c;secret\x07\rspoof\x9b31m\u202eabc\n	測試"
        rendered = terminal_safe_text(source)
        for escaped in ("\\x1b", "\\x07", "\\x0d", "\\x9b", "\\u202e"):
            self.assertIn(escaped, rendered)
        self.assertIn("\n\t測試", rendered)

    def test_only_tty_human_rendering_is_escaped(self):
        text = "reply\x1b[31m\rreset"
        self.assertEqual(human_output(text, TTY()), "reply\\x1b[31m\\x0dreset")
        self.assertEqual(human_output(text, io.StringIO()), text)


if __name__ == "__main__":
    unittest.main()
# llmtool acceptance #1002 marker v1
# llmtool acceptance #1002 marker v2
# out-of-scope attempt: REFUSED EPERM: operation not permitted, open '/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcatto
