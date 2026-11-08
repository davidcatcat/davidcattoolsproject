"""Deterministic offline contract tests; existing fixtures only, no live I/O."""

import ast
import copy
import dataclasses
import hashlib
import inspect
import json
import pathlib
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'tools/llmtool'))
import format_probe as probe
from llmtool.context import parse_context_packet
from llmtool import terminal

FROZEN = HERE.parent / '20261006_round2/stage_a'
L02_BYTES = (FROZEN / 'L02_large_log/fixture.txt').read_bytes()
L03_BYTES = (FROZEN / 'L03_large_context/fixture.txt').read_bytes()
EVENTS = probe.parse_l02_fixture(L02_BYTES)
RECORDS = probe.parse_l03_fixture(L03_BYTES)
# Test response construction derives provenance from fixture, never gold.
NON_INVENTORY = tuple(r for r in RECORDS if not r.record_id.startswith('INV-'))


def stdout(value):
    return probe.RawStdout(json.dumps(value).encode('utf-8'))


def log_output():
    ordered = sorted(EVENTS, key=lambda r: (r.timestamp, r.line))
    return {'summary': 'Supplied operational observations.',
            'error_signatures': sorted(set(r.signature for r in EVENTS)),
            'timeline': [r.text for r in ordered], 'repeated_patterns': [],
            'evidence': [r.text for r in EVENTS], 'unknowns': [], 'strong_review_reasons': []}


def packet_output(records=NON_INVENTORY):
    return {'facts': [], 'evidence': [
        {'statement': 'Supplied observation with structured provenance.',
         'source': record.source, 'locator': record.locator} for record in records],
        'inferences': [], 'hypotheses': [], 'unknowns': [], 'verify_next': []}


def load_metadata():
    return json.loads((FROZEN / 'L03_large_context/gold.json').read_text())


class FakeRequest:
    def __init__(self):
        self.full_url = 'http://home-llm.example:8000/v1/chat/completions'
        self.method = 'POST'
        self.headers = [('Content-Type', 'application/json')]
        self.data = json.dumps({'model': 'gw', 'temperature': 0, 'max_tokens': 4096,
                                'chat_template_kwargs': {'enable_thinking': False}}).encode()

    def get_method(self):
        return self.method

    def header_items(self):
        return list(self.headers)


PROOF = probe.observe_request_without_mutation(FakeRequest())


def final_evaluation_for_trial(trial_id, failure_reason=None):
    lane = trial_id.split('-')[0]
    if lane == 'L02':
        value = log_output()
        if failure_reason == 'INVALID_CANONICAL_COVERAGE':
            value['timeline'] = []
        elif failure_reason not in (None,):
            raise ValueError('unsupported synthetic L02 failure')
        return probe.evaluate_l02_without_gold(stdout(value), EVENTS)
    if lane == 'L03':
        records = NON_INVENTORY[:3] if failure_reason == 'TARGET_RECALL_BELOW_7_OF_7' else NON_INVENTORY
        if failure_reason not in (None, 'TARGET_RECALL_BELOW_7_OF_7'):
            raise ValueError('unsupported synthetic L03 failure')
        anchors = probe.evaluate_l03_anchors_without_gold(stdout(packet_output(records)), RECORDS)
        return probe.evaluate_l03_target_recall_after_freeze(anchors, load_metadata)
    raise ValueError('unsupported lane')


