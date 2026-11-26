"""Issue #2793 package H1: ProjectContext valid mapping and R2-R4 refusals.

Every refusal case checks the fixed upper-case code and the absence of any
input value in the exception text. No credential-shaped strings appear.
"""
import unittest

from llmtool.project_context import (
    PROJECT_CONTEXT_INVALID,
    ProjectContext,
    ProjectContextError,
)


def valid_mapping():
    return {
        'canonical_root': '/srv/projects/repo',
        'workspace_root': '/srv/projects/work',
        'tasks_root': '/srv/projects/tasks',
        'repository': {
            'slug': 'owner/repo',
            'allowed_origins': [
                'git@github.com:owner/repo.git',
                'https://github.com/owner/repo.git',
            ],
        },
    }


class ValidMappingTests(unittest.TestCase):
    def test_valid_mapping_builds_context(self):
        ctx = ProjectContext.from_trust('proj-1', valid_mapping())
        self.assertEqual(ctx.project_id, 'proj-1')
        self.assertEqual(ctx.slug, 'owner/repo')
        self.assertEqual(ctx.allowed_origins,
                         ('git@github.com:owner/repo.git',
                          'https://github.com/owner/repo.git'))
        self.assertEqual(ctx.canonical_root, '/srv/projects/repo')
        self.assertEqual(ctx.workspace_root, '/srv/projects/work')
        self.assertEqual(ctx.tasks_root, '/srv/projects/tasks')

    def test_valid_mapping_with_approval_refs(self):
        mapping = valid_mapping()
        mapping['repository']['approval_refs'] = ['ref-1', 'ref-2']
        ctx = ProjectContext.from_trust('proj-1', mapping)
        self.assertEqual(ctx.slug, 'owner/repo')

    def test_context_is_immutable(self):
        ctx = ProjectContext.from_trust('proj-1', valid_mapping())
        for mutation in (
                lambda: setattr(ctx, 'project_id', 'other'),
                lambda: setattr(ctx, 'allowed_origins', ['x']),
                lambda: delattr(ctx, 'slug')):
            with self.assertRaises(AttributeError) as caught:
                mutation()
            self.assertIn(PROJECT_CONTEXT_INVALID, str(caught.exception))
        with self.assertRaises(AttributeError):
            ProjectContext.other_attr
        self.assertIsInstance(ctx.allowed_origins, tuple)
        self.assertEqual(ctx, ProjectContext.from_trust('proj-1', valid_mapping()))
        self.assertNotEqual(ctx, 'proj-1')
        self.assertEqual(hash(ctx), hash((ctx.project_id, ctx.slug,
                                          ctx.allowed_origins,
                                          ctx.canonical_root,
                                          ctx.workspace_root,
                                          ctx.tasks_root)))


class RefusalTests(unittest.TestCase):
    """Each R2-R4 invalid input is refused with the fixed code and no value."""

    def _refuses(self, project_id, mapping):
        with self.assertRaises(ProjectContextError) as caught:
            ProjectContext.from_trust(project_id, mapping)
        exc = caught.exception
        self.assertEqual(exc.code, PROJECT_CONTEXT_INVALID)
        self.assertEqual(str(exc), PROJECT_CONTEXT_INVALID)
        self.assertNotIn('owner', str(exc))
        self.assertNotIn('proj', str(exc))
        self.assertNotIn('/srv', str(exc))

    def test_project_id_refusals(self):
        self._refuses('', valid_mapping())
        self._refuses('a' * 101, valid_mapping())
        self._refuses(42, valid_mapping())
        self._refuses('proj\n1', valid_mapping())
        self._refuses('proj\xc2\xa9', valid_mapping())
        self._refuses(None, valid_mapping())

    def test_mapping_shape_refusals(self):
        self._refuses('proj-1', ['canonical_root'])
        self._refuses('proj-1', 'canonical_root')
        self._refuses('proj-1', None)
        self._refuses('proj-1', {})
        self._refuses('proj-1', {'repository': valid_mapping()['repository']})
        mapping = valid_mapping()
        mapping['extra'] = '/srv/extra'
        self._refuses('proj-1', mapping)
        mapping = valid_mapping()
        del mapping['tasks_root']
        self._refuses('proj-1', mapping)

    def test_repository_refusals(self):
        for label, repository in (
                ('repository not a dict', 'owner/repo'),
                ('missing allowed_origins', {'slug': 'owner/repo'}),
                ('unexpected repository key', {
                    'slug': 'owner/repo',
                    'allowed_origins': ['git@github.com:owner/repo.git'],
                    'note': 'x'})):
            with self.subTest(label=label):
                mapping = valid_mapping()
                mapping['repository'] = repository
                self._refuses('proj-1', mapping)

    def test_slug_refusals(self):
        bad_slugs = (
            'owner',                                   # no slash
            'owner/repo/x',                             # extra slash in name
            'owner/repo/',                              # trailing slash
            '/owner/repo',                              # leading slash
            '-owner/repo',                              # bad first char
            'ownerx' * 7 + 'y/repo',                    # first part over 39 chars
            'owner/' + 'r' * 101,                       # name over 100
            'owner/.',                                  # single dot
            'owner/..',                                 # two dots
            'owner/',                                   # empty name
            42,                                         # wrong type
            None,
        )
        for slug in bad_slugs:
            with self.subTest(slug=slug):
                mapping = valid_mapping()
                mapping['repository']['slug'] = slug
                self._refuses('proj-1', mapping)

    def test_origin_refusals(self):
        good = ['git@github.com:owner/repo.git',
                'https://github.com/owner/repo.git']
        bad_origins = (
            [],
            good + [good[0]],                           # three entries
            [good[1], good[1]],                         # not distinct
            ['git@github.com:owner/other.git'],         # wrong slug
            ['git@github.com:owner/repo'],              # missing .git
            ['git@github.com:owner/repo.git/x'],        # extra path
            ['http://github.com/owner/repo.git'],       # wrong scheme
            (good,),                                    # wrong type
            ['git@github.com:owner/repo.git', 42],      # non-string member
        )
        for origins in bad_origins:
            with self.subTest(origins=origins):
                mapping = valid_mapping()
                mapping['repository']['allowed_origins'] = origins
                self._refuses('proj-1', mapping)

    def test_approval_refs_refusals(self):
        for refs in ([], None, 'ref', ['ref', 42]):
            with self.subTest(refs=refs):
                mapping = valid_mapping()
                mapping['repository']['approval_refs'] = refs
                self._refuses('proj-1', mapping)

    def test_root_refusals(self):
        bad_roots = (
            '',
            'relative/path',
            'relative/',
            '/srv//proj',
            '/srv/../proj',
            '/srv/proj/./',
            'a' * 1025,
            '/srv/\tproj',
            '/srv/\u00e9proj',
            42,
            None,
        )
        for key in ('canonical_root', 'workspace_root', 'tasks_root'):
            for root in bad_roots:
                with self.subTest(key=key, root=root):
                    mapping = valid_mapping()
                    mapping[key] = root
                    self._refuses('proj-1', mapping)

    def test_root_disjointness_refusals(self):
        cases = (
            ('canonical_root', '/srv/projects/repo'),
            ('workspace_root', '/srv/projects/repo'),
            ('tasks_root', '/srv/projects/repo'),
            ('workspace_root', '/srv/projects/repo/inner'),
            ('tasks_root', '/srv'),
        )
        for key, root in cases:
            with self.subTest(key=key):
                mapping = valid_mapping()
                mapping[key] = root
                self._refuses('proj-1', mapping)


if __name__ == '__main__':
    unittest.main()
