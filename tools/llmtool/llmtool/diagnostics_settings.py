"""Trusted user settings and pure location resolution; never create a store."""

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath

from .errors import LLMToolError
from .terminal import contains_unsafe_controls

MIB = 1024 * 1024
SETTINGS_BOUNDS = {
    'max_runs': (1, 1000),
    'max_total_bytes': (MIB, 512 * MIB),
    'max_age_days': (1, 365),
    'max_run_bytes': (64 * 1024, 8 * MIB),
}


@dataclass(frozen=True)
class DiagnosticsSettings:
    persist_metadata: bool = False
    max_runs: int = 100
    max_total_bytes: int = 64 * MIB
    max_age_days: int = 14
    max_run_bytes: int = MIB

    def __post_init__(self):
        if type(self.persist_metadata) is not bool:
            raise ValueError('diagnostics persistence requires a boolean')
        for key, (low, high) in SETTINGS_BOUNDS.items():
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('diagnostics limit outside supported range')
        if self.max_run_bytes > self.max_total_bytes:
            raise ValueError('diagnostics run limit exceeds total limit')


def parse_diagnostics_settings(value):
    if type(value) is not dict or set(value) - {'persist_metadata', *SETTINGS_BOUNDS}:
        raise LLMToolError('user diagnostics: forbidden or unsupported field')
    try:
        return DiagnosticsSettings(**value)
    except (TypeError, ValueError):
        raise LLMToolError('user diagnostics: invalid settings') from None


def resolve_diagnostics_settings(*, home=None, persist_metadata=None):
    """Explicit bool > trusted user mapping > false. No project/env overrides."""
    from .config import _read_yaml, user_config_path
    user = _read_yaml(user_config_path(home), 'user config')
    settings = parse_diagnostics_settings(user.get('diagnostics', {}))
    if persist_metadata is not None:
        if type(persist_metadata) is not bool:
            raise LLMToolError('diagnostics persistence option requires a boolean')
        settings = replace(settings, persist_metadata=persist_metadata)
    return settings


def _absolute(value, windows=False):
    if type(value) is not str or not value or len(value) > 4096:
        raise ValueError('invalid diagnostics home')
    value.encode('utf-8', errors='strict')
    if contains_unsafe_controls(value) or any(ord(c) < 32 for c in value):
        raise ValueError('invalid diagnostics home')
    path = PureWindowsPath(value) if windows else Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('invalid diagnostics home')
    return path


def resolve_diagnostics_home(*, environ=None, platform=None, home=None):
    """Location only. No expansion/interpolation, I/O, or persistence selection."""
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    windows = system == 'win32'
    try:
        if 'LLMTOOL_HOME' in env:
            return _absolute(env['LLMTOOL_HOME'], windows)
        if windows:
            return _absolute(env.get('LOCALAPPDATA', ''), True) / 'llmtool'
        base = _absolute(str(Path.home() if home is None else home))
        if system == 'darwin':
            return base / 'Library/Application Support/llmtool'
        if system.startswith('linux'):
            try:
                state = _absolute(env.get('XDG_STATE_HOME', ''))
            except (ValueError, UnicodeError):
                state = base / '.local/state'
            return state / 'llmtool'
        raise ValueError('unsupported diagnostics home platform')
    except (ValueError, UnicodeError):
        raise LLMToolError('diagnostics home requires a safe absolute location') from None
