#!/usr/bin/env python3
"""Scan git-tracked files and replace known values with placeholders."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import NoReturn


MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB limit for scanning text files
DEFAULT_MAP_PATH = Path.home() / ".config" / "llmtool" / "redact-map.json"


@dataclass(frozen=True)
class RedactStats:
    """Statistics from a redaction scan."""

    per_file: dict[str, int]
    total: int
    skipped_binary: int
    skipped_oversized: int


class RedactMapError(Exception):
    """Raised when the redaction map violates required constraints."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail_map(code: str, message: str) -> NoReturn:
    """Raise a structured map error."""
    raise RedactMapError(code, message)


def find_repo_root(start_path: Path | None = None) -> Path:
    """Find git repository root or fallback to script ancestry."""
    target = start_path or Path.cwd()
    res = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        return Path(res.stdout.strip()).resolve()
    return Path(__file__).resolve().parents[3]


def _is_tracked_by_git(repo_root: Path, path: Path) -> bool:
    """Check if a path is tracked by git."""
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    res = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(rel)],
        cwd=repo_root,
        capture_output=True,
    )
    return res.returncode == 0


def load_and_validate_map(map_path: Path, repo_root: Path) -> list[tuple[str, str]]:
    """Validate map permissions, location, and structure, returning sorted entries."""
    if not map_path.exists():
        _fail_map("MAP_NOT_FOUND", "map file does not exist")

    try:
        st = map_path.lstat()
    except OSError as exc:
        _fail_map("MAP_UNREADABLE", f"unable to stat map file: {type(exc).__name__}")

    # File must be a regular file, not a symlink or directory
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _fail_map("MAP_NOT_REGULAR", "map file must be a regular file")

    # File must be owned by the user
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        _fail_map("MAP_NOT_OWNED", "map file must be owned by current user")

    # Mode must be exactly 0600
    file_mode = stat.S_IMODE(st.st_mode)
    if file_mode != 0o600:
        _fail_map("MAP_BAD_MODE", f"map file mode must be 0600, found {oct(file_mode)}")

    # Check git tracking before inside check so tracked files report MAP_TRACKED_BY_GIT
    if _is_tracked_by_git(repo_root, map_path):
        _fail_map("MAP_TRACKED_BY_GIT", "map file must not be tracked by git")

    # File must not be inside repository
    try:
        map_path.resolve().relative_to(repo_root.resolve())
        _fail_map("MAP_INSIDE_REPO", "map file must not be inside repository")
    except ValueError:
        pass

    try:
        content = map_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _fail_map("MAP_INVALID_JSON", f"unable to read map file: {type(exc).__name__}")

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        _fail_map("MAP_INVALID_JSON", "map file content is not valid JSON")

    if not isinstance(data, list):
        _fail_map("MAP_INVALID_JSON", "map file must contain a JSON list")

    entries: list[tuple[str, str]] = []
    seen_values: set[str] = set()

    for item in data:
        if not isinstance(item, dict):
            _fail_map("MAP_INVALID_ENTRY", "each map entry must be an object")
        if "value" not in item or "placeholder" not in item:
            _fail_map("MAP_INVALID_ENTRY", "map entry missing 'value' or 'placeholder'")
        val = item["value"]
        plc = item["placeholder"]
        if not isinstance(val, str) or not isinstance(plc, str):
            _fail_map("MAP_INVALID_ENTRY", "value and placeholder must be strings")
        if not val or not plc:
            _fail_map("MAP_EMPTY_VALUE", "value and placeholder must not be empty")
        if len(val) < 6:
            _fail_map("MAP_VALUE_TOO_SHORT", "value length must be at least 6 characters")
        if val in seen_values:
            _fail_map("MAP_DUPLICATE_VALUE", "duplicate value in map entries")
        seen_values.add(val)
        entries.append((val, plc))

    # Longer values replaced first; secondary alphabetical sort for determinism
    entries.sort(key=lambda item: (-len(item[0]), item[0]))
    return entries


def _is_path_selected(tracked_rel: Path, targets: list[Path] | None) -> bool:
    """Return True if tracked path matches any target filter."""
    if not targets:
        return True
    for target in targets:
        if tracked_rel == target:
            return True
        try:
            tracked_rel.relative_to(target)
            return True
        except ValueError:
            pass
    return False


def get_git_tracked_files(repo_root: Path) -> list[str]:
    """Retrieve git-tracked files using git ls-files -z in repository root."""
    res = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return [os.fsdecode(entry) for entry in res.stdout.split(b"\0") if entry]


