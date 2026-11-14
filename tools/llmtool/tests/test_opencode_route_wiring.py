"""OpenCode Developer role-card delivery (#1502 WP-F).

The OpenCode delegate reuses the Claude/AGY mechanism: it pins main, reads
common core + Developer card from Git objects at the pin, sends
instructions + card prefix + TASK_PACKET_JSON and records a
CONTROLLER_DELIVERED receipt for the exact prefix after the run. Git object
reads are served in memory and the OpenCode provider is faked; no test here
reads GitHub main, starts a provider or touches the user's skill ledger.
No test value here looks like a credential (Owner #763 A).
"""

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import skill_ledger
from llmtool import skill_manifest
from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import DirectOpenCodeError, DirectResult

from test_claude_route_wiring import _CardGit, _filler, _wrapped


_MAIN_PIN = '9' * 40
_CORE = skill_manifest.COMMON_CORE_CARD_PATH
_DEV_CARD = skill_manifest.DEVELOPER_CARD_PATH
# Insertion order is prompt order: common core, then the Developer card.
_CARDS = {_CORE: 'Common core rules.\n', _DEV_CARD: 'Developer card rules.\n'}
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CARD_LIMIT = 6 * 1024
_TASK_ID = 'a' * 32
_RUN_ID = 'b' * 32
_HEAD = 'd' * 40
_MODEL = 'opencode/sample-model-free'
_SESSION = 'ses_Abc123'
_ACCEPTANCE = ['focused change is present']


def _sent_prefix(prompt):
    """The card prefix exactly as sent: first card marker up to TASK_PACKET_JSON."""
    return prompt[prompt.index('\nREQUIRED_SKILL_BEGIN path='):
                  prompt.index('\nTASK_PACKET_JSON=')]


def _result(session=_SESSION):
    return DirectResult(0, session, 'Updated fixture.', None, 'UNKNOWN', 'a' * 64, 32,
                        'CONFIRMED', 'opencode v2.0.32', 10, 1, 'b' * 64)


class _Store:
    """The Task store surface the OpenCode delegate uses; records every write."""

    def __init__(self, task):
        self.task = copy.deepcopy(task)
        self.writes = []

    def get(self, _task_id):
        return copy.deepcopy(self.task)

    def _write(self, name, updates):
        self.writes.append(name)
        self.task.update(copy.deepcopy(updates))
        return self.get(self.task['task_id'])

    def d6_bind_compare_and_update(self, _task_id, *, executor_task_id, expected, **updates):
        return self._write('d6_bind_compare_and_update', updates)

    def compare_and_update(self, _task_id, *, expected, **updates):
        return self._write('compare_and_update', updates)

    def update(self, _task_id, **updates):
        return self._write('update', updates)


