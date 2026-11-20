"""Owner #289: the AGY route also admits the Claude ids AGY itself serves.

One test per row of the Task acceptance case table (a)-(g). These are model
identifiers only; no credential-shaped or personal data is used.
"""

import unittest

from llmtool.developer_routes import (
    RouteError,
    check_route_model,
    check_route_model_family,
    load_route_config,
    route_has_family_check,
)


class AgyClaudeModelTests(unittest.TestCase):
    """AGY admits Gemini ids and Claude ids of the form
    claude-<name>-<version>-<effort> (effort low, medium or high)."""

    def setUp(self):
        self.config = load_route_config()

    def assert_admitted(self, model):
        self.assertEqual(check_route_model(self.config, 'agy', model), model)
        self.assertEqual(check_route_model_family('agy', model), model)

    def assert_refused(self, model):
        with self.assertRaises(RouteError) as caught:
            check_route_model(self.config, 'agy', model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
        with self.assertRaises(RouteError) as caught:
            check_route_model_family('agy', model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_a_claude_sonnet_5_5_medium_is_accepted(self):
        self.assert_admitted('claude-sonnet-5-5-medium')

    def test_b_claude_opus_5_5_high_is_accepted(self):
        self.assert_admitted('claude-opus-5-5-high')

    def test_c_gemini_3_8_flash_medium_is_accepted_as_on_main(self):
        self.assert_admitted('gemini-3.8-flash-medium')

    def test_d_codex_and_gpt_ids_are_refused(self):
        for model in ('gpt-6-sol', 'gpt-6.1-sol', 'codex-example-b',
                      'openai/gpt-6-sol', 'claude-gpt-5-5-medium',
                      'claude-codex-5-5-high', 'claude-openai-5-5-low'):
            with self.subTest(model=model):
                self.assert_refused(model)

    def test_e_claude_without_an_effort_suffix_is_refused(self):
        for model in ('claude-sonnet-5-5', 'claude-opus-5-5',
                      'claude-sonnet-5-5-minimal', 'claude-sonnet-5-5-xhigh',
                      'claude-sonnet-5-5-max'):
            with self.subTest(model=model):
                self.assert_refused(model)

    def test_f_malformed_ids_are_refused(self):
        for model in ('claude sonnet 5 5 medium', 'Claude-sonnet-5-5-medium',
                      'CLAUDE-SONNET-5-5-MEDIUM', 'claude/sonnet/5/5/medium',
                      'claude-sonnet-5-5-medium/..', '../claude-sonnet-5-5-medium',
                      'claude--5-5-medium', 'claude-sonnet--medium',
                      'claude-sonnet-5-5-'):
            with self.subTest(model=model):
                self.assert_refused(model)

    def test_g_claude_and_opencode_routes_keep_their_rules(self):
        # The AGY family check remains mandatory, so no override can skip it.
        self.assertTrue(route_has_family_check('agy'))
        # The Claude route still accepts only Claude ids.
        self.assertEqual(check_route_model(self.config, 'claude', 'claude-opus-5-5'),
                         'claude-opus-5-5')
        for model in ('gemini-3.8-flash-medium', 'gpt-6-sol'):
            with self.subTest(route='claude', model=model):
                with self.assertRaises(RouteError) as caught:
                    check_route_model(self.config, 'claude', model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
        # The OpenCode route still requires a provider/model id and admits
        # paid models.
        self.assertEqual(check_route_model(self.config, 'opencode', 'opencode/paid-model'),
                         'opencode/paid-model')
        with self.assertRaises(RouteError) as caught:
            check_route_model(self.config, 'opencode', 'bare-model')
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')


if __name__ == '__main__':
    unittest.main()
