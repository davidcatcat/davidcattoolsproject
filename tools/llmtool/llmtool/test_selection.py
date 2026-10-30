"""Deterministic, advisory test-tier selection (TEST-1).

This module builds a versioned test plan. It deliberately does not execute any
commands or claim that a CI/test pipeline consumes the plan.
"""

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Optional


PLAN_SCHEMA_VERSION = 1
IMPACT_MAP_VERSION = 1
REQUIRED_GATES = ("exact_head_ci", "security", "mac_native")

# Keep this explicit and conservative. A changed source file without an exact
# entry falls back to the full check rather than guessing at test coverage.
IMPACT_TEST_MAP = {
    # Keep Mac selection and TEST-1 aligned on the real AGY implementation
    # paths and the existing focused tests.
    "tools/llmtool/llmtool/developer_routes/agy.py": (
        "tools/llmtool/tests/test_developer_route_agy.py",
        "tools/llmtool/tests/test_task_submission_cli.py",
    ),
    "tools/llmtool/llmtool/task_submission_cli.py": (
        "tools/llmtool/tests/test_developer_route_agy.py",
        "tools/llmtool/tests/test_task_submission_cli.py",
    ),
    "tools/llmtool/developer-routes.json": (
        "tools/llmtool/tests/test_developer_route_agy.py",
    ),
    "tools/llmtool/scripts/ci_mac_modules.py": (
        "tools/llmtool/tests/test_ci_mac_modules.py",
    ),
    "tools/llmtool/llmtool/review_exchange.py": (
        "tools/llmtool/tests/test_review_exchange.py",
    ),
    "tools/llmtool/llmtool/task_lifecycle.py": (
        "tools/llmtool/tests/test_task_lifecycle.py",
        "tools/llmtool/tests/test_task_lifecycle_cli.py",
    ),
    "tools/llmtool/llmtool/task_store.py": (
        "tools/llmtool/tests/test_task_store.py",
    ),
    "tools/llmtool/llmtool/test_output_excerpts.py": (
        "tools/llmtool/tests/test_test_output_excerpts.py",
    ),
    "tools/llmtool/llmtool/test_selection.py": (
        "tools/llmtool/tests/test_test_selection.py",
    ),
}

# Changes in these areas receive the full repository check even if a direct
# test mapping exists. Prefix matches are component-aware.
HIGH_RISK_PREFIXES = (
    "tools/llmtool/llmtool/actions",
    "tools/llmtool/llmtool/authorization",
    "tools/llmtool/llmtool/execution",
    "tools/llmtool/llmtool/git",
    "tools/llmtool/llmtool/providers",
    "tools/llmtool/llmtool/security",
)
HIGH_RISK_FILES = frozenset({
    "tools/llmtool/llmtool/authorization_ledger.py",
    "tools/llmtool/llmtool/authorization_policy.py",
    "tools/llmtool/llmtool/git_intel.py",
    "tools/llmtool/llmtool/opencode_foreground_host.py",
    "tools/llmtool/llmtool/opencode_direct_developer.py",
    "tools/llmtool/llmtool/codex_direct_developer.py",
    "tools/llmtool/llmtool/opencode_native_credential.py",
    "tools/llmtool/llmtool/opencode_worker.py",
    "tools/llmtool/llmtool/review_authority.py",
    "tools/llmtool/llmtool/review_authority_cli.py",
    "tools/llmtool/llmtool/test_output_excerpts.py",
})

_LLMTOOL_CWD = "tools/llmtool"
_L0_COMMAND = ("make", "syntax")
_L3_COMMAND = ("make", "check")
_TEST_ROOT = "tools/llmtool/tests/"


