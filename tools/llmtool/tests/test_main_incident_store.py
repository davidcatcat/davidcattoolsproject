"""Tests for the forward-only main incident store (#1706 R3). No network."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import main_incident as mi  # noqa: E402
from llmtool import main_incident_store as store  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

PASS_SHA = 'a' * 40
FAIL_SHA = 'c' * 40
OWNER = 'pm-owner-session'
STAMP = '2026-11-13T00:00:00Z'


def observation(cls=mi.CODE, head=FAIL_SHA, prs=(12,), observed_at=STAMP):
    rows = [{'pr': pr, 'merge_sha': head} for pr in prs]
    return {
        'observed_at': observed_at, 'signal': [mi.LINUX], 'failing_head': head,
        'failing_runs': [{'kind': mi.LINUX, 'id': 101, 'conclusion': 'failure'}],
        'class': cls, 'evidence': [{'code': 'TEST_FAILURE', 'tests': ['tests.test_mod.Case.test_x']}],
        'range': [{'sha': head, 'pr': prs[0]}], 'candidate_rows': rows,
        'attribution': mi.attribute(rows), 'closure_available': False,
    }


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = mock.patch.dict(os.environ, {'HOME': self.tmp.name})
        home.start()
        self.addCleanup(home.stop)
        self.root = Path(self.tmp.name) / 'state' / 'tasks'
        self.directory = self.root.parent / store.DIRECTORY_NAME

    def make(self, cls=mi.CODE, last_good=PASS_SHA, prs=(12,)):
        body = store.new_record(pm_session_id=OWNER, observation=observation(cls, prs=prs),
                                last_good_head=last_good, created_at=STAMP)
        return store.create(body, self.root)

    def assertCode(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.incident_code, code)

    def confirmation(self, record, index=0, scope=(12,)):
        return {'pm_session_id': OWNER, 'confirmed_at': STAMP, 'scope': list(scope),
                'reason': 'revert the failing merge', 'observation_index': index,
                'evidence_sha256': store.observation_digest(record['observations'][index])}

    def test_incident_id_from_last_good_or_failing_head(self):
        self.assertEqual(store.incident_id(PASS_SHA, FAIL_SHA), 'mi-' + PASS_SHA)
        self.assertEqual(store.incident_id(None, FAIL_SHA), 'mi-x' + FAIL_SHA)
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.incident_id, 'nope', FAIL_SHA)

    def test_create_writes_owner_only_record_beside_task_root(self):
        record = self.make()
        self.assertEqual(record['incident_id'], 'mi-' + PASS_SHA)
        self.assertEqual(record['outcome'], store.OPEN)
        self.assertEqual(record['pm_session_id'], OWNER)
        self.assertEqual(record['signal'], [mi.LINUX])
        path = self.directory / (record['incident_id'] + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(store.get(record['incident_id'], self.root), record)
        self.assertEqual(store.record_digest(record), record['record_sha256'])

    def test_create_is_exactly_once(self):
        self.make()
        self.assertCode(store.INCIDENT_RECORD_EXISTS, self.make)

    def test_create_refuses_a_record_that_is_not_new_and_open(self):
        body = store.new_record(pm_session_id=OWNER, observation=observation(),
                                last_good_head=PASS_SHA, created_at=STAMP)
        body['outcome'] = store.RESOLVED
        body['outcome_events'] = [{'outcome': store.RESOLVED, 'at': STAMP, 'head': None}]
        self.assertCode(store.INCIDENT_OUTCOME_INVALID, store.create, body, self.root)
        body = store.new_record(pm_session_id=OWNER, observation=observation(),
                                last_good_head=PASS_SHA, created_at=STAMP)
        body['outcome'] = store.RESOLVED  # no event leads there
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.create, body, self.root)
        body = store.new_record(pm_session_id=OWNER, observation=observation(cls='MAYBE'),
                                last_good_head=PASS_SHA, created_at=STAMP)
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.create, body, self.root)
        self.assertEqual(store.list_records(self.root), [])

    def test_append_observation_keeps_earlier_entries(self):
        record = self.make(cls=mi.UNKNOWN)
        later = observation(cls=mi.CODE, observed_at='2026-11-13T01:00:00Z')
        updated = store.append_observation(record['incident_id'], record['record_sha256'],
                                           later, self.root)
        self.assertEqual(updated['observations'][0], record['observations'][0])
        self.assertEqual(updated['observations'][1], later)
        self.assertEqual(store.get(record['incident_id'], self.root), updated)

    def test_stale_digest_conflicts(self):
        record = self.make()
        store.append_observation(record['incident_id'], record['record_sha256'],
                                 observation(), self.root)
        self.assertCode(store.INCIDENT_RECORD_CONFLICT, store.append_observation,
                        record['incident_id'], record['record_sha256'], observation(), self.root)

    def test_forward_only_refuses_rewrites(self):
        record = self.make()
        record = store.append_confirmation(record['incident_id'], record['record_sha256'],
                                           self.confirmation(record), self.root)

        def rewrite_class(body):
            body['observations'][0]['class'] = mi.ENV
            return body

        def rewrite_owner(body):
            body['pm_session_id'] = 'pm-someone-else'
            return body

        def rewrite_last_good(body):
            body['last_good_head'] = 'b' * 40
            return body

        def drop_confirmation(body):
            body['confirmations'] = []
            return body

        def bogus_outcome(body):
            body['outcome'] = 'REVERTED'
            return body

        for mutate in (rewrite_class, rewrite_owner, rewrite_last_good, drop_confirmation):
            self.assertCode(store.INCIDENT_RECORD_IMMUTABLE, store._commit,
                            record['incident_id'], record['record_sha256'], mutate, self.root)
        self.assertCode(store.INCIDENT_OUTCOME_INVALID, store._commit,
                        record['incident_id'], record['record_sha256'], bogus_outcome, self.root)
        self.assertEqual(store.get(record['incident_id'], self.root), record)

    def test_outcome_advances_once_and_then_the_record_is_final(self):
        record = self.make()
        self.assertCode(store.INCIDENT_OUTCOME_INVALID, store.advance_outcome,
                        record['incident_id'], record['record_sha256'], store.OPEN,
                        at=STAMP, root=self.root)
        ended = store.advance_outcome(record['incident_id'], record['record_sha256'],
                                      store.RESOLVED, at=STAMP, head='d' * 40, root=self.root)
        self.assertEqual(ended['outcome'], store.RESOLVED)
        self.assertEqual(ended['outcome_events'],
                         [{'outcome': store.RESOLVED, 'at': STAMP, 'head': 'd' * 40}])
        self.assertEqual(ended['observations'], record['observations'])
        self.assertCode(store.INCIDENT_CLOSED, store.advance_outcome, ended['incident_id'],
                        ended['record_sha256'], store.SUPERSEDED, at=STAMP, root=self.root)
        self.assertCode(store.INCIDENT_CLOSED, store.append_observation, ended['incident_id'],
                        ended['record_sha256'], observation(), self.root)
        self.assertCode(store.INCIDENT_CLOSED, store.append_confirmation, ended['incident_id'],
                        ended['record_sha256'], self.confirmation(ended), self.root)
        self.assertEqual(store.find_open(self.root), [])

    def test_confirmation_is_bound_to_observation_evidence(self):
        record = self.make()
        wrong = dict(self.confirmation(record), evidence_sha256='0' * 64)
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.append_confirmation,
                        record['incident_id'], record['record_sha256'], wrong, self.root)
        out_of_range = dict(self.confirmation(record), observation_index=1)
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.append_confirmation,
                        record['incident_id'], record['record_sha256'], out_of_range, self.root)
        blank = dict(self.confirmation(record), reason='  ')
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.append_confirmation,
                        record['incident_id'], record['record_sha256'], blank, self.root)
        good = self.confirmation(record)
        updated = store.append_confirmation(record['incident_id'], record['record_sha256'],
                                            good, self.root)
        self.assertEqual(updated['confirmations'], [good])

    def test_tampered_record_is_invalid(self):
        record = self.make()
        path = self.directory / (record['incident_id'] + '.json')
        data = json.loads(path.read_text())
        data['observations'][0]['class'] = mi.ENV
        path.write_text(json.dumps(data))
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.get, record['incident_id'], self.root)

    def test_get_unknown_and_unsafe_ids(self):
        self.assertCode(store.INCIDENT_RECORD_UNAVAILABLE, store.get, 'mi-' + 'f' * 40, self.root)
        self.assertCode(store.INCIDENT_RECORD_INVALID, store.get, '../escape', self.root)

    def test_list_and_find_open(self):
        self.assertEqual(store.list_records(self.root), [])
        self.assertFalse(self.directory.exists())
        first = self.make()
        second = self.make(last_good=None)
        self.assertEqual([item['incident_id'] for item in store.list_records(self.root)],
                         sorted([first['incident_id'], second['incident_id']]))
        store.advance_outcome(first['incident_id'], first['record_sha256'], store.SUPERSEDED,
                              at=STAMP, root=self.root)
        self.assertEqual([item['incident_id'] for item in store.find_open(self.root)],
                         [second['incident_id']])


if __name__ == '__main__':
    unittest.main()
