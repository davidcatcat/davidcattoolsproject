"""Offline preregistration checks, not measured outputs or a semantic quality gate.

Schema/gold artifact checks occur separately from helper invocations. Helpers only
receive fixture bytes and artificial company-shaped examples derived from syntax.
"""

import ast
import builtins
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import coverage
import generator
import selector

ROOT = Path(__file__).resolve().parent


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(lane):
    return (ROOT / generator.CONFIG[lane][0] / 'fixture.txt').read_bytes()


def sample(lane, raw):
    records = coverage.parse_fixture(lane, raw)
    if lane == 'L01':
        groups = []
        for signature in sorted(set(r['signature'] for r in records)):
            rows = [r for r in records if r['signature'] == signature]
            groups.append({'category': 'unknown', 'tests': [r['identifier'] for r in rows],
                           'files': sorted(set(r['file'] for r in rows)), 'evidence': [r['text'] for r in rows]})
        return {'summary': 'Artificial syntax-only self-check', 'failure_groups': groups,
                'unknowns': [], 'strong_review_reasons': []}
    if lane == 'L02':
        lines = [r['text'] for r in records if r['signature'] in coverage.COVERABLE_SIGNATURES]
        return {'summary': 'Artificial syntax-only self-check',
                'error_signatures': list(coverage.COVERABLE_SIGNATURES), 'timeline': lines,
                'evidence': lines, 'repeated_patterns': [], 'unknowns': [], 'strong_review_reasons': []}
    relevant = [r for r in records if ' topic=upload-window ' in r['text']]
    return {**{k: [] for k in ('facts', 'inferences', 'hypotheses', 'unknowns', 'verify_next')},
            'evidence': [{'statement': 'record_id=' + r['identifier'], 'source': r['source'],
                          'locator': r['locator']} for r in relevant]}


