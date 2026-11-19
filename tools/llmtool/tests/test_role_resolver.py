"""#1502 WP-E: role_config.resolve_role as the single role resolver.

GitHub reads are injected fakes; nothing here opens a network connection.
"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from llmtool import cli, developer_routes, role_config, roles_cli, task_submission_cli
from llmtool.config import user_config_path
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError
from llmtool.owner_override import _MARKER

REPOSITORY = 'example-owner/example-repo'
REF = 'https://github.com/' + REPOSITORY + '/issues/12#issuecomment-345'
ISSUE_URL = 'https://api.github.com/repos/' + REPOSITORY + '/issues/12'
# An agy route fixture in the older file shape (a one-item allowed_models
# list). Owner #2302 (decision 11-17): the list is never consulted; any
# well-formed model id of any family resolves, and only a malformed id
# (MALFORMED) is refused, on every path.
ROUTE = {'default_model': 'gemini-default', 'default_effort': 'medium',
         'allowed_models': ['gemini-default']}
# A gw-agent route fixture; its list is not consulted either.
GW_ROUTE = {'default_model': 'gw', 'default_effort': 'low',
            'allowed_models': ['gw']}
# The claude-reviewer default model in developer-routes.json (#1502 WP-E).
REVIEWER_MODEL = 'claude-sonnet-5-5'
# Malformed model ids: refused at resolve and dispatch time.
MALFORMED = ('has space', 'https://example.invalid/m', '-leading-dash')


def comment(route='agy', model='model-extra', marker=True):
    lines = ['Relayed Owner decision.']
    if marker:
        lines.append(_MARKER)
    lines.append('owner-model-override: route=' + route + ' model=' + model)
    return {'body': '\n'.join(lines), 'issue_url': ISSUE_URL}


class ResolverBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.user = user_config_path(self.home)
        self.user.parent.mkdir(parents=True)
        self.env = {}

    def save(self, value):
        self.user.write_text(yaml.safe_dump(value), encoding='utf-8')

    def resolve(self, role, **kwargs):
        kwargs.setdefault('environ', self.env)
        return role_config.resolve_role(role, home=self.home, **kwargs)

    def isolated_resolver(self):
        """Patch the module resolver so callers use the real one on a temp home."""
        real = role_config.resolve_role
        calls = []

        def resolver(role, **kwargs):
            calls.append(role)
            kwargs.setdefault('environ', self.env)
            kwargs.setdefault('home', self.home)
            return real(role, **kwargs)

        patcher = mock.patch.object(role_config, 'resolve_role', side_effect=resolver)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class PrecedenceTests(ResolverBase):
    def test_repo_default_wins_when_no_layer_sets_the_model(self):
        resolved = self.resolve('agy-developer', route_config=ROUTE)
        self.assertEqual((resolved['model'], resolved['effort'], resolved['source'],
                          resolved['model_source']),
                         ('gemini-default', 'medium', 'repo_default', 'repo_default'))
        self.assertEqual(resolved['effort_source'], 'repo_default')

    def test_repo_default_comes_from_developer_routes_json(self):
        routes = load_route_config()['routes']
        for role, route in sorted(role_config.ROUTE_ROLES.items()):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual(resolved['model'], routes[route]['default_model'])
                self.assertEqual(resolved['effort'], routes[route]['default_effort'])

    def test_user_config_wins_over_repo_default(self):
        self.save({'roles': {'agy-developer': {'model': 'gemini-user', 'effort': 'low'}}})
        resolved = self.resolve('agy-developer', route_config=ROUTE)
        self.assertEqual((resolved['model'], resolved['effort'], resolved['source'],
                          resolved['model_source']),
                         ('gemini-user', 'low', 'roles', 'roles'))
        self.assertEqual(resolved['effort_source'], 'roles')

    def test_env_wins_over_user_config_with_either_prefix_spelling(self):
        self.save({'roles': {'agy-developer': {'model': 'gemini-user'}}})
        for prefix in ('LLMTOOL_ROLE_AGY-DEVELOPER_', 'LLMTOOL_ROLE_AGY_DEVELOPER_'):
            with self.subTest(prefix=prefix):
                self.env = {prefix + 'MODEL': 'gemini-env'}
                resolved = self.resolve('agy-developer', route_config=ROUTE)
                self.assertEqual((resolved['model'], resolved['source'],
                                  resolved['model_source']), ('gemini-env', 'env', 'env'))

    def test_conflicting_env_spellings_are_refused(self):
        self.env = {'LLMTOOL_ROLE_AGY-DEVELOPER_MODEL': 'gemini-env',
                    'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'gemini-user'}
        with self.assertRaises(LLMToolError) as caught:
            self.resolve('agy-developer', route_config=ROUTE)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_CONFIG_OVERRIDE_INVALID')

    def test_cli_wins_over_env(self):
        self.save({'roles': {'agy-developer': {'model': 'gemini-user'}}})
        self.env = {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'gemini-env'}
        resolved = self.resolve('agy-developer', cli={'model': 'gemini-cli'}, route_config=ROUTE)
        self.assertEqual((resolved['model'], resolved['source'], resolved['model_source']),
                         ('gemini-cli', 'cli', 'cli'))

    def test_non_developer_roles_keep_their_previous_shape(self):
        self.assertEqual(self.resolve('coder'), {
            'role': 'coder', 'provider': None, 'provider_profile': None, 'account': None,
            'config_dir': None, 'model': None, 'effort': None, 'source': 'builtin'})


class AllowedModelTests(ResolverBase):
    """Owner #2302 (decision 11-17): any well-formed model id, no permit list."""

    def test_any_family_model_resolves_at_every_layer(self):
        for model in ('model-extra', 'claude-sonnet-5-5-medium', 'gpt-6.1-sol',
                      'qwen3-coder'):
            cases = (
                ({'roles': {'agy-developer': {'model': model}}}, {}, None, 'roles'),
                ({}, {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': model}, None, 'env'),
                ({}, {}, {'model': model}, 'cli'),
            )
            for config, env, cli_values, layer in cases:
                with self.subTest(model=model, layer=layer):
                    self.save(config)
                    self.env = env
                    resolved = self.resolve('agy-developer', cli=cli_values,
                                            route_config=ROUTE)
                    self.assertEqual((resolved['model'], resolved['model_source']),
                                     (model, layer))
                    self.assertNotIn('owner_override', resolved)

    def test_malformed_model_is_refused_at_every_layer(self):
        for model in MALFORMED:
            cases = (
                ({'roles': {'agy-developer': {'model': model}}}, {}, None),
                ({}, {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': model}, None),
                ({}, {}, {'model': model}),
            )
            for config, env, cli_values in cases:
                with self.subTest(model=model, config=config, env=env, cli=cli_values):
                    self.save(config)
                    self.env = env
                    with self.assertRaises(LLMToolError) as caught:
                        self.resolve('agy-developer', cli=cli_values, route_config=ROUTE)
                    self.assertEqual(caught.exception.role_config_code,
                                     'ROLE_MODEL_NOT_ALLOWED')
                    self.assertIsNone(caught.exception.owner_override_code)

    def test_real_routes_accept_a_model_of_any_family_from_user_config(self):
        # Owner #2302: no allowed_models entry and no owner override needed.
        for role, model in (('agy-developer', 'gemini-4-hydrogen-medium'),
                            ('agy-developer', 'claude-sonnet-5-5-medium'),
                            ('claude-developer', 'claude-fable-5-2'),
                            ('claude-developer', 'gemini-4-hydrogen-medium'),
                            ('gw-agent-developer', 'qwen3-coder')):
            with self.subTest(role=role, model=model):
                self.save({'roles': {role: {'model': model}}})
                resolved = self.resolve(role)
                self.assertEqual((resolved['model'], resolved['model_source'],
                                  resolved['source']), (model, 'roles', 'roles'))
                self.assertNotIn('owner_override', resolved)

    def test_real_routes_accept_a_model_of_any_family_from_env_and_cli(self):
        for role, model in (('agy-developer', 'claude-sonnet-5-5-medium'),
                            ('claude-developer', 'gemini-4-hydrogen-medium')):
            env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
            with self.subTest(role=role, layer='env'):
                self.env = {env_name: model}
                self.assertEqual(self.resolve(role)['model'], model)
            with self.subTest(role=role, layer='cli'):
                self.env = {}
                self.assertEqual(self.resolve(role, cli={'model': model})['model'], model)

    def test_agy_developer_switches_model_by_user_config_only(self):
        # #2302 A4: Claude and back to Gemini, with the real route file.
        for model in ('claude-sonnet-5-5-medium', 'gemini-3.8-flash-medium',
                      'claude-sonnet-5-5-medium'):
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                resolved = self.resolve('agy-developer')
                self.assertEqual((resolved['model'], resolved['model_source']),
                                 (model, 'roles'))

    def test_review_and_debug_roles_accept_a_model_of_any_family(self):
        for role in ('codex-reviewer', 'claude-reviewer', 'codex-debug', 'claude-debug'):
            for model in ('gpt-6.1-sol', 'claude-fable-5-2', 'gemini-4-hydrogen-medium'):
                with self.subTest(role=role, model=model):
                    resolved = self.resolve(role, cli={'model': model})
                    self.assertEqual((resolved['model'], resolved['model_source']),
                                     (model, 'cli'))
            with self.subTest(role=role, model='malformed'):
                with self.assertRaises(LLMToolError) as caught:
                    self.resolve(role, cli={'model': 'has space'})
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_real_routes_refuse_a_malformed_model(self):
        for role in ('agy-developer', 'claude-developer', 'gw-agent-developer'):
            for model in MALFORMED:
                with self.subTest(role=role, model=model):
                    self.save({'roles': {role: {'model': model}}})
                    with self.assertRaises(LLMToolError) as caught:
                        self.resolve(role)
                    self.assertEqual(caught.exception.role_config_code,
                                     'ROLE_MODEL_NOT_ALLOWED')
                    self.assertIsNone(caught.exception.owner_override_code)

    def test_an_owner_override_is_never_read(self):
        # No model needs an override; a supplied one is not read or attached,
        # whether it would verify or not.
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        cases = (('agy-developer', ROUTE, 'gemini-default'),
                 ('agy-developer', ROUTE, 'claude-fable-5-2'),
                 ('gw-agent-developer', GW_ROUTE, 'model-extra'))
        for role, route, model in cases:
            for override in ({'model': model, 'ref': REF},
                             {'model': 'model-other', 'ref': 'https://example.invalid/1'}):
                with self.subTest(role=role, model=model, override=override):
                    resolved = self.resolve(role, route_config=route,
                                            cli={'model': model},
                                            owner_override=override,
                                            repository=REPOSITORY, fetch_comment=fetch)
                    self.assertEqual(resolved['model'], model)
                    self.assertNotIn('owner_override', resolved)
        fetch.assert_not_called()

    def test_route_without_allowed_models_still_resolves(self):
        route = {key: value for key, value in ROUTE.items() if key != 'allowed_models'}
        self.save({'roles': {'agy-developer': {'model': 'gemini-4-hydrogen-medium'}}})
        resolved = self.resolve('agy-developer', route_config=route)
        self.assertEqual(resolved['model'], 'gemini-4-hydrogen-medium')
        with self.assertRaises(LLMToolError) as caught:
            self.resolve('agy-developer', route_config=dict(route, allowed_models='x'))
        self.assertEqual(caught.exception.role_config_code, 'ROUTE_CONFIG_INVALID')

    def test_owner_override_never_admits_a_malformed_model(self):
        # A matching override comment cannot admit a malformed model; the
        # format check refuses and the ref is never read.
        claude_route = {'default_model': 'claude-default', 'default_effort': 'high'}
        cases = (('agy-developer', ROUTE, 'agy'),
                 ('claude-developer', claude_route, 'claude'))
        for role, route, name in cases:
            for model in ('-leading-dash', 'example://model'):
                with self.subTest(role=role, model=model):
                    self.save({'roles': {role: {'model': model}}})
                    fetch = mock.Mock(return_value=comment(route=name, model=model))
                    with self.assertRaises(LLMToolError) as caught:
                        self.resolve(role, route_config=route,
                                     owner_override={'model': model, 'ref': REF},
                                     repository=REPOSITORY, fetch_comment=fetch)
                    self.assertEqual(caught.exception.role_config_code,
                                     'ROLE_MODEL_NOT_ALLOWED')
                    self.assertIsNone(caught.exception.owner_override_code)
                    fetch.assert_not_called()

    def test_malformed_gw_agent_model_never_reads_the_override_ref(self):
        self.save({'roles': {'gw-agent-developer': {'model': 'has space'}}})
        fetch = mock.Mock(return_value=comment(route='gw-agent', model='has space'))
        with self.assertRaises(LLMToolError) as caught:
            self.resolve('gw-agent-developer', route_config=GW_ROUTE,
                         owner_override={'model': 'has space', 'ref': REF},
                         repository=REPOSITORY, fetch_comment=fetch)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertIsNone(getattr(caught.exception, 'owner_override_code', None))
        fetch.assert_not_called()


class AccountBase(ResolverBase):
    def setUp(self):
        super().setUp()
        self.account_dir = self.root / 'account'
        self.account_dir.mkdir(mode=0o700)
        self.account_dir.chmod(0o700)
        self.accounts = {'work': {'kind': 'claude', 'config_dir': str(self.account_dir)}}


class AliasTests(AccountBase):
    def test_legacy_reviewer_entry_resolves_for_the_new_id(self):
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'account': 'work', 'model': REVIEWER_MODEL}}})
        for role in ('claude-reviewer', 'reviewer'):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual((resolved['role'], resolved['account'], resolved['model'],
                                  resolved['source']), (role, 'work', REVIEWER_MODEL, 'roles'))

    def test_exact_role_entry_wins_over_its_alias(self):
        # The registry lists one claude-reviewer model, so the entries differ by effort.
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'effort': 'low'},
                             'claude-reviewer': {'effort': 'high'}}})
        self.assertEqual(self.resolve('claude-reviewer')['effort'], 'high')
        self.assertEqual(self.resolve('reviewer')['effort'], 'low')

    def add_codex_account(self):
        codex_dir = self.root / 'codex-account'
        codex_dir.mkdir(mode=0o700)
        codex_dir.chmod(0o700)
        self.accounts['codex-work'] = {'kind': 'codex', 'config_dir': str(codex_dir)}

    def assert_alias_refused(self, role):
        with self.assertRaises(LLMToolError) as caught:
            self.resolve(role)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_ALIAS_PROVIDER_MISMATCH')
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_legacy_alias_naming_a_codex_account_is_refused(self):
        # #1777 (CWE-863): a legacy alias cannot bind claude-reviewer to Codex.
        self.add_codex_account()
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'account': 'codex-work', 'model': 'sonnet'}}})
        for role in ('claude-reviewer', 'reviewer'):
            with self.subTest(role=role):
                self.assert_alias_refused(role)

    def test_legacy_alias_naming_a_codex_provider_is_refused(self):
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'provider': 'codex'}}})
        for role in ('claude-reviewer', 'reviewer'):
            with self.subTest(role=role):
                self.assert_alias_refused(role)

    def test_legacy_role_name_cannot_resolve_a_codex_bound_new_entry(self):
        # 'reviewer' stands for claude-reviewer, so its target entry is checked too.
        self.add_codex_account()
        self.save({'accounts': self.accounts,
                   'roles': {'claude-reviewer': {'account': 'codex-work'}}})
        self.assert_alias_refused('reviewer')

    def test_exact_entry_is_used_without_the_mismatched_alias(self):
        self.add_codex_account()
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'account': 'codex-work'},
                             'claude-reviewer': {'account': 'work'}}})
        resolved = self.resolve('claude-reviewer')
        self.assertEqual((resolved['provider'], resolved['account']), ('claude', 'work'))
        self.assert_alias_refused('reviewer')

    def test_legacy_alias_with_a_matching_provider_resolves(self):
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'provider': 'claude', 'account': 'work'}}})
        resolved = self.resolve('claude-reviewer')
        self.assertEqual((resolved['provider'], resolved['account'], resolved['config_dir']),
                         ('claude', 'work', str(self.account_dir)))

    def test_alias_check_fails_closed_for_a_target_without_a_provider(self):
        aliases = dict(role_config.LEGACY_ROLE_ALIASES, tester='unbound-tester')
        self.save({'accounts': self.accounts,
                   'roles': {'tester': {'account': 'work'}}})
        with mock.patch.object(role_config, 'LEGACY_ROLE_ALIASES', aliases):
            self.assert_alias_refused('unbound-tester')


