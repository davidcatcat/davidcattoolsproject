"""Tests for the private Owner instruction inbox store (#1160 PR 2).

Standard library only; each test uses its own temporary directory, chmod 0700
only, and reads no clock. No network, subprocess or CLI.
"""

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from llmtool import owner_instruction_inbox as oi  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

MISSING = 'OWNER_INBOX_STATE_MISSING'
EXISTS = 'OWNER_INBOX_STATE_EXISTS'
CORRUPT = 'OWNER_INBOX_STATE_CORRUPT'
CHANGED = 'OWNER_INBOX_STATE_CHANGED'
DIR_UNSAFE = 'OWNER_INBOX_DIR_UNSAFE'
WRITE_FAILED = 'OWNER_INBOX_WRITE_FAILED'
ARGUMENT_INVALID = 'OWNER_INBOX_ARGUMENT_INVALID'
DISABLED = 'OWNER_INBOX_DISABLED'
FULL = 'OWNER_INBOX_FULL'
HISTORY_LIMIT = 'OWNER_INBOX_HISTORY_LIMIT'
NOT_FOUND = 'OWNER_INBOX_NOT_FOUND'
NOT_ACKABLE = 'OWNER_INBOX_NOT_ACKABLE'

QUEUED, SEEN, ACKED, REVOKED = 'QUEUED', 'SEEN', 'ACKED', 'REVOKED'
EXPIRED, REQUESTED, REVOKE_ACKED = 'EXPIRED', 'REVOKE_REQUESTED', 'REVOKE_ACKED'
COMPACTED = 'COMPACTED'
NOW = 1_000_000
DAY = 86_400
TEXT = 'please run the report'


def ident(alias='ops', update_id=1):
    return oi.instruction_id(alias, update_id)


def code(alias='ops', update_id=1):
    return ident(alias, update_id)[:12]


def entry(ident_value=None, alias='ops', update_id=1, received_at=NOW,
          state=QUEUED, text=TEXT):
    if ident_value is None:
        ident_value = hashlib.sha256(
            b'llmtool.owner_instruction_inbox.v1\n' +
            str(alias).encode('ascii') + b'\n' +
            str(update_id).encode('ascii')).hexdigest()
    return {'id': ident_value, 'alias': alias, 'update_id': update_id,
            'received_at': received_at, 'state': state, 'text': text}


def document(entries=(), tombstones=(), enabled=True, **extra):
    data = {'schema_version': 1, 'enabled': enabled,
            'entries': list(entries), 'tombstones': list(tombstones)}
    data.update(extra)
    return json.dumps(data, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class InboxTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.realpath(self._tmp.name)
        os.chmod(self.dir, 0o700)
        self.addCleanup(os.chmod, self.dir, 0o700)
        self.path = os.path.join(self.dir, 'inbox.json')

    def init(self):
        oi.init_state(self.path)

    def assertCode(self, expected, func, *args, **kwargs):
        with self.assertRaises(oi.OwnerInboxError) as caught:
            func(*args, **kwargs)
        error = caught.exception
        self.assertIsInstance(error, LLMToolError)
        self.assertEqual(error.code, expected)
        self.assertEqual(str(error), expected)
        self.assertEqual(error.args, (expected,))
        self.assertIsNone(error.__cause__)
        return error

    def write_raw(self, data, mode=0o600):
        if os.path.lexists(self.path):
            os.unlink(self.path)
        descriptor = os.open(self.path,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.chmod(self.path, mode)

    def read_doc(self):
        with open(self.path, 'rb') as stream:
            return json.loads(stream.read().decode('utf-8'))

    def snapshot(self):
        info = os.stat(self.path, follow_symlinks=False)
        with open(self.path, 'rb') as stream:
            data = stream.read()
        return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size,
                stat.S_IMODE(info.st_mode), data)

    def age(self):
        os.utime(self.path, ns=(1, 1), follow_symlinks=False)

    def assertNoWrite(self, func, *args, **kwargs):
        self.age()
        before = self.snapshot()
        result = func(*args, **kwargs)
        self.assertEqual(self.snapshot(), before)
        return result

    def assertOnlyStateFile(self):
        self.assertEqual(sorted(os.listdir(self.dir)), ['inbox.json'])

    def patched_load(self, after_load):
        original = oi._load

        def wrapper(path):
            result = original(path)
            after_load()
            return result
        return mock.patch.object(oi, '_load', wrapper)

    def replace_externally(self, data):
        temp = os.path.join(self.dir, 'external.tmp')
        descriptor = os.open(temp,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)

    def add(self, alias='ops', update_id=1, text=TEXT, now=NOW):
        return oi.add(self.path, alias, update_id, text, now)

    def prepare(self, state=QUEUED, alias='ops', update_id=1):
        if os.path.lexists(self.path):
            os.unlink(self.path)
        oi.init_state(self.path)
        oi.add(self.path, alias, update_id, TEXT, NOW)
        if state != QUEUED:
            data = self.read_doc()
            data['entries'][0]['state'] = state
            self.write_raw(json.dumps(data).encode('utf-8'))
        return code(alias, update_id)


class InitTests(InboxTestCase):

    def test_init_creates_private_empty_state(self):
        previous = os.umask(0)
        try:
            self.assertIsNone(self.init())
        finally:
            os.umask(previous)
        info = os.stat(self.path, follow_symlinks=False)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(info.st_nlink, 1)
        self.assertEqual(self.read_doc(), {'schema_version': 1, 'enabled': True,
                                           'entries': [], 'tombstones': []})
        self.assertEqual(oi.status(self.path, 'ops', NOW), [])
        self.assertOnlyStateFile()

    def test_init_accepts_path_object(self):
        oi.init_state(Path(self.path))
        self.assertEqual(oi.peek(Path(self.path), NOW), [])

    def test_init_refuses_existing_file_without_change(self):
        self.init()
        self.add()
        before = self.snapshot()
        self.assertCode(EXISTS, oi.init_state, self.path)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.read_doc()['entries']), 1)

    def test_init_refuses_symlink_and_directory(self):
        target = os.path.join(self.dir, 'target.json')
        os.symlink(target, self.path)
        self.assertCode(EXISTS, oi.init_state, self.path)
        self.assertFalse(os.path.lexists(target))
        os.unlink(self.path)
        os.mkdir(self.path, 0o700)
        self.assertCode(EXISTS, oi.init_state, self.path)

    def test_init_refuses_group_or_other_writable_directory(self):
        for mode in (0o770, 0o720, 0o707, 0o702, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.dir, mode)
                self.assertCode(DIR_UNSAFE, oi.init_state, self.path)
                self.assertFalse(os.path.lexists(self.path))
        os.chmod(self.dir, 0o700)
        self.assertEqual(os.listdir(self.dir), [])

    def test_init_refuses_missing_or_symlinked_directory(self):
        self.assertCode(DIR_UNSAFE, oi.init_state,
                        os.path.join(self.dir, 'absent', 'inbox.json'))
        real = os.path.join(self.dir, 'real')
        os.mkdir(real, 0o700)
        link = os.path.join(self.dir, 'link')
        os.symlink(real, link)
        self.assertCode(DIR_UNSAFE, oi.init_state,
                        os.path.join(link, 'inbox.json'))
        self.assertEqual(os.listdir(real), [])


