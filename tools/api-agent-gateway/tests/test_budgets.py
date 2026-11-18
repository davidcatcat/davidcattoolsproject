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

    def test_max_turns_accepts_512_and_documented_ceiling_1000(self):
        # #2281 L9: max_turns accepts 512 and up to documented ceiling 1000.
        for turns in (512, 1000):
            with self.subTest(turns=turns):
                self.server.script = [reply(tool_call('finish', summary='ok'))]
                code, result, _ = self.run_task(self.budgets(max_turns=turns))
                self.assertEqual(code, 0)
                self.assertEqual(result['status'], 'DONE')

    def test_max_turns_exceeding_ceiling_refused(self):
        # #2281 L9: max_turns above ceiling 1000 is refused as TASK_INVALID.
        code, result, _ = self.run_task(self.budgets(max_turns=1001))
        self.assertEqual((code, result['status'], result['code']), (5, 'POLICY_REFUSED', 'TASK_INVALID'))

    def test_sent_bytes_budget_stops_before_sending(self):
        self.assert_budget(self.run_task(self.budgets(max_sent_bytes=1024)), 'SENT_BYTES_EXCEEDED')
        self.assertEqual(self.server.requests, [])

    def test_token_budget_fields_are_accepted_and_not_enforced(self):
        # #2281: old callers may still send token budgets, but the run never stops on tokens.
        self.server.script = [reply(tool_call('list_dir', path='src'), usage=(900, 500)),
                              reply(tool_call('finish', summary='ok'), usage=(900, 500))]
        code, result, _ = self.run_task(self.budgets(max_input_tokens=10, max_output_tokens=10))
        self.assertEqual((code, result['status']), (0, 'DONE'), result)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(result['turns'], 2)
        self.assertEqual(result['budget_used']['input_tokens'], 1800)
        self.assertEqual(result['budget_used']['output_tokens'], 1000)

    def test_task_without_token_budget_fields_is_accepted(self):
        # #2281: a task that omits the token budgets is valid and runs normally.
        budgets = dict(self.task()['budgets'])
        for key in ('max_input_tokens', 'max_output_tokens'):
            budgets.pop(key, None)
        self.server.script = [reply(tool_call('finish', summary='ok'))]
        code, result, _ = self.run_task(self.task(budgets=budgets))
        self.assertEqual((code, result['status']), (0, 'DONE'), result)
        self.assertEqual(len(self.server.requests), 1)

    def test_invalid_token_budget_field_is_refused(self):
        # #2281: token budgets are optional, but a supplied value is still validated.
        code, result, _ = self.run_task(self.budgets(max_output_tokens=0))
        self.assertEqual((code, result['status'], result['code']), (5, 'POLICY_REFUSED', 'TASK_INVALID'))

    def test_wall_time_budget(self):
        now = [1_000_000.0]

        def clock():
            now[0] += 40
            return now[0]
        self.server.script = [reply(tool_call('list_dir', path='src'))] * 5
        task = self.budgets(max_wall_seconds=100)
        auth = self.auth_for(None, task, checked_at=now[0])
        self.assert_budget(self.run_task(task, clock=clock, auth=auth), 'WALL_TIME_EXCEEDED')

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
