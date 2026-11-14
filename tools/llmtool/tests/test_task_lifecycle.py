"""Independent pins, non-author controls and replay outcomes (no runtime effects)."""

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from llmtool import task_lifecycle as t
from llmtool import task_verification as v
from llmtool.errors import LLMToolError

NOW = '2026-10-12T12:00:10Z'


def fixture():
    contract = v.TaskContract(1, 's1a-task', 1, None, 'Inspect offline records',
        ('src',), (), (v.AcceptanceCriterion('artifact', v.CriterionKind.ARTIFACT, 'Artifact'),
                     v.AcceptanceCriterion('review', v.CriterionKind.REVIEW, 'Non-author review'),
                     v.AcceptanceCriterion('tests', v.CriterionKind.DETERMINISTIC_CHECK, 'Tests')))
    dev = t.AgentBinding('codex-writer', 'writer-session', t.Role.DEVELOPER,
                         '/repo/codex', '/repo/main/.git', 'feat/s1a')
    reviewer = t.AgentBinding('codex-reviewer', 'review-session', t.Role.REVIEWER,
                              '/repo/review', '/repo/main/.git', 'review/s1a')
    expect = t.Expectations(1, contract.binding('a'*32), '1'*40, 7, t.State.QUEUED, 0,
                            (dev, reviewer), (dev,), 300, 'c'*64, ('e'*64,))
    def record(criterion, source, number):
        command = source is v.EvidenceSource.COMMAND
        return v.EvidenceRecord(1, 'evidence-'+str(number), criterion, expect.task,
            expect.source_sha, source, v.EvidenceOutcome.PASSED,
            expect.check_digest if command else None, 0 if command else None,
            'd'*64 if command else None, expect.artifact_digests if criterion == 'artifact' else (), True)
    handoff = t.HandoffRecord(expect.task, expect.source_sha, expect.epoch, dev, reviewer,
        expect.check_digest, expect.artifact_digests, 'event-3', ('review',))
    events = []
    for index, state in enumerate((t.State.QUEUED, t.State.STARTING, t.State.RUNNING,
                                   t.State.WAITING_REVIEW, t.State.COMPLETED), 1):
        evidence = ((record('tests', v.EvidenceSource.COMMAND, 1),
                     record('artifact', v.EvidenceSource.ARTIFACT, 2)) if index == 3 else
                    (record('review', v.EvidenceSource.REVIEW, 3),) if index == 5 else ())
        events.append(t.TaskEvent(1, 'event-'+str(index), index, expect.epoch, expect.task,
            expect.source_sha, reviewer if index == 5 else dev, 'STATE', state,
            'event-'+str(index-1) if index > 1 else None,
            events[-1].state if events else None, '2026-10-12T12:00:0'+str(index-1)+'Z',
            'f'*64, evidence, handoff if index == 4 else None))
    return contract, expect, tuple(events)


