"""Gold-independent canonical membership/coverage. No semantic completeness claim."""

from collections import Counter
from datetime import datetime
import re

TOKEN = r'[A-Za-z0-9_.:/-]+'
FAILURE = re.compile(r'^FAILURE test_id=(\S+) file=(\S+) kind=(FAILED|ERROR) signature=(' + TOKEN + r')$')
EVENT = re.compile(r'^EVENT event_id=(' + TOKEN + r') request_id=(' + TOKEN + r') signature=(' + TOKEN + r') ts=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$')
RECORD = re.compile(r'^RECORD record_id=(' + TOKEN + r') source=(' + TOKEN + r') locator=(' + TOKEN + r') .+$')
COVERABLE_SIGNATURES = ('UPSTREAM_TIMEOUT', 'RECOVERY_OBSERVED')
RECORD_REFERENCE = re.compile(r'(?<!\w)record_id=(' + TOKEN + r')(?!\w)')


def parse_fixture(lane, fixture, allow_duplicates=False):
    pattern = {'L01': FAILURE, 'L02': EVENT, 'L03': RECORD}[lane]
    result = []
    seen = set()
    for number, line in enumerate(fixture.decode('utf-8').splitlines(), 1):
        match = pattern.fullmatch(line)
        if not match:
            continue
        values = match.groups()
        if values[0] in seen and not allow_duplicates:
            raise ValueError('duplicate canonical identifier')
        seen.add(values[0])
        item = {'identifier': values[0], 'line': number, 'text': line}
        if lane == 'L01':
            item.update(file=values[1], kind=values[2], signature=values[3])
        elif lane == 'L02':
            datetime.strptime(values[3], '%Y-%m-%dT%H:%M:%SZ')
            item.update(request_id=values[1], signature=values[2], ts=values[3])
        else:
            item.update(source=values[1], locator=values[2])
        result.append(item)
    return result


def compare_ids(universe, returned):
    counts = Counter(returned)
    return {
        'valid': sorted(set(returned) & set(universe)),
        'missing': sorted(set(universe) - set(returned)),
        'duplicate': sorted(k for k, v in counts.items() if v > 1),
        'unexpected': sorted(set(returned) - set(universe)),
    }


def anchors(lane, ids):
    return [{'lane': lane, 'identifier': value} for value in ids]


def test_coverage(fixture, company):
    records = parse_fixture('L01', fixture)
    ids = [x for group in company['failure_groups'] for x in group['tests']]
    comparison = compare_ids([x['identifier'] for x in records], ids)
    by_id = {x['identifier']: x for x in records}
    file_errors = []
    for group in company['failure_groups']:
        expected = {by_id[x]['file'] for x in group['tests'] if x in by_id}
        if set(group['files']) != expected or len(group['files']) != len(set(group['files'])):
            file_errors.append({'tests': group['tests'], 'files': group['files']})
    return {
        'scope': 'canonical failure ID and file membership only; no root-cause judgement',
        'fixture_failure_ids': [x['identifier'] for x in records],
        'company_validated_failure_ids': comparison['valid'],
        'missing_failure_ids': comparison['missing'],
        'duplicate_failure_ids': comparison['duplicate'],
        'unexpected_failure_ids': comparison['unexpected'],
        'file_membership_errors': file_errors,
        'failure_id_coverage_complete': not any(comparison[k] for k in ('missing', 'duplicate', 'unexpected')) and not file_errors,
        'validated_anchors': anchors('L01', comparison['valid']),
    }


