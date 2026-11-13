"""Conversation pane model: reading position, follow-latest, selection and clipboard (W42).

prompt_toolkit's ``TextArea``/``BufferControl`` scrolls by moving the caret one *logical*
line per wheel event and the ``Window`` then re-clamps the view so that caret stays
visible (``containers.py`` ``Window._scroll_up``/``_scroll_down`` compare a logical-line
``vertical_scroll`` with a row ``window_height``; ``_scroll_when_linewrapping`` pins the
view to the caret's line). With wrapped chat lines the wheel got stuck, jumped a whole
paragraph at a time, or snapped back to the caret; PageUp/PageDown and following new
output were tied to the same caret. This module keeps the model in *screen rows* instead:

* the text is wrapped here (``get_cwidth``: CJK and emoji are two columns, tabs four), one
  row per screen line, so the pane's ``Window`` never wraps and its vertical scroll is
  exactly ``top``;
* ``top`` is the reading position; ``following`` is explicit and only true while the
  view is at the bottom. New output never moves a scrolled-back view; scrolling back
  to the bottom (wheel, keys, or the drag auto-scroll) resumes following;
* a resize or a re-wrap keeps the text that was at the top of the view at the top;
* the selection is a pair of text offsets, so the copied text is exactly the selected
  text whatever the wrapping (CJK, long lines, several screens); a drag beyond the
  pane edge scrolls and extends it. The selection is copied to the system clipboard
  (``pbcopy`` on macOS) while the drag moves and again on mouse up: Terminal.app has no
  OSC 52, and neither it nor Warp passes Cmd+C to the application (Warp runs its own
  Copy action), so copying at selection time is the only way the clipboard can hold the
  pane's selection. Warp also reports no release when the button goes up outside its
  grid, which is why a moving drag copies too (W52 F3).

Styles (W56a): the pane text carries ``(start, end, token)`` spans; ``STYLE_TOKENS`` holds
one table per theme (dark, light, mono) mapping a display token to a prompt_toolkit style,
and a theme swaps the whole table (W56d).
A ``rule`` line is drawn across the pane width and a ``meta.usage`` line right-aligned at
draw time, so both stay right after a resize. ``render_markdown`` uses ``tui_markdown``
when it is installed and otherwise shows the text unchanged.

Pure Python apart from ``make_pane`` (the prompt_toolkit control), so it is unit-testable
without a terminal.
"""

import bisect
import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata

try:
    from wcwidth import iter_graphemes as _iter_graphemes   # prompt_toolkit dependency (0.9.x)
except ImportError:  # older wcwidth: a conservative cluster rule below
    _iter_graphemes = None

WHEEL_ROWS = 1            # Rows per wheel event: Terminal.app sends one event per scrolled line.
AUTOSCROLL_SECONDS = 0.05  # A drag held at or beyond the pane edge scrolls one row per tick.
DRAG_COPY_SECONDS = 0.15  # A moving drag copies its selection this long after it changed.
COPIED_SECONDS = 3.0      # How long the status bar reports a copy.
DOUBLE_CLICK_SECONDS = 0.35
TAB_WIDTH = 4
HANG_LIMIT = 8            # Most leading spaces repeated before a wrapped line's later rows.
HANG_BULLETS = ("• ", "◦ ", "▪ ")   # tui_markdown bullet glyphs
FLOW_WIDTH = 1 << 16      # Markdown prose is laid out this wide; the pane soft-wraps it.
CACHE_LINES = 4096        # Wrapped-line cache entries kept before it is cleared.
RENDER_CACHE_CHARS = 1 << 20   # Source characters the Markdown render cache may hold.
COPY_LIMIT_BYTES = 1024 * 1024   # Largest selection handed to the clipboard tool (UTF-8 bytes).
COPY_SCAN_CHUNK = 65536          # Characters measured at a time while checking that limit.
WORD_SCAN_LIMIT = COPY_LIMIT_BYTES  # Characters a double-click word may extend in either direction.
_ZWJ = "\u200d"

try:
    from . import tui_markdown as _tui_markdown   # W56b+c renderer; optional until it lands
except ImportError:
    _tui_markdown = None

