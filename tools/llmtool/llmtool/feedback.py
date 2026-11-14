"""Human-reviewed skill improvement data and offline assertions, never training code.

No provider, command execution or automatic skill mutation lives here. Skill
linkage reads only trusted packaged resources; explicit case file I/O is separate.
"""

import hashlib
import json
import re
from dataclasses import dataclass, fields, replace
from enum import Enum
from typing import Optional

from .context import parse_context_packet
from .errors import LLMToolError
from .skills import load_skill, validate_name
from .test_output_excerpts import _CLOUD_KEY
from .validator import CommandRisk, Severity, ValidationFinding, classify_command, validate_context_packet

JSON_LIMIT = 1024 * 1024
TEXT_LIMIT = 128 * 1024
ITEM_LIMIT = 128
ASSERTION_LIMIT = 64
MAX_DEPTH = 6


class FailureTag(str, Enum):
    HALLUCINATION = 'HALLUCINATION'
    FACT_DROPPED = 'FACT_DROPPED'
    UNSUPPORTED_CLAIM = 'UNSUPPORTED_CLAIM'
    MISCLASSIFIED_FACT = 'MISCLASSIFIED_FACT'
    MISCLASSIFIED_HYPOTHESIS = 'MISCLASSIFIED_HYPOTHESIS'
    MISSING_UNKNOWN = 'MISSING_UNKNOWN'
    MISSING_VERIFY_NEXT = 'MISSING_VERIFY_NEXT'
    UNSAFE_COMMAND = 'UNSAFE_COMMAND'
    SENSITIVE_READ_NOT_FLAGGED = 'SENSITIVE_READ_NOT_FLAGGED'
    OVERLY_BROAD_COMMAND = 'OVERLY_BROAD_COMMAND'
    FORMAT_INVALID = 'FORMAT_INVALID'
    OTHER = 'OTHER'


class FeedbackStatus(str, Enum):
    CAPTURED = 'CAPTURED'
    REVIEWED = 'REVIEWED'
    PATCH_PROPOSED = 'PATCH_PROPOSED'
    REGRESSION_READY = 'REGRESSION_READY'
    CLOSED = 'CLOSED'


class SkillVersionState(str, Enum):
    CURRENT = 'CURRENT'
    OLDER_THAN_CURRENT = 'OLDER_THAN_CURRENT'
    NEWER_THAN_CURRENT = 'NEWER_THAN_CURRENT'


class AssertionType(str, Enum):
    CONTEXT_VALID = 'CONTEXT_VALID'
    FACT_CONTAINS = 'FACT_CONTAINS'
    FACT_NOT_CONTAINS = 'FACT_NOT_CONTAINS'
    HYPOTHESIS_CONTAINS = 'HYPOTHESIS_CONTAINS'
    HYPOTHESIS_NOT_CONTAINS = 'HYPOTHESIS_NOT_CONTAINS'
    UNKNOWN_CONTAINS = 'UNKNOWN_CONTAINS'
    VERIFY_NEXT_CONTAINS = 'VERIFY_NEXT_CONTAINS'
    COMMAND_RISK_EQUALS = 'COMMAND_RISK_EQUALS'
    FINDING_PRESENT = 'FINDING_PRESENT'
    FINDING_ABSENT = 'FINDING_ABSENT'
    TEXT_CONTAINS = 'TEXT_CONTAINS'
    TEXT_NOT_CONTAINS = 'TEXT_NOT_CONTAINS'


def _enum(kind, value):
    if not isinstance(value, str):
        raise LLMToolError('feedback enum value must be supported text')
    try:
        return kind(value)
    except ValueError:
        raise LLMToolError('feedback enum value is unsupported') from None


def _text(value, empty=False, limit=TEXT_LIMIT, *, context='feedback'):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise LLMToolError(context + ' requires nonempty text')
    if len(value) > limit:
        raise LLMToolError(context + ' text exceeds limit')
    try:
        raw = value.encode('utf-8')
    except UnicodeError:
        raise LLMToolError(context + ' text must be UTF-8') from None
    if len(raw) > limit:
        raise LLMToolError(context + ' text exceeds limit')
    from .terminal import contains_unsafe_controls
    if contains_unsafe_controls(value):
        raise LLMToolError(context + ' text contains unsupported controls')
    return value


