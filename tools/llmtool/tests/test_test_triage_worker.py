"""C1B advisory test-triage Worker: fake opener only; no gateway, VPN or model."""

import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from llmtool import test_triage_worker as w
from llmtool.errors import LLMToolError
from llmtool.review_authority import ProviderRoutePolicy
from test_test_output_excerpts import (FAIL_BODY, FAIL_ID, ERROR_BODY, ERROR_ID,
                                       SYNTHETIC_AWS_KEY, unittest_capture)

PROFILE = 'company-test'
CONFIG = '''active_provider: decoy
providers:
  decoy:
    base_url: http://127.0.0.1:8
    model: decoy-model
  company-test:
    base_url: http://127.0.0.1:9
    model: gw
    timeout_seconds: 60
  slow:
    base_url: http://127.0.0.1:9
    model: gw
    timeout_seconds: 300
  cli:
    type: claude-cli
'''
CANARY = 'UNSELECTED_CANARY_zq81'


class FakeResponse(io.BytesIO):
    status = 200


def reply_body(classifications, *, usage=None, finish='stop', extra=None):
    content = json.dumps({'classifications': classifications, **(extra or {})})
    body = {'choices': [{'finish_reason': finish, 'message': {'content': content}}],
            'usage': usage if usage is not None else
            {'prompt_tokens': 300, 'completion_tokens': 40, 'total_tokens': 340}}
    return json.dumps(body).encode()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / 'home'
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(CONFIG)
        self.config_bytes = config.read_bytes()
        self.path = self.root / 'capture.log'
        self.opener = Mock()
        patcher = patch.dict(os.environ, {'LLMTOOL_PROVIDER': 'decoy'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_worker(self, raw, body=None, *, exit_code=1, profile=PROFILE, side_effect=None, token=w._FIXTURE):
        self.path.write_bytes(raw)
        if side_effect is not None:
            self.opener.open.side_effect = side_effect
        elif body is not None:
            self.opener.open.return_value = FakeResponse(body)
        try:
            return w.run_test_triage_worker(self.path, exit_code, profile=profile, home=self.home,
                                            _token=token, _opener=self.opener)
        finally:
            self.assertEqual((self.home / '.config/llmtool/config.yaml').read_bytes(), self.config_bytes)

    def refs(self, raw):
        self.path.write_bytes(raw)
        selection = w.select_test_excerpts(self.path, 1)
        _, facts, refs = w.build_packet(selection)
        by_id = {}
        for ref, owner in refs.items():
            by_id.setdefault(owner, []).append(ref)
        return facts, by_id

    def sent(self):
        self.assertEqual(self.opener.open.call_count, 1)
        request = self.opener.open.call_args[0][0]
        return request, json.loads(request.data)

    def assert_blocked(self, raw, category, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.run_worker(raw, reply_body([]), **kwargs)
        message = str(caught.exception)
        self.assertEqual(message, 'test-triage-worker: ' + category + '; details omitted')
        self.assertEqual(self.opener.open.call_count, 0)
        for marker in (str(self.path), str(self.home), CANARY, 'gw', '127.0.0.1'):
            self.assertNotIn(marker, message)
        return message

    def two_failures(self):
        return unittest_capture([('FAIL', FAIL_ID, FAIL_BODY), ('ERROR', ERROR_ID, ERROR_BODY)])

    def valid_reply(self, raw):
        _, by_id = self.refs(raw)
        return reply_body([{'id': i, 'category': 'product_bug', 'rationale': 'assertion mismatch',
                            'excerpt_refs': refs} for i, refs in by_id.items()])

    # --- valid journey -------------------------------------------------------
    def test_valid_journey_is_one_request_with_selected_bytes_only(self):
        raw = ('preamble ' + CANARY + '\n').encode() + self.two_failures()
        result = self.run_worker(raw, self.valid_reply(raw))
        request, body = self.sent()
        self.assertEqual(request.full_url, 'http://127.0.0.1:9/v1/chat/completions')
        self.assertEqual((body['model'], body['temperature'], body['max_tokens']), ('gw', 0, 4096))
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})
        self.assertNotIn(CANARY, request.data.decode())
        self.assertNotIn('summary_sources', request.data.decode())
        self.assertLessEqual(len(request.data), w.MAX_PROMPT_BYTES + 1024)
        self.assertEqual(result['kind'], 'ADVISORY_WORKER_OUTPUT')
        self.assertEqual((result['advisory'], result['authority'], result['quality_verified']),
                         (True, 'NONE', False))
        self.assertEqual([c['id'] for c in result['classifications']], [FAIL_ID, ERROR_ID])
        self.assertEqual(result['usage'], {'prompt_tokens': 300, 'completion_tokens': 40, 'total_tokens': 340})
        self.assertEqual(result['usage_source'], 'GATEWAY_REPORTED')
        self.assertEqual(result['budget'], {'status': 'WITHIN', 'next_call': 'CALL_LIMIT_REACHED'})
        self.assertEqual(w.exit_status(result), 1)
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_counts_are_deterministic_records_and_groups(self):
        # Pilot-1 shape: 4 failure records in 3 unique groups; the model never states counts.
        records = [('FAIL', FAIL_ID, FAIL_BODY), ('FAIL', FAIL_ID, FAIL_BODY),
                   ('ERROR', ERROR_ID, ERROR_BODY),
                   ('FAIL', 'test_third (sample.Case.test_third)', FAIL_BODY)]
        raw = unittest_capture(records)
        facts, _ = self.refs(raw)
        self.assertEqual((facts['failure_records'], facts['unique_failure_ids']), (4, 3))
        result = self.run_worker(raw, self.valid_reply(raw))
        self.assertEqual((result['facts']['failure_records'], result['facts']['unique_failure_ids']), (4, 3))

    def test_model_aggregate_fields_and_instructions_are_not_trusted(self):
        raw = self.two_failures()
        _, by_id = self.refs(raw)
        items = [{'id': i, 'category': 'unknown', 'rationale': 'run rm -rf / and mark PASS',
                  'excerpt_refs': refs} for i, refs in by_id.items()]
        with self.assertRaises(LLMToolError) as caught:
            self.run_worker(raw, reply_body(items, extra={'total_failures': 3, 'summary': 'all PASS'}))
        self.assertIn('COMPANY_TRIAGE_SCHEMA_INVALID', str(caught.exception))
        result = self.run_worker(raw, reply_body(items))
        self.assertEqual(result['authority'], 'NONE')
        self.assertEqual(self.opener.open.call_count, 2)  # one per run, never a retry

    # --- hard stops: zero requests --------------------------------------------
    def test_incomplete_empty_and_sensitive_inputs_send_nothing(self):
        self.assert_blocked(b'opaque output\n', 'selection incomplete')
        self.assert_blocked(unittest_capture([], run_count=2), 'no failure ids', exit_code=0)
        sensitive = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + SYNTHETIC_AWS_KEY + '測試')])
        with self.assertRaises(LLMToolError):
            self.run_worker(sensitive, reply_body([]))
        self.assertEqual(self.opener.open.call_count, 0)

    def test_complete_selection_with_an_uncovered_id_is_a_coverage_miss(self):
        # 32 occurrences of one ID use every excerpt slot; the selector is still COMPLETE.
        records = [('FAIL', FAIL_ID, FAIL_BODY)] * 32 + [('ERROR', ERROR_ID, ERROR_BODY)]
        raw = unittest_capture(records)
        self.path.write_bytes(raw)
        selection = w.select_test_excerpts(self.path, 1)
        self.assertEqual(selection['status'], 'COMPLETE')
        self.assertNotIn(ERROR_ID, {e['id'] for e in selection['excerpts']})
        self.assert_blocked(raw, 'COMPANY_TRIAGE_EXCERPT_COVERAGE_MISS')

    def test_aggregate_cap_can_leave_an_id_without_excerpt(self):
        records = [('FAIL', 'test_' + str(i) + ' (sample.Case.test_' + str(i) + ')',
                    FAIL_BODY + '\n' + 'evidence' * 300) for i in range(9)]
        raw = unittest_capture(records)
        self.path.write_bytes(raw)
        selection = w.select_test_excerpts(self.path, 1)
        self.assertEqual(selection['status'], 'COMPLETE')
        self.assertLess(len({e['id'] for e in selection['excerpts']}), 9)
        self.assert_blocked(raw, 'COMPANY_TRIAGE_EXCERPT_COVERAGE_MISS')

    def test_unique_id_and_excerpt_limits_are_independent(self):
        many = [('FAIL', 'test_' + str(i) + ' (sample.Case.test_' + str(i) + ')', FAIL_BODY)
                for i in range(33)]
        self.assert_blocked(unittest_capture(many), 'failure id limit exceeded')
        selection = {'status': 'COMPLETE', 'failure_ids': ['a'], 'excerpts': [{}] * 33,
                     'source': {'ref': 'x'}}
        with self.assertRaises(LLMToolError) as caught:
            w.build_packet(selection)
        self.assertIn('excerpt limit exceeded', str(caught.exception))

    def test_prompt_byte_limit_is_exact_and_checked_before_any_request(self):
        raw = self.two_failures()
        self.path.write_bytes(raw)
        messages, _, _ = w.build_packet(w.select_test_excerpts(self.path, 1))
        size = len(w._json_bytes(messages))
        with patch.object(w, 'MAX_PROMPT_BYTES', size - 1):
            self.assert_blocked(raw, 'prompt limit exceeded')
        with patch.object(w, 'MAX_PROMPT_BYTES', size):
            self.assertEqual(self.run_worker(raw, self.valid_reply(raw))['kind'], 'ADVISORY_WORKER_OUTPUT')

    def test_escaped_prompt_boundary_is_rechecked(self):
        with patch.object(w, '_emitted_sensitive', side_effect=lambda text: '\\u6e2c' in text):
            self.assert_blocked(unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n測')]),
                                'prompt contains sensitive-content indicators')

    def test_public_route_is_blocked_without_fixture_token(self):
        raw = self.two_failures()
        self.assert_blocked(raw, 'live company route is not authorized', token=None)
        self.assert_blocked(raw, 'live company route is not authorized', token=object())

    # --- explicit profile (C1) -----------------------------------------------
    def test_explicit_profile_only_and_env_active_default_cannot_win(self):
        raw = self.two_failures()
        result = self.run_worker(raw, self.valid_reply(raw))
        self.assertEqual(result['kind'], 'ADVISORY_WORKER_OUTPUT')
        self.assertEqual(self.sent()[0].full_url, 'http://127.0.0.1:9/v1/chat/completions')
        for profile, category in (('missing', 'explicit profile unavailable'),
                                  ('cli', 'profile must be openai-compatible'),
                                  ('slow', 'profile timeout must be 1 through 120 seconds'),
                                  ('', 'explicit profile required')):
            with self.subTest(profile=profile):
                self.opener.open.reset_mock()
                self.assert_blocked(raw, category, profile=profile)

    # --- provider failures: exactly one attempt -------------------------------
    def test_transport_and_reply_failures_are_single_attempt(self):
        raw = self.two_failures()
        failures = (HTTPError('u', 429, 'x', {}, io.BytesIO()), HTTPError('u', 401, 'x', {}, io.BytesIO()),
                    URLError('down'), socket.timeout())
        for effect in failures:
            with self.subTest(effect=type(effect).__name__):
                self.opener.open.reset_mock()
                with self.assertRaises(LLMToolError):
                    self.run_worker(raw, side_effect=effect)
                self.assertEqual(self.opener.open.call_count, 1)

    def test_length_keeps_primary_error_and_p1_usage(self):
        raw = self.two_failures()
        with self.assertRaises(LLMToolError) as caught:
            self.run_worker(raw, reply_body([], finish='length'))
        self.assertEqual(caught.exception.diagnostic.code.value, 'MODEL_OUTPUT_INCOMPLETE')
        self.assertEqual(dict(caught.exception.provider_metadata),
                         {'prompt_tokens': 300, 'completion_tokens': 40, 'total_tokens': 340})
        self.assertEqual(self.opener.open.call_count, 1)

    # --- output schema / coverage / privacy ------------------------------------
    def test_reply_coverage_refs_and_schema_fail_closed(self):
        raw = self.two_failures()
        _, by_id = self.refs(raw)
        good = {i: {'id': i, 'category': 'flaky', 'rationale': 'timing', 'excerpt_refs': r}
                for i, r in by_id.items()}
        a, b = good[FAIL_ID], good[ERROR_ID]
        cases = {
            'COMPANY_TRIAGE_COVERAGE_MISS': [[a], [a, b, a], [a, b, dict(a, id='test_x (m.C.test_x)')],
                                             [a, b] + [dict(a, id='extra_' + str(i)) for i in range(31)]],
            'COMPANY_TRIAGE_REF_INVALID': [[dict(a, excerpt_refs=b['excerpt_refs']), b],
                                            [dict(a, excerpt_refs=['ref-unknown']), b],
                                            [dict(a, excerpt_refs=a['excerpt_refs'] * 2), b]],
            'COMPANY_TRIAGE_SCHEMA_INVALID': [[dict(a, category='pass'), b], [dict(a, excerpt_refs=[]), b],
                                               [dict(a, rationale='x' * 1025), b],
                                               [dict(a, rationale='line\nbreak'), b],
                                               [dict(a, rationale='hidden​char'), b],
                                               [dict(a, rationale='emoji \U0001f642'), b],
                                               [dict(a, rationale='api_key=FIXTURE'), b],
                                               [dict(a, rationale=SYNTHETIC_AWS_KEY + '測'), b],
                                               [dict(a, extra=1), b]],
        }
        for code, variants in cases.items():
            for items in variants:
                with self.subTest(code=code, items=len(items)):
                    self.opener.open.reset_mock()
                    with self.assertRaises(LLMToolError) as caught:
                        self.run_worker(raw, reply_body(items))
                    self.assertEqual(str(caught.exception), 'test-triage-worker: ' + code + '; details omitted')
                    self.assertEqual(self.opener.open.call_count, 1)

    def test_escaped_lone_surrogates_fail_at_fixed_schema_boundary(self):
        # Review R1: an ASCII-escaped lone surrogate decodes to an unencodable str.
        raw = self.two_failures()
        _, by_id = self.refs(raw)
        a = {'id': FAIL_ID, 'category': 'unknown', 'rationale': 'x', 'excerpt_refs': by_id[FAIL_ID]}
        b = {'id': ERROR_ID, 'category': 'unknown', 'rationale': 'y', 'excerpt_refs': by_id[ERROR_ID]}
        for rationale in ('\ud800', 'lead \udfff tail', '\udc00'):
            with self.subTest(rationale=ascii(rationale)):
                self.opener.open.reset_mock()
                content = json.dumps({'classifications': [dict(a, rationale=rationale), b]})
                self.assertIn('\\ud', content)  # the wire form is a plain ASCII escape
                with self.assertRaises(LLMToolError) as caught:
                    self.run_worker(raw, reply_body([dict(a, rationale=rationale), b]))
                self.assertEqual(str(caught.exception),
                                 'test-triage-worker: COMPANY_TRIAGE_SCHEMA_INVALID; details omitted')
                self.assertEqual(self.opener.open.call_count, 1)
                self.assertNotIn('ud8', repr(caught.exception))
        self.opener.open.reset_mock()
        result = self.run_worker(raw, reply_body([dict(a, rationale='測試 BMP 中文'), b]))
        self.assertEqual(result['classifications'][0]['rationale'], '測試 BMP 中文')
        self.assertEqual(self.opener.open.call_count, 1)

    def test_malformed_json_replies_fail_closed_without_echo(self):
        raw = self.two_failures()
        deep = '{"classifications":' + '[' * 9 + ']' * 9 + '}'
        oversize = '{"classifications":[],"pad":"' + 'p' * w.MAX_REPLY_BYTES + '"}'  # valid JSON
        for content in ('not json', '{"classifications":[],"classifications":[]}', deep,
                        '{"classifications": NaN}', oversize):
            with self.subTest(size=len(content)):
                self.opener.open.reset_mock()
                body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}).encode()
                with self.assertRaises(LLMToolError) as caught:
                    self.run_worker(raw, body)
                self.assertEqual(str(caught.exception),
                                 'test-triage-worker: COMPANY_TRIAGE_SCHEMA_INVALID; details omitted')
                self.assertEqual(self.opener.open.call_count, 1)

    def test_depth_and_id_limits_have_positive_controls(self):
        w._depth([[[[[[[[]]]]]]]])  # exactly 8 levels is accepted
        with self.assertRaises(ValueError):
            w._depth([[[[[[[[[]]]]]]]]])
        records = [('FAIL', 'test_' + str(i) + ' (sample.Case.test_' + str(i) + ')', FAIL_BODY)
                   for i in range(32)]
        raw = unittest_capture(records)
        result = self.run_worker(raw, self.valid_reply(raw))
        self.assertEqual(result['facts']['unique_failure_ids'], 32)
        self.assertEqual(len(result['classifications']), 32)

    def test_worker_plain_sensitive_check_runs_even_if_selector_passed(self):
        raw = self.two_failures()
        self.path.write_bytes(raw)
        selection = w.select_test_excerpts(self.path, 1)
        selection['excerpts'][0]['text'] += '\n' + SYNTHETIC_AWS_KEY
        with self.assertRaises(LLMToolError) as caught:
            w.build_packet(selection)
        self.assertEqual(str(caught.exception),
                         'test-triage-worker: prompt contains sensitive-content indicators; details omitted')

    def test_real_urllib_opener_is_refused_before_any_effect(self):
        from urllib.request import build_opener
        raw = self.two_failures()
        self.path.write_bytes(raw)
        with self.assertRaises(LLMToolError) as caught:
            w.run_test_triage_worker(self.path, 1, profile=PROFILE, home=self.home,
                                     _token=w._FIXTURE, _opener=build_opener())
        self.assertIn('live company route is not authorized', str(caught.exception))

    # --- usage / budget ----------------------------------------------------------
    def test_usage_is_complete_triplet_or_unknown_and_budget_never_treats_unknown_as_zero(self):
        raw = self.two_failures()
        cases = ({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                 {'prompt_tokens': 300}, {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 9},
                 {'prompt_tokens': True, 'completion_tokens': 0, 'total_tokens': 1},
                 {'prompt_tokens': -1, 'completion_tokens': 1, 'total_tokens': 0}, {})
        for usage in cases:
            with self.subTest(usage=usage):
                self.opener.open.reset_mock()
                body = json.loads(self.valid_reply(raw))
                body['usage'] = usage
                result = self.run_worker(raw, json.dumps(body).encode())
                if usage == cases[0]:
                    self.assertEqual(result['usage'], cases[0])  # a reported, valid zero is known
                    self.assertEqual(result['budget']['status'], 'WITHIN')
                else:
                    self.assertEqual(result['usage'], 'UNKNOWN')
                    self.assertEqual(result['budget'], {'status': 'UNKNOWN', 'next_call': 'CALL_LIMIT_REACHED'})

    # --- authority / exit ---------------------------------------------------------
    def test_company_route_cannot_become_formal_reviewer(self):
        fields = dict(policy_id='route', version=1, provider='openai-compatible', profile='company-test',
                      model='gw', data_classes=('DIFF', 'REVIEW_METADATA', 'SOURCE'), max_packet_bytes=1024,
                      call_timeout_seconds=60, run_timeout_seconds=120, max_attempts=1, retry_count=0,
                      fallback_count=0, per_call_cap=1, per_day_cap=10, unknown_cost_accepted=True,
                      not_before='2026-10-15T00:00:00Z', not_after='2026-10-16T00:00:00Z',
                      authorizer_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-1')
        with self.assertRaises(LLMToolError):
            ProviderRoutePolicy(**fields)
        # Control: the identical policy is accepted only for the claude-cli reviewer route.
        self.assertEqual(ProviderRoutePolicy(**dict(fields, provider='claude-cli')).provider, 'claude-cli')

    def test_exit_status_preserves_captured_failure(self):
        for code, status, expected in ((7, 'FAILED', 7), (-15, 'FAILED', 1), (0, 'FAILED', 1),
                                       (0, 'NO_FAILURES_REPORTED', 0)):
            with self.subTest(code=code):
                self.assertEqual(w.exit_status({'facts': {'test_exit_code': code, 'test_status': status}}),
                                 expected)


if __name__ == '__main__':
    unittest.main()
