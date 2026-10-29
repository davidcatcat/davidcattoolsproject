"""Pure Markdown, table, code, diff and syntax renderer for the chat TUI (W56b+c).

``render(text, width)`` returns display lines, each a list of ``(text, token)``
segments already wrapped to ``width`` terminal cells (CJK-aware).  Tokens are the
shared style-token strings below; the TUI maps them to curses attributes in ONE
table (``STYLE_TOKENS`` in ``chat_tui_view.py``).  This module has no curses
import, no I/O and no colour values: the same input always gives the same lines,
and malformed Markdown degrades to plain ``text`` instead of raising.
"""
from __future__ import annotations

import builtins
import functools
import io
import keyword
import re
import tokenize
import unicodedata
from dataclasses import dataclass

TEXT = "text"
BOLD, ITALIC, CODE = "md.bold", "md.italic", "md.code"
HEADING, BULLET, LINK, QUOTE = "md.heading", "md.bullet", "md.link", "md.quote"
TABLE_BORDER, TABLE_HEADER, TABLE_CELL = "md.table.border", "md.table.header", "md.table.cell"
CODE_BORDER, CODE_LANG, CODE_BLOCK = "md.codeblock.border", "md.codeblock.lang", "md.codeblock"
DIFF_ADD, DIFF_DEL, DIFF_HUNK, DIFF_META, DIFF_LINENO = (
    "diff.add", "diff.del", "diff.hunk", "diff.meta", "diff.lineno")
SYN_KEYWORD, SYN_STRING, SYN_COMMENT, SYN_NUMBER, SYN_BUILTIN = (
    "syntax.keyword", "syntax.string", "syntax.comment", "syntax.number", "syntax.builtin")

TOKENS = frozenset({
    TEXT, BOLD, ITALIC, CODE, HEADING, BULLET, LINK, QUOTE,
    TABLE_BORDER, TABLE_HEADER, TABLE_CELL, CODE_BORDER, CODE_LANG, CODE_BLOCK,
    DIFF_ADD, DIFF_DEL, DIFF_HUNK, DIFF_META, DIFF_LINENO,
    SYN_KEYWORD, SYN_STRING, SYN_COMMENT, SYN_NUMBER, SYN_BUILTIN,
})

TAB_WIDTH = 4
DIFF_COLLAPSE_OVER = 80   # Diffs longer than this many lines are collapsed ...
DIFF_KEEP_HEAD = 40       # ... to their first 40 ...
DIFF_KEEP_TAIL = 10       # ... and last 10 lines.
INLINE_DEPTH = 8          # Nested emphasis deeper than this stays literal.
CONTINUATION_MARK = "↪"
BULLET_GLYPHS = ("•", "◦", "▪")
HEADING_GLYPH = "▍"
QUOTE_BAR = "│ "

# Line-breaking rules: no line starts with closing punctuation or ends with opening.
_NO_LINE_START = frozenset("、。，．,.!！?？:：;；)）]」』】》〉”’ー々〜…")
_NO_LINE_END = frozenset("([（「『【《〈“‘")


@dataclass(frozen=True)
class Rendered:
    """Rendered display lines plus how many diff source lines were collapsed."""

    lines: list
    collapsed: int = 0


def render(text: str, width: int, *, collapse: bool = True) -> list:
    """Display lines of ``(text, token)`` segments, wrapped to ``width`` cells.

    With ``collapse=False`` long diffs are shown whole; ``render_document`` also
    reports how many diff lines the default rendering collapsed.
    """
    return render_document(text, width, collapse=collapse).lines


def render_document(text: str, width: int, *, collapse: bool = True) -> Rendered:
    """Like ``render`` but returns ``Rendered(lines, collapsed)``; never raises."""
    try:
        width = max(1, int(width))
    except (TypeError, ValueError):
        width = 80
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    try:
        renderer = _Renderer(width, collapse)
        renderer.document(text)
        return Rendered(renderer.lines, renderer.collapsed)
    except Exception:  # pragma: no cover - last-resort degrade to plain text
        lines = []
        for line in _source_lines(text):
            lines.extend(_wrap([(line, TEXT)], width) or [[]])
        return Rendered(lines, 0)


# --- display width ---------------------------------------------------------

