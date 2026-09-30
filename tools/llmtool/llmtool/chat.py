"""Memory-only conversation state and a text REPL; no action execution."""

import copy
import sys

from .config import api_key, selected_provider
from .errors import LLMToolError
from .inputs import INPUT_LIMIT
from .prompts import chat_messages
from .providers import client_type, create_client
from .skills import discover_skills, load_skill

HELP = """/help                 Show commands
/status               Show provider, type, skill and turn count
/provider [profile]   List profiles or switch (history sent next turn)
/skill [name|none]     List skills, select one, or clear selection
/clear                Clear conversation; keep provider and skill
/history              Show user/assistant turns only
/exit, /quit          Exit; EOF and Ctrl-C also exit cleanly"""


class ChatSession:
    """A config snapshot plus successful turns. Never saves config or history."""

    def __init__(self, config, skill=None):
        self.config = copy.deepcopy(config)
        selected_provider(self.config)
        self.skill = skill
        self.history = []
        self.active = True

    @property
    def provider(self):
        return self.config["active_provider"]

    def status(self):
        return ("provider: " + self.provider + "\n"
                "type: " + selected_provider(self.config)["type"] + "\n"
                "skill: " + (self.skill.name if self.skill else "none") + "\n"
                "conversation turns: " + str(len(self.history)))

    def switch_provider(self, name):
        candidate = copy.deepcopy(self.config)
        candidate["active_provider"] = name
        provider = selected_provider(candidate)
        client_type(provider["type"]).validate_ready(provider)
        self.config = candidate

    def select_skill(self, name):
        candidate = None if name == "none" else load_skill(name)
        self.skill = candidate

    def send(self, text):
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
        try:
            reply = create_client(self.config, api_key(self.config)).complete(messages)
            if not isinstance(reply.content, str) or not reply.content.strip():
                raise LLMToolError("invalid chat reply")
        except Exception:
            # This boundary also hides unexpected transport/auth exception details.
            # KeyboardInterrupt remains deliberate session termination.
            raise LLMToolError("chat request failed; history unchanged; retry or select another provider") from None
        self.history.extend((user, {"role": "assistant", "content": reply.content}))
        return reply.content

    def command(self, line):
        parts = line.strip().split()
        command = parts[0] if parts else ""
        argument = parts[1] if len(parts) == 2 else None
        if len(parts) > 2 or (len(parts) == 2 and command not in ("/provider", "/skill")):
            raise LLMToolError("invalid chat command arguments; use /help")
        if command == "/help":
            return HELP
        if command == "/status":
            return self.status()
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
            return "conversation cleared"
        if command == "/history":
            return "\n".join(turn["role"] + ":\n" + turn["content"] for turn in self.history) or "conversation history is empty"
        if command in ("/exit", "/quit"):
            self.active = False
            return "chat ended"
        raise LLMToolError("unknown chat command; use /help")


def run_chat(session, read_line=None, stdout=None, stderr=None):
    """One input line per turn, preserving spaces; input() removes only newline."""
    read_line = input if read_line is None else read_line
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    print("LLMTool interactive chat\n" + session.status(), file=stdout)
    print("History stays in memory. /help for commands.", file=stdout)
    try:
        while session.active:
            print("> ", end="", file=stdout, flush=True)
            try:
                line = read_line()
                if not line.strip():
                    continue
                output = session.command(line) if line.lstrip().startswith("/") else session.send(line)
                if output is not None:
                    print(output, file=stdout)
            except LLMToolError as exc:
                print("llmtool: " + str(exc), file=stderr)
            except (OSError, UnicodeError):
                print("llmtool: chat I/O failed; details omitted", file=stderr)
                break
    except (EOFError, KeyboardInterrupt):
        print("\nchat ended", file=stdout)
    finally:
        session.active = False
    return 0