class DispatchPathTests(AccountBase):
    def routes(self):
        return load_route_config()['routes']

    def test_agy_dispatch_uses_the_resolver_and_repo_default(self):
        calls = self.isolated_resolver()
        route = dict(self.routes()['agy'])
        model, _effort = task_submission_cli._agy_developer_profile(route)
        self.assertEqual(model, route['default_model'])
        self.assertEqual(calls, ['agy-developer'])

    def test_agy_dispatch_accepts_a_model_of_any_family(self):
        # Owner #2302: none of these models is listed in developer-routes.json.
        calls = self.isolated_resolver()
        for model in ('gemini-4-hydrogen-medium', 'model-extra', 'claude-fable-5-2'):
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                found, _effort = task_submission_cli._agy_developer_profile(
                    dict(self.routes()['agy']))
                self.assertEqual(found, model)
        self.assertEqual(calls, ['agy-developer'] * 3)

    def test_agy_dispatch_switches_to_claude_and_back_by_user_config_only(self):
        # #2302 A4: the refused case in the #2302 evidence, then back.
        self.isolated_resolver()
        for model in ('claude-sonnet-5-5-medium', 'gemini-3.8-flash-medium'):
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                model_run = {}
                found, _effort = task_submission_cli._agy_developer_profile(
                    dict(self.routes()['agy']), model_run=model_run)
                self.assertEqual(found, model)
                self.assertEqual(model_run, {
                    'requested': model, 'resolved': model, 'source': 'roles',
                    'family': 'claude' if model.startswith('claude-') else 'gemini',
                    'provider_reported': None})

    def test_agy_dispatch_refuses_a_malformed_model(self):
        self.isolated_resolver()
        for model in MALFORMED:
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._agy_developer_profile(dict(self.routes()['agy']))
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_claude_dispatch_accepts_a_model_of_any_family(self):
        # Owner #2302: neither model is listed in developer-routes.json.
        calls = self.isolated_resolver()
        for model in ('claude-fable-5-2', 'gemini-4-hydrogen-medium'):
            with self.subTest(model=model):
                self.save({'accounts': self.accounts,
                           'roles': {'claude-developer': {'account': 'work',
                                                          'model': model}}})
                found, _effort, account_dir = task_submission_cli._claude_developer_profile(
                    dict(self.routes()['claude']))
                self.assertEqual(found, model)
                self.assertEqual(str(account_dir), str(self.account_dir))
        self.assertEqual(calls, ['claude-developer'] * 2)

    def test_claude_dispatch_refuses_a_malformed_model(self):
        calls = self.isolated_resolver()
        self.save({'accounts': self.accounts,
                   'roles': {'claude-developer': {'account': 'work',
                                                  'model': 'has space'}}})
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._claude_developer_profile(dict(self.routes()['claude']))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertEqual(calls, ['claude-developer'])

    def test_dispatch_checks_again_when_a_resolver_returns_a_malformed_model(self):
        role = {'model': 'has space', 'effort': None, 'account': None,
                'config_dir': str(self.account_dir)}
        with mock.patch.object(role_config, 'resolve_role', return_value=role):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._claude_developer_profile(dict(self.routes()['claude']))
            self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
            # AGY's own profile check refuses the malformed model first.
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._agy_developer_profile(dict(self.routes()['agy']))
            self.assertEqual(caught.exception.role_config_code, 'AGY_ROLE_PROFILE_INVALID')

    def test_dispatch_model_check_is_format_only(self):
        # No list membership and no owner override is consulted.
        route = dict(GW_ROUTE)
        verified = {'route': 'gw-agent', 'model': 'model-extra', 'ref': REF,
                    'comment_id': 345}
        for role in ({'owner_override': verified}, {'owner_override': None}, {}, None):
            with self.subTest(role=role):
                self.assertIsNone(task_submission_cli._require_developer_model(
                    'gw-agent-developer', 'gw-agent', route, 'model-extra', role))
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._require_developer_model(
                        'gw-agent-developer', 'gw-agent', route, 'has space', role)
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_dispatch_never_reads_an_override(self):
        self.isolated_resolver()
        self.save({'roles': {'agy-developer': {'model': 'claude-fable-5-2'}}})
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        model, _effort = task_submission_cli._agy_developer_profile(
            dict(self.routes()['agy']),
            owner_override={'model': 'claude-fable-5-2', 'ref': REF},
            repository=REPOSITORY, fetch_comment=fetch)
        self.assertEqual(model, 'claude-fable-5-2')
        fetch.assert_not_called()

    def test_model_run_records_requested_resolved_source_and_family(self):
        # #2302 A2: one record per dispatch, filled by the profile helper.
        self.isolated_resolver()
        agy = dict(self.routes()['agy'])
        with self.subTest(source='repo_default'):
            self.save({})
            run = {}
            task_submission_cli._agy_developer_profile(agy, model_run=run)
            self.assertEqual(run, {'requested': None, 'resolved': agy['default_model'],
                                   'source': 'repo_default', 'family': 'gemini',
                                   'provider_reported': None})
        with self.subTest(source='env'):
            self.env = {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'gpt-6.1-sol'}
            run = {}
            task_submission_cli._agy_developer_profile(agy, model_run=run)
            self.assertEqual((run['requested'], run['source'], run['family']),
                             ('gpt-6.1-sol', 'env', 'codex'))
        self.env = {}
        with self.subTest(source='unknown family'):
            self.save({'accounts': self.accounts,
                       'roles': {'claude-developer': {'account': 'work',
                                                      'model': 'model-extra'}}})
            run = {}
            task_submission_cli._claude_developer_profile(
                dict(self.routes()['claude']), model_run=run)
            self.assertEqual(run, {'requested': 'model-extra', 'resolved': 'model-extra',
                                   'source': 'roles', 'family': 'UNKNOWN',
                                   'provider_reported': None})
        with self.subTest(source='resolver reports none'):
            role = {'model': 'claude-fable-5-2', 'effort': None, 'account': None}
            run = {}
            with mock.patch.object(role_config, 'resolve_role', return_value=role):
                task_submission_cli._agy_developer_profile(agy, model_run=run)
            self.assertEqual((run['requested'], run['source'], run['family']),
                             ('claude-fable-5-2', 'UNKNOWN', 'claude'))

    def test_model_run_takes_the_provider_reported_model_and_its_family(self):
        run = {'requested': None, 'resolved': 'claude-sonnet-5-5-medium', 'source': 'roles',
               'family': 'claude', 'provider_reported': None}
        reported = task_submission_cli._model_run_with_reported(run, 'claude-sonnet-5-5-medium')
        self.assertEqual(reported['provider_reported'], 'claude-sonnet-5-5-medium')
        self.assertEqual(reported['family'], 'claude')
        self.assertIsNone(run['provider_reported'])
        for value in (None, 7, 'has space', '\0'):
            with self.subTest(value=value):
                self.assertEqual(task_submission_cli._model_run_with_reported(run, value), run)

    def test_gw_agent_profile_uses_the_resolver(self):
        calls = self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(route),
                         (route['default_model'], route['default_effort']))
        self.assertEqual(calls, ['gw-agent-developer'])
        self.env = {'LLMTOOL_ROLE_GW_AGENT_DEVELOPER_EFFORT': 'high'}
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(route)[1], 'high')
        self.env = {'LLMTOOL_ROLE_GW_AGENT_DEVELOPER_EFFORT': 'extreme'}
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._gw_agent_developer_profile(route)
        self.assertEqual(caught.exception.role_config_code, 'GW_AGENT_ROLE_PROFILE_INVALID')
        self.env = {'LLMTOOL_ROLE_GW_AGENT_DEVELOPER_MODEL': 'model-extra'}
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(route)[0],
                         'model-extra')
        self.env = {'LLMTOOL_ROLE_GW_AGENT_DEVELOPER_MODEL': 'has space'}
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._gw_agent_developer_profile(route)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_served_model_is_checked_and_returned(self):
        # #1773: the gateway profile model is the one actually served; any
        # well-formed id is admitted (Owner #2302).
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        for served in ('served-unlisted', 'qwen3-coder'):
            with self.subTest(served=served):
                self.assertEqual(task_submission_cli._gw_agent_developer_profile(
                    route, served_model=served), (served, route['default_effort']))

    def test_gw_agent_profile_records_model_run(self):
        # Security v91 F3: the gw-agent run records its model provenance too.
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        run = {'stale': True}
        task_submission_cli._gw_agent_developer_profile(
            route, served_model='qwen3-coder', model_run=run)
        self.assertEqual(run, {'requested': 'qwen3-coder', 'resolved': 'qwen3-coder',
                               'source': 'profile', 'family': 'qwen',
                               'provider_reported': None})
        run = {}
        task_submission_cli._gw_agent_developer_profile(route, model_run=run)
        self.assertEqual((run['requested'], run['resolved'], run['source']),
                         (None, route['default_model'], 'repo_default'))
        self.env = {'LLMTOOL_ROLE_GW_AGENT_DEVELOPER_MODEL': 'deepseek-coder-v4'}
        run = {}
        task_submission_cli._gw_agent_developer_profile(route, model_run=run)
        self.assertEqual((run['requested'], run['source'], run['family']),
                         ('deepseek-coder-v4', 'env', 'deepseek'))

    def test_dispatch_model_run_keeps_valid_provenance_else_records_unknown(self):
        # Security v91 F3: a dispatch always records a run, UNKNOWN when its
        # profile gave no provenance for the launched model.
        valid = developer_routes.build_model_run('gpt-6.1-sol', 'profile', 'gpt-6.1-sol')
        self.assertEqual(task_submission_cli._dispatch_model_run(valid, 'gpt-6.1-sol'), valid)
        for label, run in (('none', None), ('other model', valid),
                           ('family mismatch', dict(valid, family='claude'))):
            with self.subTest(case=label):
                model = 'gpt-6.2-sol' if label == 'other model' else 'gpt-6.1-sol'
                self.assertEqual(task_submission_cli._dispatch_model_run(run, model), {
                    'requested': None, 'resolved': model, 'source': 'UNKNOWN',
                    'family': 'codex', 'provider_reported': None})
        self.assertEqual(task_submission_cli._dispatch_model_run(None, None)['family'],
                         'UNKNOWN')
        self.assertIsNone(task_submission_cli._dispatch_model_run(None, 'has space'))

    def test_gw_agent_malformed_served_model_is_refused(self):
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        for served in ('', 7, 'has space', 'https://example.invalid/m'):
            with self.subTest(served=served):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._gw_agent_developer_profile(
                        route, served_model=served)
                self.assertEqual(caught.exception.role_config_code,
                                 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_valid_role_model_does_not_admit_a_malformed_served_model(self):
        # The role model passing its check says nothing about the gateway.
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        with mock.patch.object(task_submission_cli, '_require_developer_model',
                               wraps=task_submission_cli._require_developer_model) as role_check:
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._gw_agent_developer_profile(
                    route, served_model='has space')
        role_check.assert_called_once()
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_served_model_never_reads_an_override(self):
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        for override in ({'model': 'served-extra', 'ref': REF},
                         {'model': 'served-other', 'ref': REF}):
            with self.subTest(override=override):
                self.assertEqual(task_submission_cli._gw_agent_developer_profile(
                    route, served_model='served-extra', owner_override=override,
                    repository=REPOSITORY, fetch_comment=fetch)[0], 'served-extra')
        fetch.assert_not_called()

    def test_a_role_override_never_admits_a_malformed_served_model(self):
        verified = {'route': 'gw-agent', 'model': 'has space', 'ref': REF,
                    'comment_id': 345}
        route = dict(self.routes()['gw-agent'])
        role = {'model': 'served-extra', 'effort': 'low', 'account': None,
                'owner_override': verified}
        with mock.patch.object(role_config, 'resolve_role', return_value=role):
            self.assertEqual(task_submission_cli._gw_agent_developer_profile(
                route, served_model='served-extra'), ('served-extra', 'low'))
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._gw_agent_developer_profile(
                    route, served_model='has space')
            self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')


class RolesCommandTests(AccountBase):
    def run_roles(self):
        out = io.StringIO()
        status = roles_cli.run_roles(None, environ=self.env, home=self.home, stdout=out)
        return status, out.getvalue().splitlines()

    def test_prints_one_line_per_role_without_paths(self):
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'account': 'work', 'model': REVIEWER_MODEL,
                                          'effort': 'high'}}})
        status, lines = self.run_roles()
        self.assertEqual(status, 0)
        rows = {line.split('\t')[0]: line.split('\t') for line in lines}
        self.assertEqual(sorted(rows), sorted(role_config.KNOWN_ROLES))
        self.assertTrue(all(len(row) == 7 for row in rows.values()))
        self.assertEqual(rows['claude-reviewer'],
                         ['claude-reviewer', 'claude', 'work', REVIEWER_MODEL, 'high', 'roles', 'roles'])
        # #1502 WP-E: an unconfigured review/debug role shows its registry model.
        roles = load_route_config()['roles']
        for role in ('codex-reviewer', 'codex-debug', 'claude-debug'):
            with self.subTest(role=role):
                self.assertEqual(rows[role][3], roles[role]['default_model'])
        agy = load_route_config()['routes']['agy']
        self.assertEqual(rows['agy-developer'],
                         ['agy-developer', 'agy', '-', agy['default_model'],
                          agy['default_effort'], 'repo_default', 'repo_default'])
        self.assertNotIn(str(self.account_dir), '\n'.join(lines))
        self.assertNotIn(str(self.home), '\n'.join(lines))

    def test_agy_developer_model_source_is_roles_env_or_repo_default(self):
        # Owner #2302 r3: the user-config model and the one-shot env override
        # take effect; with neither set the route's repo default applies.
        with self.subTest(source='roles'):
            self.save({'roles': {'agy-developer': {'model': 'gemini-3.8-flash-medium'}}})
            self.env = {}
            status, lines = self.run_roles()
            self.assertEqual(status, 0)
            rows = {line.split('\t')[0]: line.split('\t') for line in lines}
            self.assertEqual(rows['agy-developer'],
                             ['agy-developer', 'agy', '-', 'gemini-3.8-flash-medium',
                              'medium', 'roles', 'roles'])
        with self.subTest(source='env'):
            self.save({})
            self.env = {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'gemini-3.8-flash-medium'}
            status, lines = self.run_roles()
            self.assertEqual(status, 0)
            rows = {line.split('\t')[0]: line.split('\t') for line in lines}
            self.assertEqual(rows['agy-developer'],
                             ['agy-developer', 'agy', '-', 'gemini-3.8-flash-medium',
                              'medium', 'env', 'env'])
        with self.subTest(source='repo_default'):
            self.save({})
            self.env = {}
            status, lines = self.run_roles()
            self.assertEqual(status, 0)
            rows = {line.split('\t')[0]: line.split('\t') for line in lines}
            self.assertEqual(rows['agy-developer'],
                             ['agy-developer', 'agy', '-', 'gemini-3.8-flash-medium',
                              'medium', 'repo_default', 'repo_default'])

    def test_lists_extra_configured_roles_and_reports_refusals(self):
        self.save({'roles': {'pm-codex-binding': {'model': 'm1'},
                             'agy-developer': {'model': 'has space'}}})
        status, lines = self.run_roles()
        self.assertEqual(status, 1)
        rows = {line.split('\t')[0]: line.split('\t') for line in lines}
        self.assertEqual(rows['pm-codex-binding'][3], 'm1')
        self.assertEqual(rows['agy-developer'], ['agy-developer', 'ERROR', 'ROLE_MODEL_NOT_ALLOWED'])

    def test_reports_a_mismatched_legacy_alias_without_paths(self):
        codex_dir = self.root / 'codex-account'
        codex_dir.mkdir(mode=0o700)
        codex_dir.chmod(0o700)
        self.accounts['codex-work'] = {'kind': 'codex', 'config_dir': str(codex_dir)}
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'account': 'codex-work'}}})
        status, lines = self.run_roles()
        self.assertEqual(status, 1)
        rows = {line.split('\t')[0]: line.split('\t') for line in lines}
        self.assertEqual(rows['claude-reviewer'],
                         ['claude-reviewer', 'ERROR', 'ROLE_ALIAS_PROVIDER_MISMATCH'])
        self.assertNotIn('reviewer', rows)
        self.assertNotIn(str(codex_dir), '\n'.join(lines))

    def test_roles_command_uses_the_single_resolver(self):
        calls = self.isolated_resolver()
        status, _lines = self.run_roles()
        self.assertEqual(status, 0)
        self.assertEqual(sorted(calls), sorted(role_config.KNOWN_ROLES))

    def test_cli_registers_the_read_only_roles_command(self):
        self.assertEqual(cli.parser_for_cli().parse_args(['roles']).command, 'roles')
        with mock.patch.object(cli, 'run_roles', return_value=0) as run:
            self.assertEqual(cli.main(['roles']), 0)
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
