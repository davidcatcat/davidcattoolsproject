"""#2367 (Owner #2405 A): an AI Debug refused before its model started frees its slot.

Only trusted evidence in the entry frees it: the launcher's pre-launch
refusal code with nothing observed. RUN_FAILED alone, a missing report,
UNKNOWN usage or no output keep the conservative rule (the slot is used).

A1 (PR #2407 F1): ROLE_MODEL_NOT_ALLOWED in a PR #2405-shaped entry is raised
before the Debug model is launched. In llmtool/high_debug.py, _run_codex and
_run_claude call _launch_settings (-> _admit_debug_model ->
role_config.check_registry_model) before they call subprocess.run, and
run_high_debug records that code only from its `except _LaunchRefused`
handler. LaunchOrderTests checks the order; NotStartedRunTests checks that
the real path writes an entry with the PR #2405 evidence fields.
"""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import high_debug, lineage_counts
from llmtool.errors import LLMToolError

import review_trust_fixture
import test_high_debug_reservation as reservation

setUpModule = review_trust_fixture.start_module
tearDownModule = review_trust_fixture.stop_module

MODEL = 'ROLE_MODEL_NOT_ALLOWED'
PARENT, RUN = reservation.PARENT, reservation.RUN
USAGE = {'input': 1, 'cached': 0, 'output': 1, 'reasoning': 0}
# The entry fields that decide whether a RUN_FAILED Debug started.
EVIDENCE = ('status', 'report', 'report_sha256', 'model_observed', 'effort_observed',
            'usage')


def pr2405(**changes):
    """An entry shaped like Task a67c53af's PR #2405 record (2026-11-19T13:36:56Z)."""
    stored = high_debug._failed_entry(
        family='pr-2405', failed_corrections=None, early_reason=reservation.REASON,
        head=reservation.HEAD, d6='executor-hd1-aaaaaaa', code=MODEL, observed='UNKNOWN',
        effort='UNKNOWN', usage='UNKNOWN', recorded_at='2026-11-19T13:36:56Z',
        model_requested='gpt-6.1-sol', rules_sha256=reservation.RULES[1])
    return dict(stored, **changes)


def without(entry, key):
    entry = dict(entry)
    del entry[key]
    return entry


def empty_output(*, prompt, cwd, output_path):
    """A started run that exits cleanly and writes no report."""
    return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')


class EntryClassificationTests(unittest.TestCase):
    def test_pr_2405_record_is_not_started(self):
        entry = pr2405()
        self.assertEqual((entry['status'], entry['report'], entry['model_requested'],
                          entry['model_observed']),
                         ('RUN_FAILED', MODEL, 'gpt-6.1-sol', 'UNKNOWN'))
        self.assertFalse(high_debug._debug_entry_used(entry))

    def test_pre_launch_code_with_any_sign_of_a_run_is_used(self):
        for name, entry in (
                ('observed model', pr2405(model_observed='gpt-6.1-sol')),
                ('observed effort', pr2405(effort_observed='high')),
                ('known usage', pr2405(usage=dict(USAGE))),
                ('digest mismatch', pr2405(report_sha256='0' * 64)),
                ('no digest', without(pr2405(), 'report_sha256')),
                ('no usage', without(pr2405(), 'usage')),
                ('not RUN_FAILED', pr2405(status='RESERVED')),
                ('REPORT_INVALID', pr2405(status='REPORT_INVALID'))):
            with self.subTest(case=name):
                self.assertTrue(high_debug._debug_entry_used(entry))

    def test_run_failed_alone_missing_report_or_no_output_is_used(self):
        for name, entry in (
                ('RUN_FAILED', pr2405(report=RUN)),
                ('missing report', without(pr2405(), 'report')),
                ('null report', pr2405(report=None)),
                ('no output', pr2405(report='')),
                ('unknown code', pr2405(report='HIGH_DEBUG_SOMETHING_ELSE')),
                ('wrote files', pr2405(report='HIGH_DEBUG_WROTE_FILES')),
                ('model unverified', pr2405(report='HIGH_DEBUG_MODEL_UNVERIFIED')),
                ('not a dict', [MODEL])):
            with self.subTest(case=name):
                self.assertTrue(high_debug._debug_entry_used(entry))

    def test_finished_outcomes_and_quota_rule_are_unchanged(self):
        for status in ('RCA_CONFIRMED', 'RCA_UNCONFIRMED', 'BLOCKED', 'REPORT_INVALID',
                       'RESERVED'):
            with self.subTest(status=status):
                self.assertTrue(high_debug._debug_entry_used(reservation.entry(status)))
        for code in ('HIGH_DEBUG_QUOTA_EXHAUSTED', 'HIGH_DEBUG_AUTH_FAILED',
                     'HIGH_DEBUG_D6_CREATE_FAILED'):
            with self.subTest(code=code):
                entry = reservation.entry('RUN_FAILED', code)
                self.assertFalse(high_debug._debug_entry_used(entry))
                self.assertFalse(high_debug._debug_entry_used(dict(entry, usage=dict(USAGE))))
        self.assertNotIn(MODEL, high_debug.DEBUG_FALLBACK_CODES)


