import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from llmtool.config import resolve_config
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError
from llmtool.providers import PROVIDER_REGISTRY, get_provider_capabilities
from llmtool.providers.claude_cli import ClaudeCLIProvider
from llmtool.providers.cli_lifecycle import background_cli_invocation, tui_cli_signal_scope


# #1502 WP-E R1: the only model a review job may launch, and the other family's.
_ROLES = load_route_config()["roles"]
REVIEW_MODEL = _ROLES["claude-reviewer"]["allowed_models"][0]
CROSS_FAMILY_MODEL = _ROLES["codex-reviewer"]["allowed_models"][0]

CAPABILITIES = {
    "chat": True, "large_context": False, "reasoning_control": False,
    "streaming": False, "code_edit": False, "shell": False, "git": False,
}


class ClaudeCLIProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args_file = self.root / "args.json"
        self.input_file = self.root / "input.txt"
        self.home_file = self.root / "child-home.txt"
        self.cwd_file = self.root / "child-cwd.txt"
        self.executable = self.root / "fake-claude"
        script = """#!/usr/bin/env python3
import json, os, sys, time
with open(%r, "w", encoding="utf-8") as f:
    json.dump(sys.argv[1:], f)
with open(%r, "w", encoding="utf-8") as f:
    f.write(sys.stdin.read())
with open(%r, "w", encoding="utf-8") as f:
    f.write(os.environ.get("HOME", "") + "\\n" + os.environ.get("LLMTOOL_CLAUDE_CLI_HOME", ""))
with open(%r, "w", encoding="utf-8") as f:
    f.write(os.getcwd())
mode = os.environ.get("CLAUDE_TEST_MODE", "success")
if mode == "timeout":
    time.sleep(5)
elif mode == "large":
    sys.stdout.write("x" * 4096)
elif mode == "large-stderr":
    sys.stderr.write("x" * 4096)
elif mode == "descendant":
    import subprocess
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    sys.stdout.write("CLAUDE_FINAL\\n")
elif mode == "invalid":
    sys.stdout.buffer.write(b"\\xff")
elif mode == "empty":
    pass
elif mode == "failure":
    sys.stderr.write("PRIVATE_CLI_DIAGNOSTIC")
    sys.exit(7)
else:
    sys.stderr.write("PRIVATE_CLI_DIAGNOSTIC")
    sys.stdout.write("  CLAUDE_FINAL  \\n")
""" % (str(self.args_file), str(self.input_file), str(self.home_file), str(self.cwd_file))
        self.executable.write_text(script, encoding="utf-8")
        self.executable.chmod(0o700)
        self.provider_config = {
            "type": "claude-cli", "base_url": None, "model": "sonnet",
            "timeout_seconds": 5, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.which = patch("llmtool.providers.claude_cli.shutil.which", return_value=str(self.executable))
        self.which.start()
        self.addCleanup(self.which.stop)

    def client(self, **provider_updates):
        provider = {**self.provider_config, **provider_updates}
        return ClaudeCLIProvider(provider, {"temperature": 0, "max_tokens": 100})

    def complete_mode(self, mode, timeout=5):
        original = ClaudeCLIProvider._child_environment
        with patch.object(ClaudeCLIProvider, "_child_environment",
                          staticmethod(lambda: {**original(), "CLAUDE_TEST_MODE": mode})):
            return self.client(timeout_seconds=timeout).complete([{"role": "user", "content": "hello"}])

    def test_coded_runtime_sites_use_one_fake_process_and_no_private_data(self):
        cases = (("timeout", "PROVIDER_TIMEOUT"), ("failure", "PROVIDER_RUNTIME_ERROR"),
                 ("large", "MODEL_RESPONSE_TOO_LARGE"), ("empty", "MODEL_RESPONSE_INVALID"),
                 ("invalid", "MODEL_RESPONSE_INVALID"))
        for mode, code in cases:
            with self.subTest(mode=mode), patch("llmtool.providers.claude_cli.OUTPUT_LIMIT", 128), \
                 patch("llmtool.providers.claude_cli.subprocess.Popen", wraps=subprocess.Popen) as spawn:
                with self.assertRaises(LLMToolError) as caught:
                    self.complete_mode(mode, timeout=0.2 if mode == "timeout" else 5)
                diagnostic = caught.exception.diagnostic
                self.assertEqual(diagnostic.code.value, code)
                self.assertFalse(diagnostic.fallback_allowed)
                self.assertNotIn("PRIVATE_CLI_DIAGNOSTIC", diagnostic.to_json())
                if mode == "failure":
                    self.assertEqual(diagnostic.safe_cause["process_status"], 7)
                spawn.assert_called_once()
        with patch("llmtool.providers.claude_cli.shutil.which", return_value=None), \
             patch("llmtool.providers.claude_cli.subprocess.Popen") as spawn:
            with self.assertRaises(LLMToolError) as caught:
                self.client().complete([{"role": "user", "content": "hello"}])
            self.assertEqual(caught.exception.diagnostic.code.value, "ENV_PROVIDER_RUNTIME_MISSING")
            spawn.assert_not_called()

    def test_argv_stdin_and_successful_output_use_safe_noninteractive_mode(self):
        prompt = [{"role": "system", "content": "Follow instruction."},
                  {"role": "user", "content": "Reply with marker."}]
        reply = self.client().complete(prompt)
        argv = json.loads(self.args_file.read_text(encoding="utf-8"))
        self.assertEqual(argv, [
            "--print", "--output-format", "text", "--no-session-persistence",
            "--safe-mode", "--tools", "WebSearch,WebFetch", "--model", "sonnet",
        ])
        tools = argv[argv.index("--tools") + 1].split(",")
        self.assertFalse({"Read", "Write", "Edit", "Bash"}.intersection(tools))
        self.assertEqual(self.input_file.read_text(encoding="utf-8"),
                         "SYSTEM:\nFollow instruction.\n\nUSER:\nReply with marker.")
        self.assertEqual(reply.content, "CLAUDE_FINAL")
        self.assertNotIn("PRIVATE_CLI_DIAGNOSTIC", repr(reply))

    def test_tui_background_invocation_uses_bounded_cli_process(self):
        outcome = []

        def invoke():
            try:
                with background_cli_invocation():
                    outcome.append(self.client().complete(
                        [{"role": "user", "content": "hello"}]).content)
            except BaseException as exc:
                outcome.append(exc)

        worker = threading.Thread(target=invoke)
        with tui_cli_signal_scope(lambda signum: None):
            worker.start()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "Claude CLI request thread did not finish")
        self.assertEqual(outcome, ["CLAUDE_FINAL"])
        self.assertTrue(self.args_file.exists())

    def test_tui_background_timeout_reaps_owned_cli_process(self):
        outcome = []
        spawned = []
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        def invoke():
            try:
                with background_cli_invocation():
                    self.complete_mode("timeout", timeout=0.2)
            except BaseException as exc:
                outcome.append(exc)

        worker = threading.Thread(target=invoke)
        with patch("llmtool.providers.claude_cli.subprocess.Popen", side_effect=spawn), \
                tui_cli_signal_scope(lambda signum: None):
            worker.start()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "timed-out Claude CLI request thread did not finish")
        self.assertEqual(len(outcome), 1)
        self.assertEqual(outcome[0].diagnostic.code.value, "PROVIDER_TIMEOUT")
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].returncode)

    def test_process_uses_argv_without_shell(self):
        real_popen = subprocess.Popen
        calls = []

        def checked_popen(*args, **kwargs):
            calls.append((args, kwargs.copy()))
            return real_popen(*args, **kwargs)

        with patch("llmtool.providers.claude_cli.subprocess.Popen", side_effect=checked_popen):
            self.client().complete([{"role": "user", "content": "hello"}])
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][1]["shell"], False)
        self.assertIsInstance(calls[0][0][0], list)

    def test_runtime_home_keeps_claude_auth_separate_from_llmtool_home(self):
        with patch.dict(os.environ, {
            "HOME": str(self.root / "isolated-llmtool-home"),
            "LLMTOOL_CLAUDE_CLI_HOME": str(self.root / "claude-auth-home"),
        }):
            self.client().complete([{"role": "user", "content": "hello"}])
        self.assertEqual(self.home_file.read_text(encoding="utf-8"),
                         str(self.root / "claude-auth-home") + "\n")

    def test_failure_and_stderr_are_sanitized(self):
        with patch.object(ClaudeCLIProvider, "_child_environment",
                          return_value={**ClaudeCLIProvider._child_environment(), "CLAUDE_TEST_MODE": "failure"}):
            with self.assertRaises(LLMToolError) as caught:
                self.client().complete([{"role": "user", "content": "private prompt"}])
        self.assertEqual(str(caught.exception), "Claude CLI exited with status 7")
        self.assertNotIn("PRIVATE_CLI_DIAGNOSTIC", str(caught.exception))
        self.assertNotIn("private prompt", str(caught.exception))

    def test_timeout_is_normalized(self):
        with self.assertRaisesRegex(LLMToolError, "^Claude CLI timed out$"):
            self.complete_mode("timeout", timeout=0.1)

    def test_empty_output_is_rejected(self):
        with patch.object(ClaudeCLIProvider, "_child_environment",
                          return_value={**ClaudeCLIProvider._child_environment(), "CLAUDE_TEST_MODE": "empty"}):
            with self.assertRaisesRegex(LLMToolError, "^Claude CLI returned no final text$"):
                self.client().complete([{"role": "user", "content": "hello"}])

    def test_oversized_output_is_bounded(self):
        for mode in ("large", "large-stderr"):
            with self.subTest(mode=mode), patch("llmtool.providers.claude_cli.OUTPUT_LIMIT", 128):
                with self.assertRaisesRegex(LLMToolError, "^Claude CLI output exceeded 1 MiB$"):
                    with patch.object(ClaudeCLIProvider, "_child_environment",
                                      return_value={**ClaudeCLIProvider._child_environment(), "CLAUDE_TEST_MODE": mode}):
                        self.client().complete([{"role": "user", "content": "hello"}])

    def test_descendant_holding_pipes_is_killed_after_leader_exits(self):
        with self.assertRaisesRegex(LLMToolError, "^Claude CLI timed out$"):
            self.complete_mode("descendant", timeout=0.5)

    def test_child_environment_is_allowlisted_and_cwd_is_isolated(self):
        with patch.dict(os.environ, {"CLAUDE_TEST_MODE": "success", "UNRELATED_API_KEY": "MUST_NOT_LEAK",
                                     "NODE_OPTIONS": "--require evil", "GIT_DIR": "/tmp/evil"}):
            env = ClaudeCLIProvider._child_environment()
            self.assertNotIn("UNRELATED_API_KEY", env)
            self.assertNotIn("NODE_OPTIONS", env)
            self.assertNotIn("GIT_DIR", env)
            self.client().complete([{"role": "user", "content": "hello"}])
        isolated = Path(self.cwd_file.read_text(encoding="utf-8"))
        self.assertNotEqual(isolated, Path.cwd())
        self.assertFalse(isolated.exists(), "temporary Claude cwd should be removed after invocation")

    def test_relative_executable_resolution_is_rejected(self):
        with patch("llmtool.providers.claude_cli.shutil.which", return_value="./claude"):
            with self.assertRaisesRegex(LLMToolError, "executable is not available"):
                self.client().complete([{"role": "user", "content": "hello"}])

    def test_prompt_encoding_fails_before_spawn(self):
        with patch("llmtool.providers.claude_cli.subprocess.Popen", side_effect=AssertionError("spawned")):
            with self.assertRaisesRegex(LLMToolError, "invalid conversation input"):
                self.client().complete([{"role": "user", "content": "\\ud800".encode().decode("unicode_escape")}])

    def test_missing_executable_fails_safely(self):
        with patch("llmtool.providers.claude_cli.shutil.which", return_value=None):
            with self.assertRaisesRegex(LLMToolError, "^Claude CLI executable is not available$"):
                self.client().complete([{"role": "user", "content": "hello"}])

    def test_unsupported_options_and_injection_shaped_model_are_rejected(self):
        for provider in (
            {**self.provider_config, "provider_options": {"allow_tools": True}},
            {**self.provider_config, "model": "--permission-mode"},
            {**self.provider_config, "base_url": "https://example.test"},
            {**self.provider_config, "api_key": "fake-key"},
            {**self.provider_config, "api_key_env": "FAKE_KEY"},
        ):
            with self.subTest(provider=provider), self.assertRaises(LLMToolError):
                ClaudeCLIProvider.validate_config(provider)
                ClaudeCLIProvider.validate_options(provider["provider_options"])

    def test_capabilities_and_registry(self):
        self.assertIn("claude-cli", PROVIDER_REGISTRY.names())
        self.assertEqual(get_provider_capabilities("claude-cli").to_dict(), CAPABILITIES)


class ClaudeCLIConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.config_path = self.home / ".config/llmtool/config.yaml"

    def write_user_config(self, provider):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.safe_dump({
            "providers": {"claude": {"type": "claude-cli", **provider}},
            "active_provider": "claude",
        }), encoding="utf-8")

    def resolve(self):
        return resolve_config(cwd=self.cwd, home=self.home)

    def test_local_cli_profile_needs_no_http_or_api_credential(self):
        self.write_user_config({"model": "sonnet"})
        provider = self.resolve()["providers"]["claude"]
        self.assertEqual(provider["type"], "claude-cli")
        self.assertIsNone(provider["base_url"])
        self.assertIsNone(provider["api_key"])
        self.assertIsNone(provider["api_key_env"])

    def test_user_config_rejects_http_and_auth_fields(self):
        for field, value in (("base_url", "https://example.test"),
                             ("api_key", "fake-key"), ("api_key_env", "FAKE_KEY")):
            with self.subTest(field=field):
                self.write_user_config({field: value})
                with self.assertRaises(LLMToolError):
                    self.resolve()

    def test_project_config_cannot_set_executable_or_auth(self):
        self.write_user_config({"model": "sonnet"})
        for fragment in (
            {"providers": {"claude": {"executable": "/tmp/fake-claude"}}},
            {"providers": {"claude": {"api_key_env": "FAKE_KEY"}}},
        ):
            with self.subTest(fragment=fragment):
                (self.cwd / ".llmtool.yaml").write_text(yaml.safe_dump(fragment), encoding="utf-8")
                with self.assertRaises(LLMToolError):
                    self.resolve()




