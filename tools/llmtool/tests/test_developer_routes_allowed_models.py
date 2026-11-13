"""#1502 WP-E: per-route allowed_models in developer-routes.json."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import developer_routes
from llmtool.developer_routes import RouteError, check_route_model, load_route_config

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
ROUTE_NAMES = ('agy', 'claude', 'gw-agent')
# Valid but unlisted example model names, per route format.
OTHER_MODEL = {'agy': 'example-model-b', 'claude': 'claude-example-b',
               'gw-agent': 'example-model-b'}
# Badly formatted names for each route's format check.
BAD_FORMAT = {'agy': ('https://example.invalid/m', 'has space', '-leading-dash'),
              'claude': ('example-model-b', 'claude-UPPER', 'claude-'),
              'gw-agent': ('https://example.invalid/m', 'has space', '-leading-dash')}


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
        self.assertEqual(config['routes']['gw-agent']['allowed_models'], ['gw-agent-default'])
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

    def test_extra_valid_model_is_invalid(self):
        # PR #1759 F1: a well-formed extra model next to the default is refused.
        for route in ROUTE_NAMES:
            for order in ('default_first', 'default_last'):
                with self.subTest(route=route, order=order):
                    def mutate(entry, route=route, order=order):
                        extra = [entry['default_model'], OTHER_MODEL[route]]
                        entry['allowed_models'] = (
                            extra if order == 'default_first' else extra[::-1])
                    self.assert_invalid(route, mutate)

    def test_sixteen_items_are_invalid(self):
        # The former 16-item maximum no longer admits extra models.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                prefix = 'claude-example-' if route == 'claude' else 'example-model-'

                def mutate(entry, prefix=prefix):
                    entry['allowed_models'] = [entry['default_model']] + [
                        f'{prefix}{index}' for index in range(15)]
                self.assert_invalid(route, mutate)

    def test_different_single_model_is_invalid(self):
        # A one-item list that differs from the default, even only by case.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry, route=route: entry.update(
                    allowed_models=[OTHER_MODEL[route]]))
        self.assert_invalid('agy', lambda entry: entry.update(
            allowed_models=[entry['default_model'].upper()]))
        self.assert_invalid('gw-agent', lambda entry: entry.update(
            allowed_models=[entry['default_model'] + '-b']))

    def test_default_model_changed_without_list_is_invalid(self):
        # Changing default_model alone leaves allowed_models stale: refused.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['default_model'] = OTHER_MODEL[route]
                    if route == 'gw-agent':
                        entry['profile']['model'] = OTHER_MODEL[route]
                self.assert_invalid(route, mutate)

    def test_check_route_model_allows_only_default_after_load(self):
        config = load_route_config()
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                default = config['routes'][route]['default_model']
                self.assertEqual(check_route_model(config, route, default), default)
                with self.assertRaises(RouteError) as caught:
                    check_route_model(config, route, OTHER_MODEL[route])
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_missing_key_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.pop('allowed_models'))

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

    def test_default_model_not_listed_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry, route=route: entry.update(
                    allowed_models=[OTHER_MODEL[route]]))

    def test_gw_agent_profile_model_not_listed_is_invalid(self):
        def mutate(entry):
            entry['profile']['model'] = OTHER_MODEL['gw-agent']
            entry['allowed_models'] = [entry['default_model']]
        self.assert_invalid('gw-agent', mutate)

    def test_seventeen_items_are_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                prefix = 'claude-example-' if route == 'claude' else 'example-model-'

                def mutate(entry, prefix=prefix):
                    entry['allowed_models'] = [entry['default_model']] + [
                        f'{prefix}{index}' for index in range(16)]
                self.assert_invalid(route, mutate)


class CheckRouteModelTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, config, route, model):
        with self.assertRaises(RouteError) as caught:
            check_route_model(config, route, model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_exported(self):
        self.assertIn('check_route_model', developer_routes.__all__)

    def test_listed_model_is_returned(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                model = self.config['routes'][route]['default_model']
                self.assertEqual(check_route_model(self.config, route, model), model)

    def test_unlisted_model_is_refused(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_refused(self.config, route, OTHER_MODEL[route])
        self.assert_refused(self.config, 'agy', 'claude-opus-5-5')
        self.assert_refused(self.config, 'claude', 'gemini-3.8-flash-medium')
        self.assert_refused(self.config, 'claude', 'CLAUDE-OPUS-5-5')
        self.assert_refused(self.config, 'claude', '')

    def test_non_str_model_is_refused(self):
        for model in (None, 7, True, ['claude-opus-5-5'], {'claude-opus-5-5': 1}):
            with self.subTest(model=model):
                self.assert_refused(self.config, 'claude', model)

    def test_absent_route_is_refused(self):
        for route in ('opencode', 'codex', '', None, 7):
            with self.subTest(route=route):
                self.assert_refused(self.config, route, 'claude-opus-5-5')
        without_claude = copy.deepcopy(self.config)
        del without_claude['routes']['claude']
        self.assert_refused(without_claude, 'claude', 'claude-opus-5-5')


if __name__ == '__main__':
    unittest.main()
