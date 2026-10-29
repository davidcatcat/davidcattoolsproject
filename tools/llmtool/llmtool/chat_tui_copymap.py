"""Display -> source offsets for replies rendered by ``tui_markdown`` (W56a, Codex v3).

A copy of part of a rendered reply must give back the reply's own source text. This
module lays a reply out the way ``chat_tui_view.render_markdown`` does, using
``tui_markdown``'s own helpers, and records for every displayed character the source
offset it came from, or UNMAPPED for a character the layout inserted (borders, bullet
glyphs, padding, the body indent, ``": "`` of a table list, the separators a link adds).
Provenance is tracked, never guessed from character classes: a literal ``•`` or ``│``
in the source keeps its offset, an added one has none.

Every block's reconstruction is checked against the renderer's actual rows; a block
that does not match exactly (a renderer change) is left wholly UNMAPPED, so a copy is
never wrong, only shorter. Pure Python; no prompt_toolkit.
"""

import array

UNMAPPED = -1


class Mismatch(Exception):
    """The reconstruction differs from the renderer's output."""


def display_map(source, width, flow_width, blocks, md, indent=""):
    """(display text, offsets) of ``source`` rendered as ``render_markdown`` shows it:
    rows joined by newlines, each prefixed with ``indent``. ``offsets[i]`` is the source
    offset of display character ``i`` or UNMAPPED. ``blocks`` is
    ``chat_tui_view._markdown_blocks`` (the same split the display used)."""
    rows = []    # (text, offsets, source line index or None, last row of that line)
    if md is None or "\r" in source:
        if md is None:
            starts = _line_starts(source)
            for number, line in enumerate(source.split("\n")):
                rows.append((line, list(range(starts[number], starts[number] + len(line))), number, True))
        else:
            for line in _render_rows(md, source, max(1, width)):
                rows.append((line, [UNMAPPED] * len(line), None, True))
    else:
        raw = source.split("\n")
        starts = _line_starts(source)
        first = 0
        for fit, chunk in blocks(source):
            block_width = max(1, width) if fit else flow_width
            expected = _render_rows(md, "\n".join(chunk), block_width)
            try:
                mapped = _Mapper(md, raw, starts, block_width).block(first, len(chunk))
                if [text for text, _o, _l, _last in mapped] != expected:
                    raise Mismatch()
            except Exception:   # Mismatch, or a renderer change the mirror does not follow
                mapped = [(text, [UNMAPPED] * len(text), None, True) for text in expected]
            rows.extend(mapped)
            first += len(chunk)
    text, offsets = [], array.array("q")
    for number, (row, row_offsets, line, _last) in enumerate(rows):
        if number:
            before = rows[number - 1]
            joined = (before[2] is not None and line is not None and before[3]
                      and line == before[2] + 1)
            text.append("\n")
            offsets.append(starts[line] - 1 if joined else UNMAPPED)
        text.append(indent + row)
        offsets.extend([UNMAPPED] * len(indent))
        offsets.extend(row_offsets)
    return "".join(text), offsets


def _line_starts(source):
    starts, position = [], 0
    for line in source.split("\n"):
        starts.append(position)
        position += len(line) + 1
    return starts


def _render_rows(md, text, width):
    return ["".join(part for part, _token in line) for line in md.render(text, width)]


def _rows_text(lines):
    return ["".join(part for part, _token in line) for line in lines]


