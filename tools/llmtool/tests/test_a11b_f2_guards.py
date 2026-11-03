"""Pin the narrow, validate-only surface authorized for #97 F2."""

import ast
from pathlib import Path
import unittest

from llmtool import action_contract, authorization_policy, cli


FAILURE = "#97 F2 NOT AUTHORIZED: guarded surface widened"
PACKAGE = Path(__file__).resolve().parents[1] / "llmtool"


def _subparser_choices(parser):
    for action in parser._actions:
        if isinstance(action, type(parser._subparsers._group_actions[0])):
            return action.choices
    return {}


class F2GuardTests(unittest.TestCase):
    def test_enum_members_are_limited_to_the_approved_surface(self):
        self.assertEqual({member.value for member in action_contract.GitOperation}, {"ADD"}, FAILURE)
        self.assertEqual(
            {member.value for member in action_contract.ActionKind},
            {"FILE_CREATE", "FILE_WRITE", "FILE_DELETE", "GIT_OPERATION"},
            FAILURE,
        )
        self.assertEqual(
            {member.value for member in authorization_policy.EffectKind},
            {"FAKE_FILE_CREATE", "PROVIDER_REVIEW_CALL", "REVIEW_NOTIFICATION"},
            FAILURE,
        )

    def test_only_review_authority_has_a_host_issuer_subclass(self):
        subclasses = []
        forbidden_issuers = []
        for path in sorted(PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "issuer_class":
                    for returned in (child for child in ast.walk(node) if isinstance(child, ast.Return)):
                        value = returned.value
                        if (isinstance(value, ast.Attribute)
                                and value.attr in {"OWNER_P0", "POLICY_CONTRACT"}):
                            forbidden_issuers.append((path.name, value.attr))
                if not isinstance(node, ast.ClassDef):
                    continue
                if any(
                    isinstance(base, ast.Name) and base.id == "HostIssuer"
                    or isinstance(base, ast.Attribute) and base.attr == "HostIssuer"
                    for base in node.bases
                ):
                    subclasses.append((path.name, node.name, node))

        self.assertEqual(
            [(filename, name) for filename, name, _ in subclasses],
            [("review_authority.py", "ProductionReviewIssuer")],
            FAILURE,
        )
        self.assertEqual(forbidden_issuers, [], FAILURE)
        issuer_methods = [
            node for node in subclasses[0][2].body
            if isinstance(node, ast.FunctionDef) and node.name == "issuer_class"
        ]
        self.assertEqual(len(issuer_methods), 1, FAILURE)
        issuer_class = issuer_methods[0]
        returns = [node for node in ast.walk(issuer_class) if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1, FAILURE)
        self.assertEqual(
            ast.dump(returns[0].value),
            ast.dump(ast.Attribute(value=ast.Name(id="IssuerClass", ctx=ast.Load()),
                                   attr="PRODUCTION_REVIEW", ctx=ast.Load())),
            FAILURE,
        )

    def test_action_and_approval_cli_are_validate_only(self):
        parser = cli.parser_for_cli()
        commands = _subparser_choices(parser)
        for command in ("action", "approval"):
            with self.subTest(command=command):
                children = _subparser_choices(commands[command])
                self.assertEqual(set(children), {"validate"}, FAILURE)

        action_args = parser.parse_args(["action", "validate", "--file", "x"])
        approval_args = parser.parse_args([
            "approval", "validate", "--action", "a", "--approval", "b",
            "--state", "s", "--nonce", "0" * 64, "--now", "1",
        ])
        self.assertEqual((action_args.command, action_args.action), ("action", "validate"), FAILURE)
        self.assertEqual((approval_args.command, approval_args.action), ("approval", "validate"), FAILURE)

        # The parser produces command selections; main is the only dispatcher.
        # Pin its dispatch branch without calling main or executing the command.
        main = next(node for node in ast.parse(
            (PACKAGE / "cli.py").read_text(encoding="utf-8")
        ).body if isinstance(node, ast.FunctionDef) and node.name == "main")
        routed = any(
            isinstance(node, ast.If)
            and ast.dump(node.test) == ast.dump(ast.Compare(
                left=ast.Attribute(value=ast.Name(id="args", ctx=ast.Load()),
                                   attr="command", ctx=ast.Load()),
                ops=[ast.In()],
                comparators=[ast.Tuple(elts=[
                    ast.Constant(value="action"), ast.Constant(value="approval")
                ], ctx=ast.Load())],
            ))
            and any(isinstance(child, ast.Return) and isinstance(child.value, ast.Call)
                    and isinstance(child.value.func, ast.Name)
                    and child.value.func.id == "run_action_contract"
                    for child in ast.walk(node))
            for node in ast.walk(main)
        )
        self.assertTrue(routed, FAILURE)


if __name__ == "__main__":
    unittest.main()
