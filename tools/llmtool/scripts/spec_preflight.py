#!/usr/bin/env python3
"""Screen a PM dispatch spec shaped as request, summary, goal, acceptance, optional route and risk."""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


LLMTOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))

from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_submission_cli import (  # noqa: E402
    REQUEST_LIMIT,
    RESPONSE_LIMIT,
    _read_pm_record_input,
    _screen_text,
    _strict_json_object,
)

# #2006: task repair adds one bounded item and record-pm allows 16, so keep one free.
REPAIR_ROOM_MAX_ACCEPTANCE = 15

# #1502 WP-G subtraction (c6957703194): a spec must not carry the answer text, so
# any spec with an 'edits' field is refused, whatever its value.
EDITS_FORBIDDEN_CODE = "EDITS_FORBIDDEN"
EDITS_FORBIDDEN_MESSAGE = (
    "spec field 'edits' is not allowed (#1502 WP-G): a spec must not carry the answer text"
)


def has_edits_field(spec: dict[str, Any]) -> bool:
    """True when the spec carries an 'edits' key, even if its value is null or empty."""
    return "edits" in spec


def check_ascii(spec: dict[str, Any]) -> list[str]:
    """#2099: gw-agent copies non-ASCII text unreliably; fixed labels of fields that have any."""
    if spec.get("provider") != "gw-agent":
        return []
    fields = [("goal", spec.get("goal"))]
    fields += [(f"acceptance[{i}]", item) for i, item in enumerate(spec.get("acceptance") or [])]
    return [f"{label}_non_ascii" for label, value in fields
            if isinstance(value, str) and not value.isascii()]


_OPEN, _CLOSE = "([{", ")]}"
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _ends_inside_open_bracket(line: str) -> bool:
    """True when a line leaves a bracket open; strings and # comments after a space are skipped."""
    depth, quote, escaped, prev = 0, None, False, " "
    for char in line:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and prev.isspace():
            break
        elif char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth = max(depth - 1, 0)
        prev = char
    return depth > 0


def _quoted_lines(text: str) -> list[str]:
    """Lines of each JSON string literal in text; specs quote file lines this way."""
    lines: list[str] = []
    for match in _JSON_STRING.finditer(text):
        try:
            value = json.loads(match.group(0))
        except ValueError:
            continue
        lines.extend(value.split("\n"))
    return lines


def check_open_bracket_ends(spec: dict[str, Any]) -> list[str]:
    """#2124: gw-agent closes a bracket that a spec line leaves open; fixed labels only.

    It checks each JSON string literal in acceptance items, quoted old and new
    lines alike. It works line by line, so a line inside a triple-quoted string
    can be refused; that fails closed. Unquoted acceptance prose is not checked.
    """
    if spec.get("provider") != "gw-agent":
        return []
    fields = [(f"acceptance[{i}]", item) for i, item in enumerate(spec.get("acceptance") or [])]
    texts = [(label, _quoted_lines(item)) for label, item in fields if isinstance(item, str)]
    found = [label for label, lines in texts if any(map(_ends_inside_open_bracket, lines))]
    return [label + "_open_bracket_line_end" for label in found]


def check(spec: dict[str, Any]) -> list[str]:
    """Return fixed failure labels; never include untrusted field values."""
    failures: list[str] = []
    # #2281 limit row 8: goal and acceptance items have no own byte cap; the
    # response bound holds, and the record-pm check below screens the whole response.
    for label, value, limit in (
        ("request", spec["request"], REQUEST_LIMIT),
        ("summary", spec["summary"], 2048),
        ("goal", spec["goal"], RESPONSE_LIMIT),
    ):
        try:
            _screen_text(value, limit)
        except LLMToolError:
            failures.append(label)

    acceptance = spec["acceptance"]
    if not 1 <= len(acceptance) <= 16:
        failures.append("acceptance_count")
    for index, item in enumerate(acceptance):
        try:
            _screen_text(item, RESPONSE_LIMIT)
        except LLMToolError:
            failures.append(f"acceptance[{index}]")

    if failures:
        return failures

    response = json.dumps({
        "schema_version": 1,
        "summary": spec["summary"],
        "developer_assignment": {
            "route": spec.get("route", "codex"),
            "risk": spec.get("risk", "LOW"),
            "goal": spec["goal"],
            "acceptance": acceptance,
        },
    })
    envelope = json.dumps({"request": spec["request"], "response": response})
    try:
        _read_pm_record_input(io.BytesIO(envelope.encode("utf-8")))
    except LLMToolError:
        failures.append("response")
    return failures


# #1570: a home LLM that must find an anchor in a long file reads it in large
# chunks and can exhaust its context (Task 1e61753d), so a gw-agent spec that
# writes a long file is refused. The 'edits' line hints are gone with #1502 WP-G.
LARGE_FILE_LINES = 2000


# WP-120L (#120): the home LLM ladder runs from T1 to T6 (T6: Owner decision A, #763).
LADDER_MAX_TIER = 6