class _Mapper:
    """Rows (text, offsets, line index, last row) for source lines of one block."""

    def __init__(self, md, raw, starts, width):
        self.md, self.raw, self.starts, self.width = md, raw, starts, width

    # -- source lines as the renderer sees them (tabs expanded, controls replaced) -------
    def line(self, index):
        md, line, base = self.md, self.raw[index], self.starts[index]
        chars, origin = [], []
        for position, char in enumerate(line):
            if char == "\t":
                count = md.TAB_WIDTH - len(chars) % md.TAB_WIDTH
                chars.extend(" " * count)
                origin.extend([base + position] * count)
            else:
                chars.append("�" if md._CONTROL.fullmatch(char) else char)
                origin.append(base + position)
        return "".join(chars), origin

    # -- blocks ---------------------------------------------------------------------------
    def block(self, first, count):
        md = self.md
        lines = [self.line(index) for index in range(first, first + count)]
        texts = [text for text, _origin in lines]
        nonblank = next((i for i, text in enumerate(texts) if text.strip()), None)
        if nonblank is not None and md._is_diff_start(texts, nonblank):
            end = len(texts)
            while end > nonblank and not texts[end - 1].strip():
                end -= 1
            rows = [("", [], first + i, True) for i in range(nonblank)]
            return rows + self.diff(lines[nonblank:end], first + nonblank, self.width, "")
        rows, index = [], 0
        while index < count:
            text, origin = lines[index]
            number = first + index
            if not text.strip():
                rows.append(("", [], number, True))
                index += 1
                continue
            fence = md._FENCE.match(text)
            if fence and not (fence.group(2)[0] == "`" and "`" in fence.group(4)):
                done, more = self.fenced(lines, index, fence, first)
                rows.extend(done)
                index = more
                continue
            if md._is_diff_start(texts, index):
                end = index + 1
                while end < count and md._diff_like(texts[end]):
                    end += 1
                rows.extend(self.diff(lines[index:end], number, self.width, ""))
                index = end
                continue
            if "|" in text and index + 1 < count and "|" in texts[index + 1] \
                    and md._is_table_separator(texts[index + 1]):
                end = index + 2
                while end < count and "|" in texts[end] and texts[end].strip():
                    end += 1
                rows.extend(self.table(lines[index:end], number))
                index = end
                continue
            rows.extend(self.prose(text, origin, number))
            index += 1
        return rows

    def prose(self, text, origin, number):
        md, width = self.md, self.width
        heading = md._HEADING.match(text)
        if heading:
            rest = text[heading.end():]
            body = md._heading_text(rest)
            start = heading.end() + len(rest) - len(rest.lstrip())
            stream = self.inline(body, origin[start:start + len(body)])
            glyph = [(md.HEADING_GLYPH + " ", md.HEADING)]
            lines = md._wrap(md.inline(body, md.HEADING), width, glyph, [("  ", md.HEADING)])
            return self.walk(lines, number, stream, [UNMAPPED] * 2, [UNMAPPED] * 2)
        if md._RULE.match(text):
            return [("─" * width, [UNMAPPED] * width, number, True)]
        bullet = md._BULLET.match(text)
        if bullet:
            lead_text, marker, body = bullet.groups()
            level = min(len(lead_text) // 2, 6)
            glyph = md.BULLET_GLYPHS[level % len(md.BULLET_GLYPHS)] if marker in "-*+" else marker
            lead = "  " * level
            marker_offsets = (origin[bullet.start(2):bullet.end(2)] if glyph == marker
                              else [UNMAPPED] * len(glyph))   # an added glyph has no source
            first = [UNMAPPED] * len(lead) + marker_offsets + [UNMAPPED]
            hang = [UNMAPPED] * (len(lead) + md.display_width(glyph) + 1)
            prefix = [(lead, md.TEXT), (glyph + " ", md.BULLET)]
            hang_segments = [(lead + " " * (md.display_width(glyph) + 1), md.TEXT)]
            lines = md._wrap(md.inline(body), width, prefix, hang_segments)
            stream = self.inline(body, origin[bullet.start(3):bullet.end(3)])
            return self.walk(lines, number, stream, first, hang)
        if md._QUOTE.match(text):
            depth, rest = md._quote_depth(text)
            depth = min(depth, max(1, (width - 2) // (2 * len(md.QUOTE_BAR))))
            bar = [(md.QUOTE_BAR * depth, md.QUOTE)]
            lines = md._wrap(md.inline(rest, md.QUOTE), width, bar, bar)
            start = len(text) - len(rest)
            stream = self.inline(rest, origin[start:])
            marks = [UNMAPPED] * len(md.QUOTE_BAR * depth)
            return self.walk(lines, number, stream, marks, marks)
        lines = md._wrap(md.inline(text), width)
        return self.walk(lines, number, self.inline(text, origin), [], [])

    def walk(self, lines, number, stream, first, hang):
        """Map soft-wrapped rows onto a character stream; spaces the wrap dropped are
        skipped, every other character must match the stream in order."""
        chars, offsets = stream
        rows, position = [], 0
        texts = _rows_text(lines)
        for row_number, text in enumerate(texts):
            prefix = first if row_number == 0 else hang
            if len(text) < len(prefix):
                raise Mismatch()
            mapped = list(prefix)
            for char in text[len(prefix):]:
                while position < len(chars) and chars[position] != char and chars[position] == " ":
                    position += 1
                if position >= len(chars) or chars[position] != char:
                    raise Mismatch()
                mapped.append(offsets[position])
                position += 1
            rows.append((text, mapped, number, row_number == len(texts) - 1))
        if any(char != " " for char in chars[position:]):
            raise Mismatch()
        return rows

    def strict(self, lines, number, chars, offsets, prefix_lengths):
        """Hard-wrapped rows: each row is a prefix then the next characters of the line."""
        rows, position = [], 0
        texts = _rows_text(lines)
        for row_number, text in enumerate(texts):
            size = prefix_lengths[0 if row_number == 0 else 1]
            piece = text[size:]
            if chars[position:position + len(piece)] != piece:
                raise Mismatch()
            rows.append((text, [UNMAPPED] * size + list(offsets[position:position + len(piece)]),
                         number, row_number == len(texts) - 1))
            position += len(piece)
        if position != len(chars):
            raise Mismatch()
        return rows

    # -- inline ---------------------------------------------------------------------------
    def inline(self, text, origin, depth=0):
        """(characters, offsets) of ``tui_markdown.inline(text)``, checked against it."""
        chars, offsets = _inline(self.md, text, list(origin), depth)
        if "".join(chars) != "".join(part for part, _token in self.md.inline(text)):
            raise Mismatch()
        return chars, offsets

    # -- fenced code ----------------------------------------------------------------------
    def fenced(self, lines, index, fence, first):
        md, width = self.md, self.width
        indent, marker, lang = fence.group(1), fence.group(2), fence.group(3).lower()
        text, origin = lines[index]
        rows = []
        top = [("┌─", md.CODE_BORDER)]
        top_offsets = [UNMAPPED] * 2
        if lang:
            top += [(" ", md.CODE_BORDER), (lang, md.CODE_LANG), (" ", md.CODE_BORDER)]
            top_offsets += [UNMAPPED] + origin[fence.start(3):fence.end(3)] + [UNMAPPED]
        used = sum(md.display_width(t) for t, _ in top)
        top.append(("─" * max(0, width - used), md.CODE_BORDER))
        top_offsets += [UNMAPPED] * max(0, width - used)
        top_text = "".join(part for part, _token in top)
        rows.extend(self.strict(md._wrap(top, width, hard=True), first + index, top_text,
                                top_offsets, (0, 0)))
        end, body = index + 1, []
        closed = None
        while end < len(lines):
            line, line_origin = lines[end]
            stripped = line.strip()
            if stripped.startswith(marker[0] * len(marker)) and not stripped.strip(marker[0]):
                closed = first + end
                end += 1
                break
            if line.startswith(indent):
                body.append((line[len(indent):], line_origin[len(indent):], first + end))
            else:
                cut = len(line) - len(line.lstrip())
                body.append((line[cut:], line_origin[cut:], first + end))
            end += 1
        inner = width - 2
        if inner < 2:
            gutter, rest, inner = [], [], width
        else:
            gutter = [("│ ", md.CODE_BORDER)]
            rest = [("│", md.CODE_BORDER), (md.CONTINUATION_MARK, md.CODE_BORDER)]
        size = (len("│ ") if gutter else 0, len("│" + md.CONTINUATION_MARK) if rest else 0)
        body_lines = [line for line, _o, _n in body]
        if lang in ("diff", "patch", "udiff"):
            rows.extend(self.diff([(line, line_origin) for line, line_origin, _n in body],
                                  body[0][2] if body else None, inner, "│ " if gutter else "",
                                  numbers=[n for _l, _o, n in body]))
        else:
            for segments, (line, line_origin, number) in zip(md.highlight(body_lines, lang), body):
                rows.extend(self.strict(md._wrap(segments, width, gutter, rest, hard=True),
                                        number, line, line_origin, size))
        bottom = "└" + "─" * (width - 1)
        rows.append((bottom, [UNMAPPED] * len(bottom), closed, True))
        return rows, end

    # -- diffs ----------------------------------------------------------------------------
    def diff(self, lines, number, width, gutter_text, numbers=None):
        md = self.md
        texts = [text for text, _origin in lines]
        numbers = numbers or [number + i for i in range(len(lines))]
        numbered = md._number_diff(texts)
        indexed = list(range(len(numbered)))
        if len(numbered) > md.DIFF_COLLAPSE_OVER:   # the TUI renders with collapse on
            hidden = len(numbered) - md.DIFF_KEEP_HEAD - md.DIFF_KEEP_TAIL
            shown = indexed[:md.DIFF_KEEP_HEAD] + [None] + indexed[-md.DIFF_KEEP_TAIL:]
        else:
            hidden, shown = 0, indexed
        largest = max([n for _, _, old, new in numbered for n in (old, new) if n] or [0])
        digits = len(str(largest)) if largest else 0
        gutter_width = 2 * digits + 2 if digits else 0
        if gutter_width and width - gutter_width < 8:
            gutter_width = digits = 0
        outer = [UNMAPPED] * len(gutter_text)
        rows = []
        for item in shown:
            if item is None:
                gutter = [(" " * gutter_width, md.DIFF_LINENO)] if gutter_width else []
                for text in _rows_text(md._wrap([(f"… 已收合 {hidden} 行", md.DIFF_META)],
                                                width, gutter, gutter)):
                    rows.append((gutter_text + text, outer + [UNMAPPED] * len(text), None, True))
                continue
            line, token, old, new = numbered[item]
            if gutter_width:
                gutter = [(f"{str(old) if old else '':>{digits}} {str(new) if new else '':>{digits}} ",
                           md.DIFF_LINENO)]
                blank = [(" " * gutter_width, md.DIFF_LINENO)]
            else:
                gutter = blank = []
            mapped = self.strict(md._wrap([(line, token)], width, gutter, blank, hard=True),
                                 numbers[item], line, lines[item][1], (gutter_width, gutter_width))
            rows.extend((gutter_text + text, outer + offsets, n, last)
                        for text, offsets, n, last in mapped)
        return rows

    # -- tables ---------------------------------------------------------------------------
    def table(self, lines, number):
        md, width = self.md, self.width
        header_line, separator_line, body_lines = lines[0], lines[1], lines[2:]
        aligns = []
        for cell in md._split_row(separator_line[0]):
            cell = cell.strip()
            aligns.append("center" if cell.startswith(":") and cell.endswith(":")
                          else "right" if cell.endswith(":") else "left")
        header = _split_cells(header_line)
        columns = max(1, len(header))
        aligns = (aligns + ["left"] * columns)[:columns]
        rows = [_fit_cells(header, columns)] + [_fit_cells(_split_cells(line), columns)
                                                for line in body_lines]
        numbers = [number] + [number + 2 + i for i in range(len(body_lines))]
        cells, streams = [], []
        for r, row in enumerate(rows):
            token = md.TABLE_HEADER if r == 0 else md.TABLE_CELL
            cell_segments, cell_streams = [], []
            for text, origin in row:
                stripped, stripped_origin = _strip(text, origin)
                cell_segments.append(md.inline(stripped, token))
                cell_streams.append(self.inline(stripped, stripped_origin))
            cells.append(cell_segments)
            streams.append(cell_streams)
        room = width - (3 * columns + 1)
        if room < columns:
            return self.table_list(cells, streams, numbers)
        natural = [max(1, max(md._segments_width(row[c]) for row in cells)) for c in range(columns)]
        minimum = [max(1, max(md._widest_unit(row[c]) for row in cells)) for c in range(columns)]
        if room < sum(minimum):
            return self.table_list(cells, streams, numbers)
        widths = md._fit_columns(natural, minimum, room)

        def rule(left, middle, right, line):
            text = left + middle.join("─" * (w + 2) for w in widths) + right
            return (text, [UNMAPPED] * len(text), line, True)

        out = [rule("┌", "┬", "┐", None)]
        for r, row in enumerate(cells):
            token = md.TABLE_HEADER if r == 0 else md.TABLE_CELL
            wrapped, maps = [], []
            for c in range(columns):
                parts = md._wrap(row[c], widths[c])
                wrapped.append(parts)
                maps.append(self.walk(parts, numbers[r], streams[r][c], [], []))
            height = max(len(parts) for parts in wrapped)
            for line in range(height):
                text, offsets = "│", [UNMAPPED]
                for c in range(columns):
                    part = wrapped[c][line] if line < len(wrapped[c]) else []
                    part_offsets = maps[c][line][1] if line < len(maps[c]) else []
                    padded = md._pad(part, widths[c], token, aligns[c])
                    left, right = len(padded[0][0]), len(padded[-1][0])
                    part_text = "".join(t for t, _ in part)
                    text += " " + " " * left + part_text + " " * right + " │"
                    offsets += ([UNMAPPED] * (1 + left) + list(part_offsets)
                                + [UNMAPPED] * (right + 2))
                out.append((text, offsets, numbers[r], line == height - 1))
            if r == 0:
                out.append(rule("├", "┼", "┤", number + 1))
        out.append(rule("└", "┴", "┘", None))
        return out

    def table_list(self, cells, streams, numbers):
        md, width = self.md, self.width
        header, out = cells[0], []
        body = list(zip(cells[1:], streams[1:], numbers[1:])) or [
            ([[] for _ in header], [([], []) for _ in header], None)]
        for row, row_streams, line in body:
            out.append(("─" * width, [UNMAPPED] * width, None, True))
            for c, (name, value) in enumerate(zip(header, row)):
                if not name and not value:
                    continue
                label = list(name) + [(": ", md.TABLE_HEADER)]
                name_chars, name_offsets = streams[0][c]
                value_chars, value_offsets = row_streams[c]
                stream = (list(name_chars) + [":", " "] + list(value_chars),
                          list(name_offsets) + [UNMAPPED, UNMAPPED] + list(value_offsets))
                lines = md._wrap(label + list(value), width, (), [("  ", md.TABLE_CELL)])
                out.extend((text, offsets, None, True) for text, offsets, _n, _last
                           in self.walk(lines, line, stream, [], [UNMAPPED] * 2))
        return out


def _split_cells(line):
    """``tui_markdown._split_row`` with each cell's source offsets."""
    text, origin = line
    cut = len(text) - len(text.lstrip())
    row, row_origin = text.strip(), origin[cut:cut + len(text.strip())]
    if row.startswith("|"):
        row, row_origin = row[1:], row_origin[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row, row_origin = row[:-1], row_origin[:-1]
    cells, current, offsets, position = [], [], [], 0
    while position < len(row):
        char = row[position]
        if char == "\\" and position + 1 < len(row) and row[position + 1] == "|":
            current.append("|")
            offsets.append(row_origin[position + 1])
            position += 2
            continue
        if char == "|":
            cells.append(("".join(current), offsets))
            current, offsets = [], []
        else:
            current.append(char)
            offsets.append(row_origin[position])
        position += 1
    cells.append(("".join(current), offsets))
    return cells


def _strip(text, origin):
    cut = len(text) - len(text.lstrip())
    stripped = text.strip()
    return stripped, origin[cut:cut + len(stripped)]


def _fit_cells(cells, columns):
    if len(cells) > columns:
        extra = [_strip(text, origin) for text, origin in cells[columns - 1:]]
        text, origin = "", []
        for number, (part, part_origin) in enumerate(extra):
            if number:
                text += " | "
                origin += [UNMAPPED] * 3
            text += part
            origin += part_origin
        return cells[:columns - 1] + [(text, origin)]
    return cells + [("", [])] * (columns - len(cells))


def _inline(md, text, origin, depth):
    """The characters ``tui_markdown.inline`` shows for ``text`` with their offsets."""
    if depth > md.INLINE_DEPTH:
        return list(text), list(origin)
    chars, offsets = [], []
    position, length = 0, len(text)
    ticks = md._tick_runs(text) if "`" in text else {}
    failed = {}

    def plain(low, high):
        chars.extend(text[low:high])
        offsets.extend(origin[low:high])

    while position < length:
        match = md._INLINE_SPECIAL.search(text, position)
        if match is None:
            plain(position, length)
            break
        start = match.start()
        plain(position, start)
        char = text[start]
        if char == "\\":
            if start + 1 < length and text[start + 1] in md._ESCAPABLE:
                plain(start + 1, start + 2)
                position = start + 2
            else:
                plain(start, start + 1)
                position = start + 1
            continue
        if char == "`":
            run = md._run_length(text, start, "`")
            close = md._next_tick_run(ticks, start + run, run)
            if close < 0:
                plain(start, start + run)
                position = start + run
                continue
            low, high = start + run, close
            code = text[low:high]
            if len(code) > 2 and code[0] == " " and code[-1] == " " and code.strip():
                low, high = low + 1, high - 1
            plain(low, high)
            position = close + run
            continue
        if char == "[":
            link = md._LINK.match(text, start)
            if link is None:
                plain(start, start + 1)
                position = start + 1
                continue
            label, url = link.group(1), link.group(2)
            label_chars, label_offsets = (_inline(md, label, origin[link.start(1):link.end(1)], depth + 1)
                                          if label else ([], []))
            chars.extend(label_chars)
            offsets.extend(label_offsets)
            if url and url != label:
                if label_chars:
                    chars.append(" ")
                    offsets.append(UNMAPPED)       # the space the renderer adds
                opening = text.index("(", link.end(1))
                chars.append("(")
                offsets.append(origin[opening])
                plain(link.start(2), link.end(2))
                chars.append(")")
                offsets.append(origin[link.end() - 1])
            position = link.end()
            continue
        if char == "h":
            if start and (text[start - 1].isalnum() or text[start - 1] in "/:"):
                plain(start, start + 1)
                position = start + 1
                continue
            url = md._BARE_URL.match(text, start)
            stripped = url.group(0).rstrip(".,;:!?'\"") if url else ""
            if len(stripped) <= len(match.group(0)):
                plain(start, start + 1)
                position = start + 1
                continue
            plain(start, start + len(stripped))
            position = start + len(stripped)
            continue
        run = md._run_length(text, start, char)
        if char == "_" and start and text[start - 1].isalnum():
            plain(start, start + run)
            position = start + run
            continue
        span = md._emphasis(text, start, char, run, ticks, failed)
        if span is None:
            plain(start, start + run)
            position = start + run
            continue
        marker, close = span
        inner_chars, inner_offsets = _inline(md, text[start + marker:close],
                                             origin[start + marker:close], depth + 1)
        chars.extend(inner_chars)
        offsets.extend(inner_offsets)
        position = close + marker
    return chars, offsets
