import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import task_completion, task_submission_cli
from llmtool.errors import LLMToolError


TASK_ID = 'a' * 32
RUN_ID = 'b' * 32
CONTRACT = 'c' * 64
HEAD = 'd' * 40
SOURCE = 'e' * 40
BASE = 'f' * 40
COMMON = '/repo/.git'
REVIEWED_AT = '2026-11-10T10:00:00Z'


def _task_record(task_id, *, created_at, executor='executor-d6', abandoned=False,
                 pm_assignment=None, provider_profile='codex'):
    return {
        'task_id': task_id,
        'run_id': RUN_ID,
        'created_at': created_at,
        'abandonment': {'reason': 'synthetic'} if abandoned else None,
        'task_binding': {'task_id': task_id, 'run_id': RUN_ID, 'contract_digest': CONTRACT},
        'developer': {'provider_profile': provider_profile},
        'pm_assignment': (pm_assignment if pm_assignment is not None else {
            'schema_version': 1, 'route': 'claude', 'risk': 'LOW',
            'goal': f'Goal for {task_id[:4]}.',
            'acceptance': [f'Acceptance for {task_id[:4]}.'],
        }),
        'completion': {
            'executor_task_id': executor,
            'evidence': {'common_dir': COMMON,
                         'result': {'result_head': HEAD, 'source_sha': SOURCE}},
        },
    }


class _PlainStore:
    def __init__(self, reviewed):
        self.reviewed = reviewed

    def get(self, task_id):
        if task_id != self.reviewed['task_id']:
            raise AssertionError(f'unexpected task id {task_id}')
        return self.reviewed


class _SiblingStore(_PlainStore):
    def __init__(self, reviewed, others):
        super().__init__(reviewed)
        self.others = dict(others)

    def task_ids(self):
        return [self.reviewed['task_id'], *self.others]

    def get(self, task_id):
        if task_id in self.others:
            return self.others[task_id]
        return super().get(task_id)


def _registration(task_id, trusted_agent_id):
    return SimpleNamespace(task_id=task_id, root='/repo', common_dir=COMMON,
                           branch='task-branch', source_sha=SOURCE, result_sha=HEAD,
                           trusted_agent_id=trusted_agent_id)


def _snapshot():
    return SimpleNamespace(root='/repo', common_dir=COMMON,
                           branch='task-branch', head=HEAD)


