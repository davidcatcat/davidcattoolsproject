"""C3-A offline tests: all sockets, subprocesses and real openers prohibited."""

import copy
import dataclasses
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import live_wrapper as wrapper
import format_probe as probe


def log_raw():
    records = probe.parse_l02_fixture(wrapper.fixture_bytes('L02'))
    return json.dumps({'summary': 'Supplied observations.',
                       'error_signatures': sorted({r.signature for r in records}),
                       'timeline': [r.text for r in sorted(records, key=lambda r: (r.timestamp, r.line))],
                       'repeated_patterns': [], 'evidence': [r.text for r in records],
                       'unknowns': [], 'strong_review_reasons': []}).encode()


def packet_raw(count=7):
    # Derive synthetic response provenance from fixture only, never target/gold.
    records = [r for r in probe.parse_l03_fixture(wrapper.fixture_bytes('L03'))
               if not r.record_id.startswith('INV-')][:count]
    return json.dumps({'facts': [], 'evidence': [
        {'statement': 'Supplied structured observation.', 'source': r.source, 'locator': r.locator}
        for r in records], 'inferences': [], 'hypotheses': [], 'unknowns': [],
        'verify_next': []}).encode()


class OfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Registration built from REAL CLI requests, never hand-built bodies.
        cls.guard = wrapper.offline_guard()
        cls.guard.__enter__()
        cls.registration = wrapper.build_preregistration()

    @classmethod
    def tearDownClass(cls):
        cls.guard.__exit__(None, None, None)

    def capture(self, lane='L02'):
        captures = []

        def observer(req, original, options):
            captures.append(req)
            raise wrapper.DryRunCaptured()

        with self.assertRaises(wrapper.DryRunCaptured):
            wrapper.invoke_cli(lane, wrapper.user_message(lane), observer)
        self.assertEqual(len(captures), 1)
        return captures[0]

    def started(self, lane='L02'):
        state = probe.Phase0Accounting()
        trial = state.begin_next()
        if lane == 'L03':
            _, proof = wrapper.validate_request(self.capture(), wrapper.user_message('L02'))
            state.mark_request_emitted(trial.trial_id, proof)
            state.complete_evaluation(trial.trial_id, wrapper.evaluate_raw('L02', log_raw()), 10)
            trial = state.begin_next()
        return state, trial

    def emitted(self, lane='L02'):
        state, trial = self.started(lane)
        _, proof = wrapper.validate_request(self.capture(lane), wrapper.user_message(lane))
        state.mark_request_emitted(trial.trial_id, proof)
        return state, trial

    def reject(self, request, lane='L02'):
        state, trial = self.started(lane)
        calls = []
        with self.assertRaises(wrapper.WrapperIntegrityFailure):
            wrapper.admitted_open(request, lambda *a, **k: calls.append(a), {'timeout': 120},
                                  lane, self.registration, state, trial.trial_id)
        self.assertEqual(calls, [])
        self.assertFalse(state.records[-1].request_emitted)

    def mutate_body(self, change, lane='L02'):
        req = self.capture(lane)
        body = json.loads(req.data)
        change(body)
        req.data = json.dumps(body).encode()
        return req

    def test_real_cli_capture_one_request(self):
        for lane in ('L02', 'L03'):
            self.assertIs(type(self.capture(lane)), urllib.request.Request)

    def test_original_opener_and_socket_never_called(self):
        with patch('socket.socket', side_effect=AssertionError('socket')) as sock, \
                patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('open')) as opened, \
                patch('socket.create_connection', side_effect=AssertionError('connect')) as connected, \
                patch('socket.getaddrinfo', side_effect=AssertionError('DNS')) as dns, \
                patch('subprocess.Popen', side_effect=AssertionError('subprocess')) as process:
            for lane in ('L02', 'L03'):
                self.capture(lane)
            self.assertEqual(sock.call_count, 0)
            self.assertEqual(opened.call_count, 0)
            self.assertEqual(connected.call_count, 0)
            self.assertEqual(dns.call_count, 0)
            self.assertEqual(process.call_count, 0)

    def test_exact_user_message(self):
        for lane in ('L02', 'L03'):
            instruction = (wrapper.HERE / 'instructions' / (lane + '.txt')).read_bytes()
            self.assertFalse(instruction.endswith(b'\n'))
            user = instruction + b'\n\n' + wrapper.fixture_bytes(lane)
            self.assertEqual(wrapper.user_message(lane), user)
            req = self.capture(lane)
            self.assertEqual(json.loads(req.data)['messages'][1]['content'].encode(), user)
            self.assertEqual(self.registration['lanes'][lane]['user_message_sha256'], wrapper.sha(user))

    def test_exact_fields_and_body_digest(self):
        for lane in ('L02', 'L03'):
            request = self.capture(lane)
            self.assertEqual(set(json.loads(request.data)), wrapper.FIELDS)
            self.assertEqual(wrapper.sha(request.data), self.registration['lanes'][lane]['request_body_sha256'])

    def test_l02_repeated_freeze_stable(self):
        self.assertEqual(wrapper.dry_run('L02'), wrapper.dry_run('L02'))

    def test_l03_repeated_freeze_stable(self):
        self.assertEqual(wrapper.dry_run('L03'), wrapper.dry_run('L03'))

    def test_stream_rejected(self):
        self.reject(self.mutate_body(lambda b: b.update(stream=False)))

    def test_extra_fields_rejected(self):
        for field in ('extra', 'tools', 'tool_choice', 'response_format'):
            self.reject(self.mutate_body(lambda b: b.update({field: None})))

    def test_authorization_rejected(self):
        req = self.capture(); req.add_header('Authorization', 'synthetic')
        self.reject(req)

    def test_unredirected_authorization_rejected(self):
        req = self.capture(); req.add_unredirected_header('Authorization', 'synthetic')
        self.reject(req)

    def test_wrong_url(self):
        req = self.capture(); req.full_url = 'http://invalid.example/v1/chat/completions'
        self.reject(req)

    def test_wrong_method(self):
        req = self.capture(); req.method = 'GET'; self.reject(req)

    def test_wrong_model(self):
        self.reject(self.mutate_body(lambda b: b.update(model='other')))

    def test_wrong_temperature(self):
        self.reject(self.mutate_body(lambda b: b.update(temperature=1)))

    def test_wrong_max_tokens(self):
        self.reject(self.mutate_body(lambda b: b.update(max_tokens=4097)))

    def test_temperature_bool(self):
        self.reject(self.mutate_body(lambda b: b.update(temperature=False)))

    def test_max_tokens_bool(self):
        self.reject(self.mutate_body(lambda b: b.update(max_tokens=True)))

    def test_thinking_drift(self):
        for value in (True, 0, None):
            self.reject(self.mutate_body(lambda b: b.update(chat_template_kwargs={'enable_thinking': value})))

    def test_user_drift(self):
        self.reject(self.mutate_body(lambda b: b['messages'][1].update(content='different')))

    def test_full_body_whitespace_drift(self):
        req = self.capture(); req.data += b' '; self.reject(req)

    def test_system_message_drift(self):
        self.reject(self.mutate_body(lambda b: b['messages'][0].update(content='different')))

    def test_message_shape(self):
        self.reject(self.mutate_body(lambda b: b['messages'].append({'role': 'user', 'content': 'extra'})))

    def test_duplicate_json_keys(self):
        req = self.capture(); req.data = req.data[:-1] + b',"model":"gw"}'
        self.reject(req)

    def test_nan_infinity(self):
        for value in (float('nan'), float('inf')):
            self.reject(self.mutate_body(lambda b: b.update(temperature=value)))

    def test_verified_observer_no_mutation_delegates_once(self):
        state, trial = self.started()
        request = self.capture()
        before = (request.data, tuple(request.header_items()), request.full_url)
        calls = []
        outcome = wrapper.admitted_open(request, lambda req, **k: calls.append(req) or 'fake',
                                        {'timeout': 120}, 'L02', self.registration, state, trial.trial_id)
        self.assertEqual(outcome, 'fake'); self.assertEqual(len(calls), 1)
        self.assertEqual(before, (request.data, tuple(request.header_items()), request.full_url))
        self.assertTrue(state.records[-1].request_emitted)
        with self.assertRaises(ValueError):
            wrapper.admitted_open(request, lambda *a, **k: calls.append(a), {'timeout': 120},
                                  'L02', self.registration, state, trial.trial_id)
        self.assertEqual(len(calls), 1)

    def test_instructions(self):
        l02 = (wrapper.HERE / 'instructions/L02.txt').read_text()
        l03 = (wrapper.HERE / 'instructions/L03.txt').read_text()
        for token in ('verbatim complete original EVENT line', 'exactly once', 'prose ONLY', 'EVT-/REQ-'):
            self.assertIn(token, l02)
        for token in ('no inventory evidence', 'One record, one', 'multiple semantic categories', 'exact source'):
            self.assertIn(token, l03)

    def test_instruction_target_ids_absent(self):
        for lane in ('L02', 'L03'):
            raw = (wrapper.HERE / 'instructions' / (lane + '.txt')).read_bytes()
            for target in (b'REC-F01', b'REC-F02', b'REC-F03', b'REC-I01', b'REC-H01', b'REC-U01', b'REC-V01'):
                self.assertNotIn(target, raw)
        self.assertIn(b'REC-F01', wrapper.user_message('L03'))

    def test_freeze_and_construction_cannot_read_gold(self):
        original = Path.open

        def guarded(path, *args, **kwargs):
            if path.resolve() == wrapper.GOLD.resolve():
                raise AssertionError('gold forbidden')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', guarded):
            wrapper.build_preregistration()
            wrapper.user_message('L03', self.registration['lanes']['L03'])
            self.capture('L03')

    def test_adapter_cannot_read_gold(self):
        raw = packet_raw()
        records = probe.parse_l03_fixture(wrapper.fixture_bytes('L03'))
        with patch.object(Path, 'open', side_effect=AssertionError('I/O forbidden')):
            # Adapter inputs are pre-read immutable bytes, no loader available.
            evaluation = probe.evaluate_l03_anchors_without_gold(probe.RawStdout(raw), records)
            self.assertTrue(evaluation.to_dict()['passed'])

    def test_metadata_hash_checked_before_parse(self):
        with patch.object(Path, 'read_bytes', return_value=b'{"target_ids": []}'), \
                patch.object(probe, '_json', side_effect=AssertionError('must not parse')) as parse:
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.load_target_metadata()
            parse.assert_not_called()

    def test_target_loader_runs_only_after_anchor_freeze(self):
        order = []
        anchors = probe.evaluate_l03_anchors_without_gold
        loader = wrapper.load_target_metadata

        def wrapped(*args):
            result = anchors(*args); order.append('frozen'); return result

        def load():
            self.assertEqual(order, ['frozen']); order.append('loaded'); return loader()

        with patch.object(probe, 'evaluate_l03_anchors_without_gold', wrapped), \
                patch.object(wrapper, 'load_target_metadata', load):
            self.assertTrue(wrapper.evaluate_raw('L03', packet_raw()).to_dict()['passed'])
        self.assertEqual(order, ['frozen', 'loaded'])

    def test_only_target_metadata_returned(self):
        self.assertEqual(set(wrapper.load_target_metadata()), {'target_ids'})

    def test_usage_complete(self):
        self.assertEqual(wrapper.validated_usage({'prompt_tokens': 10, 'completion_tokens': 2,
                                                 'total_tokens': 12})['total_tokens'], 12)

    def test_usage_missing_unknown(self):
        for value in (None, {}, {'total_tokens': 10}):
            self.assertIsNone(wrapper.validated_usage(value))

    def test_usage_incomplete_unknown(self):
        for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            value = {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}
            del value[name]; self.assertIsNone(wrapper.validated_usage(value))

    def test_usage_arithmetic_unknown(self):
        self.assertIsNone(wrapper.validated_usage({'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 13}))

    def test_usage_type_negative_unknown(self):
        for value in (True, -1, 1.0, '10'):
            self.assertIsNone(wrapper.validated_usage({'prompt_tokens': value, 'completion_tokens': 2, 'total_tokens': 12}))

    def test_transport_mapping(self):
        for code in ('NETWORK_PROVIDER_UNREACHABLE', 'PROVIDER_TIMEOUT', 'PROVIDER_RUNTIME_ERROR',
                     'PROVIDER_HTTP_ERROR', 'PROVIDER_RATE_LIMITED', 'AUTH_PROVIDER_REJECTED'):
            self.assertEqual(wrapper.failure_reason(code), 'TRANSPORT_OR_REQUEST_FAILURE')

    def test_model_length_mapping(self):
        self.assertEqual(wrapper.failure_reason('MODEL_OUTPUT_INCOMPLETE'), 'MODEL_OUTPUT_INCOMPLETE')

    def test_unknown_code_not_inferred(self):
        with self.assertRaises(wrapper.WrapperIntegrityFailure):
            wrapper.failure_reason('a timeout happened')

    def test_no_retry_api(self):
        self.assertFalse(any('retry' in name for name in dir(wrapper.Phase0Wrapper)))
        self.assertEqual(self.registration['max_requests_per_trial'], 1)
        self.assertIs(self.registration['retry'], False)

    def test_missing_usage_stops_all_after_emitted(self):
        state, trial = self.emitted()
        state.complete_evaluation(trial.trial_id, wrapper.evaluate_raw('L02', log_raw()), None)
        self.assertIsNone(state.complete_total)
        for _ in range(5):
            next_trial = state.begin_next()
            self.assertEqual(next_trial.primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')
            self.assertIsNone(next_trial.usage_total)

    def test_anchor_stage_rejected_by_accounting(self):
        state, trial = self.emitted('L03')
        anchors = probe.evaluate_l03_anchors_without_gold(probe.RawStdout(packet_raw()),
                                                        probe.parse_l03_fixture(wrapper.fixture_bytes('L03')))
        with self.assertRaises(ValueError):
            state.complete_evaluation(trial.trial_id, anchors, 10)

    def test_three_of_seven_never_passes(self):
        state, trial = self.emitted('L03')
        final = wrapper.evaluate_raw('L03', packet_raw(3))
        self.assertEqual(final.to_dict()['target_recall_numerator'], 3)
        record = state.complete_evaluation(trial.trial_id, final, 10)
        self.assertEqual(record.status, 'FAIL'); self.assertFalse(state.gate_passed)

    def test_complete_success_unknown_usage_halts(self):
        state, trial = self.emitted()
        raw = log_raw(); evaluation = wrapper.evaluate_raw('L02', raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preserved'; path.write_bytes(raw)
            record = wrapper.complete_success(state, trial.trial_id, path, evaluation, {})
        self.assertIsNone(record.usage_total)
        self.assertEqual(record.primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')
        self.assertEqual(state.begin_next().primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')

    def test_per_fixture_stop_retained(self):
        state, trial = self.emitted()
        state.complete_evaluation(trial.trial_id, wrapper.evaluate_raw('L02', b'{}'), 10)
        trial = state.begin_next(); self.assertEqual(trial.trial_id, 'L03-1')
        _, proof = wrapper.validate_request(self.capture('L03'), wrapper.user_message('L03'))
        state.mark_request_emitted(trial.trial_id, proof)
        state.complete_evaluation(trial.trial_id, wrapper.evaluate_raw('L03', packet_raw()), 10)
        self.assertEqual(state.begin_next().primary_failure_reason, 'NOT_STARTED_EARLY_FORMAT_GATE_FAIL')

    def test_honest_replay(self):
        for lane, raw in (('L02', log_raw()), ('L03', packet_raw())):
            evaluation = wrapper.evaluate_raw(lane, raw)
            self.assertEqual(wrapper.reconcile(lane, raw, evaluation), evaluation)

    def test_forged_consistent_evaluation_rejected(self):
        raw = b'{}'
        evaluation = wrapper.evaluate_raw('L02', raw)
        result = evaluation.to_dict()
        result.update(passed=True, primary_failure_reason='PASS', secondary_failure_reasons=[])
        forged = dataclasses.replace(evaluation, result_json=json.dumps(result, sort_keys=True))
        with self.assertRaises(wrapper.WrapperIntegrityFailure):
            wrapper.reconcile('L02', raw, forged)

    def test_modified_raw_detected(self):
        raw = log_raw(); evaluation = wrapper.evaluate_raw('L02', raw)
        with self.assertRaises(wrapper.WrapperIntegrityFailure):
            wrapper.reconcile('L02', raw + b' ', evaluation)

    def test_modified_fixture_detected(self):
        evaluation = wrapper.evaluate_raw('L02', log_raw())
        with patch.object(Path, 'read_bytes', return_value=b'changed fixture'):
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.reconcile('L02', evaluation.stdout.raw, evaluation)

    def test_modified_metadata_detected(self):
        evaluation = wrapper.evaluate_raw('L03', packet_raw())
        original = Path.read_bytes

        def changed(path):
            return b'{}' if path == wrapper.GOLD else original(path)

        with patch.object(Path, 'read_bytes', changed):
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.reconcile('L03', evaluation.stdout.raw, evaluation)

    def test_replay_cannot_mutate_original_state(self):
        state, trial = self.emitted('L03')
        raw = packet_raw(); evaluation = wrapper.evaluate_raw('L03', raw)
        before = (state.records, evaluation.result_json, evaluation.stdout)
        wrapper.reconcile('L03', raw, evaluation)
        self.assertEqual(before, (state.records, evaluation.result_json, evaluation.stdout))

    def test_reconciliation_before_accounting(self):
        state, trial = self.emitted()
        raw = log_raw(); final = wrapper.evaluate_raw('L02', raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'raw'; path.write_bytes(raw + b' ')
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.complete_success(state, trial.trial_id, path, final,
                                         {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10})
        self.assertEqual(state.records[-1].status, 'STARTED')

    def test_forgery_cannot_enter_accounting(self):
        state, trial = self.emitted()
        raw = b'{}'; evaluation = wrapper.evaluate_raw('L02', raw)
        result = evaluation.to_dict()
        result.update(passed=True, primary_failure_reason='PASS', secondary_failure_reasons=[])
        forged = dataclasses.replace(evaluation, result_json=json.dumps(result, sort_keys=True))
        before = state.records
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preserved'; path.write_bytes(raw)
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.complete_success(state, trial.trial_id, path, forged,
                                         {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10})
        self.assertEqual(state.records, before)

    def test_failed_replay_cannot_mutate_original_evaluation(self):
        raw = packet_raw(); evaluation = wrapper.evaluate_raw('L03', raw)
        before = (evaluation.stdout, evaluation.result_json)
        with self.assertRaises(wrapper.WrapperIntegrityFailure):
            wrapper.reconcile('L03', raw + b' ', evaluation)
        self.assertEqual(before, (evaluation.stdout, evaluation.result_json))

    def test_secret_publication_blocked_without_redaction(self):
        raw = b'{"secret": "SECRET_MARKER_SYNTHETIC"}'
        with patch.object(probe, 'evaluate_l02_without_gold', side_effect=AssertionError('no parse')):
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.evaluate_raw('L02', raw)
        captured = probe.RawStdout(raw)
        self.assertEqual(captured.raw, raw)
        self.assertEqual(captured.sha256, hashlib.sha256(raw).hexdigest())

    def test_registration_artifact_drift(self):
        changed = copy.deepcopy(self.registration)
        key = next(iter(changed['artifacts'])); changed['artifacts'][key] = '0' * 64
        with self.assertRaises(wrapper.WrapperIntegrityFailure):
            wrapper.verify_registration(changed)

    def test_registration_pin_checked_before_decoding(self):
        with patch.object(Path, 'read_bytes', return_value=b'{}'), \
                patch.object(probe, '_json', side_effect=AssertionError('do not decode')) as decode:
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.load_preregistration('0' * 64)
            decode.assert_not_called()

    def test_instruction_drift_before_request(self):
        original = Path.read_bytes
        path = wrapper.HERE / 'instructions/L02.txt'
        def changed(filename):
            raw = original(filename)
            return raw + b' ' if filename == path else raw
        with patch.object(Path, 'read_bytes', changed):
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                wrapper.user_message('L02', self.registration['lanes']['L02'])

    def test_secret_digest_precedes_scan_and_parse(self):
        observed = []
        real_scan = probe.scan_secret_markers
        raw = log_raw()
        def scan(value):
            # Inspect the already allocated RawStdout's sha field at scan time.
            import inspect
            owner = inspect.currentframe().f_back.f_locals['self']
            self.assertEqual(owner.sha256, wrapper.sha(raw))
            observed.append('scan')
            return real_scan(value)
        real_evaluate = probe.evaluate_l02_without_gold
        def evaluate(*args):
            self.assertEqual(observed, ['scan']); observed.append('parse')
            return real_evaluate(*args)
        with patch.object(probe, 'scan_secret_markers', scan), \
                patch.object(probe, 'evaluate_l02_without_gold', evaluate):
            wrapper.evaluate_raw('L02', raw)
        self.assertEqual(observed, ['scan', 'parse'])

    def test_l03_multimap_unchanged(self):
        self.assertEqual(probe.multimap_cardinality(probe.parse_l03_fixture(wrapper.fixture_bytes('L03'))),
                         {'records': 668, 'distinct_pairs': 534, 'ambiguous_pairs': 120,
                          'records_in_ambiguous_pairs': 254})

    def test_real_provider_transport_error_safe_code(self):
        def fake(req, original, options):
            raise urllib.error.URLError('synthetic transport failure')
        outcome = wrapper.invoke_cli('L02', wrapper.user_message('L02'), fake)
        self.assertEqual(outcome['exit'], 1)
        self.assertEqual(wrapper.failure_reason(outcome['safe_error_code']), 'TRANSPORT_OR_REQUEST_FAILURE')
        self.assertEqual(outcome['raw_stdout'], b'')
        self.assertIsNone(outcome['metadata'])

    def test_real_provider_length_error(self):
        class Response(io.BytesIO):
            status = 200
        raw = json.dumps({'choices': [{'finish_reason': 'length'}]}).encode()
        outcome = wrapper.invoke_cli('L03', wrapper.user_message('L03'),
                                     lambda req, original, options: Response(raw))
        self.assertEqual(outcome['safe_error_code'], 'MODEL_OUTPUT_INCOMPLETE')
        self.assertEqual(outcome['exit'], 1)
        self.assertIsNone(outcome['metadata'])

    def test_future_runner_fake_success_replay_and_accounting(self):
        from llmtool.providers.openai_compatible import OpenAICompatibleClient
        # Inject only fake instance opener; never original transport.
        original_init = OpenAICompatibleClient.__init__
        class Response(io.BytesIO):
            status = 200
        response = json.dumps({'model': 'gw', 'choices': [{'finish_reason': 'stop',
                              'message': {'content': log_raw().decode()}}],
                              'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}}).encode()
        calls = []
        def initialize(client, *args, **kwargs):
            original_init(client, *args, **kwargs)
            client.opener.open = lambda req, **kw: calls.append(req) or Response(response)
        runner = wrapper.Phase0Wrapper(self.registration)
        with patch.object(OpenAICompatibleClient, '__init__', initialize), tempfile.TemporaryDirectory() as directory:
            record = runner.run_next(directory)
            self.assertEqual(record.status, 'PASS'); self.assertEqual(record.usage_total, 10)
            self.assertEqual(len(calls), 1)
            path = Path(directory) / 'L02-1.stdout'
            self.assertEqual(path.read_bytes(), log_raw() + b'\n')
            self.assertEqual(record.raw_stdout_sha256, wrapper.sha(path.read_bytes()))

    def run_fake(self, lane, response=None, transport=False):
        from llmtool.providers.openai_compatible import OpenAICompatibleClient as Client
        original_init = Client.__init__
        class Response(io.BytesIO):
            status = 200
        runner = wrapper.Phase0Wrapper(self.registration)
        if lane == 'L03':
            state, trial = self.emitted('L02')
            state.complete_evaluation(trial.trial_id, wrapper.evaluate_raw('L02', log_raw()), 10)
            runner.accounting = state
        calls = []
        def initialize(client, *args, **kwargs):
            original_init(client, *args, **kwargs)
            def fake(req, **options):
                calls.append(req)
                if transport:
                    raise urllib.error.URLError('synthetic offline failure')
                return Response(json.dumps(response).encode())
            client.opener.open = fake
        with patch.object(Client, '__init__', initialize), tempfile.TemporaryDirectory() as directory:
            record = runner.run_next(directory)
        self.assertEqual(len(calls), 1)
        return runner, record

    def test_future_runner_fake_transport_unknown_halts(self):
        runner, record = self.run_fake('L02', transport=True)
        self.assertEqual(record.primary_failure_reason, 'TRANSPORT_OR_REQUEST_FAILURE')
        self.assertIsNone(record.usage_total)
        self.assertEqual(runner.accounting.begin_next().primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')

    def test_future_runner_fake_length_unknown_halts(self):
        runner, record = self.run_fake('L03', {'choices': [{'finish_reason': 'length'}]})
        self.assertEqual(record.primary_failure_reason, 'MODEL_OUTPUT_INCOMPLETE')
        self.assertIsNone(record.usage_total)
        self.assertEqual(runner.accounting.begin_next().primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')

    def test_future_runner_fake_inconsistent_usage_unknown(self):
        response = {'model': 'gw', 'choices': [{'finish_reason': 'stop', 'message': {'content': log_raw().decode()}}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 11}}
        runner, record = self.run_fake('L02', response)
        self.assertEqual(record.primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')
        self.assertIsNone(record.usage_total)
        self.assertEqual(runner.accounting.begin_next().status, 'NOT_STARTED')

    def test_future_runner_fake_l03_success(self):
        response = {'model': 'gw', 'choices': [{'finish_reason': 'stop', 'message': {'content': packet_raw().decode()}}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}}
        runner, record = self.run_fake('L03', response)
        self.assertEqual(record.status, 'PASS')
        self.assertEqual(record.usage_total, 10)

    def test_future_runner_fake_three_of_seven_fails(self):
        response = {'model': 'gw', 'choices': [{'finish_reason': 'stop', 'message': {'content': packet_raw(3).decode()}}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}}
        runner, record = self.run_fake('L03', response)
        self.assertEqual(record.status, 'FAIL')
        self.assertEqual(record.primary_failure_reason, 'TARGET_RECALL_BELOW_7_OF_7')
        self.assertFalse(runner.accounting.gate_passed)

    def test_future_runner_integrity_failure_halts_before_transport(self):
        altered = copy.deepcopy(self.registration)
        altered['lanes']['L02']['request_body_sha256'] = '0' * 64
        runner = wrapper.Phase0Wrapper(altered)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                runner.run_next(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertTrue(runner.halted)
            self.assertFalse(runner.accounting.records[-1].request_emitted)
            with self.assertRaises(wrapper.WrapperIntegrityFailure):
                runner.run_next(directory)


if __name__ == '__main__':
    unittest.main(verbosity=2)
