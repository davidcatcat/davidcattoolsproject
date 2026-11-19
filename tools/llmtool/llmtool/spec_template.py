"""#1817 PR 1: standard dispatch specification schema and tool-side data builder.

The design (docs/design/dispatch-spec-template.md, section 5 PR 1) defines one
fixed field set for each of the five dispatch kinds and separates the fields a
PM writes (problem, goal, acceptance) from the fields the tool fills from source
facts (ids, revisions, scopes, lineage, carried counts, author, receipts and
digests). This module owns that split and the auto-fill rules only.

There is deliberately no caller yet: task submission, High Debug, review
dispatch and spec_preflight are wired in design PR 3, and their per-kind checks
arrive in PR 2. A source fact that is missing or contradicts another fact raises
a named refusal; the builder never guesses a value or falls back to a default
that the design does not itself prescribe.

Source-fact model
-----------------
- ``fresh`` is required for ``developer`` and ``reviewer`` so a fresh dispatch
  and a continuation can never be confused by an absent flag.
- Repair and debug source facts must name the predecessor and lineage; a repair
  must also name the predecessor's verified failed HEAD and the verified
  ancestor merge-base, and its revisions must match that evidence.
- Carried counts are derived from the predecessor audit record; a caller value
  that contradicts the audit is refused.
- The author route comes from the authoritative dispatch gate
  (``worker_dispatch.evaluate``); a caller route that contradicts the decision
  is refused.
- Read scope derives paths cited in acceptance, including repository paths
  outside the tool/document directories, and, when code is written, needs an
  admitted test module (design section 2.2).
- Base/result-head rules follow design section 2.2, including the three-dot
  ``review_range`` for reviewers.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from . import worker_dispatch
from .errors import LLMToolError

KINDS = ('developer', 'investigation', 'debug', 'reviewer', 'sub_pm')
READ_ONLY_KINDS = frozenset(('investigation', 'debug', 'reviewer'))
AUTHOR_ROUTES = frozenset(('ALLOWED', 'OWNER_ASSIGNMENT_ONLY', 'TEMPLATE_ONLY'))

# Design section 2.2: values a fresh Task sets by definition, not values guessed
# when a fact is absent.
FRESH_CARRIED_COUNTS = {'repairs': 0, 'test_failures': 0, 'attempts': 1}

# Design refusal codes used where the catalogue covers the condition. Codes not
# in the PR 2 catalogue are named here so every refused source fact is fixed.
SPEC_UNKNOWN_KIND = 'SPEC_UNKNOWN_KIND'
SPEC_SOURCE_FACTS_INVALID = 'SPEC_SOURCE_FACTS_INVALID'
SPEC_PM_FIELD_MISSING = 'SPEC_PM_FIELD_MISSING'
SPEC_ID_INVALID = 'SPEC_ID_INVALID'
SPEC_BASE_STALE = 'SPEC_BASE_STALE'
SPEC_REVIEW_HEAD_INVALID = 'SPEC_REVIEW_HEAD_INVALID'
SPEC_REVIEW_RANGE_INVALID = 'SPEC_REVIEW_RANGE_INVALID'
SPEC_WRITE_PATHS_EMPTY = 'SPEC_WRITE_PATHS_EMPTY'
SPEC_READONLY_ROLE_HAS_WRITES = 'SPEC_READONLY_ROLE_HAS_WRITES'
SPEC_SCOPE_INVALID = 'SPEC_SCOPE_INVALID'
SPEC_PREDECESSOR_MISSING = 'SPEC_PREDECESSOR_MISSING'
SPEC_LINEAGE_INVALID = 'SPEC_LINEAGE_INVALID'
SPEC_CARRIED_COUNTS_INVALID = 'SPEC_CARRIED_COUNTS_INVALID'
SPEC_FRESHNESS_MISSING = 'SPEC_FRESHNESS_MISSING'
SPEC_FRESHNESS_INVALID = 'SPEC_FRESHNESS_INVALID'
SPEC_DISPATCH_UNAUTHORIZED = 'SPEC_DISPATCH_UNAUTHORIZED'
SPEC_DIGEST_INVALID = 'SPEC_DIGEST_INVALID'
SPEC_CHECK_RESULTS_INVALID = 'SPEC_CHECK_RESULTS_INVALID'
SPEC_SCOPE_TEST_MODULE_MISSING = 'SPEC_SCOPE_TEST_MODULE_MISSING'

# Design section 2.1: every kind carries the same tool-owned fields; a reviewer
# additionally carries the explicit three-dot review range.
COMMON_FIELDS = (
    'kind', 'task_id', 'run_id', 'base', 'result_head', 'author', 'problem',
    'goal', 'acceptance', 'read_paths', 'write_paths', 'predecessor_spec',
    'continued_lineage', 'carried_counts', 'check_results', 'role_card_digest',
    'contract_digest',
)
FIELDS_BY_KIND = {
    kind: COMMON_FIELDS + (('review_range',) if kind == 'reviewer' else ())
    for kind in KINDS
}

_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_COUNT_FIELDS = ('repairs', 'test_failures', 'attempts')
_ACCEPTANCE_PATH = re.compile(
    r'(?<![a-zA-Z0-9_./-])'
    r'('
    r'(?:[a-zA-Z0-9_.-]+/)+[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]+'
    r'|(?:AGENTS|CLAUDE|README)\.md'
    r')'
    r'(?![a-zA-Z0-9_/-]|\.[a-zA-Z0-9])'
)
# Design section 3.1: unlisted workers are non-dispatchable templates only.
_TEMPLATE_ONLY_REASONS = frozenset((
    'WORKER_UNKNOWN', 'WORKER_POLICY_ENTRY_MISSING',
))
# Design examples: a dispatchable author route yields a passing preflight
# receipt; a non-dispatchable TEMPLATE_ONLY route yields the unauthorized status.
_PREFLIGHT_STATUS_BY_ROUTE = {
    'ALLOWED': 'SPEC_PREFLIGHT_OK',
    'OWNER_ASSIGNMENT_ONLY': 'SPEC_PREFLIGHT_OK',
    'TEMPLATE_ONLY': SPEC_DISPATCH_UNAUTHORIZED,
}


class SpecTemplateError(LLMToolError):
    """A dispatch spec cannot be built from the supplied source facts."""

    def __init__(self, code, field=None):
        super().__init__(code)
        self.spec_code = code
        self.spec_field = field


@dataclass(frozen=True)
class DispatchSourceFacts:
    """Source facts the tool reads before it fills a dispatch specification."""

    task_id: str
    run_id: str
    base: str | None
    result_head: str | None
    canonical_main: str | None = None
    fresh: bool | None = None
    failed_head: str | None = None
    verified_merge_base: str | None = None
    review_range: str | None = None
    read_paths: tuple[str, ...] | list[str] | None = None
    write_paths: tuple[str, ...] | list[str] | None = None
    predecessor_spec: dict | None = None
    continued_lineage: tuple[str, ...] | list[str] | None = None
    predecessor_audit: dict | None = None
    carried_counts: dict | None = None
    author_role: str | None = None
    author_worker: str | None = None
    author_route: str | None = None
    owner_ref: str | None = None
    role_card_digest: str | None = None
    contract_digest: str | None = None
    check_results: dict | None = None


def _refuse(code, field=None):
    raise SpecTemplateError(code, field) from None


def _text(value, code, field):
    if type(value) is not str or not value.strip():
        _refuse(code, field)
    return value


def _hex(value, size, code, field):
    pattern = {32: _HEX32, 40: _HEX40, 64: _HEX64}[size]
    if type(value) is not str or pattern.fullmatch(value) is None:
        _refuse(code, field)
    return value


def _path_list(value, field):
    if type(value) not in (list, tuple):
        _refuse(SPEC_SCOPE_INVALID, field)
    paths = []
    for item in value:
        if type(item) is not str or not item:
            _refuse(SPEC_SCOPE_INVALID, field)
        if item not in paths:
            paths.append(item)
    return paths


def _norm_path(path):
    return path.replace('\\', '/').removeprefix('./')


def _cited_paths(acceptance):
    """Repository paths cited in acceptance, in first-seen order."""
    cited = []
    for item in acceptance:
        for path in _ACCEPTANCE_PATH.findall(item):
            if path not in cited:
                cited.append(path)
    return cited


def _code_writes(writes):
    return [
        path for path in writes
        if path.endswith('.py')
        and (path.startswith('tools/') or path.startswith('scripts/'))
        and not path.startswith('tools/llmtool/tests/')
    ]


def _has_test_module(reads, code_writes):
    test_dirs = ['tools/llmtool/tests/']
    if all(path.startswith('scripts/') for path in code_writes):
        test_dirs.append('scripts/')
    for path in reads:
        for test_dir in test_dirs:
            name = path[len(test_dir):] if path.startswith(test_dir) else ''
            if name.startswith('test_') and name.endswith('.py') and '/' not in name:
                return True
    return False


def _predecessor(value):
    if value is None:
        _refuse(SPEC_PREDECESSOR_MISSING, 'predecessor_spec')
    if type(value) is not dict or set(value) != {'task_id', 'contract_digest'}:
        _refuse(SPEC_LINEAGE_INVALID, 'predecessor_spec')
    return {
        'task_id': _hex(value['task_id'], 32, SPEC_LINEAGE_INVALID, 'predecessor_spec'),
        'contract_digest': _hex(value['contract_digest'], 64, SPEC_LINEAGE_INVALID,
                                'predecessor_spec'),
    }


def _lineage(value):
    if value is None:
        return []
    if type(value) not in (list, tuple):
        _refuse(SPEC_LINEAGE_INVALID, 'continued_lineage')
    lineage = []
    for item in value:
        if type(item) is not str or not item or item in lineage:
            _refuse(SPEC_LINEAGE_INVALID, 'continued_lineage')
        lineage.append(item)
    return lineage


def _counts(value, *, require_failure, require_increment):
    if type(value) is not dict or set(value) != set(_COUNT_FIELDS):
        _refuse(SPEC_CARRIED_COUNTS_INVALID, 'carried_counts')
    for key in _COUNT_FIELDS:
        if type(value[key]) is not int or value[key] < 0:
            _refuse(SPEC_CARRIED_COUNTS_INVALID, 'carried_counts')
    if require_failure and value['repairs'] + value['test_failures'] < 1:
        _refuse(SPEC_CARRIED_COUNTS_INVALID, 'carried_counts')
    if require_increment and value['attempts'] < 2:
        _refuse(SPEC_CARRIED_COUNTS_INVALID, 'carried_counts')
    return {key: value[key] for key in _COUNT_FIELDS}


def _audit_counts(value):
    """The predecessor's recorded audit counts, the source of carried values."""
    if type(value) is not dict or set(value) != set(_COUNT_FIELDS):
        _refuse(SPEC_CARRIED_COUNTS_INVALID, 'predecessor_audit')
    for key in _COUNT_FIELDS:
        if type(value[key]) is not int or value[key] < 0:
            _refuse(SPEC_CARRIED_COUNTS_INVALID, 'predecessor_audit')
    return {key: value[key] for key in _COUNT_FIELDS}


