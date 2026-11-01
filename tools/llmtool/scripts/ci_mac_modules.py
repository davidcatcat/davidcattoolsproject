#!/usr/bin/env python3
"""Select explicit unittest IDs for the Mac CI subset."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
LLMTOOL_ROOT = SCRIPT_DIR.parent
DEFAULT_REPO_ROOT = LLMTOOL_ROOT.parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))
from llmtool.test_selection import (  # noqa: E402
    HIGH_RISK_FILES, HIGH_RISK_PREFIXES, IMPACT_TEST_MAP,
)

MODULE_LIST = LLMTOOL_ROOT / "ci" / "mac-modules.txt"
MONITOR_PATHS = {
    "scripts/llmtool_monitor.py",
    "scripts/test_llmtool_monitor.py",
}
TEST_ROOT = "tools/llmtool/tests/"


class InvalidManifest(ValueError):
    """The committed Mac-required unittest-ID manifest is invalid."""


def _valid_test_id(test_id: str, repo_root: Path) -> bool:
    """Accept one flat test module or an explicitly named top-level TestCase."""
    parts = test_id.split(".")
    if len(parts) not in (1, 2) or not parts[0].startswith("test_"):
        return False
    if not all(part.isidentifier() for part in parts):
        return False
    test_path = repo_root / TEST_ROOT / (parts[0] + ".py")
    if not test_path.is_file():
        return False
    try:
        tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return False
    if len(parts) == 1:
        return any(isinstance(node, ast.ClassDef) and _is_test_case(node, tree)
                   for node in tree.body) or any(
                       isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and node.name.startswith("test_") for node in tree.body
                   )
    return any(isinstance(node, ast.ClassDef) and node.name == parts[1]
               and _is_test_case(node, tree) for node in tree.body)


def _is_test_case(class_node: ast.ClassDef, module: ast.Module) -> bool:
    """Recognize unittest.TestCase bases, including imported and aliased forms."""
    unittest_names = {"unittest"}
    testcase_names = {"TestCase"}
    for node in module.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "unittest":
                    unittest_names.add(alias.asname or "unittest")
        elif isinstance(node, ast.ImportFrom) and node.module == "unittest":
            for alias in node.names:
                if alias.name == "TestCase":
                    testcase_names.add(alias.asname or alias.name)
    for base in class_node.bases:
        if isinstance(base, ast.Name) and base.id in testcase_names:
            return True
        if (isinstance(base, ast.Attribute) and base.attr == "TestCase"
                and isinstance(base.value, ast.Name) and base.value.id in unittest_names):
            return True
    return False


def required_test_ids(repo_root: Path = DEFAULT_REPO_ROOT) -> set[str]:
    """Read and validate the explicit Mac-required test-ID manifest."""
    manifest_path = MODULE_LIST
    if repo_root != DEFAULT_REPO_ROOT:
        manifest_path = repo_root / "tools" / "llmtool" / "ci" / "mac-modules.txt"
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidManifest("manifest_unreadable") from exc
    ids = []
    for line in lines:
        value = line.partition("#")[0].strip()
        if not value:
            continue
        if not _valid_test_id(value, repo_root):
            raise InvalidManifest("manifest_invalid_test_id")
        ids.append(value)
    if not ids or len(ids) != len(set(ids)):
        raise InvalidManifest("manifest_empty_or_duplicate")
    return set(ids)


def _module_id(path: str, repo_root: Path) -> str | None:
    if not path.startswith(TEST_ROOT) or not path.endswith(".py"):
        return None
    relative = path[len(TEST_ROOT):]
    if "/" in relative or not relative.startswith("test_"):
        return None
    module = relative[:-3]
    test_path = repo_root / path
    if not module.isidentifier() or not test_path.is_file():
        return None
    try:
        tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    if not (any(isinstance(node, ast.ClassDef) and _is_test_case(node, tree)
                for node in tree.body) or any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_") for node in tree.body
                )):
        return None
    return module


def select_test_ids(changed_paths: list[str], repo_root: Path = DEFAULT_REPO_ROOT) -> set[str] | None:
    """Return required plus impacted test IDs, or None when L3 FULL is needed."""
    try:
        selected = required_test_ids(repo_root)
    except InvalidManifest:
        return None
    if not changed_paths:
        return None
    has_relevant_change = False
    for raw_path in changed_paths:
        path = raw_path.strip().replace("\\", "/").removeprefix("./")
        if not path or "\0" in path:
            return None
        if path in MONITOR_PATHS:
            continue
        if path.startswith("tools/llmtool/") and path.endswith(".md"):
            continue
        if (path.startswith(".github/workflows/") or path == "tools/llmtool/ci/mac-modules.txt"
                or path in {"tools/llmtool/scripts/ci_mac_modules.py",
                            "tools/llmtool/llmtool/test_selection.py"}
                or "/ci/" in path or path == "tools/llmtool/Makefile"
                or "requirements" in Path(path).name):
            return None
        if not path.startswith("tools/llmtool/"):
            return None

        if path.startswith(TEST_ROOT) and path.endswith(".py"):
            changed_id = _module_id(path, repo_root)
            if changed_id is None:
                return None
            selected.add(changed_id)
            has_relevant_change = True
            continue

        relative = path[len("tools/llmtool/"):]
        if relative == "llmtool/__init__.py":
            return None
        if path in HIGH_RISK_FILES or any(
            path == prefix or path.startswith(prefix + "/") for prefix in HIGH_RISK_PREFIXES
        ):
            return None
        is_runtime_source = (path.startswith(("tools/llmtool/llmtool/", "tools/llmtool/scripts/"))
                             and path.endswith((".py", ".json")))
        if is_runtime_source and path not in IMPACT_TEST_MAP:
            return None
        mapped = IMPACT_TEST_MAP.get(path)
        if not mapped or not (repo_root / path).is_file():
            return None
        for test_path in mapped:
            module_id = _module_id(test_path, repo_root)
            if module_id is None:
                return None
            selected.add(module_id)
        has_relevant_change = True

    return selected if has_relevant_change else set()


def selection_report(changed_paths: list[str], repo_root: Path = DEFAULT_REPO_ROOT) -> dict:
    normalized = sorted(path.strip().replace("\\", "/").removeprefix("./")
                       for path in changed_paths if path.strip())
    ids = select_test_ids(changed_paths, repo_root)
    if ids is None:
        try:
            required_test_ids(repo_root)
            manifest_valid = True
        except InvalidManifest:
            manifest_valid = False
        if not manifest_valid:
            reason = "invalid_manifest_requires_full"
        elif any(path == "tools/llmtool/ci/mac-modules.txt" for path in normalized):
            reason = "manifest_change_requires_full"
        elif not normalized:
            reason = "no_changed_paths_requires_full"
        elif any(path.startswith(".github/workflows/") for path in normalized):
            reason = "workflow_change_requires_full"
        elif any(path in {"tools/llmtool/scripts/ci_mac_modules.py",
                          "tools/llmtool/llmtool/test_selection.py"} for path in normalized):
            reason = "selector_change_requires_full"
        elif any("requirements" in Path(path).name or "/ci/" in path for path in normalized):
            reason = "dependency_or_ci_change_requires_full"
        elif any(path not in IMPACT_TEST_MAP and path.endswith((".py", ".json"))
                 and path.startswith("tools/llmtool/") for path in normalized):
            reason = "unmapped_or_nested_source_requires_full"
        elif any(test_path and _module_id(test_path, repo_root) is None
                 for path in normalized for test_path in IMPACT_TEST_MAP.get(path, ())):
            reason = "deleted_mapped_test_requires_full"
        else:
            reason = "unsupported_or_high_risk_or_invalid_manifest_requires_full"
        return {"changed_paths": normalized, "level": "L3", "test_ids": ["FULL"],
                "selection_reason": reason, "fallback_reasons": [reason],
                "test_id_count": "full-suite"}

    if not ids:
        return {"changed_paths": normalized, "level": "L0", "test_ids": [],
                "selection_reason": "monitor or documentation-only change",
                "fallback_reasons": [], "test_id_count": 0}

    output = sorted(ids)
    has_submission = any(test_id.endswith("test_task_submission_cli") for test_id in output)
    return {"changed_paths": normalized, "level": "L2" if has_submission else "L1",
            "test_ids": output, "selection_reason": "explicit Mac manifest plus shared-map impacted tests",
            "fallback_reasons": [], "test_id_count": len(output)}


def full_fallback_report(changed_paths: list[str], reason: str) -> dict:
    normalized = sorted(path.strip().replace("\\", "/").removeprefix("./")
                       for path in changed_paths if path.strip())
    return {"changed_paths": normalized, "level": "L3", "test_ids": ["FULL"],
            "selection_reason": "selector_failure_requires_full",
            "fallback_reasons": [reason], "test_id_count": "full-suite"}


def _read_changed_paths(path: str) -> list[str]:
    return [entry.decode("utf-8") for entry in Path(path).read_bytes().split(b"\0") if entry]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-paths", required=True, help="NUL-separated changed repository paths")
    parser.add_argument("--json-report", action="store_true", help="emit selection metadata as JSON")
    args = parser.parse_args(argv)
    try:
        changed_paths = _read_changed_paths(args.changed_paths)
    except (OSError, UnicodeDecodeError):
        changed_paths = []
        report = full_fallback_report(changed_paths, "changed_paths_unreadable")
    else:
        try:
            report = selection_report(changed_paths)
        except Exception:
            report = full_fallback_report(changed_paths, "selector_internal_error")
    if args.json_report:
        print(json.dumps(report, sort_keys=True))
    elif report["level"] == "L3":
        print("FULL")
    else:
        for test_id in report["test_ids"]:
            print(test_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