class ArgumentTests(InboxTestCase):

    def setUp(self):
        super().setUp()
        self.init()
        self.age()
        self.before = self.snapshot()

    def tearDown(self):
        self.assertEqual(self.snapshot(), self.before)
        self.assertOnlyStateFile()

    def test_state_paths_must_be_absolute_and_normalized(self):
        bad = ['inbox.json', '', self.dir + '/sub/../inbox.json',
               self.dir + '//inbox.json', self.dir + '/./inbox.json',
               self.path + '/', self.path + '\x00', '/', self.path.encode(),
               None, 42]
        for path in bad:
            with self.subTest(path=repr(path)):
                self.assertCode(ARGUMENT_INVALID, oi.init_state, path)
                self.assertCode(ARGUMENT_INVALID, oi.peek, path, NOW)
                self.assertCode(ARGUMENT_INVALID, oi.add, path, 'ops', 1,
                                TEXT, NOW)
                self.assertCode(ARGUMENT_INVALID, oi.status, path, 'ops', NOW)
                self.assertCode(ARGUMENT_INVALID, oi.set_enabled, path, True)
                self.assertCode(ARGUMENT_INVALID, oi.prune_tombstones, path,
                                0)

    def test_alias_pattern(self):
        for alias in ('a', 'ops', 'a1', 'a_b-c', 'z' + 'a' * 31):
            with self.subTest(alias=alias):
                self.assertEqual(oi.status(self.path, alias, NOW), [])
        bad = ['', 'A', 'Ops', '1a', '_a', '-a', 'a' * 33, 'a.b', 'a b',
               'a\n', 'é', 'a/b', b'ops', None, 1]
        for alias in bad:
            with self.subTest(alias=repr(alias)):
                self.assertCode(ARGUMENT_INVALID, oi.add, self.path, alias, 1,
                                TEXT, NOW)
                self.assertCode(ARGUMENT_INVALID, oi.revoke, self.path, alias,
                                code(), NOW)
                self.assertCode(ARGUMENT_INVALID, oi.status, self.path, alias,
                                NOW)

    def test_codes_must_be_twelve_lowercase_hex(self):
        bad = ['', 'abc', '0' * 11, '0' * 13, 'A' * 12, 'g' * 12,
               code().upper(), code().encode(), None, 12]
        for value in bad:
            with self.subTest(code=repr(value)):
                self.assertCode(ARGUMENT_INVALID, oi.mark_seen, self.path,
                                value, NOW)
                self.assertCode(ARGUMENT_INVALID, oi.ack, self.path,
                                value, NOW)
                self.assertCode(ARGUMENT_INVALID, oi.revoke, self.path, 'ops',
                                value, NOW)
                if value is not None:
                    self.assertCode(ARGUMENT_INVALID, oi.status, self.path,
                                    'ops', NOW, value)

    def test_update_ids_now_text_and_limit_types(self):
        for value in (-1, 2 ** 63, True, False, 1.0, '5', None):
            with self.subTest(update=repr(value)):
                self.assertCode(ARGUMENT_INVALID, oi.add, self.path, 'ops',
                                value, TEXT, NOW)
        for value in (-1, True, 1.0, '5', None):
            with self.subTest(now=repr(value)):
                self.assertCode(ARGUMENT_INVALID, oi.peek, self.path, value)
                self.assertCode(ARGUMENT_INVALID, oi.status, self.path, 'ops',
                                value)
                self.assertCode(ARGUMENT_INVALID, oi.add, self.path, 'ops',
                                1, TEXT, value)
        for value in ('', 'x' * 2001, None, 7, 'a\x00b', b'bytes'):
            with self.subTest(text=repr(value)[:20]):
                self.assertCode(ARGUMENT_INVALID, oi.add, self.path, 'ops', 1,
                                value, NOW)
        for value in (0, 11, True, 1.0, '5', None):
            with self.subTest(limit=repr(value)):
                self.assertCode(ARGUMENT_INVALID, oi.status, self.path, 'ops',
                                NOW, None, value)

    def test_enabled_must_be_bool(self):
        for value in (1, 0, 'true', None, [], 1.0):
            with self.subTest(value=repr(value)):
                self.assertCode(ARGUMENT_INVALID, oi.set_enabled, self.path,
                                value)


