"""#1943 WP-6a: every review repair refusal code has a registered class.

``llmtool/refusal_codes.json`` classifies each refusal code as TERMINAL (with
a PM action), RECOVERY (with one parseable ``llmtool task`` command) or
OWNER_RESERVED (with a #1943 Owner ask gate row). This test parses each scope
file with ``ast`` and fails on any code passed to ``_refuse`` or
``_refuse_unclaimed`` that the registry does not classify, so a new code
needs a class before CI passes. Registry codes not found in scope are allowed.

Non-literal call arguments are matched by AST structure (``ast.dump``), never
by source text, so the result is the same on Python 3.13 and 3.14.

Files are located from this test file. The test uses no network, provider,
Git or subprocess, and it only parses the recovery commands; it runs none.
"""

import ast
import io
import json
import re
import shlex
import string
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from llmtool import repair_policy
from llmtool.cli import parser_for_cli


_LLMTOOL_ROOT = Path(__file__).resolve().parent.parent
_REGISTRY = _LLMTOOL_ROOT / 'llmtool' / 'refusal_codes.json'
_REPO_PREFIX = 'tools/llmtool/'
_REVIEW_REPAIR = 'tools/llmtool/llmtool/review_repair.py'
_REFUSERS = frozenset(('_refuse', '_refuse_unclaimed'))
_TOP_KEYS = ('schema_version', 'scope', 'codes')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
_MAX_SENTENCE = 200

CLASS_KEYS = {
    'TERMINAL': frozenset(('class', 'reason', 'pm_action')),
    'RECOVERY': frozenset(('class', 'reason', 'recovery')),
    'OWNER_RESERVED': frozenset(('class', 'reason', 'reserved_row')),
}
# The #1943 Owner ask gate IDs.
RESERVED_ROWS = frozenset((
    'R-SCOPE', 'R-RELEASE', 'R-SPEND', 'R-EGRESS', 'R-SECURITY', 'R-CRED', 'R-ROOT',
    'R-MACOS', 'R-REVIEWER', 'R-DISPATCH', 'R-ACCEPT', 'R-BUDGET', 'R-STOP', 'R-OTHER'))
RECOVERY_PREFIX = 'llmtool task '
PLACEHOLDERS = frozenset(('task', 'pr', 'executor_task', 'version', 'family'))
# Low-entropy dummy values; real Task IDs are 32 lowercase hex characters.
DUMMY_VALUES = {'task': '1' * 32, 'executor_task': '2' * 32, 'pr': 1, 'version': 1,
                'family': 'pr-1'}


def _escalation_codes():
    """Codes from _refuse(f'REPAIR_{action}_REQUIRED'): the real escalation actions."""
    policy = {'review_fail_max': 3, 'group_debug_max': 1}
    actions = {repair_policy.review_action(kind=kind, fail_count=3, policy=policy)
               for kind in repair_policy.KINDS}
    return {f'REPAIR_{action}_REQUIRED' for action in actions}


def _admit_repair_codes():
    """Codes from _refuse(code) after repair_policy.admit_repair."""
    policy = {'repair_max': 2}
    codes = {repair_policy.admit_repair(repair_count=count, rca_confirmed=confirmed,
                                        policy=policy)
             for count in (0, 1, 2, 3, repair_policy.UNKNOWN) for confirmed in (True, False)}
    return codes - {None}


# Every non-literal first argument the scope files may pass, as an expression
# whose ast.dump is compared, mapped to a function that returns the codes it
# can carry. None marks codes that come from outside this registry's scope.
KNOWN_DYNAMIC = {
    "f'REPAIR_{action}_REQUIRED'": _escalation_codes,
    'code': _admit_repair_codes,
    # worker_dispatch.evaluate reason codes: worker_dispatch.py is not a scope
    # file of this registry yet, so they are not enumerated here.
    'decision.reason_code': None,
}


class ScopeError(Exception):
    """A registry scope path that is malformed or does not exist."""


def _expression_dump(source):
    return ast.dump(ast.parse(source, mode='eval').body)


def load_registry(path=_REGISTRY):
    text = path.read_text(encoding='utf-8')
    return text, json.loads(text)


