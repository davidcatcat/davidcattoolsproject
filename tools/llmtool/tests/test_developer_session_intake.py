"""Offline G3 intake preflight. These fixtures never dispatch a model or CLI message."""
import unittest
from dataclasses import replace

from llmtool.action_contract import TaskBinding
from llmtool.run_budget import RunBudget
from llmtool.task_lifecycle import AgentBinding, Role
from llmtool import developer_session_intake as intake


class Liar(str):
    def __eq__(self, _other):
        return True

    def __ne__(self, _other):
        return False


class Explosive:
    def __eq__(self, _other):
        raise TypeError('malformed comparison')

    def __ne__(self, _other):
        raise TypeError('malformed comparison')


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.agent = AgentBinding(
            'codex-developer', 'active-session-1', Role.DEVELOPER,
            '/safe/task', '/safe/project/.git', 'feat/next-task')
        self.task = TaskBinding('task-next', 'a' * 32, 1, 'b' * 64)
        self.budget = RunBudget(1, 1, 0, 120000, 10000, 1000)
        self.prior = intake.PreviousTask('task-prior', 'e' * 64, 'COMPLETED',
                                         'f' * 64, 'integrator-session-1')
        self.registration = intake.SessionRegistration(
            self.agent, 'c' * 40, 7, 2000, 'ACTIVE', self.prior.task_id,
            self.prior.job_digest)
        self.observation = intake.SessionObservation(
            self.agent, 'c' * 40, 7, 1000, True, True)
        self.next = intake.NextTask(
            self.task, self.agent, 'c' * 40, 'd' * 64,
            ('tools/llmtool/llmtool/a.py',), ('tools/llmtool/llmtool/a.py',),
            self.budget, 1500, 'integrator-session-1')

    def prepare(self, **kwargs):
        args = dict(next_task=self.next, registration=self.registration,
                    observation=self.observation, previous=self.prior, now=1000)
        args.update(kwargs)
        return intake.prepare_next(**args)

    def test_next_task_is_prepared_without_live_authority_or_ack(self):
        result = self.prepare()
        self.assertEqual((result.status, result.reason), ('PREPARED', 'INTAKE_BOUNDS_MATCH'))
        self.assertEqual(result.task_id, self.task.task_id)
        self.assertEqual(result.agent_id, self.agent.agent_id)
        self.assertEqual(result.session_id, self.agent.session_id)
        self.assertEqual(result.job_digest, self.next.job_digest)
        self.assertEqual(result.source_sha, self.next.source_sha)
        self.assertEqual(result.integrator_session, self.next.integrator_session)
        self.assertEqual(len(result.packet_digest), 64)
        self.assertFalse(result.live_allowed)
        self.assertEqual(result.ack, 'NOT_OBSERVED')
        self.assertEqual(result.execution_start, 'NOT_OBSERVED')
        self.assertFalse(result.accepted)

    def test_each_refusal_names_its_reason(self):
        # #367: every blocked branch keeps its own reason code, and live use stays refused.
        cases = (
            ({'next_task': None}, 'INTAKE_EVIDENCE_MISSING'),
            ({'next_task': replace(self.next, source_sha='x')}, 'INTAKE_TASK_INVALID'),
            ({'observation': replace(self.observation, generation=8)}, 'INTAKE_SESSION_MISMATCH'),
            ({'observation': replace(self.observation, receptive=False)},
             'INTAKE_SESSION_UNAVAILABLE'),
            ({'now': 1031}, 'INTAKE_EXPIRED_OR_CLOCK_INVALID'),
            ({'next_task': replace(self.next, write_paths=('other.py',))}, 'INTAKE_SCOPE_INVALID'),
            ({'previous': None}, 'INTAKE_PREVIOUS_NOT_TERMINAL'),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                result = self.prepare(**changes)
                self.assertEqual((result.status, result.reason), ('BLOCKED', reason))
        live = intake.run_live(self.prepare())
        self.assertEqual((live.status, live.reason),
                         ('BLOCKED', 'LIVE_SESSION_TRANSPORT_NOT_ADMITTED'))

    def test_registered_previous_task_must_have_matching_terminal_receipt(self):
        self.assertEqual(self.prepare(previous=None).status, 'BLOCKED')
        self.assertEqual(self.prepare(registration=replace(
            self.registration, prior_task_id=None, prior_job_digest=None)).status,
            'BLOCKED')
        self.assertEqual(self.prepare(previous=None, registration=replace(
            self.registration, prior_task_id=None, prior_job_digest=None)).status,
            'BLOCKED')
        for bad in (replace(self.prior, task_id=self.task.task_id),
                    replace(self.prior, job_digest='1' * 64),
                    replace(self.prior, status='UNKNOWN'),
                    replace(self.prior, status=[]),
                    replace(self.prior, receipt_digest=''),
                    replace(self.prior, integrator_session='other-integrator')):
            with self.subTest(prior=bad):
                self.assertEqual(self.prepare(previous=bad).status, 'BLOCKED')

    def test_identity_source_and_generation_mismatch_fail_closed(self):
        for args in (
            dict(observation=replace(self.observation, agent=replace(
                self.agent, session_id='foreign-session'))),
            dict(observation=replace(self.observation, generation=8)),
            dict(observation=replace(self.observation, source_sha='e' * 40)),
            dict(registration=replace(self.registration, agent=replace(
                self.agent, branch='feat/foreign'))),
            dict(next_task=replace(self.next, source_sha='e' * 40)),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.prepare(**args).status, 'BLOCKED')

    def test_unavailable_closed_or_unreceptive_session_is_blocked(self):
        for args in (
            dict(observation=replace(self.observation, active=False)),
            dict(observation=replace(self.observation, receptive=False)),
            dict(registration=replace(self.registration, state='CLOSED')),
            dict(observation=None),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.prepare(**args).status, 'BLOCKED')

    def test_expiration_and_clock_anomalies_are_blocked(self):
        for args in (dict(now=2000), dict(now=1500), dict(now=999), dict(now=1031),
                     dict(now=True), dict(now=None)):
            with self.subTest(args=args):
                self.assertEqual(self.prepare(**args).status, 'BLOCKED')

    def test_scope_budget_and_digest_are_exactly_bound(self):
        original = self.prepare().packet_digest
        altered = replace(self.next, budget=replace(self.budget, max_output_tokens=999))
        self.assertNotEqual(self.prepare(next_task=altered).packet_digest, original)
        for scope in (('../escape',), ('/absolute',), ('tools/x.py', 'tools/x.py'),
                      ('tools/b.py', 'tools/a.py'), ('tools/./a.py',), ('a.py', 1)):
            with self.subTest(scope=scope):
                changed = replace(self.next, read_paths=scope, write_paths=scope)
                self.assertEqual(self.prepare(next_task=changed).status, 'BLOCKED')
        changed = replace(self.next, read_paths=('tools/a.py',),
                          write_paths=('tools/b.py',))
        self.assertEqual(self.prepare(next_task=changed).status, 'BLOCKED')
        self.assertEqual(self.prepare(next_task=replace(self.next, job_digest='x')).status,
                         'BLOCKED')

    def test_packet_binds_fresh_host_observation_and_registration_expiry(self):
        original = self.prepare().packet_digest
        self.assertNotEqual(self.prepare(observation=replace(
            self.observation, observed_at=999)).packet_digest, original)
        self.assertNotEqual(self.prepare(registration=replace(
            self.registration, expires_at=1500)).packet_digest, original)

    def test_malformed_registration_and_previous_fields_fail_closed(self):
        cases = (
            dict(registration=replace(self.registration, state=Liar('CLOSED'))),
            dict(registration=replace(self.registration,
                                      prior_task_id=Liar('other-task'))),
            dict(registration=replace(self.registration,
                                      prior_job_digest=Liar('1' * 64))),
            dict(previous=replace(self.prior,
                                  integrator_session=Liar('other-integrator'))),
            dict(registration=replace(self.registration, state=Explosive())),
            dict(registration=replace(self.registration,
                                      prior_task_id=Explosive())),
            dict(registration=replace(self.registration,
                                      prior_job_digest=Explosive())),
            dict(previous=replace(self.prior,
                                  integrator_session=Explosive())),
            dict(registration=replace(self.registration,
                                      source_sha=Liar('1' * 40))),
            dict(observation=replace(self.observation,
                                     source_sha=Liar('1' * 40))),
            dict(registration=replace(self.registration,
                                      source_sha=Explosive())),
            dict(observation=replace(self.observation,
                                     source_sha=Explosive())),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(self.prepare(**case).status, 'BLOCKED')

    def test_caller_objects_do_not_authorize_dispatch(self):
        result = self.prepare()
        self.assertEqual(intake.run_live(result).status, 'BLOCKED')
        self.assertEqual(intake.run_live(result).reason, 'LIVE_SESSION_TRANSPORT_NOT_ADMITTED')
        self.assertEqual(intake.run_live(object()).status, 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
