"""#1502 WP-E, #2302: route and role models in developer-routes.json.

Owner #2302 (decision 11-17): developer-routes.json is not a per-model
permit list. Every route and every review/debug role admits any well-formed
model id of any family, so a model switch in user config or a one-shot CLI
needs no repository change. A malformed id is still refused. OpenCode keeps
its free-only format (a budget rule). The gw-agent entry keeps its
one-item allowed_models shape rule at load (PR #1759 F1), but the list is
never consulted when a model is checked.
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import developer_routes
from llmtool.developer_routes import (
    MODEL_FAMILY_UNKNOWN, RouteError, check_registry_role_model, check_route_model,
    load_route_config, model_family, model_id_ok,
)

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
ROUTE_NAMES = ('agy', 'claude', 'gw-agent', 'opencode')
# Routes with no route-specific format: any well-formed model id.
FREE_ROUTES = ('agy', 'claude', 'gw-agent')
# Well-formed model ids of several families; each is admitted by every free route.
ANY_FAMILY = ('gemini-3.8-flash-medium', 'gemini-4-hydrogen-medium',
              'claude-sonnet-5-5-medium', 'claude-fable-5-2', 'gpt-6.1-sol',
              'qwen3-coder', 'deepseek-v4.1', 'example-model-b', 'GEMINI-UPPER',
              'vendor/gemini-example-b', 'anthropic/claude-fable-5-2')
# Malformed model ids, refused everywhere.
MALFORMED = ('https://example.invalid/m', 'has space', '-leading-dash', '',
             'x' * 129, 'line\nbreak', 'model#variant')
REGISTRY_ROLES = ('codex-reviewer', 'claude-reviewer', 'codex-debug', 'claude-debug')
# #1502 WP-E, #2302: the review and AI Debug role registry in use; each role
# names only its default model.
SHIPPED_ROLES = {
    'codex-reviewer': {'default_model': 'gpt-6.1-sol'},
    'claude-reviewer': {'default_model': 'claude-sonnet-5-5'},
    'codex-debug': {'default_model': 'gpt-6.1-sol'},
    'claude-debug': {'default_model': 'claude-opus-5-5'},
}


class ShippedFileTests(unittest.TestCase):
    def test_routes_name_only_their_default_model(self):
        config = load_route_config()
        self.assertEqual(config['routes']['agy']['default_model'], 'gemini-3.8-flash-medium')
        self.assertEqual(config['routes']['claude']['default_model'], 'claude-opus-5-5')
        self.assertEqual(config['routes']['opencode']['default_model'],
                         'opencode/deepseek-v4.1-flash-free')
        for route in ('agy', 'claude', 'opencode'):
            with self.subTest(route=route):
                self.assertNotIn('allowed_models', config['routes'][route])
        # The gw-agent entry keeps its gateway shape: [default_model] only.
        self.assertEqual(config['routes']['gw-agent']['allowed_models'], ['gw'])
        self.assertEqual(config['routes']['gw-agent']['default_model'], 'gw')
        self.assertEqual(config['routes']['gw-agent']['profile']['model'], 'gw')

    def test_roles_name_only_their_default_model(self):
        shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))
        self.assertEqual(shipped['schema_version'], 1)
        self.assertEqual(shipped['roles'], SHIPPED_ROLES)
        self.assertEqual(load_route_config()['roles'], SHIPPED_ROLES)

    def test_every_shipped_default_is_admitted(self):
        config = load_route_config()
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                default = config['routes'][route]['default_model']
                self.assertEqual(check_route_model(config, route, default), default)
        for role, entry in SHIPPED_ROLES.items():
            with self.subTest(role=role):
                self.assertEqual(check_registry_role_model(config, role, entry['default_model']),
                                 entry['default_model'])


class RouteLoadTests(unittest.TestCase):
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

    def test_any_family_default_model_loads_for_agy_and_claude(self):
        for route in ('agy', 'claude'):
            for model in ANY_FAMILY:
                with self.subTest(route=route, model=model):
                    entry = self.load_with(route, lambda entry, model=model: entry.update(
                        default_model=model))['routes'][route]
                    self.assertEqual(entry['default_model'], model)

    def test_malformed_default_model_is_invalid(self):
        for route in ('agy', 'claude', 'opencode'):
            for model in MALFORMED + (7, None):
                with self.subTest(route=route, model=model):
                    self.assert_invalid(route, lambda entry, model=model: entry.update(
                        default_model=model))

    def test_older_allowed_models_list_still_loads_and_is_copied(self):
        # An older file may still carry allowed_models; it is well formed but
        # not consulted, and it need not hold the default model.
        for route in ('agy', 'claude'):
            with self.subTest(route=route):
                entry = self.load_with(route, lambda entry: entry.update(
                    allowed_models=['qwen3-coder', 'gpt-6.1-sol']))['routes'][route]
                self.assertEqual(entry['allowed_models'], ['qwen3-coder', 'gpt-6.1-sol'])
        data = copy.deepcopy(self.shipped)
        data['routes']['agy']['allowed_models'] = ['gemini-3.8-flash-medium']
        self.path.write_text(json.dumps(data), encoding='utf-8')
        config = load_route_config(self.path)
        config['routes']['agy']['allowed_models'].append('gemini-example-b')
        self.assertEqual(load_route_config(self.path)['routes']['agy']['allowed_models'],
                         ['gemini-3.8-flash-medium'])

    def test_badly_shaped_allowed_models_is_invalid(self):
        for route in ROUTE_NAMES:
            cases = {
                'empty': [],
                'not a list': 'gw',
                'null': None,
                'duplicate': ['gw', 'gw'],
                'non-str item': ['gw', 7],
                'malformed item': ['gw', 'has space'],
                'seventeen items': ['gw'] + [f'model-{index}' for index in range(16)],
            }
            for label, value in cases.items():
                with self.subTest(route=route, case=label):
                    self.assert_invalid(route, lambda entry, value=value: entry.update(
                        allowed_models=value))

    def test_gw_agent_keeps_its_one_item_shape_rule(self):
        # PR #1759 F1: a shape rule of the gateway entry, not a model gate.
        def extra(entry):
            entry['allowed_models'] = [entry['default_model'], 'example-model-b']

        def different(entry):
            entry['allowed_models'] = ['example-model-b']

        def stale(entry):
            entry['default_model'] = 'example-model-b'
            entry['profile']['model'] = 'example-model-b'

        def missing(entry):
            entry.pop('allowed_models')

        def profile_mismatch(entry):
            entry['profile']['model'] = 'example-model-b'

        for mutate in (extra, different, stale, missing, profile_mismatch):
            with self.subTest(case=mutate.__name__):
                self.assert_invalid('gw-agent', mutate)

    def test_opencode_stays_free_only(self):
        for model in ('opencode/example-paid', 'opencode/example-model-b-free#high',
                      'other/example-model-b-free', 'example-model-b-free',
                      'claude-opus-5-5', 'gemini-3.8-flash-medium'):
            with self.subTest(model=model):
                self.assert_invalid('opencode', lambda entry, model=model: entry.update(
                    default_model=model))
        entry = self.load_with('opencode', lambda entry: entry.update(
            default_model='opencode/example-model-b-free'))['routes']['opencode']
        self.assertEqual(entry['default_model'], 'opencode/example-model-b-free')


class OpenCodeRouteTests(unittest.TestCase):
    """#1502 WP-E: the OpenCode route uses only free models."""

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

    def test_paid_or_variant_model_is_refused_at_check(self):
        config = load_route_config()
        for model in ('opencode/example-paid', 'opencode/example-model-b-free#high',
                      'other/example-model-b-free', 'example-model-b-free'):
            with self.subTest(model=model):
                with self.assertRaises(RouteError) as caught:
                    check_route_model(config, 'opencode', model)
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
    """#1502 WP-E, #2302: the review and AI Debug role registry."""

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

    def test_config_without_roles_still_loads(self):
        data = copy.deepcopy(self.shipped)
        del data['roles']
        self.assertNotIn('roles', self.load(data))

    def test_loaded_roles_are_copies(self):
        data = copy.deepcopy(self.shipped)
        data['roles']['codex-reviewer'] = {'allowed_models': ['gpt-6.1-sol']}
        config = self.load(data)
        config['roles']['codex-reviewer']['allowed_models'].append('example-model-b')
        self.assertEqual(self.load(data)['roles']['codex-reviewer'],
                         {'allowed_models': ['gpt-6.1-sol']})

    def test_role_entries_accept_a_model_of_any_family(self):
        # Owner #2302: no role is limited to its own family.
        entries = {
            'default_model only': {'default_model': 'gpt-example-b'},
            'another family default': {'default_model': 'claude-sonnet-5-5'},
            'gemini default': {'default_model': 'gemini-3.8-flash-medium'},
            'both, default not listed': {'default_model': 'gpt-example-b',
                                         'allowed_models': ['gpt-6.1-sol']},
            'older list of two families': {'allowed_models': ['gpt-6.1-sol',
                                                              'claude-opus-5-5']},
            'with timeout': {'default_model': 'gpt-6.1-sol', 'timeout': 600},
        }
        for role in REGISTRY_ROLES:
            for label, entry in entries.items():
                with self.subTest(role=role, case=label):
                    data = copy.deepcopy(self.shipped)
                    data['roles'][role] = copy.deepcopy(entry)
                    self.assertEqual(self.load(data)['roles'][role], entry)

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
                {'gw-agent-reviewer': {'default_model': 'example-model-b'}}),
            'entry not an object': lambda data: data['roles'].update(
                {'codex-debug': ['gpt-6.1-sol']}),
            'extra entry key': lambda data: data['roles']['codex-debug'].update(
                effort='high'),
            'no model key': lambda data: data['roles']['codex-debug'].pop('default_model'),
            'empty list': set_models('codex-reviewer', []),
            'non-list': set_models('codex-reviewer', 'gpt-6.1-sol'),
            'duplicate': set_models('codex-reviewer', ['gpt-6.1-sol', 'gpt-6.1-sol']),
            'non-str': set_models('codex-debug', [7]),
            'bad format': set_models('codex-debug', ['has space']),
            'url': set_models('codex-debug', ['https://example.invalid/m']),
            'default_model bad format': set_default('codex-reviewer', '-gpt-6.1-sol'),
            'default_model url': set_default('claude-reviewer', 'https://example.invalid/m'),
            'default_model non-str': set_default('codex-reviewer', 7),
            'default_model empty': set_default('codex-reviewer', ''),
            'bad timeout': lambda data: data['roles']['codex-debug'].update(timeout=0),
            'unknown top-level key': lambda data: data.update(models={}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.assert_invalid(mutate)

    def test_role_checks_are_format_only(self):
        checks = developer_routes._ROLE_MODEL_CHECKS
        self.assertEqual(set(checks), set(REGISTRY_ROLES))
        for role, check in checks.items():
            for model in ANY_FAMILY:
                with self.subTest(role=role, model=model):
                    self.assertTrue(check(model))
            for value in MALFORMED + (None, 7, True, ['gpt-6.1-sol'], {'claude-opus-5-5': 1}):
                with self.subTest(role=role, value=value):
                    self.assertFalse(check(value))


class CheckRegistryRoleModelTests(unittest.TestCase):
    """#2302 A1: any well-formed model id for every review and debug role."""

    def setUp(self):
        self.config = load_route_config()

    def test_any_family_model_is_admitted_for_every_role(self):
        for role in REGISTRY_ROLES:
            for model in ANY_FAMILY:
                with self.subTest(role=role, model=model):
                    self.assertEqual(check_registry_role_model(self.config, role, model), model)

    def test_malformed_model_or_unknown_role_is_refused(self):
        cases = [(role, model) for role in REGISTRY_ROLES
                 for model in MALFORMED + (None, 7)]
        cases += [('reviewer', 'claude-sonnet-5-5'), ('codex-developer', 'gpt-6.1-sol'),
                  (None, 'gpt-6.1-sol')]
        for role, model in cases:
            with self.subTest(role=role, model=model):
                with self.assertRaises(RouteError) as caught:
                    check_registry_role_model(self.config, role, model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_config_without_a_valid_registry_admits_nothing(self):
        without_roles = copy.deepcopy(self.config)
        del without_roles['roles']
        for config in (without_roles, {}, {'roles': {}}):
            with self.subTest(config=config):
                with self.assertRaises(RouteError) as caught:
                    check_registry_role_model(config, 'codex-reviewer', 'gpt-6.1-sol')
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')


class CheckRouteModelTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, config, route, model):
        with self.assertRaises(RouteError) as caught:
            check_route_model(config, route, model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_exported(self):
        for name in ('check_route_model', 'model_id_ok', 'model_family',
                     'MODEL_FAMILY_UNKNOWN'):
            with self.subTest(name=name):
                self.assertIn(name, developer_routes.__all__)

    def test_only_opencode_has_a_route_specific_format(self):
        self.assertEqual(set(developer_routes._ROUTE_MODEL_CHECKS), {'opencode'})
        for route in FREE_ROUTES:
            with self.subTest(route=route):
                self.assertFalse(developer_routes.route_has_family_check(route))
        self.assertTrue(developer_routes.route_has_family_check('opencode'))

    def test_any_family_model_is_admitted_without_a_repository_change(self):
        # #2302 A1: agy-developer may run claude-sonnet-5-5-medium (the
        # refused case in the #2302 evidence) and switch back to Gemini.
        for route in FREE_ROUTES:
            for model in ANY_FAMILY:
                with self.subTest(route=route, model=model):
                    self.assertEqual(check_route_model(self.config, route, model), model)
                    self.assertEqual(
                        developer_routes.check_route_model_family(route, model), model)

    def test_a_listed_model_list_is_never_consulted(self):
        listed = copy.deepcopy(self.config)
        listed['routes']['agy']['allowed_models'] = ['gemini-3.8-flash-medium']
        listed['routes']['gw-agent']['allowed_models'] = ['gw']
        for route, model in (('agy', 'claude-sonnet-5-5-medium'), ('gw-agent', 'qwen3-coder')):
            with self.subTest(route=route):
                self.assertEqual(check_route_model(listed, route, model), model)
        without_list = copy.deepcopy(self.config)
        del without_list['routes']['gw-agent']['allowed_models']
        self.assertEqual(check_route_model(without_list, 'gw-agent', 'gw'), 'gw')

    def test_malformed_model_is_refused(self):
        for route in ROUTE_NAMES:
            for model in MALFORMED:
                with self.subTest(route=route, model=model):
                    self.assert_refused(self.config, route, model)
                    with self.assertRaises(RouteError):
                        developer_routes.check_route_model_family(route, model)

    def test_non_str_model_is_refused(self):
        for route in ROUTE_NAMES:
            for model in (None, 7, True, ['claude-opus-5-5'], {'claude-opus-5-5': 1}):
                with self.subTest(route=route, model=model):
                    self.assert_refused(self.config, route, model)

    def test_absent_route_is_refused(self):
        for route in ('codex', '', None, 7):
            with self.subTest(route=route):
                self.assert_refused(self.config, route, 'claude-opus-5-5')
        without_claude = copy.deepcopy(self.config)
        del without_claude['routes']['claude']
        self.assert_refused(without_claude, 'claude', 'claude-opus-5-5')
        for config in ({}, [], None, {'routes': []}, {'routes': {'claude': 'x'}}):
            with self.subTest(config=config):
                self.assert_refused(config, 'claude', 'claude-opus-5-5')


class ModelIdAndFamilyTests(unittest.TestCase):
    """#2302 A2: the model family recorded for each run comes from the model id."""

    def test_model_id_ok(self):
        for model in ANY_FAMILY + ('gw', 'opencode/deepseek-v4.1-flash-free', 'x' * 128):
            with self.subTest(model=model):
                self.assertTrue(model_id_ok(model))
        for value in MALFORMED + (None, 7, b'gemini-3.8-flash-medium'):
            with self.subTest(value=value):
                self.assertFalse(model_id_ok(value))

    def test_family_from_model_id(self):
        cases = {
            'claude-opus-5-5': 'claude', 'claude-sonnet-5-5-medium': 'claude',
            'anthropic/claude-fable-5-2': 'claude', 'Claude-Opus-5-5': 'claude',
            'sonnet': 'claude', 'opus': 'claude',
            'gemini-3.8-flash-medium': 'gemini', 'google/gemini-4-hydrogen-medium': 'gemini',
            'gpt-6.1-sol': 'codex', 'codex-example-b': 'codex', 'o3-mini': 'codex',
            'openai/gpt-oss-120b': 'codex', 'gpt-oss-120b': 'codex',
            'qwen3-coder': 'qwen', 'deepseek-v4.1': 'deepseek',
            'opencode/deepseek-v4.1-flash-free': 'deepseek',
            'opencode/deepseek-v4.1-flash-free#high': 'deepseek',
            'llama-4-scout': 'llama', 'mistral-large': 'mistral',
        }
        for model, family in cases.items():
            with self.subTest(model=model):
                self.assertEqual(model_family(model), family)

    def test_unknown_or_ambiguous_family_is_unknown(self):
        self.assertEqual(MODEL_FAMILY_UNKNOWN, 'UNKNOWN')
        for model in ('gw', 'example-model-b', 'claudette-1', 'gemma-3',
                      'claude-gpt-6', 'gpt-claude-6', 'gemini-claude-b',
                      'openai/claude-opus-5-5', 'anthropic/gpt-6.1-sol',
                      'qwen-deepseek-merge') + MALFORMED[:-1] + (None, 7, 'x' * 257):
            with self.subTest(model=model):
                self.assertEqual(model_family(model), MODEL_FAMILY_UNKNOWN)


if __name__ == '__main__':
    unittest.main()
