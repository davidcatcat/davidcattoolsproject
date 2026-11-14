"""Provider-neutral execution contracts for direct CLI Developer routes."""

from .base import (
    DEFAULT_TIMEOUT_SECONDS, HARD_TIMEOUT_CAP_SECONDS, TIMEOUT_KEYS as _TIMEOUT_KEYS,
    DeveloperRoute, RouteError, RouteResult, capture_snapshot, resolve_route_timeout,
    run_route, timeout_limits_ok as _timeout_limits_ok,
)
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
    'check_route_model', 'check_registry_role_model', 'REGISTRY_ROLES',
    'resolve_route_timeout', 'DEFAULT_TIMEOUT_SECONDS', 'HARD_TIMEOUT_CAP_SECONDS',
    'SandboxExecBoundary', 'UnsupportedBoundary', 'WriteBoundary',
    'for_platform',
]

_MAX_ALLOWED_MODELS = 16
_GENERIC_MODEL_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}'
_CLAUDE_MODEL_PATTERN = r'claude-[a-z0-9][a-z0-9.-]{0,120}'
# Only OpenCode-hosted free models; the variant (for example high) is the
# route's default_effort, never part of the model name.
_OPENCODE_MODEL_PATTERN = r'opencode/[A-Za-z0-9][A-Za-z0-9._-]{0,94}-free'


def _generic_model_ok(value):
    import re

    return (type(value) is str and '://' not in value and
            re.fullmatch(_GENERIC_MODEL_PATTERN, value) is not None)


def _claude_model_ok(value):
    import re

    return type(value) is str and re.fullmatch(_CLAUDE_MODEL_PATTERN, value) is not None


def _opencode_model_ok(value):
    import re

    return type(value) is str and re.fullmatch(_OPENCODE_MODEL_PATTERN, value) is not None


# Security repair (PR #1782 CWE-693): each review/debug role accepts only
# identifiers of its own model family, checked by allowlist on the full
# identifier. Provider prefixes are part of the full identifier and are never
# stripped, so a prefixed form (for example 'vendor/<model>') never matches a
# family pattern. Only lower case is accepted.
_CODEX_FAMILY_PATTERN = r'(?:gpt|codex)-[a-z0-9][a-z0-9.-]{0,120}'
_CLAUDE_FAMILY_PATTERN = _CLAUDE_MODEL_PATTERN
# Family names that must never appear anywhere in another family's identifier
# (defence in depth on top of the allowlist).
_CODEX_FAMILY_MARKERS = ('gpt', 'codex', 'openai')
_CLAUDE_FAMILY_MARKERS = ('claude', 'anthropic')


def _family_model_ok(value, pattern, foreign_markers):
    import re

    if type(value) is not str or re.fullmatch(pattern, value) is None:
        return False
    folded = value.casefold()
    return not any(marker in folded for marker in foreign_markers)


def _codex_role_model_ok(value):
    """A Codex role model: a Codex/GPT family identifier, never another family."""
    return _family_model_ok(value, _CODEX_FAMILY_PATTERN, _CLAUDE_FAMILY_MARKERS)


def _claude_role_model_ok(value):
    """A Claude role model: a Claude family identifier, never another family."""
    return _family_model_ok(value, _CLAUDE_FAMILY_PATTERN, _CODEX_FAMILY_MARKERS)


# #1502 WP-E: allowed models for the review and AI Debug roles. Each role
# lists exactly the one model in use until the Owner decides to allow more
# (same rule as PR #1759 F1 for routes).
_ROLE_MODEL_CHECKS = {
    'codex-reviewer': _codex_role_model_ok,
    'claude-reviewer': _claude_role_model_ok,
    'codex-debug': _codex_role_model_ok,
    'claude-debug': _claude_role_model_ok,
}
# The canonical review/debug role ids the roles registry governs. A legacy
# alias (for example 'reviewer') is never accepted here; callers map it first.
REGISTRY_ROLES = frozenset(_ROLE_MODEL_CHECKS)


def _role_models(roles):
    """Return the normalized roles registry or raise ROUTE_CONFIG_INVALID."""
    if type(roles) is not dict or set(roles) != set(_ROLE_MODEL_CHECKS):
        raise RouteError('ROUTE_CONFIG_INVALID')
    normalized = {}
    for role, model_ok in _ROLE_MODEL_CHECKS.items():
        entry = roles[role]
        if type(entry) is not dict or set(entry) != {'allowed_models'}:
            raise RouteError('ROUTE_CONFIG_INVALID')
        models = entry['allowed_models']
        if (type(models) is not list or len(models) != 1 or
                not model_ok(models[0])):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized[role] = {'allowed_models': list(models)}
    return normalized


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


