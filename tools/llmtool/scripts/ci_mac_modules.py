#!/usr/bin/env python3
"""Select explicit unittest IDs for the Mac CI subset."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
LLMTOOL_ROOT = SCRIPT_DIR.parent
DEFAULT_REPO_ROOT = LLMTOOL_ROOT.parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))
from ci_path_classes import classify_path  # noqa: E402
from llmtool.test_selection import (  # noqa: E402
    HIGH_RISK_FILES, HIGH_RISK_PREFIXES, IMPACT_TEST_MAP,
)

MODULE_LIST = LLMTOOL_ROOT / "ci" / "mac-modules.txt"
MONITOR_PATHS = {
    "scripts/llmtool_monitor.py",
    "scripts/test_llmtool_monitor.py",
    "scripts/test_llmtool_monitor_github_reads.py",
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


def _source_module(path: str) -> str | None:
    """Return the import name for a Python module under llmtool/llmtool."""
    prefix = "tools/llmtool/llmtool/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    relative = path[len(prefix):-3]
    parts = relative.split("/")
    if parts[-1] == "__init__":
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(("llmtool", *parts))


def _imports_module(tree: ast.Module, target: str) -> bool:
    """Check imports for an exact module, including ``from parent import child``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == target for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            parent = node.module or ""
            if parent == target:
                return True
            if parent and any(f"{parent}.{alias.name}" == target for alias in node.names):
                return True
    return False


def _importing_test_ids(target: str, repo_root: Path) -> set[str] | None:
    """Find test modules that import a changed llmtool module; fail closed on I/O errors."""
    test_dir = repo_root / TEST_ROOT
    try:
        paths = sorted(test_dir.glob("test_*.py"))
    except OSError:
        return None
    selected: set[str] = set()
    for test_path in paths:
        relative = test_path.relative_to(repo_root).as_posix()
        module_id = _module_id(relative, repo_root)
        if module_id is None:
            # Non-test helper files are ignored; malformed test modules fail closed.
            try:
                tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                return None
            continue
        try:
            tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            return None
        if _imports_module(tree, target):
            selected.add(module_id)
    return selected


def _is_other_tool_markdown(path: str) -> bool:
    """Accept markdown files under tools/<name>/ where name is not llmtool."""
    parts = str(path).split("/")
    if len(parts) < 3:
        return False
    if parts[0] != "tools" or parts[1] == "llmtool":
        return False
    if not parts[-1].endswith(".md"):
        return False
    if any(p in {"", ".", ".."} for p in parts):
        return False
    return True