class MissingTests(InboxTestCase):

    def test_missing_state_fails_closed_and_never_creates(self):
        calls = [
            (oi.peek, (self.path, NOW)),
            (oi.status, (self.path, 'ops', NOW)),
            (oi.mark_seen, (self.path, code(), NOW)),
            (oi.ack, (self.path, code(), NOW)),
            (oi.revoke, (self.path, 'ops', code(), NOW)),
            (oi.set_enabled, (self.path, True)),
            (oi.add, (self.path, 'ops', 1, TEXT, NOW)),
            (oi.prune_tombstones, (self.path, 0)),
        ]
        for func, args in calls:
            with self.subTest(func=func.__name__):
                self.assertCode(MISSING, func, *args)
        self.assertEqual(os.listdir(self.dir), [])

    def test_missing_directory_is_missing_for_readers(self):
        path = os.path.join(self.dir, 'absent', 'inbox.json')
        self.assertCode(MISSING, oi.peek, path, NOW)
        self.assertCode(MISSING, oi.add, path, 'ops', 1, TEXT, NOW)
        self.assertCode(DIR_UNSAFE, oi.init_state, path)


class CorruptTests(InboxTestCase):

    def assertCorrupt(self):
        before = self.snapshot()
        self.assertCode(CORRUPT, oi.peek, self.path, NOW)
        self.assertCode(CORRUPT, oi.add, self.path, 'ops', 1, TEXT, NOW)
        self.assertCode(CORRUPT, oi.status, self.path, 'ops', NOW)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_documents_are_corrupt(self):
        too_many_entries = [entry(update_id=n) for n in range(201)]
        too_many_tombstones = [ident('ops', n) for n in range(1001)]
        too_many_live = [entry(update_id=n) for n in range(21)]
        cases = {
            'empty': b'',
            'bad_json': b'{"schema_version":1,',
            'top_list': b'[]',
            'top_null': b'null',
            'trailing': document() + b' x',
            'not_utf8': b'\xff\xfe{}',
            'duplicate_top': (b'{"schema_version":1,"schema_version":1,'
                              b'"enabled":true,"entries":[],"tombstones":[]}'),
            'duplicate_field': ('{"schema_version":1,"enabled":true,'
                                '"entries":[{"id":"%s","alias":"ops",'
                                '"update_id":1,"received_at":1,'
                                '"state":"QUEUED","state":"SEEN","text":"x"}],'
                                '"tombstones":[]}' % ('0' * 64)).encode(),
            'nan': document().replace(b'"enabled":true',
                                      b'"enabled":true,"x":NaN'),
            'unknown_top': document(extra_field=1),
            'missing_top': b'{"schema_version":1,"enabled":true,"entries":[]}',
            'version_2': document().replace(b'"schema_version":1',
                                            b'"schema_version":2'),
            'version_bool': b'{"schema_version":true,"enabled":true,'
                            b'"entries":[],"tombstones":[]}',
            'version_float': document().replace(b'"schema_version":1',
                                                b'"schema_version":1.0'),
            'enabled_int': document(enabled=1),
            'entries_object': b'{"schema_version":1,"enabled":true,'
                              b'"entries":{},"tombstones":[]}',
            'entry_list': document([[]]),
            'unknown_entry': document([dict(entry(), extra=1)]),
            'missing_entry_field': document(
                [{key: value for key, value in entry().items()
                  if key != 'text'}]),
            'bad_id': document([entry(ident_value='ABC')]),
            'bad_alias': document([entry(alias='Ops')]),
            'update_bool': document([entry(update_id=True)]),
            'update_negative': document([entry(update_id=-1)]),
            'stamp_bool': document([entry(received_at=True)]),
            'stamp_negative': document([entry(received_at=-1)]),
            'bad_state': document([entry(state='DONE')]),
            'text_int': document([entry(text=1)]),
            'duplicate_id': document([entry(update_id=1), entry(update_id=1)]),
            'id_in_tombstones': document([entry(update_id=1)],
                                         [ident('ops', 1)]),
            'bad_tombstone': document([], ['ABC']),
            'duplicate_tombstone': document([], [ident('ops', 1)] * 2),
            'too_many_entries': document(too_many_entries),
            'too_many_tombstones': document([], too_many_tombstones),
            'too_many_live': document(too_many_live),
            'text_too_long': document([entry(text='x' * 2001)]),
            'text_nul': document([entry(text='a\x00b')]),
            'id_mismatch': document([entry(ident_value=ident('ops', 2))]),
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.write_raw(data)
                self.assertCorrupt()

    def test_wrong_mode_symlink_and_hard_link_are_corrupt(self):
        for mode in (0o644, 0o640, 0o604, 0o400, 0o700, 0o660):
            with self.subTest(mode=oct(mode)):
                self.write_raw(document(), mode)
                self.assertCorrupt()
        target = os.path.join(self.dir, 'target.json')
        self.write_raw(document())
        os.replace(self.path, target)
        os.symlink(target, self.path)
        self.assertCorrupt()
        self.assertTrue(os.path.islink(self.path))
        os.unlink(self.path)
        self.init()
        os.link(self.path, os.path.join(self.dir, 'second.json'))
        self.assertCorrupt()

    def test_size_limit_is_one_mebibyte(self):
        base = document()
        exact = base + b' ' * (oi.MAX_STATE_BYTES - len(base))
        self.write_raw(exact)
        self.assertEqual(oi.peek(self.path, NOW), [])
        self.write_raw(exact + b' ')
        self.assertCode(CORRUPT, oi.peek, self.path, NOW)

    def test_unsafe_directory_blocks_load_and_write(self):
        self.init()
        self.add()
        before = self.snapshot()
        os.chmod(self.dir, 0o770)
        self.assertCode(DIR_UNSAFE, oi.peek, self.path, NOW)
        self.assertCode(DIR_UNSAFE, oi.add, self.path, 'ops', 2, TEXT, NOW)
        os.chmod(self.dir, 0o700)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(oi.peek(self.path, NOW)), 1)


