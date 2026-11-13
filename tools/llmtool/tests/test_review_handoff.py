"""Red-first and integration tests for non-interactive Claude Reviewer handoff."""

import hashlib
import json
import multiprocessing
import os
import sys
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool import authorization_ledger as l
from llmtool import review_authority as a
from llmtool import review_handoff as rh
from llmtool import task_review as r
from llmtool.errors import LLMToolError
from llmtool.providers.claude_cli import decode_review_envelope
from test_review_authority import (
    FAKE_SCRIPT, FixtureObserver, LOAD_TOLERANT_TIMEOUT_SECONDS, WorkerHarness)
from llmtool import task_completion
from llmtool import worker_dispatch
import review_trust_fixture
import worker_policy_fixture


PROCESS_WAIT_SECONDS = 60


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


class _TestDispatchStore:
    def __init__(self):
        self.record = None

    def bind(self, task, source_sha):
        binding = task.to_dict()
        assignment = {
            'assignment_id': 'd' * 32, 'state': 'DEVELOPER_DISPATCHING',
            'provider_profile': 'company-gw', 'provider_type': 'openai-compatible',
            'model': 'gw-model', 'source_sha': source_sha,
            'direct_result_callback': {
                'review_handoff': {'dispatch_state': 'DISPATCHING'},
            },
        }
        audit_binding = {
            'assignment_id': assignment['assignment_id'],
            'source_sha': source_sha,
            'contract_digest': binding['contract_digest'],
        }
        transition = worker_dispatch.audit_entry(
            worker_dispatch.evaluate('claude-reviewer', task_id=binding['task_id']),
            'REVIEWER_HANDOFF', binding=audit_binding)
        self.record = {
            'schema_version': 2, 'task_id': binding['task_id'],
            'run_id': binding['run_id'], 'task_binding': binding,
            'developer': assignment, 'dispatch_audit': [transition],
        }

    def get(self, task_id):
        if self.record is None or task_id != self.record['task_id']:
            raise worker_dispatch.WorkerDispatchDenied('TEST_TASK_MISMATCH')
        return deepcopy(self.record)

    def append_dispatch_audit(self, task_id, entry, *, expected=None):
        if (self.record is None or task_id != self.record['task_id'] or
                type(expected) is not dict or
                any(self.record.get(key) != value for key, value in expected.items())):
            raise worker_dispatch.WorkerDispatchDenied('TEST_STORE_CONFLICT')
        self.record['dispatch_audit'].append(deepcopy(entry))
        return deepcopy(self.record)


def _mp_worker_dispatch(hostpath, pin_args, ready, final, expect, events, approval, packet_input, ledger_path, ledger_nonce, budget, barrier, result_queue, policy_path):
    """Subprocess worker for cross-process / concurrent barrier replay test."""
    # Spawned children do not inherit the parent's mock; never read a real trust.json.
    review_trust_fixture.start_module()
    # Nor the parent's policy patch (#1692): use the parent's shared test policy file.
    patch.object(worker_dispatch, '_POLICY_PATH', Path(policy_path)).start()
    try:
        from test_task_review import FakeHost
        fclock = FakeHost(ready, final, expect, events).clock
        pin = a.HostPin(*pin_args)
        observer = FixtureObserver(pin, expect, events, fclock,
                                   baseline_check=lambda reg, f: True,
                                   writers_check=lambda reg, f: True)
        context = a.ReviewContext(ready, final, expect, events, approval, packet_input)
        issuer = a.ProductionReviewIssuer(pin, fclock, observer, context)
        ledger = l.ReceiptLedger.open(ledger_path, clock=fclock.utc,
                                      kind='PROVIDER_REVIEW', creation_nonce=ledger_nonce)
        try:
            barrier.wait(timeout=PROCESS_WAIT_SECONDS)
            store = _TestDispatchStore()
            store.bind(expect.task, expect.source_sha)
            receipt, raw = rh.dispatch_review_handoff(
                issuer, ledger, budget=budget, dispatch_store=store)
            result_queue.put((receipt.verdict, receipt.execution_status, receipt.provider_calls, receipt.code))
        finally:
            ledger.close()
    except BaseException as exc:
        result_queue.put(('EXCEPTION', type(exc).__name__, 0, getattr(exc, 'review_code', 'ERR')))


