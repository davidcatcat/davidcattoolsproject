"""Tests for the private Telegram cursor state file (#1160 WP6).

Standard library only; every test works in its own temporary directory and
uses no network.
"""

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from llmtool import telegram_cursor as tc  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402


MISSING = 'TELEGRAM_STATE_MISSING'
EXISTS = 'TELEGRAM_STATE_EXISTS'
CORRUPT = 'TELEGRAM_STATE_CORRUPT'
CHANGED = 'TELEGRAM_STATE_CHANGED'
DIR_UNSAFE = 'TELEGRAM_STATE_DIR_UNSAFE'
ALIAS_INVALID = 'TELEGRAM_CHAT_ALIAS_INVALID'
CHAT_LIMIT = 'TELEGRAM_CHAT_LIMIT'
ARGUMENT_INVALID = 'TELEGRAM_ARGUMENT_INVALID'

OLD_NS = 1_000_000_000


def key(number):
    return hashlib.sha256(('key-%d' % number).encode('ascii')).hexdigest()


def document(chats=None, **extra):
    data = {'schema_version': 1, 'chats': {} if chats is None else chats}
    data.update(extra)
    return json.dumps(data).encode('utf-8')


def chat(update_offset=0, claimed=()):
    return {'update_offset': update_offset, 'claimed': list(claimed)}


class CursorTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.realpath(self._tmp.name)
        os.chmod(self.dir, 0o700)
        self.addCleanup(os.chmod, self.dir, 0o700)
        self.path = os.path.join(self.dir, 'cursor.json')

    def assertCode(self, code, func, *args):
        with self.assertRaises(tc.TelegramCursorError) as caught:
            func(*args)
        error = caught.exception
        self.assertIsInstance(error, LLMToolError)
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), code)
        self.assertEqual(error.args, (code,))
        return error

    def write_raw(self, data, mode=0o600):
        if os.path.lexists(self.path):
            os.unlink(self.path)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.chmod(self.path, mode)

    def read_doc(self):
        with open(self.path, 'rb') as stream:
            return json.loads(stream.read().decode('utf-8'))

    def age(self):
        """Give the file an old mtime so any rewrite is visible."""
        os.utime(self.path, ns=(OLD_NS, OLD_NS), follow_symlinks=False)

    def snapshot(self):
        info = os.stat(self.path, follow_symlinks=False)
        with open(self.path, 'rb') as stream:
            data = stream.read()
        return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size,
                stat.S_IMODE(info.st_mode), data)

    def assertOnlyStateFile(self):
        self.assertEqual(sorted(os.listdir(self.dir)), ['cursor.json'])

    def patched_load(self, after_load):
        original = tc._load

        def wrapper(path):
            result = original(path)
            after_load()
            return result
        return mock.patch.object(tc, '_load', wrapper)

    def replace_externally(self, data):
        temp = os.path.join(self.dir, 'external.tmp')
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)


