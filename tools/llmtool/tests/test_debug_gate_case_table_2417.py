"""#2417 (D3 split child 1 of #1886 item 7): one stated rule, one test per case.

Stated rule: for a dispatch that needs a Debug, only the newest Debug entry is
considered. It is accepted only when it is a well-formed, completed
RCA_CONFIRMED entry of the same lineage and family, recorded after the newest
counted failure it is meant to cover, and bound to the right examined HEAD
(or, for a no-PR lineage, the no-PR binding). Every other newest entry
refuses; entries are never filtered before the newest is chosen.

Each test drives the real gate (_check_repair_limits, or the chain gate
_delegate_chain_gate) over an in-memory Task store with no write method.
Only the repository identity and the lineage lock wait are patched; every
gate check runs for real.

Test data rule (Owner #763 A): every ID and SHA below is a digest of a plain
label; there are no credential-shaped strings.
"""

import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_store as task_store_module
from llmtool import task_submission_cli as cli
from llmtool.errors import LLMToolError
from llmtool.high_debug import _d6_name


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _id(label):
    return _digest('task ' + label)[:32]


def _sha(label):
    return _digest('commit ' + label)[:40]


_SLUG = 'example/project'
_REF = 'https://github.com/example/project/issues/1886#issuecomment-1'
_D6 = 'case-table-d6'
_HEAD = _sha('current head')
_OLD_HEAD = _sha('earlier head')
_SOURCE = _sha('d6 source')
_POLICY = {'repair_max': 5, 'test_fail_max': 5}
_FIRST_RUN = '2026-11-20T10:00:00Z'
_NEWEST_RUN = '2026-11-20T10:15:00Z'
_AFTER = '2026-11-20T10:30:00Z'
_BEFORE = '2026-11-20T10:10:00Z'

INVALID = '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'
REQUIRED = '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'
HEAD_MOVED = '^DELEGATE_HIGH_DEBUG_HEAD_MOVED'

_REPORT = '\n'.join((
    '## Identity and source', 'Synthetic fixture.',
    '## Symptom and reproduction', 'Synthetic fixture.',
    '## Root Cause', 'Synthetic fixture.',
    '## Exactly what to modify', 'Synthetic fixture.',
    '## Verification', 'Synthetic fixture.',
    '## Risks and handoff', 'Synthetic fixture.',
    '## Outcome/cost', 'RCA_CONFIRMED', 'Cost: UNKNOWN',
)) + '\n'


class _Store:
    """The Task store reads the gate makes; nothing here can write."""

    def __init__(self, *records):
        self.records = {record['task_id']: copy.deepcopy(record) for record in records}

    def task_ids(self):
        return sorted(self.records)

    def get(self, task_id):
        if task_id not in self.records:
            raise LLMToolError('task not found')
        return copy.deepcopy(self.records[task_id])

    def completion_d6_task_ids(self, executor_task_id):
        return sorted(task_id for task_id, record in self.records.items()
                      if type(record.get('completion')) is dict and
                      record['completion'].get('executor_task_id') == executor_task_id)


def _confirmed(task_id, *, head=_HEAD, family='pr-2417', recorded_at=_AFTER,
               executor=_D6, no_pr=False, status='RCA_CONFIRMED'):
    """A finished Debug entry and the completion event run_high_debug records for it."""
    report = _REPORT.replace('\nRCA_CONFIRMED\n', '\n' + status + '\n')
    report_sha256 = _digest(report)
    d6 = _d6_name(executor, 1, head)
    entry = {'family': family, 'failed_corrections': None,
             'early_reason': 'Synthetic early reason.', 'examined_sha': head, 'd6': d6,
             'model_requested': 'gpt-6.1-sol', 'model_observed': 'gpt-6.1-sol',
             'effort_observed': 'high', 'status': status,
             'report_sha256': report_sha256, 'report': report, 'usage': 'UNKNOWN',
             'recorded_at': recorded_at, 'rules_sha256': _digest('rules')}
    if no_pr:
        entry['no_pr'] = True
    event = {
        'event_id': _digest('HIGH_DEBUG\0' + task_id + '\0' + head + '\0' +
                            report_sha256)[:32],
        'source': 'HIGH_DEBUG', 'session_id': 'high-debug-' + d6, 'source_sha': head,
        'payload_sha256': _digest(report_sha256)}
    return entry, event