def _fresh_lineage(facts, carried=None):
    """Return the design's fresh lineage and counts, refusing contradictions."""
    if facts.predecessor_spec is not None:
        _refuse(SPEC_LINEAGE_INVALID, 'predecessor_spec')
    lineage = _lineage(facts.continued_lineage)
    if lineage:
        _refuse(SPEC_LINEAGE_INVALID, 'continued_lineage')
    if carried is None:
        return None, [], dict(FRESH_CARRIED_COUNTS)
    counts = _counts(carried, require_failure=False, require_increment=False)
    if counts != FRESH_CARRIED_COUNTS:
        _refuse(SPEC_CARRIED_COUNTS_INVALID, 'carried_counts')
    return None, [], counts


def _continued_lineage(facts, carried, *, require_failure, require_increment):
    """Return (predecessor, lineage, counts) for repair, re-review or debug."""
    predecessor = _predecessor(facts.predecessor_spec)
    lineage = _lineage(facts.continued_lineage)
    if not lineage:
        _refuse(SPEC_PREDECESSOR_MISSING, 'continued_lineage')
    # The ordered lineage ends at the immediate predecessor; a predecessor that
    # appears anywhere else is not the predecessor chain (review F1).
    if lineage[-1] != predecessor['task_id']:
        _refuse(SPEC_LINEAGE_INVALID, 'continued_lineage')
    audit = _audit_counts(facts.predecessor_audit)
    expected = {
        'repairs': audit['repairs'] + (1 if require_failure else 0),
        'test_failures': audit['test_failures'],
        'attempts': audit['attempts'] + 1,
    }
    counts = _counts(expected, require_failure=require_failure,
                     require_increment=require_increment)
    if carried is not None:
        supplied = _counts(carried, require_failure=require_failure,
                           require_increment=require_increment)
        if supplied != counts:
            _refuse(SPEC_CARRIED_COUNTS_INVALID, 'carried_counts')
    return predecessor, lineage, counts