class InitTests(CursorTestCase):

    def test_init_creates_private_empty_state(self):
        previous = os.umask(0)
        try:
            self.assertIsNone(tc.init_state(self.path))
        finally:
            os.umask(previous)
        info = os.stat(self.path, follow_symlinks=False)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(info.st_nlink, 1)
        self.assertEqual(self.read_doc(), {'schema_version': 1, 'chats': {}})
        self.assertEqual(tc.offset(self.path, 'ops'), 0)
        self.assertOnlyStateFile()

    def test_init_accepts_path_object(self):
        tc.init_state(Path(self.path))
        self.assertEqual(tc.offset(Path(self.path), 'ops'), 0)

    def test_init_refuses_to_overwrite(self):
        tc.init_state(self.path)
        tc.claim(self.path, 'ops', [key(1)])
        tc.advance_offset(self.path, 'ops', 9)
        before = self.snapshot()
        self.assertCode(EXISTS, tc.init_state, self.path)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(tc.offset(self.path, 'ops'), 9)
        self.assertEqual(tc.claim(self.path, 'ops', [key(1)]), [])
        self.assertOnlyStateFile()

    def test_init_refuses_existing_corrupt_file_without_repair(self):
        self.write_raw(b'{not json')
        self.assertCode(EXISTS, tc.init_state, self.path)
        with open(self.path, 'rb') as stream:
            self.assertEqual(stream.read(), b'{not json')

    def test_init_refuses_symlink_and_directory_at_path(self):
        target = os.path.join(self.dir, 'target.json')
        os.symlink(target, self.path)
        self.assertCode(EXISTS, tc.init_state, self.path)
        self.assertFalse(os.path.lexists(target))
        os.unlink(self.path)
        os.mkdir(self.path, 0o700)
        self.assertCode(EXISTS, tc.init_state, self.path)

    def test_init_refuses_group_or_other_writable_directory(self):
        for mode in (0o770, 0o720, 0o707, 0o702, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.dir, mode)
                self.assertCode(DIR_UNSAFE, tc.init_state, self.path)
                self.assertFalse(os.path.lexists(self.path))
        os.chmod(self.dir, 0o700)
        self.assertEqual(os.listdir(self.dir), [])

    def test_init_accepts_group_readable_directory(self):
        os.chmod(self.dir, 0o750)
        tc.init_state(self.path)
        self.assertEqual(tc.offset(self.path, 'ops'), 0)

    def test_init_refuses_missing_or_symlinked_directory(self):
        missing = os.path.join(self.dir, 'absent', 'cursor.json')
        self.assertCode(DIR_UNSAFE, tc.init_state, missing)
        real = os.path.join(self.dir, 'real')
        os.mkdir(real, 0o700)
        link = os.path.join(self.dir, 'link')
        os.symlink(real, link)
        self.assertCode(DIR_UNSAFE, tc.init_state, os.path.join(link, 'cursor.json'))
        self.assertEqual(os.listdir(real), [])

    def test_init_refuses_directory_owned_by_someone_else(self):
        other = os.getuid() + 1
        with mock.patch.object(tc.os, 'getuid', return_value=other):
            self.assertCode(DIR_UNSAFE, tc.init_state, self.path)
        self.assertFalse(os.path.lexists(self.path))

    def test_paths_must_be_absolute_and_normalized(self):
        bad_paths = [
            'cursor.json',
            '',
            self.dir + '/sub/../cursor.json',
            self.dir + '//cursor.json',
            self.dir + '/./cursor.json',
            self.path + '/',
            self.path + '\x00',
            '/',
            self.path.encode('utf-8'),
            None,
            42,
        ]
        for bad in bad_paths:
            with self.subTest(path=repr(bad)):
                self.assertCode(ARGUMENT_INVALID, tc.init_state, bad)
                self.assertCode(ARGUMENT_INVALID, tc.offset, bad, 'ops')
                self.assertCode(ARGUMENT_INVALID, tc.claim, bad, 'ops', [key(1)])
                self.assertCode(ARGUMENT_INVALID, tc.release, bad, 'ops', [key(1)])
                self.assertCode(ARGUMENT_INVALID, tc.advance_offset, bad, 'ops', 1)
        self.assertEqual(os.listdir(self.dir), [])


class MissingTests(CursorTestCase):

    def test_missing_file_fails_closed_and_is_never_created(self):
        self.assertCode(MISSING, tc.offset, self.path, 'ops')
        self.assertCode(MISSING, tc.claim, self.path, 'ops', [key(1)])
        self.assertCode(MISSING, tc.release, self.path, 'ops', [key(1)])
        self.assertCode(MISSING, tc.advance_offset, self.path, 'ops', 5)
        self.assertEqual(os.listdir(self.dir), [])

    def test_missing_directory_is_missing_for_readers(self):
        path = os.path.join(self.dir, 'absent', 'cursor.json')
        self.assertCode(MISSING, tc.offset, path, 'ops')
        self.assertCode(MISSING, tc.claim, path, 'ops', [key(1)])
        self.assertEqual(os.listdir(self.dir), [])