def scope_path(rel, root=_LLMTOOL_ROOT):
    """The file for one repository-relative scope path; ScopeError when absent."""
    if (type(rel) is not str or not rel.startswith(_REPO_PREFIX) or
            not rel.endswith('.py') or '..' in rel.split('/') or '\\' in rel):
        raise ScopeError(f'scope path is not a .py file under {_REPO_PREFIX}: {rel!r}')
    path = root / rel[len(_REPO_PREFIX):]
    if not path.is_file():
        raise ScopeError(f'scope path does not exist: {rel}')
    return path


def refusal_arguments(source, filename='<scope>'):
    """Return (literal codes, [(line, ast.dump)] of non-literal first arguments).

    Only calls whose function name is _refuse or _refuse_unclaimed count. A call
    with no first argument is reported as a non-literal argument of dump None.
    """
    tree = ast.parse(source, filename=filename)
    literals = set()
    dynamic = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name not in _REFUSERS:
            continue
        if node.args:
            argument = node.args[0]
        else:
            argument = next((item.value for item in node.keywords if item.arg == 'code'), None)
        if isinstance(argument, ast.Constant) and type(argument.value) is str:
            literals.add(argument.value)
        else:
            dynamic.append((node.lineno,
                            None if argument is None or isinstance(argument, ast.Starred)
                            else ast.dump(argument)))
    dynamic.sort(key=lambda item: item[0])
    return literals, dynamic


def _sentence_errors(label, value):
    if type(value) is not str or not value.strip():
        return [f'{label} is not a non-empty string']
    errors = []
    if len(value) >= _MAX_SENTENCE:
        errors.append(f'{label} is {_MAX_SENTENCE} characters or longer')
    if '\n' in value or '\r' in value or value != value.strip():
        errors.append(f'{label} is not one line without outer spaces')
    if not value.endswith('.'):
        errors.append(f'{label} does not end with a period')
    return errors


def recovery_fields(template):
    """Placeholder names in a recovery template; ValueError when malformed."""
    fields = set()
    for _literal, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            continue
        if spec or conversion or field not in PLACEHOLDERS:
            raise ValueError('recovery placeholder is not allowed')
        fields.add(field)
    return fields


def recovery_argv(template):
    """The parser argv of a recovery template filled with dummy values."""
    argv = shlex.split(template.format(**DUMMY_VALUES))
    if argv[:2] != ['llmtool', 'task'] or len(argv) < 3:
        raise ValueError('recovery is not an llmtool task command')
    return argv[1:]


def entry_errors(code, entry):
    """Every problem with one registry entry; [] when the entry is valid."""
    if type(code) is not str or _CODE.fullmatch(code) is None:
        return [f'{code!r}: code is not an upper-case refusal code']
    if type(entry) is not dict:
        return [f'{code}: entry is not an object']
    kind = entry.get('class')
    if kind not in CLASS_KEYS:
        return [f'{code}: class {kind!r} is not TERMINAL, RECOVERY or OWNER_RESERVED']
    errors = []
    if set(entry) != CLASS_KEYS[kind]:
        errors.append(f'{code}: keys {sorted(entry)} are not {sorted(CLASS_KEYS[kind])}')
    errors += _sentence_errors(f'{code}: reason', entry.get('reason'))
    if kind == 'TERMINAL':
        errors += _sentence_errors(f'{code}: pm_action', entry.get('pm_action'))
    elif kind == 'RECOVERY':
        template = entry.get('recovery')
        if type(template) is not str or not template.startswith(RECOVERY_PREFIX):
            errors.append(f'{code}: recovery does not start with {RECOVERY_PREFIX!r}')
        else:
            try:
                recovery_fields(template)
                recovery_argv(template)
            except ValueError:
                errors.append(f'{code}: recovery template is malformed')
    elif entry.get('reserved_row') not in RESERVED_ROWS:
        errors.append(f'{code}: reserved_row is not a #1943 Owner ask gate ID')
    return errors


