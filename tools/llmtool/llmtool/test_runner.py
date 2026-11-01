"""Execute the deterministic TEST-1 plan for a registered developer checkout.

This is an iteration aid. `make check` remains the full release/CI gate and
the selector's required security, exact-head CI, and Mac-native gates are not
altered by this runner.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from .test_run_summary import summarize_test_run
from .test_selection import select_test_plan


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RAN = re.compile(rb"\bRan ([0-9]+) tests?\b")
_RESULT = re.compile(rb"^(OK|FAILED)(?: \(([^\r\n]*)\))?\s*$", re.MULTILINE)
_COUNT = re.compile(rb"(failures|errors|skipped)=([0-9]+)")
_FAILURE = re.compile(
    rb"^(?:FAIL|ERROR): ([A-Za-z_][A-Za-z0-9_.-]*) "
    rb"\((tests(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\.([A-Za-z_][A-Za-z0-9_]*)\)$",
    re.MULTILINE,
)


class RunnerError(Exception):
    """A safe, bounded failure while resolving or running the selected plan."""


GIT_TIMEOUT_SECONDS = 120


def _git(root, *args):
    # A git lock or a stuck filesystem must not hang test-scope resolution.
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RunnerError("git command failed while resolving test scope") from None
    if result.returncode:
        raise RunnerError("git command failed while resolving test scope")
    return result.stdout


def _repository_root(cwd):
    raw = _git(cwd, "rev-parse", "--show-toplevel")
    return Path(os.fsdecode(raw.rstrip(b"\n"))).resolve(strict=True)


def _commit(root, value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise RunnerError("base must be a full lowercase 40-character commit SHA")
    _git(root, "cat-file", "-e", value + "^{commit}")
    head = os.fsdecode(_git(root, "rev-parse", "HEAD").strip())
    if not _SHA.fullmatch(head):
        raise RunnerError("could not resolve a full current HEAD")
    return head


def collect_changed_paths(root, base_sha):
    """Collect tracked worktree changes plus untracked non-ignored paths."""
    tracked = _git(root, "diff", "--name-only", "-z", "--no-renames",
                   base_sha, "--")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    paths = set()
    for raw in (tracked, untracked):
        for item in raw.split(b"\0"):
            if item:
                paths.add(os.fsdecode(item))
    return sorted(paths)


def _counts_and_failures(log, exit_status):
    ran = _RAN.search(log)
    tests_run = int(ran.group(1)) if ran else 0
    failures = errors = skipped = 0
    result = _RESULT.search(log)
    if result and result.group(2):
        for name, count in _COUNT.findall(result.group(2)):
            value = int(count)
            if name == b"failures":
                failures = value
            elif name == b"errors":
                errors = value
            else:
                skipped = value
    if not exit_status and tests_run and not result:
        # Do not infer a passing test count from an unrecognized output format.
        tests_run = 0
    passed = max(0, tests_run - failures - errors - skipped)
    failure_ids = []
    if failures + errors:
        for name, module, test_class in _FAILURE.findall(log):
            failure_ids.append(
                os.fsdecode(module) + "::" + os.fsdecode(test_class) + "." +
                os.fsdecode(name)
            )
    return {
        "counts": {"tests_run": tests_run, "passed": passed,
                   "failed": failures, "errors": errors, "skipped": skipped},
        "failure_ids": sorted(set(failure_ids)),
    }


def _private_log_root(value=None):
    if value is None:
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        value = state / "llmtool" / "test-runs"
    path = Path(value).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (not path.is_dir() or path.is_symlink() or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RunnerError("test log directory must be owner-only and non-symlink")
    run_dir = path / ("run-" + uuid.uuid4().hex)
    run_dir.mkdir(mode=0o700)
    return run_dir


def _run_logged(run_dir, ordinal, runner, command, **kwargs):
    path = run_dir / ("%03d.log" % ordinal)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    digest = hashlib.sha256()
    with os.fdopen(fd, "w+b") as stream:
        result = runner(command, stdout=stream, stderr=subprocess.STDOUT,
                        check=False, **kwargs)
        stream.flush()
        stream.seek(0)
        # Retain the entire log on disk; keep only a bounded tail in memory for
        # parsing unittest's final counts and any failure IDs near the summary.
        tail = bytearray()
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
            tail.extend(chunk)
            if len(tail) > 262144:
                del tail[:-262144]
    return result, bytes(tail), path, "sha256:" + digest.hexdigest()


def _plan_commands(plan):
    commands = []
    for level in plan["levels"]:
        if level["level"] == "L0":
            commands.append((level["command"], level["cwd"], "L0"))
        elif level["level"] == "L3":
            commands.append((level["command"], level["cwd"], "L3"))
        else:
            for item in level["commands"]:
                commands.append((item["argv"], item["cwd"], level["level"]))
    return commands


def run_plan(base_sha, *, cwd=None, dependency_tests=(), log_dir=None,
             runner=subprocess.run):
    cwd = Path(cwd or Path.cwd()).resolve(strict=True)
    root = _repository_root(cwd)
    head = _commit(root, base_sha)
    changed = collect_changed_paths(root, base_sha)
    plan = select_test_plan(changed, dependency_regression_tests=dependency_tests)
    run_dir = _private_log_root(log_dir)

    # L0 includes a deterministic whitespace/diff check in addition to the
    # selector's compile/syntax command. Full `make check` remains L3.
    diff_result, _diff_log, diff_path, diff_digest = _run_logged(
        run_dir, 0, runner,
        ["git", "-C", str(root), "diff", "--check", base_sha, "--"],
    )
    execution = [{"kind": "diff_check",
                  "argv": ["git", "-C", str(root), "diff", "--check", base_sha, "--"],
                  "exit_status": diff_result.returncode,
                  "log_sha256": diff_digest, "log_path": str(diff_path)}]

    receipts = []
    test_commands = _plan_commands(plan)
    for ordinal, (argv, relative_cwd, level) in enumerate(test_commands, start=1):
        command_cwd = root / relative_cwd
        command = list(argv)
        # Keep selector's fixed argv while ensuring python3 (including a
        # nested `make check`) resolves to this task's interpreter.
        env = os.environ.copy()
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        result, log, log_path, log_digest = _run_logged(
            run_dir, ordinal, runner, command, cwd=command_cwd, env=env)
        details = _counts_and_failures(log, result.returncode)
        execution.append({"kind": level, "argv": command,
                          "exit_status": result.returncode,
                          "log_sha256": log_digest, "log_path": str(log_path),
                          **details})
        receipts.append({
            "argv_sha256": "sha256:" + hashlib.sha256(
                json.dumps(argv, ensure_ascii=True, separators=(",", ":"),
                           allow_nan=False).encode("ascii")).hexdigest(),
            "exit_status": result.returncode,
            "log_sha256": log_digest,
            "log_ref": log_digest,
            **details,
        })

    summary = None
    try:
        summary = summarize_test_run(
            plan,
            {"schema_version": 1, "head_sha": head,
             "plan_sha256": "sha256:" + hashlib.sha256(
                 json.dumps(plan, ensure_ascii=True, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest(),
             "commands": receipts},
            expected_head=head,
        )
    except ValueError:
        # L3/high-risk and unknown-impact plans intentionally remain outside
        # TEST-2's narrow known-impact validator. Report conservative local
        # command observations without presenting them as verified receipts.
        summary = {
            "reported_status": ("ALL_EXIT_ZERO" if all(
                item["exit_status"] == 0 for item in execution) else "HAS_NONZERO_EXIT"),
            "head_sha": head,
            "provenance": "CALLER_ASSERTED_UNVERIFIED",
            "execution_verified": False,
            "logs_verified": False,
        }
    observed_test_commands = [item for item in execution
                              if item["kind"] in ("L1", "L2", "L3")]
    # A command that reported no observed test cannot prove the selected tests
    # ran, so the plan must not end in a success exit code either. L0 static
    # checks are not test commands and never trigger this on their own.
    no_tests_observed = bool(observed_test_commands) and any(
        item["counts"]["tests_run"] == 0 for item in observed_test_commands)
    if no_tests_observed:
        summary["reported_status"] = "NO_TESTS_OBSERVED"
    worktree_clean = not _git(root, "status", "--porcelain", "--untracked-files=all").strip()
    return {"plan": plan, "summary": summary, "execution": execution,
            "source_binding": ("HEAD_COMMIT" if worktree_clean else
                               "HEAD_PLUS_UNCOMMITTED_WORKTREE_CONTENT_UNBOUND"),
            "log_directory": str(run_dir), "head_sha": head,
            "exit_status": (0 if not no_tests_observed
                            and all(item["exit_status"] == 0 for item in execution)
                            else 1)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="full 40-character base commit SHA")
    parser.add_argument("--dependency-test", action="append", default=[],
                        help="exact tools/llmtool/tests/test_*.py path for L2")
    parser.add_argument("--log-dir", help="owner-only directory for complete command logs")
    args = parser.parse_args(argv)
    try:
        result = run_plan(args.base, dependency_tests=args.dependency_test,
                          log_dir=args.log_dir)
    except (RunnerError, OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"status": "BLOCKED", "reason": "test_runner_failed"}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return result["exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
