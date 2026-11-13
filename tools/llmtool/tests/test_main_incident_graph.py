"""#1706 R2: static import closure from supplied file texts (no Git, no checkout, no network)."""

from __future__ import annotations

import copy
import unittest

from llmtool import main_incident_graph as graph


P = 'tools/llmtool/'
TEST_ID = 'tests.test_alpha.AlphaCase.test_one'


def tree(**changes):
    """A small tools/llmtool map; keyword names replace or add files (path keys relative to P)."""
    files = {
        'llmtool/__init__.py': '',
        'llmtool/alpha.py': 'from . import beta\nfrom .gamma import helper\n',
        'llmtool/beta.py': 'import json\n',
        'llmtool/gamma.py': 'def helper():\n    return 1\n',
        'llmtool/delta.py': 'import llmtool.epsilon\n',
        'llmtool/epsilon.py': 'VALUE = 1\n',
        'llmtool/unused.py': 'VALUE = 2\n',
        'tests/__init__.py': '',
        'tests/test_alpha.py': 'import unittest\nfrom llmtool import alpha\n',
        'README.md': 'plain text, not Python',
    }
    files.update(changes)
    return {P + path: text for path, text in files.items() if text is not None}


def rel(*paths):
    return sorted(P + path for path in paths)


ALPHA_CLOSURE = rel('llmtool/__init__.py', 'llmtool/alpha.py', 'llmtool/beta.py',
                    'llmtool/gamma.py', 'tests/__init__.py', 'tests/test_alpha.py')


class ModuleNameTests(unittest.TestCase):
    def test_names_from_repository_or_tool_relative_paths(self):
        self.assertEqual(graph.module_name(P + 'llmtool/alpha.py'), 'llmtool.alpha')
        self.assertEqual(graph.module_name('llmtool/alpha.py'), 'llmtool.alpha')
        self.assertEqual(graph.module_name('./llmtool/alpha.py'), 'llmtool.alpha')
        self.assertEqual(graph.module_name(P + 'llmtool/__init__.py'), 'llmtool')
        self.assertTrue(graph.is_package(P + 'llmtool/__init__.py'))
        self.assertFalse(graph.is_package(P + 'llmtool/alpha.py'))

    def test_non_modules_have_no_name(self):
        for path in (P + 'README.md', P + 'llmtool/bad-name.py', '__init__.py', None, 3, ''):
            with self.subTest(path=path):
                self.assertIsNone(graph.module_name(path))


class ImportClosureTests(unittest.TestCase):
    def test_relative_and_absolute_imports_from_a_test_id(self):
        result = graph.import_closure(TEST_ID, tree())
        self.assertEqual(result['root'], P + 'tests/test_alpha.py')
        self.assertEqual(result['module'], 'tests.test_alpha')
        self.assertEqual(result['files'], ALPHA_CLOSURE)
        self.assertEqual((result['unparsable'], result['complete']), ([], True))

    def test_root_by_path_or_module_name(self):
        for name in (P + 'tests/test_alpha.py', 'tests.test_alpha'):
            with self.subTest(name=name):
                self.assertEqual(graph.import_closure(name, tree())['files'], ALPHA_CLOSURE)

    def test_absolute_dotted_import_loads_the_module_and_its_parents(self):
        files = tree(**{'tests/test_delta.py': 'import llmtool.delta as d\n'})
        result = graph.import_closure('tests.test_delta.Case.test_x', files)
        self.assertEqual(result['files'], rel('llmtool/__init__.py', 'llmtool/delta.py',
                                              'llmtool/epsilon.py', 'tests/__init__.py',
                                              'tests/test_delta.py'))

    def test_imports_inside_functions_and_try_blocks_count(self):
        files = tree(**{'tests/test_alpha.py': (
            'def load():\n    from llmtool import unused\n    return unused\n'
            'try:\n    from llmtool.delta import thing\nexcept ImportError:\n    thing = None\n')})
        result = graph.import_closure(TEST_ID, files)
        self.assertTrue({P + 'llmtool/unused.py', P + 'llmtool/delta.py',
                         P + 'llmtool/epsilon.py'} <= set(result['files']))

    def test_import_cycle_terminates(self):
        files = tree(**{'llmtool/beta.py': 'from . import alpha\n'})
        self.assertEqual(graph.import_closure(TEST_ID, files)['files'], ALPHA_CLOSURE)

    def test_relative_import_beyond_the_top_package_is_ignored(self):
        files = tree(**{'llmtool/beta.py': 'from ... import nothing\nfrom .. import other\n'})
        result = graph.import_closure(TEST_ID, files)
        self.assertEqual((result['files'], result['complete']), (ALPHA_CLOSURE, True))

    def test_unparsable_file_is_reported_not_fatal(self):
        files = tree(**{'llmtool/beta.py': 'def broken(:\n', 'llmtool/gamma.py': b'not text'})
        result = graph.import_closure(TEST_ID, files)
        self.assertEqual(result['files'], ALPHA_CLOSURE)
        self.assertEqual(result['unparsable'], rel('llmtool/beta.py', 'llmtool/gamma.py'))
        self.assertFalse(result['complete'])

    def test_unparsable_root_keeps_only_the_root_and_its_packages(self):
        files = tree(**{'tests/test_alpha.py': 'import (\n'})
        result = graph.import_closure(TEST_ID, files)
        self.assertEqual(result['files'], rel('tests/__init__.py', 'tests/test_alpha.py'))
        self.assertEqual(result['unparsable'], rel('tests/test_alpha.py'))

    def test_unknown_test_has_no_root(self):
        # The tests package prefix alone is never taken as the root of a missing test.
        for name in ('tests.test_missing.Case.test_x', None, '', 7):
            with self.subTest(name=name):
                result = graph.import_closure(name, tree())
                self.assertIsNone(result['root'])
                self.assertEqual((result['files'], result['complete']), ([], False))

    def test_non_dict_files_give_an_empty_closure(self):
        for files in (None, 'files', [P + 'tests/test_alpha.py']):
            with self.subTest(files=files):
                self.assertEqual(graph.import_closure(TEST_ID, files)['files'], [])

    def test_inputs_are_not_mutated(self):
        files = tree(**{'llmtool/beta.py': 'def broken(:\n'})
        before = copy.deepcopy(files)
        result = graph.import_closure(TEST_ID, files)
        result['files'].append('extra')
        self.assertEqual(files, before)


