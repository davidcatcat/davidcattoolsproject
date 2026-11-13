"""Owner-only, forward-only main CI incident records (#1706 R3, design sections 5 and 9).

One JSON record per incident lives in ``<TaskStore root parent>/main-incidents``,
beside ``merge-trains``, with the same file pattern as ``merge_train_store``: a
record is created once with an atomic hard link, read with schema and digest
checks, and changed only by compare-and-update under an exclusive lock.

Forward only. ``incident_id``, ``created_at``, ``pm_session_id`` (the PM
session that owns the incident, set at scan time), ``signal`` and
``last_good_head`` never change. ``observations`` (one per scan that saw new
evidence: failing head and runs, class and evidence, range, candidate rows and
attribution), ``confirmations`` (PM revert-scope confirmations) and
``outcome_events`` only grow; earlier entries are never rewritten. ``outcome``
starts as UNKNOWN_OPEN and only advances to RESOLVED or SUPERSEDED, which are
final: a final record takes no further change. Every update is checked against
the stored record before it is written.

#1706 R9 adds the optional, append-only ``remote_tests`` list: one entry per
EC2 remote test run for the incident (``remote_test.run_incident_remote_test``).
A record without the field is valid, so records written before R9 still read;
``create`` never writes it. Each entry is checked against the record it lives
in: its SHA must be recorded in the incident (``admitted_shas``), its modules
must be recorded as failing (``failing_modules``) and a stored receipt must
match its own ``receipt_digest``. A stored receipt has exactly the
``REMOTE_RECEIPT_FIELDS`` (plus the optional ``failure_excerpt``); an append
with any other receipt field refuses INCIDENT_RECEIPT_FIELDS_INVALID before
anything is written, and such a field on disk makes the record invalid. Every
structured receipt field must match its fixed grammar, each ``final`` line
must be a unittest summary line or ``FINAL_REDACTED``, and the output-derived
strings (``final`` lines and ``failure_excerpt``) must pass the real
sensitive-content screen, else the record is INCIDENT_RECORD_INVALID
(PR #1763, CWE-532). A stored receipt must also be consistent with its own
``result`` (``receipt_consistent``; Codex v1 F1 on PR #1769): an append of a
well-formed receipt that contradicts its result refuses
INCIDENT_RECEIPT_INCONSISTENT before anything is written, and such a receipt
on disk makes the record INCIDENT_RECORD_INVALID even when its receipt and
record digests were recomputed.

Every doubt fails closed with a typed code in ``error.incident_code``; error
messages carry only the code and, when it is a safe identifier, the incident ID.
"""

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import main_incident
from . import task_store
from .errors import LLMToolError

SCHEMA_VERSION = 1
DIRECTORY_NAME = 'main-incidents'
MAX_RECORD_BYTES = 1024 * 1024
MAX_OBSERVATIONS = 100
MAX_CONFIRMATIONS = 20
MAX_REASON = 1000
MAX_SESSION = 256
MAX_PR = 9999999
READ_ATTEMPTS = 50
LOCK_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.01

INCIDENT_RECORD_INVALID = 'INCIDENT_RECORD_INVALID'
INCIDENT_RECORD_UNAVAILABLE = 'INCIDENT_RECORD_UNAVAILABLE'
INCIDENT_RECORD_EXISTS = 'INCIDENT_RECORD_EXISTS'
INCIDENT_RECORD_CONFLICT = 'INCIDENT_RECORD_CONFLICT'
INCIDENT_RECORD_IMMUTABLE = 'INCIDENT_RECORD_IMMUTABLE'
INCIDENT_RECORD_BUSY = 'INCIDENT_RECORD_BUSY'
INCIDENT_RECORD_FULL = 'INCIDENT_RECORD_FULL'
INCIDENT_OUTCOME_INVALID = 'INCIDENT_OUTCOME_INVALID'
INCIDENT_CLOSED = 'INCIDENT_CLOSED'
INCIDENT_RECEIPT_FIELDS_INVALID = 'INCIDENT_RECEIPT_FIELDS_INVALID'
INCIDENT_RECEIPT_INCONSISTENT = 'INCIDENT_RECEIPT_INCONSISTENT'

OPEN = 'UNKNOWN_OPEN'
RESOLVED = 'RESOLVED'
SUPERSEDED = 'SUPERSEDED'
OUTCOME_MOVES = {
    OPEN: frozenset((RESOLVED, SUPERSEDED)),
    RESOLVED: frozenset(),
    SUPERSEDED: frozenset(),
}
CLASSES = frozenset((main_incident.ENV, main_incident.FLAKY, main_incident.CODE,
                     main_incident.UNKNOWN))
SIGNALS = frozenset(main_incident.KINDS)
ATTRIBUTIONS = frozenset((main_incident.SINGLE, main_incident.BATCH))

FIELDS = frozenset((
    'schema_version', 'incident_id', 'created_at', 'pm_session_id', 'signal',
    'last_good_head', 'observations', 'confirmations', 'outcome', 'outcome_events',
    'record_sha256'))
