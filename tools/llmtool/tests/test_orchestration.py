"""A9 provider-independent sequential orchestration tests."""

import ast
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool import orchestration as o
from llmtool import orchestration_cli as oc
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply
from llmtool.roles import ROLE_NAMES, role_definition
from llmtool.providers import client_type


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

    def test_plan_rejects_empty_malformed_nested_and_invalid_unicode(self):
        for raw in ('{"steps":[]}', '{', 'Infinity', '{"steps":[{"role":"grinder",'
                    '"role":"coder","provider":"p"}]}', '[' * 7 + '0' + ']' * 7,
                    '{"steps":[]} ' + 'x' * o.JSON_LIMIT, '\ud800'):
            with self.subTest(raw=raw[:60]), self.assertRaises(LLMToolError):
                o.parse_orchestration_plan(raw)

    def test_models_detach_lists_and_reject_untyped_values(self):
        bindings = [o.RoleBinding("grinder", "p")]
        plan = o.OrchestrationPlan(bindings)
        bindings.clear()
        self.assertEqual(len(plan.steps), 1)
        for constructor, value in ((o.OrchestrationPlan, [{}]),
                                   (o.OrchestrationResult, [{}]),
                                   (o.OrchestrationResult, [])):
            with self.assertRaises(LLMToolError):
                constructor(value)
        step = o.AgentStepResult("grinder", "p", "answer", o.BenchmarkRuntime())
        for obj, field, value in ((step, "content", "changed"),
                                  (plan.steps[0], "provider", "changed"),
                                  (o.OrchestrationResult([step]), "steps", ())):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)
        with self.assertRaises(LLMToolError):
            replace(step, runtime={})

    def test_utf8_byte_limits_and_json_escaping(self):
        # Character count alone cannot enforce the byte bound.
        with self.assertRaises(LLMToolError):
            o.AgentStepResult("grinder", "p", "日" * (o.MAX_STEP_OUTPUT_BYTES // 3 + 1),
                             o.BenchmarkRuntime())
        step = o.AgentStepResult("grinder", "p", "\x00" * o.MAX_STEP_OUTPUT_BYTES,
                                o.BenchmarkRuntime())
        with self.assertRaisesRegex(LLMToolError, "JSON exceeds"):
            o.OrchestrationResult([step])

    def test_aggregate_byte_budget_and_duplicate_results(self):
        steps = [o.AgentStepResult(role, "p", "x" * 16, o.BenchmarkRuntime())
                 for role in ROLE_NAMES]
        with patch.object(o, "MAX_TOTAL_OUTPUT_BYTES", 63):
            with self.assertRaisesRegex(LLMToolError, "total output"):
                o.OrchestrationResult(steps)
        with self.assertRaises(LLMToolError):
            o.OrchestrationResult([steps[0], steps[0]])


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
            "llmtool.orchestration.resolve_profile_config",
            side_effect=lambda name, require_configured=False: {"profile": name},
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
            [call.args[0] for call in resolve.call_args_list],
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
            "llmtool.orchestration.resolve_profile_config",
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
        with patch("llmtool.orchestration.resolve_profile_config", return_value={}), patch(
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
            with patch("llmtool.orchestration.resolve_profile_config", return_value={}), patch(
                "llmtool.orchestration.api_key", return_value=None
            ), patch("llmtool.orchestration.create_client", return_value=client):
                result = o.run_orchestration_plan(make_plan(("coder", "p")), "task")
            self.assertIn("git push", result.final_content)
            self.assertFalse(marker.exists())

    def test_wrong_reply_type_fails_closed(self):
        client = Mock()
        client.complete.return_value = "raw"
        with patch("llmtool.orchestration.resolve_profile_config", return_value={}), patch(
            "llmtool.orchestration.api_key", return_value=None
        ), patch("llmtool.orchestration.create_client", return_value=client):
            with self.assertRaises(LLMToolError):
                o.run_orchestration_plan(make_plan(("grinder", "p")), "task")

    def test_serialized_overflow_stops_before_next_profile(self):
        clients = self.clients(["\x00" * o.MAX_STEP_OUTPUT_BYTES] * 2)
        with patch.object(o, "resolve_profile_config", return_value={}) as resolve, \
                patch.object(o, "api_key", return_value=None) as key, \
                patch.object(o, "create_client", side_effect=clients) as factory:
            with self.assertRaisesRegex(LLMToolError, "JSON exceeds"):
                o.run_orchestration_plan(make_plan(("grinder", "first"),
                                                   ("reviewer", "later")), "task")
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(key.call_count, 1)
        self.assertEqual(factory.call_count, 1)
        clients[0].complete.assert_called_once()
        clients[1].complete.assert_not_called()

    def test_total_overflow_stops_before_next_profile(self):
        clients = self.clients(["x" * 16, "y" * 16, "later"])
        with patch.object(o, "MAX_TOTAL_OUTPUT_BYTES", 31), \
                patch.object(o, "resolve_profile_config", return_value={}) as resolve, \
                patch.object(o, "api_key", return_value=None), \
                patch.object(o, "create_client", side_effect=clients):
            with self.assertRaisesRegex(LLMToolError, "total output"):
                o.run_orchestration_plan(make_plan(("grinder", "p"), ("coder", "p"),
                                                   ("reviewer", "p")), "task")
        self.assertEqual(resolve.call_count, 2)
        clients[2].complete.assert_not_called()

    def test_invalid_reply_content_stops_later_steps(self):
        for content in (None, {}, "", " \n", "\ud800",
                        "x" * (o.MAX_STEP_OUTPUT_BYTES + 1)):
            clients = self.clients([content, "later"])
            with self.subTest(content_type=type(content).__name__), \
                    patch.object(o, "resolve_profile_config", return_value={}), \
                    patch.object(o, "api_key", return_value=None), \
                    patch.object(o, "create_client", side_effect=clients) as factory:
                with self.assertRaises(LLMToolError):
                    o.run_orchestration_plan(make_plan(("grinder", "p"),
                                                       ("reviewer", "p")), "task")
                self.assertEqual(factory.call_count, 1)
                clients[1].complete.assert_not_called()

    def test_no_execution_mechanisms_in_a9_modules(self):
        forbidden = {"eval", "exec", "system", "Popen", "run", "call", "check_call", "check_output"}
        import llmtool.roles as roles
        for module in (o, oc, roles):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self.assertNotEqual(getattr(node, "module", None), "subprocess")
                    if isinstance(node, ast.Import):
                        self.assertFalse(any(alias.name == "subprocess" for alias in node.names))
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
            "llmtool.orchestration.resolve_profile_config", side_effect=AssertionError("config")
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

    def test_explicit_text_and_file_never_read_stdin(self):
        class UnreadableStdin:
            def isatty(self):
                return True
            @property
            def buffer(self):
                raise AssertionError("explicit input touched stdin")

        taskfile = self.root / "task.txt"
        taskfile.write_text("FILE_TASK", encoding="utf-8")
        result = o.OrchestrationResult([
            o.AgentStepResult("grinder", "fast", "done", o.BenchmarkRuntime())])
        for source, expected in ((["DIRECT_TASK"], "DIRECT_TASK"),
                                 (["--file", str(taskfile)], "FILE_TASK")):
            with patch("sys.stdin", UnreadableStdin()), \
                    patch.object(oc, "run_orchestration_plan", return_value=result) as runner:
                code, raw, error = self.invoke(["--plan", str(self.plan), *source, "--json"])
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(runner.call_args.args[1], expected)
            self.assertEqual(json.loads(raw)["final_content"], "done")

    def test_conflicting_or_empty_input_prevents_providers(self):
        taskfile = self.root / "task.txt"
        taskfile.write_text("task")
        for source in (["task", "--file", str(taskfile)], [""]):
            with patch.object(oc, "run_orchestration_plan") as runner:
                code, raw, error = self.invoke(["--plan", str(self.plan), *source])
            self.assertEqual((code, raw), (1, ""))
            self.assertTrue(error)
            runner.assert_not_called()

    def test_second_step_failure_has_no_partial_stdout(self):
        clients = [Mock(), Mock()]
        clients[0].complete.return_value = Reply("FIRST_ANSWER", {})
        clients[1].complete.side_effect = LLMToolError("provider failed")
        with patch.object(o, "resolve_profile_config", return_value={}), \
                patch.object(o, "api_key", return_value=None), \
                patch.object(o, "create_client", side_effect=clients):
            code, raw, error = self.invoke(["--plan", str(self.plan), "TASK", "--json"])
        self.assertEqual((code, raw), (1, ""))
        self.assertNotIn("FIRST_ANSWER", error)
        for client in clients:
            client.complete.assert_called_once()


class OrchestrationProfileIntegrationTests(unittest.TestCase):
    """Exercise real resolution/factories/wire normalization, replacing transport I/O."""

    def setUp(self):
        import yaml
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / "config.yaml"
        self.profiles = {
            "reasoning": {"type": "openai", "model": "openai-test", "api_key_env": "FAKE_KEY"},
            "code": {"type": "openai-compatible", "model": "compatible-test",
                     "base_url": "https://gateway.example"},
            "local-codex": {"type": "codex-cli"},
            "local-claude": {"type": "claude-cli"},
            "unused": {"type": "openai", "model": "unused", "api_key_env": "UNSET_KEY"},
        }
        self.config_path.write_text(yaml.safe_dump({"providers": self.profiles,
                                                    "active_provider": "unused"}))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"FAKE_KEY": "SYNTHETIC_KEY",
                                                        "LLMTOOL_PROVIDER": "unused"}, clear=True))
        self.stack.enter_context(patch("llmtool.config.user_config_path", return_value=self.config_path))
        self.stack.enter_context(patch("llmtool.config.Path.cwd", return_value=self.root))

    def test_four_transports_real_resolution_and_normalization(self):
        plan = make_plan(("grinder", "reasoning"), ("validator", "local-codex"),
                         ("coder", "code"), ("reviewer", "local-claude"))
        calls = []

        def http(req, **kwargs):
            body = json.loads(req.data)
            calls.append((body["model"], body.get("input", body.get("messages"))))
            self.assertNotIn("tools", body)
            if body["model"] == "openai-test":
                self.assertFalse(body["store"])
                payload = {"status": "completed", "model": "openai-test", "output": [
                    {"type": "reasoning", "summary": "PRIVATE_REASONING"},
                    {"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": "FIRST_OUTPUT"}]}],
                    "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}
            else:
                payload = {"model": "compatible-test", "choices": [{"finish_reason": "stop",
                           "message": {"content": "THIRD_OUTPUT", "reasoning_content": "PRIVATE_REASONING"}}],
                           "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
            response = Mock(status=200)
            response.read.return_value = json.dumps(payload).encode()
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            return context

        def codex(executable, prompt, runtime):
            calls.append(("codex", prompt))
            return "SECOND_OUTPUT", 0.02

        def claude(executable, prompt):
            calls.append(("claude", prompt))
            return "FOURTH_OUTPUT", 0.03

        before = self.config_path.read_bytes()
        with patch("urllib.request.OpenerDirector.open", side_effect=http), \
                patch("shutil.which", return_value="/fake/runtime"), \
                patch.object(client_type("codex-cli"), "_invoke", side_effect=codex), \
                patch.object(client_type("claude-cli"), "_invoke", side_effect=claude):
            result = o.run_orchestration_plan(plan, "ORIGINAL_TASK")
        self.assertEqual([call[0] for call in calls],
                         ["openai-test", "codex", "compatible-test", "claude"])
        for _, messages in calls:
            self.assertIn("ORIGINAL_TASK", str(messages))
        self.assertIn("FIRST_OUTPUT", calls[1][1])
        self.assertIn("SECOND_OUTPUT", str(calls[2][1]))
        self.assertNotIn("FIRST_OUTPUT", str(calls[2][1]))
        self.assertIn("THIRD_OUTPUT", calls[3][1])
        self.assertNotIn("SECOND_OUTPUT", calls[3][1])
        self.assertEqual([s.provider for s in result.steps], [s.provider for s in plan.steps])
        self.assertEqual([s.runtime.total_tokens for s in result.steps], [3, None, 6, None])
        self.assertEqual(result.final_content, "FOURTH_OUTPUT")
        self.assertNotIn("PRIVATE_REASONING", result.to_json())
        self.assertNotIn("SYNTHETIC_KEY", result.to_json())
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_real_readiness_blocks_key_lookup_and_later_profiles(self):
        import yaml
        self.profiles["reasoning"]["model"] = None
        self.config_path.write_text(yaml.safe_dump({"providers": self.profiles}))
        with patch.object(o, "api_key") as key, patch.object(o, "create_client") as factory:
            with self.assertRaisesRegex(LLMToolError, "model is not configured"):
                o.run_orchestration_plan(make_plan(("grinder", "reasoning"),
                                                   ("reviewer", "local-claude")), "task")
        key.assert_not_called()
        factory.assert_not_called()

    def test_unknown_profile_fails_without_fallback_to_active(self):
        with patch.object(o, "api_key") as key, patch.object(o, "create_client") as factory:
            with self.assertRaisesRegex(LLMToolError, "not defined"):
                o.run_orchestration_plan(make_plan(("grinder", "missing-profile")), "task")
        key.assert_not_called()
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
