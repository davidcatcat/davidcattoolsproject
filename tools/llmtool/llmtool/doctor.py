"""Offline configuration, executable-presence and read-only diagnostics inspection."""

import os
import shutil
import sys

from .config import inspect_config
from .diagnostics_reader import DiagnosticsReader, ReaderError
from .diagnostics_store import persistence_supported
from .errors import LLMToolError


def doctor_output():
    """Buffer the complete safe projection before touching stdout.

    Returns the rendered report and whether diagnostics metadata is degraded.
    """
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
    degraded = store['metadata'] == 'degraded'
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
    lines.append('metadata=' + store['metadata'] + ' damaged_count=' +
                 str(store['damaged_count']))
    lines.append('Damaged diagnostics metadata reported; nothing repaired, '
                 'displayed or traced.' if degraded else
                 'Metadata validated read-only; trace evidence and write access unverified.')
    return '\n'.join(lines) + '\n', degraded


def run_doctor():
    try:
        output, degraded = doctor_output()
        sys.stdout.write(output)
        sys.stdout.flush()
        # A damaged-metadata finding is a real nonzero result, never a silent pass.
        return 1 if degraded else 0
    except ReaderError as exc:
        print(exc.line(), file=sys.stderr)
        return 1
    except BrokenPipeError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        print(ReaderError('STORE_UNAVAILABLE').line(), file=sys.stderr)
        return 1
