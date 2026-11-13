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
ROUTE = {'default_model': 'model-default', 'default_effort': 'medium',
         'allowed_models': ['model-default', 'model-user', 'model-env', 'model-cli']}


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
                         ('model-default', 'medium', 'repo_default', 'repo_default'))
        self.assertEqual(resolved['effort_source'], 'repo_default')

    def test_repo_default_comes_from_developer_routes_json(self):
        routes = load_route_config()['routes']
        for role, route in sorted(role_config.ROUTE_ROLES.items()):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual(resolved['model'], routes[route]['default_model'])
                self.assertEqual(resolved['effort'], routes[route]['default_effort'])

    def test_user_config_wins_over_repo_default(self):
        self.save({'roles': {'agy-developer': {'model': 'model-user', 'effort': 'low'}}})
        resolved = self.resolve('agy-developer', route_config=ROUTE)
        self.assertEqual((resolved['model'], resolved['effort'], resolved['source'],
                          resolved['model_source']),
                         ('model-user', 'low', 'roles', 'roles'))
        self.assertEqual(resolved['effort_source'], 'roles')

    def test_env_wins_over_user_config_with_either_prefix_spelling(self):
        self.save({'roles': {'agy-developer': {'model': 'model-user'}}})
        for prefix in ('LLMTOOL_ROLE_AGY-DEVELOPER_', 'LLMTOOL_ROLE_AGY_DEVELOPER_'):
            with self.subTest(prefix=prefix):
                self.env = {prefix + 'MODEL': 'model-env'}
                resolved = self.resolve('agy-developer', route_config=ROUTE)
                self.assertEqual((resolved['model'], resolved['source'],
                                  resolved['model_source']), ('model-env', 'env', 'env'))

    def test_conflicting_env_spellings_are_refused(self):
        self.env = {'LLMTOOL_ROLE_AGY-DEVELOPER_MODEL': 'model-env',
                    'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'model-user'}
        with self.assertRaises(LLMToolError) as caught:
            self.resolve('agy-developer', route_config=ROUTE)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_CONFIG_OVERRIDE_INVALID')

    def test_cli_wins_over_env(self):
        self.save({'roles': {'agy-developer': {'model': 'model-user'}}})
        self.env = {'LLMTOOL_ROLE_AGY_DEVELOPER_MODEL': 'model-env'}
        resolved = self.resolve('agy-developer', cli={'model': 'model-cli'}, route_config=ROUTE)
        self.assertEqual((resolved['model'], resolved['source'], resolved['model_source']),
                         ('model-cli', 'cli', 'cli'))

    def test_non_developer_roles_keep_their_previous_shape(self):
        self.assertEqual(self.resolve('coder'), {
            'role': 'coder', 'provider': None, 'provider_profile': None, 'account': None,
            'config_dir': None, 'model': None, 'effort': None, 'source': 'builtin'})


class AllowedModelTests(ResolverBase):
    def test_model_outside_allowed_models_is_refused_at_every_layer(self):
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

    def test_real_route_refuses_a_model_it_does_not_list(self):
        self.save({'roles': {'claude-developer': {'model': 'claude-not-listed-0'}}})
        with self.assertRaises(LLMToolError) as caught:
            self.resolve('claude-developer')
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_allowed_model_never_reads_the_override_ref(self):
        fetch = mock.Mock(side_effect=AssertionError('must not read'))
        resolved = self.resolve('agy-developer', route_config=ROUTE,
                                owner_override={'model': 'model-default', 'ref': REF},
                                repository=REPOSITORY, fetch_comment=fetch)
        fetch.assert_not_called()
        self.assertNotIn('owner_override', resolved)

    def test_verified_owner_override_naming_route_and_model_is_accepted(self):
        self.save({'roles': {'agy-developer': {'model': 'model-extra'}}})
        fetch = mock.Mock(return_value=comment())
        resolved = self.resolve('agy-developer', route_config=ROUTE,
                                owner_override={'model': 'model-extra', 'ref': REF},
                                repository=REPOSITORY, fetch_comment=fetch)
        fetch.assert_called_once_with(345)
        self.assertEqual(resolved['model'], 'model-extra')
        self.assertEqual(resolved['owner_override'],
                         {'route': 'agy', 'model': 'model-extra', 'ref': REF,
                          'comment_id': 345})

    def test_bad_owner_overrides_are_refused(self):
        def unreadable(_comment_id):
            raise OSError('offline')

        cases = (
            ('OWNER_OVERRIDE_MISMATCH', {'model': 'model-extra', 'ref': REF},
             mock.Mock(return_value=comment(model='model-other'))),
            ('OWNER_OVERRIDE_MISMATCH', {'model': 'model-extra', 'ref': REF},
             mock.Mock(return_value=comment(route='claude'))),
            ('OWNER_OVERRIDE_MISMATCH', {'model': 'model-other', 'ref': REF},
             mock.Mock(return_value=comment(model='model-other'))),
            ('OWNER_OVERRIDE_MARKER_MISSING', {'model': 'model-extra', 'ref': REF},
             mock.Mock(return_value=comment(marker=False))),
            ('OWNER_OVERRIDE_REF_UNREADABLE', {'model': 'model-extra', 'ref': REF},
             unreadable),
            ('OWNER_OVERRIDE_REF_UNREADABLE', {'model': 'model-extra', 'ref': REF}, None),
            ('OWNER_OVERRIDE_REF_INVALID',
             {'model': 'model-extra', 'ref': 'https://example.invalid/issues/1'},
             mock.Mock(return_value=comment())),
        )
        self.save({'roles': {'agy-developer': {'model': 'model-extra'}}})
        for code, override, fetch in cases:
            with self.subTest(code=code, override=override):
                with self.assertRaises(LLMToolError) as caught:
                    self.resolve('agy-developer', route_config=ROUTE,
                                 owner_override=override, repository=REPOSITORY,
                                 fetch_comment=fetch)
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
                self.assertEqual(caught.exception.owner_override_code, code)


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
                   'roles': {'reviewer': {'account': 'work', 'model': 'sonnet'}}})
        for role in ('claude-reviewer', 'reviewer'):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual((resolved['role'], resolved['account'], resolved['model'],
                                  resolved['source']), (role, 'work', 'sonnet', 'roles'))

    def test_exact_role_entry_wins_over_its_alias(self):
        self.save({'accounts': self.accounts,
                   'roles': {'reviewer': {'model': 'legacy-model'},
                             'claude-reviewer': {'model': 'new-model'}}})
        self.assertEqual(self.resolve('claude-reviewer')['model'], 'new-model')
        self.assertEqual(self.resolve('reviewer')['model'], 'legacy-model')

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


