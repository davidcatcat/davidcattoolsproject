"""#2793 package H4: project, CI and test-evidence profiles agree.

Every value is built from one trust-shaped fixture. The checks tie the
fixture's slug to the allowed origins, keep every test-evidence pattern
inside the canonical root, and keep the default CI names distinct and plain.
This module performs no file, network or process access.
"""

import os
import unittest

from llmtool.ci_profile import default_profile as default_ci_profile
from llmtool.project_context import ProjectContext
from llmtool.test_profile import default_profile as default_test_profile


ROOT = "/srv/canonical"
WORKSPACE = "/srv/workspaces"
TASKS = "/srv/tasks"
SLUG = "owner/repo"
SSH_ORIGIN = "git@github.com:owner/repo.git"
HTTPS_ORIGIN = "https://github.com/owner/repo.git"

#: Representative relative paths, one per current runner kind.
EVIDENCE_SAMPLES = (
    "tools/llmtool/tests/test_a13_contracts.py",
    "scripts/test_a13_contracts.py",
)


def _trust():
    return {
        "canonical_root": ROOT,
        "workspace_root": WORKSPACE,
        "tasks_root": TASKS,
        "repository": {
            "slug": SLUG,
            "allowed_origins": [SSH_ORIGIN, HTTPS_ORIGIN],
        },
    }


class A13ContractTests(unittest.TestCase):
    def setUp(self):
        self.context = ProjectContext.from_trust("a13-contracts", _trust())
        self.ci = default_ci_profile()
        self.evidence = default_test_profile()

    def test_allowed_origins_match_the_slug(self):
        expected = {
            "git@github.com:%s.git" % self.context.slug,
            "https://github.com/%s.git" % self.context.slug,
        }
        self.assertEqual(self.context.slug, SLUG)
        self.assertEqual(set(self.context.allowed_origins), expected)

    def test_evidence_patterns_stay_inside_canonical_root(self):
        self.assertTrue(self.evidence.entries)
        for pattern, kind in self.evidence.entries:
            with self.subTest(pattern=pattern.pattern):
                # The pattern source itself must describe a relative path.
                source = pattern.pattern
                self.assertFalse(os.path.isabs(source))
                joined = os.path.normpath(
                    os.path.join(self.context.canonical_root, source))
                self.assertEqual(
                    os.path.commonpath((joined, self.context.canonical_root)),
                    self.context.canonical_root)
                self.assertNotEqual(joined, self.context.canonical_root)

                # And a concrete path it matches must resolve inside the root.
                matches = [path for path in EVIDENCE_SAMPLES
                           if pattern.fullmatch(path) is not None]
                self.assertTrue(matches)
                relative = matches[0]
                self.assertEqual(self.evidence.kind_for(relative), kind)
                resolved = os.path.normpath(
                    os.path.join(self.context.canonical_root, relative))
                self.assertEqual(
                    os.path.commonpath((resolved, self.context.canonical_root)),
                    self.context.canonical_root)
                self.assertNotEqual(resolved, self.context.canonical_root)

    def test_ci_names_are_distinct_and_workflow_names_are_plain(self):
        checks = (
            self.ci.linux_code_check,
            self.ci.docs_check,
            self.ci.gateway_check,
            self.ci.ai_loop_check,
            self.ci.macos_check,
        )
        workflows = (
            self.ci.linux_workflow_file,
            self.ci.macos_workflow_file,
        )
        self.assertEqual(len(set(checks)), len(checks))
        self.assertEqual(len(set(workflows)), len(workflows))
        self.assertFalse(set(checks) & set(workflows))
        for name in workflows:
            with self.subTest(workflow=name):
                self.assertEqual(os.path.basename(name), name)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)


if __name__ == "__main__":
    unittest.main()
