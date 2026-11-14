import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from llmtool import merge_train_store as store  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

MAIN = 'a' * 40
HEAD = 'b' * 40
MERGE = 'c' * 40


def _member(index):
    return {
        'task_id': format(index, '032x'),
        'pr': 100 + index,
        'head': format(index, '040x'),
        'executor_task': 'dev-task-%d' % index,
        'review_version': 1,
        'linux_check_run_id': 5000 + index,
        'macos_check_run_id': None,
        'remote_test_receipt_digest': format(index, '012x'),
    }


def _record(train_id='t-1', **overrides):
    record = {
        'schema_version': 1,
        'train_id': train_id,
        'main_sha': MAIN,
        'members': [_member(1), _member(2)],
        'excluded': [{'task_id': format(9, '032x'), 'pr': 109, 'code': 'CI_FAILED'}],
        'train_branch': 'llmtool/train-' + train_id,
        'train_head': HEAD,
        'train_pr': None,
        'state': 'BUILT',
        'merge_attempt': None,
        'merge_sha': None,
        'created_at': '2026-11-07T00:00:00Z',
    }
    record.update(overrides)
    return record


def _attempt(state, pr=200, head_sha=HEAD, claimed_at='2026-11-07T01:00:00Z'):
    return {'pr': pr, 'head_sha': head_sha, 'state': state, 'claimed_at': claimed_at}


class MergeTrainStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve() / 'state'
        self.root = self.base / 'tasks'
        self.directory = self.base / 'merge-trains'

    def tearDown(self):
        self._tmp.cleanup()

    def assertRefused(self, code, function, *args, **kwargs):
        with self.assertRaises(LLMToolError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.merge_train_code, code)
        return context.exception

    def create(self, train_id='t-1', **overrides):
        return store.create(_record(train_id, **overrides), root=self.root)

    def update(self, record, **changes):
        return store.compare_and_update(record['train_id'], record['record_sha256'],
                                        root=self.root, **changes)

    def path(self, train_id='t-1'):
        return self.directory / (train_id + '.json')

    def rewrite(self, train_id, data):
        path = self.path(train_id)
        path.write_bytes(data)
        os.chmod(path, 0o600)

    # Drive a fresh record into one state through allowed moves only.
    def in_state(self, state, train_id='t-1'):
        record = self.create(train_id)
        if state == 'BUILT':
            return record
        if state == 'SUPERSEDED':
            return self.update(record, state='SUPERSEDED')
        record = self.update(record, state='MERGE_CLAIMED', train_pr=200,
                             merge_attempt=_attempt('CLAIMED'))
        if state == 'MERGE_CLAIMED':
            return record
        if state == 'UNKNOWN':
            return self.update(record, state='UNKNOWN', merge_attempt=_attempt('UNKNOWN'))
        record = self.update(record, state='MERGED', merge_sha=MERGE,
                             merge_attempt=_attempt('MERGED'))
        if state == 'MERGED':
            return record
        return self.update(record, state='CLOSED')

    def test_create_and_get_round_trip(self):
        created = self.create()
        body = {key: value for key, value in created.items() if key != 'record_sha256'}
        expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'))
                                  .encode('utf-8')).hexdigest()
        self.assertEqual(created['record_sha256'], expected)
        self.assertEqual(body, _record())
        self.assertEqual(store.get('t-1', root=self.root), created)
        self.assertEqual(json.loads(self.path().read_text('utf-8')), created)

    def test_create_ignores_supplied_digest_and_detaches_input(self):
        source = _record(record_sha256='0' * 64)
        created = store.create(source, root=self.root)
        self.assertNotEqual(created['record_sha256'], '0' * 64)
        source['members'][0]['pr'] = 999
        self.assertEqual(store.get('t-1', root=self.root)['members'][0]['pr'], 101)

    def test_duplicate_create_refused(self):
        self.create()
        self.assertRefused('TRAIN_RECORD_EXISTS', self.create)
        self.assertEqual(sorted(os.listdir(self.directory)), ['t-1.json'])

    def test_create_requires_new_state(self):
        self.assertRefused('TRAIN_STATE_INVALID', self.create, state='MERGE_CLAIMED')
        self.assertRefused('TRAIN_RECORD_INVALID', self.create, merge_sha=MERGE)
        self.assertRefused('TRAIN_RECORD_INVALID', self.create,
                           merge_attempt=_attempt('CLAIMED'))
        self.assertFalse(self.path().exists())

    def test_create_valid_non_built_state_refused(self):
        # A fully valid MERGED shape passes validation, then fails the BUILT rule.
        self.assertRefused('TRAIN_STATE_INVALID', self.create, state='MERGED',
                           train_pr=200, merge_sha=MERGE, merge_attempt=_attempt('MERGED'))
        self.assertFalse(self.path().exists())

    def test_each_bad_field_refused(self):
        member = _member(1)
        bad = []

        def with_member(**changes):
            first = dict(member, **changes)
            return {'members': [first, _member(2)]}

        def without_member_key(key):
            first = dict(member)
            del first[key]
            return {'members': [first, _member(2)]}

        bad.extend([
            {'schema_version': 2}, {'schema_version': True}, {'schema_version': '1'},
            {'train_id': 'T-1', 'train_branch': 'llmtool/train-T-1'},
            {'train_id': '-t', 'train_branch': 'llmtool/train--t'},
            {'train_id': 'a' * 65, 'train_branch': 'llmtool/train-' + 'a' * 65},
            {'main_sha': 'A' * 40}, {'main_sha': 'a' * 39}, {'main_sha': None},
            {'train_head': 'g' * 40}, {'train_head': 'b' * 41},
            {'train_branch': 'llmtool/train-other'}, {'train_branch': 't-1'},
            {'train_pr': 0}, {'train_pr': 10000000}, {'train_pr': '5'}, {'train_pr': True},
            {'merge_sha': 'z' * 40},
            {'created_at': 'yesterday'}, {'created_at': '2026-11-07T00:00:00'},
            {'created_at': '2026-11-07T00:00:00+08:00'}, {'created_at': 5},
            {'state': 'DONE'},
            {'members': []}, {'members': [_member(1)]},
            {'members': [_member(index) for index in range(1, 66)]},
            {'members': [_member(1), _member(1)]},
            {'members': [_member(1), dict(_member(2), task_id=member['task_id'])]},
            {'members': [_member(1), dict(_member(2), pr=member['pr'])]},
            {'members': [_member(1), dict(_member(2), head=member['head'])]},
            {'members': 'x'},
            with_member(task_id='x' * 32), with_member(task_id='a' * 31),
            with_member(pr='1'), with_member(pr=0), with_member(head='a' * 39),
            with_member(executor_task='Bad Task'), with_member(executor_task=''),
            with_member(review_version=0), with_member(review_version=True),
            with_member(linux_check_run_id=0), with_member(macos_check_run_id='7'),
            with_member(remote_test_receipt_digest='XYZ'),
            with_member(remote_test_receipt_digest='abc'),
            with_member(extra=1), without_member_key('review_version'),
            {'excluded': None},
            {'excluded': [{'task_id': format(index + 100, '032x'), 'pr': 1, 'code': 'X'}
                          for index in range(65)]},
            {'excluded': [{'task_id': 'a' * 32, 'pr': 1, 'code': 'lower'}]},
            {'excluded': [{'task_id': 'a' * 32, 'pr': 1}]},
            {'excluded': [{'task_id': 'a' * 32, 'pr': 1, 'code': 'X', 'why': 'y'}]},
            {'excluded': [{'task_id': member['task_id'], 'pr': 1, 'code': 'X'}]},
        ])
        for overrides in bad:
            with self.subTest(overrides=overrides):
                self.assertRefused('TRAIN_RECORD_INVALID', self.create, **overrides)
        unknown = _record(extra=1)
        self.assertRefused('TRAIN_RECORD_INVALID', store.create, unknown, root=self.root)
        for key in ('main_sha', 'members', 'created_at', 'state'):
            with self.subTest(missing=key):
                missing = _record()
                del missing[key]
                self.assertRefused('TRAIN_RECORD_INVALID', store.create, missing,
                                   root=self.root)
        self.assertRefused('TRAIN_RECORD_INVALID', store.create, [], root=self.root)
        self.assertFalse(self.directory.exists() and os.listdir(self.directory))

    def test_bad_merge_attempt_refused(self):
        record = self.create()
        for attempt in (
                {'pr': 200, 'head_sha': HEAD, 'state': 'CLAIMED'},
                dict(_attempt('CLAIMED'), extra=1),
                _attempt('DONE'), _attempt('CLAIMED', pr=0),
                _attempt('CLAIMED', head_sha='x' * 40), _attempt('CLAIMED', claimed_at=5),
                'CLAIMED'):
            with self.subTest(attempt=attempt):
                self.assertRefused('TRAIN_RECORD_INVALID', self.update, record,
                                   state='MERGE_CLAIMED', merge_attempt=attempt)
        self.assertEqual(store.get('t-1', root=self.root), record)

    def test_get_missing_and_bad_id(self):
        self.assertRefused('TRAIN_RECORD_UNAVAILABLE', store.get, 'none', root=self.root)
        self.assertRefused('TRAIN_RECORD_INVALID', store.get, '../x', root=self.root)

    def test_digest_tamper_refused(self):
        created = self.create()
        tampered = dict(created, main_sha='d' * 40)
        self.rewrite('t-1', json.dumps(tampered).encode('utf-8'))
        error = self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-1', root=self.root)
        self.assertEqual(str(error), 'TRAIN_RECORD_INVALID: train t-1')
        self.assertNotIn('d' * 40, str(error))

    def test_unparseable_and_misnamed_records_refused(self):
        created = self.create()
        for data in (b'{', b'\xff', b'[]', b'{"a": NaN}',
                     b'{"train_id": "t-1", "train_id": "t-1"}'):
            with self.subTest(data=data):
                self.rewrite('t-1', data)
                self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-1', root=self.root)
        self.rewrite('t-2', json.dumps(created).encode('utf-8'))
        self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-2', root=self.root)

    def test_bad_file_mode_refused(self):
        self.create()
        os.chmod(self.path(), 0o644)
        self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-1', root=self.root)

    def test_conflict_on_stale_digest(self):
        created = self.create()
        updated = self.update(created, train_pr=200)
        self.assertEqual(updated['train_pr'], 200)
        self.assertRefused('TRAIN_RECORD_CONFLICT', self.update, created, train_pr=201)
        self.assertRefused('TRAIN_RECORD_CONFLICT', store.compare_and_update, 't-1', None,
                           root=self.root, train_pr=201)
        self.assertEqual(store.get('t-1', root=self.root), updated)

    def test_held_record_lock_refuses_busy_and_changes_nothing(self):
        # #1182: a record whose lock another holder keeps refuses TRAIN_RECORD_BUSY.
        import fcntl
        from unittest import mock
        created = self.create()
        holder = os.open(self.directory / '.t-1.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX)
            with mock.patch.object(store, 'LOCK_TIMEOUT_SECONDS', 0.05):
                self.assertRefused('TRAIN_RECORD_BUSY', self.update, created, train_pr=200)
        finally:
            os.close(holder)
        self.assertEqual(store.get('t-1', root=self.root), created)

    def test_immutable_keys_refused(self):
        created = self.create()
        for key, value in (('main_sha', 'd' * 40), ('members', []), ('excluded', []),
                           ('train_head', 'd' * 40), ('train_branch', 'x'),
                           ('created_at', '2026-11-08T00:00:00Z'), ('schema_version', 1),
                           ('record_sha256', '0' * 64), ('unknown', 1)):
            with self.subTest(key=key):
                self.assertRefused('TRAIN_RECORD_IMMUTABLE', self.update, created,
                                   **{key: value})
        self.assertEqual(store.get('t-1', root=self.root), created)

    def test_update_returns_new_digest_and_persists(self):
        created = self.create()
        updated = self.update(created, train_pr=200)
        self.assertNotEqual(updated['record_sha256'], created['record_sha256'])
        self.assertEqual(updated['record_sha256'], store.record_digest(updated))
        self.assertEqual(store.get('t-1', root=self.root), updated)
        self.assertRefused('TRAIN_RECORD_UNAVAILABLE', store.compare_and_update, 'none',
                           '0' * 64, root=self.root, train_pr=1)

    def test_every_allowed_state_move(self):
        moves = [
            ('BUILT', {'state': 'MERGE_CLAIMED', 'train_pr': 200,
                       'merge_attempt': _attempt('CLAIMED')}),
            ('BUILT', {'state': 'SUPERSEDED'}),
            ('MERGE_CLAIMED', {'state': 'MERGED', 'merge_sha': MERGE,
                               'merge_attempt': _attempt('MERGED')}),
            ('MERGE_CLAIMED', {'state': 'UNKNOWN', 'merge_attempt': _attempt('UNKNOWN')}),
            ('UNKNOWN', {'state': 'MERGE_CLAIMED',
                         'merge_attempt': _attempt('CLAIMED',
                                                   claimed_at='2026-11-07T02:00:00Z')}),
            ('UNKNOWN', {'state': 'MERGED', 'merge_sha': MERGE,
                         'merge_attempt': _attempt('MERGED')}),
            ('UNKNOWN', {'state': 'SUPERSEDED'}),
            ('MERGED', {'state': 'CLOSED'}),
        ]
        for index, (start, changes) in enumerate(moves):
            with self.subTest(start=start, target=changes['state']):
                record = self.in_state(start, 'm-%d' % index)
                updated = self.update(record, **changes)
                self.assertEqual(updated['state'], changes['state'])
                self.assertEqual(store.get(record['train_id'], root=self.root), updated)

    def test_refused_state_moves(self):
        refused = [
            ('BUILT', 'MERGED'), ('BUILT', 'UNKNOWN'), ('BUILT', 'CLOSED'),
            ('MERGE_CLAIMED', 'BUILT'), ('MERGE_CLAIMED', 'SUPERSEDED'),
            ('MERGE_CLAIMED', 'CLOSED'), ('UNKNOWN', 'BUILT'), ('UNKNOWN', 'CLOSED'),
            ('MERGED', 'UNKNOWN'), ('MERGED', 'SUPERSEDED'), ('MERGED', 'MERGE_CLAIMED'),
            ('SUPERSEDED', 'BUILT'), ('SUPERSEDED', 'MERGE_CLAIMED'),
            ('CLOSED', 'MERGED'), ('CLOSED', 'BUILT'), ('BUILT', 'DONE'), ('BUILT', None),
        ]
        for index, (start, target) in enumerate(refused):
            with self.subTest(start=start, target=target):
                record = self.in_state(start, 'r-%d' % index)
                self.assertRefused('TRAIN_STATE_INVALID', self.update, record, state=target)
                self.assertEqual(store.get(record['train_id'], root=self.root), record)

    def test_final_states_accept_no_change(self):
        for index, state in enumerate(('SUPERSEDED', 'CLOSED')):
            with self.subTest(state=state):
                record = self.in_state(state, 'f-%d' % index)
                self.assertRefused('TRAIN_STATE_INVALID', self.update, record,
                                   train_pr=300)
                self.assertRefused('TRAIN_STATE_INVALID', self.update, record, state=state)

    def test_merged_shape_rule(self):
        bad = [
            {'merge_attempt': _attempt('MERGED')},
            {'merge_sha': 'x' * 40, 'merge_attempt': _attempt('MERGED')},
            {'merge_sha': MERGE, 'merge_attempt': _attempt('CLAIMED')},
            {'merge_sha': MERGE, 'merge_attempt': _attempt('MERGED', pr=201)},
            {'merge_sha': MERGE, 'merge_attempt': _attempt('MERGED', head_sha='d' * 40)},
            {'merge_sha': MERGE, 'merge_attempt': None},
            {'merge_sha': MERGE, 'merge_attempt': _attempt('MERGED'), 'train_pr': None},
        ]
        record = self.in_state('MERGE_CLAIMED')
        for changes in bad:
            with self.subTest(changes=changes):
                self.assertRefused('TRAIN_RECORD_INVALID', self.update, record,
                                   state='MERGED', **changes)
        self.assertEqual(store.get('t-1', root=self.root), record)
        merged = self.update(record, state='MERGED', merge_sha=MERGE,
                             merge_attempt=_attempt('MERGED'))
        self.assertEqual(merged['merge_sha'], MERGE)
        self.assertRefused('TRAIN_RECORD_INVALID', self.update, merged, merge_sha=None)

    def test_find_by_pr(self):
        self.assertIsNone(store.find_by_pr(200, root=self.root))
        first = self.create('t-1')
        self.create('t-2')
        self.assertIsNone(store.find_by_pr(200, root=self.root))
        first = self.update(first, train_pr=200)
        self.assertEqual(store.find_by_pr(200, root=self.root), first)
        self.assertIsNone(store.find_by_pr(201, root=self.root))
        self.update(store.get('t-2', root=self.root), train_pr=200)
        self.assertRefused('TRAIN_RECORD_AMBIGUOUS', store.find_by_pr, 200, root=self.root)
        self.assertRefused('TRAIN_RECORD_INVALID', store.find_by_pr, 0, root=self.root)

    def test_find_by_pr_refuses_unreadable_record(self):
        self.create('t-1')
        self.rewrite('t-3', b'not json')
        error = self.assertRefused('TRAIN_RECORD_INVALID', store.find_by_pr, 200,
                                   root=self.root)
        self.assertEqual(str(error), 'TRAIN_RECORD_INVALID: train t-3')
        os.unlink(self.path('t-3'))
        (self.directory / 'stray.txt').write_bytes(b'')
        self.assertRefused('TRAIN_RECORD_INVALID', store.find_by_pr, 200, root=self.root)

    def test_file_and_directory_modes(self):
        old = os.umask(0o022)
        try:
            created = self.create()
            self.update(created, train_pr=200)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.lstat(self.directory).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.lstat(self.path()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(self.directory / '.t-1.lock').st_mode), 0o600)
        self.assertEqual(self.directory, self.root.parent / 'merge-trains')
        self.assertEqual(sorted(name for name in os.listdir(self.directory)
                                if name.endswith('.tmp')), [])

    def test_symlinked_directory_refused(self):
        real = self.base / 'elsewhere'
        real.mkdir(parents=True, mode=0o700)
        os.symlink(real, self.directory)
        self.assertRefused('TRAIN_RECORD_INVALID', self.create)
        self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-1', root=self.root)
        self.assertRefused('TRAIN_RECORD_INVALID', store.find_by_pr, 1, root=self.root)
        self.assertEqual(os.listdir(real), [])

    def test_symlinked_file_refused(self):
        created = self.create('t-1')
        target = self.base / 'outside.json'
        target.write_bytes(self.path('t-1').read_bytes().replace(b'"t-1"', b'"t-2"'))
        os.chmod(target, 0o600)
        os.symlink(target, self.path('t-2'))
        self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-2', root=self.root)
        self.assertRefused('TRAIN_RECORD_INVALID', self.update,
                           dict(created, train_id='t-2'), train_pr=5)

    def test_hard_linked_file_refused(self):
        self.create('t-1')
        os.link(self.path('t-1'), self.base / 'extra-link.json')
        self.assertRefused('TRAIN_RECORD_INVALID', store.get, 't-1', root=self.root)

    def test_default_root_uses_task_store_parent(self):
        original = store.task_store.default_root
        store.task_store.default_root = lambda: self.root
        try:
            created = store.create(copy.deepcopy(_record()))
            self.assertEqual(store.get('t-1'), created)
        finally:
            store.task_store.default_root = original
        self.assertTrue(self.path().exists())

    # A docs-only or scripts-only member may carry no EC2 receipt digest.

    def test_null_receipt_digest_round_trips(self):
        members = [dict(_member(1), remote_test_receipt_digest=None), _member(2)]
        created = self.create(members=members)
        self.assertIsNone(created['members'][0]['remote_test_receipt_digest'])
        self.assertEqual(created['members'][1]['remote_test_receipt_digest'],
                         format(2, '012x'))
        body = {key: value for key, value in created.items() if key != 'record_sha256'}
        self.assertEqual(created['record_sha256'], store.record_digest(body))
        self.assertEqual(store.get('t-1', root=self.root), created)
        # The null digest survives every later state move and re-seal.
        record = self.update(created, state='MERGE_CLAIMED', train_pr=200,
                             merge_attempt=_attempt('CLAIMED'))
        self.assertIsNone(store.get('t-1', root=self.root)['members'][0][
            'remote_test_receipt_digest'])
        self.assertEqual(store.list_records(self.root), [record])

    def test_non_string_non_null_receipt_digest_refused(self):
        for digest in (0, 12345678, True, False, ['abcdef12'], {'d': 'abcdef12'}, 1.5,
                       '', 'ABCDEF12'):
            with self.subTest(digest=digest):
                members = [dict(_member(1), remote_test_receipt_digest=digest), _member(2)]
                self.assertRefused('TRAIN_RECORD_INVALID', self.create, members=members)
        self.assertFalse(self.path().exists())

    def test_short_hex_receipt_digest_still_seals_as_before(self):
        created = self.create()
        expected = hashlib.sha256(json.dumps(_record(), sort_keys=True,
                                             separators=(',', ':')).encode('utf-8')).hexdigest()
        self.assertEqual(created['record_sha256'], expected)
        self.assertEqual(created['members'][0]['remote_test_receipt_digest'],
                         format(1, '012x'))


if __name__ == '__main__':
    unittest.main()