class WorkerEnvelopeTests(unittest.TestCase):
    def envelope(self, **changes):
        v=dict(type='result',subtype='success',is_error=False,
               session_id='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',result='{}')
        v.update(changes)
        return json.dumps(v)

    def test_single_terminal_outer_protocol_not_model_identity(self):
        from llmtool.providers.claude_cli import decode_review_envelope
        result=decode_review_envelope(self.envelope())
        self.assertEqual(result['runtime_session_id'],'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
        self.assertEqual(result['content'],'{}')
        self.assertIsNone(result['input_tokens'])
        self.assertEqual(result['identity_source'],'CLI_REPORTED_RUNTIME_ID')

    def test_outer_protocol_rejects_ambiguous_error_and_spoof(self):
        from llmtool.providers.claude_cli import decode_review_envelope
        raw=self.envelope()
        cases=[raw+raw,raw[:-1],self.envelope(subtype='error_during_execution'),
               self.envelope(is_error=True),self.envelope(session_id='not-uuid'),
               self.envelope(type='assistant'),self.envelope(extra='private'),
               self.envelope(result='\ud800'),self.envelope(result='x'*65537),
               raw.replace('"type": "result"','"type":"result","type":"result"')]
        for v in cases:
            with self.subTest(v=v[:70]),self.assertRaises(LLMToolError):decode_review_envelope(v)
        with self.assertRaises(LLMToolError):
            decode_review_envelope(raw,forbidden_sessions=('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',))


    def test_usage_is_optional_allowlisted_and_never_measured_cost(self):
        from llmtool.providers.claude_cli import decode_review_envelope
        for usage in (None,{},dict(input_tokens=True,output_tokens=2),
                      dict(input_tokens=1),dict(input_tokens=-1,output_tokens=1),
                      dict(input_tokens=1,output_tokens=2,arbitrary=123)):
            result=decode_review_envelope(self.envelope(usage=usage))
            self.assertIsNone(result['input_tokens']);self.assertIsNone(result['output_tokens'])
        result=decode_review_envelope(self.envelope(usage=dict(input_tokens=1,output_tokens=2)))
        self.assertEqual((result['input_tokens'],result['output_tokens']),(1,2))
        self.assertEqual(result['usage_source'],'CLI_REPORTED')
        self.assertNotIn('total_cost_usd',result)

    def test_outer_and_nested_privacy_unicode_depth_and_limits(self):
        from llmtool.providers.claude_cli import decode_review_envelope
        cases=[self.envelope(result=json.dumps({'summary':'Bearer FAKE_SECRET_12345678'})),
               self.envelope(result=json.dumps({'summary':'/Users/alice/private.txt'})),
               self.envelope(result='['*17+'0'+']'*17),
               self.envelope(usage={'server_tool_use':{'private':'Bearer FAKE_SECRET_12345678'}}),
               b'\xff',self.envelope(duration_ms=True),self.envelope(stop_reason='tool_use'),
               self.envelope(total_cost_usd=-1),self.envelope(uuid='invalid')]
        for value in cases:
            with self.subTest(value=str(value)[:50]),self.assertRaises(LLMToolError) as caught:
                decode_review_envelope(value)
            self.assertNotIn('FAKE_SECRET',str(caught.exception))

    def test_claude_2_1_303_telemetry_and_usage_fields_accepted_and_validated(self):
        from llmtool.providers.claude_cli import decode_review_envelope
        valid = self.envelope(
            api_error_status=None,
            duration_ms=18968,
            duration_api_ms=18656,
            fast_mode_disabled_reason='sdk_opt_in_required',
            fast_mode_state='off',
            first_content_frame_ms=1797,
            num_turns=1,
            permission_denials=[],
            queued_turn_count=0,
            result_index=0,
            safety_stops=0,
            stop_reason='end_turn',
            subagent_stats={'spawned': 0},
            terminal_reason='completed',
            time_to_request_ms=208,
            total_cost_usd=0.30,
            ttft_ms=1987,
            ttft_stream_ms=1796,
            uuid='a9c25395-d0c0-4453-95d6-27d06c0c50fa',
            modelUsage={'claude': {'tokens': 10}},
            usage=dict(
                input_tokens=42,
                output_tokens=19,
                cache_creation=dict(ephemeral_5m_input_tokens=0),
                cache_creation_input_tokens=100,
                cache_read_input_tokens=200,
                fallback_credit=None,
                inference_geo='global',
                iterations=[],
                output_tokens_details=dict(reasoning_tokens=0),
                server_tool_use=dict(web_search_requests=0),
                service_tier='standard',
                speed='standard',
            ),
        )
        decoded = decode_review_envelope(valid)
        self.assertEqual((decoded['input_tokens'], decoded['output_tokens']), (42, 19))
        for bad in (
            self.envelope(api_error_status=500),
            self.envelope(terminal_reason='aborted'),
            self.envelope(fast_mode_state='invalid'),
            self.envelope(ttft_ms=-1),
            self.envelope(ttft_ms=True),
            self.envelope(safety_stops=False),
            self.envelope(subagent_stats=['not-dict']),
        ):
            with self.subTest(bad=bad[:60]), self.assertRaises(LLMToolError):
                decode_review_envelope(bad)


    def test_worker_really_blocked_stdin_has_bounded_cleanup(self):
        import time
        from llmtool.providers import cli_lifecycle as lifecycle
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'fake-blocked';exe.write_text('#!'+sys.executable+' -IB\nimport time\ntime.sleep(10)\n');exe.chmod(0o700)
            provider=ClaudeCLIProvider({'model':REVIEW_MODEL},None)
            real_wait=subprocess.Popen.wait
            def bounded_wait(process,*args,**kwargs):
                self.assertIsNotNone(kwargs.get('timeout'))
                return real_wait(process,*args,**kwargs)
            started=time.monotonic()
            with patch.object(lifecycle,'terminate',side_effect=AssertionError('unbounded')), \
                    patch.object(subprocess.Popen,'wait',bounded_wait):
                with self.assertRaises(LLMToolError) as caught:
                    provider._invoke_review_job(str(exe),'x'*524288,deadline=started+0.5,
                        auth_home=tmp,launch_nonce='a'*32,runtime_pin='b'*64,forbidden_sessions=())
            self.assertEqual(caught.exception.review_code,'REVIEW_TIMEOUT')
            self.assertLess(time.monotonic()-started,1.5)
            self.assertIsNone(provider.pending_job)

    def test_review_job_refuses_unlisted_and_cross_family_model_before_spawn(self):
        # #1502 WP-E R1: the review job admits only the claude-reviewer listed model.
        import time
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'fake-unused';exe.write_text('#!'+sys.executable+' -IB\n');exe.chmod(0o700)
            for model in ('claude-unlisted-model', CROSS_FAMILY_MODEL,
                          'anthropic/'+REVIEW_MODEL, REVIEW_MODEL.upper(), None):
                with self.subTest(model=model):
                    provider=ClaudeCLIProvider({'model':model},None)
                    with patch('subprocess.Popen',side_effect=AssertionError('spawn')) as spawn, \
                            self.assertRaises(LLMToolError) as caught:
                        provider._invoke_review_job(str(exe),'prompt',deadline=time.monotonic()+5,
                            auth_home=tmp,launch_nonce='a'*32,runtime_pin='b'*64,
                            forbidden_sessions=())
                    self.assertEqual(caught.exception.review_code,'ROLE_MODEL_NOT_ALLOWED')
                    spawn.assert_not_called()
                    self.assertIsNone(getattr(provider,'pending_job',None))

    def test_review_job_launches_the_listed_model_explicitly(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            exe=Path(tmp)/'fake-unused';exe.write_text('#!'+sys.executable+' -IB\n');exe.chmod(0o700)
            provider=ClaudeCLIProvider({'model':REVIEW_MODEL},None)
            launched=[]
            def refuse_after_capture(argv,*args,**kwargs):
                launched.append(argv)
                raise OSError('fixture stops before a real process')
            with patch('subprocess.Popen',side_effect=refuse_after_capture), \
                    self.assertRaises(Exception):
                provider._invoke_review_job(str(exe),'prompt',deadline=time.monotonic()+5,
                    auth_home=tmp,launch_nonce='a'*32,runtime_pin='b'*64,forbidden_sessions=())
        self.assertEqual(len(launched),1)
        self.assertEqual(launched[0][launched[0].index('--model')+1],REVIEW_MODEL)


if __name__ == "__main__":
    unittest.main()
