#!/usr/bin/env python3
"""Check that Claude guidance acknowledges hard values in AGENTS.md."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path("tools/llmtool/guidance-drift.json")
URL = re.compile(r"https://[^\s`<>\])}]+")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
MODEL_NAME = re.compile(
    r"^(?:gpt-[\w.-]+|claude-[\w.-]+|opencode/[\w.-]+|deepseek-[\w.-]+|"
    r"gemini-[\w.-]+|grok-[\w.-]+|llama-[\w.-]+|mistral-[\w.-]+)$",
    re.IGNORECASE,
)
CHECK_NAME = re.compile(r"^check\s+\([^)]+\)$")
DISPATCH_VALUES = {"PAUSED", "OWNER_ASSIGNMENT_ONLY", "ALLOWED"}


def _hard_values(source: str) -> set[str]:
    values = {match.rstrip(".,;:") for match in URL.findall(source)}
    inline_values = set(INLINE_CODE.findall(source))
    values.update(inline_values & DISPATCH_VALUES)
    values.update(value for value in inline_values if MODEL_NAME.fullmatch(value))
    values.update(value for value in inline_values if CHECK_NAME.fullmatch(value))
    return values


def _finding(path: str, kind: str, value: str) -> str:
    return f"{path}: {kind} {value}"


def _load_config(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    label = CONFIG_PATH.as_posix()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, [_finding(label, "CONFIG_MISSING", "file not found")]
    except (OSError, UnicodeError) as exc:
        return None, [_finding(label, "CONFIG_INVALID", type(exc).__name__)]

    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        return None, [_finding(label, "CONFIG_INVALID", type(exc).__name__)]
    if not isinstance(config, dict):
        return None, [_finding(label, "CONFIG_INVALID", "top-level value must be an object")]
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        return None, [_finding(label, "CONFIG_SCHEMA", str(config.get("schema_version")))]

    hashes = config.get("hashes")
    if not isinstance(hashes, dict) or set(hashes) != {"AGENTS.md", "CLAUDE.md"}:
        return None, [_finding(label, "CONFIG_INVALID", "hashes must name AGENTS.md and CLAUDE.md")]
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()):
        return None, [_finding(label, "CONFIG_INVALID", "hash values must be lowercase SHA-256 digests")]

    exceptions = config.get("codex_only")
    if not isinstance(exceptions, list):
        return None, [_finding(label, "CONFIG_INVALID", "codex_only must be a list")]
    seen: set[str] = set()
    for entry in exceptions:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"value", "reason"}
            or not isinstance(entry["value"], str)
            or not entry["value"]
            or "\n" in entry["value"]
            or "\r" in entry["value"]
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
            or "\n" in entry["reason"]
            or entry["value"] in seen
            or "\r" in entry["reason"]
        ):
            return None, [_finding(label, "CONFIG_INVALID", "codex_only entries need unique values and one-line reasons")]
        seen.add(entry["value"])
    return config, []


def check(repo: str | Path = REPO) -> list[str]:
    """Return one file/kind/value finding per guidance drift problem."""
    root = Path(repo)
    config, findings = _load_config(root / CONFIG_PATH)
    if config is None:
        return findings

    sources: dict[str, str] = {}
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = root / name
        try:
            content = path.read_bytes()
            sources[name] = content.decode("utf-8")
        except FileNotFoundError:
            findings.append(_finding(name, "FILE_MISSING", "file not found"))
            continue
        except (OSError, UnicodeError) as exc:
            findings.append(_finding(name, "FILE_INVALID", type(exc).__name__))
            continue
        digest = hashlib.sha256(content).hexdigest()
        if digest != config["hashes"][name]:
            findings.append(_finding(name, "HASH_STALE", digest))

    if "AGENTS.md" not in sources or "CLAUDE.md" not in sources:
        return findings

    hard_values = _hard_values(sources["AGENTS.md"])
    codex_only = {entry["value"] for entry in config["codex_only"]}
    for value in sorted(codex_only - hard_values):
        findings.append(_finding("tools/llmtool/guidance-drift.json", "STALE_EXCEPTION", value))
    for value in sorted(hard_values - _hard_values(sources["CLAUDE.md"]) - codex_only):
        findings.append(_finding("AGENTS.md", "MISSING_IN_CLAUDE", value))
    return findings


def main() -> int:
    findings = check()
    for finding in findings:
        print(finding)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
