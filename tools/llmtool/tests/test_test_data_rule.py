"""Every llmtool Developer route carries the Owner #763 A test-data rule (#1197)."""

import ast
import contextlib
import copy
import inspect
import json
import unittest
from unittest.mock import patch

from llmtool import high_debug, task_submission_cli
from llmtool import test_data_rule
from llmtool.test_data_rule import TEST_DATA_RULE

# The wiring harnesses are reused as modules; their TestCase classes are never
# imported by name, so this module does not collect and rerun their tests.
import test_agy_route_wiring as agy_wiring
import test_claude_route_wiring as claude_wiring
import test_gw_agent_route_wiring as gw_wiring


def setUpModule():
    # The three wiring modules share one review trust fixture; start it once.
    gw_wiring.setUpModule()


def tearDownModule():
    gw_wiring.tearDownModule()


_TASK = {'task_id': 'a' * 32, 'run_id': 'b' * 32, 'pm_session_id': 'pm-rule',
         'task_binding': {'contract_digest': 'c' * 64},
         'task_contract': {'schema_version': 1, 'goal': 'Make the bounded change.'}}
_REPAIR_REFERENCE = {'opaque': 'repair reference'}


class TestDataRuleConstantTests(unittest.TestCase):
    def test_rule_is_one_shared_str_built_from_its_two_parts(self):
        self.assertIs(type(TEST_DATA_RULE), str)
        self.assertEqual(TEST_DATA_RULE,
                         test_data_rule.PART_1 + ' ' + test_data_rule.PART_2)
        self.assertTrue(TEST_DATA_RULE.startswith('TEST DATA RULE (Owner #763 A): '))
        self.assertTrue(TEST_DATA_RULE.endswith('return BLOCKED.'))
        public = {name for name in vars(test_data_rule) if not name.startswith('_')}
        self.assertEqual(public, {'PART_1', 'PART_2', 'TEST_DATA_RULE'})

    def test_task_submission_cli_uses_the_shared_constant(self):
        self.assertIs(task_submission_cli.TEST_DATA_RULE, TEST_DATA_RULE)
        source = inspect.getsource(task_submission_cli)
        self.assertNotIn(test_data_rule.PART_1, source)
        self.assertNotIn(test_data_rule.PART_2, source)

    def test_rule_passes_the_production_screens(self):
        self.assertEqual(task_submission_cli._screen_text(TEST_DATA_RULE, 4096),
                         TEST_DATA_RULE)
        self.assertEqual(high_debug._screen_hits(TEST_DATA_RULE), [])


class TextBuilderRuleTests(unittest.TestCase):
    def test_company_gw_and_codex_cli_system_messages_carry_the_rule_once(self):
        for route in ('company-gw', 'codex-cli'):
            with self.subTest(route=route):
                system, user = task_submission_cli._developer_prompt(
                    _TASK, source_sha='d' * 40, read_paths=['example.py'],
                    write_paths=['example.py'], acceptance=['ok'],
                    context='provided source text', pm_goal='g',
                    developer_route=route)
                self.assertEqual(system['role'], 'system')
                self.assertEqual(system['content'].count(TEST_DATA_RULE), 1)
                self.assertEqual(user['content'].count(TEST_DATA_RULE), 0)

    def test_opencode_prompt_carries_the_rule_once(self):
        for repair_packet in (None, _REPAIR_REFERENCE):
            with self.subTest(repair=repair_packet is not None), \
                    patch.object(task_submission_cli, '_validate_repair_packet',
                                 return_value=_REPAIR_REFERENCE):
                prompt = task_submission_cli._opencode_developer_prompt(
                    _TASK, source_sha='d' * 40, read_paths=['example.py'],
                    write_paths=['example.py'], acceptance=['ok'], pm_goal='g',
                    repair_packet=repair_packet)
                self.assertEqual(prompt.count(TEST_DATA_RULE), 1)
                self.assertLess(prompt.index(TEST_DATA_RULE),
                                prompt.index('TASK_PACKET_JSON='))

    def test_codex_direct_prompt_carries_the_rule_once_including_repair_runs(self):
        for policy in ('LOCAL', 'REMOTE_RECEIPT'):
            for repair_packet in (None, _REPAIR_REFERENCE):
                with self.subTest(policy=policy, repair=repair_packet is not None), \
                        patch.object(task_submission_cli, '_remote_receipt_test_policy',
                                     return_value=policy), \
                        patch.object(task_submission_cli, '_validate_repair_packet',
                                     return_value=_REPAIR_REFERENCE):
                    prompt = task_submission_cli._codex_direct_prompt(
                        copy.deepcopy(_TASK), source_sha='d' * 40,
                        read_paths=['example.py'], write_paths=['example.py'],
                        acceptance=['ok'], pm_goal='g', model='codex-test',
                        reasoning_effort='high', repair_packet=repair_packet)
                    self.assertEqual(prompt.count(TEST_DATA_RULE), 1)
                    self.assertLess(prompt.index(TEST_DATA_RULE),
                                    prompt.index('TASK_CONTRACT_JSON='))
                    if repair_packet is not None:
                        self.assertIn('untrusted_repair_reference', prompt)


