"""Conversation state and a text REPL; no action execution."""

import copy
import re
import sys
import time
from types import MappingProxyType

from .config import api_key, selected_provider
from .diagnostics import PROVIDER_USAGE_FIELDS, validate_provider_usage
from .errors import LLMToolError
from .inputs import INPUT_LIMIT
from .prompts import chat_messages
from .providers import client_type, create_client
from .providers.base import ProviderCapabilities, Reply
from .skills import discover_skills, load_skill
from .terminal import human_output
from .chat_history import ChatHistory

HELP = """/help                 Show commands
/status               Show provider, model, skill, turns and last-request status
/provider [profile]   List profiles or switch (history sent next turn)
/skill [name|none]     List skills, select one, or clear selection
/clear                Clear conversation; keep provider and skill
/history              Show user/assistant turns only
/exit, /quit          Exit; EOF and Ctrl-C also exit cleanly"""


UNKNOWN = "UNKNOWN"


class ChatInterrupted(LLMToolError):
    """A chat request was interrupted before any reply text could be kept."""
# Only these transports read "model" from the provider response body; CLI
# providers echo their configured value, which is not evidence of the served model.
_REPORTED_MODEL_TYPES = frozenset(("openai", "openai-compatible"))
_MODEL_PATTERN = re.compile(r"[A-Za-z0-9_./:-]{1,128}")
_ERROR_CODE_PATTERN = re.compile(r"[A-Z0-9_]{1,64}")


def reply_usage(metadata):
    """Validated immutable provider usage triplet from reply metadata, else None.

    Only the three standard fields are extracted; other metadata keys are
    ignored. Missing, partial, non-integer, bool or inconsistent usage is
    UNKNOWN (None). Never raises: usage is secondary to a valid reply.
    """
    try:
        if type(metadata) not in (dict, MappingProxyType):
            return None
        if any(name not in metadata for name in PROVIDER_USAGE_FIELDS):
            return None
        return validate_provider_usage({name: metadata[name] for name in PROVIDER_USAGE_FIELDS})
    except Exception:
        return None


def _streams(client):
    """True only for a client whose declared capabilities say it streams and that
    exposes stream(messages); a provider is never probed for it."""
    capabilities = getattr(client, "CAPABILITIES", None)
    return (type(capabilities) is ProviderCapabilities and capabilities.streaming
            and callable(getattr(client, "stream", None)))


def _stream_reply(chunks, on_text, cancel):
    """Collect str chunks until the stream ends or cancel is set between chunks.

    Returns (text, metadata, interrupted); metadata is the generator's return value
    when it has one. The stream is closed on every exit so a provider can release
    its connection. A chunk that is not text is an invalid reply.
    """
    iterator = iter(chunks)
    parts = []
    try:
        while not (cancel is not None and cancel.is_set()):
            try:
                chunk = next(iterator)
            except StopIteration as stop:
                return "".join(parts), stop.value, False
            if type(chunk) is not str:
                raise LLMToolError("invalid chat reply")
            if chunk:
                parts.append(chunk)
                on_text(chunk)
        return "".join(parts), None, True
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _reported_model(provider_type, metadata):
    try:
        if provider_type not in _REPORTED_MODEL_TYPES or type(metadata) not in (dict, MappingProxyType):
            return None
        model = metadata.get("model")
        return model if type(model) is str and _MODEL_PATTERN.fullmatch(model) else None
    except Exception:
        return None


def _error_code(exc):
    """Typed diagnostic code only; never classify from message text."""
    try:
        diagnostic = getattr(exc, "diagnostic", None)
        code = getattr(diagnostic, "code", None) or getattr(exc, "diagnostic_code", None)
        value = getattr(code, "value", None)
        return value if type(value) is str and _ERROR_CODE_PATTERN.fullmatch(value) else None
    except Exception:
        return None