class DispatchPathTests(ResolverBase):
    def routes(self):
        return load_route_config()['routes']

    def test_agy_dispatch_uses_the_resolver_and_repo_default(self):
        calls = self.isolated_resolver()
        route = dict(self.routes()['agy'])
        model, _effort = task_submission_cli._agy_developer_profile(route)
        self.assertEqual(model, route['default_model'])
        self.assertEqual(calls, ['agy-developer'])

    def test_agy_dispatch_refuses_a_model_outside_allowed_models(self):
        self.isolated_resolver()
        self.save({'roles': {'agy-developer': {'model': 'model-extra'}}})
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._agy_developer_profile(dict(self.routes()['agy']))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_claude_dispatch_refuses_a_model_outside_allowed_models(self):
        calls = self.isolated_resolver()
        self.save({'roles': {'claude-developer': {'model': 'claude-not-listed-0'}}})
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._claude_developer_profile(dict(self.routes()['claude']))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertEqual(calls, ['claude-developer'])

    def test_dispatch_checks_again_when_a_resolver_returns_an_unlisted_model(self):
        role = {'model': 'model-extra', 'effort': None, 'account': None}
        with mock.patch.object(role_config, 'resolve_role', return_value=role):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._agy_developer_profile(dict(self.routes()['agy']))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_dispatch_check_accepts_only_a_matching_verified_override(self):
        route = dict(ROUTE, allowed_models=['model-default'])
        verified = {'route': 'agy', 'model': 'model-extra', 'ref': REF, 'comment_id': 345}
        self.assertIsNone(task_submission_cli._require_developer_model(
            'agy-developer', 'agy', route, 'model-extra', {'owner_override': verified}))
        for other in (dict(verified, route='claude'), dict(verified, model='model-other'), None):
            with self.subTest(override=other):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._require_developer_model(
                        'agy-developer', 'agy', route, 'model-extra', {'owner_override': other})
                self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_dispatch_passes_an_override_through_to_the_resolver(self):
        self.isolated_resolver()
        self.save({'roles': {'agy-developer': {'model': 'model-extra'}}})
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._agy_developer_profile(
                dict(self.routes()['agy']),
                owner_override={'model': 'model-extra', 'ref': REF}, repository=REPOSITORY,
                fetch_comment=mock.Mock(return_value=comment(marker=False)))
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertIn('OWNER_OVERRIDE_MARKER_MISSING', str(caught.exception))

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
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        route['allowed_models'] = [route['default_model'], 'served-listed']
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(
            route, served_model='served-listed'), ('served-listed', route['default_effort']))

    def test_gw_agent_unlisted_served_model_is_refused(self):
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        for served in ('served-unlisted', '', 7):
            with self.subTest(served=served):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._gw_agent_developer_profile(
                        route, served_model=served)
                self.assertEqual(caught.exception.role_config_code,
                                 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_listed_role_model_does_not_admit_an_unlisted_served_model(self):
        # The role model passing allowed_models says nothing about the gateway.
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        with mock.patch.object(task_submission_cli, '_require_developer_model',
                               wraps=task_submission_cli._require_developer_model) as role_check:
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._gw_agent_developer_profile(
                    route, served_model='served-unlisted')
        role_check.assert_called_once()
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_served_model_accepts_only_a_matching_verified_override(self):
        self.isolated_resolver()
        route = dict(self.routes()['gw-agent'])
        override = {'model': 'served-extra', 'ref': REF}
        fetch = mock.Mock(return_value=comment(route='gw-agent', model='served-extra'))
        self.assertEqual(task_submission_cli._gw_agent_developer_profile(
            route, served_model='served-extra', owner_override=override,
            repository=REPOSITORY, fetch_comment=fetch)[0], 'served-extra')
        fetch.assert_called_once_with(345)
        cases = (
            ('OWNER_OVERRIDE_MISMATCH', 'served-extra',
             mock.Mock(return_value=comment(route='agy', model='served-extra'))),
            ('OWNER_OVERRIDE_MISMATCH', 'served-extra',
             mock.Mock(return_value=comment(route='gw-agent', model='served-other'))),
            ('OWNER_OVERRIDE_MISMATCH', 'served-other',
             mock.Mock(return_value=comment(route='gw-agent', model='served-extra'))),
            ('OWNER_OVERRIDE_MARKER_MISSING', 'served-extra',
             mock.Mock(return_value=comment(route='gw-agent', model='served-extra',
                                            marker=False))),
            ('OWNER_OVERRIDE_REF_UNREADABLE', 'served-extra', None),
        )
        for code, served, fake in cases:
            with self.subTest(code=code, served=served):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._gw_agent_developer_profile(
                        route, served_model=served, owner_override=override,
                        repository=REPOSITORY, fetch_comment=fake)
                self.assertEqual(caught.exception.role_config_code,
                                 'ROLE_MODEL_NOT_ALLOWED')
                self.assertIn(code, str(caught.exception))

    def test_gw_agent_served_model_reuses_the_resolver_verified_override(self):
        verified = {'route': 'gw-agent', 'model': 'served-extra', 'ref': REF,
                    'comment_id': 345}
        route = dict(self.routes()['gw-agent'])
        role = {'model': 'served-extra', 'effort': 'low', 'account': None,
                'owner_override': verified}
        with mock.patch.object(role_config, 'resolve_role', return_value=role):
            self.assertEqual(task_submission_cli._gw_agent_developer_profile(
                route, served_model='served-extra'), ('served-extra', 'low'))
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
                   'roles': {'reviewer': {'account': 'work', 'model': 'sonnet',
                                          'effort': 'high'}}})
        status, lines = self.run_roles()
        self.assertEqual(status, 0)
        rows = {line.split('\t')[0]: line.split('\t') for line in lines}
        self.assertEqual(sorted(rows), sorted(role_config.KNOWN_ROLES))
        self.assertTrue(all(len(row) == 6 for row in rows.values()))
        self.assertEqual(rows['claude-reviewer'],
                         ['claude-reviewer', 'claude', 'work', 'sonnet', 'high', 'roles'])
        agy = load_route_config()['routes']['agy']
        self.assertEqual(rows['agy-developer'],
                         ['agy-developer', 'agy', '-', agy['default_model'],
                          agy['default_effort'], 'repo_default'])
        self.assertNotIn(str(self.account_dir), '\n'.join(lines))
        self.assertNotIn(str(self.home), '\n'.join(lines))

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
