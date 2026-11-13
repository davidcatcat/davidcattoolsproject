#!/usr/bin/env python3
"""Check guidance and rule-file consistency.

- CLAUDE.md acknowledges hard values in AGENTS.md (hash-pinned).
- Every tools/llmtool/*.json rule file matches a strict schema (#1502 WP-G).
- Normative docs name only models listed in developer-routes.json
  allowed_models (#1502 WP-G); docs/history and docs/reports are exempt.
"""

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
    r"claude-(?:opus|sonnet|haiku)-[\w.-]*\w|"
    r"(?:deepseek|gemini|grok|llama|mistral|qwen\d*|kimi)-[\w.-]*\w|"
    r"o\d+-[\w.-]*\w|glm-\d+(?:-[\w.-]*\w)?|minimax-m\d+(?:-[\w.-]*\w)?)"
    r"(?![\w-]|\.\w|/)(?<!\.md)(?<!\.py)(?<!\.json)(?<!\.yml)(?<!\.yaml)(?<!\.txt)(?<!\.sh)(?<!\.toml)",
    re.IGNORECASE,
)
CHECK_NAME = re.compile(r"^check\s+\([^)]+\)$")
DISPATCH_VALUES = {"PAUSED", "OWNER_ASSIGNMENT_ONLY", "ALLOWED"}
RULE_DIR = Path("tools/llmtool")
ROUTES_PATH = RULE_DIR / "developer-routes.json"
MAX_RULE_BYTES = 262144
SHA256 = r"[0-9a-f]{64}"
# Normative documents for the model-name rule (#1502 WP-G). docs/history and
# docs/reports are archival and are never checked.
NORMATIVE_DOC_GLOBS = ("AGENTS.md", "CLAUDE.md", "skills/**/*.md", "docs/**/*.md", "tools/llmtool/*.md")
NON_NORMATIVE_DOC_PREFIXES = ("docs/history/", "docs/reports/")


# --- strict JSON schema for tools/llmtool/*.json rule files (#1502 WP-G) ---


class Str:
    def __init__(self, pattern: str | None = None, *, one_line: bool = True):
        self.pattern = re.compile(pattern) if pattern else None
        self.one_line = one_line

    def errors(self, value: Any, where: str) -> list[str]:
        if type(value) is not str or not value:
            return [f"{where} must be a non-empty string"]
        if self.one_line and ("\n" in value or "\r" in value):
            return [f"{where} must be one line"]
        if self.pattern is not None and not self.pattern.fullmatch(value):
            return [f"{where} has an invalid format"]
        return []


class Int:
    def __init__(self, minimum: int = 0):
        self.minimum = minimum

    def errors(self, value: Any, where: str) -> list[str]:
        if type(value) is not int or value < self.minimum:
            return [f"{where} must be an integer >= {self.minimum}"]
        return []


class Const:
    def __init__(self, value: Any):
        self.value = value

    def errors(self, value: Any, where: str) -> list[str]:
        if type(value) is not type(self.value) or value != self.value:
            return [f"{where} must be {json.dumps(self.value)}"]
        return []


class ListOf:
    def __init__(self, item: Any, *, min_items: int = 0, unique: bool = True):
        self.item = item
        self.min_items = min_items
        self.unique = unique

    def errors(self, value: Any, where: str) -> list[str]:
        if type(value) is not list:
            return [f"{where} must be a list"]
        if len(value) < self.min_items:
            return [f"{where} needs at least {self.min_items} item(s)"]
        errors = [error for index, item in enumerate(value) for error in self.item.errors(item, f"{where}[{index}]")]
        if not errors and self.unique and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            errors.append(f"{where} has duplicate items")
        return errors


