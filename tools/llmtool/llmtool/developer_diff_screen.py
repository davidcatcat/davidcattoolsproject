"""Developer diff screen: the shared output screen plus one Python release (#981).

Only the Developer diff gate uses this module. It runs the unchanged shared
screen (test_output_excerpts) over the exact added-line text. When that screen
refuses, a keyword-assignment match is released only if Python syntax proves it
is plain code, never a hardcoded value:

* the match lies on an added line of a ``.py`` target file;
* the new-side lines before its block are known and end outside every string
  (see below);
* the contiguous new-side block of added lines holding it parses with ``ast``;
* the keyword is exactly an assignment target, an annotated target, a walrus
  target, a parameter or a keyword argument, and its value is a name,
  attribute or call with no string or bytes literal anywhere in it (a bare
  annotation has no value; any annotation must itself be literal-free).

Each release removes only that one proven match. Every other shared pattern
(private key, cloud key, provider keys, JWT, URL userinfo, auth schemes) and
every other keyword match still refuses. Keyword text in a string literal,
f-string or comment has no proving syntax node, so it stays refused.

Strings opened before the block: a block is parsed on its own, so the release
also proves that the block does not start inside a string opened earlier in
the file. The new-side lines before the block are rebuilt from the diff's
hunks plus the file's unchanged lines and tokenized. The release stands only
if they end outside every string, f-string and t-string. If they end inside
open brackets, the brackets are closed and the lines are tokenized again. A
string that is still open would absorb the closers and still fail. A comment
cannot reach past its own line, so it cannot cover an added line. Unchanged
lines come from the diff alone only when no unchanged line comes before the
block's hunk (the file's first lines or a new file). Otherwise they come from
the caller's pre-image source, and every context and removed line must match
that source at its stated position. If those lines are unavailable or do not
match, the match stays refused.

Fail closed: a malformed diff, an unknown file type, a block that does not
parse, a prefix that is unknown or does not tokenize, any parser error, or any
unexpected state keeps the shared result. No decision depends on value
length, letter case or entropy.
"""

import ast
import bisect
import io
import os
import re
import tokenize
import warnings

from .test_output_excerpts import _SENSITIVE, _emitted_sensitive, _sensitive_jwt, _sensitive_url

_DIFF_HEADER = re.compile(r'diff --git a/([^\r\n]+) b/([^\r\n]+)')
# str.splitlines() breaks at these, but Git and the Python tokenizer do not.
_FOREIGN_BREAKS = frozenset('\x0b\x0c\x1c\x1d\x1e\x85  ')
# The unquoted keyword alternative of the shared pattern. A shared match is
# releasable only if its whole text is this form; anything else stays refused.
_KEYWORD_FORM = re.compile(
    r'(api[_-]?key|password|passwd|token|secret|access[_-]?token|'
    r'client[_-]?secret|aws_secret_access_key)\s*([:=])', re.IGNORECASE)
