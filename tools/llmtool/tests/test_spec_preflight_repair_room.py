import contextlib
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)
REPAIR_ROOM_MAX_ACCEPTANCE = spec_preflight.REPAIR_ROOM_MAX_ACCEPTANCE


def _make_spec(count: int, write: list[str] | None = None, read: list[str] | None = None) -> dict:
    spec = {
        "request": "Add a bounded helper.",
        "summary": "Screen a dispatch spec locally.",
        "goal": "Print field names only.",
        "acceptance": [f"Item {i}." for i in range(1, count + 1)],
    }
    if write is not None:
        spec["write"] = write
    if read is not None:
        spec["read"] = read
    return spec


def run_main(spec: dict, *extra_args, args=None):
    all_args = []
    for arg in extra_args:
        if isinstance(arg, (list, tuple)):
            all_args.extend(str(x) for x in arg)
        else:
            all_args.append(str(arg))
    if args:
        if isinstance(args, (list, tuple)):
            all_args.extend(str(x) for x in args)
        else:
            all_args.append(str(args))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        cmd_args = [str(path)] + all_args
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            status = spec_preflight.main(cmd_args)
        return status, out_buf.getvalue(), err_buf.getvalue()


class SpecPreflightRepairRoomTests(unittest.TestCase):
    def run_main(self, spec: dict, *extra_args, args=None):
        return run_main(spec, *extra_args, args=args)

    def test_constant_value(self):
        self.assertEqual(REPAIR_ROOM_MAX_ACCEPTANCE, 15)

    def test_sixteen_acceptance_items_warns(self):
        spec = _make_spec(16)
        status, stdout, stderr = self.run_main(spec)
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "SPEC_PREFLIGHT_OK\n")
        self.assertEqual(stderr, "SPEC_PREFLIGHT_WARN:ACCEPTANCE_NO_REPAIR_ROOM\n")

    def test_fifteen_acceptance_items_no_warn(self):
        spec = _make_spec(15)
        status, stdout, stderr = self.run_main(spec)
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "SPEC_PREFLIGHT_OK\n")
        self.assertEqual(stderr, "")

    def test_one_acceptance_item_no_warn(self):
        spec = _make_spec(1)
        status, stdout, stderr = self.run_main(spec)
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "SPEC_PREFLIGHT_OK\n")
        self.assertEqual(stderr, "")

    def test_seventeen_acceptance_items_fails_without_warn(self):
        spec = _make_spec(17)
        status, stdout, stderr = self.run_main(spec)
        self.assertEqual(status, 1)
        self.assertTrue(stdout.startswith("SPEC_PREFLIGHT_FAIL:"))
        self.assertEqual(stderr, "")

    def test_ladder_sixteen_acceptance_items_warns(self):
        spec = _make_spec(16, write=["a.py"], read=["a.py"])
        with tempfile.TemporaryDirectory() as temp_dir:
            ladder_file = Path(temp_dir) / "ladder.log"
            ladder_file.write_text(
                json.dumps({"tier": 1, "current": 1, "current_tier": 1}) + "\n",
                encoding="utf-8",
            )
            status, stdout, stderr = self.run_main(spec, "--ladder", str(ladder_file))
            self.assertEqual(status, 0)
            self.assertEqual(stdout, "SPEC_PREFLIGHT_OK:tier=1,current=1\n")
            self.assertEqual(stderr, "SPEC_PREFLIGHT_WARN:ACCEPTANCE_NO_REPAIR_ROOM\n")


if __name__ == "__main__":
    unittest.main()
