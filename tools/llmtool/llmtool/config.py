"""Strict, layered YAML configuration. No implicit credential discovery."""

import copy
import math
import os
import re
import stat
import sys
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
CODEX_DIRECT_DEFAULT_TIMEOUT_SECONDS = 600
CODEX_DIRECT_MAX_TIMEOUT_SECONDS = 1800
REPOSITORY_ERROR = "user repository: invalid configuration"
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
# Doctor uses these for configuration/location/executable lookup. A credential
# binding to one of them makes inspection ambiguous; ordinary resolution keeps
# its existing contract. User-config location bootstrap precedes known bindings.
INSPECTION_ENV_NAMES = frozenset(ENV_OPTIONS) | {
    'LLMTOOL_PROVIDER', 'LLMTOOL_HOME', 'XDG_STATE_HOME', 'LOCALAPPDATA',
    'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'PATH', 'PATHEXT',
    'NoDefaultCurrentDirectoryInExePath',
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


def _validate_repository(value, defaults=None, *, allow_partial=False):
    defaults = {} if defaults is None else defaults
    if type(value) is not dict or set(value) - {
            "slug", "dispatch_issue", "dispatch_evidence_pr"}:
        raise LLMToolError(REPOSITORY_ERROR)
    result = {**defaults, **value}
    if not allow_partial and set(result) != {"slug", "dispatch_issue", "dispatch_evidence_pr"}:
        raise LLMToolError(REPOSITORY_ERROR)
    slug = result.get("slug")
    issue = result.get("dispatch_issue")
    evidence_pr = result.get("dispatch_evidence_pr")
    if (("slug" in result and (type(slug) is not str or
             re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}",
                          slug) is None or slug.split("/", 1)[1] in (".", ".."))) or
            ("dispatch_issue" in result and
             (type(issue) is not int or not 1 <= issue <= 10**9)) or
            ("dispatch_evidence_pr" in result and
             (type(evidence_pr) is not int or not 1 <= evidence_pr <= 10**9))):
        raise LLMToolError(REPOSITORY_ERROR)
    return result


