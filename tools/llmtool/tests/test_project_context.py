"""Tests for the immutable, trust-shaped project context."""

import unittest

from llmtool.project_context import (
    PROJECT_CONTEXT_INVALID,
    ProjectContext,
    ProjectContextError,
    from_trust,
)


ROOT = "/srv/canonical"
WORKSPACE = "/srv/workspaces"
TASKS = "/srv/tasks"
SLUG = "owner/repo"
SSH_ORIGIN = "git@github.com:owner/repo.git"
HTTPS_ORIGIN = "https://github.com/owner/repo.git"


def _trust():
    return {
        "canonical_root": ROOT,
        "workspace_root": WORKSPACE,
        "tasks_root": TASKS,
        "repository": {
            "slug": SLUG,
            "allowed_origins": [SSH_ORIGIN],
        },
    }


def _approval_refs():
    return {
        "reviewer_host_policy_issue": 12,
        "worker_dispatch_issue": 34,
        "worker_dispatch_evidence_pr": 56,
        "retain_release_issues": [7, 8],
    }


def _context(**overrides):
    values = {
        "project_id": "project-1",
        "slug": SLUG,
        "allowed_origins": [SSH_ORIGIN],
        "canonical_root": ROOT,
        "workspace_root": WORKSPACE,
        "tasks_root": TASKS,
    }
    values.update(overrides)
    return ProjectContext(**values)


