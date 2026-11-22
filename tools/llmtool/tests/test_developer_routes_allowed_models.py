"""#1502 WP-E: per-route allowed_models in developer-routes.json.

#2302 part 1 (Owner B): every Developer route (agy, claude, gw-agent, opencode)
admits any well-formed model id with no repository change; allowed_models is
not a membership list on any route. agy, claude and gw-agent also need a model
family that can be read from the id. The gw-agent route file still holds its
one-item allowed_models list (a load rule, not an admission gate).
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
# #2302 part 1: routes with no membership list (allowed_models is optional on
# them). gw-agent also admits any model now, but its route file keeps the
# one-item allowed_models load rule, so it is listed apart.
FAMILY_ROUTES = ('agy', 'claude', 'opencode')
# Valid example model names that are not the default, per route.
OTHER_MODEL = {'agy': 'gemini-example-b', 'claude': 'claude-example-b',
               'gw-agent': 'qwen3-example-b', 'opencode': 'opencode/example-model-b-free'}
# Another vendor's well-formed model ids, now admitted by agy and claude.
OTHER_VENDOR = {'agy': ('claude-opus-5-5', 'gpt-6.1-sol', 'claude-sonnet-5-5-medium',
                        'qwen3.8-27b', 'deepseek-v4.1-flash'),
                'claude': ('gemini-3.8-flash-medium', 'gpt-6.1-sol', 'qwen3.8-27b',
                           'deepseek-v4.1-flash')}
# Well-formed names whose family cannot be read from the id, per route (A1b).
WRONG_FAMILY = {'agy': ('example-model-b', 'opencode/example-model-b-free',
                        'gemini-claude-b'),
                'claude': ('example-model-b', 'claude-gpt-6', 'claude-openai-b'),
                'opencode': ('claude-opus-5-5', 'gemini-3.8-flash-medium')}
# Badly formatted names for each route's format check.
BAD_FORMAT = {'agy': ('https://example.invalid/m', 'has space', '-leading-dash',
                      'gemini..b', '', 'Gemini-example-b'),
              'claude': ('https://example.invalid/m', 'has space', '-leading-dash',
                         'claude..b', '', 'Claude-example-b'),
              'gw-agent': ('https://example.invalid/m', 'has space', '-leading-dash',
                           'Qwen3-example-b'),
              'opencode': ('example-model-b-free', 'https://example.invalid/m',
                           'has space', '-leading-dash/model', 'opencode/example#HIGH')}
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
            'gw-agent': f'qwen3-example-{index}',
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
                         ['gemini-4-hydrogen-medium'])
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
        # Case changes are badly formatted, not another model of the family.
        self.assert_invalid('agy', lambda entry: entry.update(
            allowed_models=[entry['default_model'].upper()]))
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

    def test_wrong_family_or_malformed_default_model_is_invalid(self):
        for route in FAMILY_ROUTES:
            for model in WRONG_FAMILY[route] + BAD_FORMAT[route]:
                with self.subTest(route=route, model=model):
                    self.assert_invalid(route, lambda entry, model=model: entry.update(
                        default_model=model))

    def test_wrong_family_listed_model_is_invalid(self):
        for route in FAMILY_ROUTES:
            for model in WRONG_FAMILY[route]:
                with self.subTest(route=route, model=model):
                    self.assert_invalid(route, lambda entry, model=model: entry.update(
                        allowed_models=[entry['default_model'], model]))

    def test_check_route_model_after_load(self):
        config = load_route_config()
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                default = config['routes'][route]['default_model']
                self.assertEqual(check_route_model(config, route, default), default)
                # #2302 part 1: an unlisted model is admitted on every route.
                self.assertEqual(check_route_model(config, route, OTHER_MODEL[route]),
                                 OTHER_MODEL[route])

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
                         ['gemini-4-hydrogen-medium'])


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
            'two models, one of another family': set_models(
                'codex-reviewer', ['gpt-6.1-sol', 'example-model-b']),
            'duplicate': set_models('codex-reviewer', ['gpt-6.1-sol', 'gpt-6.1-sol']),
            'non-str': set_models('codex-debug', [7]),
            'bad format': set_models('codex-debug', ['has space']),
            'url': set_models('codex-debug', ['https://example.invalid/m']),
            'codex role with claude model': set_models('codex-reviewer', ['claude-opus-5-5']),
            'codex role with claude model upper': set_models('codex-debug', ['Claude-opus-5-5']),
            'claude role with codex model': set_models('claude-reviewer', ['gpt-6.1-sol']),
            'claude role bad format': set_models('claude-debug', ['claude-UPPER']),
            'default_model of another family': set_default('claude-reviewer', 'gpt-6.1-sol'),
            'default_model bad format': set_default('codex-reviewer', 'GPT-6.1-sol'),
            'default_model non-str': set_default('codex-reviewer', 7),
            'default_model empty': set_default('codex-reviewer', ''),
            'bad timeout': lambda data: data['roles']['codex-debug'].update(timeout=0),
            'unknown top-level key': lambda data: data.update(models={}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.assert_invalid(mutate)


class RoleModelFamilyAllowlistTests(unittest.TestCase):
    """Security repair (PR #1782 CWE-693): role families are an allowlist.

    The check runs on the full identifier: provider prefixes are never
    stripped, so prefixed and mixed-case forms of another family (and of the
    role's own family) are refused.
    """

    CODEX_ROLES = ('codex-reviewer', 'codex-debug')
    CLAUDE_ROLES = ('claude-reviewer', 'claude-debug')
    # Claude-family identifiers in prefixed, dotted and mixed-case forms.
    CLAUDE_FORMS = (
        'claude-opus-5-5', 'claude-example-b', 'Claude-opus-5-5', 'CLAUDE-OPUS-5-5',
        'cLaUdE-opus-5-5', 'anthropic/claude-opus-5-5', 'Anthropic/Claude-Opus-5-5',
        'ANTHROPIC/CLAUDE-OPUS-5-5', 'bedrock/anthropic.claude-opus-5-5',
        'us.anthropic.claude-opus-5-5', 'vertex_ai/claude-opus-5-5',
        'openrouter:claude-opus-5-5', 'openai/claude-opus-5-5', 'x/y/claude-opus-5-5',
        'gpt-claude-opus-5-5', 'codex-claude-b', 'gpt-6.1-anthropic',
    )
    # Codex/GPT-family identifiers in prefixed, dotted and mixed-case forms.
    CODEX_FORMS = (
        'gpt-6.1-sol', 'codex-example-b', 'GPT-6.1-sol', 'Gpt-6.1-Sol', 'CODEX-example-b',
        'openai/gpt-6.1-sol', 'OpenAI/GPT-6.1-SOL', 'azure/gpt-6.1-sol',
        'openrouter:gpt-6.1-sol', 'anthropic/gpt-6.1-sol', 'claude-gpt-6',
        'claude-codex-b', 'claude-openai-b',
    )
    # Neither family: refused for every role.
    OTHER_FORMS = ('gemini-3.8-flash-medium', 'example-model-b',
                   'opencode/deepseek-v4.1-flash-free', 'gw-agent-default',
                   'gpt-', 'codex-', 'claude-', 'gpt', 'gpt_6', 'gpt-6 sol')
    # Prefixed or mixed-case forms of a role's own family are also refused
    # (the prefix is part of the full identifier).
    CODEX_OWN_FAMILY_REFUSED = ('GPT-6.1-sol', 'Gpt-6.1-Sol', 'CODEX-example-b',
                                'openai/gpt-6.1-sol', 'OpenAI/GPT-6.1-SOL',
                                'azure/gpt-6.1-sol', 'openrouter:gpt-6.1-sol')
    CLAUDE_OWN_FAMILY_REFUSED = ('Claude-opus-5-5', 'CLAUDE-OPUS-5-5', 'cLaUdE-opus-5-5',
                                 'anthropic/claude-opus-5-5',
                                 'Anthropic/Claude-Opus-5-5',
                                 'us.anthropic.claude-opus-5-5',
                                 'vertex_ai/claude-opus-5-5')

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

    def test_codex_roles_accept_only_lowercase_unprefixed_codex_family(self):
        for role in self.CODEX_ROLES:
            for model in ('gpt-6.1-sol', 'codex-example-b', 'gpt-example-b'):
                with self.subTest(role=role, model=model):
                    config = self.load_role(role, model)
                    self.assertEqual(config['roles'][role], {'allowed_models': [model]})

    def test_claude_roles_accept_only_lowercase_unprefixed_claude_family(self):
        for role in self.CLAUDE_ROLES:
            for model in ('claude-opus-5-5', 'claude-example-b'):
                with self.subTest(role=role, model=model):
                    config = self.load_role(role, model)
                    self.assertEqual(config['roles'][role], {'allowed_models': [model]})

    def test_claude_family_in_any_form_is_refused_for_codex_roles(self):
        for role in self.CODEX_ROLES:
            for model in self.CLAUDE_FORMS:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_codex_family_in_any_form_is_refused_for_claude_roles(self):
        for role in self.CLAUDE_ROLES:
            for model in self.CODEX_FORMS:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_other_families_are_refused_for_every_role(self):
        # The former denylist accepted any well-formed non-'claude' prefix.
        for role in self.CODEX_ROLES + self.CLAUDE_ROLES:
            for model in self.OTHER_FORMS:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_prefixed_or_mixed_case_own_family_is_refused(self):
        for role in self.CODEX_ROLES:
            for model in self.CODEX_OWN_FAMILY_REFUSED:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)
        for role in self.CLAUDE_ROLES:
            for model in self.CLAUDE_OWN_FAMILY_REFUSED:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_role_checks_are_family_allowlists(self):
        checks = developer_routes._ROLE_MODEL_CHECKS
        self.assertEqual(set(checks), set(self.CODEX_ROLES + self.CLAUDE_ROLES))
        for role in self.CODEX_ROLES:
            with self.subTest(role=role):
                self.assertTrue(checks[role]('gpt-6.1-sol'))
                for model in self.CLAUDE_FORMS + self.OTHER_FORMS:
                    self.assertFalse(checks[role](model), model)
        for role in self.CLAUDE_ROLES:
            with self.subTest(role=role):
                self.assertTrue(checks[role]('claude-opus-5-5'))
                for model in self.CODEX_FORMS + self.OTHER_FORMS:
                    self.assertFalse(checks[role](model), model)
        for value in (None, 7, True, ['gpt-6.1-sol'], {'claude-opus-5-5': 1}):
            for role, check in checks.items():
                with self.subTest(role=role, value=value):
                    self.assertFalse(check(value))


class CheckRouteModelTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, config, route, model):
        with self.assertRaises(RouteError) as caught:
            check_route_model(config, route, model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_exported(self):
        self.assertIn('check_route_model', developer_routes.__all__)

    def test_route_checks_cover_every_developer_route(self):
        self.assertEqual(set(developer_routes._ROUTE_MODEL_CHECKS), set(ROUTE_NAMES))
        for route in ROUTE_NAMES:
            self.assertTrue(developer_routes.route_has_family_check(route))

    def test_listed_model_is_returned(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                model = self.config['routes'][route]['default_model']
                self.assertEqual(check_route_model(self.config, route, model), model)

    def test_any_model_is_accepted_without_a_list_entry(self):
        # #2302 part 1: a model of any known family is admitted on every
        # route, with or without allowed_models (no repository change).
        unlisted = copy.deepcopy(self.config)
        for route in ROUTE_NAMES:
            unlisted['routes'][route].pop('allowed_models', None)
        for config in (self.config, unlisted):
            for route, model in (('agy', 'gemini-4-hydrogen-medium'),
                                 ('claude', 'claude-fable-5-2'),
                                 ('gw-agent', OTHER_MODEL['gw-agent']),
                                 ('opencode', OTHER_MODEL['opencode'])):
                with self.subTest(route=route, model=model,
                                  listed='allowed_models' in config['routes'][route]):
                    self.assertEqual(check_route_model(config, route, model), model)
            for route in ('agy', 'claude'):
                for model in OTHER_VENDOR[route]:
                    with self.subTest(route=route, vendor_model=model,
                                      listed='allowed_models' in config['routes'][route]):
                        self.assertEqual(check_route_model(config, route, model), model)

    def test_routing_alias_is_admitted_for_gw_agent_only(self):
        self.assertEqual(check_route_model(self.config, 'gw-agent', 'gw'), 'gw')
        for route in ('agy', 'claude'):
            with self.subTest(route=route):
                self.assert_refused(self.config, route, 'gw')

    def test_undetermined_family_or_malformed_model_is_refused(self):
        for route in FAMILY_ROUTES:
            for model in WRONG_FAMILY[route] + BAD_FORMAT[route]:
                with self.subTest(route=route, model=model):
                    self.assert_refused(self.config, route, model)
        for model in BAD_FORMAT['gw-agent'] + ('example-model-b', 'authorized-model'):
            with self.subTest(route='gw-agent', model=model):
                self.assert_refused(self.config, 'gw-agent', model)
        for route in ROUTE_NAMES:
            self.assert_refused(self.config, route, '')

    def test_a_listed_model_of_unknown_family_is_still_refused(self):
        # allowed_models is not consulted on any route.
        config = copy.deepcopy(self.config)
        config['routes']['agy']['allowed_models'].append('example-model-b')
        self.assert_refused(config, 'agy', 'example-model-b')
        self.assertEqual(check_route_model(config, 'agy', 'claude-opus-5-5'),
                         'claude-opus-5-5')

    def test_gw_agent_unlisted_model_is_admitted(self):
        # #2302 part 1: list membership no longer gates a gw-agent model.
        self.assertEqual(check_route_model(self.config, 'gw-agent', OTHER_MODEL['gw-agent']),
                         OTHER_MODEL['gw-agent'])
        self.assertEqual(check_route_model(self.config, 'gw-agent', 'claude-opus-5-5'),
                         'claude-opus-5-5')
        without_list = copy.deepcopy(self.config)
        del without_list['routes']['gw-agent']['allowed_models']
        self.assertEqual(check_route_model(without_list, 'gw-agent', 'gw'), 'gw')
        self.assertEqual(check_route_model(without_list, 'gw-agent', 'qwen3.8-27b'),
                         'qwen3.8-27b')

    def test_model_family_is_read_from_the_id(self):
        family = developer_routes.model_family
        unknown = developer_routes.MODEL_FAMILY_UNKNOWN
        for model, expected in (
                ('claude-opus-5-5', 'claude'), ('Claude-Sonnet-5-5#high', 'claude'),
                ('anthropic/claude-opus-5-5', 'claude'),
                ('gemini-3.8-flash-medium', 'gemini'), ('gemini-4-hydrogen-medium', 'gemini'),
                ('gpt-6.1-sol', 'codex'), ('openai/gpt-6-luna', 'codex'),
                ('codex-mini', 'codex'), ('o4-mini', 'codex'), ('o3', 'codex'),
                ('qwen3.8-27b', 'qwen'), ('qwen3:8b', 'qwen'),
                ('opencode/deepseek-v4.1-flash-free#high', 'deepseek'),
                ('kimi-k2', 'kimi'), ('glm-4.6', 'glm'),
                # Other vendors' markers make the family unknown (fail closed).
                ('claude-gpt-6', unknown), ('gemini-claude-b', unknown),
                ('openai/deepseek-x', unknown), ('qwen-anthropic-b', unknown),
                ('claude-gemini-b', unknown),
                # No known family in the id.
                ('example-model-b', unknown), ('gw', unknown), ('opus-5', unknown),
                ('opencode/example-paid', unknown), ('', unknown),
                (None, unknown), (7, unknown), (['claude-opus-5-5'], unknown)):
            with self.subTest(model=model):
                self.assertEqual(family(model), expected)

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
