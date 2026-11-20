"""#2281 limit 15a: the merge train member maximum comes from policy.json.

A1: merge_train_build and merge_train_cli both read merge_train_members_max
    through repair_policy.load_policy; at the repository value (6) behaviour is
    unchanged.
A2: both paths refuse the same over-limit train at the configured value with
    their existing refusal codes (TRAIN_MEMBERS_INVALID and
    TRAIN_INPUT_INVALID).
A3: an absent key keeps the previous default (6); an invalid policy refuses
    POLICY_INVALID fail closed, with no silent fallback.
A4: the fixed 64-member cap in merge_train.build_candidate and
    merge_train_store is a real technical limit; a configured maximum above it
    is stated next to the bound and refused at load time (POLICY_INVALID), not
    silently capped to 64.
A5: the merged build path honours a raised configured maximum (8): a train of
    exactly 8 valid members is not refused for its member count, a train of 9
    is refused with TRAIN_MEMBERS_INVALID, and a train of one valid member is
    refused with TRAIN_MEMBERS_INVALID at the repository value and at the
    configured 8, the refusals before any Task store read.
"""

import contextlib
import io
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from llmtool import cli, merge_train_build, merge_train_cli, repair_policy
from llmtool.errors import LLMToolError

# policy.json before #2281 L0b: no merge_train_members_max key.
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
KEY = 'merge_train_members_max'


def _pairs(count):
    return [(format(index, '032x'), 100 + index) for index in range(1, count + 1)]


def _raw(count):
    return ['%s:%d' % pair for pair in _pairs(count)]


class _Untouched:
    """A store the refusal paths must never read."""

    def get(self, task_id):
        raise AssertionError('store must not be read')


def _build(pairs):
    return merge_train_build.build_train(
        pairs, store=_Untouched(), caller_key='caller', github_get=None,
        git_runner=None, gh_runner=None, root='.')


def _cli(raw):
    args = types.SimpleNamespace(train_action='build', member=raw)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = merge_train_cli.run_task_train(args, store=_Untouched())
    lines = out.getvalue().splitlines()
    assert len(lines) == 1, out.getvalue()
    return code, json.loads(lines[0])