class ReviewHandoffTests(unittest.TestCase):
    def setUp(self):
        # #1692: the shared test policy, never the live worker-dispatch.json.
        self.policy_path = worker_policy_fixture.install(self)
        self.dispatch_store = _TestDispatchStore()
        patcher = patch('llmtool.task_store.TaskStore', return_value=self.dispatch_store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def harness(self, **changes):
        changes.setdefault('timeout', LOAD_TOLERANT_TIMEOUT_SECONDS)
        h = WorkerHarness(**changes)
        self.addCleanup(h.close)
        self.dispatch_store.bind(h.expect.task, h.expect.source_sha)
        return h

    def test_paused_reviewer_policy_is_audited_and_provider_is_not_called(self):
        h = self.harness(mode='success')
        # The prior hand-off transition is recorded while the reviewer is ALLOWED.
        store = _TestDispatchStore()
        store.bind(h.expect.task, h.expect.source_sha)
        worker_policy_fixture.update(self.policy_path, 'claude-reviewer', 'PAUSED')
        with patch('llmtool.review_handoff.run_foreground_review') as invoke:
            receipt, raw = rh.dispatch_review_handoff(
                h.issuer, h.ledger, budget=h.fake.budget, dispatch_store=store)
        invoke.assert_not_called()
        self.assertEqual(receipt.code, 'OWNER_PAUSED')
        self.assertEqual(receipt.provider_calls, 0)
        self.assertEqual(raw['provider_calls'], 0)
        refusal = store.record['dispatch_audit'][-1]
        self.assertEqual(refusal['decision'], 'BLOCKED')
        self.assertEqual(refusal['worker_key'], 'claude-reviewer')
        self.assertEqual(refusal['reason_code'], 'OWNER_PAUSED')
        self.assertEqual(refusal['assignment_id'], 'd' * 32)
        self.assertEqual(refusal['source_sha'], h.expect.source_sha)
        self.assertEqual(refusal['contract_digest'], h.expect.task.contract_digest)

    def bind_harness_task_id(self, h, task_id):
        ready = replace(h.ready, task_id=task_id)
        expect = replace(h.expect, task=ready.binding(h.expect.task.run_id))
        events = tuple(replace(event, task=expect.task,
            evidence=tuple(replace(item, task=expect.task) for item in event.evidence),
            handoff=None if event.handoff is None else
                replace(event.handoff, task=expect.task)) for event in h.events)
        approval = replace(h.fake.approval, ready_task=expect.task)
        h.ready, h.expect, h.events = ready, expect, events
        h.fake.ready, h.fake.expect, h.fake.events = ready, expect, events
        h.fake.approval = approval
        h.fake.data['evidence'][0]['record'] = events[2].evidence[0].to_dict()
        h.packet_input = r.canonical(h.fake.data)
        h.fake.observation = replace(h.fake.observation, task=expect.task, events=events)
        h.pin = replace(h.pin, pair_digest=a.pair_pin(approval),
                        packet_input_digest=hashlib.sha256(h.packet_input).hexdigest())
        observer = FixtureObserver(h.pin, expect, events, h.clock,
                                   baseline_check=lambda reg, facts: True,
                                   writers_check=lambda reg, facts: True)
        context = a.ReviewContext(ready, h.final, expect, events, approval,
                                  h.packet_input)
        h.issuer = a.ProductionReviewIssuer(h.pin, h.clock, observer, context)
        self.dispatch_store.bind(h.expect.task, h.expect.source_sha)

    def test_task_completion_adapter_bridges_the_existing_host_handoff(self):
        h = self.harness(mode='success')
        self.bind_harness_task_id(h, '1' * 32)
        reviewer = next(agent for agent in h.expect.agents
                        if getattr(agent.role, 'value', agent.role) == 'REVIEWER')
        intent = task_completion.review_dispatch_intent(
            task_id=h.expect.task.task_id, run_id=h.expect.task.run_id,
            contract_digest=h.expect.task.contract_digest,
            developer_task_id='d6-developer-task',
            developer_identity='developer-agent',
            developer_session_ref='developer-session',
            source_sha=h.expect.source_sha, result_head=None,
            diff_sha256=h.fake.data['diff']['sha256'],
            changed_paths=h.fake.data['diff']['changed_paths'])
        adapter = rh.task_completion_adapter(
            h.issuer, h.ledger, budget=h.fake.budget,
            dispatch_store=self.dispatch_store)

        receipt = adapter(intent)

        task_completion.validate_review_dispatch_receipt(intent, receipt)
        self.assertEqual(receipt['verdict'], 'PASS', receipt)
        self.assertEqual(receipt['security'], 'PASS')
        self.assertEqual(receipt['functional'], 'PASS')
        self.assertEqual(receipt['execution_status'], 'SUCCEEDED')
        self.assertEqual(receipt['reviewer_task_id'], h.expect.task.task_id)
        self.assertEqual(receipt['reviewer_identity'], reviewer.agent_id)
        self.assertEqual(receipt['reviewer_session_ref'], reviewer.session_id)
        self.assertEqual(receipt['source_sha'], h.expect.source_sha)
        self.assertEqual(len(receipt['review_packet_digest']), 64)
        self.assertIsNotNone(h.issuer.foreground_services)

    def test_task_completion_adapter_rejects_context_mismatch_before_dispatch(self):
        h = self.harness(mode='success')
        self.bind_harness_task_id(h, '2' * 32)
        task_id = h.expect.task.task_id
        common = dict(
            task_id=task_id, run_id=h.expect.task.run_id,
            contract_digest=h.expect.task.contract_digest,
            developer_task_id='d6-developer-task',
            developer_identity='developer-agent',
            developer_session_ref='developer-session',
            source_sha=h.expect.source_sha, result_head=None,
            diff_sha256=h.fake.data['diff']['sha256'],
            changed_paths=h.fake.data['diff']['changed_paths'])
        mismatched_task = task_completion.review_dispatch_intent(
            task_id='f' * 32, run_id=h.expect.task.run_id,
            contract_digest=h.expect.task.contract_digest,
            developer_task_id='d6-developer-task',
            developer_identity='developer-agent',
            developer_session_ref='developer-session',
            source_sha=h.expect.source_sha, result_head=None,
            diff_sha256='a' * 64, changed_paths=['src/example.py'])
        mismatched_packet = task_completion.review_dispatch_intent(**{
            **common, 'diff_sha256': 'a' * 64,
            'changed_paths': ['src/example.py']})
        adapter = rh.task_completion_adapter(
            h.issuer, h.ledger, budget=h.fake.budget)

        with self.assertRaises(LLMToolError):
            adapter(mismatched_task)
        with self.assertRaises(LLMToolError):
            adapter(mismatched_packet)
        self.assertIsNone(h.issuer.foreground_services)

    def test_interactive_bracketed_paste_requires_enter_while_print_stdin_eof_submits_automatically(self):
        """Root-cause reproduction: interactive draft waits for CR; -p stdin EOF submits with 0 Enter."""
        # 1. Simulate an interactive TUI draft parser receiving bracketed paste without ASCII 13 (\r)
        paste_stream = b'\x1b[200~{"review_packet_data":{}}\n\x1b[201~'
        in_paste = False
        draft_buffer = bytearray()
        submitted_payload = None
        i = 0
        while i < len(paste_stream):
            if paste_stream[i:i+6] == b'\x1b[200~':
                in_paste = True
                i += 6
                continue
            if paste_stream[i:i+6] == b'\x1b[201~':
                in_paste = False
                i += 6
                continue
            byte = paste_stream[i:i+1]
            if not in_paste and byte == b'\r':
                submitted_payload = bytes(draft_buffer)
                break
            draft_buffer.extend(byte)
            i += 1
        self.assertIsNone(submitted_payload, 'Interactive bracketed paste must stay in draft without manual Enter')
        self.assertGreater(len(draft_buffer), 0)

        # 2. Dispatch through review_handoff -> claude -p (--print --output-format json) + stdin EOF
        # Have the spawned worker process record exact stdin EOF / trailing bytes to prove 0 Enter/CR was needed.
        h = self.harness(mode='success')
        stdin_audit = h.hostdir / 'stdin-audit.json'
        script = h.exe.read_text().replace(
            "v=json.loads(sys.stdin.read().split('\\n\\nUSER:\\n',1)[1])",
            "_raw_stdin=sys.stdin.read()\n"
            "_eof_after_read=(sys.stdin.read()=='')\n"
            f"open({str(stdin_audit)!r},'w').write(json.dumps({{'eof':_eof_after_read,'ends_with_cr':_raw_stdin.endswith('\\r'),'len':len(_raw_stdin)}}))\n"
            "v=json.loads(_raw_stdin.split('\\n\\nUSER:\\n',1)[1])"
        )
        h.exe.write_text(script)
        exe_sha = hashlib.sha256(h.exe.read_bytes()).hexdigest()
        h.hostdata['reviewer']['executable_sha256'] = exe_sha
        runtime_reg = replace(a.load_host_registration(h.pin), reviewer_executable_digest=exe_sha)
        h.policy = replace(h.policy, worker_runtime_digest=a.worker_runtime_pin(runtime_reg))
        h.hostdata['route_policies'] = [dict(policy=h.policy.to_dict(), revoked=False)]
        h.write_host()
        h.pin = replace(h.pin, host_digest=hashlib.sha256(h.hostpath.read_bytes()).hexdigest())
        h.observer = FixtureObserver(h.pin, h.expect, h.events, h.clock,
                                     baseline_check=lambda reg, f: True,
                                     writers_check=lambda reg, f: True)
        h.issuer = a.ProductionReviewIssuer(h.pin, h.clock, h.observer, h.context)

        receipt, raw = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
        self.assertEqual(receipt.verdict, 'PASS')
        self.assertEqual(receipt.execution_status, 'SUCCEEDED')
        self.assertEqual(receipt.transport_mode, 'CLAUDE_PRINT_JSON_STDIN_EOF')
        self.assertFalse(receipt.manual_enter_required)
        self.assertFalse(receipt.authorized)
        self.assertEqual(receipt.execution_disposition, 'STOP_NO_EXECUTION_AUTHORITY')
        self.assertEqual(receipt.review_authority, 'WORKER_DATA_ONLY')
        self.assertEqual(receipt.provider_calls, 1)
        self.assertEqual(receipt.wrapper_launches, 1)
        self.assertTrue(receipt.cleanup_confirmed)
        self.assertEqual(receipt.ledger_status, 'EFFECT_DONE')
        self.assertEqual(receipt.usage_status, 'KNOWN')
        self.assertEqual((receipt.input_tokens, receipt.output_tokens), (12, 8))
        self.assertEqual(receipt.source_sha, h.expect.source_sha)
        self.assertEqual(receipt.task_id, h.expect.task.task_id)
        self.assertEqual(receipt.run_id, h.expect.task.run_id)
        self.assertEqual(receipt.launch_nonce, h.issuer.reviewer_launch_nonce)
        self.assertEqual(receipt.runtime_session_id, 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
        self.assertEqual(receipt.identity_source, 'CLI_REPORTED_RUNTIME_ID')
        argv = json.loads(h.argv_file.read_text())
        self.assertEqual(
            argv,
            ['--print', '--output-format', 'json', '--no-session-persistence',
             '--safe-mode', '--tools', 'WebSearch,WebFetch', '--model', 'fake-model'],
        )
        tools = argv[argv.index('--tools') + 1].split(',')
        self.assertFalse({'Read', 'Write', 'Edit', 'Bash'}.intersection(tools))
        self.assertNotIn('--bare', argv)
        audit = json.loads(stdin_audit.read_text())
        self.assertTrue(audit['eof'])
        self.assertFalse(audit['ends_with_cr'])
        self.assertGreater(audit['len'], 0)

    def test_installed_claude_2_1_303_pin_and_telemetry_envelope_supported(self):
        """Red-first test: version 2.1.303 and real Claude CLI 2.1.303 JSON envelope keys must be accepted."""
        h = self.harness(mode='success')
        # Update host registration to version 2.1.303 and emit real 2.1.303 telemetry keys
        real_envelope_snippet = (
            "print(json.dumps(dict(type='result',subtype='success',is_error=False,"
            "session_id='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',result=json.dumps(out),"
            "api_error_status=None,duration_ms=18968,duration_api_ms=18656,"
            "fast_mode_disabled_reason='sdk_opt_in_required',fast_mode_state='off',"
            "first_content_frame_ms=1797,num_turns=1,permission_denials=[],"
            "queued_turn_count=0,result_index=0,safety_stops=0,stop_reason='end_turn',"
            "subagent_stats={'spawned':0},terminal_reason='completed',time_to_request_ms=208,"
            "total_cost_usd=0.30,ttft_ms=1987,ttft_stream_ms=1796,"
            "uuid='a9c25395-d0c0-4453-95d6-27d06c0c50fa',modelUsage={'claude':dict(tokens=10)},"
            "usage=dict(input_tokens=42,output_tokens=19,cache_creation=dict(ephemeral_5m_input_tokens=0),"
            "cache_creation_input_tokens=100,cache_read_input_tokens=200,fallback_credit=None,"
            "inference_geo='global',iterations=[],output_tokens_details=dict(reasoning_tokens=0),"
            "server_tool_use=dict(web_search_requests=0),service_tier='standard',speed='standard'))))"
        )
        script = FAKE_SCRIPT.replace('INTERPRETER', sys.executable).replace(
            'print(json.dumps(out))', real_envelope_snippet
        )
        h.exe.write_text(script)
        exe_sha = hashlib.sha256(h.exe.read_bytes()).hexdigest()
        h.hostdata['reviewer'].update(version='2.1.303', executable_sha256=exe_sha)
        runtime_reg = replace(a.load_host_registration(h.pin),
                              reviewer_role='llmtool-worker/claude-cli',
                              reviewer_version='2.1.303',
                              reviewer_executable_digest=exe_sha)
        h.policy = replace(h.policy, worker_runtime_digest=a.worker_runtime_pin(runtime_reg))
        h.hostdata['route_policies'] = [dict(policy=h.policy.to_dict(), revoked=False)]
        h.write_host()
        h.pin = replace(h.pin, host_digest=hashlib.sha256(h.hostpath.read_bytes()).hexdigest())
        h.observer = FixtureObserver(h.pin, h.expect, h.events, h.clock,
                                     baseline_check=lambda reg, f: True,
                                     writers_check=lambda reg, f: True)
        h.issuer = a.ProductionReviewIssuer(h.pin, h.clock, h.observer, h.context)

        receipt, raw = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
        self.assertEqual(receipt.verdict, 'PASS', raw)
        self.assertEqual(receipt.execution_status, 'SUCCEEDED')
        self.assertEqual(receipt.usage_status, 'KNOWN')
        self.assertEqual((receipt.input_tokens, receipt.output_tokens), (42, 19))

    def test_verdict_mapping_pass_fail_blocked_and_cli_exit_zero_not_approval(self):
        # 1. CHANGES_REQUIRED -> verdict='FAIL', execution_status='SUCCEEDED'
        h_fail = self.harness(mode='changes')
        r_fail, _ = rh.dispatch_review_handoff(h_fail.issuer, h_fail.ledger, budget=h_fail.fake.budget)
        self.assertEqual(r_fail.verdict, 'FAIL')
        self.assertEqual((r_fail.security, r_fail.functional), ('APPROVE', 'CHANGES_REQUIRED'))
        self.assertEqual(r_fail.execution_status, 'SUCCEEDED')
        self.assertFalse(r_fail.authorized)

        # 2. BLOCKED decision -> verdict='BLOCKED', execution_status='SUCCEEDED'
        h_blk = self.harness(mode='blocked')
        r_blk, _ = rh.dispatch_review_handoff(h_blk.issuer, h_blk.ledger, budget=h_blk.fake.budget)
        self.assertEqual(r_blk.verdict, 'BLOCKED')
        self.assertEqual((r_blk.security, r_blk.functional), ('APPROVE', 'BLOCKED'))
        self.assertEqual(r_blk.execution_status, 'SUCCEEDED')
        self.assertFalse(r_blk.authorized)

        # 3. CLI exit 0 with wrong inner SHA -> verdict='BLOCKED', execution_status='FAILED'
        h_bad = self.harness(mode='invalid-inner')
        r_bad, _ = rh.dispatch_review_handoff(h_bad.issuer, h_bad.ledger, budget=h_bad.fake.budget)
        self.assertEqual(r_bad.verdict, 'BLOCKED')
        self.assertEqual(r_bad.execution_status, 'FAILED')
        self.assertEqual(r_bad.code, 'REVIEW_VERDICT_INVALID')
        self.assertFalse(r_bad.authorized)

    def test_missing_usage_is_unknown_never_zero(self):
        h = self.harness(mode='success')
        script = h.exe.read_text().replace(
            ",usage=dict(input_tokens=12,output_tokens=8)", ""
        )
        h.exe.write_text(script)
        exe_sha = hashlib.sha256(h.exe.read_bytes()).hexdigest()
        h.hostdata['reviewer']['executable_sha256'] = exe_sha
        runtime_reg = replace(a.load_host_registration(h.pin), reviewer_executable_digest=exe_sha)
        h.policy = replace(h.policy, worker_runtime_digest=a.worker_runtime_pin(runtime_reg))
        h.hostdata['route_policies'] = [dict(policy=h.policy.to_dict(), revoked=False)]
        h.write_host()
        h.pin = replace(h.pin, host_digest=hashlib.sha256(h.hostpath.read_bytes()).hexdigest())
        h.observer = FixtureObserver(h.pin, h.expect, h.events, h.clock,
                                     baseline_check=lambda reg, f: True,
                                     writers_check=lambda reg, f: True)
        h.issuer = a.ProductionReviewIssuer(h.pin, h.clock, h.observer, h.context)

        receipt, _ = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
        # Missing usage is UNKNOWN, never zero, and is recorded telemetry only:
        # it cannot veto the otherwise valid, independently bound APPROVE verdict.
        self.assertEqual(receipt.code, 'REVIEW_APPROVED')
        self.assertEqual(receipt.verdict, 'PASS')
        self.assertEqual(receipt.execution_status, 'SUCCEEDED')
        self.assertEqual(receipt.provider_calls, 1)
        self.assertEqual(receipt.ledger_status, 'EFFECT_DONE')
        self.assertEqual(receipt.usage_status, 'UNKNOWN')
        self.assertIsNone(receipt.input_tokens)
        self.assertIsNone(receipt.output_tokens)
        self.assertFalse(receipt.authorized)

    def test_usage_over_self_imposed_token_target_still_passes_with_known_telemetry(self):
        h = self.harness(mode='success')
        budget = replace(h.fake.budget, max_input_tokens=1, max_output_tokens=1)
        receipt, _ = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=budget)
        self.assertEqual(receipt.verdict, 'PASS')
        self.assertEqual(receipt.execution_status, 'SUCCEEDED')
        self.assertEqual(receipt.usage_status, 'KNOWN')
        self.assertEqual((receipt.input_tokens, receipt.output_tokens), (12, 8))
        self.assertFalse(receipt.authorized)

    def test_replay_denied_across_same_issuer_fresh_issuer_and_reopened_ledger(self):
        h = self.harness(mode='success')
        r1, _ = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
        self.assertEqual(r1.verdict, 'PASS')
        self.assertEqual(r1.provider_calls, 1)

        # Same issuer replay -> blocked without spawning
        with patch('subprocess.Popen', side_effect=AssertionError('same-issuer replay')) as spawn:
            r2, _ = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
            spawn.assert_not_called()
        self.assertEqual(r2.verdict, 'BLOCKED')
        self.assertEqual(r2.code, 'WORKER_DISPATCH_AUTHORIZATION_CONSUMED')

        # Fresh issuer object + reopened ledger from disk -> blocked without spawning
        reopened = l.ReceiptLedger.open(
            h.ledger_path, clock=h.clock.utc, kind='PROVIDER_REVIEW',
            creation_nonce=h.ledger.creation_nonce
        )
        self.addCleanup(reopened.close)
        fresh_issuer = a.ProductionReviewIssuer(h.pin, h.clock, h.observer, h.context)
        with patch('subprocess.Popen', side_effect=AssertionError('fresh-issuer replay')) as spawn:
            r3, _ = rh.dispatch_review_handoff(fresh_issuer, reopened, budget=h.fake.budget)
            spawn.assert_not_called()
        self.assertEqual(r3.verdict, 'BLOCKED')
        self.assertEqual(r3.execution_status, 'BLOCKED')
        self.assertEqual(r3.provider_calls, 0)
        self.assertEqual(r3.code, 'WORKER_DISPATCH_AUTHORIZATION_CONSUMED')
        self.assertEqual(reopened._db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_concurrent_multiprocess_barrier_launches_at_most_once(self):
        h = self.harness(mode='success')
        spawn_counter = h.hostdir / 'spawn-count.txt'
        script = h.exe.read_text().replace(
            'import json,sys',
            'import json,sys,os\n'
            f'with open({str(spawn_counter)!r}, "a") as sf: sf.write("1\\n")'
        )
        h.exe.write_text(script)
        exe_sha = hashlib.sha256(h.exe.read_bytes()).hexdigest()
        h.hostdata['reviewer']['executable_sha256'] = exe_sha
        runtime_reg = replace(a.load_host_registration(h.pin), reviewer_executable_digest=exe_sha)
        h.policy = replace(h.policy, worker_runtime_digest=a.worker_runtime_pin(runtime_reg))
        h.hostdata['route_policies'] = [dict(policy=h.policy.to_dict(), revoked=False)]
        h.write_host()
        h.pin = replace(h.pin, host_digest=hashlib.sha256(h.hostpath.read_bytes()).hexdigest())

        ctx = multiprocessing.get_context('spawn')
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        pin_args = (h.pin.path, h.pin.host_digest, h.pin.authorizer_ref,
                    h.pin.pair_digest, h.pin.packet_input_digest, h.pin.repository_roots)
        procs = [
            ctx.Process(
                target=_mp_worker_dispatch,
                args=(str(h.hostpath), pin_args, h.ready, h.final, h.expect, h.events,
                      h.fake.approval, h.packet_input, str(h.ledger_path),
                      h.ledger.creation_nonce, h.fake.budget, barrier, queue,
                      str(self.policy_path))
            )
            for _ in range(2)
        ]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(timeout=PROCESS_WAIT_SECONDS)
            self.assertFalse(proc.is_alive())
        results = [queue.get(timeout=PROCESS_WAIT_SECONDS) for _ in range(2)]
        passes = [item for item in results if item[0] == 'PASS']
        blocked = [item for item in results if item[0] == 'BLOCKED']
        self.assertEqual(len(passes), 1, results)
        self.assertEqual(len(blocked), 1, results)
        lines = spawn_counter.read_text().splitlines() if spawn_counter.exists() else []
        self.assertEqual(len(lines), 1, 'Provider executable must be launched at most once across processes')

    def test_killed_handoff_recovers_consumed_receipt_without_relaunch(self):
        """A dead host leaves CONSUMED; the next open reports UNKNOWN and cannot relaunch."""
        h = self.harness(mode='success')
        marker = h.hostdir / 'reviewer-child.pid'
        h.exe.write_text(
            '#!' + sys.executable + '\nimport os,time\n'
            f'open({str(marker)!r}, "w").write(str(os.getpid()))\n'
            'time.sleep(60)\n'
        )
        exe_sha = hashlib.sha256(h.exe.read_bytes()).hexdigest()
        h.hostdata['reviewer']['executable_sha256'] = exe_sha
        runtime_reg = replace(a.load_host_registration(h.pin), reviewer_executable_digest=exe_sha)
        h.policy = replace(h.policy, worker_runtime_digest=a.worker_runtime_pin(runtime_reg))
        h.hostdata['route_policies'] = [dict(policy=h.policy.to_dict(), revoked=False)]
        h.write_host()
        h.pin = replace(h.pin, host_digest=hashlib.sha256(h.hostpath.read_bytes()).hexdigest())
        h.ledger.close()

        ctx = multiprocessing.get_context('spawn')
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        pin_args = (h.pin.path, h.pin.host_digest, h.pin.authorizer_ref,
                    h.pin.pair_digest, h.pin.packet_input_digest, h.pin.repository_roots)
        proc = ctx.Process(
            target=_mp_worker_dispatch,
            args=(str(h.hostpath), pin_args, h.ready, h.final, h.expect, h.events,
                  h.fake.approval, h.packet_input, str(h.ledger_path),
                  h.ledger.creation_nonce, h.fake.budget, barrier, queue,
                  str(self.policy_path))
        )
        proc.start()
        barrier.wait(timeout=PROCESS_WAIT_SECONDS)
        deadline = time.monotonic() + PROCESS_WAIT_SECONDS
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), 'reviewer child did not launch')
        reviewer_pid = int(marker.read_text())
        # A dead host is SIGKILL. SIGTERM is not: while the reviewer runs,
        # CLIProcessLifecycle routes SIGTERM to an orderly cancel, and
        # run_review's finally finalizes the receipt EFFECT_UNKNOWN itself
        # before the host exits, leaving nothing for recovery to do.
        proc.kill()
        proc.join(timeout=PROCESS_WAIT_SECONDS)
        self.assertFalse(proc.is_alive())
        self.assertEqual(proc.exitcode, -9)
        try:
            os.kill(reviewer_pid, 9)  # the reviewer runs in its own session
        except ProcessLookupError:
            pass

        reopened = l.ReceiptLedger.open(
            h.ledger_path, clock=h.clock.utc, kind='PROVIDER_REVIEW',
            creation_nonce=h.ledger.creation_nonce)
        self.addCleanup(reopened.close)
        h.observer = FixtureObserver(h.pin, h.expect, h.events, h.clock,
                                     baseline_check=lambda reg, facts: True,
                                     writers_check=lambda reg, facts: True)
        h.issuer = a.ProductionReviewIssuer(h.pin, h.clock, h.observer, h.context)
        rows = reopened._db.execute('SELECT nonce,status FROM receipts').fetchall()
        self.assertEqual(len(rows), 1)
        nonce, status = rows[0]
        # Reopen itself leaves the live ledger state alone. The handoff's
        # once-per-open recovery turns this abandoned CONSUMED row unknown
        # before attempting any redemption.
        self.assertEqual(status, 'CONSUMED')
        with patch.object(reopened, 'recover', wraps=reopened.recover) as recover:
            recover.assert_not_called()
            receipt, _ = rh.dispatch_review_handoff(
                h.issuer, reopened, budget=h.fake.budget, dispatch_store=self.dispatch_store)
            rows = reopened._db.execute('SELECT nonce,status FROM receipts').fetchall()
            self.assertEqual(len(rows), 1)
            recovered_nonce, status = rows[0]
            self.assertEqual(recovered_nonce, nonce)
            self.assertEqual(status, 'EFFECT_UNKNOWN')
            self.assertEqual(receipt.recovered_receipts, 1)
            recover.assert_called_once_with()
            self.assertEqual(marker.read_text(), str(reviewer_pid))
            second, _ = rh.dispatch_review_handoff(
                h.issuer, reopened, budget=h.fake.budget, dispatch_store=self.dispatch_store)
            self.assertEqual(second.recovered_receipts, 1)
            recover.assert_called_once_with()
        self.assertEqual(marker.read_text(), str(reviewer_pid))

    def test_timeout_nonzero_spoof_and_secret_failures_stay_unknown_or_blocked_without_leak(self):
        for mode in ('timeout', 'exit', 'spoof', 'private'):
            with self.subTest(mode=mode):
                h = self.harness(mode=mode, timeout=1)
                receipt, raw = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
                self.assertEqual(receipt.verdict, 'BLOCKED')
                self.assertEqual(receipt.execution_status, 'UNKNOWN')
                self.assertEqual(receipt.ledger_status, 'EFFECT_UNKNOWN')
                self.assertEqual(receipt.provider_calls, 1)
                self.assertFalse(receipt.authorized)
                serialized = json.dumps(receipt.to_dict()) + json.dumps(raw)
                self.assertNotIn('SECRET_FAKE', serialized)

    def test_receipt_dataclass_invariants_reject_forged_or_inconsistent_states(self):
        h = self.harness(mode='success')
        valid, _ = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
        self.assertEqual(len(valid.digest), 64)
        for bad_changes in (
            dict(authorized=True),
            dict(manual_enter_required=True),
            dict(execution_disposition='ALLOW'),
            dict(review_authority='REVIEW_DATA_ONLY'),
            dict(transport_mode='INTERACTIVE_PASTE'),
            dict(verdict='PASS', security='CHANGES_REQUIRED'),
            dict(verdict='FAIL', security='APPROVE', functional='APPROVE'),
            dict(verdict='PASS', execution_status='UNKNOWN', security='BLOCKED', functional='BLOCKED'),
            dict(execution_status='SUCCEEDED', cleanup_confirmed=False),
            dict(execution_status='SUCCEEDED', ledger_status='EFFECT_UNKNOWN'),
            dict(execution_status='UNKNOWN', verdict='PASS'),
            dict(provider_calls=1, execution_status='BLOCKED', verdict='BLOCKED', security='BLOCKED', functional='BLOCKED'),
            dict(usage_status='UNKNOWN', input_tokens=0, output_tokens=0),
            dict(usage_status='KNOWN', input_tokens=None, output_tokens=None),
        ):
            with self.subTest(bad_changes=bad_changes), self.assertRaises(LLMToolError):
                replace(valid, **bad_changes)

    def test_post_launch_anomalies_and_unreadable_ledger_never_return_blocked_execution_status(self):
        """M-1 regression: once provider_calls > 0, execution_status must be UNKNOWN or FAILED, never BLOCKED."""
        for anomaly in ('source_sha', 'launch_nonce', 'missing_evidence', 'changes_nonce_mismatch', 'unreadable_ledger', 'multiple_receipts'):
            with self.subTest(anomaly=anomaly):
                h = self.harness(mode='changes' if anomaly == 'changes_nonce_mismatch' else 'success')
                real_run = rh.run_foreground_review

                def mutated_run(issuer, ledger, *, budget):
                    res = real_run(issuer, ledger, budget=budget)
                    if anomaly == 'source_sha':
                        res['verdict'] = dict(res['verdict'], source_sha='9' * 40)
                    elif anomaly in ('launch_nonce', 'changes_nonce_mismatch'):
                        res['job_evidence'] = dict(res['job_evidence'], launch_nonce='f' * 32)
                    elif anomaly == 'missing_evidence':
                        res.pop('job_evidence', None)
                    elif anomaly == 'unreadable_ledger':
                        issuer.foreground_services.authority.receipts.clear()
                    elif anomaly == 'multiple_receipts':
                        issuer.foreground_services.authority.receipts['e' * 64] = object()
                    return res

                with patch.object(rh, 'run_foreground_review', side_effect=mutated_run):
                    receipt, _ = rh.dispatch_review_handoff(h.issuer, h.ledger, budget=h.fake.budget)
                self.assertEqual(receipt.provider_calls, 1)
                self.assertNotEqual(receipt.execution_status, 'BLOCKED', anomaly)
                self.assertEqual(receipt.execution_status, 'UNKNOWN', anomaly)
                self.assertEqual(receipt.verdict, 'BLOCKED')
                self.assertFalse(receipt.authorized)
                if anomaly in ('unreadable_ledger', 'multiple_receipts'):
                    self.assertEqual(receipt.ledger_status, 'EFFECT_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
