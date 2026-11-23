"""#2602 (d)/(e): task advance refusals explain themselves.

E1/E2: an exception without a valid code keeps its type name, never its
message, bounded and code-shaped. D1: PUBLISH_OUT_OF_SCOPE names the
out-of-scope paths (bounded) and the total count. D2: a Developer result with
no new commit over its base stops as PUBLISH_NO_NEW_COMMIT before EC2, with a
next step.
"""

import os
import re
import unittest

from llmtool import task_advance
import test_task_advance


class RefusalReason2602Tests(unittest.TestCase):
    def test_exception_without_a_code_keeps_its_type_name(self):
        code = task_advance._refusal(RuntimeError('message text must not leak'),
                                     'completion_code')
        self.assertEqual(code, 'RUNTIMEERROR')
        self.assertEqual(re.fullmatch(r'[A-Z0-9_]{1,64}', code).group(0), code)

    def test_a_valid_coded_refusal_is_unchanged(self):
        error = RuntimeError('ignored')
        error.review_repair_code = 'REPAIR_LIMIT_REACHED'
        self.assertEqual(task_advance._refusal(error, 'review_repair_code'),
                         'REPAIR_LIMIT_REACHED')

    def test_an_invalid_code_falls_back_to_the_type_name(self):
        for bad in (None, '', 'lowercase', 'BAD CODE', 7, 'A' * 65, ['X']):
            with self.subTest(bad=bad):
                error = RuntimeError('ignored')
                error.completion_code = bad
                self.assertEqual(task_advance._refusal(error, 'completion_code'),
                                 'RUNTIMEERROR')

    def test_a_long_type_name_is_bounded_and_code_shaped(self):
        error = type('X' * 80, (Exception,), {})('boom')
        code = task_advance._refusal(error, 'completion_code')
        self.assertEqual(code, 'X' * 64)
        self.assertIsNotNone(re.fullmatch(r'[A-Z0-9_]{1,64}', code))

    def test_the_reason_never_contains_message_or_path_text(self):
        message = 'synthetic message text from synthetic/user/content.txt'
        code = task_advance._refusal(RuntimeError(message), 'completion_code')
        self.assertEqual(code, 'RUNTIMEERROR')
        for fragment in ('SYNTHETIC', 'MESSAGE', 'USER', 'CONTENT', 'TXT'):
            self.assertNotIn(fragment, code)
        # Only a code-shaped value or the exception type name is kept.
        self.assertIn(code, ('RUNTIMEERROR', 'TASK_ADVANCE_REFUSED'))


class PublishOutOfScopeReason2602Tests(unittest.TestCase):
    _FIXTURE = test_task_advance.TaskAdvancePublishTests
    TASK_ID = _FIXTURE.TASK_ID
    EXECUTOR_TASK = _FIXTURE.EXECUTOR_TASK
    BRANCH = _FIXTURE.BRANCH
    WRITE_PATHS = _FIXTURE.WRITE_PATHS
    setUp = _FIXTURE.setUp
    git = staticmethod(_FIXTURE.git)
    git_runner = staticmethod(_FIXTURE.git_runner)
    record_result = _FIXTURE.record_result
    gh = _FIXTURE.gh
    head = _FIXTURE.head
    publish = _FIXTURE.publish

    def write(self, name, text='outside\n'):
        with open(os.path.join(self.root, name), 'w', encoding='utf-8') as stream:
            stream.write(text)

    def test_out_of_scope_paths_are_listed_bounded_with_total_count(self):
        names = [f'outside_{index:02d}.txt' for index in range(25)]
        for name in names:
            self.write(name)

        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()

        refusal = caught.exception
        self.assertEqual(refusal.code, 'PUBLISH_OUT_OF_SCOPE')
        self.assertEqual(refusal.details['out_of_scope_count'], 25)
        self.assertEqual(refusal.details['out_of_scope_paths'],
                         sorted(names)[:task_advance._OUT_OF_SCOPE_PATHS_MAX])
        self.assertEqual(len(refusal.details['out_of_scope_paths']), 20)
        self.assertIn('PUBLISH_OUT_OF_SCOPE', refusal.message)
        self.assertIn('Next step:', refusal.message)
        self.assertEqual(self.head(), self.initial_head)

        result = task_advance._publish_refusal_result(
            type('Store', (), {})(), self.TASK_ID, refusal)
        self.assertEqual(result['codes'], ['PUBLISH_OUT_OF_SCOPE'])
        self.assertEqual(result['out_of_scope_count'], 25)
        self.assertEqual(result['out_of_scope_paths'],
                         refusal.details['out_of_scope_paths'])


class PublishNoNewCommit2602Tests(unittest.TestCase):
    _FIXTURE = test_task_advance.TaskAdvancePublishTests
    TASK_ID = _FIXTURE.TASK_ID
    EXECUTOR_TASK = _FIXTURE.EXECUTOR_TASK
    BRANCH = _FIXTURE.BRANCH
    WRITE_PATHS = _FIXTURE.WRITE_PATHS
    setUp = _FIXTURE.setUp
    git = staticmethod(_FIXTURE.git)
    git_runner = staticmethod(_FIXTURE.git_runner)
    record_result = _FIXTURE.record_result
    gh = _FIXTURE.gh
    head = _FIXTURE.head
    pushed_head = _FIXTURE.pushed_head
    gated_store = _FIXTURE.gated_store
    advance_gated = _FIXTURE.advance_gated

    def test_branch_equal_to_main_stops_before_ec2_with_next_step(self):
        # setUp publishes main and the branch at the same initial commit.
        store = self.gated_store()
        tested = []

        def remote_test(*_args, **_kwargs):
            tested.append(1)
            self.fail('EC2 must not start for a result with no new commit')

        result = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual(result['codes'], ['PUBLISH_NO_NEW_COMMIT'])
        self.assertIn('Next step:', result['message'])
        self.assertIn('task advance', result['message'])
        self.assertEqual(tested, [])
        self.assertEqual(store.published, [])
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.head(), self.initial_head)
        self.assertFalse(any(args[:2] == ['pr', 'create'] for args in self.gh_calls))


if __name__ == '__main__':
    unittest.main()
