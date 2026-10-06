"""Bounded ask observation with optional metadata persistence, never recovery."""

import math
import re
import time
from dataclasses import replace

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
        self.persistence_requested = False
        self.persisted = False
        self.final_error_code = None
        self._stored_run = None
        self._store_warning = None
        self._store_warning_displayed = False
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

    def _persistence_failure(self, warning=None, *, writing=False):
        from .diagnostics_store import StoreWarning
        # Copy only a closed code; never render a foreign warning's message/repr.
        code = (d.ErrorCode.DIAGNOSTICS_WRITE_FAILED if writing else
                d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE)
        allowed = {d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE,
                   d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE,
                   d.ErrorCode.DIAGNOSTICS_STORE_FULL,
                   d.ErrorCode.DIAGNOSTICS_RUN_COLLISION,
                   d.ErrorCode.DIAGNOSTICS_WRITE_FAILED}
        if type(warning) is StoreWarning and type(warning.code) is d.ErrorCode and warning.code in allowed:
            code = warning.code
        if self._store_warning is None:
            self._store_warning = StoreWarning(code)
        # A verified durable commit is monotonic; later release warnings are secondary.
        self.close()

    def enable_persistence(self, settings):
        """Explicit settings only; reconcile a store collision before any write.

        run.start already exists in memory. Rebind buffered immutable events to
        the final ID before appending them; no event with the old ID is saved.
        """
        if not settings.persist_metadata or self.persistence_requested:
            return
        self.persistence_requested = True
        store = None
        try:
            from .diagnostics_store import DiagnosticsStore
            store = DiagnosticsStore(settings)
            self._stored_run = store.start(self.run_id)
            if self._stored_run is None:
                self._persistence_failure(store.warning)
                return
            identity = d.validate_run_id(self._stored_run.run_id)
            rebound = [replace(event, run_id=identity) for event in self._events]
            self.run_id, self._events = identity, rebound
            for event in self._events:
                self._persist_event(event)
        except Exception:
            self._persistence_failure()

    def persistence_unavailable(self):
        """A requested save with unusable settings cannot mask an input error."""
        self.persistence_requested = True
        self._persistence_failure()

    def _persist_event(self, event):
        writer = self._stored_run
        if writer is None:
            return
        try:
            if writer.append(event) is not True:
                self._persistence_failure(writer.warning, writing=True)
        except Exception:
            self._persistence_failure(writing=True)

    def close(self):
        """Release the optional writer on every CLI exit; never retry a write."""
        writer, self._stored_run = self._stored_run, None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                # Store close is secondary just like append/publication.
                from .diagnostics_store import StoreWarning
                if self._store_warning is None:
                    self._store_warning = StoreWarning(d.ErrorCode.DIAGNOSTICS_WRITE_FAILED)

    def display_store_warning(self, stream):
        if self._store_warning is not None and not self._store_warning_displayed:
            self._store_warning_displayed = True
            try:
                if self.persisted:
                    print('llmtool: code=' + self._store_warning.code.value +
                          ' diagnostics cleanup failed; saved metadata retained', file=stream)
                else:
                    print(self._store_warning.line(), file=stream)
            except Exception:
                pass

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
        self._persist_event(obj)
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
            safe, data, rejected = {}, {}, False
            for key, value in metadata.items():
                if type(key) is not str or key not in ('elapsed_seconds', 'model', *TOKEN_FIELDS):
                    continue  # Never validate/stringify unknown adapter values.
                try:
                    if key == 'elapsed_seconds':
                        if (type(value) not in (int, float) or not math.isfinite(value)
                                or not 0 <= value <= d.MAX_DURATION_MS / 1000):
                            raise ValueError('invalid provider duration')
                        data['duration_ms'] = int(value * 1000)
                    elif key == 'model' and type(value) is str and value == '[redacted]':
                        d.safe_diagnostic_text(value)
                    elif key == 'model':
                        # Display validation remains independent of event-only bounds.
                        d.safe_diagnostic_text(value, d.MODEL_MAX_CHARS)
                        if re.fullmatch(r'[A-Za-z0-9_./:@-]+', value) is None:
                            raise ValueError('invalid provider model')
                        data[key] = value
                    else:
                        if value is not None and (type(value) is not int
                                                  or not 0 <= value <= d.MAX_TOKEN_COUNT):
                            raise ValueError('invalid provider token count')
                        data[key] = value
                    safe[key] = value
                except Exception:
                    rejected = True
                    self.drop()
            if rejected and not safe:
                return {}  # Preserve the existing all-invalid event-drop contract.
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
        from .errors import LLMToolError
        try:
            # Only our typed error contract can add a failed-response usage event.
            # Revalidate before observing; error code/message handling is separate.
            if type(exc) is LLMToolError and exc.provider_metadata is not None:
                self.response(dict(d.validate_provider_usage(exc.provider_metadata)))
        except Exception:
            self.drop()
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
        self.final_error_code = code
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
        writer = self._stored_run
        if writer is not None:
            try:
                from .diagnostics_summary import RunSummary
                first, last = self._events[0], self._events[-1]
                summary = RunSummary(
                    run_id=self.run_id, mode=self.mode, status=status,
                    started_at=first.timestamp, ended_at=last.timestamp,
                    duration_ms=last.data['duration_ms'], event_count=len(self._events),
                    dropped_event_count=self.dropped_event_count,
                    trace_bytes=writer.trace_bytes, provider_type=self.provider_type,
                    final_error_code=self.final_error_code,
                )
                if writer.finish(summary) is True and writer.persisted is True:
                    self.persisted = True
                    if writer.warning is not None:
                        self._persistence_failure(writer.warning, writing=True)
                else:
                    self._persistence_failure(writer.warning, writing=True)
            except Exception:
                self._persistence_failure(writing=True)
            finally:
                self.close()

    def failure_line(self, code):
        trace = ('saved' if self.persisted else
                 'unavailable' if self.persistence_requested else 'not-saved')
        return 'llmtool: code=' + code.value + ' run_id=' + (self.run_id or 'unavailable') + ' trace=' + trace

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
            print('diagnostics_persisted=' + ('true' if self.persisted else 'false'), file=stream)
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
