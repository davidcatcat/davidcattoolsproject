"""Bounded offline measurement over A7 assertions. Never authorize or execute data."""

import math
import re
from dataclasses import dataclass, fields
from functools import partial
from typing import Optional

from .context import parse_context_packet
from .errors import LLMToolError
from .feedback import (AssertionType, AssertionResult, RegressionCase, RegressionResult, JSON_LIMIT,
                       _parse, _schema, _json, _text, _items, parse_regression_case,
                       evaluate_regression)
from .validator import CommandRisk, validate_context_packet

SUITE_LIMIT = 32
TOKEN_LIMIT = 10**12
LATENCY_LIMIT = 86400000  # One day in milliseconds; not a provider timeout.

# Reuse exactly the A7 validators, with a fixed caller-owned domain for errors.
_parse = partial(_parse, context='benchmark')
_schema = partial(_schema, context='benchmark')
_text = partial(_text, context='benchmark')
_items = partial(_items, context='benchmark')
_json = partial(_json, context='benchmark')


def _integer(value, maximum=TOKEN_LIMIT):
    if type(value) is not int or not 0 <= value <= maximum:
        raise LLMToolError('benchmark requires a bounded nonnegative integer')
    return value


def _typed(value, kind):
    if type(value) is not kind:
        raise LLMToolError('benchmark requires supported typed data')
    return value


@dataclass(frozen=True)
class SafetyExpectation:
    forbidden_command_risks: tuple[CommandRisk, ...] = ()
    preferred_verify_next_any: tuple[str, ...] = ()

    def __post_init__(self):
        risks = _items(self.forbidden_command_risks, 5)
        try:
            risks = tuple(CommandRisk(r) for r in risks)
        except (ValueError, TypeError):
            raise LLMToolError('benchmark command risk is unsupported') from None
        preferred = tuple(_text(v, limit=8192) for v in _items(self.preferred_verify_next_any, 16))
        if len(set(risks)) != len(risks) or len(set(preferred)) != len(preferred):
            raise LLMToolError('benchmark safety expectations must be unique')
        object.__setattr__(self, 'forbidden_command_risks', risks)
        object.__setattr__(self, 'preferred_verify_next_any', preferred)

    def to_dict(self):
        return {'forbidden_command_risks': [r.value for r in self.forbidden_command_risks],
                'preferred_verify_next_any': list(self.preferred_verify_next_any)}


@dataclass(frozen=True)
class BenchmarkSpec:
    regression: RegressionCase
    name: str
    tags: tuple[str, ...] = ()
    safety: Optional[SafetyExpectation] = None

    def __post_init__(self):
        _typed(self.regression, RegressionCase)
        _text(self.name, limit=256)
        tags = tuple(_text(t, limit=128) for t in _items(self.tags, 16))
        if len(set(tags)) != len(tags):
            raise LLMToolError('benchmark tags must be unique')
        object.__setattr__(self, 'tags', tags)
        if self.safety is not None:
            _typed(self.safety, SafetyExpectation)
        self.to_json()

    def to_dict(self):
        return {'regression': self.regression.to_dict(), 'name': self.name,
                'tags': list(self.tags), 'safety': None if self.safety is None else self.safety.to_dict()}

    def to_json(self):
        return _json(self.to_dict())


def parse_benchmark_spec(text):
    data = _parse(text, context='benchmark spec')
    _schema(data, ('regression', 'name', 'tags', 'safety'), context='benchmark spec')
    safety = data['safety']
    if safety is not None:
        _schema(safety, ('forbidden_command_risks', 'preferred_verify_next_any'))
        safety = SafetyExpectation(**safety)
    return BenchmarkSpec(parse_regression_case(_json(data['regression']), context='benchmark regression'),
                         data['name'], data['tags'], safety)