class MapOf:
    def __init__(self, value: Any, key_pattern: str = r"[a-z0-9][a-z0-9_-]*", *, min_items: int = 0):
        self.value = value
        self.key_pattern = re.compile(key_pattern)
        self.min_items = min_items

    def errors(self, value: Any, where: str) -> list[str]:
        if type(value) is not dict:
            return [f"{where} must be an object"]
        if len(value) < self.min_items:
            return [f"{where} needs at least {self.min_items} key(s)"]
        errors: list[str] = []
        for key in value:
            if not self.key_pattern.fullmatch(key):
                errors.append(f"{where} has an invalid key {json.dumps(key)}")
                continue
            errors.extend(self.value.errors(value[key], f"{where}.{key}"))
        return errors


class Obj:
    def __init__(self, required: dict[str, Any], optional: dict[str, Any] | None = None):
        self.required = required
        self.optional = optional or {}

    def errors(self, value: Any, where: str) -> list[str]:
        if type(value) is not dict:
            return [f"{where} must be an object"]
        errors = [f"{where} has unknown key {json.dumps(key)}"
                  for key in sorted(set(value) - set(self.required) - set(self.optional))]
        errors.extend(f"{where} is missing key {json.dumps(key)}" for key in sorted(set(self.required) - set(value)))
        for key, schema in {**self.required, **self.optional}.items():
            if key in value:
                errors.extend(schema.errors(value[key], f"{where}.{key}"))
        return errors


REPO_PATH = r"(?!/)(?!.*\\)(?!(?:.*/)?\.\.(?:/|$))[^\s]+"
ROUTE_PROFILE = Obj(
    {"adapter": Str(), "model": Str()},
    {
        "auth_env": Str(r"[A-Z][A-Z0-9_]*"),
        "context_cap_tokens": Int(1),
        "max_output_tokens": Int(1),
        "allowed_data_classes": ListOf(Str(r"[a-z_]+"), min_items=1),
        "data_class_map": MapOf(Str(r"[a-z_]+"), r"[A-Z][A-Z_]*"),
    },
)
DEVELOPER_ROUTE = Obj(
    {"default_model": Str(), "allowed_models": ListOf(Str(), min_items=1), "default_effort": Str(r"[a-z]+")},
    {
        "executable": Str(r"[A-Za-z0-9._-]+"),
        "package_path": Str(),
        "denied_dirs": ListOf(Str(r"[A-Za-z0-9._-]+")),
        "state_dirs": MapOf(Str()),
        "profile_name": Str(r"[a-z0-9][a-z0-9_-]*"),
        "profile": ROUTE_PROFILE,
        "authorization_ref": Str(r"https://\S+"),
        "allowed_data_classes": ListOf(Str(r"[a-z_]+"), min_items=1),
    },
)
EXECUTION_HOST = Obj(
    {
        "id": Str(r"[a-z0-9][a-z0-9-]*"),
        "kind": Str(r"[a-z0-9][a-z0-9-]*"),
        "os": Str(),
        "reported_memory_gib": Int(1),
        "capabilities": ListOf(Str(r"[a-z0-9][a-z0-9-]*")),
        "reference": Str(REPO_PATH),
    },
    {"arch": Str(r"[A-Za-z0-9_]+"), "reported_vcpu": Int(1)},
)
RULE_SCHEMAS: dict[str, Any] = {
    "guidance-drift.json": Obj({
        "schema_version": Const(1),
        "hashes": Obj({"AGENTS.md": Str(SHA256), "CLAUDE.md": Str(SHA256)}),
        "codex_only": ListOf(Obj({"value": Str(), "reason": Str()})),
    }),
    "sensitive-paths.json": Obj({
        "schema_version": Const(1),
        "exact": ListOf(Str(REPO_PATH), min_items=1),
        "prefixes": ListOf(Str(REPO_PATH + r"(?<=/)")),
        "globs": ListOf(Str(REPO_PATH)),
    }),
    "developer-routes.json": Obj({
        "schema_version": Const(1),
        "routes": MapOf(DEVELOPER_ROUTE, min_items=1),
    }),
    "execution-resources.json": Obj({
        "schema_version": Const(1),
        "purpose": Str(),
        "as_of": Str(r"\d{4}-\d{2}-\d{2}"),
        "hosts": ListOf(EXECUTION_HOST),
        "live_online_state": Const("UNKNOWN"),
        "live_busy_state": Const("UNKNOWN"),
        "remote_development_authority": Const("NOT_GRANTED"),
    }),
}


