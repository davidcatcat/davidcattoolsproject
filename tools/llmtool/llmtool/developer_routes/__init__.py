"""Provider-neutral execution contracts for direct CLI Developer routes."""

from .base import (
    DEFAULT_TIMEOUT_SECONDS, HARD_TIMEOUT_CAP_SECONDS, ROUTE_LIMIT_KEYS,
    TIMEOUT_KEYS as _TIMEOUT_KEYS, DeveloperRoute, RouteError, RouteResult,
    capture_snapshot, max_diff_bytes_ok, max_prompt_bytes_ok, max_task_paths_ok,
    max_source_file_bytes_ok, max_source_total_bytes_ok,
    resolve_route_max_diff_bytes, resolve_route_max_prompt_bytes,
    resolve_route_max_task_paths,
    resolve_route_max_source_file_bytes, resolve_route_max_source_total_bytes,
    resolve_route_timeout, run_route, timeout_limits_ok as _timeout_limits_ok,
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
    'check_route_model', 'check_route_model_family', 'check_registry_role_model',
    'route_has_family_check', 'REGISTRY_ROLES', 'registry_default_model',
    'resolve_route_timeout', 'resolve_route_max_diff_bytes', 'resolve_route_max_prompt_bytes',
    'resolve_route_max_task_paths', 'resolve_route_max_source_file_bytes',
    'resolve_route_max_source_total_bytes', 'max_prompt_bytes_ok',
    'max_source_file_bytes_ok', 'max_source_total_bytes_ok',
    'DEFAULT_TIMEOUT_SECONDS', 'HARD_TIMEOUT_CAP_SECONDS',
    'DEFAULT_ROLE_TIMEOUT_SECONDS', 'DEFAULT_REMOTE_TEST_TIMEOUT_SECONDS',
    'role_timeout', 'remote_test_timeout',
    'SandboxExecBoundary', 'UnsupportedBoundary', 'WriteBoundary',
    'for_platform',
]

DEFAULT_ROLE_TIMEOUT_SECONDS = 1800
DEFAULT_REMOTE_TEST_TIMEOUT_SECONDS = 1800


def _timeout_value_ok(v):
    return type(v) is int and 1 <= v <= HARD_TIMEOUT_CAP_SECONDS