class AddTests(InboxTestCase):

    def setUp(self):
        super().setUp()
        self.init()

    def test_add_queues_an_entry_and_returns_receipt(self):
        result = self.add(update_id=7, text='check the queue', now=NOW)
        self.assertEqual(result, {'code': code(update_id=7),
                                  'state': QUEUED, 'created': True})
        doc = self.read_doc()
        self.assertEqual(doc['entries'], [{
            'id': ident(update_id=7), 'alias': 'ops', 'update_id': 7,
            'received_at': NOW, 'state': QUEUED, 'text': 'check the queue'}])
        self.assertEqual(doc['tombstones'], [])
        self.assertTrue(doc['enabled'])

    def test_resend_of_live_instruction_is_inert(self):
        first = self.add()
        result = self.assertNoWrite(self.add, text='different text')
        self.assertEqual(result, {'code': first['code'], 'state': QUEUED,
                                  'created': False})
        self.assertEqual(self.read_doc()['entries'][0]['text'], TEXT)

    def test_instruction_id_formula(self):
        expected = hashlib.sha256(
            b'llmtool.owner_instruction_inbox.v1\nops\n42').hexdigest()
        self.assertEqual(ident(update_id=42), expected)
        self.assertEqual(len(expected), 64)
        self.assertEqual(code(update_id=42), expected[:12])
        self.assertNotEqual(ident(alias='ops'), ident(alias='other'))
        self.assertCode(ARGUMENT_INVALID, oi.instruction_id, 'Ops', 1)

    def test_aliases_are_isolated(self):
        first = self.add(alias='ops', update_id=1)
        second = self.add(alias='other', update_id=1)
        self.assertNotEqual(first['code'], second['code'])
        self.assertCode(NOT_FOUND, oi.revoke, self.path, 'other',
                        first['code'], NOW)
        self.assertCode(NOT_FOUND, oi.status, self.path, 'other', NOW,
                        first['code'])
        self.assertEqual(oi.status(self.path, 'other', NOW),
                         [{'code': second['code'], 'state': QUEUED}])

    def test_twenty_unacked_is_full_then_ack_makes_room(self):
        for number in range(20):
            self.assertTrue(self.add(update_id=number + 1,
                                     now=NOW + number)['created'])
        self.assertNoWrite(self.assertCode, FULL, oi.add, self.path, 'ops', 99,
                           TEXT, NOW + 20)
        oi.ack(self.path, code(update_id=1), NOW + 20)
        self.assertTrue(self.add(update_id=99, now=NOW + 20)['created'])

    def test_no_terminal_entry_to_compact_is_full(self):
        with mock.patch.object(oi, 'MAX_ENTRIES', 1), \
                mock.patch.object(oi, 'MAX_UNACKED', 5):
            self.add(update_id=1)
            self.assertCode(FULL, oi.add, self.path, 'ops', 2, TEXT, NOW)

    def test_expiry_frees_unacked_room(self):
        for number in range(20):
            self.add(update_id=number + 1, now=NOW)
        self.assertTrue(self.add(update_id=99, now=NOW + DAY)['created'])
        states = [item['state'] for item in self.read_doc()['entries']]
        self.assertEqual(states.count(EXPIRED), 20)
        self.assertEqual(states.count(QUEUED), 1)

    def test_compaction_drops_oldest_terminal_entry(self):
        with mock.patch.object(oi, 'MAX_ENTRIES', 3):
            self.add(update_id=1, now=NOW)
            second = self.add(update_id=2, now=NOW + 1)
            self.add(update_id=3, now=NOW + 2)
            oi.ack(self.path, second['code'], NOW + 2)
            self.assertTrue(self.add(update_id=4, now=NOW + 3)['created'])
            doc = self.read_doc()
            self.assertEqual([item['id'] for item in doc['entries']],
                             [ident(update_id=1), ident(update_id=3),
                              ident(update_id=4)])
            self.assertEqual(doc['tombstones'], [ident(update_id=2)])

    def test_tombstone_dedupe(self):
        with mock.patch.object(oi, 'MAX_ENTRIES', 1):
            first = self.add(update_id=1)
            oi.ack(self.path, first['code'], NOW)
            self.add(update_id=2, now=NOW + 1)
            result = self.assertNoWrite(self.add, update_id=1, now=NOW + 2)
            self.assertEqual(result, {'code': first['code'],
                                      'state': COMPACTED, 'created': False})
            self.assertEqual(self.read_doc()['tombstones'], [ident(update_id=1)])
            self.assertNotIn(COMPACTED, oi.STATES)

    def test_history_limit_refuses_compaction(self):
        with mock.patch.object(oi, 'MAX_ENTRIES', 2), \
                mock.patch.object(oi, 'MAX_TOMBSTONES', 0):
            first = self.add(update_id=1)
            self.add(update_id=2, now=NOW + 1)
            oi.ack(self.path, first['code'], NOW)
            before = self.snapshot()
            self.assertCode(HISTORY_LIMIT, oi.add, self.path, 'ops', 3, TEXT,
                            NOW)
            self.assertEqual(self.snapshot(), before)

    def test_state_size_limit_refuses_the_write(self):
        with mock.patch.object(oi, 'MAX_STATE_BYTES', 200):
            self.assertNoWrite(self.assertCode, WRITE_FAILED, oi.add,
                               self.path, 'ops', 1, 'x' * 200, NOW)

    def test_disabled_refuses_add_but_revoke_works(self):
        first = self.add()
        self.assertIs(oi.set_enabled(self.path, False), False)
        self.assertNoWrite(self.assertCode, DISABLED, oi.add, self.path, 'ops',
                           2, TEXT, NOW)
        self.assertEqual(oi.revoke(self.path, 'ops', first['code'], NOW),
                         {'code': first['code'], 'state': REVOKED})
        self.assertTrue(oi.set_enabled(self.path, True))
        self.assertTrue(self.add(update_id=3)['created'])

    def test_set_enabled_is_inert_for_same_value(self):
        self.assertNoWrite(oi.set_enabled, self.path, True)
        self.assertIs(oi.set_enabled(self.path, False), False)
        self.assertFalse(self.read_doc()['enabled'])
        self.assertNoWrite(oi.set_enabled, self.path, False)


