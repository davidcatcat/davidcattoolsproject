"""Isolated synthetic CODEX_HOME and child-process lifecycle for the Codex App-server.

Configuration and proxy environment restrict this synthetic experiment, but do not
provide OS-level network or descendant containment. Native enforcement and socket
proof remain NOT_VERIFIED. No host Codex home or credential is read or copied:
auth.json holds a random synthetic key, and HOME, CODEX_HOME and TMPDIR point
inside a fresh temporary directory.
"""

import json
import os
import secrets
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .codex_app_server import AppServerClient, AppServerTimeout
from .errors import LLMToolError, coded_error
from .providers.cli_lifecycle import CLIProcessLifecycle

DEAD_ENDPOINT = "http://127.0.0.1:9"
DISABLED_FEATURES = ("unified_exec", "shell_tool", "shell_snapshot")
# Keep command-line execution disables in the isolated config too; in particular,
# shell snapshots can otherwise spawn a shell even when shell_tool is disabled.
FEATURES_OFF = DISABLED_FEATURES + (
    "plugins", "remote_plugin", "plugin_sharing", "apps", "hooks",
    "skill_mcp_dependency_install", "skill_search",
)
GRACE_SECONDS = 10.0
SWEEP_SECONDS = 2.0


def validate_loopback_endpoint(endpoint):
    """Return a normalized http://127.0.0.1:<port> origin; anything else is refused."""
    parts = port = None
    if isinstance(endpoint, str):
        try:
            parts = urlsplit(endpoint)
            port = parts.port
        except ValueError:
            parts = None
    if (parts is None or parts.scheme != "http" or parts.hostname != "127.0.0.1"
            or port is None or not 0 < port < 65536 or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise LLMToolError("app-server endpoint must be a loopback http origin")
    return "http://127.0.0.1:%d" % port


@dataclass(frozen=True)
class IsolatedHome:
    home: Path
    codex_home: Path
    run_dir: Path
    tmp_dir: Path
    token: str


def new_synthetic_token():
    return "sk-synthetic-" + secrets.token_hex(16)


def build_isolated_home(root, endpoint=DEAD_ENDPOINT, token=None):
    """Build a synthetic home with content-free filesystem errors."""
    try:
        return _build_isolated_home(root, endpoint, token)
    except OSError:
        raise LLMToolError("app-server isolated home unavailable") from None


def _build_isolated_home(root, endpoint=DEAD_ENDPOINT, token=None):
    """Create the synthetic tree under an existing empty directory *root*.

    A caller that also builds the Responses proxy passes the same synthetic token to
    both; it must be a "sk-synthetic-" key, never a host credential.
    """
    origin = validate_loopback_endpoint(endpoint)
    if token is None:
        token = new_synthetic_token()
    elif (not isinstance(token, str) or not token.startswith("sk-synthetic-")
            or not 16 <= len(token) <= 256 or not token.isascii() or not token.isprintable()
            or " " in token):
        raise LLMToolError("app-server token must be a synthetic key")
    root = Path(root).resolve()
    home, codex_home, run_dir, tmp_dir = (
        root / "home", root / "home" / "codex", root / "run", root / "tmp")
    for directory in (home, codex_home, run_dir, tmp_dir):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    auth = codex_home / "auth.json"
    descriptor = os.open(str(auth), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"OPENAI_API_KEY": token}, stream)
    # No mcp_servers table and no [projects] trust entry: nothing is inherited or trusted.
    config = (
        "check_for_update_on_startup = false\n"
        'openai_base_url = "%s/v1"\n'
        'chatgpt_base_url = "%s/backend-api/"\n'
        "[analytics]\nenabled = false\n[features]\n" % (origin, origin)
        + "".join("%s = false\n" % name for name in FEATURES_OFF))
    (codex_home / "config.toml").write_text(config)
    return IsolatedHome(home, codex_home, run_dir, tmp_dir, token)