# Every display token -> prompt_toolkit style, one table per theme (W56d). These three
# tables are the only place a colour is named: the TUI's own chrome (menu, help, selection,
# links) is themed through the "ui." tokens. "mono" is used for NO_COLOR or a terminal
# without colour and uses only bold/dim/reverse. Unknown tokens are drawn as "text".
THEME_DARK = {
    "text": "",
    "md.bold": "bold",
    "md.italic": "italic",
    "md.code": "fg:ansicyan",
    "md.heading": "bold fg:ansiblue",
    "md.bullet": "fg:ansicyan",
    "md.link": "fg:ansiblue underline",
    "md.quote": "fg:ansibrightblack italic",
    "md.table.border": "fg:ansibrightblack",
    "md.table.header": "bold",
    "md.table.cell": "",
    "md.codeblock.border": "fg:ansibrightblack",
    "md.codeblock.lang": "fg:ansibrightblack italic",
    "md.codeblock": "fg:ansiyellow",
    "diff.add": "fg:ansigreen",
    "diff.del": "fg:ansired",
    "diff.hunk": "fg:ansicyan",
    "diff.meta": "bold",
    "diff.lineno": "fg:ansibrightblack",
    "syntax.keyword": "fg:ansimagenta",
    "syntax.string": "fg:ansigreen",
    "syntax.comment": "fg:ansibrightblack italic",
    "syntax.number": "fg:ansiyellow",
    "syntax.builtin": "fg:ansicyan",
    "role.owner": "bold fg:ansigreen",
    "role.pm": "bold fg:ansicyan",
    "role.system": "fg:ansiyellow",
    "tool.summary": "fg:ansibrightblack",
    "tool.error": "fg:ansired",
    "meta.usage": "fg:ansibrightblack",
    "rule": "fg:ansibrightblack",
    "status.bar": "reverse",
    "status.key": "reverse bold",
    "ui.menu": "bg:ansibrightblack fg:ansiwhite",
    "ui.menu.current": "reverse",
    "ui.menu.header": "bg:ansibrightblack fg:ansiwhite bold",
    "ui.help": "italic",
    "ui.selected": "reverse",
    "ui.link": "underline",
    "ui.pr.url": "fg:ansiblue",
}
# Light terminals: the ANSI yellow and bright black most palettes ship are unreadable on a
# white background, so this table names darker RGB values (prompt_toolkit maps them to the
# terminal's colour depth).
THEME_LIGHT = {
    "text": "",
    "md.bold": "bold",
    "md.italic": "italic",
    "md.code": "fg:#005f87",
    "md.heading": "bold fg:#00539c",
    "md.bullet": "fg:#005f87",
    "md.link": "fg:#0645ad underline",
    "md.quote": "fg:#5f5f5f italic",
    "md.table.border": "fg:#8a8a8a",
    "md.table.header": "bold",
    "md.table.cell": "",
    "md.codeblock.border": "fg:#9e9e9e",
    "md.codeblock.lang": "fg:#6c6c6c italic",
    "md.codeblock": "fg:#7a4a00",
    "diff.add": "fg:#1a7f1a",
    "diff.del": "fg:#b3001b",
    "diff.hunk": "fg:#005f87",
    "diff.meta": "bold",
    "diff.lineno": "fg:#8a8a8a",
    "syntax.keyword": "fg:#8700af",
    "syntax.string": "fg:#1a7f1a",
    "syntax.comment": "fg:#6c6c6c italic",
    "syntax.number": "fg:#9a5b00",
    "syntax.builtin": "fg:#005f87",
    "role.owner": "bold fg:#1a7f1a",
    "role.pm": "bold fg:#00539c",
    "role.system": "fg:#8a5a00",
    "tool.summary": "fg:#6c6c6c",
    "tool.error": "fg:#b3001b",
    "meta.usage": "fg:#7a7a7a",
    "rule": "fg:#b2b2b2",
    "status.bar": "bg:#dadada fg:#1c1c1c",
    "status.key": "bg:#dadada fg:#000000 bold",
    "ui.menu": "bg:#e8e8e8 fg:#1c1c1c",
    "ui.menu.current": "bg:#00539c fg:#ffffff",
    "ui.menu.header": "bg:#d0d0d0 fg:#000000 bold",
    "ui.help": "italic",
    "ui.selected": "reverse",
    "ui.link": "underline",
    "ui.pr.url": "fg:#0645ad",
}
THEME_MONO = {
    "text": "",
    "md.bold": "bold",
    "md.italic": "dim",
    "md.code": "bold",
    "md.heading": "bold",
    "md.bullet": "bold",
    "md.link": "bold",
    "md.quote": "dim",
    "md.table.border": "dim",
    "md.table.header": "bold",
    "md.table.cell": "",
    "md.codeblock.border": "dim",
    "md.codeblock.lang": "dim",
    "md.codeblock": "",
    "diff.add": "bold",
    "diff.del": "dim",
    "diff.hunk": "bold",
    "diff.meta": "bold",
    "diff.lineno": "dim",
    "syntax.keyword": "bold",
    "syntax.string": "",
    "syntax.comment": "dim",
    "syntax.number": "",
    "syntax.builtin": "",
    "role.owner": "bold reverse",
    "role.pm": "bold",
    "role.system": "dim",
    "tool.summary": "dim",
    "tool.error": "bold",
    "meta.usage": "dim",
    "rule": "dim",
    "status.bar": "reverse",
    "status.key": "reverse bold",
    "ui.menu": "reverse",
    "ui.menu.current": "bold",
    "ui.menu.header": "reverse bold",
    "ui.help": "",
    "ui.selected": "reverse",
    "ui.link": "",
    "ui.pr.url": "",
}
# Theme name -> its table; a theme swaps the whole table (W56a contract).
STYLE_TOKENS = {"dark": THEME_DARK, "light": THEME_LIGHT, "mono": THEME_MONO}
RULE_CHAR = "─"
FILL_TOKENS = ("rule",)                 # drawn across the pane: "──── label" fills the row
RIGHT_TOKENS = ("meta.usage",)          # drawn flush right


def colour_enabled(environ=None):
    """False for NO_COLOR or a terminal without colour (dumb, vt*, *-mono)."""
    environ = os.environ if environ is None else environ
    if environ.get("NO_COLOR"):
        return False
    term = environ.get("TERM", "")
    return bool(term) and term != "dumb" and not term.startswith("vt") and not term.endswith("-mono")


def token_style(token, colour=True, theme="dark"):
    """prompt_toolkit style string of a display token (unknown tokens: ``text``).

    ``colour`` False (NO_COLOR, a colourless terminal) always uses the mono table.
    """
    table = STYLE_TOKENS.get(theme if colour else "mono") or THEME_DARK
    style = table.get(token)
    return table["text"] if style is None else style


