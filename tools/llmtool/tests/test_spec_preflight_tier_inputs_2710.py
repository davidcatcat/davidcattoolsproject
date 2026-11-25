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


def _ladder_log_for_tier(target_tier: int, route: str | None = "gw-agent") -> str:
    rows = []
    task_num = 1
    for tier in range(1, target_tier):
        tier_value = f"{route}-{tier}" if route else tier
        for _ in range(3):
            rows.append(
                json.dumps(
                    {
                        "task": f"t-{task_num:03d}",
                        "tier": tier_value,
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
            "provider": "gw-agent",
            "request": "Update bounded helper.",
            "summary": "Measure tier inputs accurately.",
            "goal": "Keep tier sizing aligned with real inputs.",
            "acceptance": ["All bounded tier checks pass."],
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def _run_main(
        self,
        spec: dict,
        *,
        tier: int = 1,
        route: str | None = "gw-agent",
        extra_args: tuple[str, ...] = (),
    ):
        spec_path = self.dir / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        ladder_path = self.dir / "ladder.jsonl"
        ladder_path.write_text(_ladder_log_for_tier(tier, route=route), encoding="utf-8")
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
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "initial")
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
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "initial")
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

    def test_non_gw_agent_spec_keeps_existing_ladder_behaviour(self):
        for provider in (None, "codex"):
            with self.subTest(provider=provider):
                spec = {k: v for k, v in self.base_spec.items() if k != "provider"}
                if provider is not None:
                    spec["provider"] = provider
                spec["read"] = ["missing.py"]
                spec["write"] = ["missing.py"]
                status, output = self._run_main(spec, tier=1, route=None)
                self.assertEqual(status, 0)
                self.assertEqual(output, "SPEC_PREFLIGHT_OK:tier=1,current=1\n")

    def test_gw_agent_without_trusted_root_is_refused_not_sized(self):
        for read_paths in (["a.py"], []):
            with self.subTest(read=read_paths):
                spec = dict(self.base_spec, read=read_paths, write=["a.py"])
                status, output = self._run_main(spec, tier=6)
                self.assertEqual(status, 1)
                self.assertTrue(output.startswith("SPEC_PREFLIGHT_FAIL:"))
                self.assertNotIn("SPEC_PREFLIGHT_OK", output)

        missing_repo = self.dir / "does_not_exist"
        spec = dict(self.base_spec, read=[], write=["a.py"])
        status, output = self._run_main(
            spec, tier=6, extra_args=("--repo", str(missing_repo))
        )
        self.assertEqual(status, 1)
        self.assertTrue(output.startswith("SPEC_PREFLIGHT_FAIL:"))

    def test_gw_agent_base_unmeasurable_read_is_refused(self):
        repo = self.dir / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / "existing.py").write_bytes(b"x" * 100)
        _git(repo, "add", "existing.py")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "initial")
        base_sha = _git(repo, "rev-parse", "HEAD")

        # Even if only_working_tree.py exists in the working tree, it is absent at base_sha.
        (repo / "only_working_tree.py").write_bytes(b"y" * 100)
        spec = dict(
            self.base_spec,
            base=base_sha,
            read=["only_working_tree.py"],
            write=["existing.py"],
        )
        status, output = self._run_main(spec, tier=6, extra_args=("--repo", str(repo)))
        self.assertEqual(status, 1)
        self.assertEqual(output, "SPEC_PREFLIGHT_FAIL:read_path_unmeasurable\n")

    def test_non_gw_agent_with_write_paths_keeps_tier_1(self):
        spec = {k: v for k, v in self.base_spec.items() if k != "provider"}
        spec["write_paths"] = ["a.py", "b.py"]
        spec["read_paths"] = ["a.py", "b.py"]
        status, output = self._run_main(spec, tier=1, route=None)
        self.assertEqual(status, 0)
        self.assertEqual(output, "SPEC_PREFLIGHT_OK:tier=1,current=1\n")

    def test_gw_agent_without_ladder_still_refuses_unmeasurable_or_oversized(self):
        spec_no_repo = dict(self.base_spec, read=["missing.py"], write=["a.py"])
        spec_path = self.dir / "spec_no_repo.json"
        spec_path.write_text(json.dumps(spec_no_repo), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = spec_preflight.main([str(spec_path)])
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue(), "SPEC_PREFLIGHT_FAIL:trusted_root_missing\n")

        repo = self.dir / "repo_no_ladder"
        repo.mkdir()
        (repo / "huge.py").write_bytes(b"h" * 25000)
        spec_missing_read = dict(self.base_spec, read=["absent.py"], write=["absent.py"])
        spec_path.write_text(json.dumps(spec_missing_read), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = spec_preflight.main([str(spec_path), "--repo", str(repo)])
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue(), "SPEC_PREFLIGHT_FAIL:read_path_unmeasurable\n")

        spec_beyond_t6 = dict(self.base_spec, read=["huge.py"], write=["huge.py"])
        spec_path.write_text(json.dumps(spec_beyond_t6), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = spec_preflight.main([str(spec_path), "--repo", str(repo)])
        self.assertEqual(status, 2)
        self.assertIn("SPEC_LADDER_TIER_EXCEEDED:required=beyond_t6", out.getvalue())

    def test_gw_agent_absent_packet_file_is_refused(self):
        repo = self.dir / "repo_pkt"
        repo.mkdir()
        (repo / "a.py").write_bytes(b"a" * 100)
        for field, value in (
            ("context_file", "missing_context.txt"),
            ("packet_file", "missing_packet.txt"),
            ("evidence_files", ["missing_evidence.txt"]),
        ):
            with self.subTest(field=field):
                spec = dict(self.base_spec, read=["a.py"], write=["a.py"])
                spec[field] = value
                status, output = self._run_main(spec, tier=6, extra_args=("--repo", str(repo)))
                self.assertEqual(status, 1)
                self.assertEqual(output, "SPEC_PREFLIGHT_FAIL:packet_file_unmeasurable\n")

    def test_malformed_read_or_read_paths_is_refused(self):
        repo = self.dir / "repo_malformed_read"
        repo.mkdir()
        (repo / "a.py").write_bytes(b"a" * 25000)
        cases = (
            {"read": "a.py"},
            {"read_paths": "a.py"},
            {"read": 123},
            {"read": "a.py", "read_paths": ["a.py"]},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                spec = dict(self.base_spec, write=["a.py"], **overrides)
                status, output = self._run_main(spec, tier=6, extra_args=("--repo", str(repo)))
                self.assertEqual(status, 1)
                self.assertEqual(output, "SPEC_PREFLIGHT_FAIL:read_path_unmeasurable\n")


if __name__ == "__main__":
    unittest.main()
