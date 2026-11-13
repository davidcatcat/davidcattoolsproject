"""User-only account and workflow role configuration."""

import os
import re
import stat

from .errors import LLMToolError

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ROLE_FIELDS = {"provider", "provider_profile", "account", "model", "effort"}
_ENV_FIELDS = {
    "PROVIDER": "provider",
    "PROVIDER_PROFILE": "provider_profile",
    "ACCOUNT": "account",
    "MODEL": "model",
    "EFFORT": "effort",
}
# #1502 WP-E: Developer role ids bound to a developer-routes.json route. The
# route supplies the repo-default model and effort and its allowed_models.
ROUTE_ROLES = {
    "agy-developer": "agy",
    "claude-developer": "claude",
    "gw-agent-developer": "gw-agent",
}
# Legacy user-config role names (keys) are aliases of the new ids (values),
# so an existing user config keeps working without an edit.
LEGACY_ROLE_ALIASES = {"reviewer": "claude-reviewer"}
# The provider each provider-bound role id runs under. A legacy alias entry is
# accepted only when its provider and account kind match its target role's
# provider here (#1777, CWE-863); a target missing here accepts no binding.
ROLE_PROVIDERS = {
    "claude-debug": "claude", "claude-developer": "claude", "claude-reviewer": "claude",
    "codex-debug": "codex", "codex-developer": "codex", "codex-reviewer": "codex",
}
ROLE_ALIAS_PROVIDER_MISMATCH = "ROLE_ALIAS_PROVIDER_MISMATCH"
# Role ids `llmtool roles` always lists, in addition to configured roles.
KNOWN_ROLES = (
    "agy-developer", "claude-debug", "claude-developer", "claude-reviewer",
    "codex-debug", "codex-developer", "codex-reviewer", "gw-agent-developer",
    "opencode-developer", "pm",
)
ROLE_MODEL_NOT_ALLOWED = "ROLE_MODEL_NOT_ALLOWED"


def _fail(message="role configuration: invalid configuration", code="ROLE_CONFIG_INVALID"):
    error = LLMToolError(message)
    error.role_config_code = code
    raise error from None


def _fail_model(detail=None):
    """Refuse a Developer model outside its route's allowed_models."""
    message = "role model is not allowed for its route: " + ROLE_MODEL_NOT_ALLOWED
    if detail is not None:
        message += " (" + detail + ")"
    error = LLMToolError(message)
    error.role_config_code = ROLE_MODEL_NOT_ALLOWED
    error.owner_override_code = detail
    raise error from None


def config_names(role):
    """Return the user-config names for role: itself first, then its aliases."""
    names = [role]
    if role in LEGACY_ROLE_ALIASES:
        names.append(LEGACY_ROLE_ALIASES[role])
    names.extend(legacy for legacy, new in sorted(LEGACY_ROLE_ALIASES.items())
                 if new == role)
    return names


def _check_alias_provider(role, configured_name, configured, accounts):
    """Refuse a legacy alias whose entry binds another provider's account.

    Applies when the configured entry is a legacy name, or when role itself is
    a legacy name (it then stands for its target role). The entry's explicit
    provider and its account kind must each be absent or equal to the target
    role's provider; an entry with neither binds no account and is accepted.
    """
    legacy = next((name for name in (configured_name, role)
                   if name in LEGACY_ROLE_ALIASES), None)
    if legacy is None:
        return
    expected = ROLE_PROVIDERS.get(LEGACY_ROLE_ALIASES[legacy])
    account = configured.get("account")
    bound = (configured.get("provider"),
             accounts[account]["kind"] if account is not None else None)
    if any(value is not None and value != expected for value in bound):
        _fail("legacy role alias names an account of another provider",
              ROLE_ALIAS_PROVIDER_MISMATCH)


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


def _load_user(home):
    import copy
    from .config import DEFAULTS, _apply_user, user_config_path, _read_yaml

    path = user_config_path(home)
    user = _read_yaml(path, "user config") if os.path.lexists(path) else {}
    loaded = copy.deepcopy(DEFAULTS)
    _apply_user(loaded, user)
    return user


def configured_roles(home=None):
    """Return the validated role names in the user config (no other data)."""
    _, roles = validate_role_config(_load_user(home))
    return sorted(roles)