def _route_timeouts_ok(entry):
    """Return True when entry has no timeout keys or a valid pair of them.

    default_timeout and max_timeout come as a pair of ints with
    1 <= default_timeout <= max_timeout <= HARD_TIMEOUT_CAP_SECONDS. A route
    without the pair keeps the former fixed 600-second default and maximum.
    """
    present = _TIMEOUT_KEYS & set(entry)
    if not present:
        return True
    return (present == _TIMEOUT_KEYS and
            _timeout_limits_ok(entry['default_timeout'], entry['max_timeout']))


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


def check_registry_role_model(config, role, model):
    """Return model when the roles registry admits it for one review/debug role.

    #1502 WP-E invariant R1: the one shared launch check for codex-reviewer,
    claude-reviewer, codex-debug and claude-debug. config is a
    load_route_config() result; None loads the repository file. Every refusal
    fails closed with RouteError('ROLE_MODEL_NOT_ALLOWED'):

    * the file is unreadable or invalid, or its roles registry is absent or
      invalid (a route-only config admits no review/debug model);
    * role is not one of the canonical ids (aliases are mapped by the caller);
    * model is not a str, or its full identifier is not of the role's model
      family (no provider-prefix stripping, no case folding);
    * model is not exactly one of the role's allowed_models.

    There is no owner_override exemption and no other bypass.
    """
    try:
        if config is None:
            config = load_route_config()
        roles = config.get('roles') if type(config) is dict else None
        registry = _role_models(roles)
    except RouteError:
        raise RouteError('ROLE_MODEL_NOT_ALLOWED') from None
    model_ok = _ROLE_MODEL_CHECKS.get(role) if type(role) is str else None
    if model_ok is None or type(model) is not str or not model_ok(model):
        raise RouteError('ROLE_MODEL_NOT_ALLOWED')
    if not any(type(item) is str and item == model
               for item in registry[role]['allowed_models']):
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
    # 'roles' is optional so older fixtures still load; when present it is
    # validated strictly (#1502 WP-E).
    if (type(value) is not dict or set(value) - {'roles'} != {'schema_version', 'routes'} or
            type(value.get('schema_version')) is not int or value['schema_version'] != 1 or
            type(value.get('routes')) is not dict):
        raise RouteError('ROUTE_CONFIG_INVALID')
    routes = value['routes']
    if ('agy' not in routes or set(routes) - {'agy', 'claude', 'gw-agent', 'opencode'} or
            type(routes['agy']) is not dict or
            ('claude' in routes and type(routes['claude']) is not dict) or
            ('gw-agent' in routes and type(routes['gw-agent']) is not dict) or
            ('opencode' in routes and type(routes['opencode']) is not dict)):
        raise RouteError('ROUTE_CONFIG_INVALID')
    if not all(_route_timeouts_ok(item) for item in routes.values()):
        raise RouteError('ROUTE_CONFIG_INVALID')
    entry = routes['agy']
    expected = {'executable', 'default_model', 'allowed_models', 'default_effort',
                'denied_dirs', 'state_dirs'}
    if set(entry) - _TIMEOUT_KEYS != expected:
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
        if (set(claude) - _TIMEOUT_KEYS != expected_claude or claude.get('executable') != 'claude' or
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
        if (set(gw) - _TIMEOUT_KEYS != expected_gw or
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
    if 'opencode' in routes:
        opencode = routes['opencode']
        if (set(opencode) - _TIMEOUT_KEYS != {'executable', 'default_model',
                                              'allowed_models', 'default_effort'} or
                opencode.get('executable') != 'opencode' or
                not _opencode_model_ok(opencode.get('default_model')) or
                type(opencode.get('default_effort')) is not str or
                opencode['default_effort'] not in ('low', 'medium', 'high') or
                not _allowed_models_ok(opencode.get('allowed_models'),
                                       opencode['default_model'], _opencode_model_ok)):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized['opencode'] = dict(opencode)
        normalized['opencode']['allowed_models'] = list(opencode['allowed_models'])
    result = {'schema_version': 1, 'routes': normalized}
    if 'roles' in value:
        result['roles'] = _role_models(value['roles'])
    return result


def _unique_config_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate config key')
        result[key] = value
    return result
