"""Run one command using the isolated AI GitHub identity."""

import os
from pathlib import Path
import subprocess
import sys

from llmtool.github_identity import ai_github_env, ai_login


def _refuse(code):
    print(code, file=sys.stderr)
    return 2


def main(argv, base_env=None, home=None, runner=None, execer=None):
    """Validate the AI GitHub account, then replace this process with a command."""
    if runner is None:
        runner = subprocess.run
    if execer is None:
        execer = os.execvpe

    args = list(argv)
    if not args or args[0] != "--" or len(args) < 2 or not args[1]:
        return _refuse("AI_GH_USAGE")
    command, command_args = args[1], args[2:]
    if (os.path.basename(command) == "gh" and command_args
            and command_args[0] == "auth"
            and (len(command_args) < 2 or command_args[1] != "status")):
        return _refuse("AI_GH_AUTH_REFUSED")

    if base_env is None:
        base_env = os.environ
    if home is None:
        home = base_env.get("HOME") or str(Path.home())
    env = ai_github_env(base_env, home)
    if not os.path.isdir(env["GH_CONFIG_DIR"]):
        return _refuse("AI_GH_CONFIG_MISSING")

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
    except (OSError, subprocess.TimeoutExpired):
        return _refuse("AI_GH_LOGIN_MISMATCH")
    if result.returncode != 0:
        return _refuse("AI_GH_LOGIN_MISMATCH")
    try:
        login = result.stdout.decode("utf-8").rstrip("\r\n")
    except (AttributeError, UnicodeDecodeError):
        return _refuse("AI_GH_LOGIN_MISMATCH")
    if login != ai_login():
        return _refuse("AI_GH_LOGIN_MISMATCH")

    execer(command, [command] + command_args, env)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
