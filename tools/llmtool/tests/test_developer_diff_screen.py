"""Developer diff screen: Python syntax release for keyword-shaped code (#981)."""

import sys
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import developer_diff_screen, high_debug, task_submission_cli, test_output_excerpts
from llmtool.developer_diff_screen import developer_diff_sensitive
from llmtool.task_submission_cli import (
    _added_diff_content, _developer_normalized_output, _developer_output_rejection,
    _patch_paths, _strict_developer_output_rejection)
from llmtool.test_output_excerpts import _emitted_sensitive

CODE = 'DEVELOPER_OUTPUT_SENSITIVE'
PY = 'pkg/mod.py'

# Keywords and screened values are assembled at run time so that this module's
# own source never resembles text the screen refuses.
KW = 'to' + 'ken'
PW = 'pass' + 'word'
SC = 'sec' + 'ret'
API = 'api' + '_key'
VALUE = 'zq' + 'xv' * 6


def _cloud():
    return 'AK' + 'IA' + 'Q' * 16


def _jwt():
    return 'ey' + 'J' + 'h' * 12 + '.' + 'p' * 10 + '.' + 's' * 10


def _url():
    return 'ht' + 'tps://' + 'u' * 4 + ':' + 'w' * 6 + '@' + 'example.invalid/x'


def _auth():
    return 'Bear' + 'er ' + 'r' * 16


def _private():
    return '-----BEGIN ' + 'PRIVATE KEY-----'


Case = namedtuple('Case', 'name path lines refused')


def _cases():
    # To record a future false positive (or a new refusal), add one row here.
    return (
        # Released: plain Python code that only looks like a keyword assignment.
        Case('plain reference', PY, [f'{KW} = other'], False),
        Case('upper-case name', PY, [f'{KW.upper()} = other'], False),
        Case('attribute value', PY, [f'{KW} = self.other'], False),
        Case('call value', PY, [f'{KW} = load_value(other, 3)'], False),
        Case('attribute target', PY, [f'self.{KW} = other'], False),
        Case('bare annotation', PY, [f'{KW}: str'], False),
        Case('annotation with call', PY, [f'{PW}: Optional[str] = load_value()'], False),
        Case('parameters', PY, [f'def run({KW}: str, {SC}=other):', '    return run'], False),
        Case('keyword arguments', PY, [f'result = call({API}=config.value, {PW}=read())'], False),
        Case('walrus', PY, [f'if ({KW} := load_value()):', '    pass'], False),
        Case('indented block', PY, [f'        {KW} = self._load()', f'        {SC} = {KW}'], False),
        Case('non-ascii neighbours', PY, [f'{KW} = naïve_value  # café'], False),
        # Refused: literal values, short ones included.
        Case('string literal', PY, [f'{KW} = "{VALUE}"'], True),
        Case('short string literal', PY, [f'{PW} = "x"'], True),
        Case('bytes literal', PY, [f'{SC} = b"x"'], True),
        Case('numeric literal', PY, [f'{SC} = 7'], True),
        Case('literal inside call', PY, [f'{KW} = load_value("{VALUE}")'], True),
        Case('literal deep inside call', PY, [f'{KW} = a.b(c(d=["{VALUE}"]))'], True),
        Case('literal annotation', PY, [f'{PW}: "{VALUE}"'], True),
        Case('literal default', PY, [f'def run({KW}="{VALUE}"):', '    pass'], True),
        Case('literal keyword argument', PY, [f'call({API}=b"{VALUE}")'], True),
        Case('subscript value', PY, [f'{KW} = values[other]'], True),
        Case('tuple value', PY, [f'{KW} = other,'], True),
        Case('lambda parameter', PY, [f'handler = lambda {KW}: "{VALUE}"'], True),
        # Refused: keyword text inside a string literal or comment.
        Case('inside string', PY, [f'note = "{KW} = other"'], True),
        Case('inside comment', PY, [f'# {KW} = other'], True),
        Case('inside f-string', PY, [f'note = f"{{{KW}=}}"'], True),
        Case('inside triple-quoted string', PY, ['note = """', f'{KW} = other', '"""'], True),
        Case('quoted dict key', PY, [f'values = {{"{KW}": other}}'], True),
        # Refused: fail closed when the fragment does not parse.
        Case('parse failure', PY, [f'{KW} = load_value('], True),
        Case('mixed indentation', PY, [f'    {KW} = other', f'\t{SC} = other'], True),
        # Refused: a release removes only its own match.
        Case('second match unproven', PY, [f'{KW} = other', f'{PW} = "{VALUE}"'], True),
        Case('cloud key beside release', PY, [f'{KW} = other', f'# {_cloud()}'], True),
        Case('jwt beside release', PY, [f'{KW} = other', f'# {_jwt()}'], True),
        Case('url userinfo beside release', PY, [f'{KW} = other', f'# {_url()}'], True),
        Case('auth scheme beside release', PY, [f'{KW} = other', f'# {_auth()}'], True),
        Case('private key beside release', PY, [f'{KW} = other', f'# {_private()}'], True),
        # Refused: non-.py files keep exactly the shared screen.
        Case('yaml unquoted value', 'deploy/app.yaml', [f'{KW}: {VALUE}'], True),
        Case('json value', 'conf/app.json', [f'"{PW}": "{VALUE}"'], True),
        Case('shell command value', 'bin/run.sh', [f'{SC}=$(load_value)'], True),
        Case('config reference', 'conf/app.cfg', [f'{API} = other'], True),
        Case('log line', 'out/run.log', [f'{KW}=other'], True),
        Case('python code in unknown type', 'notes.txt', [f'{KW} = other'], True),
        Case('python code in stub file', 'pkg/mod.pyi', [f'{KW}: str'], True),
    )


