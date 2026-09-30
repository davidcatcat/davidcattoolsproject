"""Evidence-first data packets; structural validation, never evidence retrieval.

FACTS: directly supported observations from supplied material.
EVIDENCE: specific supporting material and its declared provenance.
INFERENCES: supported conclusions that are not directly observed.
HYPOTHESES: possible explanations requiring confirmation.
UNKNOWNS: important missing information.
VERIFY_NEXT: specific next evidence to collect/check, stored only as text.

Categories are preserved as supplied, not promoted, merged or truth-validated.
"""

import json
from dataclasses import dataclass, fields
from typing import Optional

from .errors import LLMToolError

PACKET_LIMIT = 1024 * 1024
MAX_DEPTH = 4
PACKET_FIELDS = ("facts", "evidence", "inferences", "hypotheses", "unknowns", "verify_next")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise LLMToolError("context packet items must be nonempty strings")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise LLMToolError("context packet text must be valid UTF-8") from None
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value) or "\x7f" in value:
        raise LLMToolError("context packet text contains unsupported control characters")
    return value


@dataclass(frozen=True)
class EvidenceItem:
    statement: str
    source: str
    locator: Optional[str] = None

    def __post_init__(self):
        _text(self.statement)
        _text(self.source)
        if self.locator is not None:
            _text(self.locator)

    def to_dict(self):
        return {"statement": self.statement, "source": self.source, "locator": self.locator}


@dataclass(frozen=True)
class ContextPacket:
    facts: tuple[str, ...]
    evidence: tuple[EvidenceItem, ...]
    inferences: tuple[str, ...]
    hypotheses: tuple[str, ...]
    unknowns: tuple[str, ...]
    verify_next: tuple[str, ...]

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if not isinstance(value, (list, tuple)):
                raise LLMToolError("context packet categories must be lists or tuples")
            for item in value:
                if field.name == "evidence":
                    if type(item) is not EvidenceItem:
                        raise LLMToolError("context packet evidence must contain EvidenceItem values")
                else:
                    _text(item)
            object.__setattr__(self, field.name, tuple(value))

    def to_dict(self):
        return {name: [item.to_dict() for item in self.evidence] if name == "evidence"
                else list(getattr(self, name)) for name in PACKET_FIELDS}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LLMToolError("context packet JSON contains duplicate keys")
        value[key] = item
    return value


def _constant(value):
    raise LLMToolError("context packet JSON constants are unsupported")


def _check_depth(text):
    """Bound nesting before invoking the recursive standard JSON decoder."""
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise LLMToolError("context packet JSON exceeds nesting limit")
        elif char in "]}":
            depth -= 1


def parse_context_packet(text):
    """Strict JSON only: no fences, recovery, inferred sources or prose fallback."""
    if not isinstance(text, str):
        raise LLMToolError("context packet output must be JSON text")
    if len(text) > PACKET_LIMIT:
        raise LLMToolError("context packet output exceeds 1 MiB")
    try:
        raw = text.encode("utf-8")
    except UnicodeError:
        raise LLMToolError("context packet output must be valid UTF-8") from None
    if len(raw) > PACKET_LIMIT:
        raise LLMToolError("context packet output exceeds 1 MiB")
    _check_depth(text)
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise LLMToolError("context packet output is malformed JSON") from None
    if not isinstance(value, dict) or set(value) != set(PACKET_FIELDS):
        raise LLMToolError("context packet requires exactly the six supported categories")
    for name in PACKET_FIELDS:
        if not isinstance(value[name], list):
            raise LLMToolError("context packet categories must be JSON arrays")
    evidence = []
    for item in value["evidence"]:
        if (not isinstance(item, dict) or not {"statement", "source"} <= set(item)
                or set(item) - {"statement", "source", "locator"}):
            raise LLMToolError("context packet evidence requires statement, source and optional locator only")
        evidence.append(EvidenceItem(item["statement"], item["source"], item.get("locator")))
    return ContextPacket(**{**value, "evidence": evidence})


def format_context_packet(packet):
    """Stable human-readable sections; source/locator are declared, not verified."""
    sections = []
    for name in PACKET_FIELDS:
        lines = [name.upper() + ":"]
        items = getattr(packet, name)
        if not items:
            lines.append("  (none supplied)")
        for item in items:
            if name == "evidence":
                lines.append("- " + item.statement)
                lines.append("  source: " + item.source)
                if item.locator is not None:
                    lines.append("  locator: " + item.locator)
            else:
                lines.append("- " + item)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