@functools.lru_cache(maxsize=4096)
def char_width(char: str) -> int:
    """Terminal cells of one code point: 0 for combining/format, 2 for wide."""
    code = ord(char)
    if code < 32 or 0x7F <= code < 0xA0:
        return 0
    if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF or code == 0x200D:
        return 0
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    if 0x1F300 <= code <= 0x1FAFF:
        return 2
    return 1


def display_width(text: str) -> int:
    """Terminal cells ``text`` occupies."""
    if text.isascii():
        return len(text)
    return sum(char_width(char) for char in text)


def _clusters(text):
    """``(cluster, width)`` units: zero-width marks join the previous character."""
    units = []
    joined = False
    for char in text:
        width = char_width(char)
        if units and (width == 0 or joined):
            cluster, cells = units[-1]
            units[-1] = (cluster + char, cells)
        else:
            units.append((char, width))
        joined = char == "‍"
    return units


def _source_lines(text):
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    return [_sanitize(line.expandtabs(TAB_WIDTH)) for line in cleaned.split("\n")]


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _sanitize(line):
    """Terminal control characters (ESC and friends) become a visible U+FFFD."""
    return _CONTROL.sub("�", line)


# --- wrapping --------------------------------------------------------------

def _merge(segments):
    merged = []
    for text, token in segments:
        if not text:
            continue
        if merged and merged[-1][1] == token:
            merged[-1] = (merged[-1][0] + text, token)
        else:
            merged.append((text, token))
    return merged


def _wrap(segments, width, first_prefix=(), next_prefix=(), hard=False):
    """Wrap segments to ``width`` cells with optional hanging prefixes.

    Soft wrapping breaks after spaces and around wide (CJK) characters, honouring
    simple line-start/line-end punctuation rules; ``hard`` breaks at any cell (for
    code).  A character is never split: one wider than the room is put on its own.
    """
    first_prefix, next_prefix = list(first_prefix), list(next_prefix)
    first_room = width - sum(display_width(t) for t, _ in first_prefix)
    next_room = width - sum(display_width(t) for t, _ in next_prefix)
    if first_room < 2 or next_room < 2:
        first_prefix, next_prefix, first_room, next_room = [], [], width, width
    units = []
    for text, token in segments:
        units.extend((cluster, cells, token) for cluster, cells in _clusters(text))
    rows = []
    current, cells, room, breaks = [], 0, first_room, -1

    def last_break(row):
        position = -1
        for index in range(1, len(row)):
            if _break_between(row[index - 1], row[index]):
                position = index
        return position

    for unit in units:
        skip = False
        while cells + unit[1] > room and current:
            if not hard and unit[0] == " ":
                rows.append(_trim(current))
                current, cells, room, breaks = [], 0, next_room, -1
                skip = True
                break
            cut = len(current)
            if not hard and not _break_between(current[-1], unit) and breaks > 0:
                cut = breaks
            cut = min(cut, _fitting(current, room))
            head, current = current[:cut], current[cut:]
            rows.append(_trim(head) if not hard else head)
            if not hard:
                while current and current[0][0] == " ":
                    current.pop(0)
            cells, room = sum(u[1] for u in current), next_room
            breaks = -1 if hard else last_break(current)
        if skip:
            continue
        if not hard and current and _break_between(current[-1], unit):
            breaks = len(current)
        current.append(unit)
        cells += unit[1]
    if current or not rows:
        rows.append(_trim(current) if not hard else current)
    lines = []
    for index, row in enumerate(rows):
        prefix = first_prefix if index == 0 else next_prefix
        lines.append(_merge(prefix + [(cluster, token) for cluster, _, token in row]))
    return lines


def _fitting(row, room):
    """How many leading units of ``row`` fit in ``room`` cells (at least one)."""
    used = 0
    for index, unit in enumerate(row):
        used += unit[1]
        if used > room:
            return max(1, index)
    return len(row)


def _break_between(before, after):
    """True when a soft line break may fall between two units."""
    if before[0] == " ":
        return after[0] != " "
    if after[0] in _NO_LINE_START or before[0] in _NO_LINE_END:
        return False
    return before[1] == 2 or after[1] == 2


def _trim(row):
    while row and row[-1][0] == " ":
        row = row[:-1]
    return row


def _pad(segments, room, token, align="left"):
    """Pad segments with ``token`` spaces to ``room`` cells."""
    used = sum(display_width(t) for t, _ in segments)
    spare = max(0, room - used)
    if align == "right":
        left = spare
    elif align == "center":
        left = spare // 2
    else:
        left = 0
    return [(" " * left, token)] + list(segments) + [(" " * (spare - left), token)]


