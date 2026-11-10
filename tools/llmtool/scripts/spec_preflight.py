#!/usr/bin/env python3
"""Screen a PM dispatch spec shaped as request, summary, goal, acceptance, optional route and risk."""

from __future__ import annotations

import io
import json
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


# WP-120L (#120): the home LLM ladder runs from T1 to T5.
LADDER_MAX_TIER = 5


def ladder_tier(entries, max_tier=LADDER_MAX_TIER):
    """Return (tier, streak, stable) from home LLM ladder log entries, oldest first.

    Only entries whose int tier equals the current tier count. Three first-try
    SOLVED in a row promote one tier, never above max_tier; two failures at a
    tier drop one tier, never below 1. stable is True when the tier is max_tier
    and its streak is at least 3.
    """
    tier, streak, failures = 1, 0, 0
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("tier")) is not int:
            continue
        if entry["tier"] != tier:
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


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    try:
        spec = _strict_json_object(Path(args[0]).read_text(encoding="utf-8"))
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
    print("SPEC_PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