def _version(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise LLMToolError('feedback skill version must be a bounded positive integer')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{24}', value):
        raise LLMToolError('feedback identifier must be 24 lowercase hexadecimal characters')


def _items(value, limit=ITEM_LIMIT, *, context='feedback'):
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise LLMToolError(context + ' collection must be a bounded list or tuple')
    return tuple(value)


def _unique_ids(value):
    result = _items(value)
    if not result or len(set(_text(i, limit=24) for i in result)) != len(result):
        raise LLMToolError('feedback links require nonempty unique identifiers')
    for item in result:
        _id(item)
    return result


def _json(data, *, context='feedback'):
    result = json.dumps(data, ensure_ascii=True, indent=2, allow_nan=False)
    if len(result.encode('utf-8')) > JSON_LIMIT:
        raise LLMToolError(context + ' JSON exceeds 1 MiB')
    return result


def _digest(data):
    raw = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw) > JSON_LIMIT:
        raise LLMToolError('feedback identifying data exceeds 1 MiB')
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def _schema(data, names, *, context='feedback'):
    if not isinstance(data, dict) or set(data) != set(names):
        raise LLMToolError(context + ' object requires exactly the supported fields')


def _pairs(pairs, *, context='feedback'):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LLMToolError(context + ' JSON contains duplicate keys')
        result[key] = value
    return result


def _constant(value, *, context='feedback'):
    raise LLMToolError(context + ' JSON constants are unsupported')


def _parse(text, *, context='feedback'):
    # Context labels are fixed caller-owned strings, never input-derived data.
    _text(text, limit=JSON_LIMIT, context=context)
    depth = 0
    quoted = escaped = False
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
            if depth > MAX_DEPTH:
                raise LLMToolError(context + ' JSON exceeds nesting limit')
        elif char in ']}':
            depth -= 1
    try:
        return json.loads(text, object_pairs_hook=lambda pairs: _pairs(pairs, context=context),
                          parse_constant=lambda value: _constant(value, context=context))
    except (ValueError, RecursionError):
        raise LLMToolError(context + ' JSON is malformed') from None


def _finding(data):
    _schema(data, ('code', 'severity', 'message', 'category', 'locator'))
    return ValidationFinding(_text(data['code'], limit=256), _enum(Severity, data['severity']),
                             _text(data['message']), _text(data['category'], limit=256),
                             None if data['locator'] is None else _text(data['locator'], limit=1024))


def _typed_finding(finding):
    if type(finding) is not ValidationFinding:
        raise LLMToolError('feedback findings must be typed ValidationFinding values')
    return _finding({'code': finding.code, 'severity': _enum(Severity, finding.severity).value,
                     'message': finding.message, 'category': finding.category, 'locator': finding.locator})


