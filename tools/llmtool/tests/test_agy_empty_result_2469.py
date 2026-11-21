"""#2469: an empty AGY SUCCESS with an unchanged D6 is a distinct non-delivery.

Every AGY run also keeps a bounded, screened response excerpt and the model
the resolver reported (UNKNOWN when absent).
"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import AgyRoute, RouteError, load_route_config
from llmtool.developer_routes import base
from llmtool.developer_routes.base import (
    MAX_RESPONSE_EXCERPT_CHARS, DeveloperRoute, capture_snapshot, run_route)

MODEL = load_route_config()['routes']['agy']['default_model']
OTHER_MODEL = MODEL + '-other'
# Synthetic marker only; the sensitive screen is mocked (test data rule).
SYNTHETIC_MARKER = 'SYNTHETIC-SENSITIVE-MARKER'


class Boundary:
    def prepare(self, root, write_paths, state_dirs=()):
        return ['/verified-boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64}


class NotAgyRoute(AgyRoute):
    """Same parsing as AGY, but a different route name and default hooks."""
    name = 'other'

    def empty_delivery(self, parsed, changed_paths):
        return DeveloperRoute.empty_delivery(self, parsed, changed_paths)


class AgyEmptyResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        git = ['/usr/bin/git', '-C', str(self.root)]
        subprocess.run(['/usr/bin/git', 'init', str(self.root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(git + ['config', 'user.name', 'Test'], check=True)
        subprocess.run(git + ['config', 'user.email', 'test@example.invalid'], check=True)
        (self.root / 'source.py').write_text('original\n')
        subprocess.run(git + ['add', 'source.py'], check=True)
        subprocess.run(git + ['commit', '-m', 'initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.branch = subprocess.check_output(
            git + ['branch', '--show-current'], text=True).strip()
        self.head = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()

    def run_case(self, *, response, changed, log, status='SUCCESS', route=None):
        (self.root / 'source.py').write_text('original\n')
        raw = json.dumps({'conversation_id': 'conv_12345678', 'status': status,
                          'response': response}).encode()

        def runner(argv, cwd, env, timeout):
            Path(argv[argv.index('--log-file') + 1]).write_text(log)
            if changed:
                (self.root / 'source.py').write_text('changed\n')
            return 0, raw, b''
        request = {'root': str(self.root), 'prompt': 'bounded prompt', 'model': MODEL,
                   'effort': 'medium', 'expected_branch': self.branch,
                   'expected_head': self.head, 'write_paths': ['source.py'],
                   'executable': '/usr/bin/agy', 'log_path': 'agy.log'}
        return run_route(route or AgyRoute({'executable': 'agy'}), request,
                         gate=lambda: True, runner=runner, boundary=Boundary(),
                         snapshot=capture_snapshot)

    @staticmethod
    def resolver(*names):
        return ''.join(f'model_resolver: Resolving model {name}\n' for name in names)

    def test_a_empty_success_without_change_is_non_delivery(self):
        delivered = []
        request_log = self.resolver(MODEL)
        with self.assertRaises(RouteError) as caught:
            (self.root / 'source.py').write_text('original\n')
            raw = json.dumps({'conversation_id': 'conv_12345678', 'status': 'SUCCESS',
                              'response': ''}).encode()

            def runner(argv, cwd, env, timeout):
                Path(argv[argv.index('--log-file') + 1]).write_text(request_log)
                return 0, raw, b''
            run_route(AgyRoute({'executable': 'agy'}),
                      {'root': str(self.root), 'prompt': 'p', 'model': MODEL,
                       'effort': 'medium', 'expected_branch': self.branch,
                       'expected_head': self.head, 'write_paths': ['source.py'],
                       'executable': '/usr/bin/agy', 'log_path': 'agy.log'},
                      gate=lambda: True, runner=runner, boundary=Boundary(),
                      snapshot=capture_snapshot,
                      on_delivered=lambda: delivered.append(True))
        self.assertEqual(caught.exception.code, 'AGY_EMPTY_RESULT')
        self.assertNotEqual(caught.exception.code, 'DEVELOPER_RESPONDED')
        self.assertEqual(delivered, [])
        self.assertEqual(caught.exception.facts['resolved_model'], MODEL)
        self.assertEqual(caught.exception.facts['response_excerpt'], '')

    def test_b_response_and_change_is_normal_hand_back_with_evidence(self):
        result = self.run_case(response='finished the work', changed=True,
                               log=self.resolver(MODEL))
        self.assertEqual(result.status, 'SUCCESS')
        self.assertEqual(result.changed_files, ('source.py',))
        self.assertEqual(result.response_excerpt, 'finished the work')
        self.assertEqual(result.resolved_model, MODEL)

    def test_c_two_different_resolver_models_are_a_mismatch(self):
        with self.assertRaises(RouteError) as caught:
            self.run_case(response='text', changed=True,
                          log=self.resolver(MODEL, OTHER_MODEL))
        self.assertEqual(caught.exception.code, 'AGY_MODEL_MISMATCH')
        self.assertEqual(caught.exception.facts['resolved_model'], 'MISMATCH')
        self.assertNotEqual(caught.exception.facts['resolved_model'], MODEL)

    def test_d_no_resolver_line_is_unknown(self):
        with self.assertRaises(RouteError) as caught:
            self.run_case(response='text', changed=True, log='no resolver here\n')
        self.assertEqual(caught.exception.code, 'AGY_MODEL_UNVERIFIED')
        self.assertEqual(caught.exception.facts['resolved_model'], 'UNKNOWN')

    def test_e_sensitive_response_is_screened_never_stored_raw(self):
        def fake_screen(text):
            return SYNTHETIC_MARKER in text
        with mock.patch('llmtool.test_output_excerpts._emitted_sensitive', fake_screen):
            result = self.run_case(response='note ' + SYNTHETIC_MARKER, changed=True,
                                   log=self.resolver(MODEL))
        self.assertNotIn(SYNTHETIC_MARKER, result.response_excerpt)
        self.assertEqual(result.response_excerpt, '[withheld]')

    def test_f_excerpt_is_bounded(self):
        excerpt = base.response_excerpt('x' * 5000)
        self.assertEqual(len(excerpt), MAX_RESPONSE_EXCERPT_CHARS)
        result = self.run_case(response='y' * 3000, changed=True, log=self.resolver(MODEL))
        self.assertLessEqual(len(result.response_excerpt), MAX_RESPONSE_EXCERPT_CHARS)

    def test_g_other_routes_are_unchanged(self):
        self.assertFalse(DeveloperRoute.empty_delivery(
            AgyRoute({'executable': 'agy'}), {}, ()))
        result = self.run_case(response='', changed=False, log=self.resolver(MODEL),
                               route=NotAgyRoute({'executable': 'agy'}))
        self.assertEqual(result.route, 'other')
        self.assertIsNone(result.response_excerpt)
        self.assertIsNone(result.resolved_model)


if __name__ == '__main__':
    unittest.main()
