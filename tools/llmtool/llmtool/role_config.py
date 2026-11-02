"""User-only account and workflow role configuration."""

import os
import re
import stat

from .errors import LLMToolError

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ROLE_FIELDS = {"provider", "provider_profile", "account", "model", "effort"}
_ENV_FIELDS = {
    "LLMTOOL_ROLE_PROVIDER": "provider",
    "LLMTOOL_ROLE_PROVIDER_PROFILE": "provider_profile",
    "LLMTOOL_ROLE_ACCOUNT": "account",
    "LLMTOOL_ROLE_MODEL": "model",
    "LLMTOOL_ROLE_EFFORT": "effort",
}


def _fail(message="role configuration: invalid configuration", code="ROLE_CONFIG_INVALID"):
    error = LLMToolError(message)
    error.role_config_code = code
    raise error from None


def _name(value):
    return type(value) is str and _NAME.fullmatch(value) is not None


def _safe_text(value):
    return (type(value) is str and 1 <= len(value) <= 256 and
            not any(ord(char) < 0x20 or ord(char) == 0x7f for char in value))


def _validate_account_dir(value):
    """Validate metadata only; never follow a leaf symlink or read its contents."""
    if (type(value) is not str or not value or not os.path.isabs(value) or
            any(ord(char) < 0x20 or ord(char) == 0x7f for char in value)):
        _fail("role account config directory is invalid", "ROLE_ACCOUNT_DIR_INVALID")
    try:
        info = os.lstat(value)
    except OSError:
        _fail("role account config directory is unavailable", "ROLE_ACCOUNT_DIR_INVALID")
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        _fail("role account config directory is not a private user directory",
              "ROLE_ACCOUNT_DIR_INVALID")
    return value


def validate_role_config(user):
    """Validate user-only accounts/roles plus the legacy PM alias."""
    accounts = user.get("accounts", {})
    roles = user.get("roles", {})
    if type(accounts) is not dict or type(roles) is not dict:
        _fail()
    for name, account in accounts.items():
        if not _name(name) or type(account) is not dict or set(account) != {"kind", "config_dir"}:
            _fail()
        if account["kind"] not in ("claude", "codex"):
            _fail()
        _validate_account_dir(account["config_dir"])
    for role, settings in roles.items():
        if not _name(role) or type(settings) is not dict or set(settings) - _ROLE_FIELDS:
            _fail()
        if "provider" in settings and settings["provider"] not in ("claude", "codex"):
            _fail()
        if "provider_profile" in settings and not _name(settings["provider_profile"]):
            _fail()
        if "account" in settings and (not _name(settings["account"]) or settings["account"] not in accounts):
            _fail()
        for key in ("model", "effort"):
            if key in settings and not _safe_text(settings[key]):
                _fail()
        if ("account" in settings and "provider" in settings and
                accounts[settings["account"]]["kind"] != settings["provider"]):
            _fail()
    legacy = user.get("pm_runtime", {})
    if type(legacy) is not dict:
        _fail()
    legacy_dir = legacy.get("claude_config_dir")
    pm = roles.get("pm", {})
    if legacy_dir is not None and type(pm) is dict:
        account = pm.get("account")
        if (pm.get("provider") not in (None, "claude") or
                account in accounts and accounts[account]["config_dir"] != legacy_dir):
            _fail("legacy PM account setting conflicts with roles.pm", "ROLE_CONFIG_ALIAS_CONFLICT")
    return accounts, roles


def _override_value(key, value):
    if key in ("provider",):
        if value not in ("claude", "codex"):
            _fail("role override is invalid", "ROLE_CONFIG_OVERRIDE_INVALID")
    elif key in ("provider_profile", "account"):
        if not _name(value):
            _fail("role override is invalid", "ROLE_CONFIG_OVERRIDE_INVALID")
    elif key in ("model", "effort"):
        if not _safe_text(value):
            _fail("role override is invalid", "ROLE_CONFIG_OVERRIDE_INVALID")


def resolve_role(role, cli=None, environ=None, *, home=None):
    """Resolve one role. CLI and environment overrides are one-shot and never save."""
    import copy
    from .config import DEFAULTS, _apply_user, user_config_path, _read_yaml

    if not _name(role):
        _fail("role name is invalid", "ROLE_CONFIG_ROLE_INVALID")
    path = user_config_path(home)
    user = _read_yaml(path, "user config") if os.path.lexists(path) else {}
    loaded = copy.deepcopy(DEFAULTS)
    _apply_user(loaded, user)
    # _apply_user retains validated accounts/roles in this user-config view;
    # read from it so role resolution follows the same loader path as the
    # ordinary llmtool configuration.
    accounts = loaded.get("accounts", {})
    roles = loaded.get("roles", {})
    validate_role_config({**user, "accounts": accounts, "roles": roles})
    legacy = user.get("pm_runtime", {}).get("claude_config_dir")
    configured = roles.get(role, {})
    settings = {"role": role, "provider": None, "provider_profile": None,
                "account": None, "config_dir": None, "model": None, "effort": None}
    source = "builtin"
    if role in roles:
        settings.update(configured)
        source = "roles"
    if role == "pm" and legacy is not None:
        if settings["account"] is None:
            settings["provider"] = "claude"
            settings["config_dir"] = legacy
            source = "pm_runtime"
    if settings["account"] is not None:
        account = accounts[settings["account"]]
        settings["provider"] = settings["provider"] or account["kind"]
        settings["config_dir"] = account["config_dir"]
    env = os.environ if environ is None else environ
    for source_name, values in (("env", {field: env[name] for name, field in _ENV_FIELDS.items()
                                             if name in env}),
                                ("cli", cli or {})):
        if source_name == "cli":
            values = {key: value for key, value in values.items()
                      if key in _ROLE_FIELDS and value is not None}
        if values:
            source = source_name
        for key, value in values.items():
            if key not in _ROLE_FIELDS:
                continue
            _override_value(key, value)
            settings[key] = value
            if key == "account":
                if value not in accounts:
                    _fail("role override names an unknown account", "ROLE_CONFIG_OVERRIDE_INVALID")
                settings["provider"] = accounts[value]["kind"]
                settings["config_dir"] = accounts[value]["config_dir"]
            elif key == "provider" and settings["account"] is not None:
                if accounts[settings["account"]]["kind"] != value:
                    _fail("role override conflicts with account kind", "ROLE_CONFIG_OVERRIDE_INVALID")
    settings["source"] = source
    return settings