def child_environment(isolated):
    """Allowlisted proxy environment; this is not OS-level egress enforcement."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(isolated.home),
        "CODEX_HOME": str(isolated.codex_home),
        "TMPDIR": str(isolated.tmp_dir),
        "LANG": "C",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        env[name] = DEAD_ENDPOINT
    return env


def child_argv(executable):
    argv = [executable, "app-server", "--listen", "stdio://", "--strict-config"]
    for feature in DISABLED_FEATURES:
        argv.extend(("--disable", feature))
    return argv


def process_table():
    """(pid, ppid, pgid, command) for every process; used for observation only."""
    output = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,command="],
                            capture_output=True, text=True, check=False).stdout
    rows = []
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and all(part.isdigit() for part in parts[:3]):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3]))
    return rows


def group_members(pgid):
    return [row for row in process_table() if row[2] == pgid]


def _group_present(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True         # zombie-only groups can answer EPERM on Darwin
    return True


def _wait_group_gone(pgid, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _group_present(pgid):
            return True
        time.sleep(0.02)
    return not _group_present(pgid)


class AppServerProcess:
    """One counted launch of the App-server child; owns and sweeps its process group."""

    def __init__(self, executable, isolated, ledger, deadline_seconds=None, lifecycle=None):
        if not isinstance(executable, str) or not os.path.isabs(executable):
            raise LLMToolError("app-server executable must be an absolute path")
        self._executable = executable
        self._isolated = isolated
        self._ledger = ledger
        self._lifecycle = lifecycle
        seconds = ledger.remaining_seconds()
        if deadline_seconds is not None:
            seconds = min(seconds, deadline_seconds)
        self._deadline = time.monotonic() + seconds
        self._process = None
        self._pgid = None
        self._stderr = None
        self.client = None
        self.shutdown_report = None

    @property
    def pid(self):
        return None if self._process is None else self._process.pid

    @property
    def pgid(self):
        return self._pgid

    def start(self):
        if self._process is not None:
            raise LLMToolError("app-server already started")
        if not self._ledger.reserve_launch():
            raise coded_error(
                "App-server launch budget exhausted", 'PROVIDER_RUNTIME_ERROR',
                'provider.codex-cli')
        try:
            self._stderr = open(str(self._isolated.tmp_dir / "app-server.stderr"), "wb")
            self._process = subprocess.Popen(
                child_argv(self._executable), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr, shell=False, close_fds=True, start_new_session=True,
                cwd=str(self._isolated.run_dir), env=child_environment(self._isolated))
        except (OSError, ValueError):
            if self._stderr is not None:
                try:
                    self._stderr.close()
                except OSError:
                    pass
            raise coded_error(
                "App-server could not be started", 'PROVIDER_RUNTIME_ERROR',
                'provider.codex-cli') from None
        self._pgid = self._process.pid      # start_new_session makes the child its own leader
        if self._lifecycle is not None:
            self._lifecycle.own(self._process)
        self.client = AppServerClient(
            self._process.stdin, self._process.stdout, self._deadline,
            self._lifecycle.check if self._lifecycle is not None else None)
        return self.client

    def stderr_text(self, limit=2048):
        """Bounded, path-sanitized stderr for diagnostics."""
        try:
            data = (self._isolated.tmp_dir / "app-server.stderr").read_bytes()[:limit]
        except OSError:
            return ""
        root = str(self._isolated.home.parent)
        return data.decode("utf-8", "replace").replace(root, "<isolated>")

    def shutdown(self, grace=GRACE_SECONDS):
        """Close stdin and sweep the owned group; this is not OS descendant containment.

        CLEAN: the leader exited on stdin close and no group member remained.
        SWEPT: a signal was required for the leader or for leftover descendants.
        UNKNOWN: group members still existed after SIGKILL and the sweep window.
        """
        try:
            return self._shutdown(grace)
        except Exception:
            # Observation/report failures must not skip teardown of the owned,
            # still-unreaped child. Never acquire authority over escaped PIDs.
            if self._process is not None and self._process.returncode is None:
                try:
                    self._signal_group_until_reaped()
                except Exception:
                    pass
            self.shutdown_report = {"returncode": None, "outcome": "UNKNOWN",
                                    "group_empty": False, "descendant_cleanup": "UNKNOWN",
                                    "escaped_descendants": None}
            return self.shutdown_report
        finally:
            if self._stderr is not None:
                try:
                    self._stderr.close()
                except OSError:
                    pass

    def _shutdown(self, grace):
        if self.shutdown_report is not None or self._process is None:
            return self.shutdown_report
        # A bounded snapshot can reveal a setsid escape while the leader is still
        # its parent. Absence is not proof: exited/reparented descendants may evade it.
        observed = True
        try:
            rows = process_table()
        except (OSError, ValueError):
            rows = []
            observed = False
        descendants = {self._process.pid}
        while True:
            found = {pid for pid, ppid, _, _ in rows if ppid in descendants}
            if found <= descendants:
                break
            descendants.update(found)
        escaped = sum(1 for pid, _, pgid, _ in rows
                      if pid in descendants and pgid != self._pgid)
        signalled = False
        if self.client is not None:
            self.client.close()
        for stream in (self._process.stdin, self._process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        try:
            returncode = self._process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            signalled = True
            returncode = self._signal_group_until_reaped()
        if not _wait_group_gone(self._pgid, SWEEP_SECONDS):
            signalled = True
            self._kill_group(signal.SIGKILL)
        gone = _wait_group_gone(self._pgid, SWEEP_SECONDS)
        self._stderr.close()
        outcome = ("UNKNOWN" if not gone or escaped or not observed
                   else ("SWEPT" if signalled else "CLEAN"))
        self.shutdown_report = {"returncode": returncode, "outcome": outcome,
                                "group_empty": gone, "descendant_cleanup": "UNKNOWN",
                                "escaped_descendants": escaped if observed else None}
        return self.shutdown_report

    def _kill_group(self, signum):
        try:
            os.killpg(self._pgid, signum)
        except ProcessLookupError:
            pass

    def _signal_group_until_reaped(self):
        for signum, wait in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 5.0)):
            self._kill_group(signum)
            try:
                return self._process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                continue
        return None


class AppServerSession:
    """Temporary isolated home plus one counted child; always shut down on exit."""

    def __init__(self, executable, ledger, endpoint=DEAD_ENDPOINT, deadline_seconds=None):
        self._executable = executable
        self._ledger = ledger
        self._endpoint = endpoint
        self._deadline_seconds = deadline_seconds
        self._directory = None
        self.isolated = None
        self.process = None

    def __enter__(self):
        # Same /tmp-or-/var/tmp isolation the Codex CLI provider uses (never inside a repo).
        from .providers.codex_cli import CodexCLIProvider
        self._directory = CodexCLIProvider._temporary_directory()
        try:
            self.isolated = build_isolated_home(self._directory.name, self._endpoint)
            self.process = AppServerProcess(
                self._executable, self.isolated, self._ledger, self._deadline_seconds)
            self.process.start()
        except BaseException:
            self._finish()
            raise
        return self

    @property
    def client(self):
        return self.process.client

    def _finish(self):
        try:
            if self.process is not None:
                self.process.shutdown()
        finally:
            if self._directory is not None:
                self._directory.cleanup()

    def __exit__(self, *exc):
        self._finish()
        return False


class CodexAppServerStrategy:
    """Default-off App-server route for CodexCLIProvider; injected in code, never from config.

    One run() is: a loopback Responses proxy bound to the durable ledger, a fresh isolated
    synthetic home, one counted child launch, one ephemeral read-only thread with exactly
    one turn, then full teardown. A timeout or lost connection is UNKNOWN and is reported
    as non-retryable; nothing here resends a turn or a request.
    """

    def __init__(self, ledger, upstream, allowed_tools=()):
        self._ledger = ledger
        self._upstream = validate_loopback_endpoint(upstream)
        try:
            self._allowed_tools = tuple(allowed_tools)
        except TypeError:
            raise LLMToolError("app-server tool allowlist invalid") from None
        self.last_report = None

    @staticmethod
    def _failure(exc):
        if getattr(exc, "diagnostic", None) is not None:
            return exc                  # already a classified, content-free error
        return coded_error("Codex App-server turn failed", 'PROVIDER_RUNTIME_ERROR',
                           'provider.codex-cli')

    def run(self, executable, prompt, timeout_seconds):
        from .codex_responses_proxy import ResponsesProxy
        from .providers.codex_cli import CodexCLIProvider
        started = time.monotonic()
        directory = proxy = process = None
        content = failure = shutdown = None
        phase = "lifecycle"
        self.last_report = {"phase": phase, "reason": "UNKNOWN"}
        with CLIProcessLifecycle('codex-cli') as lifecycle:
            try:
                phase = "temporary_directory"
                directory = CodexCLIProvider._temporary_directory()
                token = new_synthetic_token()
                phase = "proxy_start"
                proxy = ResponsesProxy(self._ledger, self._upstream, token, self._allowed_tools)
                proxy.start()
                phase = "isolated_home"
                isolated = build_isolated_home(directory.name, proxy.endpoint, token)
                phase = "process_start"
                process = AppServerProcess(
                    executable, isolated, self._ledger, timeout_seconds, lifecycle=lifecycle)
                client = process.start()
                phase = "initialize"
                client.initialize(expected_codex_home=str(isolated.codex_home))
                phase = "turn"
                content = client.run_turn(prompt, str(isolated.run_dir))
            except AppServerTimeout:
                failure = coded_error("Codex App-server timed out", 'PROVIDER_TIMEOUT',
                                      'provider.codex-cli')
            except (LLMToolError, OSError, ValueError, TypeError, RuntimeError) as exc:
                failure = self._failure(exc)
            finally:
                cleanup_errors = []
                for name, action in (
                        ("process", process.shutdown if process is not None else None),
                        ("proxy", proxy.close if proxy is not None else None),
                        ("directory", directory.cleanup if directory is not None else None)):
                    if action is None:
                        continue
                    try:
                        value = action()
                        if name == "process":
                            shutdown = value
                    except Exception:
                        cleanup_errors.append(name)
                try:
                    snapshot = self._ledger.snapshot()
                    if type(snapshot) is not dict or type(snapshot.get('states', [])) is not list:
                        raise ValueError
                except Exception:
                    snapshot = {"status": "UNKNOWN"}
                    if failure is None:
                        failure = self._failure(None)
                observations = {}
                for name in ("forwarded", "rejected", "rejected_tools"):
                    try:
                        observations[name] = getattr(proxy, name) if proxy is not None else []
                    except Exception:
                        observations[name] = []
                        cleanup_errors.append("report_" + name)
                if 'CAP_EXCEEDED' in snapshot.get('states', []):
                    failure = coded_error("Codex App-server output cap exceeded",
                                          'MODEL_RESPONSE_TOO_LARGE', 'provider.codex-cli')
                if cleanup_errors or (shutdown is not None and shutdown.get("outcome") == "UNKNOWN"):
                    if failure is None:
                        failure = coded_error("Codex App-server cleanup could not be confirmed",
                                              'PROVIDER_RUNTIME_ERROR', 'provider.codex-cli')
                diagnostic = getattr(failure, "diagnostic", None)
                self.last_report = dict(observations, shutdown=shutdown, ledger=snapshot,
                    phase=phase, reason=diagnostic.code.value if diagnostic else
                    ("RUNTIME_ERROR" if failure else "COMPLETED"), cleanup_errors=cleanup_errors,
                    containment={"os_egress": "NOT_VERIFIED", "descendants": "UNKNOWN"})
        if failure is not None:
            raise failure
        return content, time.monotonic() - started
