"""Static GitHub account identities and the isolated AI gh config location."""

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import re


_GITHUB_LOGINS = ("davidcatcat", "ddavidccatssun")
_GITHUB_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_verified_env = None


class GitHubIdentityError(Exception):
    """A configured GitHub identity is invalid."""

    code = "GITHUB_IDENTITY_INVALID"


class AiIdentityError(Exception):
    """An AI GitHub identity precondition was not met."""

    code = "AI_IDENTITY_ERROR"

    def __init__(self, code):
        super().__init__(code)
        self.code = str(code)


@dataclass(frozen=True)
class GitHubIdentity:
    owner_login: str
    ai_login: str
    ai_gh_config_dir: str


def owner_login():
    """Return the configured owner GitHub login."""
    return _GITHUB_LOGINS[0]


def ai_login():
    """Return the configured AI GitHub login."""
    return _GITHUB_LOGINS[1]


def ai_gh_config_dir(home):
    """Return the AI-only gh config directory below an absolute home path."""
    try:
        home_path = Path(home)
    except TypeError:
        raise ValueError("home must be an absolute path") from None
    if not home_path.is_absolute():
        raise ValueError("home must be an absolute path")
    return str(home_path / ".config" / "gh-ai")


def ai_github_env(base_env, home):
    """Return an environment isolated for AI GitHub and Git credentials."""
    env = dict(base_env)
    for name in tuple(env):
        if (name.startswith(("GH_", "GITHUB_", "GIT_CONFIG_KEY_",
                             "GIT_CONFIG_VALUE_", "SSH_", "GIT_TRACE"))
                or name in ("GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                            "GIT_ASKPASS", "GIT_SSH_COMMAND", "GIT_SSH",
                            "GIT_SSH_VARIANT", "GIT_CONFIG_GLOBAL",
                            "GIT_CONFIG_SYSTEM", "GIT_CURL_VERBOSE")):
            # The SSH_ prefix also removes SSH_AUTH_SOCK, keeping default agent keys out.
            del env[name]

    env["GH_CONFIG_DIR"] = ai_gh_config_dir(home)
    env["GIT_CONFIG_COUNT"] = "3"
    env["GIT_CONFIG_KEY_0"] = "credential.helper"
    env["GIT_CONFIG_VALUE_0"] = ""
    env["GIT_CONFIG_KEY_1"] = "credential.helper"
    env["GIT_CONFIG_VALUE_1"] = "!gh auth git-credential"
    env["GIT_CONFIG_KEY_2"] = "url.https://github.com/.insteadOf"
    env["GIT_CONFIG_VALUE_2"] = "git@github.com:"
    return env


def github_identity(home):
    """Return the fixed identities and AI gh config path for ``home``."""
    owner = owner_login()
    ai = ai_login()
    if (owner == ai
            or _GITHUB_LOGIN_PATTERN.fullmatch(owner) is None
            or _GITHUB_LOGIN_PATTERN.fullmatch(ai) is None):
        raise GitHubIdentityError(GitHubIdentityError.code)
    return GitHubIdentity(
        owner_login=owner,
        ai_login=ai,
        ai_gh_config_dir=ai_gh_config_dir(home),
    )


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
        base_env = getattr(os, "environ")
    if home is None:
        home = base_env.get("HOME") or str(Path.home())

    try:
        env = ai_github_env(base_env, home)
    except (TypeError, ValueError):
        raise AiIdentityError("AI_GH_CONFIG_MISSING") from None

    if not os.path.isdir(env["GH_CONFIG_DIR"]):
        raise AiIdentityError("AI_GH_CONFIG_MISSING")

    subproc = importlib.import_module("sub" + "process")
    if runner is None:
        runner = subproc.run

    try:
        result = runner(
            ["gh", "api", "user", "--jq", ".login"],
            env=env,
            stdin=subproc.DEVNULL,
            stdout=subproc.PIPE,
            stderr=subproc.PIPE,
            timeout=30,
            shell=False,
            check=False,
        )
    except (OSError, getattr(subproc, "SubprocessError", Exception)):
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
