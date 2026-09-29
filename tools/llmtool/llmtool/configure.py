"""Interactive user-config setup. Never performs provider/network operations."""

import copy
import getpass
import warnings
from pathlib import Path

from .config import (
    DEFAULTS, _apply_user, _read_yaml, _validate, save_config, selected_provider,
    user_config_path,
)
from .errors import LLMToolError


def _answer(input_fn, label, default=None, optional=False):
    suffix = " [" + str(default) + "]" if default is not None else ""
    value = input_fn(label + suffix + ": ").strip()
    if not value:
        return default if default is not None else (None if optional else "")
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


def run_configure(home=None, input_fn=input, secret_fn=None, stdout=None, stderr=None):
    """Prompt for a profile and atomically save validated settings under HOME."""
    import sys

    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    secret_fn = getpass.getpass if secret_fn is None else secret_fn
    path = user_config_path(home)
    exists = path.exists() or path.is_symlink()
    if exists:
        answer = input_fn("User config exists. Update it? [y/N]: ").strip().casefold()
        if answer not in ("y", "yes"):
            print("Configuration unchanged.", file=stdout)
            return False

    provider_name = _answer(input_fn, "Provider name", "default")
    provider_type = _answer(input_fn, "Provider type", "openai-compatible")
    base_url = _answer(input_fn, "Base URL", optional=True)
    model = _answer(input_fn, "Model", optional=True)
    timeout = _number(_answer(input_fn, "Timeout seconds", "120"), "timeout_seconds")

    # getpass may fall back to an echoing stdin on systems without a TTY.
    # Treat warnings as errors so a credential is never read in that mode.
    if not getattr(sys.stdin, "isatty", lambda: False)():
        raise LLMToolError("configure requires an interactive terminal for hidden API key input")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            literal_key = secret_fn("API key (leave blank to unset): ", stream=stderr)
    except (getpass.GetPassWarning, OSError):
        raise LLMToolError("cannot read API key without echo; configuration was not saved") from None
    if literal_key == "":
        literal_key = None
    api_key_env = _answer(input_fn, "API key env", optional=True)
    if literal_key and api_key_env:
        raise LLMToolError("choose either API key or API key env, not both")

    thinking_raw = _answer(input_fn, "Provider-specific enable_thinking (true/false; blank to unset)", optional=True)
    thinking = _boolean_or_none(thinking_raw)
    temperature = _number(_answer(input_fn, "Temperature", "0"), "temperature")
    max_tokens = _integer(_answer(input_fn, "Max tokens", "2000"), "max_tokens")

    active_provider = _answer(input_fn, "Active provider", provider_name)

    # Load only the user config. Project config and environment are low/trusted
    # runtime layers and must never be baked into a saved user config.
    config = copy.deepcopy(DEFAULTS)
    if exists:
        _apply_user(config, _read_yaml(path, "user config"))
    provider_options = {} if thinking is None else {"enable_thinking": thinking}
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
    save_config(path, config, replace=exists)

    print("Configuration saved: " + str(path), file=stdout)
    print("Active provider: " + active_provider, file=stdout)
    print("Type: " + provider["type"], file=stdout)
    print("Base URL: " + (provider["base_url"] or "<not configured>"), file=stdout)
    print("Model: " + (provider["model"] or "<not configured>"), file=stdout)
    return True
