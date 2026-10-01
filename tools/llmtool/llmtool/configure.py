"""Interactive user-config setup. Never performs provider/network operations."""

import copy
import getpass
import warnings
from pathlib import Path

from .config import (
    DEFAULTS, _apply_user, _provider_name, _read_yaml, _timeout_seconds, _validate,
    save_config, selected_provider, user_config_path,
)
from .errors import LLMToolError
from .providers import client_type


def _answer(input_fn, label, default=None, optional=False):
    suffix = " [" + str(default) + "]" if default is not None else ""
    try:
        value = input_fn(label + suffix + ": ").strip()
    except (EOFError, OSError, RuntimeError, ValueError):
        raise LLMToolError("configuration input ended; configuration was not saved") from None
    if not value:
        return default if default is not None else (None if optional else "")
    if optional and value == "-":
        return None
    return value


def _integer(value, field):
    try:
        if not isinstance(value, str) or not value.isdecimal():
            raise ValueError
        return int(value)
    except (ValueError, OverflowError):
        raise LLMToolError(field + " must be a positive integer") from None


def _number(value, field):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        raise LLMToolError(field + " must be a number") from None


def _boolean_or_none(value):
    if value is None:
        return None
    lowered = value.casefold()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    raise LLMToolError("enable_thinking must be true, false, or blank")


def _choice_or_none(value, field, choices):
    if value is None:
        return None
    if value not in choices:
        raise LLMToolError(field + " must be one of the supported choices or blank")
    return value


