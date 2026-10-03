"""Ephemeral ask observation only: bounded events, no persistence or recovery."""

import math
import re
import time

from . import diagnostics as d

END_SEVERITY = {
    'success': 'info',
    'output_closed': 'info',
    'error': 'error',
    'interrupted': 'warning',
    'incomplete': 'warning',
}
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens', 'total_tokens')


class RunContext:
    """One sequential ask; retain only validated metadata, never task/result text.

    Event IDs count accepted events, so a failed construction leaves no gap.
    No artificial spans: this first lifecycle has no nested workflow operations.
    Strict model/clock/adapter failures are secondary and cannot escape to ask.
    """

    def __init__(self, mode=None):
        self.command = 'ask'
        self.mode = d.DiagnosticMode.NORMAL if mode is None else mode
        self.run_id = None
        self.started = None
        self.dropped_event_count = 0
        self.emitted_drop_warning = False
        self.final_status = None
        self.provider_type = None
        self._provider_component = 'provider'
        self._events = []
        self._event_bytes = 0
        self._metadata = {}
        try:
            self.run_id = d.new_run_id()
            self.started = time.monotonic()
        except Exception:
            self.drop()
        self.emit(d.EventType.RUN_START, 'cli.ask', d.DiagnosticSeverity.INFO,
                  {'command': self.command, 'mode': self.mode.value})

    @property
    def events(self):
        return tuple(self._events)

    def _append(self, kind, component, severity, data):
        obj = d.DiagnosticEvent(
            run_id=self.run_id, event_id=len(self._events) + 1,
            timestamp=d.utc_timestamp(), type=kind, component=component,
            severity=severity, data=data,
        )
        size = len(obj.to_json().encode('utf-8'))
        if len(self._events) >= d.MAX_RUN_EVENTS or self._event_bytes + size > d.MAX_RUN_EVENT_BYTES:
            raise ValueError('ephemeral diagnostics budget exceeded')
        self._events.append(obj)
        self._event_bytes += size
        return obj

    def drop(self):
        self.dropped_event_count += 1
        if self.emitted_drop_warning or self.final_status is not None:
            # Rendering is secondary too; never append past an accepted run.end.
            return
        self.emitted_drop_warning = True  # set before construction: no recursion
        try:
            self._append(d.EventType.WARNING, 'diagnostics', d.DiagnosticSeverity.WARNING,
                         {'code': 'DIAGNOSTICS_EVENT_DROPPED',
                          'message': 'diagnostic event omitted; task result unaffected'})
        except Exception:
            self.dropped_event_count += 1

    def emit(self, kind, component, severity, data):
        if self.final_status is not None and kind is not d.EventType.RUN_END:
            self.drop()
            return None
        try:
            return self._append(kind, component, severity, data)
        except Exception:
            self.drop()
            return None

    def request(self, config):
        try:
            profile = config['active_provider']
            provider = config['providers'][profile]
            obj = self.emit(d.EventType.PROVIDER_REQUEST, 'provider.' + provider['type'],
                            d.DiagnosticSeverity.INFO,
                            {'provider_type': provider['type'], 'profile': profile})
            if obj is not None:
                self.provider_type = obj.data['provider_type']
                self._provider_component = obj.component
        except Exception:
            self.drop()

    def response(self, metadata):
        """Allowlist first; invalid selected metadata drops observation, not reply.

        Preserve accepted provider key order for legacy verbose rendering. Never
        inspect/stringify unknown fields, including raw bodies or reasoning.
        """
        try:
            if type(metadata) is not dict:
                raise ValueError('provider metadata requires a plain mapping')
            safe = {k: v for k, v in metadata.items()
                    if type(k) is str and k in ('elapsed_seconds', 'model', *TOKEN_FIELDS)}
            data = {}
            for key, value in safe.items():
                if key == 'elapsed_seconds':
                    if (type(value) not in (int, float) or not math.isfinite(value)
                            or not 0 <= value <= d.MAX_DURATION_MS / 1000):
                        raise ValueError('invalid provider duration')
                    data['duration_ms'] = int(value * 1000)
                elif key == 'model' and type(value) is str and value == '[redacted]':
                    # Existing provider redaction marker is display data, not a model ID.
                    d.safe_diagnostic_text(value)
                elif key == 'model':
                    # Display accepts provider identifiers independently of the
                    # stricter event schema; credentials/controls remain rejected.
                    d.safe_diagnostic_text(value, d.MODEL_MAX_CHARS)
                    if re.fullmatch(r'[A-Za-z0-9_./:@-]+', value) is None:
                        raise ValueError('invalid provider model')
                    data[key] = value
                else:
                    if value is not None and (type(value) is not int
                                              or not 0 <= value <= d.MAX_TOKEN_COUNT):
                        raise ValueError('invalid provider token count')
                    data[key] = value
            data['token_usage_available'] = any(type(data.get(k)) is int for k in TOKEN_FIELDS)
            # Validate bounded display scalars before storing them. Event-only
            # validation/construction must never erase legacy verbose metadata.
            d.diagnostic_json(data, d.PROVIDER_METADATA_MAX_BYTES)
            self._metadata = safe
            if self.emit(d.EventType.PROVIDER_RESPONSE, self._provider_component,
                         d.DiagnosticSeverity.INFO, data) is None:
                return {}
            return safe
        except Exception:
            self.drop()
            return {}

    def error(self, exc, default_code, component):
        """Only the CLI's safe expected errors enter this boundary."""
        code = default_code
        try:
            diagnostic = exc.diagnostic
            if getattr(exc, 'diagnostic_dropped', False):
                self.drop()
                hint = getattr(exc, 'diagnostic_code', None)
                if type(hint) is d.ErrorCode:
                    code = hint
            if type(diagnostic) is d.DiagnosticError:
                code = diagnostic.code
                component = diagnostic.component
                data = diagnostic.to_dict()
                data.pop('component')
                data.pop('severity')
            else:
                data = {'code': code.value, 'message': str(exc)}
            self.emit(d.EventType.ERROR, component, d.DiagnosticSeverity.ERROR, data)
        except Exception:
            self.drop()
        return code

    def finish(self, status):
        if self.final_status is not None:
            return
        self.final_status = status
        try:
            elapsed = time.monotonic() - self.started
            duration = min(d.MAX_DURATION_MS, max(0, int(elapsed * 1000)))
            self.emit(d.EventType.RUN_END, 'cli.ask', d.DiagnosticSeverity(END_SEVERITY[status]),
                      {'status': status, 'duration_ms': duration})
        except Exception:
            self.drop()

    def failure_line(self, code):
        return 'llmtool: code=' + code.value + ' run_id=' + (self.run_id or 'unavailable') + ' trace=not-saved'

    def display(self, stream):
        """All output here is optional; a failed display cannot alter task status."""
        if self.mode is d.DiagnosticMode.NORMAL:
            return
        try:
            for key, value in self._metadata.items():
                print(f'{key}={value}', file=stream)
            print('run_id=' + (self.run_id or 'unavailable'), file=stream)
            if self.provider_type is not None:
                print('provider_type=' + self.provider_type, file=stream)
            print('diagnostics_persisted=false', file=stream)
            if self.mode is d.DiagnosticMode.DEBUG:
                for obj in self.events:
                    line = (f'event_id={obj.event_id} type={obj.type.value} '
                            f'component={obj.component} severity={obj.severity.value}')
                    if 'duration_ms' in obj.data:
                        line += f" duration_ms={obj.data['duration_ms']}"
                    if obj.type is d.EventType.WARNING:
                        line += ' code=' + obj.data['code']
                    print(line, file=stream)
                print(f'dropped_event_count={self.dropped_event_count}', file=stream)
        except Exception:
            self.drop()
