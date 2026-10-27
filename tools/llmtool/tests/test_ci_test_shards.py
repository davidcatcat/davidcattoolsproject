import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))
from scripts import ci_test_shards


class CiTestShardsTests(unittest.TestCase):
    def test_partition_covers_discovered_modules_once_for_one_to_four_shards(self):
        modules = {path.name for path in ci_test_shards.TESTS.glob("test_*.py")}
        for count in range(1, 5):
            with self.subTest(shard_count=count):
                shards = ci_test_shards.partition(count)
                flattened = [module for shard in shards for module in shard]
                self.assertEqual(set(flattened), modules)
                self.assertEqual(len(flattened), len(set(flattened)))
                self.assertEqual(len(shards), count)

    def test_partition_is_deterministic_and_balanced_within_largest_module(self):
        timing_data = json.loads(ci_test_shards.TIMINGS.read_text(encoding="utf-8"))
        weights = timing_data["modules"]
        default = timing_data["default_seconds"]
        for count in range(1, 5):
            first = ci_test_shards.partition(count)
            self.assertEqual(first, ci_test_shards.partition(count))
            totals = [sum(weights.get(module, default) for module in shard) for shard in first]
            largest = max(
                (weights.get(module, default) for shard in first for module in shard),
                default=default,
            )
            self.assertLessEqual(max(totals) - min(totals), largest)

    def test_unknown_module_uses_default_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_dir = root / "tests"
            test_dir.mkdir()
            (test_dir / "test_known.py").touch()
            (test_dir / "test_unknown.py").touch()
            timing_path = root / "timings.json"
            timing_path.write_text(
                json.dumps({"default_seconds": 7, "modules": {"test_known.py": 50}}),
                encoding="utf-8",
            )
            with patch.object(ci_test_shards, "TESTS", test_dir), patch.object(
                ci_test_shards, "TIMINGS", timing_path
            ):
                self.assertEqual(
                    ci_test_shards.partition(2),
                    [["test_known.py"], ["test_unknown.py"]],
                )

    def test_workflow_has_aggregate_gate_and_third_runner_mapping(self):
        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("check (Linux ${{ matrix.python }})", workflow)
        self.assertIn("needs: shards", workflow)
        self.assertIn("always()", workflow)
        self.assertIn("ec2-self-runner-3) account=ghrunner3", workflow)


if __name__ == "__main__":
    unittest.main()
