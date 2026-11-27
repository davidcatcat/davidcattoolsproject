"""Project test-evidence profile: path patterns paired with runner kinds.

Issue #2793 package H3a.  A :class:`TestProfile` is an immutable value that
keeps an ordered tuple of entries, each pairing a compiled path pattern with
the runner kind that owns matching paths.  :func:`default_profile` returns
the profile equal to the current registry, in registry order.

This module is named ``test_profile`` but it is a production module: it
defines no test cases and performs no file, network, or process access.
"""

import re

__all__ = ['TestProfile', 'TestProfileError', 'default_profile']

_RUNNER_KINDS = ('unittest', 'script')


class TestProfileError(ValueError):
    """Invalid profile entry at construction time.

    Carries one fixed upper-case code and no input value: the message never
    contains the offending pattern, kind, path, or any other caller data.
    """

    CODE = 'TEST_PROFILE_INVALID'

    def __init__(self):
        super().__init__(self.CODE)


class TestProfile:
    """Immutable ordered profile of (compiled path pattern, runner kind)."""

    __slots__ = ('_entries', '_keys')

    def __init__(self, entries):
        """Build a profile from an ordered iterable of (pattern, kind) pairs.

        Raises :class:`TestProfileError` when the entry list is empty, when an
        entry is not a two-item pair whose first item is a compiled string
        pattern (a compiled bytes pattern is also refused), or when a kind
        is not exactly the string ``unittest`` or ``script``.
        """
        validated = []
        for entry in entries:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                raise TestProfileError()
            pattern, kind = entry
            if not isinstance(pattern, re.Pattern):
                raise TestProfileError()
            # A compiled bytes pattern is still a re.Pattern, but it would
            # make kind_for raise TypeError for every normal string path;
            # refuse it at construction with the fixed code instead.
            if isinstance(pattern.pattern, bytes):
                raise TestProfileError()
            # A type check first: plain membership relies on __eq__, so an
            # object with a permissive __eq__ could otherwise be accepted.
            if not isinstance(kind, str) or kind not in _RUNNER_KINDS:
                raise TestProfileError()
            validated.append((pattern, kind))
        if not validated:
            raise TestProfileError()
        # object.__setattr__ bypasses the __setattr__ guard below, which is
        # what makes every post-construction attribute assignment fail.
        object.__setattr__(self, '_entries', tuple(validated))
        object.__setattr__(
            self, '_keys',
            tuple((p.pattern, p.flags, k) for p, k in validated))

    @property
    def entries(self):
        """The ordered tuple of ``(pattern, kind)`` pairs."""
        return self._entries

    def kind_for(self, relative_path):
        """Return the kind of the first entry whose pattern matches the whole
        relative path, or ``None`` when none matches.

        The lookup anchors the match at both ends, so a caller-supplied
        pattern only counts when it matches the entire path: a pattern of
        ``foo`` never matches ``foobar``, even without an end anchor.  This
        is pure string matching: no file, network, or process access.
        ``relative_path`` is a slash-separated path relative to the project
        root.
        """
        for pattern, kind in self._entries:
            if pattern.fullmatch(relative_path) is not None:
                return kind
        return None

    def __eq__(self, other):
        """Profiles are equal when entry order, pattern sources, pattern
        flags, and kinds all match; pattern objects are compared by source
        and flags, not identity."""
        if type(other) is not TestProfile:
            return NotImplemented
        return other._keys == self._keys

    def __hash__(self):
        return hash(self._keys)

    def __setattr__(self, name, value):
        raise AttributeError(
            'TestProfile is immutable; it cannot change after construction')

    def __delattr__(self, name):
        raise AttributeError(
            'TestProfile is immutable; it cannot change after construction')

    def __repr__(self):
        parts = ', '.join('(re.compile(%r), %r)' % (p.pattern, k)
                          for p, k in self._entries)
        return 'TestProfile((%s))' % parts


# The current registry, in order.
_DEFAULT_PROFILE = TestProfile((
    (re.compile(r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
    (re.compile(r'scripts/test_[A-Za-z0-9_]+\.py\Z'), 'script'),
))


def default_profile():
    """Return the :class:`TestProfile` equal to the current registry."""
    return _DEFAULT_PROFILE