class ChatSession:
    """A config snapshot plus successful turns, optionally backed by private local history."""

    def __init__(self, config, skill=None, history_path=None):
        self.config = copy.deepcopy(config)
        selected_provider(self.config)
        self.skill = skill
        self._history_store = ChatHistory(history_path) if history_path is not None else None
        self.history = self._history_store.load() if self._history_store is not None else []
        self.history_notice = self._history_store.notice if self._history_store is not None else None
        self.active = True
        self.reset_status()

    def reset_status(self):
        """Forget last-request status; nothing is carried across clear/switch."""
        self.state = "idle"
        self.request_started = None
        self.last_elapsed = None
        self.last_usage = None
        self.last_reported_model = None
        self.last_error_code = None
        self.last_interrupted = False

    @property
    def provider(self):
        return self.config["active_provider"]

    def status(self):
        return ("provider: " + self.provider + "\n"
                "type: " + selected_provider(self.config)["type"] + "\n"
                "skill: " + (self.skill.name if self.skill else "none") + "\n"
                "conversation turns: " + str(len(self.history)))

    def runtime_status(self):
        """UX-2 status: configured vs reported model, state, measured time, usage."""
        model = selected_provider(self.config).get("model")
        state, started, elapsed = self.state, self.request_started, self.last_elapsed
        usage, code, reported = self.last_usage, self.last_error_code, self.last_reported_model
        if state == "working" and started is not None:
            elapsed_text = "%.1fs so far (measured locally)" % max(0.0, time.monotonic() - started)
        elif elapsed is not None:
            elapsed_text = "%.3fs (measured locally)" % elapsed
        else:
            elapsed_text = UNKNOWN
        if state == "error":
            state = "error (" + (code or "unclassified") + ")"
        return ("model: " + (model + " (configured)" if model else "provider default (not configured)") + "\n"
                "reported model: " + (reported or UNKNOWN) + "\n"
                "state: " + state + "\n"
                "elapsed: " + elapsed_text + "\n"
                "usage: " + format_usage(usage) + "\n"
                "connectivity: not probed")

    def switch_provider(self, name):
        candidate = copy.deepcopy(self.config)
        candidate["active_provider"] = name
        provider = selected_provider(candidate)
        client_type(provider["type"]).validate_ready(provider)
        self.config = candidate
        self.reset_status()

    def select_skill(self, name):
        candidate = None if name == "none" else load_skill(name)
        self.skill = candidate

    def send(self, text, on_text=None, cancel=None):
        """One user turn; returns the reply text kept in history.

        on_text(chunk) receives reply text as it streams when the selected client
        declares streaming; cancel is a threading.Event the caller sets to interrupt.
        A stream stops at the next chunk after cancel is set and the partial text is
        kept as the assistant turn with state "interrupted" (ChatInterrupted when no
        text arrived yet: history unchanged). A client without streaming returns its
        whole reply, which is kept and marked interrupted when cancel was set.
        """
        if not self.active:
            raise LLMToolError("chat session is closed")
        if not text.strip():
            return None
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            raise LLMToolError("chat input must be UTF-8 text") from None
        if len(raw) > INPUT_LIMIT:
            raise LLMToolError("input exceeds 16 MiB")
        if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
            raise LLMToolError("chat input contains unsupported control characters")
        user = {"role": "user", "content": text}
        messages = chat_messages(self.history + [user], self.skill)
        config = self.config
        self.reset_status()
        started = time.monotonic()
        self.request_started = started
        self.state = "working"
        try:
            provider_type = selected_provider(config)["type"]
            client = create_client(config, api_key(config))
            if on_text is not None and _streams(client):
                content, metadata, interrupted = _stream_reply(client.stream(messages), on_text, cancel)
                reply = Reply(content, metadata if type(metadata) is dict else {})
            else:
                reply = client.complete(messages)
                interrupted = cancel is not None and cancel.is_set()
            if not isinstance(reply.content, str) or not reply.content.strip():
                if interrupted:
                    raise ChatInterrupted("reply interrupted before any text arrived; history unchanged")
                raise LLMToolError("invalid chat reply")
        except ChatInterrupted:
            self._request_failed(started, None)
            self.state = "interrupted"
            self.last_interrupted = True
            raise
        except LLMToolError as exc:
            self._request_failed(started, _error_code(exc))
            raise
        except Exception:
            self._request_failed(started, None)
            # This boundary also hides unexpected transport/auth exception details.
            # KeyboardInterrupt remains deliberate session termination.
            raise LLMToolError("chat request failed; history unchanged; retry or select another provider") from None
        except BaseException:
            self._request_failed(started, None)
            raise
        # Secondary metadata never raises and is computed before history changes.
        usage = reply_usage(reply.metadata)
        reported = _reported_model(provider_type, reply.metadata)
        self.history.extend((user, {"role": "assistant", "content": reply.content}))
        if self._history_store is not None:
            self._history_store.save(self.history)
            self.history_notice = self._history_store.notice
        self.last_elapsed = max(0.0, time.monotonic() - started)
        self.last_usage = usage
        self.last_reported_model = reported
        self.request_started = None
        self.last_interrupted = interrupted
        self.state = "interrupted" if interrupted else "idle"
        return reply.content

    def _request_failed(self, started, code):
        self.last_elapsed = max(0.0, time.monotonic() - started)
        self.last_usage = None
        self.last_reported_model = None
        self.last_error_code = code
        self.request_started = None
        self.state = "error"

    def command(self, line):
        parts = line.strip().split()
        command = parts[0] if parts else ""
        argument = parts[1] if len(parts) == 2 else None
        if len(parts) > 2 or (len(parts) == 2 and command not in ("/provider", "/skill")):
            raise LLMToolError("invalid chat command arguments; use /help")
        if command == "/help":
            return HELP
        if command == "/status":
            return self.status() + "\n" + self.runtime_status()
        if command == "/provider":
            if argument is not None:
                self.switch_provider(argument)
                return "provider: " + self.provider + "; conversation history will be sent to this provider on the next user turn"
            return "provider: " + self.provider + "\navailable profiles:\n" + "\n".join(
                "  " + name + " (" + self.config["providers"][name]["type"] + ")"
                for name in sorted(self.config["providers"])
            )
        if command == "/skill":
            if argument is not None:
                self.select_skill(argument)
                return "skill: " + (self.skill.name if self.skill else "none")
            return "skill: " + (self.skill.name if self.skill else "none") + "\navailable skills:\n" + "\n".join(
                "  " + skill.name for skill in discover_skills()
            )
        if command == "/clear":
            self.history.clear()
            if self._history_store is not None:
                self._history_store.clear()
                self.history_notice = self._history_store.notice
            self.reset_status()
            return "conversation cleared"
        if command == "/history":
            return "\n".join(turn["role"] + ":\n" + turn["content"] for turn in self.history) or "conversation history is empty"
        if command in ("/exit", "/quit"):
            self.active = False
            return "chat ended"
        raise LLMToolError("unknown chat command; use /help")