class L02Tests(unittest.TestCase):
    def evaluate(self, value, records=EVENTS):
        return probe.evaluate_l02_without_gold(stdout(value), records).to_dict()

    def assert_coverage_fail(self, result):
        self.assertFalse(result['passed'])
        self.assertEqual(result['primary_failure_reason'], 'INVALID_CANONICAL_COVERAGE')

    def test_full_subset_with_unrelated_extras_passes(self):
        value = log_output()
        value['evidence'].append('WARN inventory check was deferred')
        value['timeline'].insert(0, 'INFO background connection was accepted')
        result = self.evaluate(value)
        self.assertTrue(result['passed'])
        self.assertEqual(result['evidence_extras'], value['evidence'][-1:])
        self.assertEqual(result['timeline_extras'], value['timeline'][:1])

    def test_colon_form_canonical_fields_are_not_harmless_extras(self):
        value = log_output()
        value['evidence'].append('event_id: EVT-T99 signature: UPSTREAM_TIMEOUT')
        result = self.evaluate(value)
        self.assert_coverage_fail(result)
        self.assertEqual(result['invalid_canonical_claims'][-1]['reason'],
                         'MALFORMED_OR_PARTIAL_EVENT')

    def test_benign_event_word_prose_is_extra(self):
        value = log_output()
        prose = 'Four EVENT lines were observed; no later EVENT lines.'
        value['evidence'].append(prose)
        result = self.evaluate(value)
        self.assertTrue(result['passed'])
        self.assertIn(prose, result['evidence_extras'])

    def test_fixture_event_comment_is_extra_not_canonical_claim(self):
        comment = next(line for line in L02_BYTES.decode().splitlines()
                       if 'Canonical EVENT' in line)
        value = log_output(); value['evidence'].append(comment)
        result = self.evaluate(value)
        self.assertTrue(result['passed'])
        self.assertIn(comment, result['evidence_extras'])

    def test_namespace_identifier_reference_in_extra_fails(self):
        for prose in ('EVT-T09 UPSTREAM_TIMEOUT on REQ-99 at 08:02:20Z',
                      'EVT-T01 REQ-17 timed out at 09:30:00Z',
                      'event EVT-T01 repeated'):
            with self.subTest(prose=prose):
                value = log_output(); value['evidence'].append(prose)
                result = self.evaluate(value)
                self.assert_coverage_fail(result)
                self.assertEqual(result['invalid_canonical_claims'][-1]['reason'],
                                 'NAMESPACE_IDENTIFIER_REFERENCE_IN_EXTRA')
                self.assertTrue(result['unknown_identifier_references'])

    def test_round2_recorded_extra_shape_is_nonfatal(self):
        # Not replay of unavailable raw stdout: fixture canonical lines plus the
        # existing recorded extras form a gold-independent offline counterexample.
        historical = json.loads((FROZEN / 'results.json').read_text())
        coverage = next(x['coverage'] for x in historical['company_attempts'] if x['fixture'] == 'L02')
        value = log_output()
        value['evidence'] += coverage['noncanonical_evidence_items']
        value['timeline'] += coverage['noncanonical_timeline_items']
        result = self.evaluate(value)
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['evidence_extras']), 6)
        self.assertEqual(len(result['timeline_extras']), 18)

    def test_empty_timeline_fails(self):
        value = log_output(); value['timeline'] = []
        result = self.evaluate(value)
        self.assert_coverage_fail(result)
        self.assertEqual(set(result['missing_timeline_ids']), {r.event_id for r in EVENTS})

    def test_partial_timeline_fails(self):
        value = log_output(); value['timeline'].pop()
        self.assert_coverage_fail(self.evaluate(value))

    def test_reordered_timeline_fails(self):
        value = log_output(); value['timeline'].reverse()
        result = self.evaluate(value)
        self.assert_coverage_fail(result)
        self.assertFalse(result['timeline_order_valid'])

    def test_duplicate_evidence_fails_independently(self):
        value = log_output(); value['evidence'].append(value['evidence'][0])
        result = self.evaluate(value)
        self.assert_coverage_fail(result)
        self.assertEqual(result['duplicate_evidence_ids'], [EVENTS[0].event_id])
        self.assertEqual(result['duplicate_timeline_ids'], [])

    def test_duplicate_timeline_fails_independently(self):
        value = log_output(); value['timeline'].append(value['timeline'][0])
        result = self.evaluate(value)
        self.assert_coverage_fail(result)
        self.assertEqual(result['duplicate_evidence_ids'], [])
        self.assertEqual(result['duplicate_timeline_ids'], [EVENTS[0].event_id])

    def test_one_occurrence_per_array_is_not_duplicate(self):
        result = self.evaluate(log_output())
        self.assertTrue(result['passed'])
        self.assertEqual(result['duplicate_evidence_ids'], [])
        self.assertEqual(result['duplicate_timeline_ids'], [])

    def wrong_field(self, original, replacement):
        for array in ('evidence', 'timeline'):
            with self.subTest(array=array):
                value = log_output()
                value[array][0] = value[array][0].replace(original, replacement)
                result = self.evaluate(value)
                self.assert_coverage_fail(result)
                self.assertEqual(result['invalid_canonical_claims'][0]['reason'], 'EVENT_FIELD_MISMATCH')

    def test_wrong_request_id(self):
        self.wrong_field('request_id=REQ-17', 'request_id=REQ-WRONG')

    def test_wrong_signature(self):
        self.wrong_field('signature=UPSTREAM_TIMEOUT', 'signature=UNSUPPORTED')

    def test_wrong_timestamp(self):
        self.wrong_field('08:00:20Z', '08:00:21Z')

    def test_fabricated_event_in_either_array_fails(self):
        for array in ('evidence', 'timeline'):
            with self.subTest(array=array):
                value = log_output()
                value[array].append(EVENTS[0].text.replace('EVT-T01', 'EVT-FABRICATED'))
                result = self.evaluate(value)
                self.assert_coverage_fail(result)
                self.assertNotIn('EVT-FABRICATED', result['validated_event_ids'])

    def test_partial_or_embedded_event_cannot_repair_coverage(self):
        for item in ('EVENT event_id=EVT-T01', 'quoted: ' + EVENTS[0].text,
                     EVENTS[0].text + ' extra prose', 'event_id=EVT-T01 was observed'):
            with self.subTest(item=item):
                value = log_output(); value['evidence'][0] = item
                result = self.evaluate(value)
                self.assert_coverage_fail(result)
                self.assertIn('EVT-T01', result['missing_evidence_ids'])
                self.assertEqual(result['invalid_canonical_claims'][0]['reason'],
                                 'MALFORMED_OR_PARTIAL_EVENT')

    def test_malformed_event_does_not_hide_among_extras(self):
        value = log_output(); value['evidence'].append('EVENT event_id=EVT-FABRICATED')
        self.assert_coverage_fail(self.evaluate(value))

    def test_extras_never_alter_counts(self):
        value = log_output(); value['evidence'] += ['Timeout observed', 'Timeout observed']
        value['timeline'] += ['background timeout observation']
        result = self.evaluate(value)
        self.assertEqual(result['canonical_signature_counts'],
                         {'UPSTREAM_TIMEOUT': 3, 'RECOVERY_OBSERVED': 1})
        self.assertTrue(result['passed'])

    def test_extras_never_become_anchors(self):
        value = log_output(); value['evidence'].append('unrelated identifier INVENTORY-9')
        result = self.evaluate(value)
        self.assertEqual({x['identifier'] for x in result['validated_anchors']},
                         {r.event_id for r in EVENTS})

    def test_extra_signature_recorded_nonfatal(self):
        value = log_output(); value['error_signatures'].append('UNRELATED_WARN')
        result = self.evaluate(value)
        self.assertTrue(result['passed'])
        self.assertEqual(result['extra_signatures'], ['UNRELATED_WARN'])

    def test_missing_required_signature_fails(self):
        value = log_output(); value['error_signatures'] = ['UNRELATED_WARN']
        self.assert_coverage_fail(self.evaluate(value))

    def test_missing_evidence_event_cannot_be_replaced_by_extra(self):
        value = log_output(); value['evidence'][0] = 'a timeout was seen'
        self.assert_coverage_fail(self.evaluate(value))

    def test_timestamp_source_line_tie_break(self):
        # In-memory counterexample only: no new persisted benchmark fixture.
        text = ('EVENT event_id=Z request_id=R signature=S ts=2026-10-06T08:00:00Z\n'
                'noise\nEVENT event_id=A request_id=R signature=S ts=2026-10-06T08:00:00Z\n')
        records = probe.parse_l02_fixture(text.encode())
        value = log_output(); value.update(evidence=[r.text for r in records],
            timeline=[r.text for r in records], error_signatures=['S'])
        self.assertTrue(self.evaluate(value, records)['passed'])
        value['timeline'].reverse()
        self.assert_coverage_fail(self.evaluate(value, records))

    def test_full_fixture_universe_not_signature_whitelist(self):
        extra = 'EVENT event_id=OTHER request_id=R signature=OTHER ts=2026-10-06T09:00:00Z\n'
        records = probe.parse_l02_fixture(L02_BYTES + extra.encode())
        self.assertEqual(len(records), 5)
        result = self.evaluate(log_output(), records)
        self.assertIn('OTHER', result['missing_evidence_ids'])

    def test_schema_invalid(self):
        for update in ({'unexpected': []}, {'summary': []}, {'evidence': [None]},
                       {'timeline': 'not an array'}, {'summary': '\x1b[31m'}):
            with self.subTest(update=update):
                value = log_output(); value.update(update)
                self.assertEqual(self.evaluate(value)['primary_failure_reason'], 'SCHEMA_OR_PARSE_FAILURE')

    def test_json_duplicate_key_and_fence_rejected(self):
        raw = json.dumps(log_output()).encode()
        for bad in (b'{"summary":"x",' + raw[1:], b'```json\n' + raw + b'\n```', b'\xff'):
            with self.subTest(bad=bad[:20]):
                result = probe.evaluate_l02_without_gold(probe.RawStdout(bad), EVENTS).to_dict()
                self.assertEqual(result['primary_failure_reason'], 'SCHEMA_OR_PARSE_FAILURE')