def add_pr_url_styles(view, trusted_prefix):
    """Style full PR URLs from the trusted repository prefix without changing text."""
    if not isinstance(trusted_prefix, str) or not trusted_prefix:
        return
    text = view.text
    pattern = re.compile(re.escape(trusted_prefix) + r"\d{1,9}(?![\w/])")
    matches = [(match.start(), match.end()) for match in pattern.finditer(text)]
    if not matches:
        return

    spans = []
    for token, low, high in view.styled(0, len(text)):
        cuts = {low, high}
        for match_low, match_high in matches:
            if match_low < high and match_high > low:
                cuts.update((max(low, match_low), min(high, match_high)))
        cuts = sorted(cuts)
        for start, end in zip(cuts, cuts[1:]):
            styled_token = ("ui.pr.url" if any(match_low <= start and end <= match_high
                                               for match_low, match_high in matches)
                            else token)
            if styled_token != "text":
                spans.append((start, end, styled_token))
    view.spans = spans


def render_markdown(text, width):
    """Lines of (text, token) segments: ``tui_markdown.render`` when present, else plain lines.

    Tables, fenced code, diffs and rules are laid out at ``width``; prose is laid out at
    FLOW_WIDTH and soft-wrapped by the pane, so a copied paragraph has no line breaks the
    reply did not have and a resize reflows it.
    """
    if _tui_markdown is not None:
        try:
            lines = []
            blocks = _markdown_blocks(text)
            for number, (fit, chunk) in enumerate(blocks):
                # The last block may still be streaming: rendered, never cached.
                lines.extend(_render_block("\n".join(chunk), max(1, width) if fit else FLOW_WIDTH,
                                           cache=number < len(blocks) - 1))
            return lines
        except Exception:
            pass   # a renderer bug never hides the reply: show it unformatted
    return [[(line, "text")] for line in text.split("\n")]


# (block source, width) -> rendered lines of a finished block, so a streamed reply
# re-renders only its tail. Bounded by the source characters held (RENDER_CACHE_CHARS).
_RENDERED = collections.OrderedDict()
_rendered_chars = [0]


def _render_block(source, width, cache=True):
    key = (source, width)
    found = _RENDERED.get(key)
    if found is not None:
        _RENDERED.move_to_end(key)
        return found
    found = _tui_markdown.render(source, width)
    if cache and len(source) <= RENDER_CACHE_CHARS // 4:
        _RENDERED[key] = found
        _rendered_chars[0] += len(source)
        while _rendered_chars[0] > RENDER_CACHE_CHARS or len(_RENDERED) > CACHE_LINES:
            (old, _width), _lines = _RENDERED.popitem(last=False)
            _rendered_chars[0] -= len(old)
    return found


def render_cache_chars():
    """Source characters held by the Markdown render cache (tests)."""
    return _rendered_chars[0]


class SourceRegion:
    """Pane text ``[start, end)`` that shows ``source`` rendered at ``width`` with each row
    prefixed by ``indent``; copies go back to the source (Codex v2/v3 P2)."""

    __slots__ = ("start", "end", "source", "width", "indent", "_map")

    def __init__(self, start, end, source, width=0, indent=""):
        self.start, self.end, self.source = start, end, source
        self.width, self.indent, self._map = width, indent, None

    def moved(self, delta):
        region = SourceRegion(self.start + delta, self.end + delta, self.source, self.width,
                              self.indent)
        region._map = self._map
        return region

    def copy(self, shown, low, high):
        """Source text for displayed ``[low, high)`` (offsets in the pane): the whole
        source for a whole selection, else the source span from the first to the last
        selected character that came from the source (see chat_tui_copymap)."""
        if low <= self.start and high >= self.end:
            return self.source
        first, last = max(low, self.start) - self.start, min(high, self.end) - self.start
        if self._map is None:
            from . import chat_tui_copymap
            display, offsets = chat_tui_copymap.display_map(
                self.source, self.width, FLOW_WIDTH, _markdown_blocks, _tui_markdown, self.indent)
            self._map = offsets if display == shown else False
        if self._map is False:   # never expected: the pane shows other text than the map
            return shown[first:last]
        mapped = [offset for offset in self._map[first:last] if offset >= 0]
        if not mapped:
            return ""
        return self.source[min(mapped):max(mapped) + 1]


def _markdown_blocks(text):
    """[(fit, source lines)]: fit blocks (table, fence, diff, rule) need the pane width.

    Mirrors the block scan of ``tui_markdown`` so each block is rendered whole; a prose
    line is its own block (the renderer lays out each prose line on its own).
    """
    md = _tui_markdown
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is not None and md._is_diff_start(lines, first):
        return [(True, lines)]
    blocks, index = [], 0

    def add(fit, chunk):
        blocks.append((fit, list(chunk)))

    while index < len(lines):
        line = lines[index]
        end = index + 1
        fence = md._FENCE.match(line)
        if fence and not (fence.group(2)[0] == "`" and "`" in fence.group(4)):
            marker = fence.group(2)
            while end < len(lines):
                stripped = lines[end].strip()
                end += 1
                if stripped.startswith(marker[0] * len(marker)) and not stripped.strip(marker[0]):
                    break
        elif md._is_diff_start(lines, index):
            while end < len(lines) and md._diff_like(lines[end]):
                end += 1
        elif "|" in line and end < len(lines) and "|" in lines[end] \
                and md._is_table_separator(lines[end]):
            end += 1
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
        elif not md._RULE.match(line):
            add(False, [line])
            index = end
            continue
        add(True, lines[index:end])
        index = end
    return blocks


