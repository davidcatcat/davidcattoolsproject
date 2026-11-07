"""Tests for the bounded GitHub sync poll step (#1144 WP5).

A fake GitHub GET and a fake parser stand in for the network and the message
format. Nothing here calls gh, the network or a model. Contention tests run
each competing call in a separate spawned process and order them with events
and bounded waits, never sleeps.
"""

import contextlib
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import fcntl
except ImportError:  # Non-POSIX: the module fails closed; contention tests skip.
    fcntl = None

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from llmtool import github_sync_poll as sync  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

TASK_A = 'a' * 32
TASK_B = 'b' * 32
QUERY = 'issues/comments?sort=created&direction=desc&per_page=100&page='
STATE_NAME = 'sync-state.json'
WAIT = 60.0  # Upper bound for every cross-process wait; no test sleeps.


def comment(comment_id, body='plain chat secret-body-text'):
    return {'id': comment_id, 'body': body, 'user': {'login': 'someone'}}


def sync_comment(comment_id, key, revision, delivery, kind='request'):
    return comment(comment_id, f'SYNC key={key} rev={revision} delivery={delivery} '
                               f'kind={kind} secret-body-text')


def refuse_comment(comment_id, code):
    return comment(comment_id, f'REFUSE {code} secret-refusal-text')


class FakeGitHub:
    """Repository issue comments, newest first, 100 per page."""

    def __init__(self, comments=()):
        self.comments = list(comments)
        self.calls = []
        self.fault = None
        self.fault_page = 1
        self.after_call = None

    def add(self, *items):
        self.comments.extend(items)

    def remove_newest(self):
        newest = max(self.comments, key=lambda item: item['id'])
        self.comments.remove(newest)
        return newest['id']

    def pages(self):
        return [int(call[len(QUERY):]) for call in self.calls]

    def __call__(self, path):
        self.calls.append(path)
        if not path.startswith(QUERY) or 'since' in path:
            raise AssertionError('unexpected GitHub path: ' + path)
        page_text = path[len(QUERY):]
        if not page_text.isdigit():
            raise AssertionError('unexpected GitHub page: ' + path)
        page = int(page_text)
        try:
            if self.fault is not None and page >= self.fault_page:
                if self.fault == 'raise':
                    raise LLMToolError('GitHub unavailable')
                return self.fault
            ordered = sorted(self.comments, key=lambda item: item['id'], reverse=True)
            return [dict(item) for item in ordered[(page - 1) * 100:page * 100]]
        finally:
            if self.after_call is not None:
                self.after_call(page)


class FakeParser:
    """Reads the fake SYNC/REFUSE bodies; everything else is not a sync message."""

    def __init__(self):
        self.seen = []

    def __call__(self, item):
        self.seen.append(item['id'])
        words = item['body'].split()
        if words and words[0] == 'SYNC':
            fields = dict(word.split('=', 1) for word in words[1:5])
            return {'request_key': fields['key'], 'revision': int(fields['rev']),
                    'delivery_id': fields['delivery'], 'kind': fields['kind']}
        if words and words[0] == 'REFUSE':
            return words[1]
        return None


def _contending_flock(real, report):
    """Wrap flock: the first acquire tries nonblocking and reports whether it had to wait.

    CONTENDED proves another holder had the lock at that moment; the call then
    blocks on the real lock exactly as the module would.
    """
    done = []

    def flock(fd, operation):
        if operation & fcntl.LOCK_UN or done:
            return real(fd, operation)
        done.append(operation)
        try:
            real(fd, operation | fcntl.LOCK_NB)
        except BlockingIOError:
            report.put(('CONTENDED',))
            return real(fd, operation)
        report.put(('FREE',))
        return None
    return flock


