"""Truncated model replies are dropped and retried within normal budgets."""
from __future__ import annotations

import json

from helpers import GatewayCase, reply, tool_call
from api_agent_gateway.audit import canonical
from api_agent_gateway.contract import parse_profile, parse_task


class LengthReplyTests(GatewayCase):
    def test_truncated_tool_call_is_never_executed_or_retained(self):
        partial = 'TRUNCATED_ARGUMENT_SENTINEL'
        self.server.script = [
            reply(tool_call('apply_edit', path='src/app.py', old='hello', new=partial),
                  finish_reason='length', reasoning_content='PRIVATE_REASONING_SENTINEL'),
            reply(tool_call('finish', summary='completed')),
        ]

        code, result, audit = self.run_task()

        self.assertEqual((code, result['status']), (0, 'DONE'))
        self.assertIn('hello', self.read('src/app.py'))
        second_messages = self.server.requests[1]['messages']
        self.assertEqual(second_messages[-1], {
            'role': 'user',
            'content': 'Your previous response was truncated by the output limit. '
                       'Make one short tool call now; do not repeat or refer to the truncated response.',
        })
        self.assertNotIn(partial, self.server.raw[1].decode())
        self.assertNotIn('PRIVATE_REASONING_SENTINEL', self.server.all_text())
        self.assertNotIn('PRIVATE_REASONING_SENTINEL', json.dumps(result))
        self.assertNotIn(partial.encode(), self.state_bytes())
        with open(audit) as fh:
            audit_text = fh.read()
            records = [json.loads(line) for line in audit_text.splitlines()]
        self.assertNotIn('PRIVATE_REASONING_SENTINEL', audit_text)
        calls = [r for r in records if r['type'] == 'model_call']
        self.assertEqual(calls[0]['finish_reason'], 'length')
        self.assertFalse(any(r['type'] == 'tool' and r['turn'] == 1 and r['ok'] for r in records))
        self.assert_audit_ok(audit, result)

    def test_repeated_length_replies_end_with_distinct_budget_code(self):
        self.server.script = [reply(content='partial', finish_reason='length') for _ in range(3)]

        code, result, audit = self.run_task()

        self.assertEqual((code, result['status'], result['code']),
                         (6, 'BUDGET_EXCEEDED', 'OUTPUT_TRUNCATED'))
        self.assertEqual(result['turns'], 3)
        self.assertEqual(len(self.server.requests), 3)
        self.assert_audit_ok(audit, result)

    def test_text_truncations_do_not_count_as_no_tool_replies(self):
        self.server.script = [reply(content='part one', finish_reason='length'),
                              reply(content='part two', finish_reason='length'),
                              reply(tool_call('finish', summary='done'))]

        code, result, audit = self.run_task()

        self.assertEqual((code, result['status'], result['code']), (0, 'DONE', None))
        self.assertEqual(result['turns'], 3)
        self.assert_audit_ok(audit, result)

    def test_non_length_reply_resets_consecutive_length_count(self):
        self.write_profiles(max_length_retries=1)
        self.server.script = [reply(content='partial', finish_reason='length'),
                              reply(tool_call('list_dir', path='src')),
                              reply(content='partial', finish_reason='length'),
                              reply(tool_call('finish', summary='done'))]

        code, result, audit = self.run_task()

        self.assertEqual((code, result['status']), (0, 'DONE'))
        self.assertEqual(result['turns'], 4)
        self.assert_audit_ok(audit, result)

    def test_profile_request_extra_is_sent_and_digest_bound(self):
        option = {'chat_template_kwargs': {'enable_thinking': False}}
        self.write_profiles(request_extra=option)
        profile_file = {'schema_version': 1, 'profiles': {'fake': dict(self.profile, request_extra=option)}}
        first = parse_profile(profile_file, 'fake')
        changed = dict(self.profile, request_extra={'chat_template_kwargs': {'enable_thinking': True}})
        second = parse_profile({'schema_version': 1, 'profiles': {'fake': changed}}, 'fake')
        self.assertNotEqual(first.sha256, second.sha256)
        self.server.script = [reply(tool_call('list_dir', path='src')),
                              reply(tool_call('finish', summary='done'))]
        self.assertEqual(self.run_task()[0], 0)
        self.assertEqual(len(self.server.requests), 2)
        for request in self.server.requests:
            self.assertEqual(request['chat_template_kwargs'], option['chat_template_kwargs'])

        # A session created with one profile cannot resume under a changed profile.
        self.write_profiles(request_extra=option)
        self.server.script = [reply(tool_call('finish', summary='round one'))]
        _, result, _ = self.run_task()
        self.write_profiles(request_extra=changed['request_extra'])
        code, resumed, audit = self.resume(result['session_id'], self.task())
        self.assertEqual((code, resumed['status'], resumed['code']),
                         (5, 'POLICY_REFUSED', 'SESSION_PROFILE_MISMATCH'))
        self.assert_audit_ok(audit, resumed)

    def test_scope_digest_golden_vector(self):
        task_obj = self.task()
        raw = canonical(task_obj)
        task = parse_task(task_obj, raw)
        self.assertEqual(task.scope_digest(),
                         '94382345ddbdf0c5d1ff048b51e2343d0eb842e8dac2bc5df08ddd7479b7ff26')


if __name__ == '__main__':
    import unittest
    unittest.main()
