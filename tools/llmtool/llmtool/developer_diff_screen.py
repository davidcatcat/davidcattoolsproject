"""Developer diff screen: the shared output screen plus one Python release (#981).

Only the Developer diff gate uses this module. It runs the unchanged shared
screen (test_output_excerpts) over the exact added-line text. When that screen
refuses, a keyword-assignment match is released only if Python syntax proves it
is plain code, never a hardcoded value:

* the match lies on an added line of a ``.py`` target file;
* the contiguous new-side block of added lines holding it parses with ``ast``;
* the keyword is exactly an assignment target, an annotated target, a walrus
  target, a parameter or a keyword argument, and its value is a name,
  attribute or call with no string or bytes literal anywhere in it (a bare
  annotation has no value; any annotation must itself be literal-free).

Each release removes only that one proven match. Every other shared pattern
(private key, cloud key, provider keys, JWT, URL userinfo, auth schemes) and
every other keyword match still refuses. Keyword text in a string literal,
f-string or comment has no proving syntax node, so it stays refused.

Fail closed: a malformed diff, an unknown file type, a block that does not
parse, any parser error, or any unexpected state keeps the shared result.
No decision depends on value length, letter case or entropy.

Residual limit: a block is parsed without the file's unchanged lines, so text
inside a string that opens on an unchanged line is read as code.
"""

import ast
import bisect
import os
import re
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


def developer_diff_sensitive(value, added, patch_paths):
    """Return True when the Developer diff must be refused as sensitive.

    ``added`` is the exact added-line text the shared screen sees, and
    ``patch_paths`` is the strict diff parser. The result is the shared
    screen result unless every shared match is a proven Python release.
    """
    if not _emitted_sensitive(added):
        return False
    try:
        return not _all_released(value, added, patch_paths)
    except Exception:
        return True


def _all_released(value, added, patch_paths):
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
    for text, mapping in texts:
        # Visit every match start, not only non-overlapping ones.
        position = 0
        while True:
            match = _SENSITIVE.search(text, position)
            if match is None:
                break
            if not _released(layout, proofs, added, match, mapping):
                return False
            position = match.start() + 1
    return True


def _layout(value, paths):
    """Mirror the shared added-line walk, recording file and block per line."""
    lines, entries, blocks = [], [], []
    path, in_hunk, previous, block = None, False, None, None
    for line in value.splitlines():
        if line.startswith('diff --git '):
            header = _DIFF_HEADER.fullmatch(line)
            path = (header[1] if header is not None and header[1] == header[2]
                    and header[1] in paths else None)
            in_hunk, block = False, None
        elif line.startswith('@@ '):
            in_hunk, block = True, None
        elif (not in_hunk and line.startswith('+++ ') and previous is not None and
              previous.startswith('--- ')):
            pass
        elif in_hunk and line.startswith('+'):
            if block is None:
                block = len(blocks)
                blocks.append([])
            entries.append((path, block, len(blocks[block])))
            blocks[block].append(line[1:])
            lines.append(line[1:])
        elif not (in_hunk and line.startswith('-')):
            # Removed lines are absent from the new side, so added lines on
            # either side of them stay one new-side block. Anything else ends it.
            block = None
        previous = line
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    return lines, starts, entries, blocks


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


def _released(layout, proofs, added, match, mapping):
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
    lines, starts, entries, blocks = layout
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
        proofs[block] = _block_proofs(blocks[block])
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
