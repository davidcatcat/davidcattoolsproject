"""#1502 WP-F PR 3: run_route delivery boundary (design section 2.2 step 5).

Every child is a local interpreter process started by the real
``_default_runner``; no provider is ever called. The test boundary's prefix
is a local wrapper script that, like the platform wrapper, execs its argv
tail. Fake provider output uses fixed, non-secret values. Git reads are
monkeypatched (design section 3, rules for every PR).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from llmtool.developer_routes import base  # noqa: E402


MODEL = 'fake-model-a'
OTHER_MODEL = 'fake-model-b'
BRANCH = 'fixture-branch'
HEAD = '1' * 40
POLICY_DIGEST = '0' * 64

WRAPPER_SOURCE = '''\
import os
import sys
try:
    os.execv(sys.argv[1], sys.argv[1:])
except OSError:
    sys.stderr.write('fixture wrapper: exec failed\\n')
    sys.exit(71)
'''

PROVIDER_SOURCE = '''\
import json
import sys
log_path, model, mode = sys.argv[1], sys.argv[2], sys.argv[3]
if mode != 'no_evidence':
    reported = 'fake-model-b' if mode == 'wrong_model' else model
    with open(log_path, 'w') as handle:
        json.dump({'resolved_model': reported}, handle)
sys.stdout.write(json.dumps({'type': 'result', 'session_id': 'fixture-session-1',
                             'status': 'DONE', 'summary': 'fixture done'}))
sys.exit(3 if mode == 'exit_nonzero' else 0)
'''


class FixtureRoute(base.DeveloperRoute):
    """Minimal route whose provider is the local fixture script."""

    name = 'fixtureroute'
    worker_key = 'fixture-route'
    family = 'fixture'

    def validate_profile(self, model, effort):
        if model != MODEL or effort != 'high':
            raise base.RouteError('FIXTURE_PROFILE_INVALID')
        return model, effort

    def build_argv(self, executable, request):
        return [executable, '-I', self.config['script'], request['log_path'],
                request['model'], self.config['mode']]

    def build_env(self, tmpdir):
        return {'PATH': '/usr/bin:/bin'}

    def parse_output(self, stdout, stderr, log_text):
        parsed = json.loads(stdout.decode('utf-8'))
        if (type(parsed) is not dict or parsed.get('type') != 'result' or
                type(parsed.get('session_id')) is not str):
            raise base.RouteError(base._route_code(self, 'OUTPUT_INVALID'))
        return parsed

    def verified_model(self, log_text, requested):
        if not log_text:
            return None
        value = json.loads(log_text).get('resolved_model')
        return value if type(value) is str else None

    def quota_exhausted(self, parsed, stderr):
        return False


class FixtureBoundary:
    """Test boundary: returns the wrapper prefix; optional probe via the runner."""

    def __init__(self, prefix, *, probe=True):
        self.prefix = list(prefix)
        self.probe = probe
        self.probe_codes = []

    def prepare(self, root, write_paths, state_dirs=()):
        return list(self.prefix), POLICY_DIGEST

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        if self.probe:
            rc, _out, _err = runner(
                self.prefix + [sys.executable, '-I', '-c', 'import sys; sys.exit(0)'],
                str(root), {'PATH': '/usr/bin:/bin'}, 10)
            self.probe_codes.append(rc)
            if rc != 0:
                return {'verified': False, 'policy_sha256': POLICY_DIGEST}
        return {'verified': True, 'policy_sha256': POLICY_DIGEST}


class RouteDeliveryBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='llmtool-delivery-route-test-'))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.root = self.work / 'd6'
        self.root.mkdir()
        (self.root / 'notes.txt').write_text('fixture\n', encoding='utf-8')
        scripts = self.work / 'scripts'
        scripts.mkdir()
        self.wrapper = scripts / 'wrapper.py'
        self.wrapper.write_text(WRAPPER_SOURCE, encoding='utf-8')
        self.provider = scripts / 'provider.py'
        self.provider.write_text(PROVIDER_SOURCE, encoding='utf-8')
        self.missing = str(scripts / 'no-such-provider')
        git_view = types.SimpleNamespace(branch=BRANCH, head=HEAD, clean=True)
        patches = (
            mock.patch.object(base.task_completion, 'collect_git_snapshot',
                              return_value=git_view),
            mock.patch.object(base, '_git_status_paths', return_value=()),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = []
        real_result = base.RouteResult

        def record_result(*args, **kwargs):
            self.events.append('result')
            return real_result(*args, **kwargs)

        result_patch = mock.patch.object(base, 'RouteResult', side_effect=record_result)
        result_patch.start()
        self.addCleanup(result_patch.stop)
        self.real_result = real_result

    def _wrapper_prefix(self):
        return [sys.executable, '-I', str(self.wrapper)]

    def _request(self, executable=None):
        return {'root': str(self.root), 'model': MODEL, 'effort': 'high',
                'expected_branch': BRANCH, 'expected_head': HEAD,
                'write_paths': ['notes.txt'],
                'executable': executable or sys.executable,
                'timeout': 30, 'log_path': 'provider.log'}

    def _run(self, mode, *, boundary=None, executable=None, runner=None,
             on_delivered='record'):
        route = FixtureRoute({'script': str(self.provider), 'mode': mode})
        if on_delivered == 'record':
            on_delivered = lambda: self.events.append('delivered')
        return base.run_route(
            route, self._request(executable), gate=lambda: True, runner=runner,
            boundary=boundary or FixtureBoundary(self._wrapper_prefix()),
            snapshot=lambda root: {'files': {}},
            on_launch=lambda: self.events.append('launch'),
            on_delivered=on_delivered)

    def _refusal(self, *args, **kwargs):
        with self.assertRaises(base.RouteError) as caught:
            self._run(*args, **kwargs)
        return caught.exception

    # F1: the wrapper starts, but cannot start the provider.
    def test_wrapper_starts_provider_exec_fails_no_delivery(self):
        boundary = FixtureBoundary(self._wrapper_prefix())
        exc = self._refusal('valid', boundary=boundary, executable=self.missing)
        self.assertEqual(boundary.probe_codes, [0])
        self.assertEqual(self.events, ['launch'])
        # No provider result: the first provider-result check refuses.
        self.assertEqual(exc.code, 'FIXTUREROUTE_OUTPUT_INVALID')
        # The wrapper process ran and reported its own exec failure.
        self.assertEqual(exc.execution_facts['process_exit_code'], 71)

    def test_provider_reports_other_model_no_delivery(self):
        exc = self._refusal('wrong_model')
        self.assertEqual(exc.code, 'FIXTUREROUTE_MODEL_MISMATCH')
        self.assertEqual(self.events, ['launch'])

    def test_provider_without_model_evidence_no_delivery(self):
        exc = self._refusal('no_evidence')
        self.assertEqual(exc.code, 'FIXTUREROUTE_MODEL_UNVERIFIED')
        self.assertEqual(self.events, ['launch'])

    def test_provider_exit_nonzero_no_delivery(self):
        exc = self._refusal('exit_nonzero')
        self.assertEqual(exc.code, 'FIXTUREROUTE_EXIT_NONZERO')
        self.assertEqual(self.events, ['launch'])

    def test_valid_provider_result_fires_once_before_route_result(self):
        result = self._run('valid')
        self.assertIsInstance(result, self.real_result)
        self.assertEqual(result.model, MODEL)
        self.assertEqual(result.session_id, 'fixture-session-1')
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.events, ['launch', 'delivered', 'result'])

    def test_without_callback_behaviour_unchanged(self):
        result = self._run('valid', on_delivered=None)
        self.assertIsInstance(result, self.real_result)
        self.assertEqual(result.model, MODEL)
        self.assertEqual(self.events, ['launch', 'result'])

    def test_popen_failure_runner_failed_no_delivery(self):
        boundary = FixtureBoundary([str(self.work / 'no-such-wrapper')], probe=False)
        exc = self._refusal('valid', boundary=boundary)
        self.assertEqual(exc.code, 'FIXTUREROUTE_RUNNER_FAILED')
        self.assertEqual(self.events, ['launch'])

    def test_boundary_probes_never_fire_hook(self):
        calls = []

        def recording_runner(argv, cwd, env, timeout):
            calls.append(len([item for item in self.events if item == 'delivered']))
            return base._default_runner(argv, cwd, env, timeout)

        boundary = FixtureBoundary(self._wrapper_prefix())
        result = self._run('valid', boundary=boundary, runner=recording_runner)
        self.assertIsInstance(result, self.real_result)
        self.assertEqual(boundary.probe_codes, [0])
        # Probe run, then provider run; nothing delivered at either start.
        self.assertEqual(calls, [0, 0])
        self.assertEqual(self.events, ['launch', 'delivered', 'result'])

    def test_probe_passes_provider_fails_no_hook(self):
        boundary = FixtureBoundary(self._wrapper_prefix())
        exc = self._refusal('exit_nonzero', boundary=boundary)
        self.assertEqual(boundary.probe_codes, [0])
        self.assertEqual(exc.code, 'FIXTUREROUTE_EXIT_NONZERO')
        self.assertNotIn('delivered', self.events)

    def test_failed_probe_refuses_before_launch(self):
        boundary = FixtureBoundary([str(self.work / 'no-such-wrapper')])
        exc = self._refusal('valid', boundary=boundary)
        self.assertEqual(exc.code, 'BLOCKED_WRITE_ISOLATION')
        self.assertEqual(self.events, [])

    def test_raising_callback_keeps_its_code_and_returns_no_result(self):
        def refuse():
            self.events.append('delivered')
            raise base.RouteError('SKILL_DELIVERY_RECEIPT_MISSING')

        exc = self._refusal('valid', on_delivered=refuse)
        self.assertEqual(exc.code, 'SKILL_DELIVERY_RECEIPT_MISSING')
        self.assertEqual(self.events, ['launch', 'delivered'])
        self.assertEqual(exc.execution_facts['process_exit_code'], 0)

    def test_callback_other_exception_gives_fixed_code(self):
        def broken():
            self.events.append('delivered')
            raise ValueError('fixture failure')

        exc = self._refusal('valid', on_delivered=broken)
        self.assertEqual(exc.code, 'FIXTUREROUTE_DELIVERY_CALLBACK_FAILED')
        self.assertEqual(self.events, ['launch', 'delivered'])

    def test_non_callable_callback_refuses_before_launch(self):
        exc = self._refusal('valid', on_delivered='not-callable')
        self.assertEqual(exc.code, 'FIXTUREROUTE_REQUEST_INVALID')
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