_MAX_ALLOWED_MODELS = 16
_GENERIC_MODEL_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}'
_CLAUDE_MODEL_PATTERN = r'claude-[a-z0-9][a-z0-9.-]{0,120}'
# Owner #289: any well-formed OpenCode model id (free or paid, any provider
# prefix OpenCode supports, with or without a #variant).
_OPENCODE_MODEL_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,94}(?:#[a-z]{1,16})?'


def _generic_model_ok(value):
    import re

    return (type(value) is str and '://' not in value and
            re.fullmatch(_GENERIC_MODEL_PATTERN, value) is not None)


def _claude_model_ok(value):
    import re

    return type(value) is str and re.fullmatch(_CLAUDE_MODEL_PATTERN, value) is not None


def _opencode_model_ok(value):
    import re

    return (type(value) is str and 1 <= len(value) <= 128 and
            '://' not in value and '..' not in value and
            '\n' not in value and '\r' not in value and
            re.fullmatch(_OPENCODE_MODEL_PATTERN, value) is not None)


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


# Owner #2302 P2: the AGY route runs Gemini-family models only. Same allowlist
# rules as the role families: full identifier, lower case, no prefix.
_GEMINI_FAMILY_PATTERN = r'gemini-[a-z0-9][a-z0-9.-]{0,120}'


def _agy_model_ok(value):
    """An AGY route model: a Gemini family identifier, never another family."""
    return _family_model_ok(value, _GEMINI_FAMILY_PATTERN,
                            _CLAUDE_FAMILY_MARKERS + _CODEX_FAMILY_MARKERS)


# Owner #2302 P2, Owner #289: a route accepts any model of its own provider family
# in a valid provider format; allowed_models is not a membership list for these
# routes. gw-agent is not listed: it has no provider family and keeps its
# allowed_models gate on the gateway-served model (#1773) unchanged.
# OpenCode accepts any well-formed model id (free or paid, any provider prefix).
_ROUTE_MODEL_CHECKS = {
    'agy': _agy_model_ok,
    'claude': _claude_role_model_ok,
    'opencode': _opencode_model_ok,
}
# Owner #2302 P2 Repair 2: a route with no provider family (gw-agent) keeps
# its allowed_models membership gate, but a malformed identifier is still
# refused before any owner override is read.
_GENERIC_ROUTE_CHECKS = {'gw-agent': _generic_model_ok}


# #1502 WP-E, Owner #2302 P2: the family check for each review and AI Debug
# role. A role accepts any model of its own family; its registry entry names
# the default model (default_model, else the first allowed_models item).
_ROLE_MODEL_CHECKS = {
    'codex-reviewer': _codex_role_model_ok,
    'claude-reviewer': _claude_role_model_ok,
    'codex-debug': _codex_role_model_ok,
    'claude-debug': _claude_role_model_ok,
}
# The canonical review/debug role ids the roles registry governs. A legacy
# alias (for example 'reviewer') is never accepted here; callers map it first.
REGISTRY_ROLES = frozenset(_ROLE_MODEL_CHECKS)


_ROLE_ENTRY_KEYS = frozenset({'default_model', 'allowed_models', 'timeout'})


def _role_models(roles):
    """Return the normalized roles registry or raise ROUTE_CONFIG_INVALID.

    Owner #2302 P2: each entry names its default model with default_model,
    allowed_models or both (at least one). allowed_models is accepted but not
    a membership list: when present it is a well-formed list of the role's
    family. Every named model must pass the role's family check.
    """
    if type(roles) is not dict or set(roles) != set(_ROLE_MODEL_CHECKS):
        raise RouteError('ROUTE_CONFIG_INVALID')
    normalized = {}
    for role, model_ok in _ROLE_MODEL_CHECKS.items():
        entry = roles[role]
        if (type(entry) is not dict or set(entry) - _ROLE_ENTRY_KEYS or
                not {'default_model', 'allowed_models'} & set(entry)):
            raise RouteError('ROUTE_CONFIG_INVALID')
        if 'default_model' in entry and not model_ok(entry['default_model']):
            raise RouteError('ROUTE_CONFIG_INVALID')
        if 'allowed_models' in entry and not _listed_models_ok(entry['allowed_models'], model_ok):
            raise RouteError('ROUTE_CONFIG_INVALID')
        if 'timeout' in entry and not _timeout_value_ok(entry['timeout']):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized[role] = {}
        if 'default_model' in entry:
            normalized[role]['default_model'] = entry['default_model']
        if 'allowed_models' in entry:
            normalized[role]['allowed_models'] = list(entry['allowed_models'])
        if 'timeout' in entry:
            normalized[role]['timeout'] = entry['timeout']
    return normalized


def registry_default_model(entry):
    """Return a roles-registry entry's default model, else None.

    default_model wins; otherwise the first allowed_models item (the form the
    registry used before Owner #2302). Shape only: callers family-check it.
    """
    if type(entry) is not dict:
        return None
    if 'default_model' in entry:
        model = entry['default_model']
    else:
        models = entry.get('allowed_models')
        model = models[0] if type(models) is list and models else None
    return model if type(model) is str else None


def _gw_agent_budgets_ok(budgets, profile=None):
    """Return True when budgets has valid gw-agent budget keys and ranges (#2281 L9).

    #2281: max_wall_seconds is no shorter than the route default timeout (1800 s)
    and bounded by max_timeout (3600 s). Basis: the route default timeout and
    the two recorded 600 s stops (N3 and N1, AI Debug RCA_CONFIRMED on #2302)
    where runs were ended by the whole-run wall budget before the route default
    timeout.
    """
    if type(budgets) is not dict:
        return False
    keys = set(budgets)
    valid_direct_keys = {'max_turns', 'max_input_tokens', 'max_output_tokens',
                         'max_wall_seconds', 'max_tool_result_bytes', 'max_sent_bytes'}
    valid_multiplier_keys = {'max_turns', 'max_input_tokens_multiplier',
                            'max_output_tokens_multiplier', 'max_wall_seconds',
                            'max_tool_result_bytes', 'max_sent_bytes'}
    if keys != valid_direct_keys and keys != valid_multiplier_keys:
        return False
    max_turns = budgets.get('max_turns')
    if type(max_turns) is not int or type(max_turns) is bool or not 1 <= max_turns <= 1000:
        return False
    max_wall_seconds = budgets.get('max_wall_seconds')
    if (type(max_wall_seconds) is not int or type(max_wall_seconds) is bool or
            not 1 <= max_wall_seconds <= 86400):
        return False
    max_tool_result_bytes = budgets.get('max_tool_result_bytes')
    if (type(max_tool_result_bytes) is not int or type(max_tool_result_bytes) is bool or
            not 256 <= max_tool_result_bytes <= 1048576):
        return False
    max_sent_bytes = budgets.get('max_sent_bytes')
    if (type(max_sent_bytes) is not int or type(max_sent_bytes) is bool or
            not 1024 <= max_sent_bytes <= 512 * 1024 * 1024):
        return False
    if 'max_input_tokens' in budgets:
        val = budgets['max_input_tokens']
        if type(val) is not int or type(val) is bool or not 1 <= val <= 50_000_000:
            return False
    if 'max_input_tokens_multiplier' in budgets:
        val = budgets['max_input_tokens_multiplier']
        if type(val) is not int or type(val) is bool or val < 1:
            return False
        context_cap = profile.get('context_cap_tokens') if type(profile) is dict else None
        if type(context_cap) is not int or type(context_cap) is bool or context_cap < 1:
            return False
        resolved_input = val * context_cap
        if not 1 <= resolved_input <= 50_000_000:
            return False
    if 'max_output_tokens' in budgets:
        val = budgets['max_output_tokens']
        if type(val) is not int or type(val) is bool or not 1 <= val <= 5_000_000:
            return False
    if 'max_output_tokens_multiplier' in budgets:
        val = budgets['max_output_tokens_multiplier']
        if type(val) is not int or type(val) is bool or val < 1:
            return False
        output_cap = profile.get('max_output_tokens') if type(profile) is dict else None
        if type(output_cap) is not int or type(output_cap) is bool or output_cap < 1:
            return False
        resolved_output = val * output_cap
        if not 1 <= resolved_output <= 5_000_000:
            return False
    return True


def _listed_models_ok(value, model_ok):
    """Return True when value is a well-formed allowed_models list.

    Owner #2302 P2: allowed_models is accepted but not enforced as a
    membership list. When present it holds 1 to _MAX_ALLOWED_MODELS unique
    strings, each passing model_ok (the route's or role's format and family).
    """
    return (type(value) is list and 1 <= len(value) <= _MAX_ALLOWED_MODELS and
            all(type(item) is str for item in value) and
            len(set(value)) == len(value) and
            all(model_ok(item) for item in value))


def _allowed_models_ok(value, default_model, model_ok):
    """Return True when value is exactly [default_model], a valid one-item list.

    Security repair (PR #1759 F1): the gw-agent route's allowed_models must
    list only its current default model. Any extra or different entry is
    refused at load. Owner #2302 P2 leaves this gw-agent rule unchanged.
    """
    return (_listed_models_ok(value, model_ok) and
            len(value) == 1 and type(default_model) is str and
            value[0] == default_model)


def _optional_listed_models_ok(entry, model_ok):
    """Owner #2302 P2: a route's allowed_models is optional; when present it
    must be well formed, but it need not contain default_model."""
    return 'allowed_models' not in entry or _listed_models_ok(entry['allowed_models'], model_ok)


def _copy_route(entry):
    """Return a copy of a route entry with its own allowed_models list, if any."""
    copied = dict(entry)
    if 'allowed_models' in copied:
        copied['allowed_models'] = list(copied['allowed_models'])
    return copied


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
    RouteError('ROLE_MODEL_NOT_ALLOWED') for an absent route or a non-str
    model. Owner #2302 P2: agy, claude and opencode accept any model of their
    own provider family in a valid format (_ROUTE_MODEL_CHECKS) and refuse a
    wrong-family or malformed one; allowed_models is not consulted. gw-agent
    keeps its allowed_models membership gate, but a malformed identifier is
    refused here too, before membership (Repair 2). A config that is not a
    load_route_config() mapping (including None) admits no model: this check
    never loads the repository file itself.
    """
    routes = config.get('routes') if type(config) is dict else None
    entry = (routes.get(route) if type(routes) is dict and type(route) is str
             else None)
    if type(entry) is not dict or type(model) is not str:
        raise RouteError('ROLE_MODEL_NOT_ALLOWED')
    model_ok = _ROUTE_MODEL_CHECKS.get(route)
    if model_ok is not None:
        if not model_ok(model):
            raise RouteError('ROLE_MODEL_NOT_ALLOWED')
        return model
    format_ok = _GENERIC_ROUTE_CHECKS.get(route)
    if format_ok is not None and not format_ok(model):
        raise RouteError('ROLE_MODEL_NOT_ALLOWED')
    allowed = entry.get('allowed_models')
    if (type(allowed) is not list or
            not any(type(item) is str and item == model for item in allowed)):
        raise RouteError('ROLE_MODEL_NOT_ALLOWED')
    return model


def route_has_family_check(route):
    """Return True when route has a mandatory provider family/format check.

    Owner #2302 P2 Repair 2: agy, claude and opencode are gated by their
    provider family and format, and no owner override may relax that gate.
    gw-agent has no provider family, so only its allowed_models membership
    gate may be relaxed.
    """
    return route in _ROUTE_MODEL_CHECKS


def check_route_model_family(route, model):
    """Return model when a route's own format/family check admits it.

    Owner #2302 P2 Repair 2: agy, claude and opencode have a mandatory
    format/family check that no owner override may bypass. A model this function
    refuses is refused on every path, before any override is read. gw-agent has
    no provider family, so only its generic identifier format is checked here;
    it keeps its allowed_models membership gate (which an override may relax).
    """
    model_ok = _ROUTE_MODEL_CHECKS.get(route, _GENERIC_ROUTE_CHECKS.get(route))
    if model_ok is not None and not model_ok(model):
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
    * an unloadable repository file refuses ROUTE_CONFIG_UNAVAILABLE (#2027).

    Owner #2302 P2: any model of the role's own family is admitted; the
    role's allowed_models is not a membership list. There is no
    owner_override exemption and no other bypass.
    """
    if config is None:
        try:
            config = load_route_config()
        except RouteError:
            raise RouteError('ROUTE_CONFIG_UNAVAILABLE') from None
    try:
        roles = config.get('roles') if type(config) is dict else None
        _role_models(roles)
    except RouteError:
        raise RouteError('ROLE_MODEL_NOT_ALLOWED') from None
    model_ok = _ROLE_MODEL_CHECKS.get(role) if type(role) is str else None
    if model_ok is None or type(model) is not str or not model_ok(model):
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
    # 'roles' and 'remote_test_timeout' are optional so older fixtures still
    # load; when present they are validated strictly (#1502 WP-E, #1815).
    if (type(value) is not dict or set(value) - {'roles', 'remote_test_timeout'} != {'schema_version', 'routes'} or
            type(value.get('schema_version')) is not int or value['schema_version'] != 1 or
            type(value.get('routes')) is not dict):
        raise RouteError('ROUTE_CONFIG_INVALID')
    if 'remote_test_timeout' in value:
        if not _timeout_value_ok(value['remote_test_timeout']):
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
    if not all(max_diff_bytes_ok(item['max_diff_bytes'])
               for item in routes.values() if 'max_diff_bytes' in item):
        raise RouteError('ROUTE_CONFIG_INVALID')
    task_paths = [item['max_task_paths'] for item in routes.values() if 'max_task_paths' in item]
    if not all(map(max_task_paths_ok, task_paths)):
        raise RouteError('ROUTE_CONFIG_INVALID')
    if not all(max_prompt_bytes_ok(item['max_prompt_bytes'])
               for item in routes.values() if 'max_prompt_bytes' in item):
        raise RouteError('ROUTE_CONFIG_INVALID')
    if not all(max_source_file_bytes_ok(item['max_source_file_bytes'])
               for item in routes.values() if 'max_source_file_bytes' in item):
        raise RouteError('ROUTE_CONFIG_INVALID')
    if not all(max_source_total_bytes_ok(item['max_source_total_bytes'])
               for item in routes.values() if 'max_source_total_bytes' in item):
        raise RouteError('ROUTE_CONFIG_INVALID')
    entry = routes['agy']
    expected = {'executable', 'default_model', 'default_effort', 'denied_dirs', 'state_dirs'}
    # Owner #2302 P2: allowed_models is optional and not a membership list.
    if set(entry) - ROUTE_LIMIT_KEYS - {'allowed_models'} != expected:
        raise RouteError('ROUTE_CONFIG_INVALID')
    if (entry['executable'] != 'agy' or not _agy_model_ok(entry['default_model']) or
            entry['default_effort'] not in ('minimal', 'low', 'medium', 'high') or
            type(entry['denied_dirs']) is not list or entry['denied_dirs'] != ['.gemini'] or
            entry['state_dirs'] != {'policy': 'private-temp-only'} or
            not _optional_listed_models_ok(entry, _agy_model_ok)):
        raise RouteError('ROUTE_CONFIG_INVALID')
    normalized = {'agy': _copy_route(entry)}
    if 'claude' in routes:
        claude = routes['claude']
        expected_claude = {'executable', 'default_model', 'default_effort',
                           'denied_dirs', 'state_dirs'}
        if (set(claude) - ROUTE_LIMIT_KEYS - {'allowed_models'} != expected_claude or
                claude.get('executable') != 'claude' or
                not _claude_role_model_ok(claude.get('default_model')) or
                type(claude.get('default_effort')) is not str or
                claude['default_effort'] not in ('low', 'medium', 'high', 'xhigh', 'max') or
                type(claude.get('denied_dirs')) is not list or
                claude['denied_dirs'] != ['.claude'] or
                claude.get('state_dirs') != {'policy': 'private-temp-only'} or
                not _optional_listed_models_ok(claude, _claude_role_model_ok)):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized['claude'] = _copy_route(claude)
    if 'gw-agent' in routes:
        gw = routes['gw-agent']
        expected_gw = {'package_path', 'default_model', 'allowed_models', 'default_effort',
                       'profile_name', 'profile', 'authorization_ref',
                       'allowed_data_classes', 'budgets'}
        if (set(gw) - ROUTE_LIMIT_KEYS != expected_gw or
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
                not _gw_agent_budgets_ok(gw.get('budgets'), gw.get('profile')) or
                gw['profile'].get('model') not in gw['allowed_models']):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized['gw-agent'] = dict(gw)
        normalized['gw-agent']['budgets'] = dict(gw['budgets'])
        normalized['gw-agent']['allowed_models'] = list(gw['allowed_models'])
    if 'opencode' in routes:
        opencode = routes['opencode']
        if (set(opencode) - ROUTE_LIMIT_KEYS - {'allowed_models'} !=
                {'executable', 'default_model', 'default_effort'} or
                opencode.get('executable') != 'opencode' or
                not _opencode_model_ok(opencode.get('default_model')) or
                type(opencode.get('default_effort')) is not str or
                opencode['default_effort'] not in ('low', 'medium', 'high') or
                not _optional_listed_models_ok(opencode, _opencode_model_ok)):
            raise RouteError('ROUTE_CONFIG_INVALID')
        normalized['opencode'] = _copy_route(opencode)
    result = {'schema_version': 1, 'routes': normalized}
    if 'roles' in value:
        result['roles'] = _role_models(value['roles'])
    if 'remote_test_timeout' in value:
        result['remote_test_timeout'] = value['remote_test_timeout']
    return result


def role_timeout(role, path=None):
    """Return the timeout in seconds configured for role or DEFAULT_ROLE_TIMEOUT_SECONDS."""
    if role not in REGISTRY_ROLES:
        raise RouteError('ROUTE_CONFIG_INVALID')
    config = load_route_config(path)
    roles = config.get('roles')
    if type(roles) is dict and role in roles:
        timeout = roles[role].get('timeout')
        if timeout is not None:
            return timeout
    return DEFAULT_ROLE_TIMEOUT_SECONDS


def remote_test_timeout(path=None):
    """Return the remote test timeout in seconds or DEFAULT_REMOTE_TEST_TIMEOUT_SECONDS."""
    config = load_route_config(path)
    return config.get('remote_test_timeout', DEFAULT_REMOTE_TEST_TIMEOUT_SECONDS)


def _unique_config_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate config key')
        result[key] = value
    return result