_STRING_NODES = frozenset(('JoinedStr', 'TemplateStr', 'Interpolation'))
_HUNK_RANGE = re.compile(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?')
_OPENERS = {'(': ')', '[': ']', '{': '}'}
_CLOSERS = frozenset(_OPENERS.values())
_STRING_STARTS = frozenset(getattr(tokenize, name) for name in ('FSTRING_START', 'TSTRING_START')
                           if hasattr(tokenize, name))
_STRING_ENDS = frozenset(getattr(tokenize, name) for name in ('FSTRING_END', 'TSTRING_END')
                         if hasattr(tokenize, name))


def developer_diff_sensitive(value, added, patch_paths, sources=None):
    """Return True when the Developer diff must be refused as sensitive.

    ``added`` is the exact added-line text the shared screen sees, and
    ``patch_paths`` is the strict diff parser. ``sources`` is None or a
    callable returning one path's pre-image text. It is called only when a
    release needs proof and only for ``.py`` files the diff modifies. The
    result is the shared screen result unless every shared match is a proven
    Python release.
    """
    if not _emitted_sensitive(added):
        return False
    try:
        return not _all_released(value, added, patch_paths, sources)
    except Exception:
        return True


def _all_released(value, added, patch_paths, sources=None):
    if (type(value) is not str or type(added) is not str
            or any(char in _FOREIGN_BREAKS for char in value)
            or '\r' in value.replace('\r\n', '')):
        return False
    try:
        paths = patch_paths(value)
    except Exception:
        return False
    layout = _layout(value, paths)
    if '\n'.join(layout[0]) != added:
        return False
    escaped, origin = _escaped(added)
    if escaped != added.encode('ascii', 'backslashreplace').decode('ascii'):
        return False
    texts = [(added, None)] if origin is None else [(added, None), (escaped, origin)]
    for text, _ in texts:
        if _sensitive_jwt(text) or _sensitive_url(text):
            return False
    proofs = {}
    # Pre-image lines per path, read at most once and only when a proof needs them.
    origins = (paths, sources, {})
    for text, mapping in texts:
        # Visit every match start, not only non-overlapping ones.
        position = 0
        while True:
            match = _SENSITIVE.search(text, position)
            if match is None:
                break
            if not _released(layout, proofs, added, match, mapping, origins):
                return False
            position = match.start() + 1
    return True


def _layout(value, paths):
    """Mirror the shared added-line walk, recording file and block per line.

    Each block also gets a head: its file's hunk events and how many of them
    come before the block, so the new-side lines above it can be rebuilt.
    """
    lines, entries, blocks, heads = [], [], [], []
    path, in_hunk, previous, block, events = None, False, None, None, []
    for line in value.splitlines():
        if line.startswith('diff --git '):
            header = _DIFF_HEADER.fullmatch(line)
            path = (header[1] if header is not None and header[1] == header[2]
                    and header[1] in paths else None)
            in_hunk, block, events = False, None, []
        elif line.startswith('@@ '):
            in_hunk, block = True, None
            events.append(('@', _hunk_range(line)))
        elif (not in_hunk and line.startswith('+++ ') and previous is not None and
              previous.startswith('--- ')):
            pass
        elif in_hunk and line.startswith('+'):
            if block is None:
                block = len(blocks)
                blocks.append([])
                heads.append((path, events, len(events)))
            entries.append((path, block, len(blocks[block])))
            blocks[block].append(line[1:])
            lines.append(line[1:])
            events.append(('+', line[1:]))
        elif not (in_hunk and line.startswith('-')):
            # Removed lines are absent from the new side, so added lines on
            # either side of them stay one new-side block. Anything else ends it.
            block = None
            if in_hunk and line.startswith(' '):
                events.append((' ', line[1:]))
            elif in_hunk and not line.startswith('\\'):
                events.append(('?', None))
        else:
            events.append(('-', line[1:]))
        previous = line
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    return lines, starts, entries, blocks, heads


def _hunk_range(line):
    """Return (old lines before, new lines before) a hunk, or None."""
    hunk = _HUNK_RANGE.fullmatch(line)
    if hunk is None:
        return None
    old_start, new_start = int(hunk.group(1)), int(hunk.group(3))
    old_count, new_count = int(hunk.group(2) or '1'), int(hunk.group(4) or '1')
    # A zero-count range names the line after which the hunk applies.
    return (old_start - 1 if old_count else old_start,
            new_start - 1 if new_count else new_start)


def _old_lines(path, operation, sources):
    """Return a path's pre-image lines, [] for a new file, or None if unknown."""
    if operation == 'new':
        return []
    if operation != 'modified' or sources is None:
        return None
    try:
        text = sources(path)
    except Exception:
        return None
    if type(text) is not str:
        return None
    old = text.split('\n')
    if old[-1] == '':
        old.pop()
    return old


def _new_side_prefix(events, upto, old):
    """Rebuild the new-side lines before a block, or None if they are unknown.

    ``old`` is the pre-image as lines. When it is None, only the diff's own
    lines are available, so no unchanged line may precede any hunk.
    """
    new, cursor = [], 0
    for kind, item in events[:upto]:
        if kind == '@':
            if item is None:
                return None
            old_before, new_before = item
            if old_before < cursor:
                return None
            if old_before > cursor:
                if old is None or old_before > len(old):
                    return None
                new.extend(old[cursor:old_before])
                cursor = old_before
            if len(new) != new_before:
                return None
        elif kind in (' ', '-'):
            if old is not None and (cursor >= len(old) or old[cursor] != item):
                return None
            if kind == ' ':
                new.append(item)
            cursor += 1
        elif kind == '+':
            new.append(item)
        else:
            return None
    return new


def _opens_nothing(head, origins):
    """True only if the lines before the block provably end outside strings."""
    path, events, upto = head
    paths, sources, cache = origins
    if path not in cache:
        cache[path] = _old_lines(path, paths.get(path), sources)
    old = cache[path]
    if sources is not None and old is None and paths.get(path) != 'new':
        return False  # A source was promised for this file and is unavailable.
    prefix = _new_side_prefix(events, upto, old)
    if prefix is None:
        return False
    if not prefix:
        return True
    source = ''.join(line + '\n' for line in prefix)
    if '\r' in source or '\0' in source:
        return False
    stack = []
    state = _tokens_close(source, stack)
    if state is not False:
        return state is True
    if not stack:
        return False
    # The prefix may end inside open brackets. Close them and tokenize again.
    # A string still open would absorb the closers and still be unterminated.
    closers = ''.join(_OPENERS[opener] for opener in reversed(stack))
    return _tokens_close(source + closers + '\n', []) is True


def _tokens_close(source, stack):
    """Tokenize ``source``; record open brackets on ``stack``.

    Return True when every token, string and f-/t-string closes, False when
    tokenizing stops with a TokenError outside any f-/t-string, and None for
    any other error.
    """
    depth = 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.ERRORTOKEN:
                    return None
                if token.type in _STRING_STARTS:
                    depth += 1
                elif token.type in _STRING_ENDS:
                    depth -= 1
                elif token.type == tokenize.OP and depth == 0:
                    if token.string in _OPENERS:
                        stack.append(token.string)
                    elif token.string in _CLOSERS:
                        if not stack or _OPENERS[stack.pop()] != token.string:
                            return None
    except tokenize.TokenError:
        return False if depth == 0 else None
    except Exception:
        return None
    return True if depth == 0 and not stack else None


def _escaped(text):
    """Return the shared screen's ASCII view and its map to original indexes."""
    if text.isascii():
        return text, None
    pieces, origin = [], []
    for index, char in enumerate(text):
        if char.isascii():
            pieces.append(char)
            origin.append(index)
        else:
            piece = char.encode('ascii', 'backslashreplace').decode('ascii')
            pieces.append(piece)
            origin.extend([None] * len(piece))
    return ''.join(pieces), origin


def _released(layout, proofs, added, match, mapping, origins):
    form = _KEYWORD_FORM.fullmatch(match.group(0))
    if form is None:
        return False
    keyword, delimiter = form.group(1), form.group(2)
    start = match.start()
    if mapping is not None:
        span = mapping[start:start + len(keyword)]
        if (len(span) != len(keyword) or None in span
                or span != list(range(span[0], span[0] + len(keyword)))):
            return False
        start = span[0]
    if added[start:start + len(keyword)] != keyword:
        return False
    lines, starts, entries, blocks, heads = layout
    index = bisect.bisect_right(starts, start) - 1
    if index < 0:
        return False
    column = start - starts[index]
    if lines[index][column:column + len(keyword)] != keyword:
        return False
    path, block, row = entries[index]
    if path is None or not path.endswith('.py'):
        return False
    if block not in proofs:
        # A block that may start inside an earlier string has no proof at all.
        proofs[block] = (_block_proofs(blocks[block])
                         if _opens_nothing(heads[block], origins) else None)
    proof = proofs[block]
    if proof is None:
        return False
    indent, positions = proof
    line = blocks[block][row]
    if column < indent or not line.strip(' \t'):
        return False
    offset = len(line[indent:column].encode('utf-8'))
    return positions.get((row + 1, offset)) == (keyword, delimiter)


def _block_proofs(lines):
    """Parse one added block; return its indent and proven keyword positions."""
    indents = [line[:len(line) - len(line.lstrip(' \t'))]
               for line in lines if line.strip(' \t')]
    if not indents:
        return None
    prefix = os.path.commonprefix(indents)
    source = '\n'.join(line[len(prefix):] if line.strip(' \t') else ''
                       for line in lines) + '\n'
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            tree = ast.parse(source)
        return len(prefix), _proven_positions(tree)
    except Exception:
        return None


def _proven_positions(tree):
    positions = {}
    pending = [tree]
    while pending:
        node = pending.pop()
        if type(node).__name__ in _STRING_NODES:
            continue  # Code inside an f-string or t-string is string text here.
        for line, offset, name, delimiter in _roles(node):
            key = (line, offset)
            if positions.get(key, (name, delimiter)) != (name, delimiter):
                positions[key] = None
            else:
                positions[key] = (name, delimiter)
        pending.extend(ast.iter_child_nodes(node))
    return positions


def _roles(node):
    """Yield (line, byte column, identifier, delimiter) for proven roles."""
    if isinstance(node, ast.Assign):
        if _plain(node.value):
            for target in node.targets:
                yield from _target(target, '=')
    elif isinstance(node, ast.AnnAssign):
        if (_literal_free(node.annotation)
                and (node.value is None or _plain(node.value))):
            yield from _target(node.target, ':')
    elif isinstance(node, ast.NamedExpr):
        if _plain(node.value):
            yield from _target(node.target, ':')
    elif isinstance(node, ast.arguments):
        positional = [*node.posonlyargs, *node.args]
        if len(node.defaults) > len(positional):
            return
        defaults = [None] * (len(positional) - len(node.defaults)) + list(node.defaults)
        pairs = [*zip(positional, defaults), *zip(node.kwonlyargs, node.kw_defaults)]
        pairs.extend((item, None) for item in (node.vararg, node.kwarg) if item is not None)
        for argument, default in pairs:
            if argument.annotation is not None:
                if (_literal_free(argument.annotation)
                        and (default is None or _plain(default))):
                    yield argument.lineno, argument.col_offset, argument.arg, ':'
            elif default is not None and _plain(default):
                yield argument.lineno, argument.col_offset, argument.arg, '='
    elif isinstance(node, ast.keyword):
        line, offset = getattr(node, 'lineno', None), getattr(node, 'col_offset', None)
        if node.arg is not None and line is not None and offset is not None and _plain(node.value):
            yield line, offset, node.arg, '='


def _target(node, delimiter):
    if isinstance(node, ast.Name):
        yield node.lineno, node.col_offset, node.id, delimiter
    elif (isinstance(node, ast.Attribute) and node.end_lineno is not None
          and node.end_col_offset is not None):
        # An attribute node ends exactly at the end of its attribute name.
        yield (node.end_lineno, node.end_col_offset - len(node.attr.encode('utf-8')),
               node.attr, delimiter)


def _plain(node):
    return isinstance(node, (ast.Name, ast.Attribute, ast.Call)) and _literal_free(node)


def _literal_free(node):
    for item in ast.walk(node):
        if type(item).__name__ in _STRING_NODES:
            return False
        if isinstance(item, ast.Constant) and isinstance(item.value, (str, bytes)):
            return False
    return True
