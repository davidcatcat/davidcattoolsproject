"""Round-3 Phase-0 offline rules. No execution, network, provider, or file I/O.

Adapters consume immutable raw stdout and fixture-derived records only. Evaluation
metadata enters exclusively through the post-freeze recall function. This module
does not construct company prompts, launch requests, or run Codex.
"""

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime


FAILURE_PRECEDENCE = (
    'TRANSPORT_OR_REQUEST_FAILURE', 'MODEL_OUTPUT_INCOMPLETE',
    'SCHEMA_OR_PARSE_FAILURE', 'INVALID_CANONICAL_COVERAGE',
    'UNKNOWN_STRUCTURED_ANCHOR', 'AMBIGUOUS_STRUCTURED_ANCHOR',
    'DUPLICATE_STRUCTURED_ANCHOR', 'EMPTY_VALIDATED_ANCHOR_SET',
    'TARGET_RECALL_BELOW_7_OF_7',
)
TRIAL_ORDER = ('L02-1', 'L03-1', 'L02-2', 'L03-2', 'L02-3', 'L03-3')
TOKEN_CEILING = 300000
UPPER_BOUNDS = {'L02': 35000, 'L03': 59000}
FIXTURE_SHA256 = {
    'L02': '859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da',
    'L03': '44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce',
}
TOKEN = r'[A-Za-z0-9_.:/-]+'
EVENT = re.compile(r'EVENT event_id=(' + TOKEN + r') request_id=(' + TOKEN
                   + r') signature=(' + TOKEN + r') ts=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)')
RECORD = re.compile(r'RECORD record_id=(' + TOKEN + r') source=(' + TOKEN
                    + r') locator=(' + TOKEN + r') .+')
CANONICAL_LOOKING = re.compile(r'(?i)(?:\bEVENT\s+event_id\b|\b(?:event_id|request_id|signature|ts)\s*[:=])')
NAMESPACE_REFERENCE = re.compile(r'\b(?:EVT|REQ)-[A-Za-z0-9_.:/-]+\b')
LOG_FIELDS = ('summary', 'error_signatures', 'timeline', 'repeated_patterns',
              'evidence', 'unknowns', 'strong_review_reasons')
