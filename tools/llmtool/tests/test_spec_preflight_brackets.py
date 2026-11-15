"""#2124: spec_preflight refuses gw-agent spec lines that leave a bracket open."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
SPEC = importlib.util.spec_from_file_location("spec_preflight_brackets", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)

BASE = dict(request="Add a test", summary="Add a test", goal="Create one file")
BASE.update(acceptance=["The file exists"], read=[], write=["new_file.py"], provider="gw-agent")
OPENERS = (chr(40), chr(91), chr(123))
FAIL = "SPEC_PREFLIGHT_FAIL:"


def quoted(*lines):
    return "Create new_file.py: " + " / ".join(json.dumps(line) for line in lines)


class OpenBracketGuardTests(unittest.TestCase):
    def run_main(self, spec):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = spec_preflight.main(["--repo", tmp, str(path)])
        return code, out.getvalue().strip()

    def test_balanced_lines_pass(self):
        item = quoted("x = [1, 2]", "y = f(x)", "z = {'a': 1}")
        self.assertEqual(self.run_main(dict(BASE, acceptance=[item])), (0, "SPEC_PREFLIGHT_OK"))

    def test_open_bracket_in_quoted_acceptance_line_refuses(self):
        for opener in OPENERS:
            with self.subTest(opener=opener):
                line = "cases = " + opener + "1,"
                code, out = self.run_main(dict(BASE, acceptance=["ok", quoted(line, "2")]))
                self.assertEqual((code, out), (1, FAIL + "acceptance[1]_open_bracket_line_end"))
                self.assertNotIn(line, out)

    def test_open_bracket_in_edit_new_text_refuses(self):
        new = "y = foo" + chr(40) + "\n    1)\n"
        first = dict(path="new_file.py", old="a", new="b\n")
        edits = [first, dict(path="new_file.py", old="c", new=new)]
        code, out = self.run_main(dict(BASE, edits=edits))
        self.assertEqual((code, out), (1, FAIL + "edits[1]_open_bracket_line_end"))
        self.assertNotIn("foo", out)

    def test_brackets_in_strings_and_comments_pass(self):
        for opener in OPENERS:
            with self.subTest(opener=opener):
                lines = ('s = "' + opener + '"', "t = '" + opener + "'", "y = 1  # " + opener)
                spec = dict(BASE, acceptance=[quoted(*lines)])
                self.assertEqual(spec_preflight.check_open_bracket_ends(spec), [])

    def test_close_then_open_on_one_line_refuses(self):
        self.assertTrue(spec_preflight._ends_inside_open_bracket(chr(41) + ", g" + chr(40)))
        self.assertFalse(spec_preflight._ends_inside_open_bracket("g" + chr(40) + chr(41)))
        issue_ref = "see " + chr(40) + "#2124" + chr(41)
        self.assertFalse(spec_preflight._ends_inside_open_bracket(issue_ref))
        self.assertTrue(spec_preflight._ends_inside_open_bracket("f" + chr(40) + "  # note"))

    def test_other_routes_are_not_checked(self):
        spec = dict(BASE, provider="claude", acceptance=[quoted("x = " + chr(91))])
        self.assertEqual(spec_preflight.check_open_bracket_ends(spec), [])


if __name__ == "__main__":
    unittest.main()
