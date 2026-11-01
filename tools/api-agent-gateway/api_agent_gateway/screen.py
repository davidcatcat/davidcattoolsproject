"""Egress screening: replace known credential shapes in every outgoing string.

Screening supplements the per-task egress authorization (``egress_basis`` and
``data_class``); it never replaces it. The pattern set is a minimal copy of
the shapes llmtool already screens for (no llmtool import by design).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

PLACEHOLDER = '[REDACTED:credential]'

_PEM = r'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'
_PEM_END = r'-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'
# A complete block, or a BEGIN without END in this string (redact to the end).
_PRIVATE_KEY_BLOCK = re.compile(_PEM + r'(?:.*?' + _PEM_END + r'|.*\Z)', re.S)
_PRIVATE_KEY_END = re.compile(_PEM_END)
# Bare PEM/OpenSSH body lines (ranged reads can start mid-key): a whole line of
# 60+ base64 characters mixing upper case, lower case and digits.
_B64_LINE = re.compile(r'(?m)^[ \t]*(?=[A-Za-z0-9+/=]*[A-Z])(?=[A-Za-z0-9+/=]*[a-z])'
                       r'(?=[A-Za-z0-9+/=]*[0-9])[A-Za-z0-9+/]{60,}={0,2}[ \t]*$')
_TOKENS = re.compile(
    r'\bAKIA[A-Z0-9]{16}\b|\bsk-[A-Za-z0-9_-]{12,}|'
    r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|'
    r'\bxox[abprs]-[A-Za-z0-9-]{20,}|'
    r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
    re.IGNORECASE,
)
_AUTH_VALUE = re.compile(r'\b(bearer|basic)(\s+)(?!\[REDACTED)([A-Za-z0-9._~+/-]{8,}={0,2})', re.IGNORECASE)
# Quoted literal values assigned to credential-like names (code-friendly: calls
# such as ``token = get_token()`` are left alone).
_ASSIGNMENT = re.compile(
    r'(["\']?\b(?:api[_-]?key|apikey|password|passwd|token|secret|access[_-]?token|'
    r'auth[_-]?token|client[_-]?secret|private[_-]?key|aws_secret_access_key)["\']?\s*[:=]\s*)'
    r'("[^"\n]{8,}"|\'[^\'\n]{8,}\')',
    re.IGNORECASE,
)
# High-confidence shapes for the residual check on the serialized request.
_RESIDUAL = re.compile(_PEM + r'|' + _TOKENS.pattern, re.IGNORECASE)


class Screener:
    """Redacts credential shapes and any literal secret values it is given."""

    def __init__(self, literals: Iterable[str] = ()):
        self._literals = sorted({v for v in literals if isinstance(v, str) and len(v) >= 8},
                                key=len, reverse=True)

    def text(self, value: str) -> tuple[str, int]:
        count = 0
        for literal in self._literals:
            n = value.count(literal)
            if n:
                value = value.replace(literal, PLACEHOLDER)
                count += n
        value, n = _PRIVATE_KEY_BLOCK.subn(PLACEHOLDER, value)
        count += n
        value, n = _PRIVATE_KEY_END.subn(PLACEHOLDER, value)
        count += n
        value, n = _B64_LINE.subn(PLACEHOLDER, value)
        count += n
        value, n = _TOKENS.subn(PLACEHOLDER, value)
        count += n
        value, n = _AUTH_VALUE.subn(lambda m: m.group(1) + m.group(2) + PLACEHOLDER, value)
        count += n

        def _assign(m: re.Match[str]) -> str:
            if PLACEHOLDER in m.group(2):
                return m.group(0)
            q = m.group(2)[0]
            return m.group(1) + q + PLACEHOLDER + q

        value, n = _ASSIGNMENT.subn(_assign, value)
        count += n
        return value, count

    def obj(self, value: Any) -> tuple[Any, int]:
        """Screen every string (keys included) in a JSON-like structure."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            total = 0
            out = []
            for item in value:
                item, n = self.obj(item)
                total += n
                out.append(item)
            return out, total
        if isinstance(value, dict):
            total = 0
            out = {}
            for k, v in value.items():
                k2, n1 = self.text(k) if isinstance(k, str) else (k, 0)
                v2, n2 = self.obj(v)
                total += n1 + n2
                out[k2] = v2
            return out, total
        return value, 0

    def residual(self, serialized: str) -> bool:
        """True if a high-confidence credential shape or literal survived."""
        if _RESIDUAL.search(serialized):
            return True
        return any(lit in serialized for lit in self._literals)