@dataclass(frozen=True)
class BenchmarkRuntime:
    wall_latency_ms: Optional[int] = None
    provider_latency_ms: Optional[int] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def __post_init__(self):
        for name in ('wall_latency_ms', 'provider_latency_ms'):
            if getattr(self, name) is not None:
                _integer(getattr(self, name), LATENCY_LIMIT)
        if self.model is not None and (not isinstance(self.model, str)
                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', self.model) is None):
            raise LLMToolError('benchmark model must be a safe identifier')
        tokens = (self.input_tokens, self.output_tokens, self.total_tokens)
        if any(t is not None for t in tokens):
            if any(t is None for t in tokens):
                raise LLMToolError('benchmark token usage requires all three counts')
            for token in tokens:
                _integer(token)
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise LLMToolError('benchmark token usage counts are inconsistent')

    @property
    def token_usage_available(self):
        return self.input_tokens is not None

    def to_dict(self):
        return {**{f.name: getattr(self, f.name) for f in fields(self)},
                'token_usage_available': self.token_usage_available}


def parse_benchmark_runtime(text):
    data = _parse(text, context='benchmark runtime')
    _schema(data, (f.name for f in fields(BenchmarkRuntime)))
    return BenchmarkRuntime(**data)


def normalize_runtime(metadata, wall_latency_ms):
    """Ignore unknown fields; invalid/incomplete usage is unavailable, never estimated."""
    _integer(wall_latency_ms, LATENCY_LIMIT)
    if type(metadata) is not dict:
        metadata = {}
    elapsed = metadata.get('elapsed_seconds')
    provider_ms = None
    if type(elapsed) in (float, int) and 0 <= elapsed <= LATENCY_LIMIT / 1000 and math.isfinite(elapsed):
        provider_ms = round(elapsed * 1000)
    model = metadata.get('model')
    if not isinstance(model, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model) is None:
        model = None
    usage = (metadata.get('input_tokens', metadata.get('prompt_tokens')),
             metadata.get('output_tokens', metadata.get('completion_tokens')),
             metadata.get('total_tokens'))
    if (not all(type(t) is int and 0 <= t <= TOKEN_LIMIT for t in usage)
            or usage[2] != usage[0] + usage[1]):
        usage = (None, None, None)
    return BenchmarkRuntime(wall_latency_ms, provider_ms, model, *usage)


@dataclass(frozen=True)
class Metric:
    numerator: int
    denominator: int
    assessed: bool = True

    def __post_init__(self):
        _integer(self.numerator)
        _integer(self.denominator)
        if self.numerator > self.denominator or type(self.assessed) is not bool:
            raise LLMToolError('benchmark metric counts are invalid')

    @property
    def ratio(self):
        return self.numerator / self.denominator if self.assessed and self.denominator else None

    def to_dict(self):
        return {'numerator': self.numerator, 'denominator': self.denominator,
                'ratio': self.ratio, 'assessed': self.assessed}


QUALITY_NAMES = ('assertion_pass_rate', 'fact_retention', 'hallucination_rate',
                 'finding_recall', 'false_positive_rate', 'hypothesis_retention',
                 'unknown_retention', 'verify_next_retention', 'command_risk_pass_rate',
                 'context_validity')
QUALITY_TYPES = (None, AssertionType.FACT_CONTAINS, AssertionType.FACT_NOT_CONTAINS,
                 AssertionType.FINDING_PRESENT, AssertionType.FINDING_ABSENT,
                 AssertionType.HYPOTHESIS_CONTAINS, AssertionType.UNKNOWN_CONTAINS,
                 AssertionType.VERIFY_NEXT_CONTAINS, AssertionType.COMMAND_RISK_EQUALS,
                 AssertionType.CONTEXT_VALID)


@dataclass(frozen=True)
class BenchmarkMetrics:
    values: tuple[Metric, ...]

    def __post_init__(self):
        values = _items(self.values, len(QUALITY_NAMES))
        if len(values) != len(QUALITY_NAMES):
            raise LLMToolError('benchmark requires all quality dimensions')
        for value in values:
            _typed(value, Metric)
        object.__setattr__(self, 'values', values)

    def to_dict(self):
        return {name: value.to_dict() for name, value in zip(QUALITY_NAMES, self.values)}


@dataclass(frozen=True)
class Efficiency:
    input_bytes: int
    output_bytes: int

    def __post_init__(self):
        _integer(self.input_bytes)
        _integer(self.output_bytes)
        if not self.input_bytes:
            raise LLMToolError('benchmark input must be meaningful')

    def to_dict(self):
        return {'input_bytes': self.input_bytes, 'output_bytes': self.output_bytes,
                'compression_ratio': self.output_bytes / self.input_bytes}


@dataclass(frozen=True)
class SafetyResult:
    context_available: bool
    counts: tuple[int, ...]
    expectation_applied: bool
    preferred_evidence_present: Optional[bool]
    passed: bool

    def __post_init__(self):
        for value in (self.context_available, self.expectation_applied, self.passed):
            if type(value) is not bool:
                raise LLMToolError('benchmark safety flags must be boolean')
        if self.preferred_evidence_present is not None and type(self.preferred_evidence_present) is not bool:
            raise LLMToolError('benchmark preferred evidence must be boolean or null')
        counts = _items(self.counts, 5)
        if len(counts) != 5:
            raise LLMToolError('benchmark requires all command risk counts')
        for count in counts:
            _integer(count, 1024)
        object.__setattr__(self, 'counts', counts)

    def to_dict(self):
        return {'context_available': self.context_available, 'expectation_applied': self.expectation_applied,
                'verify_next_total': sum(self.counts),
                **{r.value: c for r, c in zip(CommandRisk, self.counts)},
                'preferred_evidence_present': self.preferred_evidence_present, 'passed': self.passed}


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    name: str
    regression: RegressionResult
    quality: BenchmarkMetrics
    efficiency: Efficiency
    runtime: BenchmarkRuntime
    safety: SafetyResult

    def __post_init__(self):
        _text(self.name, limit=256)
        _typed(self.regression, RegressionResult)
        if (not isinstance(self.case_id, str) or re.fullmatch('[0-9a-f]{24}', self.case_id) is None
                or self.case_id != self.regression.case_id):
            raise LLMToolError('benchmark case identity mismatch')
        assertions = _items(self.regression.results, 64)
        if not assertions:
            raise LLMToolError('benchmark requires assertion results')
        for index, assertion in enumerate(assertions):
            _typed(assertion, AssertionResult)
            if (type(assertion.index) is not int or assertion.index != index
                    or type(assertion.type) is not AssertionType or type(assertion.passed) is not bool):
                raise LLMToolError('benchmark assertion result is invalid')
            _text(assertion.message, limit=256)
        for name, kind in (('quality', BenchmarkMetrics), ('efficiency', Efficiency),
                           ('runtime', BenchmarkRuntime), ('safety', SafetyResult)):
            _typed(getattr(self, name), kind)

    @property
    def passed(self):
        return self.regression.passed and self.safety.passed

    def to_dict(self):
        return {'case_id': self.case_id, 'name': self.name, 'passed': self.passed,
                **{name: getattr(self, name).to_dict() for name in
                   ('regression', 'quality', 'efficiency', 'runtime', 'safety')}}

    def to_json(self):
        return _json(self.to_dict())


def evaluate_benchmark(spec, output, runtime=None):
    _typed(spec, BenchmarkSpec)
    runtime = BenchmarkRuntime() if runtime is None else _typed(runtime, BenchmarkRuntime)
    regression = evaluate_regression(spec.regression, output, context='benchmark output')
    packet = validation = None
    try:
        packet = parse_context_packet(output)
        validation = validate_context_packet(packet)
    except LLMToolError:
        packet = None
    metrics = []
    for kind in QUALITY_TYPES:
        results = [r for r in regression.results if kind is None or r.type == kind]
        assessed = kind in (None, AssertionType.COMMAND_RISK_EQUALS, AssertionType.CONTEXT_VALID) or packet is not None
        negative = kind in (AssertionType.FACT_NOT_CONTAINS, AssertionType.FINDING_ABSENT)
        count = sum(not r.passed if negative else r.passed for r in results) if assessed else 0
        metrics.append(Metric(count, len(results), assessed))
    counts = tuple(sum(a.risk == risk for a in validation.command_assessments)
                   if packet is not None else 0 for risk in CommandRisk)
    preferred = None
    safety_passed = True
    if spec.safety is not None:
        preferred = (any(command in packet.verify_next for command in spec.safety.preferred_verify_next_any)
                     if packet is not None and spec.safety.preferred_verify_next_any else
                     False if spec.safety.preferred_verify_next_any else None)
        safety_passed = (packet is not None and validation.valid and preferred is not False
                         and not any(counts[list(CommandRisk).index(risk)] for risk in spec.safety.forbidden_command_risks))
    result = BenchmarkResult(spec.regression.id, spec.name, regression, BenchmarkMetrics(tuple(metrics)),
                             Efficiency(len(spec.regression.input.encode('utf-8')), len(output.encode('utf-8'))),
                             runtime, SafetyResult(packet is not None, counts, spec.safety is not None, preferred, safety_passed))
    result.to_json()
    return result


@dataclass(frozen=True)
class SuiteEntry:
    case_file: str
    output_file: str
    runtime: Optional[BenchmarkRuntime] = None

    def __post_init__(self):
        for name in ('case_file', 'output_file'):
            _text(getattr(self, name), limit=4096)
        if self.runtime is not None:
            _typed(self.runtime, BenchmarkRuntime)

    def to_dict(self):
        return {'case_file': self.case_file, 'output_file': self.output_file,
                'runtime': None if self.runtime is None else
                {f.name: getattr(self.runtime, f.name) for f in fields(BenchmarkRuntime)}}


@dataclass(frozen=True)
class BenchmarkSuite:
    entries: tuple[SuiteEntry, ...]

    def __post_init__(self):
        entries = _items(self.entries, SUITE_LIMIT)
        if not entries:
            raise LLMToolError('benchmark suite requires cases')
        for entry in entries:
            _typed(entry, SuiteEntry)
        object.__setattr__(self, 'entries', entries)
        self.to_json()

    def to_json(self):
        return _json({'entries': [e.to_dict() for e in self.entries]})


def parse_benchmark_suite(text):
    data = _parse(text, context='benchmark suite')
    _schema(data, ('entries',), context='benchmark suite')
    entries = []
    for entry in _items(data['entries'], SUITE_LIMIT):
        _schema(entry, ('case_file', 'output_file', 'runtime'))
        runtime = None if entry['runtime'] is None else parse_benchmark_runtime(_json(entry['runtime']))
        entries.append(SuiteEntry(entry['case_file'], entry['output_file'], runtime))
    return BenchmarkSuite(entries)


@dataclass(frozen=True)
class BenchmarkSuiteResult:
    results: tuple[BenchmarkResult, ...]

    def __post_init__(self):
        results = _items(self.results, SUITE_LIMIT)
        if not results or any(type(r) is not BenchmarkResult for r in results):
            raise LLMToolError('benchmark suite requires bounded typed results')
        if len({r.case_id for r in results}) != len(results):
            raise LLMToolError('benchmark suite contains duplicate case identifiers')
        object.__setattr__(self, 'results', results)
        self.to_json()

    @property
    def passed(self):
        return all(r.passed for r in self.results)

    def to_dict(self):
        metrics = []
        for index in range(len(QUALITY_NAMES)):
            values = [r.quality.values[index] for r in self.results]
            # Unknown observations are not silently excluded from aggregate denominators.
            metrics.append(Metric(sum(v.numerator for v in values), sum(v.denominator for v in values),
                                  all(v.assessed or v.denominator == 0 for v in values)))
        latencies = [r.runtime.wall_latency_ms for r in self.results if r.runtime.wall_latency_ms is not None]
        provider = [r.runtime.provider_latency_ms for r in self.results if r.runtime.provider_latency_ms is not None]
        tokens = [r.runtime for r in self.results if r.runtime.token_usage_available]
        def summary(values):
            return {'sample_count': len(values), 'minimum_ms': min(values) if values else None,
                    'maximum_ms': max(values) if values else None,
                    'mean_ms': sum(values) / len(values) if values else None}
        return {'passed': self.passed, 'results': [r.to_dict() for r in self.results],
                'quality': BenchmarkMetrics(metrics).to_dict(),
                'efficiency': Efficiency(sum(r.efficiency.input_bytes for r in self.results),
                                         sum(r.efficiency.output_bytes for r in self.results)).to_dict(),
                'runtime': {'wall_latency': summary(latencies), 'provider_latency': summary(provider),
                            'token_sample_count': len(tokens),
                            **{name: sum(getattr(t, name) for t in tokens) if tokens else None
                               for name in ('input_tokens', 'output_tokens', 'total_tokens')}},
                'safety': {'assessed_case_count': sum(r.safety.context_available for r in self.results),
                           'expectation_case_count': sum(r.safety.expectation_applied for r in self.results),
                           'failed_case_count': sum(not r.safety.passed for r in self.results),
                           **{risk.value: sum(r.safety.counts[i] for r in self.results)
                              for i, risk in enumerate(CommandRisk)}}}

    def to_json(self):
        return _json(self.to_dict())


def format_benchmark(result):
    data = result.to_dict()
    lines = ['PASS' if result.passed else 'FAIL', 'QUALITY']
    for name, metric in data['quality'].items():
        lines.append('  ' + name + ': ' + str(metric['numerator']) + '/' + str(metric['denominator'])
                     + (' (unavailable)' if metric['ratio'] is None else ' (' + str(round(metric['ratio'], 6)) + ')'))
    for name in ('efficiency', 'runtime', 'safety'):
        lines.append(name.upper())
        lines.extend('  ' + key + ': ' + str(value) for key, value in data[name].items())
    return '\n'.join(lines)
