"""#1502 WP-E: role_config.resolve_role as the single role resolver.

GitHub reads are injected fakes; nothing here opens a network connection.
"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from llmtool import cli, role_config, roles_cli, task_submission_cli
from llmtool.config import user_config_path
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError
from llmtool.owner_override import _MARKER

REPOSITORY = 'example-owner/example-repo'
REF = 'https://github.com/' + REPOSITORY + '/issues/12#issuecomment-345'
ISSUE_URL = 'https://api.github.com/repos/' + REPOSITORY + '/issues/12'
# An agy route fixture. #2302 part 1: agy admits any well-formed model id of a
# known family, so the precedence layers use Gemini names; 'model-extra' names
# no model family and is refused on every path, including the owner-override
# path.
ROUTE = {'default_model': 'gemini-default', 'default_effort': 'medium',
         'allowed_models': ['gemini-default']}
# A gw-agent route fixture. #2302 part 1: gw-agent admits any well-formed id of
# a known family (or its routing alias 'gw'); allowed_models is not a gate.
GW_ROUTE = {'default_model': 'gw', 'default_effort': 'low',
            'allowed_models': ['gw']}
# The claude-reviewer model listed in developer-routes.json (#1502 WP-E).
REVIEWER_MODEL = 'claude-sonnet-5-5'


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
    def test_unknown_family_model_is_refused_at_every_layer(self):
        cases = (
            ({'roles': {'agy-developer': {'model': 'model-extra'}}}, {}, None),
            ({}, {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'model-extra'}, None),
            ({}, {}, {'model': 'model-extra'}),
        )
        for config, env, cli_values in cases:
            with self.subTest(config=config, env=env, cli=cli_values):
                self.save(config)
                self.env = env
                with self.assertRaises(LLMToolError) as caught:
                    self.resolve('agy-developer', cli=cli_values, route_config=ROUTE)
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
                self.assertIsNone(caught.exception.owner_override_code)

    # #2302 part 1 (A1): every Developer role takes any well-formed model id of a
    # known family, from user config, the environment or a one-shot CLI, with
    # no repository change and no owner override.
    ANY_MODEL = (
        ('agy-developer', 'gemini-4-hydrogen-medium'),
        ('agy-developer', 'claude-sonnet-5-5-medium'),
        ('agy-developer', 'gpt-6.1-sol'),
        ('claude-developer', 'claude-fable-5-2'),
        ('claude-developer', 'gemini-3.8-flash-medium'),
        ('claude-developer', 'gpt-6.1-sol'),
        ('gw-agent-developer', 'qwen3.8-27b'),
        ('gw-agent-developer', 'claude-opus-5-5'),
    )

    def test_real_routes_accept_any_model_from_user_config(self):
        for role, model in self.ANY_MODEL:
            with self.subTest(role=role, model=model):
                self.save({'roles': {role: {'model': model}}})
                resolved = self.resolve(role)
                self.assertEqual((resolved['model'], resolved['model_source'],
                                  resolved['source']), (model, 'roles', 'roles'))
                self.assertNotIn('owner_override', resolved)

    def test_real_routes_accept_any_model_from_env_and_cli(self):
        for role, model in self.ANY_MODEL:
            env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
            with self.subTest(role=role, model=model, layer='env'):
                self.env = {env_name: model}
                resolved = self.resolve(role)
                self.assertEqual((resolved['model'], resolved['model_source']),
                                 (model, 'env'))
            with self.subTest(role=role, model=model, layer='cli'):
                self.env = {}
                resolved = self.resolve(role, cli={'model': model})
                self.assertEqual((resolved['model'], resolved['model_source']),
                                 (model, 'cli'))

    def test_agy_developer_switches_to_a_claude_model_and_back_by_config_only(self):
        # A4: only the user config changes; the repository files are not touched.
        for model in ('claude-sonnet-5-5-medium', 'gemini-3.8-flash-medium'):
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                resolved = self.resolve('agy-developer')
                self.assertEqual((resolved['model'], resolved['model_source']),
                                 (model, 'roles'))
        self.save({})
        resolved = self.resolve('agy-developer')
        self.assertEqual((resolved['model'], resolved['model_source']),
                         (load_route_config()['routes']['agy']['default_model'],
                          'repo_default'))

    def test_real_routes_refuse_an_unknown_family_or_malformed_model(self):
        cases = (
            ('agy-developer', 'example-model-b'),
            ('agy-developer', 'claude-gpt-6'),
            ('agy-developer', 'https://example.invalid/gemini-4'),
            ('agy-developer', 'gemini..4'),
            ('claude-developer', 'example-model-b'),
            ('claude-developer', 'claude-gpt-6'),
            ('claude-developer', 'claude..b'),
            ('gw-agent-developer', 'example-model-b'),
            ('gw-agent-developer', 'has space'),
        )
        for role, model in cases:
            with self.subTest(role=role, model=model):
                self.save({'roles': {role: {'model': model}}})
                with self.assertRaises(LLMToolError) as caught:
                    self.resolve(role)
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
                self.assertIsNone(caught.exception.owner_override_code)

    def test_allowed_model_never_reads_the_override_ref(self):
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        for model in ('gemini-default', 'gemini-4-hydrogen-medium'):
            with self.subTest(model=model):
                resolved = self.resolve('agy-developer', route_config=ROUTE,
                                        cli={'model': model},
                                        owner_override={'model': model, 'ref': REF},
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

    def test_an_existing_verified_owner_override_record_is_still_accepted(self):
        # #2302 part 1: the override is optional; an already verified record
        # that names this route and model is kept, with no GitHub read.
        self.save({'roles': {'gw-agent-developer': {'model': 'qwen3.8-27b'}}})
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        verified = {'route': 'gw-agent', 'model': 'qwen3.8-27b', 'ref': REF,
                    'comment_id': 345}
        resolved = self.resolve('gw-agent-developer', route_config=GW_ROUTE,
                                owner_override=verified,
                                repository=REPOSITORY, fetch_comment=fetch)
        fetch.assert_not_called()
        self.assertEqual(resolved['model'], 'qwen3.8-27b')
        self.assertEqual(resolved['owner_override'], verified)

    def test_an_owner_override_never_refuses_or_admits_a_model(self):
        # The model is admitted without an override, so any override record,
        # whatever it holds, leaves the result as it is: no refusal, no record
        # for a mismatch, and its ref is never read.
        self.save({'roles': {'gw-agent-developer': {'model': 'qwen3.8-27b'}}})
        cases = (
            {'model': 'qwen3.8-27b', 'ref': REF},
            {'model': 'qwen3.8-other', 'ref': REF},
            {'model': 'qwen3.8-27b', 'ref': 'https://example.invalid/issues/1'},
            {'route': 'claude', 'model': 'qwen3.8-27b', 'ref': REF, 'comment_id': 345},
            {'route': 'gw-agent', 'model': 'qwen3.8-other', 'ref': REF, 'comment_id': 345},
            'not-a-mapping',
        )
        for override in cases:
            with self.subTest(override=override):
                fetch = mock.Mock(side_effect=AssertionError('must not read'))
                resolved = self.resolve('gw-agent-developer', route_config=GW_ROUTE,
                                        owner_override=override, repository=REPOSITORY,
                                        fetch_comment=fetch)
                fetch.assert_not_called()
                self.assertEqual(resolved['model'], 'qwen3.8-27b')
                self.assertNotIn('owner_override', resolved)

    def test_owner_override_never_bypasses_the_family_or_format_check(self):
        # Repair 2: a matching, verifiable override cannot admit a malformed
        # model or one of unknown family; the check refuses before any read.
        claude_route = {'default_model': 'claude-default',
                        'default_effort': 'high',
                        'allowed_models': ['claude-default']}
        cases = (
            ('agy-developer', ROUTE, 'agy', 'example-model-b'),
            ('agy-developer', ROUTE, 'agy', 'claude-gpt-6'),
            ('claude-developer', claude_route, 'claude', 'example-model-b'),
            ('claude-developer', claude_route, 'claude', 'claude..b'),
            ('gw-agent-developer', GW_ROUTE, 'gw-agent', 'example-model-b'),
        )
        for role, route, name, model in cases:
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
        # Repair 2: gw-agent keeps its membership gate, but a malformed
        # identifier is refused before any owner override is read.
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

    def test_agy_dispatch_refuses_an_unknown_family_model(self):
        self.isolated_resolver()
        for model in ('model-extra', 'claude-gpt-6'):
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._agy_developer_profile(dict(self.routes()['agy']))
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_agy_dispatch_accepts_any_model_from_user_config(self):
        # #2302 part 1: gemini-4-hydrogen-medium and a Claude id are admitted
        # with no allowed_models entry and no owner override.
        calls = self.isolated_resolver()
        for model in ('gemini-4-hydrogen-medium', 'claude-sonnet-5-5-medium'):
            with self.subTest(model=model):
                self.save({'roles': {'agy-developer': {'model': model}}})
                profile = task_submission_cli._agy_developer_profile(
                    dict(self.routes()['agy']))
                resolved, _effort = profile
                self.assertEqual(resolved, model)
                self.assertEqual((profile.requested_model, profile.model_source),
                                 (model, 'roles'))
        self.assertEqual(calls, ['agy-developer'] * 2)

    def test_dispatch_profiles_carry_the_model_source(self):
        # A2: the profile says where the model came from (repo default, user
        # config or environment); it still equals the plain tuple.
        self.isolated_resolver()
        route = dict(self.routes()['agy'])
        profile = task_submission_cli._agy_developer_profile(route)
        self.assertEqual(profile, (route['default_model'], route['default_effort']))
        self.assertEqual((profile.requested_model, profile.model_source),
                         (route['default_model'], 'repo_default'))
        self.env = {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'gemini-3.8-flash-medium'}
        profile = task_submission_cli._agy_developer_profile(route)
        self.assertEqual(profile[0], 'gemini-3.8-flash-medium')
        self.assertEqual(profile.model_source, 'env')
        self.env = {}
        gw_route = dict(self.routes()['gw-agent'])
        profile = task_submission_cli._gw_agent_developer_profile(
            gw_route, served_model='qwen3.8-27b')
        self.assertEqual(profile[0], 'qwen3.8-27b')
        self.assertEqual((profile.requested_model, profile.model_source),
                         (gw_route['default_model'], 'repo_default'))

    def test_a_plain_profile_tuple_records_an_unknown_source(self):
        record = task_submission_cli._model_provenance(('gemini-test', 'high'), 'gemini-test')
        self.assertEqual(record, {
            'requested_model': 'gemini-test', 'resolved_model': 'gemini-test',
            'model_source': 'UNKNOWN', 'model_family': 'gemini', 'provider_model': None})

    def test_claude_dispatch_accepts_any_model_from_user_config(self):
        # #2302 part 1: claude-fable-5-2 and another vendor's id are admitted.
        calls = self.isolated_resolver()
        self.save({'accounts': self.accounts,
                   'roles': {'claude-developer': {'account': 'work',
                                                  'model': 'claude-fable-5-2'}}})
        profile = task_submission_cli._claude_developer_profile(dict(self.routes()['claude']))
        if str(profile[0]) == str(self.account_dir):
            account_dir, model, _effort = profile
        else:
            model, _effort, account_dir = profile
        self.assertEqual(model, 'claude-fable-5-2')
        self.assertEqual(str(account_dir), str(self.account_dir))
        self.assertEqual(calls, ['claude-developer'])

    def test_claude_dispatch_accepts_another_vendors_model(self):
        calls = self.isolated_resolver()
        self.save({'accounts': self.accounts,
                   'roles': {'claude-developer': {'account': 'work',
                                                  'model': 'gemini-4-hydrogen-medium'}}})
        profile = task_submission_cli._claude_developer_profile(dict(self.routes()['claude']))
        self.assertEqual(profile[0], 'gemini-4-hydrogen-medium')
        self.assertEqual((profile.requested_model, profile.model_source),
                         ('gemini-4-hydrogen-medium', 'roles'))
        self.assertEqual(calls, ['claude-developer'])

    def test_claude_dispatch_refuses_an_unknown_family_model(self):
        calls = self.isolated_resolver()
        self.save({'accounts': self.accounts,
                   'roles': {'claude-developer': {'account': 'work',
                                                  'model': 'example-model-b'}}})
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._claude_developer_profile(dict(self.routes()['claude']))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertEqual(calls, ['claude-developer'])

    def test_dispatch_checks_again_when_a_resolver_returns_an_unknown_family_model(self):
        role = {'model': 'model-extra', 'effort': None, 'account': None}
        with mock.patch.object(role_config, 'resolve_role', return_value=role):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._agy_developer_profile(dict(self.routes()['agy']))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_dispatch_check_needs_no_override_and_an_override_admits_nothing(self):
        # #2302 part 1: a known-family model is admitted at dispatch with no
        # override; a verified override cannot admit an unknown-family model.
        route = dict(GW_ROUTE)
        verified = {'route': 'gw-agent', 'model': 'model-extra', 'ref': REF,
                    'comment_id': 345}
        for override in (None, verified):
            with self.subTest(override=override):
                self.assertIsNone(task_submission_cli._require_developer_model(
                    'gw-agent-developer', 'gw-agent', route, 'qwen3.8-27b',
                    {'owner_override': override}))
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._require_developer_model(
                        'gw-agent-developer', 'gw-agent', route, 'model-extra',
                        {'owner_override': override})
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_dispatch_refuses_an_unknown_family_model_before_reading_an_override(self):
        # Repair 2: the production dispatch path cannot admit such a model
        # through a matching override; the ref is never fetched.
        self.isolated_resolver()
        self.save({'roles': {'agy-developer': {'model': 'claude-gpt-6'}}})
        fetch = mock.Mock(return_value=comment(route='agy', model='claude-gpt-6'))
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._agy_developer_profile(
                dict(self.routes()['agy']),
                owner_override={'model': 'claude-gpt-6', 'ref': REF},
                repository=REPOSITORY, fetch_comment=fetch)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertIsNone(getattr(caught.exception, 'owner_override_code', None))
        fetch.assert_not_called()

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
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._gw_agent_developer_profile(route)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_served_model_is_checked_and_returned(self):
        # #1773 (CWE-863): the gateway profile model is the one actually served.
        # #2302 part 1: any well-formed id of a known family is admitted; it
        # need not be listed in allowed_models.
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(
            route, served_model='qwen3.8-27b'), ('qwen3.8-27b', route['default_effort']))
        # The routing alias is also a valid profile model (the shipped value).
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(
            route, served_model='gw'), ('gw', route['default_effort']))

    def test_gw_agent_unknown_family_served_model_is_refused(self):
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        for served in ('served-unlisted', 'authorized-model', '', 7):
            with self.subTest(served=served):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._gw_agent_developer_profile(
                        route, served_model=served)
                self.assertEqual(caught.exception.role_config_code,
                                 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_role_model_passing_the_check_does_not_admit_the_served_model(self):
        # The role model passing says nothing about the gateway: the served model
        # is checked on its own.
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        with mock.patch.object(task_submission_cli, '_require_developer_model',
                               wraps=task_submission_cli._require_developer_model) as role_check:
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._gw_agent_developer_profile(
                    route, served_model='served-unlisted')
        role_check.assert_called_once()
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_served_model_needs_no_override_and_an_override_admits_nothing(self):
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        override = {'model': 'served-extra', 'ref': REF}
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        # An unknown-family model is refused even with an override record.
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._gw_agent_developer_profile(
                route, served_model='served-extra', owner_override=override,
                repository=REPOSITORY, fetch_comment=fetch)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        # A known-family model passes with an override that is never read.
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(
            route, served_model='deepseek-v4.1-flash', owner_override=override,
            repository=REPOSITORY, fetch_comment=fetch)[0], 'deepseek-v4.1-flash')
        fetch.assert_not_called()

    def test_gw_agent_served_model_is_checked_whatever_the_role_record_holds(self):
        verified = {'route': 'gw-agent', 'model': 'qwen3.8-27b', 'ref': REF,
                    'comment_id': 345}
        route = dict(self.routes()['gw-agent'])
        role = {'model': 'qwen3.8-27b', 'effort': 'low', 'account': None,
                'owner_override': verified}
        with mock.patch.object(role_config, 'resolve_role', return_value=role):
            self.assertEqual(task_submission_cli._gw_agent_developer_profile(
                route, served_model='qwen3.8-27b'), ('qwen3.8-27b', 'low'))
            for other in ('served-other', route['default_model'] + '-x'):
                with self.subTest(served=other):
                    with self.assertRaises(LLMToolError) as caught:
                        task_submission_cli._gw_agent_developer_profile(
                            route, served_model=other)
                    self.assertEqual(caught.exception.role_config_code,
                                     'ROLE_MODEL_NOT_ALLOWED')


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
                self.assertEqual(rows[role][3], roles[role]['allowed_models'][0])
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
            self.save({'roles': {'agy-developer': {'model': 'gemini-4-hydrogen-medium'}}})
            self.env = {}
            status, lines = self.run_roles()
            self.assertEqual(status, 0)
            rows = {line.split('\t')[0]: line.split('\t') for line in lines}
            self.assertEqual(rows['agy-developer'],
                             ['agy-developer', 'agy', '-', 'gemini-4-hydrogen-medium',
                              'medium', 'roles', 'roles'])
        with self.subTest(source='env'):
            self.save({})
            self.env = {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'gemini-4-hydrogen-medium'}
            status, lines = self.run_roles()
            self.assertEqual(status, 0)
            rows = {line.split('\t')[0]: line.split('\t') for line in lines}
            self.assertEqual(rows['agy-developer'],
                             ['agy-developer', 'agy', '-', 'gemini-4-hydrogen-medium',
                              'medium', 'env', 'env'])
        with self.subTest(source='repo_default'):
            self.save({})
            self.env = {}
            status, lines = self.run_roles()
            self.assertEqual(status, 0)
            rows = {line.split('\t')[0]: line.split('\t') for line in lines}
            self.assertEqual(rows['agy-developer'],
                             ['agy-developer', 'agy', '-', 'gemini-4-hydrogen-medium',
                              'medium', 'repo_default', 'repo_default'])

    def test_lists_extra_configured_roles_and_reports_refusals(self):
        self.save({'roles': {'pm-codex-binding': {'model': 'm1'},
                             'agy-developer': {'model': 'model-extra'}}})
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