def _unfinished(status, code, *, executor=_D6):
    """A RESERVED or RUN_FAILED entry as _failed_entry writes it; it has no event."""
    return {'family': 'pr-2417', 'failed_corrections': None,
            'early_reason': 'Synthetic early reason.', 'examined_sha': _HEAD,
            'd6': _d6_name(executor, 2, _HEAD), 'model_requested': 'gpt-6.1-sol',
            'model_observed': 'UNKNOWN', 'effort_observed': 'UNKNOWN', 'status': status,
            'report_sha256': _digest(code), 'report': code, 'usage': 'UNKNOWN',
            'recorded_at': '2026-11-20T10:40:00Z', 'rules_sha256': _digest('rules')}


def _pr_task(task_id, created_at, *, executor=_D6, continues=None, entries=None,
             events=None):
    record = {'task_id': task_id, 'completion': {'executor_task_id': executor},
              'developer': {'executor_task_id': executor, 'state': 'DEVELOPER_RESPONDED',
                            'created_at': created_at}}
    if continues is not None:
        record['continues'] = continues
    if entries is not None:
        record['high_debug'] = list(entries)
        record['completion_events'] = list(events or [])
    return record


def _no_pr_task(task_id, created_at, *, continues=None, entries=None, events=None):
    """A no-PR Task: no completion; its developer record names the D6."""
    record = _pr_task(task_id, created_at, continues=continues, entries=entries,
                      events=events)
    del record['completion']
    return record


def _delegated(task_id, continues=None):
    record = {'task_id': task_id, 'developer': None, 'completion': None}
    if continues is not None:
        record['continues'] = continues
    return record


def _args(task_id, high_debug_task, family='pr-2417'):
    return SimpleNamespace(task_id=task_id, executor_task=_D6, high_debug_ref=_REF,
                           high_debug_task=high_debug_task, high_debug_family=family,
                           repair_packet=None)


def _no_debug_args(task_id):
    return SimpleNamespace(task_id=task_id, executor_task=_D6, high_debug_ref=None,
                           high_debug_task=None, high_debug_family=None,
                           repair_packet=None)


def _binding(head=_HEAD):
    return (SimpleNamespace(task_id=_D6, root='/d6', source_sha=_SOURCE,
                            trusted_agent_id='codex-developer'),
            SimpleNamespace(head=head))