def log_coverage(fixture, company):
    records = [x for x in parse_fixture('L02', fixture) if x['signature'] in COVERABLE_SIGNATURES]
    by_id = {x['identifier']: x for x in records}
    # Frozen adaptation of the existing log-triage string-array schema:
    # evidence and timeline items must quote complete canonical EVENT lines.
    # No inferred counts, fuzzy IDs or prose-number parsing.
    quoted = parse_fixture('L02', ('\n'.join(company['evidence']) + '\n').encode(), allow_duplicates=True)
    timeline = parse_fixture('L02', ('\n'.join(company['timeline']) + '\n').encode(), allow_duplicates=True)
    invalid_evidence = [line for line in company['evidence'] if not EVENT.fullmatch(line)]
    invalid_timeline = [line for line in company['timeline'] if not EVENT.fullmatch(line)]
    returned = [x['identifier'] for x in quoted]
    cmp = compare_ids(by_id, returned)
    mismatches = [x['identifier'] for x in quoted if x['identifier'] in by_id and x['text'] != by_id[x['identifier']]['text']]
    validated = [x for x in quoted if x['identifier'] in by_id and x['text'] == by_id[x['identifier']]['text']]
    expected_counts = dict(Counter(x['signature'] for x in records))
    observed_counts = dict(Counter(x['signature'] for x in validated))
    ordered = sorted(records, key=lambda x: (x['ts'], x['line']))
    timeline_valid = ([x['text'] for x in timeline] == [x['text'] for x in ordered])
    valid_ids = sorted(x['identifier'] for x in validated)
    signature_match = (expected_counts == observed_counts and set(company['error_signatures']) == set(expected_counts))
    return {
        'scope': 'syntactic event/signature/request/timestamp coverage and order only; never causality',
        'fixture_signature_counts': expected_counts,
        'company_signature_counts': observed_counts,
        'fixture_event_ids': [x['identifier'] for x in records],
        'company_validated_event_ids': valid_ids,
        'fixture_request_ids': sorted(set(x['request_id'] for x in records)),
        'company_validated_request_ids': sorted(set(x['request_id'] for x in validated)),
        'missing_event_ids': sorted(set(by_id) - set(valid_ids)),
        'unexpected_event_ids': cmp['unexpected'],
        'duplicate_event_ids': cmp['duplicate'],
        'mismatched_event_fields': mismatches,
        'noncanonical_evidence_items': invalid_evidence,
        'noncanonical_timeline_items': invalid_timeline,
        'signature_count_match': signature_match,
        'fixture_timestamp_order_valid': [x['ts'] for x in records] == sorted(x['ts'] for x in records),
        'timeline_identifier_order_valid': timeline_valid,
        'event_coverage_complete': not cmp['missing'] and not cmp['unexpected'] and not cmp['duplicate'] and not mismatches and not invalid_evidence and not invalid_timeline and signature_match and timeline_valid,
        'validated_anchors': anchors('L02', sorted(set(valid_ids))),
    }


def context_coverage(fixture, company, selected_ranges=None):
    # Use the existing strict A4 parser; do not invent a second packet contract.
    import json
    from llmtool.context import parse_context_packet
    packet = parse_context_packet(json.dumps(company))
    records = parse_fixture('L03', fixture)
    by_id = {x['identifier']: x for x in records}
    valid, unknown, seen, duplicate = [], [], set(), []
    for citation in packet.evidence:
        identifiers = RECORD_REFERENCE.findall(citation.statement)
        if len(identifiers) != 1:
            unknown.append({'statement': citation.statement, 'reason': 'requires exactly one record_id reference'})
            continue
        key = identifiers[0]
        if key in seen:
            duplicate.append(key)
        seen.add(key)
        record = by_id.get(key)
        if record is None or citation.source != record['source'] or citation.locator != record['locator']:
            unknown.append({'identifier': key, 'source': citation.source, 'locator': citation.locator})
        else:
            valid.append(key)
    contained = None if selected_ranges is None else all(
        any(r['start_line'] <= by_id[key]['line'] <= r['end_line'] for r in selected_ranges) for key in valid)
    return {
        'scope': 'citation existence/membership only; semantic completeness is not evaluated',
        'company_validated_record_ids': sorted(set(valid)),
        'unknown_citations': unknown,
        'duplicate_citation_ids': sorted(set(duplicate)),
        'citation_membership_valid': not unknown and not duplicate,
        'selected_ranges_contain_cited_records': contained,
        'validated_anchors': anchors('L03', sorted(set(valid))),
    }
