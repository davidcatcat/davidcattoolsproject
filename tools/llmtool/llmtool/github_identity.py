"""Static GitHub account identities and the isolated AI gh config location."""

from dataclasses import dataclass
from pathlib import Path
import re


_GITHUB_LOGINS = ("davidcatcat", "ddavidccatssun")
_GITHUB_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


class GitHubIdentityError(Exception):
    """A configured GitHub identity is invalid."""

    code = "GITHUB_IDENTITY_INVALID"


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
