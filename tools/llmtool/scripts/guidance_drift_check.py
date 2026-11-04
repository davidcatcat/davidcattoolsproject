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
URL = re.compile(r"https://[^\s`<>\[\]{}]+", re.IGNORECASE)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
MODEL_NAME = re.compile(
    r"(?<![\w./-])(?:(?:anthropic|openrouter|opencode|openai|google|deepinfra|together)/)?"
    r"(?:gpt-?\d+(?:\.\d+)*(?:-[\w.]*\w)?|"
    r"claude-(?:opus|sonnet|haiku)-[\w.]*\w|"
    r"(?:deepseek|gemini|grok|llama|mistral|qwen\d*|kimi)-[\w.]*\w|"
    r"o\d+-[\w.]*\w|glm-\d+(?:-[\w.]*\w)?|minimax-m\d+(?:-[\w.]*\w)?)"
    r"(?![\w-]|\.\w|/)",
    re.IGNORECASE,
)
CHECK_NAME = re.compile(r"^check\s+\([^)]+\)$")
DISPATCH_VALUES = {"PAUSED", "OWNER_ASSIGNMENT_ONLY", "ALLOWED"}


def _clean_url(value: str) -> str:
    value = value.rstrip(".,;:!?*_\"'”’")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def _hard_values(source: str) -> set[str]:
    values = {_clean_url(match) for match in URL.findall(source)}
    inline_values = set(INLINE_CODE.findall(source))
    values.update(value for value in DISPATCH_VALUES if re.search(rf"\b{value}\b", source))
    values.update(match.group(0) for match in MODEL_NAME.finditer(source))
    values.update(value for value in inline_values if CHECK_NAME.fullmatch(value))
    return values


def _comparison_value(value: str) -> str:
    """Normalize URL scheme and authority case for cross-file comparisons."""
    return re.sub(
        r"^(https?)://([^/?#]+)",
        lambda match: f"{match.group(1).lower()}://{match.group(2).lower()}",
        value,
        flags=re.IGNORECASE,
    )


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

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        config = json.loads(raw, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
        return None, [_finding(label, "CONFIG_INVALID", type(exc).__name__)]
    if not isinstance(config, dict):
        return None, [_finding(label, "CONFIG_INVALID", "top-level value must be an object")]
    if set(config) != {"schema_version", "hashes", "codex_only"}:
        return None, [_finding(label, "CONFIG_INVALID", "unknown or missing top-level keys")]
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
    normalized_hard_values = {_comparison_value(value) for value in hard_values}
    exceptions = {_comparison_value(value) for value in codex_only}
    for value in sorted(codex_only):
        if _comparison_value(value) in normalized_hard_values:
            continue
        findings.append(_finding("tools/llmtool/guidance-drift.json", "STALE_EXCEPTION", value))
    claude_values = {_comparison_value(value) for value in _hard_values(sources["CLAUDE.md"])}
    for value in sorted(hard_values):
        if _comparison_value(value) in claude_values or _comparison_value(value) in exceptions:
            continue
        findings.append(_finding("AGENTS.md", "MISSING_IN_CLAUDE", value))
    return findings


def main() -> int:
    findings = check()
    for finding in findings:
        print(finding)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