class CorruptTests(CursorTestCase):

    def assertCorruptAndUntouched(self):
        before = self.snapshot()
        self.assertCode(CORRUPT, tc.offset, self.path, 'ops')
        self.assertCode(CORRUPT, tc.claim, self.path, 'ops', [key(1)])
        self.assertCode(CORRUPT, tc.release, self.path, 'ops', [key(1)])
        self.assertCode(CORRUPT, tc.advance_offset, self.path, 'ops', 5)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_contents_are_corrupt(self):
        many_aliases = {('a%d' % index): chat() for index in range(9)}
        cases = {
            'empty': b'',
            'invalid_json': b'{"schema_version": 1, "chats": {',
            'trailing_garbage': document() + b' x',
            'not_utf8': b'\xff\xfe{}',
            'utf8_bom': b'\xef\xbb\xbf' + document(),
            'top_list': b'[]',
            'top_null': b'null',
            'duplicate_top_key': b'{"schema_version":1,"schema_version":1,"chats":{}}',
            'duplicate_alias': (b'{"schema_version":1,"chats":{"ops":'
                                b'{"update_offset":0,"claimed":[]},"ops":'
                                b'{"update_offset":9,"claimed":[]}}}'),
            'duplicate_chat_field': (b'{"schema_version":1,"chats":{"ops":'
                                     b'{"update_offset":0,"update_offset":1,'
                                     b'"claimed":[]}}}'),
            'nan': (b'{"schema_version":1,"chats":{"ops":'
                    b'{"update_offset":NaN,"claimed":[]}}}'),
            'infinity': (b'{"schema_version":1,"chats":{"ops":'
                         b'{"update_offset":Infinity,"claimed":[]}}}'),
            'negative_infinity': (b'{"schema_version":1,"chats":{"ops":'
                                  b'{"update_offset":-Infinity,"claimed":[]}}}'),
            'unknown_top_field': document(extra_field=1),
            'unknown_chat_field': document({'ops': dict(chat(), chat_id=12345)}),
            'token_field': document({'ops': dict(chat(), bot_token='x')}),
            'missing_chats': b'{"schema_version":1}',
            'missing_chat_field': document({'ops': {'update_offset': 0}}),
            'schema_version_2': b'{"schema_version":2,"chats":{}}',
            'schema_version_0': b'{"schema_version":0,"chats":{}}',
            'schema_version_true': b'{"schema_version":true,"chats":{}}',
            'schema_version_string': b'{"schema_version":"1","chats":{}}',
            'schema_version_float': b'{"schema_version":1.0,"chats":{}}',
            'chats_list': b'{"schema_version":1,"chats":[]}',
            'bad_alias': document({'Ops': chat()}),
            'long_alias': document({'a' * 33: chat()}),
            'too_many_aliases': document(many_aliases),
            'chat_not_object': document({'ops': []}),
            'offset_negative': document({'ops': chat(-1)}),
            'offset_too_large': document({'ops': chat(2 ** 63)}),
            'offset_bool': document({'ops': chat(True)}),
            'offset_float': document({'ops': chat(1.5)}),
            'offset_string': document({'ops': chat('1')}),
            'claimed_not_list': document({'ops': {'update_offset': 0, 'claimed': {}}}),
            'claimed_bad_key': document({'ops': chat(0, ['ABC'])}),
            'claimed_upper_key': document({'ops': chat(0, [key(1).upper()])}),
            'claimed_non_string': document({'ops': chat(0, [[key(1)]])}),
            'claimed_duplicate': document({'ops': chat(0, [key(1), key(1)])}),
            'claimed_too_many': document({'ops': chat(0, [key(n) for n in range(1001)])}),
            'deep_nesting': b'[' * 200000 + b']' * 200000,
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.write_raw(data)
                self.assertCorruptAndUntouched()

    def test_size_limit_is_one_mebibyte(self):
        base = document({'ops': chat(4)})
        self.write_raw(base + b' ' * (tc.MAX_STATE_BYTES - len(base)))
        self.assertEqual(os.stat(self.path).st_size, tc.MAX_STATE_BYTES)
        self.assertEqual(tc.offset(self.path, 'ops'), 4)
        self.write_raw(base + b' ' * (tc.MAX_STATE_BYTES + 1 - len(base)))
        self.assertCorruptAndUntouched()

    def test_symlink_is_corrupt(self):
        target = os.path.join(self.dir, 'target.json')
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(document())
        os.symlink(target, self.path)
        self.assertCode(CORRUPT, tc.offset, self.path, 'ops')
        self.assertCode(CORRUPT, tc.claim, self.path, 'ops', [key(1)])
        self.assertTrue(os.path.islink(self.path))
        with open(target, 'rb') as stream:
            self.assertEqual(stream.read(), document())

    def test_hard_link_is_corrupt(self):
        tc.init_state(self.path)
        os.link(self.path, os.path.join(self.dir, 'second.json'))
        self.assertCorruptAndUntouched()

    def test_wrong_mode_is_corrupt(self):
        for mode in (0o644, 0o640, 0o604, 0o400, 0o700, 0o660):
            with self.subTest(mode=oct(mode)):
                self.write_raw(document(), mode)
                self.assertCorruptAndUntouched()

    def test_wrong_owner_is_corrupt(self):
        tc.init_state(self.path)
        before = self.snapshot()
        other = os.getuid() + 1
        with mock.patch.object(tc, '_check_dir_info'), \
                mock.patch.object(tc.os, 'getuid', return_value=other):
            self.assertCode(CORRUPT, tc.offset, self.path, 'ops')
            self.assertCode(CORRUPT, tc.claim, self.path, 'ops', [key(1)])
        self.assertEqual(self.snapshot(), before)

    def test_directory_and_fifo_are_corrupt(self):
        os.mkdir(self.path, 0o700)
        self.assertCode(CORRUPT, tc.offset, self.path, 'ops')
        self.assertCode(CORRUPT, tc.claim, self.path, 'ops', [key(1)])
        os.rmdir(self.path)
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.path, 0o600)
            self.assertCode(CORRUPT, tc.offset, self.path, 'ops')
            self.assertCode(CORRUPT, tc.claim, self.path, 'ops', [key(1)])
            self.assertTrue(stat.S_ISFIFO(os.stat(self.path, follow_symlinks=False).st_mode))

    def test_unsafe_directory_blocks_load_and_write(self):
        tc.init_state(self.path)
        tc.advance_offset(self.path, 'ops', 3)
        before = self.snapshot()
        os.chmod(self.dir, 0o770)
        self.assertCode(DIR_UNSAFE, tc.offset, self.path, 'ops')
        self.assertCode(DIR_UNSAFE, tc.claim, self.path, 'ops', [key(1)])
        self.assertCode(DIR_UNSAFE, tc.advance_offset, self.path, 'ops', 4)
        os.chmod(self.dir, 0o700)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(tc.offset(self.path, 'ops'), 3)