class ExpiryTests(InboxTestCase):

    def setUp(self):
        super().setUp()
        self.init()

    def test_expiry_boundary_and_read_only_states(self):
        first = self.add(now=NOW)
        self.assertEqual(oi.peek(self.path, NOW + DAY - 1)[0]['state'], QUEUED)
        self.assertEqual(oi.peek(self.path, NOW + DAY), [])
        self.assertEqual(oi.status(self.path, 'ops', NOW + DAY),
                         [{'code': first['code'], 'state': EXPIRED}])
        self.assertEqual(self.read_doc()['entries'][0]['state'], QUEUED)
        self.assertNoWrite(oi.peek, self.path, NOW + DAY)
        self.assertNoWrite(oi.status, self.path, 'ops', NOW + DAY)
        self.assertEqual(self.read_doc()['entries'][0]['state'], QUEUED)

    def test_seen_expires_after_a_day(self):
        first = self.add(now=NOW)
        oi.mark_seen(self.path, first['code'], NOW + 10)
        self.assertEqual(oi.peek(self.path, NOW + DAY - 1)[0]['state'], SEEN)
        self.assertEqual(oi.status(self.path, 'ops', NOW + DAY),
                         [{'code': first['code'], 'state': EXPIRED}])

    def test_expired_entry_refusal_and_revoke(self):
        first = self.add(now=NOW)
        later = NOW + DAY
        self.assertCode(NOT_ACKABLE, oi.mark_seen, self.path, first['code'],
                        later)
        self.assertCode(NOT_ACKABLE, oi.ack, self.path, first['code'], later)
        self.assertEqual(oi.revoke(self.path, 'ops', first['code'], later),
                         {'code': first['code'], 'state': REVOKED})
        self.assertEqual(self.read_doc()['entries'][0]['state'], REVOKED)


