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


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    return None


def _fold(node: ast.AST, names: dict[str, str]) -> tuple[str, list[str]] | None:
    """Return a foldable string and its independent text parts."""
    literal = _constant_string(node)
    if literal is not None:
        return literal, [literal]
    if isinstance(node, ast.Name):
        value = names.get(node.id)
        return (value, [value]) if value is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left, names), _fold(node.right, names)
        if left is None or right is None:
            return None
        return left[0] + right[0], left[1] + right[1]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if isinstance(node.right, ast.Constant) and type(node.right.value) is int:
            base, count = _fold(node.left, names), node.right.value
        elif isinstance(node.left, ast.Constant) and type(node.left.value) is int:
            base, count = _fold(node.right, names), node.left.value
        else:
            return None
        if base is None or abs(count) > 10000:
            return None
        return base[0] * count, base[1]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join" and len(node.args) == 1
            and not node.keywords):
        separator = _constant_string(node.func.value)
        sequence = node.args[0]
        if separator is None or not isinstance(sequence, (ast.Tuple, ast.List)):
            return None
        values = [_fold(item, names) for item in sequence.elts]
        if any(value is None for value in values):
            return None
        folded = [value for value in values if value is not None]
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

    def _check(self, node: ast.AST, folded: tuple[str, list[str]]) -> None:
        text, parts = folded
        if _emitted_sensitive(text) and not any(_emitted_sensitive(part) for part in parts):
            rules = tuple(sorted({rule for rule, _line in _screen_hits(text)}))
            if rules:
                self.hits.append((getattr(node, "lineno", 1), rules))

    def _expr(self, node: ast.AST, names: dict[str, str]) -> tuple[str, list[str]] | None:
        folded = _fold(node, names)
        if folded is not None:
            # A folded outer expression owns its descendants; don't count them twice.
            if not isinstance(node, ast.Constant) and not isinstance(node, ast.Name):
                self._check(node, folded)
            return folded
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expr(child, names)
        return None

    def _target_names(self, target: ast.AST) -> Iterable[str]:
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                yield from self._target_names(item)

    def _block(self, statements: list[ast.stmt], names: dict[str, str]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(statement)
                # Decorators/defaults execute in the containing scope.
                for expr in [*statement.decorator_list, *statement.args.defaults,
                             *statement.args.kw_defaults]:
                    if expr is not None:
                        self._expr(expr, names)
                continue
            if isinstance(statement, ast.Assign):
                folded = self._expr(statement.value, names)
                for target in statement.targets:
                    for name in self._target_names(target):
                        if folded is not None:
                            names[name] = folded[0]
                        else:
                            names.pop(name, None)
                continue
            if isinstance(statement, ast.AnnAssign):
                if statement.value is not None:
                    folded = self._expr(statement.value, names)
                    for name in self._target_names(statement.target):
                        if folded is not None:
                            names[name] = folded[0]
                        else:
                            names.pop(name, None)
                continue
            if isinstance(statement, ast.AugAssign):
                folded = self._expr(ast.BinOp(statement.target, statement.op, statement.value), names)
                for name in self._target_names(statement.target):
                    if folded is not None:
                        names[name] = folded[0]
                    else:
                        names.pop(name, None)
                continue
            if isinstance(statement, ast.Expr):
                self._expr(statement.value, names)
                continue
            # Visit nested blocks in source order. Assignments inside a branch
            # become available only to later source statements in this function.
            for field, value in ast.iter_fields(statement):
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, ast.stmt):
                            self._block([item], names)
                        elif isinstance(item, ast.expr):
                            self._expr(item, names)
                elif isinstance(value, ast.expr):
                    self._expr(value, names)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        self._block(node.body, {})

    def run(self) -> list[tuple[int, tuple[str, ...]]]:
        # Module-level test setup is not resolved across functions; only scan
        # function bodies, whose locals have the required same-function scope.
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(node)
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._function(member)
        return sorted(self.hits)


def _module_paths() -> list[Path]:
    return sorted((LLMTOOL / "tests").glob("*.py")) + sorted((LLMTOOL / "scripts").glob("test_*.py"))


def _labelled_lines(lines: list[str], line: int) -> bool:
    return line > 1 and lines[line - 2].strip() == LABEL


def analyze_source(source: str, module_name: str, filename: str) -> list[str]:
    """Return text-free findings for one already-read module source."""
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()
    findings = []
    for line, rules in _ModuleScan(tree, module_name, filename).run():
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