SECRET_PATTERNS = (
    ('synthetic_secret_marker', re.compile(rb'SECRET_MARKER_[A-Za-z0-9_]+')),
    ('credential_assignment', re.compile(
        rb'(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|authorization)'
        rb'["\s]*[:=]["\s]*[^\s",}]+')),
    ('bearer_value', re.compile(rb'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+')),
    ('key_like_value', re.compile(rb'\b(?:sk-[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{16})\b')),
    ('private_key', re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
)


def primary_failure(reasons):
    """Return one stable primary; keep callers' complete secondary diagnostics."""
    if any(x not in FAILURE_PRECEDENCE for x in reasons):
        raise ValueError('unsupported failure reason')
    return next((x for x in FAILURE_PRECEDENCE if x in reasons), 'PASS')


def scan_secret_markers(raw):
    """Conservative publication guard, returning categories without secret values."""
    return tuple(name for name, pattern in SECRET_PATTERNS if pattern.search(raw))


@dataclass(frozen=True)
class RawStdout:
    raw: bytes
    sha256: str = field(init=False)
    secret_markers: tuple = field(init=False)

    def __post_init__(self):
        if type(self.raw) is not bytes:
            raise TypeError('immutable stdout bytes required')
        # Digest original bytes FIRST; neither parsing nor redaction precedes it.
        object.__setattr__(self, 'sha256', hashlib.sha256(self.raw).hexdigest())
        object.__setattr__(self, 'secret_markers', scan_secret_markers(self.raw))

    @property
    def publication_allowed(self):
        return not self.secret_markers


@dataclass(frozen=True)
class FrozenEvaluation:
    lane: str
    stdout: RawStdout
    result_json: str

    def to_dict(self):
        # A detached deep copy; evaluation cannot mutate previously frozen state.
        return json.loads(self.result_json)


def _freeze(lane, stdout, result, reasons=()):
    if type(stdout) is not RawStdout:
        raise TypeError('capture raw stdout before evaluation')
    result = dict(result)
    result.update(primary_failure_reason=primary_failure(reasons),
                  secondary_failure_reasons=[x for x in FAILURE_PRECEDENCE if x in reasons],
                  passed=not reasons, raw_stdout_sha256=stdout.sha256,
                  publication_allowed=stdout.publication_allowed,
                  secret_marker_categories=list(stdout.secret_markers))
    return FrozenEvaluation(lane, stdout, json.dumps(result, sort_keys=True, allow_nan=False))


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    request_id: str
    signature: str
    timestamp: str
    line: int
    text: str


@dataclass(frozen=True)
class FixtureRecord:
    record_id: str
    source: str
    locator: str
    line: int


def verify_fixture_digest(lane, raw):
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256[lane]:
        raise ValueError('PREREGISTRATION_DRIFT')


def parse_l02_fixture(raw):
    records, seen = [], set()
    for line, text in enumerate(raw.decode('utf-8').splitlines(), 1):
        match = EVENT.fullmatch(text)
        if not match:
            continue
        identifier, request, signature, timestamp = match.groups()
        datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')
        if identifier in seen:
            raise ValueError('duplicate fixture EVENT')
        seen.add(identifier)
        records.append(EventRecord(identifier, request, signature, timestamp, line, text))
    if not records:
        raise ValueError('empty fixture EVENT universe')
    return tuple(records)


def parse_l03_fixture(raw):
    records, seen = [], set()
    for line, text in enumerate(raw.decode('utf-8').splitlines(), 1):
        match = RECORD.fullmatch(text)
        if not match:
            continue
        identifier, source, locator = match.groups()
        if identifier in seen:
            raise ValueError('duplicate fixture RECORD')
        seen.add(identifier)
        records.append(FixtureRecord(identifier, source, locator, line))
    if not records:
        raise ValueError('empty fixture RECORD universe')
    return tuple(records)


def build_l03_multimap(records):
    index = defaultdict(list)
    for record in records:
        index[(record.source, record.locator)].append(record)
    return {pair: tuple(matches) for pair, matches in index.items()}


def multimap_cardinality(records):
    index = build_l03_multimap(records)
    return {'records': len(records), 'distinct_pairs': len(index),
            'ambiguous_pairs': sum(len(v) > 1 for v in index.values()),
            'records_in_ambiguous_pairs': sum(len(v) for v in index.values() if len(v) > 1)}


def _json(raw):
    if len(raw) > 1024 * 1024:
        raise ValueError('output too large')
    text = raw.decode('utf-8')
    # Bound nesting before calling the recursive JSON decoder.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > 6:
                raise ValueError('output nesting too deep')
        elif char in ']}':
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('unsupported JSON constant')

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _parse_log(stdout):
    from llmtool.terminal import contains_unsafe_controls
    value = _json(stdout.raw)
    if type(value) is not dict or set(value) != set(LOG_FIELDS):
        raise ValueError('invalid log schema')
    for name in LOG_FIELDS:
        items = [value[name]] if name == 'summary' else value[name]
        if type(items) is not list or any(
                type(item) is not str or not item.strip() or contains_unsafe_controls(item)
                for item in items):
            raise ValueError('invalid log field')
        for item in items:
            item.encode('utf-8')
    return value


def evaluate_l02_without_gold(stdout, records):
    result = {'stage': 'canonical_coverage', 'validated_event_ids': [],
              'missing_evidence_ids': [], 'missing_timeline_ids': [],
              'duplicate_evidence_ids': [], 'duplicate_timeline_ids': [],
              'invalid_canonical_claims': [], 'evidence_extras': [], 'timeline_extras': [],
              'canonical_signature_counts': {}, 'extra_signatures': [],
              'missing_signatures': [], 'unknown_identifier_references': [],
              'timeline_order_valid': False, 'validated_anchors': []}
    try:
        company = _parse_log(stdout)
    except (ValueError, UnicodeError, RecursionError):
        return _freeze('L02', stdout, result, ('SCHEMA_OR_PARSE_FAILURE',))
    by_id = {record.event_id: record for record in records}
    expected = sorted(records, key=lambda r: (r.timestamp, r.line))
    validated = {}
    for field_name in ('evidence', 'timeline'):
        candidates, claimed_ids = [], []
        for position, item in enumerate(company[field_name]):
            match = EVENT.fullmatch(item)
            if match:
                identifier = match.group(1)
                claimed_ids.append(identifier)
                if identifier in by_id and item == by_id[identifier].text:
                    candidates.append(identifier)
                    continue
                reason = 'UNKNOWN_EVENT' if identifier not in by_id else 'EVENT_FIELD_MISMATCH'
            elif CANONICAL_LOOKING.search(item):
                reason = 'MALFORMED_OR_PARTIAL_EVENT'
            else:
                namespace_refs = sorted(set(NAMESPACE_REFERENCE.findall(item)))
                if namespace_refs:
                    known_ids = set(by_id) | {record.request_id for record in records}
                    unknown_refs = [identifier for identifier in namespace_refs
                                    if identifier not in known_ids]
                    result['unknown_identifier_references'].append(
                        {'field': field_name, 'item_index': position,
                         'identifiers': namespace_refs, 'unknown_identifiers': unknown_refs})
                    reason = 'NAMESPACE_IDENTIFIER_REFERENCE_IN_EXTRA'
                else:
                    result[field_name + '_extras'].append(item)
                    continue
            result['invalid_canonical_claims'].append(
                {'field': field_name, 'item_index': position, 'reason': reason})
        validated[field_name] = candidates
        result['missing_' + field_name + '_ids'] = sorted(set(by_id) - set(candidates))
        result['duplicate_' + field_name + '_ids'] = sorted(
            identifier for identifier, count in Counter(claimed_ids).items() if count > 1)
    counts = dict(Counter(by_id[identifier].signature for identifier in validated['evidence']))
    required_signatures = set(record.signature for record in records)
    result.update(
        validated_event_ids=sorted(set(validated['evidence'])),
        canonical_signature_counts=counts,
        extra_signatures=sorted(set(company['error_signatures']) - required_signatures),
        missing_signatures=sorted(required_signatures - set(company['error_signatures'])),
        timeline_order_valid=validated['timeline'] == [r.event_id for r in expected],
        validated_anchors=[{'lane': 'L02', 'identifier': identifier}
                           for identifier in sorted(set(validated['evidence']))])
    failed = any(result[name] for name in (
        'missing_evidence_ids', 'missing_timeline_ids', 'duplicate_evidence_ids',
        'duplicate_timeline_ids', 'invalid_canonical_claims', 'missing_signatures'))
    failed = failed or not result['timeline_order_valid'] or counts != dict(
        Counter(record.signature for record in records))
    return _freeze('L02', stdout, result, ('INVALID_CANONICAL_COVERAGE',) if failed else ())


def evaluate_l03_anchors_without_gold(stdout, records):
    from llmtool.context import parse_context_packet
    from llmtool.errors import LLMToolError
    result = {'stage': 'structured_anchors_without_gold', 'parser_passed': False,
              'evidence_item_count': 0, 'unknown_structured_anchors': [],
              'ambiguous_structured_anchors': [], 'duplicate_mapped_record_ids': [],
              'inventory_evidence': [], 'validated_unique_anchor_record_ids': [],
              'validated_anchors': []}
    try:
        packet = parse_context_packet(stdout.raw.decode('utf-8'))
    except (LLMToolError, UnicodeError):
        return _freeze('L03', stdout, result, ('SCHEMA_OR_PARSE_FAILURE',))
    index = build_l03_multimap(records)
    mapped = []
    result.update(parser_passed=True, evidence_item_count=len(packet.evidence))
    for position, item in enumerate(packet.evidence):
        matches = index.get((item.source, item.locator), ())
        if not matches:
            result['unknown_structured_anchors'].append(position)
        elif len(matches) > 1:
            result['ambiguous_structured_anchors'].append(
                {'item_index': position, 'record_ids': [r.record_id for r in matches]})
        elif matches[0].record_id.startswith('INV-'):
            # Frozen fixture's background namespace, not a gold-target whitelist.
            # Membership exists but this is not a usable benchmark anchor.
            result['inventory_evidence'].append(position)
        else:
            mapped.append(matches[0].record_id)
    unique = sorted(set(mapped))
    result.update(duplicate_mapped_record_ids=sorted(
        identifier for identifier, count in Counter(mapped).items() if count > 1),
        validated_unique_anchor_record_ids=unique,
        validated_anchors=[{'lane': 'L03', 'identifier': identifier} for identifier in unique])
    reasons = []
    if result['unknown_structured_anchors'] or result['inventory_evidence']:
        reasons.append('UNKNOWN_STRUCTURED_ANCHOR')
    if result['ambiguous_structured_anchors']:
        reasons.append('AMBIGUOUS_STRUCTURED_ANCHOR')
    if result['duplicate_mapped_record_ids']:
        reasons.append('DUPLICATE_STRUCTURED_ANCHOR')
    if not unique:
        reasons.append('EMPTY_VALIDATED_ANCHOR_SET')
    return _freeze('L03', stdout, result, reasons)


def evaluate_l03_target_recall_after_freeze(frozen, load_evaluation_metadata):
    """Only this stage invokes the caller's grading-metadata loader; never repairs."""
    if type(frozen) is not FrozenEvaluation or frozen.lane != 'L03':
        raise TypeError('frozen L03 evaluation required')
    result = frozen.to_dict()
    if result['stage'] != 'structured_anchors_without_gold':
        raise ValueError('target recall already evaluated')
    metadata = load_evaluation_metadata()
    targets = metadata['target_ids']
    if (type(targets) is not list or len(targets) != 7
            or any(type(x) is not str or not x for x in targets) or len(set(targets)) != 7):
        raise ValueError('exactly seven distinct evaluation targets required')
    found = sorted(set(result['validated_unique_anchor_record_ids']) & set(targets))
    result.update(stage='phase0_l03_target_recall', anchor_result_sha256=hashlib.sha256(
        frozen.result_json.encode()).hexdigest(), target_recall_numerator=len(found),
        target_recall_denominator=7, recalled_target_record_ids=found,
        missing_target_record_ids=sorted(set(targets) - set(found)))
    reasons = list(result['secondary_failure_reasons'])
    if len(found) != 7:
        reasons.append('TARGET_RECALL_BELOW_7_OF_7')
    return _freeze('L03', frozen.stdout, result, reasons)


@dataclass(frozen=True)
class RequestObservation:
    request_body_sha256: str
    enable_thinking: bool
    model: str
    temperature: object
    max_tokens: int
    authorization_header_present: bool
    observer_changed_request: bool


def observe_request_without_mutation(request):
    """Validate an already-built Request-shaped object; never sends it."""
    body = _json(request.data)
    if type(body) is not dict:
        raise ValueError('REQUEST_OBSERVER_CONTRACT_FAILURE')
    headers = request.header_items()
    checks = {
        'url': request.full_url == 'http://home-llm.example:8000/v1/chat/completions',
        'method': request.get_method() == 'POST',
        'enable_thinking': body.get('chat_template_kwargs') == {'enable_thinking': False}
        and body.get('chat_template_kwargs', {}).get('enable_thinking') is False,
        'model': body.get('model') == 'gw',
        'temperature': type(body.get('temperature')) in (int, float) and body['temperature'] == 0,
        'max_tokens': type(body.get('max_tokens')) is int and body['max_tokens'] == 4096,
        'no_authorization': not any(name.lower() == 'authorization' for name, _ in headers),
    }
    if not all(checks.values()):
        raise ValueError('REQUEST_OBSERVER_CONTRACT_FAILURE')
    return RequestObservation(
        request_body_sha256=hashlib.sha256(request.data).hexdigest(),
        enable_thinking=False, model='gw', temperature=0, max_tokens=4096,
        authorization_header_present=False, observer_changed_request=False)


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    status: str
    request_emitted: bool = False
    request_proof_json: object = None
    usage_total: object = None
    primary_failure_reason: object = None
    secondary_diagnostics: tuple = ()
    evaluation_sha256: object = None
    raw_stdout_sha256: object = None


class Phase0Accounting:
    """Admission/accounting only. No callable transport or retry API exists."""

    def __init__(self):
        self._records = []
        self._stopped_lanes = set()
        self._active = None
        self._known_total = 0
        self._accounting_unknown = False

    @property
    def records(self):
        return tuple(self._records)

    @property
    def known_cumulative(self):
        return self._known_total

    @property
    def complete_total(self):
        return None if self._accounting_unknown else self._known_total

    @property
    def gate_passed(self):
        return (len(self._records) == 6 and self._active is None
                and not self._accounting_unknown and self._known_total <= TOKEN_CEILING
                and all(r.status == 'PASS' and r.request_emitted for r in self._records))

    def begin_next(self):
        if self._active is not None:
            raise ValueError('previous logical trial incomplete')
        if len(self._records) == len(TRIAL_ORDER):
            raise ValueError('all logical trials recorded')
        trial = TRIAL_ORDER[len(self._records)]
        lane = trial.split('-')[0]
        if self._accounting_unknown:
            reason = 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN'
        elif lane in self._stopped_lanes:
            reason = 'NOT_STARTED_EARLY_FORMAT_GATE_FAIL'
        elif self._known_total + UPPER_BOUNDS[lane] > TOKEN_CEILING:
            reason = 'COMPANY_TOKEN_CEILING_NOT_STARTED'
        else:
            reason = None
        record = TrialRecord(trial, 'NOT_STARTED' if reason else 'STARTED',
                             primary_failure_reason=reason)
        self._records.append(record)
        if reason is None:
            self._active = trial
        return record

    def mark_request_emitted(self, trial_id, observation):
        record = self._require_active(trial_id)
        if record.request_emitted:
            raise ValueError('one physical request maximum; no retry')
        if type(observation) is not RequestObservation:
            raise TypeError('RequestObservation from observer required')
        if (type(observation.enable_thinking) is not bool or observation.enable_thinking is not False
                or observation.model != 'gw'
                or type(observation.temperature) not in (int, float)
                or type(observation.temperature) is bool or observation.temperature != 0
                or type(observation.max_tokens) is not int or observation.max_tokens != 4096
                or type(observation.authorization_header_present) is not bool
                or observation.authorization_header_present
                or type(observation.observer_changed_request) is not bool
                or observation.observer_changed_request
                or type(observation.request_body_sha256) is not str
                or not re.fullmatch('[0-9a-f]{64}', observation.request_body_sha256)):
            raise ValueError('valid request observation required')
        self._records[-1] = replace(
            record, request_emitted=True,
            request_proof_json=json.dumps({
                'request_body_sha256': observation.request_body_sha256,
                'enable_thinking': observation.enable_thinking,
                'model': observation.model,
                'temperature': observation.temperature,
                'max_tokens': observation.max_tokens,
                'authorization_header_present': observation.authorization_header_present,
                'observer_changed_request': observation.observer_changed_request,
            }, sort_keys=True))

    def _require_active(self, trial_id):
        if trial_id != self._active or self._active is None:
            raise ValueError('no matching active logical trial')
        return self._records[-1]

    def _record_usage_and_finish(self, record, trial_id, reason, diagnostics,
                                 usage_total, evaluation=None):
        if usage_total is not None and (type(usage_total) is not int or usage_total < 0):
            raise ValueError('provider total must be a nonnegative integer or unknown')
        if record.request_emitted and usage_total is None:
            self._accounting_unknown = True
            diagnostics = tuple(diagnostics) + ('COMPANY_TOKEN_ACCOUNTING_UNKNOWN',)
        elif usage_total is not None:
            self._known_total += usage_total
        status = 'PASS' if reason == 'PASS' and not self._accounting_unknown else 'FAIL'
        if status == 'FAIL':
            self._stopped_lanes.add(trial_id.split('-')[0])
        if reason == 'PASS' and self._accounting_unknown:
            reason = 'COMPANY_TOKEN_ACCOUNTING_UNKNOWN'
        evaluation_sha256 = None
        raw_stdout_sha256 = None
        if evaluation is not None:
            evaluation_sha256 = hashlib.sha256(evaluation.result_json.encode()).hexdigest()
            raw_stdout_sha256 = evaluation.stdout.sha256
        self._records[-1] = replace(
            record, status=status, usage_total=usage_total,
            primary_failure_reason=reason, secondary_diagnostics=tuple(diagnostics),
            evaluation_sha256=evaluation_sha256, raw_stdout_sha256=raw_stdout_sha256)
        self._active = None
        return self._records[-1]

    def complete_evaluation(self, trial_id, evaluation, usage_total=None):
        """Finish an emitted logical trial only from its final frozen adapter verdict."""
        record = self._require_active(trial_id)
        if not record.request_emitted:
            raise ValueError('evaluated trial requires an emitted request')
        if type(evaluation) is not FrozenEvaluation:
            raise TypeError('final FrozenEvaluation required')
        lane = trial_id.split('-')[0]
        if evaluation.lane != lane:
            raise ValueError('evaluation lane does not match logical trial')
        result = evaluation.to_dict()
        if lane == 'L02':
            if result.get('stage') != 'canonical_coverage':
                raise ValueError('final L02 canonical coverage evaluation required')
        elif lane == 'L03':
            if result.get('stage') != 'phase0_l03_target_recall':
                raise ValueError('final L03 post-freeze recall evaluation required')
        else:
            raise ValueError('unsupported trial lane')
        if result.get('raw_stdout_sha256') != evaluation.stdout.sha256:
            raise ValueError('evaluation/stdout digest mismatch')
        diagnostics = tuple(result.get('secondary_failure_reasons', ()))
        reason = primary_failure(diagnostics)
        if result.get('primary_failure_reason') != reason:
            raise ValueError('evaluation primary failure mismatch')
        if result.get('passed') is not (reason == 'PASS'):
            raise ValueError('evaluation pass/failure mismatch')
        return self._record_usage_and_finish(
            record, trial_id, reason, diagnostics, usage_total, evaluation=evaluation)

    def complete_pre_output_failure(self, trial_id, reason, usage_total=None):
        """Finish before adapter evaluation for transport/request or model-length failure."""
        record = self._require_active(trial_id)
        if reason not in ('TRANSPORT_OR_REQUEST_FAILURE', 'MODEL_OUTPUT_INCOMPLETE'):
            raise ValueError('only pre-output failure reasons are allowed')
        if not record.request_emitted:
            if reason != 'TRANSPORT_OR_REQUEST_FAILURE' or usage_total is not None:
                raise ValueError('unemitted failure must be pre-request transport with unknown usage')
        return self._record_usage_and_finish(
            record, trial_id, reason, (reason,), usage_total, evaluation=None)
