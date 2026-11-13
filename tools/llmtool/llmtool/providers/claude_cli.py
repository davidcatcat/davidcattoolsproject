"""Plain-text provider backed by the user's local Claude CLI installation."""

import os
import selectors
import shutil
import subprocess
import tempfile
import time
import uuid
import math
import hashlib
from pathlib import Path

from ..errors import LLMToolError, coded_error
from .base import ProviderCapabilities, Reply
from .cli_lifecycle import CLIProcessLifecycle, terminate

OUTPUT_LIMIT = 1024 * 1024
READ_SIZE = 8192


REVIEW_ENVELOPE_LIMIT = 128 * 1024


def decode_review_envelope(raw, *, forbidden_sessions=()):
    """Pinned Claude SDKResultMessage result/success subset (upstream v2.1.301/2.1.303).

    Basis: Claude Code 2.1.301/2.1.303 CLI output-format=json and the
    official Claude Agent SDK SDKResultSuccess result envelope (see WORKFLOW). This is a
    strict fixture codec, NOT evidence of the installed CLI's live protocol.
    Usage is allowlisted CLI_REPORTED data, never measured billing or identity.
    """
    from ..task_review import load_json, screen, reject, VERDICT_LIMIT
    try:
        if type(raw) is bytes:
            raw = raw.decode('utf-8')
        value = load_json(raw, REVIEW_ENVELOPE_LIMIT)
        required = {'type', 'subtype', 'is_error', 'session_id', 'result'}
        # Only this documented subset is consumed. Unknown top-level data is
        # rejected rather than reflected into diagnostics or interpreted as pins.
        allowed = required | {'usage', 'duration_ms', 'duration_api_ms',
                              'num_turns', 'total_cost_usd', 'stop_reason', 'uuid',
                              'permission_denials', 'modelUsage',
                              'api_error_status', 'terminal_reason', 'fast_mode_state',
                              'fast_mode_disabled_reason', 'ttft_ms', 'ttft_stream_ms',
                              'time_to_request_ms', 'first_content_frame_ms',
                              'queued_turn_count', 'result_index', 'safety_stops',
                              'subagent_stats'}
        if (type(value) is not dict or not required <= set(value) <= allowed or
                value['type'] != 'result' or value['subtype'] != 'success' or
                type(value['is_error']) is not bool or value['is_error'] or
                type(value['session_id']) is not str or
                str(uuid.UUID(value['session_id'])) != value['session_id'] or
                value['session_id'] in forbidden_sessions or
                type(value['result']) is not str or not value['result'] or
                len(value['result'].encode('utf-8')) > VERDICT_LIMIT):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        screen(raw)
        def screen_strings(item):
            if type(item) is str:
                screen(item)
            elif type(item) is dict:
                for key, child in item.items():
                    screen(key); screen_strings(child)
            elif type(item) is list:
                for child in item: screen_strings(child)
        screen_strings(value)
        # The result is itself JSON DATA. Decode it for privacy checks so JSON
        # escapes cannot hide secrets/paths before the strict verdict parser.
        screen_strings(load_json(value['result'], VERDICT_LIMIT))
        for key in ('duration_ms', 'duration_api_ms', 'num_turns', 'ttft_ms', 'ttft_stream_ms',
                    'time_to_request_ms', 'first_content_frame_ms', 'queued_turn_count',
                    'result_index', 'safety_stops'):
            if key in value and value[key] is not None and (
                    type(value[key]) is not int or not 0 <= value[key] <= 10**12):
                reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'api_error_status' in value and value['api_error_status'] is not None:
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'terminal_reason' in value and value['terminal_reason'] not in (None, 'completed'):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'fast_mode_state' in value and value['fast_mode_state'] not in (None, 'off', 'on', 'cooldown'):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'fast_mode_disabled_reason' in value and value['fast_mode_disabled_reason'] is not None and (
                type(value['fast_mode_disabled_reason']) is not str or
                len(value['fast_mode_disabled_reason']) > 128):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'subagent_stats' in value and value['subagent_stats'] is not None and (
                type(value['subagent_stats']) is not dict or len(value['subagent_stats']) > 32):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'total_cost_usd' in value and (type(value['total_cost_usd']) not in (int, float) or
                not math.isfinite(value['total_cost_usd']) or value['total_cost_usd'] < 0):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'stop_reason' in value and value['stop_reason'] not in (None, 'end_turn', 'stop_sequence'):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'uuid' in value and (type(value['uuid']) is not str or str(uuid.UUID(value['uuid'])) != value['uuid']):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'permission_denials' in value and value['permission_denials'] != []:
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        if 'modelUsage' in value and (type(value['modelUsage']) is not dict or
                len(value['modelUsage']) > 16):
            reject('REVIEW_WORKER_ENVELOPE_INVALID')
        # modelUsage is explicitly ignored; no per-model billing is inferred.
        usage = value.get('usage')
        counts = dict(input_tokens=None, output_tokens=None)
        if type(usage) is dict:
            allowed_usage = {'input_tokens', 'output_tokens', 'cache_creation_input_tokens',
                             'cache_read_input_tokens', 'server_tool_use', 'service_tier',
                             'cache_creation', 'inference_geo', 'iterations', 'speed',
                             'fallback_credit', 'output_tokens_details'}
            # Optional unsupported usage is UNKNOWN, not a reason to fabricate 0.
            if set(usage) <= allowed_usage and all(type(usage.get(k)) is int and
                    0 <= usage[k] <= 10**12 for k in ('input_tokens', 'output_tokens')):
                counts = {k: usage[k] for k in counts}
        return dict(content=value['result'], runtime_session_id=value['session_id'],
                    identity_source='CLI_REPORTED_RUNTIME_ID', usage_source='CLI_REPORTED', **counts)
    except (LLMToolError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        reject('REVIEW_WORKER_ENVELOPE_INVALID')


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
        terminate(process)

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
            "--no-session-persistence", "--safe-mode", "--tools", "WebSearch,WebFetch",
        ]
        model = self.provider.get("model")
        if model:
            argv.extend(("--model", model))
        try:
            with CLIProcessLifecycle(self.TYPE) as lifecycle:
                with self._temporary_directory() as cwd:
                    try:
                        lifecycle.check()
                        process = subprocess.Popen(
                            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, shell=False, close_fds=True,
                            start_new_session=True, cwd=cwd, env=self._child_environment(),
                        )
                        lifecycle.own(process)
                        return self._collect(process, input_bytes, lifecycle)
                    finally:
                        lifecycle.stop()
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

    def _collect(self, process, input_bytes, lifecycle):
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
            while True:
                lifecycle.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill(process)
                    raise coded_error("Claude CLI timed out", 'PROVIDER_TIMEOUT',
                                      'provider.claude-cli', retryable=True)
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


    def _invoke_review_job(self, executable, prompt, *, deadline, auth_home,
                           launch_nonce, runtime_pin, forbidden_sessions):
        """Trusted Host-only opt-in; generic complete/_invoke stay text-compatible.

        A single immutable monotonic deadline includes a <=20s cleanup reserve.
        An uncertain lifecycle remains reachable as pending_job; no retry/fallback.
        Only a fixed, empty runtime directory is removed (no unbounded rmtree).
        """
        from ..task_review import reject
        from .. import role_config
        started = time.monotonic()
        if (type(deadline) not in (int, float) or not math.isfinite(deadline) or
                deadline <= started or deadline > started + 120 or
                not os.path.isabs(executable) or not os.path.isabs(auth_home)):
            reject('REVIEW_TIMEOUT')
        # #1502 WP-E R1: a review job runs only a model the shared registry
        # check admits for claude-reviewer; refused before any process starts.
        try:
            model = role_config.check_registry_model('claude-reviewer', self.provider.get('model'))
        except LLMToolError:
            reject('ROLE_MODEL_NOT_ALLOWED')
        work_deadline = deadline - min(20, (deadline - started) * 0.2)
        raw = prompt.encode('utf-8')
        selector = selectors.DefaultSelector()
        output, errors = bytearray(), bytearray()
        offset = 0
        drained = False
        closed = False
        self.pending_job = None
        self.pending_runtime = None
        self.job_evidence = None
        runtime = self._temporary_directory()
        # TemporaryDirectory's recursive finalizer is deliberately not used by
        # this bounded path. Unexpected contents stay private and block success.
        runtime._finalizer.detach()
        lifecycle = CLIProcessLifecycle(self.TYPE, deadline=deadline)
        try:
            with lifecycle:
                try:
                    lifecycle.check()
                    if time.monotonic() >= work_deadline:
                        reject('REVIEW_TIMEOUT')
                    environment = self._child_environment()
                    environment['HOME'] = auth_home
                    argv = [executable, '--print', '--output-format', 'json',
                            '--no-session-persistence', '--safe-mode', '--tools', 'WebSearch,WebFetch',
                            '--model', model]
                    process = subprocess.Popen(argv, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                        shell=False, close_fds=True, start_new_session=True,
                        cwd=runtime.name, env=environment)
                    self.pending_job = lifecycle
                    lifecycle.own(process)
                    evidence = dict(launch_nonce=launch_nonce, owned_pid=process.pid,
                        owned_pgid=process.pid, host_pid=os.getpid(),
                        executable_path_digest=hashlib.sha256(executable.encode()).hexdigest(),
                        runtime_pin=runtime_pin, started=started, deadline=deadline, wrapper_launches=1)
                    for stream, event, kind in ((process.stdin, selectors.EVENT_WRITE, 'stdin'),
                            (process.stdout, selectors.EVENT_READ, 'stdout'),
                            (process.stderr, selectors.EVENT_READ, 'stderr')):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, event, kind)
                    while selector.get_map():
                        lifecycle.check()
                        remaining = work_deadline - time.monotonic()
                        if remaining <= 0:
                            reject('REVIEW_TIMEOUT')
                        for key, _ in selector.select(min(remaining, 0.05)):
                            stream, kind = key.fileobj, key.data
                            if kind == 'stdin':
                                try:
                                    if offset < len(raw):
                                        offset += os.write(stream.fileno(), raw[offset:offset+READ_SIZE])
                                    else:
                                        selector.unregister(stream); stream.close()
                                except (BlockingIOError, InterruptedError):
                                    continue
                                except BrokenPipeError:
                                    reject('REVIEW_WORKER_INPUT_INCOMPLETE')
                            else:
                                target = output if kind == 'stdout' else errors
                                result = self._read_pipe(stream, target)
                                if result == 'limit' or len(output) > REVIEW_ENVELOPE_LIMIT:
                                    reject('REVIEW_WORKER_OUTPUT_LIMIT')
                                if result:
                                    selector.unregister(stream); stream.close()
                    drained = True
                    while process.returncode is None:
                        lifecycle.check()
                        remaining = work_deadline - time.monotonic()
                        if remaining <= 0:
                            reject('REVIEW_TIMEOUT')
                        try:
                            process.wait(timeout=min(remaining, 0.05))
                        except subprocess.TimeoutExpired:
                            pass
                    if process.returncode != 0:
                        reject('REVIEW_WORKER_PROCESS_FAILED')
                    parsed = decode_review_envelope(bytes(output), forbidden_sessions=forbidden_sessions)
                    if time.monotonic() >= work_deadline:
                        reject('REVIEW_TIMEOUT')
                    evidence.update(runtime_session_id=parsed.pop('runtime_session_id'),
                        identity_source=parsed.pop('identity_source'), actual_exit=process.returncode,
                        reaped=True, pipes_drained=drained, pipes_closed=False)
                    self.job_evidence = evidence
                finally:
                    lifecycle.stop()  # Always bounded; __exit__ also stays bounded.
                    close_failed = False
                    try:
                        selector.close()
                    except OSError:
                        close_failed = True
                    if lifecycle.process is not None:
                        streams = (lifecycle.process.stdin, lifecycle.process.stdout, lifecycle.process.stderr)
                        for stream in streams:
                            try:
                                if stream is not None and not stream.closed:
                                    stream.close()  # unbuffered descriptors, no flush/wait
                            except OSError:
                                close_failed = True  # preserve original exception/cancellation
                        closed = not close_failed and all(s is not None and s.closed for s in streams)
                        if self.job_evidence is not None:
                            self.job_evidence['pipes_closed'] = closed
        finally:
            try:
                os.rmdir(runtime.name)
                runtime_closed = True
            except OSError:
                runtime_closed = False
                self.pending_runtime = runtime.name
            if lifecycle.cleanup_confirmed and runtime_closed and (closed or lifecycle.process is None):
                self.pending_job = None
            else:
                self.job_evidence = None
        if self.pending_job is not None or not drained or time.monotonic() >= deadline:
            reject('REVIEW_WORKER_CLEANUP_UNKNOWN')
        self.job_evidence['runtime_closed'] = True
        parsed['elapsed_seconds'] = time.monotonic() - started
        return parsed