class DirectRouteRuleTests(unittest.TestCase):
    def test_claude_direct_prompt_carries_the_rule_once_before_skill_text(self):
        harness = claude_wiring.ClaudeRouteWiringTests()
        cli, store, args, _reg, _evaluations, prompts, _requests, patches, runner = (
            harness._setup())
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        self.assertEqual(status, 0)
        prompt = prompts[0]
        self.assertEqual(prompt.count(TEST_DATA_RULE), 1)
        self.assertLess(prompt.index(TEST_DATA_RULE), prompt.index('REQUIRED_SKILL_BEGIN'))
        self.assertLess(prompt.index(TEST_DATA_RULE), prompt.index('\nTASK_JSON='))
        self.assertEqual(store.task['developer']['acceptance'], ['record the bounded result'])

    def test_agy_direct_prompt_carries_the_rule_once_before_skill_text(self):
        harness = agy_wiring.AgyRouteWiringTests()
        status, task, _events, prompts = harness._run_agy_delegate()
        self.assertEqual(status, 0)
        prompt = prompts[0]
        self.assertEqual(prompt.count(TEST_DATA_RULE), 1)
        self.assertLess(prompt.index(TEST_DATA_RULE), prompt.index('REQUIRED_SKILL_BEGIN'))
        self.assertLess(prompt.index(TEST_DATA_RULE), prompt.index('\nTASK_JSON='))
        self.assertEqual(task['developer']['acceptance'], ['the result is recorded'])

    def test_gw_agent_prompt_and_task_acceptance_carry_the_rule_once(self):
        case = gw_wiring.GwAgentRealStoreTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        seen = []

        def capture(route, request, **kwargs):
            seen.append(copy.deepcopy(request))
            return task_submission_cli.run_route(route, request, **kwargs)

        target = 'gw-agent-test-data-rule.md'
        code, task = case._delegate(target, route_runner=capture)
        self.assertEqual(code, 0)
        case._assert_success_persisted(task, target)
        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request['prompt'].count(TEST_DATA_RULE), 1)
        self.assertLess(request['prompt'].index(TEST_DATA_RULE),
                        request['prompt'].index('\nTASK_JSON='))
        packet = json.loads(request['prompt'].split('\nTASK_JSON=', 1)[1])
        self.assertEqual(packet['acceptance'], [f'Only {target} is created.'])
        self.assertEqual(request['task']['acceptance'],
                         [f'Only {target} is created.', TEST_DATA_RULE])
        # The stored Developer record keeps the PM acceptance unchanged.
        self.assertEqual(task['developer']['acceptance'], [f'Only {target} is created.'])


class RouteBuilderGuardTests(unittest.TestCase):
    _MARKERS = ('Developer for this bounded llmtool Task', ' code Developer')
    _BUILDERS = {'_developer_prompt', '_opencode_developer_prompt', '_codex_direct_prompt',
                 'run_task_agy_delegate', 'run_task_claude_delegate',
                 'run_task_gw_agent_delegate'}

    def test_every_developer_prompt_builder_references_the_rule(self):
        tree = ast.parse(inspect.getsource(task_submission_cli))
        builders = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            texts = [item.value for item in ast.walk(node)
                     if isinstance(item, ast.Constant) and type(item.value) is str]
            if not any(marker in text for text in texts for marker in self._MARKERS):
                continue
            builders.add(node.name)
            names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
            with self.subTest(function=node.name):
                self.assertIn('TEST_DATA_RULE', names)
        self.assertTrue(self._BUILDERS <= builders, sorted(self._BUILDERS - builders))


if __name__ == '__main__':
    unittest.main()