def _row_tier(value, route):
    """Return the int tier of a ladder row for this route, or None."""
    if route is None:
        return value if type(value) is int else None
    prefix = route + '-'
    if isinstance(value, str) and value.startswith(prefix) and value[len(prefix):].isdigit():
        return int(value[len(prefix):])
    return None


def ladder_tier(entries, max_tier=LADDER_MAX_TIER, route=None):
    """Return (tier, streak, stable) from home LLM ladder log entries, oldest first.

    Only entries whose int tier equals the current tier count. Three first-try
    SOLVED in a row promote one tier, never above max_tier; two failures at a
    tier drop one tier, never below 1. stable is True when the tier is max_tier
    and its streak is at least 3. route None counts int tiers; a route such as
    'gw-agent' counts only rows whose tier is '<route>-N'.
    """
    tier, streak, failures = 1, 0, 0
    # Count only the newest real row per task; CORRECTION and note rows are annotations.
    kept = [e for e in entries if isinstance(e, dict) and not str(e.get("kind", "")).startswith(("CORRECTION", "note"))]
    last = {e.get("task"): i for i, e in enumerate(kept) if e.get("task")}
    entries = [e for i, e in enumerate(kept) if not e.get("task") or last[e.get("task")] == i]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row_tier = _row_tier(entry.get("tier"), route)
        if row_tier is None or row_tier != tier:
            continue
        outcome = str(entry.get("outcome", ""))
        if outcome.startswith("UNAVAILABLE"):
            # A VPN or provider outage neither counts nor breaks a streak.
            continue
        if outcome.startswith("SOLVED") and entry.get("attempts") == 1:
            streak += 1
            if streak >= 3 and tier < max_tier:
                tier, streak, failures = tier + 1, 0, 0
        elif outcome.startswith("FAILED"):
            streak, failures = 0, failures + 1
            if failures >= 2 and tier > 1:
                tier, streak, failures = tier - 1, 0, 0
        else:
            streak = 0
    return tier, streak, tier == max_tier and streak >= 3


# limits per tier as (files written, context bytes, gw-agent turns)
LADDER_SIZES = {1: (1, 1536, 6), 2: (1, 3072, 10), 3: (2, 6144, 12), 4: (3, 10240, 16),
                5: (4, 16384, 16), 6: (5, 24576, 16)}

REPO_PATH_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9_./-])"
    r"((?:tools|scripts|docs|\.claude)/[a-zA-Z0-9_/-]+(?:\.[a-zA-Z0-9_/-]+)*\.(?:py|md|json))"
    r"(?![a-zA-Z0-9_/-]|\.[a-zA-Z0-9])"
)


def _norm_path(path: Any) -> str:
    if not isinstance(path, str):
        return ""
    return path.replace("\\", "/").removeprefix("./")


def check_read_scope(spec: dict[str, Any], repo: str | Path) -> list[str]:
    """Check read scope covers acceptance paths and tests when --repo is given."""
    repo_dir = Path(repo)
    raw_writes = spec.get("write")
    if not isinstance(raw_writes, list):
        raw_writes = spec.get("write_paths")
    writes = raw_writes if isinstance(raw_writes, list) else []

    raw_reads = spec.get("read")
    if not isinstance(raw_reads, list):
        raw_reads = spec.get("read_paths")
    reads = raw_reads if isinstance(raw_reads, list) else []

    acceptance = spec.get("acceptance") if isinstance(spec.get("acceptance"), list) else []

    failures: list[str] = []

    code_writes = [
        _norm_path(w) for w in writes
        if isinstance(w, str)
        and _norm_path(w).endswith(".py")
        and (_norm_path(w).startswith("tools/") or _norm_path(w).startswith("scripts/"))
        and not _norm_path(w).startswith("tools/llmtool/tests/")
    ]
    if code_writes:
        # #2206 follow-up: monitor code in scripts/ keeps its tests in scripts/test_*.py;
        # accept those only when every code write is under scripts/.
        test_dirs = ["tools/llmtool/tests/"]
        if all(w.startswith("scripts/") for w in code_writes):
            test_dirs.append("scripts/")
        has_test_module = any(
            isinstance(r, str)
            and _norm_path(r).startswith(test_dir + "test_")
            and _norm_path(r).endswith(".py")
            and "/" not in _norm_path(r)[len(test_dir + "test_"):]
            and (repo_dir / _norm_path(r)).is_file()
            for r in reads
            for test_dir in test_dirs
        )
        if not has_test_module:
            failures.append("read_no_test_module")

    read_write_set = {
        _norm_path(p) for p in reads if isinstance(p, str)
    } | {
        _norm_path(p) for p in writes if isinstance(p, str)
    }
    missing_acceptance_path = False
    for item in acceptance:
        if not isinstance(item, str):
            continue
        for path in REPO_PATH_PATTERN.findall(item):
            if _norm_path(path) not in read_write_set:
                missing_acceptance_path = True
                break
        if missing_acceptance_path:
            break
    if missing_acceptance_path:
        failures.append("acceptance_path_not_in_read")

    write_set = {_norm_path(p) for p in writes if isinstance(p, str)}
    missing_read_path = any(
        isinstance(r, str)
        and _norm_path(r) not in write_set
        and not (repo_dir / _norm_path(r)).exists()
        for r in reads
    )
    if missing_read_path:
        failures.append("read_path_missing")

    return failures