def _child(spec, path, report, release):
    """Run one sync call in this separate process and report each step on the queue.

    ``pause`` holds the call at one point while it owns the lock: ``github`` (the
    first GitHub read of a poll), ``replace`` (just before os.replace publishes
    a write), ``init_link`` (after os.link, while the new file still has two
    names) or ``load`` (inside the JSON load of pending). The hold ends when the
    parent sets ``release`` or after WAIT seconds.
    """
    def hold():
        report.put(('HOLDING',))
        release.wait(WAIT)

    action, pause = spec['action'], spec.get('pause')
    with contextlib.ExitStack() as stack:
        if spec.get('contend'):
            stack.enter_context(mock.patch.object(
                sync.fcntl, 'flock', new=_contending_flock(sync.fcntl.flock, report)))
        if pause == 'replace':
            real_replace = os.replace

            def replace(source, target, **kwargs):
                hold()
                return real_replace(source, target, **kwargs)
            stack.enter_context(mock.patch.object(sync.os, 'replace', new=replace))
        elif pause == 'init_link':
            real_unlink = os.unlink

            def unlink(target, **kwargs):
                if str(target).endswith('.tmp'):
                    hold()
                return real_unlink(target, **kwargs)
            stack.enter_context(mock.patch.object(sync.os, 'unlink', new=unlink))
        elif pause == 'load':
            real_loads = json.loads

            def loads(*args, **kwargs):
                hold()
                return real_loads(*args, **kwargs)
            stack.enter_context(mock.patch.object(sync.json, 'loads', new=loads))
        try:
            if action == 'poll':
                github = FakeGitHub(spec.get('comments', ()))
                github.fault = spec.get('fault')
                if pause == 'github':
                    held = []

                    def after_call(page):
                        if not held:
                            held.append(page)
                            hold()
                    github.after_call = after_call
                result = sync.poll_once(path, github, FakeParser())
            elif action == 'link':
                result = sync.link_task(path, spec['request_key'], spec['task_id'])
            elif action == 'pending':
                result = sync.pending(path)
            elif action == 'init':
                result = sync.init_state(path, spec['start'])
            else:
                raise ValueError('unknown child action')
        except LLMToolError as error:
            report.put(('ERROR', getattr(error, 'sync_code', None)))
        except Exception as error:
            report.put(('CRASH', type(error).__name__))
        else:
            report.put(('RESULT', result))


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.dir = holder.name
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, STATE_NAME)
        self.github = FakeGitHub()
        self.parser = FakeParser()

    def poll(self, **kwargs):
        return sync.poll_once(self.path, self.github, self.parser, **kwargs)

    def assertCode(self, code, func, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            func(*args, **kwargs)
        self.assertEqual(getattr(caught.exception, 'sync_code', None), code)
        return caught.exception

    def state(self, path=None):
        return json.loads(Path(path or self.path).read_text(encoding='utf-8'))

    def snapshot(self, path=None):
        path = path or self.path
        info = os.lstat(path)
        return (Path(path).read_bytes(), info.st_ino, info.st_mtime_ns,
                stat.S_IMODE(info.st_mode))

    def assertPrivate(self, path=None):
        path = path or self.path
        info = os.lstat(path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)
        self.assertNoTempFiles(os.path.dirname(path))

    def assertNoTempFiles(self, directory=None):
        names = os.listdir(directory or self.dir)
        self.assertEqual([name for name in names if name.endswith('.tmp')], [])

    def can_lock(self, operation):
        """Try a nonblocking flock on the state directory from a new descriptor."""
        fd = os.open(self.dir, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        finally:
            os.close(fd)

    def assertUnlocked(self):
        self.assertTrue(self.can_lock(fcntl.LOCK_EX))
        self.assertNoTempFiles()


class InitStateTests(SyncTestCase):
    def test_init_creates_private_state_file(self):
        result = sync.init_state(self.path, 7)
        self.assertEqual(result, {'status': 'INITIALIZED', 'cursor': 7})
        self.assertEqual(self.state(), {'schema_version': 1, 'cursor': 7,
                                        'failure_count': 0, 'requests': {}})
        self.assertPrivate()
        self.assertEqual(os.listdir(self.dir), [STATE_NAME])

    def test_init_refuses_existing_state_and_keeps_cursor(self):
        sync.init_state(self.path, 7)
        before = self.snapshot()
        self.assertCode('SYNC_STATE_EXISTS', sync.init_state, self.path, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.state()['cursor'], 7)
        self.assertNoTempFiles()

    def test_init_refuses_symlink_path(self):
        target = os.path.join(self.dir, 'elsewhere.json')
        os.symlink(target, self.path)
        self.assertCode('SYNC_STATE_EXISTS', sync.init_state, self.path, 0)
        self.assertFalse(os.path.exists(target))

    def test_init_refuses_directory_writable_by_group_or_other(self):
        for mode in (0o770, 0o707, 0o777):
            os.chmod(self.dir, mode)
            try:
                self.assertCode('SYNC_STATE_DIR_UNSAFE', sync.init_state, self.path, 0)
            finally:
                os.chmod(self.dir, 0o700)
            self.assertFalse(os.path.lexists(self.path))

    def test_init_rejects_invalid_arguments(self):
        for path, start in (('relative.json', 0), (self.path, -1), (self.path, True),
                            (self.path, '5'), (self.path, 2**63),
                            (self.dir + '/./' + STATE_NAME, 0), (self.dir + '/', 0)):
            self.assertCode('SYNC_ARGUMENT_INVALID', sync.init_state, path, start)
        self.assertFalse(os.path.lexists(self.path))


class StateFailClosedTests(SyncTestCase):
    def assertEveryEntryFails(self, code):
        self.assertCode(code, self.poll)
        self.assertCode(code, sync.pending, self.path)
        self.assertCode(code, sync.link_task, self.path, 'req-1', TASK_A)
        self.assertEqual(self.github.calls, [])
        self.assertEqual(self.parser.seen, [])

    def test_missing_state_fails_closed_and_is_not_created(self):
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.assertEveryEntryFails('SYNC_STATE_MISSING')
        self.assertFalse(os.path.lexists(self.path))

    def test_corrupt_json_fails_closed_without_reset(self):
        for raw in (b'{not json', b'', b'[]', b'{"schema_version":1}',
                    b'{"schema_version":1,"cursor":"5","failure_count":0,"requests":{}}',
                    b'{"schema_version":1,"cursor":5,"cursor":6,"failure_count":0,"requests":{}}',
                    b'{"schema_version":1,"cursor":NaN,"failure_count":0,"requests":{}}',
                    b'{"schema_version":2,"cursor":5,"failure_count":0,"requests":{}}'):
            with self.subTest(raw=raw):
                if os.path.lexists(self.path):
                    os.unlink(self.path)
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                before = self.snapshot()
                self.assertEveryEntryFails('SYNC_STATE_CORRUPT')
                self.assertEqual(self.snapshot(), before)

    def test_inconsistent_ledger_entry_is_corrupt(self):
        sync.init_state(self.path, 0)
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.poll()
        value = self.state()
        value['requests']['req-1']['state'] = 'LINKED'
        raw = json.dumps(value).encode('utf-8')
        os.unlink(self.path)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        self.github.calls.clear()
        self.parser.seen.clear()
        self.assertEveryEntryFails('SYNC_STATE_CORRUPT')

    def test_wrong_mode_fails_closed_and_is_not_repaired(self):
        sync.init_state(self.path, 5)
        for mode in (0o644, 0o640, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.path, mode)
                before = self.snapshot()
                self.assertEveryEntryFails('SYNC_STATE_CORRUPT')
                self.assertEqual(self.snapshot(), before)

    def test_symlink_state_fails_closed(self):
        real_dir = os.path.join(self.dir, 'real')
        os.mkdir(real_dir, 0o700)
        target = os.path.join(real_dir, STATE_NAME)
        sync.init_state(target, 3)
        target_before = self.snapshot(target)
        os.symlink(target, self.path)
        self.assertEveryEntryFails('SYNC_STATE_CORRUPT')
        self.assertTrue(os.path.islink(self.path))
        self.assertEqual(self.snapshot(target), target_before)

    def test_directory_writable_by_group_fails_closed(self):
        sync.init_state(self.path, 5)
        before = self.snapshot()
        os.chmod(self.dir, 0o770)
        try:
            self.assertEveryEntryFails('SYNC_STATE_CORRUPT')
        finally:
            os.chmod(self.dir, 0o700)
        self.assertEqual(self.snapshot(), before)

    def test_hard_linked_state_fails_closed(self):
        sync.init_state(self.path, 5)
        os.link(self.path, os.path.join(self.dir, 'second-name.json'))
        self.assertEveryEntryFails('SYNC_STATE_CORRUPT')


class PollTests(SyncTestCase):
    def test_first_run_handles_only_comments_after_start(self):
        sync.init_state(self.path, 100)
        self.github.add(*(sync_comment(i, f'req-{i}', 1, f'd{i}') for i in range(98, 106)))
        result = self.poll()
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(result['previous_cursor'], 100)
        self.assertEqual(result['cursor'], 105)
        self.assertEqual(result['pages_read'], 1)
        self.assertEqual(result['new_comments'], 5)
        self.assertEqual(result['counts']['pending_pm'], 5)
        self.assertEqual(result['new_request_keys'], [f'req-{i}' for i in range(101, 106)])
        self.assertEqual(self.parser.seen, [101, 102, 103, 104, 105])
        self.assertEqual(self.github.calls, [QUERY + '1'])
        state = self.state()
        self.assertEqual(state['cursor'], 105)
        self.assertEqual(sorted(state['requests']), [f'req-{i}' for i in range(101, 106)])
        self.assertEqual(state['requests']['req-101'], {
            'state': 'PENDING_PM', 'kind': 'request', 'revision': 1, 'delivery_id': 'd101',
            'first_comment_id': 101, 'comment_id': 101, 'task_id': None,
            'linked_revision': None})
        self.assertPrivate()
        self.assertNoTempFiles()
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('secret', Path(self.path).read_text(encoding='utf-8'))

    def test_idle_poll_reads_one_page_and_does_not_write(self):
        sync.init_state(self.path, 0)
        self.github.add(*(comment(i) for i in range(1, 251)))
        self.assertEqual(self.poll(max_pages=6)['status'], 'SYNCED')
        self.github.calls.clear()
        self.parser.seen.clear()
        before = self.snapshot()
        real_replace, real_mkstemp = os.replace, tempfile.mkstemp
        with mock.patch.object(sync.os, 'replace', wraps=real_replace) as replace, \
                mock.patch.object(sync.tempfile, 'mkstemp', wraps=real_mkstemp) as mkstemp:
            result = self.poll()
        self.assertEqual(result['status'], 'IDLE')
        self.assertEqual(result['pages_read'], 1)
        self.assertEqual(result['new_comments'], 0)
        self.assertEqual(self.github.calls, [QUERY + '1'])
        self.assertEqual(self.parser.seen, [])
        replace.assert_not_called()
        mkstemp.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_empty_repository_is_idle_without_write(self):
        sync.init_state(self.path, 0)
        before = self.snapshot()
        result = self.poll()
        self.assertEqual(result['status'], 'IDLE')
        self.assertEqual(self.github.calls, [QUERY + '1'])
        self.assertEqual(self.snapshot(), before)

    def test_catch_up_across_pages_handles_oldest_first(self):
        sync.init_state(self.path, 1000)
        self.github.add(*(comment(i) for i in range(901, 1251)))
        self.github.add(sync_comment(1251, 'req-x', 1, 'd1'))
        result = self.poll()
        self.assertEqual(result['status'], 'SYNCED')
        # A multi-page walk is confirmed by a second walk before it is accepted.
        self.assertEqual(self.github.pages(), [1, 2, 3, 1, 2, 3])
        self.assertEqual(result['pages_read'], 6)
        self.assertEqual(self.parser.seen, list(range(1001, 1252)))
        self.assertEqual(result['new_comments'], 251)
        self.assertEqual(result['counts']['non_sync'], 250)
        self.assertEqual(result['cursor'], 1251)
        self.assertEqual(self.state()['cursor'], 1251)

    def test_catch_up_limit_leaves_state_unchanged(self):
        sync.init_state(self.path, 0)
        self.github.add(*(sync_comment(i, f'req-{i}', 1, 'd') for i in range(1, 251)))
        before = self.snapshot()
        self.assertCode('SYNC_CATCHUP_LIMIT', self.poll, max_pages=2)
        self.assertEqual(self.github.pages(), [1, 2])
        self.assertEqual(self.parser.seen, [])
        self.assertEqual(self.snapshot(), before)
        self.assertNoTempFiles()
        # max_pages bounds every read in the poll, confirmation walk included: a
        # three-page catch-up plus its confirmation walk needs six reads.
        self.github.calls.clear()
        self.assertCode('SYNC_CATCHUP_LIMIT', self.poll, max_pages=5)
        self.assertEqual(self.github.pages(), [1, 2, 3, 1, 2])
        self.assertEqual(self.parser.seen, [])
        self.assertEqual(self.snapshot(), before)
        self.assertNoTempFiles()
        self.github.calls.clear()
        result = self.poll(max_pages=6)
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(self.github.pages(), [1, 2, 3, 1, 2, 3])
        self.assertEqual(result['pages_read'], 6)
        self.assertEqual(result['cursor'], 250)
        self.assertEqual(len(self.state()['requests']), 250)

    def test_unsettled_walks_within_page_limit_give_catch_up_limit(self):
        sync.init_state(self.path, 0)
        self.github.add(*(comment(i) for i in range(1, 151)))
        deleted = []

        def delete_once(page):
            if page == 1 and not deleted:
                deleted.append(self.github.remove_newest())
        self.github.after_call = delete_once
        before = self.snapshot()
        # The first two walks disagree and a third walk would be read five.
        self.assertCode('SYNC_CATCHUP_LIMIT', self.poll, max_pages=4)
        self.github.after_call = None
        self.assertEqual(self.github.pages(), [1, 2, 1, 2])
        self.assertEqual(self.parser.seen, [])
        self.assertEqual(self.snapshot(), before)
        self.assertNoTempFiles()

    def test_max_pages_and_callables_are_validated(self):
        sync.init_state(self.path, 0)
        for value in (0, 51, -1, True, '10', 2.0, None):
            self.assertCode('SYNC_ARGUMENT_INVALID', self.poll, max_pages=value)
        self.assertCode('SYNC_ARGUMENT_INVALID', sync.poll_once, self.path, None, self.parser)
        self.assertCode('SYNC_ARGUMENT_INVALID', sync.poll_once, self.path, self.github, 'x')
        self.assertEqual(self.github.calls, [])
        self.assertEqual(self.poll(max_pages=50)['status'], 'IDLE')

    def arrive_once(self, *items):
        pending_items = list(items)

        def arrive(page):
            if page == 1 and pending_items:
                self.github.add(*pending_items)
                pending_items.clear()
        self.github.after_call = arrive

    def test_comment_arriving_mid_catch_up_is_handled_once(self):
        sync.init_state(self.path, 0)
        self.github.add(*(comment(i) for i in range(1, 151)))
        self.arrive_once(comment(151))
        result = self.poll()
        self.github.after_call = None
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(self.github.pages(), [1, 2, 1, 2])
        self.assertEqual(self.parser.seen, list(range(1, 151)))
        self.assertEqual(result['cursor'], 150)
        self.parser.seen.clear()
        result = self.poll()
        self.assertEqual(self.parser.seen, [151])
        self.assertEqual(result['cursor'], 151)

    def test_several_comments_arriving_between_pages_are_not_skipped(self):
        sync.init_state(self.path, 0)
        self.github.add(*(comment(i) for i in range(1, 151)))
        self.arrive_once(comment(151), comment(152))
        result = self.poll()
        self.github.after_call = None
        # Page 2 repeats ids 52 and 51; every id up to 150 is handled exactly once.
        self.assertEqual(self.parser.seen, list(range(1, 151)))
        self.assertEqual(result['cursor'], 150)
        self.parser.seen.clear()
        result = self.poll()
        self.assertEqual(self.parser.seen, [151, 152])
        self.assertEqual(result['cursor'], 152)
        self.parser.seen.clear()
        self.assertEqual(self.poll()['status'], 'IDLE')
        self.assertEqual(self.parser.seen, [])

    def test_comment_deleted_between_pages_does_not_skip_a_comment(self):
        sync.init_state(self.path, 0)
        self.github.add(*(comment(i) for i in range(1, 151)))
        deleted = []

        def delete_once(page):
            if page == 1 and not deleted:
                deleted.append(self.github.remove_newest())
        self.github.after_call = delete_once
        result = self.poll()
        self.github.after_call = None
        # The first walk misses comment 50 after comment 150 is deleted; the second walk
        # disagrees, and the third walk confirms the second.
        self.assertEqual(deleted, [150])
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(self.github.pages(), [1, 2, 1, 2, 1, 2])
        self.assertEqual(result['pages_read'], 6)
        self.assertEqual(self.parser.seen, list(range(1, 150)))
        self.assertEqual(result['cursor'], 149)
        self.assertEqual(result['new_comments'], 149)
        self.parser.seen.clear()
        self.assertEqual(self.poll()['status'], 'IDLE')
        self.assertEqual(self.parser.seen, [])

    def test_every_refusal_code_keeps_its_own_count(self):
        sync.init_state(self.path, 0)
        codes = [f'SYNC_REFUSED_{number:02d}' for number in range(40)]
        self.github.add(*(refuse_comment(i + 1, code) for i, code in enumerate(codes)))
        self.github.add(refuse_comment(41, codes[39]), refuse_comment(42, codes[0]))
        result = self.poll()
        expected = dict.fromkeys(codes, 1)
        expected[codes[0]] = 2
        expected[codes[39]] = 2
        self.assertEqual(result['refusals'], expected)
        self.assertEqual(list(result['refusals']), sorted(codes))
        self.assertEqual(result['counts']['refused'], 42)
        self.assertNotIn('SYNC_REFUSAL_OTHER', result['refusals'])
        self.assertEqual(self.state()['requests'], {})
        self.assertNotIn('SYNC_REFUSED', Path(self.path).read_text(encoding='utf-8'))

    def test_refusals_are_counted_by_code_and_never_stored(self):
        sync.init_state(self.path, 0)
        self.github.add(refuse_comment(1, 'SYNC_NOT_OWNER'), refuse_comment(2, 'SYNC_NOT_OWNER'),
                        refuse_comment(3, 'SYNC_EDITED'), refuse_comment(4, 'bad-code'),
                        comment(5, 'RAISE secret-body-text'), comment(6))
        parser = self.parser

        def parse(item):
            if item['body'].startswith('RAISE'):
                raise ValueError('parser failed on secret-body-text')
            return parser(item)
        result = sync.poll_once(self.path, self.github, parse)
        self.assertEqual(result['refusals'], {'SYNC_EDITED': 1, 'SYNC_NOT_OWNER': 2,
                                              'SYNC_PARSE_ERROR': 1, 'SYNC_PARSE_INVALID': 1})
        self.assertEqual(result['counts']['refused'], 5)
        self.assertEqual(result['counts']['non_sync'], 1)
        self.assertEqual(result['cursor'], 6)
        text = Path(self.path).read_text(encoding='utf-8')
        self.assertNotIn('secret', text)
        self.assertNotIn('SYNC_NOT_OWNER', text)
        self.assertEqual(self.state()['requests'], {})
        self.assertNotIn('secret', json.dumps(result))

    def test_invalid_parsed_values_are_refused(self):
        sync.init_state(self.path, 0)
        values = {
            1: {'request_key': 'req-1', 'revision': 1, 'delivery_id': 'd1', 'kind': 'request',
                'body': 'extra'},
            2: {'request_key': 'req-2', 'revision': True, 'delivery_id': 'd2', 'kind': 'request'},
            3: {'request_key': 'bad key', 'revision': 1, 'delivery_id': 'd3', 'kind': 'request'},
            4: {'request_key': 'req-4', 'revision': -1, 'delivery_id': 'd4', 'kind': 'request'},
            5: {'request_key': 'req-5', 'revision': 1, 'delivery_id': '', 'kind': 'request'},
            6: {'request_key': 'req-6', 'revision': 1, 'delivery_id': 'd6', 'kind': 'Bad'},
            7: ['req-7'],
        }
        self.github.add(*(comment(i) for i in values))
        result = sync.poll_once(self.path, self.github, lambda item: values[item['id']])
        self.assertEqual(result['refusals'], {'SYNC_PARSE_INVALID': 7})
        self.assertEqual(self.state()['requests'], {})
        self.assertEqual(self.state()['cursor'], 7)


class LedgerTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        sync.init_state(self.path, 0)

    def test_duplicate_delivery_and_lower_revision_are_ignored(self):
        self.github.add(sync_comment(1, 'req-1', 2, 'd1'))
        self.assertEqual(self.poll()['counts']['pending_pm'], 1)
        self.github.add(sync_comment(2, 'req-1', 2, 'd1'), sync_comment(3, 'req-1', 1, 'd2'),
                        sync_comment(4, 'req-1', 2, 'd3'), sync_comment(5, 'req-1', 5, 'd1'))
        result = self.poll()
        self.assertEqual(result['counts']['inert'], 2)
        self.assertEqual(result['counts']['duplicate'], 2)
        self.assertEqual(result['counts']['pending_pm'], 0)
        self.assertEqual(result['counts']['revision_pending'], 0)
        self.assertEqual(result['new_request_keys'], [])
        entry = self.state()['requests']['req-1']
        self.assertEqual((entry['state'], entry['revision'], entry['delivery_id'],
                          entry['comment_id']), ('PENDING_PM', 2, 'd1', 1))
        self.assertEqual(self.state()['cursor'], 5)

    def test_duplicate_delivery_within_one_page(self):
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'), sync_comment(2, 'req-1', 1, 'd1'))
        result = self.poll()
        self.assertEqual(result['counts']['pending_pm'], 1)
        self.assertEqual(result['counts']['inert'], 1)
        self.assertEqual(result['new_request_keys'], ['req-1'])

    def test_revision_raise_before_link_keeps_request_pending(self):
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.poll()
        self.github.add(sync_comment(2, 'req-1', 2, 'd2', kind='update'))
        result = self.poll()
        self.assertEqual(result['counts']['revision_pending'], 1)
        self.assertEqual(result['counts']['stale_link'], 0)
        self.assertEqual(result['revised_request_keys'], ['req-1'])
        self.assertEqual(result['stale_link_request_keys'], [])
        entry = self.state()['requests']['req-1']
        self.assertEqual((entry['state'], entry['revision'], entry['delivery_id'], entry['kind'],
                          entry['first_comment_id'], entry['comment_id'], entry['task_id']),
                         ('PENDING_PM', 2, 'd2', 'update', 1, 2, None))
        self.assertEqual(sync.pending(self.path)['requests'], [{
            'request_key': 'req-1', 'kind': 'update', 'revision': 2,
            'first_comment_id': 1, 'comment_id': 2}])
        linked = sync.link_task(self.path, 'req-1', TASK_A)
        self.assertEqual(linked['status'], 'LINKED')
        entry = self.state()['requests']['req-1']
        self.assertEqual((entry['state'], entry['task_id'], entry['linked_revision']),
                         ('LINKED', TASK_A, 2))

    def test_revision_raise_after_link_flags_stale_and_never_relinks(self):
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.poll()
        sync.link_task(self.path, 'req-1', TASK_A)
        self.github.add(sync_comment(2, 'req-1', 2, 'd2'))
        result = self.poll()
        self.assertEqual(result['counts']['revision_pending'], 1)
        self.assertEqual(result['counts']['stale_link'], 1)
        self.assertEqual(result['stale_link_request_keys'], ['req-1'])
        entry = self.state()['requests']['req-1']
        self.assertEqual((entry['state'], entry['task_id'], entry['linked_revision'],
                          entry['revision']), ('STALE_LINK', TASK_A, 1, 2))
        before = self.snapshot()
        self.assertCode('SYNC_REQUEST_ALREADY_LINKED', sync.link_task,
                        self.path, 'req-1', TASK_B)
        again = sync.link_task(self.path, 'req-1', TASK_A)
        self.assertEqual(again['status'], 'INERT')
        self.assertEqual(again['state'], 'STALE_LINK')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sync.pending(self.path)['requests'], [])

    def test_link_conflict_unknown_and_invalid_arguments(self):
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.poll()
        self.assertEqual(sync.link_task(self.path, 'req-1', TASK_A)['status'], 'LINKED')
        before = self.snapshot()
        self.assertEqual(sync.link_task(self.path, 'req-1', TASK_A)['status'], 'INERT')
        self.assertCode('SYNC_REQUEST_ALREADY_LINKED', sync.link_task, self.path, 'req-1', TASK_B)
        self.assertCode('SYNC_REQUEST_UNKNOWN', sync.link_task, self.path, 'req-2', TASK_A)
        for task_id in ('A' * 32, 'a' * 31, 'a' * 33, 123, None, 'g' * 32):
            self.assertCode('SYNC_ARGUMENT_INVALID', sync.link_task, self.path, 'req-1', task_id)
        for key in ('', 'bad key', None, 7):
            self.assertCode('SYNC_ARGUMENT_INVALID', sync.link_task, self.path, key, TASK_B)
        self.assertEqual(self.snapshot(), before)

    def test_pending_lists_unlinked_requests_without_writing(self):
        self.github.add(sync_comment(1, 'req-b', 1, 'd1'), sync_comment(2, 'req-a', 3, 'd2'),
                        sync_comment(3, 'req-c', 1, 'd3'))
        self.poll()
        sync.link_task(self.path, 'req-c', TASK_A)
        before = self.snapshot()
        with mock.patch.object(sync.os, 'replace', wraps=os.replace) as replace:
            listed = sync.pending(self.path)
        replace.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(listed['cursor'], 3)
        self.assertEqual([item['request_key'] for item in listed['requests']], ['req-b', 'req-a'])
        self.assertEqual(listed['requests'][1]['revision'], 3)

    def test_ledger_full_leaves_state_unchanged(self):
        with mock.patch.object(sync, 'MAX_REQUESTS', 2):
            self.github.add(sync_comment(1, 'req-1', 1, 'd1'), sync_comment(2, 'req-2', 1, 'd2'))
            self.poll()
            self.github.add(sync_comment(3, 'req-1', 2, 'd3'))
            self.assertEqual(self.poll()['counts']['revision_pending'], 1)
            before = self.snapshot()
            self.github.add(sync_comment(4, 'req-2', 2, 'd4'), sync_comment(5, 'req-3', 1, 'd5'))
            self.assertCode('SYNC_LEDGER_FULL', self.poll)
            self.assertEqual(self.snapshot(), before)
            self.assertNoTempFiles()
            self.assertEqual(self.state()['cursor'], 3)


class CrashReplayTests(SyncTestCase):
    def prepare(self, path, github):
        sync.init_state(path, 0)
        github.add(sync_comment(1, 'req-1', 1, 'd1'), sync_comment(2, 'req-2', 1, 'd2'))
        sync.poll_once(path, github, FakeParser())
        sync.link_task(path, 'req-1', TASK_A)
        github.add(sync_comment(3, 'req-1', 2, 'd3'), sync_comment(4, 'req-2', 1, 'd4'),
                   sync_comment(5, 'req-2', 1, 'd2'), sync_comment(6, 'req-3', 1, 'd6'),
                   refuse_comment(7, 'SYNC_NOT_OWNER'), comment(8))

    def test_crash_before_write_replays_the_same_comments_identically(self):
        control_dir = os.path.join(self.dir, 'control')
        os.mkdir(control_dir, 0o700)
        control_path = os.path.join(control_dir, STATE_NAME)
        control_github = FakeGitHub()
        self.prepare(control_path, control_github)
        expected = sync.poll_once(control_path, control_github, FakeParser())

        crash_dir = os.path.join(self.dir, 'crash')
        os.mkdir(crash_dir, 0o700)
        crash_path = os.path.join(crash_dir, STATE_NAME)
        crash_github = FakeGitHub()
        self.prepare(crash_path, crash_github)
        before = self.snapshot(crash_path)
        with mock.patch.object(sync.os, 'replace', side_effect=OSError('crash')):
            self.assertCode('SYNC_STATE_WRITE_FAILED', sync.poll_once,
                            crash_path, crash_github, FakeParser())
        self.assertEqual(self.snapshot(crash_path), before)
        self.assertNoTempFiles(crash_dir)

        parser = FakeParser()
        replayed = sync.poll_once(crash_path, crash_github, parser)
        self.assertEqual(parser.seen, [3, 4, 5, 6, 7, 8])
        self.assertEqual(replayed, expected)
        self.assertEqual(Path(crash_path).read_bytes(), Path(control_path).read_bytes())
        self.assertEqual(replayed['stale_link_request_keys'], ['req-1'])
        self.assertEqual(replayed['new_request_keys'], ['req-3'])
        self.assertEqual(replayed['counts']['inert'], 1)
        self.assertEqual(replayed['counts']['duplicate'], 1)
        self.assertEqual(sync.poll_once(crash_path, crash_github, FakeParser())['status'], 'IDLE')
        self.assertPrivate(crash_path)


class OfflineTests(SyncTestCase):
    def setUp(self):
        super().setUp()
        sync.init_state(self.path, 0)
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.poll()
        self.github.calls.clear()
        self.parser.seen.clear()

    def assertOnlyFailureCountChanged(self, before, count):
        after = self.state()
        self.assertEqual(after['failure_count'], count)
        after['failure_count'] = before['failure_count']
        self.assertEqual(after, before)

    def test_read_error_backs_off_and_success_resets(self):
        before = self.state()
        self.github.fault = 'raise'
        expected = [30, 60, 120, 240, 480, 900, 900]
        for count, seconds in enumerate(expected, start=1):
            result = self.poll()
            self.assertEqual(result['status'], 'OFFLINE')
            self.assertEqual(result['failure_count'], count)
            self.assertEqual(result['backoff_seconds'], seconds)
            self.assertEqual(result['cursor'], 1)
            self.assertOnlyFailureCountChanged(before, count)
            self.assertPrivate()
        self.assertEqual(self.parser.seen, [])
        self.github.fault = None
        self.github.add(sync_comment(2, 'req-2', 1, 'd2'))
        result = self.poll()
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual((result['failure_count'], result['backoff_seconds']), (0, 0))
        self.assertEqual(self.state()['failure_count'], 0)
        self.github.fault = 'raise'
        self.assertEqual(self.poll()['backoff_seconds'], 30)

    def test_idle_success_after_failure_resets_count(self):
        self.github.fault = 'raise'
        self.poll()
        self.poll()
        self.assertEqual(self.state()['failure_count'], 2)
        self.github.fault = None
        result = self.poll()
        self.assertEqual(result['status'], 'IDLE')
        self.assertEqual(self.state()['failure_count'], 0)
        before = self.snapshot()
        self.assertEqual(self.poll()['status'], 'IDLE')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_response_is_offline(self):
        before = self.state()
        faults = ({'message': 'Not Found'}, 'text', 42,
                  [{'id': i} for i in range(200, 99, -1)],
                  [{'body': 'no id'}], [{'id': True}], [{'id': 0}], [{'id': '9'}], [7],
                  [{'id': 5}, {'id': 9}], [{'id': 9}, {'id': 9}], [{'id': 9}, {'id': 1}, {'id': 4}])
        for count, fault in enumerate(faults, start=1):
            with self.subTest(fault=fault if type(fault) is not list else fault[:1]):
                self.github.fault = fault
                result = self.poll()
                self.assertEqual(result['status'], 'OFFLINE')
                self.assertEqual(result['failure_count'], count)
                self.assertOnlyFailureCountChanged(before, count)
        self.assertEqual(self.parser.seen, [])

    def test_out_of_order_page_is_offline_and_skips_nothing(self):
        # An id at the cursor before a higher id must not end the walk early.
        self.github.add(sync_comment(2, 'req-2', 1, 'd2'))
        before = self.state()
        self.github.fault = [comment(1), sync_comment(2, 'req-2', 1, 'd2')]
        result = self.poll()
        self.assertEqual(result['status'], 'OFFLINE')
        self.assertEqual((result['failure_count'], result['backoff_seconds']), (1, 30))
        self.assertEqual(result['cursor'], 1)
        self.assertEqual(self.parser.seen, [])
        self.assertOnlyFailureCountChanged(before, 1)
        self.github.fault = None
        result = self.poll()
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(self.parser.seen, [2])
        self.assertEqual(result['new_request_keys'], ['req-2'])
        self.assertEqual(self.state()['cursor'], 2)

    def test_out_of_order_later_page_is_offline(self):
        self.github.add(*(comment(i) for i in range(2, 152)))
        before = self.state()
        self.github.fault = [{'id': 40}, {'id': 1}, {'id': 45}]
        self.github.fault_page = 2
        result = self.poll()
        self.assertEqual(result['status'], 'OFFLINE')
        self.assertEqual(self.github.pages(), [1, 2])
        self.assertEqual(self.parser.seen, [])
        self.assertOnlyFailureCountChanged(before, 1)

    def test_error_on_later_page_keeps_cursor_and_ledger(self):
        self.github.add(*(sync_comment(i, f'req-{i}', 1, 'd') for i in range(2, 152)))
        before = self.state()
        self.github.fault = 'raise'
        self.github.fault_page = 2
        result = self.poll()
        self.assertEqual(result['status'], 'OFFLINE')
        self.assertEqual(result['pages_read'], 2)
        self.assertEqual(self.github.pages(), [1, 2])
        self.assertEqual(self.parser.seen, [])
        self.assertOnlyFailureCountChanged(before, 1)
        self.github.fault = None
        result = self.poll()
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(result['cursor'], 151)
        self.assertEqual(len(self.state()['requests']), 151)

    def test_listing_that_never_settles_is_offline_and_keeps_cursor(self):
        self.github.add(*(sync_comment(i, f'req-{i}', 1, 'd') for i in range(2, 152)))
        before = self.state()
        deleted = []

        def delete_every_time(page):
            if page == 1:
                deleted.append(self.github.remove_newest())
        self.github.after_call = delete_every_time
        result = self.poll()
        self.github.after_call = None
        self.assertEqual(deleted, [151, 150, 149])
        self.assertEqual(result['status'], 'OFFLINE')
        self.assertEqual(result['pages_read'], 6)
        self.assertEqual(self.github.pages(), [1, 2, 1, 2, 1, 2])
        self.assertEqual((result['failure_count'], result['backoff_seconds']), (1, 30))
        self.assertEqual(result['cursor'], 1)
        self.assertEqual(self.parser.seen, [])
        self.assertOnlyFailureCountChanged(before, 1)
        result = self.poll()
        self.assertEqual(result['status'], 'SYNCED')
        self.assertEqual(result['cursor'], 148)
        self.assertEqual(self.parser.seen, list(range(2, 149)))
        self.assertEqual(len(self.state()['requests']), 148)
        self.assertEqual(self.state()['failure_count'], 0)

    def test_unexpected_exception_is_not_treated_as_offline(self):
        before = self.snapshot()

        def broken(path):
            raise RuntimeError('programming error')
        with self.assertRaises(RuntimeError):
            sync.poll_once(self.path, broken, self.parser)
        self.assertEqual(self.snapshot(), before)


@unittest.skipIf(fcntl is None, 'flock is POSIX only; the module fails closed without it')
class LockTests(SyncTestCase):
    def test_each_call_takes_one_lock_of_its_kind(self):
        exclusive, shared, unlock = fcntl.LOCK_EX, fcntl.LOCK_SH, fcntl.LOCK_UN
        calls = []
        real = fcntl.flock

        def flock(fd, operation):
            calls.append(operation)
            return real(fd, operation)
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))

        def offline():
            self.github.fault = 'raise'
            try:
                return self.poll()
            finally:
                self.github.fault = None
        steps = (('init', lambda: sync.init_state(self.path, 0), exclusive, 'INITIALIZED'),
                 ('synced', self.poll, exclusive, 'SYNCED'),
                 ('idle', self.poll, exclusive, 'IDLE'),
                 ('offline', offline, exclusive, 'OFFLINE'),
                 ('reset', self.poll, exclusive, 'IDLE'),
                 ('link', lambda: sync.link_task(self.path, 'req-1', TASK_A), exclusive, 'LINKED'),
                 ('inert', lambda: sync.link_task(self.path, 'req-1', TASK_A), exclusive, 'INERT'),
                 ('pending', lambda: sync.pending(self.path), shared, None))
        with mock.patch.object(sync.fcntl, 'flock', new=flock):
            for name, func, operation, status in steps:
                with self.subTest(step=name):
                    calls.clear()
                    result = func()
                    if status is not None:
                        self.assertEqual(result['status'], status)
                    # One acquire and one release: _write never locks again.
                    self.assertEqual(calls, [operation, unlock])
        self.assertUnlocked()

    def test_lock_failure_fails_closed_and_nothing_runs_unlocked(self):
        sync.init_state(self.path, 0)
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.poll()
        self.github.calls.clear()
        self.parser.seen.clear()
        self.github.add(sync_comment(2, 'req-2', 1, 'd2'))
        before = self.snapshot()
        other = os.path.join(self.dir, 'other-state.json')
        failures = (('flock-error',
                     lambda: mock.patch.object(sync.fcntl, 'flock', side_effect=OSError('no lock'))),
                    ('no-fcntl', lambda: mock.patch.object(sync, 'fcntl', None)))
        for name, make_patch in failures:
            with self.subTest(failure=name):
                with make_patch():
                    self.assertCode('SYNC_STATE_LOCK_FAILED', self.poll)
                    # A lock failure is never reported as OFFLINE, even when GitHub is down.
                    self.github.fault = 'raise'
                    self.assertCode('SYNC_STATE_LOCK_FAILED', self.poll)
                    self.github.fault = None
                    self.assertCode('SYNC_STATE_LOCK_FAILED', sync.link_task,
                                    self.path, 'req-1', TASK_A)
                    self.assertCode('SYNC_STATE_LOCK_FAILED', sync.pending, self.path)
                    self.assertCode('SYNC_STATE_LOCK_FAILED', sync.init_state, other, 0)
                self.assertEqual(self.github.calls, [])
                self.assertEqual(self.parser.seen, [])
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(os.path.lexists(other))
                self.assertUnlocked()
        self.assertEqual(self.poll()['status'], 'SYNCED')
        self.assertEqual(self.state()['failure_count'], 0)

    def test_lock_is_released_after_every_return_and_exception(self):
        self.assertCode('SYNC_STATE_MISSING', self.poll)
        self.assertCode('SYNC_STATE_MISSING', sync.pending, self.path)
        self.assertUnlocked()
        self.assertEqual(sync.init_state(self.path, 0)['status'], 'INITIALIZED')
        self.assertUnlocked()
        self.assertCode('SYNC_STATE_EXISTS', sync.init_state, self.path, 0)
        self.assertUnlocked()
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.assertEqual(self.poll()['status'], 'SYNCED')
        self.assertUnlocked()
        self.assertEqual(self.poll()['status'], 'IDLE')
        self.assertUnlocked()
        self.assertEqual(sync.link_task(self.path, 'req-1', TASK_A)['status'], 'LINKED')
        self.assertUnlocked()
        self.assertEqual(sync.link_task(self.path, 'req-1', TASK_A)['status'], 'INERT')
        self.assertUnlocked()
        self.assertCode('SYNC_REQUEST_ALREADY_LINKED', sync.link_task, self.path, 'req-1', TASK_B)
        self.assertUnlocked()
        self.assertCode('SYNC_REQUEST_UNKNOWN', sync.link_task, self.path, 'req-9', TASK_A)
        self.assertUnlocked()
        self.assertEqual(sync.pending(self.path)['requests'], [])
        self.assertUnlocked()
        self.github.fault = 'raise'
        self.assertEqual(self.poll()['status'], 'OFFLINE')
        self.assertUnlocked()
        self.github.fault = None
        self.github.add(*(comment(i) for i in range(2, 252)))
        self.assertCode('SYNC_CATCHUP_LIMIT', self.poll, max_pages=2)
        self.assertUnlocked()

        def broken(path):
            raise RuntimeError('programming error')
        with self.assertRaises(RuntimeError):
            sync.poll_once(self.path, broken, self.parser)
        self.assertUnlocked()
        with mock.patch.object(sync.os, 'replace', side_effect=OSError('crash')):
            self.assertCode('SYNC_STATE_WRITE_FAILED', self.poll)
        self.assertUnlocked()
        with mock.patch.object(sync, 'MAX_REQUESTS', 1):
            self.github.add(sync_comment(252, 'req-2', 1, 'd2'))
            self.assertCode('SYNC_LEDGER_FULL', self.poll)
        self.assertUnlocked()
        os.chmod(self.path, 0o644)
        try:
            self.assertCode('SYNC_STATE_CORRUPT', self.poll)
            self.assertCode('SYNC_STATE_CORRUPT', sync.link_task, self.path, 'req-1', TASK_A)
            self.assertCode('SYNC_STATE_CORRUPT', sync.pending, self.path)
            self.assertUnlocked()
        finally:
            os.chmod(self.path, 0o600)
        self.assertEqual(self.poll()['status'], 'SYNCED')
        self.assertUnlocked()


