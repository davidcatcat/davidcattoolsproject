"""Owner #289 / #2302: OpenCode Developer route model admission and validation.

The OpenCode Developer route admits whatever model the Owner configures
(free or paid, any provider prefix OpenCode supports, with or without a
#variant), with no free-only check and no per-model allow-list refusal,
while preserving strict input validation against malformed models.
"""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.developer_routes import check_route_model, load_route_config
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import (
    DirectOpenCodeError,
    DirectResult,
    is_free_model,
    is_valid_model,
    validate_model,
)
from llmtool.task_store import TaskStore, new_id
import llmtool.task_submission_cli as submission_cli
from llmtool.task_submission_cli import run_task_delegate
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract

import worker_policy_fixture


def make_test_task(route="opencode"):
    task_id, run_id = new_id(), new_id()
    request = "Bounded test task request."
    request_sha = hashlib.sha256(request.encode("utf-8")).hexdigest()
    contract = TaskContract(
        schema_version=1,
        task_id=task_id,
        contract_version=1,
        previous_digest=None,
        goal="PM intake request sha256:" + request_sha,
        authorized_scope=("test-scope",),
        forbidden_scope=(),
        criteria=(
            AcceptanceCriterion(
                "task_response",
                CriterionKind.ARTIFACT,
                "Record the developer outcome.",
            ),
        ),
    )
    response = json.dumps(
        {
            "schema_version": 1,
            "summary": "Ready.",
            "developer_assignment": {
                "route": route,
                "risk": "LOW",
                "goal": "Do task.",
                "acceptance": ["A test passes."],
            },
        },
        separators=(",", ":"),
    )
    return {
        "schema_version": 2,
        "task_id": task_id,
        "run_id": run_id,
        "pm_session_id": "pm-" + new_id(),
        "pm_provider_profile": "active-pm-runtime",
        "pm_provider_type": "active-pm-runtime",
        "pm_model": None,
        "backend_session_semantics": "ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL",
        "pm_route_binding": {
            "type": "active-pm-runtime",
            "model": None,
            "base_url": None,
        },
        "native_session_id": None,
        "state": "PM_RESPONDED",
        "created_at": "2026-11-18T00:00:00Z",
        "updated_at": "2026-11-18T00:00:00Z",
        "task_contract": contract.to_dict(),
        "task_binding": contract.binding(run_id).to_dict(),
        "request": request,
        "request_sha256": request_sha,
        "response": response,
        "usage": None,
        "error_code": None,
        "record_sha256": None,
        "developer": None,
        "pm_assignment": {
            "schema_version": 1,
            "route": route,
            "risk": "LOW",
            "goal": "Do task.",
            "acceptance": ["A test passes."],
        },
        "completion": None,
        "pm_provenance": {
            "source": "ACTIVE_PM_RUNTIME",
            "provider_invoked": False,
            "native_session_id_status": "UNAVAILABLE",
            "usage_status": "UNKNOWN",
            "session_restored": False,
            "automatic_callback": False,
        },
    }


def developer_assignment_for(task, *, provider="opencode", max_tokens=128):
    return {
        "assignment_id": new_id(),
        "selection_source": "PM",
        "state": "DEVELOPER_PENDING",
        "provider_profile": provider,
        "provider_type": None,
        "model": None,
        "timeout_seconds": 60,
        "account": None,
        "backend_session_semantics": "CLI_REPORTED_SESSION_ID",
        "session_id": "dev-" + new_id(),
        "source_sha": "a" * 40,
        "source_sha_binding": "D6_REGISTRY_OBSERVED",
        "context_sha256": "b" * 64,
        "egress_basis": "Bounded non-sensitive test context.",
        "input_bytes": 128,
        "max_tokens": max_tokens,
        "read_paths": ["source.py"],
        "write_paths": ["source.py"],
        "acceptance": ["The change is tested."],
        "artifact_format": "unified_diff",
        "artifact": None,
        "artifact_sha256": None,
        "usage": None,
        "error_code": None,
        "created_at": "2026-11-18T00:00:00Z",
        "execution": None,
    }


