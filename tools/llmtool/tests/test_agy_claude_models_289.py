"""Owner #289: the AGY route also admits the Claude ids AGY itself serves.

One test per row of the first delivery's case table (a)-(g) and per row of the
security-repair dispute case table (a)-(e). These are model identifiers only;
no credential-shaped or personal data is used.
"""

import unittest

from llmtool.developer_routes import (
    RouteError,
    check_route_model,
    check_route_model_family,
    load_route_config,
    route_has_family_check,
)
from llmtool.errors import LLMToolError
from llmtool.review_repair import choose_arbiter, developer_family


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


class AgyDisputeArbiterFamilyTests(unittest.TestCase):
    """Security repair (Codex security v91 F1, CWE-863): the family of an
    AGY-authored run comes from its recorded model, never the route name."""

    def assert_unknown_arbiter_refused(self, developer):
        self.assertIsNone(developer_family(developer))
        with self.assertRaises(LLMToolError) as caught:
            choose_arbiter(developer_family=developer_family(developer),
                           first_reviewer='codex-reviewer',
                           first_review_session='review-session-1')
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ARBITER_UNAVAILABLE')

    def test_a_agy_claude_developer_gets_no_claude_arbiter(self):
        developer = {'provider_profile': 'agy', 'model': 'claude-sonnet-5-5-medium'}
        self.assertEqual(developer_family(developer), 'claude')
        arbiter = choose_arbiter(developer_family=developer_family(developer),
                                 first_reviewer='codex-reviewer',
                                 first_review_session='review-session-1')
        self.assertEqual(arbiter['reviewer'], 'codex-reviewer')
        self.assertEqual(arbiter['family'], 'codex')

    def test_b_agy_gemini_developer_keeps_the_main_choice(self):
        developer = {'provider_profile': 'agy', 'model': 'gemini-3.8-flash-medium'}
        self.assertEqual(developer_family(developer), 'gemini')
        arbiter = choose_arbiter(developer_family=developer_family(developer),
                                 first_reviewer='codex-reviewer',
                                 first_review_session='review-session-1')
        self.assertEqual(arbiter['reviewer'], 'claude-reviewer')
        self.assertEqual(arbiter['family'], 'claude')

    def test_c_agy_developer_without_a_recorded_model_is_unknown(self):
        for developer in ({'provider_profile': 'agy'},
                          {'provider_profile': 'agy', 'model': None}):
            with self.subTest(developer=developer):
                self.assert_unknown_arbiter_refused(developer)

    def test_d_claude_and_opencode_developers_are_unchanged(self):
        self.assertEqual(developer_family(
            {'provider_profile': 'claude', 'model': 'claude-opus-5-5'}), 'claude')
        self.assertEqual(developer_family(
            {'provider_profile': 'opencode',
             'model': 'anthropic/claude-sonnet-5-5'}), 'claude')
        self.assertEqual(developer_family(
            {'provider_profile': 'opencode', 'model': 'openai/gpt-6-sol'}), 'codex')
        self.assertEqual(developer_family(
            {'provider_profile': 'opencode', 'model': 'opencode/paid-model'}), 'opencode')
        # A Claude-authored run still gets the cross-family Codex arbiter.
        arbiter = choose_arbiter(developer_family='claude',
                                 first_reviewer='claude-reviewer',
                                 first_review_session='review-session-1')
        self.assertEqual(arbiter['reviewer'], 'codex-reviewer')

    def test_e_agy_route_admission_rows_still_pass(self):
        config = load_route_config()
        for model in ('claude-sonnet-5-5-medium', 'claude-opus-5-5-high',
                      'gemini-3.8-flash-medium'):
            with self.subTest(model=model):
                self.assertEqual(check_route_model(config, 'agy', model), model)
                self.assertEqual(check_route_model_family('agy', model), model)
        for model in ('claude-sonnet-5-5', 'gpt-6-sol',
                      'Claude-Sonnet-5-5-medium'):
            with self.subTest(model=model):
                with self.assertRaises(RouteError):
                    check_route_model(config, 'agy', model)


if __name__ == '__main__':
    unittest.main()
