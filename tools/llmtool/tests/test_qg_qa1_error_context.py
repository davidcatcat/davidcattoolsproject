"""CO-14: public command errors name the supplied domain, never raw input."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import patch

from llmtool.cli import main
from llmtool.feedback import FeedbackCase, RegressionCase, RegressionAssertion, AssertionType
from llmtool.benchmark import BenchmarkSpec


class ErrorContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.config = self.home / '.config/llmtool/config.yaml'
        self.config.parent.mkdir(parents=True)
        self.configure()
        self.file = self.root / 'data.json'
        self.output = self.root / 'output.txt'
        self.output.write_text('GOOD')
        self.case = RegressionCase.create(['0' * 24], 'context-pack', 1, 'synthetic',
            [RegressionAssertion(AssertionType.TEXT_CONTAINS, 'GOOD')])

    def configure(self, active='known'):
        self.config.write_text('active_provider: ' + active + '\nproviders:\n  known:\n'
                               '    type: claude-cli\n    model: test-model\n')

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'HOME': str(self.home),
                'LLMTOOL_HOME': str(self.root / 'absent-store')}, clear=True))
            stack.enter_context(patch('llmtool.config.Path.home', return_value=self.home))
            stack.enter_context(patch('llmtool.cli.sys.stdin', io.StringIO()))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            factories = [stack.enter_context(patch('llmtool.' + module + '.create_client'))
                         for module in ('cli', 'orchestration', 'retrieval')]
            result = main(args)
            for factory in factories:
                factory.assert_not_called()
        self.assertFalse((self.root / 'absent-store').exists())
        return result, out.getvalue(), err.getvalue()

    def bad(self, args, domain):
        result, out, err = self.invoke(args)
        self.assertEqual((result, out), (1, ''))
        self.assertIn(domain, err)
        if domain != 'feedback':
            self.assertNotIn('feedback', err)
        for marker in ('PRIVATE_BAD_INPUT', 'Traceback', str(self.root)):
            self.assertNotIn(marker, err)
        return err

    def plan(self, data):
        self.file.write_text(data if isinstance(data, str) else json.dumps(data))
        return ['orchestrate', '--dry-run', '--plan', str(self.file)]

    def test_malformed_feedback_retains_feedback_context(self):
        self.file.write_text('{PRIVATE_BAD_INPUT')
        self.bad(['feedback', 'validate', '--file', str(self.file)], 'feedback')

    def test_malformed_orchestration_plan(self):
        self.bad(self.plan('{PRIVATE_BAD_INPUT'), 'orchestration plan')

    def test_orchestration_plan_extra_field(self):
        self.bad(self.plan({'steps': [], 'PRIVATE_BAD_INPUT': True}), 'orchestration plan')

    def test_orchestration_plan_too_many_steps(self):
        self.bad(self.plan({'steps': [{'role': 'grinder', 'provider': 'known'}] * 9}), 'orchestration plan')

    def benchmark(self, kind, raw):
        self.file.write_text(raw)
        if kind == 'suite':
            return ['benchmark', 'suite', '--manifest', str(self.file)]
        return ['benchmark', 'evaluate', '--' + kind, str(self.file), '--output-file', str(self.output)]

    def test_malformed_benchmark_spec(self):
        self.bad(self.benchmark('spec', '{PRIVATE_BAD_INPUT'), 'benchmark')

    def test_malformed_benchmark_suite_manifest(self):
        self.bad(self.benchmark('suite', '{PRIVATE_BAD_INPUT'), 'benchmark')

    def test_benchmark_schema_error(self):
        for kind in ('spec', 'suite', 'case'):
            with self.subTest(kind=kind):
                self.bad(self.benchmark(kind, '{"PRIVATE_BAD_INPUT": true}'), 'benchmark')

    def test_malformed_benchmark_regression_case(self):
        self.bad(self.benchmark('case', '{PRIVATE_BAD_INPUT'), 'benchmark')

    def test_nested_benchmark_regression_schema_error(self):
        data = BenchmarkSpec(self.case, 'synthetic').to_dict()
        data['regression'] = {}
        self.bad(self.benchmark('spec', json.dumps(data)), 'benchmark')

    def test_strict_duplicate_depth_controls_keep_domain(self):
        for raw in ('{"steps":[],"steps":[]}', '[' * 20 + ']' * 20, '{"steps":"\x00"}'):
            with self.subTest(raw=raw):
                self.bad(self.plan(raw), 'orchestration plan')

    def test_explicit_ask_missing_provider_profile(self):
        self.bad(['ask', 'synthetic', '--provider', 'missing'], 'requested provider profile is not defined')

    def test_orchestration_explicit_missing_profile(self):
        args = self.plan({'steps': [{'role': 'grinder', 'provider': 'missing'}]})
        self.bad(['orchestrate', '--plan', str(self.file), 'synthetic'], 'requested provider profile is not defined')

    def test_retrieval_explicit_missing_planner_and_synthesizer(self):
        document = self.root / 'document.txt'
        document.write_text('synthetic')
        for planner, synthesizer in (('missing', 'known'), ('known', 'missing')):
            with self.subTest(planner=planner, synthesizer=synthesizer):
                self.bad(['retrieve-context', '--root', str(self.root), '--scope', 'document.txt',
                    '--planner-provider', planner, '--synthesizer-provider', synthesizer, 'synthetic'],
                    'requested provider profile is not defined')

    def test_genuinely_invalid_active_provider_retains_active_wording(self):
        self.configure(active='missing')
        err = self.bad(['ask', 'synthetic'], 'active provider is not defined')
        self.assertNotIn('requested provider profile', err)

    def test_valid_feedback_benchmark_and_orchestration_offline(self):
        case = FeedbackCase.capture('context-pack', 1, 'synthetic', 'GOOD', 'GOOD')
        self.file.write_text(case.to_json())
        self.assertEqual(self.invoke(['feedback', 'validate', '--file', str(self.file)])[0], 0)
        self.file.write_text(BenchmarkSpec(self.case, 'synthetic').to_json())
        self.assertEqual(self.invoke(['benchmark', 'evaluate', '--spec', str(self.file),
            '--output-file', str(self.output), '--json'])[0], 0)
        self.assertEqual(self.invoke(self.plan({'steps': [{'role': 'grinder', 'provider': 'known'}]}))[0], 0)