class RepositoryConfigError(LLMToolError):
    """Sanitized repository trust/configuration rejection."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def legacy_repository_config(home=None):
    """Return pre-trust repository defaults for trust verify-legacy only."""
    defaults = {
        "slug": "davidcatcat/davidcattoolsproject",
        "dispatch_issue": 289,
        "dispatch_evidence_pr": 283,
    }
    path = user_config_path(home)
    try:
        user = _read_yaml(path, "user config") if os.path.lexists(path) else {}
        if "repository" not in user:
            return dict(defaults)
        return _validate_repository(user["repository"], defaults)
    except LLMToolError:
        raise


def repository_config(home=None):
    """Return repository identity from owner trust, never from config.yaml."""
    from .trust import TrustError, load_trust
    path = user_config_path(home)
    try:
        user = _read_yaml(path, "user config") if os.path.lexists(path) else {}
        if "repository" in user:
            raise RepositoryConfigError("TRUST_LEGACY_REPOSITORY_CONFIG")
        trusted = load_trust(home=home)
        repository = trusted.repository
        approval_refs = repository["approval_refs"]
        return {
            "slug": repository["slug"],
            "dispatch_issue": approval_refs["worker_dispatch_issue"],
            "dispatch_evidence_pr": approval_refs["worker_dispatch_evidence_pr"],
        }
    except RepositoryConfigError:
        raise
    except TrustError as error:
        raise RepositoryConfigError(error.code) from None
    except LLMToolError:
        raise RepositoryConfigError("TRUST_REPOSITORY_CONFIG_INVALID") from None
    except (KeyError, TypeError, AttributeError):
        raise RepositoryConfigError("TRUST_REPOSITORY_CONFIG_INVALID") from None


def _apply_user(config, fragment):
    _fields(fragment, {"providers", "generation", "active_provider", "diagnostics", "codex_direct",
                       "repository", "pm_runtime", "accounts", "roles"}, "user config")
    if "pm_runtime" in fragment:
        _validate_pm_runtime(fragment["pm_runtime"])
    if "accounts" in fragment or "roles" in fragment:
        from .role_config import validate_role_config
        # Validate schema at config-load time, but defer account-directory
        # metadata checks until a role actually selects an account.
        validate_role_config(fragment)
    if "repository" in fragment:
        _validate_repository(fragment["repository"], allow_partial=True)
    if "diagnostics" in fragment:
        from .diagnostics_settings import parse_diagnostics_settings
        parse_diagnostics_settings(fragment["diagnostics"])
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
    if "codex_direct" in fragment:
        _fields(fragment["codex_direct"], {"timeout_seconds"}, "user codex_direct")
        config["codex_direct"] = {
            "timeout_seconds": CODEX_DIRECT_DEFAULT_TIMEOUT_SECONDS,
            **fragment["codex_direct"],
        }


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


def validate_codex_direct_timeout(value):
    """Validate the configured direct Codex wall clock limit."""
    if type(value) is not int or not 1 <= value <= CODEX_DIRECT_MAX_TIMEOUT_SECONDS:
        raise LLMToolError("codex_direct.timeout_seconds must be an integer from 1 through 1800")
    return value


def validate_pm_claude_config_dir(value):
    """Validate the trusted Claude Code account directory for the PM process."""
    if (type(value) is not str or not 1 <= len(value) <= 1024 or not os.path.isabs(value) or
            any(ord(char) < 0x20 or ord(char) == 0x7f for char in value)):
        raise LLMToolError("pm_runtime.claude_config_dir must be an absolute path")
    return value


def _validate_pm_runtime(fragment):
    _fields(fragment, {"claude_config_dir"}, "user pm_runtime")
    if "claude_config_dir" in fragment:
        validate_pm_claude_config_dir(fragment["claude_config_dir"])


def _validate_provider(provider, profile_name):
    implementation = client_type(provider["type"])
    options = provider["provider_options"]
    if profile_name == "opencode-developer" and isinstance(options, dict) and "steps" in options:
        # OpenCode's direct Developer route consumes this profile-only setting;
        # it is not an option understood by the configured provider client.
        options = {key: value for key, value in options.items() if key != "steps"}
    implementation.validate_options(options)
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
        _validate_provider(provider, name)
    generation = config["generation"]
    generation["max_tokens"] = _number(generation["max_tokens"], "max_tokens", integer=True)
    if generation["max_tokens"] <= 0:
        raise LLMToolError("max_tokens must be positive")
    generation["temperature"] = _number(generation["temperature"], "temperature")
    if not 0 <= generation["temperature"] <= 2:
        raise LLMToolError("temperature must be between 0 and 2")
    direct_config = config.get("codex_direct")
    if direct_config is not None:
        validate_codex_direct_timeout(direct_config["timeout_seconds"])


def _inspection_env_name(name):
    """Windows environment names are case-insensitive; POSIX names are exact."""
    return name.upper() if sys.platform == 'win32' else name


def _config_view(cli=None, environ=None, cwd=None, home=None, *, inspection=False):
    """Strict layered validation only; never look up a credential value."""
    env = os.environ if environ is None else environ
    cli = cli or {}
    config = copy.deepcopy(DEFAULTS)
    user = _read_yaml(user_config_path(home), "user config")
    _apply_user(config, user)
    _apply_project(config, _read_yaml((Path.cwd() if cwd is None else Path(cwd)) / ".llmtool.yaml", "project config"))
    if inspection:
        reserved = {_inspection_env_name(name) for name in INSPECTION_ENV_NAMES}
        for provider in config['providers'].values():
            name = provider['api_key_env']
            if isinstance(name, str) and _inspection_env_name(name) in reserved:
                raise LLMToolError('offline inspection credential binding conflicts with configuration environment')
    # Only trusted sources may select a profile. Resolve selection before applying
    # endpoint/model overrides so credentials and overrides come from one profile.
    if "LLMTOOL_PROVIDER" in env:
        config["active_provider"] = env["LLMTOOL_PROVIDER"]
    if cli.get("provider") is not None:
        config["active_provider"] = cli["provider"]
    if cli.get('provider') is not None or 'LLMTOOL_PROVIDER' in env:
        _provider_name(config['active_provider'])
        if config['active_provider'] not in config['providers']:
            raise LLMToolError('requested provider profile is not defined')
    selected_provider(config)
    for name, path in ENV_OPTIONS.items():
        if name in env:
            _set(config, path, env[name])
    for name, value in cli.items():
        if name in CLI_OPTIONS and value is not None:
            _set(config, CLI_OPTIONS[name], value)
    _validate(config)
    return config, user


def resolve_config(cli=None, environ=None, cwd=None, home=None, require_configured=False):
    """Resolve a viewable config; model requests must require readiness as well."""
    config, _ = _config_view(cli, environ, cwd, home)
    config.pop("codex_direct", None)
    if require_configured:
        provider = selected_provider(config)
        client_type(provider["type"]).validate_ready(provider)
    return config


def codex_direct_timeout(cwd=None, home=None):
    """Return the configured direct Codex timeout without changing config output."""
    user = _read_yaml(user_config_path(home), "user config")
    direct_config = user.get("codex_direct", {})
    _fields(direct_config, {"timeout_seconds"}, "user codex_direct")
    return validate_codex_direct_timeout(
        direct_config.get("timeout_seconds", CODEX_DIRECT_DEFAULT_TIMEOUT_SECONDS))


def pm_claude_config_dir(home=None):
    """Return the user-configured PM Claude account directory, or None when unset.

    Read from trusted user config only; project config cannot set it.
    """
    path = user_config_path(home)
    user = _read_yaml(path, "user config") if os.path.lexists(path) else {}
    fragment = user.get("pm_runtime", {})
    _validate_pm_runtime(fragment)
    value = fragment.get("claude_config_dir")
    return None if value is None else validate_pm_claude_config_dir(value)


def inspect_config(*, environ=None, cwd=None, home=None):
    """Offline projection: configuration readiness and credential presence only.

    Preserve normal layering and validation, without calling the model-request
    loader, constructing a client, or resolving literal/environment credentials.
    Only membership is checked for a configured credential environment name;
    even an empty value is present and does not establish authentication.
    """
    from .diagnostics_settings import parse_diagnostics_settings
    env = os.environ if environ is None else environ
    config, user = _config_view(environ=env, cwd=cwd, home=home, inspection=True)
    provider = selected_provider(config)
    ready = True
    try:
        client_type(provider['type']).validate_ready(provider)
    except LLMToolError:
        ready = False
    if provider['api_key'] is not None:
        credential = 'literal-configured'
    elif provider['api_key_env'] is not None:
        # Mapping.__contains__ (including os._Environ) can fetch the value.
        # Iterate keys instead, so configured secret values are never accessed.
        keys = tuple(_inspection_env_name(name) for name in env)
        credential = ('env-configured-present' if _inspection_env_name(provider['api_key_env']) in keys
                      else 'env-configured-absent')
    else:
        credential = 'not-configured'
    return {'settings': parse_diagnostics_settings(user.get('diagnostics', {})),
            'provider_type': provider['type'], 'readiness': 'ready' if ready else 'not-configured',
            'credential': credential}


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
        raise LLMToolError("requested provider profile is not defined")
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