class OpenCodePaidModelAdmissionTests(unittest.TestCase):
    """Test model admission, validation and persistence per Owner #289 / #2302."""

    ADMITTED_PAID_MODELS = (
        "opencode/paid-model",
        "opencode/paid-model#high",
        "opencode/deepseek-v4.1",
        "opencode/deepseek-v4.1#low",
        "opencode/claude-sonnet-4",
        "opencode/gpt-5-mini#medium",
    )

    ADMITTED_OTHER_PROVIDER_MODELS = (
        "opencode-go/deepseek-v4.1-flash#high",
        "other/name-free",
        "other/name-paid",
        "fireworks/deepseek-v3",
        "together/llama-3.3-70b#low",
        "custom-provider/special-model",
    )

    ADMITTED_FREE_MODELS = (
        "opencode/deepseek-v4.1-flash-free",
        "opencode/deepseek-v4.1-flash-free#high",
        "opencode/test-free",
    )

    MALFORMED_MODELS = (
        "",
        "   ",
        "\t",
        "opencode/model\n",
        "opencode/model\r",
        "opencode/model\x01",
        "opencode/..",
        "../model",
        "opencode/../model",
        "https://example.invalid/m",
        "opencode://deepseek",
        "bare-model",
        "deepseek-v4.1-flash-free",
        "-leading/model",
        "opencode/-model",
        "opencode/a/b",
        "opencode/a/b-free",
        "opencode/" + "a" * 125,
        "opencode/example-free#",
        "opencode/example-free#HIGH",
        "opencode/example-free#a b",
        "opencode/example-free#x#y",
        "opencode/example-free#toolongvariantname123",
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "tasks"
        self.store = TaskStore(self.root)
        self.policy_path = worker_policy_fixture.install(self)

    def test_paid_models_are_admitted_by_direct_developer_validators(self):
        for model in self.ADMITTED_PAID_MODELS:
            with self.subTest(model=model):
                self.assertTrue(is_valid_model(model))
                self.assertEqual(validate_model(model), model)
                self.assertTrue(check_route_model("opencode", model))

    def test_other_provider_models_are_admitted_by_direct_developer_validators(self):
        for model in self.ADMITTED_OTHER_PROVIDER_MODELS:
            with self.subTest(model=model):
                self.assertTrue(is_valid_model(model))
                self.assertEqual(validate_model(model), model)
                self.assertTrue(check_route_model("opencode", model))

    def test_free_models_are_admitted_by_direct_developer_validators(self):
        for model in self.ADMITTED_FREE_MODELS:
            with self.subTest(model=model):
                self.assertTrue(is_valid_model(model))
                self.assertTrue(is_free_model(model))
                self.assertEqual(validate_model(model), model)
                self.assertTrue(check_route_model("opencode", model))

    def test_malformed_models_are_refused_by_direct_developer_validators(self):
        for model in self.MALFORMED_MODELS:
            with self.subTest(model=model):
                self.assertFalse(is_valid_model(model))
                self.assertFalse(check_route_model("opencode", model))
                with self.assertRaises(DirectOpenCodeError) as caught:
                    validate_model(model)
                self.assertEqual(
                    caught.exception.direct_opencode_code,
                    "OPENCODE_MODEL_INVALID",
                )

    def test_task_store_persists_admitted_models_across_all_states(self):
        artifact = (
            "diff --git a/source.py b/source.py\n"
            "--- a/source.py\n"
            "+++ b/source.py\n"
            "@@ -1 +1 @@\n"
            "-x = 1\n"
            "+x = 2\n"
        )
        succeeded_execution = {
            "status": "SUCCEEDED",
            "native_session_id": "ses_abcdefgh12345678",
            "process_exit_code": 0,
            "stdout_sha256": "c" * 64,
            "elapsed_ms": 15,
            "cleanup_status": "CONFIRMED",
            "usage_source": "UNKNOWN",
            "source_bytes": 20,
            "source_file_count": 1,
            "source_manifest_sha256": "d" * 64,
        }
        all_admitted = (
            self.ADMITTED_PAID_MODELS
            + self.ADMITTED_OTHER_PROVIDER_MODELS
            + self.ADMITTED_FREE_MODELS
        )
        for model in all_admitted:
            with self.subTest(admitted_model=model):
                task = make_test_task(route="opencode")
                self.store.create(task)
                pending = developer_assignment_for(task, provider="opencode")
                self.store.compare_and_update(
                    task["task_id"],
                    expected={"developer": None},
                    developer=pending,
                )

                # State: DEVELOPER_DISPATCHING
                dispatching = {
                    **pending,
                    "state": "DEVELOPER_DISPATCHING",
                    "provider_type": "opencode-cli",
                    "model": model,
                }
                self.store.update(task["task_id"], developer=dispatching)
                saved_dispatching = self.store.get(task["task_id"])
                self.assertEqual(
                    saved_dispatching["developer"]["model"], model
                )
                self.assertEqual(
                    saved_dispatching["developer"]["state"],
                    "DEVELOPER_DISPATCHING",
                )

                # State: DEVELOPER_RESPONDED
                responded = {
                    **dispatching,
                    "state": "DEVELOPER_RESPONDED",
                    "artifact": artifact,
                    "artifact_sha256": hashlib.sha256(
                        artifact.encode("utf-8")
                    ).hexdigest(),
                    "execution": succeeded_execution,
                }
                self.store.update(task["task_id"], developer=responded)
                saved_responded = self.store.get(task["task_id"])
                self.assertEqual(
                    saved_responded["developer"]["model"], model
                )
                self.assertEqual(
                    saved_responded["developer"]["state"],
                    "DEVELOPER_RESPONDED",
                )

                # State: DEVELOPER_OUTCOME_UNKNOWN (on fresh task)
                task_unknown = make_test_task(route="opencode")
                self.store.create(task_unknown)
                self.store.compare_and_update(
                    task_unknown["task_id"],
                    expected={"developer": None},
                    developer=pending,
                )
                self.store.update(task_unknown["task_id"], developer=dispatching)
                failed_execution = {
                    "status": "FAILED",
                    "native_session_id": "ses_fail12345678",
                    "process_exit_code": 1,
                    "stdout_sha256": "c" * 64,
                    "elapsed_ms": 15,
                    "cleanup_status": "CONFIRMED",
                    "usage_source": "UNKNOWN",
                    "source_bytes": 20,
                    "source_file_count": 1,
                    "source_manifest_sha256": "d" * 64,
                }
                unknown = {
                    **dispatching,
                    "state": "DEVELOPER_OUTCOME_UNKNOWN",
                    "error_code": "OPENCODE_EXECUTION_FAILED",
                    "execution": failed_execution,
                }
                self.store.update(task_unknown["task_id"], developer=unknown)
                saved_unknown = self.store.get(task_unknown["task_id"])
                self.assertEqual(
                    saved_unknown["developer"]["model"], model
                )
                self.assertEqual(
                    saved_unknown["developer"]["state"],
                    "DEVELOPER_OUTCOME_UNKNOWN",
                )

    def test_task_store_refuses_malformed_models_on_opencode_record(self):
        task = make_test_task(route="opencode")
        self.store.create(task)
        pending = developer_assignment_for(task, provider="opencode")
        self.store.compare_and_update(
            task["task_id"],
            expected={"developer": None},
            developer=pending,
        )
        for malformed in self.MALFORMED_MODELS:
            with self.subTest(malformed=malformed):
                invalid_dispatch = {
                    **pending,
                    "state": "DEVELOPER_DISPATCHING",
                    "provider_type": "opencode-cli",
                    "model": malformed,
                }
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(
                        task["task_id"], developer=invalid_dispatch
                    )
                self.assertEqual(
                    caught.exception.task_store_error_code,
                    "TASK_STORE_INVALID",
                )

    def test_other_routes_model_checks_remain_unchanged(self):
        # A3: AGY, Claude, gw-agent, and Codex roles keep their checks unchanged.
        # AGY: Gemini family only
        self.assertTrue(check_route_model("agy", "gemini-3.8-flash-medium"))
        self.assertFalse(check_route_model("agy", "claude-opus-5-5"))
        self.assertFalse(check_route_model("agy", "gpt-6.1-sol"))
        self.assertFalse(check_route_model("agy", "opencode/paid-model"))

        # Claude: Claude family only
        self.assertTrue(check_route_model("claude", "claude-opus-5-5"))
        self.assertFalse(check_route_model("claude", "gemini-3.8-flash-medium"))
        self.assertFalse(check_route_model("claude", "gpt-6.1-sol"))
        self.assertFalse(check_route_model("claude", "opencode/paid-model"))

        # gw-agent: allowed_models gate (default 'gw')
        self.assertTrue(check_route_model("gw-agent", "gw"))
        self.assertFalse(check_route_model("gw-agent", "opencode/paid-model"))
        self.assertFalse(check_route_model("gw-agent", "other-unlisted-model"))

        # Codex role check: Codex/GPT family only
        from llmtool.developer_routes import check_registry_role_model
        self.assertTrue(check_registry_role_model("codex-reviewer", "gpt-6.1-sol"))
        self.assertFalse(check_registry_role_model("codex-reviewer", "claude-opus-5-5"))
        self.assertFalse(check_registry_role_model("codex-reviewer", "gemini-3.8-flash-medium"))


class OpenCodeDelegateEndToEndModelTests(unittest.TestCase):
    """Test delegate execution and profile handling with admitted models."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir(mode=0o700)
        self.tasks_root = self.home / ".local/state/llmtool/tasks"
        self.store = TaskStore(self.tasks_root)
        self.executor_root = Path(self.temp.name) / "worktree"
        self.executor_root.mkdir(mode=0o700)
        (self.executor_root / "sample.py").write_text("VALUE = 1\n")
        self.policy_path = worker_policy_fixture.install(self)

    def _write_config(self, model, steps=10):
        config_dir = self.home / ".config/llmtool"
        config_dir.mkdir(parents=True, exist_ok=True)
        content = (
            "schema_version: 1\n"
            "profiles:\n"
            "  opencode-developer:\n"
            f"    model: {model}\n"
            f"    steps: {steps}\n"
        )
        (config_dir / "config.yaml").write_text(content, encoding="utf-8")

    def _opencode_delegate_args(self, task):
        return SimpleNamespace(
            task_id=task["task_id"],
            selection_source="USER",
            context_stdin=False,
            executor_task="executor-d6-test",
            read_path=["sample.py"],
            write_path=["sample.py"],
            acceptance=["sample.py updated."],
            egress_basis="Approved non-sensitive test context.",
        )

    def test_delegate_admits_configured_paid_model_end_to_end(self):
        task = make_test_task(route="opencode")
        self.store.create(task)
        self._write_config("opencode-go/deepseek-v4.1-flash#high", steps=20)
        output = io.StringIO()

        def fake_run(**kwargs):
            self.assertEqual(kwargs["model"], "opencode-go/deepseek-v4.1-flash#high")
            self.assertEqual(kwargs["steps"], 20)
            (self.executor_root / "sample.py").write_text("VALUE = 2\n")
            return DirectResult(
                0,
                "ses_Abc12345678",
                "Updated fixture.",
                None,
                "UNKNOWN",
                "a" * 64,
                100,
                "CONFIRMED",
                "opencode v2.1.4",
                10,
                1,
                "b" * 64,
            )

        snapshot = SimpleNamespace(
            root=str(self.executor_root),
            common_dir="/repo/.git",
            branch="task-branch",
            head="a" * 40,
        )
        registration = SimpleNamespace(
            task_id="executor-d6-test",
            trusted_agent_id="opencode-developer",
            session_ref="session-ref",
            native_session_id=None,
            root=str(self.executor_root),
            common_dir=snapshot.common_dir,
            branch=snapshot.branch,
            env2_pass_marker="pass",
            source_sha=snapshot.head,
        )

        with patch.dict(os.environ, {"HOME": str(self.home)}), \
                patch.object(submission_cli.task_completion, "validate_task_binding"), \
                patch.object(submission_cli.task_completion, "collect_git_snapshot", return_value=snapshot), \
                patch.object(submission_cli.task_completion, "bind_executor_task", return_value=(registration, snapshot)), \
                patch.object(submission_cli.task_completion, "validate_env2_pass_marker"), \
                patch.object(submission_cli.task_completion, "read_d6_registration", return_value=registration), \
                patch.object(submission_cli.task_completion, "_git", return_value=b""), \
                patch.object(submission_cli, "_bootstrap_registered_d6"), \
                patch.object(submission_cli, "_ignored_file_fingerprints", return_value={}), \
                patch.object(submission_cli, "_ignored_output_rejection", return_value=None), \
                patch("llmtool.task_submission_cli.opencode_direct_developer.run", side_effect=fake_run) as invoke, \
                redirect_stdout(output):
            run_task_delegate(self._opencode_delegate_args(task))

        self.assertEqual(invoke.call_count, 1)
        stored = self.store.get(task["task_id"])
        self.assertEqual(stored["developer"]["state"], "DEVELOPER_RESPONDED")
        self.assertEqual(
            stored["developer"]["model"],
            "opencode-go/deepseek-v4.1-flash#high",
        )

    def test_delegate_refuses_malformed_model_before_cli_launch(self):
        task = make_test_task(route="opencode")
        self.store.create(task)
        self._write_config("bare-model-without-provider")

        snapshot = SimpleNamespace(
            root=str(self.executor_root),
            common_dir="/repo/.git",
            branch="task-branch",
            head="a" * 40,
        )
        registration = SimpleNamespace(
            task_id="executor-d6-test",
            trusted_agent_id="opencode-developer",
            session_ref="session-ref",
            native_session_id=None,
            root=str(self.executor_root),
            common_dir=snapshot.common_dir,
            branch=snapshot.branch,
            env2_pass_marker="pass",
            source_sha=snapshot.head,
        )

        with patch.dict(os.environ, {"HOME": str(self.home)}), \
                patch.object(submission_cli.task_completion, "validate_task_binding"), \
                patch.object(submission_cli.task_completion, "collect_git_snapshot", return_value=snapshot), \
                patch.object(submission_cli.task_completion, "bind_executor_task", return_value=(registration, snapshot)), \
                patch.object(submission_cli.task_completion, "validate_env2_pass_marker"), \
                patch.object(submission_cli.task_completion, "read_d6_registration", return_value=registration), \
                patch.object(submission_cli.task_completion, "_git", return_value=b""), \
                patch.object(submission_cli, "_bootstrap_registered_d6"), \
                patch("llmtool.task_submission_cli.opencode_direct_developer.run") as invoke:
            with self.assertRaises(LLMToolError) as caught:
                run_task_delegate(self._opencode_delegate_args(task))
            self.assertEqual(
                getattr(caught.exception, "direct_opencode_code", None),
                "OPENCODE_MODEL_INVALID",
            )
        invoke.assert_not_called()
        stored = self.store.get(task["task_id"])
        self.assertIsNone(stored["developer"])


if __name__ == "__main__":
    unittest.main()
