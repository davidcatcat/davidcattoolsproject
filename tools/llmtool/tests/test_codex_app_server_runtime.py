import json
import os
import signal
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from llmtool.codex_app_server import AppServerTimeout
from llmtool.codex_app_server_budget import RunLedger
from llmtool.codex_app_server_runtime import (
    DEAD_ENDPOINT, FEATURES_OFF, AppServerProcess, AppServerSession, CodexAppServerStrategy,
    build_isolated_home, child_argv, child_environment, group_members, new_synthetic_token,
    validate_loopback_endpoint)
from llmtool.errors import LLMToolError
from llmtool.providers.codex_cli import CodexCLIProvider
from llmtool.providers.cli_lifecycle import CLIProcessLifecycle

# A stand-in child speaking just enough of the 0.163.0 initialize protocol. It never
# touches the network and is the only "server" these tests start (no real codex).
FAKE_CHILD = '''#!%(python)s -I
import json, os, signal, subprocess, sys, time

BEHAVIOR = "@@BEHAVIOR@@"
DUMP = "@@DUMP@@"
PIDFILE = "@@PIDFILE@@"
with open(DUMP, "w") as dump:
    json.dump({"env": dict(os.environ), "cwd": os.getcwd(), "argv": sys.argv}, dump)
if BEHAVIOR == "mute":
    time.sleep(60)          # never reads stdin: a write larger than the pipe buffer must stall
if BEHAVIOR in ("orphan", "escaped"):
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                             start_new_session=BEHAVIOR == "escaped")
    with open(PIDFILE, "w") as pids:
        pids.write(str(child.pid))
if BEHAVIOR == "deaf":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        agent = "fake/9.9.9 (test)" if BEHAVIOR == "badversion" else "fake/0.163.0 (test)"
        reply = {"id": message["id"],
                 "result": {"userAgent": agent, "codexHome": os.environ["CODEX_HOME"]}}
        sys.stdout.write(json.dumps(reply) + "\\n")
        sys.stdout.flush()
if BEHAVIOR in ("stubborn", "deaf"):
    time.sleep(60)
''' % {"python": sys.executable}


class RuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.dump = self.root / "dump.json"
        self.pidfile = self.root / "child.pid"
        self.ledger = RunLedger(self.root / "ledger", "run-1")

    def fake(self, behavior="normal"):
        path = self.root / ("fake-" + behavior)
        path.write_text(FAKE_CHILD.replace("@@BEHAVIOR@@", behavior)
                        .replace("@@DUMP@@", str(self.dump))
                        .replace("@@PIDFILE@@", str(self.pidfile)))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def isolated(self, name="iso"):
        directory = self.root / name
        directory.mkdir()
        return build_isolated_home(directory)

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


