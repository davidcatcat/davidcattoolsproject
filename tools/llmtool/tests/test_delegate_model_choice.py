"""#2302 P3: one-shot Developer model choice and a started Task keeps its model.

Covers: ``task delegate --model`` is one-shot and never saved as a default; a
repair uses the model recorded for the started Task; a model the provider
cannot run is refused with MODEL_UNSUPPORTED and nothing falls back; OpenCode
takes its default model from developer-routes.json; the worker-dispatch gate
and OpenCode free-only still apply. No test here holds credential-shaped data.
"""

import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import developer_routes
from llmtool import opencode_direct_developer
from llmtool import review_repair
from llmtool import role_config
from llmtool import task_submission_cli as submission_cli
from llmtool.cli import parser_for_cli
from llmtool.developer_routes import AgyRoute, ClaudeRoute, RouteError
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import DirectOpenCodeError

AGY_ROUTE = {'executable': 'agy', 'default_model': 'gemini-default',
             'allowed_models': ['gemini-default', 'gemini-chosen'],
             'default_effort': 'medium'}
CLAUDE_ROUTE = {'executable': 'claude', 'default_model': 'claude-opus-5-5',
                'allowed_models': ['claude-opus-5-5', 'claude-sonnet-5-5'],
                'default_effort': 'high'}


class _NoStore:
    """A store that fails the test on any use: refusals must come first."""

    def __getattr__(self, name):
        raise AssertionError('store used before the model refusal: ' + name)


def _code(error):
    return getattr(error, 'role_config_code', None)


class DelegateParserTests(unittest.TestCase):
    ARGV = ['task', 'delegate', 'a' * 32, '--provider', 'agy', '--mode', 'direct',
            '--selection-source', 'PM', '--executor-task', 'executor-task-1',
            '--read-path', 'README.md', '--write-path', 'README.md',
            '--acceptance', 'docs updated', '--egress-basis', 'synthetic fixture']

    def test_model_is_optional_and_one_shot(self):
        parser = parser_for_cli()
        self.assertIsNone(parser.parse_args(self.ARGV).model)
        args = parser.parse_args(self.ARGV + ['--model', 'gemini-chosen'])
        self.assertEqual(args.model, 'gemini-chosen')
        self.assertFalse(hasattr(args, 'recorded_model'),
                         'the CLI must not be able to set the recorded model')


class ModelChoiceTests(unittest.TestCase):
    def test_choice_sources(self):
        choose = submission_cli._delegate_model_choice
        self.assertIsNone(choose(Namespace(), 'agy-developer'))
        self.assertEqual(choose(Namespace(model='gemini-chosen'), 'agy-developer'),
                         'gemini-chosen')
        self.assertEqual(choose(Namespace(recorded_model='gemini-recorded'), 'agy-developer'),
                         'gemini-recorded')
        self.assertEqual(choose(Namespace(recorded_model='gemini-recorded',
                                          model='gemini-recorded'), 'agy-developer'),
                         'gemini-recorded')

    def test_malformed_or_conflicting_choice_is_model_unsupported(self):
        choose = submission_cli._delegate_model_choice
        for args in (Namespace(model='bad model'), Namespace(model=''),
                     Namespace(model=7), Namespace(recorded_model='a\nb'),
                     Namespace(recorded_model='gemini-recorded', model='gemini-other')):
            with self.subTest(args=args), self.assertRaises(LLMToolError) as caught:
                choose(args, 'agy-developer')
            self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')
            self.assertIn('MODEL_UNSUPPORTED', str(caught.exception))

    def test_route_without_model_choice_refuses_before_any_task_read(self):
        for provider in ('gw-agent', 'codex'):
            args = Namespace(task_id='a' * 32, provider=provider, model='some-model',
                             mode='direct', background=False, timeout=None)
            with self.subTest(provider=provider), self.assertRaises(LLMToolError) as caught:
                submission_cli.run_task_delegate(args, store=_NoStore())
            self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')

    def test_malformed_one_shot_model_refuses_before_any_task_read(self):
        args = Namespace(task_id='a' * 32, provider='agy', model='bad model',
                         mode='direct', background=False, timeout=None)
        with self.assertRaises(LLMToolError) as caught:
            submission_cli.run_task_delegate(args, store=_NoStore())
        self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')

    def test_worker_dispatch_gate_still_applies_with_a_model_choice(self):
        args = Namespace(task_id='a' * 32, provider='opencode', model='opencode/chosen-free',
                         mode='proposal', background=True, timeout=None,
                         acceptance=['docs updated'])
        denied = SimpleNamespace(allowed=False, reason_code='WORKER_DISPATCH_PAUSED')
        with patch.object(submission_cli.worker_dispatch, 'evaluate',
                          return_value=denied) as evaluate:
            with self.assertRaisesRegex(LLMToolError, 'WORKER_DISPATCH_PAUSED'):
                submission_cli.run_task_delegate(args, store=_NoStore())
        evaluate.assert_called_once_with('opencode-developer')


