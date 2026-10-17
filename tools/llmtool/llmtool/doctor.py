"""Offline configuration, executable-presence and store-structure inspection."""

import os
import shutil
import sys

from .config import inspect_config
from .diagnostics_reader import DiagnosticsReader, ReaderError
from .diagnostics_store import persistence_supported
from .errors import LLMToolError


def _doctor_snapshot():
    """Buffer the complete safe projection before touching stdout."""
    try:
        view = inspect_config()
    except LLMToolError:
        # Do not render foreign/config strings, even if normally safe to show.
        raise LLMToolError('code=CONFIG_INVALID offline configuration invalid; details omitted') from None
    kind = view['provider_type']
    executable = 'not-applicable'
    if kind in ('claude-cli', 'codex-cli'):
        # Same lookup/absolute-path requirement as complete(); never invoke it.
        found = shutil.which('claude' if kind == 'claude-cli' else 'codex')
        executable = 'available' if found is not None and os.path.isabs(found) else 'missing'
    supported = persistence_supported()
    store = DiagnosticsReader().inspect_store()
    settings = view['settings']
    lines = ['Offline checks only; authentication and inference unverified.',
             'persistence_supported=' + str(supported).lower(),
             'persist_metadata=' + str(settings.persist_metadata).lower()]
    lines.extend(key + '=' + str(getattr(settings, key)) for key in
                 ('max_runs', 'max_total_bytes', 'max_age_days', 'max_run_bytes'))
    lines.append('provider_type=' + kind + ' readiness=' + view['readiness'] +
                 ' executable=' + executable + ' credential=' + view['credential'])
    lines.append('store=' + store['state'] + ' run_count=' + str(store['run_count']) +
                 ' active_count=' + str(store['active_count']) +
                 ' unknown_count=' + str(store['unknown_count']))
    lines.append('damaged_count=' + str(store['damaged_count']) +
                 ' staging_count=' + str(store['staging_count']) +
                 ' root_unknown_count=' + str(store['root_unknown_count']) +
                 ' recovery=' + store['recovery'])
    lines.append('Bounded metadata headers checked; trace contents, metadata completion and write access unverified.')
    if store['recovery'] == 'BLOCKED':
        lines.append('Preserve store data; inspect runs metadata before any separately reviewed repair. No automatic cleanup.')
    return '\n'.join(lines) + '\n', int(store['recovery'] == 'BLOCKED')


def doctor_output():
    return _doctor_snapshot()[0]


def run_doctor():
    try:
        output, status = _doctor_snapshot()
        sys.stdout.write(output)
        sys.stdout.flush()
        return status
    except ReaderError as exc:
        print(exc.line(), file=sys.stderr)
        return 1
    except BrokenPipeError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        print(ReaderError('STORE_UNAVAILABLE').line(), file=sys.stderr)
        return 1
