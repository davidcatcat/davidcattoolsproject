"""#1943 WP-6a: every refusal code in scope has a class in refusal-codes.json.

The registry classifies each refusal code as TERMINAL (with a PM action),
RECOVERY (with one parseable ``llmtool task`` command) or OWNER_RESERVED (with
one #1943 reserved row). A code passed to ``_refuse`` or ``_refuse_unclaimed``
in a scope file without a registry entry fails here, so a new code cannot
reach main unclassified.

The test reads files located from this test file, parses the scope files with
``ast`` (never imports or runs them), and builds the CLI parser without running
any command. No network, provider, Git or subprocess is used.
"""

import ast
import contextlib
import io
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from llmtool import repair_policy
from llmtool.cli import parser_for_cli

_LLMTOOL_ROOT = Path(__file__).resolve().parent.parent
_REPO_PREFIX = 'tools/llmtool/'
REGISTRY = _LLMTOOL_ROOT / 'refusal-codes.json'

REFUSE_FUNCTIONS = frozenset(('_refuse', '_refuse_unclaimed'))
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
MAX_REASON_CHARS = 200
_PLACEHOLDER = re.compile(r'\{([^{}]*)\}')

# Non-literal first arguments in scope today, each covered another way:
#   f'REPAIR_{action}_REQUIRED': every escalation step (test below);
#   code: every admit_repair refusal (test below);
#   decision.reason_code: worker_dispatch reason codes, outside this
#   package's scope and not classified here.
# A new non-literal call site fails until it is reviewed and listed.
KNOWN_DYNAMIC_ARGUMENTS = frozenset((
    "f'REPAIR_{action}_REQUIRED'", 'code', 'decision.reason_code'))


def load_registry(path=REGISTRY):
    return json.loads(path.read_text(encoding='utf-8'))


def scope_file(relative):
    """The file for one repository-relative scope path, located from this test."""
    if (type(relative) is not str or not relative.startswith(_REPO_PREFIX) or
            '..' in Path(relative).parts or Path(relative).is_absolute()):
        raise ValueError('scope path must sit under ' + _REPO_PREFIX)
    return _LLMTOOL_ROOT / relative[len(_REPO_PREFIX):]


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def collect_refusals(path):
    """Return (literal codes, unparsed non-literal first arguments) of one file.

    A missing file raises; it is never read as an empty scope.
    """
    tree = ast.parse(Path(path).read_text(encoding='utf-8'), filename=str(path))
    literal, dynamic = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in REFUSE_FUNCTIONS:
            continue
        if not node.args:
            dynamic.add('<no argument>')
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and type(first.value) is str:
            literal.add(first.value)
        else:
            dynamic.add(ast.unparse(first))
    return literal, dynamic


def recovery_argv(template):
    """The parser argv of one RECOVERY template with dummy placeholder values."""
    dummy = {'task': format(1, '032x'), 'executor_task': format(2, '032x'),
             'pr': '1', 'version': '1', 'family': 'pr-1'}
    words = shlex.split(_PLACEHOLDER.sub(lambda match: dummy[match.group(1)], template))
    if not words or words[0] != 'llmtool':
        raise ValueError('recovery template must start with llmtool')
    return words[1:]


def entry_problems(code, entry):
    """Return a list of problems with one registry entry; [] when it is valid."""
    if type(code) is not str or re.fullmatch(r'[A-Z][A-Z0-9_]*', code) is None:
        return ['code name is not an upper-case identifier']
    if type(entry) is not dict:
        return ['entry is not an object']
    kind = entry.get('class')
    if kind not in CLASS_KEYS:
        return ['class is not TERMINAL, RECOVERY or OWNER_RESERVED']
    problems = []
    if set(entry) != CLASS_KEYS[kind]:
        problems.append(f'{kind} keys must be exactly {sorted(CLASS_KEYS[kind])}')
    for key in sorted(CLASS_KEYS[kind] - {'class'}):
        value = entry.get(key)
        if type(value) is not str or not value.strip() or '\n' in value:
            problems.append(f'{key} must be a non-empty one-line string')
    reason = entry.get('reason')
    if type(reason) is str and len(reason) >= MAX_REASON_CHARS:
        problems.append(f'reason must be under {MAX_REASON_CHARS} characters')
    if kind == 'OWNER_RESERVED' and entry.get('reserved_row') not in RESERVED_ROWS:
        problems.append('reserved_row is not a #1943 Owner ask gate ID')
    if kind == 'RECOVERY' and type(entry.get('recovery')) is str:
        template = entry['recovery']
        if not template.startswith(RECOVERY_PREFIX):
            problems.append(f'recovery must start with {RECOVERY_PREFIX!r}')
        unknown = set(_PLACEHOLDER.findall(template)) - PLACEHOLDERS
        if unknown:
            problems.append(f'recovery has unknown placeholders {sorted(unknown)}')
        elif '{' in _PLACEHOLDER.sub('', template) or '}' in _PLACEHOLDER.sub('', template):
            problems.append('recovery has an unbalanced brace')
    return problems