class ReviewPromptSiblingContractsTests(unittest.TestCase):
    def _render(self, store, *, reviewer='claude'):
        reviewer_identity = ('codex-reviewer' if reviewer == 'codex'
                             else 'claude-reviewer')
        registrations = {'executor-d6': _registration('executor-d6', 'agy'),
                         'reviewer-d6': _registration('reviewer-d6', reviewer_identity)}
        args = Namespace(task_id=TASK_ID, executor_task='executor-d6',
                         reviewer_task='reviewer-d6', pr=42, version=1)
        output = io.StringIO()

        def github_get(path):
            if path == 'pulls/42':
                return {'number': 42, 'head': {'sha': HEAD}, 'base': {'sha': BASE}}
            return {'total_count': 0, 'check_runs': []}

        with patch.object(task_completion, 'read_d6_registration',
                          side_effect=lambda task_id, *_a, **_kw: registrations[task_id]), \
                patch.object(task_completion, 'collect_git_snapshot',
                             side_effect=[_snapshot(), _snapshot()]), \
                patch.object(task_completion, '_github_get', side_effect=github_get), \
                redirect_stdout(output):
            code = task_submission_cli.run_task_review_prompt(args, store=store)
        self.assertEqual(code, 0)
        return output.getvalue()

    def _refuse(self, store, *, reviewer='claude'):
        reviewer_identity = ('codex-reviewer' if reviewer == 'codex'
                             else 'claude-reviewer')
        registrations = {'executor-d6': _registration('executor-d6', 'agy'),
                         'reviewer-d6': _registration('reviewer-d6', reviewer_identity)}
        args = Namespace(task_id=TASK_ID, executor_task='executor-d6',
                         reviewer_task='reviewer-d6', pr=42, version=1)
        output = io.StringIO()
        with patch.object(task_completion, 'read_d6_registration',
                          side_effect=lambda task_id, *_a, **_kw: registrations[task_id]), \
                patch.object(task_completion, 'collect_git_snapshot',
                             side_effect=[_snapshot(), _snapshot()]), \
                redirect_stdout(output), self.assertRaises(LLMToolError) as raised:
            task_submission_cli.run_task_review_prompt(args, store=store)
        self.assertEqual(output.getvalue(), '')
        return raised.exception.completion_code

    def test_three_task_chain_shows_all_three_contracts_in_order(self):
        first_id = '1' * 32
        middle_id = '2' * 32
        reviewed = _task_record(
            TASK_ID, created_at=REVIEWED_AT,
            pm_assignment={
                'schema_version': 1, 'route': 'agy', 'risk': 'LOW',
                'goal': 'Current goal.',
                'acceptance': ['Item from first.', 'Item from middle.', 'Current item.'],
            })
        others = {
            middle_id: _task_record(
                middle_id, created_at='2026-11-06T10:00:00Z',
                pm_assignment={
                    'schema_version': 1, 'route': 'agy', 'risk': 'LOW',
                    'goal': 'Middle goal.',
                    'acceptance': ['Item from middle.'],
                }),
            first_id: _task_record(
                first_id, created_at='2026-11-05T10:00:00Z',
                pm_assignment={
                    'schema_version': 1, 'route': 'agy', 'risk': 'LOW',
                    'goal': 'First goal.',
                    'acceptance': ['Item from first.'],
                }),
        }
        store = _SiblingStore(reviewed, others)
        for reviewer in ('claude', 'codex'):
            with self.subTest(reviewer=reviewer):
                output = self._render(store, reviewer=reviewer)
                self.assertEqual(output.count('<<< BEGIN ORIGINAL TASK CONTRACT DATA >>>'), 2)
                self.assertLess(
                    output.index('Task goal: "Current goal."'),
                    output.index('Original Task id: ' + json.dumps(first_id)))
                self.assertLess(
                    output.index('Original Task id: ' + json.dumps(first_id)),
                    output.index('Original Task id: ' + json.dumps(middle_id)))
                self.assertIn('Original Task goal: "Middle goal."', output)
                self.assertIn(
                    'Repair delta: current acceptance items not in any earlier contract: 3.',
                    output.splitlines())
                self.assertIn(
                    'Judge the PR against all contracts in order; where a later contract item '
                    'replaces an earlier one, the later item governs; an item not replaced still '
                    'applies, and the current Task contract governs last.',
                    output.splitlines())
                binding = (f'LLMTOOL-REVIEW-BINDING v1 task={TASK_ID} run={RUN_ID} '
                           f'contract={CONTRACT} head={HEAD}')
                self.assertIn(binding, output)

    def test_abandoned_sibling_is_excluded(self):
        first_id = '1' * 32
        abandoned_id = '2' * 32
        third_id = '3' * 32
        reviewed = _task_record(TASK_ID, created_at=REVIEWED_AT)
        others = {
            first_id: _task_record(first_id, created_at='2026-11-05T10:00:00Z'),
            abandoned_id: _task_record(
                abandoned_id, created_at='2026-11-06T10:00:00Z', abandoned=True),
            third_id: _task_record(third_id, created_at='2026-11-07T10:00:00Z'),
        }
        output = self._render(_SiblingStore(reviewed, others))
        self.assertEqual(output.count('<<< BEGIN ORIGINAL TASK CONTRACT DATA >>>'), 2)
        self.assertIn('Original Task id: ' + json.dumps(first_id), output)
        self.assertIn('Original Task id: ' + json.dumps(third_id), output)
        self.assertNotIn(abandoned_id, output)

    def test_invalid_sibling_contract_refuses(self):
        valid_id = '1' * 32
        bad_id = '2' * 32
        reviewed = _task_record(TASK_ID, created_at=REVIEWED_AT)
        bad = _task_record(bad_id, created_at='2026-11-06T10:00:00Z')
        bad['pm_assignment']['acceptance'] = []
        others = {
            valid_id: _task_record(valid_id, created_at='2026-11-05T10:00:00Z'),
            bad_id: bad,
        }
        self.assertEqual(
            self._refuse(_SiblingStore(reviewed, others)),
            'REVIEW_PROMPT_CONTRACT_MISSING')

    def test_eight_siblings_succeed_and_nine_refuse_set_too_large(self):
        reviewed = _task_record(TASK_ID, created_at=REVIEWED_AT)
        eight = {
            f'{index:032x}': _task_record(
                f'{index:032x}', created_at=f'2026-11-0{index}T10:00:00Z')
            for index in range(1, 9)
        }
        output = self._render(_SiblingStore(reviewed, eight))
        self.assertEqual(output.count('<<< BEGIN ORIGINAL TASK CONTRACT DATA >>>'), 8)

        nine = {
            **eight,
            f'{9:032x}': _task_record(
                f'{9:032x}', created_at='2026-11-09T10:00:00Z'),
        }
        self.assertEqual(
            self._refuse(_SiblingStore(reviewed, nine)),
            'REVIEW_PROMPT_CONTRACT_SET_TOO_LARGE')

    def test_single_task_d6_prompt_is_byte_identical(self):
        reviewed = _task_record(TASK_ID, created_at=REVIEWED_AT)
        for reviewer in ('claude', 'codex'):
            with self.subTest(reviewer=reviewer):
                plain = self._render(_PlainStore(reviewed), reviewer=reviewer)
                single = self._render(_SiblingStore(reviewed, {}), reviewer=reviewer)
                self.assertEqual(single, plain)
                self.assertNotIn('ORIGINAL TASK CONTRACT', single)
                self.assertNotIn('Repair delta', single)


if __name__ == '__main__':
    unittest.main()
