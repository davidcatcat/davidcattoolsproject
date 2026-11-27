"""Merged Linux diagnostics: a valid XDG state path resolves without a user-home lookup."""

import contextlib
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.diagnostics_settings import resolve_diagnostics_home

XDG_STATE = '/var/lib/llmtool-state'
EXPLICIT_OVERRIDE = '/opt/llmtool'
INJECTED_HOME = '/home/user'


@contextlib.contextmanager
def no_home_lookup_or_access():
    """User home lookup, filesystem and user settings access all fail if attempted."""
    with patch('pathlib.Path.home',
               side_effect=AssertionError('unavailable user home lookup')), \
            patch('os.open', side_effect=AssertionError('filesystem access')), \
            patch('llmtool.config._read_yaml',
                  side_effect=AssertionError('user settings access')):
        yield


class XdgStateResolverTests(unittest.TestCase):
    def test_valid_xdg_state_resolves_without_home_lookup(self):
        with no_home_lookup_or_access():
            resolved = resolve_diagnostics_home(
                environ={'XDG_STATE_HOME': XDG_STATE}, platform='linux')
        self.assertEqual(resolved, Path(XDG_STATE) / 'llmtool')

    def test_explicit_override_precedence_in_same_setup(self):
        with no_home_lookup_or_access():
            resolved = resolve_diagnostics_home(
                environ={'LLMTOOL_HOME': EXPLICIT_OVERRIDE,
                         'XDG_STATE_HOME': XDG_STATE},
                platform='linux')
        self.assertEqual(resolved, Path(EXPLICIT_OVERRIDE))

    def test_injected_home_fallback_never_performs_home_lookup(self):
        with no_home_lookup_or_access():
            resolved = resolve_diagnostics_home(
                environ={}, platform='linux', home=INJECTED_HOME)
        self.assertEqual(resolved, Path(INJECTED_HOME) / '.local/state/llmtool')


if __name__ == '__main__':
    unittest.main()
