"""Owner #289 and #2302 part 1: the AGY route admits any well-formed model id.

Owner #289 first admitted the Claude ids AGY itself serves next to Gemini ids.
#2302 part 1 (Owner B) lifts the one-vendor rule: any well-formed model id of a
determinable family is admitted with no repository change, so a bare Claude id,
a Codex/GPT id or a Qwen/DeepSeek id is admitted too. A model the provider
cannot serve fails from the provider path. A malformed id, or an id whose family
cannot be read from it, is still refused. These are model identifiers only; no
credential-shaped or personal data is used.
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


class AgyModelAdmissionTests(unittest.TestCase):
    """AGY admits any well-formed id of a known family (A1, A1b)."""

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

    def test_c_gemini_models_are_accepted_as_on_main(self):
        self.assert_admitted('gemini-4-hydrogen-medium')
        self.assert_admitted('gemini-3.8-flash-medium')

    def test_d_other_vendor_ids_are_admitted(self):
        for model in ('gpt-6-sol', 'gpt-6.1-sol', 'codex-example-b', 'openai/gpt-6-sol',
                      'qwen3.8-27b', 'deepseek-v4.1-flash'):
            with self.subTest(model=model):
                self.assert_admitted(model)

    def test_e_claude_without_an_effort_suffix_is_admitted(self):
        # The provider decides whether it serves the id; there is no fallback.
        for model in ('claude-sonnet-5-5', 'claude-opus-5-5',
                      'claude-sonnet-5-5-minimal', 'claude-sonnet-5-5-xhigh',
                      'claude-sonnet-5-5-max'):
            with self.subTest(model=model):
                self.assert_admitted(model)

    def test_f_malformed_ids_are_refused(self):
        for model in ('claude sonnet 5 5 medium', 'claude-sonnet-5-5-medium/..',
                      '../claude-sonnet-5-5-medium', '-claude-sonnet-5-5-medium',
                      'https://example.invalid/claude-sonnet-5-5-medium',
                      'claude-sonnet-5-5-medium\n', '',
                      # AGY #289 review F1: the route caps the whole identifier
                      # at 128 characters; an over-long id is malformed.
                      'claude-sonnet-' + '9' * 108 + '-medium',   # 129 characters
                      'claude-sonnet-' + '9' * 130 + '-medium'):  # 130-digit version
            with self.subTest(model=model):
                self.assert_refused(model)

    def test_g_an_id_of_unknown_family_is_refused(self):
        # A1b: the family must be readable from the id, never taken from the route.
        for model in ('example-model-b', 'opencode/example-model-b-free', 'gw',
                      'claude-gpt-5-5-medium', 'claude-codex-5-5-high',
                      'claude-openai-5-5-low', 'claude-gemini-5-5-medium',
                      'claude-gemini-3.8-medium', 'gemini-claude-b'):
            with self.subTest(model=model):
                self.assert_refused(model)

    def test_h_the_check_is_mandatory_and_other_routes_keep_their_rules(self):
        # The format check remains mandatory, so no override can skip it.
        self.assertTrue(route_has_family_check('agy'))
        # The Claude route admits any well-formed id of a known family too.
        for model in ('claude-opus-5-5', 'gemini-3.8-flash-medium', 'gpt-6-sol'):
            with self.subTest(route='claude', model=model):
                self.assertEqual(check_route_model(self.config, 'claude', model), model)
        with self.assertRaises(RouteError) as caught:
            check_route_model(self.config, 'claude', 'example-model-b')
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
        # The OpenCode route still requires a provider/model id and admits
        # paid models.
        self.assertEqual(check_route_model(self.config, 'opencode', 'opencode/paid-model'),
                         'opencode/paid-model')
        with self.assertRaises(RouteError) as caught:
            check_route_model(self.config, 'opencode', 'bare-model')
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')


class AgyDisputeArbiterFamilyTests(unittest.TestCase):
    """Security repair (Codex security v91 F1, CWE-863; #2302 A1c): the family of
    a run comes from its recorded model, never the route name."""

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
        developer = {'provider_profile': 'agy', 'model': 'gemini-4-hydrogen-medium'}
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

    def test_d_claude_and_opencode_developers_with_their_own_models(self):
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
                      'gemini-4-hydrogen-medium'):
            with self.subTest(model=model):
                self.assertEqual(check_route_model(config, 'agy', model), model)
                self.assertEqual(check_route_model_family('agy', model), model)
        for model in ('example-model-b', 'claude-gpt-5-5-medium'):
            with self.subTest(model=model):
                with self.assertRaises(RouteError):
                    check_route_model(config, 'agy', model)

    def test_f_the_family_follows_the_model_not_the_route(self):
        # A1c: a Claude route run of another vendor's model takes that vendor's
        # family, so a same-family arbiter is never chosen for it.
        for developer, family, first, expected in (
                ({'provider_profile': 'claude', 'model': 'gpt-6.1-sol'}, 'codex',
                 'claude-reviewer', 'claude-reviewer'),
                ({'provider_profile': 'claude', 'model': 'gemini-3.8-flash-medium'},
                 'gemini', 'codex-reviewer', 'claude-reviewer'),
                ({'provider_profile': 'agy', 'model': 'gpt-6-sol'}, 'codex',
                 'claude-reviewer', 'claude-reviewer'),
                ({'provider_profile': 'agy', 'model': 'qwen3.8-27b'}, 'qwen',
                 'claude-reviewer', 'codex-reviewer'),
                ({'provider_profile': 'gw-agent', 'model': 'claude-opus-5-5'}, 'claude',
                 'codex-reviewer', 'codex-reviewer')):
            with self.subTest(developer=developer):
                self.assertEqual(developer_family(developer), family)
                arbiter = choose_arbiter(developer_family=developer_family(developer),
                                         first_reviewer=first,
                                         first_review_session='review-session-1')
                self.assertEqual(arbiter['reviewer'], expected)
                self.assertNotEqual(arbiter['family'], family)

    def test_g_a_model_of_unknown_family_is_unknown_not_the_route(self):
        # A1b/A1c: no family is inferred from the route for such a record.
        for profile in ('claude', 'agy', 'codex'):
            for model in ('example-model-b', 'claude-gpt-6'):
                with self.subTest(profile=profile, model=model):
                    self.assert_unknown_arbiter_refused(
                        {'provider_profile': profile, 'model': model})

    def test_h_a_legacy_record_with_no_model_keeps_its_result(self):
        for profile, family in (('claude', 'claude'), ('codex', 'codex'),
                                ('opencode', 'opencode'), ('gw-agent', 'gw-agent'),
                                ('agy', None)):
            for developer in ({'provider_profile': profile},
                              {'provider_profile': profile, 'model': None},
                              {'provider_profile': profile, 'model': ''}):
                with self.subTest(developer=developer):
                    self.assertEqual(developer_family(developer), family)


if __name__ == '__main__':
    unittest.main()
