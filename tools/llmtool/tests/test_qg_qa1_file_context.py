"""R-1: public file/output errors preserve their owner's domain and strictness."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import patch

from llmtool.cli import main
from llmtool.feedback import JSON_LIMIT, RegressionCase, RegressionAssertion
from llmtool.benchmark import BenchmarkSpec, BenchmarkRuntime, BenchmarkSuite, SuiteEntry


class FileContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.state = self.root / 'absent-store'
        self.case = RegressionCase.create(['0' * 24], 'context-pack', 1, 'synthetic',
            [RegressionAssertion('TEXT_CONTAINS', 'GOOD')])
        self.casefile = self.write('case.json', self.case.to_json().encode())
        self.spec = self.write('spec.json', BenchmarkSpec(self.case, 'synthetic').to_json().encode())
        self.output = self.write('output.txt', b'GOOD')
        self.runtime = self.write('runtime.json', json.dumps(
            {field: getattr(BenchmarkRuntime(), field) for field in
             ('wall_latency_ms', 'provider_latency_ms', 'model', 'input_tokens', 'output_tokens', 'total_tokens')}).encode())
        self.plan = self.write('plan.json', b'{"steps":[{"role":"grinder","provider":"synthetic"}]}')
        self.suite = self.root / 'suite.json'
        self.manifest(self.casefile.name, self.output.name)

    def write(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        return path

    def manifest(self, case, output):
        self.suite.write_text(BenchmarkSuite([SuiteEntry(str(case), str(output))]).to_json())

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'HOME': str(self.home),
                'LLMTOOL_HOME': str(self.state)}, clear=True))
            stack.enter_context(patch('llmtool.cli.sys.stdin', io.StringIO()))
            for target in ('llmtool.cli.create_client', 'llmtool.providers.create_client',
                           'llmtool.orchestration.create_client'):
                factory = stack.enter_context(patch(target))
                stack.callback(factory.assert_not_called)
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            result = main(args)
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.home.iterdir()), [])
        return result, out.getvalue(), err.getvalue()

    def rejected(self, args, domain, suffix):
        result, out, err = self.invoke(args)
        self.assertEqual((result, out), (1, ''))
        self.assertEqual(err, 'llmtool: ' + domain + suffix + '\n')
        for marker in ('Traceback', str(self.root), 'PRIVATE'):
            self.assertNotIn(marker, err)
        if domain != 'feedback':
            self.assertNotIn('feedback', err)

    def invalid_files(self):
        missing = self.root / 'PRIVATE-missing'
        directory = self.root / 'PRIVATE-directory'
        directory.mkdir(exist_ok=True)
        large = self.write('PRIVATE-large', b'x' * (JSON_LIMIT + 1))
        binary = self.write('PRIVATE-binary', b'\xffPRIVATE')
        traversal = self.root / 'PRIVATE-parent' / '..' / 'case.json'
        link = self.root / 'PRIVATE-link'
        if not link.is_symlink():
            link.symlink_to(self.casefile)
        parent = self.root / 'PRIVATE-linked-parent'
        if not parent.is_symlink():
            parent.symlink_to(self.root, target_is_directory=True)
        return ((missing, ' file operation failed; details omitted'),
                (directory, ' file operation failed; details omitted'),
                (large, ' file exceeds 1 MiB'), (binary, ' file must be UTF-8'),
                (traversal, ' file paths cannot contain parent traversal'),
                (link, ' file operation failed; details omitted'),
                (parent / 'case.json', ' file operation failed; details omitted'))

    def file_matrix(self, args_for, domain):
        for path, suffix in self.invalid_files():
            with self.subTest(domain=domain, failure=suffix, file=path.name):
                self.rejected(args_for(path), domain, suffix)

    def evaluate(self, option, path):
        args = ['benchmark', 'evaluate', '--spec', str(self.spec), '--output-file', str(self.output)]
        if option == '--case':
            args[2:4] = ['--case', str(path)]
        elif option == '--spec':
            args[3] = str(path)
        elif option == '--output-file':
            args[5] = str(path)
        else:
            args += [option, str(path)]
        return args

    def test_feedback_file_errors_keep_exact_original_wording(self):
        self.file_matrix(lambda path: ['feedback', 'validate', '--file', str(path)], 'feedback')

    def test_orchestration_plan_file_errors_use_orchestration_domain(self):
        self.file_matrix(lambda path: ['orchestrate', '--dry-run', '--plan', str(path)], 'orchestration plan')

    def test_benchmark_spec_file_errors(self):
        self.file_matrix(lambda path: self.evaluate('--spec', path), 'benchmark spec')

    def test_benchmark_case_file_errors(self):
        self.file_matrix(lambda path: self.evaluate('--case', path), 'benchmark case')

    def test_benchmark_suite_manifest_file_errors(self):
        self.file_matrix(lambda path: ['benchmark', 'suite', '--manifest', str(path)], 'benchmark suite')

    def test_benchmark_runtime_file_errors(self):
        self.file_matrix(lambda path: self.evaluate('--runtime-file', path), 'benchmark runtime')

    def test_benchmark_output_file_errors(self):
        self.file_matrix(lambda path: self.evaluate('--output-file', path), 'benchmark output')

    def test_benchmark_live_spec_and_case_fail_before_provider_call(self):
        for option, domain in (('--spec', 'benchmark spec'), ('--case', 'benchmark case')):
            self.file_matrix(lambda path: ['benchmark', 'live', option, str(path)], domain)

    def test_suite_case_entry_file_errors(self):
        def args(path):
            self.manifest(path, self.output.name)
            return ['benchmark', 'suite', '--manifest', str(self.suite)]
        self.file_matrix(args, 'benchmark case')

    def test_suite_output_entry_file_errors(self):
        def args(path):
            self.manifest(self.casefile.name, path)
            return ['benchmark', 'suite', '--manifest', str(self.suite)]
        self.file_matrix(args, 'benchmark output')

    def test_benchmark_candidate_controls_use_output_context(self):
        for raw in (b'GOOD\x1b[31mPRIVATE', b'GOOD\x00PRIVATE', b'GOOD\x07PRIVATE'):
            with self.subTest(raw=raw):
                self.output.write_bytes(raw)
                self.rejected(self.evaluate('--output-file', self.output), 'benchmark output',
                              ' text contains unsupported controls')

    def test_suite_candidate_controls_use_output_context(self):
        self.output.write_bytes(b'GOOD\x1b[31mPRIVATE')
        self.rejected(['benchmark', 'suite', '--manifest', str(self.suite)],
                      'benchmark output', ' text contains unsupported controls')

    def test_feedback_regression_controls_keep_feedback_context(self):
        self.output.write_bytes(b'GOOD\x1b[31mPRIVATE')
        self.rejected(['feedback', 'regression', '--case', str(self.casefile),
                       '--output-file', str(self.output)], 'feedback', ' text contains unsupported controls')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX FIFO')
    def test_fifo_regular_file_requirement_unchanged(self):
        fifo = self.root / 'PRIVATE-fifo'
        os.mkfifo(fifo)
        self.rejected(['feedback', 'validate', '--file', str(fifo)],
                      'feedback', ' input requires a regular file')
        self.rejected(['orchestrate', '--dry-run', '--plan', str(fifo)],
                      'orchestration plan', ' input requires a regular file')
        self.rejected(self.evaluate('--output-file', fifo), 'benchmark output',
                      ' input requires a regular file')

    def test_exact_file_limit_is_still_accepted_for_every_domain(self):
        for path in (self.plan, self.spec, self.casefile, self.runtime, self.suite, self.output):
            raw = path.read_bytes()
            path.write_bytes(raw + b' ' * (JSON_LIMIT - len(raw)))
        commands = [['orchestrate', '--dry-run', '--plan', str(self.plan)],
            self.evaluate('--spec', self.spec), self.evaluate('--case', self.casefile),
            self.evaluate('--runtime-file', self.runtime),
            ['benchmark', 'suite', '--manifest', str(self.suite)]]
        for args in commands:
            with self.subTest(command=args[:2]):
                rc, out, err = self.invoke(args)
                self.assertEqual((rc, err), (0, ''))
                self.assertTrue(out)

    def test_evaluation_pass_fail_and_whitespace_semantics_unchanged(self):
        for candidate, code in ((b'  GOOD\t\n', 0), (b'OTHER', 1), (b'', 1)):
            self.output.write_bytes(candidate)
            for args in (self.evaluate('--spec', self.spec),
                         ['feedback', 'regression', '--case', str(self.casefile),
                          '--output-file', str(self.output)]):
                with self.subTest(candidate=candidate, command=args[0]):
                    rc, out, err = self.invoke(args + ['--json'])
                    self.assertEqual((rc, err), (code, ''))
                    self.assertEqual(json.loads(out)['passed'], code == 0)