def select_test_ids(
    changed_paths: list[str], repo_root: Path = DEFAULT_REPO_ROOT,
    rejection_reasons: list[str] | None = None,
    rejection_details: list[dict[str, str]] | None = None,
) -> set[str] | None:
    """Return required plus impacted test IDs, or None when L3 FULL is needed.

    Import discovery sees direct static imports only; it does not find transitive,
    subprocess, or patch-string importers.
    """
    rejected = False
    rejection_count = 0

    def reject(path: str, reason: str) -> None:
        nonlocal rejected, rejection_count
        rejected = True
        rejection_count += 1
        if rejection_reasons is not None:
            if reason not in rejection_reasons:
                rejection_reasons.append(reason)
        if rejection_details is not None:
            detail = {"path": path, "reason": reason}
            if detail not in rejection_details:
                rejection_details.append(detail)

    try:
        selected = required_test_ids(repo_root)
    except InvalidManifest:
        manifest_path = "tools/llmtool/ci/mac-modules.txt"
        if changed_paths and any(
            raw.strip().replace("\\", "/").removeprefix("./") == manifest_path
            for raw in changed_paths
        ):
            reject(manifest_path, "manifest_change")
        else:
            reject(manifest_path, "invalid_manifest")
        return None
    if not changed_paths:
        reject("", "no_changed_paths")
        return None
    has_relevant_change = False
    for raw_path in changed_paths:
        rejections_before_path = rejection_count
        path = raw_path.strip().replace("\\", "/").removeprefix("./")
        if not path or "\0" in path:
            reject(path, "unsupported_path")
            continue
        path_class = classify_path(path)
        if path in MONITOR_PATHS:
            continue
        if (path_class == "docs" and path.startswith("tools/llmtool/")
                and path.endswith(".md")):
            continue
        if path_class == "runtime" and path.endswith(".md"):
            continue
        if (path_class == "workflow" or path == "tools/llmtool/ci/mac-modules.txt"
                or path in {"tools/llmtool/scripts/ci_mac_modules.py",
                            "tools/llmtool/scripts/ci_path_classes.py",
                            "tools/llmtool/llmtool/test_selection.py"}
                or "/ci/" in path or path == "tools/llmtool/Makefile"
                or "requirements" in Path(path).name):
            reason = "workflow_change" if path_class == "workflow" else (
                "selector_change" if path in {
                    "tools/llmtool/scripts/ci_mac_modules.py",
                    "tools/llmtool/llmtool/test_selection.py",
                } else "ci_or_dependency_change" if (
                    "/ci/" in path or path == "tools/llmtool/Makefile"
                    or "requirements" in Path(path).name
                ) else "manifest_change")
            reject(path, reason)
            continue
        if not path.startswith("tools/llmtool/"):
            if (path_class == "docs" and (path in {"AGENTS.md", "CLAUDE.md"}
                                           or path.startswith(("skills/", "docs/")))):
                continue
            if path_class != "workflow" and _is_other_tool_markdown(path):
                continue
            reject(path, "unsupported_path")
            continue

        if path.startswith(TEST_ROOT) and path.endswith(".py"):
            changed_id = _module_id(path, repo_root)
            if changed_id is None:
                reject(path, "missing_or_invalid_test_module")
                continue
            selected.add(changed_id)
            has_relevant_change = True
            continue

        relative = path[len("tools/llmtool/"):]
        if relative == "llmtool/__init__.py":
            reject(path, "high_risk_file")
            continue
        if path in HIGH_RISK_FILES or any(
            path == prefix or path.startswith(prefix + "/") for prefix in HIGH_RISK_PREFIXES
        ):
            reject(path, "high_risk_file")
            continue
        is_llmtool_module = (path.startswith("tools/llmtool/llmtool/")
                             and path.endswith(".py"))
        mapped = IMPACT_TEST_MAP.get(path)
        dynamic_module = _source_module(path)
        # Runtime modules are discovered from the test tree, including nested
        # packages. Other runtime inputs still need an explicit impact mapping.
        if is_llmtool_module and dynamic_module is None:
            reject(path, "unsupported_source_path")
            continue
        if not is_llmtool_module and not mapped:
            reject(path, "unmapped_source")
            continue
        if not (repo_root / path).is_file():
            reject(path, "missing_file")
            continue
        for test_path in mapped or ():
            module_id = _module_id(test_path, repo_root)
            if module_id is None:
                reject(test_path, "missing_mapped_test")
                break
            selected.add(module_id)
        if rejection_count > rejections_before_path:
            continue
        if dynamic_module is not None:
            dynamic_ids: set[str] = set()
            direct_id = "test_" + Path(path).stem
            direct_path = f"{TEST_ROOT}{direct_id}.py"
            if (repo_root / direct_path).is_file():
                module_id = _module_id(direct_path, repo_root)
                if module_id is None:
                    reject(direct_path, "missing_direct_test")
                    break
                dynamic_ids.add(module_id)
            if rejection_count > rejections_before_path:
                continue
            importing = _importing_test_ids(dynamic_module, repo_root)
            if importing is None:
                reject(path, "test_import_scan_failed")
                continue
            dynamic_ids.update(importing)
            if (Path(path).name.startswith("chat_tui")
                    or Path(path).name == "tui_markdown.py"):
                # The PTY scenario suite loads the TUI in a subprocess, so its
                # source dependency is not visible to the static import scan.
                scenario_id = "test_chat_tui_scenarios"
                if _module_id(f"{TEST_ROOT}{scenario_id}.py", repo_root) is None:
                    reject(f"{TEST_ROOT}{scenario_id}.py", "missing_scenario_test")
                    continue
                dynamic_ids.add(scenario_id)
            if not dynamic_ids and not mapped:
                reject(path, "no_discoverable_tests")
                continue
            selected.update(dynamic_ids)
        has_relevant_change = True

    if rejected:
        return None
    return selected if has_relevant_change else set()


