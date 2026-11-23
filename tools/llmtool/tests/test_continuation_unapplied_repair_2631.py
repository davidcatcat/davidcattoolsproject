"""#2631: completion of a PM continuation Task with an unapplied EC2 repair.

Real case: Task c7171878 continues ded81184 on the same D6 (source e46bdee6,
a descendant of the D6 base 0d178da2). Its EC2 repair claim (head c67da97f,
repair Task d8eeb45c) responded but the PM never applied the result; the D6
head 8c9cf37f is c67da97f plus one clean trusted-main merge. The chain
builder refused DEVELOPER_ARTIFACT_CHAIN_INVALID twice: the continuation's
source is not the D6 base, and the unapplied repair was treated as a chain
segment with no Developer commit.
"""
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError

BASE, SOURCE, CLAIM_HEAD, RESULT = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
MAIN = 'e' * 40
ARTIFACT = 'diff --git a/sample.py b/sample.py\n'
DIGEST = hashlib.sha256(ARTIFACT.encode()).hexdigest()
REPAIR_ID = '4' * 32


def _task():
    return {
        'task_id': '1' * 32,
        'developer': {'source_sha': SOURCE, 'write_paths': ['sample.py'],
                      'artifact': ARTIFACT, 'artifact_sha256': DIGEST},
        'review_repair_claims': [
            {'attempt': 1, 'state': 'DISPATCHED', 'head_sha': CLAIM_HEAD,
             'family': 'pr-2631-ec2', 'repair_task_id': REPAIR_ID}],
    }


class _Store:
    def __init__(self, repair):
        self.repair = repair

    def get(self, task_id):
        if task_id != REPAIR_ID:
            raise AssertionError(f'unexpected Task load {task_id}')
        return self.repair


def _responded():
    return {'developer': {'state': 'DEVELOPER_RESPONDED', 'source_sha': CLAIM_HEAD,
                          'artifact': ARTIFACT, 'artifact_sha256': DIGEST}}


class ContinuationUnappliedRepair2631Tests(unittest.TestCase):
    def _build(self, repair, *, tip, ancestor=True, trusted=MAIN,
               registration_source=BASE):
        registration = SimpleNamespace(root='/repo/task', branch='task-branch',
                                       source_sha=registration_source,
                                       result_sha=RESULT)
        tc = submission_cli.task_completion
        with patch.object(tc, 'read_d6_registration', return_value=registration), \
                patch.object(tc, '_is_ancestor', return_value=ancestor), \
                patch.object(tc, '_developer_tip', return_value=tip), \
                patch.object(submission_cli, '_continuation_repair_members',
                             return_value=[]):
            return submission_cli._completion_repair_artifact_segments(
                _task(), _Store(repair), executor_task='executor',
                common_dir='/repo/.git', root='/repo/task', branch='task-branch',
                source_sha=SOURCE, trusted_base_sha=trusted)

    def test_unapplied_responded_repair_behind_clean_main_merge_is_skipped(self):
        # Only clean trusted-main merges lie between the claim head and the D6
        # head: the repair made no commit, so the single-artifact path is used.
        self.assertIsNone(self._build(_responded(), tip=CLAIM_HEAD))

    def test_responded_repair_with_a_developer_commit_stays_in_the_chain(self):
        segments = self._build(_responded(), tip=RESULT)
        self.assertEqual(segments, [
            (ARTIFACT, DIGEST, SOURCE, CLAIM_HEAD),
            (ARTIFACT, DIGEST, CLAIM_HEAD, RESULT),
        ])

    def test_without_trusted_main_the_old_rule_applies(self):
        # No trusted main sha: a responded repair stays a segment (as before).
        segments = self._build(_responded(), tip=CLAIM_HEAD, trusted=None)
        self.assertEqual(len(segments), 2)
        # And an unusable repair whose head moved still refuses (fail closed).
        unusable = {'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'}}
        with self.assertRaises(LLMToolError) as raised:
            self._build(unusable, tip=CLAIM_HEAD, trusted=None)
        self.assertEqual(raised.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_unusable_repair_with_a_developer_commit_after_it_refuses(self):
        unusable = {'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'}}
        with self.assertRaises(LLMToolError) as raised:
            self._build(unusable, tip=RESULT)
        self.assertEqual(raised.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_continuation_source_must_descend_from_the_d6_base(self):
        with self.assertRaises(LLMToolError) as raised:
            self._build(_responded(), tip=CLAIM_HEAD, ancestor=False)
        self.assertEqual(raised.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_source_equal_to_the_d6_base_is_unchanged(self):
        self.assertIsNone(self._build(_responded(), tip=CLAIM_HEAD,
                                      registration_source=SOURCE))


if __name__ == '__main__':
    unittest.main()