class TransitionTests(InboxTestCase):

    def test_mark_seen_transitions(self):
        table = {QUEUED: (SEEN, True), SEEN: (SEEN, False),
                 ACKED: (NOT_ACKABLE, False), REVOKED: (NOT_ACKABLE, False),
                 EXPIRED: (NOT_ACKABLE, False),
                 REQUESTED: (NOT_ACKABLE, False),
                 REVOKE_ACKED: (NOT_ACKABLE, False)}
        for state, (outcome, writes) in table.items():
            with self.subTest(state=state):
                target = self.prepare(state)
                if writes:
                    self.assertEqual(oi.mark_seen(self.path, target, NOW),
                                     {'code': target, 'state': outcome})
                    self.assertEqual(self.read_doc()['entries'][0]['state'],
                                     outcome)
                elif outcome == NOT_ACKABLE:
                    self.assertNoWrite(self.assertCode, NOT_ACKABLE,
                                       oi.mark_seen, self.path, target, NOW)
                else:
                    self.assertEqual(
                        self.assertNoWrite(oi.mark_seen, self.path, target,
                                           NOW),
                        {'code': target, 'state': outcome})
                if not writes:
                    self.assertEqual(self.read_doc()['entries'][0]['state'],
                                     state)

    def test_ack_transitions(self):
        table = {QUEUED: (ACKED, True), SEEN: (ACKED, True),
                 REQUESTED: (REVOKE_ACKED, True),
                 ACKED: (ACKED, False), REVOKE_ACKED: (REVOKE_ACKED, False),
                 REVOKED: (NOT_ACKABLE, False),
                 EXPIRED: (NOT_ACKABLE, False)}
        for state, (outcome, writes) in table.items():
            with self.subTest(state=state):
                target = self.prepare(state)
                if writes:
                    self.assertEqual(oi.ack(self.path, target, NOW),
                                     {'code': target, 'state': outcome})
                    self.assertEqual(self.read_doc()['entries'][0]['state'],
                                     outcome)
                elif outcome == NOT_ACKABLE:
                    self.assertNoWrite(self.assertCode, NOT_ACKABLE, oi.ack,
                                       self.path, target, NOW)
                else:
                    self.assertEqual(
                        self.assertNoWrite(oi.ack, self.path, target, NOW),
                        {'code': target, 'state': outcome})
                if not writes:
                    self.assertEqual(self.read_doc()['entries'][0]['state'],
                                     state)

    def test_revoke_transitions(self):
        table = {QUEUED: (REVOKED, True), SEEN: (REVOKED, True),
                 EXPIRED: (REVOKED, True), ACKED: (REQUESTED, True),
                 REVOKED: (REVOKED, False), REQUESTED: (REQUESTED, False),
                 REVOKE_ACKED: (REVOKE_ACKED, False)}
        for state, (outcome, writes) in table.items():
            with self.subTest(state=state):
                target = self.prepare(state)
                if writes:
                    self.assertEqual(oi.revoke(self.path, 'ops', target, NOW),
                                     {'code': target, 'state': outcome})
                    self.assertEqual(self.read_doc()['entries'][0]['state'],
                                     outcome)
                else:
                    self.assertEqual(
                        self.assertNoWrite(oi.revoke, self.path, 'ops',
                                           target, NOW),
                        {'code': target, 'state': outcome})
                    self.assertEqual(self.read_doc()['entries'][0]['state'],
                                     state)

    def test_unknown_codes_are_not_found(self):
        self.init()
        unknown = 'f' * 12
        for func, args in ((oi.mark_seen, (self.path, unknown, NOW)),
                           (oi.ack, (self.path, unknown, NOW)),
                           (oi.revoke, (self.path, 'ops', unknown, NOW))):
            with self.subTest(func=func.__name__):
                self.assertCode(NOT_FOUND, func, *args)


class LiveCapRevokeTests(InboxTestCase):
    """F1 (codex-reviewer v1): ACKED -> REVOKE_REQUESTED needs live room."""

    def setUp(self):
        super().setUp()
        self.init()

    def fill(self, count, start=2):
        return [self.add(update_id=start + number, now=NOW)
                for number in range(count)]

    def test_acked_target_at_live_cap_is_full_without_change(self):
        target = self.add(update_id=1, now=NOW)
        oi.ack(self.path, target['code'], NOW)
        live = self.fill(20)
        before = self.read_doc()
        self.assertNoWrite(self.assertCode, FULL, oi.revoke, self.path, 'ops',
                           target['code'], NOW)
        self.assertEqual(self.read_doc(), before)
        self.assertEqual(len(oi.peek(self.path, NOW)), 20)
        self.assertEqual(oi.status(self.path, 'ops', NOW, code=target['code']),
                         [{'code': target['code'], 'state': ACKED}])
        oi.ack(self.path, live[0]['code'], NOW)
        self.assertEqual(oi.revoke(self.path, 'ops', target['code'], NOW),
                         {'code': target['code'], 'state': REQUESTED})
        doc = self.read_doc()
        self.assertEqual([item['state'] for item in doc['entries']].count(
            REQUESTED), 1)
        self.assertEqual(oi.status(self.path, 'ops', NOW, code=target['code']),
                         [{'code': target['code'], 'state': REQUESTED}])

    def test_expired_live_entries_make_room_for_the_request(self):
        target = self.add(update_id=1, now=NOW)
        oi.ack(self.path, target['code'], NOW)
        self.fill(20)
        later = NOW + DAY
        self.assertEqual(oi.revoke(self.path, 'ops', target['code'], later),
                         {'code': target['code'], 'state': REQUESTED})
        states = [item['state'] for item in self.read_doc()['entries']]
        self.assertEqual(states.count(REQUESTED), 1)
        self.assertEqual(states.count(EXPIRED), 20)

    def test_lowering_revokes_work_at_cap_and_repeat_stays_inert(self):
        entries = [self.add(update_id=number + 1, now=NOW)
                   for number in range(20)]
        self.assertEqual(oi.revoke(self.path, 'ops', entries[0]['code'], NOW),
                         {'code': entries[0]['code'], 'state': REVOKED})
        self.add(update_id=21, now=NOW)
        oi.mark_seen(self.path, entries[1]['code'], NOW)
        self.assertEqual(oi.revoke(self.path, 'ops', entries[1]['code'], NOW),
                         {'code': entries[1]['code'], 'state': REVOKED})
        self.add(update_id=22, now=NOW)
        oi.ack(self.path, entries[2]['code'], NOW)
        self.assertEqual(oi.revoke(self.path, 'ops', entries[2]['code'], NOW),
                         {'code': entries[2]['code'], 'state': REQUESTED})
        self.assertEqual(
            self.assertNoWrite(oi.revoke, self.path, 'ops',
                               entries[2]['code'], NOW),
            {'code': entries[2]['code'], 'state': REQUESTED})
        states = [item['state'] for item in self.read_doc()['entries']]
        self.assertEqual(states.count(REQUESTED), 1)


