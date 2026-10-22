"""Deterministic, advisory test-tier selection (TEST-1).

This module builds a versioned test plan. It deliberately does not execute any
commands or claim that a CI/test pipeline consumes the plan.
"""

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath


PLAN_SCHEMA_VERSION = 1
IMPACT_MAP_VERSION = 1
REQUIRED_GATES = ("exact_head_ci", "security", "mac_native")

# Keep this explicit and conservative. A changed source file without an exact
# entry falls back to the full check rather than guessing at test coverage.
IMPACT_TEST_MAP = {
    "tools/llmtool/llmtool/review_exchange.py": (
        "tools/llmtool/tests/test_review_exchange.py",
    ),
    "tools/llmtool/llmtool/task_lifecycle.py": (
        "tools/llmtool/tests/test_task_lifecycle.py",
        "tools/llmtool/tests/test_task_lifecycle_cli.py",
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

_LLMTOOL_CWD = "tools/llmtool"
_L0_COMMAND = ("make", "syntax")
_L3_COMMAND = ("make", "check")
_TEST_ROOT = "tools/llmtool/tests/"


def _relative_path(value: object, *, test_path: bool = False) -> str:
    """Validate one repository-relative POSIX path without filesystem access."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("paths must be non-empty strings without NUL")
    if ("\\" in value or value.startswith("/")
            or (len(value) >= 2 and value[1] == ":")):
        raise ValueError("paths must be relative POSIX paths")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("paths must not contain empty, dot, or parent components")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise ValueError("paths must use canonical repository-relative form")
    if test_path and (path.parent.as_posix() != _TEST_ROOT.rstrip("/")
                      or not path.name.startswith("test_")
                      or path.suffix != ".py"):
        raise ValueError("regression tests must be flat Python test files under tools/llmtool/tests")
    return value


def _path_list(values: Sequence[str], *, test_path: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("paths must be a sequence of relative path strings")
    return tuple(sorted({_relative_path(item, test_path=test_path) for item in values}))


def _under_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _test_command(path: str) -> tuple[str, ...]:
    relative_to_package = path.removeprefix("tools/llmtool/")
    return ("python3", "-m", "unittest", relative_to_package)


def select_test_plan(
    changed_paths: Sequence[str],
    *,
    dependency_regression_tests: Sequence[str] = (),
    impact_test_map: Mapping[str, Sequence[str]] | None = None,
    impact_map_version: int | None = IMPACT_MAP_VERSION,
) -> dict:
    """Return a deterministic L0-L3 test plan for validated relative paths.

    Unknown paths, a stale map, and high-risk changes select L3 (`make check`).
    The L1 map is exact-file based; L2 tests must be supplied explicitly by the
    caller. Test-map entries must use the repository's flat tests directory;
    L1/L2 commands name one exact file, never a discovery pattern. This
    function never invokes a test runner.
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
                 if any(_under_prefix(path, prefix) for prefix in HIGH_RISK_PREFIXES)]
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
