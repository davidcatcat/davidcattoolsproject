"""Immutable project context validated from a trust-shaped mapping.

Issue #2793 package H1. ``ProjectContext.from_trust`` builds the context from a
project id and a mapping with the keys ``canonical_root``, ``workspace_root``,
``tasks_root`` and ``repository`` (with ``slug`` and ``allowed_origins``, and an
optional ``approval_refs``). Validation is pure: it performs no file, network
or process access. Every invalid input is refused with
``ProjectContextError``, which carries the fixed upper-case code
``PROJECT_CONTEXT_INVALID`` and never an input value.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

PROJECT_CONTEXT_INVALID = 'PROJECT_CONTEXT_INVALID'

_MAX_PROJECT_ID = 100
_MAX_ROOT_LENGTH = 1024
_SLUG_RE = re.compile(r'\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}\Z')
_ORIGIN_RE = re.compile(r'\A(git@github\.com:|https://github\.com/)(?P<slug>.+)\.git\Z')


class ProjectContextError(ValueError):
    """Refusal for an invalid project context mapping.

    ``code`` is always :data:`PROJECT_CONTEXT_INVALID`; the exception carries
    the fixed code and no input value.
    """

    code = PROJECT_CONTEXT_INVALID

    def __init__(self):
        super().__init__(PROJECT_CONTEXT_INVALID)


def _refuse() -> ProjectContextError:
    return ProjectContextError()


def _is_printable_ascii(value, min_length, max_length):
    if type(value) is not str:
        return False
    if not min_length <= len(value) <= max_length:
        return False
    return all(32 <= ord(char) <= 126 for char in value)


def _valid_root(value):
    """A string of 1..1024 printable ASCII characters, absolute, normalized."""
    if not _is_printable_ascii(value, 1, _MAX_ROOT_LENGTH):
        return False
    path = PurePosixPath(value)
    return path.is_absolute() and posixpath.normpath(value) == value


def _valid_slug(value):
    if type(value) is not str or not _SLUG_RE.match(value):
        return False
    name = value.partition('/')[2]
    return name not in ('.', '..')


def _valid_origins(value, slug):
    if type(value) is not list or not 1 <= len(value) <= 2:
        return False
    for origin in value:
        if type(origin) is not str:
            return False
    if len(value) == 2 and value[0] == value[1]:
        return False
    for origin in value:
        match = _ORIGIN_RE.match(origin)
        if match is None or match.group('slug') != slug:
            return False
    return True


def _valid_approval_refs(value):
    if type(value) not in (list, tuple) or not value:
        return False
    return all(type(ref) is str for ref in value)


def _one_inside(container, candidate):
    """True when *candidate* lies strictly inside *container*."""
    a = PurePosixPath(container)
    b = PurePosixPath(candidate)
    if len(b.parts) <= len(a.parts):
        return False
    return a.parts == b.parts[:len(a.parts)]


def _disjoint_roots(roots):
    for i, left in enumerate(roots):
        for right in roots[i + 1:]:
            if left == right or _one_inside(left, right) or _one_inside(right, left):
                return False
    return True


class _FrozenType(type):
    def __getattr__(cls, name):
        # Refuse unknown class attributes as loudly as instance mutation.
        raise AttributeError(PROJECT_CONTEXT_INVALID)


class ProjectContext(metaclass=_FrozenType):
    """Immutable project context: id, slug, allowed origins, three roots."""

    __slots__ = ('project_id', 'slug', 'allowed_origins', 'canonical_root',
                 'workspace_root', 'tasks_root')
    _fields = ('project_id', 'slug', 'allowed_origins', 'canonical_root',
               'workspace_root', 'tasks_root')
    _code = PROJECT_CONTEXT_INVALID

    def __init__(self, project_id, slug, allowed_origins, canonical_root,
                 workspace_root, tasks_root):
        if not _is_printable_ascii(project_id, 1, _MAX_PROJECT_ID):
            raise _refuse()
        if not _valid_slug(slug):
            raise _refuse()
        if not _valid_origins(allowed_origins, slug):
            raise _refuse()
        roots = (canonical_root, workspace_root, tasks_root)
        for root in roots:
            if not _valid_root(root):
                raise _refuse()
        if not _disjoint_roots(roots):
            raise _refuse()
        object.__setattr__(self, 'project_id', project_id)
        object.__setattr__(self, 'slug', slug)
        object.__setattr__(self, 'allowed_origins', tuple(allowed_origins))
        object.__setattr__(self, 'canonical_root', canonical_root)
        object.__setattr__(self, 'workspace_root', workspace_root)
        object.__setattr__(self, 'tasks_root', tasks_root)

    @staticmethod
    def from_trust(project_id, mapping):
        """Build a :class:`ProjectContext` from a project id and trust mapping.

        Pure validation; no file, network or process access. Raises
        :class:`ProjectContextError` for any invalid input, including wrong
        types and unexpected keys.
        """
        if not _is_printable_ascii(project_id, 1, _MAX_PROJECT_ID):
            raise _refuse()
        if type(mapping) is not dict:
            raise _refuse()
        if set(mapping) != {'canonical_root', 'workspace_root', 'tasks_root', 'repository'}:
            raise _refuse()
        repository = mapping['repository']
        if type(repository) is not dict:
            raise _refuse()
        allowed_keys = {'slug', 'allowed_origins'}
        if 'approval_refs' in repository:
            allowed_keys.add('approval_refs')
        if set(repository) != allowed_keys:
            raise _refuse()
        slug = repository['slug']
        if not _valid_slug(slug):
            raise _refuse()
        if not _valid_origins(repository['allowed_origins'], slug):
            raise _refuse()
        if 'approval_refs' in repository and not _valid_approval_refs(repository['approval_refs']):
            raise _refuse()
        roots = (mapping['canonical_root'], mapping['workspace_root'], mapping['tasks_root'])
        for root in roots:
            if not _valid_root(root):
                raise _refuse()
        if not _disjoint_roots(roots):
            raise _refuse()
        return ProjectContext(project_id, slug, repository['allowed_origins'], *roots)

    def __setattr__(self, name, value):
        raise AttributeError(PROJECT_CONTEXT_INVALID)

    def __delattr__(self, name):
        raise AttributeError(PROJECT_CONTEXT_INVALID)

    def __eq__(self, other):
        if type(other) is not ProjectContext:
            return NotImplemented
        return tuple(getattr(self, field) for field in self._fields) == \
            tuple(getattr(other, field) for field in self._fields)

    def __hash__(self):
        return hash(tuple(getattr(self, field) for field in self._fields))

    def __repr__(self):
        return 'ProjectContext(' + ', '.join(
            f'{field}={getattr(self, field)!r}' for field in self._fields) + ')'
