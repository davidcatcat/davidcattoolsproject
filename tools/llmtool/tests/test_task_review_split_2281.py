"""#2281: the bounded task_review split keeps each module and its public names."""
import unittest
from pathlib import Path

from llmtool import task_review


LIMIT_BYTES = 24576
PRODUCTION_MODULES = (
    'task_review.py',
    'task_review_part1.py',
    'task_review_part2.py',
)
PUBLIC_NAMES = (
    'PACKET_LIMIT', 'SOURCE_LIMIT', 'EVIDENCE_LIMIT', 'VERDICT_LIMIT',
    'REVIEWER_ROLES', 'HOST_INSTRUCTIONS',
    'reject', 'canonical', 'digest', 'load_json', 'screen', 'safe_id',
    'scope_path', 'ReviewCallBinding', 'Admission', 'ConsumeToken',
    'CallOutcome', 'LaunchResult', 'ReviewAuthority', 'ReviewerLauncher',
    'AuthorshipObserver', 'Clock', 'HostServices', 'UnavailableReviewAuthority',
    'review_authority_factory', 'clock_utc', 'PairApproval', 'ReviewPacket',
    'make_packet', 'parse_verdict', 'envelope', 'run_review',
)


class TaskReviewSplitTests(unittest.TestCase):
    def test_each_production_module_is_within_the_bounded_read_limit(self):
        package = Path(task_review.__file__).resolve().parent
        for name in PRODUCTION_MODULES:
            with self.subTest(module=name):
                size = (package / name).stat().st_size
                self.assertLessEqual(size, LIMIT_BYTES, f'{name} is {size} bytes')

    def test_public_names_still_resolve_from_task_review(self):
        for name in PUBLIC_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(task_review, name), name)

    def test_run_review_resolves_patchable_names_from_task_review(self):
        # patch.object(task_review, name) must reach the globals run_review uses.
        for name in ('parse_verdict', 'make_packet', 'envelope', 'ReviewCallBinding',
                     'HostServices', 'PairApproval'):
            with self.subTest(name=name):
                self.assertIs(task_review.run_review.__globals__[name],
                              getattr(task_review, name))


if __name__ == '__main__':
    unittest.main()
