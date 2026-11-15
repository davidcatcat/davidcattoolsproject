"""#1943 WP-5 PR1: the O6 advice ledger in llmtool.high_debug_consult.

Every test uses a temp Task store root and an injected clock; nothing touches
the default state root (the default-root test injects ``default_root``), Git,
a provider or the network. The report digest is the SHA-256 of a fixed plain
label; no test holds credential-shaped data.
"""
import dataclasses
import errno
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from llmtool import high_debug_consult as consult
from llmtool import task_store
from llmtool.errors import LLMToolError

# Wednesday of ISO week 2026-W46 (UTC); that week runs Mon 9 to Sun 15 November.
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
WEEK = '2026-W46'
REPORT = hashlib.sha256(b'advice report fixture').hexdigest()
OTHER_REPORT = hashlib.sha256(b'second advice report fixture').hexdigest()


def _race_worker(root, wp, now_text, barrier, results):
    """Top-level so every multiprocessing start method can run it."""
    try:
        barrier.wait(timeout=60)
        handle = consult.reserve(wp, now=datetime.fromisoformat(now_text), root=root)
    except LLMToolError as error:
        results.put(('REFUSED', getattr(error, 'high_debug_code', None)))
    except Exception as error:  # surfaced to the parent as a test failure
        results.put(('ERROR', type(error).__name__))
    else:
        results.put(('RESERVED', handle.week_slot))