class L03Tests(unittest.TestCase):
    def anchors(self, value):
        return probe.evaluate_l03_anchors_without_gold(stdout(value), RECORDS)

    def grade(self, value):
        return probe.evaluate_l03_target_recall_after_freeze(self.anchors(value), load_metadata).to_dict()

    def test_unique_source_locator_anchor_passes(self):
        result = self.anchors(packet_output(NON_INVENTORY[:1])).to_dict()
        self.assertTrue(result['passed'])
        self.assertEqual(result['validated_unique_anchor_record_ids'], ['REC-F01'])

    def test_valid_a4_empty_evidence_fails(self):
        value = packet_output(())
        parse_context_packet(json.dumps(value))  # Valid existing A4 contract.
        result = self.anchors(value).to_dict()
        self.assertFalse(result['passed'])
        self.assertEqual(result['primary_failure_reason'], 'EMPTY_VALIDATED_ANCHOR_SET')

    def test_null_locator_fails(self):
        value = packet_output(); value['evidence'][0]['locator'] = None
        result = self.anchors(value).to_dict()
        self.assertEqual(result['primary_failure_reason'], 'UNKNOWN_STRUCTURED_ANCHOR')

    def test_absent_locator_fails(self):
        value = packet_output(); del value['evidence'][0]['locator']
        self.assertEqual(self.anchors(value).to_dict()['primary_failure_reason'], 'UNKNOWN_STRUCTURED_ANCHOR')

    def test_unknown_pair_fails(self):
        value = packet_output(); value['evidence'][0]['source'] = 'not-in-fixture'
        self.assertEqual(self.anchors(value).to_dict()['primary_failure_reason'], 'UNKNOWN_STRUCTURED_ANCHOR')

    def test_no_fuzzy_normalization(self):
        for source in (' metrics-baseline', 'metrics-baseline ', 'METRICS-BASELINE'):
            with self.subTest(source=source):
                value = packet_output(); value['evidence'][0]['source'] = source
                self.assertEqual(self.anchors(value).to_dict()['primary_failure_reason'], 'UNKNOWN_STRUCTURED_ANCHOR')

    def test_ambiguous_pair_fails(self):
        index = probe.build_l03_multimap(RECORDS)
        collision = next(v for v in index.values() if len(v) > 1)
        result = self.anchors(packet_output(collision[:1])).to_dict()
        self.assertEqual(result['primary_failure_reason'], 'AMBIGUOUS_STRUCTURED_ANCHOR')
        self.assertEqual(result['validated_anchors'], [])

    def test_duplicate_record_fails(self):
        value = packet_output(); value['evidence'].append(copy.deepcopy(value['evidence'][0]))
        result = self.anchors(value).to_dict()
        self.assertEqual(result['primary_failure_reason'], 'DUPLICATE_STRUCTURED_ANCHOR')
        self.assertEqual(result['duplicate_mapped_record_ids'], ['REC-F01'])

    def test_no_free_text_record_id_still_passes(self):
        value = packet_output()
        self.assertTrue(all('record_id' not in x['statement'] for x in value['evidence']))
        self.assertTrue(self.grade(value)['passed'])

    def test_misleading_free_text_cannot_override_unique_provenance(self):
        value = packet_output(NON_INVENTORY[:1])
        value['evidence'][0]['statement'] = 'record_id=REC-V01'
        result = self.anchors(value).to_dict()
        self.assertEqual(result['validated_unique_anchor_record_ids'], ['REC-F01'])
        self.assertEqual(self.grade(value)['target_recall_numerator'], 1)

    def test_free_text_cannot_resolve_ambiguous_pair(self):
        collision = next(v for v in probe.build_l03_multimap(RECORDS).values() if len(v) > 1)
        value = packet_output(collision[:1])
        value['evidence'][0]['statement'] = 'record_id=' + collision[0].record_id
        self.assertEqual(self.anchors(value).to_dict()['primary_failure_reason'], 'AMBIGUOUS_STRUCTURED_ANCHOR')

    def test_actual_multimap_collision_cardinality(self):
        self.assertEqual(probe.multimap_cardinality(RECORDS),
                         {'records': 668, 'distinct_pairs': 534,
                          'ambiguous_pairs': 120, 'records_in_ambiguous_pairs': 254})

    def test_three_of_seven_fails_readiness(self):
        value = packet_output(NON_INVENTORY[:3])
        value['facts'] = ['Three observations only.']
        result = self.grade(value)
        self.assertEqual(result['target_recall_numerator'], 3)
        self.assertEqual(result['primary_failure_reason'], 'TARGET_RECALL_BELOW_7_OF_7')

    def test_seven_of_seven_passes_readiness(self):
        result = self.grade(packet_output())
        self.assertTrue(result['passed'])
        self.assertEqual(result['target_recall_numerator'], 7)
        self.assertEqual(result['missing_target_record_ids'], [])

    def test_one_evidence_item_supports_multiple_categories(self):
        value = packet_output()
        value['facts'] = ['A supplied observation.']
        value['inferences'] = ['An inference with shared supporting evidence.']
        value['verify_next'] = ['Verify that same observation with additional evidence.']
        self.assertTrue(self.grade(value)['passed'])
        self.assertEqual(len(value['evidence']), 7)

    def test_two_category_specific_copies_are_still_duplicate(self):
        value = packet_output()
        duplicate = copy.deepcopy(value['evidence'][0]); duplicate['statement'] = 'Support for another category.'
        value['evidence'].append(duplicate)
        self.assertEqual(self.grade(value)['primary_failure_reason'], 'DUPLICATE_STRUCTURED_ANCHOR')

    def test_no_inventory_and_seven_unique_target_evidence_passes(self):
        value = packet_output()
        self.assertTrue(self.grade(value)['passed'])
        self.assertEqual(len(value['evidence']), 7)

    def test_ambiguous_inventory_cannot_satisfy_recall(self):
        collision = next(v for v in probe.build_l03_multimap(RECORDS).values() if len(v) > 1)
        result = self.grade(packet_output(collision[:1]))
        self.assertEqual(result['target_recall_numerator'], 0)
        self.assertIn('AMBIGUOUS_STRUCTURED_ANCHOR', result['secondary_failure_reasons'])

    def test_unique_inventory_is_not_usable_evidence(self):
        unique = next(v[0] for v in probe.build_l03_multimap(RECORDS).values()
                      if len(v) == 1 and v[0].record_id.startswith('INV-'))
        value = packet_output(); value['evidence'] += packet_output((unique,))['evidence']
        result = self.grade(value)
        self.assertFalse(result['passed'])
        self.assertEqual(result['inventory_evidence'], [7])
        self.assertEqual(result['target_recall_numerator'], 7)

    def test_parse_failure_primary(self):
        for raw in (b'not JSON', b'{}', b'\xff'):
            result = probe.evaluate_l03_anchors_without_gold(probe.RawStdout(raw), RECORDS).to_dict()
            self.assertEqual(result['primary_failure_reason'], 'SCHEMA_OR_PARSE_FAILURE')

    def test_unknown_precedes_ambiguous_duplicate_empty_recall(self):
        collision = next(v for v in probe.build_l03_multimap(RECORDS).values() if len(v) > 1)
        value = packet_output(collision[:1]); value['evidence'][0]['locator'] = 'missing'
        value['evidence'] += packet_output(collision[:1])['evidence']
        result = self.grade(value)
        self.assertEqual(result['primary_failure_reason'], 'UNKNOWN_STRUCTURED_ANCHOR')
        self.assertIn('AMBIGUOUS_STRUCTURED_ANCHOR', result['secondary_failure_reasons'])
        self.assertIn('EMPTY_VALIDATED_ANCHOR_SET', result['secondary_failure_reasons'])
        self.assertIn('TARGET_RECALL_BELOW_7_OF_7', result['secondary_failure_reasons'])