IMMUTABLE_FIELDS = ('schema_version', 'incident_id', 'created_at', 'pm_session_id', 'signal',
                    'last_good_head')
APPEND_FIELDS = ('observations', 'confirmations', 'outcome_events')
OBSERVATION_FIELDS = frozenset((
    'observed_at', 'signal', 'failing_head', 'failing_runs', 'class', 'evidence', 'range',
    'candidate_rows', 'attribution', 'closure_available'))
RUN_FIELDS = frozenset(('kind', 'id', 'conclusion'))
CONFIRMATION_FIELDS = frozenset((
    'pm_session_id', 'confirmed_at', 'scope', 'reason', 'observation_index',
    'evidence_sha256'))
EVENT_FIELDS = frozenset(('outcome', 'at', 'head'))
REMOTE_TESTS = 'remote_tests'
OPTIONAL_FIELDS = frozenset((REMOTE_TESTS,))
MAX_REMOTE_TESTS = 50
MAX_REMOTE_MODULES = 8
REMOTE_TEST_WHERE = 'EC2'
REMOTE_TEST_FIELDS = frozenset((
    'sha', 'modules', 'result', 'code', 'receipt', 'where', 'recorded_at'))
# A validated EC2 receipt carries one of these results and no outcome code; a
# run without a valid receipt carries one of the outcome results and its code.
REMOTE_RECEIPT_RESULTS = frozenset(('PASS', 'FAIL', 'TIMEOUT', 'SHA_MISMATCH'))
REMOTE_OUTCOME_RESULTS = frozenset(('TIMEOUT', 'UNAVAILABLE', 'UNKNOWN'))
# The exact fields of a stored receipt: the validated EC2 receipt
# (remote_test._RECEIPT_FIELDS) plus the three fields bound before recording.
# ``failure_excerpt`` is the only optional field; nothing else is accepted.
REMOTE_RECEIPT_FIELDS = frozenset((
    'result', 'exit_code', 'source_sha', 'tree', 'fetch_head', 'modules',
    'identity', 'started_at', 'ended_at', 'elapsed_s', 'ran_total', 'final',
    'failures', 'log_bytes', 'log_sha256', 'remote_log_path',
    'incident_id', 'command_digest', 'receipt_digest'))
REMOTE_RECEIPT_OPTIONAL = frozenset(('failure_excerpt',))
# A recorded final line is a unittest summary line or the remote NO_FINAL
# marker; remote_test replaces any other line with FINAL_REDACTED.
_SUMMARY_COUNT = (r'(?:failures|errors|skipped|expected failures|unexpected successes)'
                  r'=[0-9]{1,6}')
FINAL_SUMMARY = re.compile(r'(?:OK|FAILED)(?: \(%s(?:, %s){0,4}\))?|NO_FINAL'
                           % (_SUMMARY_COUNT, _SUMMARY_COUNT))
FINAL_REDACTED = 'REDACTED_NOT_SUMMARY'
# The final lines a PASS receipt can carry: the passing lines the remote
# program and the receipt validator accept that are also summary lines; any
# other line the validator accepts on PASS was redacted before recording.
PASS_FINAL = re.compile(r'OK(?: \(skipped=[1-9][0-9]{0,5}\))?')
# The exit code the remote program sets for each result; FAIL has any
# non-zero code (the failing test process's own code, or 1).
TIMEOUT_EXIT_CODE = 124
SHA_MISMATCH_EXIT_CODE = 41
MAX_FINAL_LINE = 512
MAX_RECEIPT_FAILURES = 10
MAX_LOG_BYTES = 20_000_000
MAX_EXCERPT_BYTES = 4096
MAX_EXCERPT_LINES = 40