class ArgumentTests(CursorTestCase):

    def setUp(self):
        super().setUp()
        tc.init_state(self.path)
        self.age()
        self.before = self.snapshot()

    def tearDown(self):
        self.assertEqual(self.snapshot(), self.before)
        self.assertOnlyStateFile()

    def test_alias_pattern(self):
        for alias in ('a', 'ops', 'a1', 'a_b-c', 'z' + 'a' * 31):
            with self.subTest(alias=alias):
                self.assertEqual(tc.offset(self.path, alias), 0)
        bad_aliases = ['', 'A', 'Ops', '1a', '_a', '-a', 'a' * 33, 'a.b', 'a b',
                       'a\n', 'é', 'a/b', b'ops', None, 1]
        for alias in bad_aliases:
            with self.subTest(alias=repr(alias)):
                self.assertCode(ALIAS_INVALID, tc.offset, self.path, alias)
                self.assertCode(ALIAS_INVALID, tc.claim, self.path, alias, [key(1)])
                self.assertCode(ALIAS_INVALID, tc.release, self.path, alias, [key(1)])
                self.assertCode(ALIAS_INVALID, tc.advance_offset, self.path, alias, 1)

    def test_keys_must_be_one_to_hundred_lowercase_hex64(self):
        bad_keys = [
            [],
            [key(n) for n in range(101)],
            [key(1).upper()],
            [key(1)[:63]],
            [key(1) + '0'],
            ['g' * 64],
            [key(1), 'nope'],
            [key(1).encode('ascii')],
            [None],
            key(1),
            set([key(1)]),
            None,
            iter([key(1)]),
        ]
        for keys in bad_keys:
            with self.subTest(keys=repr(keys)[:40]):
                self.assertCode(ARGUMENT_INVALID, tc.claim, self.path, 'ops', keys)
                self.assertCode(ARGUMENT_INVALID, tc.release, self.path, 'ops', keys)

    def test_update_id_range_and_type(self):
        for value in (-1, 2 ** 63, True, False, 1.0, '5', None):
            with self.subTest(value=repr(value)):
                self.assertCode(ARGUMENT_INVALID, tc.advance_offset, self.path, 'ops', value)