def selection_report(
    changed_paths: list[str], repo_root: Path = DEFAULT_REPO_ROOT,
    source_head: str | None = None,
) -> dict:
    normalized = sorted(path.strip().replace("\\", "/").removeprefix("./")
                       for path in changed_paths if path.strip())
    rejection_reasons: list[str] = []
    rejection_details: list[dict[str, str]] = []
    ids = select_test_ids(changed_paths, repo_root, rejection_reasons, rejection_details)
    if ids is None:
        reasons = rejection_reasons or ["selector_rejected_without_reason"]
        reason = "selector_rejections_require_full"
        return {"changed_paths": normalized, "level": "L3", "test_ids": ["FULL"],
                "selection_reason": reason, "fallback_reasons": reasons,
                "fallback_paths": sorted({item["path"] for item in rejection_details if item["path"]}),
                "fallback_details": rejection_details,
                "test_id_count": "full-suite", "selector_revision": _selector_revision(),
                "source_head": source_head}

    if not ids:
        return {"changed_paths": normalized, "level": "L0", "test_ids": [],
                "selection_reason": "monitor or documentation-only change",
                "fallback_reasons": [], "fallback_paths": [], "fallback_details": [],
                "test_id_count": 0,
                "selector_revision": _selector_revision(), "source_head": source_head}

    # A whole-module ID already includes every class in that module. Avoid
    # running those classes twice when both forms come from the manifest,
    # shared impact map, or a directly changed test module.
    whole_modules = {test_id for test_id in ids if "." not in test_id}
    output = sorted(test_id for test_id in ids
                    if "." not in test_id or test_id.partition(".")[0] not in whole_modules)
    has_submission = any(test_id.endswith("test_task_submission_cli") for test_id in output)
    return {"changed_paths": normalized, "level": "L2" if has_submission else "L1",
            "test_ids": output, "selection_reason": "explicit Mac manifest plus shared-map impacted tests",
            "fallback_reasons": [], "fallback_paths": [], "fallback_details": [],
            "test_id_count": len(output),
            "selector_revision": _selector_revision(), "source_head": source_head}


def _selector_revision() -> str:
    """Identify the exact selector source evaluated for this report."""
    try:
        source = Path(__file__).read_bytes()
    except OSError:
        return "unavailable"
    return hashlib.sha256(source).hexdigest()


def full_fallback_report(
    changed_paths: list[str], reason: str, source_head: str | None = None,
) -> dict:
    normalized = sorted(path.strip().replace("\\", "/").removeprefix("./")
                       for path in changed_paths if path.strip())
    return {"changed_paths": normalized, "level": "L3", "test_ids": ["FULL"],
            "selection_reason": "selector_failure_requires_full",
            "fallback_reasons": [reason], "fallback_paths": [], "fallback_details": [],
            "test_id_count": "full-suite",
            "selector_revision": _selector_revision(), "source_head": source_head}


def _read_changed_paths(path: str) -> list[str]:
    return [entry.decode("utf-8") for entry in Path(path).read_bytes().split(b"\0") if entry]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-paths", required=True, help="NUL-separated changed repository paths")
    parser.add_argument("--source-head", help="source/candidate HEAD evaluated by the selector")
    parser.add_argument("--json-report", action="store_true", help="emit selection metadata as JSON")
    args = parser.parse_args(argv)
    try:
        changed_paths = _read_changed_paths(args.changed_paths)
    except (OSError, UnicodeDecodeError):
        changed_paths = []
        report = full_fallback_report(changed_paths, "changed_paths_unreadable", args.source_head)
    else:
        try:
            report = selection_report(changed_paths, source_head=args.source_head)
        except Exception:
            report = full_fallback_report(changed_paths, "selector_internal_error", args.source_head)
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
