"""Provider-neutral execution contracts for direct CLI Developer routes."""

from .base import DeveloperRoute, RouteError, RouteResult, capture_snapshot, run_route
from .agy import AgyRoute, owner_assignment_gate
from .claude import ClaudeRoute
from .gw_agent import GwAgentRoute
from .write_boundary import (
    SandboxExecBoundary,
    UnsupportedBoundary,
    WriteBoundary,
    for_platform,
)

__all__ = [
    'DeveloperRoute', 'RouteError', 'RouteResult', 'capture_snapshot', 'run_route',
    'AgyRoute', 'ClaudeRoute', 'GwAgentRoute', 'owner_assignment_gate', 'load_route_config',
    'check_route_model',
    'SandboxExecBoundary', 'UnsupportedBoundary', 'WriteBoundary',
    'for_platform',
]

_MAX_ALLOWED_MODELS = 16
_GENERIC_MODEL_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}'
_CLAUDE_MODEL_PATTERN = r'claude-[a-z0-9][a-z0-9.-]{0,120}'


def _generic_model_ok(value):
    import re

    return (type(value) is str and '://' not in value and
            re.fullmatch(_GENERIC_MODEL_PATTERN, value) is not None)


def _claude_model_ok(value):
    import re

    return type(value) is str and re.fullmatch(_CLAUDE_MODEL_PATTERN, value) is not None


def _allowed_models_ok(value, default_model, model_ok):
    """Return True when value is exactly [default_model], a valid one-item list.

    Security repair (PR #1759 F1): until the Owner decides to allow more
    models, a route's allowed_models must list only its current default
    model. Any extra or different entry is refused at load.
    """
    return (type(value) is list and 1 <= len(value) <= _MAX_ALLOWED_MODELS and
            all(type(item) is str for item in value) and
            len(set(value)) == len(value) and
            all(model_ok(item) for item in value) and
            default_model in value and
            len(value) == 1 and type(default_model) is str and
            value[0] == default_model)


def check_route_model(config, route, model):
    """Return model when it is allowed for route in a load_route_config() result.

    Pure check: no file, network or environment reads. Raises
    RouteError('ROLE_MODEL_NOT_ALLOWED') for an absent route, a non-str model
    or a model not listed in that route's allowed_models.
    """
    routes = config.get('routes') if type(config) is dict else None
    entry = (routes.get(route) if type(routes) is dict and type(route) is str
             else None)
    allowed = entry.get('allowed_models') if type(entry) is dict else None
    if (type(model) is not str or type(allowed) is not list or
            not any(type(item) is str and item == model for item in allowed)):
        raise RouteError('ROLE_MODEL_NOT_ALLOWED')
    return model


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
    if ('agy' not in routes or set(routes) - {'agy', 'claude', 'gw-agent'} or
            type(routes['agy']) is not dict or
            ('claude' in routes and type(routes['claude']) is not dict) or
            ('gw-agent' in routes and type(routes['gw-agent']) is not dict)):
        raise RouteError('ROUTE_CONFIG_INVALID')
    entry = routes['agy']
    expected = {'executable', 'default_model', 'allowed_models', 'default_effort',
                'denied_dirs', 'state_dirs'}
    if set(entry) != expected:
        raise RouteError('ROUTE_CONFIG_INVALID')
    if (entry['executable'] != 'agy' or type(entry['default_model']) is not str or
            '://' in entry['default_model'] or
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', entry['default_model']) is None or
            entry['default_effort'] not in ('minimal', 'low', 'medium', 'high') or
            type(entry['denied_dirs']) is not list or entry['denied_dirs'] != ['.gemini'] or
            entry['state_dirs'] != {'policy': 'private-temp-only'} or
            not _allowed_models_ok(entry['allowed_models'], entry['default_model'],
                                   _generic_model_ok)):
        raise RouteError('ROUTE_CONFIG_INVALID')
    normalized = {'agy': dict(entry)}
    normalized['agy']['allowed_models'] = list(entry['allowed_models'])
    if 'claude' in routes:
        claude = routes['claude']
        expected_claude = {'executable', 'default_model', 'allowed_models', 'default_effort',
                           'denied_dirs', 'state_dirs'}
        if (set(claude) != expected_claude or claude.get('executable') != 'claude' or
                type(claude.get('default_model')) is not str or
                re.fullmatch(r'claude-[a-z0-9][a-z0-9.-]{0,120}',
                             claude['default_model']) is None or
                type(claude.get('default_effort')) is not str or
                claude['default_effort'] not in ('low', 'medium', 'high', 'xhigh', 'max') or
                type(claude.get('denied_dirs')) is not list or
                claude['denied_dirs'] != ['.claude'] or
                claude.get('state_dirs') != {'policy': 'private-temp-only'} or
                not _allowed_models_ok(claude.get('allowed_models'), claude['default_model'],
                                       _claude_model_ok)):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized['claude'] = dict(claude)
        normalized['claude']['allowed_models'] = list(claude['allowed_models'])
    if 'gw-agent' in routes:
        gw = routes['gw-agent']
        expected_gw = {'package_path', 'default_model', 'allowed_models', 'default_effort',
                       'profile_name', 'profile', 'authorization_ref',
                       'allowed_data_classes'}
        if (set(gw) != expected_gw or
                type(gw.get('package_path')) is not str or
                not (gw['package_path'].startswith('/') or
                     gw['package_path'].startswith('~/')) or
                type(gw.get('default_model')) is not str or
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', gw['default_model']) is None or
                gw.get('default_effort') not in ('minimal', 'low', 'medium', 'high') or
                type(gw.get('profile_name')) is not str or not gw['profile_name'] or
                type(gw.get('profile')) is not dict or
                'auth_env' not in gw['profile'] or
                gw['profile'].get('auth_env') not in (None, 'GW_AGENT_API_KEY') or
                gw['profile'].get('model') != gw['default_model'] or
                type(gw.get('allowed_data_classes')) is not list or
                not gw['allowed_data_classes'] or
                any(type(item) is not str or not item for item in gw['allowed_data_classes']) or
                len(set(gw['allowed_data_classes'])) != len(gw['allowed_data_classes']) or
                type(gw.get('authorization_ref')) is not str or
                not gw['authorization_ref'].strip() or
                not _allowed_models_ok(gw.get('allowed_models'), gw['default_model'],
                                       _generic_model_ok) or
                gw['profile'].get('model') not in gw['allowed_models']):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized['gw-agent'] = dict(gw)
        normalized['gw-agent']['allowed_models'] = list(gw['allowed_models'])
    return {'schema_version': 1, 'routes': normalized}


def _unique_config_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate config key')
        result[key] = value
    return result
