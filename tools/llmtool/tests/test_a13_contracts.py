"""#2793 package H4: project, CI and test-evidence profiles agree.

Every value is built from one trust-shaped fixture. The checks tie the
fixture's slug to the allowed origins, keep every test-evidence pattern
inside the canonical root, and keep the default CI names distinct and plain.
The same checks are also applied to substituted CI and test profiles, and a
test-evidence pattern outside the canonical root is shown to fail the root
check (tracking #2793).  This module performs no file, network or process
access.
"""

import os
import re
import unittest

from llmtool.ci_profile import CiProfile
from llmtool.ci_profile import default_profile as default_ci_profile
from llmtool.project_context import ProjectContext
from llmtool.test_profile import TestProfile
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

#: Representative relative paths for the substituted test directories.
SUBSTITUTED_EVIDENCE_SAMPLES = (
    "src/llmtool/tests/test_a13_contracts.py",
    "bin/test_a13_contracts.py",
)

#: Representative relative paths that leave the canonical root.
OUTSIDE_ROOT_SAMPLES = (
    "../workspaces/runner/test_a13_contracts.py",
    "/srv/elsewhere/test_a13_contracts.py",
)


def _check_evidence_patterns_stay_inside(
        context, evidence, samples):
    """Assert that every evidence pattern stays inside the canonical root.

    This is the root-check sequence the existing test applies to the default
    profile; the substituted-profile tests apply it unchanged.
    """
    assert evidence.entries
    for pattern, kind in evidence.entries:
        # The pattern source itself must describe a relative path.
        source = pattern.pattern
        assert not os.path.isabs(source)
        joined = os.path.normpath(
            os.path.join(context.canonical_root, source))
        assert os.path.commonpath(
            (joined, context.canonical_root)) == context.canonical_root
        assert joined != context.canonical_root

        # And a concrete path it matches must resolve inside the root.
        matches = [path for path in samples
                   if pattern.fullmatch(path) is not None]
        assert matches
        relative = matches[0]
        assert evidence.kind_for(relative) == kind
        resolved = os.path.normpath(
            os.path.join(context.canonical_root, relative))
        assert os.path.commonpath(
            (resolved, context.canonical_root)) == context.canonical_root
        assert resolved != context.canonical_root


def _check_ci_names_are_distinct_and_plain(ci):
    """Assert distinct CI names and plain workflow file names.

    This is the CI-name sequence the existing test applies to the default
    profile; the substituted-profile tests apply it unchanged.
    """
    checks = (
        ci.linux_code_check,
        ci.docs_check,
        ci.gateway_check,
        ci.ai_loop_check,
        ci.macos_check,
    )
    workflows = (
        ci.linux_workflow_file,
        ci.macos_workflow_file,
    )
    assert len(set(checks)) == len(checks)
    assert len(set(workflows)) == len(workflows)
    assert not set(checks) & set(workflows)
    for name in workflows:
        assert os.path.basename(name) == name
        assert "/" not in name
        assert "\\" not in name


def _substituted_test_profile():
    """Build a TestProfile for two substituted test directories.

    Both patterns stay under the same canonical root; the directories are
    different from the default registry's.
    """
    return TestProfile((
        (re.compile(r"src/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z"),
         "unittest"),
        (re.compile(r"bin/test_[A-Za-z0-9_]+\.py\Z"), "script"),
    ))


def _substituted_ci_profile():
    """Build a CiProfile with distinct substituted check names.

    The check names differ from the default profile's while the workflow
    file names stay plain, as the default profile's are.
    """
    return CiProfile(
        linux_code_check="code checks (Linux 3.14)",
        docs_check="documentation checks",
        gateway_check="gateway checks (Linux 3.14)",
        ai_loop_check="ai-loop helper checks (Python 3.14)",
        macos_check="code checks (macOS 3.13)",
        linux_workflow_file="substituted-linux-ci.yml",
        macos_workflow_file="substituted-mac-ci.yml",
    )


def _outside_root_test_profile():
    """Build a TestProfile whose patterns leave the canonical root."""
    return TestProfile((
        (re.compile(r"\.\./workspaces/runner/test_[A-Za-z0-9_]+\.py\Z"),
         "unittest"),
        (re.compile(r"\A/srv/elsewhere/test_[A-Za-z0-9_]+\.py\Z"),
         "script"),
    ))


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

    def test_substituted_test_profile_passes_the_contract_checks(self):
        evidence = _substituted_test_profile()
        # The substituted profile keeps the same canonical root and stays
        # inside it for every pattern, like the default profile.
        _check_evidence_patterns_stay_inside(
            self.context, evidence, SUBSTITUTED_EVIDENCE_SAMPLES)

    def test_substituted_ci_profile_passes_the_contract_checks(self):
        ci = _substituted_ci_profile()
        _check_ci_names_are_distinct_and_plain(ci)
        # The substituted check names differ from the default profile's.
        self.assertNotEqual(ci.linux_code_check, self.ci.linux_code_check)
        self.assertNotEqual(ci.docs_check, self.ci.docs_check)
        self.assertNotEqual(ci.gateway_check, self.ci.gateway_check)
        self.assertNotEqual(ci.ai_loop_check, self.ci.ai_loop_check)
        self.assertNotEqual(ci.macos_check, self.ci.macos_check)

    def test_evidence_patterns_outside_canonical_root_fail(self):
        evidence = _outside_root_test_profile()
        for pattern, kind in evidence.entries:
            with self.subTest(pattern=pattern.pattern):
                with self.assertRaises(AssertionError):
                    _check_evidence_patterns_stay_inside(
                        self.context, evidence, OUTSIDE_ROOT_SAMPLES)


if __name__ == "__main__":
    unittest.main()
