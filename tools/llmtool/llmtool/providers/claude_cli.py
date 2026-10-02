"""Plain-text provider backed by the user's local Claude CLI installation."""

import os
import selectors
import shutil
import subprocess
import signal
import tempfile
import time
from pathlib import Path

from ..errors import LLMToolError, coded_error
from .base import ProviderCapabilities, Reply

OUTPUT_LIMIT = 1024 * 1024
READ_SIZE = 8192


class ClaudeCLIProvider:
    TYPE = "claude-cli"
    CAPABILITIES = ProviderCapabilities(
        chat=True, large_context=False, reasoning_control=False,
        streaming=False, code_edit=False, shell=False, git=False,
    )

    @staticmethod
    def validate_config(provider):
        for field in ("base_url", "api_key", "api_key_env"):
            if provider.get(field) is not None:
                raise LLMToolError("claude-cli does not support HTTP endpoints or API credentials")
        model = provider.get("model")
        if isinstance(model, str) and model.startswith("-"):
            raise LLMToolError("claude-cli model must not start with a dash")

    @staticmethod
    def validate_options(options):
        if not isinstance(options, dict) or options:
            raise LLMToolError("unsupported claude-cli provider_options")

    @staticmethod
    def validate_ready(provider):
        # Claude CLI chooses its default model when no explicit model is configured.
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
        try:
            if os.name == "posix" and hasattr(os, "killpg"):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                process.kill()
            except (ProcessLookupError, PermissionError):
                pass
        process.wait()

    @staticmethod
    def _child_environment():
        # This explicit runtime-only override separates llmtool's isolated HOME
        # from the home in which the user configured Claude CLI authentication.
        claude_home = os.environ.get("LLMTOOL_CLAUDE_CLI_HOME", os.environ.get("HOME"))
        environment = {
            name: os.environ[name] for name in (
                "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE",
                "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
            ) if name in os.environ
        }
        if claude_home is not None and (not os.path.isabs(claude_home)
                                        or any(ord(char) < 32 for char in claude_home)):
            raise LLMToolError("Claude CLI home must be an absolute path")
        if claude_home is not None:
            environment["HOME"] = claude_home
        return environment

    @staticmethod
    def _temporary_directory():
        caller = Path.cwd().resolve()
        repository = next((path for path in (caller, *caller.parents)
                           if (path / ".git").exists()), caller)
        for candidate in (Path("/tmp"), Path("/var/tmp")):
            base = candidate.resolve()
            if repository == base or repository in base.parents:
                continue
            try:
                return tempfile.TemporaryDirectory(prefix="llmtool-claude-", dir=base)
            except OSError:
                continue
        raise coded_error(
            "Claude CLI isolated working directory is not available",
            'PROVIDER_RUNTIME_ERROR',
            'provider.claude-cli',
        )

    def _invoke(self, executable, prompt):
        try:
            input_bytes = prompt.encode("utf-8")
        except UnicodeError:
            raise LLMToolError("invalid conversation input") from None
        argv = [
            executable, "--print", "--output-format", "text",
            "--no-session-persistence", "--safe-mode", "--tools", "",
        ]
        model = self.provider.get("model")
        if model:
            argv.extend(("--model", model))
        try:
            with self._temporary_directory() as cwd:
                process = subprocess.Popen(
                    argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, shell=False, close_fds=True,
                    start_new_session=True, cwd=cwd, env=self._child_environment(),
                )
                return self._collect(process, input_bytes)
        except FileNotFoundError:
            raise coded_error(
                "Claude CLI executable is not available",
                'ENV_PROVIDER_RUNTIME_MISSING',
                'provider.claude-cli',
                user_action_required=True,
            ) from None
        except OSError:
            raise coded_error(
                "Claude CLI could not be started",
                'PROVIDER_RUNTIME_ERROR',
                'provider.claude-cli',
            ) from None

    def _collect(self, process, input_bytes):
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill(process)
                    raise coded_error(
                        "Claude CLI timed out",
                        'PROVIDER_TIMEOUT',
                        'provider.claude-cli',
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
                        "Claude CLI output exceeded 1 MiB",
                        'MODEL_RESPONSE_TOO_LARGE',
                        'provider.claude-cli',
                    )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill(process)
                raise coded_error(
                    "Claude CLI timed out",
                    'PROVIDER_TIMEOUT',
                    'provider.claude-cli',
                    retryable=True,
                )
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                self._kill(process)
                raise coded_error(
                    "Claude CLI timed out",
                    'PROVIDER_TIMEOUT',
                    'provider.claude-cli',
                    retryable=True,
                ) from None
        finally:
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
            if process.poll() is None:
                self._kill(process)

        if returncode != 0:
            raise coded_error(
                "Claude CLI exited with status " + str(returncode),
                'PROVIDER_RUNTIME_ERROR',
                'provider.claude-cli',
                safe_cause={"process_status": returncode},
            )
        try:
            content = bytes(stdout).decode("utf-8").strip()
        except UnicodeError:
            raise coded_error(
                "Claude CLI returned invalid UTF-8",
                'MODEL_RESPONSE_INVALID',
                'provider.claude-cli',
            ) from None
        if not content:
            raise coded_error("Claude CLI returned no final text", 'MODEL_RESPONSE_INVALID', 'provider.claude-cli')
        return content, time.monotonic() - started

    def complete(self, messages):
        self.validate_config(self.provider)
        self.validate_options(self.provider.get("provider_options", {}))
        self.validate_ready(self.provider)
        executable = shutil.which("claude")
        if executable is None or not os.path.isabs(executable):
            raise coded_error(
                "Claude CLI executable is not available",
                'ENV_PROVIDER_RUNTIME_MISSING',
                'provider.claude-cli',
                user_action_required=True,
            )
        content, elapsed = self._invoke(executable, self._prompt(messages))
        metadata = {"elapsed_seconds": round(elapsed, 3)}
        model = self.provider.get("model")
        if isinstance(model, str) and model and all(
                char.isalnum() or char in "._:/-" for char in model):
            metadata["model"] = model
        return Reply(content, metadata)
