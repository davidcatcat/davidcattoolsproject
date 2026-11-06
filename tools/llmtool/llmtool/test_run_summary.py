"""Bounded, caller-asserted summaries for deterministic TEST-1 plans.

This module validates receipt shape and binds it to a known plan and an
expected Git HEAD. It cannot prove that a command ran, that its output came
from that command, or that a referenced log is authentic. It never accepts or
returns raw logs, excerpts, or filesystem paths.
"""

import hashlib
import json
import re

from .test_output_excerpts import _CLOUD_KEY
from .test_selection import IMPACT_MAP_VERSION, select_test_plan


SUMMARY_SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_SUMMARY_BYTES = 64 * 1024
MAX_CHANGED_PATHS = 64
MAX_PATH_CHARS = 512
MAX_COMMANDS = 64
MAX_ARG_CHARS = 1024
MAX_FAILURE_IDS = 128
MAX_FAILURE_ID_CHARS = 160
MAX_TEST_COUNT = 10**7
MAX_INPUT_NODES = 4096
MAX_INPUT_DEPTH = 16
MAX_INPUT_CONTAINER_ITEMS = 256
MAX_INPUT_STRING_CHARS = 4096

_HEAD = re.compile(r'[0-9a-f]{40}')
_DIGEST_REF = re.compile(r'sha256:([0-9a-f]{64})')
_FAILURE_ID = re.compile(
    r'[A-Za-z_][A-Za-z0-9_.-]{0,95}'
    r'(?:::[A-Za-z_][A-Za-z0-9_.-]{0,95}'
    r'(?:\[[A-Za-z0-9_.:-]{1,64}\])?)?')
_PYTEST_FAILURE_ID = re.compile(
    r'(?:(?:tools/llmtool/)?tests/)?test_[A-Za-z0-9_]{1,80}\.py::'
    r'[A-Za-z_][A-Za-z0-9_.-]{0,95}'
    r'(?:\[[A-Za-z0-9_.:-]{1,64}\])?')
_SENSITIVE_ID = re.compile(
    r'-----BEGIN|' + _CLOUD_KEY + r'|\bsk-[A-Za-z0-9_-]{12,}|'
    r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|'
    r'\bxox[abprs]-[A-Za-z0-9-]{20,}|\beyJ[A-Za-z0-9_-]{10,}\.'
    r'[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|'
    r'(?:api[_-]?key|password|passwd|token|secret|bearer|authorization)[:=]',
    re.IGNORECASE)
_COUNT_FIELDS = ('tests_run', 'passed', 'failed', 'errors', 'skipped')


def _invalid():
    # Do not echo caller-controlled values (which may contain logs or secrets).
    raise ValueError('test-run-summary: invalid or unverified evidence; details omitted')


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('ascii')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid()


def _bounded_input(value, max_bytes):
    """Bound JSON-shaped caller data before json.dumps can allocate output."""
    pending = [(value, 0)]
    estimated_bytes = 0
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_INPUT_NODES or depth > MAX_INPUT_DEPTH:
            _invalid()
        if type(item) is dict:
            if len(item) > MAX_INPUT_CONTAINER_ITEMS:
                _invalid()
            estimated_bytes += 2 + max(0, len(item) - 1)
            for key, child in item.items():
                if type(key) is not str or len(key) > MAX_INPUT_STRING_CHARS:
                    _invalid()
                # ensure_ascii=True escapes at most six ASCII bytes per code
                # point, plus quotes. This upper bound needs no large buffer.
                estimated_bytes += 6 * len(key) + 2 + 1
                pending.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > MAX_INPUT_CONTAINER_ITEMS:
                _invalid()
            estimated_bytes += 2 + max(0, len(item) - 1)
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > MAX_INPUT_STRING_CHARS:
                _invalid()
            estimated_bytes += 6 * len(item) + 2
        elif type(item) is int:
            if item.bit_length() > 64:
                _invalid()
            estimated_bytes += 21
        elif item is None or type(item) is bool:
            estimated_bytes += 5
        else:
            _invalid()
        if estimated_bytes > max_bytes:
            _invalid()
    return estimated_bytes