class RegistryShapeTest(unittest.TestCase):
    def test_registry_loads_with_schema_version_one(self):
        data = load_registry()
        self.assertEqual(set(data), {'schema_version', 'scope', 'codes'})
        self.assertIs(type(data['schema_version']), int)
        self.assertEqual(data['schema_version'], 1)
        self.assertIs(type(data['scope']), list)
        self.assertTrue(data['scope'])
        self.assertIs(type(data['codes']), dict)
        self.assertTrue(data['codes'])

    def test_codes_are_sorted_and_file_is_canonical(self):
        text = REGISTRY.read_text(encoding='utf-8')
        data = json.loads(text)
        self.assertEqual(list(data['codes']), sorted(data['codes']))
        self.assertEqual(text, json.dumps(data, indent=2, ensure_ascii=False) + '\n')

    def test_every_entry_has_exactly_its_class_keys(self):
        problems = {}
        for code, entry in load_registry()['codes'].items():
            found = entry_problems(code, entry)
            if found:
                problems[code] = found
        self.assertEqual(problems, {})

    def test_every_owner_reserved_row_is_a_gate_id(self):
        for code, entry in load_registry()['codes'].items():
            if entry.get('class') == 'OWNER_RESERVED':
                self.assertIn(entry.get('reserved_row'), RESERVED_ROWS, code)
        self.assertEqual(len(RESERVED_ROWS), 14)


