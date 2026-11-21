"""#2469: every AGY run keeps a bounded, screened response excerpt and a resolved model.

The resolved model is the one name the resolver lines agree on, MISMATCH when
they differ and UNKNOWN when absent; never raw log text. A value that fails a
bound or the sensitive screen is replaced by the fixed withheld marker. The
emptiness decision itself (AGY_EMPTY_RESULT) is not made here.
"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from llmtool import task_store
from llmtool.developer_routes import AgyRoute, RouteError, load_route_config
from llmtool.developer_routes import base
from llmtool.developer_routes.base import (
    MAX_RESPONSE_EXCERPT_CHARS, WITHHELD_MARKER, capture_snapshot, run_route)
from llmtool.task_submission_cli import _agy_evidence_fields

MODEL = load_route_config()['routes']['agy']['default_model']
OTHER_MODEL = MODEL + '-other'
# Synthetic marker only; the sensitive screen is mocked (test data rule).
SYNTHETIC_MARKER = 'SYNTHETIC-SENSITIVE-MARKER'


def fake_screen(text):
    return SYNTHETIC_MARKER in text


class Boundary:
    def prepare(self, root, write_paths, state_dirs=()):
        return ['/verified-boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64}


class NotAgyRoute(AgyRoute):
    """Same parsing as AGY, but a different route name."""
    name = 'other'


class EmptyResultRoute(AgyRoute):
    """Raises the main-line empty-result refusal after the output was parsed."""

    def validate_status(self, parsed):
        raise RouteError('AGY_EMPTY_RESULT', facts={'changed_files': []})


class AgyEvidenceTests(unittest.TestCase):
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

    def run_case(self, *, response, changed, log, route=None):
        (self.root / 'source.py').write_text('original\n')
        raw = json.dumps({'conversation_id': 'conv_12345678', 'status': 'SUCCESS',
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

    def test_a_normal_delivery_stores_excerpt_and_resolved_model(self):
        result = self.run_case(response='finished the work', changed=True,
                               log=self.resolver(MODEL))
        self.assertEqual(result.status, 'SUCCESS')
        self.assertEqual(result.response_excerpt, 'finished the work')
        self.assertEqual(result.resolved_model, MODEL)
        fields = _agy_evidence_fields(result.response_excerpt, result.resolved_model)
        self.assertEqual(fields, {'agy_response_excerpt': 'finished the work',
                                  'agy_resolved_model': MODEL})

    def test_b_empty_result_refusal_keeps_code_and_stores_evidence(self):
        with self.assertRaises(RouteError) as caught:
            self.run_case(response='', changed=False, log=self.resolver(MODEL),
                          route=EmptyResultRoute({'executable': 'agy'}))
        self.assertEqual(caught.exception.code, 'AGY_EMPTY_RESULT')
        self.assertEqual(caught.exception.facts['changed_files'], [])
        self.assertEqual(caught.exception.facts['resolved_model'], MODEL)
        self.assertEqual(caught.exception.facts['response_excerpt'], '')

    def test_c_two_different_resolver_models_are_a_mismatch(self):
        with self.assertRaises(RouteError) as caught:
            self.run_case(response='text', changed=True,
                          log=self.resolver(MODEL, OTHER_MODEL))
        self.assertEqual(caught.exception.code, 'AGY_MODEL_MISMATCH')
        self.assertEqual(caught.exception.facts['resolved_model'], 'MISMATCH')

    def test_d_no_resolver_line_is_unknown(self):
        with self.assertRaises(RouteError) as caught:
            self.run_case(response='text', changed=True, log='no resolver here\n')
        self.assertEqual(caught.exception.code, 'AGY_MODEL_UNVERIFIED')
        self.assertEqual(caught.exception.facts['resolved_model'], 'UNKNOWN')

    def test_e_overlong_or_control_character_label_is_refused_to_marker(self):
        for label in ('a' * 200, 'abc\x01def', 'UNKNOWN'):
            with self.subTest(label=repr(label[:12])):
                with self.assertRaises(RouteError) as caught:
                    self.run_case(response='text', changed=True,
                                  log=self.resolver(label, label))
                self.assertEqual(caught.exception.facts['resolved_model'], WITHHELD_MARKER)
                self.assertNotIn(label, str(caught.exception.facts))

    def test_f_sensitive_response_is_withheld_never_stored_raw(self):
        with mock.patch('llmtool.test_output_excerpts._emitted_sensitive', fake_screen):
            result = self.run_case(response='note ' + SYNTHETIC_MARKER, changed=True,
                                   log=self.resolver(MODEL))
        self.assertEqual(result.response_excerpt, WITHHELD_MARKER)
        self.assertNotIn(SYNTHETIC_MARKER, result.response_excerpt)

    def test_f2_sensitive_resolver_label_is_withheld(self):
        label = 'model-' + SYNTHETIC_MARKER
        with mock.patch('llmtool.test_output_excerpts._emitted_sensitive', fake_screen):
            with self.assertRaises(RouteError) as caught:
                self.run_case(response='text', changed=True, log=self.resolver(label, label))
        self.assertEqual(caught.exception.facts['resolved_model'], WITHHELD_MARKER)

    def test_f3_excerpt_is_bounded(self):
        excerpt = base.response_excerpt('x' * 5000)
        self.assertEqual(len(excerpt), MAX_RESPONSE_EXCERPT_CHARS)
        result = self.run_case(response='y' * 3000, changed=True, log=self.resolver(MODEL))
        self.assertLessEqual(len(result.response_excerpt), MAX_RESPONSE_EXCERPT_CHARS)

    def test_g_store_rules_refuse_oversize_or_malformed_values(self):
        valid_excerpt = task_store.valid_agy_response_excerpt
        valid_model = task_store.valid_agy_resolved_model
        self.assertTrue(valid_excerpt('') and valid_excerpt('x' * 1024))
        self.assertFalse(valid_excerpt('x' * 1025))
        self.assertFalse(valid_excerpt('line\nbreak'))
        self.assertFalse(valid_excerpt('tab\there'))
        self.assertFalse(valid_excerpt(None))
        with mock.patch.object(task_store, '_emitted_sensitive', fake_screen):
            self.assertFalse(valid_excerpt(SYNTHETIC_MARKER))
            self.assertFalse(valid_model('m-' + SYNTHETIC_MARKER))
        for good in (MODEL, 'UNKNOWN', 'MISMATCH', WITHHELD_MARKER):
            self.assertTrue(valid_model(good))
        for bad in ('a' * 129, 'bad name', 'bad\x01', '', None, 7):
            self.assertFalse(valid_model(bad))
        # The record-level rule: AGY only, both fields together.
        record = {'provider_profile': 'agy', 'agy_response_excerpt': '',
                  'agy_resolved_model': 'UNKNOWN'}
        self.assertTrue(task_store._valid_agy_evidence_record(record))
        self.assertFalse(task_store._valid_agy_evidence_record(
            dict(record, provider_profile='opencode')))
        self.assertFalse(task_store._valid_agy_evidence_record(
            {'provider_profile': 'agy', 'agy_response_excerpt': ''}))
        self.assertFalse(task_store._valid_agy_evidence_record(
            dict(record, agy_response_excerpt='x' * 2000)))
        self.assertIn('agy_resolved_model', task_store._DEVELOPER_OPTIONAL_FIELDS)

    def test_g2_record_helper_never_stores_text_that_fails_a_rule(self):
        fields = _agy_evidence_fields('x' * 2000, 'bad name')
        self.assertEqual(fields, {'agy_response_excerpt': WITHHELD_MARKER,
                                  'agy_resolved_model': WITHHELD_MARKER})

    def test_h_other_routes_are_unchanged(self):
        result = self.run_case(response='', changed=False, log=self.resolver(MODEL),
                               route=NotAgyRoute({'executable': 'agy'}))
        self.assertEqual(result.route, 'other')
        self.assertIsNone(result.response_excerpt)
        self.assertIsNone(result.resolved_model)
        with self.assertRaises(RouteError) as caught:
            self.run_case(response='t', changed=True, log=self.resolver(MODEL, OTHER_MODEL),
                          route=NotAgyRoute({'executable': 'agy'}))
        self.assertNotIn('resolved_model', caught.exception.facts)
        self.assertNotIn('response_excerpt', caught.exception.facts)


if __name__ == '__main__':
    unittest.main()
