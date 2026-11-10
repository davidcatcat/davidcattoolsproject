"""Run a command using the isolated AI GitHub identity.

This launcher guards against accidents in direct ``gh`` use; it is not a
containment or security boundary. Any wrapped command holds the AI credential:
environment access, ``sh -c``, ``git credential fill``, and ``gh`` aliases are
not blocked.
"""

import os
from pathlib import Path
import subprocess
import sys

from llmtool.github_identity import ai_github_env, ai_login

_verified_env = None


class AiIdentityError(Exception):
    """An AI GitHub identity precondition was not met."""

    code = "AI_IDENTITY_ERROR"

    def __init__(self, code):
        super().__init__(code)
        self.code = str(code)


def clear_verified_ai_env_cache():
    """Clear the cached verified AI environment."""
    global _verified_env
    _verified_env = None


def verified_ai_env(base_env=None, home=None, runner=None):
    """Return an isolated and verified environment for AI operations."""
    global _verified_env
    if _verified_env is not None:
        return dict(_verified_env)

    if base_env is None:
        base_env = os.environ
    if home is None:
        home = base_env.get("HOME") or str(Path.home())

    try:
        env = ai_github_env(base_env, home)
    except (TypeError, ValueError):
        raise AiIdentityError("AI_GH_CONFIG_MISSING") from None

    if not os.path.isdir(env["GH_CONFIG_DIR"]):
        raise AiIdentityError("AI_GH_CONFIG_MISSING")

    if runner is None:
        runner = subprocess.run

    try:
        result = runner(
            ["gh", "api", "user", "--jq", ".login"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise AiIdentityError("AI_GH_LOGIN_MISMATCH") from None
    except Exception:
        raise AiIdentityError("AI_GH_LOGIN_MISMATCH") from None

    if getattr(result, "returncode", 1) != 0:
        raise AiIdentityError("AI_GH_LOGIN_MISMATCH")

    try:
        stdout = getattr(result, "stdout", b"")
        if isinstance(stdout, bytes):
            login = stdout.decode("utf-8").rstrip("\r\n")
        elif isinstance(stdout, str):
            login = stdout.rstrip("\r\n")
        else:
            raise AiIdentityError("AI_GH_LOGIN_MISMATCH")
    except (AttributeError, UnicodeDecodeError):
        raise AiIdentityError("AI_GH_LOGIN_MISMATCH") from None

    if login != ai_login():
        raise AiIdentityError("AI_GH_LOGIN_MISMATCH")

    _verified_env = dict(env)
    return dict(_verified_env)


def _refuse(code):
    print(code, file=sys.stderr)
    return 2


def main(argv, base_env=None, home=None, runner=None, execer=None):
    """Validate the AI GitHub account, then replace this process with a command."""
    if execer is None:
        execer = os.execvpe

    args = list(argv)
    if not args or args[0] != "--" or len(args) < 2 or not args[1]:
        return _refuse("AI_GH_USAGE")
    command, command_args = args[1], args[2:]
    if (os.path.basename(command).lower() == "gh" and command_args
            and command_args[0] == "auth"
            and command_args != ["auth", "status"]):
        return _refuse("AI_GH_AUTH_REFUSED")

    try:
        env = verified_ai_env(base_env=base_env, home=home, runner=runner)
    except AiIdentityError as exc:
        return _refuse(exc.code)

    try:
        execer(command, [command] + command_args, env)
    except OSError:
        return _refuse("AI_GH_EXEC_FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