class RefusalCodeRegistryTest(unittest.TestCase):
    def setUp(self):
        self.text, self.registry = load_registry()

    def test_registry_shape_and_entries(self):
        registry = self.registry
        self.assertEqual(tuple(registry), _TOP_KEYS)
        self.assertIs(type(registry['schema_version']), int)
        self.assertEqual(registry['schema_version'], 1)
        scope = registry['scope']
        self.assertIs(type(scope), list)
        self.assertIn(_REVIEW_REPAIR, scope)
        self.assertEqual(len(scope), len(set(scope)))
        codes = registry['codes']
        self.assertIs(type(codes), dict)
        self.assertTrue(codes)
        self.assertEqual(list(codes), sorted(codes), 'codes are not sorted')
        errors = []
        for code, entry in codes.items():
            errors += entry_errors(code, entry)
        self.assertEqual(errors, [], '\n'.join(errors))

    def test_registry_is_two_space_json_with_final_newline(self):
        self.assertTrue(self.text.endswith('}\n'))
        self.assertEqual(self.text, json.dumps(self.registry, indent=2) + '\n')

    def test_every_scope_refusal_code_is_classified(self):
        codes = set(self.registry['codes'])
        known = {_expression_dump(source): (source, expand)
                 for source, expand in KNOWN_DYNAMIC.items()}
        seen = set()
        unclassified = set()
        unknown_arguments = []
        for rel in self.registry['scope']:
            path = scope_path(rel)
            literals, dynamic = refusal_arguments(path.read_text(encoding='utf-8'), rel)
            self.assertTrue(literals or dynamic, f'{rel}: no refusal call found')
            unclassified |= literals - codes
            for line, dump in dynamic:
                if dump not in known:
                    unknown_arguments.append(f'{rel}:{line}')
                    continue
                source, expand = known[dump]
                seen.add(source)
                if expand is not None:
                    unclassified |= expand() - codes
        self.assertEqual(sorted(unclassified), [],
                         'Refusal codes without a class in refusal_codes.json: ' +
                         ', '.join(sorted(unclassified)))
        self.assertEqual(unknown_arguments, [],
                         'Refusal calls with an unregistered non-literal code argument: ' +
                         ', '.join(unknown_arguments))
        stale = sorted(set(KNOWN_DYNAMIC) - seen)
        self.assertEqual(stale, [], 'KNOWN_DYNAMIC entries no longer in scope; remove them')

    def test_dynamic_codes_expand_to_real_policy_outcomes(self):
        self.assertEqual(_escalation_codes(),
                         {'REPAIR_SPLIT_REQUIRED', 'REPAIR_PM_DECIDE_REQUIRED',
                          'REPAIR_FAILED_FINAL_REQUIRED'})
        self.assertEqual(_admit_repair_codes(),
                         {'REPAIR_LIMIT_REACHED', 'REPAIR_DEBUG_REQUIRED'})

    def test_recovery_commands_parse(self):
        parser = parser_for_cli()
        for code, entry in self.registry['codes'].items():
            if entry.get('class') != 'RECOVERY':
                continue
            with self.subTest(code=code):
                argv = recovery_argv(entry['recovery'])
                try:
                    with redirect_stderr(io.StringIO()):
                        parsed = parser.parse_args(argv)
                except SystemExit:
                    self.fail(f'{code}: recovery does not parse as llmtool {argv[0]} {argv[1]}')
                self.assertEqual(parsed.command, 'task')
                self.assertEqual(getattr(parsed, 'action', None), argv[1])

    def test_owner_reserved_rows_are_gate_ids(self):
        self.assertEqual(len(RESERVED_ROWS), 14)
        for code, entry in self.registry['codes'].items():
            if entry.get('class') == 'OWNER_RESERVED':
                self.assertIn(entry.get('reserved_row'), RESERVED_ROWS, code)


class ScopePathTest(unittest.TestCase):
    def test_missing_scope_path_fails(self):
        with self.assertRaises(ScopeError):
            scope_path('tools/llmtool/llmtool/no_such_refusal_module.py')

    def test_scope_path_outside_llmtool_fails(self):
        for rel in ('docs/review_repair.py', 'tools/llmtool/../review_repair.py',
                    'tools/llmtool/llmtool/refusal_codes.json', None):
            with self.subTest(rel=rel):
                with self.assertRaises(ScopeError):
                    scope_path(rel)

    def test_review_repair_scope_path_exists(self):
        self.assertTrue(scope_path(_REVIEW_REPAIR).is_file())


