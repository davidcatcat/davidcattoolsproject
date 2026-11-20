"""#1502 WP-E: per-route allowed_models in developer-routes.json.

Owner #2302 part 1: agy and claude accept any well-formed model id, of any
family; opencode accepts any well-formed provider/model id. allowed_models is
optional for those routes and is not a membership list. gw-agent keeps its
one-item allowed_models gate. The model family is recorded, not enforced
(model_family).
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import developer_routes
from llmtool.developer_routes import RouteError, check_route_model, load_route_config

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
ROUTE_NAMES = ('agy', 'claude', 'gw-agent', 'opencode')
# Owner #2302 P2: routes gated by provider family and format, not by a list.
FAMILY_ROUTES = ('agy', 'claude', 'opencode')
# Valid, same-family example model names that are not the default, per route.
OTHER_MODEL = {'agy': 'gemini-example-b', 'claude': 'claude-example-b',
               'gw-agent': 'example-model-b', 'opencode': 'opencode/example-model-b-free'}
# Well-formed names of another model family, per CLI route (Owner #2302 part 1:
# admitted). OpenCode ids are provider/model, so a bare name is malformed there.
OTHER_FAMILY = {'agy': ('claude-opus-5-5', 'gpt-6.1-sol', 'example-model-b',
                        'opencode/example-model-b-free', 'gemini-claude-b',
                        'GEMINI-UPPER', 'vendor/gemini-example-b', 'qwen3-coder-next'),
                'claude': ('gemini-3.8-flash-medium', 'gpt-6.1-sol', 'example-model-b',
                           'claude-gpt-6', 'claude-UPPER', 'anthropic/claude-fable-5-2',
                           'deepseek-v4.1-flash')}
# Badly formatted names for each route's format check.
BAD_FORMAT = {'agy': ('https://example.invalid/m', 'has space', '-leading-dash', '',
                      'x' * 129),
              'claude': ('https://example.invalid/m', 'has space', '-leading-dash', '',
                         'x' * 129),
              'gw-agent': ('https://example.invalid/m', 'has space', '-leading-dash'),
              'opencode': ('example-model-b-free', 'https://example.invalid/m',
                           'has space', '-leading-dash/model', 'opencode/example#HIGH',
                           'claude-opus-5-5', 'gemini-3.8-flash-medium')}
# #1502 WP-E: the review and AI Debug role registry in use.
SHIPPED_ROLES = {
    'codex-reviewer': {'allowed_models': ['gpt-6.1-sol']},
    'claude-reviewer': {'allowed_models': ['claude-sonnet-5-5']},
    'codex-debug': {'allowed_models': ['gpt-6.1-sol']},
    'claude-debug': {'allowed_models': ['claude-opus-5-5']},
}


def same_family(route, index):
    """A valid, same-family example model name for route, numbered index."""
    return {'agy': f'gemini-example-{index}', 'claude': f'claude-example-{index}',
            'gw-agent': f'example-model-{index}',
            'opencode': f'opencode/example-{index}-free'}[route]


class AllowedModelsConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load_with(self, route, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data['routes'][route])
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, route, mutate):
        with self.assertRaises(RouteError) as caught:
            self.load_with(route, mutate)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_shipped_file_lists_only_each_default_model(self):
        config = load_route_config()
        self.assertEqual(config['routes']['agy']['allowed_models'],
                         ['gemini-3.8-flash-medium'])
        self.assertEqual(config['routes']['claude']['allowed_models'], ['claude-opus-5-5'])
        # #1502 WP-E: gw-agent lists today's actual served default model.
        self.assertEqual(config['routes']['gw-agent']['allowed_models'], ['gw'])
        self.assertEqual(config['routes']['gw-agent']['default_model'], 'gw')
        self.assertEqual(config['routes']['gw-agent']['profile']['model'], 'gw')
        self.assertEqual(config['routes']['opencode']['allowed_models'],
                         ['opencode/deepseek-v4.1-flash-free'])
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                entry = config['routes'][route]
                self.assertIs(type(entry['allowed_models']), list)
                self.assertEqual(entry['allowed_models'], [entry['default_model']])
        self.assertEqual(self.shipped['schema_version'], 1)

    def test_only_default_model_is_accepted(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                config = self.load_with(route, lambda entry: entry.update(
                    allowed_models=[entry['default_model']]))
                entry = config['routes'][route]
                self.assertEqual(entry['allowed_models'], [entry['default_model']])

    def test_extra_same_family_model_is_accepted_for_family_routes(self):
        # Owner #2302 P2: a well-formed extra model of the route's own family
        # loads; the list is accepted but not enforced.
        for route in FAMILY_ROUTES:
            for order in ('default_first', 'default_last'):
                with self.subTest(route=route, order=order):
                    def mutate(entry, route=route, order=order):
                        extra = [entry['default_model'], OTHER_MODEL[route]]
                        entry['allowed_models'] = (
                            extra if order == 'default_first' else extra[::-1])
                    entry = self.load_with(route, mutate)['routes'][route]
                    self.assertIn(OTHER_MODEL[route], entry['allowed_models'])

    def test_gw_agent_extra_valid_model_is_invalid(self):
        # PR #1759 F1, unchanged by Owner #2302 P2 for gw-agent.
        for order in ('default_first', 'default_last'):
            with self.subTest(order=order):
                def mutate(entry, order=order):
                    extra = [entry['default_model'], OTHER_MODEL['gw-agent']]
                    entry['allowed_models'] = (
                        extra if order == 'default_first' else extra[::-1])
                self.assert_invalid('gw-agent', mutate)

    def test_sixteen_items_load_for_family_routes_only(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['allowed_models'] = [entry['default_model']] + [
                        same_family(route, index) for index in range(15)]
                if route in FAMILY_ROUTES:
                    entry = self.load_with(route, mutate)['routes'][route]
                    self.assertEqual(len(entry['allowed_models']), 16)
                else:
                    self.assert_invalid(route, mutate)

    def test_different_single_model(self):
        # A one-item list that differs from the default: accepted for a family
        # route when it is of the route's family, refused for gw-agent.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['allowed_models'] = [OTHER_MODEL[route]]
                if route in FAMILY_ROUTES:
                    entry = self.load_with(route, mutate)['routes'][route]
                    self.assertEqual(entry['allowed_models'], [OTHER_MODEL[route]])
                else:
                    self.assert_invalid(route, mutate)
        self.assert_invalid('gw-agent', lambda entry: entry.update(
            allowed_models=[entry['default_model'] + '-b']))

    def test_default_model_changed_without_list(self):
        # Family routes: a new same-family default loads with a stale list.
        # gw-agent: the stale list is still refused.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['default_model'] = OTHER_MODEL[route]
                    if route == 'gw-agent':
                        entry['profile']['model'] = OTHER_MODEL[route]
                if route in FAMILY_ROUTES:
                    entry = self.load_with(route, mutate)['routes'][route]
                    self.assertEqual(entry['default_model'], OTHER_MODEL[route])
                else:
                    self.assert_invalid(route, mutate)

    def test_malformed_default_model_is_invalid(self):
        for route in FAMILY_ROUTES:
            for model in BAD_FORMAT[route]:
                with self.subTest(route=route, model=model):
                    self.assert_invalid(route, lambda entry, model=model: entry.update(
                        default_model=model))

    def test_other_family_default_and_listed_models_load_for_cli_routes(self):
        # Owner #2302 part 1: the model family is not part of the format check.
        for route in ('agy', 'claude'):
            for model in OTHER_FAMILY[route]:
                with self.subTest(route=route, model=model):
                    entry = self.load_with(route, lambda entry, model=model: entry.update(
                        default_model=model))['routes'][route]
                    self.assertEqual(entry['default_model'], model)
                    entry = self.load_with(route, lambda entry, model=model: entry.update(
                        allowed_models=[entry['default_model'], model]))['routes'][route]
                    self.assertIn(model, entry['allowed_models'])

    def test_check_route_model_after_load(self):
        config = load_route_config()
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                default = config['routes'][route]['default_model']
                self.assertEqual(check_route_model(config, route, default), default)
                if route in FAMILY_ROUTES:
                    self.assertEqual(check_route_model(config, route, OTHER_MODEL[route]),
                                     OTHER_MODEL[route])
                else:
                    with self.assertRaises(RouteError) as caught:
                        check_route_model(config, route, OTHER_MODEL[route])
                    self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_missing_key(self):
        # Owner #2302 P2: allowed_models is optional for family routes only.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry):
                    entry.pop('allowed_models')
                if route in FAMILY_ROUTES:
                    entry = self.load_with(route, mutate)['routes'][route]
                    self.assertNotIn('allowed_models', entry)
                else:
                    self.assert_invalid(route, mutate)

    def test_empty_list_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.update(allowed_models=[]))

    def test_non_list_is_invalid(self):
        for route in ROUTE_NAMES:
            for value_kind in ('str', 'dict', 'null', 'int'):
                with self.subTest(route=route, value_kind=value_kind):
                    def mutate(entry, value_kind=value_kind):
                        model = entry['default_model']
                        entry['allowed_models'] = {
                            'str': model, 'dict': {model: True},
                            'null': None, 'int': 1}[value_kind]
                    self.assert_invalid(route, mutate)

    def test_duplicate_item_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.update(
                    allowed_models=[entry['default_model'], entry['default_model']]))

    def test_non_str_item_is_invalid(self):
        for route in ROUTE_NAMES:
            for item in (7, None, True, ['nested'], {'k': 'v'}):
                with self.subTest(route=route, item=item):
                    self.assert_invalid(route, lambda entry, item=item: entry.update(
                        allowed_models=[entry['default_model'], item]))

    def test_badly_formatted_item_is_invalid(self):
        for route in ROUTE_NAMES:
            for item in BAD_FORMAT[route]:
                with self.subTest(route=route, item=item):
                    self.assert_invalid(route, lambda entry, item=item: entry.update(
                        allowed_models=[entry['default_model'], item]))

    def test_default_model_not_listed(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['allowed_models'] = [OTHER_MODEL[route]]
                if route in FAMILY_ROUTES:
                    entry = self.load_with(route, mutate)['routes'][route]
                    self.assertNotIn(entry['default_model'], entry['allowed_models'])
                else:
                    self.assert_invalid(route, mutate)

    def test_gw_agent_profile_model_not_listed_is_invalid(self):
        def mutate(entry):
            entry['profile']['model'] = OTHER_MODEL['gw-agent']
            entry['allowed_models'] = [entry['default_model']]
        self.assert_invalid('gw-agent', mutate)

    def test_seventeen_items_are_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['allowed_models'] = [entry['default_model']] + [
                        same_family(route, index) for index in range(16)]
                self.assert_invalid(route, mutate)

    def test_loaded_route_lists_are_copies(self):
        config = load_route_config()
        config['routes']['agy']['allowed_models'].append('gemini-example-b')
        self.assertEqual(load_route_config()['routes']['agy']['allowed_models'],
                         ['gemini-3.8-flash-medium'])


class OpenCodeRouteTests(unittest.TestCase):
    """#1502 WP-E, Owner #289: the OpenCode route accepts free and paid models."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data['routes']['opencode'])
        with self.assertRaises(RouteError) as caught:
            self.load(data)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_shipped_route_matches_adapter_model_and_variant(self):
        from llmtool import opencode_direct_developer

        entry = load_route_config()['routes']['opencode']
        self.assertEqual(entry['executable'], 'opencode')
        self.assertEqual(entry['default_model'], 'opencode/deepseek-v4.1-flash-free')
        self.assertEqual(entry['default_effort'], 'high')
        self.assertEqual(opencode_direct_developer.MODEL,
                         f"{entry['default_model']}#{entry['default_effort']}")
        self.assertTrue(opencode_direct_developer.is_free_model(entry['default_model']))

    def test_malformed_default_model_is_invalid(self):
        for model in ('example-model-b-free', 'has space', '-leading-dash/model',
                      'opencode/-leading-dash', 'opencode/a/b', 'opencode/example#HIGH'):
            with self.subTest(model=model):
                self.assert_invalid(lambda entry, model=model: entry.update(
                    default_model=model, allowed_models=[model]))

    def test_paid_and_variant_models_are_admitted_at_check(self):
        # Owner #289: paid, other provider, and variant models are admitted.
        config = load_route_config()
        for model in ('opencode/example-paid', 'opencode/example-model-b-free#high',
                      'other/example-model-b-free', 'opencode-go/deepseek-v4.1-flash#high'):
            with self.subTest(model=model):
                self.assertEqual(check_route_model(config, 'opencode', model), model)
        with self.assertRaises(RouteError) as caught:
            check_route_model(config, 'opencode', 'example-model-b-free')
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_wrong_fields_are_invalid(self):
        mutations = {
            'executable': lambda entry: entry.update(executable='opencode-other'),
            'effort': lambda entry: entry.update(default_effort='max'),
            'effort type': lambda entry: entry.update(default_effort=3),
            'extra key': lambda entry: entry.update(denied_dirs=[]),
            'missing executable': lambda entry: entry.pop('executable'),
            'missing effort': lambda entry: entry.pop('default_effort'),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.assert_invalid(mutate)

    def test_route_must_be_an_object(self):
        data = copy.deepcopy(self.shipped)
        data['routes']['opencode'] = 'opencode/deepseek-v4.1-flash-free'
        with self.assertRaises(RouteError) as caught:
            self.load(data)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_config_without_opencode_still_loads(self):
        data = copy.deepcopy(self.shipped)
        del data['routes']['opencode']
        self.assertNotIn('opencode', self.load(data)['routes'])


class RoleRegistryTests(unittest.TestCase):
    """#1502 WP-E: allowed models for codex-reviewer, claude-reviewer and AI Debug."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data)
        with self.assertRaises(RouteError) as caught:
            self.load(data)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_shipped_roles_hold_the_models_in_use(self):
        self.assertEqual(self.shipped['roles'], SHIPPED_ROLES)
        self.assertEqual(load_route_config()['roles'], SHIPPED_ROLES)

    def test_roles_are_cross_family_with_their_names(self):
        roles = load_route_config()['roles']
        for role, entry in roles.items():
            with self.subTest(role=role):
                is_claude_model = entry['allowed_models'][0].startswith('claude-')
                self.assertEqual(is_claude_model, role.startswith('claude-'))

    def test_config_without_roles_still_loads(self):
        data = copy.deepcopy(self.shipped)
        del data['roles']
        self.assertNotIn('roles', self.load(data))

    def test_loaded_roles_are_copies(self):
        config = load_route_config()
        config['roles']['codex-reviewer']['allowed_models'].append('example-model-b')
        self.assertEqual(load_route_config()['roles'], SHIPPED_ROLES)

    def test_role_entries_accept_default_model_and_more_family_models(self):
        # Owner #2302 P2: default_model is accepted; allowed_models is optional
        # and may list more models of the role's family.
        entries = {
            'default_model only': {'default_model': 'gpt-example-b'},
            'both, default not listed': {'default_model': 'gpt-example-b',
                                         'allowed_models': ['gpt-6.1-sol']},
            'two family models': {'allowed_models': ['gpt-6.1-sol', 'gpt-example-b']},
            'with timeout': {'default_model': 'gpt-6.1-sol', 'timeout': 600},
        }
        for label, entry in entries.items():
            with self.subTest(case=label):
                data = copy.deepcopy(self.shipped)
                data['roles']['codex-reviewer'] = copy.deepcopy(entry)
                self.assertEqual(self.load(data)['roles']['codex-reviewer'], entry)
        data = copy.deepcopy(self.shipped)
        data['roles']['claude-debug'] = {'default_model': 'claude-fable-5-2'}
        self.assertEqual(self.load(data)['roles']['claude-debug'],
                         {'default_model': 'claude-fable-5-2'})

    def test_role_entries_accept_models_of_any_family(self):
        # Owner #2302 part 1: a role entry may name any well-formed model id.
        for role in ('codex-reviewer', 'claude-reviewer', 'codex-debug', 'claude-debug'):
            for model in ('gpt-example-b', 'claude-fable-5-2', 'gemini-3.8-flash-medium',
                          'Claude-Upper', 'anthropic/claude-opus-5-5', 'example-model-b'):
                with self.subTest(role=role, model=model):
                    data = copy.deepcopy(self.shipped)
                    data['roles'][role] = {'default_model': model, 'allowed_models': [model]}
                    self.assertEqual(self.load(data)['roles'][role],
                                     {'default_model': model, 'allowed_models': [model]})

    def test_registry_default_model_prefers_default_model(self):
        default = developer_routes.registry_default_model
        self.assertEqual(default({'allowed_models': ['gpt-6.1-sol']}), 'gpt-6.1-sol')
        self.assertEqual(default({'default_model': 'gpt-example-b',
                                  'allowed_models': ['gpt-6.1-sol']}), 'gpt-example-b')
        for entry in (None, [], {}, {'allowed_models': []}, {'allowed_models': 'gpt-6.1-sol'},
                      {'allowed_models': [7]}, {'default_model': 7}):
            with self.subTest(entry=entry):
                self.assertIsNone(default(entry))

    def test_invalid_role_registry_is_refused(self):
        def set_models(role, models):
            return lambda data: data['roles'][role].update(allowed_models=models)

        def set_default(role, model):
            return lambda data: data['roles'][role].update(default_model=model)

        mutations = {
            'not an object': lambda data: data.update(roles=['codex-reviewer']),
            'empty': lambda data: data.update(roles={}),
            'missing role': lambda data: data['roles'].pop('claude-debug'),
            'unknown role': lambda data: data['roles'].update(
                {'gw-agent-reviewer': {'allowed_models': ['example-model-b']}}),
            'entry not an object': lambda data: data['roles'].update(
                {'codex-debug': ['gpt-6.1-sol']}),
            'extra entry key': lambda data: data['roles']['codex-debug'].update(
                effort='high'),
            'no model key': lambda data: data['roles']['codex-debug'].pop(
                'allowed_models'),
            'empty list': set_models('codex-reviewer', []),
            'non-list': set_models('codex-reviewer', 'gpt-6.1-sol'),
            'duplicate': set_models('codex-reviewer', ['gpt-6.1-sol', 'gpt-6.1-sol']),
            'non-str': set_models('codex-debug', [7]),
            'bad format': set_models('codex-debug', ['has space']),
            'url': set_models('codex-debug', ['https://example.invalid/m']),
            'two models, one bad format': set_models(
                'codex-reviewer', ['gpt-6.1-sol', 'has space']),
            'claude role bad format': set_models('claude-debug', ['-leading-dash']),
            'default_model bad format': set_default('codex-reviewer', 'has space'),
            'default_model url': set_default('claude-reviewer', 'https://example.invalid/m'),
            'default_model non-str': set_default('codex-reviewer', 7),
            'default_model empty': set_default('codex-reviewer', ''),
            'bad timeout': lambda data: data['roles']['codex-debug'].update(timeout=0),
            'unknown top-level key': lambda data: data.update(models={}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.assert_invalid(mutate)


class RoleModelFormatTests(unittest.TestCase):
    """Owner #2302 part 1: role models are admitted by format, not by family.

    The check runs on the full identifier: it is never rewritten, so prefixed
    and mixed-case forms are admitted exactly as written and a malformed one
    is refused.
    """

    CODEX_ROLES = ('codex-reviewer', 'codex-debug')
    CLAUDE_ROLES = ('claude-reviewer', 'claude-debug')
    # Well-formed identifiers of every family and form: admitted for every role.
    WELL_FORMED = (
        'claude-opus-5-5', 'claude-example-b', 'Claude-opus-5-5', 'CLAUDE-OPUS-5-5',
        'anthropic/claude-opus-5-5', 'bedrock/anthropic.claude-opus-5-5',
        'us.anthropic.claude-opus-5-5', 'vertex_ai/claude-opus-5-5',
        'openrouter:claude-opus-5-5', 'gpt-claude-opus-5-5', 'codex-claude-b',
        'gpt-6.1-sol', 'codex-example-b', 'GPT-6.1-sol', 'openai/gpt-6.1-sol',
        'azure/gpt-6.1-sol', 'claude-gpt-6', 'gemini-3.8-flash-medium',
        'example-model-b', 'opencode/deepseek-v4.1-flash-free', 'gw-agent-default',
        'gpt-', 'claude-', 'gpt', 'gpt_6', 'qwen3-coder-next', 'x' * 128,
    )
    # Malformed: refused for every role.
    MALFORMED = ('gpt-6 sol', ' gpt-6.1-sol', 'gpt-6.1-sol\n', '-gpt-6.1-sol', '',
                 'https://example.invalid/m', 'x' * 129, 'gpt-6.1-sol;x', 'gpt-6.1-sol\x00')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load_role(self, role, model):
        data = copy.deepcopy(self.shipped)
        data['roles'][role] = {'allowed_models': [model]}
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_refused(self, role, model):
        with self.assertRaises(RouteError) as caught:
            self.load_role(role, model)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_every_role_loads_any_well_formed_model_of_any_family(self):
        for role in self.CODEX_ROLES + self.CLAUDE_ROLES:
            for model in self.WELL_FORMED:
                with self.subTest(role=role, model=model):
                    config = self.load_role(role, model)
                    self.assertEqual(config['roles'][role], {'allowed_models': [model]})

    def test_a_malformed_model_is_refused_for_every_role(self):
        for role in self.CODEX_ROLES + self.CLAUDE_ROLES:
            for model in self.MALFORMED:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_role_checks_are_the_one_format_check(self):
        checks = developer_routes._ROLE_MODEL_CHECKS
        self.assertEqual(set(checks), set(self.CODEX_ROLES + self.CLAUDE_ROLES))
        for role, check in checks.items():
            with self.subTest(role=role):
                for model in self.WELL_FORMED:
                    self.assertTrue(check(model), model)
                for model in self.MALFORMED:
                    self.assertFalse(check(model), model)
        for value in (None, 7, True, ['gpt-6.1-sol'], {'claude-opus-5-5': 1}):
            for role, check in checks.items():
                with self.subTest(role=role, value=value):
                    self.assertFalse(check(value))


class ModelFamilyTests(unittest.TestCase):
    """Owner #2302 part 1 (A2): the recorded family comes from the model id."""

    def test_known_families_come_from_the_model_id(self):
        cases = {
            'claude-opus-5-5': 'claude', 'claude-sonnet-5-5-medium': 'claude',
            'Claude-Fable-5-2': 'claude', 'anthropic/claude-fable-5-2': 'claude',
            'bedrock/anthropic.claude-opus-5-5': 'claude',
            'gemini-3.8-flash-medium': 'gemini', 'GEMINI-4-HYDROGEN-MEDIUM': 'gemini',
            'google/gemini-4-hydrogen-medium': 'gemini',
            'gpt-6.1-sol': 'codex', 'gpt-6-luna': 'codex', 'gpt4o': 'codex',
            'codex-example-b': 'codex', 'openai/gpt-6.1-sol': 'codex',
            'o3-mini': 'codex', 'openrouter/o1': 'codex',
            'qwen3-coder-next': 'qwen', 'qwen-max': 'qwen',
            'deepseek-v4.1-flash': 'deepseek',
            'opencode/deepseek-v4.1-flash-free': 'deepseek',
            'opencode/deepseek-v4.1-flash-free#high': 'deepseek',
            'llama3-70b': 'llama', 'mistral-large': 'mistral', 'grok-4': 'grok',
            'kimi-k2': 'kimi', 'glm-4.6': 'glm', 'minimax-m2': 'minimax',
            'gemma-3-27b': 'gemma',
        }
        for model, family in cases.items():
            with self.subTest(model=model):
                self.assertEqual(developer_routes.model_family(model), family)

    def test_unknown_when_no_or_several_families_or_not_text(self):
        for model in ('example-model-b', 'gw', 'opus', 'sonnet', 'model-o3', '',
                      'gemini-claude-b', 'claude-gpt-6', 'gpt-6.1-anthropic',
                      None, 7, True, ['claude-opus-5-5'], b'claude-opus-5-5'):
            with self.subTest(model=model):
                self.assertEqual(developer_routes.model_family(model),
                                 developer_routes.MODEL_FAMILY_UNKNOWN)
        self.assertEqual(developer_routes.MODEL_FAMILY_UNKNOWN, 'UNKNOWN')

    def test_family_is_not_taken_from_a_route_or_role(self):
        # The shipped defaults: the route default model names its own family.
        config = load_route_config()
        self.assertEqual(developer_routes.model_family(config['routes']['agy']['default_model']),
                         'gemini')
        self.assertEqual(developer_routes.model_family(config['routes']['claude']['default_model']),
                         'claude')
        self.assertEqual(developer_routes.model_family(config['roles']['codex-reviewer']
                                                       ['allowed_models'][0]), 'codex')


class CheckRouteModelTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, config, route, model):
        with self.assertRaises(RouteError) as caught:
            check_route_model(config, route, model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_exported(self):
        self.assertIn('check_route_model', developer_routes.__all__)

    def test_route_checks_cover_the_family_routes_only(self):
        self.assertEqual(set(developer_routes._ROUTE_MODEL_CHECKS), set(FAMILY_ROUTES))

    def test_listed_model_is_returned(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                model = self.config['routes'][route]['default_model']
                self.assertEqual(check_route_model(self.config, route, model), model)

    def test_same_family_model_is_accepted_without_a_list_entry(self):
        # Owner #2302 P2: agy accepts gemini-4-hydrogen-medium and claude
        # accepts claude-fable-5-2, with or without allowed_models.
        unlisted = copy.deepcopy(self.config)
        for route in FAMILY_ROUTES:
            del unlisted['routes'][route]['allowed_models']
        for config in (self.config, unlisted):
            for route, model in (('agy', 'gemini-4-hydrogen-medium'),
                                 ('claude', 'claude-fable-5-2'),
                                 ('opencode', OTHER_MODEL['opencode'])):
                with self.subTest(route=route, model=model,
                                  listed='allowed_models' in config['routes'][route]):
                    self.assertEqual(check_route_model(config, route, model), model)

    def test_malformed_model_is_refused(self):
        for route in FAMILY_ROUTES:
            for model in BAD_FORMAT[route]:
                with self.subTest(route=route, model=model):
                    self.assert_refused(self.config, route, model)

    def test_a_model_of_another_family_is_admitted_on_a_cli_route(self):
        # Owner #2302 part 1: agy and claude admit any well-formed model id.
        for route in ('agy', 'claude'):
            for model in OTHER_FAMILY[route]:
                with self.subTest(route=route, model=model):
                    self.assertEqual(check_route_model(self.config, route, model), model)

    def test_allowed_models_is_not_consulted_for_a_family_route(self):
        # Neither a listed nor an unlisted model changes the outcome.
        config = copy.deepcopy(self.config)
        config['routes']['agy']['allowed_models'] = ['gemini-3.8-flash-medium']
        for model in ('claude-opus-5-5', 'gemini-3.8-flash-medium', 'example-model-b'):
            self.assertEqual(check_route_model(config, 'agy', model), model)
        config['routes']['agy']['allowed_models'].append('has space')
        self.assert_refused(config, 'agy', 'has space')

    def test_route_family_check_helpers_are_format_only(self):
        # check_route_model_family and route_has_family_check keep their names
        # but check the format only (Owner #2302 part 1).
        for route in ('agy', 'claude', 'opencode'):
            self.assertTrue(developer_routes.route_has_family_check(route))
        self.assertFalse(developer_routes.route_has_family_check('gw-agent'))
        self.assertEqual(developer_routes.check_route_model_family('agy', 'claude-opus-5-5'),
                         'claude-opus-5-5')
        for route, model in (('agy', 'has space'), ('claude', 'https://example.invalid/m'),
                             ('opencode', 'claude-opus-5-5'), ('gw-agent', 'has space')):
            with self.subTest(route=route, model=model):
                with self.assertRaises(RouteError) as caught:
                    developer_routes.check_route_model_family(route, model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_gw_agent_unlisted_model_is_refused(self):
        # gw-agent is unchanged: list membership still gates its model.
        self.assert_refused(self.config, 'gw-agent', OTHER_MODEL['gw-agent'])
        self.assert_refused(self.config, 'gw-agent', 'claude-opus-5-5')
        without_list = copy.deepcopy(self.config)
        del without_list['routes']['gw-agent']['allowed_models']
        self.assert_refused(without_list, 'gw-agent', 'gw')

    def test_non_str_model_is_refused(self):
        for route in ROUTE_NAMES:
            for model in (None, 7, True, ['claude-opus-5-5'], {'claude-opus-5-5': 1}):
                with self.subTest(route=route, model=model):
                    self.assert_refused(self.config, route, model)

    def test_absent_route_is_refused(self):
        for route in ('opencode', 'codex', '', None, 7):
            with self.subTest(route=route):
                self.assert_refused(self.config, route, 'claude-opus-5-5')
        without_claude = copy.deepcopy(self.config)
        del without_claude['routes']['claude']
        self.assert_refused(without_claude, 'claude', 'claude-opus-5-5')
        for config in ({}, [], None, {'routes': []}, {'routes': {'claude': 'x'}}):
            with self.subTest(config=config):
                self.assert_refused(config, 'claude', 'claude-opus-5-5')


if __name__ == '__main__':
    unittest.main()
