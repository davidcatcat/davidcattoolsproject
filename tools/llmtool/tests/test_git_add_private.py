"""F1 frame/post-state tests and native disposable-fixture integration.

Only the outer runner executes these tests. Mocks never prove Mac containment.
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from llmtool import action_contract as c
from llmtool import git_mutation as g
from llmtool import _git_add_private as p
from llmtool import _git_add_host as host
from llmtool import _git_containment_protocol as wire
from llmtool import _git_process_identity as kernel
from llmtool.errors import LLMToolError

_VENV = Path(__file__).resolve().parents[1]/'.venv/bin/python'
PYTHON = str(_VENV) if _VENV.exists() else sys.executable


def sample_action(char='a', path_bytes=767, target_bytes=None):
    # Legal Action letters, three <=255-byte components for actual files.
    width = len(char.encode('utf-8'))
    paths = []
    for i in range(32):
        remaining = path_bytes-4  # two slashes + two ASCII identity digits
        components = []
        for j in range(3):
            # Reserve a byte for each later component even in short frames;
            # otherwise the default request ends in two empty path components.
            budget = min(remaining-(2-j), 253 if j == 0 else 255)
            text = char*(budget//width)+'a'*(budget % width)
            components.append(('{:02d}'.format(i) if j == 0 else '')+text)
            remaining -= budget
        # Larger Unicode-frame cases can use a fourth component (Action cap
        # is per path, not per POSIX component).
        if remaining:
            components.append(char*(remaining//width)+'a'*(remaining % width))
        paths.append('/'.join(components))
    paths.sort()
    def build(device=1, root='/tmp/r'):
        md = c.FileMetadata(device, 1, 0o644, 1, 1, 1, 1, 1)
        files = tuple(c.FileExpectation(path, c.FileState(True, c.FileType.REGULAR,
                     '4'*64, md)) for path in paths)
        return c.Action(1, 'action-1', 100, c.ActionKind.GIT_OPERATION,
            c.TaskBinding('task-1', '1'*32, 1, '2'*64),
            c.RepositoryBinding('3'*64, root, 1, 1, '5'*40), paths,
            c.GitAddPayload(c.GitOperation.ADD, files, '6'*64))
    action = build()
    if target_bytes is not None:
        # Pad metadata first, then root, without narrowing any Action limit.
        for digits in range(1, 19):
            candidate = build(10**digits-1)
            delta = target_bytes-len(candidate.to_json().encode('utf-8'))
            if 0 <= delta <= 1000:
                return build(10**digits-1, '/tmp/r'+'r'*delta)
        raise AssertionError('requested Action byte size is unreachable')
    return action


def request(action=None):
    action = action or sample_action(path_bytes=30)
    return {'domain': p.DOMAIN, 'version': 1, 'nonce': 'a'*64,
        'parent': {'pid': 1, 'ppid': 1, 'pgid': 1, 'sid': 1, 'uid': 1,
                   'seconds': 1, 'micros': 0}, 'runtime': {}, 'fixture': {},
        'action': action.to_dict(), 'action_digest': action.digest,
        'preflight_digest': 'b'*64, 'timeout': 1, 'pipe': [1, 1]}


class FrameTests(unittest.TestCase):
    def test_legitimate_exact_57430_ascii_action_roundtrip(self):
        action = sample_action(target_bytes=57430)
        self.assertEqual(len(action.to_json().encode('utf-8')), 57430)
        self.assertEqual(len(action.scope), 32)
        value = request(action)
        raw = p.encode_frame(value)
        decoded = wire.strict_load(raw[4:])
        self.assertEqual(p.validate_frame(decoded), action)
        self.assertIsInstance(decoded['action'], dict)
        self.assertEqual(raw[4:], p.canonical(decoded))

    def test_near_limit_cjk_and_supplementary_cjk_are_utf8_letters(self):
        for char in ('測', '𠀀'):
            with self.subTest(char=char):
                action = sample_action(char, path_bytes=850, target_bytes=63000)
                value = request(action)
                raw = p.encode_frame(value)
                self.assertIn(char.encode('utf-8'), raw)
                self.assertNotIn(b'\\u', raw)
                self.assertEqual(p.validate_frame(wire.strict_load(raw[4:])), action)
                escaped = json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode('ascii')
                self.assertGreater(len(escaped), c.JSON_LIMIT)
                self.assertEqual(c.parse_action(action.to_json()), action)

    def test_existing_emoji_grammar_is_unchanged(self):
        with self.assertRaises(LLMToolError):
            c.FileExpectation('😀.txt', sample_action().payload.files[0].state)
        self.assertEqual((c.MAX_FILES, c.MAX_PATH_BYTES, c.JSON_LIMIT), (32, 1024, 65536))

    def test_independent_action_metadata_and_total_caps(self):
        value = request()
        value['runtime'] = {'oversize': 'r'*p.BINDING_LIMIT}
        with self.assertRaises(p.AdmissionRejected):
            p.encode_frame(value)
        value = request()
        value['action']['scope'] = ['a'*c.JSON_LIMIT]
        with self.assertRaises(p.AdmissionRejected):
            p.encode_frame(value)
        with self.assertRaises(p.AdmissionRejected):
            p.canonical({'x': 'r'*p.LIMIT})
        self.assertEqual(len(p.canonical({'x': 'r'*(p.LIMIT-8)})), p.LIMIT)
        for size in (0, p.LIMIT+1, 2**32-1):
            r, w = os.pipe()
            try:
                os.write(w, struct.pack('>I', size))
                with self.assertRaises(p.AdmissionRejected):
                    p.Reader(r).receive(time.monotonic()+.1)
            finally:
                os.close(r); os.close(w)

    def test_wrong_domain_digest_unknown_fields_and_action_string(self):
        for key, bad in (('domain', 'A11b.2.2.1/fake-only/action/v1'),
                         ('action_digest', '0'*64), ('action', '{}'), ('version', True),
                         ('timeout', float('nan'))):
            value = request()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises((p.AdmissionRejected, wire.ProtocolFailure)):
                p.encode_frame(value)
        value = request(); value['extra'] = 1
        with self.assertRaises(p.AdmissionRejected):
            p.encode_frame(value)

    def test_duplicate_invalid_utf8_depth_noncanonical_and_early_eof(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":"\xff"}', b'['*9+b'0'+b']'*9,
                    b'{ "x":1}', b'{"x":NaN}'):
            r, w = os.pipe()
            try:
                os.write(w, struct.pack('>I', len(raw))+raw)
                os.close(w); w = None
                with self.assertRaises((p.AdmissionRejected, wire.ProtocolFailure)):
                    p.Reader(r).receive(time.monotonic()+.1)
            finally:
                os.close(r)
                if w is not None: os.close(w)
        for raw in (b'\0\0', struct.pack('>I', 10)+b'{}'):
            r, w = os.pipe()
            os.write(w, raw); os.close(w)
            try:
                with self.assertRaises(p.AdmissionRejected):
                    p.Reader(r).receive(time.monotonic()+.1)
            finally:
                os.close(r)

    def test_trailing_frame_held_writer_deadline_and_epipe(self):
        for trailing in (b'x', p.encode_frame(request())):
            r, w = os.pipe()
            try:
                os.write(w, b'\0\0\0\2{}'+trailing[:1])
                reader = p.Reader(r)
                self.assertEqual(reader.receive(time.monotonic()+.1), {})
                with self.assertRaises(p.AdmissionRejected):
                    reader.eof(time.monotonic()+.1)
            finally:
                os.close(r); os.close(w)
        r, w = os.pipe()
        try:
            with self.assertRaises(TimeoutError):
                p.Reader(r).receive(time.monotonic()+.02)
            with self.assertRaises(TimeoutError):
                p.write_bytes(w, b'x'*p.LIMIT, time.monotonic()+.02)
        finally:
            os.close(r); os.close(w)
        r, w = os.pipe(); os.close(r)
        try:
            with self.assertRaises(BrokenPipeError):
                p.write_bytes(w, b'x', time.monotonic()+.1)
        finally:
            os.close(w)

    def test_larger_than_pipe_buffer_streams_without_thread_or_deadlock(self):
        # Isolated test-only receiver has no Git or host admission authority.
        value = request(sample_action(target_bytes=57430))
        r, w = os.pipe()
        child = subprocess.Popen([PYTHON, '-I', '-c',
            'import os,time; from llmtool import _git_add_private as p; '
            'fd=int(os.environ["TEST_READ_FD"]); q=p.Reader(fd); '
            'v=q.receive(time.monotonic()+2); q.eof(time.monotonic()+2); '
            'print(len(p.canonical(v["action"])))'], close_fds=True, pass_fds=(r,),
            env={'TEST_READ_FD': str(r), 'PATH': '/usr/bin:/bin'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(r)
        try:
            p.write_bytes(w, p.encode_frame(value), time.monotonic()+2)
            os.close(w); w = None
            out, err = child.communicate(timeout=3)
            self.assertEqual((child.returncode, out, err), (0, b'57430\n', b''))
        finally:
            if w is not None: os.close(w)
            if child.returncode is None:
                child.kill(); child.wait()


class SemanticTests(unittest.TestCase):
    def test_all_first_match_rows_and_noop(self):
        pre, expected, other = ('pre',), ('expected',), ('other',)
        for contained, safe_index, safe_repo, post, code, interrupt, result in (
            (False, True, True, expected, 0, False, 'STATE_UNKNOWN'),
            (True, False, True, expected, 0, False, 'USER_ACTION_REQUIRED'),
            (True, True, False, expected, 0, False, 'USER_ACTION_REQUIRED'),
            (True, True, True, expected, 0, False, 'APPLIED'),
            (True, True, True, expected, 1, False, 'APPLIED_BUT_CONTROL_INTERRUPTED'),
            (True, True, True, expected, 0, True, 'APPLIED_BUT_CONTROL_INTERRUPTED'),
            (True, True, True, expected, None, False, 'APPLIED_BUT_CONTROL_INTERRUPTED'),
            (True, True, True, pre, 0, False, 'NOT_APPLIED'),
            (True, True, True, pre, 1, True, 'NOT_APPLIED'),
            (True, True, True, other, 0, False, 'USER_ACTION_REQUIRED')):
            with self.subTest(result=result, code=code):
                self.assertEqual(p.classify(pre, expected, post, contained=contained,
                    safe_index=safe_index, safe_repo=safe_repo, exit_code=code,
                    interrupted=interrupt), result)
        for code in (0, 1, None):
            self.assertEqual(p.classify(pre, pre, pre, contained=True,
                exit_code=code, interrupted=False),
                'APPLIED' if code == 0 else 'APPLIED_BUT_CONTROL_INTERRUPTED')


@unittest.skipUnless(sys.platform == 'darwin' and os.geteuid() != 0, 'native Mac required')
class NativeFixtureTests(unittest.TestCase):
    def test_natural_leader_exit_descendant_cleanup_permanent_regression(self):
        from test_git_containment import AcceptanceScenarios
        scenarios = AcceptanceScenarios()
        self.assertEqual(scenarios.leader_exit_descendant(), [])
        self.assertGreater(scenarios.checks, 0)

    def test_private_launcher_tracked_new_and_noop(self):
        for changes in ({'target.txt': b'change'}, {'new.txt': b'new'}, {'target.txt': b'original'}):
            with self.subTest(paths=tuple(changes)):
                script = ('import json; from llmtool import _git_add_private as p; '
                    'print(json.dumps(p.run_fixture_add('
                    '{"target.txt":b"original","other.txt":b"other"}, '+repr(changes)+')))')
                raw = subprocess.check_output([PYTHON, '-I', '-c', script],
                    stderr=subprocess.DEVNULL, timeout=20)
                result = json.loads(raw)
                self.assertEqual(result['outcome'], 'APPLIED')
                self.assertTrue(result['containment_confirmed'])
                self.assertEqual(result['no_op'], changes == {'target.txt': b'original'})
                self.assertEqual(len(result['fixture_provenance_digest']), 64)

    def test_real_post_verifier_preserves_unrelated_work_and_checks_lock_ref(self):
        root, action, fixture, nonce = p._make_fixture(
            {'target.txt': b'old', 'other.txt': b'original'}, {'target.txt': b'new'})
        self.addCleanup(shutil.rmtree, root)
        repo = Path(action.repository.canonical_root)
        (repo/'other.txt').write_bytes(b'unstaged')
        (repo/'untracked.txt').write_bytes(b'untracked')
        plan = g.preflight_git_add(action)
        # Fixture-only setup stages the exact paths; engine lifecycle is tested
        # above. The verifier is independently assessed against real Git state.
        p._fixture_git(repo, p._Setup.BASELINE, tuple(action.scope))
        result = p.post_state(action, plan, exit_code=0, interrupted=False)
        self.assertEqual(result['outcome'], 'APPLIED')
        self.assertEqual((repo/'other.txt').read_bytes(), b'unstaged')
        self.assertEqual((repo/'untracked.txt').read_bytes(), b'untracked')
        lock = repo/'.git/index.lock'; lock.write_bytes(b'preserve')
        self.assertEqual(p.post_state(action, plan, exit_code=0, interrupted=False)['outcome'], 'USER_ACTION_REQUIRED')
        self.assertEqual(lock.read_bytes(), b'preserve')

    def test_post_index_change_hook_never_executes(self):
        root, action, fixture, nonce = p._make_fixture({'target.txt': b'old'}, {'target.txt': b'new'})
        self.addCleanup(shutil.rmtree, root)
        repo = Path(action.repository.canonical_root)
        hooks = repo/'.git/hooks'; hooks.mkdir()
        hook = hooks/'post-index-change'
        hook.write_text('#!/bin/sh\ntouch "'+str(repo/'helper-marker')+'"\n')
        hook.chmod(0o700)
        # Fresh preflight admits on-disk hooks only because hooksPath is pinned
        # /dev/null in every query and ADD. The sentinel proves suppression.
        p.executable_surface(action)
        plan = g.preflight_git_add(action)
        subprocess.check_call(plan.future_add_argv, cwd=repo,
            env=dict(plan.future_environment), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=2)
        self.assertFalse((repo/'helper-marker').exists())

    def test_unknown_notice_epipe_holds_past_budget_no_reap_or_release(self):
        budget, margin = .05, .15
        for dropped in (False, True):
            with self.subTest(dropped_notice=dropped):
                script = ('import os; from llmtool import _git_add_host as h; '
                    + ('os.close(1); ' if dropped else '')
                    + 'h._unknown({"bound":None}, '
                    '{"action_digest":"a"*64,"fixture":{}}, RuntimeError("private"))')
                child = subprocess.Popen([PYTHON, '-I', '-c', script], close_fds=True,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                incident = None
                try:
                    if not dropped:
                        incident = p.Reader(child.stdout.fileno()).receive(time.monotonic()+2)
                        self.assertEqual(incident['outcome'], 'STATE_UNKNOWN')
                        self.assertFalse(incident['containment_confirmed'])
                    with self.assertRaises(subprocess.TimeoutExpired):
                        child.wait(timeout=budget+margin)
                    # Test-owned host only: terminating it does not amend UNKNOWN.
                    child.kill(); child.wait(timeout=2)
                    self.assertEqual(child.returncode, -signal.SIGKILL)
                    if incident is not None:
                        self.assertEqual(incident['outcome'], 'STATE_UNKNOWN')
                finally:
                    if child.returncode is None:
                        child.kill(); child.wait(timeout=2)
                    child.stdout.close()


if __name__ == '__main__':
    unittest.main()