def _env_values(env, role):
    """Return env overrides; LLMTOOL_ROLE_<ROLE>_ accepts '-' or '_' in ROLE."""
    prefixes = ["LLMTOOL_ROLE_" + role.upper() + "_"]
    underscored = "LLMTOOL_ROLE_" + role.upper().replace("-", "_") + "_"
    if underscored != prefixes[0]:
        prefixes.append(underscored)
    values = {}
    for suffix, field in _ENV_FIELDS.items():
        found = [env[prefix + suffix] for prefix in prefixes if prefix + suffix in env]
        if any(value != found[0] for value in found):
            _fail("role override is ambiguous", "ROLE_CONFIG_OVERRIDE_INVALID")
        if found:
            values[field] = found[0]
    return values


def registry_role(role):
    """Return the canonical review/debug role id for role (or its alias), else None."""
    from .developer_routes import REGISTRY_ROLES
    canonical = LEGACY_ROLE_ALIASES.get(role, role)
    return canonical if canonical in REGISTRY_ROLES else None


def check_registry_model(role, model, role_registry=None):
    """Return model when the shared registry check admits it for role, else refuse.

    #1502 WP-E R1: a thin wrapper over developer_routes.check_registry_role_model
    that raises the role-config error (role_config_code ROLE_MODEL_NOT_ALLOWED).
    role may be a legacy alias; role_registry None loads developer-routes.json.
    """
    from .developer_routes import RouteError, check_registry_role_model
    try:
        return check_registry_role_model(role_registry, registry_role(role), model)
    except RouteError:
        _fail_registry_model()


def _fail_registry_model():
    error = LLMToolError("role model is not allowed for its role: " + ROLE_MODEL_NOT_ALLOWED)
    error.role_config_code = ROLE_MODEL_NOT_ALLOWED
    error.owner_override_code = None
    raise error from None


def _registry_default(role, role_registry):
    """Return (config, the registry's listed model for role); refuse when absent."""
    from .developer_routes import RouteError, load_route_config
    try:
        config = load_route_config() if role_registry is None else role_registry
        models = config["roles"][role]["allowed_models"]
    except (RouteError, KeyError, TypeError):
        _fail_registry_model()
    if type(models) is not list or not models or type(models[0]) is not str:
        _fail_registry_model()
    return config, models[0]


def _route_entry(route, route_config):
    """Return one developer-routes.json entry, loaded when not supplied."""
    if route_config is None:
        from .developer_routes import RouteError, load_route_config
        try:
            route_config = load_route_config()["routes"].get(route)
        except RouteError:
            _fail("developer route configuration is invalid", "ROUTE_CONFIG_INVALID")
    if (type(route_config) is not dict or
            type(route_config.get("default_model")) is not str or
            type(route_config.get("default_effort")) is not str or
            type(route_config.get("allowed_models")) is not list):
        _fail("developer route configuration is invalid", "ROUTE_CONFIG_INVALID")
    return route_config


def _default_repository():
    from .config import repository_config
    from .owner_override import OwnerOverrideError
    try:
        return repository_config()["slug"]
    except Exception:
        raise OwnerOverrideError("OWNER_OVERRIDE_INVALID") from None


def check_role_model(route, route_config, model, *, owner_override=None,
                     repository=None, fetch_comment=None):
    """Return None for an allowed model, else the verified owner override.

    A model outside the route's allowed_models is refused with
    ROLE_MODEL_NOT_ALLOWED unless owner_override ({"model", "ref"}) names the
    same model and owner_override.py verifies that its ref names this route and
    model. fetch_comment(comment_id) is the injected GitHub reader; without it
    the ref is unreadable and the override is refused.
    """
    from .developer_routes import RouteError, check_route_model
    from .owner_override import OwnerOverrideError, validate_owner_override

    try:
        check_route_model({"routes": {route: route_config}}, route, model)
        return None
    except RouteError:
        pass
    if owner_override is None:
        _fail_model()
    try:
        if type(owner_override) is not dict or owner_override.get("model") != model:
            raise OwnerOverrideError("OWNER_OVERRIDE_MISMATCH")
        if fetch_comment is None:
            raise OwnerOverrideError("OWNER_OVERRIDE_REF_UNREADABLE")
        if repository is None:
            repository = _default_repository()
        accepted = validate_owner_override(owner_override, route, repository=repository,
                                           fetch_comment=fetch_comment)
    except OwnerOverrideError as exc:
        _fail_model(exc.code)
    if accepted.get("route") != route or accepted.get("model") != model:
        _fail_model("OWNER_OVERRIDE_MISMATCH")
    return accepted


