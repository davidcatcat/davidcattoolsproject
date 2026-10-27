#!/usr/bin/env python3
"""Select Mac CI test modules from runtime discovery and changed paths."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
LLMTOOL_ROOT = SCRIPT_DIR.parent
DEFAULT_REPO_ROOT = LLMTOOL_ROOT.parents[1]
MODULE_LIST = LLMTOOL_ROOT / "ci" / "mac-modules.txt"
MONITOR_PATHS = {
    "scripts/llmtool_monitor.py",
    "scripts/test_llmtool_monitor.py",
}
MAC_RELEVANT_TEST = re.compile(
    r"darwin|sys\.platform|platform\.system|macOS|launchctl|sandbox-exec|"
    r"keychain|Keychain|/Applications|killpg|setsid|os\.setpgid|getpgid|start_new_session",
    re.IGNORECASE,
)
SHARED_MODULES = {
    "llmtool/__init__.py",
    "llmtool/cli.py",
    "llmtool/task_store.py",
    "llmtool/task_submission_cli.py",
}


def extra_modules() -> set[str]:
    return {
        line.partition("#")[0].strip()
        for line in MODULE_LIST.read_text(encoding="utf-8").splitlines()
        if line.partition("#")[0].strip()
    }


def scanned_test_modules(repo_root: Path = DEFAULT_REPO_ROOT) -> set[str]:
    tests = repo_root / "tools" / "llmtool" / "tests"
    return {
        path.stem
        for path in tests.glob("test_*.py")
        if MAC_RELEVANT_TEST.search(path.read_text(encoding="utf-8"))
    }


def select_modules(changed_paths: list[str], repo_root: Path = DEFAULT_REPO_ROOT) -> set[str] | None:
    """Return selected test names, or None when full coverage is required."""
    selected = scanned_test_modules(repo_root) | extra_modules()
    test_dir = repo_root / "tools" / "llmtool" / "tests"

    for raw_path in changed_paths:
        path = raw_path.strip().replace("\\", "/").removeprefix("./")
        if not path:
            return None
        if path in MONITOR_PATHS:
            continue
        if (
            not path.startswith("tools/llmtool/")
            or path == "tools/llmtool/Makefile"
            or "requirements" in Path(path).name
            or "/ci/" in path
            or path.startswith(".github/workflows/")
        ):
            return None

        prefix = "tools/llmtool/"
        relative = path[len(prefix):]
        if relative in SHARED_MODULES:
            return None
        if relative.startswith("tests/test_") and relative.endswith(".py"):
            if not (repo_root / path).is_file():
                return None
            selected.add(Path(relative).stem)
            continue

        source_parts = ("llmtool/", "scripts/")
        source_name = next(
            (relative[len(part):] for part in source_parts
             if relative.startswith(part) and relative.endswith(".py")),
            None,
        )
        if source_name is None or "/" in source_name:
            return None
        for test_path in test_dir.glob(f"test_{Path(source_name).stem}*.py"):
            selected.add(test_path.stem)

    return selected


def _read_changed_paths(path: str) -> list[str]:
    return [entry.decode("utf-8") for entry in Path(path).read_bytes().split(b"\0") if entry]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-paths", required=True, help="file with one changed repo path per line")
    args = parser.parse_args(argv)
    selected = select_modules(_read_changed_paths(args.changed_paths))
    if not selected:
        print("FULL")
        return 0
    for module in sorted(selected):
        print(module)
    return 0


if __name__ == "__main__":
    sys.exit(main())
