"""Strict, layered YAML configuration. No implicit credential discovery."""

import copy
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .errors import LLMToolError

DEFAULTS = {
    "providers": {
        "company": {
            "type": "openai-compatible",
            "base_url": "http://10.29.7.58:4000",
            "model": "gw",
            "timeout_seconds": 120,
            "api_key": None,
            "api_key_env": None,
        }
    },
    "generation": {"temperature": 0, "max_tokens": 2000, "enable_thinking": False},
}
ENV_OPTIONS = {
    "LLMTOOL_BASE_URL": ("providers", "company", "base_url"),
    "LLMTOOL_MODEL": ("providers", "company", "model"),
    "LLMTOOL_TIMEOUT": ("providers", "company", "timeout_seconds"),
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


def _merge(target, fragment, schema=DEFAULTS):
    for key, value in fragment.items():
        if key not in schema:
            raise LLMToolError("config contains an unsupported field")
        if isinstance(schema[key], dict):
            if not isinstance(value, dict):
                raise LLMToolError("config section must be a mapping")
            _merge(target[key], value, schema[key])
        else:
            target[key] = value


def _set(config, path, value):
    target = config
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


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


def _validate(config):
    provider = config["providers"]["company"]
    generation = config["generation"]
    if provider["type"] != "openai-compatible":
        raise LLMToolError("A1 supports only provider type openai-compatible")
    url = provider["base_url"]
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
    if not isinstance(model, str) or not model.strip() or any(ord(c) < 32 for c in model):
        raise LLMToolError("model must be a non-empty string without control characters")
    provider["timeout_seconds"] = _number(provider["timeout_seconds"], "timeout_seconds")
    if provider["timeout_seconds"] <= 0:
        raise LLMToolError("timeout_seconds must be positive")
    generation["max_tokens"] = _number(generation["max_tokens"], "max_tokens", integer=True)
    if generation["max_tokens"] <= 0:
        raise LLMToolError("max_tokens must be positive")
    generation["temperature"] = _number(generation["temperature"], "temperature")
    if not 0 <= generation["temperature"] <= 2:
        raise LLMToolError("temperature must be between 0 and 2")
    if generation["enable_thinking"] is not False:
        raise LLMToolError("A1 requires enable_thinking: false")
    for field in ("api_key", "api_key_env"):
        value = provider[field]
        if value is not None and (not isinstance(value, str) or not value
                                  or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise LLMToolError(field + " must be a non-empty printable ASCII string or null")
    if provider["api_key"] and provider["api_key_env"]:
        raise LLMToolError("choose either api_key or api_key_env, not both")
    if provider["api_key_env"] and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", provider["api_key_env"]):
        raise LLMToolError("api_key_env must be an environment variable name")


def resolve_config(cli=None, environ=None, cwd=None, home=None):
    env = os.environ if environ is None else environ
    config = copy.deepcopy(DEFAULTS)
    _merge(config, _read_yaml(user_config_path(home), "user config"))
    _merge(config, _read_yaml((Path.cwd() if cwd is None else Path(cwd)) / ".llmtool.yaml", "project config"))
    for name, path in ENV_OPTIONS.items():
        if name in env:
            _set(config, path, env[name])
    for name, value in (cli or {}).items():
        if name in CLI_OPTIONS and value is not None:
            _set(config, CLI_OPTIONS[name], value)
    _validate(config)
    return config


def api_key(config, environ=None):
    env = os.environ if environ is None else environ
    provider = config["providers"]["company"]
    value = provider["api_key"]
    if provider["api_key_env"]:
        value = env.get(provider["api_key_env"])
        if not value:
            raise LLMToolError("configured API key environment variable is missing or empty")
    if value and any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise LLMToolError("API key must contain only non-whitespace printable ASCII characters")
    return value


def show_config(config):
    result = copy.deepcopy(config)
    provider = result["providers"]["company"]
    if provider["api_key"] or provider["api_key_env"]:
        provider["api_key"] = "***REDACTED***"
    return yaml_module().safe_dump(result, sort_keys=False)


def init_config(path, force=False):
    """Create privately; refuse symlinks and replace atomically only with --force."""
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise LLMToolError("refusing to replace a config symlink")
        if path.exists() and not force:
            raise LLMToolError("user config already exists; use --force to replace it")
        content = yaml_module().safe_dump(DEFAULTS, sort_keys=False)
        descriptor, temporary = tempfile.mkstemp(prefix=".llmtool-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        if force:
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