def format_usage(usage):
    if usage is None:
        return UNKNOWN
    return ("prompt " + str(usage["prompt_tokens"]) + " / completion " + str(usage["completion_tokens"])
            + " / total " + str(usage["total_tokens"]) + " (provider-reported)")


def run_chat(session, read_line=None, stdout=None, stderr=None):
    """One input line per turn, preserving spaces; input() removes only newline."""
    read_line = input if read_line is None else read_line
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    print("LLMTool interactive chat\n" + session.status(), file=stdout)
    if session._history_store is None:
        print("History stays in memory. /help for commands.", file=stdout)
    else:
        print("History is stored locally. /help for commands.", file=stdout)
    if session.history_notice:
        print("llmtool: " + session.history_notice, file=stderr)
        session.history_notice = None
    try:
        while session.active:
            print("> ", end="", file=stdout, flush=True)
            try:
                line = read_line()
                if not line.strip():
                    continue
                output = session.command(line) if line.lstrip().startswith("/") else session.send(line)
                if output is not None:
                    print(human_output(output, stdout), file=stdout)
                if session.history_notice:
                    print("llmtool: " + session.history_notice, file=stderr)
                    session.history_notice = None
            except LLMToolError as exc:
                print("llmtool: " + str(exc), file=stderr)
            except (OSError, UnicodeError):
                print("llmtool: chat I/O failed; details omitted", file=stderr)
                return 1
            except (RuntimeError, ValueError):
                print("llmtool: chat I/O failed; details omitted", file=stderr)
                return 1
    except (EOFError, KeyboardInterrupt):
        print("\nchat ended", file=stdout)
    finally:
        session.active = False
    return 0
