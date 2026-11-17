"""Tests for confirmed main incident revert admission (#1706 R5a). No network."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import main_incident as mi  # noqa: E402
from llmtool import main_incident_store as store  # noqa: E402
from llmtool import task_revert as tr  # noqa: E402

PASS_SHA = 'a' * 40
FAIL_SHA = 'c' * 40
LATER_SHA = 'b' * 40
OWNER = 'pm-owner-session'
STAMP = '2026-11-13T00:00:00Z'

SCOPE_PATH = 'tools/llmtool/llmtool/widget.py'
OTHER_PATH = 'tools/llmtool/llmtool/helper.py'


def observation(cls=mi.CODE, head=FAIL_SHA, prs=(12,), observed_at=STAMP):
    rows = [{'pr': pr, 'merge_sha': head} for pr in prs]
    return {
        'observed_at': observed_at, 'signal': [mi.LINUX], 'failing_head': head,
        'failing_runs': [{'kind': mi.LINUX, 'id': 101, 'conclusion': 'failure'}],
        'class': cls, 'evidence': [{'code': 'TEST_FAILURE', 'tests': ['tests.test_mod.Case.test_x']}],
        'range': [{'sha': head, 'pr': prs[0]}], 'candidate_rows': rows,
        'attribution': mi.attribute(rows), 'closure_available': False,
    }


class AdmitTests(unittest.TestCase):
    def make(self, cls=mi.CODE, prs=(12,)):
        return store.new_record(pm_session_id=OWNER, observation=observation(cls=cls, prs=prs),
                                last_good_head=PASS_SHA, created_at=STAMP)

    def confirmation(self, record, index=0, scope=(12,)):
        return {'pm_session_id': OWNER, 'confirmed_at': STAMP, 'scope': list(scope),
                'reason': 'revert the failing merge', 'observation_index': index,
                'evidence_sha256': store.observation_digest(record['observations'][index])}

    def confirmed(self, cls=mi.CODE, prs=(12,), scope=(12,)):
        record = self.make(cls=cls, prs=prs)
        record['confirmations'].append(self.confirmation(record, scope=scope))
        return record

    def call(self, record, dependency):
        """admit plus the deep-equal input check every call must satisfy."""
        before_record = copy.deepcopy(record)
        before_dependency = copy.deepcopy(dependency)
        result = tr.admit(record, dependency)
        self.assertEqual(record, before_record)
        self.assertEqual(dependency, before_dependency)
        return result

    def assertCode(self, code, record, dependency):
        result = self.call(record, dependency)
        self.assertEqual(result['code'], code)
        self.assertTrue(result['refused'])
        return result

    def test_invalid_record_is_refused(self):
        self.assertCode(tr.REVERT_INCIDENT_INVALID, None, None)
        record = self.make()
        record['incident_id'] = ''
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, None)
        record = self.make()
        record['observations'] = []
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, None)
        record = self.make()
        record['observations'] = ['not-a-dict']
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, None)
        record = self.make()
        record['confirmations'] = {}
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, None)

    def test_invalid_scope_is_refused(self):
        record = self.confirmed()
        record['confirmations'][-1] = dict(record['confirmations'][-1], scope=[2, 1])
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, {'code': mi.NO_DEPENDENT})
        record = self.confirmed()
        record['confirmations'][-1] = dict(record['confirmations'][-1], scope=[True])
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, {'code': mi.NO_DEPENDENT})
        record = self.confirmed()
        record['confirmations'][-1] = dict(record['confirmations'][-1], scope=[])
        self.assertCode(tr.REVERT_INCIDENT_INVALID, record, {'code': mi.NO_DEPENDENT})

    def test_closed_incident_is_refused_before_confirmation_checks(self):
        record = self.make()
        record['outcome'] = store.RESOLVED
        self.assertEqual(record['confirmations'], [])
        self.assertCode(tr.REVERT_INCIDENT_NOT_OPEN, record, None)

    def test_unconfirmed_incident_is_refused(self):
        self.assertCode(tr.REVERT_NOT_CONFIRMED, self.make(), {'code': mi.NO_DEPENDENT})

    def test_stale_confirmation_is_refused(self):
        record = self.make()
        record['confirmations'].append(self.confirmation(record))
        record['observations'].append(
            observation(cls=mi.UNKNOWN, observed_at='2026-11-13T01:00:00Z'))
        self.assertCode(tr.REVERT_CONFIRMATION_STALE, record, {'code': mi.NO_DEPENDENT})
        record = self.make()
        record['confirmations'].append('not-a-confirmation')
        self.assertCode(tr.REVERT_CONFIRMATION_STALE, record, {'code': mi.NO_DEPENDENT})

    def test_bool_observation_index_is_refused(self):
        record = self.make()
        record['confirmations'].append(dict(self.confirmation(record), observation_index=True))
        self.assertCode(tr.REVERT_CONFIRMATION_STALE, record, {'code': mi.NO_DEPENDENT})

    def test_stale_confirmation_on_non_code_observation_is_stale(self):
        record = self.make(cls=mi.ENV)
        record['confirmations'].append(
            dict(self.confirmation(record), evidence_sha256='0' * 64))
        self.assertCode(tr.REVERT_CONFIRMATION_STALE, record, {'code': mi.NO_DEPENDENT})

    def test_env_and_flaky_observations_are_not_code(self):
        for cls in (mi.ENV, mi.FLAKY):
            self.assertCode(tr.REVERT_CLASSIFICATION_NOT_CODE, self.confirmed(cls=cls),
                            {'code': mi.NO_DEPENDENT})

    def test_other_class_values_are_unknown(self):
        for cls in (mi.UNKNOWN, 'SOMETHING_ELSE', None):
            self.assertCode(tr.REVERT_CLASSIFICATION_UNKNOWN, self.confirmed(cls=cls),
                            {'code': mi.NO_DEPENDENT})

    def test_dependency_unknown_code_is_shared_with_main_incident(self):
        self.assertIs(tr.REVERT_DEPENDENCY_UNKNOWN, mi.DEPENDENCY_UNKNOWN)

    def test_undigestable_last_observation_is_invalid(self):
        for value in (float('nan'), {1, 2}):
            record = self.confirmed()
            record['observations'][-1] = dict(record['observations'][-1], extra=value)
            self.assertCode(tr.REVERT_INCIDENT_INVALID, record, {'code': mi.NO_DEPENDENT})

    def test_more_than_five_prs_is_refused(self):
        record = self.confirmed(scope=(12, 13, 14, 15, 16, 17))
        self.assertCode(tr.REVERT_SCOPE_TOO_LARGE, record, {'code': mi.NO_DEPENDENT})

    def test_five_prs_are_admitted(self):
        record = self.confirmed(scope=(12, 13, 14, 15, 16))
        result = self.call(record, {'code': mi.NO_DEPENDENT})
        self.assertEqual(result['code'], tr.ADMITTED)
        self.assertFalse(result['refused'])
        self.assertEqual(result['scope'], [12, 13, 14, 15, 16])

    def test_unknown_dependency_is_refused(self):
        record = self.confirmed()
        self.assertCode(tr.REVERT_DEPENDENCY_UNKNOWN, record, None)
        self.assertCode(tr.REVERT_DEPENDENCY_UNKNOWN, record, {'code': 'SOMETHING_ELSE'})

    def test_admitted_returns_a_new_result(self):
        record = self.confirmed()
        result = self.call(record, {'code': mi.NO_DEPENDENT, 'dependents': []})
        confirmation = record['confirmations'][-1]
        self.assertEqual(result['code'], tr.ADMITTED)
        self.assertFalse(result['refused'])
        self.assertEqual(result['incident_id'], record['incident_id'])
        self.assertEqual(result['scope'], confirmation['scope'])
        self.assertIsNot(result['scope'], confirmation['scope'])
        self.assertEqual(result['observation_index'], 0)
        self.assertEqual(result['evidence_sha256'], confirmation['evidence_sha256'])

    def test_later_merge_editing_the_scope_file_is_a_dependent(self):
        record = self.confirmed()
        scope = {'files': {SCOPE_PATH: {'before': 'VALUE = 1\n', 'after': 'VALUE = 2\n'}}}
        dependency = mi.dependency_check(scope, [
            {'pr': 21, 'merge_sha': LATER_SHA, 'files': {SCOPE_PATH: 'VALUE = 3\n'}}])
        self.assertEqual(dependency['code'], mi.REVERT_DEPENDENT)
        result = self.assertCode(tr.REVERT_DEPENDENT_LATER_MERGE, record, dependency)
        self.assertEqual([item['pr'] for item in result['dependents']], [21])
        self.assertIsNot(result['dependents'], dependency['dependents'])

    def test_unrelated_later_merge_is_admitted(self):
        record = self.confirmed()
        scope = {'files': {SCOPE_PATH: {'before': 'VALUE = 1\n', 'after': 'VALUE = 2\n'}}}
        dependency = mi.dependency_check(scope, [
            {'pr': 22, 'merge_sha': LATER_SHA, 'files': {OTHER_PATH: 'OTHER = 1\n'}}])
        self.assertEqual(dependency['code'], mi.NO_DEPENDENT)
        result = self.call(record, dependency)
        self.assertEqual(result['code'], tr.ADMITTED)
        self.assertFalse(result['refused'])

    def test_dependent_without_a_dependents_list_gives_empty_list(self):
        record = self.confirmed()
        result = self.assertCode(tr.REVERT_DEPENDENT_LATER_MERGE, record,
                                 {'code': mi.REVERT_DEPENDENT})
        self.assertEqual(result['dependents'], [])


if __name__ == '__main__':
    unittest.main()