class RefusalArgumentsTest(unittest.TestCase):
    SOURCE = (
        "def sample(code, decision, action):\n"
        "    _refuse('SAMPLE_ONE')\n"
        "    _refuse_unclaimed('SAMPLE_TWO', 'task-id')\n"
        "    module._refuse('SAMPLE_THREE')\n"
        "    _refuse(code)\n"
        "    _refuse(f'SAMPLE_{action}_REQUIRED')\n"
        "    _refuse(code='SAMPLE_FOUR')\n"
        "    dispute_refuse('SAMPLE_NOT_COUNTED')\n"
        "    _refuse()\n"
    )

    def test_literals_and_non_literals_are_collected(self):
        literals, dynamic = refusal_arguments(self.SOURCE)
        self.assertEqual(literals, {'SAMPLE_ONE', 'SAMPLE_TWO', 'SAMPLE_THREE', 'SAMPLE_FOUR'})
        self.assertEqual(dynamic, [
            (5, _expression_dump('code')),
            (6, _expression_dump("f'SAMPLE_{action}_REQUIRED'")),
            (9, None),
        ])

    def test_unclassified_literal_is_reported(self):
        literals, _dynamic = refusal_arguments("_refuse('SAMPLE_UNCLASSIFIED')\n")
        self.assertEqual(literals - set(load_registry()[1]['codes']), {'SAMPLE_UNCLASSIFIED'})

    def test_non_literal_structure_differs_from_known(self):
        _literals, dynamic = refusal_arguments("_refuse(f'REPAIR_{other}_REQUIRED')\n")
        known = {_expression_dump(source) for source in KNOWN_DYNAMIC}
        self.assertEqual(len(dynamic), 1)
        self.assertNotIn(dynamic[0][1], known)


class EntryErrorsTest(unittest.TestCase):
    """Synthetic entries; none is a real registry entry."""

    def test_valid_entries_of_each_class(self):
        self.assertEqual(entry_errors('SAMPLE_CODE', {
            'class': 'TERMINAL', 'reason': 'Sample reason.', 'pm_action': 'Replan.'}), [])
        self.assertEqual(entry_errors('SAMPLE_CODE', {
            'class': 'RECOVERY', 'reason': 'Sample reason.',
            'recovery': 'llmtool task advance {task} --pr {pr}'}), [])
        self.assertEqual(entry_errors('SAMPLE_CODE', {
            'class': 'OWNER_RESERVED', 'reason': 'Sample reason.',
            'reserved_row': 'R-DISPATCH'}), [])

    def test_invalid_entries_are_reported(self):
        cases = (
            {'class': 'OTHER', 'reason': 'Sample reason.'},
            {'class': 'TERMINAL', 'reason': 'Sample reason.'},
            {'class': 'TERMINAL', 'reason': '', 'pm_action': 'Replan.'},
            {'class': 'TERMINAL', 'reason': 'x' * 250 + '.', 'pm_action': 'Replan.'},
            {'class': 'TERMINAL', 'reason': 'Two\nlines.', 'pm_action': 'Replan.'},
            {'class': 'TERMINAL', 'reason': 'Sample reason.', 'pm_action': 'Replan.',
             'recovery': 'llmtool task advance {task}'},
            {'class': 'RECOVERY', 'reason': 'Sample reason.', 'recovery': 'git status'},
            {'class': 'RECOVERY', 'reason': 'Sample reason.',
             'recovery': 'llmtool task advance {head}'},
            {'class': 'RECOVERY', 'reason': 'Sample reason.',
             'recovery': 'llmtool task advance {task!r}'},
            {'class': 'RECOVERY', 'reason': 'Sample reason.', 'recovery': 'llmtool task '},
            {'class': 'OWNER_RESERVED', 'reason': 'Sample reason.', 'reserved_row': 'R-NONE'},
            {'class': 'OWNER_RESERVED', 'reason': 'Sample reason.'},
        )
        for entry in cases:
            with self.subTest(entry=entry):
                self.assertNotEqual(entry_errors('SAMPLE_CODE', entry), [])

    def test_malformed_code_is_reported(self):
        for code in ('', 'lower_case', 'SAMPLE-CODE', 'S' * 65, None):
            with self.subTest(code=code):
                self.assertNotEqual(entry_errors(code, {
                    'class': 'TERMINAL', 'reason': 'Sample reason.', 'pm_action': 'Replan.'}), [])


if __name__ == '__main__':
    unittest.main()