class NameTests(unittest.TestCase):
    def test_imports_resolve_relative_names_against_the_package(self):
        self.assertEqual(graph.imports(P + 'llmtool/alpha.py', tree()[P + 'llmtool/alpha.py']),
                         {'llmtool', 'llmtool.beta', 'llmtool.gamma', 'llmtool.gamma.helper'})
        self.assertEqual(graph.imports(P + 'llmtool/__init__.py', 'from .alpha import x\n'),
                         {'llmtool', 'llmtool.alpha', 'llmtool.alpha.x'})
        self.assertIsNone(graph.imports(P + 'llmtool/alpha.py', 'def broken(:\n'))

    def test_defined_names_cover_module_level_bindings(self):
        text = ('import os.path\nfrom json import dumps as dump\nfrom x import *\n'
                'VALUE, (OTHER, *REST) = 1, (2, 3)\nLIMIT: int = 3\n'
                'def run():\n    INNER = 1\nclass Box:\n    pass\n'
                'if True:\n    FLAG = 1\nelse:\n    FLAG = 0\n'
                'try:\n    import fast\nexcept ImportError:\n    fast = None\n')
        self.assertEqual(graph.defined_names(text),
                         {'os', 'dump', 'VALUE', 'OTHER', 'REST', 'LIMIT', 'run', 'Box',
                          'FLAG', 'fast'})
        self.assertIsNone(graph.defined_names('def broken(:\n'))
        self.assertIsNone(graph.defined_names(None))

    def test_defined_names_cover_with_targets(self):
        text = ('with open("a") as added:\n    INSIDE = 1\n'
                'with first() as (LEFT, *RIGHT), second() as SECOND, third():\n    pass\n'
                'async def run():\n    async with lock() as LOCAL:\n        pass\n')
        self.assertEqual(graph.defined_names(text),
                         {'added', 'INSIDE', 'LEFT', 'RIGHT', 'SECOND', 'run'})

    def test_used_names_from_from_imports_and_module_attributes(self):
        text = ('from llmtool.alpha import run\nfrom llmtool import gamma as g\n'
                'import llmtool.delta\nimport llmtool.epsilon as eps\nfrom . import beta\n'
                'from llmtool.unused import *\n'
                'g.helper()\nllmtool.delta.check()\neps.VALUE\nbeta.load\n')
        used = graph.used_names(P + 'llmtool/zeta.py', text)
        for pair in (('llmtool.alpha', 'run'), ('llmtool', 'gamma'), ('llmtool.gamma', 'helper'),
                     ('llmtool.delta', 'check'), ('llmtool.epsilon', 'VALUE'),
                     ('llmtool.beta', 'load'), ('llmtool.unused', '*')):
            with self.subTest(pair=pair):
                self.assertIn(pair, used)
        self.assertIsNone(graph.used_names(P + 'llmtool/zeta.py', 'import (\n'))


if __name__ == '__main__':
    unittest.main()