def _sha256(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _digest(value):
    if type(value) is not str or not _DIGEST_REF.fullmatch(value):
        _invalid()
    return value


def _head(value):
    if type(value) is not str or not _HEAD.fullmatch(value):
        _invalid()
    return value


def _commands(plan):
    commands = []
    for level in plan['levels']:
        if level['level'] == 'L0':
            commands.append(level['command'])
        elif level['level'] in ('L1', 'L2'):
            commands.extend(item['argv'] for item in level['commands'])
        else:
            _invalid()
    if not commands or len(commands) > MAX_COMMANDS:
        _invalid()
    for argv in commands:
        if (type(argv) is not list or not argv or len(argv) > 32
                or any(type(arg) is not str or not arg or len(arg) > MAX_ARG_CHARS
                       or '\x00' in arg for arg in argv)):
            _invalid()
    return commands


def _validated_plan(plan):
    if type(plan) is not dict:
        _invalid()
    _bounded_input(plan, MAX_PLAN_BYTES)
    raw = _canonical(plan)
    if len(raw) > MAX_PLAN_BYTES:
        _invalid()
    paths = plan.get('changed_paths')
    if (type(paths) is not list or not paths or len(paths) > MAX_CHANGED_PATHS
            or any(type(path) is not str or not path or len(path) > MAX_PATH_CHARS
                   for path in paths)
            or paths != sorted(set(paths))):
        _invalid()
    if plan.get('impact_map_version') != IMPACT_MAP_VERSION:
        _invalid()
    try:
        expected = select_test_plan(paths)
    except (TypeError, ValueError):
        _invalid()
    # Rebuilding with the current conservative map rejects forged plans,
    # unknown paths, high-risk paths, stale maps, and modified commands.
    if raw != _canonical(expected) or expected['fallback_reasons']:
        _invalid()
    if any(level['level'] == 'L3' for level in expected['levels']):
        _invalid()
    return expected, _sha256(raw), _commands(expected)


def _counts(value):
    if type(value) is not dict or set(value) != set(_COUNT_FIELDS):
        _invalid()
    result = {}
    for name in _COUNT_FIELDS:
        item = value[name]
        if type(item) is not int or not 0 <= item <= MAX_TEST_COUNT:
            _invalid()
        result[name] = item
    if result['tests_run'] != sum(result[name] for name in _COUNT_FIELDS[1:]):
        _invalid()
    return result


def _failure_ids(value, counts):
    if type(value) is not list or len(value) > MAX_FAILURE_IDS:
        _invalid()
    identifiers = []
    for identifier in value:
        if (type(identifier) is not str or len(identifier) > MAX_FAILURE_ID_CHARS
                or not (_FAILURE_ID.fullmatch(identifier)
                        or _PYTEST_FAILURE_ID.fullmatch(identifier))
                or '..' in identifier or _SENSITIVE_ID.search(identifier)):
            _invalid()
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        _invalid()
    if identifiers and counts['failed'] + counts['errors'] == 0:
        _invalid()
    return sorted(identifiers)


def summarize_test_run(plan, receipt, *, expected_head):
    """Return a bounded summary bound to a known plan and caller's expected HEAD.

    ``receipt`` is an assertion supplied by the caller. Its digests, counts,
    exit statuses, and failure IDs are not independently verified here.
    ``expected_head`` must come from the caller's separately established source
    context; this pure function does not inspect Git or execute commands.

    Receipt schema::

        {
          "schema_version": 1,
          "head_sha": "<40 lowercase hex>",
          "plan_sha256": "sha256:<64 lowercase hex>",
          "commands": [{
            "argv_sha256": "sha256:<64 lowercase hex>",
            "exit_status": 0,
            "log_sha256": "sha256:<64 lowercase hex>",
            "log_ref": "sha256:<same 64 lowercase hex>",
            "counts": {"tests_run": 1, "passed": 1, "failed": 0,
                       "errors": 0, "skipped": 0},
            "failure_ids": []
          }]
        }

    Exact-key validation rejects extra raw-log, excerpt, and path fields. Log
    references must be content digests, never filesystem or URL references.
    """
    head = _head(expected_head)
    expected_plan, plan_digest, argv_list = _validated_plan(plan)
    if type(receipt) is not dict:
        _invalid()
    _bounded_input(receipt, MAX_RECEIPT_BYTES)
    raw_receipt = _canonical(receipt)
    if len(raw_receipt) > MAX_RECEIPT_BYTES:
        _invalid()
    if set(receipt) != {'schema_version', 'head_sha', 'plan_sha256', 'commands'}:
        _invalid()
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != SUMMARY_SCHEMA_VERSION:
        _invalid()
    if _head(receipt['head_sha']) != head or _digest(receipt['plan_sha256']) != plan_digest:
        _invalid()
    submitted = receipt['commands']
    if type(submitted) is not list or len(submitted) != len(argv_list):
        _invalid()

    commands = []
    overall = 'ALL_EXIT_ZERO'
    for ordinal, (argv, item) in enumerate(zip(argv_list, submitted)):
        if type(item) is not dict or set(item) != {
                'argv_sha256', 'exit_status', 'log_sha256', 'log_ref', 'counts', 'failure_ids'}:
            _invalid()
        argv_digest = _sha256(_canonical(argv))
        if _digest(item['argv_sha256']) != argv_digest:
            _invalid()
        exit_status = item['exit_status']
        if type(exit_status) is not int or not 0 <= exit_status <= 255:
            _invalid()
        counts = _counts(item['counts'])
        failure_ids = _failure_ids(item['failure_ids'], counts)
        if exit_status == 0 and (counts['failed'] or counts['errors']):
            _invalid()
        log_digest = _digest(item['log_sha256'])
        if _digest(item['log_ref']) != log_digest:
            _invalid()
        if exit_status:
            overall = 'HAS_NONZERO_EXIT'
        commands.append({
            'ordinal': ordinal,
            'argv_sha256': argv_digest,
            'exit_status': exit_status,
            'log_sha256': log_digest,
            'log_ref': log_digest,
            'counts': counts,
            'failure_ids': failure_ids,
            'failure_ids_completeness': 'NOT_VERIFIED',
        })

    summary = {
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'reported_status': overall,
        'head_sha': head,
        'plan_sha256': plan_digest,
        'impact_map_version': expected_plan['impact_map_version'],
        'selected_tests': [test for level in expected_plan['levels']
                           for test in level.get('tests', [])],
        'provenance': 'CALLER_ASSERTED_UNVERIFIED',
        'execution_verified': False,
        'logs_verified': False,
        'commands': commands,
    }
    encoded = _canonical(summary)
    if len(encoded) > MAX_SUMMARY_BYTES:
        _invalid()
    return summary