def _evaluate(worker, owner_ref, task_id):
    """Evaluate the authoritative dispatch gate for one author binding."""
    return worker_dispatch.evaluate(worker, owner_ref, task_id)


def _author(facts, task_id):
    role = _text(facts.author_role, SPEC_DISPATCH_UNAUTHORIZED, 'author.role')
    worker = _text(facts.author_worker, SPEC_DISPATCH_UNAUTHORIZED, 'author.worker')
    decision = _evaluate(worker, facts.owner_ref, task_id)
    if getattr(decision, 'worker_key', None) != worker:
        _refuse(SPEC_DISPATCH_UNAUTHORIZED, 'author.worker')
    reason = getattr(decision, 'reason_code', None)
    allowed = getattr(decision, 'allowed', None)
    if allowed is True and reason == 'OWNER_POLICY_ALLOWED':
        route = 'ALLOWED'
    elif allowed is True and reason == 'OWNER_ASSIGNMENT_MATCHED':
        route = 'OWNER_ASSIGNMENT_ONLY'
    elif allowed is False and reason in _TEMPLATE_ONLY_REASONS:
        route = 'TEMPLATE_ONLY'
    else:
        _refuse(SPEC_DISPATCH_UNAUTHORIZED, 'author.route')
    if facts.author_route is not None and facts.author_route != route:
        _refuse(SPEC_DISPATCH_UNAUTHORIZED, 'author.route')
    owner_ref = facts.owner_ref
    if route == 'OWNER_ASSIGNMENT_ONLY':
        if type(owner_ref) is not str or not owner_ref:
            _refuse(SPEC_DISPATCH_UNAUTHORIZED, 'author.owner_ref')
    else:
        if owner_ref not in (None, ''):
            _refuse(SPEC_DISPATCH_UNAUTHORIZED, 'author.owner_ref')
        owner_ref = None
    return {'role': role, 'worker': worker, 'route': route, 'owner_ref': owner_ref}


