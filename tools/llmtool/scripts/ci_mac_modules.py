#!/usr/bin/env python3
"""Select Mac CI test modules from runtime discovery and changed paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
LLMTOOL_ROOT = SCRIPT_DIR.parent
DEFAULT_REPO_ROOT = LLMTOOL_ROOT.parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))
from llmtool.test_selection import IMPACT_TEST_MAP  # noqa: E402
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
    has_mac_relevant_change = False

    for raw_path in changed_paths:
        path = raw_path.strip().replace("\\", "/").removeprefix("./")
        if not path:
            return None
        if path in MONITOR_PATHS:
            continue
        if path.startswith("tools/llmtool/") and path.endswith(".md"):
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
        has_mac_relevant_change = True
        if relative == "llmtool/__init__.py":
            return None
        if relative.startswith("tests/test_") and relative.endswith(".py"):
            if not (repo_root / path).is_file():
                return None
            changed_test = repo_root / path
            try:
                is_mac_relevant = MAC_RELEVANT_TEST.search(changed_test.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                return None
            if is_mac_relevant:
                selected.add(Path(relative).stem)
            continue

        is_runtime_source = (
            path.startswith(("tools/llmtool/llmtool/", "tools/llmtool/scripts/"))
            and path.endswith((".py", ".json"))
        )
        if is_runtime_source and path not in IMPACT_TEST_MAP:
            return None

        source_parts = ("llmtool/", "scripts/")
        source_name = next(
            (relative[len(part):] for part in source_parts
             if relative.startswith(part) and relative.endswith(".py")),
            None,
        )
        if source_name is None or "/" in source_name:
            # Exact shared impact-map entries may identify nested sources.
            # Everything else remains conservative FULL.
            if path not in IMPACT_TEST_MAP:
                return None
        if not (repo_root / path).is_file():
            return None
        mapped_tests = IMPACT_TEST_MAP.get(path, ())
        if any(not (repo_root / test_path).is_file() for test_path in mapped_tests):
            return None
        selected.update(Path(test_path).stem for test_path in mapped_tests)

    if has_mac_relevant_change and not selected:
        return None
    return selected if has_mac_relevant_change else set()


def selection_report(changed_paths: list[str], repo_root: Path = DEFAULT_REPO_ROOT) -> dict:
    """Describe Mac selection with the shared TEST-1 impact map."""
    normalized = sorted(path.strip().replace("\\", "/").removeprefix("./")
                       for path in changed_paths if path.strip())
    modules = select_modules(changed_paths, repo_root)
    if modules is None:
        if any(path.startswith(".github/workflows/") for path in normalized):
            reason = "workflow_change_requires_full"
        elif any("requirements" in Path(path).name or "/ci/" in path for path in normalized):
            reason = "dependency_or_ci_change_requires_full"
        elif any(path.startswith("tools/llmtool/llmtool/providers/") for path in normalized):
            reason = "high_risk_provider_path_requires_full"
        elif any(path not in IMPACT_TEST_MAP and path.endswith((".py", ".json"))
                 and path.startswith("tools/llmtool/") for path in normalized):
            reason = "unmapped_or_nested_source_requires_full"
        elif any(test_path and not (repo_root / test_path).is_file()
                 for path in normalized for test_path in IMPACT_TEST_MAP.get(path, ())):
            reason = "deleted_mapped_test_requires_full"
        elif any(not (repo_root / path).is_file() for path in normalized
                 if path.startswith("tools/llmtool/")):
            reason = "deleted_or_missing_source_or_test_requires_full"
        else:
            reason = "unsupported_or_high_risk_path_requires_full"
        level = "L3"
        output_modules = ["FULL"]
        module_count = "full-suite"
    elif not modules:
        reason = "monitor or documentation-only change"
        level = "L0"
        output_modules = []
        module_count = 0
    else:
        reason = "Mac baseline plus shared-map impacted tests"
        level = "L2" if any(
            "tools/llmtool/tests/test_task_submission_cli.py"
            in IMPACT_TEST_MAP.get(path, ())
            for path in normalized
        ) else "L1"
        output_modules = sorted(modules)
        module_count = len(output_modules)
    return {
        "changed_paths": normalized,
        "level": level,
        "modules": output_modules,
        "selection_reason": reason,
        "fallback_reasons": ([reason] if modules is None else []),
        "module_count": module_count,
    }


def full_fallback_report(changed_paths: list[str], reason: str) -> dict:
    """Build telemetry when selector execution or report parsing fails."""
    normalized = sorted(path.strip().replace("\\", "/").removeprefix("./")
                       for path in changed_paths if path.strip())
    return {
        "changed_paths": normalized,
        "level": "L3",
        "modules": ["FULL"],
        "selection_reason": "selector_failure_requires_full",
        "fallback_reasons": [reason],
        "module_count": "full-suite",
    }


def _read_changed_paths(path: str) -> list[str]:
    return [entry.decode("utf-8") for entry in Path(path).read_bytes().split(b"\0") if entry]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-paths", required=True, help="file with one changed repo path per line")
    parser.add_argument("--json-report", action="store_true", help="emit selection metadata as JSON")
    args = parser.parse_args(argv)
    try:
        changed_paths = _read_changed_paths(args.changed_paths)
    except (OSError, UnicodeDecodeError):
        if args.json_report:
            print(json.dumps(full_fallback_report([], "changed_paths_unreadable"), sort_keys=True))
        else:
            print("FULL")
        return 0
    if args.json_report:
        try:
            report = selection_report(changed_paths)
        except Exception:
            report = full_fallback_report(changed_paths, "selector_internal_error")
        print(json.dumps(report, sort_keys=True))
        return 0
    selected = select_modules(changed_paths)
    if selected is None:
        print("FULL")
        return 0
    if not selected:
        print("NONE")
        return 0
    for module in sorted(selected):
        print(module)
    return 0


if __name__ == "__main__":
    sys.exit(main())