class OpenCodeRouteWiringTests(unittest.TestCase):
    def _task(self, goal='Update the sample value.'):
        return {
            'schema_version': 2, 'task_id': _TASK_ID, 'run_id': _RUN_ID,
            'state': 'PM_RESPONDED', 'request': 'Update the sample value.',
            'response': 'Route the change to OpenCode.', 'developer': None,
            'completion': None, 'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'opencode', 'risk': 'LOW', 'goal': goal,
                              'acceptance': list(_ACCEPTANCE)},
        }

    def _run(self, *, outcome=None, cards=_CARDS, git_kwargs=None, goal=None,
             extra=(), real_ledger=False, expect_error=None):
        """Run the OpenCode delegate once; return what it sent and wrote.

        ``outcome`` is the fake provider's return value or the exception it
        raises (default: a successful result with a session).
        """
        task = self._task() if goal is None else self._task(goal)
        store = _Store(task)
        registration = SimpleNamespace(
            task_id='executor-d6', root='/registered/root', common_dir='/repo/.git',
            branch='task-branch', trusted_agent_id='opencode-developer',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(root=registration.root, common_dir=registration.common_dir,
                                   branch=registration.branch, head=_HEAD)
        args = Namespace(task_id=_TASK_ID, provider='opencode', selection_source='PM',
                         executor_task='executor-d6', read_path=['src.py'],
                         write_path=['src.py'], acceptance=list(_ACCEPTANCE),
                         egress_basis='Synthetic test context.', context_stdin=False,
                         timeout=None, repair_packet=None)
        outcome = _result() if outcome is None else outcome
        prompts = []
        writes_at_launch = []

        def provider_run(**kwargs):
            prompts.append(kwargs['prompt'])
            writes_at_launch.append(list(store.writes))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        git = _CardGit(cards, **(git_kwargs or {}))
        # A patched seam may inspect the run's store and Git reads mid-run.
        self.live = SimpleNamespace(store=store, git=git)
        patches = {
            'profile': patch.object(submission_cli, '_opencode_developer_profile',
                                    return_value=(_MODEL, 12)),
            'bind': patch.object(submission_cli.task_completion, 'bind_executor_task',
                                 return_value=(registration, snapshot)),
            'bootstrap': patch.object(submission_cli, '_bootstrap_registered_d6'),
            'refresh': patch.object(submission_cli, '_refresh_d6_binding_after_bootstrap',
                                    return_value=registration),
            'collect': patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                                    return_value=snapshot),
            'diff': patch.object(submission_cli.task_completion, '_worktree_diff',
                                 return_value=(['src.py'],
                                               b'diff --git a/src.py b/src.py\n')),
            'rejection': patch.object(submission_cli, '_developer_output_rejection',
                                      return_value=None),
            'paths': patch.object(submission_cli, '_assignment_paths',
                                  return_value=(['src.py'], ['src.py'])),
            'callback': patch.object(submission_cli, '_direct_result_callback',
                                     return_value={'state': 'WAITING_EXECUTOR'}),
            'session': patch.object(submission_cli, '_bind_direct_native_session',
                                    return_value=False),
            'event': patch.object(submission_cli, 'run_task_direct_result_event',
                                  return_value=0),
            'view': patch.object(submission_cli, '_task_view', return_value={}),
            'provider': patch.object(submission_cli.opencode_direct_developer, 'run',
                                     side_effect=provider_run),
            # Role cards go through the real build_role_cards() and
            # role_card_prefix(); only their Git object reads are in memory.
            'git': patch.object(submission_cli.skill_manifest, '_run_git', git),
            'generic': patch.object(submission_cli.skill_ledger, 'record',
                                    side_effect=AssertionError('generic ledger record used')),
        }
        if real_ledger:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            ledger_path = Path(temp.name) / 'ledger.jsonl'
            patches['ledger_path'] = patch.object(skill_ledger, '_path',
                                                  return_value=ledger_path)
            patches['main'] = patch.object(submission_cli.task_completion,
                                           'read_authenticated_main_sha',
                                           return_value=_MAIN_PIN)
        else:
            ledger_path = None
            patches['pin'] = patch.object(submission_cli.skill_ledger, 'pin_dispatch',
                                          return_value=_MAIN_PIN)
            patches['receipt'] = patch.object(submission_cli.skill_ledger,
                                              'record_card_delivery', return_value='receipt')
        patches.update(extra)
        stderr = io.StringIO()
        status = None
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(item) for name, item in patches.items()}
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            if expect_error is not None:
                with self.assertRaisesRegex(LLMToolError, expect_error):
                    submission_cli.run_task_opencode_delegate(
                        args, store=store, d6_binding=(registration, snapshot))
            else:
                status = submission_cli.run_task_opencode_delegate(
                    args, store=store, d6_binding=(registration, snapshot))
            verified = chain = None
            if real_ledger and prompts:
                verified = skill_ledger.check(
                    _TASK_ID, _RUN_ID, _SESSION, 'opencode-developer', 'developer',
                    'opencode', _MODEL, '/repo/.git', _HEAD,
                    hashlib.sha256(_sent_prefix(prompts[0]).encode('utf-8')).hexdigest())
            if real_ledger:
                chain = skill_ledger.verify_chain()
        rows = ([json.loads(line) for line in
                 ledger_path.read_text(encoding='utf-8').splitlines()]
                if ledger_path is not None and ledger_path.exists() else [])
        return SimpleNamespace(status=status, store=store, prompts=prompts, git=git,
                               writes_at_launch=writes_at_launch, mocks=mocks,
                               stderr=stderr.getvalue(), rows=rows, verified=verified,
                               chain=chain, args=args)

    def _assert_prewrite_refusal(self, message, **kwargs):
        run = self._run(expect_error=message, **kwargs)
        self.assertEqual(run.store.writes, [])
        self.assertIsNone(run.store.task['developer'])
        self.assertEqual(run.prompts, [])
        run.mocks['provider'].assert_not_called()
        return run

    def test_prompt_holds_exactly_the_pinned_cards_between_instructions_and_packet(self):
        run = self._run()
        self.assertEqual(run.status, 0)
        self.assertEqual(len(run.prompts), 1)
        prompt = run.prompts[0]
        prefix = _sent_prefix(prompt)
        # Exactly common core + Developer card; no legacy AGENTS.md core.
        self.assertEqual(prefix, _wrapped(_CARDS))
        self.assertNotIn('path=AGENTS.md', prompt)
        self.assertNotIn('path=skills/ai-team-delivery/SKILL.md', prompt)
        self.assertTrue(prompt.startswith('You are the OpenCode Developer'))
        rule = submission_cli.TEST_DATA_RULE
        self.assertEqual(prompt.count(rule), 1)
        self.assertLess(prompt.index(rule), prompt.index('\nREQUIRED_SKILL_BEGIN'))
        self.assertEqual(prompt.count('\nTASK_PACKET_JSON='), 1)
        # instructions + prefix + TASK_PACKET_JSON, nothing else.
        self.assertEqual(prompt, submission_cli._opencode_developer_prompt(
            self._task(), source_sha=_HEAD, read_paths=['src.py'], write_paths=['src.py'],
            acceptance=list(_ACCEPTANCE), pm_goal='Update the sample value.',
            card_prefix=prefix))
        packet = json.loads(prompt.split('\nTASK_PACKET_JSON=', 1)[1])
        self.assertEqual((packet['task_id'], packet['run_id'], packet['source_sha']),
                         (_TASK_ID, _RUN_ID, _HEAD))
        # The recorded context digest and size cover the final prompt.
        raw = prompt.encode('utf-8')
        developer = run.store.task['developer']
        self.assertEqual(developer['context_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(developer['input_bytes'], len(raw))
        self.assertLessEqual(len(raw), submission_cli.REQUEST_LIMIT)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')

    def test_cards_are_read_at_the_pin_before_any_store_write(self):
        state = {}

        def pin(*call_args):
            state.update(args=call_args, git_calls=len(self.live.git.calls),
                         writes=list(self.live.store.writes))
            return _MAIN_PIN

        run = self._run(extra={'pin': patch.object(
            submission_cli.skill_ledger, 'pin_dispatch', side_effect=pin)})
        self.assertEqual(run.status, 0)
        self.assertEqual(state['args'], (_TASK_ID, _RUN_ID, 'opencode-developer'))
        self.assertEqual(state['git_calls'], 0)
        self.assertEqual(state['writes'], [])
        reads = run.git.card_reads()
        self.assertEqual([args[-1] for args in reads], [_CORE, _DEV_CARD])
        self.assertTrue(all(args[3] == _MAIN_PIN for args in reads))
        # The Task was claimed only after the cards were built.
        self.assertEqual(run.writes_at_launch[0][0], 'd6_bind_compare_and_update')

    def test_receipt_digest_is_the_sha256_of_the_prefix_sent(self):
        run = self._run()
        self.assertEqual(run.status, 0)
        prefix = _sent_prefix(run.prompts[0])
        run.mocks['receipt'].assert_called_once_with({
            'task_id': _TASK_ID, 'run_id': _RUN_ID, 'session_id': _SESSION,
            'agent_id': 'opencode-developer', 'role': 'developer', 'provider': 'opencode',
            'model': _MODEL, 'repo': '/repo/.git', 'head': _HEAD,
            'manifest_sha256': hashlib.sha256(prefix.encode('utf-8')).hexdigest(),
            'route': 'opencode-developer',
        }, repo_root='/registered/root')
        run.mocks['generic'].assert_not_called()

    def test_real_ledger_receipt_binds_the_sent_prefix_and_the_pin(self):
        run = self._run(real_ledger=True)
        self.assertEqual(run.status, 0)
        self.assertEqual(run.chain, (True, None))
        self.assertEqual(run.verified['status'], 'VERIFIED')
        self.assertEqual(run.verified['evidence_level'], 'CONTROLLER_DELIVERED')
        run.mocks['main'].assert_called_once_with()
        self.assertEqual([row.get('entry_type') for row in run.rows], ['DISPATCH_PIN', None])
        pin, receipt = run.rows
        self.assertEqual((pin['task_id'], pin['run_id'], pin['route'], pin['base_commit']),
                         (_TASK_ID, _RUN_ID, 'opencode-developer', _MAIN_PIN))
        self.assertEqual(receipt['pin_id'], pin['receipt_id'])
        self.assertEqual(receipt['route'], 'opencode-developer')
        self.assertEqual(receipt['provider'], 'opencode')
        sent = _sent_prefix(run.prompts[0]).encode('utf-8')
        self.assertEqual(receipt['manifest_sha256'], hashlib.sha256(sent).hexdigest())
        self.assertEqual(receipt['cards'], [
            {'path': path, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
            for path, text in _CARDS.items()])

    def test_provider_error_with_a_session_records_the_receipt(self):
        error = DirectOpenCodeError('OPENCODE_PROVIDER_FAILED', attempted=True,
                                    cleanup_status='CONFIRMED', native_session_id=_SESSION)
        run = self._run(outcome=error)
        self.assertEqual(run.status, 1)
        self.assertEqual(run.store.task['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(run.store.task['developer']['error_code'],
                         'OPENCODE_PROVIDER_FAILED')
        prefix = _sent_prefix(run.prompts[0])
        run.mocks['receipt'].assert_called_once()
        entry = run.mocks['receipt'].call_args.args[0]
        self.assertEqual(entry['session_id'], _SESSION)
        self.assertEqual(entry['manifest_sha256'],
                         hashlib.sha256(prefix.encode('utf-8')).hexdigest())

    def test_provider_error_without_a_session_records_no_receipt(self):
        for error in (DirectOpenCodeError('OPENCODE_RUNTIME_UNAVAILABLE'),
                      DirectOpenCodeError('OPENCODE_PROVIDER_FAILED', attempted=True)):
            with self.subTest(attempted=error.attempted):
                run = self._run(outcome=error)
                self.assertEqual(run.status, 1)
                run.mocks['receipt'].assert_not_called()

    def test_ledger_failure_keeps_the_finished_run_and_reports_the_code(self):
        run = self._run(extra={'receipt': patch.object(
            submission_cli.skill_ledger, 'record_card_delivery',
            side_effect=skill_ledger.SkillLedgerError('CHAIN_BROKEN'))})
        self.assertEqual(run.status, 0)
        self.assertEqual(run.store.task['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIn('OPENCODE_SKILL_LEDGER_OUTCOME_UNKNOWN: CHAIN_BROKEN', run.stderr)

    def test_missing_card_at_the_pin_refuses_with_no_store_write_or_provider_call(self):
        for missing in (_CORE, _DEV_CARD):
            with self.subTest(missing=missing):
                cards = {path: text for path, text in _CARDS.items() if path != missing}
                run = self._assert_prewrite_refusal('OPENCODE_ROLE_CARD_MISSING', cards=cards)
                run.mocks['receipt'].assert_not_called()

    def test_failed_pin_refuses_with_no_store_write_or_provider_call(self):
        run = self._assert_prewrite_refusal(
            'OPENCODE_SKILL_PIN_FAILED: CHAIN_BROKEN',
            extra={'pin': patch.object(submission_cli.skill_ledger, 'pin_dispatch',
                                       side_effect=skill_ledger.SkillLedgerError(
                                           'CHAIN_BROKEN'))})
        self.assertEqual(run.git.calls, [])

    def test_failed_main_read_refuses_and_appends_nothing(self):
        run = self._assert_prewrite_refusal(
            'OPENCODE_SKILL_PIN_FAILED: MAIN_REF_PIN_INVALID', real_ledger=True,
            extra={'main': patch.object(submission_cli.task_completion,
                                        'read_authenticated_main_sha',
                                        side_effect=LLMToolError('MAIN_REF_PIN_INVALID'))})
        self.assertEqual(run.rows, [])

    def test_unsafe_or_off_main_cards_refuse_before_any_write(self):
        cases = (
            ('OPENCODE_SKILL_TEXT_REFUSED', {}, {**_CARDS, _CORE: 'rules\x01\n'}),
            (skill_manifest.SKILL_MANIFEST_NOT_FILE, {'modes': {_DEV_CARD: '120000'}}, _CARDS),
            (skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN, {'main': 'e' * 40}, _CARDS),
        )
        for code, git_kwargs, cards in cases:
            with self.subTest(code=code):
                self._assert_prewrite_refusal(code, cards=cards, git_kwargs=git_kwargs)

    def test_cards_are_built_only_after_the_existing_refusal_checks(self):
        refusal = LLMToolError('ROLE_MODEL_NOT_ALLOWED')
        run = self._assert_prewrite_refusal(
            'ROLE_MODEL_NOT_ALLOWED',
            extra={'profile': patch.object(submission_cli, '_opencode_developer_profile',
                                           side_effect=refusal)})
        run.mocks['pin'].assert_not_called()
        self.assertEqual(run.git.calls, [])

    def test_real_card_files_fit_the_14_kib_cap(self):
        # Real card files where the checkout has them; a card not yet on this
        # checkout is taken at the 6 KiB production ceiling instead.
        real = {}
        for path in (_CORE, _DEV_CARD):
            file = _REPO_ROOT / path
            real[path] = (file.read_text(encoding='utf-8') if file.is_file()
                          else _filler(_CARD_LIMIT))
        for text in real.values():
            self.assertLessEqual(len(text.encode('utf-8')), _CARD_LIMIT)
        run = self._run(cards=real)
        self.assertEqual(run.status, 0)
        prefix = _sent_prefix(run.prompts[0])
        self.assertEqual(prefix, _wrapped(real))
        self.assertLessEqual(len(prefix.encode('utf-8')),
                             skill_manifest.MAX_DEVELOPER_PREFIX_SIZE)
        self.assertEqual(skill_manifest.MAX_DEVELOPER_PREFIX_SIZE, 14 * 1024)
        self.assertLessEqual(len(run.prompts[0].encode('utf-8')), submission_cli.REQUEST_LIMIT)

    def test_whole_prompt_limit_counts_the_prefix_and_never_truncates(self):
        def padding(size):
            # No newline: TASK_PACKET_JSON escapes it to two bytes.
            return (' goal rule' * (size // 10 + 1))[:size]

        limit = submission_cli.REQUEST_LIMIT
        self.assertEqual(limit, 64 * 1024)
        base = self._run().prompts[0]
        room = limit - len(base.encode('utf-8'))
        self.assertGreater(room, 0)
        fitted = self._run(goal='Update the sample value.' + padding(room)).prompts[0]
        self.assertEqual(len(fitted.encode('utf-8')), limit)
        self.assertEqual(_sent_prefix(fitted), _wrapped(_CARDS))
        # One byte over: the prompt without cards still fits, but with them it
        # does not, so the dispatch is refused before any write.
        self._assert_prewrite_refusal(
            'OPENCODE_SKILL_PROMPT_TOO_LARGE',
            goal='Update the sample value.' + padding(room + 1))

    def test_generic_record_still_refuses_controller_delivered(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger_path = Path(temp) / 'ledger.jsonl'
            with patch.object(skill_ledger, '_path', return_value=ledger_path):
                with self.assertRaises(skill_ledger.SkillLedgerError) as caught:
                    skill_ledger.record({
                        'task_id': _TASK_ID, 'run_id': _RUN_ID, 'session_id': _SESSION,
                        'agent_id': 'opencode-developer', 'role': 'developer',
                        'provider': 'opencode', 'model': _MODEL, 'repo': '/repo/.git',
                        'head': _HEAD, 'manifest_sha256': '7' * 64,
                        'evidence_level': 'CONTROLLER_DELIVERED'})
            self.assertFalse(ledger_path.exists())
        self.assertEqual(caught.exception.skill_ledger_code, 'CONTROLLER_DELIVERED_REFUSED')

    def test_opencode_and_gw_agent_card_rows_use_the_common_core_card(self):
        table = skill_manifest._ROLE_CARD_TABLE
        for route in ('opencode-developer', 'gw-agent', 'claude-developer', 'agy'):
            with self.subTest(route=route):
                self.assertEqual(table[route], (_CORE, _DEV_CARD))
        self.assertEqual(submission_cli._SKILL_CARD_ROUTES['opencode'], 'opencode-developer')
        # gw-agent has no card delivery; its runs record NOT_DELIVERED instead.
        self.assertNotIn('gw-agent', submission_cli._SKILL_CARD_ROUTES)


if __name__ == '__main__':
    unittest.main()