# #2005: the ladder sizes a new file as T4 (.claude/skills/company-llm-delegation/SKILL.md).
NEW_FILE_MIN_TIER = 4


def spec_tier(spec, context_bytes=0, repo=None):
    """Return the smallest ladder tier whose limits fit the spec: write count,
    context bytes, and (for gw-agent specs) turn count; LADDER_MAX_TIER if none fits.
    If repo is given and any write path does not exist under repo, return at least
    NEW_FILE_MIN_TIER."""
    writes = spec.get("write") if isinstance(spec.get("write"), list) else []
    reads = spec.get("read") if isinstance(spec.get("read"), list) else []
    agent = spec.get("provider") == "gw-agent"
    turns = len(reads) + 2 * len(writes) + 1
    tier = LADDER_MAX_TIER
    for candidate in sorted(LADDER_SIZES):
        files, limit_bytes, turn_limit = LADDER_SIZES[candidate]
        if len(writes) <= files and context_bytes <= limit_bytes and (not agent or turns <= turn_limit):
            tier = candidate
            break
    if repo is not None and any(isinstance(w, str) and not (Path(repo) / w).is_file() for w in writes):
        tier = max(tier, NEW_FILE_MIN_TIER)
    return min(tier, LADDER_MAX_TIER)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    ladder_log: str | None = None
    repo_dir: Path = Path.cwd()
    repo_given = False
    spec_path: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--ladder":
            if i + 1 >= len(args):
                print("SPEC_PREFLIGHT_INVALID")
                return 2
            ladder_log = args[i + 1]
            i += 2
        elif arg == "--repo":
            if i + 1 >= len(args):
                print("SPEC_PREFLIGHT_INVALID")
                return 2
            repo_dir = Path(args[i + 1])
            repo_given = True
            i += 2
        elif arg.startswith("-"):
            print("SPEC_PREFLIGHT_INVALID")
            return 2
        else:
            if spec_path is not None:
                print("SPEC_PREFLIGHT_INVALID")
                return 2
            spec_path = arg
            i += 1

    if spec_path is None:
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    try:
        spec = _strict_json_object(Path(spec_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    if (not all(isinstance(spec.get(key), str) for key in ("request", "summary", "goal"))
            or not isinstance(spec.get("acceptance"), list)
            or not all(isinstance(item, str) for item in spec["acceptance"])):
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    if has_edits_field(spec):
        print(f"SPEC_PREFLIGHT_INVALID:{EDITS_FORBIDDEN_CODE}")
        print(EDITS_FORBIDDEN_MESSAGE, file=sys.stderr)
        return 2

    failures = check(spec) + check_ascii(spec) + check_open_bracket_ends(spec)
    if failures:
        print("SPEC_PREFLIGHT_FAIL:" + ",".join(failures))
        return 1

    if len(spec["acceptance"]) > REPAIR_ROOM_MAX_ACCEPTANCE:
        print("SPEC_PREFLIGHT_WARN:ACCEPTANCE_NO_REPAIR_ROOM", file=sys.stderr)

    if spec.get("provider") == "gw-agent":
        for path in spec.get("write") or []:
            target = repo_dir / path if isinstance(path, str) else None
            if (target is not None and target.is_file() and
                    target.read_bytes().count(b"\n") >= LARGE_FILE_LINES):
                print("SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1")
                return 2

    if repo_given:
        scope_failures = check_read_scope(spec, repo=repo_dir)
        if scope_failures:
            print("SPEC_PREFLIGHT_FAIL:" + ",".join(scope_failures))
            return 1

    if ladder_log is None:
        print("SPEC_PREFLIGHT_OK")
        return 0

    # Ladder mode, only reached after check(spec) passed.
    try:
        log_text = Path(ladder_log).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    entries: list[dict[str, Any]] = []
    for line in log_text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            entries.append(value)

    route = 'gw-agent' if spec.get('provider') == 'gw-agent' else None
    current = ladder_tier(entries, route=route)[0]
    context_file = spec.get("context_file")
    if isinstance(context_file, str) and os.path.isfile(context_file):
        context_bytes = os.path.getsize(context_file)
    else:
        context_bytes = 0
    required = spec_tier(spec, context_bytes, repo=repo_dir if repo_given else None)
    if required > current:
        print(f"SPEC_LADDER_TIER_EXCEEDED:required={required},current={current}")
        return 2
    print(f"SPEC_PREFLIGHT_OK:tier={required},current={current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
