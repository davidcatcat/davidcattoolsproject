"""Strict, layered YAML configuration. No implicit credential discovery."""

import copy
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .errors import LLMToolError, coded_error
from .providers import client_type

PROVIDER_DEFAULTS = {
    "type": "openai-compatible",
    "base_url": None,
    "model": None,
    "timeout_seconds": 120,
    "api_key": None,
    "api_key_env": None,
    "provider_options": {},
}
DEFAULTS = {
    "providers": {"default": copy.deepcopy(PROVIDER_DEFAULTS)},
    "generation": {"temperature": 0, "max_tokens": 2000},
    "active_provider": "default",
}
# Paths beginning with 'provider' apply only to the explicitly selected profile.
ENV_OPTIONS = {
    "LLMTOOL_BASE_URL": ("provider", "base_url"),
    "LLMTOOL_MODEL": ("provider", "model"),
    "LLMTOOL_TIMEOUT": ("provider", "timeout_seconds"),
    "LLMTOOL_MAX_TOKENS": ("generation", "max_tokens"),
}
CLI_OPTIONS = {
    "base_url": ENV_OPTIONS["LLMTOOL_BASE_URL"],
    "model": ENV_OPTIONS["LLMTOOL_MODEL"],
    "timeout": ENV_OPTIONS["LLMTOOL_TIMEOUT"],
    "max_tokens": ENV_OPTIONS["LLMTOOL_MAX_TOKENS"],
    "temperature": ("generation", "temperature"),
}
CONFIG_LIMIT = 64 * 1024
MAX_TIMEOUT_SECONDS = 3600.0


def yaml_module():
    try:
        import yaml
    except ImportError:
        raise LLMToolError("PyYAML is required; install tools/llmtool in a virtualenv") from None
    return yaml


def user_config_path(home=None):
    return (Path(home) if home is not None else Path.home()) / ".config/llmtool/config.yaml"


def _read_yaml(path, label):
    yaml = yaml_module()

    class StrictLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise LLMToolError(label + ": YAML aliases are not supported")
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise LLMToolError(label + ": keys must be unique strings")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise LLMToolError(label + ": expected a regular config file")
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise LLMToolError(label + ": expected a regular config file")
            raw = stream.read(CONFIG_LIMIT + 1)
    except FileNotFoundError:
        if path.is_symlink():
            raise LLMToolError(label + ": broken config symlink") from None
        return {}
    except OSError:
        raise LLMToolError(label + ": cannot read config file") from None
    if len(raw) > CONFIG_LIMIT:
        raise LLMToolError(label + ": config exceeds 64 KiB")
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=StrictLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError):
        raise LLMToolError(label + ": malformed YAML") from None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LLMToolError(label + ": expected a YAML mapping")
    return value


def _fields(value, allowed, label):
    if not isinstance(value, dict):
        raise LLMToolError(label + ": expected a mapping")
    if set(value) - set(allowed):
        # Never include attacker-controlled keys, values, or profile names.
        raise LLMToolError(label + ": forbidden or unsupported field")


def _provider_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise LLMToolError("provider name must use 1-64 letters, digits, underscores or hyphens")


def _apply_user(config, fragment):
    _fields(fragment, {"providers", "generation", "active_provider"}, "user config")
    if "active_provider" in fragment:
        config["active_provider"] = fragment["active_provider"]
    if "providers" in fragment:
        profiles = fragment["providers"]
        if not isinstance(profiles, dict):
            raise LLMToolError("user config: providers must be a mapping")
        for name, fields in profiles.items():
            _provider_name(name)
            _fields(fields, PROVIDER_DEFAULTS, "user provider")
            profile = config["providers"].setdefault(name, copy.deepcopy(PROVIDER_DEFAULTS))
            # provider_options is replaced as a unit; {} explicitly opts out.
            profile.update(fields)
    if "generation" in fragment:
        _fields(fragment["generation"], DEFAULTS["generation"], "user generation")
        config["generation"].update(fragment["generation"])


def _apply_project(config, fragment):
    """Validate the entire low-trust fragment before merging any of it."""
    _fields(fragment, {"providers", "generation"}, "project config")
    profiles = fragment.get("providers", {})
    if not isinstance(profiles, dict):
        raise LLMToolError("project config: providers must be a mapping")
    for name, fields in profiles.items():
        _provider_name(name)
        if name not in config["providers"]:
            raise LLMToolError("project config cannot define a provider")
        _fields(fields, {"model", "timeout_seconds"}, "project provider")
    generation = fragment.get("generation", {})
    _fields(generation, {"temperature", "max_tokens"}, "project generation")
    for name, fields in profiles.items():
        config["providers"][name].update(fields)
    config["generation"].update(generation)


def _set(config, path, value):
    target = selected_provider(config) if path[0] == "provider" else config[path[0]]
    target[path[1]] = value


def selected_provider(config):
    name = config["active_provider"]
    _provider_name(name)
    if name not in config["providers"]:
        raise LLMToolError("active provider is not defined in user config or defaults")
    return config["providers"][name]


def _number(value, field, integer=False):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError
        if integer:
            if isinstance(value, float) or not re.fullmatch(r"[0-9]+", str(value)):
                raise ValueError
            result = int(value)
        else:
            result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise LLMToolError(field + " must be a finite " + ("integer" if integer else "number")) from None


def _timeout_seconds(value):
    result = _number(value, "timeout_seconds")
    if not 0 < result <= MAX_TIMEOUT_SECONDS:
        raise LLMToolError("timeout_seconds must be greater than 0 and at most 3600")
    return result