def utf8_size(text, start, end, limit, measure=None):
    """UTF-8 byte count of ``text[start:end]``, stopping as soon as it exceeds ``limit``.

    Measures bounded chunks, so a huge selection is never copied or encoded whole on
    the caller's thread; the result is exact when it is at most ``limit``.
    """
    measure = measure or (lambda part: len(part.encode("utf-8")))
    total = 0
    for position in range(start, end, COPY_SCAN_CHUNK):
        total += measure(text[position:min(position + COPY_SCAN_CHUNK, end)])
        if total > limit:
            break
    return total


def iter_graphemes(line):
    """Grapheme clusters of ``line`` (wcwidth's segmentation; a small rule without it)."""
    if _iter_graphemes is not None:
        yield from _iter_graphemes(line)
        return
    cluster = ""
    pending_join = False
    for char in line:
        code = ord(char)
        joins = (pending_join or unicodedata.combining(char)
                 or unicodedata.category(char) in ("Mn", "Me", "Mc")
                 or char == _ZWJ or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF
                 or 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F
                 or (0x1F1E6 <= code <= 0x1F1FF and len(cluster) == 1
                     and 0x1F1E6 <= ord(cluster) <= 0x1F1FF))
        if cluster and not joins:
            yield cluster
            cluster = ""
        cluster += char
        pending_join = char == _ZWJ
    if cluster:
        yield cluster