class RouteModelSupportTests(unittest.TestCase):
    def test_agy_validate_model(self):
        route = AgyRoute(dict(AGY_ROUTE))
        self.assertEqual(route.validate_model('gemini-chosen'), 'gemini-chosen')
        for model in ('bad model', '', None, '-leading'):
            with self.subTest(model=model), self.assertRaises(RouteError) as caught:
                route.validate_model(model)
            self.assertEqual(caught.exception.code, 'MODEL_UNSUPPORTED')

    def test_claude_validate_model(self):
        route = ClaudeRoute(dict(CLAUDE_ROUTE))
        self.assertEqual(route.validate_model('claude-sonnet-5-5'), 'claude-sonnet-5-5')
        for model in ('gpt-6-luna', 'Claude-Upper', None):
            with self.subTest(model=model), self.assertRaises(RouteError) as caught:
                route.validate_model(model)
            self.assertEqual(caught.exception.code, 'MODEL_UNSUPPORTED')


class AgyProfileTests(unittest.TestCase):
    def test_without_choice_resolver_call_is_unchanged(self):
        with patch.object(submission_cli.role_config, 'resolve_role', return_value={
                'model': None, 'effort': None, 'account': None}) as resolve:
            self.assertEqual(submission_cli._agy_developer_profile(dict(AGY_ROUTE)),
                             ('gemini-default', 'medium'))
        resolve.assert_called_once_with('agy-developer')

    def test_one_shot_model_is_the_cli_layer_and_wins_over_config(self):
        role = {'model': 'gemini-default', 'effort': None, 'account': None,
                'model_source': 'roles'}
        with patch.object(submission_cli.role_config, 'resolve_role',
                          return_value=role) as resolve:
            self.assertEqual(submission_cli._agy_developer_profile(
                dict(AGY_ROUTE), model_choice='gemini-chosen'), ('gemini-chosen', 'medium'))
        resolve.assert_called_once_with('agy-developer', cli={'model': 'gemini-chosen'})

    def test_unsupported_choice_is_refused_without_fallback(self):
        role = {'model': None, 'effort': None, 'account': None}
        with patch.object(submission_cli.role_config, 'resolve_role',
                          return_value=role) as resolve:
            with self.assertRaises(LLMToolError) as caught:
                submission_cli._agy_developer_profile(dict(AGY_ROUTE),
                                                      model_choice='bad model')
        self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')
        self.assertEqual(resolve.call_count, 1)

    def test_route_check_refusal_of_a_choice_is_model_unsupported(self):
        role = {'model': None, 'effort': None, 'account': None}
        with patch.object(submission_cli.role_config, 'resolve_role', return_value=role), \
                patch.object(submission_cli, 'check_route_model',
                             side_effect=RouteError('ROUTE_MODEL_INVALID')) as check:
            with self.assertRaises(LLMToolError) as caught:
                submission_cli._agy_developer_profile(dict(AGY_ROUTE),
                                                      model_choice='gemini-chosen')
        self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')
        check.assert_called_once()
        self.assertEqual(check.call_args.args[2], 'gemini-chosen')

    def test_resolver_refusal_maps_by_source(self):
        refused = LLMToolError('refused')
        refused.role_config_code = role_config.ROLE_MODEL_NOT_ALLOWED
        refused.owner_override_code = None
        with patch.object(submission_cli.role_config, 'resolve_role', side_effect=refused):
            with self.assertRaises(LLMToolError) as chosen:
                submission_cli._agy_developer_profile(dict(AGY_ROUTE),
                                                      model_choice='gemini-chosen')
            with self.assertRaises(LLMToolError) as configured:
                submission_cli._agy_developer_profile(dict(AGY_ROUTE))
        self.assertEqual(_code(chosen.exception), 'MODEL_UNSUPPORTED')
        self.assertEqual(_code(configured.exception), role_config.ROLE_MODEL_NOT_ALLOWED)

    def test_effort_refusal_keeps_its_code_with_a_choice(self):
        role = {'model': None, 'effort': 'extreme', 'account': None}
        with patch.object(submission_cli.role_config, 'resolve_role', return_value=role):
            with self.assertRaises(LLMToolError) as caught:
                submission_cli._agy_developer_profile(dict(AGY_ROUTE),
                                                      model_choice='gemini-chosen')
        self.assertEqual(_code(caught.exception), 'AGY_ROLE_PROFILE_INVALID')