def _validate_provider(provider):
    implementation = client_type(provider["type"])
    implementation.validate_options(provider["provider_options"])
    implementation.validate_config(provider)
    url = provider["base_url"]
    if url is not None:
        try:
            if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
                raise ValueError
            parsed = urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
                raise ValueError
            if parsed.port is not None and parsed.port < 1:
                raise ValueError
        except ValueError:
            raise LLMToolError("base_url must be an HTTP(S) origin without credentials, path, query, or fragment") from None
        provider["base_url"] = url.rstrip("/")
    model = provider["model"]
    if model is not None and (not isinstance(model, str) or not model.strip()
                              or any(ord(c) < 32 for c in model)):
        raise LLMToolError("model must be a non-empty string without control characters")
    provider["timeout_seconds"] = _timeout_seconds(provider["timeout_seconds"])
    value = provider["api_key"]
    if value is not None and (not isinstance(value, str) or not value
                              or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise LLMToolError("api_key must be nonempty printable ASCII without whitespace or null")
    value = provider["api_key_env"]
    if value is not None and (not isinstance(value, str) or not value
                              or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise LLMToolError("api_key_env must be a non-empty printable ASCII string or null")
    if provider["api_key"] and provider["api_key_env"]:
        raise LLMToolError("choose either api_key or api_key_env, not both")
    if provider["api_key_env"] and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", provider["api_key_env"]):
        raise LLMToolError("api_key_env must be an environment variable name")


def _validate(config):
    selected_provider(config)
    for name, provider in config["providers"].items():
        _provider_name(name)
        _validate_provider(provider)
    generation = config["generation"]
    generation["max_tokens"] = _number(generation["max_tokens"], "max_tokens", integer=True)
    if generation["max_tokens"] <= 0:
        raise LLMToolError("max_tokens must be positive")
    generation["temperature"] = _number(generation["temperature"], "temperature")
    if not 0 <= generation["temperature"] <= 2:
        raise LLMToolError("temperature must be between 0 and 2")


def resolve_config(cli=None, environ=None, cwd=None, home=None, require_configured=False):
    """Resolve a viewable config; model requests must require readiness as well."""
    env = os.environ if environ is None else environ
    cli = cli or {}
    config = copy.deepcopy(DEFAULTS)
    _apply_user(config, _read_yaml(user_config_path(home), "user config"))
    _apply_project(config, _read_yaml((Path.cwd() if cwd is None else Path(cwd)) / ".llmtool.yaml", "project config"))
    # Only trusted sources may select a profile. Resolve selection before applying
    # endpoint/model overrides so credentials and overrides come from one profile.
    if "LLMTOOL_PROVIDER" in env:
        config["active_provider"] = env["LLMTOOL_PROVIDER"]
    if cli.get("provider") is not None:
        config["active_provider"] = cli["provider"]
    selected_provider(config)
    for name, path in ENV_OPTIONS.items():
        if name in env:
            _set(config, path, env[name])
    for name, value in cli.items():
        if name in CLI_OPTIONS and value is not None:
            _set(config, CLI_OPTIONS[name], value)
    _validate(config)
    if require_configured:
        provider = selected_provider(config)
        client_type(provider["type"]).validate_ready(provider)
    return config


def resolve_profile_config(profile_name, *, home=None, require_configured=True):
    """Resolve an explicit persisted profile without project/runtime overrides.

    Credential values are deliberately not read here; api_key() reads the
    configured credential environment variable only after readiness succeeds.
    """
    _provider_name(profile_name)
    config = copy.deepcopy(DEFAULTS)
    saved_user = _read_yaml(user_config_path(home), "user config")
    _apply_user(config, saved_user)
    if profile_name not in saved_user.get("providers", {}):
        raise LLMToolError("active provider is not defined")
    config["active_provider"] = profile_name
    _validate(config)
    provider = selected_provider(config)
    if require_configured:
        client_type(provider["type"]).validate_ready(provider)
    return config


def api_key(config, environ=None):
    env = os.environ if environ is None else environ
    provider = selected_provider(config)
    value = provider["api_key"]
    if provider["api_key_env"]:
        value = env.get(provider["api_key_env"])
        if not value:
            raise coded_error("configured API key environment variable is missing or empty",
                              "AUTH_CREDENTIAL_MISSING", "config.auth", user_action_required=True)
    if value is not None and (not isinstance(value, str) or not value
                              or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise LLMToolError("API key must contain only non-whitespace printable ASCII characters")
    return value


def show_config(config):
    result = copy.deepcopy(config)
    for provider in result["providers"].values():
        if provider["api_key"] or provider["api_key_env"]:
            provider["api_key"] = "***REDACTED***"
    return yaml_module().safe_dump(result, sort_keys=False)


def init_config(path, force=False):
    """Create privately; refuse symlinks and replace atomically only with --force."""
    save_config(path, DEFAULTS, replace=force)


def save_config(path, config, replace=False):
    """Atomically write a validated config with owner-only permissions."""
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise LLMToolError("refusing to replace a config symlink")
        if path.exists() and not replace:
            raise LLMToolError("user config already exists; use --force to replace it")
        content = yaml_module().safe_dump(config, sort_keys=False)
        descriptor, temporary = tempfile.mkstemp(prefix=".llmtool-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # Atomic no-clobber, including concurrent init.
    except FileExistsError:
        raise LLMToolError("user config already exists; use --force to replace it") from None
    except OSError:
        raise LLMToolError("cannot create user config") from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
