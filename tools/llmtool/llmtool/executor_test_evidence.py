"""#2119 WP-3 slice 1: expected remote test modules for a Task. Pure; not wired in.

Design: docs/design/executor-step-tests-off-mac.md section 4.1. The remote set
uses the same pattern as remote_test._task_path_modules; the TUI set uses the
test-file patterns of task_advance. Anything that looks like a test but is not
in the remote set is unsupported.
"""
import hashlib
import re

from .errors import LLMToolError
from .remote_test import valid_module_results
from .task_store import _json_bytes
from .test_profile import default_profile

_TUI = re.compile(r'test_(chat_tui|tui_)[a-z0-9_]*')
_TEST_SHAPED = re.compile(r'test_[^/]*\.py')
_LOWERCASE_STEM = re.compile(r'[a-z0-9_]{1,80}')
_TEST_PATHS_INVALID = 'EXECUTOR_TEST_PATHS_INVALID'


def _unittest_entries(profile):
    """The unittest (pattern, kind) entries of the project TestProfile.

    The entries are checked structurally; an invalid profile raises the
    llmtool EXECUTOR_TEST_PATHS_INVALID error and the default registry
    paths are never a fallback.
    """
    entries = profile.entries
    if type(entries) is not tuple:
        raise LLMToolError(_TEST_PATHS_INVALID)
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise LLMToolError(_TEST_PATHS_INVALID)
        pattern, kind = entry
        if (type(pattern) is not re.Pattern
                or not isinstance(pattern.pattern, str)):
            raise LLMToolError(_TEST_PATHS_INVALID)
        if type(kind) is not str or kind not in ('unittest', 'script'):
            raise LLMToolError(_TEST_PATHS_INVALID)
    unittest_entries = tuple(entry for entry in entries if entry[1] == 'unittest')
    if not unittest_entries:
        raise LLMToolError(_TEST_PATHS_INVALID)
    return unittest_entries


def expected_remote_modules(read_paths, write_paths):
    """Return (remote, tui, unsupported) as sorted tuples.

    The remote module paths come from the unittest entries of the default
    TestProfile; the existing lowercase stem constraint applies on top of a
    unittest pattern match. With the default profile the result is identical
    to the hard-coded registry paths.

    remote: module stems of declared remote test paths, TUI modules included.
    tui: the remote stems that are TUI test modules.
    unsupported: sorted test-shaped paths outside the remote pattern.
    """
    unittest_entries = _unittest_entries(default_profile())
    paths = set()
    for value in (read_paths, write_paths):
        if type(value) not in (list, tuple) or any(type(item) is not str for item in value):
            raise LLMToolError(_TEST_PATHS_INVALID)
        paths.update(value)
    remote_paths = sorted(
        path for path in paths
        if _LOWERCASE_STEM.fullmatch(path.rsplit('/', 1)[-1][:-3]) and any(
            pattern.fullmatch(path) is not None
            for pattern, _kind in unittest_entries))
    remote = tuple(sorted(path.rsplit('/', 1)[-1][:-3] for path in remote_paths))
    tui = tuple(stem for stem in remote if _TUI.fullmatch(stem))
    unsupported = {path for path in paths
                   if _TEST_SHAPED.fullmatch(path.rsplit('/', 1)[-1]) and
                   not any(pattern.fullmatch(path) is not None
                           for pattern, _kind in unittest_entries)}
    return remote, tui, tuple(sorted(unsupported))


STEP_PASS = 'PASS'
STEP_RUN = 'RUN_REMOTE_TEST'
STEP_REPAIR = 'REPAIR'
STEP_STOP = 'STOP_FOR_PM'
STEP_ATTENTION = 'NEEDS_ATTENTION'
ARGUMENT_INVALID = 'EXECUTOR_TEST_EVIDENCE_ARGUMENT_INVALID'
RECEIPT_MISSING = 'REMOTE_TEST_RECEIPT_MISSING'
MODULES_MISMATCH = 'TEST_REMOTE_MODULES_MISMATCH'
EVIDENCE_LEGACY = 'TEST_REMOTE_EVIDENCE_LEGACY'
EVIDENCE_INVALID = 'TEST_REMOTE_EVIDENCE_INVALID'
MODULE_NOT_EXECUTED = 'TEST_REMOTE_MODULE_NOT_EXECUTED'
MODULE_SKIPPED = 'TEST_REMOTE_MODULE_SKIPPED'
MODULE_UNSUPPORTED = 'TEST_REMOTE_MODULE_UNSUPPORTED'
TEST_FAILED = 'TEST_FAILED'
_PASSING_SKIP = re.compile(r'OK \(skipped=[1-9][0-9]{0,5}\)')