# --- inline Markdown -------------------------------------------------------

_INLINE_SPECIAL = re.compile(r"[\\`*_\[]|https?://")
_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)\s*\)")
_BARE_URL = re.compile(r"https?://[^\s<>()\[\]]+")
_ESCAPABLE = frozenset("\\`*_{}[]()#+-.!|>~")


def inline(text, base=TEXT, depth=0):
    """``(text, token)`` segments for one line of inline Markdown."""
    if depth > INLINE_DEPTH:
        return [(text, base)] if text else []
    out = []
    plain = []
    position, length = 0, len(text)

    def flush():
        if plain:
            out.append(("".join(plain), base))
            plain.clear()

    while position < length:
        match = _INLINE_SPECIAL.search(text, position)
        if match is None:
            plain.append(text[position:])
            break
        start = match.start()
        plain.append(text[position:start])
        char = text[start]
        if char == "\\":
            if start + 1 < length and text[start + 1] in _ESCAPABLE:
                plain.append(text[start + 1])
                position = start + 2
            else:
                plain.append("\\")
                position = start + 1
            continue
        if char == "`":
            run = _run_length(text, start, "`")
            close = _find_run(text, start + run, "`", run)
            if close < 0:
                plain.append("`" * run)
                position = start + run
                continue
            code = text[start + run:close]
            if len(code) > 2 and code[0] == " " and code[-1] == " " and code.strip():
                code = code[1:-1]
            flush()
            out.append((code, CODE))
            position = close + run
            continue
        if char == "[":
            link = _LINK.match(text, start)
            if link is None:
                plain.append("[")
                position = start + 1
                continue
            label, url = link.group(1), link.group(2)
            flush()
            label_segments = inline(label, LINK, depth + 1) if label else []
            out.extend(label_segments)
            if url and url != label:
                out.append(((" " if label_segments else "") + "(" + url + ")", LINK))
            position = link.end()
            continue
        if char == "h":
            if start and (text[start - 1].isalnum() or text[start - 1] in "/:"):
                plain.append(text[start])
                position = start + 1
                continue
            url = _BARE_URL.match(text, start)
            stripped = url.group(0).rstrip(".,;:!?'\"") if url else ""
            if len(stripped) <= len(match.group(0)):
                plain.append(text[start])
                position = start + 1
                continue
            flush()
            out.append((stripped, LINK))
            position = start + len(stripped)
            continue
        # Emphasis: ``***x***``, ``**x**``/``__x__``, ``*x*``/``_x_``.
        run = _run_length(text, start, char)
        if char == "_" and start and text[start - 1].isalnum():
            plain.append(char * run)
            position = start + run
            continue
        span = _emphasis(text, start, char, run)
        if span is None:
            plain.append(char * run)
            position = start + run
            continue
        marker, close = span
        token = ITALIC if marker == 1 else BOLD
        flush()
        out.extend(inline(text[start + marker:close], token, depth + 1))
        position = close + marker
    flush()
    return _merge(out)


def _run_length(text, start, char):
    end = start
    while end < len(text) and text[end] == char:
        end += 1
    return end - start


def _find_run(text, start, char, run):
    """Index of the next run of exactly ``run`` ``char``s at or after ``start``."""
    position = text.find(char * run, start)
    while position >= 0:
        actual = _run_length(text, position, char)
        if actual == run:
            return position
        position = text.find(char * run, position + actual)
    return -1


def _emphasis(text, start, char, run):
    """``(marker_length, close_index)`` for an emphasis span opening at ``start``."""
    for marker in ((3, 2, 1) if run == 3 else (run,) if run < 3 else ()):
        content_start = start + marker
        if content_start >= len(text) or text[content_start].isspace():
            continue
        close = _find_closer(text, content_start, char, marker)
        if close >= 0:
            return marker, close
    return None


def _find_closer(text, position, char, marker):
    """Closing delimiter of ``marker`` chars, skipping code spans and other runs."""
    length = len(text)
    opening = position
    while position < length:
        current = text[position]
        if current == "\\":
            position += 2
            continue
        if current == "`":
            run = _run_length(text, position, "`")
            close = _find_run(text, position + run, "`", run)
            position = (close + run) if close >= 0 else position + run
            continue
        if current != char:
            position += 1
            continue
        run = _run_length(text, position, char)
        if position == opening:   # ``***x***`` read as bold: its third star is content
            position += run
            continue
        before = text[position - 1]
        after = text[position + run] if position + run < length else ""
        closes = not before.isspace() and not (char == "_" and after.isalnum())
        if closes and run == marker:
            return position
        if closes and run == 3 and marker < 3:
            return position + 3 - marker   # ``**a *b***``: the inner span closes first
        position += run
    return -1


