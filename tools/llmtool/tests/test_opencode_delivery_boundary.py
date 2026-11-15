"""#1502 WP-F PR 3: OpenCode prompt delivery boundary (design section 2.2 step 5).

The "OpenCode executable" is a local /bin/sh shim that execs a local
interpreter fixture script; no provider is ever called. Fixture output uses
fixed, non-secret values. Prompt-pipe faults are injected through an ``os``
proxy patched into the module under test only (its single ``os.write`` call
is the prompt write); every other ``os`` attribute is the real one. Dispatch
authority is injected. No production check is changed.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from llmtool import opencode_direct_developer as odd  # noqa: E402


PROMPT = 'Fixture prompt: append one short greeting line to notes.txt.\n' * 4
PAYLOAD = PROMPT.encode('utf-8')
SESSION = 'ses_fixture01'

FAKE_OPENCODE_SOURCE = '''\
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = %r

if sys.argv[1:] == ['--version']:
    sys.stdout.write('opencode v1.0.0\\n')
    sys.exit(0)
with open(os.path.join(HERE, 'mode.txt')) as handle:
    mode = handle.read().strip()
if mode == 'complete':
    data = sys.stdin.buffer.read()
    with open(os.path.join(HERE, 'received.bin'), 'wb') as handle:
        handle.write(data)
    for event in (
            {'type': 'step_start', 'sessionID': SESSION},
            {'type': 'text', 'sessionID': SESSION, 'part': {'text': 'fixture done'}},
            {'type': 'step_finish', 'sessionID': SESSION,
             'part': {'tokens': {'input': 10, 'output': 5}}}):
        sys.stdout.write(json.dumps(event) + '\\n')
    sys.exit(0)
if mode == 'hang':
    sys.stdin.buffer.read()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with open(os.path.join(HERE, 'beats.txt'), 'a') as handle:
            handle.write('.')
        time.sleep(0.05)
    sys.exit(0)
if mode == 'drain':
    sys.stdin.buffer.read()
    sys.exit(0)
if mode == 'flood':
    chunk = b'x' * 65536
    for _ in range(272):  # 17 MiB, above the 16 MiB stdout cap
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    time.sleep(30)
    sys.exit(0)
if mode == 'deny':
    sys.stdout.write(json.dumps({'type': 'permission.asked', 'sessionID': SESSION}) + '\\n')
    sys.stdout.flush()
    time.sleep(30)
    sys.exit(0)
time.sleep(30)  # silent: never reads stdin
sys.exit(0)
''' % SESSION


class _OsProxy:
    """Stands in for ``os`` inside the module under test; only write is scripted."""

    def __init__(self, plan):
        self.plan = plan
        self.calls = 0
        self.accepted = 0

    def __getattr__(self, name):
        return getattr(os, name)

    def write(self, fd, data):
        self.calls += 1
        if self.plan is None:
            written = os.write(fd, data)
        else:
            written = self.plan(self, fd, data)
        self.accepted += written
        return written


def _short_writes(proxy, fd, data):
    return os.write(fd, data[:7])


def _partial_then_broken_pipe(proxy, fd, data):
    if proxy.calls == 1:
        return os.write(fd, data[:10])
    raise BrokenPipeError(errno.EPIPE, 'fixture broken pipe')


def _pipe_full(proxy, fd, data):
    raise BlockingIOError(errno.EAGAIN, 'fixture pipe full')


class OpenCodeDeliveryBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='llmtool-delivery-opencode-test-'))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.root = self.work / 'd6'
        self.root.mkdir()
        (self.root / 'notes.txt').write_text('fixture\n', encoding='utf-8')
        self.scripts = self.work / 'scripts'
        self.scripts.mkdir()
        script = self.scripts / 'fake_opencode.py'
        script.write_text(FAKE_OPENCODE_SOURCE, encoding='utf-8')
        self.shim = self.scripts / 'opencode'
        self.shim.write_text('#!/bin/sh\nexec %s -I %s "$@"\n' % (
            shlex.quote(sys.executable), shlex.quote(str(script))), encoding='utf-8')
        os.chmod(self.shim, 0o700)
        dispatch = types.SimpleNamespace(
            authorize_dispatch=lambda *args, **kwargs: None,
            WorkerDispatchDenied=odd.worker_dispatch.WorkerDispatchDenied)
        patcher = mock.patch.object(odd, 'worker_dispatch', dispatch)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.launches = []
        self.deliveries = []
        self.proxy = None

    def _record_delivery(self):
        self.deliveries.append(self.proxy.accepted)

    def _run(self, mode, plan, *, timeout=30, delivered_callback='record'):
        (self.scripts / 'mode.txt').write_text(mode, encoding='utf-8')
        if delivered_callback == 'record':
            delivered_callback = self._record_delivery
        self.proxy = _OsProxy(plan)
        with mock.patch.object(odd, 'os', self.proxy):
            return odd.run(
                root=str(self.root), read_paths=['notes.txt'], write_paths=['notes.txt'],
                prompt=PROMPT, prelaunch_validate=lambda: True,
                dispatch_store=object(), dispatch_task_id='fixture-task',
                launch_callback=lambda *args: self.launches.append(args[0]),
                delivered_callback=delivered_callback,
                timeout_seconds=timeout, executable=str(self.shim))

    def _refusal(self, *args, **kwargs):
        with self.assertRaises(odd.DirectOpenCodeError) as caught:
            self._run(*args, **kwargs)
        return caught.exception

    def test_partial_write_then_broken_pipe_is_incomplete(self):
        exc = self._refusal('drain', _partial_then_broken_pipe)
        self.assertEqual(exc.direct_opencode_code, 'OPENCODE_PROMPT_DELIVERY_INCOMPLETE')
        self.assertIs(exc.attempted, True)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.deliveries, [])
        self.assertEqual(self.proxy.accepted, 10)
        self.assertLess(self.proxy.accepted, len(PAYLOAD))

    def test_completed_short_writes_fire_once(self):
        result = self._run('complete', _short_writes)
        self.assertIsInstance(result, odd.DirectResult)
        self.assertEqual(result.session_id, SESSION)
        self.assertGreater(self.proxy.calls, 1)
        # Fired once, only after every prompt byte was accepted.
        self.assertEqual(self.deliveries, [len(PAYLOAD)])
        self.assertEqual(len(self.launches), 1)
        self.assertEqual((self.scripts / 'received.bin').read_bytes(), PAYLOAD)

    def test_without_callback_behaviour_unchanged(self):
        result = self._run('complete', None, delivered_callback=None)
        self.assertIsInstance(result, odd.DirectResult)
        self.assertEqual(result.session_id, SESSION)
        self.assertEqual(result.text, 'fixture done')
        self.assertEqual((self.scripts / 'received.bin').read_bytes(), PAYLOAD)

    def test_timeout_before_stdin_finishes_no_callback(self):
        exc = self._refusal('silent', _pipe_full, timeout=1)
        self.assertEqual(exc.direct_opencode_code, 'OPENCODE_TIMEOUT')
        self.assertIs(exc.attempted, True)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.deliveries, [])
        self.assertEqual(self.proxy.accepted, 0)

    def test_output_cap_before_stdin_finishes_no_callback(self):
        exc = self._refusal('flood', _pipe_full)
        self.assertEqual(exc.direct_opencode_code, 'OPENCODE_OUTPUT_TOO_LARGE')
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.deliveries, [])
        self.assertEqual(self.proxy.accepted, 0)

    def test_tool_denied_before_stdin_finishes_no_callback(self):
        exc = self._refusal('deny', _pipe_full)
        self.assertEqual(exc.direct_opencode_code, 'TOOL_DENIED')
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.deliveries, [])
        self.assertEqual(self.proxy.accepted, 0)

    def test_raising_callback_stops_process_and_returns_no_result(self):
        def refuse():
            self._record_delivery()
            raise odd.DirectOpenCodeError('SKILL_DELIVERY_RECEIPT_MISSING')

        started = time.monotonic()
        exc = self._refusal('hang', None, delivered_callback=refuse)
        self.assertEqual(exc.direct_opencode_code, 'SKILL_DELIVERY_RECEIPT_MISSING')
        self.assertIs(exc.attempted, True)
        self.assertEqual(exc.cleanup_status, 'CONFIRMED')
        self.assertEqual(self.deliveries, [len(PAYLOAD)])
        self.assertLess(time.monotonic() - started, 20)
        beats = self.scripts / 'beats.txt'
        before = beats.stat().st_size if beats.exists() else 0
        time.sleep(0.5)
        after = beats.stat().st_size if beats.exists() else 0
        self.assertEqual(before, after)

    def test_callback_other_exception_gives_fixed_code(self):
        def broken():
            self._record_delivery()
            raise ValueError('fixture failure')

        exc = self._refusal('hang', None, delivered_callback=broken)
        self.assertEqual(exc.direct_opencode_code, 'OPENCODE_DELIVERY_CALLBACK_FAILED')
        self.assertEqual(exc.cleanup_status, 'CONFIRMED')
        self.assertEqual(self.deliveries, [len(PAYLOAD)])

    def test_non_callable_callback_refuses_before_launch(self):
        exc = self._refusal('complete', None, delivered_callback='not-callable')
        self.assertEqual(exc.direct_opencode_code, 'OPENCODE_DELIVERY_CALLBACK_INVALID')
        self.assertIs(exc.attempted, False)
        self.assertEqual(self.launches, [])
        self.assertEqual(self.proxy.calls, 0)


if __name__ == '__main__':
    unittest.main()
