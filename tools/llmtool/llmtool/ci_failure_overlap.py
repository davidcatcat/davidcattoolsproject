"""#2124: do failing CI test modules overlap a PR's changes? Pure; not wired in yet.

A counted CI repair should start only when a failing test module relates to the
PR. The caller gives module names (``test_x`` or ``.../test_x.py``), the PR's
changed paths and the Task's declared test modules. Malformed input is UNKNOWN,
so the caller can hold for a PM decision instead of guessing.
"""
import re

OVERLAP, NO_OVERLAP, UNKNOWN = 'OVERLAP', 'NO_OVERLAP', 'UNKNOWN'
_MODULE = re.compile(r'test_[A-Za-z0-9_]{1,120}')


def _module_name(value):
    """'test_x', 'test_x.py' or 'a/b/test_x.py' -> 'test_x'; anything else None."""
    if type(value) is not str:
        return None
    name = value.rsplit('/', 1)[-1]
    if name.endswith('.py'):
        name = name[:-3]
    return name if _MODULE.fullmatch(name) else None


def _valid_changed_path(path):
    """A changed path must be a non-blank file path, not a directory path."""
    return (type(path) is str and bool(path.strip()) and
            not path.rstrip().endswith('/'))


def overlap(failing_modules, changed_paths, declared_modules=()):
    """Return OVERLAP, NO_OVERLAP or UNKNOWN for one red required check.

    OVERLAP when a failing module is declared by the Task, is itself a changed
    file, or tests a changed source file of the same stem (``x.py`` and
    ``test_x``). An empty or malformed failing list, declared module or changed
    path is UNKNOWN.
    """
    if (type(failing_modules) not in (list, tuple) or not failing_modules or
            type(changed_paths) not in (list, tuple) or
            type(declared_modules) not in (list, tuple)):
        return UNKNOWN
    failing = [_module_name(item) for item in failing_modules]
    declared = [_module_name(item) for item in declared_modules]
    if None in failing or None in declared:
        return UNKNOWN
    if not all(_valid_changed_path(path) for path in changed_paths):
        return UNKNOWN
    related = set(declared)
    for path in changed_paths:
        name = path.rsplit('/', 1)[-1]
        if name.endswith('.py'):
            stem = name[:-3]
            related.add(stem if stem.startswith('test_') else 'test_' + stem)
    return OVERLAP if related & set(failing) else NO_OVERLAP
