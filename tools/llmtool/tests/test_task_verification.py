"""A11b.5 task verification: pinned contract, strict evidence, no authority."""

import json
import os
import random
import socket
import subprocess
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from llmtool import task_verification as t
from llmtool.action_contract import TaskBinding
from llmtool.errors import LLMToolError

RUN = 'a' * 32
SUBJECT = 'b' * 40
CMD = 'c' * 64
ENV = 'd' * 64
ART = 'e' * 64
S = t.VerificationStatus
C = t.VerificationCode
K = t.CriterionKind
Src = t.EvidenceSource
O = t.EvidenceOutcome


def make_contract(**overrides):
    values = dict(schema_version=1, task_id='task-1', contract_version=1,
                  previous_digest=None, goal='Add verified feature',
                  authorized_scope=['src', 'tests'], forbidden_scope=['src/vendor'],
                  criteria=[t.AcceptanceCriterion('c1-tests', K.DETERMINISTIC_CHECK, 'Unit tests pass'),
                            t.AcceptanceCriterion('c2-review', K.REVIEW, 'Independent review'),
                            t.AcceptanceCriterion('c3-artifact', K.ARTIFACT, 'Report produced')])
    values.update(overrides)
    return t.TaskContract(**values)


def record(contract, criterion_id, evidence_id, source=None, outcome=O.PASSED,
           complete=True, run_id=RUN, subject=SUBJECT, task=None, **overrides):
    kinds = {c.criterion_id: c.kind for c in contract.criteria}
    if source is None:
        source = {K.DETERMINISTIC_CHECK: Src.COMMAND, K.REVIEW: Src.REVIEW,
                  K.ARTIFACT: Src.ARTIFACT}[kinds[criterion_id]]
    command = source is Src.COMMAND
    exit_status = None
    if command and outcome in (O.PASSED, O.FAILED):
        exit_status = 0 if outcome is O.PASSED else 1
    values = dict(schema_version=1, evidence_id=evidence_id, criterion_id=criterion_id,
                  task=task or contract.binding(run_id), subject_digest=subject,
                  source=source, outcome=outcome,
                  command_digest=CMD if command else None, exit_status=exit_status,
                  environment_digest=ENV if command else None,
                  artifact_digests=[ART] if source is Src.ARTIFACT else [],
                  complete=complete)
    values.update(overrides)
    return t.EvidenceRecord(**values)


def all_pass(contract):
    return [record(contract, 'c1-tests', 'e1'), record(contract, 'c2-review', 'e2'),
            record(contract, 'c3-artifact', 'e3')]


def verify(contract, records, **overrides):
    kwargs = dict(pinned_digest=contract.digest, run_id=RUN, subject_digest=SUBJECT,
                  evidence=records)
    kwargs.update(overrides)
    return t.verify_task(contract, **kwargs)


def codes(result):
    return [(f.code, f.criterion_id, f.evidence_id) for f in result.findings]


