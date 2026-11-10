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
import review_trust_fixture
from test_test_output_excerpts import (FAIL_BODY, FAIL_ID, ERROR_BODY, ERROR_ID,
                                       SYNTHETIC_AWS_KEY, unittest_capture)


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()

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


class _WorkerFixture:
    # Shared setup/helpers only; test classes below add the TestCase base.
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


class WorkerTests(_WorkerFixture, unittest.TestCase):
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
                                  ('company-gw', 'company-gw route is retired'),
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
        fields = dict(reviewer_role='llmtool-review/claude-cli', policy_id='route', version=1, provider='openai-compatible', profile='company-test',
                      model='gw', data_classes=('DIFF', 'REVIEW_METADATA', 'SOURCE'), max_packet_bytes=1024,
                      call_timeout_seconds=60, run_timeout_seconds=120, max_attempts=1, retry_count=0,
                      fallback_count=0, per_call_cap=1, per_day_cap=10, unknown_cost_accepted=True,
                      not_before='2026-10-15T00:00:00Z', not_after='2026-10-16T00:00:00Z',
                      authorizer_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-1')
        with review_trust_fixture.use_trust():
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


GRANT_REF = 'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-1'


class Phase0GrantTests(_WorkerFixture, unittest.TestCase):
    """P0-LIVE-SEAM: explicit pinned grant; provider's own opener; exact wire bytes."""

    def setUp(self):
        super().setUp()
        self.grant_dir = self.root / 'grant'
        self.grant_dir.mkdir(mode=0o700)
        self.grant_path = self.grant_dir / 'phase0-grant.json'
        self.now = w.datetime.datetime(2026, 10, 15, 12, 0, tzinfo=w.datetime.timezone.utc)

    def grant_fields(self, raw, **overrides):
        values = {'schema_version': 1, 'kind': 'llmtool.company-phase0-grant', 'policy_id': 'phase0-c0c6',
                  'version': 1, 'profile': PROFILE, 'origin': 'http://127.0.0.1:9', 'model': 'gw',
                  'capture_sha256': [w.hashlib.sha256(raw).hexdigest()], 'max_calls': 7,
                  'not_before': '2026-10-15T00:00:00Z', 'not_after': '2026-10-16T00:00:00Z',
                  'authorizer_ref': GRANT_REF}
        values.update(overrides)
        return values

    def write_grant(self, value, mode=0o600):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        if self.grant_path.exists() or self.grant_path.is_symlink():
            self.grant_path.unlink()
        self.grant_path.write_bytes(data)
        os.chmod(self.grant_path, mode)
        return w.hashlib.sha256(data).hexdigest()

    def live(self, raw, body=None, *, pin=None, grant=None, now=None, fake=None, **kwargs):
        self.path.write_bytes(raw)
        pin = pin if pin is not None else self.write_grant(grant if grant is not None else self.grant_fields(raw))
        fake = fake or Mock()
        if body is not None:
            fake.open.return_value = FakeResponse(body)
        with patch('llmtool.providers.openai_compatible.request.build_opener', return_value=fake) as built:
            try:
                return w.run_test_triage_worker(self.path, 1, profile=PROFILE, home=self.home,
                                                phase0_grant=self.grant_path, phase0_grant_sha256=pin,
                                                _now=lambda: now or self.now, **kwargs), fake
            finally:
                self.last_fake, self.last_built = fake, built

    def assert_live_blocked(self, raw, category, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.live(raw, self.valid_reply(raw), **kwargs)
        self.assertEqual(str(caught.exception), 'test-triage-worker: ' + category + '; details omitted')
        self.assertEqual(self.last_fake.open.call_count, 0)
        if category != 'request body limit exceeded':  # only the wire cap runs after the client exists
            self.assertEqual(self.last_built.call_count, 0)
        for marker in (str(self.grant_path), str(self.path), GRANT_REF, '127.0.0.1', CANARY):
            self.assertNotIn(marker, str(caught.exception))

    def test_valid_grant_sends_exactly_one_request_through_provider_opener(self):
        raw = self.two_failures()
        result, fake = self.live(raw, self.valid_reply(raw))
        self.assertEqual(fake.open.call_count, 1)
        request_obj = fake.open.call_args[0][0]
        self.assertEqual(request_obj.full_url, 'http://127.0.0.1:9/v1/chat/completions')
        self.assertEqual(fake.open.call_args[1]['timeout'], 60)
        self.assertEqual(result['route'], 'PHASE0_GRANT')
        self.assertEqual(result['kind'], 'ADVISORY_WORKER_OUTPUT')
        self.assertEqual(result['grant']['policy_id'], 'phase0-c0c6')
        # Digest, cap and the bytes actually sent are one serialization.
        sent = request_obj.data
        self.assertEqual(result['digests']['request_body'], 'sha256:' + w.hashlib.sha256(sent).hexdigest())
        self.assertEqual(result['request_body_bytes'], len(sent))
        body = json.loads(sent)
        self.assertEqual((body['model'], body['temperature'], body['max_tokens']), ('gw', 0, 4096))
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': False})
        self.assertNotIn(CANARY, sent.decode())

    def test_missing_or_partial_grant_arguments_stay_blocked(self):
        raw = self.two_failures()
        self.path.write_bytes(raw)
        pin = self.write_grant(self.grant_fields(raw))
        fake = Mock()
        with patch('llmtool.providers.openai_compatible.request.build_opener', return_value=fake):
            for kwargs in ({}, {'phase0_grant': self.grant_path}, {'phase0_grant_sha256': pin}):
                with self.subTest(kwargs=sorted(kwargs)):
                    with self.assertRaises(LLMToolError) as caught:
                        w.run_test_triage_worker(self.path, 1, profile=PROFILE, home=self.home, **kwargs)
                    self.assertIn('live company route is not authorized', str(caught.exception))
        self.assertEqual(fake.open.call_count, 0)

    def test_pin_mismatch_and_malformed_pins_block(self):
        raw = self.two_failures()
        good = self.write_grant(self.grant_fields(raw))
        for pin in ('0' * 64, good.upper(), good[:-1], 'not-hex', ''):
            with self.subTest(pin=pin[:8]):
                self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)

    def test_grant_file_safety(self):
        raw = self.two_failures()
        fields = self.grant_fields(raw)
        pin = self.write_grant(fields, mode=0o644)
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)
        pin = self.write_grant(fields)
        target = self.grant_dir / 'real.json'
        self.grant_path.rename(target)
        self.grant_path.symlink_to(target)
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)
        self.grant_path.unlink()
        pin = self.write_grant(b'{' + b' ' * (w.MAX_GRANT_BYTES + 1) + b'}')
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)
        os.chmod(self.grant_dir, 0o755)
        pin = self.write_grant(fields)
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)
        os.chmod(self.grant_dir, 0o750)
        pin = self.write_grant(fields)
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)
        os.chmod(self.grant_dir, 0o700)
        os.link(self.grant_path, self.grant_dir / 'second-link.json')
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)
        (self.grant_dir / 'second-link.json').unlink()
        self.grant_path.unlink()
        self.assert_live_blocked(raw, 'phase0 grant invalid', pin=pin)

    def test_grant_schema_is_strict(self):
        raw = self.two_failures()
        bad = [b'not json', b'{"schema_version": 1, "schema_version": 1}', b'[]',
               json.dumps(dict(self.grant_fields(raw), extra=1)).encode()]
        for key, value in (('schema_version', 2), ('kind', 'other'), ('version', 0), ('version', True),
                           ('policy_id', 'bad id/with space'), ('max_calls', 0), ('max_calls', 8), ('max_calls', 9),
                           ('capture_sha256', []), ('capture_sha256', ['%064x' % i for i in range(8)]), ('capture_sha256', ['A' * 64]),
                           ('capture_sha256', ['a' * 64] * 2), ('capture_sha256', ['a' * 64] * 9),
                           ('origin', 'http://127.0.0.1:9/path'), ('origin', 'ftp://x'),
                           ('not_before', '2026-10-15 00:00:00'), ('not_after', '2026-10-14T00:00:00Z'),
                           ('authorizer_ref', 'https://example.invalid/grant')):
            bad.append(json.dumps(self.grant_fields(raw, **{key: value})).encode())
        for data in bad:
            with self.subTest(data=data[:40]):
                self.assert_live_blocked(raw, 'phase0 grant invalid', grant=data)

    def test_grant_binding_mismatches_block_before_any_request(self):
        raw = self.two_failures()
        for overrides, category in (({'profile': 'decoy'}, 'phase0 grant does not match profile'),
                                    ({'origin': 'http://127.0.0.1:8'}, 'phase0 grant does not match profile'),
                                    ({'model': 'other-model'}, 'phase0 grant does not match profile'),
                                    ({'capture_sha256': ['b' * 64]}, 'phase0 grant does not cover capture')):
            with self.subTest(overrides=overrides):
                self.assert_live_blocked(raw, category, grant=self.grant_fields(raw, **overrides))

    def test_grant_time_window_is_enforced(self):
        raw = self.two_failures()
        utc = w.datetime.timezone.utc
        for now in (w.datetime.datetime(2026, 10, 14, 23, 59, 59, tzinfo=utc),
                    w.datetime.datetime(2026, 10, 16, 0, 0, 0, tzinfo=utc)):
            with self.subTest(now=now.isoformat()):
                self.assert_live_blocked(raw, 'phase0 grant outside its time window', now=now)
        for now in (w.datetime.datetime(2026, 10, 15, 0, 0, 0, tzinfo=utc),
                    w.datetime.datetime(2026, 10, 15, 23, 59, 59, tzinfo=utc)):
            result, fake = self.live(raw, self.valid_reply(raw), now=now)
            self.assertEqual(fake.open.call_count, 1)
        with self.assertRaises(LLMToolError):  # a naive clock is never trusted
            self.live(raw, self.valid_reply(raw), now=w.datetime.datetime(2026, 10, 15, 12, 0))
        self.assertEqual(self.last_fake.open.call_count, 0)

    def test_request_body_cap_is_on_exact_wire_bytes(self):
        raw = self.two_failures()
        result, fake = self.live(raw, self.valid_reply(raw))
        size = result['request_body_bytes']
        with patch.object(w, 'MAX_REQUEST_BODY_BYTES', size - 1):
            self.assert_live_blocked(raw, 'request body limit exceeded')
        with patch.object(w, 'MAX_REQUEST_BODY_BYTES', size):
            result, fake = self.live(raw, self.valid_reply(raw))
            self.assertEqual(fake.open.call_count, 1)
            self.assertEqual(len(fake.open.call_args[0][0].data), size)

    def test_live_path_refuses_injected_openers_and_ignores_environment(self):
        raw = self.two_failures()
        for kwargs in ({'_opener': Mock()}, {'_token': w._FIXTURE}, {'_token': w._FIXTURE, '_opener': Mock()}):
            with self.subTest(kwargs=sorted(kwargs)):
                with self.assertRaises(LLMToolError):
                    self.live(raw, self.valid_reply(raw), **kwargs)
                self.assertEqual((self.last_fake.open.call_count, self.last_built.call_count), (0, 0))
        with patch.dict(os.environ, {'LLMTOOL_PROVIDER': 'decoy', 'LLMTOOL_BASE_URL': 'http://127.0.0.1:7',
                                     'LLMTOOL_MODEL': 'env-model'}):
            result, fake = self.live(raw, self.valid_reply(raw))
        self.assertEqual(fake.open.call_args[0][0].full_url, 'http://127.0.0.1:9/v1/chat/completions')
        self.assertEqual(json.loads(fake.open.call_args[0][0].data)['model'], 'gw')

    def test_live_failures_are_single_attempt_without_fallback(self):
        raw = self.two_failures()
        for effect in (HTTPError('u', 429, 'x', {}, io.BytesIO()), URLError('down'), socket.timeout()):
            with self.subTest(effect=type(effect).__name__):
                fake = Mock()
                fake.open.side_effect = effect
                with self.assertRaises(LLMToolError):
                    self.live(raw, fake=fake)
                self.assertEqual(fake.open.call_count, 1)


    def test_g1_expiry_crossed_during_encode_has_zero_send(self):
        raw = self.two_failures(); self.path.write_bytes(raw)
        pin = self.write_grant(self.grant_fields(raw)); fake = Mock()
        times = iter((self.now, w.datetime.datetime(2026, 10, 16, tzinfo=w.datetime.timezone.utc)))
        with patch('llmtool.providers.openai_compatible.request.build_opener', return_value=fake):
            with self.assertRaises(LLMToolError) as caught:
                w.run_test_triage_worker(self.path, 1, profile=PROFILE, home=self.home,
                    phase0_grant=self.grant_path, phase0_grant_sha256=pin, _now=lambda: next(times))
        self.assertIn('phase0 grant outside its time window', str(caught.exception))
        self.assertEqual(fake.open.call_count, 0)

    def test_g2_worker_schema_v2_both_routes_and_actual_wire_digest(self):
        raw = self.two_failures()
        fixture = self.run_worker(raw, self.valid_reply(raw))
        result, fake = self.live(raw, self.valid_reply(raw))
        for value in (fixture, result):
            self.assertEqual(value['schema_version'], 2)
            self.assertEqual(set(value['digests']), {'request_body', 'reply'})
        sent = fake.open.call_args[0][0].data
        self.assertEqual(result['digests']['request_body'], 'sha256:' + w.hashlib.sha256(sent).hexdigest())

    def test_g3_secret_policy_metadata_zero_client_and_no_echo(self):
        raw = self.two_failures()
        self.assert_live_blocked(raw, 'phase0 grant invalid',
            grant=self.grant_fields(raw, policy_id=SYNTHETIC_AWS_KEY))

    def test_la2_every_git_boundary_is_rejected_even_private_subdir(self):
        raw = self.two_failures(); original = self.grant_path
        for marker in ('directory', 'file'):
            repo = self.root / ('other-repo-' + marker); repo.mkdir()
            dotgit = repo / '.git'
            if marker == 'directory': dotgit.mkdir()
            else: dotgit.write_text('gitdir: /not-a-real-repository')
            private = repo / 'nested' / 'private'; private.mkdir(parents=True, mode=0o700)
            self.grant_path = private / 'grant.json'
            self.assert_live_blocked(raw, 'phase0 grant invalid')
        self.grant_path = original
        self.assertEqual(self.live(raw, self.valid_reply(raw))[1].open.call_count, 1)


    def test_grant_exact_modes_and_identity_change_block_before_client(self):
        raw = self.two_failures(); fields = self.grant_fields(raw)
        pin = self.write_grant(fields, mode=0o400)
        self.assert_live_blocked(raw,'phase0 grant invalid',pin=pin)
        pin = self.write_grant(fields); self.grant_dir.chmod(0o500)
        try:self.assert_live_blocked(raw,'phase0 grant invalid',pin=pin)
        finally:self.grant_dir.chmod(0o700)
        original = os.read
        def replace_during_read(fd, limit):
            value = original(fd,limit)
            self.grant_path.rename(self.grant_dir / 'previous.json')
            self.grant_path.write_bytes(value); self.grant_path.chmod(0o600)
            return value
        with patch.object(w.os,'read',side_effect=replace_during_read):
            self.assert_live_blocked(raw,'phase0 grant invalid',pin=pin)

    def test_la2_known_roots_and_unknown_boundary_fail_closed(self):
        # #369 PR-7: trust canonical_root and the whole workspace_root (stricter
        # than the former codex/claude subdirectories; Q9).
        canonical = review_trust_fixture.OWNER_CANONICAL_ROOT
        workspace = review_trust_fixture.OWNER_WORKSPACE_ROOT
        roots=(canonical, workspace, workspace+'/codex', workspace+'/claude',
               workspace+'/tasks', workspace+'/tasks/some-task', workspace+'/other')
        for root in roots:
            with patch.object(w.os,'open',side_effect=AssertionError('must reject before open')):
                with self.assertRaises(LLMToolError):
                    w.load_phase0_grant(root+'/private/grant.json','a'*64)
        raw=self.two_failures(); original=os.lstat
        def denied(path,*args,**kwargs):
            if str(path).endswith('/.git'):raise PermissionError('synthetic')
            return original(path,*args,**kwargs)
        with patch.object(w.os,'lstat',side_effect=denied):
            self.assert_live_blocked(raw,'phase0 grant invalid')

    def test_grant_under_fixture_trust_roots_refused_and_outside_passes(self):
        raw = self.two_failures(); original = self.grant_path
        base = self.root / 'trust-roots'
        canonical, workspace = base / 'canonical', base / 'workspace'
        config = review_trust_fixture.make_trust(canonical_root=str(canonical),
                                                 workspace_root=str(workspace))
        with review_trust_fixture.use_trust(config):
            for folder in (canonical / 'private', workspace / 'private',
                           workspace / 'tasks' / 'task-a' / 'private'):
                with self.subTest(folder=str(folder.relative_to(base))):
                    folder.mkdir(parents=True, mode=0o700); folder.chmod(0o700)
                    self.grant_path = folder / 'grant.json'
                    self.assert_live_blocked(raw, 'phase0 grant invalid')
            # Control: a private grant directory outside the trust roots passes.
            self.grant_path = original
            self.assertEqual(self.live(raw, self.valid_reply(raw))[1].open.call_count, 1)

    def test_missing_trust_blocks_grant_before_any_request(self):
        from llmtool import trust
        raw = self.two_failures()
        with patch.object(trust, 'load_trust', side_effect=trust.TrustError('TRUST_MISSING')):
            self.assert_live_blocked(raw, 'phase0 grant invalid')

    def test_full_wire_actual_64k_boundary_and_identical_bytes_object(self):
        from llmtool.providers.openai_compatible import OpenAICompatibleClient
        raw=self.two_failures(); original=OpenAICompatibleClient.encode_request
        for size in (65535,65536,65537):
            sent=[]
            def encode(client,messages):
                data=original(client,messages); data=data+b' '*(size-len(data)); sent.append(data);return data
            with patch.object(OpenAICompatibleClient,'encode_request',encode):
                if size>65536:self.assert_live_blocked(raw,'request body limit exceeded')
                else:
                    result,fake=self.live(raw,self.valid_reply(raw))
                    self.assertIs(fake.open.call_args[0][0].data,sent[0])
                    self.assertEqual(result['request_body_bytes'],size)
                    self.assertEqual(result['digests']['request_body'],w._digest(sent[0]))

    def test_g3_complete_escaped_output_scan_is_fixed_and_does_not_echo(self):
        raw=self.two_failures()
        with patch.object(w,'_budget',return_value={'status':SYNTHETIC_AWS_KEY}):
            with self.assertRaises(LLMToolError) as caught:self.live(raw,self.valid_reply(raw))
        self.assertEqual(str(caught.exception),'test-triage-worker: output contains sensitive-content indicators; details omitted')
        self.assertNotIn(SYNTHETIC_AWS_KEY,str(caught.exception))
        self.assertEqual(self.last_fake.open.call_count,1)


if __name__ == '__main__':
    unittest.main()