class ReadTests(InboxTestCase):

    def setUp(self):
        super().setUp()
        self.init()

    def test_peek_lists_live_entries_oldest_first(self):
        first = self.add(update_id=1, text='alpha text', now=NOW)
        second = self.add(update_id=2, text='beta text', now=NOW + 1)
        third = self.add(update_id=3, text='gamma text', now=NOW + 2)
        fourth = self.add(alias='other', update_id=4, text='delta text',
                          now=NOW + 3)
        oi.mark_seen(self.path, second['code'], NOW + 3)
        oi.ack(self.path, fourth['code'], NOW + 3)
        oi.ack(self.path, third['code'], NOW + 3)
        oi.revoke(self.path, 'ops', third['code'], NOW + 3)
        self.assertEqual(oi.peek(self.path, NOW + 3), [
            {'code': first['code'], 'alias': 'ops', 'received_at': NOW,
             'state': QUEUED, 'text': 'alpha text'},
            {'code': second['code'], 'alias': 'ops', 'received_at': NOW + 1,
             'state': SEEN, 'text': 'beta text'},
            {'code': third['code'], 'alias': 'ops', 'received_at': NOW + 2,
             'state': REQUESTED, 'text': ''},
        ])
        self.assertNoWrite(oi.peek, self.path, NOW + 3)

    def test_status_shows_states_without_text(self):
        first = self.add(update_id=1, now=NOW)
        second = self.add(update_id=2, now=NOW + 1)
        third = self.add(update_id=3, now=NOW + 2)
        self.assertEqual(oi.status(self.path, 'ops', NOW + 2, limit=2), [
            {'code': third['code'], 'state': QUEUED},
            {'code': second['code'], 'state': QUEUED}])
        self.assertEqual(oi.status(self.path, 'ops', NOW + 2), [
            {'code': third['code'], 'state': QUEUED},
            {'code': second['code'], 'state': QUEUED},
            {'code': first['code'], 'state': QUEUED}])
        self.assertEqual(oi.status(self.path, 'ops', NOW + 2,
                                   code=first['code']),
                         [{'code': first['code'], 'state': QUEUED}])
        for item in oi.status(self.path, 'ops', NOW + 2):
            self.assertEqual(set(item), {'code', 'state'})
        self.assertNoWrite(oi.status, self.path, 'ops', NOW + 2)
        self.assertCode(NOT_FOUND, oi.status, self.path, 'ops', NOW + 2,
                        'f' * 12)
        self.assertEqual(oi.status(self.path, 'missing', NOW + 2), [])


