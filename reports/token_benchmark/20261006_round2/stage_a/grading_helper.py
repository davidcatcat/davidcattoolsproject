"""Post-pair deterministic grading only; never builds prompts or selected ranges."""

from collections import Counter
import json
from pathlib import Path
import sys

import jsonschema
import coverage
from execution_helper import DIRECTORIES, STAGE, strict_json


def grade(lane, output):
    folder = STAGE / DIRECTORIES[lane]
    # This grading-only function is called after both measured variants are complete.
    schema = json.loads((folder / 'output_schema.json').read_text())
    gold = json.loads((folder / 'gold.json').read_text())
    misses = []
    try:
        value = strict_json(output)
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(value))
        if errors:
            return {'status': 'FAIL', 'misses': ['FINAL_SCHEMA_INVALID'], 'error_paths': [list(e.path) for e in errors],
                    'semantic_status': 'BLIND_REVIEW_REQUIRED'}
    except ValueError:
        return {'status': 'FAIL', 'misses': ['FINAL_JSON_INVALID'], 'semantic_status': 'BLIND_REVIEW_REQUIRED'}
    if lane == 'L01':
        expected = {g['signature']: g for g in gold['expected_groups']}
        groups = value['groups']
        actual = {g['signature']: g for g in groups}
        if set(actual) != set(expected) or len(actual) != len(groups):
            misses.append('SIGNATURE_GROUPS_MISMATCH_OR_DUPLICATED')
        identifiers = [t for g in groups for t in g['tests']]
        if Counter(identifiers) != Counter(gold['target_ids']):
            misses.append('FAILURE_TEST_IDS_MISSING_DUPLICATE_OR_UNEXPECTED')
        for name in ('failure_count', 'group_count'):
            if value[name] != gold['structured_counts'][name]: misses.append(name.upper() + '_MISMATCH')
        for signature, expected_group in expected.items():
            group = actual.get(signature)
            if group is None: continue
            for name in ('category', 'occurrences'):
                if group[name] != expected_group[name]: misses.append(signature + ':' + name + '_MISMATCH')
            for name in ('tests', 'files', 'kinds'):
                if Counter(group[name]) != Counter(expected_group[name]): misses.append(signature + ':' + name + '_MISMATCH')
            if not group['evidence']: misses.append(signature + ':EVIDENCE_EMPTY')
        if not value['unknowns']: misses.append('REQUIRED_UNKNOWNS_EMPTY')
    elif lane == 'L02':
        if value['events'] != gold['expected_events']:
            misses.append('EXACT_EVENT_TUPLES_OR_ORDER_MISMATCH')
        rows = value['signature_counts']
        counts = {r['signature']: r['occurrences'] for r in rows}
        if len(counts) != len(rows) or counts != gold['structured_counts']['signature_counts']:
            misses.append('STRUCTURED_SIGNATURE_COUNTS_MISMATCH_OR_DUPLICATED')
        if not value['repeated_patterns']: misses.append('REPEATED_PATTERNS_EMPTY')
        if not value['unknowns']: misses.append('REQUIRED_UNKNOWNS_EMPTY')
    else:
        from llmtool.context import parse_context_packet
        from llmtool.errors import LLMToolError
        try:
            parse_context_packet(output)
        except LLMToolError:
            return {'status': 'FAIL', 'misses': ['A4_PACKET_PARSER_REJECTED'], 'semantic_status': 'BLIND_REVIEW_REQUIRED'}
        raw = (folder / 'fixture.txt').read_bytes()
        result = coverage.context_coverage(raw, value)
        if not result['citation_membership_valid']:
            misses.append('CITATION_MEMBERSHIP_INVALID')
        required = set(gold['target_ids'])
        observed = set(result['company_validated_record_ids'])
        missing = sorted(required - observed)
        if missing: misses.append({'MISSING_REQUIRED_CITATIONS': missing})
        for name in ('facts', 'evidence', 'inferences', 'hypotheses', 'unknowns', 'verify_next'):
            if not value[name]: misses.append(name.upper() + '_REQUIRED_CATEGORY_EMPTY')
    return {'status': 'PASS' if not misses else 'FAIL', 'misses': misses,
            'scope': 'schema and structured exact counts/IDs/groups/events/citations only; not semantic quality',
            'semantic_status': 'BLIND_REVIEW_REQUIRED'}


def main(lane, runtime):
    state = json.loads((runtime / 'state.json').read_text())
    pair = [s for s in state['codex_sessions'] if s['fixture'] == lane]
    assert len(pair) == 2 and {s['variant'] for s in pair} == {'CONTROL', 'DOGFOOD'}
    results = {}
    for session in pair:
        path = runtime / (lane + '-' + session['variant']) / 'final.raw'
        results[session['variant']] = grade(lane, path.read_text()) if path.exists() else {
            'status': 'FAIL', 'misses': ['NO_FINAL_OUTPUT'], 'semantic_status': 'BLIND_REVIEW_REQUIRED'}
    company = [a for a in state['company_attempts'] if a['fixture'] == lane][-1]
    manifest = company.get('selection')
    gold = json.loads((STAGE / DIRECTORIES[lane] / 'gold.json').read_text())
    results['selected_target_line_coverage'] = None if manifest is None else {
        target['identifier']: any(r['start_line'] <= target['start_line'] <= r['end_line']
                                  for r in manifest['selected_ranges']) for target in gold['target_line_ranges']}
    (runtime / (lane + '-deterministic_grade.json')).write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__':
    main(sys.argv[1], Path(sys.argv[2]))