def _clean_url(value: str) -> str:
    while True:
        cleaned = value.rstrip(".,;:!?*_\"'”’")
        if cleaned.endswith(")") and cleaned.count(")") > cleaned.count("("):
            cleaned = cleaned[:-1]
        if cleaned == value:
            break
        value = cleaned
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


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite JSON number")


def _read_rule_json(path: Path) -> tuple[Any, str | None]:
    """Return (value, None) or (None, reason) for one rule file."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_RULE_BYTES + 1)
        if len(raw) > MAX_RULE_BYTES:
            return None, "file too large"
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant), None
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        return None, type(exc).__name__


def check_rule_files(root: Path) -> list[str]:
    """Validate every tools/llmtool/*.json rule file against its strict schema."""
    findings: list[str] = []
    rule_dir = root / RULE_DIR
    paths = sorted(rule_dir.glob("*.json")) if rule_dir.is_dir() else []
    for path in paths:
        if path.name.startswith("."):
            continue  # Local, untracked state such as D6 bootstrap reports; not a rule file.
        label = (RULE_DIR / path.name).as_posix()
        schema = RULE_SCHEMAS.get(path.name)
        if schema is None:
            findings.append(_finding(label, "RULE_SCHEMA_MISSING", "no strict schema registered"))
            continue
        value, error = _read_rule_json(path)
        if error is not None:
            findings.append(_finding(label, "RULE_INVALID", error))
            continue
        findings.extend(_finding(label, "RULE_SCHEMA", error) for error in schema.errors(value, "$"))
    return findings


def _allowed_models(root: Path) -> tuple[set[str] | None, list[str]]:
    label = ROUTES_PATH.as_posix()
    value, error = _read_rule_json(root / ROUTES_PATH)
    if error is None and RULE_SCHEMAS["developer-routes.json"].errors(value, "$"):
        error = "schema invalid"
    if error is not None:
        return None, [_finding(label, "MODEL_ALLOWLIST_UNAVAILABLE", error)]
    return {model.lower() for route in value["routes"].values() for model in route["allowed_models"]}, []


def _normative_docs(root: Path) -> list[tuple[str, Path]]:
    docs: dict[str, Path] = {}
    for pattern in NORMATIVE_DOC_GLOBS:
        for path in root.glob(pattern):
            label = path.relative_to(root).as_posix()
            if path.is_file() and not label.startswith(NON_NORMATIVE_DOC_PREFIXES):
                docs[label] = path
    return sorted(docs.items())


def _model_allowed(name: str, allowed: set[str]) -> bool:
    lowered = name.lower()
    return lowered in allowed or lowered.rsplit("/", 1)[-1] in allowed


def check_model_names(root: Path) -> list[str]:
    """Normative docs may name a model only if developer-routes.json allows it.

    Only model-shaped names are scanned; dispatch states, counts and worker
    IDs such as claude-reviewer or gw-agent are not model names.
    """
    allowed, findings = _allowed_models(root)
    if allowed is None:
        return findings
    for label, path in _normative_docs(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            findings.append(_finding(label, "FILE_INVALID", type(exc).__name__))
            continue
        names = {match.group(0) for match in MODEL_NAME.finditer(text)}
        findings.extend(
            _finding(label, "MODEL_NOT_ALLOWED", name)
            for name in sorted(names)
            if not _model_allowed(name, allowed)
        )
    return findings


def check(repo: str | Path = REPO) -> list[str]:
    """Return one file/kind/value finding per guidance drift problem."""
    root = Path(repo)
    config, findings = _load_config(root / CONFIG_PATH)
    if config is None:
        return findings
    findings.extend(check_rule_files(root))
    findings.extend(check_model_names(root))

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
        return list(dict.fromkeys(findings))

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
    return list(dict.fromkeys(findings))


def main() -> int:
    findings = check()
    for finding in findings:
        print(finding)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