def statuses(result):
    return {c.criterion_id: c.status for c in result.criteria}


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.contract = make_contract()

    def test_all_complete_pass(self):
        result = verify(self.contract, all_pass(self.contract))
        self.assertIs(result.status, S.PASS)
        self.assertTrue(result.verified)
        self.assertEqual(codes(result), [])
        self.assertEqual([c.evidence_ids for c in result.criteria], [('e1',), ('e2',), ('e3',)])
        self.assertEqual(result.contract_digest, self.contract.digest)

    def test_never_authorizes_and_has_no_done(self):
        result = verify(self.contract, all_pass(self.contract))
        self.assertIs(result.authorized, False)
        self.assertIs(result.to_dict()['authorized'], False)
        self.assertIs(json.loads(result.to_json())['authorized'], False)
        self.assertFalse(hasattr(result, 'done'))
        self.assertNotIn('done', result.to_dict())

    def test_no_evidence_not_tested(self):
        result = verify(self.contract, [])
        self.assertIs(result.status, S.NOT_TESTED)
        self.assertFalse(result.verified)
        self.assertEqual(codes(result), [(C.MISSING_EVIDENCE, cid, None) for cid in
                                         ('c1-tests', 'c2-review', 'c3-artifact')])

    def test_partial_coverage_incomplete(self):
        result = verify(self.contract, all_pass(self.contract)[:2])
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.MISSING_EVIDENCE, 'c3-artifact', None)])

    def test_fail_dominates(self):
        evidence = all_pass(self.contract)
        evidence[1] = record(self.contract, 'c2-review', 'e2', outcome=O.FAILED)
        result = verify(self.contract, evidence[:2])
        self.assertIs(result.status, S.FAIL)
        self.assertEqual(codes(result), [(C.EVIDENCE_FAILED, 'c2-review', 'e2'),
                                         (C.MISSING_EVIDENCE, 'c3-artifact', None)])

    def test_fail_dominates_later_pass_on_same_criterion(self):
        evidence = all_pass(self.contract) + [
            record(self.contract, 'c1-tests', 'e0', outcome=O.FAILED)]
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.FAIL)
        self.assertIs(statuses(result)['c1-tests'], S.FAIL)
        self.assertEqual(result.criteria[0].evidence_ids, ('e0', 'e1'))
        self.assertEqual(codes(result), [(C.EVIDENCE_FAILED, 'c1-tests', 'e0')])

    def test_passed_but_incomplete(self):
        evidence = all_pass(self.contract)
        evidence[0] = record(self.contract, 'c1-tests', 'e1', complete=False)
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertIs(statuses(result)['c1-tests'], S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.EVIDENCE_PARTIAL, 'c1-tests', 'e1')])

    def test_interrupted(self):
        evidence = all_pass(self.contract)
        evidence[0] = record(self.contract, 'c1-tests', 'e1', outcome=O.INTERRUPTED)
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.EVIDENCE_INTERRUPTED, 'c1-tests', 'e1')])

    def test_not_run(self):
        evidence = all_pass(self.contract)
        evidence[0] = record(self.contract, 'c1-tests', 'e1', outcome=O.NOT_RUN)
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertIs(statuses(result)['c1-tests'], S.NOT_TESTED)
        self.assertEqual(codes(result), [(C.EVIDENCE_NOT_RUN, 'c1-tests', 'e1')])
        only = verify(make_contract(criteria=[self.contract.criteria[0]]),
                      [record(make_contract(criteria=[self.contract.criteria[0]]),
                              'c1-tests', 'e1', outcome=O.NOT_RUN)])
        self.assertIs(only.status, S.NOT_TESTED)

    def test_pass_and_not_run_on_one_criterion_is_not_pass(self):
        evidence = all_pass(self.contract) + [
            record(self.contract, 'c1-tests', 'e4', outcome=O.NOT_RUN)]
        result = verify(self.contract, evidence)
        self.assertIs(statuses(result)['c1-tests'], S.NOT_TESTED)
        self.assertIs(result.status, S.INCOMPLETE)

    def test_model_claim_alone_not_tested(self):
        claims = [record(self.contract, c.criterion_id, 'm%d' % i, source=Src.MODEL_CLAIM)
                  for i, c in enumerate(self.contract.criteria)]
        result = verify(self.contract, claims)
        self.assertIs(result.status, S.NOT_TESTED)
        self.assertEqual(codes(result), [
            (C.MISSING_EVIDENCE, 'c1-tests', None), (C.MISSING_EVIDENCE, 'c2-review', None),
            (C.MISSING_EVIDENCE, 'c3-artifact', None),
            (C.MODEL_CLAIM_IGNORED, 'c1-tests', 'm0'), (C.MODEL_CLAIM_IGNORED, 'c2-review', 'm1'),
            (C.MODEL_CLAIM_IGNORED, 'c3-artifact', 'm2')])

    def test_model_claim_plus_failing_test_fails(self):
        evidence = all_pass(self.contract)
        evidence[0] = record(self.contract, 'c1-tests', 'e1', outcome=O.FAILED)
        evidence.append(record(self.contract, 'c1-tests', 'm1', source=Src.MODEL_CLAIM))
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.FAIL)
        self.assertEqual(codes(result), [(C.EVIDENCE_FAILED, 'c1-tests', 'e1'),
                                         (C.MODEL_CLAIM_IGNORED, 'c1-tests', 'm1')])

    def test_model_claim_does_not_cap_pass(self):
        evidence = all_pass(self.contract) + [
            record(self.contract, 'c1-tests', 'm1', source=Src.MODEL_CLAIM)]
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.PASS)
        self.assertEqual(codes(result), [(C.MODEL_CLAIM_IGNORED, 'c1-tests', 'm1')])

    def test_unpinned_contract_evaluates_nothing(self):
        evidence = all_pass(self.contract)
        evidence[0] = record(self.contract, 'c1-tests', 'e1', outcome=O.FAILED)
        result = verify(self.contract, evidence, pinned_digest='f' * 64)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.CONTRACT_NOT_PINNED, None, None)])
        self.assertEqual(statuses(result), {c.criterion_id: S.NOT_TESTED
                                            for c in self.contract.criteria})
        self.assertEqual([c.evidence_ids for c in result.criteria], [(), (), ()])

    def test_old_version_evidence_cannot_pass_new_version(self):
        v2 = make_contract(contract_version=2, previous_digest=self.contract.digest,
                           goal='Add verified feature, revised')
        old = all_pass(self.contract)
        result = verify(v2, old)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), sorted(
            [(C.BINDING_MISMATCH, r.criterion_id, r.evidence_id) for r in old] +
            [(C.MISSING_EVIDENCE, c.criterion_id, None) for c in v2.criteria],
            key=lambda x: (x[0].value, x[1], x[2] or '')))
        # Even with a forged version number, the digest binding still differs.
        forged = TaskBinding('task-1', RUN, 2, self.contract.digest)
        result = verify(v2, [record(v2, r.criterion_id, r.evidence_id, task=forged)
                             for r in old])
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual({f.code for f in result.findings},
                         {C.BINDING_MISMATCH, C.MISSING_EVIDENCE})

    def test_other_run_rejected(self):
        evidence = all_pass(self.contract)
        evidence[2] = record(self.contract, 'c3-artifact', 'e3', run_id='9' * 32)
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.BINDING_MISMATCH, 'c3-artifact', 'e3'),
                                         (C.MISSING_EVIDENCE, 'c3-artifact', None)])

    def test_subject_mismatch_rejected(self):
        evidence = all_pass(self.contract)
        evidence[0] = record(self.contract, 'c1-tests', 'e1', subject='f' * 64)
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.MISSING_EVIDENCE, 'c1-tests', None),
                                         (C.SUBJECT_MISMATCH, 'c1-tests', 'e1')])

    def test_unknown_criterion_caps(self):
        evidence = all_pass(self.contract) + [
            replace(record(self.contract, 'c1-tests', 'e9'), criterion_id='c9-extra')]
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertTrue(all(c.status is S.PASS for c in result.criteria))
        self.assertEqual(codes(result), [(C.UNKNOWN_CRITERION, 'c9-extra', 'e9')])

    def test_source_kind_mismatch_rejected(self):
        evidence = all_pass(self.contract)
        evidence[1] = record(self.contract, 'c2-review', 'e2', source=Src.COMMAND)
        result = verify(self.contract, evidence)
        self.assertIs(result.status, S.INCOMPLETE)
        self.assertEqual(codes(result), [(C.MISSING_EVIDENCE, 'c2-review', None),
                                         (C.SOURCE_KIND_MISMATCH, 'c2-review', 'e2')])
        # A rejected FAILED record cannot produce FAIL either.
        evidence[1] = record(self.contract, 'c2-review', 'e2', source=Src.ARTIFACT,
                             outcome=O.FAILED)
        self.assertIs(verify(self.contract, evidence).status, S.INCOMPLETE)

    def test_order_independence(self):
        evidence = all_pass(self.contract) + [
            record(self.contract, 'c1-tests', 'e4', outcome=O.FAILED),
            record(self.contract, 'c2-review', 'e5', complete=False),
            record(self.contract, 'c3-artifact', 'e6', run_id='9' * 32),
            record(self.contract, 'c1-tests', 'm1', source=Src.MODEL_CLAIM),
            replace(record(self.contract, 'c1-tests', 'e7'), criterion_id='c0-x')]
        expected = verify(self.contract, evidence).to_json()
        rng = random.Random(7)
        for _ in range(20):
            shuffled = list(evidence)
            rng.shuffle(shuffled)
            self.assertEqual(verify(self.contract, tuple(shuffled)).to_json(), expected)

    def test_invalid_inputs_fail_closed(self):
        evidence = all_pass(self.contract)
        bad = [dict(pinned_digest='F' * 64), dict(pinned_digest=None),
               dict(run_id='a' * 31), dict(run_id=1), dict(subject_digest='b' * 39),
               dict(evidence=evidence + [evidence[0]]), dict(evidence=evidence[0]),
               dict(evidence=[evidence[0].to_dict()]), dict(evidence=None)]
        for overrides in bad:
            with self.subTest(overrides=list(overrides)):
                with self.assertRaises(LLMToolError):
                    verify(self.contract, evidence, **overrides)
        with self.assertRaises(LLMToolError):
            t.verify_task(self.contract.to_dict(), pinned_digest=self.contract.digest,
                          run_id=RUN, subject_digest=SUBJECT, evidence=[])

    def test_duplicate_evidence_id_rejected(self):
        evidence = [record(self.contract, 'c1-tests', 'e1'),
                    record(self.contract, 'c2-review', 'e1')]
        with self.assertRaises(LLMToolError):
            verify(self.contract, evidence)

    def test_record_limit(self):
        many = [record(self.contract, 'c1-tests', 'e%03d' % i) for i in range(128)]
        self.assertIs(verify(self.contract, many).status, S.INCOMPLETE)
        many.append(record(self.contract, 'c1-tests', 'e128'))
        with self.assertRaises(LLMToolError):
            verify(self.contract, many)