def redact_repo(
    repo_root: Path,
    map_path: Path,
    apply: bool = False,
    check_mode: bool = False,
    paths: list[str] | None = None,
) -> RedactStats:
    """Scan git-tracked files and replace or check values from map."""
    entries = load_and_validate_map(map_path, repo_root)

    target_paths: list[Path] | None = None
    if paths:
        target_paths = []
        for p in paths:
            user_target = Path(p)
            if not user_target.is_absolute():
                user_target = (Path.cwd() / user_target).resolve()
            else:
                user_target = user_target.resolve()
            try:
                rel = user_target.relative_to(repo_root.resolve())
                target_paths.append(rel)
            except ValueError:
                # Path outside repository
                pass

    tracked_files = get_git_tracked_files(repo_root)

    mapping = {val: plc for val, plc in entries}
    pattern: re.Pattern[str] | None = None
    if entries:
        pattern = re.compile("|".join(re.escape(val) for val, _ in entries))

    per_file_counts: dict[str, int] = {}
    total_replacements = 0
    skipped_binary = 0
    skipped_oversized = 0

    for rel_str in tracked_files:
        rel_path = Path(rel_str)
        if not _is_path_selected(rel_path, target_paths):
            continue

        full_path = repo_root / rel_path
        if not full_path.is_file():
            continue

        try:
            st = full_path.stat()
        except OSError:
            continue

        # Skip oversized files
        if st.st_size > MAX_FILE_SIZE:
            skipped_oversized += 1
            continue

        # Read bytes to check for binary content
        try:
            with open(full_path, "rb") as f:
                raw_bytes = f.read()
        except OSError:
            continue

        if b"\0" in raw_bytes:
            skipped_binary += 1
            continue

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            skipped_binary += 1
            continue

        if not pattern:
            continue

        if check_mode:
            matches = pattern.findall(text)
            count = len(matches)
            if count > 0:
                per_file_counts[rel_str] = count
                total_replacements += count
        else:
            file_count = 0

            def _repl(match: re.Match[str]) -> str:
                nonlocal file_count
                file_count += 1
                return mapping[match.group(0)]

            new_text = pattern.sub(_repl, text)
            if file_count > 0:
                per_file_counts[rel_str] = file_count
                total_replacements += file_count
                if apply:
                    with open(full_path, "w", encoding="utf-8", newline="") as f:
                        f.write(new_text)

    return RedactStats(
        per_file=per_file_counts,
        total=total_replacements,
        skipped_binary=skipped_binary,
        skipped_oversized=skipped_oversized,
    )


def resolve_map_path(arg_map: str | None) -> Path:
    """Resolve map path from CLI argument, environment, or default config."""
    if arg_map:
        return Path(arg_map).expanduser()
    env_map = os.environ.get("LLMTOOL_REDACT_MAP")
    if env_map:
        return Path(env_map).expanduser()
    return DEFAULT_MAP_PATH


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Redact known private values from git-tracked files."
    )
    parser.add_argument(
        "--map",
        help="Path to private JSON map file (default: $LLMTOOL_REDACT_MAP or ~/.config/llmtool/redact-map.json).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write replacements to matching files in place.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for occurrences and exit 1 if any found.",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        action="extend",
        help="Limit scan to specific files or directories under the repo.",
    )
    parser.add_argument(
        "--repo",
        help="Repository root path (optional, default auto-detected).",
    )

    args = parser.parse_args(argv)

    if args.apply and args.check:
        sys.stderr.write("Error: cannot combine --apply and --check\n")
        return 2

    repo_root = Path(args.repo).resolve() if args.repo else find_repo_root()
    map_path = resolve_map_path(args.map)

    try:
        stats = redact_repo(
            repo_root=repo_root,
            map_path=map_path,
            apply=args.apply,
            check_mode=args.check,
            paths=args.paths,
        )
    except RedactMapError as exc:
        sys.stderr.write(f"{exc.code}: {exc.message}\n")
        return 2

    if args.check:
        if stats.total > 0:
            for path in sorted(stats.per_file.keys()):
                print(f"{path}: {stats.per_file[path]}")
            return 1
        return 0

    # Default dry run or apply mode
    for path in sorted(stats.per_file.keys()):
        if stats.per_file[path] > 0:
            print(f"{path}: {stats.per_file[path]}")
    print(f"Total: {stats.total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
