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


def _commit_symlink(repo_dir: Path, rel_path: str, target: Path | str) -> Path:
    """Create and git commit a symlink in repo_dir."""
    full_path = repo_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if full_path.is_symlink() or full_path.exists():
        full_path.unlink()
    full_path.symlink_to(target)
    subprocess.run(["git", "add", rel_path], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"add symlink {rel_path}"],
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
        """Dry run prints per-file replacement counts and total, leaving files intact."""
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
        """Check mode exits 1 when value found and 0 when clean."""
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
        self.assertNotIn(FAKE_VAL_BASE, out)
        self.assertIn("skipped_binary: 0", out)
        self.assertIn("skipped_oversized: 0", out)
        self.assertIn("unreadable: 0", out)
        self.assertIn("symlinks: 0", out)

        # After replacement, check mode must exit 0
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
        self.assertNotIn("sample.txt", out_clean)
        self.assertNotIn("Total:", out_clean)
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
        self.assertIn("skipped_oversized: 0", out)
        self.assertIn("unreadable: 0", out)
        self.assertIn("symlinks: 0", out)

        # file_a replaced, file_b unchanged
        self.assertIn(FAKE_PLC_BASE, file_a.read_text(encoding="utf-8"))
        self.assertIn(FAKE_VAL_BASE, file_b.read_text(encoding="utf-8"))

    def test_binary_and_oversized_files_skipped(self) -> None:
        """Binary files are scanned as bytes and not rewritten; oversized files skipped."""
        # Binary file containing fake value
        bin_path = _commit_file(
            self.repo_dir,
            "data.bin",
            f"prefix\0{FAKE_VAL_BASE}".encode("utf-8"),
        )
        # Oversized file (>2 MiB) containing fake value
        oversized_content = ("x" * (MAX_FILE_SIZE + 100)) + FAKE_VAL_BASE
        huge_path = _commit_file(
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
        self.assertEqual(stats.total, 1)
        self.assertEqual(stats.per_file["data.bin"], 1)
        # Ensure binary file was NOT rewritten
        self.assertEqual(
            bin_path.read_bytes(),
            f"prefix\0{FAKE_VAL_BASE}".encode("utf-8"),
        )
        # Ensure oversized file was NOT rewritten
        self.assertEqual(huge_path.read_text(encoding="utf-8"), oversized_content)

    def test_paths_missing_path_rejected(self) -> None:
        """--paths with missing path exits 2 and prints PATHS_INVALID; no file written."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Target: {FAKE_VAL_BASE}\n",
        )
        code, out, err = self._run_cli(
            [
                "--apply",
                "--paths",
                "nonexistent.txt",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("PATHS_INVALID", err)
        self.assertEqual(file_path.read_text(encoding="utf-8"), f"Target: {FAKE_VAL_BASE}\n")

        # Combining an existing tracked path with a missing path must also fail with no writes
        code, out, err = self._run_cli(
            [
                "--apply",
                "--paths",
                "sample.txt",
                "also_missing.txt",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("PATHS_INVALID", err)
        self.assertEqual(file_path.read_text(encoding="utf-8"), f"Target: {FAKE_VAL_BASE}\n")

    def test_paths_outside_repo_rejected(self) -> None:
        """--paths with path outside repo exits 2 and prints PATHS_INVALID; no file written."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Target: {FAKE_VAL_BASE}\n",
        )
        # Relative path pointing outside repo
        code, out, err = self._run_cli(
            [
                "--apply",
                "--paths",
                "../outside.txt",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("PATHS_INVALID", err)
        self.assertEqual(file_path.read_text(encoding="utf-8"), f"Target: {FAKE_VAL_BASE}\n")

        # Absolute path outside repo
        outside_file = self.map_dir / "external.txt"
        outside_file.write_text("external content\n", encoding="utf-8")
        code, out, err = self._run_cli(
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
        self.assertEqual(code, 2)
        self.assertIn("PATHS_INVALID", err)
        self.assertEqual(file_path.read_text(encoding="utf-8"), f"Target: {FAKE_VAL_BASE}\n")

    def test_paths_matches_no_tracked_file_rejected(self) -> None:
        """--paths with untracked or non-matching path exits 2 and prints PATHS_INVALID."""
        file_path = _commit_file(
            self.repo_dir,
            "sample.txt",
            f"Target: {FAKE_VAL_BASE}\n",
        )
        # Untracked file inside repo
        untracked = self.repo_dir / "untracked.txt"
        untracked.write_text(f"Untracked: {FAKE_VAL_BASE}\n", encoding="utf-8")

        code, out, err = self._run_cli(
            [
                "--apply",
                "--paths",
                "untracked.txt",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("PATHS_INVALID", err)
        self.assertEqual(file_path.read_text(encoding="utf-8"), f"Target: {FAKE_VAL_BASE}\n")

        # Empty directory inside repo
        empty_dir = self.repo_dir / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        code, out, err = self._run_cli(
            [
                "--apply",
                "--paths",
                "empty_dir",
                "--map",
                str(self.default_map_path),
                "--repo",
                str(self.repo_dir),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("PATHS_INVALID", err)
        self.assertEqual(file_path.read_text(encoding="utf-8"), f"Target: {FAKE_VAL_BASE}\n")

    def test_tracked_symlink_never_read_or_written(self) -> None:
        """Tracked symlinks are never read or written, left untouched, and counted."""
        outside_target = self.map_dir / "target_outside.txt"
        outside_target.write_text(f"outside {FAKE_VAL_BASE}\n", encoding="utf-8")

        # Commit symlink to file outside repo
        link_out = _commit_symlink(self.repo_dir, "link_outside.txt", outside_target)

        # Commit symlink to file inside repo
        inside_target = _commit_file(self.repo_dir, "internal.txt", f"internal {FAKE_VAL_BASE}\n")
        link_in = _commit_symlink(self.repo_dir, "link_inside.txt", inside_target)

        # Run dry run
        code, out, err = self._run_cli(
            ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("symlinks: 2", out)
        # Outside target is untouched
        self.assertEqual(outside_target.read_text(encoding="utf-8"), f"outside {FAKE_VAL_BASE}\n")
        # Symlinks remain intact
        self.assertTrue(link_out.is_symlink())
        self.assertTrue(link_in.is_symlink())

    def test_incomplete_scan_exit_codes(self) -> None:
        """Incomplete scan (symlink, unreadable, oversized) causes exit 3 in check and apply."""
        # Clean file in repo
        _commit_file(self.repo_dir, "clean.txt", "clean file content\n")

        # Case 1: Symlink skipped with no hits
        outside_file = self.map_dir / "ext.txt"
        outside_file.write_text("ext\n", encoding="utf-8")
        _commit_symlink(self.repo_dir, "link.txt", outside_file)

        # --check with no hits exits 3
        code, out, err = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 3)
        self.assertIn("INCOMPLETE_SCAN", err)
        self.assertIn("symlinks: 1", out)

        # --apply exits 3 after writing nothing for skipped files
        code, out, err = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 3)
        self.assertIn("INCOMPLETE_SCAN", err)
        self.assertIn("symlinks: 1", out)

    def test_incomplete_scan_oversized_and_unreadable(self) -> None:
        """Oversized and unreadable files trigger exit 3 when no hits are found."""
        # Oversized file with no hits
        _commit_file(self.repo_dir, "big.txt", "y" * (MAX_FILE_SIZE + 50))
        code, out, err = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 3)
        self.assertIn("INCOMPLETE_SCAN", err)
        self.assertIn("skipped_oversized: 1", out)

        # Commit a file and make it unreadable via mock open
        unreadable_file = _commit_file(self.repo_dir, "locked.txt", "locked content\n")
        orig_open = open

        def mock_open_failure(file, *args, **kwargs):
            if str(file) == str(unreadable_file):
                raise PermissionError("Access denied")
            return orig_open(file, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=mock_open_failure):
            code, out, err = self._run_cli(
                [
                    "--check",
                    "--paths",
                    "locked.txt",
                    "--map",
                    str(self.default_map_path),
                    "--repo",
                    str(self.repo_dir),
                ]
            )
            self.assertEqual(code, 3)
            self.assertIn("INCOMPLETE_SCAN", err)
            self.assertIn("unreadable: 1", out)

    def test_check_exits_1_on_hit_even_with_incomplete_scan(self) -> None:
        """--check exits 1 when any hit is found, even if an oversized or symlink was skipped."""
        _commit_file(self.repo_dir, "hit.txt", f"Contains {FAKE_VAL_BASE}\n")
        _commit_file(self.repo_dir, "huge.txt", "z" * (MAX_FILE_SIZE + 50))

        code, out, err = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        # Must exit 1 due to hit, NOT 3
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertIn("hit.txt: 1", out)
        self.assertIn("skipped_oversized: 1", out)

    def test_apply_writes_normal_and_exits_3_on_incomplete_scan(self) -> None:
        """--apply replaces normal files but exits 3 when an oversized file is skipped."""
        normal_file = _commit_file(self.repo_dir, "normal.txt", f"Replace {FAKE_VAL_BASE}\n")
        huge_file = _commit_file(
            self.repo_dir,
            "huge.txt",
            ("w" * (MAX_FILE_SIZE + 50)) + FAKE_VAL_BASE,
        )

        code, out, err = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 3)
        self.assertIn("INCOMPLETE_SCAN", err)
        self.assertIn("normal.txt: 1", out)
        self.assertIn("skipped_oversized: 1", out)
        # Normal file was replaced
        self.assertEqual(normal_file.read_text(encoding="utf-8"), f"Replace {FAKE_PLC_BASE}\n")
        # Huge file was untouched
        self.assertIn(FAKE_VAL_BASE, huge_file.read_text(encoding="utf-8"))

    def test_binary_scan_hit_reported_and_never_rewritten(self) -> None:
        """Binary files are scanned as bytes: hits reported, --check exits 1, --apply never rewrites."""
        bin_content = b"\x00\x01\x02" + FAKE_VAL_BASE.encode("utf-8") + b"\xff"
        bin_file = _commit_file(self.repo_dir, "binary.bin", bin_content)

        # --check reports hit and exits 1
        code, out, err = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 1)
        self.assertIn("binary.bin: 1", out)
        self.assertIn("skipped_binary: 1", out)

        # --apply reports hit, exits 0, and does NOT rewrite the binary file
        code, out, err = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("binary.bin: 1", out)
        self.assertIn("skipped_binary: 1", out)
        self.assertEqual(bin_file.read_bytes(), bin_content)

    def test_every_mode_prints_counts(self) -> None:
        """Every mode (dry-run, apply, check) prints the 4 skipped counts."""
        _commit_file(self.repo_dir, "sample.txt", "Clean file without values\n")

        # Dry run
        code, out, _ = self._run_cli(
            ["--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("skipped_binary: 0\n", out)
        self.assertIn("skipped_oversized: 0\n", out)
        self.assertIn("unreadable: 0\n", out)
        self.assertIn("symlinks: 0\n", out)

        # Apply
        code, out, _ = self._run_cli(
            ["--apply", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("skipped_binary: 0\n", out)
        self.assertIn("skipped_oversized: 0\n", out)
        self.assertIn("unreadable: 0\n", out)
        self.assertIn("symlinks: 0\n", out)

        # Check
        code, out, _ = self._run_cli(
            ["--check", "--map", str(self.default_map_path), "--repo", str(self.repo_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("skipped_binary: 0\n", out)
        self.assertIn("skipped_oversized: 0\n", out)
        self.assertIn("unreadable: 0\n", out)
        self.assertIn("symlinks: 0\n", out)

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


if __name__ == "__main__":
    unittest.main()