def run_configure(home=None, input_fn=input, secret_fn=None, stdout=None, stderr=None):
    """Prompt for a profile and atomically save validated settings under HOME."""
    import sys

    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    secret_fn = getpass.getpass if secret_fn is None else secret_fn
    path = user_config_path(home)
    exists = path.exists() or path.is_symlink()
    if exists:
        try:
            answer = input_fn("User config exists. Update it? [y/N]: ").strip().casefold()
        except (EOFError, OSError, RuntimeError, ValueError):
            raise LLMToolError("configuration input ended; configuration was not saved") from None
        if answer not in ("y", "yes"):
            print("Configuration unchanged.", file=stdout)
            return False

    config = copy.deepcopy(DEFAULTS)
    saved_user = {}
    if exists:
        saved_user = _read_yaml(path, "user config")
        _apply_user(config, saved_user)
        if ("providers" in saved_user and "default" not in saved_user["providers"]
                and config["active_provider"] != "default"):
            config["providers"].pop("default", None)
        _validate(config)
    provider_name = _answer(input_fn, "Provider name", config["active_provider"])
    _provider_name(provider_name)
    previous = config["providers"].get(provider_name, {})
    provider_type = _answer(input_fn, "Provider type", previous.get("type", "openai-compatible"))
    client_type(provider_type)
    if previous.get("type") != provider_type:
        previous = {}  # An explicit transport change discards incompatible settings.
    is_openai = provider_type == "openai"
    is_cli = provider_type in ("claude-cli", "codex-cli")
    cli_label = "Codex CLI" if provider_type == "codex-cli" else "Claude CLI"
    base_url = None if is_openai or is_cli else _answer(input_fn, "Base URL (- to unset)", previous.get("base_url"), optional=True)
    model = _answer(input_fn, "Model (- to unset)", previous.get("model"), optional=True)
    timeout = _timeout_seconds(_number(_answer(input_fn, "Timeout seconds", str(previous.get("timeout_seconds", 120))), "timeout_seconds"))

    literal_key = None
    api_key_env = None
    if not is_cli:
        # getpass may fall back to an echoing stdin on systems without a TTY.
        # Treat warnings as errors so a credential is never read in that mode.
        if not getattr(sys.stdin, "isatty", lambda: False)():
            raise LLMToolError("configure requires an interactive terminal for hidden API key input")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                literal_key = secret_fn("API key (blank preserves existing; - unsets): ", stream=stderr)
        except (getpass.GetPassWarning, EOFError, OSError, RuntimeError, ValueError):
            raise LLMToolError("cannot read API key without echo; configuration was not saved") from None
        entered_key = literal_key
        literal_key = previous.get("api_key") if entered_key == "" else (None if entered_key == "-" else entered_key)
        api_key_env_default = previous.get("api_key_env") if not entered_key else None
        if not previous and is_openai and not literal_key:
            api_key_env_default = "OPENAI_API_KEY"
        api_key_env = _answer(input_fn, "API key env (- to unset)", api_key_env_default, optional=True)
        if api_key_env and entered_key == "":
            literal_key = None
        if literal_key and api_key_env:
            raise LLMToolError("choose either API key or API key env, not both")

    if is_openai:
        effort_raw = _answer(input_fn, "OpenAI reasoning_effort (none/low/medium/high; - to unset)", previous.get("provider_options", {}).get("reasoning_effort"), optional=True)
        effort = _choice_or_none(effort_raw, "reasoning_effort", ("none", "low", "medium", "high"))
        provider_options = dict(previous.get("provider_options", {}))
        if effort is not None or "reasoning_effort" in provider_options:
            provider_options["reasoning_effort"] = effort
    elif is_cli:
        provider_options = {}
    else:
        old_thinking = previous.get("provider_options", {}).get("enable_thinking")
        thinking_raw = _answer(input_fn, "Provider-specific enable_thinking (true/false; - to unset)", str(old_thinking).lower() if old_thinking is not None else None, optional=True)
        thinking = _boolean_or_none(thinking_raw)
        provider_options = dict(previous.get("provider_options", {}))
        if thinking is not None or "enable_thinking" in provider_options:
            provider_options["enable_thinking"] = thinking

    if is_openai:
        temperature_prompt = "Temperature (stored, not sent by OpenAI)"
    elif is_cli:
        temperature_prompt = "Temperature (stored, not sent by " + cli_label + ")"
    else:
        temperature_prompt = "Temperature"
    temperature = _number(_answer(input_fn, temperature_prompt, str(config["generation"]["temperature"])), "temperature")
    max_label = "Max tokens (stored, not enforced by " + cli_label + ")" if is_cli else "Max tokens"
    max_tokens = _integer(_answer(input_fn, max_label, str(config["generation"]["max_tokens"])), "max_tokens")

    active_provider = _answer(input_fn, "Active provider", config["active_provider"] if exists else provider_name)

    # Load only the user config. Project config and environment are low/trusted
    # runtime layers and must never be baked into a saved user config.
    config["providers"][provider_name] = {
        "type": provider_type,
        "base_url": base_url,
        "model": model,
        "timeout_seconds": timeout,
        "api_key": literal_key,
        "api_key_env": api_key_env,
        "provider_options": provider_options,
    }
    config["active_provider"] = active_provider
    config["generation"] = {"temperature": temperature, "max_tokens": max_tokens}
    _validate(config)
    provider = selected_provider(config)
    # Validation uses a fully expanded view; persistence preserves untouched
    # profiles and generation mappings, including intentionally omitted defaults.
    persisted = copy.deepcopy(config)
    for name, fields in saved_user.get("providers", {}).items():
        if name != provider_name:
            persisted["providers"][name] = copy.deepcopy(fields)
    old_generation = copy.deepcopy(DEFAULTS["generation"])
    old_generation.update(saved_user.get("generation", {}))
    if "generation" in saved_user and config["generation"] == old_generation:
        persisted["generation"] = copy.deepcopy(saved_user["generation"])
    save_config(path, persisted, replace=exists)

    print("Configuration saved: " + str(path), file=stdout)
    print("Active provider: " + active_provider, file=stdout)
    print("Type: " + provider["type"], file=stdout)
    if provider["type"] == "openai-compatible":
        print("Base URL: " + (provider["base_url"] or "<not configured>"), file=stdout)
    print("Model: " + (provider["model"] or "<not configured>"), file=stdout)
    return True
