import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/llmtool-remote-test.yml"

CHECKOUT_ACTION = "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd"
SETUP_PYTHON_ACTION = "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"

FORBIDDEN_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "GW_AGENT_API_KEY",
)


def _load_workflow():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    return raw, parsed


def _on_section(parsed):
    return parsed.get("on", parsed.get(True))


class RemoteTestWorkflowTests(unittest.TestCase):
    def test_trigger_inputs_run_name_and_permissions(self):
        raw, parsed = _load_workflow()
        self.assertEqual(parsed["name"], "llmtool remote-test")
        self.assertEqual(parsed["run-name"], "llmtool remote-test ${{ inputs.nonce }}")
        self.assertEqual(parsed["permissions"], {"contents": "read"})
        self.assertNotIn("concurrency", parsed)
        self.assertNotIn("env", parsed)

        on_block = _on_section(parsed)
        self.assertEqual(set(on_block), {"workflow_dispatch"})
        inputs = on_block["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"sha", "ref", "modules", "nonce"})
        for name, spec in inputs.items():
            with self.subTest(input_name=name):
                self.assertEqual(spec["type"], "string")
                self.assertIs(spec["required"], True)

        for forbidden in (
            "secrets.",
            "id-token",
            "pull_request",
            "pull_request_target",
            "workflow_run",
            "concurrency",
            "environment",
        ):
            self.assertNotIn(forbidden, raw)

    def test_single_job_gate_runner_and_timeout(self):
        _, parsed = _load_workflow()
        jobs = parsed["jobs"]
        self.assertEqual(set(jobs), {"remote-test"})
        job = jobs["remote-test"]
        self.assertEqual(job["name"], "remote-test")
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], 15)
        for forbidden_key in ("environment", "concurrency", "permissions", "strategy"):
            self.assertNotIn(forbidden_key, job)

        job_if = job["if"]
        for required_clause in (
            "vars.HOSTED_CI_ENABLED == 'true'",
            "github.repository == 'davidcatcat/davidcattoolsproject'",
            "github.ref == 'refs/heads/main'",
            "github.event_name == 'workflow_dispatch'",
            "(github.actor == 'davidcatcat' || github.actor == 'ddavidccatssun')",
            "(github.triggering_actor == 'davidcatcat' || github.triggering_actor == 'ddavidccatssun')",
        ):
            self.assertIn(required_clause, job_if)

    def test_no_forbidden_network_or_privilege_commands(self):
        raw, parsed = _load_workflow()
        self.assertNotIn("secrets.", raw)
        for word in ("sudo", "ssh", "curl", "wget", "vpn", "wireguard", "tailscale"):
            self.assertIsNone(re.search(rf"\b{word}\b", raw, re.IGNORECASE))
        for step in parsed["jobs"]["remote-test"]["steps"]:
            if "run" in step:
                self.assertNotIn("inputs.", step["run"])

    def test_boundary_step_is_first_before_any_checkout_and_enforces_checks(self):
        _, parsed = _load_workflow()
        steps = parsed["jobs"]["remote-test"]["steps"]
        boundary = steps[0]
        self.assertNotIn("uses", boundary)
        self.assertEqual(boundary["shell"], "bash")
        script = boundary["run"]

        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RUNNER_ENVIRONMENT": "github-hosted"}
        ok = subprocess.run(["bash", "-c", script], env=base_env, capture_output=True, text=True, check=False)
        self.assertEqual(ok.returncode, 0, ok.stderr or ok.stdout)

        bad_runner = subprocess.run(
            ["bash", "-c", script],
            env={**base_env, "RUNNER_ENVIRONMENT": "self-hosted"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(bad_runner.returncode, 0)

        for var_name in FORBIDDEN_ENV_VARS:
            with self.subTest(var_name=var_name):
                res = subprocess.run(
                    ["bash", "-c", script],
                    env={**base_env, var_name: "present"},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(res.returncode, 0)
                empty_res = subprocess.run(
                    ["bash", "-c", script],
                    env={**base_env, var_name: ""},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(empty_res.returncode, 0)

        with tempfile.TemporaryDirectory() as tmp:
            id_stub = Path(tmp) / "id"
            id_stub.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            id_stub.chmod(0o755)
            root_res = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "PATH": f"{tmp}{os.pathsep}{base_env['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(root_res.returncode, 0)

    def test_input_step_reads_env_only_and_validates_all_inputs(self):
        _, parsed = _load_workflow()
        steps = parsed["jobs"]["remote-test"]["steps"]
        input_step = steps[1]
        self.assertEqual(input_step["id"], "inputs")
        self.assertEqual(input_step["shell"], "bash")
        self.assertEqual(
            input_step["env"],
            {
                "INPUT_SHA": "${{ inputs.sha }}",
                "INPUT_REF": "${{ inputs.ref }}",
                "INPUT_MODULES": "${{ inputs.modules }}",
                "INPUT_NONCE": "${{ inputs.nonce }}",
            },
        )
        self.assertNotIn("inputs.", input_step["run"])

        for step in steps[2:]:
            step_dump = json.dumps(step)
            self.assertNotIn("inputs.sha", step_dump)
            self.assertNotIn("inputs.ref", step_dump)
            self.assertNotIn("inputs.modules", step_dump)
            self.assertNotIn("inputs.nonce", step_dump)

        valid_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "INPUT_SHA": "a" * 40,
            "INPUT_REF": "refs/pull/42/head",
            "INPUT_MODULES": "test_alpha test_beta",
            "INPUT_NONCE": "c" * 32,
        }
        with tempfile.TemporaryDirectory() as tmp:
            for accepted_ref in ("refs/pull/42/head", "feature/branch-1", "refs/pull/notanumber/head"):
                with self.subTest(accepted_ref=accepted_ref):
                    out_file = Path(tmp) / "github_output"
                    if out_file.exists():
                        out_file.unlink()
                    res = subprocess.run(
                        ["bash", "-c", input_step["run"]],
                        env={**valid_env, "INPUT_REF": accepted_ref, "GITHUB_OUTPUT": str(out_file)},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(res.returncode, 0, res.stderr or res.stdout)
                    outputs = dict(
                        line.split("=", 1) for line in out_file.read_text(encoding="utf-8").splitlines()
                    )
                    self.assertEqual(
                        outputs,
                        {
                            "sha": "a" * 40,
                            "ref": accepted_ref,
                            "modules": "test_alpha test_beta",
                            "nonce": "c" * 32,
                        },
                    )

            invalid_cases = [
                {"INPUT_SHA": "a" * 39},
                {"INPUT_SHA": "A" * 40},
                {"INPUT_REF": ""},
                {"INPUT_REF": "-invalid"},
                {"INPUT_REF": "branch~1"},
                {"INPUT_REF": "b" * 101},
                {"INPUT_NONCE": "c" * 31},
                {"INPUT_NONCE": "C" * 32},
                {"INPUT_MODULES": ""},
                {"INPUT_MODULES": "   "},
                {"INPUT_MODULES": "test_dup test_dup"},
                {"INPUT_MODULES": "bad_module"},
                {"INPUT_MODULES": "test_Upper"},
                {"INPUT_MODULES": " ".join(f"test_m{i}" for i in range(9))},
            ]
            for override in invalid_cases:
                with self.subTest(override=override):
                    bad_out = Path(tmp) / "bad_output"
                    if bad_out.exists():
                        bad_out.unlink()
                    bad_res = subprocess.run(
                        ["bash", "-c", input_step["run"]],
                        env={**valid_env, **override, "GITHUB_OUTPUT": str(bad_out)},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(bad_res.returncode, 0)
                    self.assertFalse(bad_out.exists())

    def test_pinned_actions_trusted_src_split_and_venv_install(self):
        _, parsed = _load_workflow()
        steps = parsed["jobs"]["remote-test"]["steps"]

        trusted_checkout = steps[2]
        self.assertEqual(trusted_checkout["uses"], CHECKOUT_ACTION)
        self.assertEqual(
            trusted_checkout["with"],
            {"ref": "${{ github.sha }}", "path": "trusted", "persist-credentials": False},
        )

        setup_python = steps[3]
        self.assertEqual(setup_python["uses"], SETUP_PYTHON_ACTION)
        self.assertEqual(setup_python["with"], {"python-version": "3.14"})

        ref_step = steps[4]
        self.assertEqual(ref_step["id"], "ref")

        src_checkout = steps[5]
        self.assertEqual(src_checkout["uses"], CHECKOUT_ACTION)
        self.assertEqual(
            src_checkout["with"],
            {
                "ref": "${{ steps.inputs.outputs.sha }}",
                "path": "src",
                "persist-credentials": False,
            },
        )

        verify_src = steps[6]
        self.assertEqual(verify_src["env"], {"EXPECTED_SHA": "${{ steps.inputs.outputs.sha }}"})
        self.assertIn("git -C src rev-parse HEAD", verify_src["run"])

        install_step = steps[7]
        self.assertIn('python -m venv "$RUNNER_TEMP/venv"', install_step["run"])
        self.assertIn(
            '"$RUNNER_TEMP/venv/bin/python" -m pip install --quiet -r src/tools/llmtool/requirements-ci.txt',
            install_step["run"],
        )
        self.assertIn(
            '"$RUNNER_TEMP/venv/bin/python" -m pip install --quiet --no-deps -e src/tools/llmtool',
            install_step["run"],
        )

        run_step = steps[8]
        self.assertIn("trusted/tools/llmtool/scripts/remote_test_receipt.py", ref_step["run"])
        self.assertIn("trusted/tools/llmtool/scripts/remote_test_receipt.py", run_step["run"])
        self.assertNotIn("src/tools/llmtool/scripts/remote_test_receipt.py", run_step["run"])

    def test_ref_check_and_builder_argv_contract(self):
        _, parsed = _load_workflow()
        steps = parsed["jobs"]["remote-test"]["steps"]
        ref_step = steps[4]
        run_step = steps[8]

        self.assertEqual(
            ref_step["env"],
            {
                "SHA": "${{ steps.inputs.outputs.sha }}",
                "REF": "${{ steps.inputs.outputs.ref }}",
                "MODULES": "${{ steps.inputs.outputs.modules }}",
            },
        )
        self.assertEqual(
            run_step["env"],
            {
                "SHA": "${{ steps.inputs.outputs.sha }}",
                "FETCH_HEAD": "${{ steps.ref.outputs.fetch_head }}",
                "MODULES": "${{ steps.inputs.outputs.modules }}",
            },
        )
        self.assertIn('--out "$RUNNER_TEMP/remote-test-receipt"', ref_step["run"])
        self.assertIn('--src "$GITHUB_WORKSPACE/src/tools/llmtool"', run_step["run"])
        self.assertIn('--python "$RUNNER_TEMP/venv/bin/python"', run_step["run"])
        self.assertIn('--out "$RUNNER_TEMP/remote-test-receipt"', run_step["run"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "trusted").mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            git_log = root / "git_calls.json"
            py_log = root / "py_calls.json"

            git_stub = bin_dir / "git"
            git_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['GIT_LOG'], 'w', encoding='utf-8') as f:\n"
                "    json.dump({'cwd': os.getcwd(), 'argv': sys.argv[1:]}, f)\n"
                "sys.stdout.write(os.environ.get('GIT_LS_REMOTE_OUT', ''))\n"
                "sys.exit(int(os.environ.get('GIT_EXIT', '0')))\n",
                encoding="utf-8",
            )
            git_stub.chmod(0o755)

            py_stub = bin_dir / "python"
            py_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['PY_LOG'], 'w', encoding='utf-8') as f:\n"
                "    json.dump(sys.argv[1:], f)\n"
                "sys.exit(int(os.environ.get('PY_EXIT', '0')))\n",
                encoding="utf-8",
            )
            py_stub.chmod(0o755)

            sha = "a" * 40
            other_sha = "b" * 40
            base_env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
                "GITHUB_WORKSPACE": str(root),
                "RUNNER_TEMP": str(root / "temp"),
                "GIT_LOG": str(git_log),
                "PY_LOG": str(py_log),
                "SHA": sha,
                "REF": "feature/branch-1",
                "MODULES": "test_one test_two",
            }

            # Matching branch ref -> refs/heads/feature/branch-1, then run builder.
            out_file = root / "ref_output"
            match_res = subprocess.run(
                ["bash", "-c", ref_step["run"]],
                cwd=root,
                env={
                    **base_env,
                    "GITHUB_OUTPUT": str(out_file),
                    "GIT_LS_REMOTE_OUT": f"{sha}\trefs/heads/feature/branch-1\n",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(match_res.returncode, 0, match_res.stderr or match_res.stdout)
            git_call = json.loads(git_log.read_text(encoding="utf-8"))
            self.assertEqual(Path(git_call["cwd"]).resolve(), (root / "trusted").resolve())
            self.assertEqual(git_call["argv"], ["ls-remote", "origin", "refs/heads/feature/branch-1"])
            self.assertFalse(py_log.exists())
            self.assertEqual(out_file.read_text(encoding="utf-8").strip(), f"fetch_head={sha}")

            run_res = subprocess.run(
                ["bash", "-c", run_step["run"]],
                cwd=root,
                env={**base_env, "FETCH_HEAD": sha},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_res.returncode, 0, run_res.stderr or run_res.stdout)
            self.assertEqual(
                json.loads(py_log.read_text(encoding="utf-8")),
                [
                    "-I",
                    "-B",
                    "trusted/tools/llmtool/scripts/remote_test_receipt.py",
                    "run",
                    "--src",
                    f"{root}/src/tools/llmtool",
                    "--python",
                    f"{root / 'temp'}/venv/bin/python",
                    "--out",
                    f"{root / 'temp'}/remote-test-receipt",
                    "--sha",
                    sha,
                    "--fetch-head",
                    sha,
                    "test_one",
                    "test_two",
                ],
            )

            # PR ref stays refs/pull/N/head; mismatched SHA invokes builder mismatch with listed sha.
            py_log.unlink()
            mismatch_res = subprocess.run(
                ["bash", "-c", ref_step["run"]],
                cwd=root,
                env={
                    **base_env,
                    "REF": "refs/pull/99/head",
                    "GITHUB_OUTPUT": str(root / "mismatch_out"),
                    "GIT_LS_REMOTE_OUT": f"{other_sha}\trefs/pull/99/head\n",
                    "PY_EXIT": "41",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(mismatch_res.returncode, 0)
            git_call = json.loads(git_log.read_text(encoding="utf-8"))
            self.assertEqual(git_call["argv"], ["ls-remote", "origin", "refs/pull/99/head"])
            self.assertEqual(
                json.loads(py_log.read_text(encoding="utf-8")),
                [
                    "-I",
                    "-B",
                    "trusted/tools/llmtool/scripts/remote_test_receipt.py",
                    "mismatch",
                    "--out",
                    f"{root / 'temp'}/remote-test-receipt",
                    "--sha",
                    sha,
                    "--fetch-head",
                    other_sha,
                    "test_one",
                    "test_two",
                ],
            )

            # Failing git ls-remote (non-zero exit) is handled as empty listing; runs builder mismatch with fetch-head none.
            py_log.unlink()
            fail_ls_res = subprocess.run(
                ["bash", "-c", ref_step["run"]],
                cwd=root,
                env={
                    **base_env,
                    "REF": "feature/branch-1",
                    "GITHUB_OUTPUT": str(root / "fail_ls_out"),
                    "GIT_LS_REMOTE_OUT": "fatal: remote error\n",
                    "GIT_EXIT": "128",
                    "PY_EXIT": "41",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(fail_ls_res.returncode, 0)
            git_call = json.loads(git_log.read_text(encoding="utf-8"))
            self.assertEqual(
                git_call["argv"],
                ["ls-remote", "origin", "refs/heads/feature/branch-1"],
            )
            self.assertEqual(
                json.loads(py_log.read_text(encoding="utf-8")),
                [
                    "-I",
                    "-B",
                    "trusted/tools/llmtool/scripts/remote_test_receipt.py",
                    "mismatch",
                    "--out",
                    f"{root / 'temp'}/remote-test-receipt",
                    "--sha",
                    sha,
                    "--fetch-head",
                    "none",
                    "test_one",
                    "test_two",
                ],
            )

            # Static check: git ls-remote failure cannot end the step before the mismatch builder.
            self.assertIn("if ! ls_out=$(cd trusted && git ls-remote origin \"$full_ref\"); then", ref_step["run"])
            self.assertIn('ls_out=""', ref_step["run"])

            # Missing remote ref (including branch-shaped refs/pull/notanumber/head) invokes builder mismatch with fetch-head none.
            py_log.unlink()
            missing_res = subprocess.run(
                ["bash", "-c", ref_step["run"]],
                cwd=root,
                env={
                    **base_env,
                    "REF": "refs/pull/notanumber/head",
                    "GITHUB_OUTPUT": str(root / "missing_out"),
                    "GIT_LS_REMOTE_OUT": "",
                    "PY_EXIT": "41",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(missing_res.returncode, 0)
            git_call = json.loads(git_log.read_text(encoding="utf-8"))
            self.assertEqual(
                git_call["argv"],
                ["ls-remote", "origin", "refs/heads/refs/pull/notanumber/head"],
            )
            self.assertEqual(
                json.loads(py_log.read_text(encoding="utf-8")),
                [
                    "-I",
                    "-B",
                    "trusted/tools/llmtool/scripts/remote_test_receipt.py",
                    "mismatch",
                    "--out",
                    f"{root / 'temp'}/remote-test-receipt",
                    "--sha",
                    sha,
                    "--fetch-head",
                    "none",
                    "test_one",
                    "test_two",
                ],
            )

    def test_upload_artifact_step_settings(self):
        _, parsed = _load_workflow()
        steps = parsed["jobs"]["remote-test"]["steps"]
        upload = steps[-1]
        self.assertEqual(upload["uses"], UPLOAD_ARTIFACT_ACTION)
        self.assertEqual(upload["if"], "always() && steps.inputs.outcome == 'success'")
        self.assertEqual(
            upload["with"],
            {
                "name": "remote-test-receipt-${{ steps.inputs.outputs.nonce }}",
                "path": "${{ runner.temp }}/remote-test-receipt/receipt.json",
                "retention-days": 3,
                "if-no-files-found": "error",
            },
        )


if __name__ == "__main__":
    unittest.main()