def wire_events(events):
    return '\n'.join(event.to_json() for event in events) + '\n'


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.contract, self.expect, self.events = fixture()

    def inspect(self, events=None, expect=None, now=NOW):
        return t.inspect_task(self.contract, expect or self.expect,
                              self.events if events is None else events, now)

    def test_complete_replay_preview_is_only_structural_same_provider_non_author(self):
        with patch('time.time', side_effect=AssertionError('machine clock')):
            result = self.inspect()
        data = result.to_dict()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(data['reported_state'], 'COMPLETED')
        self.assertEqual(data['structural_validation'], 'SUCCESS')
        self.assertEqual(data['runtime_evidence_status'], 'NOT_VERIFIED')
        self.assertFalse(data['authorized'])
        self.assertFalse(data['handoff_preview']['triggered'])
        self.assertEqual(data['handoff_preview']['to_agent_id'], 'codex-reviewer')
        self.assertNotIn('/repo', json.dumps(data))
        self.assertEqual(data['evidence_structure'], 'PASS')

    def test_immutable_and_canonical_round_trip(self):
        expect = t.parse_expectations(self.expect.to_json())
        events = t.parse_events(wire_events(self.events))
        self.assertEqual((expect, events), (self.expect, self.events))
        for value in (expect, expect.agents[0], events[0], events[3].handoff):
            with self.assertRaises(FrozenInstanceError):
                value.task = None

    def test_malicious_internally_consistent_stream_cannot_supply_expectations(self):
        other_dev = replace(self.expect.agents[0], root='/wrong/root', session_id='attacker')
        other = replace(self.expect, agents=(other_dev, self.expect.agents[1]),
                        authored_by=(other_dev,), source_sha='2'*40, epoch=8,
                        task=self.contract.binding('b'*32))
        forged = tuple(replace(e, task=other.task, source_sha=other.source_sha, epoch=other.epoch,
                               agent=other_dev if e.agent.role is t.Role.DEVELOPER else e.agent)
                       for e in self.events)
        # Even the first record is self-consistent with its siblings, not the pins.
        with self.assertRaises(LLMToolError):
            self.inspect(forged)

    def test_each_event_binding_is_checked_against_expect(self):
        agent = self.events[0].agent
        mutations = dict(source_sha='2'*40, epoch=8, task=self.contract.binding('b'*32),
                         agent=replace(agent, session_id='wrong-session'))
        for field, value in mutations.items():
            with self.subTest(field=field), self.assertRaises(LLMToolError):
                self.inspect((replace(self.events[0], **{field: value}), *self.events[1:]))
        for field, value in dict(root='/wrong/root', common_dir='/wrong/.git', branch='feat/wrong',
                                 agent_id='wrong-agent', role=t.Role.REVIEWER).items():
            with self.subTest(agent=field), self.assertRaises(LLMToolError):
                self.inspect((replace(self.events[0], agent=replace(agent, **{field: value})),
                              *self.events[1:]))
        for field, value in dict(contract_digest='f'*64, contract_version=2, task_id='wrong-task').items():
            task = replace(self.expect.task, **{field: value})
            with self.subTest(task=field), self.assertRaises(LLMToolError):
                self.inspect(expect=replace(self.expect, task=task))

    def test_either_author_id_or_session_reuse_fails_independence(self):
        dev, reviewer = self.expect.agents
        for changes in ({'agent_id': dev.agent_id}, {'session_id': dev.session_id}):
            with self.subTest(changes=changes), self.assertRaises(LLMToolError):
                replace(self.expect, agents=(dev, replace(reviewer, **changes)))
        historical = replace(dev, agent_id=reviewer.agent_id)
        with self.assertRaises(LLMToolError):
            replace(self.expect, authored_by=(dev, historical))
        historical = replace(dev, session_id=reviewer.session_id)
        with self.assertRaises(LLMToolError):
            replace(self.expect, authored_by=(dev, historical))

    def test_unknown_authorship_or_provenance_is_incomplete_not_independent(self):
        for authors in (None, ()):
            result = self.inspect(expect=replace(self.expect, authored_by=authors))
            self.assertEqual(result.exit_code, 1)
            self.assertIn('AUTHORSHIP_INCOMPLETE', result.findings)
        result = self.inspect((replace(self.events[0], provenance_digest=None), *self.events[1:]))
        self.assertEqual(result.exit_code, 1)
        self.assertIn('PROVENANCE_INCOMPLETE', result.findings)

    def test_sequence_gap_duplicate_replay_and_post_terminal_reject(self):
        changes = ((self.events[0], self.events[0]), self.events[::-1],
                   self.events[:1]+self.events[2:],
                   self.events+(replace(self.events[-1], event_id='event-6', sequence=6,
                                        prior_event_id='event-5', prior_state=t.State.COMPLETED),))
        for events in changes:
            with self.subTest(events=len(events)), self.assertRaises(LLMToolError):
                self.inspect(events)
        with self.assertRaises(LLMToolError):
            self.inspect((self.events[0], replace(self.events[1], event_id='event-1'), *self.events[2:]))

    def test_state_skips_wrong_prior_and_initial_state_reject(self):
        for changes in ({'state': t.State.RUNNING}, {'prior_state': t.State.RUNNING},
                        {'prior_event_id': 'wrong'}, {'kind': 'RESUME'}):
            with self.subTest(changes=changes), self.assertRaises(LLMToolError):
                self.inspect((self.events[0], replace(self.events[1], **changes), *self.events[2:]))
        with self.assertRaises(LLMToolError):
            self.inspect((replace(self.events[0], prior_event_id='wrong'),))

    def test_starting_can_fail_or_block_explicit_resume_only(self):
        prefix = self.events[:2]
        for state in (t.State.BLOCKED, t.State.FAILED):
            stop = replace(self.events[2], state=state, evidence=())
            result = self.inspect(prefix+(stop,))
            self.assertEqual(result.reported_state, state)
            self.assertEqual(result.exit_code, 1)
        blocked = replace(self.events[2], state=t.State.BLOCKED, evidence=())
        resume = replace(self.events[1], event_id='resume', sequence=4, prior_event_id='event-3',
                         prior_state=t.State.BLOCKED, kind='RESUME', timestamp='2026-10-12T12:00:03Z')
        result = self.inspect(prefix+(blocked, resume))
        self.assertEqual(result.reported_state, t.State.STARTING)
        for invalid_resume in (replace(resume, kind='STATE'),
                               replace(resume, timestamp=blocked.timestamp),
                               replace(resume, state=t.State.RUNNING)):
            with self.assertRaises(LLMToolError):
                self.inspect(prefix+(blocked, invalid_resume))

    def test_clock_future_rollback_ambiguity_and_exact_expiry(self):
        for now in (None, '', '2026-10-12T12:00:10+00:00', '2026-10-12T12:00:10.0Z',
                    '2026-02-30T12:00:10Z', '2026-10-12T12:00:00Z'):
            with self.subTest(now=now), self.assertRaises(LLMToolError):
                self.inspect(now=now)
        with self.assertRaises(LLMToolError):
            self.inspect((self.events[0], replace(self.events[1], timestamp='2026-10-12T11:59:59Z')))
        result = self.inspect(now='2026-10-12T12:05:04Z')
        self.assertEqual((result.exit_code, result.structural_validation), (1, 'STALE'))
        self.assertIn('HEARTBEAT_EXPIRED', result.findings)
        self.assertEqual(self.inspect(now='2026-10-12T12:05:03Z').exit_code, 0)

    def test_handoff_all_pins_and_outstanding_evidence_are_checked(self):
        h = self.events[3].handoff
        changes = dict(source_sha='2'*40, epoch=8, task=self.contract.binding('b'*32),
                       check_digest='d'*64, artifact_digests=(), prior_event_id='wrong',
                       outstanding_criteria=(), to_agent=self.expect.agents[0])
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(LLMToolError):
                self.inspect(self.events[:3]+(replace(self.events[3], handoff=replace(h, **{field: value})),)
                             +self.events[4:])
        with self.assertRaises(LLMToolError):
            self.inspect(self.events[:4]+(replace(self.events[4], agent=self.expect.agents[0]),))
        result = self.inspect(self.events[:3]+(replace(self.events[3], handoff=None),))
        self.assertEqual(result.exit_code, 1)
        self.assertIn('HANDOFF_INCOMPLETE', result.findings)

    def test_handoff_state_and_author_refusals_name_their_code(self):
        # #367: replay refusals keep TASK_HANDOFF_INVALID, TASK_STATE_INVALID and
        # TASK_REVIEW_NOT_INDEPENDENT.
        h, e = self.events[3].handoff, self.events
        cases = (
            (e[:3] + (replace(e[3], handoff=replace(h, epoch=8)),) + e[4:], 'TASK_HANDOFF_INVALID'),
            (e[:3] + (replace(e[3], handoff=replace(h, outstanding_criteria=())),) + e[4:],
             'TASK_HANDOFF_INVALID'),
            ((e[0], replace(e[1], state=t.State.RUNNING), *e[2:]), 'TASK_STATE_INVALID'),
            ((replace(e[0], prior_event_id='wrong'),), 'TASK_STATE_INVALID'),
            (e[:4] + (replace(e[4], agent=self.expect.agents[0]),), 'TASK_REVIEW_NOT_INDEPENDENT'),
        )
        for events, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(LLMToolError) as raised:
                    self.inspect(events)
                self.assertEqual(raised.exception.lifecycle_code, code)

    def test_model_only_false_completion_cannot_satisfy_tests(self):
        review = self.events[-1].evidence[0]
        claim = replace(review, source=v.EvidenceSource.MODEL_CLAIM)
        result = self.inspect(self.events[:-1]+(replace(self.events[-1], evidence=(claim,)),))
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.reported_state, t.State.COMPLETED)
        self.assertIn('review', result.missing_evidence)
        self.assertEqual(result.to_dict()['runtime_evidence_status'], 'NOT_VERIFIED')
        self.assertFalse(result.to_dict()['authorized'])

    def test_supplemental_rejected_evidence_cannot_promote_verifier_incomplete(self):
        bad = replace(self.events[-1].evidence[0], evidence_id='extra-review', criterion_id='tests')
        result = self.inspect(self.events[:-1]+(replace(self.events[-1],
            evidence=self.events[-1].evidence+(bad,)),))
        self.assertEqual(result.evidence_structure, 'INCOMPLETE')
        self.assertEqual((result.exit_code, result.structural_validation), (1, 'INCOMPLETE'))
        self.assertIn('SOURCE_KIND_MISMATCH', result.findings)
        self.assertEqual(result.missing_evidence, ())

    def test_evidence_binding_check_digest_duplicate_and_author_review_reject(self):
        record = self.events[2].evidence[0]
        for changes in ({'subject_digest': '2'*40}, {'task': self.contract.binding('b'*32)},
                        {'command_digest': 'd'*64}, {'criterion_id': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(LLMToolError):
                self.inspect(self.events[:2]+(replace(self.events[2], evidence=(replace(record, **changes),)),))
        with self.assertRaises(LLMToolError):
            self.inspect(self.events[:2]+(replace(self.events[2], evidence=(record, record)),))
        with self.assertRaises(LLMToolError):
            self.inspect(self.events[:2]+(replace(self.events[2], evidence=self.events[-1].evidence),))

    def test_strict_schema_types_bounds_and_secret_screening(self):
        for key, value in (('epoch', True), ('heartbeat_ttl_seconds', 0),
                           ('heartbeat_ttl_seconds', 86401), ('initial_sequence', 1),
                           ('agents', []), ('agents', self.expect.to_dict()['agents']*17)):
            raw = self.expect.to_dict(); raw[key] = value
            with self.subTest(key=key, value=str(value)[:30]), self.assertRaises((LLMToolError, ValueError)):
                t.parse_expectations(json.dumps(raw))
        for raw in ('{"epoch":7,"epoch":8}', '{}', '{', 'NaN', '['*10+'0'+']'*10,
                    '{"epoch":'+ '1'*3000+'}', '{"epoch":1.0}',
                    ' '* (t.FILE_LIMIT+1)):
            with self.assertRaises((LLMToolError, ValueError)):
                t.parse_expectations(raw)
        raw = self.expect.to_dict(); raw['authored_by_missing'] = []
        with self.assertRaises(LLMToolError):
            t.parse_expectations(json.dumps(raw))
        raw = self.events[0].to_dict(); raw['expect'] = self.expect.to_dict()
        with self.assertRaises(LLMToolError):
            t.parse_events(json.dumps(raw))
        for raw in ('{', '\n', wire_events(self.events)*26, ' '* (t.EVENT_LIMIT+1)):
            with self.assertRaises((LLMToolError, ValueError)):
                t.parse_events(raw)
        with self.assertRaises((LLMToolError, ValueError)):
            replace(self.expect.agents[0], root='/repo/api_key=SECRET')

    def test_empty_replay_is_incomplete(self):
        result = self.inspect(())
        self.assertEqual(result.exit_code, 1)
        self.assertIsNone(result.reported_state)
        self.assertIn('EVENTS_INCOMPLETE', result.findings)

    def test_raw_event_and_envelope_exact_byte_bounds(self):
        event = self.events[0].to_json()
        padded = event + ' '*(t.EVENT_LIMIT-len(event.encode('utf-8')))
        self.assertEqual(t.parse_events(padded), self.events[:1])
        with self.assertRaises(LLMToolError):
            t.parse_events(padded+' ')
        envelope = self.expect.to_json()
        padded = envelope+' '*(t.FILE_LIMIT-len(envelope.encode('utf-8')))
        self.assertEqual(t.parse_expectations(padded), self.expect)
        with self.assertRaises(LLMToolError):
            t.parse_expectations(padded+' ')

    def test_secret_material_in_supported_identity_and_raw_controls_reject(self):
        for agent_id in ('sk-'+'a'*40, 'agent\u202e', 'agent\x00'):
            with self.subTest(agent=agent_id), self.assertRaises((LLMToolError, ValueError)):
                replace(self.expect.agents[0], agent_id=agent_id)

    def test_pinned_authors_must_cover_developer_and_no_main_branch_claim(self):
        other = replace(self.expect.agents[0], agent_id='historical-author', session_id='old-session')
        result = self.inspect(expect=replace(self.expect, authored_by=(other,)))
        self.assertEqual(result.exit_code, 1)
        self.assertIn('AUTHORSHIP_INCOMPLETE', result.findings)
        with self.assertRaises(LLMToolError):
            replace(self.expect.agents[0], branch='main')
        self.assertEqual(replace(self.expect.agents[0], branch='automation/ai-loop').branch,
                         'automation/ai-loop')


if __name__ == '__main__':
    unittest.main()