def _reject_credentials(text):
    """Reject recognizable credential material; callers must still curate/redact input."""
    if re.search(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|' + _CLOUD_KEY + r'|(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}|(?<![A-Za-z0-9_])Bearer\s+[A-Za-z0-9._/-]{12,}', text):
        raise LLMToolError('feedback contains recognizable credential material; redact before capture')
    assignments = re.finditer(
        r'''["']?(?<!(?-i:[A-Za-z0-9_]))(?:api[_-]?key|password|token|secret|access[_-]?token|client[_-]?secret|aws_secret_access_key)["']?\s*[:=]\s*["']?([^\s"',;{}]+)''',
        text, re.IGNORECASE)
    for match in assignments:
        if match.group(1).lower() not in ('redacted', '<redacted>', '***', 'null', 'none', 'placeholder'):
            raise LLMToolError('feedback contains recognizable credential material; redact before capture')


@dataclass(frozen=True)
class FeedbackCase:
    id: str
    skill: str
    skill_version: int
    input: str
    actual_output: str
    expected_behavior: str
    failure_tags: tuple[FailureTag, ...] = ()
    validator_findings: tuple[ValidationFinding, ...] = ()
    review_notes: str = ''
    status: FeedbackStatus = FeedbackStatus.CAPTURED

    def __post_init__(self):
        validate_name(self.skill)
        _version(self.skill_version)
        for name in ('input', 'actual_output', 'expected_behavior', 'review_notes'):
            text = _text(getattr(self, name), empty=name in ('actual_output', 'review_notes'))
            _reject_credentials(text)
        tags = tuple(_enum(FailureTag, tag) for tag in _items(self.failure_tags))
        if len(set(tags)) != len(tags):
            raise LLMToolError('feedback tags must be unique')
        object.__setattr__(self, 'failure_tags', tags)
        findings = tuple(_typed_finding(f) for f in _items(self.validator_findings))
        for finding in findings:
            _reject_credentials(_json(finding.to_dict()))
        object.__setattr__(self, 'validator_findings', findings)
        object.__setattr__(self, 'status', _enum(FeedbackStatus, self.status))
        if self.status != FeedbackStatus.CAPTURED and not self.review_notes.strip():
            raise LLMToolError('reviewed feedback requires review notes')
        _id(self.id)
        if self.id != _digest(self.identifying_fields()):
            raise LLMToolError('feedback identifier does not match identifying fields')
        self.to_json()

    def identifying_fields(self):
        return {'skill': self.skill, 'skill_version': self.skill_version, 'input': self.input,
                'actual_output': self.actual_output, 'expected_behavior': self.expected_behavior,
                'failure_tags': [tag.value for tag in self.failure_tags],
                'validator_findings': [finding.to_dict() for finding in self.validator_findings]}

    @classmethod
    def capture(cls, skill, skill_version, input, actual_output, expected_behavior,
                failure_tags=(), validator_findings=()):
        tags = tuple(_enum(FailureTag, tag) for tag in _items(failure_tags))
        findings = tuple(_typed_finding(f) for f in _items(validator_findings))
        data = {'skill': skill, 'skill_version': skill_version, 'input': input,
                'actual_output': actual_output, 'expected_behavior': expected_behavior,
                'failure_tags': [t.value for t in tags], 'validator_findings': [f.to_dict() for f in findings]}
        # Bound/type-check before JSON can see attacker-controlled values.
        validate_name(skill)
        _version(skill_version)
        _text(input)
        _text(actual_output, empty=True)
        _text(expected_behavior)
        for finding in findings:
            _finding(finding.to_dict())
        return cls(_digest(data), skill, skill_version, input, actual_output, expected_behavior, tags, findings)

    def to_dict(self):
        return {'id': self.id, **self.identifying_fields(), 'review_notes': self.review_notes, 'status': self.status.value}

    def to_json(self):
        return _json(self.to_dict())


def parse_feedback_case(text):
    data = _parse(text)
    _schema(data, (field.name for field in fields(FeedbackCase)))
    data['validator_findings'] = tuple(_finding(f) for f in _items(data['validator_findings']))
    return FeedbackCase(**data)


def feedback_skill_state(case):
    current = load_skill(case.skill).version
    return (SkillVersionState.CURRENT if case.skill_version == current else
            SkillVersionState.OLDER_THAN_CURRENT if case.skill_version < current else SkillVersionState.NEWER_THAN_CURRENT)


def transition_feedback(case, status, review_notes=None):
    """Exactly one forward lifecycle step; human review is an explicit data operation."""
    target = _enum(FeedbackStatus, status)
    order = tuple(FeedbackStatus)
    if order.index(target) != order.index(case.status) + 1:
        raise LLMToolError('feedback transition must advance exactly one lifecycle step')
    return replace(case, status=target, review_notes=case.review_notes if review_notes is None else review_notes)


@dataclass(frozen=True)
class SkillPatchProposal:
    skill: str
    from_version: int
    proposed_version: int
    rationale: str
    added_rules: tuple[str, ...]
    changed_rules: tuple[str, ...]
    linked_feedback_case_ids: tuple[str, ...]

    def __post_init__(self):
        validate_name(self.skill)
        _version(self.from_version)
        _version(self.proposed_version)
        if self.proposed_version != self.from_version + 1:
            raise LLMToolError('skill proposal version must increment exactly by one')
        _reject_credentials(_text(self.rationale))
        for name in ('added_rules', 'changed_rules'):
            rules = _items(getattr(self, name))
            for rule in rules:
                _reject_credentials(_text(rule))
            object.__setattr__(self, name, rules)
        if not self.added_rules and not self.changed_rules:
            raise LLMToolError('skill proposal requires a declarative rule change')
        object.__setattr__(self, 'linked_feedback_case_ids', _unique_ids(self.linked_feedback_case_ids))
        self.to_json()

    def to_dict(self):
        return {'skill': self.skill, 'from_version': self.from_version, 'proposed_version': self.proposed_version,
                'rationale': self.rationale, 'added_rules': list(self.added_rules),
                'changed_rules': list(self.changed_rules), 'linked_feedback_case_ids': list(self.linked_feedback_case_ids)}

    def to_json(self):
        return _json(self.to_dict())


def parse_skill_patch_proposal(text):
    data = _parse(text)
    _schema(data, (field.name for field in fields(SkillPatchProposal)))
    return SkillPatchProposal(**data)


def propose_skill_patch(case, rationale, added_rules=(), changed_rules=()):
    if case.status != FeedbackStatus.REVIEWED or feedback_skill_state(case) != SkillVersionState.CURRENT:
        raise LLMToolError('proposal requires a reviewed case for the current packaged skill version')
    return SkillPatchProposal(case.skill, case.skill_version, case.skill_version + 1,
                              rationale, added_rules, changed_rules, (case.id,))


@dataclass(frozen=True)
class RegressionAssertion:
    type: AssertionType
    value: Optional[str] = None
    command: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, 'type', _enum(AssertionType, self.type))
        if self.type == AssertionType.CONTEXT_VALID:
            if self.value is not None or self.command is not None:
                raise LLMToolError('CONTEXT_VALID assertion takes no value or command')
        else:
            _reject_credentials(_text(self.value))
        if self.type == AssertionType.COMMAND_RISK_EQUALS:
            _enum(CommandRisk, self.value)
            if self.command is not None:
                _reject_credentials(_text(self.command, limit=8192))
        elif self.command is not None:
            raise LLMToolError('only command risk assertions support command selection')

    def to_dict(self):
        return {'type': self.type.value, 'value': self.value, 'command': self.command}