def _pm_fields(problem, goal, acceptance):
    problem = _text(problem, SPEC_PM_FIELD_MISSING, 'problem')
    goal = _text(goal, SPEC_PM_FIELD_MISSING, 'goal')
    if type(acceptance) not in (list, tuple) or not acceptance:
        _refuse(SPEC_PM_FIELD_MISSING, 'acceptance')
    items = []
    for item in acceptance:
        if type(item) is not str or not item.strip():
            _refuse(SPEC_PM_FIELD_MISSING, 'acceptance')
        items.append(item)
    return problem, goal, items


def _scopes(kind, facts, acceptance):
    reads = _path_list(facts.read_paths, 'read_paths')
    seen = {_norm_path(path) for path in reads}
    # Design section 2.2: read scope covers every path cited in acceptance.
    for path in _cited_paths(acceptance):
        normalized = _norm_path(path)
        if normalized not in seen:
            reads.append(normalized)
            seen.add(normalized)
    if kind in READ_ONLY_KINDS:
        if facts.write_paths not in (None, [], ()):
            writes = _path_list(facts.write_paths, 'write_paths')
            if writes:
                _refuse(SPEC_READONLY_ROLE_HAS_WRITES, 'write_paths')
        writes = []
    else:
        if type(facts.write_paths) not in (list, tuple) or not facts.write_paths:
            _refuse(SPEC_WRITE_PATHS_EMPTY, 'write_paths')
        writes = _path_list(facts.write_paths, 'write_paths')
    # Design section 2.2: read scope covers every write path.
    for path in writes:
        normalized = _norm_path(path)
        if normalized not in seen:
            reads.append(path)
            seen.add(normalized)
    code_writes = _code_writes([_norm_path(path) for path in writes])
    if code_writes and not _has_test_module(
            [_norm_path(path) for path in reads], code_writes):
        _refuse(SPEC_SCOPE_TEST_MODULE_MISSING, 'read_paths')
    return reads, writes


