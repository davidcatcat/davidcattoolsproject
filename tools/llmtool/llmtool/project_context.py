"""Immutable project context built purely from a trust-shaped mapping.

Validation here never reads files, uses the network or starts processes.
Every rejection raises :class:`ProjectContextError` with one fixed code and
no input value, so failures are safe to display.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
import re

PROJECT_CONTEXT_INVALID = "PROJECT_CONTEXT_INVALID"

_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}\Z")
_TRUST_KEYS = frozenset({"canonical_root", "workspace_root", "tasks_root", "repository"})
_REPOSITORY_KEYS = frozenset({"slug", "allowed_origins"})
_REPOSITORY_OPTIONAL_KEYS = frozenset({"approval_refs"})
_APPROVAL_KEYS = frozenset({
    "reviewer_host_policy_issue",
    "worker_dispatch_issue",
    "worker_dispatch_evidence_pr",
    "retain_release_issues",
})
_INTEGER_REFERENCE_KEYS = (
    "reviewer_host_policy_issue",
    "worker_dispatch_issue",
    "worker_dispatch_evidence_pr",
)
_MAX_REFERENCE = 10 ** 9
_FIELDS = (
    "project_id",
    "slug",
    "allowed_origins",
    "canonical_root",
    "workspace_root",
    "tasks_root",
)


class ProjectContextError(ValueError):
    """Sanitized project-context rejection; ``code`` is the only detail."""

    def __init__(self, code=PROJECT_CONTEXT_INVALID):
        self.code = code
        super().__init__(code)


def _fail():
    raise ProjectContextError()


def _printable_ascii(value, minimum, maximum):
    return (type(value) is str and minimum <= len(value) <= maximum and
            all(0x20 <= ord(char) <= 0x7E for char in value))


def _validate_project_id(value):
    if not _printable_ascii(value, 1, 100):
        _fail()


def _validate_slug(value):
    if (type(value) is not str or _SLUG.fullmatch(value) is None or
            value.split("/", 1)[1] in (".", "..")):
        _fail()


def _validate_origins(value, slug):
    if type(value) is not list or not 1 <= len(value) <= 2:
        _fail()
    allowed = (f"git@github.com:{slug}.git", f"https://github.com/{slug}.git")
    for origin in value:
        if type(origin) is not str or origin not in allowed:
            _fail()
    # Types are checked above, so distinctness can never raise TypeError.
    if len(set(value)) != len(value):
        _fail()


def _validate_approval_refs(value):
    if not isinstance(value, Mapping) or set(value.keys()) != _APPROVAL_KEYS:
        _fail()
    for key in _INTEGER_REFERENCE_KEYS:
        reference = value[key]
        if type(reference) is not int or not 1 <= reference <= _MAX_REFERENCE:
            _fail()
    retain = value["retain_release_issues"]
    if type(retain) is not list or not 1 <= len(retain) <= 8:
        _fail()
    for issue in retain:
        if type(issue) is not int or not 1 <= issue <= _MAX_REFERENCE:
            _fail()
    # Types are checked above, so distinctness can never raise TypeError.
    if len(set(retain)) != len(retain):
        _fail()


def _overlaps(left, right):
    if left == right:
        return True
    try:
        common = os.path.commonpath((left, right))
    except ValueError:
        return True
    return common in (left, right)


def _validate_roots(value):
    if type(value) is not tuple or len(value) != 3:
        _fail()
    for root in value:
        if (not _printable_ascii(root, 1, 1024) or
                not os.path.isabs(root) or os.path.normpath(root) != root):
            _fail()
    for index, left in enumerate(value):
        for right in value[index + 1:]:
            if _overlaps(left, right):
                _fail()


class ProjectContext:
    """Validated, immutable project identity and its three filesystem roots."""

    __slots__ = (
        "project_id",
        "slug",
        "allowed_origins",
        "canonical_root",
        "workspace_root",
        "tasks_root",
    )

    def __init__(self, *args, **kwargs):
        # Bind explicitly so unexpected keywords, duplicate fields and missing
        # fields all take the fixed-code refusal path instead of raising TypeError.
        if len(args) > len(_FIELDS):
            _fail()
        values = dict(zip(_FIELDS, args))
        for name, value in kwargs.items():
            if name not in _FIELDS or name in values:
                _fail()
            values[name] = value
        if len(values) != len(_FIELDS):
            _fail()
        project_id = values["project_id"]
        slug = values["slug"]
        allowed_origins = values["allowed_origins"]
        canonical_root = values["canonical_root"]
        workspace_root = values["workspace_root"]
        tasks_root = values["tasks_root"]
        _validate_project_id(project_id)
        _validate_slug(slug)
        _validate_origins(allowed_origins, slug)
        _validate_roots((canonical_root, workspace_root, tasks_root))
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "slug", slug)
        object.__setattr__(self, "allowed_origins", tuple(allowed_origins))
        object.__setattr__(self, "canonical_root", canonical_root)
        object.__setattr__(self, "workspace_root", workspace_root)
        object.__setattr__(self, "tasks_root", tasks_root)

    @classmethod
    def from_trust(cls, project_id, trust):
        """Build a context from a trust-shaped mapping without side effects."""
        _validate_project_id(project_id)
        if not isinstance(trust, Mapping) or set(trust.keys()) != _TRUST_KEYS:
            _fail()
        repository = trust["repository"]
        if not isinstance(repository, Mapping):
            _fail()
        keys = set(repository.keys())
        allowed_keys = _REPOSITORY_KEYS | _REPOSITORY_OPTIONAL_KEYS
        if not _REPOSITORY_KEYS <= keys <= allowed_keys:
            _fail()
        slug = repository["slug"]
        origins = repository["allowed_origins"]
        _validate_slug(slug)
        _validate_origins(origins, slug)
        if "approval_refs" in repository:
            _validate_approval_refs(repository["approval_refs"])
        return cls(project_id, slug, origins,
                   trust["canonical_root"], trust["workspace_root"], trust["tasks_root"])

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name):
        raise AttributeError(f"{type(self).__name__} is immutable")

    def _values(self):
        return (self.project_id, self.slug, self.allowed_origins,
                self.canonical_root, self.workspace_root, self.tasks_root)

    def __eq__(self, other):
        if not isinstance(other, ProjectContext):
            return NotImplemented
        return self._values() == other._values()

    def __hash__(self):
        return hash(self._values())

    def __repr__(self):
        return (f"ProjectContext(project_id={self.project_id!r}, slug={self.slug!r}, "
                f"allowed_origins={self.allowed_origins!r}, "
                f"canonical_root={self.canonical_root!r}, "
                f"workspace_root={self.workspace_root!r}, "
                f"tasks_root={self.tasks_root!r})")


def from_trust(project_id, trust):
    """Module-level alias for :meth:`ProjectContext.from_trust`."""
    return ProjectContext.from_trust(project_id, trust)


__all__ = [
    "PROJECT_CONTEXT_INVALID",
    "ProjectContext",
    "ProjectContextError",
    "from_trust",
]