# --- block structure -------------------------------------------------------

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^\s`]*)[^`]*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_HUNK = re.compile(r"^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@+")
_DIFF_META_PREFIXES = (
    "diff --git ", "index ", "--- ", "+++ ", "new file mode", "deleted file mode",
    "old mode", "new mode", "similarity index", "dissimilarity index", "rename from",
    "rename to", "copy from", "copy to", "Binary files", "GIT binary patch",
)


def _is_diff_start(lines, index):
    line = lines[index]
    if line.startswith("diff --git "):
        return True
    if line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
        return True
    return bool(_HUNK.match(line))


class _Renderer:
    def __init__(self, width, collapse):
        self.width = width
        self.collapse = collapse
        self.lines = []
        self.collapsed = 0

    def document(self, text):
        lines = _source_lines(text)
        first = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first is not None and _is_diff_start(lines, first):
            self.lines.extend([] for _ in range(first))
            end = len(lines)
            while end > first and not lines[end - 1].strip():
                end -= 1
            self.lines.extend(self.diff(lines[first:end], self.width))
            return
        index = 0
        while index < len(lines):
            index = self.block(lines, index)

    def block(self, lines, index):
        line = lines[index]
        width = self.width
        if not line.strip():
            self.lines.append([])
            return index + 1
        fence = _FENCE.match(line)
        if fence:
            return self.fenced(lines, index, fence)
        if _is_diff_start(lines, index):
            end = index + 1
            while end < len(lines) and _diff_like(lines[end]):
                end += 1
            self.lines.extend(self.diff(lines[index:end], width))
            return end
        if "|" in line and index + 1 < len(lines) and "|" in lines[index + 1] \
                and _TABLE_SEPARATOR.match(lines[index + 1]):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            self.lines.extend(self.table(lines[index], lines[index + 1], lines[index + 2:end]))
            return end
        heading = _HEADING.match(line)
        if heading:
            glyph = [(HEADING_GLYPH + " ", HEADING)]
            body = inline(heading.group(2) or "", HEADING)
            self.lines.extend(_wrap(body, width, glyph, [("  ", HEADING)]))
            return index + 1
        if _RULE.match(line):
            self.lines.append([("─" * width, TEXT)])
            return index + 1
        bullet = _BULLET.match(line)
        if bullet:
            indent, marker, body = bullet.groups()
            level = min(len(indent) // 2, 6)
            glyph = BULLET_GLYPHS[level % len(BULLET_GLYPHS)] if marker in "-*+" else marker
            lead = "  " * level
            prefix = [(lead, TEXT), (glyph + " ", BULLET)]
            hang = [(lead + " " * (display_width(glyph) + 1), TEXT)]
            self.lines.extend(_wrap(inline(body), width, prefix, hang))
            return index + 1
        if _QUOTE.match(line):
            depth, rest = _quote_depth(line)
            bar = [(QUOTE_BAR * depth, QUOTE)]
            self.lines.extend(_wrap(inline(rest, QUOTE), width, bar, bar))
            return index + 1
        self.lines.extend(_wrap(inline(line), width))
        return index + 1

    # Fenced code -----------------------------------------------------------

    def fenced(self, lines, index, fence):
        indent, marker, lang = fence.group(1), fence.group(2), fence.group(3).lower()
        end = index + 1
        body = []
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped.startswith(marker[0] * len(marker)) and not stripped.strip(marker[0]):
                end += 1
                break
            line = lines[end]
            body.append(line[len(indent):] if line.startswith(indent) else line.lstrip())
            end += 1
        self.code_block(body, lang)
        return end

    def code_block(self, body, lang):
        width = self.width
        top = [("┌─", CODE_BORDER)]
        if lang:
            top += [(" ", CODE_BORDER), (lang, CODE_LANG), (" ", CODE_BORDER)]
        used = sum(display_width(t) for t, _ in top)
        top.append(("─" * max(0, width - used), CODE_BORDER))
        self.lines.extend(_wrap(top, width, hard=True))
        inner = width - 2
        if inner < 2:
            gutter, rest = [], []
            inner = width
        else:
            gutter = [("│ ", CODE_BORDER)]
            rest = [("│", CODE_BORDER), (CONTINUATION_MARK, CODE_BORDER)]
        if lang in ("diff", "patch", "udiff"):
            for line in self.diff(body, inner):
                self.lines.append(_merge(gutter + line))
        else:
            for segments in highlight(body, lang):
                self.lines.extend(_wrap(segments, width, gutter, rest, hard=True))
        self.lines.append([("└" + "─" * (width - 1), CODE_BORDER)])

    # Tables ----------------------------------------------------------------

    def table(self, header_line, separator_line, body_lines):
        aligns = []
        for cell in _split_row(separator_line):
            cell = cell.strip()
            if cell.startswith(":") and cell.endswith(":"):
                aligns.append("center")
            elif cell.endswith(":"):
                aligns.append("right")
            else:
                aligns.append("left")
        header = _split_row(header_line)
        columns = max(1, len(header))
        aligns = (aligns + ["left"] * columns)[:columns]
        rows = [_fit_cells(header, columns)] + [_fit_cells(_split_row(b), columns) for b in body_lines]
        cells = [[inline(cell.strip(), TABLE_HEADER if r == 0 else TABLE_CELL) for cell in row]
                 for r, row in enumerate(rows)]
        natural = [max(1, max(_segments_width(row[c]) for row in cells)) for c in range(columns)]
        minimum = [max(1, max(_widest_unit(row[c]) for row in cells)) for c in range(columns)]
        room = self.width - (3 * columns + 1)
        if room < sum(minimum):
            return self.table_as_list(cells)
        widths = _fit_columns(natural, minimum, room)
        border = TABLE_BORDER

        def rule(left, middle, right):
            return [(left + middle.join("─" * (w + 2) for w in widths) + right, border)]

        out = [rule("┌", "┬", "┐")]
        for r, row in enumerate(cells):
            token = TABLE_HEADER if r == 0 else TABLE_CELL
            wrapped = [_wrap(row[c], widths[c]) for c in range(columns)]
            height = max(len(w) for w in wrapped)
            for line in range(height):
                segments = [("│", border)]
                for c in range(columns):
                    part = wrapped[c][line] if line < len(wrapped[c]) else []
                    segments.append((" ", token))
                    segments.extend(_pad(part, widths[c], token, aligns[c]))
                    segments.extend([(" ", token), ("│", border)])
                out.append(_merge(segments))
            if r == 0:
                out.append(rule("├", "┼", "┤"))
        out.append(rule("└", "┴", "┘"))
        return out

    def table_as_list(self, cells):
        """Too narrow for borders: each row as ``header: value`` lines."""
        header, out = cells[0], []
        for row in cells[1:] or [header]:
            out.append([("─" * self.width, TABLE_BORDER)])
            for name, value in zip(header, row):
                label = list(name) + [(": ", TABLE_HEADER)]
                out.extend(_wrap(label + list(value), self.width, (), [("  ", TABLE_CELL)]))
        return out

    # Diffs -----------------------------------------------------------------

    def diff(self, lines, width):
        numbered = _number_diff(lines)
        if self.collapse and len(numbered) > DIFF_COLLAPSE_OVER:
            hidden = len(numbered) - DIFF_KEEP_HEAD - DIFF_KEEP_TAIL
            self.collapsed += hidden
            shown = numbered[:DIFF_KEEP_HEAD] + [None] + numbered[-DIFF_KEEP_TAIL:]
        else:
            hidden, shown = 0, numbered
        largest = max([n for _, _, old, new in numbered for n in (old, new) if n] or [0])
        digits = len(str(largest)) if largest else 0
        gutter_width = 2 * digits + 2 if digits else 0
        if gutter_width and width - gutter_width < 8:
            gutter_width = digits = 0
        out = []
        for item in shown:
            if item is None:
                gutter = [(" " * gutter_width, DIFF_LINENO)] if gutter_width else []
                out.extend(_wrap([(f"… 已收合 {hidden} 行", DIFF_META)], width, gutter, gutter))
                continue
            line, token, old, new = item
            if gutter_width:
                old_text = str(old) if old else ""
                new_text = str(new) if new else ""
                gutter = [(f"{old_text:>{digits}} {new_text:>{digits}} ", DIFF_LINENO)]
                blank = [(" " * gutter_width, DIFF_LINENO)]
            else:
                gutter = blank = []
            out.extend(_wrap([(line, token)], width, gutter, blank, hard=True))
        return out


def _diff_like(line):
    if not line:
        return False
    return line[0] in " +-\\@" or line.startswith(_DIFF_META_PREFIXES)


def _number_diff(lines):
    """``(line, token, old_number, new_number)`` for each diff line."""
    out = []
    old = new = 0
    old_left = new_left = 0
    for line in lines:
        if old_left > 0 or new_left > 0:
            mark = line[:1]
            if mark == "+":
                new += 1
                new_left -= 1
                out.append((line, DIFF_ADD, None, new))
                continue
            if mark == "-":
                old += 1
                old_left -= 1
                out.append((line, DIFF_DEL, old, None))
                continue
            if mark in (" ", ""):
                old += 1
                new += 1
                old_left -= 1
                new_left -= 1
                out.append((line, TEXT, old, new))
                continue
            if mark == "\\":
                out.append((line, DIFF_META, None, None))
                continue
            old_left = new_left = 0
        hunk = _HUNK.match(line)
        if hunk:
            old = int(hunk.group(1)) - 1
            new = int(hunk.group(3)) - 1
            old_left = int(hunk.group(2)) if hunk.group(2) is not None else 1
            new_left = int(hunk.group(4)) if hunk.group(4) is not None else 1
            out.append((line, DIFF_HUNK, None, None))
        elif line.startswith(_DIFF_META_PREFIXES) or line.startswith("\\"):
            out.append((line, DIFF_META, None, None))
        elif line.startswith("+"):
            out.append((line, DIFF_ADD, None, None))
        elif line.startswith("-"):
            out.append((line, DIFF_DEL, None, None))
        else:
            out.append((line, TEXT, None, None))
    return out


def _quote_depth(line):
    depth = 0
    rest = line.lstrip()
    while rest.startswith(">"):
        depth += 1
        rest = rest[1:]
        if rest.startswith(" "):
            rest = rest[1:]
        rest_stripped = rest.lstrip()
        if rest_stripped.startswith(">"):
            rest = rest_stripped
    return depth, rest


def _split_row(line):
    """Cells of a pipe-table row; ``\\|`` is a literal pipe."""
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells, current, position = [], [], 0
    while position < len(row):
        char = row[position]
        if char == "\\" and position + 1 < len(row) and row[position + 1] == "|":
            current.append("|")
            position += 2
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        position += 1
    cells.append("".join(current))
    return cells


def _fit_cells(cells, columns):
    if len(cells) > columns:
        return cells[:columns - 1] + [" | ".join(c.strip() for c in cells[columns - 1:])]
    return cells + [""] * (columns - len(cells))


def _segments_width(segments):
    return sum(display_width(text) for text, _ in segments)


def _widest_unit(segments):
    return max([cells for text, _ in segments for _, cells in _clusters(text)] or [1])


def _fit_columns(natural, minimum, room):
    """Column widths within ``room``: shrink the widest columns first."""
    if sum(natural) <= room:
        return list(natural)
    low, high = 0, max(natural)
    while low < high:          # largest cap whose capped widths fit
        cap = (low + high + 1) // 2
        if sum(min(n, max(cap, m)) for n, m in zip(natural, minimum)) <= room:
            low = cap
        else:
            high = cap - 1
    widths = [min(n, max(low, m)) for n, m in zip(natural, minimum)]
    spare = room - sum(widths)
    for index in range(len(widths)):
        if spare <= 0:
            break
        if widths[index] < natural[index]:
            widths[index] += 1
            spare -= 1
    return widths


# --- syntax colouring ------------------------------------------------------

_PY_BUILTINS = frozenset(name for name in dir(builtins) if not name.startswith("_"))
_PY_KEYWORDS = frozenset(keyword.kwlist)
_PY_SOFT_KEYWORDS = frozenset(getattr(keyword, "softkwlist", ())) - {"_"} - _PY_BUILTINS
_PY_STRING_TOKENS = frozenset(
    number for number, name in tokenize.tok_name.items()
    if name in ("STRING", "FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END",
                "TSTRING_START", "TSTRING_MIDDLE", "TSTRING_END")
)
_LANG_ALIASES = {
    "python": "python", "py": "python", "python3": "python", "py3": "python",
    "json": "json", "jsonc": "json", "json5": "json",
    "sh": "sh", "bash": "sh", "shell": "sh", "zsh": "sh", "console": "sh", "shell-session": "sh",
}


def highlight(lines, lang):
    """Per-line ``(text, token)`` segments for a code block; unknown languages are plain."""
    kind = _LANG_ALIASES.get((lang or "").lower())
    try:
        if kind == "python":
            return _paint(lines, _python_spans(lines))
        if kind == "json":
            return _paint(lines, [_json_spans(line) for line in lines])
        if kind == "sh":
            return _paint(lines, [_sh_spans(line) for line in lines])
    except Exception:
        pass
    return [[(line, CODE_BLOCK)] if line else [] for line in lines]


def _paint(lines, spans):
    out = []
    for line, line_spans in zip(lines, spans):
        segments, position = [], 0
        for start, end, token in sorted(line_spans):
            start, end = max(start, position), min(end, len(line))
            if start >= end:
                continue
            segments.append((line[position:start], CODE_BLOCK))
            segments.append((line[start:end], token))
            position = end
        segments.append((line[position:], CODE_BLOCK))
        out.append(_merge(segments))
    return out


def _python_spans(lines):
    spans = [[] for _ in lines]
    source = io.StringIO("".join(line + "\n" for line in lines))
    try:
        for kind, value, (start_row, start_col), (end_row, end_col), _ in \
                tokenize.generate_tokens(source.readline):
            if kind == tokenize.NAME:
                if value in _PY_KEYWORDS or value in _PY_SOFT_KEYWORDS:
                    token = SYN_KEYWORD
                elif value in _PY_BUILTINS:
                    token = SYN_BUILTIN
                else:
                    continue
            elif kind in _PY_STRING_TOKENS:
                token = SYN_STRING
            elif kind == tokenize.COMMENT:
                token = SYN_COMMENT
            elif kind == tokenize.NUMBER:
                token = SYN_NUMBER
            else:
                continue
            for row in range(start_row, end_row + 1):
                if row - 1 >= len(lines):
                    break
                first = start_col if row == start_row else 0
                last = end_col if row == end_row else len(lines[row - 1])
                spans[row - 1].append((first, last, token))
    except Exception:
        pass   # tokenize.TokenError, SyntaxError ...: unterminated input: keep what was coloured so far
    return spans


_JSON_TOKEN = re.compile(
    r'(?P<string>"(?:\\.|[^"\\])*"?)'
    r"|(?P<number>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)"
    r"|(?P<keyword>\b(?:true|false|null)\b)"
    r"|(?P<comment>//.*$)"
)


def _json_spans(line):
    return [(m.start(), m.end(), "syntax." + m.lastgroup) for m in _JSON_TOKEN.finditer(line)]


_SH_KEYWORDS = frozenset(
    "if then else elif fi for while until do done case esac in function select return time".split())
_SH_BUILTINS = frozenset(
    "echo cd export local set unset read printf test exit source eval exec shift trap alias "
    "unalias declare readonly type command builtin wait kill pwd pushd popd true false".split())
_SH_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_SH_NUMBER = re.compile(r"\d+\b")


def _sh_spans(line):
    spans, position, length = [], 0, len(line)
    while position < length:
        char = line[position]
        if char == "#" and (position == 0 or line[position - 1].isspace()):
            spans.append((position, length, SYN_COMMENT))
            break
        if char in "'\"":
            end = position + 1
            while end < length and line[end] != char:
                end += 2 if (char == '"' and line[end] == "\\") else 1
            end = min(end + 1, length)
            spans.append((position, end, SYN_STRING))
            position = end
            continue
        if char == "\\":
            position += 2
            continue
        if char == "$":
            word = _SH_WORD.match(line, position + 1)
            if word:
                spans.append((position, word.end(), SYN_BUILTIN))
                position = word.end()
                continue
        boundary = position == 0 or not (line[position - 1].isalnum() or line[position - 1] in "_-./$")
        if boundary and (char.isalpha() or char == "_"):
            word = _SH_WORD.match(line, position)
            value = word.group(0)
            if value in _SH_KEYWORDS:
                spans.append((position, word.end(), SYN_KEYWORD))
            elif value in _SH_BUILTINS:
                spans.append((position, word.end(), SYN_BUILTIN))
            position = word.end()
            continue
        if boundary and char.isdigit():
            number = _SH_NUMBER.match(line, position)
            if number:
                spans.append((position, number.end(), SYN_NUMBER))
                position = number.end()
                continue
        position += 1
    return spans
