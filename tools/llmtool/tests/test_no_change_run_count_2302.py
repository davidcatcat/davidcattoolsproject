"""#2302 (#2415, same class as #2098): a CLAUDE_NO_CHANGES run is not an effective repair.

llmtool records CLAUDE_NO_CHANGES only after it confirmed the D6 HEAD did not
move and the run's worktree diff was empty, so the record alone proves the run
did no work. Before this fix the guard also required the live D6 to still equal
the run's source SHA; once a later run moved that D6 the no-change run counted,
which raised a false High Debug requirement (the #2302 part 1 deadlock).

Every test goes through the real delegate repair guards
(_check_delegate_repair_cap / _check_chain_repair_count). IDs and SHAs are
repeated hex digits; no secret-shaped value.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.repair_policy import REVIEW_REPAIRS_WITHOUT_DEBUG

from test_task_delegate_repair_cap import _EXECUTOR_TASK, _HEAD, _POLICY, _patch_trust_and_binding
from test_effective_repair_count_2367 import _execution, _guard, _store_with

_LIMIT = '^DELEGATE_REPAIR_LIMIT_REACHED$'
_DEBUG = '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'
_OTHER_D6 = 'executor-other-d6'


def _no_change_record(*, executor=_OTHER_D6, created_at='2026-11-04T00:00:00Z',
                      **developer_changes):
    """A stored Claude run that llmtool recorded as CLAUDE_NO_CHANGES."""
    developer = {
        'provider_profile': 'claude', 'state': 'BLOCKED', 'error_code': 'CLAUDE_NO_CHANGES',
        'artifact': None, 'artifact_sha256': None, 'usage': None,
        'execution': _execution(status='BLOCKED', process_exit_code=0),
        'source_sha': _HEAD, 'write_paths': ['sample.py'], 'created_at': created_at,
    }
    developer.update(developer_changes)
    return {'completion': {'executor_task_id': executor}, 'developer': developer}


def _responded(created_at, *, executor=_OTHER_D6):
    return {'completion': {'executor_task_id': executor},
            'developer': {'provider_profile': 'claude', 'state': 'DEVELOPER_RESPONDED',
                          'created_at': created_at}}


def _moved(_source_sha, write_paths=None):
    """The live D6 no longer equals the run's source (a later run moved it)."""
    return False


def _chain(members, *, repair_max=5, probe=_moved, args=None):
    args = args or SimpleNamespace(task_id='f' * 32, executor_task=_EXECUTOR_TASK)
    policy = dict(_POLICY, repair_max=repair_max)
    with patch.object(task_submission_cli, '_d6_unchanged_probe', return_value=probe):
        return task_submission_cli._check_chain_repair_count(
            args, None, None, policy, {'members': members})


class NoChangeRunGuardTests(unittest.TestCase):
    """repair_max=1: one real run plus the run under test passes only if it is excluded."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def test_no_change_run_is_excluded_even_after_its_d6_moved(self):
        store = _store_with(_no_change_record(executor=_EXECUTOR_TASK))
        for probe in (None, _moved):
            self.assertIsNone(_guard(store, probe=probe))
        self.assertEqual(store.writes, [])

    def test_no_change_run_on_another_d6_is_excluded_in_the_chain(self):
        _chain({'1' * 32: _responded('2026-11-03T00:00:00Z'),
                '2' * 32: _no_change_record()}, repair_max=1)

    def test_doubtful_no_change_records_still_count(self):
        doubtful = {
            'artifact present': {'artifact': 'diff --git a/sample.py b/sample.py\n',
                                 'artifact_sha256': 'c' * 64},
            'changed files listed': {'changed_files': ['sample.py']},
            'cleanup not confirmed': {'execution': _execution(cleanup_status='UNKNOWN')},
            'other provider': {'provider_profile': 'agy'},
            'other state': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'},
            'other code': {'error_code': 'CLAUDE_STATUS_FAILED'},
        }
        for name, changes in doubtful.items():
            with self.subTest(record=name):
                with self.assertRaisesRegex(LLMToolError, _LIMIT):
                    _chain({'1' * 32: _responded('2026-11-03T00:00:00Z'),
                            '2' * 32: _no_change_record(**changes)}, repair_max=1)

    def test_a_run_that_changed_files_still_counts(self):
        with self.assertRaisesRegex(LLMToolError, _LIMIT):
            _chain({'1' * 32: _responded('2026-11-03T00:00:00Z'),
                    '2' * 32: _responded('2026-11-04T00:00:00Z')}, repair_max=1)


class LineageShape2302Tests(unittest.TestCase):
    """The #2302 part 1 lineage: original, security repair, no-change port try, port."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _members(self, *, third):
        return {
            '1' * 32: _responded('2026-11-20T16:41:27Z'),          # original run
            '2' * 32: _responded('2026-11-20T20:42:25Z'),          # security repair 1
            '3' * 32: third,                                       # port attempt
            '4' * 32: _responded('2026-11-21T00:14:53Z'),          # port
        }

    def test_review_count_is_three_and_no_debug_is_required(self):
        self.assertEqual(REVIEW_REPAIRS_WITHOUT_DEBUG, 3)
        members = self._members(third=_no_change_record(created_at='2026-11-20T21:48:32Z'))
        self.assertIsNone(_chain(members))

    def test_the_same_lineage_with_a_real_third_run_still_requires_a_debug(self):
        members = self._members(third=_responded('2026-11-20T21:48:32Z'))
        with self.assertRaisesRegex(LLMToolError, _DEBUG):
            _chain(members)

    def test_a_no_change_retry_on_the_new_d6_keeps_the_count(self):
        # Follow-on state 2: a no-change continuation run does not raise the count.
        members = self._members(third=_no_change_record(created_at='2026-11-20T21:48:32Z'))
        members['5' * 32] = _no_change_record(executor=_EXECUTOR_TASK,
                                              created_at='2026-11-22T10:00:00Z')
        self.assertIsNone(_chain(members))


if __name__ == '__main__':
    unittest.main()