def _diff(path, lines):
    return (f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n'
            f'@@ -1 +1,{len(lines)} @@\n-previous_line\n'
            + ''.join('+' + line + '\n' for line in lines))


def _modified(path):
    return lambda value: {path: 'modified'}


class CaseTableTests(unittest.TestCase):
    def test_table(self):
        for case in _cases():
            with self.subTest(case.name):
                rejection = _strict_developer_output_rejection(
                    _diff(case.path, case.lines), [case.path])
                self.assertEqual(rejection, CODE if case.refused else None)

    def test_every_row_is_a_shared_screen_match(self):
        # Each row is meaningful only if the unchanged shared screen refuses it.
        for case in _cases():
            with self.subTest(case.name):
                self.assertTrue(_emitted_sensitive('\n'.join(case.lines)))
                self.assertTrue(_emitted_sensitive(_added_diff_content(
                    _diff(case.path, case.lines))))


class SharedScreenUnchangedTests(unittest.TestCase):
    def test_other_callers_use_the_same_shared_screen(self):
        shared = test_output_excerpts._emitted_sensitive
        self.assertIs(developer_diff_screen._emitted_sensitive, shared)
        self.assertIs(task_submission_cli._emitted_sensitive, shared)
        self.assertIs(high_debug._emitted_sensitive, shared)
        self.assertIs(developer_diff_screen._SENSITIVE, test_output_excerpts._SENSITIVE)

    def test_shared_results_unchanged_after_developer_screen(self):
        for case in _cases():
            text = '\n'.join(case.lines)
            with self.subTest(case.name):
                before = _emitted_sensitive(text)
                _strict_developer_output_rejection(_diff(case.path, case.lines), [case.path])
                self.assertEqual(_emitted_sensitive(text), before)
                self.assertTrue(before)

    def test_non_python_targets_get_exactly_the_shared_result(self):
        lines_list = [case.lines for case in _cases()] + [['plain = 1'], ['x = other']]
        for path in ('conf/app.json', 'deploy/app.yaml', 'bin/run.sh', 'conf/app.ini',
                     'out/run.log', 'Makefile', 'pkg/mod.PY'):
            for lines in lines_list:
                value = _diff(path, lines)
                added = _added_diff_content(value)
                with self.subTest(path=path, lines=lines):
                    self.assertEqual(developer_diff_sensitive(value, added, _patch_paths),
                                     _emitted_sensitive(added))

    def test_no_shared_match_is_never_refused(self):
        def unused(value):
            raise AssertionError('parser must not run without a shared match')
        self.assertFalse(developer_diff_sensitive(_diff(PY, ['x = 1']), 'x = 1', unused))


class RefusalCarriesNoTextTests(unittest.TestCase):
    def test_code_and_record_carry_no_matched_text(self):
        for lines in ([f'{KW} = "{VALUE}"'], [f'{KW} = other', f'# {_cloud()}'],
                      [f'{KW} = other', f'# {_auth()}']):
            value = _diff(PY, lines)
            with self.subTest(lines=lines):
                self.assertIs(developer_diff_sensitive(
                    value, _added_diff_content(value), _patch_paths), True)
                rejection = _strict_developer_output_rejection(value, [PY])
                self.assertEqual(rejection, CODE)
                record = _developer_normalized_output(value, [PY])
                self.assertEqual(record, (None, (), CODE))
                self.assertEqual(_developer_output_rejection(value, [PY], normalize=True), CODE)
                for text in (VALUE, _cloud(), _auth(), KW):
                    self.assertNotIn(text, repr(rejection))
                    self.assertNotIn(text, repr(record))


