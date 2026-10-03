"""User trust boundary, config compatibility and pure platform location rules."""

import io
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import yaml

from llmtool import cli
from llmtool.config import DEFAULTS, resolve_config, show_config, user_config_path
from llmtool.configure import run_configure
from llmtool.diagnostics_settings import (DiagnosticsSettings, SETTINGS_BOUNDS,
    parse_diagnostics_settings, resolve_diagnostics_settings, resolve_diagnostics_home)
from llmtool.errors import LLMToolError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.user = user_config_path(self.home)
        self.user.parent.mkdir(parents=True)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()

    def save(self, value):
        self.user.write_text(yaml.safe_dump({'diagnostics': value}))

    def test_default_off_and_precedence(self):
        self.assertFalse(resolve_diagnostics_settings(home=self.home).persist_metadata)
        self.save({'persist_metadata': True, 'max_runs': 12})
        for override, expected in ((None, True), (False, False), (True, True)):
            settings = resolve_diagnostics_settings(home=self.home, persist_metadata=override)
            self.assertEqual(settings.persist_metadata, expected)
            self.assertEqual(settings.max_runs, 12)
        self.save({'persist_metadata': False})
        self.assertTrue(resolve_diagnostics_settings(home=self.home, persist_metadata=True).persist_metadata)

    def test_strict_types_ranges_unknown_and_cross_limit(self):
        bad = [None, [], True, {'persist_metadata': 1}, {'persist_metadata': 'true'},
               {'home': '/tmp'}, {'max_total_bytes': 1048576, 'max_run_bytes': 2097152}]
        for key, (low, high) in SETTINGS_BOUNDS.items():
            bad.extend({key: value} for value in (True, 1.5, '100', low - 1, high + 1))
            for value in (low, high):
                config = {key: value}
                if key == 'max_run_bytes':
                    config['max_total_bytes'] = max(1048576, value)
                self.assertEqual(getattr(parse_diagnostics_settings(config), key), value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(LLMToolError):
                parse_diagnostics_settings(value)
        with self.assertRaises(LLMToolError):
            resolve_diagnostics_settings(home=self.home, persist_metadata=1)

    def test_diagnostics_do_not_change_resolved_or_show_contract(self):
        before = show_config(resolve_config(environ={}, cwd=self.project, home=self.home))
        self.save({'persist_metadata': True, 'max_runs': 7})
        config = resolve_config(environ={}, cwd=self.project, home=self.home)
        self.assertEqual(config, DEFAULTS)
        self.assertEqual(show_config(config), before)

    def test_project_diagnostics_rejected(self):
        (self.project / '.llmtool.yaml').write_text('diagnostics:\n  persist_metadata: true\n')
        with self.assertRaises(LLMToolError):
            resolve_config(environ={}, cwd=self.project, home=self.home)

    def test_bad_user_settings_rejected_even_when_cli_disables_save(self):
        self.save({'max_runs': False})
        with self.assertRaises(LLMToolError):
            resolve_diagnostics_settings(home=self.home, persist_metadata=False)
        with self.assertRaises(LLMToolError):
            resolve_config(environ={}, cwd=self.project, home=self.home)

    def test_configure_preserves_exact_partial_mapping(self):
        original = {'persist_metadata': True, 'max_runs': 17}
        self.save(original)
        answers = iter(['', '', 'https://llm.example', 'model', '', '', '', '', '', ''])
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdin.isatty', return_value=True):
            run_configure(home=self.home, input_fn=lambda _: next(answers),
                          secret_fn=lambda *a, **k: '', stdout=out, stderr=err)
        self.assertEqual(yaml.safe_load(self.user.read_text())['diagnostics'], original)

    def test_invalid_config_fails_before_provider_io(self):
        self.save({'persist_metadata': 'yes'})
        with patch('llmtool.config.Path.home', return_value=self.home), \
                patch('llmtool.cli.create_client') as factory, \
                patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()) as err:
            self.assertEqual(cli.main(['ask', 'SAFE_INPUT']), 1)
        factory.assert_not_called()
        self.assertIn('code=CONFIG_INVALID', err.getvalue())

    def test_internal_option_precedence_without_public_parser_fields(self):
        self.save({'persist_metadata': True})
        for value in (None, True, False):
            self.assertEqual(resolve_diagnostics_settings(home=self.home,
                persist_metadata=value).persist_metadata, value is not False)
        args = cli.parser_for_cli().parse_args(['ask', 'x'])
        self.assertFalse(hasattr(args, 'persist_metadata'))


class LocationTests(unittest.TestCase):
    def test_platform_defaults_and_xdg(self):
        self.assertEqual(resolve_diagnostics_home(environ={}, platform='darwin', home='/home/u'),
                         Path('/home/u/Library/Application Support/llmtool'))
        for env, expected in (({}, '/home/u/.local/state/llmtool'),
                              ({'XDG_STATE_HOME': '/state'}, '/state/llmtool'),
                              ({'XDG_STATE_HOME': 'relative'}, '/home/u/.local/state/llmtool'),
                              ({'XDG_STATE_HOME': '/unsafe\n'}, '/home/u/.local/state/llmtool')):
            self.assertEqual(resolve_diagnostics_home(environ=env, platform='linux', home='/home/u'), Path(expected))
        self.assertEqual(resolve_diagnostics_home(environ={'LOCALAPPDATA': r'C:\Users\u\AppData\Local'},
                         platform='win32'), PureWindowsPath(r'C:\Users\u\AppData\Local\llmtool'))

    def test_explicit_location_pure_no_expansion_or_home_lookup(self):
        with patch('pathlib.Path.home', side_effect=AssertionError('home lookup')), \
                patch('os.open', side_effect=AssertionError('I/O')):
            self.assertEqual(resolve_diagnostics_home(environ={'LLMTOOL_HOME': '/safe/$NAME/~'}, platform='linux'),
                             Path('/safe/$NAME/~'))
        self.assertFalse(DiagnosticsSettings().persist_metadata)

    def test_invalid_locations_fail_safely(self):
        for value in ('', 'relative', '~/state', '/safe/../state', '/bad\x00path', '/bad\n', '/bad\x1b', '/bad\ud800'):
            with self.subTest(value=repr(value)), self.assertRaises(LLMToolError):
                resolve_diagnostics_home(environ={'LLMTOOL_HOME': value}, platform='linux')
        with self.assertRaises(LLMToolError):
            resolve_diagnostics_home(environ={}, platform='unsupported', home='/home/u')
