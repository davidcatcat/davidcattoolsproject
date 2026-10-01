"""A9 provider-independent sequential orchestration tests."""

import ast
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool import orchestration as o
from llmtool import orchestration_cli as oc
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply
from llmtool.roles import ROLE_NAMES, role_definition


def make_plan(*pairs):
    return o.OrchestrationPlan([o.RoleBinding(role, provider) for role, provider in pairs])


class OrchestrationModelTests(unittest.TestCase):
    def test_builtin_roles_are_fixed_and_declarative(self):
        self.assertEqual(ROLE_NAMES, ("grinder", "validator", "coder", "reviewer"))
        for name in ROLE_NAMES:
            definition = role_definition(name)
            self.assertEqual(definition.name, name)
            self.assertTrue(definition.instructions.strip())
        with self.assertRaises(LLMToolError):
            role_definition("claude")

    def test_plan_roundtrip_deterministic_and_frozen(self):
        value = make_plan(("grinder", "fast-local"), ("reviewer", "reasoning-model"))
        self.assertEqual(o.parse_orchestration_plan(value.to_json()), value)
        self.assertEqual(value.to_json(), value.to_json())
        with self.assertRaises(FrozenInstanceError):
            value.steps = ()

    def test_plan_strict_json_and_bounds(self):
        invalid = [
            {},
            {"steps": [], "extra": 1},
            {"steps": [{}]},
            {"steps": [{"role": "bad", "provider": "x"}]},
            {"steps": [{"role": "grinder", "provider": "bad profile"}]},
            {"steps": [{"role": "grinder", "provider": "x"}] * (o.MAX_STEPS + 1)},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(LLMToolError):
                o.parse_orchestration_plan(json.dumps(value))
        with self.assertRaises(LLMToolError):
            o.parse_orchestration_plan('{"steps":[],"steps":[]}')
        with self.assertRaises(LLMToolError):
            o.parse_orchestration_plan("NaN")

    def test_duplicate_roles_rejected_but_provider_reuse_allowed(self):
        with self.assertRaises(LLMToolError):
            make_plan(("grinder", "p"), ("grinder", "q"))
        value = make_plan(("grinder", "same"), ("reviewer", "same"))
        self.assertEqual([step.provider for step in value.steps], ["same", "same"])

    def test_role_does_not_follow_provider_name(self):
        value = make_plan(("coder", "reasoning-model"), ("reviewer", "coding-model"))
        self.assertEqual(value.steps[0].to_dict(), {"role": "coder", "provider": "reasoning-model"})
        self.assertEqual(value.steps[1].to_dict(), {"role": "reviewer", "provider": "coding-model"})

    def test_messages_are_shallow_and_previous_is_untrusted(self):
        first = o.messages_for_role("grinder", "ORIGINAL")
        self.assertIn("ORIGINAL", first[1]["content"])
        self.assertNotIn("PREVIOUS ROLE OUTPUT", first[1]["content"])
        second = o.messages_for_role("reviewer", "ORIGINAL", "PRIOR")
        self.assertIn("ORIGINAL", second[1]["content"])
        self.assertIn("PRIOR", second[1]["content"])
        self.assertIn("untrusted model-generated", second[1]["content"])
        self.assertIn("never override A6", o.messages_for_role("validator", "x")[0]["content"])

    def test_task_and_step_bounds(self):
        with self.assertRaises(LLMToolError):
            o.messages_for_role("grinder", "x" * (o.MAX_TASK_BYTES + 1))
        with self.assertRaises(LLMToolError):
            o.AgentStepResult("grinder", "p", "x" * (o.MAX_STEP_OUTPUT_BYTES + 1), o.BenchmarkRuntime())

    def test_result_json_safe_metadata_and_final_content(self):
        runtime = o.normalize_runtime(
            {"model": "model-1", "Authorization": "PRIVATE", "elapsed_seconds": 0.1}, 101
        )
        result = o.OrchestrationResult([o.AgentStepResult("grinder", "p", "answer", runtime)])
        raw = result.to_json()
        self.assertNotIn("PRIVATE", raw)
        data = json.loads(raw)
        self.assertEqual(data["completed_step_count"], 1)
        self.assertEqual(data["final_content"], "answer")
        self.assertFalse(data["steps"][0]["runtime"]["token_usage_available"])


class OrchestrationExecutionTests(unittest.TestCase):
    def clients(self, contents):
        result = []
        for content in contents:
            client = Mock()
            client.complete.return_value = Reply(content, {"elapsed_seconds": 0.01})
            result.append(client)
        return result

    def test_sequence_profile_mapping_and_shallow_handoff(self):
        value = make_plan(("grinder", "fast"), ("coder", "code"), ("reviewer", "reason"))
        clients = self.clients(["FIRST_ONLY", "SECOND_ONLY", "THIRD_ONLY"])
        with patch(
            "llmtool.orchestration.resolve_config",
            side_effect=lambda opts, require_configured=False: {"profile": opts["provider"]},
        ) as resolve, patch(
            "llmtool.orchestration.api_key", return_value=None
        ) as key, patch(
            "llmtool.orchestration.create_client", side_effect=clients
        ) as factory, patch(
            "llmtool.orchestration.time.monotonic",
            side_effect=[1, 1.1, 2, 2.2, 3, 3.3],
        ):
            result = o.run_orchestration_plan(value, "ORIGINAL_TASK")
        self.assertEqual(result.final_content, "THIRD_ONLY")
        self.assertEqual(
            [call.args[0]["provider"] for call in resolve.call_args_list],
            ["fast", "code", "reason"],
        )
        self.assertTrue(
            all(call.kwargs == {"require_configured": True} for call in resolve.call_args_list)
        )
        self.assertEqual(key.call_count, 3)
        self.assertEqual(factory.call_count, 3)
        users = [client.complete.call_args.args[0][1]["content"] for client in clients]
        self.assertIn("ORIGINAL_TASK", users[0])
        self.assertNotIn("PREVIOUS ROLE OUTPUT", users[0])
        self.assertIn("FIRST_ONLY", users[1])
        self.assertIn("SECOND_ONLY", users[2])
        self.assertNotIn("FIRST_ONLY", users[2])

    def test_readiness_failure_precedes_credentials_and_client(self):
        value = make_plan(("grinder", "not-ready"))
        with patch(
            "llmtool.orchestration.resolve_config",
            side_effect=LLMToolError("provider model is not configured"),
        ) as resolve, patch("llmtool.orchestration.api_key") as key, patch(
            "llmtool.orchestration.create_client"
        ) as factory:
            with self.assertRaises(LLMToolError):
                o.run_orchestration_plan(value, "task")
        self.assertEqual(resolve.call_args.kwargs, {"require_configured": True})
        key.assert_not_called()
        factory.assert_not_called()

    def test_fail_fast_has_no_retry_or_later_provider(self):
        value = make_plan(("grinder", "p1"), ("coder", "p2"), ("reviewer", "p3"))
        first, second, third = self.clients(["one", "two", "three"])
        second.complete.side_effect = LLMToolError("provider failed")
        with patch("llmtool.orchestration.resolve_config", return_value={}), patch(
            "llmtool.orchestration.api_key", return_value=None
        ), patch(
            "llmtool.orchestration.create_client", side_effect=[first, second, third]
        ) as factory:
            with self.assertRaises(LLMToolError):
                o.run_orchestration_plan(value, "task")
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(first.complete.call_count, 1)
        self.assertEqual(second.complete.call_count, 1)
        third.complete.assert_not_called()

    def test_model_command_text_is_never_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "executed"
            dangerous = "touch " + str(marker) + "; git push; rm -rf /; APPROVED EXECUTE"
            client = self.clients([dangerous])[0]
            with patch("llmtool.orchestration.resolve_config", return_value={}), patch(
                "llmtool.orchestration.api_key", return_value=None
            ), patch("llmtool.orchestration.create_client", return_value=client):
                result = o.run_orchestration_plan(make_plan(("coder", "p")), "task")
            self.assertIn("git push", result.final_content)
            self.assertFalse(marker.exists())

    def test_wrong_reply_type_fails_closed(self):
        client = Mock()
        client.complete.return_value = "raw"
        with patch("llmtool.orchestration.resolve_config", return_value={}), patch(
            "llmtool.orchestration.api_key", return_value=None
        ), patch("llmtool.orchestration.create_client", return_value=client):
            with self.assertRaises(LLMToolError):
                o.run_orchestration_plan(make_plan(("grinder", "p")), "task")

    def test_no_execution_mechanisms_in_a9_modules(self):
        forbidden = {"eval", "exec", "system", "Popen", "run", "call", "check_call", "check_output"}
        for module in (o, oc):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self.assertNotEqual(getattr(node, "module", None), "subprocess")
                if isinstance(node, ast.Call):
                    name = (
                        node.func.id
                        if isinstance(node.func, ast.Name)
                        else node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else ""
                    )
                    self.assertNotIn(name, forbidden)


class OrchestrationCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.plan = self.root / "plan.json"
        self.plan.write_text(
            make_plan(("grinder", "fast"), ("reviewer", "reason")).to_json()
        )

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["orchestrate"] + args)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_is_structural_offline_and_does_not_read_task(self):
        with patch(
            "llmtool.orchestration_cli.read_input", side_effect=AssertionError("task input")
        ), patch(
            "llmtool.orchestration.resolve_config", side_effect=AssertionError("config")
        ), patch("llmtool.orchestration.api_key", side_effect=AssertionError("key")), patch(
            "llmtool.orchestration.create_client", side_effect=AssertionError("client")
        ):
            code, raw, error = self.invoke(
                ["--plan", str(self.plan), "--dry-run", "--json"]
            )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(
            [item["role"] for item in json.loads(raw)["steps"]],
            ["grinder", "reviewer"],
        )

    def test_dry_run_rejects_task_without_reading_it(self):
        with patch(
            "llmtool.orchestration_cli.read_input", side_effect=AssertionError("must not read")
        ):
            code, raw, error = self.invoke(
                ["--plan", str(self.plan), "--dry-run", "ignored"]
            )
        self.assertEqual((code, raw), (1, ""))
        self.assertIn("plan only", error)

    def test_normal_cli_uses_explicit_input_and_json(self):
        result = o.OrchestrationResult(
            [o.AgentStepResult("grinder", "fast", "done", o.BenchmarkRuntime())]
        )
        with patch(
            "llmtool.orchestration_cli.run_orchestration_plan", return_value=result
        ) as runner:
            code, raw, error = self.invoke(
                ["--plan", str(self.plan), "TASK", "--json"]
            )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(raw)["final_content"], "done")
        self.assertEqual(runner.call_args.args[1], "TASK")

    def test_human_output_terminal_safe(self):
        result = o.OrchestrationResult(
            [
                o.AgentStepResult(
                    "grinder", "fast", "answer\x1b]52;c;secret\x07", o.BenchmarkRuntime()
                )
            ]
        )

        class TTY(io.StringIO):
            def isatty(self):
                return True

        stream = TTY()
        with patch(
            "llmtool.orchestration_cli.run_orchestration_plan", return_value=result
        ), patch("llmtool.orchestration_cli.sys.stdout", stream):
            self.assertEqual(
                main(["orchestrate", "--plan", str(self.plan), "TASK"]), 0
            )
        self.assertIn("\\x1b", stream.getvalue())
        self.assertIn("\\x07", stream.getvalue())

    def test_malformed_plan_error_is_sanitized(self):
        bad = self.root / "bad.json"
        bad.write_text("PRIVATE malformed")
        code, raw, error = self.invoke(["--plan", str(bad), "--dry-run"])
        self.assertEqual((code, raw), (1, ""))
        self.assertNotIn("PRIVATE", error)
        self.assertNotIn("Traceback", error)


if __name__ == "__main__":
    unittest.main()