class ProjectContextTests(unittest.TestCase):
    def assertCode(self, call):
        with self.assertRaises(ProjectContextError) as caught:
            call()
        self.assertEqual(caught.exception.code, PROJECT_CONTEXT_INVALID)
        self.assertEqual(str(caught.exception), PROJECT_CONTEXT_INVALID)
        return caught.exception

    def test_code_is_fixed_and_upper_case(self):
        self.assertTrue(PROJECT_CONTEXT_INVALID.isupper())

    def test_public_constructor_builds_valid_context(self):
        context = _context(allowed_origins=[SSH_ORIGIN, HTTPS_ORIGIN])
        self.assertEqual(context.project_id, "project-1")
        self.assertEqual(context.slug, SLUG)
        self.assertEqual(context.allowed_origins, (SSH_ORIGIN, HTTPS_ORIGIN))
        self.assertIsInstance(context.allowed_origins, tuple)
        self.assertEqual(context.canonical_root, ROOT)
        self.assertEqual(context.workspace_root, WORKSPACE)
        self.assertEqual(context.tasks_root, TASKS)

    def test_public_constructor_refuses_malformed_arguments(self):
        self.assertCode(lambda: _context(extra="value"))
        self.assertCode(lambda: ProjectContext(project_id="project-1"))
        self.assertCode(
            lambda: ProjectContext("project-1", SLUG, [SSH_ORIGIN], ROOT, WORKSPACE,
                                   TASKS, "extra"))
        self.assertCode(
            lambda: ProjectContext("project-1", SLUG, [SSH_ORIGIN], ROOT, WORKSPACE,
                                   TASKS, project_id="other-project"))

    def test_from_trust_without_approval_refs(self):
        context = ProjectContext.from_trust("project-1", _trust())
        self.assertEqual(context, _context())
        self.assertEqual(context.allowed_origins, (SSH_ORIGIN,))

    def test_from_trust_with_approval_refs(self):
        trust = _trust()
        trust["repository"]["approval_refs"] = _approval_refs()
        context = ProjectContext.from_trust("project-1", trust)
        self.assertEqual(context, _context())

    def test_from_trust_accepts_both_origins_and_eight_retained_issues(self):
        trust = _trust()
        trust["repository"]["allowed_origins"] = [SSH_ORIGIN, HTTPS_ORIGIN]
        approval = _approval_refs()
        approval["retain_release_issues"] = list(range(1, 9))
        trust["repository"]["approval_refs"] = approval
        context = ProjectContext.from_trust("project-1", trust)
        self.assertEqual(context.allowed_origins, (SSH_ORIGIN, HTTPS_ORIGIN))

    def test_module_level_from_trust_matches_classmethod(self):
        self.assertEqual(from_trust("project-1", _trust()),
                         ProjectContext.from_trust("project-1", _trust()))

    def test_context_cannot_change_after_construction(self):
        context = ProjectContext.from_trust("project-1", _trust())
        with self.assertRaises(AttributeError):
            context.project_id = "other-project"
        with self.assertRaises(AttributeError):
            context.slug = "other/repo"
        with self.assertRaises(AttributeError):
            context.allowed_origins = (HTTPS_ORIGIN,)
        with self.assertRaises(AttributeError):
            del context.canonical_root
        with self.assertRaises(AttributeError):
            context.extra = "value"
        # Defining __slots__ for the fields removes the instance __dict__.
        with self.assertRaises(AttributeError):
            context.__dict__

    def test_refuses_invalid_project_id_on_both_paths(self):
        bad_values = (None, 7, "", "x" * 101, "caf\u00e9", "line\nbreak")
        for value in bad_values:
            with self.subTest(value=repr(value)):
                self.assertCode(lambda value=value: _context(project_id=value))
                self.assertCode(
                    lambda value=value: ProjectContext.from_trust(value, _trust()))

    def test_refuses_invalid_slug_on_both_paths(self):
        bad_values = (
            None,
            5,
            "",
            "owner",
            "/repo",
            "owner/repo/extra",
            "-owner/repo",
            "owner_repo/repo",
            "owner/.",
            "owner/..",
            "owner/" + "a" * 101,
        )
        for value in bad_values:
            with self.subTest(value=repr(value)):
                self.assertCode(lambda value=value: _context(slug=value))
                trust = _trust()
                trust["repository"]["slug"] = value
                self.assertCode(
                    lambda trust=trust: ProjectContext.from_trust("project-1", trust))

    def test_accepts_boundary_slug_lengths(self):
        slug = "a" * 39 + "/" + "b" * 100
        context = _context(slug=slug, allowed_origins=[f"git@github.com:{slug}.git"])
        self.assertEqual(context.slug, slug)

    def test_refuses_invalid_origins_on_both_paths(self):
        cases = (
            ("not a list", (SSH_ORIGIN,)),
            ("empty", []),
            ("too many", [SSH_ORIGIN, HTTPS_ORIGIN, SSH_ORIGIN]),
            ("duplicate", [SSH_ORIGIN, SSH_ORIGIN]),
            ("unknown slug", ["git@github.com:other/repo.git"]),
            ("wrong scheme", ["ssh://github.com/owner/repo.git"]),
            ("wrong type", [7]),
            ("unhashable", [{"origin": SSH_ORIGIN}]),
        )
        for label, origins in cases:
            with self.subTest(label=label):
                self.assertCode(
                    lambda origins=origins: _context(allowed_origins=origins))
                trust = _trust()
                trust["repository"]["allowed_origins"] = origins
                self.assertCode(
                    lambda trust=trust: ProjectContext.from_trust("project-1", trust))

    def test_refuses_invalid_roots_on_both_paths(self):
        cases = (
            ("not a string", 7, WORKSPACE, TASKS),
            ("empty", "", WORKSPACE, TASKS),
            ("too long", "/" + "a" * 1024, WORKSPACE, TASKS),
            ("control", "/srv/\x01canonical", WORKSPACE, TASKS),
            ("non ascii", "/srv/caf\u00e9", WORKSPACE, TASKS),
            ("relative", "srv/canonical", WORKSPACE, TASKS),
            ("unnormalized", ROOT + "/", WORKSPACE, TASKS),
            ("dot segment", "/srv/./canonical", WORKSPACE, TASKS),
            ("equal", ROOT, ROOT, TASKS),
            ("contains", "/srv", WORKSPACE, TASKS),
            ("inside", ROOT, ROOT + "/nested", TASKS),
        )
        for label, canonical, workspace, tasks in cases:
            with self.subTest(label=label):
                self.assertCode(lambda c=canonical, w=workspace, t=tasks: _context(
                    canonical_root=c, workspace_root=w, tasks_root=t))
                trust = _trust()
                trust.update(canonical_root=canonical, workspace_root=workspace,
                             tasks_root=tasks)
                self.assertCode(
                    lambda trust=trust: ProjectContext.from_trust("project-1", trust))

    def test_from_trust_refuses_invalid_mapping_shape(self):
        cases = (
            ("not a mapping", None),
            ("list", []),
            ("missing key", {key: value for key, value in _trust().items()
                             if key != "tasks_root"}),
            ("unexpected key", dict(_trust(), extra="value")),
        )
        for label, value in cases:
            with self.subTest(label=label):
                self.assertCode(
                    lambda value=value: ProjectContext.from_trust("project-1", value))

    def test_from_trust_refuses_invalid_repository_shape(self):
        good = _trust()["repository"]
        cases = (
            ("not a mapping", None),
            ("list", []),
            ("missing slug", {"allowed_origins": [SSH_ORIGIN]}),
            ("missing origins", {"slug": SLUG}),
            ("unexpected key", dict(good, extra="value")),
        )
        for label, repository in cases:
            with self.subTest(label=label):
                trust = _trust()
                trust["repository"] = repository
                self.assertCode(
                    lambda trust=trust: ProjectContext.from_trust("project-1", trust))

    def test_from_trust_refuses_invalid_approval_refs(self):
        cases = (
            ("not a mapping", []),
            ("missing key", {key: value for key, value in _approval_refs().items()
                             if key != "worker_dispatch_issue"}),
            ("unexpected key", dict(_approval_refs(), extra=1)),
            ("boolean integer", dict(_approval_refs(), worker_dispatch_issue=True)),
            ("zero integer", dict(_approval_refs(), reviewer_host_policy_issue=0)),
            ("too large integer",
             dict(_approval_refs(), worker_dispatch_evidence_pr=10 ** 9 + 1)),
            ("float integer", dict(_approval_refs(), worker_dispatch_issue=1.0)),
            ("string integer", dict(_approval_refs(), worker_dispatch_issue="1")),
            ("retain not list", dict(_approval_refs(), retain_release_issues=(7, 8))),
            ("retain empty", dict(_approval_refs(), retain_release_issues=[])),
            ("retain too many",
             dict(_approval_refs(), retain_release_issues=list(range(1, 10)))),
            ("retain duplicate",
             dict(_approval_refs(), retain_release_issues=[7, 7])),
            ("retain boolean",
             dict(_approval_refs(), retain_release_issues=[True])),
            ("retain zero", dict(_approval_refs(), retain_release_issues=[0])),
            ("retain too large",
             dict(_approval_refs(), retain_release_issues=[10 ** 9 + 1])),
            ("retain wrong type",
             dict(_approval_refs(), retain_release_issues=["1"])),
        )
        for label, approval in cases:
            with self.subTest(label=label):
                trust = _trust()
                trust["repository"]["approval_refs"] = approval
                self.assertCode(
                    lambda trust=trust: ProjectContext.from_trust("project-1", trust))


if __name__ == "__main__":
    unittest.main()
