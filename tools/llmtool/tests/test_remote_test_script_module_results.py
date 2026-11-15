"""#2119 WP-1 step 3: the EC2 _REMOTE_SCRIPT emits module_results with last-match ran."""
import ast
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import remote_test as rt  # noqa: E402

SHA = 'b' * 40
LOG_PATH = '/tmp/llmtool-remote-log.Abc123/remote-test.log'


def script_module_result():
    parsed = ast.parse(rt._REMOTE_SCRIPT)
    found = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
    helper = next(node for node in found if node.name == 'module_result')
    namespace = dict(re=re)
    code = compile(ast.Module(body=[helper], type_ignores=[]), '<remote-module-result>', 'exec')
    exec(code, namespace)
    return namespace['module_result']


def compiled(nodes, label):
    return compile(ast.Module(body=list(nodes), type_ignores=[]), label, 'exec')


def emission_parts():
    """The real _REMOTE_SCRIPT statements that build module_results, compiled for exec."""
    parsed = ast.parse(rt._REMOTE_SCRIPT)
    nodes = list(ast.walk(parsed))
    text = ast.unparse
    init = [node for node in parsed.body if text(node) == 'module_results = []']
    helper = [node for node in parsed.body if getattr(node, 'name', '') == 'module_result']
    loops = [node for node in nodes if isinstance(node, ast.For)]
    body = next(loop.body for loop in loops if 'entry = module_result(' in text(loop))
    start = next(i for i, node in enumerate(body) if text(node).startswith('entry = '))
    ifs = [node for node in nodes if isinstance(node, ast.If)]
    emit = [node for node in ifs if text(node.test) == 'module_results']
    setup = compiled(init + helper, '<emit-init>')
    step = compiled(body[start:start + 3], '<emit-loop>')
    return setup, step, compiled(emit, '<emit-receipt>')


class ScriptModuleResultTests(unittest.TestCase):
    def setUp(self):
        self.module_result = script_module_result()

    def test_last_ran_line_wins_over_nested_output(self):
        output = b'Ran 3 tests in 0.100s\nOK\n...\nRan 12 tests in 1.000s\n\nOK\n'
        entry = self.module_result('test_a', output, 'OK')
        self.assertEqual(entry, dict(module='test_a', ran=12, skipped=0, final='OK'))

    def test_class_level_skip_gives_ran_zero_skipped_one(self):
        output = b'Ran 0 tests in 0.000s\n\nOK (skipped=1)\n'
        entry = self.module_result('test_chat_tui_pty', output, 'OK (skipped=1)')
        self.assertEqual((entry['ran'], entry['skipped']), (0, 1))

    def test_no_ran_line_gives_zero(self):
        entry = self.module_result('test_a', b'ImportError: boom\n', 'NO_FINAL')
        self.assertEqual((entry['ran'], entry['skipped'], entry['final']), (0, 0, 'NO_FINAL'))

    def test_emitted_entries_pass_the_validator(self):
        out = b'Ran 2 tests in 0.1s\n\nOK (skipped=1)\n'
        first = self.module_result('test_a', out, 'OK (skipped=1)')
        second = self.module_result('test_b', b'Ran 3 tests in 0.1s\n\nOK\n', 'OK')
        body = dict(result='PASS', exit_code=0, source_sha=SHA, tree='c' * 40, fetch_head=SHA)
        body.update(modules=['test_a', 'test_b'], identity=dict(uid=65534), elapsed_s=1.0)
        body.update(started_at='2026-11-16T00:00:00Z', ended_at='2026-11-16T00:00:01Z')
        body.update(ran_total=5, final=['OK (skipped=1)', 'OK'], failures=[], log_bytes=0)
        body.update(log_sha256='d' * 64, remote_log_path=LOG_PATH, module_results=[first, second])
        raw = json.dumps(body, separators=(',', ':')).encode()
        parsed = rt._validate_remote_receipt(raw, sha=SHA, modules=('test_a', 'test_b'))
        self.assertEqual(parsed['module_results'], [first, second])

    def test_script_emits_the_field_and_drops_first_match_aggregation(self):
        script = rt._REMOTE_SCRIPT
        self.assertIn('entry=module_result(mod,test_output,final)', script)
        self.assertIn('module_results.append(entry); ran+=entry["ran"]', script)
        self.assertIn('module_results=[]\ndef failure_excerpt', script)
        emit = 'if module_results: receipt["module_results"]=module_results[-len(mods)*2:]'
        self.assertIn(emit, script)
        self.assertNotIn('\nreceipt["module_results"]=', script)
        self.assertNotIn('ran+=int(re.search(rb"Ran', script)

    def run_emission(self, runs):
        setup, step, emit = emission_parts()
        namespace = dict(re=re, ran=0)
        exec(setup, namespace)
        for mod, output, final in runs:
            namespace.update(mod=mod, test_output=output, final=final)
            exec(step, namespace)
        finals = [run[2] for run in runs]
        namespace.update(mods=[run[0] for run in runs], receipt=dict(final=finals))
        exec(emit, namespace)
        return namespace['receipt'], namespace['ran']

    def test_script_statements_emit_validator_consistent_module_results(self):
        runs = [('test_a', b'Ran 1 tests in 0.1s\nRan 4 tests in 0.2s\n\nOK\n', 'OK')]
        runs.append(('test_b', b'Ran 0 tests in 0.0s\n\nOK (skipped=1)\n', 'OK (skipped=1)'))
        receipt, ran = self.run_emission(runs)
        self.assertEqual(ran, 4)
        self.assertEqual([entry['ran'] for entry in receipt['module_results']], [4, 0])
        self.assertEqual([entry['skipped'] for entry in receipt['module_results']], [0, 1])
        body = dict(result='PASS', exit_code=0, source_sha=SHA, tree='c' * 40, fetch_head=SHA)
        body.update(modules=['test_a', 'test_b'], identity=dict(uid=65534), elapsed_s=1.0)
        body.update(started_at='2026-11-16T00:00:00Z', ended_at='2026-11-16T00:00:01Z')
        body.update(ran_total=ran, final=receipt['final'], failures=[], log_bytes=0)
        body.update(log_sha256='d' * 64, remote_log_path=LOG_PATH)
        body['module_results'] = receipt['module_results']
        raw = json.dumps(body, separators=(',', ':')).encode()
        parsed = rt._validate_remote_receipt(raw, sha=SHA, modules=('test_a', 'test_b'))
        self.assertEqual(parsed['module_results'], receipt['module_results'])

    def test_script_statements_omit_the_field_when_no_module_ran(self):
        receipt, ran = self.run_emission([])
        self.assertEqual((ran, receipt), (0, dict(final=[])))


if __name__ == '__main__':
    unittest.main()