class ConsultLedgerTest(unittest.TestCase):

    def setUp(self):
        # realpath: the ledger refuses any symlink in the root's path chain, and
        # some temp locations (macOS /var) are reached through one.
        self.base = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.base, True)
        os.chmod(self.base, 0o700)  # the state root holding the ledger
        self.root = self.base / 'tasks'  # Task store root; the ledger sits beside it
        self.root.mkdir(mode=0o700)
        os.chmod(self.root, 0o700)
        self.ledger = self.base / consult.LEDGER_DIR

    # helpers

    def refuses(self, code, func, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            func(*args, **kwargs)
        self.assertEqual(caught.exception.high_debug_code, code)
        return caught.exception

    def reserve(self, wp, now=NOW):
        return consult.reserve(wp, now=now, root=self.root)

    def load(self, name):
        return json.loads((self.ledger / name).read_text(encoding='utf-8'))

    def names(self, prefix):
        return sorted(p.name for p in self.ledger.iterdir() if p.name.startswith(prefix))

    def make_ledger(self):
        self.ledger.mkdir(mode=0o700)
        os.chmod(self.ledger, 0o700)

    def plant(self, name, data, mode=0o600):
        fd = os.open(self.ledger / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)

    def run_race(self, wps):
        ctx = multiprocessing.get_context()
        barrier = ctx.Barrier(len(wps))
        results = ctx.Queue()
        procs = [ctx.Process(target=_race_worker,
                             args=(str(self.root), wp, NOW.isoformat(), barrier, results))
                 for wp in wps]
        for proc in procs:
            proc.start()
        try:
            outcomes = [results.get(timeout=120) for _ in procs]
        finally:
            for proc in procs:
                proc.join(timeout=60)
            for proc in procs:
                if proc.is_alive():
                    proc.terminate()
                    proc.join()
        self.assertTrue(all(proc.exitcode == 0 for proc in procs))
        return outcomes

    @staticmethod
    def identity(path):
        info = os.lstat(path)
        return info.st_dev, info.st_ino

    def fsync_fault(self, matches, fail_on=None):
        """Patch os.fsync: calls whose fd ``matches(fstat)`` are recorded and may fail.

        ``calls`` collects the fstat of every matching call. ``fail_on`` is the
        set of 1-based matching calls that raise EIO; None fails every matching
        call. Every call that does not fail is the real fsync.
        """
        real_fsync = os.fsync
        calls = []

        def fsync(fd):
            info = os.fstat(fd)
            if matches(info):
                calls.append(info)
                if fail_on is None or len(calls) in fail_on:
                    raise OSError(errno.EIO, 'injected fsync failure')
            return real_fsync(fd)

        return mock.patch.object(consult.os, 'fsync', side_effect=fsync), calls

    def directory_fsync_fault(self, path, fail_on=None):
        target = self.identity(path)
        return self.fsync_fault(
            lambda info: stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == target,
            fail_on)

    # reservation

    def test_first_slot_reserves_wp_and_week_slot(self):
        handle = self.reserve('wp5-pr1')
        self.assertEqual((handle.wp, handle.week, handle.week_slot), ('wp5-pr1', WEEK, 1))
        self.assertEqual(self.names('wp-'), ['wp-wp5-pr1.json'])
        self.assertEqual(self.names('week-'), ['week-2026-W46-1.json'])
        wp_record = self.load('wp-wp5-pr1.json')
        week_record = self.load('week-2026-W46-1.json')
        self.assertEqual(set(wp_record), {'schema', 'wp', 'state', 'week', 'week_slot',
                                          'reserved_at', 'finished_at', 'report_sha256'})
        self.assertEqual(set(week_record), {'schema', 'week', 'slot', 'wp', 'state',
                                            'reserved_at', 'finished_at', 'report_sha256'})
        self.assertEqual((wp_record['state'], wp_record['week'], wp_record['week_slot']),
                         ('RESERVED', WEEK, 1))
        self.assertEqual((week_record['state'], week_record['wp'], week_record['slot']),
                         ('RESERVED', 'wp5-pr1', 1))
        self.assertEqual(wp_record['reserved_at'], NOW.isoformat())
        self.assertIsNone(wp_record['report_sha256'])
        # Private and metadata only: no path of any kind in a record.
        self.assertEqual(stat.S_IMODE(self.ledger.lstat().st_mode), 0o700)
        for name in self.names(''):
            path = self.ledger / name
            self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o600)
            self.assertEqual(path.lstat().st_nlink, 1)  # the temporary name is gone
            text = path.read_text(encoding='utf-8')
            self.assertNotIn(str(self.base), text)
            self.assertNotIn('/', text)
        # Only the two slots remain; no temp names are left behind.
        self.assertEqual(len(self.names('')), 2)

    def test_same_wp_refuses_wp_used_and_never_relaunches(self):
        handle = self.reserve('wp5')
        error = self.refuses(consult.WP_USED, self.reserve, 'wp5')
        self.assertIn('state=RESERVED', str(error))
        self.assertIn('report_sha256=none', error.high_debug_detail)
        self.assertEqual(self.names('week-'), ['week-2026-W46-1.json'])
        consult.finish(handle, consult.DONE, REPORT, now=NOW + timedelta(hours=1))
        error = self.refuses(consult.WP_USED, self.reserve, 'wp5',
                             NOW + timedelta(days=30))
        self.assertIn('state=DONE', str(error))
        self.assertIn('report_sha256=' + REPORT, str(error))
        self.assertEqual(self.names('week-'), ['week-2026-W46-1.json'])

    def test_eleventh_wp_in_one_week_refuses_week_cap_and_next_week_passes(self):
        slots = [self.reserve('wp%02d' % i, NOW + timedelta(minutes=i)).week_slot
                 for i in range(1, 11)]
        self.assertEqual(sorted(slots), list(range(1, 11)))
        error = self.refuses(consult.WEEK_CAP, self.reserve, 'wp11')
        self.assertIn('week=' + WEEK, str(error))
        # Later in the same week (Sunday, UTC) is still capped.
        self.refuses(consult.WEEK_CAP, self.reserve, 'wp11',
                     datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc))
        # A plain cap refusal takes no WP slot, so that WP stays usable.
        self.assertNotIn('wp-wp11.json', self.names('wp-'))
        handle = self.reserve('wp11', NOW + timedelta(days=7))
        self.assertEqual((handle.week, handle.week_slot), ('2026-W47', 1))
        self.assertEqual(len(self.names('week-2026-W46-')), 10)

    def test_week_is_the_iso_week_in_utc(self):
        sunday_late = datetime(2026, 11, 15, 23, 30, tzinfo=timezone.utc)
        monday_tokyo = datetime(2026, 11, 16, 0, 30, tzinfo=timezone(timedelta(hours=9)))
        monday_utc = datetime(2026, 11, 16, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(self.reserve('a', sunday_late).week, WEEK)
        self.assertEqual(self.reserve('b', monday_tokyo).week, WEEK)  # Sunday in UTC
        self.assertEqual(self.reserve('c', monday_utc).week, '2026-W47')
        self.assertEqual(self.load('wp-b.json')['reserved_at'],
                         '2026-11-15T15:30:00+00:00')

    def test_naive_or_non_datetime_now_refuses_argument_invalid(self):
        self.refuses(consult.ARGUMENT_INVALID, self.reserve, 'wp5', datetime(2026, 11, 11))
        self.refuses(consult.ARGUMENT_INVALID, self.reserve, 'wp5', '2026-11-11')
        self.assertFalse(self.ledger.exists())

    def test_unsafe_wp_refuses_argument_invalid_before_any_write(self):
        for wp in ('', 'a/b', '../up', 'a b', 'x' * 65, 'line\n', 'wp*', None, 5, b'wp5'):
            with self.subTest(wp=wp):
                self.refuses(consult.ARGUMENT_INVALID, self.reserve, wp)
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.reserve('A.b_c-9').wp, 'A.b_c-9')

    def test_taken_week_slot_moves_on_to_the_next_n(self):
        self.assertEqual(self.reserve('first').week_slot, 1)
        self.assertEqual(self.reserve('second').week_slot, 2)
        self.assertEqual(self.load('week-2026-W46-2.json')['wp'], 'second')

    def test_wp_slot_kept_when_week_fills_after_it_was_created(self):
        for i in range(1, 11):
            self.reserve('wp%02d' % i)
        # Simulate losing every week-slot race after the pre-check saw a free n.
        with mock.patch.object(consult, '_week_has_free_slot', return_value=True):
            self.refuses(consult.WEEK_CAP, self.reserve, 'late')
        record = self.load('wp-late.json')
        self.assertEqual((record['state'], record['week_slot'], record['report_sha256']),
                         ('FAILED', None, None))
        self.assertEqual(len(self.names('week-')), 10)
        # Fail closed: that WP is used for good, even in a later week.
        error = self.refuses(consult.WP_USED, self.reserve, 'late', NOW + timedelta(days=7))
        self.assertIn('state=FAILED', str(error))
        self.assertIn('week_slot=none', str(error))

    def test_same_wp_reserved_while_the_week_fills_refuses_wp_used_not_week_cap(self):
        # Formal finding F2: slots 1-9 are taken; a rival for the same WP wins
        # between this call's first WP read and its week scan, taking slot 10.
        for i in range(1, 10):
            self.reserve('wp%02d' % i)
        real_scan = consult._week_has_free_slot
        scans, rival = [], []

        def rival_wins_before_the_scan(dfd, week):
            scans.append(week)
            if len(scans) == 1:
                # The nested call is the rival; its own scan is the second one.
                rival.append(self.reserve('contested'))
            return real_scan(dfd, week)

        with mock.patch.object(consult, '_week_has_free_slot',
                               side_effect=rival_wins_before_the_scan):
            error = self.refuses(consult.WP_USED, self.reserve, 'contested')
        self.assertEqual(scans, [WEEK, WEEK])
        self.assertEqual(rival[0].week_slot, 10)
        self.assertIn('state=RESERVED', str(error))
        self.assertIn('week_slot=10', str(error))
        self.assertIn('report_sha256=none', error.high_debug_detail)
        # The loser wrote nothing: the rival's slots stand as it left them.
        record = self.load('wp-contested.json')
        self.assertEqual((record['state'], record['week_slot']), ('RESERVED', 10))
        self.assertEqual(self.load('week-2026-W46-10.json')['wp'], 'contested')
        self.assertEqual(len(self.names('week-')), 10)
        self.assertEqual(self.names('.consult-'), [])

    def test_two_processes_same_wp_exactly_one_wins(self):
        outcomes = sorted(self.run_race(['shared-wp', 'shared-wp']))
        self.assertEqual(outcomes, [('REFUSED', consult.WP_USED), ('RESERVED', 1)])
        self.assertEqual(self.names('wp-'), ['wp-shared-wp.json'])
        self.assertEqual(self.names('week-'), ['week-2026-W46-1.json'])
        self.assertEqual(self.load('wp-shared-wp.json')['week_slot'], 1)

    def test_two_processes_distinct_wps_take_distinct_week_slots(self):
        outcomes = sorted(self.run_race(['left', 'right']))
        self.assertEqual(outcomes, [('RESERVED', 1), ('RESERVED', 2)])
        owners = {self.load(name)['wp'] for name in self.names('week-')}
        self.assertEqual(owners, {'left', 'right'})

    # invalid ledger

    def test_corrupt_wp_slot_refuses_ledger_invalid(self):
        self.make_ledger()
        self.plant('wp-broken.json', b'{not json')
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'broken')
        self.assertEqual(self.names('week-'), [])

    def test_wrong_schema_slots_refuse_ledger_invalid(self):
        self.reserve('good')
        record = self.load('wp-good.json')
        cases = {
            'extra key': dict(record, note='x'),
            'other schema': dict(record, schema='high-debug-consult-wp.v0'),
            'other wp': dict(record, wp='elsewhere'),
            'unknown state': dict(record, state='LAUNCHED'),
            'done without digest': dict(record, state='DONE', finished_at=NOW.isoformat()),
            'bool slot': dict(record, week_slot=True),
            'slot out of range': dict(record, week_slot=11),
            'not a dict': [record],
        }
        for label, bad in cases.items():
            with self.subTest(label):
                wp = 'bad-' + label.replace(' ', '-')
                if isinstance(bad, dict) and bad.get('wp') == 'good':
                    bad = dict(bad, wp=wp)
                self.plant('wp-%s.json' % wp, json.dumps(bad).encode('ascii'))
                self.refuses(consult.LEDGER_INVALID, self.reserve, wp)

    def test_corrupt_week_slot_refuses_before_the_wp_slot_is_taken(self):
        self.make_ledger()
        self.plant('week-2026-W46-3.json', b'[]')
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(self.names('wp-'), [])

    def test_symlinked_slots_and_directory_refuse_ledger_invalid(self):
        self.reserve('real')
        target = self.base / 'outside.json'
        shutil.copyfile(self.ledger / 'wp-real.json', target)
        os.chmod(target, 0o600)
        os.symlink(target, self.ledger / 'wp-linked.json')
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'linked')
        os.symlink(self.ledger / 'week-2026-W46-1.json', self.ledger / 'week-2026-W46-2.json')
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'other')
        self.assertNotIn('wp-other.json', self.names('wp-'))
        # A symlinked ledger directory refuses too.
        moved = self.base / 'moved-ledger'
        os.rename(self.ledger, moved)
        os.symlink(moved, self.ledger)
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'third')

    def test_non_regular_slot_refuses_ledger_invalid(self):
        self.make_ledger()
        os.mkdir(self.ledger / 'wp-dir.json', 0o700)
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'dir')

    def test_bad_permissions_refuse_ledger_invalid(self):
        self.reserve('open')
        os.chmod(self.ledger / 'wp-open.json', 0o644)
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'open')
        os.chmod(self.ledger / 'wp-open.json', 0o600)
        os.chmod(self.ledger, 0o755)
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'another')
        self.assertNotIn('wp-another.json', self.names('wp-'))

    # hard links (security repair 1a)

    def test_hard_linked_slot_refuses_ledger_invalid(self):
        handle = self.reserve('real')
        os.link(self.ledger / 'wp-real.json', self.base / 'outside-alias.json')
        pause = mock.patch.object(consult, '_settle_pause')
        pauses = pause.start()
        self.addCleanup(pause.stop)
        # A used WP whose slot carries a planted second name is invalid, not used.
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'real')
        self.refuses(consult.LEDGER_INVALID, consult.finish, handle, consult.DONE, REPORT,
                     now=NOW)
        self.assertEqual(self.load('wp-real.json')['state'], 'RESERVED')
        # A hard-linked week slot blocks the week before any new WP slot is taken.
        os.link(self.ledger / 'week-2026-W46-1.json', self.base / 'week-alias.json')
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'other')
        self.assertNotIn('wp-other.json', self.names('wp-'))
        # A second name outside the ledger is never waited out: it refuses at once.
        pauses.assert_not_called()

    def test_lingering_in_ledger_temporary_name_refuses_after_the_bounded_wait(self):
        self.reserve('stale')
        os.link(self.ledger / 'wp-stale.json', self.ledger / '.consult-left-behind')
        with mock.patch.object(consult, '_settle_pause') as pauses:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'stale')
        self.assertEqual(pauses.call_count, consult._SETTLE_TRIES)
        self.assertEqual(self.load('wp-stale.json')['state'], 'RESERVED')

    def test_hard_linked_temporary_name_of_another_inode_does_not_excuse_a_slot(self):
        self.reserve('seed')
        os.link(self.ledger / 'wp-seed.json', self.base / 'outside-alias.json')
        # An unrelated in-ledger temporary (another inode) is no excuse either.
        fd = os.open(self.ledger / '.consult-unrelated', os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        with mock.patch.object(consult, '_settle_pause') as pauses:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'seed')
        pauses.assert_not_called()

    def test_planted_hard_link_into_the_ledger_refuses_ledger_invalid(self):
        self.reserve('seed')
        outside = self.base / 'outside.json'
        record = dict(self.load('wp-seed.json'), wp='planted')
        fd = os.open(outside, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(record).encode('ascii'))
        finally:
            os.close(fd)
        os.link(outside, self.ledger / 'wp-planted.json')
        self.refuses(consult.LEDGER_INVALID, self.reserve, 'planted')

    def test_another_callers_temporary_second_name_is_waited_out(self):
        self.reserve('busy')
        in_flight = self.ledger / '.consult-in-flight'
        os.link(self.ledger / 'wp-busy.json', in_flight)
        pauses = []

        def other_caller_finishes():
            pauses.append(True)
            os.unlink(in_flight)

        with mock.patch.object(consult, '_settle_pause', side_effect=other_caller_finishes):
            error = self.refuses(consult.WP_USED, self.reserve, 'busy')
        self.assertIn('state=RESERVED', str(error))
        self.assertEqual(pauses, [True])

    def test_created_slot_keeping_its_temporary_name_refuses_and_stays(self):
        real_unlink = os.unlink

        def keep_temporary(path, *args, **kwargs):
            if os.path.basename(os.fspath(path)).startswith('.consult-'):
                raise OSError('temporary name kept')
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(consult.os, 'unlink', side_effect=keep_temporary):
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'stuck')
        # Fail closed: the WP slot is never removed and no week slot was taken.
        self.assertEqual(self.names('wp-'), ['wp-stuck.json'])
        self.assertEqual(self.names('week-'), [])
        self.assertEqual((self.ledger / 'wp-stuck.json').lstat().st_nlink, 2)

    # root confinement (security repair 1b)

    def test_symlinked_root_refuses_ledger_invalid(self):
        real = self.base / 'real-tasks'
        real.mkdir(mode=0o700)
        os.chmod(real, 0o700)
        link = self.base / 'tasks-link'
        os.symlink(real, link)
        self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW, root=link)
        self.assertFalse(self.ledger.exists())

    def test_symlinked_parent_refuses_ledger_invalid(self):
        state = self.base / 'real-state'
        state.mkdir(mode=0o700)
        (state / 'tasks').mkdir(mode=0o700)
        os.chmod(state, 0o700)
        os.chmod(state / 'tasks', 0o700)
        link = self.base / 'state-link'
        os.symlink(state, link)
        self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW,
                     root=link / 'tasks')
        self.assertFalse((state / consult.LEDGER_DIR).exists())
        # A handle whose root runs through a symlink refuses too.
        handle = self.reserve('wp5')
        os.symlink(self.base, self.base / 'base-link')
        aliased = dataclasses.replace(handle, root=self.base / 'base-link' / 'tasks',
                                      directory=self.base / 'base-link' / consult.LEDGER_DIR)
        self.refuses(consult.LEDGER_INVALID, consult.finish, aliased, consult.DONE, REPORT,
                     now=NOW)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'RESERVED')

    def test_group_or_world_writable_root_refuses_ledger_invalid(self):
        self.addCleanup(os.chmod, self.base, 0o700)
        self.addCleanup(os.chmod, self.root, 0o700)
        for mode in (0o770, 0o707, 0o750, 0o755):
            with self.subTest(root_mode=oct(mode)):
                os.chmod(self.root, mode)
                self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        os.chmod(self.root, 0o700)
        for mode in (0o770, 0o702):
            with self.subTest(state_mode=oct(mode)):
                os.chmod(self.base, mode)
                self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        os.chmod(self.base, 0o700)
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.reserve('wp5').week_slot, 1)

    def test_finish_rechecks_the_supplied_root_confinement(self):
        handle = self.reserve('wp5')
        self.addCleanup(os.chmod, self.root, 0o700)
        os.chmod(self.root, 0o750)
        self.refuses(consult.LEDGER_INVALID, consult.finish, handle, consult.DONE, REPORT,
                     now=NOW)
        os.chmod(self.root, 0o700)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'RESERVED')
        consult.finish(handle, consult.DONE, REPORT, now=NOW)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'DONE')

    def test_state_root_swapped_after_the_check_is_not_followed(self):
        state = self.base / 'state'
        (state / 'tasks').mkdir(mode=0o700, parents=True)
        os.chmod(state, 0o700)
        os.chmod(state / 'tasks', 0o700)
        decoy = self.base / 'decoy'
        (decoy / 'tasks').mkdir(mode=0o700, parents=True)
        os.chmod(decoy, 0o700)
        os.chmod(decoy / 'tasks', 0o700)
        moved = self.base / 'state-moved'
        real_confine = consult._confine

        def confine_then_swap(store):
            result = real_confine(store)
            os.rename(state, moved)
            os.symlink(decoy, state)
            return result

        with mock.patch.object(consult, '_confine', side_effect=confine_then_swap):
            consult.reserve('wp5', now=NOW, root=state / 'tasks')
        # Every write stayed in the checked directory, which now sits at ``moved``.
        self.assertFalse((decoy / consult.LEDGER_DIR).exists())
        self.assertTrue((moved / consult.LEDGER_DIR / 'wp-wp5.json').is_file())
        self.assertTrue((moved / consult.LEDGER_DIR / 'week-2026-W46-1.json').is_file())

    def test_platform_without_descriptor_calls_fails_closed(self):
        with mock.patch.object(consult, '_PLATFORM_READY', False):
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
            self.refuses(consult.LEDGER_INVALID, consult.ledger_directory, self.root)
        self.assertFalse(self.ledger.exists())

    def test_missing_or_non_directory_root_refuses(self):
        self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW,
                     root=self.base / 'absent')
        plain = self.base / 'plain'
        plain.write_bytes(b'')
        self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW, root=plain)
        self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW,
                     root=plain / 'tasks')
        self.refuses(consult.ARGUMENT_INVALID, consult.reserve, 'wp5', now=NOW, root=5)
        self.assertFalse(self.ledger.exists())

    # supplied root shape (formal finding F1)

    def test_dot_dot_after_a_symlink_refuses_before_any_normalisation(self):
        elsewhere = self.base / 'elsewhere'
        (elsewhere / 'inner').mkdir(mode=0o700, parents=True)
        (elsewhere / 'tasks').mkdir(mode=0o700)
        for path in (elsewhere, elsewhere / 'inner', elsewhere / 'tasks'):
            os.chmod(path, 0o700)
        os.symlink(elsewhere / 'inner', self.base / 'link')
        # Lexically base/tasks; the kernel would follow link and reach elsewhere/tasks.
        dotted = str(self.base) + '/link/../tasks'
        for root in (dotted, Path(dotted)):
            with self.subTest(root=type(root).__name__):
                self.refuses(consult.LEDGER_INVALID, consult.ledger_directory, root)
                self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW,
                             root=root)
        self.assertFalse(self.ledger.exists())
        self.assertFalse((elsewhere / consult.LEDGER_DIR).exists())
        # The plain base/tasks root still passes.
        self.assertEqual(consult.ledger_directory(self.root), self.ledger)
        self.assertEqual(consult.ledger_directory(str(self.root)), self.ledger)
        self.assertEqual(self.reserve('wp5').week_slot, 1)

    def test_dot_dot_after_a_missing_directory_refuses(self):
        self.assertFalse((self.base / 'missing').exists())
        dotted = str(self.base) + '/missing/../tasks'
        self.refuses(consult.LEDGER_INVALID, consult.ledger_directory, dotted)
        self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW, root=dotted)
        self.assertFalse(self.ledger.exists())
        self.assertFalse((self.base / 'missing').exists())

    def test_relative_empty_or_dot_component_root_refuses_ledger_invalid(self):
        text = str(self.root)
        for root in ('tasks', './tasks', Path('tasks'), text + '/', str(self.base) + '//tasks',
                     str(self.base) + '/./tasks', '/' + text, '/'):
            with self.subTest(root=root):
                self.refuses(consult.LEDGER_INVALID, consult.ledger_directory, root)
                self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp5', now=NOW,
                             root=root)
        self.refuses(consult.ARGUMENT_INVALID, consult.reserve, 'wp5', now=NOW, root='')
        self.assertFalse(self.ledger.exists())

    def test_default_root_uses_the_task_store_state_root(self):
        with mock.patch.object(task_store, 'default_root', return_value=str(self.root)):
            self.assertEqual(consult.ledger_directory(), self.ledger)
            handle = consult.reserve('wp5', now=NOW)
        self.assertEqual(handle.directory, self.ledger)
        self.assertEqual(self.names('wp-'), ['wp-wp5.json'])
        os.symlink(self.base, self.base / 'state-link')
        aliased = self.base / 'state-link' / 'tasks'
        with mock.patch.object(task_store, 'default_root', return_value=str(aliased)):
            self.refuses(consult.LEDGER_INVALID, consult.reserve, 'wp6', now=NOW)
        self.assertEqual(self.names('wp-'), ['wp-wp5.json'])

    # finish

    def test_finish_done_records_digest_and_keeps_both_slots(self):
        handle = self.reserve('wp5')
        later = NOW + timedelta(hours=2)
        consult.finish(handle, consult.DONE, REPORT, now=later)
        for name in ('wp-wp5.json', 'week-2026-W46-1.json'):
            record = self.load(name)
            self.assertEqual((record['state'], record['report_sha256'], record['finished_at']),
                             ('DONE', REPORT, later.isoformat()))
            self.assertEqual(stat.S_IMODE((self.ledger / name).lstat().st_mode), 0o600)
        self.assertEqual(self.load('wp-wp5.json')['week_slot'], 1)
        # The same finish again changes nothing; a different one refuses.
        consult.finish(handle, consult.DONE, REPORT, now=later + timedelta(hours=1))
        self.assertEqual(self.load('wp-wp5.json')['finished_at'], later.isoformat())
        self.refuses(consult.ARGUMENT_INVALID, consult.finish, handle, consult.FAILED, None)
        self.refuses(consult.ARGUMENT_INVALID, consult.finish, handle, consult.DONE,
                     OTHER_REPORT)
        self.assertEqual(self.load('week-2026-W46-1.json')['report_sha256'], REPORT)
        self.assertEqual(len(self.names('')), 2)

    def test_finish_failed_with_or_without_digest(self):
        first = self.reserve('one')
        second = self.reserve('two')
        consult.finish(first, consult.FAILED, None, now=NOW)
        consult.finish(second, consult.FAILED, REPORT, now=NOW)
        self.assertEqual((self.load('wp-one.json')['state'],
                          self.load('wp-one.json')['report_sha256']), ('FAILED', None))
        self.assertEqual((self.load('week-2026-W46-2.json')['state'],
                          self.load('week-2026-W46-2.json')['report_sha256']),
                         ('FAILED', REPORT))
        # Finished slots still count: the week keeps both and the WPs stay used.
        self.refuses(consult.WP_USED, self.reserve, 'one')
        self.assertEqual(self.reserve('three').week_slot, 3)

    def test_finish_argument_checks_leave_the_slots_reserved(self):
        handle = self.reserve('wp5')
        bad_calls = [
            (handle, 'RESERVED', REPORT),
            (handle, 'done', REPORT),
            (handle, consult.DONE, None),
            (handle, consult.DONE, REPORT.upper()),
            (handle, consult.DONE, REPORT[:40]),
            (('wp5', WEEK, 1), consult.DONE, REPORT),
            (dataclasses.replace(handle, week_slot=0), consult.DONE, REPORT),
            (dataclasses.replace(handle, wp='../x'), consult.DONE, REPORT),
            (dataclasses.replace(handle, directory=Path(consult.LEDGER_DIR)), consult.DONE,
             REPORT),
            (dataclasses.replace(handle, directory=self.base), consult.DONE, REPORT),
            (dataclasses.replace(handle, directory=self.root / '..' / consult.LEDGER_DIR),
             consult.DONE, REPORT),
            # The root is re-derived: a relative, non-normalised or non-Path root
            # refuses, and the directory must be the ledger of that root.
            (dataclasses.replace(handle, root=Path('tasks')), consult.DONE, REPORT),
            (dataclasses.replace(handle, root=self.root / '..' / 'tasks'), consult.DONE,
             REPORT),
            (dataclasses.replace(handle, root=str(self.root)), consult.DONE, REPORT),
        ]
        for args in bad_calls:
            with self.subTest(args=args[1:]):
                self.refuses(consult.ARGUMENT_INVALID, consult.finish, *args, now=NOW)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'RESERVED')
        self.assertEqual(self.load('week-2026-W46-1.json')['state'], 'RESERVED')

    def test_finish_refuses_ledger_invalid_when_slots_disagree(self):
        handle = self.reserve('wp5')
        self.reserve('neighbour')
        foreign = dataclasses.replace(handle, week_slot=2)
        self.refuses(consult.LEDGER_INVALID, consult.finish, foreign, consult.DONE, REPORT,
                     now=NOW)
        absent = dataclasses.replace(handle, week_slot=5)
        self.refuses(consult.LEDGER_INVALID, consult.finish, absent, consult.DONE, REPORT,
                     now=NOW)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'RESERVED')
        self.assertEqual(self.load('week-2026-W46-2.json')['state'], 'RESERVED')

    # durability is fail-closed (security repair 2, Codex security v93)

    def test_healthy_reserve_and_finish_sync_every_step(self):
        patcher, calls = self.fsync_fault(lambda info: True, fail_on=set())
        with patcher:
            handle = self.reserve('wp5')
        directories = [(i.st_dev, i.st_ino) for i in calls if stat.S_ISDIR(i.st_mode)]
        # State root once (the ledger directory's name), ledger after the WP
        # slot and after the full reservation; three slot files written.
        self.assertEqual(directories.count(self.identity(self.base)), 1)
        self.assertEqual(directories.count(self.identity(self.ledger)), 2)
        self.assertEqual(sum(stat.S_ISREG(i.st_mode) for i in calls), 3)
        patcher, calls = self.fsync_fault(lambda info: True, fail_on=set())
        with patcher:
            consult.finish(handle, consult.DONE, REPORT, now=NOW)
        directories = [(i.st_dev, i.st_ino) for i in calls if stat.S_ISDIR(i.st_mode)]
        self.assertEqual(directories, [self.identity(self.ledger)])
        self.assertEqual(sum(stat.S_ISREG(i.st_mode) for i in calls), 2)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'DONE')

    def test_ledger_fsync_failure_after_the_wp_slot_refuses_and_keeps_it(self):
        self.make_ledger()
        patcher, calls = self.directory_fsync_fault(self.ledger, fail_on={1})
        with patcher:
            error = self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(len(calls), 1)
        self.assertIn('ledger directory not synced', str(error))
        record = self.load('wp-wp5.json')
        self.assertEqual((record['state'], record['week_slot']), ('RESERVED', None))
        self.assertEqual(self.names('week-'), [])
        self.assertEqual(self.names('.consult-'), [])
        # Fail closed: the WP stays used; a healthy run for another WP passes.
        self.refuses(consult.WP_USED, self.reserve, 'wp5')
        self.assertEqual(self.reserve('other').week_slot, 1)

    def test_ledger_fsync_failure_after_the_week_slot_refuses_and_keeps_both(self):
        self.make_ledger()
        patcher, calls = self.directory_fsync_fault(self.ledger, fail_on={2})
        with patcher:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(len(calls), 2)
        record = self.load('wp-wp5.json')
        self.assertEqual((record['state'], record['week_slot']), ('RESERVED', 1))
        self.assertEqual(self.load('week-2026-W46-1.json')['wp'], 'wp5')
        self.assertEqual(self.names('.consult-'), [])
        error = self.refuses(consult.WP_USED, self.reserve, 'wp5')
        self.assertIn('week_slot=1', str(error))
        self.assertEqual(self.reserve('other').week_slot, 2)

    def test_state_root_fsync_failure_refuses_before_any_slot(self):
        patcher, calls = self.directory_fsync_fault(self.base)
        with patcher:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.names(''), [])
        self.assertEqual(self.reserve('wp5').week_slot, 1)

    def test_slot_file_fsync_failure_refuses_in_reserve_and_finish(self):
        patcher, calls = self.fsync_fault(lambda info: stat.S_ISREG(info.st_mode))
        with patcher:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.names(''), [])  # the temporary was removed
        handle = self.reserve('wp5')
        patcher, calls = self.fsync_fault(lambda info: stat.S_ISREG(info.st_mode))
        with patcher:
            self.refuses(consult.LEDGER_INVALID, consult.finish, handle, consult.DONE,
                         REPORT, now=NOW)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.load('wp-wp5.json')['state'], 'RESERVED')
        self.assertEqual(self.load('week-2026-W46-1.json')['state'], 'RESERVED')
        self.assertEqual(self.names('.consult-'), [])

    def test_ledger_fsync_failure_in_finish_refuses_also_on_a_repeat(self):
        handle = self.reserve('wp5')
        patcher, calls = self.directory_fsync_fault(self.ledger)
        with patcher:
            self.refuses(consult.LEDGER_INVALID, consult.finish, handle, consult.DONE,
                         REPORT, now=NOW)
            # Both slots now read DONE, yet a repeat still syncs and refuses.
            self.refuses(consult.LEDGER_INVALID, consult.finish, handle, consult.DONE,
                         REPORT, now=NOW)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.names(''), ['week-2026-W46-1.json', 'wp-wp5.json'])
        # A healthy repeat completes.
        consult.finish(handle, consult.DONE, REPORT, now=NOW)
        for name in ('wp-wp5.json', 'week-2026-W46-1.json'):
            self.assertEqual((self.load(name)['state'], self.load(name)['report_sha256']),
                             ('DONE', REPORT))

    def test_week_cap_after_the_wp_slot_refuses_ledger_invalid_when_not_durable(self):
        for i in range(1, 11):
            self.reserve('wp%02d' % i)
        patcher, calls = self.directory_fsync_fault(self.ledger, fail_on={2})
        with mock.patch.object(consult, '_week_has_free_slot', return_value=True), patcher:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'late')
        self.assertEqual(len(calls), 2)
        self.assertIn('wp-late.json', self.names('wp-'))
        self.assertEqual(len(self.names('week-')), 10)
        self.assertEqual(self.names('.consult-'), [])
        self.refuses(consult.WP_USED, self.reserve, 'late', NOW + timedelta(days=7))

    def test_week_cap_failed_mark_write_error_refuses_and_keeps_the_wp_slot(self):
        for i in range(1, 11):
            self.reserve('wp%02d' % i)

        def replace_fails(*args, **kwargs):
            raise OSError(errno.EIO, 'injected replace failure')

        with mock.patch.object(consult, '_week_has_free_slot', return_value=True), \
                mock.patch.object(consult.os, 'replace', side_effect=replace_fails):
            error = self.refuses(consult.LEDGER_INVALID, self.reserve, 'late')
        self.assertIn('slot not updated', str(error))
        record = self.load('wp-late.json')
        self.assertEqual((record['state'], record['week_slot']), ('RESERVED', None))
        self.assertEqual(self.names('.consult-'), [])
        self.refuses(consult.WP_USED, self.reserve, 'late', NOW + timedelta(days=7))

    def close_fault(self, matches):
        """Patch os.close: the descriptor is really closed, then a match raises EIO."""
        real_close = os.close
        failures = []

        def close(fd):
            try:
                info = os.fstat(fd)
            except OSError:
                info = None
            real_close(fd)
            if info is not None and matches(info):
                failures.append(fd)
                raise OSError(errno.EIO, 'injected close failure')

        return mock.patch.object(consult.os, 'close', side_effect=close), failures

    def test_ledger_directory_close_failure_refuses_and_keeps_both_slots(self):
        self.make_ledger()
        target = self.identity(self.ledger)
        patcher, failures = self.close_fault(
            lambda info: stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == target)
        with patcher:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(len(failures), 1)
        record = self.load('wp-wp5.json')
        self.assertEqual((record['state'], record['week_slot']), ('RESERVED', 1))
        self.assertEqual(self.names('week-'), ['week-2026-W46-1.json'])
        self.refuses(consult.WP_USED, self.reserve, 'wp5')

    def test_slot_file_close_failure_refuses_before_any_slot(self):
        patcher, failures = self.close_fault(lambda info: stat.S_ISREG(info.st_mode))
        with patcher:
            self.refuses(consult.LEDGER_INVALID, self.reserve, 'wp5')
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.names(''), [])
        self.assertEqual(self.reserve('wp5').week_slot, 1)


if __name__ == '__main__':
    unittest.main()