class IsolatedHomeTests(RuntimeTestCase):
    def test_endpoint_must_be_a_plain_loopback_http_origin(self):
        self.assertEqual(validate_loopback_endpoint("http://127.0.0.1:8123"),
                         "http://127.0.0.1:8123")
        self.assertEqual(validate_loopback_endpoint("http://127.0.0.1:9/"), DEAD_ENDPOINT)
        for bad in ("https://127.0.0.1:80", "http://localhost:80", "http://0.0.0.0:80",
                    "http://example.com:80", "http://127.0.0.1", "http://127.0.0.1:0",
                    "http://127.0.0.1:99999", "http://u:p@127.0.0.1:80",
                    "http://127.0.0.1:80/v1", "http://127.0.0.1:80/?x=1",
                    "http://127.0.0.1:80/#f", "ws://127.0.0.1:80", "", None, 80):
            with self.subTest(endpoint=bad), self.assertRaises(LLMToolError):
                validate_loopback_endpoint(bad)

    def test_home_is_minimal_synthetic_and_private(self):
        isolated = self.isolated()
        config = (isolated.codex_home / "config.toml").read_text()
        self.assertIn('openai_base_url = "http://127.0.0.1:9/v1"', config)
        self.assertIn('chatgpt_base_url = "http://127.0.0.1:9/backend-api/"', config)
        for name in FEATURES_OFF:
            self.assertIn(name + " = false", config)
        for forbidden in ("mcp_servers", "projects", "trust"):
            self.assertNotIn(forbidden, config)
        auth = isolated.codex_home / "auth.json"
        self.assertEqual(json.loads(auth.read_text()), {"OPENAI_API_KEY": isolated.token})
        self.assertTrue(isolated.token.startswith("sk-synthetic-"))
        self.assertEqual(auth.stat().st_mode & 0o777, 0o600)
        for directory in (isolated.home, isolated.codex_home, isolated.run_dir, isolated.tmp_dir):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(sorted(p.name for p in isolated.run_dir.iterdir()), [])

    def test_tokens_are_not_reused_between_homes(self):
        self.assertNotEqual(self.isolated("a").token, self.isolated("b").token)

    def test_environment_is_an_allowlist_that_never_inherits_host_state(self):
        isolated = self.isolated()
        host = {"OPENAI_API_KEY": "host-secret", "CODEX_HOME": "/host/.codex", "HOME": "/host",
                "HTTPS_PROXY": "http://evil:1", "GITHUB_TOKEN": "x", "PATH": "/usr/bin:/bin"}
        with patch.dict(os.environ, host, clear=True):
            env = child_environment(isolated)
        self.assertEqual(sorted(env), sorted([
            "PATH", "HOME", "CODEX_HOME", "TMPDIR", "LANG", "NO_PROXY", "no_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]))
        self.assertNotIn("host-secret", json.dumps(env))
        self.assertEqual(env["HOME"], str(isolated.home))
        self.assertEqual(env["CODEX_HOME"], str(isolated.codex_home))
        self.assertEqual(env["TMPDIR"], str(isolated.tmp_dir))
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.assertEqual(env[name], DEAD_ENDPOINT)
        self.assertEqual(env["NO_PROXY"], "127.0.0.1,localhost,::1")

    def test_argv_pins_stdio_strict_config_and_disables_exec_tools(self):
        self.assertEqual(child_argv("/x/codex"), [
            "/x/codex", "app-server", "--listen", "stdio://", "--strict-config",
            "--disable", "unified_exec", "--disable", "shell_tool",
            "--disable", "shell_snapshot"])


class AppServerProcessTests(RuntimeTestCase):
    def test_relative_executable_is_refused(self):
        with self.assertRaises(LLMToolError):
            AppServerProcess("codex", self.isolated(), self.ledger)

    def test_normal_session_handshake_clean_shutdown_and_cleanup(self):
        with AppServerSession(self.fake(), self.ledger) as session:
            home = session.isolated
            identity = session.client.initialize(expected_codex_home=str(home.codex_home))
            self.assertEqual(identity["version"], "0.163.0")
            pgid = session.process.pgid
            self.assertEqual(pgid, session.process.pid)
            self.assertNotEqual(pgid, os.getpgrp())
        report = session.process.shutdown_report
        self.assertEqual(report, {"returncode": 0, "outcome": "CLEAN", "group_empty": True,
                                  "descendant_cleanup": "UNKNOWN", "escaped_descendants": 0})
        self.assertEqual(group_members(pgid), [])
        self.assertFalse(home.home.parent.exists())
        self.assertEqual(self.ledger.snapshot()["launches"], 1)

    def test_shell_snapshot_is_disabled_in_config_and_launched_argv(self):
        # Check the launch boundary using an offline stand-in, never the real Codex.
        with AppServerSession(self.fake(), self.ledger) as session:
            session.client.initialize()
            config = (session.isolated.codex_home / "config.toml").read_text()
            self.assertIn("shell_snapshot = false\n", config)
        argv = json.loads(self.dump.read_text())["argv"]
        self.assertIn(["--disable", "shell_snapshot"],
                      [argv[index:index + 2] for index in range(len(argv) - 1)])
        self.assertEqual(session.process.shutdown_report["outcome"], "CLEAN")
        self.assertTrue(session.process.shutdown_report["group_empty"])

    def test_child_sees_only_the_isolated_environment(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "host-secret"}):
            with AppServerSession(self.fake(), self.ledger) as session:
                session.client.initialize()
                run_dir = session.isolated.run_dir
                codex_home = session.isolated.codex_home
        seen = json.loads(self.dump.read_text())
        self.assertNotIn("host-secret", json.dumps(seen))
        self.assertEqual(seen["env"]["CODEX_HOME"], str(codex_home))
        self.assertEqual(os.path.realpath(seen["cwd"]), os.path.realpath(str(run_dir)))
        self.assertEqual(seen["argv"][1:], child_argv("x")[1:])

    def test_unsupported_version_is_refused_and_still_shuts_down_cleanly(self):
        with self.assertRaisesRegex(LLMToolError, "unsupported"):
            with AppServerSession(self.fake("badversion"), self.ledger) as session:
                session.client.initialize()
        self.assertEqual(session.process.shutdown_report["outcome"], "CLEAN")

    def test_foreign_codex_home_is_refused(self):
        with self.assertRaisesRegex(LLMToolError, "isolated home"):
            with AppServerSession(self.fake(), self.ledger) as session:
                session.client.initialize(expected_codex_home="/host/.codex")

    def test_leftover_descendants_are_swept_and_reported(self):
        with AppServerSession(self.fake("orphan"), self.ledger) as session:
            session.client.initialize()
            pgid = session.process.pgid
        orphan = int(self.pidfile.read_text())
        report = session.process.shutdown_report
        self.assertEqual(report["outcome"], "SWEPT")
        self.assertTrue(report["group_empty"])
        self.assertEqual(group_members(pgid), [])
        self.assertFalse(self.alive(orphan))

    def test_setsid_descendant_is_not_mistaken_for_complete_cleanup(self):
        process = AppServerProcess(self.fake("escaped"), self.isolated(), self.ledger)
        process.start()
        self.addCleanup(process.shutdown)
        process.client.initialize()
        escaped = int(self.pidfile.read_text())
        def cleanup_owned_fixture():
            try:
                os.kill(escaped, signal.SIGKILL)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + 3
            while self.alive(escaped) and time.monotonic() < deadline:
                time.sleep(0.02)
        self.addCleanup(cleanup_owned_fixture)
        report = process.shutdown()
        self.assertTrue(report["group_empty"])
        self.assertEqual(report["outcome"], "UNKNOWN")
        self.assertEqual(report["descendant_cleanup"], "UNKNOWN")
        self.assertGreaterEqual(report["escaped_descendants"], 1)
        self.assertTrue(self.alive(escaped))  # only the test owns out-of-group teardown

    def test_process_observation_error_does_not_skip_owned_group_teardown(self):
        process = AppServerProcess(self.fake(), self.isolated(), self.ledger)
        process.start()
        process.client.initialize()
        with patch("llmtool.codex_app_server_runtime.process_table",
                   side_effect=OSError("PRIVATE_PROCESS_TABLE")):
            report = process.shutdown()
        self.assertEqual(report["outcome"], "UNKNOWN")
        self.assertTrue(report["group_empty"])
        self.assertEqual(report["descendant_cleanup"], "UNKNOWN")
        self.assertFalse(self.alive(process.pid))

    def test_child_that_ignores_stdin_close_is_terminated_by_group(self):
        process = AppServerProcess(self.fake("stubborn"), self.isolated(), self.ledger)
        process.start()
        process.client.initialize()
        report = process.shutdown(grace=0.3)
        self.assertEqual(report["outcome"], "SWEPT")
        self.assertTrue(report["group_empty"])
        self.assertNotEqual(report["returncode"], 0)
        self.assertEqual(group_members(process.pgid), [])

    def test_child_that_ignores_sigterm_is_killed(self):
        process = AppServerProcess(self.fake("deaf"), self.isolated(), self.ledger)
        process.start()
        process.client.initialize()
        report = process.shutdown(grace=0.3)
        self.assertEqual((report["outcome"], report["group_empty"]), ("SWEPT", True))
        self.assertEqual(group_members(process.pgid), [])

    def test_child_that_stops_reading_cannot_hold_a_large_write_past_the_deadline(self):
        process = AppServerProcess(self.fake("mute"), self.isolated(), self.ledger, 0.5)
        process.start()
        started = time.monotonic()
        with self.assertRaises(AppServerTimeout):
            process.client.request("big", {"blob": "x" * (1 << 20)})   # >> pipe buffer
        self.assertLess(time.monotonic() - started, 3)
        report = process.shutdown(grace=0.3)
        self.assertEqual((report["outcome"], report["group_empty"]), ("SWEPT", True))
        self.assertNotEqual(report["returncode"], 0)
        self.assertEqual(group_members(process.pgid), [])
        self.assertFalse(self.alive(process.pid))

    def test_shutdown_is_idempotent_and_never_raises_before_start(self):
        process = AppServerProcess(self.fake(), self.isolated(), self.ledger)
        self.assertIsNone(process.shutdown())
        process.start()
        first = process.shutdown()
        self.assertIs(process.shutdown(), first)

    def test_launch_budget_is_enforced_before_any_child_exists(self):
        ledger = RunLedger(self.root / "ledger2", "run-2", max_launches=2)
        for _ in range(2):
            with AppServerSession(self.fake(), ledger) as session:
                session.client.initialize()
        self.dump.unlink()
        with self.assertRaisesRegex(LLMToolError, "launch budget"):
            with AppServerSession(self.fake(), ledger):
                self.fail("a third launch must not start")
        self.assertFalse(self.dump.exists())
        self.assertEqual(ledger.snapshot()["launches"], 2)

    def test_restart_with_a_new_session_object_does_not_reset_the_launch_count(self):
        for _ in range(3):
            with AppServerSession(self.fake(), RunLedger(self.root / "l3", "run-3")) as session:
                session.client.initialize()
        self.assertEqual(RunLedger(self.root / "l3", "run-3").snapshot()["launches"], 3)

    def test_non_loopback_endpoint_never_starts_a_child(self):
        with self.assertRaises(LLMToolError):
            with AppServerSession(self.fake(), self.ledger, endpoint="http://example.com:80"):
                self.fail("must not start")
        self.assertFalse(self.dump.exists())
        self.assertEqual(self.ledger.snapshot()["launches"], 0)


# A richer stand-in child: speaks thread/turn, then makes real HTTP requests to the proxy
# found in its isolated CODEX_HOME, exactly like Codex would. Behavior is baked per test.
FAKE_SERVER = """#!%(python)s -I
import http.client, json, os, pathlib, sys, time, urllib.parse

BEHAVIOR = "@@BEHAVIOR@@"
RESULTS = "@@RESULTS@@"
home = pathlib.Path(os.environ["CODEX_HOME"])
config = (home / "config.toml").read_text()
base = [l.split('"')[1] for l in config.splitlines() if l.startswith("openai_base_url")][0]
token = json.load(open(home / "auth.json"))["OPENAI_API_KEY"]
port = urllib.parse.urlsplit(base).port
results = {"statuses": [], "client_replies": []}


def save():
    open(RESULTS, "w").write(json.dumps(results))


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()


def request(method, tools=None, headers=None):
    body = {"model": "m", "input": "hi", "stream": True}
    if tools is not None:
        body["tools"] = tools
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        send = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        send.update(headers or {})
        connection.request(method, "/v1/responses", headers=send,
                           body=json.dumps(body) if method == "POST" else None)
        response = connection.getresponse()
        response.read()
        status = response.status
    except Exception as exc:
        status = type(exc).__name__
    results["statuses"].append(status)
    save()
    return status


def finish(ok=True):
    if ok:
        out({"method": "item/completed", "params": {"threadId": "t1", "turnId": "u1", "item": {
            "type": "agentMessage", "id": "m", "text": "FAKE_ANSWER", "phase": "final_answer"}}})
    out({"method": "turn/completed", "params": {"threadId": "t1", "turn": {
        "id": "u1", "status": "completed" if ok else "failed"}}})


for line in sys.stdin:
    message = json.loads(line)
    ident, method = message.get("id"), message.get("method")
    if method is None:
        results["client_replies"].append(message)
        save()
    elif method == "initialize":
        out({"id": ident, "result": {"userAgent": "fake/0.163.0 (t)", "codexHome": str(home)}})
    elif method == "thread/start":
        out({"id": ident, "result": {"thread": {"id": "t1"}}})
        if BEHAVIOR == "stop-reading":
            time.sleep(60)      # answers the handshake, then never reads stdin again
    elif method == "turn/start":
        out({"id": ident, "result": {"turn": {"id": "u1"}}})
        if BEHAVIOR == "hang":
            continue
        if BEHAVIOR == "die":
            os._exit(3)
        if BEHAVIOR == "tool-item":
            out({"method": "item/started", "params": {"threadId": "t1", "item": {
                "type": "commandExecution", "id": "c"}}})
            finish()
            continue
        if BEHAVIOR == "server-request":
            out({"id": 500, "method": "item/commandExecution/requestApproval", "params": {}})
            results["client_replies"].append(json.loads(sys.stdin.readline()))
            save()
        if BEHAVIOR == "websocket":
            request("GET", headers={"Upgrade": "websocket", "Connection": "Upgrade"})
        if BEHAVIOR == "triple":
            for _ in range(3):
                request("POST")
            finish()
            continue
        tools = {"tools": [{"type": "function", "name": "update_plan"}],
                 "exec-tool": [{"type": "local_shell"}]}.get(BEHAVIOR)
        finish(request("POST", tools) == 200)
""" % {"python": sys.executable}


class StrategyUpstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, mode="ok"):
        super().__init__(("127.0.0.1", 0), _StrategyUpstreamHandler)
        self.mode = mode
        self.requests = []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return "http://127.0.0.1:%d" % self.server_address[1]

    def handle_error(self, *_):
        pass

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(5)


class _StrategyUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        with self.server.lock:
            self.server.requests.append(json.loads(body))
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        events = [{"type": "response.created", "response": {"id": "r"}}]
        if self.server.mode in ("ok", "overcap"):
            events.append({"type": "response.completed", "response": {
                "id": "r", "usage": {"input_tokens": 4,
                "output_tokens": 129 if self.server.mode == "overcap" else 2}}})
        for event in events:
            data = ("event: %s\ndata: %s\n\n" % (event["type"], json.dumps(event))).encode()
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        if self.server.mode in ("ok", "overcap"):
            self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class StrategyTests(RuntimeTestCase):
    """CodexCLIProvider -> CodexAppServerStrategy -> proxy/ledger/child, all offline."""

    def setUp(self):
        super().setUp()
        self.results = self.root / "results.json"
        self.upstream = StrategyUpstream()
        self.addCleanup(self.upstream.stop)
        self.dirs = []
        original = CodexCLIProvider._temporary_directory

        def recording():
            directory = original()
            self.dirs.append(directory.name)
            return directory

        patcher = patch.object(CodexCLIProvider, "_temporary_directory", staticmethod(recording))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_provider(self, behavior, timeout=10, allowed=(), ledger=None, upstream=None,
                     prompt_bytes=0):
        path = self.root / ("server-" + behavior)
        path.write_text(FAKE_SERVER.replace("@@BEHAVIOR@@", behavior)
                        .replace("@@RESULTS@@", str(self.results)))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.strategy = CodexAppServerStrategy(
            ledger or self.ledger, upstream or self.upstream.endpoint, allowed)
        provider = {"type": "codex-cli", "model": "test-model", "timeout_seconds": timeout,
                    "base_url": None, "api_key": None, "api_key_env": None,
                    "provider_options": {}}
        client = CodexCLIProvider(provider, {}, app_server=self.strategy)
        with patch("llmtool.providers.codex_cli.shutil.which", return_value=str(path)):
            content = "private prompt" if not prompt_bytes else "p" * prompt_bytes
            return client.complete([{"role": "user", "content": content}])

    def fake_results(self):
        return json.loads(self.results.read_text())

    def assert_cleaned_up(self, outcome="CLEAN"):
        self.assertEqual(self.strategy.last_report["shutdown"]["outcome"], outcome)
        self.assertTrue(self.strategy.last_report["shutdown"]["group_empty"])
        self.assertTrue(self.dirs)
        for directory in self.dirs:
            self.assertFalse(os.path.exists(directory), directory)

    def assert_code(self, caught, code, retryable=False):
        self.assertEqual(caught.exception.diagnostic.code.value, code)
        self.assertEqual(caught.exception.diagnostic.retryable, retryable)

    # -- the normal path -----------------------------------------------------
    def test_one_turn_flows_through_proxy_ledger_and_cleans_up(self):
        reply = self.run_provider("ok")
        self.assertEqual(reply.content, "FAKE_ANSWER")
        self.assertEqual(reply.metadata["model"], "test-model")
        self.assertEqual(self.fake_results()["statuses"], [200])
        self.assertEqual(len(self.upstream.requests), 1)
        self.assertEqual(self.upstream.requests[0]["max_output_tokens"], 128)
        snapshot = self.ledger.snapshot()
        self.assertEqual((snapshot["launches"], snapshot["requests"], snapshot["states"]),
                         (1, 1, ["SUCCEEDED"]))
        self.assert_cleaned_up()

    def test_client_refuses_server_approval_requests(self):
        self.assertEqual(self.run_provider("server-request").content, "FAKE_ANSWER")
        replies = self.fake_results()["client_replies"]
        self.assertEqual(replies, [{"id": 500, "error": {"code": -32601, "message": "not supported"}}])

    def test_websocket_upgrade_attempt_is_refused_and_http_still_works(self):
        self.assertEqual(self.run_provider("websocket").content, "FAKE_ANSWER")
        self.assertEqual(self.fake_results()["statuses"], [426, 200])
        self.assertEqual(len(self.upstream.requests), 1)

    def test_request_budget_holds_even_if_the_child_keeps_asking(self):
        self.run_provider("triple")
        self.assertEqual(self.fake_results()["statuses"], [200, 200, 429])
        self.assertEqual(len(self.upstream.requests), 2)
        self.assertEqual([r["max_output_tokens"] for r in self.upstream.requests], [128, 128])
        self.assertEqual(self.ledger.snapshot()["requests"], 2)

    # -- tool denial -----------------------------------------------------------
    def test_unlisted_tool_is_denied_before_the_upstream_and_the_turn_fails(self):
        with self.assertRaises(LLMToolError) as caught:
            self.run_provider("tools")
        self.assert_code(caught, "PROVIDER_RUNTIME_ERROR")
        self.assertEqual(self.fake_results()["statuses"], [403])
        self.assertEqual(self.upstream.requests, [])
        self.assertEqual(self.strategy.last_report["rejected_tools"], [{
            "code": "TOOLS_NOT_ALLOWED", "count": 1, "tools": ["function:update_plan"]}])
        self.assertEqual(self.ledger.snapshot()["requests"], 0)
        self.assert_cleaned_up()

    def test_execution_tool_is_denied_even_when_allowlisted(self):
        with self.assertRaises(LLMToolError):
            self.run_provider("exec-tool", allowed=(("local_shell", None),))
        self.assertEqual(self.fake_results()["statuses"], [403])
        self.assertEqual(self.upstream.requests, [])
        self.assertEqual(self.strategy.last_report["rejected_tools"][0]["code"],
                         "TOOL_EXECUTION_FORBIDDEN")
        self.assert_cleaned_up()

    def test_explicitly_allowed_benign_tool_is_forwarded(self):
        self.assertEqual(self.run_provider("tools", allowed=(("function", "update_plan"),)).content, "FAKE_ANSWER")
        self.assertEqual(self.upstream.requests[0]["tools"],
                         [{"type": "function", "name": "update_plan"}])

    def test_a_reported_tool_item_aborts_the_run_and_cleans_up(self):
        with self.assertRaises(LLMToolError) as caught:
            self.run_provider("tool-item")
        self.assert_code(caught, "PROVIDER_RUNTIME_ERROR")
        self.assertEqual(self.upstream.requests, [])
        self.assert_cleaned_up()

    # -- timeout / disconnect / UNKNOWN ---------------------------------------
    def test_timeout_is_not_retryable_and_leaves_nothing_behind(self):
        started = time.monotonic()
        with self.assertRaises(LLMToolError) as caught:
            self.run_provider("hang", timeout=2)
        self.assertLess(time.monotonic() - started, 8)
        self.assert_code(caught, "PROVIDER_TIMEOUT", retryable=False)
        self.assertEqual(self.upstream.requests, [])
        self.assert_cleaned_up()

    def test_child_disconnect_mid_turn_is_a_runtime_error_and_cleans_up(self):
        with self.assertRaises(LLMToolError) as caught:
            self.run_provider("die")
        self.assert_code(caught, "PROVIDER_RUNTIME_ERROR")
        self.assertEqual(self.strategy.last_report["shutdown"]["returncode"], 3)
        self.assert_cleaned_up()

    def test_unknown_upstream_outcome_fails_the_turn_and_is_never_replayed_later(self):
        self.upstream.mode = "truncate"
        with self.assertRaises(LLMToolError):
            self.run_provider("ok")
        self.assertEqual(self.ledger.snapshot()["states"], ["UNKNOWN"])
        with self.assertRaises(LLMToolError):                  # a whole new run, same ledger
            self.run_provider("ok")
        self.assertEqual(self.fake_results()["statuses"], [429])
        self.assertEqual(len(self.upstream.requests), 1)       # the UNKNOWN request was not resent
        self.assertEqual(self.ledger.snapshot()["launches"], 2)
        self.assert_cleaned_up()

    # -- launch budget and construction ---------------------------------------
    def test_launch_budget_stops_a_new_run_before_any_child_starts(self):
        ledger = RunLedger(self.root / "single", "run-single", max_launches=1)
        self.run_provider("ok", ledger=ledger)
        self.results.unlink()
        with self.assertRaises(LLMToolError) as caught:
            self.run_provider("ok", ledger=ledger)
        self.assert_code(caught, "PROVIDER_RUNTIME_ERROR")
        self.assertFalse(self.results.exists())                # the second child never ran
        self.assertEqual(ledger.snapshot()["launches"], 1)
        self.assertEqual(len(self.upstream.requests), 1)
        for directory in self.dirs:
            self.assertFalse(os.path.exists(directory), directory)

    def test_upstream_must_be_loopback_at_construction(self):
        for bad in ("http://example.com:80", "https://127.0.0.1:443", "http://localhost:80"):
            with self.subTest(upstream=bad), self.assertRaises(LLMToolError):
                CodexAppServerStrategy(self.ledger, bad)

    def record_real_shutdowns(self, claim=None):
        """Wrap AppServerProcess.shutdown: the real teardown always runs and is recorded;
        `claim` (a dict of overrides) changes only what the strategy is told afterwards."""
        real_shutdown = AppServerProcess.shutdown
        self.teardowns = []

        def shutdown(process, *args, **kwargs):
            real = real_shutdown(process, *args, **kwargs)
            self.teardowns.append((process.pid, process.pgid, real))
            return real if claim is None else dict(real, **claim)

        return patch.object(AppServerProcess, "shutdown", shutdown)

    def assert_really_torn_down(self):
        self.assertEqual(len(self.teardowns), 1)
        pid, pgid, real = self.teardowns[0]
        self.assertTrue(real["group_empty"], real)
        self.assertEqual(group_members(pgid), [])
        self.assertFalse(self.alive(pid))
        self.assertTrue(self.dirs)
        for directory in self.dirs:
            self.assertFalse(os.path.exists(directory), directory)
        return real

    def test_unconfirmed_cleanup_is_reported_not_swallowed(self):
        injected = {"outcome": "UNKNOWN", "group_empty": False}
        with self.record_real_shutdowns(claim=injected):
            with self.assertRaises(LLMToolError) as caught:
                self.run_provider("ok")
        self.assert_code(caught, "PROVIDER_RUNTIME_ERROR")
        self.assertIn("cleanup", str(caught.exception))
        real = self.assert_really_torn_down()
        self.assertEqual(real["outcome"], "CLEAN")                    # what actually happened
        reported = self.strategy.last_report["shutdown"]
        self.assertEqual((reported["outcome"], reported["group_empty"]), ("UNKNOWN", False))

    def test_large_prompt_to_a_child_that_stopped_reading_times_out_and_is_swept(self):
        started = time.monotonic()
        with self.record_real_shutdowns():
            with self.assertRaises(LLMToolError) as caught:
                self.run_provider("stop-reading", timeout=2, prompt_bytes=1 << 20)
        self.assertLess(time.monotonic() - started, 25)
        self.assert_code(caught, "PROVIDER_TIMEOUT", retryable=False)
        real = self.assert_really_torn_down()
        self.assertEqual(real["outcome"], "SWEPT")                     # it ignored stdin EOF
        self.assertNotEqual(real["returncode"], 0)
        self.assertEqual(self.upstream.requests, [])
        snapshot = self.ledger.snapshot()
        self.assertEqual((snapshot["launches"], snapshot["requests"]), (1, 0))

    def test_strategy_uses_existing_signal_lifecycle(self):
        events = []
        class ObservedLifecycle(CLIProcessLifecycle):
            def __enter__(self):
                events.append("enter")
                return super().__enter__()
            def own(self, process):
                events.append("own")
                return super().own(process)
            def __exit__(self, *args):
                events.append("exit")
                return super().__exit__(*args)
        with patch("llmtool.codex_app_server_runtime.CLIProcessLifecycle", ObservedLifecycle):
            self.assertEqual(self.run_provider("ok").content, "FAKE_ANSWER")
        self.assertEqual(events, ["enter", "own", "exit"])
        self.assertEqual(self.strategy.last_report["containment"],
                         {"os_egress": "NOT_VERIFIED", "descendants": "UNKNOWN"})
        self.assert_cleaned_up()

    def test_actual_output_overrun_is_a_distinct_failure_with_no_more_admission(self):
        self.upstream.mode = "overcap"
        with self.assertRaises(LLMToolError) as caught:
            self.run_provider("ok")
        self.assert_code(caught, "MODEL_RESPONSE_TOO_LARGE")
        self.assertEqual(self.ledger.snapshot()["states"], ["CAP_EXCEEDED"])
        self.assertEqual(self.ledger.snapshot()["usage"], [
            {"input_tokens": 4, "output_tokens": 129}])
        self.assertEqual(len(self.upstream.requests), 1)
        self.assert_cleaned_up()

    def test_isolated_home_oserror_is_sanitized_and_phase_reported(self):
        with patch("llmtool.codex_app_server_runtime.build_isolated_home",
                   side_effect=OSError("PRIVATE_HOME")):
            with self.assertRaisesRegex(LLMToolError, "^Codex App-server turn failed$"):
                self.run_provider("ok")
        self.assertEqual(self.strategy.last_report["phase"], "isolated_home")
        self.assertNotIn("PRIVATE_HOME", json.dumps(self.strategy.last_report))
        self.assertTrue(all(not os.path.exists(path) for path in self.dirs))

    def test_stderr_open_oserror_is_sanitized_and_phase_reported(self):
        with patch("llmtool.codex_app_server_runtime.open", create=True,
                   side_effect=OSError("PRIVATE_STDERR")):
            with self.assertRaisesRegex(LLMToolError, "^App-server could not be started$"):
                self.run_provider("ok")
        self.assertEqual(self.strategy.last_report["phase"], "process_start")
        self.assertNotIn("PRIVATE_STDERR", json.dumps(self.strategy.last_report))
        self.assertTrue(all(not os.path.exists(path) for path in self.dirs))

    def test_malformed_ledger_during_startup_is_sanitized_and_reported_unknown(self):
        self.ledger.reserve_launch()
        (self.root / "ledger" / "run-1.ledger.json").write_text('{"PRIVATE_STATE":true}')
        with self.assertRaisesRegex(LLMToolError, "^Codex App-server turn failed$"):
            self.run_provider("ok")
        self.assertEqual(self.strategy.last_report["phase"], "process_start")
        self.assertEqual(self.strategy.last_report["ledger"], {"status": "UNKNOWN"})
        self.assertNotIn("PRIVATE_STATE", json.dumps(self.strategy.last_report))
        self.assertTrue(all(not os.path.exists(path) for path in self.dirs))

    def test_report_snapshot_failure_cannot_leak_or_mask_the_primary_error(self):
        with patch.object(self.ledger, "snapshot", side_effect=KeyError("PRIVATE_LEDGER")):
            with self.assertRaisesRegex(LLMToolError, "^Codex App-server turn failed$"):
                self.run_provider("die")
        self.assertEqual(self.strategy.last_report["ledger"], {"status": "UNKNOWN"})
        self.assertEqual(self.strategy.last_report["phase"], "turn")
        self.assertNotIn("PRIVATE_LEDGER", json.dumps(self.strategy.last_report))
        self.assert_cleaned_up()

    def test_malformed_snapshot_result_cannot_turn_success_into_a_raw_exception(self):
        with patch.object(self.ledger, "snapshot", return_value=[]):
            with self.assertRaisesRegex(LLMToolError, "^Codex App-server turn failed$"):
                self.run_provider("ok")
        self.assertEqual(self.strategy.last_report["ledger"], {"status": "UNKNOWN"})
        self.assert_cleaned_up()

    def test_cleanup_exception_is_sanitized_after_actual_proxy_teardown(self):
        from llmtool.codex_responses_proxy import ResponsesProxy
        original = ResponsesProxy.close
        def fail_after_close(proxy):
            original(proxy)
            raise OSError("PRIVATE_CLEANUP")
        with patch.object(ResponsesProxy, "close", fail_after_close):
            with self.assertRaisesRegex(LLMToolError, "^Codex App-server cleanup could not be confirmed$"):
                self.run_provider("ok")
        self.assertEqual(self.strategy.last_report["cleanup_errors"], ["proxy"])
        self.assertNotIn("PRIVATE_CLEANUP", json.dumps(self.strategy.last_report))
        self.assert_cleaned_up()

    def test_synthetic_token_helpers(self):
        token = new_synthetic_token()
        self.assertTrue(token.startswith("sk-synthetic-"))
        self.assertNotEqual(token, new_synthetic_token())
        root = self.root / "tok"
        root.mkdir()
        for bad in ("sk-live-" + "x" * 30, "short", "sk-synthetic- spaced-token-value",
                    "sk-synthetic-" + "x" * 300, 5):
            with self.subTest(token=bad), self.assertRaises(LLMToolError):
                build_isolated_home(root, DEAD_ENDPOINT, bad)
        self.assertEqual(build_isolated_home(root, DEAD_ENDPOINT, token).token, token)


if __name__ == "__main__":
    unittest.main()
