#!/usr/bin/env python3
"""Check joined sensitive-looking strings in test source without importing it."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Iterable


REPO = Path(__file__).resolve().parents[3]
LLMTOOL = REPO / "tools" / "llmtool"
if str(LLMTOOL) not in sys.path:
    sys.path.insert(0, str(LLMTOOL))

from llmtool.high_debug import _emitted_sensitive, _screen_hits


SCREEN_TOOL_TEST_MODULES = (
    "test_output_screen_scope",
    "test_test_output_excerpts",
    "test_test_output_excerpts_fidelity",
    "test_high_debug",
    "test_spec_preflight",
    "test_fixture_check",
)
LABEL = "# synthetic-fixture: screen-tool test"
BASELINE = REPO / "tools" / "llmtool" / "tests" / "fixture_check_baseline.json"
MAX_FOLDED_LENGTH = 65536
MAX_FOLDED_PARTS = 16384


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    return None


def _fold(node: ast.AST, names: dict[str, str]) -> tuple[str, list[str]] | None:
    """Return a foldable string and its independent text parts."""
    literal = _constant_string(node)
    if literal is not None:
        if len(literal) > MAX_FOLDED_LENGTH:
            return None
        return literal, [literal]
    if isinstance(node, ast.Name):
        value = names.get(node.id)
        return (value, [value]) if value is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left, names), _fold(node.right, names)
        if left is None or right is None:
            return None
        if len(left[0]) + len(right[0]) > MAX_FOLDED_LENGTH:
            return None
        if len(left[1]) + len(right[1]) > MAX_FOLDED_PARTS:
            return None
        return left[0] + right[0], left[1] + right[1]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if isinstance(node.right, ast.Constant) and type(node.right.value) is int:
            base, count = _fold(node.left, names), node.right.value
        elif isinstance(node.left, ast.Constant) and type(node.left.value) is int:
            base, count = _fold(node.right, names), node.left.value
        else:
            return None
        if base is None or abs(count) > 10000 or len(base[0]) * abs(count) > MAX_FOLDED_LENGTH:
            return None
        return base[0] * count, base[1]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join" and len(node.args) == 1
            and not node.keywords):
        separator = _constant_string(node.func.value)
        sequence = node.args[0]
        if (separator is None or len(separator) > MAX_FOLDED_LENGTH
                or not isinstance(sequence, (ast.Tuple, ast.List))):
            return None
        values = [_fold(item, names) for item in sequence.elts]
        if any(value is None for value in values):
            return None
        folded = [value for value in values if value is not None]
        if sum(len(value[0]) for value in folded) + max(0, len(folded) - 1) * len(separator) > MAX_FOLDED_LENGTH:
            return None
        if 1 + sum(len(value[1]) for value in folded) > MAX_FOLDED_PARTS:
            return None
        text = separator.join(value[0] for value in folded)
        parts: list[str] = [separator]
        for value in folded:
            parts.extend(value[1])
        return text, parts
    return None


class _ModuleScan:
    def __init__(self, tree: ast.Module, module_name: str, filename: str):
        self.tree = tree
        self.module_name = module_name
        self.filename = filename
        self.hits: list[tuple[int, tuple[str, ...]]] = []

    def _expr(self, node: ast.AST, names: dict[str, str], statement_line: int) -> tuple[str, list[str]] | None:
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            local_names = dict(names)
            for generator in node.generators:
                self._expr(generator.iter, local_names, statement_line)
                self._expr(generator.target, local_names, statement_line)
                for name in self._target_names(generator.target):
                    local_names.pop(name, None)
                for condition in generator.ifs:
                    self._expr(condition, local_names, statement_line)
            if isinstance(node, ast.DictComp):
                self._expr(node.key, local_names, statement_line)
                self._expr(node.value, local_names, statement_line)
            else:
                self._expr(node.elt, local_names, statement_line)
            return None
        if isinstance(node, ast.NamedExpr):
            self._expr(node.value, names, statement_line)
            for name in self._target_names(node.target):
                names.pop(name, None)
            return None
        folded = _fold(node, names)
        if folded is not None:
            # A folded outer expression owns its descendants; don't count them twice.
            if not isinstance(node, ast.Constant) and not isinstance(node, ast.Name):
                self._check_line(statement_line, folded)
            return folded
        for child in ast.iter_child_nodes(node):
            self._expr(child, names, statement_line)
        return None

    def _check_line(self, line: int, folded: tuple[str, list[str]]) -> None:
        text, parts = folded
        if _emitted_sensitive(text) and not any(_emitted_sensitive(part) for part in parts):
            rules = tuple(sorted({rule for rule, _line in _screen_hits(text)}))
            if rules:
                self.hits.append((line, rules))

    def _target_names(self, target: ast.AST) -> Iterable[str]:
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, ast.Starred):
            yield from self._target_names(target.value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                yield from self._target_names(item)

    def _pattern_names(self, pattern: ast.AST) -> Iterable[str]:
        pattern_as = getattr(ast, "MatchAs", None)
        pattern_star = getattr(ast, "MatchStar", None)
        pattern_mapping = getattr(ast, "MatchMapping", None)
        if pattern_as is not None and isinstance(pattern, pattern_as) and pattern.name:
            yield pattern.name
        if pattern_star is not None and isinstance(pattern, pattern_star) and pattern.name:
            yield pattern.name
        if pattern_mapping is not None and isinstance(pattern, pattern_mapping) and pattern.rest:
            yield pattern.rest
        pattern_base = getattr(ast, "pattern", None)
        if pattern_base is not None:
            for child in ast.iter_child_nodes(pattern):
                if isinstance(child, pattern_base):
                    yield from self._pattern_names(child)

    def _block(self, statements: list[ast.stmt], names: dict[str, str]) -> None:
        for statement in statements:
            line = getattr(statement, "lineno", 1)
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorator_line = min((getattr(item, "lineno", line) for item in statement.decorator_list), default=line)
                for expr in statement.decorator_list:
                    self._expr(expr, names, decorator_line)
                for expr in [*statement.args.defaults, *statement.args.kw_defaults]:
                    if expr is not None:
                        self._expr(expr, names, line)
                arguments = [*statement.args.posonlyargs, *statement.args.args, *statement.args.kwonlyargs]
                if statement.args.vararg is not None:
                    arguments.append(statement.args.vararg)
                if statement.args.kwarg is not None:
                    arguments.append(statement.args.kwarg)
                for argument in arguments:
                    if argument.annotation is not None:
                        self._expr(argument.annotation, names, line)
                if statement.returns is not None:
                    self._expr(statement.returns, names, line)
                self._block(statement.body, {})
                names.pop(statement.name, None)
                continue
            if isinstance(statement, ast.ClassDef):
                decorator_line = min((getattr(item, "lineno", line) for item in statement.decorator_list), default=line)
                for expr in statement.decorator_list:
                    self._expr(expr, names, decorator_line)
                for expr in [*statement.bases, *(keyword.value for keyword in statement.keywords)]:
                    self._expr(expr, names, line)
                self._block(statement.body, {})
                names.pop(statement.name, None)
                continue
            if isinstance(statement, ast.Assign):
                folded = self._expr(statement.value, names, line)
                for target in statement.targets:
                    self._expr(target, names, line)
                    for name in self._target_names(target):
                        if folded is not None:
                            names[name] = folded[0]
                        else:
                            names.pop(name, None)
                continue
            if isinstance(statement, ast.AnnAssign):
                self._expr(statement.annotation, names, line)
                self._expr(statement.target, names, line)
                if statement.value is not None:
                    folded = self._expr(statement.value, names, line)
                    for name in self._target_names(statement.target):
                        if folded is not None:
                            names[name] = folded[0]
                        else:
                            names.pop(name, None)
                continue
            if isinstance(statement, ast.AugAssign):
                self._expr(statement.target, names, line)
                expression = ast.copy_location(ast.BinOp(statement.target, statement.op, statement.value), statement)
                folded = self._expr(expression, names, line)
                for name in self._target_names(statement.target):
                    if folded is not None:
                        names[name] = folded[0]
                    else:
                        names.pop(name, None)
                continue
            if isinstance(statement, ast.Expr):
                self._expr(statement.value, names, line)
                continue
            if isinstance(statement, ast.Delete):
                for target in statement.targets:
                    self._expr(target, names, line)
                    for name in self._target_names(target):
                        names.pop(name, None)
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    names.pop(alias.asname or alias.name.split(".")[0], None)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                self._expr(statement.iter, names, line)
                self._expr(statement.target, names, line)
                for name in self._target_names(statement.target):
                    names.pop(name, None)
                self._block(statement.body, names)
                self._block(statement.orelse, names)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    self._expr(item.context_expr, names, line)
                    if item.optional_vars is not None:
                        self._expr(item.optional_vars, names, line)
                        for name in self._target_names(item.optional_vars):
                            names.pop(name, None)
                self._block(statement.body, names)
                continue
            try_star = getattr(ast, "TryStar", None)
            try_types = (ast.Try,) if try_star is None else (ast.Try, try_star)
            if isinstance(statement, try_types):
                self._block(statement.body, names)
                for handler in statement.handlers:
                    if handler.type is not None:
                        self._expr(handler.type, names, line)
                    if handler.name:
                        names.pop(handler.name, None)
                    self._block(handler.body, names)
                    if handler.name:
                        names.pop(handler.name, None)
                self._block(statement.orelse, names)
                self._block(statement.finalbody, names)
                continue
            match_type = getattr(ast, "Match", None)
            if match_type is not None and isinstance(statement, match_type):
                self._expr(statement.subject, names, line)
                for case in statement.cases:
                    self._expr(case.pattern, names, line)
                    for name in self._pattern_names(case.pattern):
                        names.pop(name, None)
                    if case.guard is not None:
                        self._expr(case.guard, names, line)
                    self._block(case.body, names)
                continue
            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.expr):
                    self._expr(child, names, line)
                elif isinstance(child, ast.stmt):
                    self._block([child], names)

    def run(self) -> list[tuple[int, tuple[str, ...]]]:
        self._block(self.tree.body, {})
        return sorted(self.hits)


def _module_paths() -> list[Path]:
    return sorted((LLMTOOL / "tests").glob("*.py")) + sorted((REPO / "scripts").glob("test_*.py"))


def _labelled_lines(lines: list[str], line: int) -> bool:
    return line > 1 and lines[line - 2].strip() == LABEL


def analyze_source(source: str, module_name: str, filename: str) -> list[str]:
    """Return text-free findings for one already-read module source."""
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()
    findings = []
    try:
        hits = _ModuleScan(tree, module_name, filename).run()
    except (RecursionError, MemoryError):
        raise ValueError("input exceeds fixture-check limits") from None
    for line, rules in hits:
        if module_name in SCREEN_TOOL_TEST_MODULES and _labelled_lines(lines, line):
            continue
        findings.append(f"{filename}:{line}: {','.join(rules)}")
    return findings


def analyze_path(path: Path, module_name: str) -> list[str]:
    source = path.read_text(encoding="utf-8")
    return analyze_source(source, module_name, path.name)


def compare_counts(counts: dict[str, int], baseline: dict[str, int]) -> tuple[bool, list[str]]:
    findings = []
    exceeded = False
    for module in sorted(set(counts) | set(baseline)):
        count = counts.get(module, 0)
        previous = baseline.get(module, 0)
        if count > previous:
            exceeded = True
        elif count < previous:
            findings.append(f"{module}: baseline can shrink ({previous} to {count})")
    return exceeded, findings


def _read_baseline() -> dict[str, int]:
    value = json.loads(BASELINE.read_text(encoding="utf-8"))
    if (type(value) is not dict or any(type(name) is not str or type(count) is not int or count < 0
                                       for name, count in value.items())):
        raise ValueError("invalid baseline")
    return value


def check(*, write_baseline: bool = False) -> tuple[int, list[str]]:
    counts: dict[str, int] = {}
    violations: list[str] = []
    for path in _module_paths():
        try:
            findings = analyze_path(path, path.stem)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError(f"could not parse {path.relative_to(REPO)} ({type(exc).__name__})") from None
        except (RecursionError, MemoryError):
            raise ValueError("input exceeds fixture-check limits") from None
        # analyze_path uses a display name without exposing source text.
        findings = [item.replace(path.name + ":", str(path.relative_to(REPO)) + ":", 1)
                    for item in findings]
        count = len(findings)
        violations.extend(findings)
        counts[path.name] = count
    if write_baseline:
        BASELINE.write_text(json.dumps(dict(sorted(counts.items())), indent=2) + "\n", encoding="utf-8")
        return 0, []
    baseline = _read_baseline()
    exceeded, baseline_findings = compare_counts(counts, baseline)
    violations.extend(baseline_findings)
    return int(exceeded), violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)
    try:
        status, violations = check(write_baseline=args.write_baseline)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"fixture-check: {exc}")
        return 2
    for item in violations:
        print(item)
    print(f"fixture-check: {len(violations)} violation(s)")
    return status


if __name__ == "__main__":
    sys.exit(main())
