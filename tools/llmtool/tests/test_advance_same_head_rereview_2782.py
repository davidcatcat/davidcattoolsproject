"""#2782: a newer COMPLETED review claim on a head with an older verdict is recorded.

The older verdict is already a completion event but the event carries no version.
The newer claim on the same head must be recorded by task advance, and the next
step must follow the newer verdict. An older PASS must never admit a merge while
the newer claim on that head is not a recorded PASS.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_advance


HEAD = 'a' * 40
PR_NUMBER = 17
TASK = '1' * 32
RUN = '2' * 32

D6 = {'root': '/repo/d6', 'task_id': 'executor-1', 'result_head': HEAD,
      'recorded_result_head': HEAD, 'registered_result_head': HEAD,
      'dirty': False, 'state': 'CLEAN'}
PR = {'number': PR_NUMBER, 'head': {'sha': HEAD}, 'state': 'open',
      'merged': False, 'mergeable': True}


def record(claims, events=(), **changes):
    value = {
        'task_id': TASK, 'run_id': RUN,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                       'evidence': {'result': {'result_head': HEAD}}},
        'review_dispatch_claim': list(claims),
        'review_repair_claims': [],
        'completion_events': list(events),
        'remote_test_outcomes': [{
            'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
            'executor_task': 'executor-1', 'sha': HEAD, 'modules': []}],
    }
    value.update(changes)
    return value


def claim(state='COMPLETED', version=1, **changes):
    value = {'executor_task': 'executor-1', 'pr': PR_NUMBER, 'head_sha': HEAD,
             'version': version, 'state': state}
    value.update(changes)
    return value


def old_event(*, outcome=None, version=None, state='RECORDED'):
    """A stored completion event; no version key when version is None."""
    event = {'source': 'REVIEWER', 'source_sha': HEAD, 'state': state}
    if outcome is not None:
        event['outcome'] = outcome
    if version is not None:
        event['version'] = version
    return event


def related(events):
    return {'events': list(events), 'repair_claims': []}


class Store:
    def __init__(self, value):
        self.value = value

    def get(self, _task_id):
        return self.value


class NewerCompletedClaimRecordedTest(unittest.TestCase):
    """R1: the newer claim is recorded and its verdict decides the next step."""

    def test_advance_records_the_newer_claim_version(self):
        older = claim(version=1)
        newer = claim(version=2)
        outcome = record([older, newer], [old_event()])
        calls = []

        def record_result(**kwargs):
            calls.append(kwargs['version'])
            return {'state': 'REVIEW_FAIL'}

        with patch.object(task_advance, '_d6_snapshot', return_value=D6), \
                patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                patch.object(task_advance, '_review_outcomes',
                             side_effect=lambda value, _claims, _get:
                             value.get('completion_events', [])):
            result = task_advance.advance(
                task_id=TASK, store=Store(outcome), pr_number=PR_NUMBER,
                github_get=lambda _path: [],
                record_review_result_call=record_result)

        self.assertEqual((result['step'], result['codes']),
                         ('RECORD_REVIEW_RESULT', []))
        self.assertEqual(result['result'], {'state': 'REVIEW_FAIL'})
        self.assertEqual(calls, [2])

    def test_newer_claim_wins_regardless_of_list_order(self):
        older = claim(version=1)
        newer = claim(version=2)
        for claims in ([older, newer], [newer, older]):
            with self.subTest(order=[item['version'] for item in claims]):
                calls = []

                def record_result(**kwargs):
                    calls.append(kwargs['version'])
                    return {'state': 'REVIEW_FAIL'}

                with patch.object(task_advance, '_d6_snapshot', return_value=D6), \
                        patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                        patch.object(task_advance, '_review_outcomes',
                                     side_effect=lambda value, _claims, _get:
                                     value.get('completion_events', [])):
                    result = task_advance.advance(
                        task_id=TASK, store=Store(record(claims, [old_event()])),
                        pr_number=PR_NUMBER, github_get=lambda _path: [],
                        record_review_result_call=record_result)

                self.assertEqual(result['step'], 'RECORD_REVIEW_RESULT')
                self.assertEqual(calls, [2])

    def test_next_step_follows_the_recorded_newer_verdict(self):
        older = claim(version=1)
        newer = claim(version=2)
        outcome = record([older, newer], [old_event(outcome='REVIEW_PASS')])
        calls = []

        def record_result(**kwargs):
            calls.append(kwargs['version'])
            outcome['completion_events'].append(
                old_event(outcome='REVIEW_FAIL', version=kwargs['version']))
            return {'state': 'REVIEW_FAIL'}

        def read_outcomes(value, _claims, _get):
            return value.get('completion_events', [])

        repaired = []

        def repair(**kwargs):
            repaired.append(kwargs['version'])
            return {'repair_task_id': '3' * 32}

        with patch.object(task_advance, '_d6_snapshot', return_value=D6), \
                patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                patch.object(task_advance, '_review_outcomes', side_effect=read_outcomes):
            first = task_advance.advance(
                task_id=TASK, store=Store(outcome), pr_number=PR_NUMBER,
                github_get=lambda _path: [],
                record_review_result_call=record_result)
            second = task_advance.advance(
                task_id=TASK, store=Store(outcome), pr_number=PR_NUMBER,
                github_get=lambda _path: [], repair_review_call=repair)

        self.assertEqual(first['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(calls, [2])
        self.assertEqual(second['step'], 'REPAIR_REVIEW')
        self.assertEqual(repaired, [2])


class VersionlessEventDoesNotStandInTest(unittest.TestCase):
    """R2: an event stored without a version never answers for a newer claim."""

    def test_old_unversioned_event_does_not_hold_the_newer_claim_unknown(self):
        older = claim(version=1)
        newer = claim(version=2)
        events = [old_event()]
        self.assertEqual(
            task_advance.decide(record([older, newer], events), D6, PR, related(events)),
            'RECORD_REVIEW_RESULT')

    def test_old_unversioned_pass_does_not_advance_the_newer_claim(self):
        older = claim(version=1)
        newer = claim('DISPATCHED', version=2)
        events = [old_event(outcome='REVIEW_PASS')]
        value = record(
            [older, newer], events,
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY',
                        'evidence': {'result': {'result_head': HEAD},
                                     'review_pr_ci': {'stage': 'READY'}}})
        self.assertEqual(task_advance.decide(value, D6, PR, related(events)),
                         'RECORD_REVIEW_RESULT')


class OlderPassNeverAdmitsMergeTest(unittest.TestCase):
    """R3: an older PASS does not merge while the newer claim is not recorded PASS."""

    def _merge(self, newer):
        older = claim(version=1, review_result={'verdict': 'PASS'})
        events = [old_event(outcome='REVIEW_PASS', version=1)]
        calls = []

        def gh_runner(_root, args):
            calls.append(args)
            return SimpleNamespace(returncode=0)

        result = task_advance._merge_once(
            store=object(), record=record([older, newer], events),
            number=PR_NUMBER, head=HEAD, pr=dict(PR), events=events,
            github_get=lambda path: self.fail(f'no GitHub read expected: {path}'),
            gh_runner=gh_runner, root='/repo')
        return result, calls

    def test_newer_completed_pass_without_a_recorded_event_is_not_merged(self):
        result, calls = self._merge(
            claim(version=2, review_result={'verdict': 'PASS'}))
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_NEWER_REVIEW_EXISTS'])
        self.assertEqual(calls, [])

    def test_newer_completed_non_pass_is_not_merged(self):
        result, calls = self._merge(
            claim(version=2, review_result={'verdict': 'FIX'}))
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_NEWER_REVIEW_EXISTS'])
        self.assertEqual(calls, [])

    def test_newer_dispatched_claim_is_not_merged(self):
        result, calls = self._merge(claim('DISPATCHED', version=2))
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_NEWER_REVIEW_EXISTS'])
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