INCIDENT_ID = re.compile(r'mi-x?[0-9a-f]{40}')
_RECORD_NAME = re.compile(r'(mi-x?[0-9a-f]{40})\.json')
_SHA40 = re.compile(r'[0-9a-f]{40}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_TEST_MODULE = re.compile(r'test_[a-z0-9_]{1,80}')
_TEST_PATH = re.compile(r'tools/llmtool/tests/(test_[a-z0-9_]{1,80})\.py')
_REMOTE_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
_RECEIPT_STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')
_REMOTE_LOG = re.compile(r'/tmp/llmtool-remote-log\.[A-Za-z0-9]+/remote-test\.log')


def _safe_id(value):
    return type(value) is str and INCIDENT_ID.fullmatch(value) is not None


def _refuse(code, incident_id=None):
    """Raise the typed refusal; the message is the code plus a safe incident ID only."""
    message = code
    if _safe_id(incident_id):
        message = code + ': incident ' + incident_id
    error = LLMToolError(message)
    error.incident_code = code
    raise error from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def record_digest(record):
    """SHA-256 of the canonical JSON of ``record`` without ``record_sha256``."""
    body = {key: item for key, item in record.items() if key != 'record_sha256'}
    return hashlib.sha256(_canonical(body)).hexdigest()


def observation_digest(observation):
    """SHA-256 of the canonical JSON of one observation (the confirmed evidence)."""
    return hashlib.sha256(_canonical(observation)).hexdigest()


def utc_stamp(now=None):
    """UTC ISO time in seconds, ``YYYY-MM-DDTHH:MM:SSZ``; now is an epoch or None."""
    now = time.time() if now is None else now
    return datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha40(value):
    return type(value) is str and _SHA40.fullmatch(value) is not None


def _pr(value):
    return type(value) is int and 1 <= value <= MAX_PR


def _optional_pr(value):
    return value is None or _pr(value)


def _text(value, limit):
    return (type(value) is str and 0 < len(value) <= limit and
            not any(ord(char) < 0x20 or 0x7f <= ord(char) <= 0x9f for char in value))


def _session(value):
    return _text(value, MAX_SESSION) and not any(char.isspace() for char in value)


def _utc_iso(value):
    if type(value) is not str or not 0 < len(value) <= 64 or not value.isascii():
        return False
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def incident_id(last_good_head, failing_head):
    """The incident ID: ``mi-<last good head>``, or ``mi-x<failing head>`` with no last good."""
    if last_good_head is not None:
        if not _sha40(last_good_head):
            _refuse(INCIDENT_RECORD_INVALID)
        return 'mi-' + last_good_head
    if not _sha40(failing_head):
        _refuse(INCIDENT_RECORD_INVALID)
    return 'mi-x' + failing_head


def _signal(value):
    return (type(value) is list and value and
            all(type(item) is str and item in SIGNALS for item in value) and
            value == sorted(set(value)))


def _valid_run(run):
    return (type(run) is dict and set(run) == RUN_FIELDS and
            type(run['kind']) is str and run['kind'] in SIGNALS and
            type(run['id']) is int and run['id'] >= 1 and
            _text(run['conclusion'], 32))


def _valid_range(value):
    return value is None or (type(value) is list and all(
        type(item) is dict and _sha40(item.get('sha')) and _optional_pr(item.get('pr'))
        for item in value))


def _valid_rows(rows):
    return type(rows) is list and all(
        type(row) is dict and _optional_pr(row.get('pr')) for row in rows)


def _valid_attribution(value):
    if type(value) is not dict or value.get('attribution') not in ATTRIBUTIONS:
        return False
    if value['attribution'] == main_incident.SINGLE:
        return _optional_pr(value.get('pr')) and _valid_rows(value.get('rows'))
    return _valid_rows(value.get('rows'))


def _valid_observation(item):
    return (type(item) is dict and set(item) == OBSERVATION_FIELDS and
            _utc_iso(item['observed_at']) and
            _signal(item['signal']) and
            _sha40(item['failing_head']) and
            type(item['failing_runs']) is list and
            all(_valid_run(run) for run in item['failing_runs']) and
            type(item['class']) is str and item['class'] in CLASSES and
            type(item['evidence']) is list and
            all(type(entry) is dict for entry in item['evidence']) and
            _valid_range(item['range']) and
            _valid_rows(item['candidate_rows']) and
            _valid_attribution(item['attribution']) and
            type(item['closure_available']) is bool)


def valid_reason(value):
    """A stripped, non-empty reason of at most MAX_REASON characters, no control characters."""
    return _text(value, MAX_REASON) and value == value.strip()


def _valid_confirmation(item, observations):
    if (type(item) is not dict or set(item) != CONFIRMATION_FIELDS or
            not _session(item['pm_session_id']) or
            not _utc_iso(item['confirmed_at']) or
            not valid_reason(item['reason']) or
            type(item['evidence_sha256']) is not str or
            _SHA256.fullmatch(item['evidence_sha256']) is None):
        return False
    scope = item['scope']
    if (type(scope) is not list or not scope or not all(_pr(pr) for pr in scope) or
            scope != sorted(set(scope))):
        return False
    index = item['observation_index']
    if type(index) is not int or not 0 <= index < len(observations):
        return False
    # The confirmation is bound to the exact evidence it was made on.
    return item['evidence_sha256'] == observation_digest(observations[index])


def _valid_events(events, outcome):
    if type(events) is not list:
        return False
    state = OPEN
    for event in events:
        if (type(event) is not dict or set(event) != EVENT_FIELDS or
                type(event['outcome']) is not str or
                event['outcome'] not in OUTCOME_MOVES[state] or
                not _utc_iso(event['at']) or
                not (event['head'] is None or _sha40(event['head']))):
            return False
        state = event['outcome']
    return outcome == state


def failing_test_module(test):
    """The unittest module of one failing test ID or test file path, or None.

    ``tools/llmtool/tests/test_x.py`` gives ``test_x``. A dotted test ID gives
    its first ``test_*`` part, but never its last part when it has several:
    in ``__main__.Case.test_y`` the ``test_y`` part is a method, not a module.
    """
    if type(test) is not str or not test:
        return None
    match = _TEST_PATH.fullmatch(test)
    if match is not None:
        return match.group(1)
    if '/' in test:
        return None
    parts = test.split('.')
    for index, part in enumerate(parts):
        if _TEST_MODULE.fullmatch(part):
            return part if index < len(parts) - 1 or len(parts) == 1 else None
    return None


def failing_modules(record):
    """Modules of every TEST_FAILURE test recorded in any observation of ``record``."""
    modules = set()
    observations = record.get('observations') if type(record) is dict else None
    for observation in observations if type(observations) is list else []:
        evidence = observation.get('evidence') if type(observation) is dict else None
        for entry in evidence if type(evidence) is list else []:
            if type(entry) is not dict or entry.get('code') != 'TEST_FAILURE':
                continue
            tests = entry.get('tests')
            for test in tests if type(tests) is list else []:
                module = failing_test_module(test)
                if module is not None:
                    modules.add(module)
    return frozenset(modules)


def admitted_shas(record):
    """SHAs recorded in ``record``: last-good head, failing heads and range SHAs."""
    shas = set()
    if type(record) is not dict:
        return frozenset()
    if _sha40(record.get('last_good_head')):
        shas.add(record['last_good_head'])
    observations = record.get('observations')
    for observation in observations if type(observations) is list else []:
        if type(observation) is not dict:
            continue
        if _sha40(observation.get('failing_head')):
            shas.add(observation['failing_head'])
        merges = observation.get('range')
        for item in merges if type(merges) is list else []:
            if type(item) is dict and _sha40(item.get('sha')):
                shas.add(item['sha'])
    return frozenset(shas)


def remote_test_digest(receipt):
    """SHA-256 of the canonical JSON of a stored receipt without ``receipt_digest``."""
    body = {key: item for key, item in receipt.items() if key != 'receipt_digest'}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _remote_modules(value, failing):
    return (type(value) is list and 1 <= len(value) <= MAX_REMOTE_MODULES and
            all(type(item) is str and _TEST_MODULE.fullmatch(item) for item in value) and
            value == sorted(set(value)) and set(value) <= failing)


def receipt_fields_allowed(receipt):
    """True when ``receipt`` is a dict with exactly the allowlisted receipt fields."""
    return type(receipt) is dict and set(receipt) in (
        REMOTE_RECEIPT_FIELDS, REMOTE_RECEIPT_FIELDS | REMOTE_RECEIPT_OPTIONAL)


def _sha256(value):
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _valid_receipt_shape(receipt, item):
    """Every structured receipt field matches its fixed grammar; no free text."""
    identity = receipt['identity']
    uid = identity.get('uid') if type(identity) is dict else None
    failures = receipt['failures']
    log_path = receipt['remote_log_path']
    return (type(receipt['exit_code']) is int and
            all(receipt[key] is None or _sha40(receipt[key]) for key in ('tree', 'fetch_head')) and
            type(identity) is dict and set(identity) == {'uid'} and
            (uid is None or (type(uid) is int and uid >= 0)) and
            all(type(receipt[key]) is str and _RECEIPT_STAMP.fullmatch(receipt[key]) is not None
                for key in ('started_at', 'ended_at')) and
            type(receipt['elapsed_s']) in (int, float) and receipt['elapsed_s'] >= 0 and
            type(receipt['ran_total']) is int and receipt['ran_total'] >= 0 and
            type(failures) is list and len(failures) <= MAX_RECEIPT_FAILURES and
            all(type(name) is str and name in item['modules'] for name in failures) and
            type(receipt['log_bytes']) is int and 0 <= receipt['log_bytes'] <= MAX_LOG_BYTES and
            _sha256(receipt['log_sha256']) and _sha256(receipt['command_digest']) and
            _sha256(receipt['receipt_digest']) and
            (log_path is None or (type(log_path) is str and
                                  _REMOTE_LOG.fullmatch(log_path) is not None)))


def _valid_excerpt(value):
    if type(value) is not str or len(value.splitlines()) > MAX_EXCERPT_LINES:
        return False
    try:
        return len(value.encode('utf-8')) <= MAX_EXCERPT_BYTES
    except UnicodeError:
        return False


def _screened_output(receipt):
    """The output-derived strings (``final``, ``failure_excerpt``) pass the real screen.

    Each ``final`` line must also be a unittest summary line or FINAL_REDACTED,
    so a line the screen misses is still never stored as free text. A
    ``failure_excerpt`` on PASS is a result contradiction (``receipt_consistent``).
    """
    finals = receipt['final']
    if type(finals) is not list or not all(
            type(line) is str and len(line) <= MAX_FINAL_LINE and
            (line == FINAL_REDACTED or FINAL_SUMMARY.fullmatch(line) is not None)
            for line in finals):
        return False
    values = list(finals)
    if 'failure_excerpt' in receipt:
        excerpt = receipt['failure_excerpt']
        if not _valid_excerpt(excerpt):
            return False
        values.append(excerpt)
    return not any(task_store._emitted_sensitive(value) for value in values)


def receipt_consistent(receipt):
    """True when no field of a stored receipt contradicts its ``result``.

    Codex v1 F1 on PR #1769. The invariants follow the fixed remote program
    (``remote_test._REMOTE_SCRIPT``) and its receipt validator:

    - every result: ``failures`` lists failing modules once each, in module
      order; every failing module has a final line, and there are at most
      two final lines per module (``len(failures) <= len(final) <=
      2 * len(modules)``); a ``tree`` is only read after FETCH_HEAD matched
      ``source_sha``;
    - PASS: exit code 0, no ``failure_excerpt``, no failures, at least one
      test ran, FETCH_HEAD is ``source_sha``, a tree, a non-root uid, a
      passing final line (``PASS_FINAL`` or FINAL_REDACTED) per module;
    - FAIL: a non-zero exit code; FETCH_HEAD is ``source_sha`` or unknown;
    - TIMEOUT: exit code 124; FETCH_HEAD is ``source_sha`` or unknown;
    - SHA_MISMATCH: exit code 41, FETCH_HEAD is not ``source_sha``, and
      nothing ran (no tree, no tests, no final lines, no failures).

    Any doubt, including a field of the wrong type, returns False.
    """
    try:
        return _receipt_invariants(receipt)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _receipt_invariants(receipt):
    result, exit_code = receipt['result'], receipt['exit_code']
    modules, final, failures = receipt['modules'], receipt['final'], receipt['failures']
    source, fetched, tree = receipt['source_sha'], receipt['fetch_head'], receipt['tree']
    if (type(result) is not str or result not in REMOTE_RECEIPT_RESULTS or
            type(exit_code) is not int or type(modules) is not list or
            type(final) is not list or type(failures) is not list):
        return False
    if failures != [name for name in modules if name in failures]:
        return False  # repeated, out of module order or not a module
    if not len(failures) <= len(final) <= 2 * len(modules):
        return False
    if tree is not None and fetched != source:
        return False
    if result == 'PASS':
        uid = receipt['identity']['uid']
        return (exit_code == 0 and 'failure_excerpt' not in receipt and not failures and
                type(receipt['ran_total']) is int and receipt['ran_total'] > 0 and
                fetched == source and tree is not None and
                type(uid) is int and uid != 0 and len(final) >= len(modules) and
                all(type(line) is str and
                    (line == FINAL_REDACTED or PASS_FINAL.fullmatch(line) is not None)
                    for line in final))
    if result == 'SHA_MISMATCH':
        return (exit_code == SHA_MISMATCH_EXIT_CODE and fetched != source and
                tree is None and receipt['ran_total'] == 0 and not final and not failures)
    if result == 'TIMEOUT' and exit_code != TIMEOUT_EXIT_CODE:
        return False
    return exit_code != 0 and (fetched is None or fetched == source)


def _well_formed_receipt(receipt, item, incident):
    """Every stored-receipt check except the result invariants and the digest."""
    return (receipt_fields_allowed(receipt) and receipt['result'] == item['result'] and
            receipt['source_sha'] == item['sha'] and
            receipt['modules'] == item['modules'] and
            receipt['incident_id'] == incident and
            _valid_receipt_shape(receipt, item) and
            _screened_output(receipt))


def _receipt_entry_typed(entry):
    """The entry fields a receipt is checked against have their basic types."""
    return (type(entry) is dict and set(entry) == REMOTE_TEST_FIELDS and
            type(entry['result']) is str and entry['result'] in REMOTE_RECEIPT_RESULTS and
            type(entry['modules']) is list and
            all(type(name) is str for name in entry['modules']))


def _valid_receipt(receipt, item, incident):
    if (not _well_formed_receipt(receipt, item, incident) or
            not receipt_consistent(receipt)):
        return False
    try:
        return receipt['receipt_digest'] == remote_test_digest(receipt)
    except (TypeError, ValueError):
        return False


def _valid_remote_test(item, incident, shas, failing):
    """One remote-test entry, bound to the SHAs and failing modules of its record."""
    if (type(item) is not dict or set(item) != REMOTE_TEST_FIELDS or
            not _sha40(item['sha']) or item['sha'] not in shas or
            not _remote_modules(item['modules'], failing) or
            item['where'] != REMOTE_TEST_WHERE or
            not _utc_iso(item['recorded_at']) or type(item['result']) is not str):
        return False
    if item['receipt'] is None:
        return (item['result'] in REMOTE_OUTCOME_RESULTS and type(item['code']) is str and
                _REMOTE_CODE.fullmatch(item['code']) is not None)
    return (item['code'] is None and item['result'] in REMOTE_RECEIPT_RESULTS and
            _valid_receipt(item['receipt'], item, incident))


def _valid_remote_tests(record):
    if REMOTE_TESTS not in record:
        return True
    items = record[REMOTE_TESTS]
    if type(items) is not list or len(items) > MAX_REMOTE_TESTS:
        return False
    shas, failing = admitted_shas(record), failing_modules(record)
    return all(_valid_remote_test(item, record['incident_id'], shas, failing)
               for item in items)


def _valid_body(record):
    """Check every field except ``record_sha256``; return False on any doubt."""
    body_fields = FIELDS - {'record_sha256'}
    if type(record) is not dict or set(record) not in (body_fields,
                                                       body_fields | OPTIONAL_FIELDS):
        return False
    if (type(record['schema_version']) is not int or
            record['schema_version'] != SCHEMA_VERSION or
            not _safe_id(record['incident_id']) or
            not _utc_iso(record['created_at']) or
            not _session(record['pm_session_id']) or
            not _signal(record['signal']) or
            not (record['last_good_head'] is None or _sha40(record['last_good_head'])) or
            type(record['outcome']) is not str or record['outcome'] not in OUTCOME_MOVES):
        return False
    observations = record['observations']
    if (type(observations) is not list or not 1 <= len(observations) <= MAX_OBSERVATIONS or
            not all(_valid_observation(item) for item in observations)):
        return False
    if record['incident_id'] != _expected_id(record):
        return False
    confirmations = record['confirmations']
    if (type(confirmations) is not list or len(confirmations) > MAX_CONFIRMATIONS or
            not all(_valid_confirmation(item, observations) for item in confirmations)):
        return False
    if not _valid_remote_tests(record):
        return False
    return _valid_events(record['outcome_events'], record['outcome'])


def _expected_id(record):
    head = record['last_good_head']
    return ('mi-' + head) if head is not None else 'mi-x' + record['observations'][0]['failing_head']


def _validate(record, incident=None):
    """Return ``record`` when its shape and digest are valid, else refuse INVALID."""
    if (type(record) is not dict or
            set(record) not in (FIELDS, FIELDS | OPTIONAL_FIELDS) or
            type(record.get('record_sha256')) is not str or
            _SHA256.fullmatch(record['record_sha256']) is None):
        _refuse(INCIDENT_RECORD_INVALID, incident)
    body = {key: item for key, item in record.items() if key != 'record_sha256'}
    if not _valid_body(body):
        _refuse(INCIDENT_RECORD_INVALID, incident)
    try:
        digest = record_digest(record)
    except (TypeError, ValueError):
        _refuse(INCIDENT_RECORD_INVALID, incident)
    if digest != record['record_sha256']:
        _refuse(INCIDENT_RECORD_INVALID, incident)
    return record


def _sealed(body, incident):
    """Return a detached, validated record with ``record_sha256`` set."""
    try:
        # The JSON round trip detaches the body and turns tuples into lists.
        plain = json.loads(_canonical(body).decode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        _refuse(INCIDENT_RECORD_INVALID, incident)
    if not _valid_body(plain):
        _refuse(INCIDENT_RECORD_INVALID, incident)
    plain['record_sha256'] = record_digest(plain)
    return _validate(plain, incident)


def new_record(*, pm_session_id, observation, last_good_head, created_at):
    """Return the body of a new UNKNOWN_OPEN record; create() validates it."""
    if type(observation) is not dict:
        _refuse(INCIDENT_RECORD_INVALID)
    return {
        'schema_version': SCHEMA_VERSION,
        'incident_id': incident_id(last_good_head, observation.get('failing_head')),
        'created_at': created_at,
        'pm_session_id': pm_session_id,
        'signal': copy.deepcopy(observation.get('signal')),
        'last_good_head': last_good_head,
        'observations': [copy.deepcopy(observation)],
        'confirmations': [],
        'outcome': OPEN,
        'outcome_events': [],
    }


def _directory_path(root):
    base = Path(task_store.default_root() if root is None else root).absolute()
    return base.parent / DIRECTORY_NAME


def _open_directory(root):
    """Create or open the owner-only record directory; a symlink is refused."""
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    directory = _directory_path(root)
    try:
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    try:
        named = os.lstat(directory)
    except OSError:
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    if (stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode) or
            named.st_uid != os.getuid()):
        _refuse(INCIDENT_RECORD_INVALID)
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _refuse(INCIDENT_RECORD_INVALID)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.getuid() or
                (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            _refuse(INCIDENT_RECORD_INVALID)
        if stat.S_IMODE(opened.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                _refuse(INCIDENT_RECORD_UNAVAILABLE)
    except LLMToolError:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    return fd


def _record_name(incident):
    if not _safe_id(incident):
        _refuse(INCIDENT_RECORD_INVALID)
    return incident + '.json'


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError('non-finite number')


def _read_at(directory, incident):
    """Read and validate one record through the directory descriptor."""
    name = _record_name(incident)
    for _ in range(READ_ATTEMPTS):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            _refuse(INCIDENT_RECORD_UNAVAILABLE, incident)
        except OSError:
            _refuse(INCIDENT_RECORD_INVALID, incident)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_RECORD_BYTES):
                _refuse(INCIDENT_RECORD_INVALID, incident)
            if info.st_nlink != 1:
                # A replace unlinked this inode, or a create is between its
                # link and temporary unlink; a planted extra link persists.
                time.sleep(POLL_SECONDS)
                continue
            chunks, remaining = [], MAX_RECORD_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except LLMToolError:
            raise
        except OSError:
            _refuse(INCIDENT_RECORD_INVALID, incident)
        finally:
            os.close(fd)
        raw = b''.join(chunks)
        if len(raw) > MAX_RECORD_BYTES:
            _refuse(INCIDENT_RECORD_INVALID, incident)
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                               parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError):
            _refuse(INCIDENT_RECORD_INVALID, incident)
        _validate(value, incident)
        if value['incident_id'] != incident:
            _refuse(INCIDENT_RECORD_INVALID, incident)
        return value
    _refuse(INCIDENT_RECORD_INVALID, incident)


def _write_temporary(directory, incident, record):
    """Write the fsynced owner-only temporary; return its name."""
    raw = _canonical(record) + b'\n'
    if len(raw) > MAX_RECORD_BYTES:
        _refuse(INCIDENT_RECORD_FULL, incident)
    temporary = '.' + incident + '.' + uuid.uuid4().hex + '.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return temporary


def create(record, root=None):
    """Validate and write one new UNKNOWN_OPEN record exactly once; return it."""
    if type(record) is not dict:
        _refuse(INCIDENT_RECORD_INVALID)
    incident = record.get('incident_id')
    body = {key: item for key, item in record.items() if key != 'record_sha256'}
    sealed = _sealed(body, incident)
    if (sealed['outcome'] != OPEN or sealed['outcome_events'] or sealed['confirmations'] or
            len(sealed['observations']) != 1 or REMOTE_TESTS in sealed):
        _refuse(INCIDENT_OUTCOME_INVALID, incident)
    name = _record_name(sealed['incident_id'])
    directory = _open_directory(root)
    temporary = None
    try:
        temporary = _write_temporary(directory, incident, sealed)
        # A hard link is atomic and fails when the name already exists.
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        temporary = None
        os.fsync(directory)
    except FileExistsError:
        _refuse(INCIDENT_RECORD_EXISTS, incident)
    except LLMToolError:
        raise
    except OSError:
        _refuse(INCIDENT_RECORD_UNAVAILABLE, incident)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        os.close(directory)
    return copy.deepcopy(sealed)


def get(incident, root=None):
    """Return the validated record for ``incident``."""
    _record_name(incident)
    directory = _open_directory(root)
    try:
        return _read_at(directory, incident)
    finally:
        os.close(directory)


def _acquire_lock(directory, incident):
    lock = '.' + incident + '.lock'
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                     dir_fd=directory)
    except OSError:
        _refuse(INCIDENT_RECORD_INVALID, incident)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1):
            _refuse(INCIDENT_RECORD_INVALID, incident)
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _refuse(INCIDENT_RECORD_BUSY, incident)
                time.sleep(POLL_SECONDS)
    except LLMToolError:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        _refuse(INCIDENT_RECORD_UNAVAILABLE, incident)