@dataclass(frozen=True)
class RegressionCase:
    id: str
    source_feedback_ids: tuple[str, ...]
    skill: str
    minimum_skill_version: int
    input: str
    assertions: tuple[RegressionAssertion, ...]

    def __post_init__(self):
        validate_name(self.skill)
        _version(self.minimum_skill_version)
        _reject_credentials(_text(self.input))
        object.__setattr__(self, 'source_feedback_ids', _unique_ids(self.source_feedback_ids))
        assertions = _items(self.assertions, ASSERTION_LIMIT)
        if not assertions or any(type(a) is not RegressionAssertion for a in assertions):
            raise LLMToolError('regression requires bounded typed assertions')
        object.__setattr__(self, 'assertions', assertions)
        _id(self.id)
        if self.id != _digest(self.identifying_fields()):
            raise LLMToolError('regression identifier does not match identifying fields')
        self.to_json()

    def identifying_fields(self):
        return {'source_feedback_ids': list(self.source_feedback_ids), 'skill': self.skill,
                'minimum_skill_version': self.minimum_skill_version, 'input': self.input,
                'assertions': [a.to_dict() for a in self.assertions]}

    @classmethod
    def create(cls, source_feedback_ids, skill, minimum_skill_version, input, assertions):
        ids = _unique_ids(source_feedback_ids)
        validate_name(skill)
        _version(minimum_skill_version)
        _text(input)
        assertions = _items(assertions, ASSERTION_LIMIT)
        if any(type(a) is not RegressionAssertion for a in assertions):
            raise LLMToolError('regression requires typed assertions')
        data = {'source_feedback_ids': list(ids), 'skill': skill, 'minimum_skill_version': minimum_skill_version,
                'input': input, 'assertions': [a.to_dict() for a in assertions]}
        return cls(_digest(data), ids, skill, minimum_skill_version, input, assertions)

    def to_dict(self):
        return {'id': self.id, **self.identifying_fields()}

    def to_json(self):
        return _json(self.to_dict())