class DeferredStoreTests(InboxTestCase):
    """#1160 deferred items: expired peek, tombstone pruning, COMPACTED."""

    def setUp(self):
        super().setUp()
        self.init()

    def seed_tombstones(self, count):
        ids = [ident('ops', number) for number in range(1, count + 1)]
        self.write_raw(document([], ids))
        return ids

    def test_peek_include_expired_true_and_false(self):
        first = self.add(update_id=1, now=NOW)
        old = self.add(update_id=2, now=NOW - DAY)
        self.assertEqual([item['code'] for item in oi.peek(self.path, NOW)],
                         [first['code']])
        self.assertEqual(oi.peek(self.path, NOW, include_expired=True), [
            {'code': old['code'], 'alias': 'ops',
             'received_at': NOW - DAY, 'state': EXPIRED, 'text': TEXT},
            {'code': first['code'], 'alias': 'ops', 'received_at': NOW,
             'state': QUEUED, 'text': TEXT}])
        self.assertNoWrite(oi.peek, self.path, NOW, include_expired=True)

    def test_peek_include_expired_must_be_bool(self):
        for value in (1, 0, 'true', 'yes', None, [], 1.0):
            with self.subTest(value=repr(value)):
                self.assertCode(ARGUMENT_INVALID, oi.peek, self.path, NOW,
                                value)

    def test_prune_keep_zero_removes_all_tombstones(self):
        self.seed_tombstones(3)
        self.assertEqual(oi.prune_tombstones(self.path, 0),
                         {'removed': 3, 'kept': 0})
        self.assertEqual(self.read_doc()['tombstones'], [])

    def test_prune_partial_keeps_the_newest_tombstones(self):
        ids = self.seed_tombstones(3)
        self.assertEqual(oi.prune_tombstones(self.path, 2),
                         {'removed': 1, 'kept': 2})
        self.assertEqual(self.read_doc()['tombstones'], ids[-2:])

    def test_prune_above_the_count_writes_nothing(self):
        ids = self.seed_tombstones(3)
        result = self.assertNoWrite(oi.prune_tombstones, self.path, 5)
        self.assertEqual(result, {'removed': 0, 'kept': 3})
        self.assertEqual(self.read_doc()['tombstones'], ids)

    def test_prune_keep_must_be_a_bounded_int(self):
        self.seed_tombstones(1)
        for value in (-1, oi.MAX_TOMBSTONES + 1, True, False, 1.0, '2', None):
            with self.subTest(value=repr(value)):
                self.assertNoWrite(self.assertCode, ARGUMENT_INVALID,
                                   oi.prune_tombstones, self.path, value)

    def test_prune_leaves_entries_untouched(self):
        first = self.add(update_id=11, now=NOW)
        self.seed_tombstones(2)
        data = self.read_doc()
        data['entries'] = [entry(update_id=11, received_at=NOW)]
        self.write_raw(json.dumps(data).encode('utf-8'))
        self.assertEqual(oi.prune_tombstones(self.path, 1),
                         {'removed': 1, 'kept': 1})
        self.assertEqual(self.read_doc()['entries'],
                         [entry(update_id=11, received_at=NOW)])
        self.assertEqual(oi.peek(self.path, NOW), [
            {'code': first['code'], 'alias': 'ops', 'received_at': NOW,
             'state': QUEUED, 'text': TEXT}])

    def test_resend_of_compacted_id_reports_compact_until_pruned(self):
        with mock.patch.object(oi, 'MAX_ENTRIES', 1):
            first = self.add(update_id=1)
            oi.ack(self.path, first['code'], NOW)
            self.add(update_id=2, now=NOW + 1)
        result = self.assertNoWrite(self.add, update_id=1, now=NOW + 2)
        self.assertEqual(result, {'code': first['code'],
                                  'state': COMPACTED, 'created': False})
        self.assertEqual(oi.prune_tombstones(self.path, 0),
                         {'removed': 1, 'kept': 0})
        # Once pruned the id is unknown again, so only the Telegram cursor
        # offset and the 120 s window keep the resend out.
        self.assertTrue(self.add(update_id=1, now=NOW + 3)['created'])


class ChangedFileTests(InboxTestCase):

    def setUp(self):
        super().setUp()
        self.init()
        self.add()
        self.external = document([dict(entry(), state=ACKED)])

    def test_replaced_file_between_load_and_write(self):
        with self.patched_load(lambda: self.replace_externally(self.external)):
            self.assertCode(CHANGED, oi.add, self.path, 'ops', 2, TEXT, NOW)
        self.assertEqual(self.read_doc(), json.loads(self.external.decode()))
        self.assertOnlyStateFile()

    def test_rewritten_in_place_between_load_and_write(self):
        def rewrite():
            descriptor = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(self.external)
        with self.patched_load(rewrite):
            self.assertCode(CHANGED, oi.set_enabled, self.path, False)
        self.assertEqual(self.read_doc(), json.loads(self.external.decode()))

    def test_removed_file_between_load_and_write(self):
        with self.patched_load(lambda: os.unlink(self.path)):
            self.assertCode(CHANGED, oi.mark_seen, self.path, code(), NOW)
        self.assertEqual(os.listdir(self.dir), [])


class ErrorHygieneTests(InboxTestCase):

    def test_error_codes_are_exactly_the_fixed_set(self):
        expected = frozenset({
            MISSING, EXISTS, CORRUPT, CHANGED, DIR_UNSAFE, WRITE_FAILED,
            ARGUMENT_INVALID, DISABLED, FULL, HISTORY_LIMIT, NOT_FOUND,
            NOT_ACKABLE})
        self.assertEqual(oi.ERROR_CODES, expected)
        for value in expected:
            error = oi.OwnerInboxError(value)
            self.assertEqual((str(error), error.code, error.args),
                             (value, value, (value,)))
            self.assertIsInstance(error, LLMToolError)

    def test_errors_never_carry_a_path_or_input_value(self):
        self.init()
        error = self.assertCode(ARGUMENT_INVALID, oi.add, self.path, 'Ops',
                                1, 'input-value-xyz', NOW)
        self.assertNotIn(self.dir, str(error))
        self.assertNotIn('Ops', str(error))
        self.assertNotIn('input-value-xyz', str(error))
        self.write_raw(document(extra_field='input-value-xyz'))
        error = self.assertCode(CORRUPT, oi.peek, self.path, NOW)
        self.assertNotIn(self.dir, str(error))
        self.assertNotIn('input-value-xyz', str(error))


if __name__ == '__main__':
    unittest.main()
