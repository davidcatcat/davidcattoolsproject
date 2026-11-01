"""Budget exhaustion (distinct codes) and context compaction."""
from __future__ import annotations

import json

from helpers import GatewayCase, reply, tool_call


class BudgetTests(GatewayCase):
    def budgets(self, **overrides):
        b = dict(self.task()['budgets'])
        b.update(overrides)
        return self.task(budgets=b)

    def assert_budget(self, result_tuple, expected):
        code, result, audit = result_tuple
        self.assertEqual((code, result['status'], result['code']), (6, 'BUDGET_EXCEEDED', expected))
        self.assert_audit_ok(audit, result)
        return result

    def test_turn_budget(self):
        self.server.script = [reply(tool_call('list_dir', path='src'))] * 5
        result = self.assert_budget(self.run_task(self.budgets(max_turns=2)), 'TURN_BUDGET_EXCEEDED')
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(result['turns'], 2)

    def test_sent_bytes_budget_stops_before_sending(self):
        self.assert_budget(self.run_task(self.budgets(max_sent_bytes=1024)), 'SENT_BYTES_EXCEEDED')
        self.assertEqual(self.server.requests, [])

    def test_input_token_budget(self):
        self.assert_budget(self.run_task(self.budgets(max_input_tokens=10)), 'INPUT_TOKEN_BUDGET_EXCEEDED')
        self.assertEqual(self.server.requests, [])
        self.server.script = [reply(tool_call('list_dir', path='src'), usage=(900, 5))] * 3
        self.assert_budget(self.run_task(self.budgets(max_input_tokens=1500)), 'INPUT_TOKEN_BUDGET_EXCEEDED')

    def test_output_token_budget(self):
        self.server.script = [reply(tool_call('apply_edit', path='src/app.py', old='hello', new='x'),
                                    usage=(10, 500))]
        self.assert_budget(self.run_task(self.budgets(max_output_tokens=100)), 'OUTPUT_TOKEN_BUDGET_EXCEEDED')
        # Tool calls from the over-budget reply were not executed.
        self.assertIn('hello', self.read('src/app.py'))
        self.assertEqual(self.server.requests[0]['max_tokens'], 100)

    def test_wall_time_budget(self):
        now = [1_000_000.0]

        def clock():
            now[0] += 40
            return now[0]
        self.server.script = [reply(tool_call('list_dir', path='src'))] * 5
        self.assert_budget(self.run_task(self.budgets(max_wall_seconds=100), clock=clock), 'WALL_TIME_EXCEEDED')

    def test_tool_result_cap_is_enforced_by_tools(self):
        big = 'x = 1\n' * 3000
        with open(f'{self.repo}/src/util.py', 'w') as fh:
            fh.write(big)
        self.server.script = [reply(tool_call('read_file', path='src/util.py')),
                              reply(tool_call('finish', summary='ok'))]
        code, result, _ = self.run_task(self.budgets(max_tool_result_bytes=1024))
        self.assertEqual(code, 0)
        tool = json.loads(self.server.requests[1]['messages'][-1]['content'])
        self.assertTrue(tool['truncated'])
        self.assertLessEqual(len(self.server.requests[1]['messages'][-1]['content'].encode()), 1024)

    def test_context_exhausted(self):
        self.write_profiles(context_cap_tokens=600, max_output_tokens=100)
        self.assert_budget(self.run_task(), 'CONTEXT_EXHAUSTED')
        self.assertEqual(self.server.requests, [])

    def test_compaction_keeps_contract_and_findings(self):
        self.write_profiles(context_cap_tokens=5000, max_output_tokens=200)
        with open(f'{self.repo}/src/util.py', 'w') as fh:
            fh.write('y = 2\n' * 600)
        task = self.task(findings=['FINDING-KEEP: fix the greeting'])
        self.server.script = ([reply(tool_call('read_file', path='src/util.py', max_lines=500))] * 4
                              + [reply(tool_call('finish', summary='ok'))])
        code, result, audit = self.run_task(task)
        self.assertEqual(code, 0, result)
        last = self.server.requests[-1]['messages']
        text = json.dumps(last)
        self.assertIn('GOALMARK', last[1]['content'])
        self.assertIn('FINDING-KEEP', text)
        self.assertIn('EARLIER CONVERSATION (compacted', text)
        with open(audit) as fh:
            records = [json.loads(line) for line in fh]
        compactions = [r for r in records if r['type'] == 'compaction']
        self.assertTrue(compactions)
        self.assertEqual(set(compactions[0]) - {'type', 'seq', 'prev_sha256'},
                         {'turn', 'removed_messages', 'summary_bytes', 'summary_sha256', 'redactions'})
        # Every request stayed within the cap estimate.
        for raw in self.server.raw:
            self.assertLessEqual(len(raw) / 4 + 200, 5000)
