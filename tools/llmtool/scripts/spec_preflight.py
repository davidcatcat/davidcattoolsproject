#!/usr/bin/env python3
"""Screen a PM dispatch spec without printing any field values."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


LLMTOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))

from llmtool.test_output_excerpts import _emitted_sensitive  # noqa: E402


MAX_FIELD_BYTES = 512


def check(spec: dict[str, Any]) -> list[str]:
    """Return fixed failure labels; never include untrusted field values."""
    failures: list[str] = []
    fields = (("request", spec["request"]), ("summary", spec["summary"]))
    for label, value in fields:
        if _emitted_sensitive(value):
            failures.append(label)

    goal = spec["goal"]
    if (_emitted_sensitive(goal)
            or len(goal.encode("utf-8")) > MAX_FIELD_BYTES):
        failures.append("goal")

    acceptance = spec["acceptance"]
    if not 1 <= len(acceptance) <= 16:
        failures.append("acceptance_count")
    for index, item in enumerate(acceptance):
        label = f"acceptance[{index}]"
        if (_emitted_sensitive(item)
                or len(item.encode("utf-8")) > MAX_FIELD_BYTES):
            failures.append(label)
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    try:
        spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        print("SPEC_PREFLIGHT_INVALID")
        return 2

    if (not isinstance(spec, dict)
            or not all(isinstance(spec.get(key), str) for key in ("request", "summary", "goal"))
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