class DebugGateCaseTableTests(unittest.TestCase):
    """Rows 1 to 11 of the #2417 case table."""

    ROOT = _id('pr root')
    RUN2 = _id('pr second run')
    CURRENT = _id('delegated repair')

    def setUp(self):
        started = patch('llmtool.trust.load_trust',
                        return_value=SimpleNamespace(repository={'slug': _SLUG}))
        started.start()
        self.addCleanup(started.stop)
        lock_wait = patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 2)
        lock_wait.start()
        self.addCleanup(lock_wait.stop)

    # -- helpers ---------------------------------------------------------

    def _pr_store(self, entries, events=()):
        """Repair #2 on one D6: the root and a second run, the root holding the Debug."""
        return _Store(_pr_task(self.ROOT, _FIRST_RUN, entries=entries, events=events),
                      _pr_task(self.RUN2, _NEWEST_RUN),
                      _delegated(self.CURRENT))

    def _gate(self, store, args, binding=None):
        before = copy.deepcopy(store.records)
        try:
            return cli._check_repair_limits(
                args, store, binding or _binding(), _POLICY, self.CURRENT,
                [self.ROOT, self.RUN2], set(), set(), _NEWEST_RUN,
                stamps={self.ROOT: _FIRST_RUN, self.RUN2: _NEWEST_RUN})
        finally:
            self.assertEqual(store.records, before)

    def _accepts(self, store, args, binding=None):
        self.assertIsNone(self._gate(store, args, binding))

    def _refuses(self, store, args, code=INVALID, binding=None):
        with self.assertRaisesRegex(LLMToolError, code):
            self._gate(store, args, binding)

    def _chain_gate(self, store, args, binding=None):
        claims = []
        before = copy.deepcopy(store.records)
        try:
            result = cli._delegate_chain_gate(
                args, store, binding or _binding(),
                lambda: claims.append(args.task_id) or 'CLAIMED')
        finally:
            self.assertEqual(store.records, before)
        self.assertEqual(claims, [args.task_id])
        return result

    def _chain_gate_refuses(self, store, args, code):
        with self.assertRaisesRegex(LLMToolError, code):
            cli._delegate_chain_gate(args, store, _binding(),
                                     lambda: self.fail('claimed after a refusal'))

    # -- row 1 -----------------------------------------------------------

    def test_row_01_newest_confirmed_after_the_newest_failure_is_accepted(self):
        entry, event = _confirmed(self.ROOT)
        self._accepts(self._pr_store([entry], [event]), _args(self.CURRENT, self.ROOT))

    # -- row 2 -----------------------------------------------------------

    def test_row_02_newest_confirmed_before_the_newest_failure_is_refused(self):
        for recorded_at in (_BEFORE, _NEWEST_RUN):
            entry, event = _confirmed(self.ROOT, recorded_at=recorded_at)
            with self.subTest(recorded_at=recorded_at):
                self._refuses(self._pr_store([entry], [event]),
                              _args(self.CURRENT, self.ROOT))

    # -- row 3 -----------------------------------------------------------

    def test_row_03_malformed_newest_entry_refuses_beside_an_older_valid_one(self):
        valid, event = _confirmed(self.ROOT)
        # Control: the older entry alone is accepted.
        self._accepts(self._pr_store([valid], [event]), _args(self.CURRENT, self.ROOT))
        malformed = {'none': None, 'word': 'RCA_CONFIRMED', 'list': ['RCA_CONFIRMED'],
                     'empty dict': {}, 'status only': {'status': 'RCA_CONFIRMED'}}
        for field in ('family', 'examined_sha', 'd6', 'recorded_at', 'report',
                      'report_sha256', 'model_observed'):
            missing = dict(valid)
            del missing[field]
            malformed['missing ' + field] = missing
        malformed['malformed recorded_at'] = dict(valid, recorded_at='2026-11-20 10:30')
        for name, newest in malformed.items():
            with self.subTest(newest=name):
                self._refuses(self._pr_store([valid, newest], [event]),
                              _args(self.CURRENT, self.ROOT))

    # -- row 4 -----------------------------------------------------------

    def test_row_04_newest_reserved_or_run_failed_refuses(self):
        valid, event = _confirmed(self.ROOT)
        for newest in (_unfinished('RESERVED', 'HIGH_DEBUG_RESERVED'),
                       _unfinished('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED')):
            with self.subTest(status=newest['status']):
                self._refuses(self._pr_store([valid, newest], [event]),
                              _args(self.CURRENT, self.ROOT))

    # -- row 5 -----------------------------------------------------------

    def test_row_05_newest_unconfirmed_refuses(self):
        valid, event = _confirmed(self.ROOT)
        unconfirmed, unconfirmed_event = _confirmed(
            self.ROOT, status='RCA_UNCONFIRMED', recorded_at='2026-11-20T10:40:00Z')
        for entries, events in (([unconfirmed], [unconfirmed_event]),
                                ([valid, unconfirmed], [event, unconfirmed_event])):
            with self.subTest(entries=len(entries)):
                self._refuses(self._pr_store(entries, events),
                              _args(self.CURRENT, self.ROOT))

    # -- row 6 -----------------------------------------------------------

    def test_row_06_debug_of_another_lineage_refuses(self):
        other = _id('other lineage')
        # A completed Task on a D6 that is neither this D6 nor a predecessor's.
        entry, event = _confirmed(other, executor='unrelated-d6')
        store = self._pr_store(None)
        store.records[other] = _pr_task(other, _FIRST_RUN, executor='unrelated-d6',
                                        entries=[entry], events=[event])
        with self.subTest(case='completed on another D6'):
            self._refuses(store, _args(self.CURRENT, other))
        # A no-PR Task naming this D6 but outside the delegated Task's chain.
        entry, event = _confirmed(other, family='no-pr', no_pr=True)
        store = self._pr_store(None)
        store.records[other] = _no_pr_task(other, _FIRST_RUN, entries=[entry],
                                           events=[event])
        with self.subTest(case='no-PR Task outside the chain'):
            self._refuses(store, _args(self.CURRENT, other, family='no-pr'))

    # -- row 7 -----------------------------------------------------------

    def test_row_07_debug_of_another_family_refuses(self):
        entry, event = _confirmed(self.ROOT)
        with self.subTest(case='named family differs'):
            self._refuses(self._pr_store([entry], [event]),
                          _args(self.CURRENT, self.ROOT, family='pr-9999'))
        # The no-PR mark and family no-pr must agree (#1943 WP-5).
        entry, event = _confirmed(self.ROOT, family='no-pr')
        with self.subTest(case='family no-pr without the no-PR mark'):
            self._refuses(self._pr_store([entry], [event]),
                          _args(self.CURRENT, self.ROOT, family='no-pr'))
        entry, event = _confirmed(self.ROOT, no_pr=True)
        with self.subTest(case='no-PR mark on a PR family'):
            self._refuses(self._pr_store([entry], [event]),
                          _args(self.CURRENT, self.ROOT))

    # -- row 8 -----------------------------------------------------------

    def test_row_08_wrong_examined_head_on_a_pr_lineage_refuses(self):
        entry, event = _confirmed(self.ROOT, head=_OLD_HEAD)
        with self.subTest(case='examined an earlier head'):
            self._refuses(self._pr_store([entry], [event]),
                          _args(self.CURRENT, self.ROOT), HEAD_MOVED)
        entry, event = _confirmed(self.ROOT)
        with self.subTest(case='current head moved after the Debug'):
            self._refuses(self._pr_store([entry], [event]),
                          _args(self.CURRENT, self.ROOT), HEAD_MOVED,
                          binding=_binding(head=_OLD_HEAD))
        entry, event = _confirmed(self.ROOT)
        entry['d6'] = _d6_name(_D6, 1, _OLD_HEAD)
        with self.subTest(case='D6 name derived from another head'):
            self._refuses(self._pr_store([entry], [event]),
                          _args(self.CURRENT, self.ROOT))

    # -- row 9 -----------------------------------------------------------

    def test_row_09_completed_no_pr_debug_on_a_no_pr_lineage_is_accepted(self):
        first, second, current = _id('no-pr first'), _id('no-pr second'), _id('no-pr next')
        entry, event = _confirmed(second, family='no-pr', no_pr=True)
        store = _Store(_no_pr_task(first, _FIRST_RUN),
                       _no_pr_task(second, _NEWEST_RUN, continues=first,
                                   entries=[entry], events=[event]),
                       _delegated(current, continues=second))
        self.assertEqual(self._chain_gate(store, _args(current, second, family='no-pr')),
                         'CLAIMED')

    # -- row 10 ----------------------------------------------------------

    def test_row_10_chain_count_root_cause_with_a_covering_entry_is_accepted(self):
        # PR #2406 / PR #2377 shape: the chain's Debug, on a predecessor's D6
        # and an earlier head, covers the continuation; a new Debug would be
        # refused HIGH_DEBUG_DEBUG_PARENT_USED.
        root, second, current = _id('chain root'), _id('chain second'), _id('chain next')
        entry, event = _confirmed(second, family='pr-2406-ec2', executor='second-d6',
                                  head=_OLD_HEAD)
        store = _Store(_pr_task(root, _FIRST_RUN, executor='first-d6'),
                       _pr_task(second, _NEWEST_RUN, executor='second-d6', continues=root,
                                entries=[entry], events=[event]),
                       _delegated(current, continues=second))
        # The chain count requires a root cause ...
        self._chain_gate_refuses(store, _no_debug_args(current), REQUIRED)
        # ... and the covering entry satisfies it.
        self.assertEqual(self._chain_gate(store, _args(current, second,
                                                       family='pr-2406-ec2')),
                         'CLAIMED')

    # -- row 11 ----------------------------------------------------------

    def test_row_11_no_debug_arguments_where_one_is_required(self):
        entry, event = _confirmed(self.ROOT)
        with self.subTest(lineage='PR'):
            self._refuses(self._pr_store([entry], [event]),
                          _no_debug_args(self.CURRENT), REQUIRED)
        first, second, current = _id('no-pr first'), _id('no-pr second'), _id('no-pr next')
        entry, event = _confirmed(second, family='no-pr', no_pr=True)
        store = _Store(_no_pr_task(first, _FIRST_RUN),
                       _no_pr_task(second, _NEWEST_RUN, continues=first,
                                   entries=[entry], events=[event]),
                       _delegated(current, continues=second))
        with self.subTest(lineage='no-PR'):
            self._chain_gate_refuses(store, _no_debug_args(current), REQUIRED)

    # -- A2: no exemption from the repair limit --------------------------

    def test_a_valid_debug_never_lifts_the_repair_limit(self):
        entry, event = _confirmed(self.ROOT)
        store = self._pr_store([entry], [event])
        runs = [_id(f'limit run {number}') for number in range(1 + _POLICY['repair_max'])]
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            cli._check_repair_limits(
                _args(self.CURRENT, self.ROOT), store, _binding(), _POLICY,
                self.CURRENT, runs, set(), set(), _NEWEST_RUN,
                stamps={run: _FIRST_RUN for run in runs})


if __name__ == '__main__':
    unittest.main()