def _check_forward(old, new, incident):
    """Refuse IMMUTABLE unless ``new`` only appends to and advances ``old``."""
    if type(new) is not dict:
        _refuse(INCIDENT_RECORD_IMMUTABLE, incident)
    for key in IMMUTABLE_FIELDS:
        if key not in new or new[key] != old[key]:
            _refuse(INCIDENT_RECORD_IMMUTABLE, incident)
    for key in APPEND_FIELDS:
        value = new.get(key)
        if type(value) is not list or value[:len(old[key])] != old[key]:
            _refuse(INCIDENT_RECORD_IMMUTABLE, incident)
    # The optional remote-test list: absent reads as empty; once written it stays.
    if REMOTE_TESTS in old and REMOTE_TESTS not in new:
        _refuse(INCIDENT_RECORD_IMMUTABLE, incident)
    earlier = old.get(REMOTE_TESTS, [])
    value = new.get(REMOTE_TESTS, [])
    if type(value) is not list or value[:len(earlier)] != earlier:
        _refuse(INCIDENT_RECORD_IMMUTABLE, incident)
    outcome = new.get('outcome')
    if outcome != old['outcome'] and (type(outcome) is not str or
                                      outcome not in OUTCOME_MOVES[old['outcome']]):
        _refuse(INCIDENT_OUTCOME_INVALID, incident)