def _fresh_heads(facts):
    main = _hex(facts.canonical_main, 40, SPEC_BASE_STALE, 'canonical_main')
    base = _hex(facts.base, 40, SPEC_BASE_STALE, 'base')
    head = _hex(facts.result_head, 40, SPEC_BASE_STALE, 'result_head')
    if base != main:
        _refuse(SPEC_BASE_STALE, 'base')
    if head != main:
        _refuse(SPEC_BASE_STALE, 'result_head')
    return base, head


def _repair_heads(facts):
    merge_base = _hex(facts.verified_merge_base, 40, SPEC_BASE_STALE,
                      'verified_merge_base')
    base = _hex(facts.base, 40, SPEC_BASE_STALE, 'base')
    if base != merge_base:
        _refuse(SPEC_BASE_STALE, 'base')
    if facts.failed_head is None:
        _refuse(SPEC_BASE_STALE, 'failed_head')
    failed = _hex(facts.failed_head, 40, SPEC_BASE_STALE, 'failed_head')
    head = _hex(facts.result_head, 40, SPEC_BASE_STALE, 'result_head')
    if head != failed:
        _refuse(SPEC_BASE_STALE, 'result_head')
    if base == head:
        _refuse(SPEC_BASE_STALE, 'result_head')
    return base, head


def _review_heads(facts):
    base = _hex(facts.base, 40, SPEC_BASE_STALE, 'base')
    head = _hex(facts.result_head, 40, SPEC_REVIEW_HEAD_INVALID, 'result_head')
    if base == head:
        _refuse(SPEC_REVIEW_HEAD_INVALID, 'result_head')
    return base, head


def _debug_heads(facts):
    base = _hex(facts.base, 40, SPEC_BASE_STALE, 'base')
    head = _hex(facts.result_head, 40, SPEC_REVIEW_HEAD_INVALID, 'result_head')
    if base == head:
        _refuse(SPEC_REVIEW_HEAD_INVALID, 'result_head')
    return base, head


def _digest(value, field):
    return _hex(value, 64, SPEC_DIGEST_INVALID, field)