class ClaudeProfileTests(unittest.TestCase):
    def setUp(self):
        self.config_dir = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, self.config_dir)

    def _role(self, model=None):
        return {'model': model, 'effort': None, 'account': None,
                'config_dir': self.config_dir, 'model_source': 'roles'}

    def test_one_shot_model_is_used(self):
        with patch.object(submission_cli.role_config, 'resolve_role',
                          return_value=self._role('claude-opus-5-5')) as resolve:
            model, effort, config_dir = submission_cli._claude_developer_profile(
                dict(CLAUDE_ROUTE), model_choice='claude-sonnet-5-5')
        self.assertEqual((model, effort, config_dir),
                         ('claude-sonnet-5-5', 'high', self.config_dir))
        resolve.assert_called_once_with('claude-developer',
                                        cli={'model': 'claude-sonnet-5-5'})

    def test_non_claude_model_is_model_unsupported(self):
        with patch.object(submission_cli.role_config, 'resolve_role',
                          return_value=self._role()):
            with self.assertRaises(LLMToolError) as caught:
                submission_cli._claude_developer_profile(dict(CLAUDE_ROUTE),
                                                         model_choice='gpt-6-luna')
        self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')


class OpenCodeProfileTests(unittest.TestCase):
    """Real role_config.resolve_role on an empty temporary user home."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        real = role_config.resolve_role
        self.resolve_calls = []

        def resolve(role, **kwargs):
            self.resolve_calls.append((role, kwargs))
            return real(role, environ={}, home=Path(self.home.name), **kwargs)

        for target in (patch.object(submission_cli.role_config, 'resolve_role',
                                    side_effect=resolve),
                       patch.object(submission_cli, 'resolve_profile_config',
                                    return_value=object())):
            target.start()
            self.addCleanup(target.stop)
        self.profile_model = None
        selected = patch.object(submission_cli, 'selected_provider', side_effect=lambda _:
                                {'model': self.profile_model, 'provider_options': {}})
        selected.start()
        self.addCleanup(selected.stop)

    def _home_files(self):
        return sorted(str(path.relative_to(self.home.name))
                      for path in Path(self.home.name).rglob('*'))

    def test_one_shot_model_is_used_once_and_never_saved(self):
        self.profile_model = 'opencode/profile-model-free#high'
        before = self._home_files()
        self.assertEqual(submission_cli._opencode_developer_profile('opencode/chosen-free'),
                         ('opencode/chosen-free', opencode_direct_developer.DEFAULT_AGENT_STEPS))
        self.assertEqual(self.resolve_calls[-1],
                         ('opencode-developer', {'cli': {'model': 'opencode/chosen-free'}}))
        # The next delegate without a choice is back on the configuration.
        self.assertEqual(submission_cli._opencode_developer_profile(),
                         ('opencode/profile-model-free#high',
                          opencode_direct_developer.DEFAULT_AGENT_STEPS))
        self.assertEqual(self._home_files(), before)

    def test_real_resolver_reports_cli_source_and_does_not_persist(self):
        resolved = role_config.resolve_role('opencode-developer',
                                            cli={'model': 'opencode/chosen-free'})
        self.assertEqual((resolved['model'], resolved['source']),
                         ('opencode/chosen-free', 'cli'))
        self.assertIsNone(role_config.resolve_role('opencode-developer')['model'])
        self.assertEqual(self._home_files(), [])

    def test_paid_choice_is_model_unsupported_without_fallback(self):
        self.profile_model = 'opencode/profile-model-free'
        for model in ('opencode/paid-model', 'other/name-free'):
            with self.subTest(model=model), self.assertRaises(LLMToolError) as caught:
                submission_cli._opencode_developer_profile(model)
            self.assertEqual(_code(caught.exception), 'MODEL_UNSUPPORTED')
            self.assertIn('opencode/<name>-free', str(caught.exception))

    def test_default_model_comes_from_the_route_configuration(self):
        routes = {'routes': {'opencode': {'default_model': 'opencode/route-default-free'}}}
        with patch.object(developer_routes, 'load_route_config', return_value=routes):
            self.assertEqual(submission_cli._opencode_developer_profile(),
                             ('opencode/route-default-free',
                              opencode_direct_developer.DEFAULT_AGENT_STEPS))

    def test_invalid_route_default_is_refused_not_replaced(self):
        for entry in ({'default_model': 'opencode/paid-model'}, {}, None):
            routes = {'routes': {'opencode': entry}}
            with self.subTest(entry=entry), \
                    patch.object(developer_routes, 'load_route_config', return_value=routes):
                with self.assertRaises(LLMToolError) as caught:
                    submission_cli._opencode_developer_profile()
            self.assertEqual(_code(caught.exception), 'OPENCODE_ROUTE_MODEL_INVALID')


class RouteDefaultModelTests(unittest.TestCase):
    def test_route_default_model(self):
        config = {'routes': {'opencode': {'default_model': 'opencode/route-default-free#high'}}}
        self.assertEqual(opencode_direct_developer.route_default_model(config),
                         'opencode/route-default-free#high')
        for bad in ({'routes': {'opencode': {'default_model': 'opencode/paid'}}},
                    {'routes': {}}, {}, []):
            with self.subTest(config=bad), self.assertRaises(DirectOpenCodeError) as caught:
                opencode_direct_developer.route_default_model(bad)
            self.assertEqual(caught.exception.direct_opencode_code,
                             'OPENCODE_ROUTE_MODEL_INVALID')

    def test_unreadable_route_file_is_refused(self):
        with patch.object(developer_routes, 'load_route_config',
                          side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            with self.assertRaises(DirectOpenCodeError) as caught:
                opencode_direct_developer.route_default_model()
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_ROUTE_MODEL_INVALID')


class RepairKeepsRecordedModelTests(unittest.TestCase):
    def _call(self, developer):
        captured = []
        task = {'task_id': 'b' * 32, 'pm_assignment': {'acceptance': ['docs updated']}}
        store = SimpleNamespace(get=lambda task_id: {'task_id': task_id})
        with patch.object(review_repair, '_registered_d6',
                          return_value=(SimpleNamespace(root='/d6/root'), None)):
            review_repair._delegate_repair(
                task=task, developer=developer, packet={'task': 'c' * 32},
                executor_task='executor-task-1', store=store,
                delegate=lambda args, store: captured.append(args) or 0)
        return captured

    def _developer(self, model):
        return {'provider_profile': 'agy', 'model': model, 'read_paths': ['README.md'],
                'write_paths': ['README.md'], 'egress_basis': 'synthetic fixture',
                'owner_assignment_ref': None}

    def test_repair_passes_the_recorded_model(self):
        captured = self._call(self._developer('gemini-recorded'))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].recorded_model, 'gemini-recorded')
        self.assertFalse(hasattr(captured[0], 'model'))

    def test_repair_without_a_recorded_model_is_refused_before_delegate(self):
        for model in (None, ''):
            with self.subTest(model=model), self.assertRaises(LLMToolError) as caught:
                self._call(self._developer(model))
            self.assertEqual(caught.exception.review_repair_code, 'REPAIR_MODEL_UNKNOWN')

    def test_config_change_mid_task_does_not_change_the_repair_model(self):
        captured = self._call(self._developer('gemini-chosen'))
        # The configuration now names another model; the repair keeps its own.
        role = {'model': 'gemini-default', 'effort': None, 'account': None,
                'model_source': 'roles'}
        with patch.object(submission_cli.role_config, 'resolve_role',
                          return_value=role) as resolve:
            model, _effort = submission_cli._agy_developer_profile(
                dict(AGY_ROUTE), model_choice=submission_cli._delegate_model_choice(
                    captured[0], 'agy-developer'))
        self.assertEqual(model, 'gemini-chosen')
        resolve.assert_called_once_with('agy-developer', cli={'model': 'gemini-chosen'})


if __name__ == '__main__':
    unittest.main()