def _commit(incident, expected_sha256, mutate, root=None):
    """Apply ``mutate(body copy) -> new body`` when the stored digest is expected.

    The new body must only append to and advance the stored record; a final
    record (RESOLVED or SUPERSEDED) takes no change at all.
    """
    name = _record_name(incident)
    directory = _open_directory(root)
    try:
        lock_fd = _acquire_lock(directory, incident)
        try:
            current = _read_at(directory, incident)
            if (type(expected_sha256) is not str or
                    current['record_sha256'] != expected_sha256):
                _refuse(INCIDENT_RECORD_CONFLICT, incident)
            if current['outcome'] != OPEN:
                _refuse(INCIDENT_CLOSED, incident)
            old = {key: item for key, item in current.items() if key != 'record_sha256'}
            new = mutate(copy.deepcopy(old))
            _check_forward(old, new, incident)
            updated = _sealed(new, incident)
            _check_forward(old, updated, incident)
            temporary = None
            try:
                temporary = _write_temporary(directory, incident, updated)
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                temporary = None
                os.fsync(directory)
            except OSError:
                _refuse(INCIDENT_RECORD_UNAVAILABLE, incident)
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except OSError:
                        pass
            return copy.deepcopy(updated)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    finally:
        os.close(directory)


def append_observation(incident, expected_sha256, observation, root=None):
    """Append one observation to an open record; return the new record."""
    def mutate(body):
        if len(body['observations']) >= MAX_OBSERVATIONS:
            _refuse(INCIDENT_RECORD_FULL, incident)
        body['observations'].append(copy.deepcopy(observation))
        return body
    return _commit(incident, expected_sha256, mutate, root)