def resolve_role(role, cli=None, environ=None, *, home=None, route_config=None,
                 owner_override=None, repository=None, fetch_comment=None,
                 role_registry=None):
    """Resolve one role. CLI and environment overrides are one-shot and never save.

    Precedence: CLI > env > user config (the role or its legacy alias) >
    repo default. Developer roles (ROUTE_ROLES) take their repo default model
    and effort from developer-routes.json (route_config overrides the loaded
    entry) and their model must be in allowed_models; see check_role_model.
    Review/debug roles (and the legacy 'reviewer' alias) take their default
    model from the developer-routes.json roles registry (role_registry
    overrides the loaded config) and the resolved model must pass
    check_registry_model; the registry default never changes 'source'.
    """
    if not _name(role):
        _fail("role name is invalid", "ROLE_CONFIG_ROLE_INVALID")
    user = _load_user(home)
    # Reuse the ordinary user-config validation path, then resolve the
    # user-only role data from that explicit user file.
    # Role data is intentionally not part of the general provider config
    # projection. Validate it directly from the trusted user-config load.
    accounts, roles = validate_role_config(user)
    legacy = user.get("pm_runtime", {}).get("claude_config_dir")
    configured_name = next((name for name in config_names(role) if name in roles), None)
    configured = roles.get(configured_name, {}) if configured_name is not None else {}
    _check_alias_provider(role, configured_name, configured, accounts)
    settings = {"role": role, "provider": None, "provider_profile": None,
                "account": None, "config_dir": None, "model": None, "effort": None}
    source = "builtin"
    if configured_name is not None:
        settings.update(configured)
        source = "roles"
    model_source = "roles" if settings["model"] is not None else None
    effort_source = "roles" if settings["effort"] is not None else None
    if role == "pm" and legacy is not None:
        if settings["account"] is None:
            settings["provider"] = "claude"
            settings["config_dir"] = legacy
            source = "pm_runtime"
    if settings["account"] is not None:
        account = accounts[settings["account"]]
        settings["provider"] = settings["provider"] or account["kind"]
        settings["config_dir"] = _validate_account_dir(account["config_dir"])
    elif settings["config_dir"] is not None:
        settings["config_dir"] = _validate_account_dir(settings["config_dir"])
    env = os.environ if environ is None else environ
    for source_name, values in (("env", _env_values(env, role)),
                                ("cli", cli or {})):
        if source_name == "cli":
            values = {key: value for key, value in values.items()
                      if key in _ROLE_FIELDS and value is not None}
        if values:
            source = source_name
        # Resolve account before provider so a matching pair is independent of
        # mapping insertion order.
        ordered = sorted(values.items(), key=lambda item: (item[0] != "account", item[0]))
        layer_has_account = any(key == "account" for key, _ in ordered)
        for key, value in ordered:
            if key not in _ROLE_FIELDS:
                continue
            _override_value(key, value)
            if key == "account":
                settings[key] = value
                if value not in accounts:
                    _fail("role override names an unknown account", "ROLE_CONFIG_OVERRIDE_INVALID")
                settings["provider"] = accounts[value]["kind"]
                settings["config_dir"] = _validate_account_dir(accounts[value]["config_dir"])
            elif key == "provider":
                if settings["account"] is not None and accounts[settings["account"]]["kind"] != value:
                    _fail("role override conflicts with account kind", "ROLE_CONFIG_OVERRIDE_INVALID")
                if (not layer_has_account and settings["config_dir"] is not None and
                        settings["provider"] != value):
                    _fail("role override conflicts with account directory", "ROLE_CONFIG_OVERRIDE_INVALID")
                settings["provider"] = value
            else:
                settings[key] = value
                if key == "model":
                    model_source = source_name
                elif key == "effort":
                    effort_source = source_name
    review_role = registry_role(role)
    if review_role is not None:
        # #1502 WP-E R1: after precedence, the review/debug model must pass the
        # shared registry check. An unset model takes the registry's listed
        # model. No owner_override applies to these roles.
        if owner_override is not None:
            _fail_registry_model()
        registry, default_model = _registry_default(review_role, role_registry)
        if settings["model"] is None:
            settings["model"] = default_model
            model_source = "repo_default"
        settings["model_source"] = model_source
        check_registry_model(review_role, settings["model"], registry)
    route = ROUTE_ROLES.get(role)
    if route is not None:
        entry = _route_entry(route, route_config)
        if settings["model"] is None:
            settings["model"] = entry["default_model"]
            model_source = "repo_default"
        if settings["effort"] is None:
            settings["effort"] = entry["default_effort"]
            effort_source = "repo_default"
        if source == "builtin":
            source = "repo_default"
        settings["model_source"] = model_source
        settings["effort_source"] = effort_source
        accepted = check_role_model(route, entry, settings["model"],
                                    owner_override=owner_override,
                                    repository=repository, fetch_comment=fetch_comment)
        if accepted is not None:
            settings["owner_override"] = accepted
    settings["source"] = source
    return settings
