from __future__ import annotations

from pathlib import Path
import unittest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml"

EXPECTED_JOB_NAME = "check (macOS 3.13)"
EXPECTED_SELECT_JOB_NAME = "select"
EXPECTED_RUNS_ON = (
    "${{ github.event_name == 'pull_request' && "
    "!contains(github.event.pull_request.labels.*.name, 'mac-ci-intel') && "
    "'macos-15' || 'macos-15-intel' }}"
)
EXPECTED_IF = (
    "vars.HOSTED_CI_ENABLED == 'true' && "
    "(github.event_name != 'pull_request' || "
    "contains(github.event.pull_request.labels.*.name, 'mac-ci')) && "
    "(github.event.action != 'labeled' || "
    "github.event.label.name == 'mac-ci' || "
    "github.event.label.name == 'mac-ci-intel')"
)
EXPECTED_CHECK_RUNS_ON = (
    "${{ github.event_name == 'pull_request' && "
    "!contains(github.event.pull_request.labels.*.name, 'mac-ci-intel') && "
    "needs.select.outputs.mode != 'full' && "
    "'macos-15' || 'macos-15-intel' }}"
)
EXPECTED_CHECK_IF = (
    "always() && "
    "vars.HOSTED_CI_ENABLED == 'true' && "
    "(github.event_name != 'pull_request' || "
    "contains(github.event.pull_request.labels.*.name, 'mac-ci')) && "
    "(github.event.action != 'labeled' || "
    "github.event.label.name == 'mac-ci' || "
    "github.event.label.name == 'mac-ci-intel')"
)


def evaluate_runner(event_name: str, pr_labels: list[str] | set[str] | tuple[str, ...]) -> str:
    """Evaluate runner choice matching the runs-on expression in llmtool-mac-ci.yml.

    Expression:
    ${{ github.event_name == 'pull_request' && !contains(github.event.pull_request.labels.*.name, 'mac-ci-intel') && 'macos-15' || 'macos-15-intel' }}
    """
    labels = set(pr_labels)
    if event_name == "pull_request" and "mac-ci-intel" not in labels:
        return "macos-15"
    return "macos-15-intel"


def evaluate_if(
    hosted_ci_enabled: bool | str,
    event_name: str,
    pr_labels: list[str] | set[str] | tuple[str, ...] | None = None,
    action: str | None = None,
    label_name: str | None = None,
) -> bool:
    """Evaluate if condition matching the select job if in llmtool-mac-ci.yml.

    Expression:
    vars.HOSTED_CI_ENABLED == 'true' && (github.event_name != 'pull_request' || contains(github.event.pull_request.labels.*.name, 'mac-ci')) && (github.event.action != 'labeled' || github.event.label.name == 'mac-ci' || github.event.label.name == 'mac-ci-intel')
    """
    enabled_str = str(hosted_ci_enabled).lower() if isinstance(hosted_ci_enabled, bool) else str(hosted_ci_enabled)
    if enabled_str != "true":
        return False

    labels = set(pr_labels or [])
    if event_name == "pull_request" and "mac-ci" not in labels:
        return False

    if action == "labeled" and label_name not in ("mac-ci", "mac-ci-intel"):
        return False

    return True


class MacCIRunnerSelectionTests(unittest.TestCase):
    def _load_workflow(self) -> dict:
        self.assertTrue(WORKFLOW_PATH.is_file(), f"Workflow file missing: {WORKFLOW_PATH}")
        content = WORKFLOW_PATH.read_text(encoding="utf-8")
        return yaml.safe_load(content)

    def test_workflow_exact_strings_and_job_name(self):
        workflow = self._load_workflow()
        self.assertIn("jobs", workflow)
        self.assertIn("select", workflow["jobs"])
        self.assertIn("check", workflow["jobs"])

        select_job = workflow["jobs"]["select"]
        self.assertEqual(select_job.get("name"), EXPECTED_SELECT_JOB_NAME)
        self.assertEqual(select_job.get("runs-on"), EXPECTED_RUNS_ON)
        self.assertEqual(select_job.get("if"), EXPECTED_IF)

        check_job = workflow["jobs"]["check"]
        self.assertEqual(check_job.get("name"), EXPECTED_JOB_NAME)
        self.assertEqual(check_job.get("runs-on"), EXPECTED_CHECK_RUNS_ON)
        self.assertEqual(check_job.get("if"), EXPECTED_CHECK_IF)

    def test_workflow_text_contains_expressions(self):
        workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn(EXPECTED_RUNS_ON, workflow_text)
        self.assertIn(EXPECTED_IF, workflow_text)
        self.assertIn(EXPECTED_CHECK_RUNS_ON, workflow_text)
        self.assertIn(EXPECTED_CHECK_IF, workflow_text)

    def test_evaluate_runner_cases(self):
        # Case 1: pull_request with labels [mac-ci] gives macos-15
        self.assertEqual(evaluate_runner("pull_request", ["mac-ci"]), "macos-15")

        # Case 2: pull_request with [mac-ci, mac-ci-intel] gives macos-15-intel
        self.assertEqual(
            evaluate_runner("pull_request", ["mac-ci", "mac-ci-intel"]),
            "macos-15-intel",
        )

        # Case 3: workflow_dispatch gives macos-15-intel
        self.assertEqual(evaluate_runner("workflow_dispatch", []), "macos-15-intel")

    def test_evaluate_if_cases(self):
        # Case 1: labeled event for mac-ci-intel on a PR that has mac-ci runs
        self.assertTrue(
            evaluate_if(
                hosted_ci_enabled=True,
                event_name="pull_request",
                pr_labels=["mac-ci"],
                action="labeled",
                label_name="mac-ci-intel",
            )
        )

        # Case 2: labeled event for mac-ci-intel on a PR without mac-ci does not run
        self.assertFalse(
            evaluate_if(
                hosted_ci_enabled=True,
                event_name="pull_request",
                pr_labels=[],
                action="labeled",
                label_name="mac-ci-intel",
            )
        )

        # Case 3: labeled event for another label does not run
        self.assertFalse(
            evaluate_if(
                hosted_ci_enabled=True,
                event_name="pull_request",
                pr_labels=["mac-ci"],
                action="labeled",
                label_name="synthetic-other-label",
            )
        )

        # Case 4: synchronize on a PR with mac-ci runs
        self.assertTrue(
            evaluate_if(
                hosted_ci_enabled=True,
                event_name="pull_request",
                pr_labels=["mac-ci"],
                action="synchronize",
            )
        )

        # Case 5: workflow_dispatch runs
        self.assertTrue(
            evaluate_if(
                hosted_ci_enabled=True,
                event_name="workflow_dispatch",
            )
        )

        # Case 6: HOSTED_CI_ENABLED false never runs
        self.assertFalse(
            evaluate_if(
                hosted_ci_enabled=False,
                event_name="workflow_dispatch",
            )
        )
        self.assertFalse(
            evaluate_if(
                hosted_ci_enabled="false",
                event_name="pull_request",
                pr_labels=["mac-ci"],
                action="synchronize",
            )
        )
        self.assertFalse(
            evaluate_if(
                hosted_ci_enabled=False,
                event_name="pull_request",
                pr_labels=["mac-ci"],
                action="labeled",
                label_name="mac-ci-intel",
            )
        )


if __name__ == "__main__":
    unittest.main()