def _relative_path(value: object, *, test_path: bool = False) -> str:
    """Validate one repository-relative POSIX path without filesystem access."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("paths must be non-empty strings without NUL")
    if any(character in value for character in "*?[]"):
        raise ValueError("paths must be literal and must not contain glob characters")
    if ("\\" in value or value.startswith("/")
            or (len(value) >= 2 and value[1] == ":")):
        raise ValueError("paths must be relative POSIX paths")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("paths must not contain empty, dot, or parent components")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise ValueError("paths must use canonical repository-relative form")
    if test_path:
        filename = path.name
        module_name = (filename[len("test_"):-len(".py")]
                       if filename.startswith("test_") and filename.endswith(".py")
                       else "")
        if (path.parent.as_posix() != _TEST_ROOT.rstrip("/")
                or not module_name
                or not module_name.isidentifier()):
            raise ValueError(
                "regression tests must be flat test_<identifier>.py files under tools/llmtool/tests"
            )
    return value


def _path_list(values: Sequence[str], *, test_path: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("paths must be a sequence of relative path strings")
    return tuple(sorted({_relative_path(item, test_path=test_path) for item in values}))


def _under_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _test_command(path: str) -> tuple[str, ...]:
    # Import one validated module by its exact name. unittest discovery's -p
    # pattern can match the same basename in nested directories and silently
    # expand the selected set.
    module_name = PurePosixPath(path).stem
    module_name = module_name[len("test_"):]
    runner = (
        "import sys; sys.path.insert(0, 'tests'); "
        "import unittest; unittest.main(module='test_" + module_name + "')"
    )
    return ("python3", "-c", runner)


def select_test_plan(
    changed_paths: Sequence[str],
    *,
    dependency_regression_tests: Sequence[str] = (),
    impact_test_map: Optional[Mapping[str, Sequence[str]]] = None,
    impact_map_version: Optional[int] = IMPACT_MAP_VERSION,
) -> dict:
    """Return a deterministic L0-L3 test plan for validated relative paths.

    Unknown paths, a stale map, and high-risk changes select L3 (`make check`).
    The L1 map is exact-file based; L2 tests must be supplied explicitly by the
    caller. Test-map entries must use the repository's flat tests directory.
    L1/L2 commands invoke one literal test file, avoiding discovery-pattern
    expansion. This function never invokes a runner.
    """
    changed = _path_list(changed_paths)
    dependency_tests = _path_list(dependency_regression_tests, test_path=True)
    mapping = IMPACT_TEST_MAP if impact_test_map is None else impact_test_map

    # Validate the supplied map even when a full-check fallback is already
    # indicated. Invalid command paths are input errors, never executable plans.
    normalized_map = {}
    if not isinstance(mapping, Mapping):
        raise ValueError("impact_test_map must be a mapping")
    for source, tests in mapping.items():
        source_path = _relative_path(source)
        test_paths = _path_list(tests, test_path=True)
        if not test_paths:
            raise ValueError("each mapped source path must select at least one regression test")
        normalized_map[source_path] = test_paths

    reasons = []
    if not changed:
        reasons.append("no_changed_paths")
    if (type(impact_map_version) is not int
            or impact_map_version != IMPACT_MAP_VERSION):
        reasons.append("stale_impact_map")

    high_risk = [path for path in changed
                 if path in HIGH_RISK_FILES
                 or any(_under_prefix(path, prefix) for prefix in HIGH_RISK_PREFIXES)]
    if high_risk:
        reasons.append("high_risk_path")

    unknown = [path for path in changed if path not in normalized_map]
    if unknown:
        reasons.append("unmapped_path")

    selected = sorted({test for path in changed
                       for test in normalized_map.get(path, ())})

    levels = [{"level": "L0", "kind": "static", "cwd": _LLMTOOL_CWD,
               "command": list(_L0_COMMAND)}]
    if reasons:
        levels.append({"level": "L3", "kind": "full_check", "cwd": _LLMTOOL_CWD,
                       "command": list(_L3_COMMAND)})
    else:
        if selected:
            levels.append({"level": "L1", "kind": "impacted_tests", "cwd": _LLMTOOL_CWD,
                           "tests": selected,
                           "commands": [{"argv": list(_test_command(path)),
                                         "cwd": _LLMTOOL_CWD} for path in selected]})
        if dependency_tests:
            levels.append({"level": "L2", "kind": "dependency_regression_tests",
                           "cwd": _LLMTOOL_CWD,
                           "tests": list(dependency_tests),
                           "commands": [{"argv": list(_test_command(path)),
                                         "cwd": _LLMTOOL_CWD}
                                        for path in dependency_tests]})

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "impact_map_version": impact_map_version,
        "changed_paths": list(changed),
        "levels": levels,
        "fallback_reasons": sorted(set(reasons)),
        "required_gates": list(REQUIRED_GATES),
        "executes_tests": False,
        "pipeline_integrated": False,
    }