def _check_results(value, route):
    if type(value) is not dict or not value:
        _refuse(SPEC_CHECK_RESULTS_INVALID, 'check_results')
    status = value.get('preflight_status')
    if type(status) is not str or not status.strip():
        _refuse(SPEC_CHECK_RESULTS_INVALID, 'check_results')
    # The receipt must agree with the route derived from the dispatch gate: a
    # TEMPLATE_ONLY author is non-dispatchable (review F2).
    if status != _PREFLIGHT_STATUS_BY_ROUTE[route]:
        _refuse(SPEC_CHECK_RESULTS_INVALID, 'check_results')
    return dict(value)


def build_spec(kind, problem, goal, acceptance, facts):
    """Build one dispatch spec from PM text plus tool-owned source facts.

    Returns a dict with exactly the design field set for ``kind``. Raises
    SpecTemplateError with a fixed ``spec_code`` when a source fact is missing
    or contradicts another fact.
    """
    if kind not in KINDS:
        _refuse(SPEC_UNKNOWN_KIND, 'kind')
    if not isinstance(facts, DispatchSourceFacts):
        _refuse(SPEC_SOURCE_FACTS_INVALID, 'facts')
    problem, goal, acceptance = _pm_fields(problem, goal, acceptance)
    task_id = _hex(facts.task_id, 32, SPEC_ID_INVALID, 'task_id')
    run_id = _hex(facts.run_id, 32, SPEC_ID_INVALID, 'run_id')
    reads, writes = _scopes(kind, facts, acceptance)
    author = _author(facts, task_id)
    role_card_digest = _digest(facts.role_card_digest, 'role_card_digest')
    contract_digest = _digest(facts.contract_digest, 'contract_digest')
    check_results = _check_results(facts.check_results, author['route'])
    review_range = None

    if kind == 'developer':
        if type(facts.fresh) is not bool:
            _refuse(SPEC_FRESHNESS_MISSING, 'fresh')
        if facts.fresh:
            base, head = _fresh_heads(facts)
            predecessor, lineage, counts = _fresh_lineage(facts, facts.carried_counts)
        else:
            base, head = _repair_heads(facts)
            predecessor, lineage, counts = _continued_lineage(
                facts, facts.carried_counts, require_failure=True,
                require_increment=True)
    elif kind == 'reviewer':
        if type(facts.fresh) is not bool:
            _refuse(SPEC_FRESHNESS_MISSING, 'fresh')
        base, head = _review_heads(facts)
        review_range = f'{base}...{head}'
        if (facts.review_range is not None and
                facts.review_range != review_range):
            _refuse(SPEC_REVIEW_RANGE_INVALID, 'review_range')
        if facts.fresh:
            predecessor, lineage, counts = _fresh_lineage(facts, facts.carried_counts)
        else:
            predecessor, lineage, counts = _continued_lineage(
                facts, facts.carried_counts, require_failure=False,
                require_increment=False)
    elif kind == 'debug':
        if facts.fresh is True:
            _refuse(SPEC_FRESHNESS_INVALID, 'fresh')
        base, head = _debug_heads(facts)
        predecessor, lineage, counts = _continued_lineage(
            facts, facts.carried_counts, require_failure=False,
            require_increment=False)
    else:
        if facts.fresh is False:
            _refuse(SPEC_FRESHNESS_INVALID, 'fresh')
        base, head = _fresh_heads(facts)
        predecessor, lineage, counts = _fresh_lineage(facts, facts.carried_counts)

    spec = {
        'kind': kind,
        'task_id': task_id,
        'run_id': run_id,
        'base': base,
        'result_head': head,
    }
    if kind == 'reviewer':
        spec['review_range'] = review_range
    spec.update({
        'author': author,
        'problem': problem,
        'goal': goal,
        'acceptance': acceptance,
        'read_paths': reads,
        'write_paths': writes,
        'predecessor_spec': predecessor,
        'continued_lineage': lineage,
        'carried_counts': counts,
        'check_results': check_results,
        'role_card_digest': role_card_digest,
        'contract_digest': contract_digest,
    })
    return spec
