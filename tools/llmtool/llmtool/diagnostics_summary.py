"""Version-1 persisted summary: safe immutable scalars, no task/provider bodies."""

import json
import math
from dataclasses import dataclass, fields
from typing import Optional

from . import diagnostics as d

SUMMARY_SCHEMA = 'llmtool.diagnostics.summary'
SUMMARY_MAX_BYTES = 4 * 1024
MAX_STORED_RUN_BYTES = 8 * 1024 * 1024
RUN_STATUSES = ('success', 'error', 'interrupted', 'output_closed', 'incomplete')
PROVIDER_TYPES = ('openai', 'openai-compatible', 'claude-cli', 'codex-cli')


def _integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('invalid diagnostics summary integer')


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    mode: d.DiagnosticMode
    status: str
    started_at: str
    ended_at: str
    duration_ms: int
    event_count: int
    dropped_event_count: int
    trace_bytes: int
    provider_type: Optional[str] = None
    final_error_code: Optional[d.ErrorCode] = None
    command: str = 'ask'
    persisted: bool = True
    schema: str = SUMMARY_SCHEMA
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema) is not str or self.schema != SUMMARY_SCHEMA:
            raise ValueError('invalid diagnostics summary schema')
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('invalid diagnostics summary version')
        d.validate_run_id(self.run_id)
        if type(self.mode) is not d.DiagnosticMode:
            raise ValueError('invalid diagnostics summary mode')
        if type(self.command) is not str or self.command != 'ask':
            raise ValueError('invalid diagnostics summary command')
        if type(self.status) is not str or self.status not in RUN_STATUSES:
            raise ValueError('invalid diagnostics summary status')
        d.validate_timestamp(self.started_at)
        d.validate_timestamp(self.ended_at)
        if self.ended_at < self.started_at:
            raise ValueError('invalid diagnostics summary time order')
        if type(self.persisted) is not bool or not self.persisted:
            raise ValueError('summary requires persisted metadata')
        for name in ('duration_ms', 'event_count', 'dropped_event_count', 'trace_bytes'):
            maximum = (d.MAX_DURATION_MS if name == 'duration_ms' else
                       MAX_STORED_RUN_BYTES if name == 'trace_bytes' else d.MAX_EVENT_ID)
            _integer(getattr(self, name), maximum)
        if self.provider_type is not None and (type(self.provider_type) is not str
                                               or self.provider_type not in PROVIDER_TYPES):
            raise ValueError('invalid diagnostics summary provider')
        if self.final_error_code is not None and type(self.final_error_code) is not d.ErrorCode:
            raise ValueError('invalid diagnostics summary error code')
        self.to_json()

    def to_dict(self):
        result = {f.name: getattr(self, f.name) for f in fields(self)}
        result['mode'] = self.mode.value
        result['final_error_code'] = None if self.final_error_code is None else self.final_error_code.value
        return result

    def to_json(self):
        return d.diagnostic_json(self.to_dict(), SUMMARY_MAX_BYTES)


def strict_object(raw, limit):
    """Internal bounded cleanup validation; not a public diagnostics reader CLI."""
    if type(raw) is not bytes or len(raw) > limit:
        raise ValueError('diagnostics document exceeds bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate diagnostics key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('invalid diagnostics numeric constant')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, RecursionError):
        raise ValueError('invalid diagnostics document') from None
    if type(value) is not dict:
        raise ValueError('diagnostics document requires an object')
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > d.MAX_JSON_DEPTH or count > d.MAX_JSON_ITEMS:
            raise ValueError('diagnostics document exceeds structural bounds')
        if type(item) in (dict, list):
            if type(item) is dict:
                for key in item:
                    d.safe_diagnostic_text(key)
                children = item.values()
            else:
                children = item
            pending.extend((child, depth + 1) for child in children)
        elif type(item) is str:
            d.safe_diagnostic_text(item)
        elif type(item) is float and not math.isfinite(item):
            raise ValueError('invalid diagnostics numeric value')
    return value


def parse_run_summary(raw):
    value = strict_object(raw, SUMMARY_MAX_BYTES)
    required = {f.name for f in fields(RunSummary)}
    if not required <= set(value):
        raise ValueError('invalid diagnostics summary fields')
    # Supported-version readers ignore extensions, never interpret them.
    value = {key: value[key] for key in required}
    value['mode'] = d.DiagnosticMode(value['mode'])
    if value['final_error_code'] is not None:
        value['final_error_code'] = d.ErrorCode(value['final_error_code'])
    return RunSummary(**value)
