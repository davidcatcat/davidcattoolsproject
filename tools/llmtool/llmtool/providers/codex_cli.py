"""Plain-text provider backed by the user's local Codex CLI installation."""

import os
import re
import tempfile
import selectors
import shutil
import subprocess
import time
from pathlib import Path

from ..errors import LLMToolError, coded_error
from .base import ProviderCapabilities, Reply
from .cli_lifecycle import CLIProcessLifecycle, terminate

OUTPUT_LIMIT = 1024 * 1024
READ_SIZE = 8192


class CodexCLIProvider:
    TYPE = "codex-cli"
    CAPABILITIES = ProviderCapabilities(
        chat=True, large_context=False, reasoning_control=False,
        streaming=False, code_edit=False, shell=False, git=False,
    )

    @staticmethod
    def validate_config(provider):
        for field in ("base_url", "api_key", "api_key_env"):
            if provider.get(field) is not None:
                raise LLMToolError("codex-cli does not support HTTP endpoints or API credentials")
        allowed = {
            "type", "base_url", "model", "timeout_seconds", "api_key",
            "api_key_env", "provider_options",
        }
        if set(provider) - allowed:
            raise LLMToolError("codex-cli: forbidden or unsupported field")
        model = provider.get("model")
        if model is not None and (not isinstance(model, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model)):
            raise LLMToolError("codex-cli model must be a safe model identifier")

    @staticmethod
    def validate_options(options):
        if (not isinstance(options, dict) or
                set(options) - {"reasoning_effort"}):
            raise LLMToolError("unsupported codex-cli provider_options")
        effort = options.get("reasoning_effort")
        if ("reasoning_effort" in options and
                effort not in ("low", "medium", "high", "xhigh")):
            raise LLMToolError(
                "codex-cli reasoning_effort must be low, medium, high, or xhigh")

    @staticmethod
    def validate_ready(provider):
        # Codex CLI chooses its default model when no explicit model is configured.
        return None

    def __init__(self, provider, generation, key=None):
        self.provider = provider

    @staticmethod
    def _prompt(messages):
        if not isinstance(messages, list) or not messages:
            raise LLMToolError("invalid conversation input")
        parts = []
        for message in messages:
            if (not isinstance(message, dict)
                    or message.get("role") not in ("system", "developer", "user", "assistant")
                    or not isinstance(message.get("content"), str)):
                raise LLMToolError("invalid conversation input")
            parts.append(message["role"].upper() + ":\n" + message["content"])
        return "\n\n".join(parts)

    @staticmethod
    def _read_pipe(stream, output):
        try:
            chunk = os.read(stream.fileno(), READ_SIZE)
        except BlockingIOError:
            return False
        if not chunk:
            return True
        if len(output) + len(chunk) > OUTPUT_LIMIT:
            return "limit"
        output.extend(chunk)
        return False

    @staticmethod
    def _kill(process):
        terminate(process)

    @staticmethod
    def _child_environment(runtime):
        # Preserve CLI-owned login/session lookup, not arbitrary project secrets,
        # Git overrides, shell startup hooks, Node injection or daemon endpoints.
        environment = {
            name: os.environ[name] for name in (
                "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
                "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
            ) if name in os.environ
        }
        for name in ("HOME", "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR"):
            value = environment.get(name)
            if value is not None and (not os.path.isabs(value)
                                      or any(ord(char) < 32 for char in value)):
                raise LLMToolError("Codex CLI runtime paths must be absolute")
        environment["TMPDIR"] = runtime
        return environment

    @staticmethod
    def _temporary_directory():
        caller = Path.cwd().resolve()
        # Only locate the caller's repository boundary; never read source or run Git.
        repository = next((path for path in (caller, *caller.parents)
                           if (path / ".git").exists()), caller)
        # Ignore inherited TMPDIR: it may point into the caller's repository.
        for candidate in (Path("/tmp"), Path("/var/tmp")):
            base = candidate.resolve()
            if repository == base or repository in base.parents:
                continue
            try:
                return tempfile.TemporaryDirectory(prefix="llmtool-codex-", dir=base)
            except OSError:
                continue
        raise coded_error(
            "Codex CLI isolated working directory is not available",
            'PROVIDER_RUNTIME_ERROR',
            'provider.codex-cli',
        )

    def _argv(self, executable, runtime):
        argv = [
            executable, "--no-daemon", "--ask-for-approval", "never", "exec",
            "--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config",
            "--skip-git-repo-check", "--color", "never", "--cd", runtime,
        ]
        # --sandbox would override this stronger, restricted read-only profile.
        settings = (
            'default_permissions="llmtool-text"',
            'permissions.llmtool-text.extends=":read-only"',
            'permissions.llmtool-text.filesystem={":root"="deny",'
            '":minimal"="read",":tmpdir"="deny",":slash_tmp"="deny",'
            '":workspace_roots"={"."="read"}}',
            'permissions.llmtool-text.network.enabled=false',
            'project_doc_max_bytes=0',
            'web_search="live"',
            'features.shell_tool=false',
            'features.unified_exec=false',
            'features.shell_snapshot=false',
            'features.multi_agent=false',
            'features.hooks=false',
            'features.apps=false',
            'features.memories=false',
            'features.remote_plugin=false',
            'features.code_mode.enabled=false',
            'shell_environment_policy.inherit="none"',
            'allow_login_shell=false',
            'model_reasoning_summary="none"',
            'hide_agent_reasoning=true',
            'show_raw_agent_reasoning=false',
        )
        effort = self.provider.get("provider_options", {}).get("reasoning_effort")
        if effort is not None:
            settings += ('model_reasoning_effort="' + effort + '"',)
        for setting in settings:
            argv.extend(("-c", setting))
        model = self.provider.get("model")
        if model:
            argv.extend(("--model", model))
        argv.append("-")
        return argv

    def _invoke(self, executable, prompt, runtime, lifecycle):
        try:
            input_bytes = prompt.encode("utf-8")
        except UnicodeError:
            raise LLMToolError("invalid conversation input") from None
        try:
            lifecycle.check()
            process = subprocess.Popen(
                self._argv(executable, runtime), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                close_fds=True, start_new_session=True, cwd=runtime,
                env=self._child_environment(runtime),
            )
            lifecycle.own(process)
        except FileNotFoundError:
            raise coded_error(
                "Codex CLI executable is not available; install codex on PATH",
                'ENV_PROVIDER_RUNTIME_MISSING',
                'provider.codex-cli',
                user_action_required=True,
            ) from None
        except OSError:
            raise coded_error(
                "Codex CLI could not be started",
                'PROVIDER_RUNTIME_ERROR',
                'provider.codex-cli',
            ) from None

        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        input_offset = 0
        streams = {
            process.stdout: ("stdout", stdout),
            process.stderr: ("stderr", stderr),
        }
        overflow = False
        started = time.monotonic()
        deadline = started + self.provider["timeout_seconds"]
        try:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, streams[process.stdout])
            selector.register(process.stderr, selectors.EVENT_READ, streams[process.stderr])
            selector.register(process.stdin, selectors.EVENT_WRITE, ("stdin", None))

            while selector.get_map():
                lifecycle.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill(process)
                    raise coded_error(
                        "Codex CLI timed out",
                        'PROVIDER_TIMEOUT',
                        'provider.codex-cli',
                        retryable=True,
                    )
                for key, _ in selector.select(min(remaining, 0.1)):
                    stream = key.fileobj
                    kind, output = key.data
                    if kind == "stdin":
                        try:
                            if input_offset < len(input_bytes):
                                input_offset += os.write(
                                    stream.fileno(), input_bytes[input_offset:input_offset + READ_SIZE]
                                )
                            else:
                                selector.unregister(stream)
                                stream.close()
                        except (BlockingIOError, InterruptedError):
                            pass
                        except (BrokenPipeError, OSError):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        result = self._read_pipe(stream, output)
                        if result == "limit":
                            overflow = True
                            self._kill(process)
                            break
                        if result:
                            selector.unregister(stream)
                            stream.close()
                if overflow:
                    raise coded_error(
                        "Codex CLI output exceeded 1 MiB",
                        'MODEL_RESPONSE_TOO_LARGE',
                        'provider.codex-cli',
                    )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill(process)
                raise coded_error("Codex CLI timed out", 'PROVIDER_TIMEOUT', 'provider.codex-cli', retryable=True)
            while True:
                lifecycle.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill(process)
                    raise coded_error("Codex CLI timed out", 'PROVIDER_TIMEOUT',
                                      'provider.codex-cli', retryable=True)
                try:
                    returncode = process.wait(timeout=min(remaining, 0.1))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
            lifecycle.stop()

        if returncode != 0:
            raise coded_error(
                "Codex CLI exited with status " + str(returncode),
                'PROVIDER_RUNTIME_ERROR',
                'provider.codex-cli',
                safe_cause={"process_status": returncode},
            )
        try:
            content = bytes(stdout).decode("utf-8").strip()
        except UnicodeError:
            raise coded_error(
                "Codex CLI returned invalid UTF-8",
                'MODEL_RESPONSE_INVALID',
                'provider.codex-cli',
            ) from None
        if not content:
            raise coded_error("Codex CLI returned no final text", 'MODEL_RESPONSE_INVALID', 'provider.codex-cli')
        return content, time.monotonic() - started

    def complete(self, messages):
        self.validate_config(self.provider)
        self.validate_options(self.provider.get("provider_options", {}))
        self.validate_ready(self.provider)
        executable = shutil.which("codex")
        if executable is None or not os.path.isabs(executable):
            raise coded_error(
                "Codex CLI executable is not available; install codex on PATH",
                'ENV_PROVIDER_RUNTIME_MISSING',
                'provider.codex-cli',
                user_action_required=True,
            )
        prompt = self._prompt(messages)
        try:
            with CLIProcessLifecycle(self.TYPE) as lifecycle:
                with self._temporary_directory() as directory:
                    try:
                        content, elapsed = self._invoke(executable, prompt, str(Path(directory).resolve()), lifecycle)
                    finally:
                        lifecycle.stop()
        except OSError:
            raise coded_error(
                "Codex CLI isolated runtime failed",
                'PROVIDER_RUNTIME_ERROR',
                'provider.codex-cli',
            ) from None
        metadata = {"elapsed_seconds": round(elapsed, 3)}
        model = self.provider.get("model")
        if isinstance(model, str) and model and all(
                char.isalnum() or char in "._:/-" for char in model):
            metadata["model"] = model
        effort = self.provider.get("provider_options", {}).get("reasoning_effort")
        if effort is not None:
            metadata["reasoning_effort"] = effort
        return Reply(content, metadata)
