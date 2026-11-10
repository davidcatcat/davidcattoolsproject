"""Strict gateway profile loading; credential values are never returned."""

import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

from .errors import coded_error

SCHEMA_VERSION = 1
FILE_LIMIT = 64 * 1024
TOP_FIELDS = frozenset(("schema_version", "default", "profiles"))
PROFILE_FIELDS = frozenset((
    "base_url", "model", "api_key_env", "context_cap_tokens", "max_output_tokens",
))
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def default_path():
    """Return the standard per-user profile file location."""
    return Path.home() / ".config/llmtool/gateway-profiles.json"


def _error(message, code, user_action_required=False):
    return coded_error(message, code, "config.gateway_profiles",
                       user_action_required=user_action_required)


def _owned_by_user(info):
    getuid = getattr(os, "getuid", None)
    return getuid is None or info.st_uid == getuid()


def _read_file(path):
    """Read a private regular file. Refuse links, foreign owners and open modes."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise _error("gateway profiles file is missing", "CONFIG_INVALID") from None
    except OSError:
        raise _error("gateway profiles file cannot be read", "CONFIG_INVALID") from None
    if stat.S_ISLNK(info.st_mode):
        raise _error("gateway profiles file must not be a symlink", "CONFIG_INVALID")
    if not stat.S_ISREG(info.st_mode):
        raise _error("gateway profiles file is not a regular file", "CONFIG_INVALID")
    if not _owned_by_user(info):
        raise _error("gateway profiles file is not owned by the user", "CONFIG_INVALID")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise _error("gateway profiles file mode must be 0600", "CONFIG_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise _error("gateway profiles file cannot be read", "CONFIG_INVALID") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or not _owned_by_user(opened)
                    or stat.S_IMODE(opened.st_mode) != 0o600):
                raise _error("gateway profiles file is not safe", "CONFIG_INVALID")
            raw = stream.read(FILE_LIMIT + 1)
    except OSError:
        raise _error("gateway profiles file cannot be read", "CONFIG_INVALID") from None
    if len(raw) > FILE_LIMIT:
        raise _error("gateway profiles file exceeds 64 KiB", "CONFIG_INVALID")
    return raw


def _reject_constant(value):
    raise ValueError(value)


def _parse(raw):
    try:
        document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError):
        raise _error("gateway profiles file is not valid JSON", "CONFIG_INVALID") from None
    if type(document) is not dict or set(document) != TOP_FIELDS:
        raise _error("gateway profiles file has unexpected fields", "CONFIG_INVALID")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise _error("gateway profiles schema version is unsupported", "CONFIG_INVALID")
    profiles = document["profiles"]
    if type(profiles) is not dict or not profiles:
        raise _error("gateway profiles must be a non-empty mapping", "CONFIG_INVALID")
    default = document["default"]
    if type(default) is not str or PROFILE_NAME.fullmatch(default) is None:
        raise _error("gateway profiles default name is invalid", "CONFIG_INVALID")
    for name, value in profiles.items():
        if type(name) is not str or PROFILE_NAME.fullmatch(name) is None:
            raise _error("gateway profile name is invalid", "CONFIG_INVALID")
        _validate_profile(value)
    if default not in profiles:
        raise _error("gateway profile default is not defined", "CONFIG_PROVIDER_NOT_READY")
    return profiles, default


def _validate_profile(value):
    if type(value) is not dict or set(value) != PROFILE_FIELDS:
        raise _error("gateway profile has unexpected fields", "CONFIG_INVALID")
    try:
        base_url = value["base_url"]
        if (type(base_url) is not str or not base_url
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in base_url)):
            raise ValueError
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError
        if parsed.port is not None and parsed.port < 1:
            raise ValueError
    except ValueError:
        raise _error("gateway profile base_url must be an HTTP(S) origin",
                     "CONFIG_INVALID") from None
    model = value["model"]
    if (type(model) is not str or not model.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in model)):
        raise _error("gateway profile model must be non-empty text", "CONFIG_INVALID")
    env_name = value["api_key_env"]
    if type(env_name) is not str or ENV_NAME.fullmatch(env_name) is None:
        raise _error("gateway profile api_key_env must be an environment variable name",
                     "CONFIG_INVALID")
    for field in ("context_cap_tokens", "max_output_tokens"):
        cap = value[field]
        if type(cap) is not int or cap <= 0:
            raise _error("gateway profile token caps must be positive integers",
                         "CONFIG_INVALID")


def load_gateway_profile(name=None, path=None, environ=None):
    """Load one validated profile. The API key value is checked but never returned."""
    profiles, default = _parse(_read_file(default_path() if path is None else Path(path)))
    if name is None:
        selected = default
    elif type(name) is not str or PROFILE_NAME.fullmatch(name) is None or name not in profiles:
        raise _error("requested gateway profile is not defined", "CONFIG_PROVIDER_NOT_READY")
    else:
        selected = name
    profile = profiles[selected]
    env = os.environ if environ is None else environ
    if not env.get(profile["api_key_env"]):
        raise _error("gateway profile API key environment variable is missing or empty",
                     "AUTH_CREDENTIAL_MISSING", user_action_required=True)
    return {
        "name": selected,
        "base_url": profile["base_url"].rstrip("/"),
        "model": profile["model"],
        "api_key_env": profile["api_key_env"],
        "context_cap_tokens": profile["context_cap_tokens"],
        "max_output_tokens": profile["max_output_tokens"],
    }
