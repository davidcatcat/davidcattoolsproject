"""Unit tests for redact_known_values script."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "redact_known_values.py"
_spec = importlib.util.spec_from_file_location("redact_known_values", _SCRIPT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load spec from {_SCRIPT_PATH}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

MAX_FILE_SIZE = _mod.MAX_FILE_SIZE
RedactStats = _mod.RedactStats
RedactPathError = _mod.RedactPathError
RedactMapError = _mod.RedactMapError
main = _mod.main
redact_repo = _mod.redact_repo


FAKE_VAL_BASE = "fake-host.example.invalid"
FAKE_VAL_EXTRA = "fake-host.example.invalid.extra"
FAKE_VAL_OTHER = "fake-service.example.invalid"
FAKE_PLC_BASE = "[REDACTED_HOST]"
FAKE_PLC_EXTRA = "[REDACTED_EXTRA]"
FAKE_PLC_OTHER = "[REDACTED_OTHER]"


def _init_git_repo(repo_dir: Path) -> None:
    """Initialize a git repository in repo_dir with basic config."""
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )


def _commit_file(repo_dir: Path, rel_path: str, content: str | bytes) -> Path:
    """Write and git commit a file in repo_dir."""
    full_path = repo_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        full_path.write_text(content, encoding="utf-8")
    else:
        full_path.write_bytes(content)
    subprocess.run(["git", "add", rel_path], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"add {rel_path}"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    return full_path


def _write_map(
    map_path: Path,
    entries: list[dict[str, str]],
    mode: int = 0o600,
) -> None:
    """Write map JSON and set permissions."""
    map_path.write_text(json.dumps(entries), encoding="utf-8")
    map_path.chmod(mode)


class TestRedactKnownValues(unittest.TestCase):
    """Test suite covering redact_known_values CLI and functions."""

    def setUp(self) -> None:
        self.temp_repo_dir = tempfile.TemporaryDirectory()
        self.temp_map_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_repo_dir.name).resolve()
        self.map_dir = Path(self.temp_map_dir.name).resolve()
        _init_git_repo(self.repo_dir)

        self.default_map_path = self.map_dir / "redact-map.json"
        self.default_map_entries = [
            {"value": FAKE_VAL_EXTRA, "placeholder": FAKE_PLC_EXTRA},
            {"value": FAKE_VAL_BASE, "placeholder": FAKE_PLC_BASE},
        ]
        _write_map(self.default_map_path, self.default_map_entries, mode=0o600)

    def tearDown(self) -> None:
        self.temp_map_dir.cleanup()
        self.temp_repo_dir.cleanup()

    def _run_cli(self, args: list[str]) -> tuple[int, str, str]:
        """Run main with args and capture stdout/stderr."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            code = main(args)
        return code, out_buf.getvalue(), err_buf.getvalue()

    def test_dry_run_counts_and_leaves_files_unchanged(self) -> None:
        """Dry run prints per-file replacement counts, total, and skip counts."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Config endpoint: {FAKE_VAL_BASE}\n",
        )
        code, out, err = self._run_cli(
            ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("sample.txt: 1", out)
        self.assertIn("Total: 1", out)
        self.assertIn("skipped_binary: 0", out)
        self.assertIn("skipped_oversized: 0", out)
        self.assertIn("unreadable: 0", out)
        self.assertIn("symlinks: 0", out)
        # Verify file content is unchanged
        self.assertEqual(
            file_path.read_text(encoding="utf-8"),
            f"Config endpoint: {FAKE_VAL_BASE}\n",
        )
        # Ensure output never contains fake value or placeholder
        self.assertNotIn(FAKE_VAL_BASE, out)
        self.assertNotIn(FAKE_PLC_BASE, out)

    def test_apply_replaces(self) -> None:
        """Apply flag writes replacements to tracked files."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Connect to {FAKE_VAL_BASE} now.\n",
        )
        code, out, err = self._run_cli(
            [
                "--apply",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("sample.txt: 1", out)
        self.assertIn("Total: 1", out)
        self.assertIn("skipped_binary: 0", out)
        self.assertIn("skipped_oversized: 0", out)
        self.assertIn("unreadable: 0", out)
        self.assertIn("symlinks: 0", out)
        # File is replaced with placeholder
        replaced = file_path.read_text(encoding="utf-8")
        self.assertEqual(replaced, f"Connect to {FAKE_PLC_BASE} now.\n")
        self.assertNotIn(FAKE_VAL_BASE, replaced)

    def test_check_exit_codes(self) -> None:
        """Check mode exits 1 when value found, prints counts, and exits 0 when clean."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Address is {FAKE_VAL_BASE}.\n",
        )
        # Value is present, exit code must be 1
        code, out, err = self._run_cli(
            [
                "--check",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn("sample.txt: 1", out)
        self.assertNotIn("Total:", out)
        self.assertIn("skipped_binary: 0", out)
        self.assertIn("skipped_oversized: 0", out)
        self.assertIn("unreadable: 0", out)
        self.assertIn("symlinks: 0", out)
        self.assertNotIn(FAKE_VAL_BASE, out)

        # After replacement, check mode must exit 0 and print counts
        file_path.write_text(f"Address is {FAKE_PLC_BASE}.\n", encoding="utf-8")
        code_clean, out_clean, err_clean = self._run_cli(
            [
                "--check",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code_clean, 0)
        self.assertEqual(err_clean, "")
        self.assertIn("skipped_binary: 0", out_clean)
        self.assertIn("skipped_oversized: 0", out_clean)
        self.assertIn("unreadable: 0", out_clean)
        self.assertIn("symlinks: 0", out_clean)

    def test_refuses_0644_map(self) -> None:
        """Map with permissions 0644 must be rejected with exit code 2."""
        bad_map = self.map_dir / "bad-mode-map.json"
        _write_map(bad_map, self.default_map_entries, mode=0o644)

        code, out, err = self._run_cli(
            ["--map", str(bad_map), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_BAD_MODE", err)
        self.assertNotIn(FAKE_VAL_BASE, err)

    def test_refuses_map_inside_repo(self) -> None:
        """Map inside repository must be rejected with exit code 2."""
        inside_map = self.repo_dir / "untracked-map.json"
        _write_map(inside_map, self.default_map_entries, mode=0o600)

        code, out, err = self._run_cli(
            ["--map", str(inside_map), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_INSIDE_REPO", err)
        self.assertNotIn(FAKE_VAL_BASE, err)

    def test_refuses_git_tracked_map(self) -> None:
        """Map tracked by git must be rejected with exit code 2."""
        tracked_map = self.repo_dir / "tracked-map.json"
        _write_map(tracked_map, self.default_map_entries, mode=0o600)
        subprocess.run(
            ["git", "add", "tracked-map.json"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

        code, out, err = self._run_cli(
            ["--map", str(tracked_map), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_TRACKED_BY_GIT", err)
        self.assertNotIn(FAKE_VAL_BASE, err)

    def test_output_never_contains_value(self) -> None:
        """Neither stdout nor stderr should ever print values or placeholders from map."""
        _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Target: {FAKE_VAL_BASE} and {FAKE_VAL_EXTRA}\n",
        )
        # Test dry run
        _, out, err = self._run_cli(
            ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertNotIn(FAKE_VAL_BASE, out)
        self.assertNotIn(FAKE_VAL_EXTRA, out)
        self.assertNotIn(FAKE_PLC_BASE, out)
        self.assertNotIn(FAKE_PLC_EXTRA, out)
        self.assertNotIn(FAKE_VAL_BASE, err)
        self.assertNotIn(FAKE_VAL_EXTRA, err)
        self.assertNotIn(FAKE_PLC_BASE, err)
        self.assertNotIn(FAKE_PLC_EXTRA, err)

        # Test check
        _, out, err = self._run_cli(
            [
                "--check",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertNotIn(FAKE_VAL_BASE, out)
        self.assertNotIn(FAKE_VAL_EXTRA, out)
        self.assertNotIn(FAKE_PLC_BASE, out)
        self.assertNotIn(FAKE_PLC_EXTRA, out)
        self.assertNotIn(FAKE_VAL_BASE, err)
        self.assertNotIn(FAKE_VAL_EXTRA, err)
        self.assertNotIn(FAKE_PLC_BASE, err)
        self.assertNotIn(FAKE_PLC_EXTRA, err)

        # Test error report
        invalid_map = self.map_dir / "invalid-entries.json"
        _write_map(
            invalid_map,
            [
                {"value": FAKE_VAL_BASE, "placeholder": FAKE_PLC_BASE},
                {"value": FAKE_VAL_BASE, "placeholder": FAKE_PLC_BASE},
            ],
            mode=0o600,
        )
        _, out, err = self._run_cli(
            ["--map", str(invalid_map), "--repo", str(self.repo_dir)]
        )
        self.assertNotIn(FAKE_VAL_BASE, out)
        self.assertNotIn(FAKE_VAL_BASE, err)
        self.assertNotIn(FAKE_PLC_BASE, out)
        self.assertNotIn(FAKE_PLC_BASE, err)

    def test_longest_first_replacement(self) -> None:
        """Longer matching values must be replaced before shorter prefixes."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Host is {FAKE_VAL_EXTRA} here.\n",
        )
        code, _, _ = self._run_cli(
            [
                "--apply",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 0)
        content = file_path.read_text(encoding="utf-8")
        # Must replace the full longer string, not partial prefix
        self.assertEqual(content, f"Host is {FAKE_PLC_EXTRA} here.\n")
        self.assertNotIn(f"{FAKE_PLC_BASE}.extra", content)

    def test_paths_filtering(self) -> None:
        """Paths flag restricts scanning to specified files or directories."""
        file_a = _commit_file(
            self.repo_dir,
            "dir_a/file_a.txt",
            f"Target {FAKE_VAL_BASE}\n",
        )
        file_b = _commit_file(
            self.repo_dir,
            "dir_b/file_b.txt",
            f"Target {FAKE_VAL_BASE}\n",
        )
        # Scan only dir_a
        code, out, _ = self._run_cli(
            [
                "--apply",
                "--paths",
                "dir_a",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("dir_a/file_a.txt: 1", out)
        self.assertNotIn("dir_b/file_b.txt", out)
        self.assertIn("Total: 1", out)
        self.assertIn("skipped_binary: 0", out)

        # file_a replaced, file_b unchanged
        self.assertIn(FAKE_PLC_BASE, file_a.read_text(encoding="utf-8"))
        self.assertIn(FAKE_VAL_BASE, file_b.read_text(encoding="utf-8"))

    def test_binary_and_oversized_files_skipped(self) -> None:
        """Binary files are scanned as bytes and counted; oversized files (>2 MiB) are skipped and counted."""
        # Binary file containing fake value
        _commit_file(
            self.repo_dir,
            "data.bin",
            f"prefix\0{FAKE_VAL_BASE}".encode("utf-8"),
        )
        # Oversized file (>2 MiB) containing fake value
        oversized_content = ("x" * (MAX_FILE_SIZE + 100)) + FAKE_VAL_BASE
        _commit_file(
            self.repo_dir,
            "huge.txt",
            oversized_content,
        )

        stats = redact_repo(
            repo_root=self.repo_dir,
            map_path=self.default_map_path,
            apply=True,
        )
        self.assertEqual(stats.skipped_binary, 1)
        self.assertEqual(stats.skipped_oversized, 1)
        self.assertEqual(stats.unreadable, 0)
        self.assertEqual(stats.symlinks, 0)
        # Binary file is scanned as bytes, so hit is reported
        self.assertEqual(stats.total, 1)
        # But binary file is never rewritten by apply
        self.assertEqual(
            (self.repo_dir / "data.bin").read_bytes(),
            f"prefix\0{FAKE_VAL_BASE}".encode("utf-8"),
        )

    def test_untracked_files_ignored(self) -> None:
        """Untracked files are not read or modified."""
        untracked = self.repo_dir / "untracked.txt"
        untracked.write_text(f"Untracked: {FAKE_VAL_BASE}\n", encoding="utf-8")

        code, out, _ = self._run_cli(
            [
                "--apply",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("untracked.txt", out)
        self.assertIn(FAKE_VAL_BASE, untracked.read_text(encoding="utf-8"))

    def test_refuses_symlink_map(self) -> None:
        """Map that is a symlink must be rejected with exit code 2."""
        symlink_map = self.map_dir / "symlink-map.json"
        symlink_map.symlink_to(self.default_map_path)
        code, _, err = self._run_cli(
            ["--map", str(symlink_map), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_NOT_REGULAR", err)

    def test_refuses_not_owned_map(self) -> None:
        """Map not owned by current user must be rejected with exit code 2."""
        if not hasattr(os, "getuid"):
            self.skipTest("os.getuid not supported on this platform")
        current_uid = os.getuid()
        with mock.patch("os.getuid", return_value=current_uid + 1):
            code, _, err = self._run_cli(
                ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
            )
            self.assertEqual(code, 2)
            self.assertIn("MAP_NOT_OWNED", err)

    def test_refuses_combining_apply_and_check(self) -> None:
        """Combining --apply and --check must be rejected with exit code 2."""
        code, _, err = self._run_cli(
            [
                "--apply",
                "--check",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot combine", err)

    def test_env_var_map_path(self) -> None:
        """Environment variable LLMTOOL_REDACT_MAP is used when --map is omitted."""
        _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Target {FAKE_VAL_BASE}\n",
        )
        with mock.patch.dict(os.environ, {"LLMTOOL_REDACT_MAP": str(self.default_map_path)}):
            code, out, _ = self._run_cli(["--repo", str(self.repo_dir)])
            self.assertEqual(code, 0)
            self.assertIn("sample.txt: 1", out)

    def test_map_validation_constraints(self) -> None:
        """Map validation rejects non-existent, malformed, or short entries."""
        # Non-existent map
        code, _, err = self._run_cli(
            [
                "--map",
                str(self.map_dir / "missing.json"),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_NOT_FOUND", err)

        # Short value (< 6 characters)
        short_map = self.map_dir / "short-val.json"
        _write_map(
            short_map,
            [{"value": "short", "placeholder": "[P]"}],
            mode=0o600,
        )
        code, _, err = self._run_cli(
            ["--map", str(short_map), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_VALUE_TOO_SHORT", err)

        # Empty value
        empty_map = self.map_dir / "empty-val.json"
        _write_map(
            empty_map,
            [{"value": "", "placeholder": "[P]"}],
            mode=0o600,
        )
        code, _, err = self._run_cli(
            ["--map", str(empty_map), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("MAP_EMPTY_VALUE", err)

    # -------------------------------------------------------------------------
    # Acceptance tests for Task rules
    # -------------------------------------------------------------------------

    def test_rule1_paths_invalid_scenarios(self) -> None:
        """Rule 1: --paths with missing, outside, or non-matching path exits 2 and prints PATHS_INVALID."""
        target_file = _commit_file(
            self.repo_dir,
            "target.txt",
            f"Target address is {FAKE_VAL_BASE}\n",
        )

        # Case 1: Missing path
        code_missing, _, err_missing = self._run_cli(
            [
                "--apply",
                "--paths",
                "does_not_exist.txt",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code_missing, 2)
        self.assertIn("PATHS_INVALID", err_missing)

        # Case 2: Path outside repository
        outside_file = self.map_dir / "outside_repo.txt"
        outside_file.write_text("outside repo\n", encoding="utf-8")
        code_outside, _, err_outside = self._run_cli(
            [
                "--apply",
                "--paths",
                str(outside_file),
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code_outside, 2)
        self.assertIn("PATHS_INVALID", err_outside)

        # Case 3: Path matching no tracked file (untracked file in repo)
        untracked = self.repo_dir / "untracked_file.txt"
        untracked.write_text("untracked\n", encoding="utf-8")
        code_untracked, _, err_untracked = self._run_cli(
            [
                "--apply",
                "--paths",
                "untracked_file.txt",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code_untracked, 2)
        self.assertIn("PATHS_INVALID", err_untracked)

        # Case 4: Empty --paths list
        code_empty, _, err_empty = self._run_cli(
            [
                "--apply",
                "--paths",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code_empty, 2)
        self.assertIn("PATHS_INVALID", err_empty)

        # Verify no file was written across all failed attempts
        self.assertEqual(
            target_file.read_text(encoding="utf-8"),
            f"Target address is {FAKE_VAL_BASE}\n",
        )

    def test_rule2_counts_printed_and_incomplete_scan_exit_codes(self) -> None:
        """Rule 2: Every mode prints skip counts; --check and --apply exit 3 on incomplete scan."""
        # Commit oversized file (> 2 MiB) with no map hits
        oversized_content = "y" * (MAX_FILE_SIZE + 100)
        _commit_file(self.repo_dir, "huge.txt", oversized_content)

        # Commit a symlink pointing to an external file
        outside_dummy = self.map_dir / "dummy.txt"
        outside_dummy.write_text("clean\n", encoding="utf-8")
        symlink_path = self.repo_dir / "link_dummy"
        symlink_path.symlink_to(outside_dummy)
        subprocess.run(["git", "add", "link_dummy"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add link_dummy"], cwd=self.repo_dir, capture_output=True, check=True)

        # Commit a clean text file
        _commit_file(self.repo_dir, "clean.txt", "Plain text without targets\n")

        # Dry run prints counts and exits 0
        code_dry, out_dry, err_dry = self._run_cli(
            ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_dry, 0)
        self.assertEqual(err_dry, "")
        self.assertIn("skipped_binary: 0", out_dry)
        self.assertIn("skipped_oversized: 1", out_dry)
        self.assertIn("unreadable: 0", out_dry)
        self.assertIn("symlinks: 1", out_dry)

        # --check with no hits exits 3 (INCOMPLETE_SCAN)
        code_check, out_check, err_check = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_check, 3)
        self.assertEqual(err_check, "")
        self.assertIn("skipped_oversized: 1", out_check)
        self.assertIn("symlinks: 1", out_check)

        # --apply exits 3 (INCOMPLETE_SCAN) after writing nothing for skipped files
        code_apply, out_apply, err_apply = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_apply, 3)
        self.assertEqual(err_apply, "")
        self.assertIn("skipped_oversized: 1", out_apply)
        self.assertIn("symlinks: 1", out_apply)

        # When a hit is present, --check exits 1 despite incomplete scan
        _commit_file(self.repo_dir, "hit.txt", f"Target {FAKE_VAL_BASE}\n")
        code_hit, out_hit, err_hit = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_hit, 1)
        self.assertEqual(err_hit, "")
        self.assertIn("hit.txt: 1", out_hit)

    def test_rule3_tracked_symlinks_never_read_or_written(self) -> None:
        """Rule 3: Tracked symlinks are never read or written and are left untouched."""
        outside_target = self.map_dir / "ext_target.txt"
        outside_target.write_text(f"Host: {FAKE_VAL_BASE}\n", encoding="utf-8")

        symlink_path = self.repo_dir / "tracked_symlink"
        symlink_path.symlink_to(outside_target)
        subprocess.run(["git", "add", "tracked_symlink"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add tracked_symlink"], cwd=self.repo_dir, capture_output=True, check=True)

        code, out, err = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 3)
        self.assertEqual(err, "")
        self.assertIn("symlinks: 1", out)
        # Symlink is still a symlink and points to the outside target
        self.assertTrue(os.path.islink(symlink_path))
        # Outside target is left untouched
        self.assertEqual(
            outside_target.read_text(encoding="utf-8"),
            f"Host: {FAKE_VAL_BASE}\n",
        )

    def test_rule4_binary_files_scanned_as_bytes(self) -> None:
        """Rule 4: Binary files scanned as bytes for map values; --apply never rewrites them."""
        raw_bin = b"hdr\x00data_" + FAKE_VAL_BASE.encode("utf-8") + b"_tail\xff"
        bin_file = _commit_file(self.repo_dir, "sample.bin", raw_bin)

        # --check reports hit in binary file and exits 1
        code_check, out_check, err_check = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_check, 1)
        self.assertEqual(err_check, "")
        self.assertIn("sample.bin: 1", out_check)
        self.assertIn("skipped_binary: 1", out_check)

        # --apply reports hit, counts skipped_binary, exits 0, and never rewrites binary file
        code_apply, out_apply, err_apply = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_apply, 0)
        self.assertEqual(err_apply, "")
        self.assertIn("sample.bin: 1", out_apply)
        self.assertIn("Total: 1", out_apply)
        self.assertIn("skipped_binary: 1", out_apply)
        self.assertEqual(bin_file.read_bytes(), raw_bin)

    def test_happy_path_clean_scan(self) -> None:
        """Happy path: clean repository with ordinary files scans cleanly in all modes."""
        _commit_file(self.repo_dir, "clean.txt", "Safe clean text content without any hits\n")

        # Dry run
        code_dry, out_dry, err_dry = self._run_cli(
            ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_dry, 0)
        self.assertEqual(err_dry, "")
        self.assertIn("Total: 0", out_dry)
        self.assertIn("skipped_binary: 0", out_dry)
        self.assertIn("skipped_oversized: 0", out_dry)
        self.assertIn("unreadable: 0", out_dry)
        self.assertIn("symlinks: 0", out_dry)

        # Check mode
        code_check, out_check, err_check = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_check, 0)
        self.assertEqual(err_check, "")
        self.assertIn("skipped_binary: 0", out_check)
        self.assertIn("skipped_oversized: 0", out_check)
        self.assertIn("unreadable: 0", out_check)
        self.assertIn("symlinks: 0", out_check)

        # Apply mode
        code_apply, out_apply, err_apply = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code_apply, 0)
        self.assertEqual(err_apply, "")
        self.assertIn("Total: 0", out_apply)
        self.assertIn("skipped_binary: 0", out_apply)
        self.assertIn("skipped_oversized: 0", out_apply)
        self.assertIn("unreadable: 0", out_apply)
        self.assertIn("symlinks: 0", out_apply)


if __name__ == "__main__":
    unittest.main()
