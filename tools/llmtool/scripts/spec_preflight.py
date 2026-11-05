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