class EvidenceRecordTests(unittest.TestCase):
    def setUp(self):
        self.contract = make_contract()
        self.base = record(self.contract, 'c1-tests', 'e1')

    def invalid(self, **changes):
        with self.assertRaises(LLMToolError):
            replace(self.base, **changes)

    def test_command_field_requirements(self):
        self.invalid(command_digest=None)
        self.invalid(environment_digest=None)
        self.invalid(exit_status=None)
        self.invalid(exit_status=1)          # PASSED needs exit 0
        self.invalid(exit_status=False)      # bool is not int
        self.invalid(exit_status=0.0)
        self.invalid(outcome=O.FAILED, exit_status=None)
        self.invalid(outcome=O.FAILED, exit_status=256)
        self.invalid(outcome=O.FAILED, exit_status=-256)
        self.invalid(outcome=O.NOT_RUN, exit_status=0)
        replace(self.base, outcome=O.FAILED, exit_status=-255)
        replace(self.base, outcome=O.FAILED, exit_status=255)
        replace(self.base, outcome=O.INTERRUPTED, exit_status=None)
        replace(self.base, outcome=O.INTERRUPTED, exit_status=-9)
        replace(self.base, outcome=O.NOT_RUN, exit_status=None)
        review = record(self.contract, 'c2-review', 'e2')
        for name, value in (('command_digest', CMD), ('exit_status', 0),
                            ('environment_digest', ENV)):
            with self.subTest(name=name), self.assertRaises(LLMToolError):
                replace(review, **{name: value})
        with self.assertRaises(LLMToolError):
            replace(record(self.contract, 'c1-tests', 'm', source=Src.MODEL_CLAIM),
                    exit_status=0)

    def test_artifact_digest_requirement(self):
        artifact = record(self.contract, 'c3-artifact', 'e3')
        with self.assertRaises(LLMToolError):
            replace(artifact, artifact_digests=[])
        replace(artifact, artifact_digests=[], outcome=O.FAILED)
        for digests in (['f' * 64, 'e' * 64], [ART, ART], ['E' * 64], ['e' * 40],
                        ['%064x' % i for i in range(33)], 'e' * 64, [1]):
            with self.subTest(digests=str(digests)[:20]), self.assertRaises(LLMToolError):
                replace(artifact, artifact_digests=digests)
        self.assertEqual(replace(artifact, artifact_digests=[ART, 'f' * 64]).artifact_digests,
                         (ART, 'f' * 64))

    def test_bool_is_not_int_and_types(self):
        for changes in (dict(schema_version=True), dict(complete=1), dict(complete=None),
                        dict(source='COMMAND'), dict(outcome='PASSED'),
                        dict(task=self.base.task.to_dict()), dict(subject_digest='b' * 41),
                        dict(evidence_id='-bad'), dict(criterion_id='')):
            with self.subTest(changes=list(changes)):
                self.invalid(**changes)
        with self.assertRaises(LLMToolError):
            replace(self.contract, contract_version=True)
        with self.assertRaises(LLMToolError):
            replace(self.contract, schema_version=True)

    def test_immutability(self):
        self.assertIsInstance(self.base.artifact_digests, tuple)
        with self.assertRaises(FrozenInstanceError):
            self.base.complete = False
        self.assertIsInstance(self.contract.authorized_scope, tuple)
        self.assertIsInstance(self.contract.forbidden_scope, tuple)
        self.assertIsInstance(self.contract.criteria, tuple)
        with self.assertRaises(FrozenInstanceError):
            self.contract.goal = 'other'
        result = verify(self.contract, all_pass(self.contract))
        self.assertIsInstance(result.criteria, tuple)
        self.assertIsInstance(result.findings, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.status = S.FAIL


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = make_contract()

    def invalid(self, **changes):
        with self.assertRaises(LLMToolError):
            make_contract(**changes)

    def test_scope_rules(self):
        for scope in (['tests', 'src'], ['src', 'src'], ['/abs'], ['src/../etc'], ['..'],
                      ['.git'], ['src/.git/config'], ['src/'], [], ['x'] * 0 + ['a-' + 'b'] * 2,
                      ['%02d' % i for i in range(33)], 'src', [1]):
            with self.subTest(scope=str(scope)[:30]):
                self.invalid(authorized_scope=scope)
        for scope in (['src/z', 'src/vendor'], ['src/vendor', 'src/vendor'], ['/etc'],
                      ['../x'], ['.git'], ['src'], ['tests'], ['src/vendor/'], 'src/vendor',
                      ['%02d' % i for i in range(33)]):
            with self.subTest(forbidden=str(scope)[:30]):
                self.invalid(forbidden_scope=scope)
        # Overlap: forbidden equals or contains an authorized path.
        self.invalid(authorized_scope=['src/app'], forbidden_scope=['src'])
        # A forbidden carve-out beneath an authorized path is allowed; empty is allowed.
        self.assertEqual(make_contract(forbidden_scope=[]).forbidden_scope, ())
        make_contract(authorized_scope=['src'], forbidden_scope=['src/vendor', 'srcx'])

    def test_criteria_rules(self):
        c1, c2, c3 = self.contract.criteria
        for criteria in ([], [c2, c1], [c1, c1], [c1.to_dict()], c1,
                         [replace(c1, criterion_id='c%02d' % i) for i in range(33)]):
            with self.subTest(criteria=str(criteria)[:30]):
                self.invalid(criteria=criteria)
        with self.assertRaises(LLMToolError):
            t.AcceptanceCriterion('c1', 'REVIEW', 'x')
        for description in ('', ' ', 'x' * 513, 'a\nb', 'a‮b', 'a​b'):
            with self.subTest(description=len(description)), self.assertRaises(LLMToolError):
                t.AcceptanceCriterion('c1', K.REVIEW, description)
        t.AcceptanceCriterion('c1', K.REVIEW, 'x' * 512)
        t.AcceptanceCriterion('c1', K.REVIEW, '中文' + 'x' * 506)
        with self.assertRaises(LLMToolError):
            t.AcceptanceCriterion('c1', K.REVIEW, '中文' + 'x' * 507)
        self.invalid(goal='g' * 1025)
        make_contract(goal='g' * 1024)

    def test_credential_text_rejected_without_echo(self):
        secrets_ = ('ghp_' + 'A1b2C3d4E5f6G7h8I9j0K', 'token=hunter2hunter2',
                    'sk-' + 'Zq9' * 6, 'Authorization: Bearer abcdefghijklmnop')
        for secret in secrets_:
            for build in (lambda s: make_contract(goal='do it ' + s),
                          lambda s: t.AcceptanceCriterion('c1', K.REVIEW, 'see ' + s)):
                with self.subTest(secret=secret[:3]):
                    with self.assertRaises(LLMToolError) as caught:
                        build(secret)
                    exc = caught.exception
                    rendered = ' '.join((str(exc), repr(exc), repr(exc.args),
                                         str(exc.__context__), str(exc.__cause__)))
                    self.assertNotIn(secret, rendered)
                    self.assertIn('details omitted', str(exc))
        with self.assertRaises(LLMToolError) as caught:
            t.parse_task_contract(contract_json(self.contract, goal='key ' + secrets_[0]))
        self.assertNotIn(secrets_[0], str(caught.exception) + repr(caught.exception.args))

    def test_versioning_and_successor(self):
        self.invalid(previous_digest='f' * 64)
        self.invalid(contract_version=2, previous_digest=None)
        self.invalid(contract_version=2, previous_digest='F' * 64)
        self.invalid(contract_version=0)
        self.invalid(contract_version=2**31)
        self.invalid(schema_version=2)
        make_contract(contract_version=2**31 - 1, previous_digest='f' * 64)
        v2 = make_contract(contract_version=2, previous_digest=self.contract.digest,
                           goal='Revised goal')
        self.assertTrue(t.is_contract_successor(self.contract, v2))
        self.assertNotEqual(v2.digest, self.contract.digest)
        self.assertFalse(t.is_contract_successor(v2, self.contract))
        self.assertFalse(t.is_contract_successor(self.contract, self.contract))
        self.assertFalse(t.is_contract_successor(self.contract, replace(v2, task_id='task-2')))
        self.assertFalse(t.is_contract_successor(self.contract,
                                                 replace(v2, previous_digest='f' * 64)))
        self.assertFalse(t.is_contract_successor(self.contract,
            make_contract(contract_version=3, previous_digest=self.contract.digest)))
        with self.assertRaises(LLMToolError):
            t.is_contract_successor(self.contract, v2.to_dict())
        binding = v2.binding(RUN)
        self.assertEqual(binding, TaskBinding('task-1', RUN, 2, v2.digest))
        with self.assertRaises(LLMToolError):
            v2.binding('short')

    def test_stable_domain_separated_digest(self):
        self.assertEqual(self.contract.digest, EXPECTED_DIGEST)
        self.assertEqual(make_contract().digest, EXPECTED_DIGEST)
        import hashlib
        envelope = json.dumps({'domain': 'llmtool.task-contract', 'version': 1,
                               'value': self.contract.to_dict()}, sort_keys=True,
                              separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        self.assertEqual(hashlib.sha256(envelope).hexdigest(), EXPECTED_DIGEST)
        raw = hashlib.sha256(self.contract.to_json().encode('utf-8')).hexdigest()
        self.assertNotEqual(raw, EXPECTED_DIGEST)
        c1, c2, c3 = self.contract.criteria
        mutations = dict(task_id='task-2', contract_version=2, previous_digest='e' * 64,
                         goal='Other goal', authorized_scope=['src', 'test'],
                         forbidden_scope=['src/vendor2'],
                         criteria=[replace(c1, description='Other'), c2, c3])
        seen = {EXPECTED_DIGEST}
        for name, value in mutations.items():
            changes = {name: value}
            if name == 'contract_version':
                changes['previous_digest'] = 'f' * 64
            if name == 'previous_digest':
                changes['contract_version'] = 2
            digest = make_contract(**changes).digest
            self.assertNotIn(digest, seen, name)
            seen.add(digest)
        for crit in (replace(c1, criterion_id='c0-tests'), replace(c1, kind=K.REVIEW)):
            digest = make_contract(criteria=sorted([crit, c2, c3],
                                                   key=lambda c: c.criterion_id)).digest
            self.assertNotIn(digest, seen)
            seen.add(digest)


EXPECTED_DIGEST = 'cf9fb21f3bba9398f6b7c3772cfa0220f21541d700b913bf8ca1ebcd07655693'


def contract_json(contract, **changes):
    value = contract.to_dict()
    value.update(changes)
    return json.dumps(value)


def evidence_json(item, **changes):
    value = item.to_dict()
    value.update(changes)
    return json.dumps(value)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.contract = make_contract()
        self.record = record(self.contract, 'c1-tests', 'e1')

    def test_round_trip(self):
        self.assertEqual(t.parse_task_contract(self.contract.to_json()), self.contract)
        self.assertEqual(t.parse_task_contract(self.contract.to_json()).digest,
                         self.contract.digest)
        for item in all_pass(self.contract):
            self.assertEqual(t.parse_evidence_record(item.to_json()), item)

    def test_strict_contract_json(self):
        good = self.contract.to_dict()
        missing = dict(good)
        del missing['goal']
        crit_extra = json.loads(json.dumps(good))
        crit_extra['criteria'][0]['weight'] = 1
        crit_kind = json.loads(json.dumps(good))
        crit_kind['criteria'][0]['kind'] = 'SELF_ASSESSMENT'
        cases = [json.dumps(dict(good, extra=1)), json.dumps(missing),
                 json.dumps(crit_extra), json.dumps(crit_kind),
                 contract_json(self.contract, schema_version=2),
                 contract_json(self.contract, schema_version=True),
                 contract_json(self.contract, contract_version=1.0),
                 contract_json(self.contract, authorized_scope='src'),
                 contract_json(self.contract, criteria={}),
                 self.contract.to_json().replace('"contract_version":1',
                                                 '"contract_version":NaN'),
                 self.contract.to_json()[:-1] + ',"goal":"x"}',
                 '[]', '', 'null', b'{}', None]
        for text in cases:
            with self.subTest(text=str(text)[:40]), self.assertRaises(LLMToolError):
                t.parse_task_contract(text)

    def test_strict_evidence_json(self):
        good = self.record.to_dict()
        task_extra = json.loads(json.dumps(good))
        task_extra['task']['scope'] = 'x'
        cases = [json.dumps(dict(good, verified=True)),
                 json.dumps({k: v for k, v in good.items() if k != 'complete'}),
                 json.dumps(task_extra),
                 evidence_json(self.record, schema_version=2),
                 evidence_json(self.record, complete=1),
                 evidence_json(self.record, exit_status=False),
                 evidence_json(self.record, exit_status=0.0),
                 evidence_json(self.record, source='MODEL'),
                 evidence_json(self.record, outcome='SUCCESS'),
                 evidence_json(self.record, outcome='passed'),
                 evidence_json(self.record, artifact_digests=None),
                 self.record.to_json().replace('"exit_status":0', '"exit_status":Infinity'),
                 self.record.to_json()[:-1] + ',"complete":true}']
        for text in cases:
            with self.subTest(text=text[:60]), self.assertRaises(LLMToolError):
                t.parse_evidence_record(text)

    def test_pure_no_io(self):
        contract_text = self.contract.to_json()
        evidence_text = [r.to_json() for r in all_pass(self.contract)]

        def boom(*args, **kwargs):
            raise AssertionError('I/O attempted')
        with patch('builtins.open', boom), patch('os.open', boom), patch('os.stat', boom), \
                patch.object(subprocess, 'Popen', boom), patch('os.system', boom), \
                patch.object(socket, 'socket', boom):
            contract = t.parse_task_contract(contract_text)
            evidence = [t.parse_evidence_record(text) for text in evidence_text]
            result = t.verify_task(contract, pinned_digest=contract.digest, run_id=RUN,
                                   subject_digest=SUBJECT, evidence=evidence)
            self.assertIs(result.status, S.PASS)
            result.to_json()


if __name__ == '__main__':
    unittest.main()
