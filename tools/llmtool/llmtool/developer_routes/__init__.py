"""Provider-neutral execution contracts for direct CLI Developer routes."""

from .base import DeveloperRoute, RouteError, RouteResult, capture_snapshot, run_route
from .agy import MAX_PROMPT_BYTES as AGY_MAX_PROMPT_BYTES, AgyRoute, owner_assignment_gate
from .write_boundary import (
    SandboxExecBoundary,
    UnsupportedBoundary,
    WriteBoundary,
    for_platform,
)

__all__ = [
    'DeveloperRoute', 'RouteError', 'RouteResult', 'capture_snapshot', 'run_route',
    'AGY_MAX_PROMPT_BYTES', 'AgyRoute', 'owner_assignment_gate', 'load_route_config',
    'SandboxExecBoundary', 'UnsupportedBoundary', 'WriteBoundary',
    'for_platform',
]


def load_route_config(path=None):
    """Load and strictly validate the provider-neutral route configuration."""
    import json
    import re
    from pathlib import Path

    if path is None:
        path = Path(__file__).resolve().parents[2] / 'developer-routes.json'
    try:
        raw = Path(path).read_text(encoding='utf-8')
        value = json.loads(raw, object_pairs_hook=_unique_config_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise RouteError('ROUTE_CONFIG_INVALID') from None
    if (type(value) is not dict or set(value) != {'schema_version', 'routes'} or
            type(value.get('schema_version')) is not int or value['schema_version'] != 1 or
            type(value.get('routes')) is not dict):
        raise RouteError('ROUTE_CONFIG_INVALID')
    routes = value['routes']
    if set(routes) != {'agy'} or type(routes['agy']) is not dict:
        raise RouteError('ROUTE_CONFIG_INVALID')
    entry = routes['agy']
    expected = {'executable', 'default_model', 'default_effort', 'denied_dirs', 'state_dirs'}
    if set(entry) != expected:
        raise RouteError('ROUTE_CONFIG_INVALID')
    if (entry['executable'] != 'agy' or type(entry['default_model']) is not str or
            '://' in entry['default_model'] or
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', entry['default_model']) is None or
            entry['default_effort'] not in ('minimal', 'low', 'medium', 'high') or
            type(entry['denied_dirs']) is not list or entry['denied_dirs'] != ['.gemini'] or
            entry['state_dirs'] != {'policy': 'private-temp-only'}):
        raise RouteError('ROUTE_CONFIG_INVALID')
    return {'schema_version': 1, 'routes': {'agy': dict(entry)}}


def _unique_config_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate config key')
        result[key] = value
    return result