class MergeTrainMembersPolicyTests(unittest.TestCase):
    # A1
    def test_both_paths_read_the_same_key(self):
        configured = dict(BASE, **{KEY: 9})
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=configured) as load:
            self.assertEqual(merge_train_build._max_members(), 9)
            self.assertEqual(merge_train_cli._max_members(), 9)
        self.assertEqual(load.call_count, 2)

    def test_repository_value_keeps_the_previous_behaviour(self):
        self.assertEqual(merge_train_build._max_members(), 6)
        self.assertEqual(merge_train_cli._max_members(), 6)
        self.assertIsNotNone(merge_train_build._member_pairs(_pairs(6)))
        self.assertIsNotNone(merge_train_cli._members(_raw(6)))

    # A2
    def test_build_refuses_over_the_configured_limit(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 3})):
            result = _build(_pairs(4))
        self.assertEqual(result, {'step': 'REFUSED',
                                  'codes': ['TRAIN_MEMBERS_INVALID'], 'result': None})

    def test_cli_refuses_over_the_configured_limit(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 3})):
            self.assertIsNone(merge_train_cli._members(_raw(4)))
            code, value = _cli(_raw(4))
        self.assertEqual(code, 1)
        self.assertEqual(value['step'], 'REFUSED')
        self.assertEqual(value['codes'], ['TRAIN_INPUT_INVALID'])

    def test_a_higher_configured_limit_is_honoured(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 8})):
            self.assertEqual(merge_train_cli._max_members(), 8)
            self.assertIsNotNone(merge_train_cli._members(_raw(8)))
            self.assertIsNone(merge_train_cli._members(_raw(9)))

    # A5: the merged build path honours a raised configured maximum too, and
    # keeps the two-member minimum under it.
    def test_build_honours_a_higher_configured_limit(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 8})):
            self.assertEqual(merge_train_build._max_members(), 8)
            self.assertIsNotNone(merge_train_build._member_pairs(_pairs(8), 8))
            self.assertIsNone(merge_train_build._member_pairs(_pairs(9), 8))

    def test_build_refuses_nine_valid_members_at_eight(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 8})):
            result = _build(_pairs(9))
        self.assertEqual(result, {'step': 'REFUSED',
                                  'codes': ['TRAIN_MEMBERS_INVALID'], 'result': None})

    def test_eight_valid_members_pass_the_member_count_at_eight(self):
        def no_store_read(*_args, **_kwargs):
            raise AssertionError('store must not be read')

        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 8})), \
                mock.patch.object(merge_train_build.pm_caller,
                                  'owner_refusal_code',
                                  side_effect=no_store_read), \
                mock.patch.object(merge_train_build.task_completion,
                                  'read_authenticated_main_sha',
                                  return_value='f' * 40):
            result = _build(_pairs(8))
        self.assertNotEqual(result['codes'], ['TRAIN_MEMBERS_INVALID'],
                            'eight valid members must not be refused for the '
                            'member count')
        self.assertNotEqual(result['codes'], ['TRAIN_CLOCK_INVALID'])

    def test_build_refuses_one_valid_member_at_the_repository_value(self):
        result = _build(_pairs(1))
        self.assertEqual(result, {'step': 'REFUSED',
                                  'codes': ['TRAIN_MEMBERS_INVALID'], 'result': None})

    def test_build_refuses_one_valid_member_at_eight(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 8})):
            result = _build(_pairs(1))
        self.assertEqual(result, {'step': 'REFUSED',
                                  'codes': ['TRAIN_MEMBERS_INVALID'], 'result': None})

    # A4 (F1 repair): the fixed 64-member candidate/record cap is a real
    # technical limit, so it is enforced at load time and never hides behind a
    # larger configured maximum.
    def test_the_technical_cap_value_is_accepted(self):
        with mock.patch.object(repair_policy, 'load_policy',
                               return_value=dict(BASE, **{KEY: 64})):
            self.assertEqual(merge_train_build._max_members(), 64)
            self.assertEqual(merge_train_cli._max_members(), 64)
            self.assertIsNotNone(merge_train_cli._members(_raw(64)))
            self.assertIsNone(merge_train_cli._members(_raw(65)))

    def test_a_configured_limit_above_the_technical_cap_is_refused_at_load(self):
        configured = dict(BASE, **{KEY: 65})
        with mock.patch.object(repair_policy, 'load_policy', return_value=configured):
            with self.assertRaises(LLMToolError) as loader:
                merge_train_build._max_members()
            with self.assertRaises(merge_train_cli._PolicyRefused) as caught:
                merge_train_cli._members(_raw(2))
            result = _build(_pairs(2))
            code, value = _cli(_raw(2))
        self.assertEqual(str(loader.exception), 'POLICY_INVALID')
        self.assertEqual(caught.exception.code, 'POLICY_INVALID')
        self.assertEqual(result, {'step': 'REFUSED', 'codes': ['POLICY_INVALID'],
                                  'result': None})
        self.assertEqual(code, 1)
        self.assertEqual(value['codes'], ['POLICY_INVALID'])

    # A3
    def test_absent_key_keeps_the_default(self):
        with mock.patch.object(repair_policy, 'load_policy', return_value=dict(BASE)):
            self.assertEqual(merge_train_build._max_members(), 6)
            self.assertEqual(merge_train_cli._max_members(), 6)
            self.assertEqual(_build(_pairs(7))['codes'], ['TRAIN_MEMBERS_INVALID'])
            code, value = _cli(_raw(7))
        self.assertEqual(code, 1)
        self.assertEqual(value['codes'], ['TRAIN_INPUT_INVALID'])

    def _policy_file(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            json.dump(value, stream)
        self.addCleanup(os.unlink, path)
        return path

    def test_invalid_policy_refuses_the_parser_before_any_call(self):
        path = self._policy_file(dict(BASE, **{KEY: 0}))
        member_a, member_b = _raw(2)
        out = io.StringIO()
        with mock.patch.object(repair_policy, 'policy_path', return_value=path), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as exited:
                cli.parser_for_cli().parse_args(
                    ['task', 'train', 'build', '--member', member_a, '--member', member_b])
        self.assertEqual(exited.exception.code, 1)
        value = json.loads(out.getvalue())
        self.assertEqual(value['step'], 'REFUSED')
        self.assertEqual(value['codes'], ['POLICY_INVALID'])

    def test_invalid_policy_refuses_policy_invalid_in_both_paths(self):
        path = self._policy_file(dict(BASE, **{KEY: 0}))
        with mock.patch.object(repair_policy, 'policy_path', return_value=path):
            with self.assertRaises(LLMToolError) as loader:
                repair_policy.load_policy()
            result = _build(_pairs(2))
            with self.assertRaises(merge_train_cli._PolicyRefused) as caught:
                merge_train_cli._members(_raw(2))
            code, value = _cli(_raw(2))
        self.assertEqual(str(loader.exception), 'POLICY_INVALID')
        self.assertEqual(result, {'step': 'REFUSED', 'codes': ['POLICY_INVALID'],
                                  'result': None})
        self.assertEqual(caught.exception.code, 'POLICY_INVALID')
        self.assertEqual(code, 1)
        self.assertEqual(value['codes'], ['POLICY_INVALID'])


if __name__ == '__main__':
    unittest.main()
