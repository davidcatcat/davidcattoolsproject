#!/usr/bin/env python3
"""Screen a PM dispatch spec shaped as request, summary, goal, acceptance, optional route and risk."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any


LLMTOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))

from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_submission_cli import (  # noqa: E402
    REQUEST_LIMIT,
    _read_pm_record_input,
    _screen_text,
    _strict_json_object,
)


def check(spec: dict[str, Any]) -> list[str]:
    """Return fixed failure labels; never include untrusted field values."""
    failures: list[str] = []
    for label, value, limit in (
        ("request", spec["request"], REQUEST_LIMIT),
        ("summary", spec["summary"], 2048),
        ("goal", spec["goal"], 512),
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
            _screen_text(item, 512)
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
# chunks and can exhaust its context (Task 1e61753d), so long files need a line hint.
LARGE_FILE_LINES = 2000
LINE_HINT_WINDOW = 20


def check_edits(spec: dict[str, Any], repo: str | Path | None = None) -> tuple[str, int] | None:
    """Validate optional spec field 'edits' under the given repo directory.

    Returns None if valid or if 'edits' is not present.
    On failure returns (code, index) where code is one of:
      - EDIT_COUNT_OVER_CAP
      - EDIT_PATH_OUT_OF_SCOPE
      - EDIT_NEW_TEXT_INVALID
      - EDIT_ANCHOR_MISSING
      - EDIT_ANCHOR_NOT_UNIQUE
    """
    if "edits" not in spec or spec["edits"] is None:
        return None

    edits = spec["edits"]
    if not isinstance(edits, list):
        return None

    if len(edits) > 6:
        return ("EDIT_COUNT_OVER_CAP", 6)

    write_list = spec.get("write")
    write_paths = set(write_list) if isinstance(write_list, list) else set()
    repo_dir = Path.cwd() if repo is None else Path(repo)

    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return ("EDIT_PATH_OUT_OF_SCOPE", index)

        path = edit.get("path")
        if not isinstance(path, str) or path not in write_paths:
            return ("EDIT_PATH_OUT_OF_SCOPE", index)

        new_text = edit.get("new")
        old_text = edit.get("old")

        if not isinstance(new_text, str) or len(new_text) == 0 or new_text == old_text:
            return ("EDIT_NEW_TEXT_INVALID", index)

        if not isinstance(old_text, str) or len(old_text) == 0:
            return ("EDIT_ANCHOR_MISSING", index)

        target_file = repo_dir / path
        if not target_file.is_file():
            return ("EDIT_ANCHOR_MISSING", index)

        try:
            content = target_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ("EDIT_ANCHOR_MISSING", index)

        count = content.count(old_text)
        if count == 0:
            return ("EDIT_ANCHOR_MISSING", index)
        if count > 1:
            return ("EDIT_ANCHOR_NOT_UNIQUE", index)
        if content.count("\n") >= LARGE_FILE_LINES:
            line = edit.get("line")
            if type(line) is not int or line < 1:
                return ("EDIT_LINE_HINT_MISSING", index)
            if abs(content[:content.index(old_text)].count("\n") + 1 - line) > LINE_HINT_WINDOW:
                return ("EDIT_LINE_HINT_WRONG", index)

    return None


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


def spec_tier(spec, context_bytes=0):
    """Return the smallest ladder tier whose limits fit the spec: write count,
    context bytes, and (for gw-agent specs) turn count; LADDER_MAX_TIER if none fits."""
    writes = spec.get("write") if isinstance(spec.get("write"), list) else []
    reads = spec.get("read") if isinstance(spec.get("read"), list) else []
    agent = spec.get("provider") == "gw-agent"
    turns = len(reads) + 2 * len(writes) + 1
    for tier in sorted(LADDER_SIZES):
        files, limit_bytes, turn_limit = LADDER_SIZES[tier]
        if len(writes) <= files and context_bytes <= limit_bytes and (not agent or turns <= turn_limit):
            return tier
    return LADDER_MAX_TIER


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    ladder_log: str | None = None
    repo_dir: Path = Path.cwd()
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

    failures = check(spec)
    if failures:
        print("SPEC_PREFLIGHT_FAIL:" + ",".join(failures))
        return 1

    if "edits" in spec and spec["edits"] is not None:
        if not isinstance(spec["edits"], list):
            print("SPEC_PREFLIGHT_INVALID")
            return 2
        edit_failure = check_edits(spec, repo=repo_dir)
        if edit_failure is not None:
            code, index = edit_failure
            print(f"SPEC_PREFLIGHT_INVALID:{code}:{index}")
            return 2
    elif spec.get("provider") == "gw-agent":
        for path in spec.get("write") or []:
            target = repo_dir / path if isinstance(path, str) else None
            if (target is not None and target.is_file() and
                    target.read_bytes().count(b"\n") >= LARGE_FILE_LINES):
                print("SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1")
                return 2

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
    required = spec_tier(spec, context_bytes)
    if required > current:
        print(f"SPEC_LADDER_TIER_EXCEEDED:required={required},current={current}")
        return 2
    print(f"SPEC_PREFLIGHT_OK:tier={required},current={current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
