"""Bounded NEXT_COMMANDS display filtering; never execute or approve suggestions."""

import re
import shlex
import unicodedata

from .errors import LLMToolError
from .terminal import contains_unsafe_controls
from .validator import CommandRisk, classify_command, credential_display

OUTPUT_LIMIT = 1024 * 1024
_HEADING = re.compile(r'^\s*(?:#{1,6}\s*)?(?:\*\*)?NEXT_COMMANDS:?(?:\*\*)?\s*(.*)$')
_BULLET = re.compile(r'^\s*(?:[-*]\s+|[0-9]+[.)]\s+)(.+)$')
_PLACEHOLDER = re.compile(r'<([A-Z][A-Z0-9_]*)>')
_KNOWN_START = re.compile(r'^(?:sudo |xargs |/\S+/)?(?:kubectl|oc|systemctl|journalctl|cat|grep|head|tail|sed|awk|less|more|base64|hexdump|xxd|strings|od|env|printenv|aws|vault|helm|git|ls|stat|pwd|sh|bash|python[0-9.]*)\b')
_MARKER = '- [suppressed unsafe or unsupported suggestion]'


def _safe(command):
    if len(command.encode('utf-8')) > 8192 or contains_unsafe_controls(command):
        return False
    if any(unicodedata.category(c) == 'Cf' for c in command):
        return False
    if any(c in command for c in ('`', '$', '\\')):
        return False
    normalized = _PLACEHOLDER.sub(r'\1', command)
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return False
    if not tokens or credential_display(normalized):
        return False
    if re.search(r'(?:^|[|;&]\s*)(?:(?:sudo|xargs)\s+)?(?:/\S+/)?(?:env|sh|bash|zsh|fish|python[0-9.]*|perl|ruby)(?:\s|$)', normalized):
        return False
    return classify_command(normalized).risk not in (CommandRisk.MUTATING, CommandRisk.DESTRUCTIVE)


def filter_log_suggestions(content):
    """Return (display text, suppressed count), preserving analysis verbatim.

    Bounds/invalid UTF-8 still fail as transport data errors. Formatting/risk in
    NEXT_COMMANDS never invalidates usable preceding analysis. Unknown trailing
    prose is preserved; unsupported command blocks are visibly withheld.
    """
    if not isinstance(content, str):
        raise LLMToolError('analyze-log output must be bounded UTF-8 text')
    try:
        if len(content) > OUTPUT_LIMIT or len(content.encode('utf-8')) > OUTPUT_LIMIT:
            raise LLMToolError('analyze-log output exceeds 1 MiB')
    except UnicodeError:
        raise LLMToolError('analyze-log output must be UTF-8') from None
    lines = content.split('\n')
    output, suppressed, count = [], 0, 0
    active, fenced = False, False
    section_start, kept = 0, 0
    for line in lines:
        heading = _HEADING.fullmatch(line)
        if heading:
            active = True
            output.append('NEXT_COMMANDS:')
            section_start = len(output)
            line = heading.group(1).strip()
            if not line:
                continue
        if not active:
            output.append(line)
            continue
        if re.fullmatch(r'\s*(?:#{1,6}\s*)?(?:\*\*)?(?:ROOT_CAUSE|EVIDENCE|DISTRACTORS|UNKNOWN_OR_UNPROVEN):?(?:\*\*)?\s*', line):
            active = False
            output.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith('```'):
            fenced = not fenced
            continue
        if not stripped or stripped.lower() == '(none)':
            output.append(line)
            continue
        bullet = _BULLET.fullmatch(line)
        candidate = bullet.group(1) if bullet else stripped
        quoted = re.fullmatch(r'`([^`]+)`(?:\s+\([^\n]*\))?\s*', candidate)
        normalized = _PLACEHOLDER.sub(r'\1', candidate)
        known_write = (len(normalized.encode('utf-8')) <= 8192
                       and classify_command(normalized).risk in (CommandRisk.MUTATING, CommandRisk.DESTRUCTIVE))
        if not (bullet or quoted or fenced or _KNOWN_START.match(candidate)
                or credential_display(normalized) or known_write):
            output.append(line)
            continue
        count += 1
        command = quoted.group(1) if quoted else candidate
        if count > 3 or not _safe(command):
            output.append(_MARKER)
            suppressed += 1
        else:
            output.append(line if bullet else '- ' + line.strip())
            kept += 1
    if active and kept == 0:
        if not any(line.strip().lower() == '(none)' for line in output[section_start:]):
            output.insert(section_start, '(none)')
    return '\n'.join(output), suppressed


def validate_log_suggestions(content):
    """Compatibility API: callers receive filtered text and suppression count."""
    return filter_log_suggestions(content)