def parse_regression_case(text, *, context='feedback'):
    data = _parse(text, context=context)
    _schema(data, (field.name for field in fields(RegressionCase)), context=context)
    assertions = []
    try:
        for assertion in _items(data['assertions'], ASSERTION_LIMIT, context=context):
            _schema(assertion, ('type', 'value', 'command'), context=context)
            assertions.append(RegressionAssertion(**assertion))
        return RegressionCase(**{**data, 'assertions': assertions})
    except LLMToolError as exc:
        # Reused A7 typed fields have fixed safe errors; preserve their semantics
        # while identifying benchmark-owned cases. Never catch unexpected errors.
        message = str(exc)
        if context != 'feedback' and message.startswith('feedback '):
            raise LLMToolError(context + message[len('feedback'):]) from None
        raise


@dataclass(frozen=True)
class AssertionResult:
    index: int
    type: AssertionType
    passed: bool
    message: str

    def to_dict(self):
        return {'index': self.index, 'type': self.type.value, 'passed': self.passed, 'message': self.message}


@dataclass(frozen=True)
class RegressionResult:
    case_id: str
    results: tuple[AssertionResult, ...]

    def __post_init__(self):
        object.__setattr__(self, 'results', tuple(self.results))

    @property
    def passed(self):
        return all(result.passed for result in self.results)

    def to_dict(self):
        return {'case_id': self.case_id, 'passed': self.passed, 'results': [r.to_dict() for r in self.results]}

    def to_json(self):
        return _json(self.to_dict())


def evaluate_regression(case, output, *, context='feedback'):
    """Evaluate supplied output only. Input is provenance, not a prompt to a provider."""
    if type(case) is not RegressionCase:
        raise LLMToolError('regression requires a RegressionCase')
    if load_skill(case.skill).version < case.minimum_skill_version:
        raise LLMToolError('regression requires a newer packaged skill version')
    _text(output, empty=True, limit=JSON_LIMIT, context=context)
    packet = validation = None
    structured_error = False
    try:
        packet = parse_context_packet(output)
        validation = validate_context_packet(packet)
    except LLMToolError:
        structured_error = True
    results = []
    for index, assertion in enumerate(case.assertions):
        kind, value = assertion.type, assertion.value
        passed = False
        message = 'assertion failed'
        if kind in (AssertionType.TEXT_CONTAINS, AssertionType.TEXT_NOT_CONTAINS):
            passed = (value in output) == (kind == AssertionType.TEXT_CONTAINS)
        elif kind == AssertionType.COMMAND_RISK_EQUALS:
            command = assertion.command
            if command is None:
                command = output.rstrip('\r\n')
                present = True
            else:
                present = command in output.splitlines() or (packet is not None and command in packet.verify_next)
            if present:
                try:
                    passed = classify_command(command).risk.value == value
                except LLMToolError:
                    message = 'candidate command cannot be assessed within validator bounds'
            else:
                message = 'selected command is absent from candidate output'
        elif structured_error:
            message = 'candidate is not a bounded structurally supported ContextPacket'
        elif kind == AssertionType.CONTEXT_VALID:
            passed = validation.valid
        elif kind in (AssertionType.FINDING_PRESENT, AssertionType.FINDING_ABSENT):
            passed = (value in {f.code for f in validation.findings}) == (kind == AssertionType.FINDING_PRESENT)
        else:
            category = {'FACT': 'facts', 'HYPOTHESIS': 'hypotheses', 'UNKNOWN': 'unknowns', 'VERIFY_NEXT': 'verify_next'}
            prefix = kind.value.split('_NOT_CONTAINS')[0] if kind.value.endswith('_NOT_CONTAINS') else kind.value[:-len('_CONTAINS')]
            present = value in getattr(packet, category[prefix])
            passed = not present if kind.value.endswith('_NOT_CONTAINS') else present
        results.append(AssertionResult(index, kind, passed, 'assertion passed' if passed else message))
    return RegressionResult(case.id, tuple(results))