class EntryValidationTest(unittest.TestCase):
    """The checker itself refuses malformed entries, so an empty class is not a pass."""

    def test_valid_entries_of_each_class_pass(self):
        for entry in ({'class': 'TERMINAL', 'reason': 'Ends.', 'pm_action': 'Replan.'},
                      {'class': 'RECOVERY', 'reason': 'Waits.',
                       'recovery': 'llmtool task advance {task} --pr {pr}'},
                      {'class': 'OWNER_RESERVED', 'reason': 'Paused.',
                       'reserved_row': 'R-DISPATCH'}):
            self.assertEqual(entry_problems('SAMPLE_CODE', entry), [], entry)

    def test_unknown_reserved_row_fails(self):
        entry = {'class': 'OWNER_RESERVED', 'reason': 'Paused.', 'reserved_row': 'R-NEW'}
        self.assertTrue(entry_problems('SAMPLE_CODE', entry))

    def test_wrong_or_extra_keys_fail(self):
        for entry in ({'class': 'TERMINAL', 'reason': 'Ends.'},
                      {'class': 'TERMINAL', 'reason': 'Ends.', 'pm_action': 'Replan.',
                       'recovery': 'llmtool task advance {task}'},
                      {'class': 'RECOVERY', 'reason': 'Waits.', 'pm_action': 'Replan.'},
                      {'class': 'UNKNOWN', 'reason': 'Ends.'}):
            self.assertTrue(entry_problems('SAMPLE_CODE', entry), entry)

    def test_empty_or_long_reason_fails(self):
        too_long = 'word ' * (MAX_REASON_CHARS // 5)
        self.assertEqual(len(too_long), MAX_REASON_CHARS)
        for reason in ('', '   ', too_long, 'two\nlines'):
            entry = {'class': 'TERMINAL', 'reason': reason, 'pm_action': 'Replan.'}
            self.assertTrue(entry_problems('SAMPLE_CODE', entry), reason)

    def test_bad_recovery_template_fails(self):
        for template in ('llmtool advance {task}', 'task advance {task}',
                         'llmtool task advance {task_id}', 'llmtool task advance {task'):
            entry = {'class': 'RECOVERY', 'reason': 'Waits.', 'recovery': template}
            self.assertTrue(entry_problems('SAMPLE_CODE', entry), template)


class ScopeCoverageTest(unittest.TestCase):
    def test_scope_paths_exist(self):
        for relative in load_registry()['scope']:
            path = scope_file(relative)
            self.assertTrue(path.is_file(), f'scope path does not exist: {relative}')

    def test_missing_scope_path_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                collect_refusals(Path(directory) / 'absent.py')
        with self.assertRaises(ValueError):
            scope_file('elsewhere/module.py')
        with self.assertRaises(ValueError):
            scope_file('tools/llmtool/../outside.py')

    def test_collector_reads_both_refuse_functions(self):
        source = ("def run(code):\n"
                  "    _refuse('FIRST_CODE')\n"
                  "    module._refuse_unclaimed('SECOND_CODE', 'x')\n"
                  "    _refuse(code)\n"
                  "    other('IGNORED_CODE')\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.py'
            path.write_text(source, encoding='utf-8')
            literal, dynamic = collect_refusals(path)
        self.assertEqual(literal, {'FIRST_CODE', 'SECOND_CODE'})
        self.assertEqual(dynamic, {'code'})

    def test_every_literal_refusal_code_is_classified(self):
        data = load_registry()
        unclassified = set()
        for relative in data['scope']:
            literal, _dynamic = collect_refusals(scope_file(relative))
            unclassified |= literal - set(data['codes'])
        self.assertEqual(sorted(unclassified), [],
                         'refusal codes without a class in refusal-codes.json: ' +
                         ', '.join(sorted(unclassified)))

    def test_scope_finds_refusal_codes(self):
        literal = set()
        for relative in load_registry()['scope']:
            literal |= collect_refusals(scope_file(relative))[0]
        self.assertTrue(literal)

    def test_non_literal_refusals_are_known(self):
        dynamic = set()
        for relative in load_registry()['scope']:
            dynamic |= collect_refusals(scope_file(relative))[1]
        self.assertEqual(sorted(dynamic - KNOWN_DYNAMIC_ARGUMENTS), [],
                         'new non-literal refusal argument; classify its codes and list it')

    def test_escalation_refusals_are_classified(self):
        codes = load_registry()['codes']
        # The f'REPAIR_{action}_REQUIRED' call site: every escalation step.
        for action in sorted(set(repair_policy._ESCALATION.values())):
            self.assertIn(f'REPAIR_{action}_REQUIRED', codes)

    def test_admit_repair_refusals_are_classified(self):
        codes = load_registry()['codes']
        policy = {'repair_max': 2}
        found = {repair_policy.admit_repair(repair_count=count, rca_confirmed=confirmed,
                                            policy=policy)
                 for count in (0, 1, 2, repair_policy.UNKNOWN)
                 for confirmed in (True, False)}
        found.discard(None)
        self.assertTrue(found)
        for code in sorted(found):
            self.assertIn(code, codes)


class RecoveryCommandTest(unittest.TestCase):
    def test_every_recovery_template_parses(self):
        recoveries = {code: entry['recovery']
                      for code, entry in load_registry()['codes'].items()
                      if entry.get('class') == 'RECOVERY'}
        for code, template in sorted(recoveries.items()):
            with self.subTest(code=code):
                argv = recovery_argv(template)
                stderr = io.StringIO()
                try:
                    with contextlib.redirect_stderr(stderr):
                        args = parser_for_cli().parse_args(argv)
                except SystemExit:
                    self.fail(f'{code}: recovery command does not parse: {template}')
                self.assertEqual(args.command, 'task')
                self.assertEqual(args.action, argv[1])

    def test_unknown_task_action_does_not_parse(self):
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
            parser_for_cli().parse_args(recovery_argv('llmtool task no-such-action {task}'))


if __name__ == '__main__':
    unittest.main()