class GeneratorChecks(unittest.TestCase):
    def test_byte_identical_regeneration_in_temp_directory(self):
        with tempfile.TemporaryDirectory(prefix='llmtool-preregeneration-') as name:
            output = Path(name)
            for lane, (directory, size, seed) in generator.CONFIG.items():
                regenerated = generator.generate(lane, seed)
                path = output / (lane + '.txt')
                path.write_bytes(regenerated)
                self.assertEqual(path.read_bytes(), fixture(lane))
                self.assertEqual(len(regenerated), size)
                self.assertTrue(regenerated.endswith(b'\n'))

    def test_different_seed_changes_only_generation(self):
        for lane, (_, _, seed) in generator.CONFIG.items():
            self.assertNotEqual(generator.generate(lane, seed + 1), fixture(lane))
            self.assertEqual(coverage.parse_fixture(lane, generator.generate(lane, seed + 1))[0]['identifier'],
                             coverage.parse_fixture(lane, fixture(lane))[0]['identifier'])

    def test_target_counts_distribution_and_timestamp_order(self):
        for lane, count in [('L01', 4), ('L02', 4), ('L03', 7)]:
            rows = coverage.parse_fixture(lane, fixture(lane))
            targets = rows if lane != 'L03' else [r for r in rows if ' topic=upload-window ' in r['text']]
            self.assertEqual(len(targets), count)
            positions = [fixture(lane).index(r['text'].encode()) for r in targets]
            self.assertTrue(all(b - a > len(fixture(lane)) // (count + 2) for a, b in zip(positions, positions[1:])))
        events = coverage.parse_fixture('L02', fixture('L02'))
        self.assertEqual([r['ts'] for r in events], sorted(r['ts'] for r in events))

    def test_helpers_cannot_read_or_receive_grading_input(self):
        functions = [generator.generate, generator.targets, generator.noise, coverage.parse_fixture,
                     coverage.test_coverage, coverage.log_coverage, coverage.context_coverage, selector.select]
        for function in functions:
            self.assertFalse(any('gold' in name for name in inspect.signature(function).parameters))
        for module in (generator, coverage, selector):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn('gold.json', node.value)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names]
                    self.assertFalse(any('gold' in name for name in names))
        original_open = builtins.open
        original_read_bytes, original_read_text = Path.read_bytes, Path.read_text
        def guard(path, *args, **kwargs):
            if str(path).endswith('gold.json'):
                raise AssertionError('grading read in helper execution')
            return original_open(path, *args, **kwargs)
        def bytes_guard(path, *args, **kwargs):
            if path.name == 'gold.json':
                raise AssertionError('grading read in helper execution')
            return original_read_bytes(path, *args, **kwargs)
        def text_guard(path, *args, **kwargs):
            if path.name == 'gold.json':
                raise AssertionError('grading read in helper execution')
            return original_read_text(path, *args, **kwargs)
        with patch('builtins.open', guard), patch.object(Path, 'read_bytes', bytes_guard), patch.object(Path, 'read_text', text_guard):
            for lane in generator.CONFIG:
                raw = generator.generate(lane)
                result = {'L01': coverage.test_coverage, 'L02': coverage.log_coverage,
                          'L03': coverage.context_coverage}[lane](raw, sample(lane, raw))
                selector.select(raw, result['validated_anchors'])


class CoverageChecks(unittest.TestCase):
    def test_l01_complete_and_missing(self):
        raw = fixture('L01'); company = sample('L01', raw)
        self.assertTrue(coverage.test_coverage(raw, company)['failure_id_coverage_complete'])
        omitted = company['failure_groups'].pop()
        result = coverage.test_coverage(raw, company)
        self.assertEqual(result['missing_failure_ids'], sorted(omitted['tests']))
        self.assertFalse(result['failure_id_coverage_complete'])

    def test_l01_duplicate_unexpected_and_file_membership(self):
        raw = fixture('L01'); company = sample('L01', raw)
        group = company['failure_groups'][0]
        group['tests'] += [group['tests'][0], 'tests/missing.py::test_fabricated']
        group['files'] = ['tests/missing.py']
        result = coverage.test_coverage(raw, company)
        self.assertEqual(len(result['duplicate_failure_ids']), 1)
        self.assertEqual(len(result['unexpected_failure_ids']), 1)
        self.assertTrue(result['file_membership_errors'])
        self.assertFalse(result['failure_id_coverage_complete'])

    def test_l02_counts_and_order(self):
        raw = fixture('L02'); company = sample('L02', raw)
        result = coverage.log_coverage(raw, company)
        self.assertEqual(result['fixture_signature_counts'], {'UPSTREAM_TIMEOUT': 3, 'RECOVERY_OBSERVED': 1})
        self.assertTrue(result['event_coverage_complete'])
        company['timeline'].reverse()
        self.assertFalse(coverage.log_coverage(raw, company)['timeline_identifier_order_valid'])

    def test_l02_omission_duplicate_unknown_mismatch_and_prose(self):
        raw = fixture('L02')
        for mutation in ('missing', 'duplicate', 'unknown', 'mismatch', 'prose'):
            company = sample('L02', raw)
            if mutation == 'missing': company['evidence'].pop()
            elif mutation == 'duplicate': company['evidence'].append(company['evidence'][0])
            elif mutation == 'unknown': company['evidence'][0] = company['evidence'][0].replace('EVT-T01', 'EVT-FAKE')
            elif mutation == 'mismatch': company['evidence'][0] = company['evidence'][0].replace('REQ-17', 'REQ-FAKE')
            else: company['evidence'] = ['There were 3 UPSTREAM_TIMEOUT events']
            result = coverage.log_coverage(raw, company)
            self.assertFalse(result['event_coverage_complete'], mutation)

    def test_l03_membership_and_selection_only(self):
        raw = fixture('L03'); company = sample('L03', raw)
        result = coverage.context_coverage(raw, company)
        self.assertTrue(result['citation_membership_valid'])
        self.assertIsNone(result['selected_ranges_contain_cited_records'])
        manifest, _ = selector.select(raw, result['validated_anchors'])
        self.assertTrue(coverage.context_coverage(raw, company, manifest['selected_ranges'])['selected_ranges_contain_cited_records'])
        self.assertFalse(coverage.context_coverage(raw, company, [])['selected_ranges_contain_cited_records'])
        self.assertNotIn('semantic_completeness', result)

    def test_l03_unknown_duplicate_and_mismatched_source_locator(self):
        raw = fixture('L03')
        for mutation in ('unknown', 'duplicate', 'source', 'locator'):
            company = sample('L03', raw)
            if mutation == 'unknown': company['evidence'][0]['statement'] = 'record_id=REC-FAKE'
            elif mutation == 'duplicate': company['evidence'].append(deepcopy(company['evidence'][0]))
            else: company['evidence'][0][mutation] = 'fabricated'
            self.assertFalse(coverage.context_coverage(raw, company)['citation_membership_valid'], mutation)


class SelectorChecks(unittest.TestCase):
    @staticmethod
    def synthetic(positions, size=12000):
        lines = [b'ordinary line\n'] * 150
        for i, position in enumerate(positions):
            lines[position - 1] = ('FAILURE test_id=T%d file=x.py kind=FAILED signature=X\n' % i).encode()
        raw = b''.join(lines)
        return raw + b'#' + b' ' * (size - len(raw) - 2) + b'\n'

    def test_two_line_padding_merge_gap_and_source_sort(self):
        raw = self.synthetic([20, 27, 35])
        anchors = coverage.anchors('L01', ['T2', 'T0', 'T1'])
        manifest, selected = selector.select(raw, anchors)
        self.assertEqual([(r['start_line'], r['end_line']) for r in manifest['selected_ranges']], [(18, 29), (33, 37)])
        self.assertEqual(selected, b''.join(raw[r['start_byte']:r['end_byte_exclusive']] for r in manifest['selected_ranges']))
        self.assertEqual(manifest, selector.select(raw, anchors[::-1])[0])

    def test_manifest_and_raw_digests(self):
        raw = self.synthetic([20])
        manifest, selected = selector.select(raw, coverage.anchors('L01', ['T0']))
        digest_field = manifest.pop('manifest_sha256')
        self.assertEqual(digest_field, digest(selector.canonical_json(manifest)))
        self.assertEqual(manifest['selected_raw_sha256'], digest(selected))

    def test_unknown_and_noncanonical_anchor_rejected(self):
        raw = self.synthetic([20])
        for anchor in ({'lane': 'L01', 'identifier': 'T0-prefix'}, {'lane': 'L01', 'identifier': 'T0', 'line': 20}):
            with self.assertRaises(ValueError): selector.select(raw, [anchor])

    def test_32_premerge_limit_without_silent_truncation(self):
        raw = self.synthetic(list(range(10, 109, 3)), 100000)
        manifest, _ = selector.select(raw, coverage.anchors('L01', ['T%d' % i for i in range(32)]))
        self.assertEqual(manifest['pre_merge_range_count'], 32)
        with self.assertRaisesRegex(ValueError, 'more than 32'):
            selector.select(raw, coverage.anchors('L01', ['T%d' % i for i in range(33)]))

    def test_exact_cap_boundary_and_one_byte_over(self):
        # First anchor selects exactly the first three lines (two after, none before).
        selected = b'FAILURE test_id=T0 file=x.py kind=FAILED signature=X\nx\ny\n'
        exact = selected + b'#' + b' ' * (len(selected) * 4 - 2) + b'\n'
        manifest, raw = selector.select(exact, coverage.anchors('L01', ['T0']))
        self.assertEqual(len(raw), manifest['raw_cap_bytes'])
        self.assertEqual(raw, selected)
        with self.assertRaisesRegex(ValueError, 'exceeds cap'):
            selector.select(exact[:-2] + b'\n', coverage.anchors('L01', ['T0']))

    def test_cap_exceeded_and_whole_fixture_rejected(self):
        raw = b'FAILURE test_id=T0 file=x.py kind=FAILED signature=X\n'
        with self.assertRaisesRegex(ValueError, 'exceeds cap'):
            selector.select(raw, coverage.anchors('L01', ['T0']))

    def test_caps_on_all_fixtures_and_no_manual_expansion(self):
        for lane, expected in [('L01', 9830), ('L02', 13107), ('L03', 26214)]:
            raw = fixture(lane)
            company = sample(lane, raw)
            result = {'L01': coverage.test_coverage, 'L02': coverage.log_coverage,
                      'L03': coverage.context_coverage}[lane](raw, company)
            manifest, selected = selector.select(raw, result['validated_anchors'])
            self.assertEqual(manifest['raw_cap_bytes'], expected)
            self.assertLessEqual(len(selected), expected)
            self.assertNotEqual(selected, raw)
            self.assertEqual(manifest['pre_merge_range_count'], len(result['validated_anchors']))
            empty, no_bytes = selector.select(raw, [])
            self.assertEqual(no_bytes, b'')
            self.assertEqual(empty['selected_ranges'], [])


class ArtifactChecks(unittest.TestCase):
    def test_schema_metaschemas_and_structured_example_validation(self):
        import jsonschema
        for lane, (directory, _, _) in generator.CONFIG.items():
            schema = json.loads((ROOT / directory / 'output_schema.json').read_text())
            jsonschema.Draft202012Validator.check_schema(schema)
            if lane == 'L01':
                value = {'summary': 'synthetic check', 'failure_count': 0, 'group_count': 0, 'groups': [], 'unknowns': []}
            elif lane == 'L02':
                value = {'summary': 'synthetic check', 'signature_counts': [], 'events': [], 'repeated_patterns': [], 'unknowns': []}
            else:
                value = sample(lane, fixture(lane))
            jsonschema.validate(value, schema)
            invalid = {**value, 'unexpected': True}
            with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(invalid, schema)
            if lane != 'L03':
                invalid = deepcopy(value)
                if lane == 'L01': invalid['failure_count'] = '4'
                else: invalid['signature_counts'] = [{'signature': 'X', 'occurrences': '3'}]
                with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(invalid, schema)

    def test_gold_targets_match_frozen_fixture_syntax(self):
        # Grading artifact validation only: these values never flow into helpers.
        for lane, (directory, _, _) in generator.CONFIG.items():
            rows = coverage.parse_fixture(lane, fixture(lane))
            if lane == 'L03': rows = [r for r in rows if ' topic=upload-window ' in r['text']]
            registration = json.loads((ROOT / directory / 'gold.json').read_text())
            self.assertTrue(registration['grading_only'])
            self.assertEqual(registration['target_ids'], [r['identifier'] for r in rows])
            self.assertEqual([x['start_line'] for x in registration['target_line_ranges']], [r['line'] for r in rows])
            if lane == 'L01':
                counts = registration['structured_counts']
                self.assertEqual(counts['failure_count'], len(rows))
                self.assertEqual(counts['group_count'], len(set(r['signature'] for r in rows)))
                for group in registration['expected_groups']:
                    matching = [r for r in rows if r['signature'] == group['signature']]
                    self.assertEqual(set(group['tests']), set(r['identifier'] for r in matching))
                    self.assertEqual(set(group['files']), set(r['file'] for r in matching))
                    self.assertEqual(set(group['kinds']), set(r['kind'] for r in matching))
                    self.assertEqual(group['occurrences'], len(matching))
            elif lane == 'L02':
                expected = [{key: r[key] for key in ('request_id', 'signature', 'ts')} for r in rows]
                observed = [{key: r[key] for key in ('request_id', 'signature', 'ts')} for r in registration['expected_events']]
                self.assertEqual(expected, observed)
                self.assertEqual(registration['structured_counts']['signature_counts'], dict(coverage.Counter(r['signature'] for r in rows)))
            else:
                by_id = {r['identifier']: r for r in rows}
                for name in ('required_facts', 'supported_inferences', 'hypotheses', 'required_unknowns', 'verify_next'):
                    for item in registration[name]:
                        original = by_id[item['record_id']]
                        self.assertEqual((item['source'], item['locator']), (original['source'], original['locator']))

    def test_preregistration_hashes_and_fixed_budget(self):
        registration = json.loads((ROOT / 'preregistration.json').read_text())
        for filename, expected in registration['artifact_sha256'].items():
            self.assertEqual(digest((ROOT / filename).read_bytes()), expected, filename)
        self.assertEqual(registration['pair_allowances'], {'L01': 97593, 'L02': 113976, 'L03': 179514})
        self.assertEqual(sum(registration['pair_allowances'].values()), 391083)
        self.assertEqual(registration['stage_a_ceiling'], 400000)
        self.assertFalse(registration['stage_a_executed'])
        self.assertFalse(registration['company_semantic_calls_executed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
