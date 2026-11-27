#!/usr/bin/env python3
"""Compute changed paths between git merge-base and HEAD."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT_SECONDS = 60
ERROR_MESSAGE = "ci_changed_paths: failed to determine changed paths\n"
_LINE_MODE_ESCAPES = {
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
    "\f": "\\f",
    "\x1c": "\\x1c",
    "\x1d": "\\x1d",
    "\x1e": "\\x1e",
    "\x85": "\\x85",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def _escape_for_lines(path: str) -> str:
    """Render *path* on a single diagnostic line."""
    return "".join(_LINE_MODE_ESCAPES.get(char, char) for char in path)


def _fail() -> None:
    sys.stderr.write(ERROR_MESSAGE)
    sys.exit(2)


class _StrictParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _fail()


def _run_git(args: list[str], repo: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        _fail()
    if proc.returncode != 0:
        _fail()
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        _fail()


def _run_git_bytes(args: list[str], repo: Path) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        _fail()
    if proc.returncode != 0:
        _fail()
    return proc.stdout


def main(argv: list[str] | None = None) -> int:
    parser = _StrictParser(
        description="Compute changed paths between git merge-base and HEAD."
    )
    parser.add_argument("--base", required=True, help="Base commit 40-hex SHA")
    parser.add_argument("--head", required=True, help="Head commit 40-hex SHA")
    parser.add_argument(
        "--format",
        required=True,
        choices=["nul", "lines"],
        help="Output format: nul or lines",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository directory (default: current working directory)",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        _fail()

    base_sha = args.base
    head_sha = args.head

    if not SHA_PATTERN.match(base_sha) or not SHA_PATTERN.match(head_sha):
        _fail()

    repo = args.repo.resolve() if args.repo is not None else Path.cwd()
    if not repo.is_dir():
        _fail()

    # Verify both SHAs resolve to commits
    base_commit = _run_git(["rev-parse", "--verify", f"{base_sha}^{{commit}}"], repo).strip()
    if not SHA_PATTERN.match(base_commit):
        _fail()

    head_commit = _run_git(["rev-parse", "--verify", f"{head_sha}^{{commit}}"], repo).strip()
    if not SHA_PATTERN.match(head_commit):
        _fail()

    # Find merge base: exactly one 40-hex line
    mb_raw = _run_git(["merge-base", base_sha, head_sha], repo)
    mb_lines = [line.strip() for line in mb_raw.splitlines() if line.strip()]
    if len(mb_lines) != 1 or not SHA_PATTERN.match(mb_lines[0]):
        _fail()
    mb = mb_lines[0]

    # Diff between merge base and head
    diff_raw = _run_git_bytes(
        ["diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", mb, head_sha, "--"],
        repo,
    )
    try:
        diff_str = diff_raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail()

    paths = [p for p in diff_str.split("\0") if p]

    if args.format == "nul":
        for path in paths:
            sys.stdout.write(f"{path}\0")
    else:
        for path in paths:
            sys.stdout.write(f"{_escape_for_lines(path)}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