@unittest.skipIf(fcntl is None, 'flock is POSIX only; the module fails closed without it')
class ContentionTests(SyncTestCase):
    """Competing calls in separate spawned processes, ordered by events, never sleeps."""

    def setUp(self):
        super().setUp()
        self.ctx = multiprocessing.get_context('spawn')
        self.release = self.ctx.Event()

    def seed(self):
        sync.init_state(self.path, 0)
        self.github.add(sync_comment(1, 'req-1', 1, 'd1'))
        self.assertEqual(self.poll()['status'], 'SYNCED')

    def start(self, **spec):
        report = self.ctx.Queue()
        process = self.ctx.Process(target=_child, args=(spec, self.path, report, self.release))
        process.start()
        self.addCleanup(self.stop, process)
        return process, report

    def stop(self, process):
        self.release.set()
        process.join(WAIT)
        if process.is_alive():
            process.kill()
            process.join(WAIT)

    def expect(self, child, tag):
        message = child[1].get(timeout=WAIT)
        self.assertEqual(message[0], tag, message)

    def finish(self, child):
        process, report = child
        message = report.get(timeout=WAIT)
        process.join(WAIT)
        self.assertEqual(process.exitcode, 0)
        return message

    @contextlib.contextmanager
    def held(self):
        """Hold the exclusive state lock from this test process."""
        fd = os.open(self.dir, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def assertExcluded(self):
        # A nonblocking competing attempt fails while the child holds the lock.
        self.assertFalse(self.can_lock(fcntl.LOCK_EX))
        self.assertFalse(self.can_lock(fcntl.LOCK_SH))

    def assertPollAndLinkBothKept(self, polled, linked):
        self.assertEqual(polled[0], 'RESULT', polled)
        self.assertEqual(polled[1]['status'], 'SYNCED')
        self.assertEqual(polled[1]['new_request_keys'], ['req-2'])
        self.assertEqual(linked, ('RESULT', {'status': 'LINKED', 'request_key': 'req-1',
                                             'task_id': TASK_A, 'state': 'LINKED'}))
        state = self.state()
        self.assertEqual(state['cursor'], 2)
        self.assertEqual((state['requests']['req-1']['state'],
                          state['requests']['req-1']['task_id']), ('LINKED', TASK_A))
        self.assertEqual(state['requests']['req-2']['state'], 'PENDING_PM')
        self.assertPrivate()
        self.assertUnlocked()

    def test_link_during_a_poll_keeps_both_updates(self):
        self.seed()
        comments = [sync_comment(1, 'req-1', 1, 'd1'), sync_comment(2, 'req-2', 1, 'd2')]
        poller = self.start(action='poll', comments=comments, pause='github')
        self.expect(poller, 'HOLDING')
        self.assertExcluded()
        linker = self.start(action='link', request_key='req-1', task_id=TASK_A, contend=True)
        self.expect(linker, 'CONTENDED')
        self.release.set()
        self.assertPollAndLinkBothKept(self.finish(poller), self.finish(linker))

    def test_poll_during_a_link_publish_keeps_both_updates(self):
        # The RCA window: the link passed its identity check and is about to replace.
        self.seed()
        comments = [sync_comment(1, 'req-1', 1, 'd1'), sync_comment(2, 'req-2', 1, 'd2')]
        linker = self.start(action='link', request_key='req-1', task_id=TASK_A, pause='replace')
        self.expect(linker, 'HOLDING')
        self.assertExcluded()
        poller = self.start(action='poll', comments=comments, contend=True)
        self.expect(poller, 'CONTENDED')
        self.release.set()
        linked = self.finish(linker)
        self.assertPollAndLinkBothKept(self.finish(poller), linked)

    def test_two_links_to_one_request_link_once(self):
        self.seed()
        with self.held():
            first = self.start(action='link', request_key='req-1', task_id=TASK_A, contend=True)
            second = self.start(action='link', request_key='req-1', task_id=TASK_B, contend=True)
            self.expect(first, 'CONTENDED')
            self.expect(second, 'CONTENDED')
        outcomes = [self.finish(first), self.finish(second)]
        self.assertIn(('ERROR', 'SYNC_REQUEST_ALREADY_LINKED'), outcomes)
        won = [value for tag, value in outcomes if tag == 'RESULT']
        self.assertEqual(len(won), 1, outcomes)
        self.assertEqual(won[0]['status'], 'LINKED')
        entry = self.state()['requests']['req-1']
        self.assertEqual((entry['state'], entry['task_id'], entry['linked_revision']),
                         ('LINKED', won[0]['task_id'], 1))
        self.assertUnlocked()

    def test_two_polls_are_serialized(self):
        self.seed()
        comments = [sync_comment(i, f'req-{i}', 1, f'd{i}') for i in (1, 2, 3)]
        with self.held():
            first = self.start(action='poll', comments=comments, contend=True)
            second = self.start(action='poll', comments=comments, contend=True)
            self.expect(first, 'CONTENDED')
            self.expect(second, 'CONTENDED')
        outcomes = [self.finish(first), self.finish(second)]
        self.assertEqual([tag for tag, _ in outcomes], ['RESULT', 'RESULT'], outcomes)
        results = sorted((value for _, value in outcomes), key=lambda item: item['status'])
        self.assertEqual([item['status'] for item in results], ['IDLE', 'SYNCED'])
        idle, synced = results
        self.assertEqual((synced['previous_cursor'], synced['cursor']), (1, 3))
        self.assertEqual(synced['new_request_keys'], ['req-2', 'req-3'])
        self.assertEqual((idle['previous_cursor'], idle['cursor']), (3, 3))
        state = self.state()
        self.assertEqual(state['cursor'], 3)
        self.assertEqual(sorted(state['requests']), ['req-1', 'req-2', 'req-3'])
        self.assertEqual(state['failure_count'], 0)
        self.assertUnlocked()

    def test_offline_and_reset_are_serialized(self):
        self.seed()
        self.github.fault = 'raise'
        self.poll()
        self.poll()
        before = self.state()
        self.assertEqual(before['failure_count'], 2)
        comments = [sync_comment(1, 'req-1', 1, 'd1')]
        with self.held():
            offline = self.start(action='poll', comments=comments, fault='raise', contend=True)
            reset = self.start(action='poll', comments=comments, contend=True)
            self.expect(offline, 'CONTENDED')
            self.expect(reset, 'CONTENDED')
        failed, idle = self.finish(offline), self.finish(reset)
        self.assertEqual((failed[0], failed[1]['status']), ('RESULT', 'OFFLINE'), failed)
        self.assertEqual((idle[0], idle[1]['status'], idle[1]['failure_count']),
                         ('RESULT', 'IDLE', 0), idle)
        final = self.state()
        # Either order is valid; neither update may be lost.
        pair = (failed[1]['failure_count'], final['failure_count'])
        self.assertIn(pair, ((3, 0), (1, 1)))
        self.assertEqual(failed[1]['backoff_seconds'], {3: 120, 1: 30}[pair[0]])
        final['failure_count'] = before['failure_count']
        self.assertEqual(final, before)
        self.assertUnlocked()

    def test_pending_never_sees_a_replace_window(self):
        self.seed()
        linker = self.start(action='link', request_key='req-1', task_id=TASK_A, pause='replace')
        self.expect(linker, 'HOLDING')
        self.assertExcluded()
        reader = self.start(action='pending', contend=True)
        self.expect(reader, 'CONTENDED')
        self.release.set()
        self.assertEqual(self.finish(linker)[1]['status'], 'LINKED')
        self.assertEqual(self.finish(reader), ('RESULT', {'cursor': 1, 'requests': []}))
        self.assertUnlocked()

    def test_pending_never_sees_the_init_link_window(self):
        initializer = self.start(action='init', start=7, pause='init_link')
        self.expect(initializer, 'HOLDING')
        # The new file has two names now; an unlocked reader would call it corrupt.
        self.assertEqual(os.lstat(self.path).st_nlink, 2)
        self.assertExcluded()
        reader = self.start(action='pending', contend=True)
        self.expect(reader, 'CONTENDED')
        self.release.set()
        self.assertEqual(self.finish(initializer),
                         ('RESULT', {'status': 'INITIALIZED', 'cursor': 7}))
        self.assertEqual(self.finish(reader), ('RESULT', {'cursor': 7, 'requests': []}))
        self.assertPrivate()
        self.assertUnlocked()

    def test_two_initializers_give_one_state(self):
        with self.held():
            first = self.start(action='init', start=5, contend=True)
            second = self.start(action='init', start=9, contend=True)
            self.expect(first, 'CONTENDED')
            self.expect(second, 'CONTENDED')
        outcomes = [self.finish(first), self.finish(second)]
        self.assertIn(('ERROR', 'SYNC_STATE_EXISTS'), outcomes)
        won = [value for tag, value in outcomes if tag == 'RESULT']
        self.assertEqual(len(won), 1, outcomes)
        self.assertIn(won[0], ({'status': 'INITIALIZED', 'cursor': 5},
                               {'status': 'INITIALIZED', 'cursor': 9}))
        self.assertEqual(self.state()['cursor'], won[0]['cursor'])
        self.assertPrivate()
        self.assertEqual(os.listdir(self.dir), [STATE_NAME])
        self.assertUnlocked()

    def test_pending_shares_its_lock_with_readers_only(self):
        self.seed()
        listed = {'cursor': 1, 'requests': [{'request_key': 'req-1', 'kind': 'request',
                                             'revision': 1, 'first_comment_id': 1,
                                             'comment_id': 1}]}
        reader = self.start(action='pending', pause='load')
        self.expect(reader, 'HOLDING')
        self.assertTrue(self.can_lock(fcntl.LOCK_SH))
        self.assertFalse(self.can_lock(fcntl.LOCK_EX))
        second = self.start(action='pending', contend=True)
        self.expect(second, 'FREE')
        # The second reader finishes while the first still holds its shared lock.
        self.assertEqual(self.finish(second), ('RESULT', listed))
        linker = self.start(action='link', request_key='req-1', task_id=TASK_A, contend=True)
        self.expect(linker, 'CONTENDED')
        self.release.set()
        self.assertEqual(self.finish(reader), ('RESULT', listed))
        self.assertEqual(self.finish(linker)[1]['status'], 'LINKED')
        self.assertEqual(sync.pending(self.path)['requests'], [])
        self.assertUnlocked()


if __name__ == '__main__':
    unittest.main()