class LaunchOrderTests(unittest.TestCase):
    """A1 (PR #2407 F1): each launcher admits its model before any provider process."""

    LAUNCHERS = ((high_debug._run_codex, 'codex-debug', 'codex', {}),
                 (high_debug._run_claude, 'claude-debug', 'claude',
                  {'config_dir': '/safe/claude'}))

    def launch(self, launcher, extra, refuse):
        """Run launcher with recorded steps; return (steps, refusal code or None)."""
        steps = []

        def check(role, model):
            steps.append(('admit', role))
            if refuse:
                raise LLMToolError('synthetic registry refusal')
            return model

        def timeout(role):
            steps.append(('timeout', role))
            return 60

        def run(argv, *_args, **_kwargs):
            steps.append(('launch', list(argv)[0]))
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        fake = SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE)
        with patch.object(high_debug.role_config, 'check_registry_model', side_effect=check), \
             patch.object(high_debug, '_debug_timeout', side_effect=timeout), \
             patch.object(high_debug, 'subprocess', fake), \
             patch('llmtool.codex_direct_developer._command_path', return_value='/usr/bin'), \
             patch('llmtool.codex_direct_developer._environment', return_value={}), \
             patch('llmtool.developer_routes.claude.ClaudeRoute') as route:
            route.return_value.build_env.return_value = {}
            try:
                launcher(prompt='p', cwd='/safe/d6', output_path='/safe/d6/out', **extra)
            except high_debug._LaunchRefused as error:
                return steps, error.high_debug_code
        return steps, None

    def test_admitted_model_is_checked_before_the_provider_process(self):
        for launcher, role, executable, extra in self.LAUNCHERS:
            with self.subTest(launcher=launcher.__name__):
                self.assertEqual(self.launch(launcher, extra, refuse=False),
                                 ([('admit', role), ('timeout', role),
                                   ('launch', executable)], None))

    def test_refused_model_stops_before_the_provider_process(self):
        for launcher, role, _executable, extra in self.LAUNCHERS:
            with self.subTest(launcher=launcher.__name__):
                self.assertEqual(self.launch(launcher, extra, refuse=True),
                                 ([('admit', role)], MODEL))