def append_confirmation(incident, expected_sha256, confirmation, root=None):
    """Append one PM confirmation to an open record; return the new record."""
    def mutate(body):
        if len(body['confirmations']) >= MAX_CONFIRMATIONS:
            _refuse(INCIDENT_RECORD_FULL, incident)
        body['confirmations'].append(copy.deepcopy(confirmation))
        return body
    return _commit(incident, expected_sha256, mutate, root)


def append_remote_test(incident, expected_sha256, entry, root=None):
    """Append one EC2 remote-test entry to an open record; return the new record.

    The sealed record is validated whole, so an entry whose SHA is not recorded
    in the incident, whose modules are not recorded as failing, or whose
    receipt does not match its digest refuses INCIDENT_RECORD_INVALID. A
    receipt with any field outside the exact allowlist refuses
    INCIDENT_RECEIPT_FIELDS_INVALID, and a well-formed receipt whose fields
    contradict its result (``receipt_consistent``) refuses
    INCIDENT_RECEIPT_INCONSISTENT, both before the record is read or written.
    The whole-record validation checks the same invariants again.
    """
    receipt = entry.get('receipt') if type(entry) is dict else None
    if receipt is not None:
        if not receipt_fields_allowed(receipt):
            _refuse(INCIDENT_RECEIPT_FIELDS_INVALID, incident)
        if (_receipt_entry_typed(entry) and _well_formed_receipt(receipt, entry, incident) and
                not receipt_consistent(receipt)):
            _refuse(INCIDENT_RECEIPT_INCONSISTENT, incident)

    def mutate(body):
        entries = body.setdefault(REMOTE_TESTS, [])
        if len(entries) >= MAX_REMOTE_TESTS:
            _refuse(INCIDENT_RECORD_FULL, incident)
        entries.append(copy.deepcopy(entry))
        return body
    return _commit(incident, expected_sha256, mutate, root)