def copy_to_clipboard(text):
    """Put text on the system clipboard; True when a clipboard tool accepted it."""
    if sys.platform == "darwin":
        commands = (["pbcopy"],)
    else:
        commands = (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"])
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            done = subprocess.run(command, input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0
    return False


def common_prefix_length(old, new):
    """Length of the common prefix of two strings, compared in chunks (C speed)."""
    limit = min(len(old), len(new))
    low = 0
    while low < limit and old[low:low + COPY_SCAN_CHUNK] == new[low:low + COPY_SCAN_CHUNK]:
        low += COPY_SCAN_CHUNK
    low, high = min(low, limit), min(limit, low + COPY_SCAN_CHUNK)
    while low < high:                       # first difference inside the chunk
        middle = (low + high + 1) // 2
        if old[low:middle] == new[low:middle]:
            low = middle
        else:
            high = middle - 1
    return low


class Wrapper:
    """Wrap logical lines into rows of at most ``width`` columns; cached per line.

    Rows break only between grapheme clusters (joined emoji, flags, base + combining
    marks stay together) and a cluster's width is what prompt_toolkit's renderer will
    use for it: the sum of ``get_cwidth`` over its code points. ``wcswidth`` would say a
    ZWJ family is 2 cells, but the renderer places the code points one by one (2+0+2+0+2),
    so counting fewer cells than it draws would overflow the row and get it cut.
    """

    def __init__(self, cwidth):
        self._cwidth = cwidth
        self._cache = {}
        self._clusters = {}

    def char_width(self, char):
        if char == "\t":
            return TAB_WIDTH
        return max(0, self._cwidth(char))

    def clusters(self, line):
        """[(start, end, width)] grapheme clusters of ``line`` (offsets into it)."""
        found = self._clusters.get(line)
        if found is not None:
            return found
        found = []
        position = 0
        for cluster in iter_graphemes(line):
            found.append((position, position + len(cluster), sum(map(self.char_width, cluster))))
            position += len(cluster)
        if len(self._clusters) >= CACHE_LINES:
            del self._clusters[next(iter(self._clusters))]
        self._clusters[line] = found
        return found

    @staticmethod
    def hang(line, width):
        """Indent drawn before a line's wrapped rows: its leading spaces (W56a), so an
        indented reply paragraph stays indented without hard line breaks in the text."""
        spaces = len(line) - len(line.lstrip(" "))
        if line[spaces:spaces + 2] in HANG_BULLETS:
            spaces += 2   # a rendered bullet item: later rows line up with its text
        return spaces if 0 < spaces <= min(HANG_LIMIT, width // 2) and spaces < len(line) else 0

    def line_rows(self, line, width):
        """[(start, end)] offsets into ``line`` of each row; a cluster never splits."""
        key = (line, width)
        rows = self._cache.get(key)
        if rows is not None:
            return rows
        rows = []
        start = column = 0
        hang = self.hang(line, width)
        for begin, _end, span in self.clusters(line):
            if column + span > (width - hang if rows else width) and column > 0:
                rows.append((start, begin))
                start, column = begin, 0
            column += span
        if start < len(line) or not rows:
            rows.append((start, len(line)))   # a line that exactly fills its rows adds no blank row
        if len(self._cache) >= CACHE_LINES:
            del self._cache[next(iter(self._cache))]   # oldest entry only, never the whole cache
        self._cache[key] = rows
        return rows

    def wrap(self, text, width):
        """[(line, start, end)] for the whole text: offsets into ``text``."""
        rows = []
        position = 0
        for number, line in enumerate(text.split("\n")):
            for start, end in self.line_rows(line, width):
                rows.append((number, position + start, position + end))
            position += len(line) + 1
        return rows


class ConversationView:
    """Rows, reading position, follow state and selection of the conversation pane."""

    def __init__(self, cwidth, copier=None):
        self._wrapper = Wrapper(cwidth)
        self.text = ""
        self.rows = [(0, 0, 0)]
        self._starts = [0]
        self._line_rows = [1]    # rows each logical line occupies (incremental re-wrap)
        self.spans = []          # sorted, disjoint (start, end, token); gaps are "text"
        self.sources = []        # sorted, disjoint SourceRegions (rendered reply bodies)
        self._span_starts = []
        self.width = 0
        self.height = 1
        self._dirty = True
        self._changed_from = 0   # lowest text offset changed since the last layout
        self.top = 0            # first visible row
        self.top_offset = 0     # text offset at the start of that row (kept across re-wraps)
        self.following = True   # view at the bottom: new output stays in view
        self.unseen = False     # output arrived below while not following
        self.anchor = None      # selection: offset where the drag started
        self.head = None        # selection: offset of the moving end
        self.dragging = False
        self._copier = copier
        self.copied = None      # (characters, monotonic time) of the last copy
        self.copy_failed = False
        self.copy_error = None  # why the last copy failed (status text)
        self.copy_note = None   # extra hint shown with 已複製 (Ctrl-C: how to interrupt next)
        # One clipboard worker at a time: copies are serialized through ``_copy_next`` (only
        # the newest request is kept) so the clipboard always ends with the newest selection
        # and the status describes it; ``_copy_generation`` tags the newest request.
        self._copy_lock = threading.Lock()
        self._copy_thread = None
        self._copy_next = None       # (generation, text) waiting for the worker, or None
        self._copy_generation = 0
        self.on_copied = None   # called (from the copy thread) when a copy finishes
        self._last_click = None  # (offset, monotonic time) of the last click without a drag

    # -- text and layout -------------------------------------------------------------
    def set_text(self, text):
        """Replace the text; spans after the first changed character are dropped."""
        if text == self.text:
            return
        changed = common_prefix_length(self.text, text)
        self._clip_spans(changed)
        self._changed_from = changed if not self._dirty else min(self._changed_from, changed)
        self.text = text
        self._dirty = True
        if self.anchor is not None:
            self.anchor = min(self.anchor, len(text))
            self.head = min(self.head, len(text))
            if self.anchor == self.head and not self.dragging:
                self.clear_selection()
        if not self.following:
            self.unseen = True

    def _clip_spans(self, offset):
        while self.sources and self.sources[-1].end > offset:
            self.sources.pop()   # a region is kept whole or dropped (its text changed)
        index = bisect.bisect_left(self._span_starts, offset)
        del self.spans[index:], self._span_starts[index:]
        if self.spans and self.spans[-1][1] > offset:
            self.spans[-1] = (self.spans[-1][0], offset, self.spans[-1][2])

    def _add_segments(self, offset, segments):
        """Spans for styled segments placed at ``offset`` (after every existing span)."""
        for part, token in segments:
            if part and token != "text":
                self.spans.append((offset, offset + len(part), token))
                self._span_starts.append(offset)
            offset += len(part)

    def set_tail(self, start, segments, sources=()):
        """Replace the text from ``start`` on with [(text, token)] segments; ``sources``
        are (begin, end, source, width, indent) regions relative to ``start`` that copy
        as ``source`` (``width``/``indent``: how the source was rendered)."""
        self.set_text(self.text[:start] + "".join(part for part, _token in segments))
        self._clip_spans(start)
        self._add_segments(start, segments)
        self.sources.extend(SourceRegion(start + low, start + high, *region)
                            for low, high, *region in sources if high > low)

    def replace(self, start, end, segments):
        """Replace ``text[start:end]`` with styled segments; later spans, the reading
        anchor and the selection move with the text after it."""
        new = "".join(part for part, _token in segments)
        delta = len(new) - (end - start)
        after = [(s + delta, e + delta, t) for s, e, t in self.spans if s >= end]
        before = [(s, min(e, start), t) for s, e, t in self.spans if s < start]
        anchor, head = self.anchor, self.head
        regions = [region if region.end <= start else region.moved(delta)
                   for region in self.sources if region.end <= start or region.start >= end]
        self.set_text(self.text[:start] + new + self.text[end:])
        self.sources = regions
        self.spans = before
        self._span_starts = [s for s, _e, _t in before]
        self._add_segments(start, segments)
        self.spans.extend(after)
        self._span_starts.extend(s for s, _e, _t in after)
        if anchor is not None and head is not None:
            self.anchor, self.head = (value + delta if value >= end else min(value, start)
                                      for value in (anchor, head))
        if self.top_offset >= end:
            self.top_offset += delta

    def token_at(self, offset):
        index = bisect.bisect_right(self._span_starts, offset) - 1
        if index >= 0 and self.spans[index][1] > offset:
            return self.spans[index][2]
        return "text"

    def styled(self, start, end):
        """[(token, start, end)] covering ``text[start:end]``; gaps are "text"."""
        parts = []
        index = max(0, bisect.bisect_right(self._span_starts, start) - 1)
        position = start
        while position < end and index < len(self.spans):
            low, high, token = self.spans[index]
            index += 1
            if high <= position:
                continue
            if low >= end:
                break
            if low > position:
                parts.append(("text", position, low))
            parts.append((token, max(low, position), min(high, end)))
            position = min(high, end)
        if position < end:
            parts.append(("text", position, end))
        return parts

    def layout(self, width, height):
        """Rows for this width and the first visible row for this height (each render)."""
        width, height = max(1, width), max(1, height)
        rewrapped = self._dirty or width != self.width
        if rewrapped:
            if width == self.width:
                self._rewrap_tail(self._changed_from)
            else:
                self._set_rows(self._wrapper.wrap(self.text, width), 0, 0, 0)
            self.width, self._dirty = width, False
            if not self.following:
                self.top = self.row_of(self.top_offset)
        self.height = height
        # After a re-wrap the anchor offset stays, so resizing back and forth never drifts.
        self._clamp(keep_anchor=rewrapped)
        return self.rows

    def _rewrap_tail(self, changed_from):
        """Re-wrap only the logical lines from the first changed character on.

        Rows of the unchanged lines before it are kept as they are (same offsets), so a
        streamed chunk costs the changed line(s), not the whole conversation.
        """
        text = self.text
        line_start = text.rfind("\n", 0, changed_from) + 1
        line_index = text.count("\n", 0, line_start)
        keep_rows = sum(self._line_rows[:line_index])
        tail = self._wrapper.wrap(text[line_start:], self.width)
        self._set_rows(tail, keep_rows, line_index, line_start)

    def _set_rows(self, tail, keep_rows, line_index, line_start):
        """Replace the rows from ``keep_rows`` on with ``tail`` (relative to line_start)."""
        rows = self.rows[:keep_rows]
        starts = self._starts[:keep_rows]
        counts = self._line_rows[:line_index]
        last = None
        for line, start, end in tail:
            rows.append((line_index + line, line_start + start, line_start + end))
            starts.append(line_start + start)
            if line == last:
                counts[-1] += 1
            else:
                counts.append(1)
                last = line
        self.rows, self._starts, self._line_rows = rows, starts, counts

    def max_top(self):
        return max(0, len(self.rows) - self.height)

    def _clamp(self, keep_anchor=False):
        limit = self.max_top()
        if self.following or self.top >= limit:
            self.top, self.following, self.unseen = limit, True, False
        else:
            self.top = max(0, self.top)
        if not self.rows:
            self.top_offset = 0
        elif not keep_anchor or self.row_of(self.top_offset) != self.top:
            self.top_offset = self._starts[self.top]

    def scroll_rows(self, delta):
        """Move the view; arriving at the bottom resumes following, leaving it stops."""
        if not delta:
            return
        self.top += delta
        self.following = False
        self._clamp()

    def page(self, direction):
        self.scroll_rows(direction * max(1, self.height - 1))

    def to_top(self):
        self.scroll_rows(-self.top)

    def to_bottom(self):
        self.following = True
        self._clamp()

    def row_of(self, offset):
        """Row that shows the character at ``offset`` (the last row for the text end)."""
        return max(0, bisect.bisect_right(self._starts, offset) - 1)

    def offset_at(self, row, column):
        """Text offset under screen column ``column`` of ``row``; past its end, the row end.

        A column inside a cluster (the right half of a wide char, a joined emoji) maps to
        the cluster start, so a selection never splits one.
        """
        if not self.rows:
            return 0
        row = max(0, min(row, len(self.rows) - 1))
        _line, start, end = self.rows[row]
        line_start = self.text.rfind("\n", 0, start) + 1
        line_end = self.text.find("\n", start)
        line_text = self.text[line_start:None if line_end < 0 else line_end]
        if start > line_start:
            column -= self._wrapper.hang(line_text, self.width)
        used = 0
        for begin, _finish, span in self._wrapper.clusters(line_text):
            offset = line_start + begin
            if offset < start:
                continue
            if offset >= end:
                break
            if used + span > column:
                return offset
            used += span
        return end

    def row_indent(self, index):
        """Columns of hanging indent drawn before row ``index`` (0 on a line's first row)."""
        _line, start, _end = self.rows[index]
        line_start = self.text.rfind("\n", 0, start) + 1
        if start == line_start:
            return 0
        line_end = self.text.find("\n", start)
        return self._wrapper.hang(self.text[line_start:None if line_end < 0 else line_end], self.width)

    def visible_rows(self):
        return range(self.top, min(len(self.rows), self.top + self.height))

    # -- selection and clipboard ----------------------------------------------------
    def selected_range(self):
        if self.anchor is None or self.head is None or self.anchor == self.head:
            return None
        return min(self.anchor, self.head), max(self.anchor, self.head)

    def selected_text(self):
        span = self.selected_range()
        return self.copy_text(*span) if span else ""

    def copy_text(self, start, end):
        """What a copy of ``text[start:end]`` puts on the clipboard: displayed text, except
        that a rendered reply copies as its source Markdown (whole, or the part shown)."""
        parts, position = [], start
        index = max(0, bisect.bisect_right([r.start for r in self.sources], start) - 1)
        for region in self.sources[index:]:
            if region.start >= end:
                break
            if region.end <= position:
                continue
            if region.start > position:
                parts.append(self.text[position:region.start])
            parts.append(region.copy(self.text[region.start:region.end], position, end))
            position = min(end, region.end)
        parts.append(self.text[position:end])
        return "".join(parts)

    def clear_selection(self):
        self.anchor = self.head = None
        self.dragging = False

    def begin_drag(self, offset):
        self.anchor = self.head = offset
        self.dragging = True

    def extend_drag(self, offset):
        if self.dragging:
            self.head = offset

    def end_drag(self, offset=None):
        """Mouse up: a moved drag copies its selection and returns it; a click returns ''.

        A second click on the same spot within DOUBLE_CLICK_SECONDS selects the word there.
        """
        if not self.dragging:
            return ""
        if offset is not None:
            self.head = offset
        self.dragging = False
        span = self.selected_range()
        if span:
            self._last_click = None
            return self.copy_range(*span)
        now = time.monotonic()
        last, self._last_click = self._last_click, (self.anchor, now)
        self.clear_selection()
        if last is not None and last[0] == offset and now - last[1] <= DOUBLE_CLICK_SECONDS:
            self._last_click = None
            return self.select_word(offset)
        return ""

    def select_word(self, offset):
        """Select the run of non-blank characters around ``offset`` and copy it.

        The scan is bounded (WORD_SCAN_LIMIT each way) and the copy goes through
        ``copy_range``, so a huge blank-free run is neither walked nor sliced whole.
        """
        text = self.text
        if offset >= len(text) or text[offset].isspace():
            return ""
        start = end = offset
        while start > 0 and offset - start < WORD_SCAN_LIMIT and not text[start - 1].isspace():
            start -= 1
        while end < len(text) and end - offset < WORD_SCAN_LIMIT and not text[end].isspace():
            end += 1
        self.anchor, self.head = start, end
        return self.copy_range(start, end)

    def copy_range(self, start, end):
        """Copy ``text[start:end]``: the selected text when accepted, '' when over the limit.

        The byte limit is checked chunk by chunk first (``utf8_size``), so an oversized
        selection is refused without building or encoding it on the UI thread.
        """
        if utf8_size(self.text, start, end, COPY_LIMIT_BYTES) > COPY_LIMIT_BYTES:
            self.copied, self.copy_failed = (end - start, time.monotonic()), True
            self.copy_error = "選取超過複製上限 %d KB" % (COPY_LIMIT_BYTES // 1024)
            return ""
        text = self.copy_text(start, end)
        if not self.copy(text):
            return ""
        return text

    def copy(self, text):
        """Queue ``text`` for the clipboard worker; False when it is over the byte limit.

        Every route (drag release, double-click, Ctrl-C) ends here, so the limit holds for
        all of them. The UI loop never waits for pbcopy: encoding happens in the worker,
        ``status()`` says 複製中… meanwhile and ``wait_copy()`` joins it (tests). A newer
        request supersedes a queued one; a copy still running finishes first and its
        result is ignored, so the clipboard ends with the newest text.
        """
        if utf8_size(text, 0, len(text), COPY_LIMIT_BYTES) > COPY_LIMIT_BYTES:
            self.copied, self.copy_failed = (len(text), time.monotonic()), True
            self.copy_error = "選取超過複製上限 %d KB" % (COPY_LIMIT_BYTES // 1024)
            return False
        with self._copy_lock:
            self._copy_generation += 1
            self._copy_next = (self._copy_generation, text)
            self.copied, self.copy_failed, self.copy_error = (len(text), time.monotonic()), False, None
            self.copy_note = None
            # "queue empty -> worker exits" (below) and "enqueue -> ensure a worker" happen
            # under this one lock: a worker that is about to exit has already cleared the
            # reference, so a request arriving now always gets a worker.
            if self._copy_thread is None:
                self._copy_thread = threading.Thread(target=self._copy_worker,
                                                     name="llmtool-clipboard", daemon=True)
                self._copy_thread.start()
        return True

    def _copy_worker(self):
        copier = self._copier or copy_to_clipboard
        while True:
            with self._copy_lock:
                if self._copy_next is None:
                    self._copy_thread = None   # under the lock: the next copy() starts a worker
                    return
                generation, text = self._copy_next
                self._copy_next = None
            try:
                ok = bool(copier(text))
            except Exception:
                ok = False
            with self._copy_lock:
                if generation != self._copy_generation:
                    continue   # Superseded while running: the newer copy reports instead.
                self.copy_error = None if ok else "複製失敗 (無剪貼簿工具)"
                self.copied, self.copy_failed = (len(text), time.monotonic()), not ok
            if self.on_copied is not None:
                self.on_copied()

    def copy_pending(self):
        with self._copy_lock:
            return self._copy_thread is not None

    def wait_copy(self, timeout=5.0):
        """Wait until every queued copy has been processed (tests)."""
        deadline = time.monotonic() + timeout
        while True:
            with self._copy_lock:
                thread = self._copy_thread
            if thread is None:
                return True
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                return False

    def copy_selection(self):
        span = self.selected_range()
        if span:
            self.copy_range(*span)
        return bool(span)

    # -- status ------------------------------------------------------------------------
    def status(self):
        """Short status-bar field: copy result, selection size, or the reading position."""
        if self.copy_pending():
            return "複製中…"
        if self.copied is not None and time.monotonic() - self.copied[1] < COPIED_SECONDS:
            if self.copy_failed:
                return self.copy_error
            return "已複製 %d 字" % self.copied[0] + ("；" + self.copy_note if self.copy_note else "")
        span = self.selected_range()
        if span:
            return "已選 %d 字" % (span[1] - span[0])
        if self.following:
            return "最新"
        percent = 100 * self.top // max(1, self.max_top())
        return "捲動 %d%%" % percent + (" ⇣新輸出" if self.unseen else "")


def parse_mouse(data):
    """(event_type, button, x, y) of a vt100 mouse key press (0-based), or None."""
    from prompt_toolkit.key_binding.bindings import mouse as bindings
    try:
        if data[2] == "M":
            code, x, y = map(ord, data[3:])
            button, kind, _modifiers = bindings.typical_mouse_events[code]
            x, y = (x - 0xDC00 if x >= 0xDC00 else x) - 32, (y - 0xDC00 if y >= 0xDC00 else y) - 32
        else:
            body = data[2:]
            sgr = body.startswith("<")
            code, x, y = map(int, body[1 if sgr else 0:-1].split(";"))
            if sgr:
                button, kind, _modifiers = bindings.xterm_sgr_mouse_events[code, body[-1]]
            else:
                button, kind, _modifiers = bindings.urxvt_mouse_events.get(
                    code, (bindings.UNKNOWN_BUTTON, bindings.MOUSE_MOVE, bindings.UNKNOWN_MODIFIER))
    except (KeyError, ValueError, IndexError):
        return None
    return kind, button, x - 1, y - 1


def make_pane(view, style="class:conversation.selected", colour=None, styler=None,
              linker=None):
    """A prompt_toolkit Window that shows ``view`` one row per line and never wraps.

    Rows are drawn with their span styles (``styler``, default ``token_style``); a
    whole-line ``rule`` span fills the row and a whole-line ``meta.usage`` span is drawn
    flush right. ``linker`` (a callable returning a ``chat_tui_theme.Linker`` or None) adds
    OSC 8 links: each linked cell is preceded by its open sequence as a zero-width escape;
    the text, and so selection and copy, is unchanged (W56d).
    """
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import UIContent, UIControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.utils import get_cwidth

    colour = colour_enabled() if colour is None else colour
    styler = styler or (lambda token: token_style(token, colour))
    linker = linker or (lambda: None)

    def aligned(text, start, end, token, width):
        """The row's drawn text for a whole-line fill/right token, else None."""
        if token not in FILL_TOKENS + RIGHT_TOKENS:
            return None
        if (start and text[start - 1] != "\n") or (end < len(text) and text[end] != "\n"):
            return None   # only a line that fits one row is aligned
        shown = text[start:end]
        room = width - get_cwidth(shown)
        if token in FILL_TOKENS:
            label = shown.lstrip(RULE_CHAR).strip()
            room = width - get_cwidth(label) - (1 if label else 0)
            return RULE_CHAR * max(0, room) + (" " + label if label else "")
        return " " * max(0, room) + shown

    def links_open(url):
        found = linker()
        return found.open_sequence(url) if found is not None else ""

    class Control(UIControl):
        def is_focusable(self):
            return True

        def create_content(self, width, height):
            rows = view.layout(width, height)
            text, span = view.text, view.selected_range()

            def get_line(index):
                _line, start, end = rows[index]
                parts = view.styled(start, end)
                selected = span is not None and span[0] < end and span[1] > start
                if len(parts) == 1:
                    drawn = aligned(text, start, end, parts[0][0], width)
                    if drawn is not None:
                        klass = styler(parts[0][0])
                        return [((klass + " " + style) if selected else klass, drawn)]
                links = linker()
                links = links.links(text, start, end) if links is not None else []
                link_style = styler("ui.link") if links else ""
                fragments = []
                for token, low, high in parts:
                    klass = styler(token)
                    cuts = {low, high}
                    if selected:
                        cuts |= {min(max(span[0], low), high), min(max(span[1], low), high)}
                    for link_low, link_high, _url in links:
                        cuts |= {min(max(link_low, low), high), min(max(link_high, low), high)}
                    cuts = sorted(cuts)
                    for cut_low, cut_high in zip(cuts, cuts[1:]):
                        inside = selected and span[0] <= cut_low and cut_high <= span[1]
                        piece_style = (klass + " " + style).strip() if inside else klass
                        piece = text[cut_low:cut_high].replace("\t", " " * TAB_WIDTH)
                        url = next((u for a, b, u in links if a <= cut_low and cut_high <= b), None)
                        if url is None:
                            fragments.append((piece_style, piece))
                            continue
                        opening = links_open(url)
                        piece_style = (piece_style + " " + link_style).strip()
                        for char in piece:
                            fragments.append(("[ZeroWidthEscape]", opening))
                            fragments.append((piece_style, char))
                indent = view.row_indent(index)
                if indent:
                    fragments.insert(0, ("", " " * indent))
                return [fragment for fragment in fragments if fragment[1]]

            # The cursor is hidden; its row is the view's top, so the Window keeps that row.
            return UIContent(get_line=get_line, line_count=len(rows),
                             cursor_position=Point(x=0, y=view.top), show_cursor=False)

        def mouse_handler(self, mouse_event):
            return NotImplemented  # ChatTUI routes mouse events to the view itself.

    class Pane:
        """``text``/``set_text`` over the view, plus the Window the layout shows."""

        def __init__(self):
            self.view = view
            self.control = Control()
            self.window = Window(content=self.control, wrap_lines=False, always_hide_cursor=True,
                                 get_vertical_scroll=lambda _window: view.top,
                                 height=Dimension(min=1, weight=1))

        @property
        def text(self):
            return view.text

        def set_text(self, text):
            view.set_text(text)

        def __pt_container__(self):
            return self.window

    return Pane()