class NotStartedRunTests(reservation.ReservationCase):
    def refuse_after_reservation(self, task_id):
        """Registry check that admits until this Task holds an entry, then refuses."""
        def check(_role, model):
            if self.entries(task_id):
                raise LLMToolError('synthetic registry refusal')
            return model
        return check

    def launch(self, task_id, timeout=lambda _role: 60):
        """Run AI Debug with the real Codex launcher; return (code, provider argv0s)."""
        provider = []

        def run(argv, *_args, **_kwargs):
            if list(argv)[:1] != ['git']:
                provider.append(argv[0])
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        fake = SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE,
                               CalledProcessError=subprocess.CalledProcessError)
        registration = SimpleNamespace(result_sha=reservation.HEAD,
                                       root=str(self.base / 'executor-d6'),
                                       common_dir=str(self.base / 'repo' / '.git'))
        with patch.object(high_debug, '_d6_taken', return_value=False), \
             patch.object(high_debug, '_debug_timeout', side_effect=timeout), \
             patch.object(high_debug, 'subprocess', fake), \
             redirect_stdout(io.StringIO()):
            try:
                high_debug.run_high_debug_with_fallback(
                    store=self.store, task_id=task_id, executor_task='executor',
                    pr_number=17, family='pr-17', early_reason=reservation.REASON,
                    runner=None, create_d6=lambda **_kwargs: str(self.base),
                    repo_root=str(self.base / 'repo'),
                    read_registration=lambda *_a, **_k: registration)
            except LLMToolError as error:
                return error.high_debug_code, provider
        return None, provider

    def test_model_refused_before_reservation_uses_no_slot(self):
        task_id, runner = self.task(True), Mock()
        with patch.object(high_debug.role_config, 'check_registry_model',
                          side_effect=LLMToolError('synthetic registry refusal')):
            self.eq(self.debug(task_id, runner), (MODEL, []))
        runner.assert_not_called()
        self.eq(self.entries(task_id), [])
        self.eq(self.debug(task_id, reservation.succeeds), (None, [['RESERVED']]))
        self.eq(self.statuses(task_id), ['RCA_CONFIRMED'])

    def test_launcher_setting_refused_before_reservation_uses_no_slot(self):
        task_id = self.task(True)
        refuse = lambda _role: high_debug._refuse('ROUTE_CONFIG_UNAVAILABLE')  # noqa: E731
        self.eq(self.launch(task_id, timeout=refuse), ('ROUTE_CONFIG_UNAVAILABLE', []))
        self.eq(self.entries(task_id), [])
        self.eq(self.debug(task_id, reservation.succeeds), (None, [['RESERVED']]))

    def test_launch_time_model_refusal_frees_the_slot_and_a_legal_debug_runs(self):
        task_id = self.task(True)
        with patch.object(high_debug.role_config, 'check_registry_model',
                          side_effect=self.refuse_after_reservation(task_id)):
            self.eq(self.launch(task_id), (MODEL, []))
        self.eq(self.pairs(task_id), [('RUN_FAILED', MODEL)])
        stored = self.entries(task_id)[0]
        self.eq((stored['model_observed'], stored['effort_observed'], stored['usage']),
                ('UNKNOWN', 'UNKNOWN', 'UNKNOWN'))
        # A1: the real path writes the PR #2405 entry's evidence fields exactly.
        self.eq({key: stored[key] for key in EVIDENCE},
                {key: pr2405()[key] for key in EVIDENCE})
        self.assertFalse(high_debug._debug_entry_used(stored))
        # The retry the PR #2405 chain was refused (HIGH_DEBUG_DEBUG_PARENT_USED).
        self.eq(self.debug(task_id, reservation.succeeds),
                (None, [['RUN_FAILED', 'RESERVED']]))
        self.eq(self.statuses(task_id), ['RUN_FAILED', 'RCA_CONFIRMED'])
        runner = Mock()
        self.eq(self.debug(task_id, runner), (PARENT, []))
        runner.assert_not_called()

    def test_launchers_refuse_before_any_provider_process(self):
        for launcher, extra in ((high_debug._run_codex, {}),
                                (high_debug._run_claude, {'config_dir': '/safe/claude'})):
            process = Mock()
            with self.subTest(launcher=launcher.__name__), \
                 patch.object(high_debug.role_config, 'check_registry_model',
                              side_effect=LLMToolError('synthetic registry refusal')), \
                 patch.object(high_debug, 'subprocess', process):
                with self.assertRaises(high_debug._LaunchRefused) as caught:
                    launcher(prompt='p', cwd=str(self.base),
                             output_path=str(self.base / 'out'), **extra)
                self.assertIsInstance(caught.exception, LLMToolError)
                self.eq(caught.exception.high_debug_code, MODEL)
                process.run.assert_not_called()

    def test_started_debug_without_a_valid_report_still_counts(self):
        for name, runner, refused, pair in (
                ('exit 1', reservation.exits(1), RUN, ('RUN_FAILED', RUN)),
                ('no output', empty_output, None, None)):
            with self.subTest(case=name):
                task_id = self.task(True)
                self.eq(self.debug(task_id, runner)[0], refused)
                if pair is not None:
                    self.eq(self.pairs(task_id), [pair])
                else:
                    self.eq(self.statuses(task_id), ['REPORT_INVALID'])
                self.assertTrue(high_debug._debug_entry_used(self.entries(task_id)[0]))
                later = Mock()
                self.eq(self.debug(task_id, later), (PARENT, []))
                later.assert_not_called()

    def test_unknown_outcome_still_counts(self):
        mid_run = lambda **_kwargs: high_debug._refuse(MODEL)  # noqa: E731
        for name, runner in (('unknown error', reservation.raises(RuntimeError, 'stopped')),
                             ('timeout', reservation.raises(subprocess.TimeoutExpired,
                                                            'codex', 1)),
                             ('model code not from the launcher', mid_run)):
            with self.subTest(case=name):
                task_id = self.task(True)
                self.eq(self.debug(task_id, runner)[0], RUN)
                self.eq(self.pairs(task_id), [('RUN_FAILED', RUN)])
                self.eq(self.entries(task_id)[0]['usage'], 'UNKNOWN')
                self.eq(self.debug(task_id, Mock())[0], PARENT)

    def test_two_requests_after_a_freed_entry_keep_mutual_exclusion(self):
        first, second = self.chain(2)
        self.store.append_high_debug(first, pr2405())
        self.eq(lineage_counts.chain_debug_used(
            self.store, self.store.get(first), self.entries(first),
            high_debug._debug_entry_used), (0, 0))
        winner, loser = self.race(first, second)
        self.assertIn(winner, (0, 1))
        self.eq(loser, PARENT)
        self.eq((self.statuses(first) + self.statuses(second)).count('RESERVED'), 1)
        alone = self.task()
        self.store.append_high_debug(alone, pr2405())
        winner, loser = self.race(alone, alone)
        self.eq(winner, 1)
        self.assertIn(loser, ('HIGH_DEBUG_BUSY', PARENT))
        self.eq(self.statuses(alone), ['RUN_FAILED', 'RESERVED'])

    def test_pr_2405_record_is_kept_and_counts_as_not_started(self):
        task_id = self.task(True)
        self.store.append_high_debug(task_id, pr2405())
        self.eq(self.debug(task_id, reservation.succeeds),
                (None, [['RUN_FAILED', 'RESERVED']]))
        # The original record stays as it was written.
        self.eq(self.entries(task_id)[0], pr2405())
        self.eq(self.statuses(task_id), ['RUN_FAILED', 'RCA_CONFIRMED'])


if __name__ == '__main__':
    unittest.main()