class AccountingTests(unittest.TestCase):
    def start(self, state):
        trial = state.begin_next()
        if trial.status == 'STARTED':
            state.mark_request_emitted(trial.trial_id, PROOF)
        return trial

    def complete(self, state, trial, reasons=(), usage=100):
        if reasons:
            primary = probe.primary_failure(reasons)
            if primary in ('TRANSPORT_OR_REQUEST_FAILURE', 'MODEL_OUTPUT_INCOMPLETE'):
                return state.complete_pre_output_failure(trial.trial_id, primary, usage)
            if primary in ('INVALID_CANONICAL_COVERAGE', 'TARGET_RECALL_BELOW_7_OF_7'):
                evaluation = final_evaluation_for_trial(trial.trial_id, primary)
                return state.complete_evaluation(trial.trial_id, evaluation, usage)
            raise ValueError('unsupported synthetic accounting failure')
        evaluation = final_evaluation_for_trial(trial.trial_id)
        return state.complete_evaluation(trial.trial_id, evaluation, usage)

    def test_frozen_order_and_six_request_max(self):
        state = probe.Phase0Accounting()
        for name in probe.TRIAL_ORDER:
            trial = self.start(state); self.assertEqual(trial.trial_id, name)
            self.complete(state, trial)
        self.assertTrue(state.gate_passed)
        self.assertEqual(sum(r.request_emitted for r in state.records), 6)
        self.assertEqual(state.complete_total, 600)
        with self.assertRaises(ValueError): state.begin_next()

    def test_one_request_per_trial(self):
        state = probe.Phase0Accounting(); trial = self.start(state)
        with self.assertRaises(ValueError): state.mark_request_emitted(trial.trial_id, PROOF)

    def test_no_retry_api_or_state(self):
        state = probe.Phase0Accounting()
        self.assertFalse(any('retry' in name.lower() for name in vars(state)))
        self.assertFalse(hasattr(state, 'retry'))
        trial = self.start(state)
        self.complete(state, trial, ('TRANSPORT_OR_REQUEST_FAILURE',))
        with self.assertRaises(ValueError): state.mark_request_emitted(trial.trial_id, PROOF)

    def test_l02_failure_stops_only_l02(self):
        state = probe.Phase0Accounting(); trial = self.start(state)
        self.complete(state, trial, ('INVALID_CANONICAL_COVERAGE',))
        for _ in range(5):
            trial = self.start(state)
            if trial.trial_id.startswith('L02'):
                self.assertEqual(trial.primary_failure_reason, 'NOT_STARTED_EARLY_FORMAT_GATE_FAIL')
                self.assertIsNone(trial.usage_total)
            else:
                self.assertEqual(trial.status, 'STARTED'); self.complete(state, trial)
        self.assertFalse(state.gate_passed)
        self.assertEqual(sum(r.request_emitted for r in state.records), 4)

    def test_l03_failure_stops_only_l03(self):
        state = probe.Phase0Accounting()
        self.complete(state, self.start(state))
        self.complete(state, self.start(state), ('MODEL_OUTPUT_INCOMPLETE',))
        for _ in range(4):
            trial = self.start(state)
            if trial.trial_id.startswith('L03'):
                self.assertEqual(trial.status, 'NOT_STARTED')
            else:
                self.assertEqual(trial.status, 'STARTED'); self.complete(state, trial)
        self.assertEqual(sum(r.request_emitted for r in state.records), 4)

    def test_unknown_consumed_usage_halts_all_remaining(self):
        state = probe.Phase0Accounting(); trial = self.start(state)
        self.complete(state, trial, ('TRANSPORT_OR_REQUEST_FAILURE',), None)
        self.assertIsNone(state.complete_total)
        self.assertIsNone(state.records[0].usage_total)
        self.assertEqual(state.records[0].primary_failure_reason, 'TRANSPORT_OR_REQUEST_FAILURE')
        for _ in range(5):
            trial = state.begin_next()
            self.assertEqual(trial.primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')
            self.assertIsNone(trial.usage_total)

    def test_missing_usage_after_success_is_not_pass(self):
        state = probe.Phase0Accounting(); trial = self.start(state)
        record = self.complete(state, trial, usage=None)
        self.assertEqual(record.status, 'FAIL')
        self.assertEqual(record.primary_failure_reason, 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN')

    def test_l03_admission_uses_59000_upper_bound(self):
        state = probe.Phase0Accounting()
        self.complete(state, self.start(state), usage=241001)
        record = state.begin_next()
        self.assertEqual(record.primary_failure_reason, 'COMPANY_TOKEN_CEILING_NOT_STARTED')
        self.assertFalse(record.request_emitted)
        self.assertIsNone(record.usage_total)

    def test_l02_admission_uses_35000_upper_bound(self):
        state = probe.Phase0Accounting()
        self.complete(state, self.start(state), usage=100)
        self.complete(state, self.start(state), usage=264901)
        record = state.begin_next()
        self.assertEqual(record.primary_failure_reason, 'COMPANY_TOKEN_CEILING_NOT_STARTED')

    def test_exact_ceiling_admission_allowed(self):
        state = probe.Phase0Accounting()
        self.complete(state, self.start(state), usage=241000)
        self.assertEqual(state.begin_next().status, 'STARTED')

    def test_pre_request_failure_preserves_unknown_without_global_stop(self):
        state = probe.Phase0Accounting(); trial = state.begin_next()
        record = state.complete_pre_output_failure(trial.trial_id, 'TRANSPORT_OR_REQUEST_FAILURE')
        self.assertFalse(record.request_emitted)
        self.assertIsNone(record.request_proof_json)
        self.assertIsNone(record.usage_total)
        self.assertEqual(state.begin_next().trial_id, 'L03-1')

    def test_unemitted_success_rejected(self):
        state = probe.Phase0Accounting(); trial = state.begin_next()
        with self.assertRaises(ValueError): state.complete_evaluation(trial.trial_id, final_evaluation_for_trial(trial.trial_id))

    def test_out_of_order_completion_rejected(self):
        state = probe.Phase0Accounting(); self.start(state)
        with self.assertRaises(ValueError): state.complete_evaluation('L03-1', final_evaluation_for_trial('L03-1'), 100)
        with self.assertRaises(ValueError): state.begin_next()

    def test_missing_request_proof_rejected(self):
        state = probe.Phase0Accounting(); trial = state.begin_next()
        with self.assertRaises(TypeError): state.mark_request_emitted(trial.trial_id, {})

    def test_boolean_coercion_cannot_forge_observer_proof(self):
        state = probe.Phase0Accounting(); trial = state.begin_next()
        proof = dataclasses.replace(PROOF, enable_thinking=0)
        with self.assertRaises(ValueError): state.mark_request_emitted(trial.trial_id, proof)

    def test_invalid_usage_does_not_mutate_accounting(self):
        for invalid in (-1, True, 1.5, '100'):
            state = probe.Phase0Accounting(); trial = self.start(state)
            with self.assertRaises(ValueError): self.complete(state, trial, usage=invalid)
            self.assertEqual(state.known_cumulative, 0)

    def test_actual_usage_over_ceiling_is_retained_not_clamped(self):
        state = probe.Phase0Accounting(); trial = self.start(state)
        self.complete(state, trial, usage=300001)
        self.assertEqual(state.complete_total, 300001)
        self.assertEqual(state.begin_next().primary_failure_reason, 'COMPANY_TOKEN_CEILING_NOT_STARTED')
        self.assertFalse(state.gate_passed)

    def test_failure_precedence_keeps_secondary(self):
        reasons = ('SCHEMA_OR_PARSE_FAILURE', 'MODEL_OUTPUT_INCOMPLETE',
                   'TRANSPORT_OR_REQUEST_FAILURE')
        self.assertEqual(probe.primary_failure(reasons), 'TRANSPORT_OR_REQUEST_FAILURE')

    def test_accounting_rejects_anchor_stage_l03_evaluation(self):
        state = probe.Phase0Accounting()
        self.complete(state, self.start(state))
        trial = self.start(state)
        anchors = probe.evaluate_l03_anchors_without_gold(
            stdout(packet_output(NON_INVENTORY[:3])), RECORDS)
        self.assertTrue(anchors.to_dict()['passed'])
        with self.assertRaisesRegex(ValueError, 'final L03 post-freeze recall'):
            state.complete_evaluation(trial.trial_id, anchors, 100)

    def test_accounting_rejects_wrong_lane_evaluation(self):
        state = probe.Phase0Accounting()
        trial = self.start(state)
        wrong = probe.evaluate_l03_target_recall_after_freeze(
            probe.evaluate_l03_anchors_without_gold(stdout(packet_output()), RECORDS),
            load_metadata)
        with self.assertRaisesRegex(ValueError, 'lane'):
            state.complete_evaluation(trial.trial_id, wrong, 100)

    def test_accounting_cannot_override_failed_evaluation_with_empty_reasons(self):
        state = probe.Phase0Accounting()
        self.complete(state, self.start(state))
        trial = self.start(state)
        failed = final_evaluation_for_trial(trial.trial_id, 'TARGET_RECALL_BELOW_7_OF_7')
        record = state.complete_evaluation(trial.trial_id, failed, 100)
        self.assertEqual(record.status, 'FAIL')
        self.assertEqual(record.primary_failure_reason, 'TARGET_RECALL_BELOW_7_OF_7')
        self.assertFalse(state.gate_passed)

    def test_request_proof_must_be_typed_observation_not_dict(self):
        state = probe.Phase0Accounting(); trial = state.begin_next()
        forged = dataclasses.asdict(PROOF)
        with self.assertRaises(TypeError):
            state.mark_request_emitted(trial.trial_id, forged)

    def test_three_of_seven_cannot_make_accounting_gate_pass(self):
        state = probe.Phase0Accounting()
        while len(state.records) < len(probe.TRIAL_ORDER):
            trial = self.start(state)
            if trial.status != 'STARTED':
                continue
            if trial.trial_id.startswith('L03'):
                evaluation = final_evaluation_for_trial(
                    trial.trial_id, 'TARGET_RECALL_BELOW_7_OF_7')
            else:
                evaluation = final_evaluation_for_trial(trial.trial_id)
            state.complete_evaluation(trial.trial_id, evaluation, 100)
        self.assertFalse(state.gate_passed)
        self.assertTrue(any(
            r.primary_failure_reason == 'TARGET_RECALL_BELOW_7_OF_7'
            for r in state.records))


class RawObserverIsolationTests(unittest.TestCase):
    def test_raw_stdout_digest_before_parse_and_scan(self):
        events = []
        digest = hashlib.sha256
        def observed_digest(raw):
            events.append('digest'); return digest(raw)
        scanner = probe.scan_secret_markers
        def observed_scan(raw):
            events.append('scan'); return scanner(raw)
        with patch.object(probe.hashlib, 'sha256', observed_digest), patch.object(
                probe, 'scan_secret_markers', observed_scan), patch.object(
                probe, '_parse_log', side_effect=lambda raw: events.append('parse') or log_output()):
            capture = probe.RawStdout(b'unchanged stdout')
            probe.evaluate_l02_without_gold(capture, EVENTS)
        self.assertEqual(events[:3], ['digest', 'scan', 'parse'])
        self.assertEqual(capture.raw, b'unchanged stdout')

    def test_raw_stdout_immutable_and_digest_original_bytes(self):
        raw = b'raw\r\nbytes  \n'
        capture = probe.RawStdout(raw)
        self.assertEqual(capture.sha256, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(dataclasses.FrozenInstanceError): capture.raw = b'edited'
        with self.assertRaises(TypeError): probe.RawStdout(bytearray(raw))

    def test_secret_scan_blocks_publication_without_redaction(self):
        for raw in (b'SECRET_MARKER_SYNTHETIC_ONLY', b'api_key=SYNTHETIC_ONLY',
                    b'Authorization: Bearer SYNTHETIC_ONLY',
                    b'sk-SYNTHETIC_ONLY_123456', b'-----BEGIN PRIVATE KEY-----'):
            with self.subTest(raw=raw):
                capture = probe.RawStdout(raw)
                self.assertFalse(capture.publication_allowed)
                self.assertEqual(capture.raw, raw)
                self.assertEqual(capture.sha256, hashlib.sha256(raw).hexdigest())

    def test_secret_free_output_publishable(self):
        self.assertTrue(stdout(packet_output()).publication_allowed)

    def test_observer_validates_without_mutation(self):
        request = FakeRequest(); before = copy.deepcopy(vars(request))
        proof = probe.observe_request_without_mutation(request)
        self.assertEqual(vars(request), before)
        self.assertEqual(proof.request_body_sha256, hashlib.sha256(request.data).hexdigest())
        self.assertIs(proof.enable_thinking, False)
        self.assertFalse(proof.authorization_header_present)

    def test_observer_rejects_each_wrong_body_field_without_mutation(self):
        for change in ({'model': 'other'}, {'temperature': 1}, {'temperature': False},
                       {'max_tokens': 8192}, {'chat_template_kwargs': {'enable_thinking': True}},
                       {'chat_template_kwargs': {'enable_thinking': 0}}):
            request = FakeRequest(); body = json.loads(request.data); body.update(change)
            request.data = json.dumps(body).encode(); before = copy.deepcopy(vars(request))
            with self.assertRaises(ValueError): probe.observe_request_without_mutation(request)
            self.assertEqual(vars(request), before)

    def test_observer_rejects_authorization_any_case(self):
        for header in ('Authorization', 'authorization', 'AUTHORIZATION'):
            request = FakeRequest(); request.headers.append((header, 'SYNTHETIC_ONLY'))
            before = copy.deepcopy(vars(request))
            with self.assertRaises(ValueError): probe.observe_request_without_mutation(request)
            self.assertEqual(vars(request), before)

    def test_observer_rejects_wrong_endpoint_and_method(self):
        for field, value in (('full_url', 'http://example.invalid'), ('method', 'GET')):
            request = FakeRequest(); setattr(request, field, value)
            with self.assertRaises(ValueError): probe.observe_request_without_mutation(request)

    def test_observer_rejects_nonobject_json(self):
        request = FakeRequest(); request.data = b'[]'
        with self.assertRaisesRegex(ValueError, 'REQUEST_OBSERVER_CONTRACT_FAILURE'):
            probe.observe_request_without_mutation(request)

    def test_adapter_source_guard_no_gold_io_or_execution(self):
        tree = ast.parse((HERE / 'format_probe.py').read_text())
        allowed_top_level = {'hashlib', 'json', 're', 'collections', 'dataclasses', 'datetime',
                             'llmtool'}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [x.name for x in node.names] if isinstance(node, ast.Import) else [node.module]
                for module in modules:
                    self.assertIn(module.split('.')[0], allowed_top_level)
                self.assertFalse(any('provider' in m for m in modules))
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name): self.assertNotIn(node.func.id, ('open', 'exec', 'eval'))
                if isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ('read_text', 'read_bytes', 'write_text', 'write_bytes', 'send'))
        functions = (probe.parse_l02_fixture, probe.evaluate_l02_without_gold,
                     probe.parse_l03_fixture, probe.build_l03_multimap,
                     probe.evaluate_l03_anchors_without_gold)
        for function in functions:
            params = inspect.signature(function).parameters
            self.assertFalse(any('gold' in p or 'target' in p for p in params))
            self.assertNotIn('load_evaluation_metadata', inspect.getsource(function))

    def test_adapter_runtime_io_guard(self):
        with patch('builtins.open', side_effect=AssertionError('gold/file I/O forbidden')), patch(
                'pathlib.Path.open', side_effect=AssertionError('gold/file I/O forbidden')):
            records = probe.parse_l03_fixture(L03_BYTES)
            result = probe.evaluate_l03_anchors_without_gold(stdout(packet_output()), records)
            self.assertTrue(result.to_dict()['passed'])
            self.assertTrue(probe.evaluate_l02_without_gold(stdout(log_output()), EVENTS).to_dict()['passed'])

    def test_post_freeze_loader_order_and_no_mutation(self):
        capture = stdout(packet_output()); frozen = probe.evaluate_l03_anchors_without_gold(capture, RECORDS)
        state = probe.Phase0Accounting(); state.begin_next()
        prior_state = state.records; prior_result = frozen.result_json
        def loader():
            self.assertEqual(capture.sha256, hashlib.sha256(capture.raw).hexdigest())
            self.assertEqual(frozen.result_json, prior_result)
            return load_metadata()
        graded = probe.evaluate_l03_target_recall_after_freeze(frozen, loader)
        self.assertTrue(graded.to_dict()['passed'])
        self.assertEqual(frozen.result_json, prior_result)
        self.assertEqual(state.records, prior_state)
        self.assertIs(graded.stdout, capture)
        detached = frozen.to_dict(); detached['validated_anchors'].clear()
        self.assertEqual(len(frozen.to_dict()['validated_anchors']), 7)

    def test_replay_does_not_change_original_verdict(self):
        capture = stdout(packet_output(NON_INVENTORY[:3]))
        original = probe.evaluate_l03_target_recall_after_freeze(
            probe.evaluate_l03_anchors_without_gold(capture, RECORDS), load_metadata)
        prior = original.result_json
        replay = probe.evaluate_l03_anchors_without_gold(capture, RECORDS)
        self.assertTrue(replay.to_dict()['passed'])
        self.assertEqual(original.result_json, prior)
        self.assertFalse(original.to_dict()['passed'])

    def test_post_freeze_rejects_unfrozen_or_regraded_result(self):
        called = []
        with self.assertRaises(TypeError):
            probe.evaluate_l03_target_recall_after_freeze({}, lambda: called.append(1))
        self.assertEqual(called, [])
        frozen = probe.evaluate_l03_anchors_without_gold(stdout(packet_output()), RECORDS)
        graded = probe.evaluate_l03_target_recall_after_freeze(frozen, load_metadata)
        with self.assertRaises(ValueError):
            probe.evaluate_l03_target_recall_after_freeze(graded, load_metadata)

    def test_recall_metadata_requires_seven_distinct_targets(self):
        frozen = probe.evaluate_l03_anchors_without_gold(stdout(packet_output()), RECORDS)
        for ids in ([], ['X'] * 7, ['X'] * 6, [{}] * 7):
            with self.assertRaises(ValueError):
                probe.evaluate_l03_target_recall_after_freeze(frozen, lambda: {'target_ids': ids})

    def test_frozen_fixture_digests(self):
        probe.verify_fixture_digest('L02', L02_BYTES)
        probe.verify_fixture_digest('L03', L03_BYTES)
        with self.assertRaisesRegex(ValueError, 'PREREGISTRATION_DRIFT'):
            probe.verify_fixture_digest('L03', L03_BYTES + b'\n')

    def test_failure_precedence_full_order(self):
        for i, reason in enumerate(probe.FAILURE_PRECEDENCE):
            self.assertEqual(probe.primary_failure(probe.FAILURE_PRECEDENCE[i:]), reason)
        self.assertEqual(probe.primary_failure(()), 'PASS')
        with self.assertRaises(ValueError): probe.primary_failure(('unsupported',))


if __name__ == '__main__':
    # Defense-in-depth: even an accidental network/process path fails this run.
    with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), patch.object(
            subprocess, 'Popen', side_effect=AssertionError('process execution forbidden')):
        unittest.main(verbosity=2)