class FailClosedTests(unittest.TestCase):
    def test_malformed_diff_keeps_shared_result(self):
        released = [f'{KW} = other']
        value = _diff(PY, released).replace('@@ -1 +1,1 @@', '@@ -1 +1,3 @@')
        self.assertEqual(_strict_developer_output_rejection(value, [PY]), CODE)
        self.assertEqual(_strict_developer_output_rejection(_diff(PY, released), [PY]), None)

    def test_parser_error_keeps_shared_result(self):
        value = _diff(PY, [f'{KW} = other'])
        added = _added_diff_content(value)

        def broken(value):
            raise RuntimeError('unexpected')
        self.assertTrue(developer_diff_sensitive(value, added, broken))
        with mock.patch.object(developer_diff_screen.ast, 'parse', side_effect=RecursionError):
            self.assertTrue(developer_diff_sensitive(value, added, _patch_paths))
        with mock.patch.object(developer_diff_screen, '_layout', side_effect=RuntimeError):
            self.assertTrue(developer_diff_sensitive(value, added, _patch_paths))
        self.assertFalse(developer_diff_sensitive(value, added, _patch_paths))

    def test_added_text_must_match_the_shared_input(self):
        value = _diff(PY, [f'{KW} = other'])
        added = _added_diff_content(value)
        self.assertTrue(developer_diff_sensitive(value, added + '\nx = 1', _patch_paths))

    def test_line_breaks_outside_git_and_python_keep_shared_result(self):
        joined = f'diff --git a/{PY} b/{PY}\n--- a/{PY}\n+++ b/{PY}\n@@ -1 +1,2 @@\n-old\n'
        separate = joined + f'+other = 1\n+{KW} = other\n'
        self.assertFalse(developer_diff_sensitive(
            separate, _added_diff_content(separate), _modified(PY)))
        for brk in (' ', ' ', '\x85', '\x0b', '\r'):
            value = joined + f'+other = 1{brk}+{KW} = other\n'
            with self.subTest(repr(brk)):
                self.assertTrue(developer_diff_sensitive(
                    value, _added_diff_content(value), _modified(PY)))

    def test_path_outside_parsed_diff_keeps_shared_result(self):
        value = _diff(PY, [f'{KW} = other'])
        self.assertTrue(developer_diff_sensitive(
            value, _added_diff_content(value), _modified('pkg/other.py')))


class DiffShapeTests(unittest.TestCase):
    def test_new_python_file_is_released(self):
        value = (f'diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n'
                 f'+++ b/new.py\n@@ -0,0 +1,2 @@\n+def run({KW}: str):\n+    return {KW}\n')
        self.assertIsNone(_strict_developer_output_rejection(value, ['new.py'], allow_new_files=True))

    def test_files_are_screened_by_their_own_type(self):
        py = _diff(PY, [f'{KW} = other'])
        yaml = _diff('deploy/app.yaml', [f'{KW}: other'])
        for value in (py + yaml, yaml + py):
            with self.subTest(order=value[:30]):
                self.assertEqual(_strict_developer_output_rejection(
                    value, [PY, 'deploy/app.yaml']), CODE)
        self.assertIsNone(_strict_developer_output_rejection(
            py + _diff('pkg/two.py', [f'{SC} = other']), [PY, 'pkg/two.py']))

    def test_match_across_files_is_refused(self):
        value = _diff(PY, ['x = 1', KW]) + _diff('pkg/two.py', ['= other'])
        self.assertEqual(_strict_developer_output_rejection(value, [PY, 'pkg/two.py']), CODE)

    def test_removed_lines_do_not_split_a_new_side_block(self):
        value = (f'diff --git a/{PY} b/{PY}\n--- a/{PY}\n+++ b/{PY}\n@@ -1,2 +1,2 @@\n'
                 f'+{KW} = load_value(\n-old_one\n-old_two\n+    other)\n')
        self.assertIsNone(_strict_developer_output_rejection(value, [PY]))

    def test_context_lines_split_blocks(self):
        value = (f'diff --git a/{PY} b/{PY}\n--- a/{PY}\n+++ b/{PY}\n@@ -1,2 +1,3 @@\n'
                 f'+{KW} = load_value(\n same\n+    other)\n-old\n')
        self.assertEqual(_strict_developer_output_rejection(value, [PY]), CODE)


if __name__ == '__main__':
    unittest.main()