def _bound_receipt(record, executor_task, head):
    """The newest receipt for this Task run, executor_task and head, or None."""
    receipts = record.get('remote_test_receipts')
    if type(receipts) is not list:
        return None
    for receipt in reversed(receipts):
        if type(receipt) is not dict or receipt.get('source_sha') != head:
            continue
        same_run = receipt.get('run_id') == record.get('run_id')
        same_task = receipt.get('task_id') == record['task_id']
        if same_task and same_run and receipt.get('executor_task') == executor_task:
            return receipt
    return None


def _digest_ok(receipt):
    claimed = receipt.get('receipt_digest')
    body = {key: value for key, value in receipt.items() if key != 'receipt_digest'}
    try:
        actual = hashlib.sha256(_json_bytes(body)).hexdigest()
    except Exception:
        return False
    return type(claimed) is str and claimed == actual


def _passing(final):
    return type(final) is str and (final.endswith('OK') or bool(_PASSING_SKIP.fullmatch(final)))


def _fail_never_ran(receipt, entries):
    """The 4.3 FAIL row: nothing failed, one passing final per module, a module never ran."""
    finals, modules = receipt.get('final'), receipt['modules']
    if receipt.get('failures') != [] or type(finals) is not list or len(finals) != len(modules):
        return False
    if not all(map(_passing, finals)):
        return False
    if entries is None:
        total = receipt.get('ran_total')
        return type(total) is int and total == 0
    return any(entry['ran'] == 0 for entry in entries)


def remote_evidence_status(record, *, executor_task, head, expected):
    """#2119 WP-3 slice 2, not wired: (step, code, failing_modules, receipt_digest).
    Design section 4.2 and 4.3. expected is the (remote, tui, unsupported) triple
    from expected_remote_modules. step is PASS, RUN_REMOTE_TEST, REPAIR,
    STOP_FOR_PM or NEEDS_ATTENTION, as in the 4.3 step column, and code is None
    only for PASS. Only the newest receipt for the Task run, executor_task and
    head counts; any other receipt is ignored. failing_modules is a sorted tuple.
    receipt_digest is set only when the receipt digest recomputes.
    """
    args_ok = type(record) is dict and type(record.get('task_id')) is str
    if not args_ok or type(executor_task) is not str or type(head) is not str:
        raise LLMToolError(ARGUMENT_INVALID)
    if type(expected) is not tuple or len(expected) != 3:
        raise LLMToolError(ARGUMENT_INVALID)
    remote, _tui, unsupported = expected
    if unsupported:
        return STEP_STOP, MODULE_UNSUPPORTED, tuple(sorted(unsupported)), None
    receipt = _bound_receipt(record, executor_task, head)
    if receipt is None:
        return STEP_RUN, RECEIPT_MISSING, (), None
    if not _digest_ok(receipt):
        return STEP_STOP, EVIDENCE_INVALID, (), None
    digest = receipt['receipt_digest']
    result, modules = receipt.get('result'), receipt.get('modules')
    if result in ('TIMEOUT', 'SHA_MISMATCH'):
        return STEP_ATTENTION, 'REMOTE_TEST_' + result, (), digest
    if result not in ('PASS', 'FAIL') or type(modules) is not list:
        return STEP_STOP, EVIDENCE_INVALID, (), digest
    if not valid_module_results(receipt) or any(type(name) is not str for name in modules):
        return STEP_STOP, EVIDENCE_INVALID, (), digest
    if tuple(sorted(modules)) != tuple(remote):
        return STEP_RUN, MODULES_MISMATCH, (), digest
    entries = receipt.get('module_results')
    not_run = [entry['module'] for entry in entries or () if entry['ran'] == 0]
    ran_entries = [entry for entry in entries or () if entry['ran'] > 0]
    skipped = [entry['module'] for entry in ran_entries if entry['skipped'] > 0]
    failing = tuple(sorted(not_run + skipped))
    if result == 'FAIL' and _fail_never_ran(receipt, entries):
        listed = failing if entries is not None else tuple(sorted(modules))
        return STEP_STOP, MODULE_NOT_EXECUTED, listed, digest
    if entries is None:
        return STEP_RUN, EVIDENCE_LEGACY, (), digest
    if result == 'FAIL':
        failures = receipt.get('failures') if type(receipt.get('failures')) is list else []
        return STEP_REPAIR, TEST_FAILED, tuple(sorted(str(name) for name in failures)), digest
    if not_run:
        return STEP_STOP, MODULE_NOT_EXECUTED, failing, digest
    if skipped:
        return STEP_STOP, MODULE_SKIPPED, failing, digest
    return STEP_PASS, None, (), digest