class ClaimTests(CursorTestCase):

    def setUp(self):
        super().setUp()
        tc.init_state(self.path)

    def test_claim_returns_new_keys_in_input_order(self):
        keys = [key(3), key(1), key(2)]
        self.assertEqual(tc.claim(self.path, 'ops', keys), keys)
        self.assertEqual(self.read_doc(), {
            'schema_version': 1,
            'chats': {'ops': {'update_offset': 0, 'claimed': keys}},
        })
        self.assertEqual(tc.claim(self.path, 'ops', (key(4), key(1), key(5), key(3))),
                         [key(4), key(5)])
        self.assertEqual(self.read_doc()['chats']['ops']['claimed'],
                         keys + [key(4), key(5)])

    def test_duplicate_input_keys_are_returned_once(self):
        self.assertEqual(tc.claim(self.path, 'ops', [key(1), key(2), key(1)]),
                         [key(1), key(2)])
        self.assertEqual(self.read_doc()['chats']['ops']['claimed'], [key(1), key(2)])

    def test_claim_accepts_one_hundred_keys_and_does_not_mutate_input(self):
        keys = [key(n) for n in range(100)]
        original = list(keys)
        self.assertEqual(tc.claim(self.path, 'ops', keys), original)
        self.assertEqual(keys, original)

    def test_reclaim_writes_nothing(self):
        tc.claim(self.path, 'ops', [key(1), key(2)])
        self.age()
        before = self.snapshot()
        self.assertEqual(tc.claim(self.path, 'ops', [key(2), key(1)]), [])
        self.assertEqual(tc.claim(self.path, 'ops', [key(1)]), [])
        self.assertEqual(self.snapshot(), before)
        self.assertOnlyStateFile()

    def test_new_claim_replaces_file_atomically_with_private_mode(self):
        before = os.stat(self.path)
        previous = os.umask(0)
        try:
            tc.claim(self.path, 'ops', [key(1)])
        finally:
            os.umask(previous)
        after = os.stat(self.path, follow_symlinks=False)
        self.assertNotEqual(after.st_ino, before.st_ino)
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)
        self.assertEqual(after.st_nlink, 1)
        self.assertOnlyStateFile()

    def test_aliases_are_independent(self):
        self.assertEqual(tc.claim(self.path, 'ops', [key(1)]), [key(1)])
        self.assertEqual(tc.claim(self.path, 'alerts', [key(1)]), [key(1)])
        tc.advance_offset(self.path, 'ops', 10)
        self.assertEqual(tc.offset(self.path, 'alerts'), 0)
        self.assertEqual(tc.claim(self.path, 'alerts', [key(1)]), [])

    def test_alias_limit_is_eight(self):
        for index in range(8):
            tc.claim(self.path, 'chat%d' % index, [key(index)])
        self.age()
        before = self.snapshot()
        self.assertCode(CHAT_LIMIT, tc.claim, self.path, 'ninth', [key(99)])
        self.assertCode(CHAT_LIMIT, tc.advance_offset, self.path, 'ninth', 5)
        self.assertEqual(tc.advance_offset(self.path, 'ninth', 0), 0)
        self.assertEqual(tc.offset(self.path, 'ninth'), 0)
        self.assertEqual(tc.release(self.path, 'ninth', [key(1)]), [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(tc.claim(self.path, 'chat0', [key(50)]), [key(50)])
        self.assertEqual(len(self.read_doc()['chats']), 8)

    def test_each_alias_keeps_at_most_1000_keys_oldest_dropped_first(self):
        for batch in range(10):
            keys = [key(n) for n in range(batch * 100, batch * 100 + 100)]
            self.assertEqual(tc.claim(self.path, 'ops', keys), keys)
        claimed = self.read_doc()['chats']['ops']['claimed']
        self.assertEqual(claimed, [key(n) for n in range(1000)])
        self.assertEqual(tc.claim(self.path, 'ops', [key(1000)]), [key(1000)])
        claimed = self.read_doc()['chats']['ops']['claimed']
        self.assertEqual(len(claimed), 1000)
        self.assertEqual(claimed[0], key(1))
        self.assertEqual(claimed[-1], key(1000))
        self.assertNotIn(key(0), claimed)
        self.assertEqual(tc.claim(self.path, 'ops', [key(500)]), [])
        newer = [key(n) for n in range(2000, 2050)]
        self.assertEqual(tc.claim(self.path, 'ops', newer), newer)
        claimed = self.read_doc()['chats']['ops']['claimed']
        self.assertEqual(len(claimed), 1000)
        self.assertEqual(claimed[0], key(51))
        self.assertEqual(claimed[-50:], newer)
        self.assertEqual(tc.claim(self.path, 'ops', [key(0)]), [key(0)])

    def test_state_file_holds_only_offset_and_keys(self):
        tc.claim(self.path, 'ops', [key(1)])
        tc.advance_offset(self.path, 'ops', 77)
        data = self.read_doc()
        self.assertEqual(set(data), {'schema_version', 'chats'})
        for entry in data['chats'].values():
            self.assertEqual(set(entry), {'update_offset', 'claimed'})


class ReleaseTests(CursorTestCase):

    def setUp(self):
        super().setUp()
        tc.init_state(self.path)
        tc.claim(self.path, 'ops', [key(1), key(2), key(3)])

    def test_release_removes_only_present_keys(self):
        self.assertEqual(tc.release(self.path, 'ops', [key(9), key(2), key(2)]), [key(2)])
        self.assertEqual(self.read_doc()['chats']['ops']['claimed'], [key(1), key(3)])
        self.assertEqual(tc.claim(self.path, 'ops', [key(2)]), [key(2)])
        self.assertEqual(self.read_doc()['chats']['ops']['claimed'],
                         [key(1), key(3), key(2)])

    def test_release_keeps_offset(self):
        tc.advance_offset(self.path, 'ops', 12)
        tc.release(self.path, 'ops', [key(1), key(2), key(3)])
        self.assertEqual(tc.offset(self.path, 'ops'), 12)
        self.assertEqual(self.read_doc()['chats']['ops']['claimed'], [])

    def test_inert_release_writes_nothing(self):
        self.age()
        before = self.snapshot()
        self.assertEqual(tc.release(self.path, 'ops', [key(7), key(8)]), [])
        self.assertEqual(tc.release(self.path, 'other', [key(1)]), [])
        self.assertEqual(self.snapshot(), before)
        self.assertOnlyStateFile()


class OffsetTests(CursorTestCase):

    def setUp(self):
        super().setUp()
        tc.init_state(self.path)

    def test_offset_is_monotonic(self):
        self.assertEqual(tc.offset(self.path, 'ops'), 0)
        self.assertEqual(tc.advance_offset(self.path, 'ops', 5), 5)
        self.assertEqual(tc.offset(self.path, 'ops'), 5)
        self.age()
        before = self.snapshot()
        self.assertEqual(tc.advance_offset(self.path, 'ops', 5), 5)
        self.assertEqual(tc.advance_offset(self.path, 'ops', 3), 5)
        self.assertEqual(tc.advance_offset(self.path, 'ops', 0), 5)
        self.assertEqual(tc.offset(self.path, 'ops'), 5)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(tc.advance_offset(self.path, 'ops', 2 ** 63 - 1), 2 ** 63 - 1)
        self.assertEqual(tc.offset(self.path, 'ops'), 2 ** 63 - 1)

    def test_offset_keeps_claimed_keys(self):
        tc.claim(self.path, 'ops', [key(1)])
        tc.advance_offset(self.path, 'ops', 8)
        self.assertEqual(self.read_doc()['chats']['ops'],
                         {'update_offset': 8, 'claimed': [key(1)]})

    def test_reads_and_unseen_aliases_write_nothing(self):
        self.age()
        before = self.snapshot()
        self.assertEqual(tc.offset(self.path, 'unseen'), 0)
        self.assertEqual(tc.advance_offset(self.path, 'unseen', 0), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.read_doc()['chats'], {})


class ChangedFileTests(CursorTestCase):

    def setUp(self):
        super().setUp()
        tc.init_state(self.path)
        self.external = document({'ops': chat(7, [key(40)])})

    def assertExternalStateKept(self):
        self.assertEqual(self.read_doc(), json.loads(self.external.decode('utf-8')))
        self.assertEqual(tc.offset(self.path, 'ops'), 7)
        self.assertOnlyStateFile()

    def test_replaced_file_between_load_and_write(self):
        with self.patched_load(lambda: self.replace_externally(self.external)):
            self.assertCode(CHANGED, tc.claim, self.path, 'ops', [key(1)])
        self.assertExternalStateKept()
        self.assertEqual(tc.claim(self.path, 'ops', [key(1), key(40)]), [key(1)])

    def test_rewritten_in_place_between_load_and_write(self):
        def rewrite():
            descriptor = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(self.external)
        with self.patched_load(rewrite):
            self.assertCode(CHANGED, tc.advance_offset, self.path, 'ops', 50)
        self.assertExternalStateKept()

    def test_mtime_only_change_between_load_and_write(self):
        def touch():
            info = os.stat(self.path)
            os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns + 10 ** 9))
        before = self.read_doc()
        with self.patched_load(touch):
            self.assertCode(CHANGED, tc.claim, self.path, 'ops', [key(1)])
        self.assertEqual(self.read_doc(), before)
        self.assertOnlyStateFile()

    def test_removed_file_between_load_and_write(self):
        with self.patched_load(lambda: os.unlink(self.path)):
            self.assertCode(CHANGED, tc.claim, self.path, 'ops', [key(1)])
        self.assertEqual(os.listdir(self.dir), [])

    def test_release_detects_change(self):
        tc.claim(self.path, 'ops', [key(40)])
        with self.patched_load(lambda: self.replace_externally(self.external)):
            self.assertCode(CHANGED, tc.release, self.path, 'ops', [key(40)])
        self.assertExternalStateKept()

    def test_other_lock_on_state_file_inode_does_not_matter(self):
        descriptor = os.open(self.path, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(tc.claim(self.path, 'ops', [key(1)]), [key(1)])
        self.assertOnlyStateFile()


_WAITER = (
    'import json, sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from llmtool import telegram_cursor as tc\n'
    'path, keys = sys.argv[2], sys.argv[3:]\n'
    'sys.stdout.write("ready\\n")\n'
    'sys.stdout.flush()\n'
    'result = {"new": tc.claim(path, "ops", keys), "offset": tc.offset(path, "ops")}\n'
    'sys.stdout.write(json.dumps(result) + "\\n")\n'
)


class WriterLockTests(CursorTestCase):
    """Writers serialise on one flock of the state directory (PR #1165 review)."""

    def setUp(self):
        super().setUp()
        tc.init_state(self.path)

    def try_lock(self):
        descriptor = os.open(self.dir, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            os.close(descriptor)
        return True

    def assertDirLocked(self):
        self.assertFalse(self.try_lock())

    def assertDirUnlocked(self):
        self.assertTrue(self.try_lock())

    def contend(self, first, second):
        """Run ``first``; once it has loaded, start ``second`` in a thread.

        ``first`` pauses after its load, while it holds the writer lock, until
        ``second`` has reached the lock and is still waiting on it. Returns
        both results.
        """
        original_lock = tc._lock_exclusive
        original_load = tc._load
        reached = threading.Event()
        outcome = {}

        def run_second():
            try:
                outcome['second'] = second()
            except BaseException as error:  # re-raised in the test thread
                outcome['error'] = error

        worker = threading.Thread(target=run_second, daemon=True)

        def lock(descriptor):
            if threading.current_thread() is worker:
                reached.set()
            original_lock(descriptor)

        def load(path):
            result = original_load(path)
            if 'started' not in outcome:
                outcome['started'] = True
                self.assertDirLocked()
                worker.start()
                self.assertTrue(reached.wait(30))
                worker.join(0.3)
                outcome['second_waited'] = worker.is_alive()
                self.assertNotIn('second', outcome)
            return result

        with mock.patch.object(tc, '_lock_exclusive', lock), \
                mock.patch.object(tc, '_load', load):
            try:
                first_result = first()
            finally:
                if 'started' in outcome:
                    worker.join(30)
        self.assertIn('started', outcome)
        self.assertFalse(worker.is_alive())
        if 'error' in outcome:
            raise outcome['error']
        self.assertTrue(outcome['second_waited'])
        self.assertDirUnlocked()
        self.assertOnlyStateFile()
        return first_result, outcome['second']

    def test_second_claim_waits_and_sees_first_claim(self):
        first, second = self.contend(
            lambda: tc.claim(self.path, 'ops', [key(1)]),
            lambda: tc.claim(self.path, 'ops', [key(1), key(2)]))
        self.assertEqual(first, [key(1)])
        self.assertEqual(second, [key(2)])
        self.assertEqual(self.read_doc()['chats']['ops'], chat(0, [key(1), key(2)]))

    def test_second_claim_keeps_first_offset(self):
        first, second = self.contend(
            lambda: tc.advance_offset(self.path, 'ops', 10),
            lambda: tc.claim(self.path, 'ops', [key(1)]))
        self.assertEqual((first, second), (10, [key(1)]))
        self.assertEqual(self.read_doc()['chats']['ops'], chat(10, [key(1)]))

    def test_second_offset_keeps_first_keys(self):
        first, second = self.contend(
            lambda: tc.claim(self.path, 'ops', [key(1)]),
            lambda: tc.advance_offset(self.path, 'ops', 20))
        self.assertEqual((first, second), ([key(1)], 20))
        self.assertEqual(self.read_doc()['chats']['ops'], chat(20, [key(1)]))

    def test_lower_offset_waits_and_sees_first_offset(self):
        first, second = self.contend(
            lambda: tc.advance_offset(self.path, 'ops', 10),
            lambda: tc.advance_offset(self.path, 'ops', 5))
        self.assertEqual((first, second), (10, 10))
        self.assertEqual(tc.offset(self.path, 'ops'), 10)

    def test_release_waits_and_sees_first_claim(self):
        first, second = self.contend(
            lambda: tc.claim(self.path, 'ops', [key(1), key(2)]),
            lambda: tc.release(self.path, 'ops', [key(1)]))
        self.assertEqual((first, second), ([key(1), key(2)], [key(1)]))
        self.assertEqual(self.read_doc()['chats']['ops'], chat(0, [key(2)]))

    def test_lock_is_held_from_load_through_replace_and_fsync(self):
        original_load = tc._load
        original_replace = tc.os.replace
        original_fsync = tc._fsync_dir
        seen = []

        def load(path):
            self.assertDirLocked()
            seen.append('load')
            return original_load(path)

        def replace(source, target):
            self.assertDirLocked()
            seen.append('replace')
            original_replace(source, target)

        def fsync(directory):
            self.assertDirLocked()
            seen.append('fsync')
            original_fsync(directory)

        with mock.patch.object(tc, '_load', load), \
                mock.patch.object(tc.os, 'replace', replace), \
                mock.patch.object(tc, '_fsync_dir', fsync):
            tc.claim(self.path, 'ops', [key(1)])
            tc.advance_offset(self.path, 'ops', 3)
            tc.release(self.path, 'ops', [key(1)])
        self.assertEqual(seen, ['load', 'replace', 'fsync'] * 3)
        self.assertDirUnlocked()
        self.assertEqual(self.read_doc()['chats']['ops'], chat(3, []))

    def test_init_holds_the_lock(self):
        sub = os.path.join(self.dir, 'sub')
        os.mkdir(sub, 0o700)
        original_fsync = tc._fsync_dir
        seen = []

        def fsync(directory):
            descriptor = os.open(sub, os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
            seen.append(directory)
            original_fsync(directory)

        with mock.patch.object(tc, '_fsync_dir', fsync):
            tc.init_state(os.path.join(sub, 'cursor.json'))
        self.assertEqual(seen, [sub])

    def test_lock_is_released_after_inert_and_failed_calls(self):
        tc.claim(self.path, 'ops', [key(1)])
        self.assertEqual(tc.claim(self.path, 'ops', [key(1)]), [])
        self.assertDirUnlocked()
        self.assertEqual(tc.advance_offset(self.path, 'ops', 0), 0)
        self.assertDirUnlocked()
        with self.patched_load(lambda: self.replace_externally(document())):
            self.assertCode(CHANGED, tc.claim, self.path, 'ops', [key(2)])
        self.assertDirUnlocked()
        self.assertCode(EXISTS, tc.init_state, self.path)
        self.assertDirUnlocked()
        self.write_raw(b'{not json')
        self.assertCode(CORRUPT, tc.advance_offset, self.path, 'ops', 1)
        self.assertDirUnlocked()

    def test_writer_in_another_process_waits_for_the_lock(self):
        holder = os.open(self.dir, os.O_RDONLY)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        child = subprocess.Popen(
            [sys.executable, '-I', '-c', _WAITER, str(_ROOT), self.path, key(40), key(1)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline(), b'ready\n')
            time.sleep(0.3)
            self.assertIsNone(child.poll())
            # As a first writer would, the lock holder changes the state.
            self.replace_externally(document({'ops': chat(7, [key(40)])}))
            fcntl.flock(holder, fcntl.LOCK_UN)
            out, err = child.communicate(timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
        self.assertEqual(child.returncode, 0, err.decode('utf-8', 'replace'))
        self.assertEqual(json.loads(out.decode('utf-8')), {'new': [key(1)], 'offset': 7})
        self.assertEqual(self.read_doc()['chats']['ops'], chat(7, [key(40), key(1)]))
        self.assertOnlyStateFile()


_CHILD = (
    'import json, sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from llmtool import telegram_cursor as tc\n'
    'path, keys = sys.argv[2], sys.argv[3:]\n'
    'result = {"new": tc.claim(path, "ops", keys), "offset": tc.offset(path, "ops")}\n'
    'sys.stdout.write(json.dumps(result))\n'
)


class RestartTests(CursorTestCase):

    def run_child(self, *keys):
        completed = subprocess.run(
            [sys.executable, '-I', '-c', _CHILD, str(_ROOT), self.path] + list(keys),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', 'replace'))
        return json.loads(completed.stdout.decode('utf-8'))

    def test_new_process_sees_same_keys_and_offset(self):
        tc.init_state(self.path)
        self.assertEqual(tc.claim(self.path, 'ops', [key(1), key(2)]), [key(1), key(2)])
        tc.advance_offset(self.path, 'ops', 314)
        self.age()
        before = self.snapshot()
        self.assertEqual(self.run_child(key(2), key(1)), {'new': [], 'offset': 314})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.run_child(key(1), key(3)), {'new': [key(3)], 'offset': 314})
        self.assertEqual(tc.claim(self.path, 'ops', [key(3)]), [])


class ErrorHygieneTests(CursorTestCase):

    def assertClean(self, error, *values):
        texts = [str(error), repr(error)] + traceback.format_exception_only(type(error), error)
        chain = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__context__ is None or error.__suppress_context__)
        self.assertNotIn(self.dir, chain)
        for text in texts:
            self.assertNotIn(self.dir, text)
            for value in values:
                self.assertNotIn(value, text)

    def test_errors_carry_only_fixed_codes(self):
        self.assertClean(self.assertCode(MISSING, tc.offset, self.path, 'ops'))
        self.assertClean(self.assertCode(DIR_UNSAFE, tc.init_state,
                                         os.path.join(self.dir, 'absent', 'x.json')))
        tc.init_state(self.path)
        self.assertClean(self.assertCode(EXISTS, tc.init_state, self.path))
        secret_alias = 'Secret-Alias-Value'
        self.assertClean(self.assertCode(ALIAS_INVALID, tc.claim, self.path,
                                         secret_alias, [key(1)]), secret_alias)
        secret_key = 'secret-key-value'
        self.assertClean(self.assertCode(ARGUMENT_INVALID, tc.claim, self.path,
                                         'ops', [secret_key]), secret_key)
        self.assertClean(self.assertCode(ARGUMENT_INVALID, tc.advance_offset,
                                         self.path, 'ops', -98765), '98765')
        self.assertClean(self.assertCode(ARGUMENT_INVALID, tc.init_state,
                                         self.dir + '/sub/../x.json'))
        with self.patched_load(lambda: self.replace_externally(document())):
            self.assertClean(self.assertCode(CHANGED, tc.claim, self.path, 'ops', [key(1)]))
        for index in range(8):
            tc.claim(self.path, 'chat%d' % index, [key(index)])
        self.assertClean(self.assertCode(CHAT_LIMIT, tc.claim, self.path,
                                         'extra', [key(9)]), 'extra')
        self.write_raw(b'{"secret_field": "secret-content"}')
        self.assertClean(self.assertCode(CORRUPT, tc.offset, self.path, 'ops'),
                         'secret_field', 'secret-content')

    def test_error_codes_are_fixed(self):
        self.assertEqual(tc.ERROR_CODES, frozenset({
            MISSING, EXISTS, CORRUPT, CHANGED, DIR_UNSAFE, ALIAS_INVALID,
            CHAT_LIMIT, ARGUMENT_INVALID, 'TELEGRAM_STATE_WRITE_FAILED'}))


if __name__ == '__main__':
    unittest.main()
