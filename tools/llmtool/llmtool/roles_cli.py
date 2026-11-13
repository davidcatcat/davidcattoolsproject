"""`llmtool roles`: read-only view of every resolved role (#1502 WP-E).

Each line is tab-separated: role, provider, account name, model, effort and
source layer. It never prints a config_dir path, a credential or any other
account data, and it never writes configuration.
"""

import re
import sys

from . import role_config
from .errors import LLMToolError

_CODE = re.compile(r'[A-Z0-9_]{1,64}\Z')


def add_roles_parser(sub):
    sub.add_parser('roles', help='List resolved roles offline (read-only; no paths or secrets)')


def _cell(value):
    return value if type(value) is str and value else '-'


def role_lines(environ=None, home=None):
    """Return (lines, failed) for KNOWN_ROLES plus roles named in the user config."""
    names = set(role_config.KNOWN_ROLES)
    try:
        configured = role_config.configured_roles(home)
    except LLMToolError:
        # Every role line below then reports the configuration error itself.
        configured = []
    for name in configured:
        # A legacy alias shows under its new id, not as a second role.
        if role_config.LEGACY_ROLE_ALIASES.get(name) not in names:
            names.add(name)
    lines = []
    failed = False
    for name in sorted(names):
        try:
            resolved = role_config.resolve_role(name, environ=environ, home=home)
        except LLMToolError as exc:
            code = getattr(exc, 'role_config_code', None)
            if type(code) is not str or _CODE.fullmatch(code) is None:
                code = 'ROLE_CONFIG_INVALID'
            lines.append('\t'.join((name, 'ERROR', code)))
            failed = True
            continue
        provider = resolved.get('provider') or role_config.ROUTE_ROLES.get(name)
        lines.append('\t'.join((
            name, _cell(provider), _cell(resolved.get('account')),
            _cell(resolved.get('model')), _cell(resolved.get('effort')),
            _cell(resolved.get('source')))))
    return lines, failed


def run_roles(args, *, environ=None, home=None, stdout=None):
    """Print one line per role; exit 1 when any role cannot be resolved."""
    lines, failed = role_lines(environ=environ, home=home)
    stream = sys.stdout if stdout is None else stdout
    for line in lines:
        stream.write(line + '\n')
    return 1 if failed else 0
