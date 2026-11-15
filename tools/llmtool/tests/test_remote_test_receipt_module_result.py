"""#2119 PR 2 part (a): remote_test_receipt.module_result builds one module entry."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "remote_test_receipt.py"
SPEC = importlib.util.spec_from_file_location("remote_test_receipt_module_result", SCRIPT)
receipt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt)


def output(*lines):
    return "\n".join(lines) + "\n"


class ModuleResultTests(unittest.TestCase):
    def test_normal_module(self):
        text = output("....", "Ran 12 tests in 0.512s", "", "OK")
        expected = {"module": "test_a", "ran": 12, "skipped": 0, "final": "OK"}
        self.assertEqual(receipt.module_result("test_a", text, "OK"), expected)

    def test_class_level_skip(self):
        final = "OK (skipped=1)"
        text = output("Ran 0 tests in 0.001s", "", final)
        expected = {"module": "test_tui", "ran": 0, "skipped": 1, "final": final}
        self.assertEqual(receipt.module_result("test_tui", text, final), expected)

    def test_last_ran_line_wins_over_nested_output(self):
        text = output("Ran 3 tests in 0.1s", "OK", "....", "Ran 9 tests in 1.0s", "OK")
        self.assertEqual(receipt.module_result("test_b", text, "OK")["ran"], 9)

    def test_no_ran_line_is_zero(self):
        text = output("ERROR: test_c", "Traceback")
        result = receipt.module_result("test_c", text, "NO_FINAL")
        self.assertEqual((result["ran"], result["skipped"]), (0, 0))

    def test_skip_count_from_final(self):
        final = "OK (skipped=2)"
        text = output("Ran 5 tests in 0.2s", final)
        self.assertEqual(receipt.module_result("test_d", text, final)["skipped"], 2)

    def test_bytes_and_str_give_the_same_entry(self):
        text = output("Ran 4 tests in 0.3s", "OK")
        as_bytes = receipt.module_result("test_e", text.encode("utf8"), "OK")
        self.assertEqual(as_bytes, receipt.module_result("test_e", text, "OK"))


if __name__ == "__main__":
    unittest.main()