def advance_outcome(incident, expected_sha256, outcome, *, at, head=None, root=None):
    """Advance UNKNOWN_OPEN to RESOLVED or SUPERSEDED; return the new record."""
    def mutate(body):
        if type(outcome) is not str or outcome not in OUTCOME_MOVES[body['outcome']]:
            _refuse(INCIDENT_OUTCOME_INVALID, incident)
        body['outcome_events'].append({'outcome': outcome, 'at': at, 'head': head})
        body['outcome'] = outcome
        return body
    return _commit(incident, expected_sha256, mutate, root)


def list_records(root=None):
    """Return every record, validated as ``get`` does, sorted by incident ID.

    Read-only: a missing directory returns [] and is not created. Any
    unreadable or invalid record, or an unsafe directory, refuses INVALID.
    """
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    path = _directory_path(root)
    try:
        named = os.lstat(path)
    except FileNotFoundError:
        return []
    except OSError:
        _refuse(INCIDENT_RECORD_UNAVAILABLE)
    if (stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode) or
            named.st_uid != os.getuid()):
        _refuse(INCIDENT_RECORD_INVALID)
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _refuse(INCIDENT_RECORD_INVALID)
    try:
        try:
            opened = os.fstat(directory)
            names = sorted(os.listdir(directory))
        except OSError:
            _refuse(INCIDENT_RECORD_UNAVAILABLE)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.getuid() or
                (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino) or
                stat.S_IMODE(opened.st_mode) != 0o700):
            _refuse(INCIDENT_RECORD_INVALID)
        records = []
        for name in names:
            if name.startswith('.'):
                continue  # lock files and in-flight temporaries
            match = _RECORD_NAME.fullmatch(name)
            if match is None:
                _refuse(INCIDENT_RECORD_INVALID)
            incident = match.group(1)
            try:
                records.append(_read_at(directory, incident))
            except LLMToolError:
                _refuse(INCIDENT_RECORD_INVALID, incident)
        return sorted(records, key=lambda record: record['incident_id'])
    finally:
        os.close(directory)


def find_open(root=None):
    """Return every UNKNOWN_OPEN record, sorted by incident ID."""
    return [record for record in list_records(root) if record['outcome'] == OPEN]
