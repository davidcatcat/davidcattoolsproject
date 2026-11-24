import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"

SPEC = importlib.util.spec_from_file_location("spec_preflight_2710", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)


def _ladder_log_for_tier(target_tier: int) -> str:
    rows = []
    task_num = 1
    for tier in range(1, target_tier):
        for _ in range(3):
            rows.append(
                json.dumps(
                    {
                        "task": f"t-{task_num:03d}",
                        "tier": tier,
                        "attempts": 1,
                        "outcome": "SOLVED",
                    }
                )
            )
            task_num += 1
    return "\n".join(rows) + ("\n" if rows else "")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class SpecPreflightTierInputs2710Tests(unittest.TestCase):
    def setUp(self):
        self.base_spec = {
            "request": "Update bounded helper.",
            "summary": "Measure tier inputs accurately.",
            "goal": "Keep tier sizing aligned with real inputs.",
            "acceptance": ["All bounded tier checks pass."],
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def _run_main(self, spec: dict, *, tier: int = 1, extra_args: tuple[str, ...] = ()):
        spec_path = self.dir / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        ladder_path = self.dir / "ladder.jsonl"
        ladder_path.write_text(_ladder_log_for_tier(tier), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = spec_preflight.main(
                [str(spec_path), "--ladder", str(ladder_path), *extra_args]
            )
        return status, output.getvalue()

    def test_large_read_set_without_context_file_raises_required_tier(self):
        repo = self.dir / "repo"
        repo.mkdir()
        (repo / "a.py").write_bytes(b"a" * 1000)
        (repo / "b.py").write_bytes(b"b" * 1000)
        # Duplicate read and write-also-read should only count each unique read once (2000 bytes -> T2).
        spec = dict(
            self.base_spec,
            read=["a.py", "b.py", "a.py"],
            write=["a.py"],
        )
        status, output = self._run_main(spec, tier=1, extra_args=("--repo", str(repo)))
        self.assertEqual(status, 2)
        self.assertEqual(output, "SPEC_LADDER_TIER_EXCEEDED:required=2,current=1\n")

        status2, output2 = self._run_main(spec, tier=2, extra_args=("--repo", str(repo)))
        self.assertEqual(status2, 0)
        self.assertEqual(output2, "SPEC_PREFLIGHT_OK:tier=2,current=2\n")

    def test_packet_and_repair_evidence_parts_add_to_context_bytes(self):
        repo = self.dir / "repo"
        repo.mkdir()
        (repo / "a.py").write_bytes(b"a" * 1200)
        spec = dict(
            self.base_spec,
            read=["a.py"],
            write=["a.py"],
            repair_evidence="r" * 500,
        )
        # 1200 + 500 = 1700 bytes -> T2.
        status, output = self._run_main(spec, tier=1, extra_args=("--repo", str(repo)))
        self.assertEqual(status, 2)
        self.assertEqual(output, "SPEC_LADDER_TIER_EXCEEDED:required=2,current=1\n")

    def test_base_spec_with_new_write_file_requires_tier_4(self):
        repo = self.dir / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / "existing.py").write_bytes(b"x" * 100)
        _git(repo, "add", "existing.py")
        _git(repo, "commit", "-m", "initial")
        base_sha = _git(repo, "rev-parse", "HEAD")

        # Even if new_file.py exists in the working tree, it does not exist at base_sha.
        (repo / "new_file.py").write_bytes(b"y" * 100)

        spec = dict(
            self.base_spec,
            base=base_sha,
            read=["existing.py"],
            write=["new_file.py"],
        )
        status, output = self._run_main(spec, tier=3, extra_args=("--repo", str(repo)))
        self.assertEqual(status, 2)
        self.assertEqual(output, "SPEC_LADDER_TIER_EXCEEDED:required=4,current=3\n")

        status_ok, output_ok = self._run_main(spec, tier=4, extra_args=("--repo", str(repo)))
        self.assertEqual(status_ok, 0)
        self.assertEqual(output_ok, "SPEC_PREFLIGHT_OK:tier=4,current=4\n")

    def test_base_spec_measures_read_bytes_at_base_not_working_tree(self):
        repo = self.dir / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / "mod.py").write_bytes(b"x" * 2000)
        _git(repo, "add", "mod.py")
        _git(repo, "commit", "-m", "initial")
        base_sha = _git(repo, "rev-parse", "HEAD")

        # Working tree shrinks mod.py to 10 bytes, but base_sha still has 2000 bytes (T2).
        (repo / "mod.py").write_bytes(b"x" * 10)
        spec = dict(
            self.base_spec,
            base=base_sha,
            read=["mod.py"],
            write=["mod.py"],
        )
        status, output = self._run_main(spec, tier=1, extra_args=("--repo", str(repo)))
        self.assertEqual(status, 2)
        self.assertEqual(output, "SPEC_LADDER_TIER_EXCEEDED:required=2,current=1\n")

    def test_spec_beyond_t6_is_explicitly_refused(self):
        repo = self.dir / "repo"
        repo.mkdir()
        (repo / "huge.py").write_bytes(b"h" * 25000)
        spec = dict(
            self.base_spec,
            read=["huge.py"],
            write=["huge.py"],
        )
        status, output = self._run_main(spec, tier=6, extra_args=("--repo", str(repo)))
        self.assertEqual(status, 2)
        self.assertIn("SPEC_LADDER_TIER_EXCEEDED", output)
        self.assertNotIn("SPEC_PREFLIGHT_OK", output)

    def test_unmeasurable_read_file_fails_preflight(self):
        repo = self.dir / "repo"
        repo.mkdir()
        (repo / "subdir").mkdir()
        (self.dir / "escape.py").write_bytes(b"e")
        for bad_read in (["missing.py"], ["subdir"], ["../escape.py"]):
            with self.subTest(read=bad_read):
                spec = dict(
                    self.base_spec,
                    read=bad_read,
                    write=["missing.py"],
                )
                status, output = self._run_main(spec, tier=6, extra_args=("--repo", str(repo)))
                self.assertEqual(status, 1)
                self.assertEqual(output, "SPEC_PREFLIGHT_FAIL:read_path_unmeasurable\n")


if __name__ == "__main__":
    unittest.main()
