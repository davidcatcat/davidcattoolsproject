"""Admission, target provenance, helper denial and retained ownership regressions."""
import ast
from dataclasses import replace
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from llmtool import _git_add_private as p
from llmtool import _git_add_host as host
from llmtool import _git_containment_protocol as wire
from llmtool import _git_process_identity as kernel
from llmtool import _git_supervisor as supervisor
from llmtool import git_mutation as g
from test_git_add_private import PYTHON, request


def fds():
    live = set()
    for name in os.listdir('/dev/fd'):
        try:
            fd = int(name); os.fstat(fd)
        except (ValueError, OSError):
            continue
        live.add(fd)
    return live


class AdmissionTests(unittest.TestCase):
    def test_product_manifest_excludes_all_fake_artifacts(self):
        self.assertFalse(any('tests/' in name or 'child' in name or name == '_git_containment.py'
                             for name in p.ARTIFACTS))
        try:
            binding = p.runtime()
        except wire.ProtocolFailure:
            # Keep the rejection (no skip, no weaker file_hash); report only
            # which trusted-runtime predicate the interpreter metadata trips.
            st = os.stat(os.path.realpath(sys.executable))
            self.fail('interpreter rejected: regular={} mode={:o} group_other_write={} '
                      'uid_is_euid={} uid_is_root={} size_over_64MiB={}'.format(
                          stat.S_ISREG(st.st_mode), stat.S_IMODE(st.st_mode),
                          bool(st.st_mode & 0o022), st.st_uid == os.geteuid(),
                          st.st_uid == 0, st.st_size > 64*1024*1024))
        self.assertEqual(set(binding['manifest']), set(p.ARTIFACTS))
        p.verify_runtime(binding)
        binding['manifest']['_git_add_host.py'] = '0'*64
        with self.assertRaises(p.AdmissionRejected):
            p.verify_runtime(binding)

    def test_direct_isolated_module_no_fd_refuses(self):
        result = subprocess.run([PYTHON, '-I', '-m', 'llmtool._git_add_host'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, timeout=3)
        self.assertEqual(result.returncode, 78)
        self.assertNotIn(b'PRIVATE', result.stdout+result.stderr)

    # endpoints() checks the exact FD inventory of the *current* process, as the
    # freshly spawned Host does. Run it in a clean child (close_fds + pass_fds)
    # so FDs left open by unrelated tests in this interpreter cannot decide it.
    ENDPOINTS_CHILD = (
        'import json, os, sys\n'
        'from llmtool import _git_add_private as p\n'
        'spec = json.loads(sys.argv[1])\n'
        'if spec["extra_dir"]:\n'
        '    os.open("/", os.O_RDONLY | os.O_DIRECTORY)\n'
        'expected = {int(fd): getattr(os, mode) for fd, mode in spec["expected"].items()}\n'
        'try:\n'
        '    p.endpoints(expected)\n'
        'except p.AdmissionRejected:\n'
        '    print("REJECTED")\n'
        'else:\n'
        '    print("ACCEPTED", all(not os.get_inheritable(fd) for fd in expected))\n')

    def endpoints_in_child(self, passed, expected, extra_dir=False):
        spec = {'expected': {str(fd): mode for fd, mode in expected.items()},
                'extra_dir': extra_dir}
        result = subprocess.run(
            [PYTHON, '-I', '-c', self.ENDPOINTS_CHILD, json.dumps(spec)],
            pass_fds=passed, close_fds=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode().strip()

    def test_fixed_fifo_requires_read_access_and_exact_inventory(self):
        r, w = os.pipe()
        try:
            both = (r, w)
            self.assertEqual(self.endpoints_in_child(
                both, {r: 'O_RDONLY', w: 'O_WRONLY'}), 'ACCEPTED True')
            self.assertEqual(self.endpoints_in_child(
                both, {r: 'O_RDONLY'}), 'REJECTED')
            self.assertEqual(self.endpoints_in_child(
                both, {r: 'O_WRONLY', w: 'O_RDONLY'}), 'REJECTED')
            self.assertEqual(self.endpoints_in_child(
                both, {r: 'O_RDONLY', w: 'O_WRONLY'}, extra_dir=True), 'REJECTED')
        finally:
            os.close(r); os.close(w)
        with tempfile.TemporaryFile() as regular:
            self.assertEqual(self.endpoints_in_child(
                (regular.fileno(),), {regular.fileno(): 'O_RDONLY'}), 'REJECTED')
        with socket.socket() as sock:
            self.assertEqual(self.endpoints_in_child(
                (sock.fileno(),), {sock.fileno(): 'O_RDONLY'}), 'REJECTED')

    def test_absent_file_socket_write_pipe_and_extra_fd_refuse_host(self):
        # Test process controls its own FD3 before invoking actual Host code;
        # product still requires -I/-m/main origin. No repo or Git is present.
        for setup in ('f=open(os.devnull,"rb"); os.dup2(f.fileno(),3)',
                      'import socket; f=socket.socket(); os.dup2(f.fileno(),3)',
                      'r,w=os.pipe(); os.dup2(w,3)',
                      'r,w=os.pipe(); os.dup2(r,3); extra=os.dup(w)'):
            script = ('import os,sys; '+setup+'; '
                'from llmtool import _git_add_host as h; '
                'from llmtool import _git_add_private as p; '
                'p.standalone=lambda _:None; sys.exit(h._main())')
            result = subprocess.run([PYTHON, '-I', '-c', script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            self.assertEqual(result.returncode, 78)

    def test_parent_kernel_identity_mismatch_refuses_before_execute(self):
        value = request()
        value['parent']['uid'] = os.geteuid()
        observed = kernel.Identity.parse(value['parent'])
        for key in ('pid', 'uid', 'seconds', 'micros'):
            changed = replace(observed, **{key: getattr(observed, key)+1})
            reader = Mock(); reader.receive.return_value = value
            with self.subTest(key=key), patch.object(p, 'standalone'), \
                    patch.object(p, 'endpoints'), patch.object(p, 'Reader', return_value=reader), \
                    patch.object(host.os, 'fstat', return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                    patch.object(host.os, 'close'), patch.object(kernel, 'identity', return_value=changed), \
                    patch.object(host, '_execute') as execute, patch.object(host, '_emit') as emit:
                self.assertEqual(host._main(), 78)
                execute.assert_not_called()
                self.assertEqual(emit.call_args.args[0]['outcome'], 'AUTHORITY_REJECTED')

    def test_old_frame_on_another_fifo_refuses_before_parent_or_git(self):
        value = request(); value['pipe'] = [1, 999]
        reader = Mock(); reader.receive.return_value = value
        with patch.object(p, 'standalone'), patch.object(p, 'endpoints'), \
                patch.object(p, 'Reader', return_value=reader), \
                patch.object(host.os, 'fstat', return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                patch.object(host.os, 'close'), patch.object(host, '_emit'), \
                patch.object(kernel, 'identity') as observe, patch.object(host, '_execute') as execute:
            self.assertEqual(host._main(), 78)
            observe.assert_not_called()
            execute.assert_not_called()

    def test_import_runpy_and_public_routes_do_not_reach_engine(self):
        private = set(p.ARTIFACTS) | {'_git_containment.py', '_git_containment_child.py'}
        markers = ('_git_add_private', '_git_add_host', '_git_supervisor', '_git_containment')
        package = Path(p.__file__).resolve().parent
        checked = 0
        for source in package.rglob('*.py'):
            if source.parent == package and source.name in private:
                continue
            text = source.read_text()
            tree = ast.parse(text)
            self.assertFalse(any(marker in text for marker in markers), str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    def literal(item):
                        if isinstance(item, ast.Constant) and isinstance(item.value, str):
                            return item.value
                        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Add):
                            a, b = literal(item.left), literal(item.right)
                            if a is not None and b is not None: return a+b
                        return None
                    combined = literal(node)
                    self.assertFalse(combined and any(m in combined for m in markers))
            checked += 1
        self.assertGreater(checked, 0)
        with patch.object(host, '_execute') as execute:
            self.assertEqual(host._main(), 78)  # importing is not standalone admission
            execute.assert_not_called()

    def test_repeated_raw_open_fdopen_directory_failure_does_not_leak(self):
        with tempfile.TemporaryDirectory(prefix='llmtool-fd-test-') as directory:
            directory = os.path.realpath(directory)
            before = fds()
            for _ in range(100):
                with self.assertRaises(IsADirectoryError):
                    wire.file_hash(directory)
                self.assertEqual(fds(), before)
            error = ValueError('original private error')
            real_close = os.close
            def close_then_fail(fd):
                real_close(fd)
                raise OSError(errno.EIO, 'secondary private error')
            with patch.object(wire.os, 'fdopen', side_effect=error), \
                    patch.object(wire.os, 'close', side_effect=close_then_fail):
                with self.assertRaises(ValueError) as caught:
                    wire.file_hash(directory)
            self.assertIs(caught.exception, error)
            self.assertEqual(fds(), before)

    def test_strict_host_premises_reject_custom_reaper_threads_trace(self):
        with patch.object(kernel.signal, 'getsignal', return_value=signal.SIG_IGN):
            with self.assertRaises(kernel.NotSupported): kernel.host_check()
        with patch.object(kernel.threading, 'active_count', return_value=2):
            with self.assertRaises(kernel.NotSupported): kernel.host_check()
        with patch.object(p.sys, 'gettrace', return_value=object()):
            with self.assertRaises(kernel.NotSupported): p.standalone('_git_add_host')


class RetentionTests(unittest.TestCase):
    def test_uncertain_anchor_retains_owned_resources_no_reap_or_restore(self):
        value = request()
        # Popen is mocked, but evaluating its cwd still requires a root.
        value['fixture']['root'] = '/unused-disposable-fixture'
        owner = kernel.Identity(123, 123, 123, os.geteuid(), os.getpid(), 1, 1)
        process, anchor, note, signals = Mock(), Mock(), Mock(), Mock()
        process.pid = 123
        anchor.kill_group.side_effect = kernel.IdentityUnknown('kernel identity changed')
        held = []
        class Retained(BaseException): pass
        def retain(ownership, request, error):
            held.append(ownership)
            for fd in ownership['fds']:
                self.addCleanup(os.close, fd)  # Test-owned anonymous pipes only.
            raise Retained()
        with patch.object(host, 'Signals', return_value=signals), \
                patch.object(host.subprocess, 'Popen', return_value=process), \
                patch.object(kernel, 'identity', return_value=owner), \
                patch.object(kernel, 'Anchor', return_value=anchor), \
                patch.object(kernel, 'ExitNote', return_value=note), \
                patch.object(p, 'send', side_effect=BrokenPipeError()), \
                patch.object(p, 'post_state') as post_state, \
                patch.object(host, '_unknown', side_effect=retain):
            signals.first = None
            with self.assertRaises(Retained): host._execute(value, None, None)
        post_state.assert_not_called()
        anchor.kill_group.assert_called_once()
        anchor.reap.assert_not_called()
        process.wait.assert_not_called()
        note.close.assert_not_called()
        signals.restore.assert_not_called()
        self.assertIs(held[0]['process'], process)
        self.assertIsInstance(held[0]['primary'], BrokenPipeError)
        self.assertEqual(len(held[0]['fds']), 2)

    def test_escape_is_unknown_without_numeric_signal(self):
        own = kernel.Identity(123, 123, 123, 1, 2, 1, 1)
        child = Mock(pid=124, returncode=None)
        escaped = kernel.Identity(124, 999, 999, 1, 123, 1, 1)
        with patch.object(kernel.os, 'getpid', return_value=123), \
                patch.object(kernel, 'identity', return_value=escaped), \
                patch.object(kernel.os, 'killpg') as kill:
            with self.assertRaises(kernel.IdentityUnknown):
                kernel.owned_git_identity(child, own)
            kill.assert_not_called()


@unittest.skipUnless(sys.platform == 'darwin' and os.geteuid() != 0, 'native Mac required')
class FixtureAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root, self.action, self.fixture, self.nonce = p._make_fixture(
            {'target.txt': b'old'}, {'target.txt': b'new'})
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = Path(self.action.repository.canonical_root)

    def bound_request(self):
        return {'domain': p.DOMAIN, 'version': 1, 'nonce': self.nonce,
            'parent': kernel.identity(os.getpid()).data(), 'runtime': p.runtime(),
            'fixture': self.fixture, 'action': self.action.to_dict(),
            'action_digest': self.action.digest,
            'preflight_digest': p.preflight_digest(g.preflight_git_add(self.action)),
            'timeout': 1, 'pipe': [1, 1]}

    def test_verified_fresh_fixture_and_missing_wrong_replayed_marker(self):
        p.fixture_check(self.fixture, self.action, self.nonce)
        for nonce in ('0'*64, '1'*64):
            with self.assertRaises(p.AdmissionRejected):
                p.fixture_check(self.fixture, self.action, nonce)
        marker = self.root/p.MARKER
        marker.chmod(0o644)
        with self.assertRaises(p.AdmissionRejected):
            p.fixture_check(self.fixture, self.action, self.nonce)
        marker.unlink()
        with self.assertRaises(p.AdmissionRejected):
            p.fixture_check(self.fixture, self.action, self.nonce)

    def test_marker_recreated_after_git_init_is_not_provenance(self):
        marker = self.root/p.MARKER
        marker.unlink(); marker.write_text(self.nonce); marker.chmod(0o600)
        forged = dict(self.fixture, marker=list(g._fingerprint(marker.stat())))
        with self.assertRaises(p.AdmissionRejected):
            p.fixture_check(forged, self.action, self.nonce)

    def test_root_inode_drift_canonical_outside_and_symlink_git_refuse(self):
        for forged in (dict(self.fixture, inode=self.fixture['inode']+1),
                       dict(self.fixture, root=p.CANONICAL),
                       dict(self.fixture, root=str(self.root.parent))):
            with self.assertRaises(p.AdmissionRejected):
                p.fixture_check(forged, self.action, self.nonce)
        original = self.repo/'.git'; moved = self.root/'saved-git'
        original.rename(moved); original.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(p.AdmissionRejected):
            p.fixture_check(self.fixture, self.action, self.nonce)

    def test_marker_symlink_refuses(self):
        marker = self.root/p.MARKER
        saved = self.root/'saved-marker'
        marker.rename(saved); marker.symlink_to(saved)
        with self.assertRaises(p.AdmissionRejected):
            p.fixture_check(self.fixture, self.action, self.nonce)

    def test_toplevel_mismatch_reaches_query_and_refuses(self):
        # Fresh, untouched fixture from setUp: every earlier provenance check
        # must pass, so only the patched top-level answer can cause refusal.
        p.fixture_check(self.fixture, self.action, self.nonce)
        reads = []
        def wrong_toplevel(reads_self, kind):
            reads.append(kind)
            return b'/wrong\n'
        with patch.object(g._Reads, 'query', wrong_toplevel):
            with self.assertRaises(p.AdmissionRejected):
                p.fixture_check(self.fixture, self.action, self.nonce)
        self.assertEqual(reads, [g._Read.TOPLEVEL])

    def test_malicious_helpers_includes_and_attributes_refuse_before_spawn(self):
        config = self.repo/'.git/config'
        baseline = config.read_bytes()
        for addition in (b'\n[filter "x"]\n clean = echo PRIVATE\n',
                         b'\n[filter "x"]\n process = echo PRIVATE\n',
                         b'\n[core]\n fsmonitor = PRIVATE\n',
                         b'\n[core]\n hooksPath = PRIVATE\n',
                         b'\n[includeIf "gitdir:*"]\n path = PRIVATE\n',
                         b'\n[credential]\n helper = PRIVATE\n',
                         b'\n[core]\n alternateRefsCommand = PRIVATE\n',
                         b'\n[core]\n attributesFile = PRIVATE\n',
                         b'\n[gpg]\n program = PRIVATE\n',
                         b'\n[core]\n sshCommand = PRIVATE\n'):
            config.write_bytes(baseline+addition)
            with self.subTest(addition=addition), self.assertRaises((p.AdmissionRejected, g.GitPreflightError)):
                p.executable_surface(self.action)
        config.write_bytes(baseline)
        for path in (self.repo/'.gitattributes', self.repo/'.git/info/attributes'):
            path.write_text('* filter=evil\n')
            with self.assertRaises((p.AdmissionRejected, g.GitPreflightError)):
                p.executable_surface(self.action)
            path.unlink()

    def test_ambient_config_and_helper_variables_are_not_reachable(self):
        with patch.dict(os.environ, {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.fsmonitor',
                'GIT_CONFIG_VALUE_0': 'PRIVATE', 'GIT_SSH_COMMAND': 'PRIVATE',
                'GIT_ASKPASS': 'PRIVATE', 'HOME': '/PRIVATE', 'PATH': '/PRIVATE'}):
            plan = g.preflight_git_add(self.action)
            env = dict(plan.future_environment)
            self.assertNotIn('PRIVATE', repr(env))
            self.assertEqual(env['PATH'], '/usr/bin:/bin')
            self.assertNotIn('GIT_CONFIG_COUNT', env)
            p.executable_surface(self.action)

    def test_final_preflight_digest_and_lock_refuse(self):
        plan = g.preflight_git_add(self.action)
        value = {'domain': p.DOMAIN, 'version': 1, 'nonce': self.nonce,
            'parent': kernel.identity(os.getpid()).data(), 'runtime': p.runtime(),
            'fixture': self.fixture, 'action': self.action.to_dict(),
            'action_digest': self.action.digest, 'preflight_digest': p.preflight_digest(plan),
            'timeout': 1, 'pipe': [1, 1]}
        p.verify_request(value)
        for bad in ('0'*64, '1'*64):
            wrong = dict(value, preflight_digest=bad)
            with self.assertRaises(p.AdmissionRejected): p.verify_request(wrong)
        lock = self.repo/'.git/index.lock'; lock.write_bytes(b'preserve')
        with self.assertRaises(g.GitPreflightError): p.verify_request(value)
        self.assertEqual(lock.read_bytes(), b'preserve')

    def test_changed_file_and_attached_ref_refuse_bound_request(self):
        value = self.bound_request()
        target = self.repo/'target.txt'
        target.write_bytes(b'changed again')
        with self.assertRaises(g.GitPreflightError): p.verify_request(value)
        # Restore the bytes but not the old metadata: exact file binding must
        # still reject rather than interpreting content equality as authority.
        target.write_bytes(b'new')
        with self.assertRaises(g.GitPreflightError): p.verify_request(value)

    def test_changed_head_or_index_refuses_before_mutation(self):
        value = self.bound_request()
        head = self.repo/'.git/HEAD'
        head.write_text('ref: refs/heads/not-present\n')
        with self.assertRaises(g.GitPreflightError): p.verify_request(value)

    def test_supervisor_repeats_final_fixture_gate_before_git_spawn(self):
        value = self.bound_request()
        plan = g.preflight_git_add(self.action)
        # The real frame reader needs an owned pipe, not a guessed FD number.
        cr, cw = os.pipe()
        for fd in (cr, cw):
            self.addCleanup(os.close, fd)
        sr, sw = os.pipe()
        for fd in (sr, sw):
            self.addCleanup(os.close, fd)
        pipes = [[os.fstat(fd).st_dev, os.fstat(fd).st_ino] for fd in (cr, sw)]
        own = kernel.Identity(123, 123, 123, os.geteuid(), 124, 1, 1)
        peer = kernel.Identity(124, 124, 124, os.geteuid(), value['parent']['pid'], 1, 1)
        start = {'domain': p.DOMAIN, 'type': 'START', 'request': value,
            'parent': peer.data(), 'launch': own.data(), 'pipes': pipes}
        with patch.object(p, 'standalone'), patch.object(p, 'send'), \
                patch.object(p, 'verify_request', return_value=(self.action, plan)) as revalidate, \
                patch.object(p, 'fixture_check', side_effect=p.AdmissionRejected()) as guard, \
                patch.object(supervisor, 'identity', return_value=peer), \
                patch.object(supervisor.signal, 'signal'), patch.object(kernel, 'git_inventory'), \
                patch.object(supervisor, 'host_check'), \
                patch.object(supervisor.subprocess, 'Popen') as spawn:
            self.assertEqual(supervisor._product_main(start, own,
                (cr, os.O_RDONLY, pipes[0]), (sw, os.O_WRONLY, pipes[1])), 78)
            revalidate.assert_called_once_with(value)
            guard.assert_called_once_with(self.fixture, self.action, self.nonce)
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
