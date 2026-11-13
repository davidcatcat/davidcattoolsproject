"""Static import graph for main CI incidents (#1706 R2, design section 4).

Pure and read-only: every function takes already-read source texts and returns
a new value. Nothing here calls Git, checks out a revision, touches the file
system or the network, and inputs are never mutated. Callers pass a
``{path: source text}`` map of ``tools/llmtool`` read with ``git show
<sha>:<path>`` at the SHA they study.

Paths are POSIX paths, repository relative (``tools/llmtool/llmtool/x.py``) or
relative to ``tools/llmtool`` (``llmtool/x.py``); the module name drops the
``tools/llmtool/`` prefix in both cases. Only ``.py`` paths become modules.

The closure is static and conservative: every ``import`` and ``from ... import``
statement anywhere in a file counts (also inside functions and ``try``), both
absolute and relative, and importing ``a.b.c`` also loads ``a`` and ``a.b``.
Dynamic imports (``importlib``, ``__import__``) are not seen. A file that does
not parse stays in the closure, is reported in ``unparsable`` and its own
imports are unknown; it is never fatal.
"""

from __future__ import annotations

import ast

PREFIX = 'tools/llmtool/'
_TRY = tuple(kind for kind in (ast.Try, getattr(ast, 'TryStar', None)) if kind is not None)


def module_name(path):
    """Return the dotted module name of a .py path, or None."""
    if not isinstance(path, str) or not path.endswith('.py'):
        return None
    rel = path[2:] if path.startswith('./') else path
    if rel.startswith(PREFIX):
        rel = rel[len(PREFIX):]
    parts = rel[:-3].split('/')
    if parts[-1] == '__init__':
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return '.'.join(parts)


def is_package(path):
    """True when path is a package ``__init__.py``."""
    return isinstance(path, str) and (path == '__init__.py' or path.endswith('/__init__.py'))


def parse(text, path='<supplied>'):
    """Return the parsed module, or None when text is not a str or does not parse."""
    if not isinstance(text, str):
        return None
    try:
        return ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _with_parents(name):
    parts = name.split('.')
    return ['.'.join(parts[:end]) for end in range(1, len(parts) + 1)]


def _base(module, package, level):
    """The package a relative import of level resolves against; '' when absolute; None if invalid."""
    if not level:
        return ''
    if module is None:
        return None
    parts = module.split('.') if package else module.split('.')[:-1]
    keep = len(parts) - (level - 1)
    if keep < 1:
        return None
    return '.'.join(parts[:keep])


def _import_refs(tree, path):
    """Yield (module, imported name or None) for every import statement in tree."""
    module, package = module_name(path), is_package(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom):
            base = _base(module, package, node.level)
            if base is None:
                continue
            target = '.'.join(part for part in (base, node.module or '') if part)
            if not target:
                continue
            for alias in node.names:
                yield target, alias.name


def _modules(tree, path):
    found = set()
    for target, name in _import_refs(tree, path):
        found.update(_with_parents(target))
        if name is not None and name != '*':
            # ``from pkg import x`` may load the submodule pkg.x.
            found.add(target + '.' + name)
    return found


def imports(path, text):
    """Return the set of module names a file may import, or None when it does not parse."""
    tree = parse(text, path)
    return None if tree is None else _modules(tree, path)


def _bind(target, out):
    if isinstance(target, ast.Name):
        out.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _bind(item, out)
    elif isinstance(target, ast.Starred):
        _bind(target.value, out)


def _top_level(body, out):
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _bind(target, out)
        elif isinstance(node, ast.AnnAssign):
            _bind(node.target, out)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            out.update(alias.asname or alias.name for alias in node.names if alias.name != '*')
        elif isinstance(node, ast.If):
            _top_level(node.body, out)
            _top_level(node.orelse, out)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _bind(node.target, out)
            _top_level(node.body, out)
            _top_level(node.orelse, out)
        elif isinstance(node, ast.While):
            _top_level(node.body, out)
            _top_level(node.orelse, out)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            _top_level(node.body, out)
        elif _TRY and isinstance(node, _TRY):
            _top_level(node.body, out)
            for handler in node.handlers:
                _top_level(handler.body, out)
            _top_level(node.orelse, out)
            _top_level(node.finalbody, out)


def defined_names(text):
    """Return the module-level names a source binds, or None when it does not parse."""
    tree = parse(text)
    if tree is None:
        return None
    out = set()
    _top_level(tree.body, out)
    return out


def _chain(node):
    """['a', 'b', 'c'] for the attribute chain a.b.c, or None when it does not start at a name."""
    attrs = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return [node.id] + attrs[::-1]


def used_names(path, text):
    """Return {(module, name)} a file takes from other modules, or None when it does not parse.

    ``from m import n`` uses (m, n); ``from m import *`` uses (m, '*'); an
    attribute read through an imported module alias (``import m as x; x.n``)
    uses (m, n). Aliases are read without flow analysis, so the result is an
    over-approximation.
    """
    tree = parse(text, path)
    if tree is None:
        return None
    module, package = module_name(path), is_package(path)
    aliases, out = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                aliases[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom):
            base = _base(module, package, node.level)
            target = '.'.join(part for part in (base or '', node.module or '') if part)
            if base is None or not target:
                continue
            for alias in node.names:
                out.add((target, alias.name))
                if alias.name != '*':
                    # The bound name may be a submodule: x.n then uses (target.name, n).
                    aliases[alias.asname or alias.name] = target + '.' + alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chain = _chain(node)
            if chain and chain[0] in aliases:
                dotted = '.'.join([aliases[chain[0]]] + chain[1:])
                module, _, name = dotted.rpartition('.')
                if module:
                    out.add((module, name))
    return out


def _index(files):
    index = {}
    for path in sorted(path for path in files if isinstance(path, str)):
        name = module_name(path)
        if name is not None:
            index.setdefault(name, path)
    return index


def _root(test_module, files, index):
    """The file of a test given as a path key, a dotted module or a test ID; None if unknown."""
    if not isinstance(test_module, str) or not test_module:
        return None
    if test_module in files and module_name(test_module) is not None:
        return test_module
    parts = test_module.split('.')
    for end in range(len(parts), 0, -1):
        path = index.get('.'.join(parts[:end]))
        # A shorter prefix that is only a package (tests/__init__.py) is not the test.
        if path is not None and (end == len(parts) or not is_package(path)):
            return path
    return None


def import_closure(test_module, files):
    """Return the static import closure of one failing test inside files.

    test_module is a path key of files, a dotted module name or a full test ID
    (the longest prefix that names a module wins). Returns a new dict:
    ``root`` (path or None), ``module``, ``files`` (sorted paths of the closure,
    the root included), ``unparsable`` (sorted paths in the closure that did not
    parse) and ``complete`` (a root was found and every file parsed).
    """
    files = files if isinstance(files, dict) else {}
    index = _index(files)
    root = _root(test_module, files, index)
    result = {'test_module': test_module, 'root': root,
              'module': module_name(root) if root else None,
              'files': [], 'unparsable': [], 'complete': False}
    if root is None:
        return result
    seen, unparsable, todo = set(), set(), [root]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        # Loading a module first loads every parent package.
        wanted = set(_with_parents(module_name(path))[:-1])
        tree = parse(files[path], path)
        if tree is None:
            unparsable.add(path)
        else:
            wanted |= _modules(tree, path)
        todo.extend(index[name] for name in sorted(wanted)
                    if name in index and index[name] not in seen)
    result.update(files=sorted(seen), unparsable=sorted(unparsable), complete=not unparsable)
    return result
