"""Tests for the three review findings of ``llmtool.project_context``.

T1: an ``allowed_origins`` list with an unhashable member (e.g. a list) is
    refused with ``ProjectContextError`` carrying the fixed code, both through
    ``ProjectContext.from_trust`` and the public constructor.
T2: a project id with non-printable characters or more than 100 characters is
    refused the same way.
T3: a constructed context cannot be changed through ``__dict__`` or attribute
    assignment or deletion.
"""
import unittest

from llmtool.project_context import (
    PROJECT_CONTEXT_INVALID,
    ProjectContext,
    ProjectContextError,
)


def make_mapping(slug='owner/repo', origins=('git@github.com:owner/repo.git',),
                 extra_repo_key=None):
    mapping = {
        'canonical_root': '/srv/projects/repo',
        'workspace_root': '/srv/workspaces/repo',
        'tasks_root': '/srv/tasks/repo',
        'repository': {'slug': slug, 'allowed_origins': list(origins)},
    }
    if extra_repo_key is not None:
        mapping['repository'][extra_repo_key[0]] = extra_repo_key[1]
    return mapping


class FindingTests(unittest.TestCase):
    def assert_refused(self, fn):
        with self.assertRaises(ProjectContextError) as caught:
            fn()
        self.assertEqual(caught.exception.code, PROJECT_CONTEXT_INVALID)
        self.assertEqual(str(caught.exception), PROJECT_CONTEXT_INVALID)

    # T1: unhashable allowed_origins member is refused (from_trust and __init__).

    def test_unhashable_origin_member_is_refused_via_from_trust(self):
        mapping = make_mapping()
        mapping['repository']['allowed_origins'] = ['git@github.com:owner/repo.git',
                                                    ['git@github.com:owner/repo.git']]
        self.assert_refused(lambda: ProjectContext.from_trust('proj-1', mapping))

    def test_unhashable_origin_member_is_refused_via_constructor(self):
        origins = ['git@github.com:owner/repo.git', ['git@github.com:owner/repo.git']]
        self.assert_refused(lambda: ProjectContext('proj-1', 'owner/repo', origins,
                                                   '/srv/projects/repo',
                                                   '/srv/workspaces/repo',
                                                   '/srv/tasks/repo'))

    # T2: invalid project id (non-printable or over 100 chars) is refused.

    def test_nonprintable_project_id_is_refused(self):
        for project_id in ('proj\x00', 'proj\n', 'proj\t'):
            with self.subTest(project_id=project_id):
                self.assert_refused(lambda: ProjectContext.from_trust(project_id,
                                                                      make_mapping()))
                self.assert_refused(lambda: ProjectContext(project_id, 'owner/repo',
                                                           ['git@github.com:owner/repo.git'],
                                                           '/srv/projects/repo',
                                                           '/srv/workspaces/repo',
                                                           '/srv/tasks/repo'))

    def test_overlong_project_id_is_refused(self):
        for project_id in ('p' * 101, 'p' * 1024):
            with self.subTest(length=len(project_id)):
                self.assert_refused(lambda: ProjectContext.from_trust(project_id,
                                                                      make_mapping()))
                self.assert_refused(lambda: ProjectContext(project_id, 'owner/repo',
                                                           ['git@github.com:owner/repo.git'],
                                                           '/srv/projects/repo',
                                                           '/srv/workspaces/repo',
                                                           '/srv/tasks/repo'))

    def test_boundary_project_ids(self):
        ok_100 = 'p' * 100
        context = ProjectContext.from_trust(ok_100, make_mapping())
        self.assertEqual(context.project_id, ok_100)
        self.assert_refused(lambda: ProjectContext('p' * 101, 'owner/repo',
                                                   ['git@github.com:owner/repo.git'],
                                                   '/srv/projects/repo',
                                                   '/srv/workspaces/repo',
                                                   '/srv/tasks/repo'))

    # T3: a constructed context cannot be mutated.

    def test_context_cannot_be_mutated(self):
        context = ProjectContext.from_trust('proj-1', make_mapping())
        with self.assertRaises(AttributeError):
            context.__dict__
        with self.assertRaises(AttributeError) as caught:
            context.__setattr__('slug', 'other/repo')
        self.assertEqual(caught.exception.args[0], PROJECT_CONTEXT_INVALID)
        with self.assertRaises(AttributeError) as caught:
            context.slug = 'other/repo'
        self.assertEqual(caught.exception.args[0], PROJECT_CONTEXT_INVALID)
        with self.assertRaises(AttributeError) as caught:
            context.__delattr__('slug')
        self.assertEqual(caught.exception.args[0], PROJECT_CONTEXT_INVALID)
        with self.assertRaises(AttributeError) as caught:
            del context.slug
        self.assertEqual(caught.exception.args[0], PROJECT_CONTEXT_INVALID)
        # Nothing changed: every field still reads back with its original value.
        self.assertEqual(context.project_id, 'proj-1')
        self.assertEqual(context.slug, 'owner/repo')
        self.assertEqual(context.allowed_origins, ('git@github.com:owner/repo.git',))
        self.assertEqual(context.canonical_root, '/srv/projects/repo')
        self.assertEqual(context.workspace_root, '/srv/workspaces/repo')
        self.assertEqual(context.tasks_root, '/srv/tasks/repo')


if __name__ == '__main__':
    unittest.main()
