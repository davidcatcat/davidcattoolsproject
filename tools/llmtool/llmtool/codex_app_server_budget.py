"""Durable per-run admission ledger for App-server upstream requests and launches.

The ledger is keyed by the run, never by Request ID, connection or child process,
so reconnects, new Request IDs, automatic retries and process restarts cannot
reset admission. Admission reuses run_budget.admit_next; a request that was
reserved but never finished (crash, restart, disconnect) is read back as UNKNOWN
usage, which admit_next denies, so an UNKNOWN transport is never replayed.
"""

import fcntl
import json
import os
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import LLMToolError
from .run_budget import (BudgetCode, CallPhase, CallUsage, RunBudget,
                         UsageLedger, admit_next)

MAX_UPSTREAM_REQUESTS = 2
MAX_OUTPUT_TOKENS_PER_REQUEST = 128
MAX_WALL_CLOCK_SECONDS = 120
MAX_LAUNCHES = 8
MAX_INPUT_TOKENS = 1_000_000
_SCHEMA = 1
_RUN_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class EntryState(str, Enum):
    RESERVED = 'RESERVED'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    UNKNOWN = 'UNKNOWN'


def default_budget():
    # One initial request plus at most one retry; no repair phase is admitted.
    return RunBudget(_SCHEMA, MAX_UPSTREAM_REQUESTS, 0, MAX_WALL_CLOCK_SECONDS * 1000,
                     MAX_INPUT_TOKENS,
                     MAX_UPSTREAM_REQUESTS * MAX_OUTPUT_TOKENS_PER_REQUEST)


@dataclass(frozen=True)
class Reservation:
    sequence: int
    phase: CallPhase
    max_output_tokens: int


@dataclass(frozen=True)
class Denial:
    code: BudgetCode


class RunLedger:
    """File-backed ledger; every mutation happens under an exclusive flock."""

    def __init__(self, directory, run_id, budget=None, max_launches=MAX_LAUNCHES,
                 clock=time.time):
        if (not isinstance(run_id, str) or not 0 < len(run_id) <= 64
                or set(run_id) - _RUN_ID_CHARS):
            raise LLMToolError("app-server run id invalid")
        budget = default_budget() if budget is None else budget
        if (type(budget) is not RunBudget or type(max_launches) is not int
                or not 0 <= max_launches <= MAX_LAUNCHES):
            raise LLMToolError("app-server run budget invalid")
        # Caller-supplied budgets may tighten this synthetic route, never widen it.
        limits = default_budget()
        if any(getattr(budget, name) > getattr(limits, name) for name in (
                "max_provider_calls", "max_repair_attempts", "wall_clock_ms",
                "max_input_tokens", "max_output_tokens")):
            raise LLMToolError("app-server run budget invalid")
        self._budget = budget
        self._max_launches = max_launches
        self._clock = clock
        root = Path(directory)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path = root / (run_id + '.ledger.json')
        self._lock_path = root / (run_id + '.ledger.lock')

    # -- persistence ---------------------------------------------------------
    def _locked(self):
        handle = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
        except BaseException:
            os.close(handle)
            raise
        return handle

    @staticmethod
    def _unlock(handle):
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)

    def _load(self):
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            now = self._clock()
            return {'schema': _SCHEMA, 'started': now, 'last': now, 'launches': 0,
                    'denied': 0, 'entries': []}
        try:
            state = json.loads(raw.decode('utf-8'))
            if (type(state) is not dict or state.get('schema') != _SCHEMA
                    or type(state.get('entries')) is not list
                    or type(state.get('launches')) is not int
                    or type(state.get('denied')) is not int
                    or type(state.get('started')) not in (int, float)
                    or type(state.get('last')) not in (int, float)):
                raise ValueError
            return state
        except (UnicodeError, ValueError):
            # A corrupt ledger must deny, not silently reset admission.
            raise LLMToolError("app-server run ledger unreadable") from None

    def _store(self, state):
        encoded = json.dumps(state, separators=(',', ':')).encode('utf-8')
        descriptor, temp = tempfile.mkstemp(dir=str(self._path.parent), prefix='.ledger-')
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, str(self._path))
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def _mutate(self, change):
        handle = self._locked()
        try:
            state = self._load()
            # A clock stepping backwards must never hand back wall-clock budget.
            state['last'] = max(state['last'], self._clock())
            result = change(state)
            self._store(state)
            return result
        finally:
            self._unlock(handle)

    def _read(self):
        handle = self._locked()
        try:
            return self._load()
        finally:
            self._unlock(handle)

    # -- derived views -------------------------------------------------------
    @staticmethod
    def _elapsed_seconds(state):
        return max(0.0, state['last'] - state['started'])

    def _usage_ledger(self, state):
        calls = []
        for entry in state['entries']:
            phase = CallPhase(entry['phase'])
            if entry['state'] == EntryState.SUCCEEDED.value:
                calls.append(CallUsage(phase, entry['input_tokens'], entry['output_tokens']))
            elif entry['state'] == EntryState.FAILED.value:
                # A complete non-2xx reply: nothing was generated, but the call counts.
                calls.append(CallUsage(phase, 0, 0))
            else:
                # RESERVED (in flight, crashed or restarted) and UNKNOWN: never zero.
                calls.append(CallUsage(phase, None, None))
        return UsageLedger(tuple(calls), 0, int(self._elapsed_seconds(state) * 1000))

    def snapshot(self):
        state = self._read()
        return {
            'launches': state['launches'],
            'requests': len(state['entries']),
            'retries': sum(1 for e in state['entries'] if e['phase'] == CallPhase.RETRY.value),
            'denied': state['denied'],
            'states': [e['state'] for e in state['entries']],
            'caps': [e['max_output_tokens'] for e in state['entries']],
        }

    def remaining_seconds(self):
        """Wall-clock left for the whole run (persisted start, not this process)."""
        state = self._read()
        elapsed = max(self._elapsed_seconds(state), self._clock() - state['started'])
        return max(0.0, self._budget.wall_clock_ms / 1000 - elapsed)

    # -- launches ------------------------------------------------------------
    def reserve_launch(self):
        """Count one local App-server launch before it starts; False when denied."""
        def change(state):
            if (state['launches'] >= self._max_launches
                    or self._elapsed_seconds(state) * 1000 >= self._budget.wall_clock_ms):
                state['denied'] += 1
                return False
            state['launches'] += 1
            return True
        return self._mutate(change)

    # -- upstream requests ---------------------------------------------------
    def reserve_request(self, requested_cap=None):
        """Admit and persist one upstream request before it is forwarded."""
        def change(state):
            usage = self._usage_ledger(state)
            phase = CallPhase.PRIMARY if not state['entries'] else CallPhase.RETRY
            code = admit_next(self._budget, usage, phase)
            if code is not BudgetCode.WITHIN_BUDGET:
                state['denied'] += 1
                return Denial(code)
            used = sum(call.output_tokens for call in usage.calls)
            cap = min(MAX_OUTPUT_TOKENS_PER_REQUEST, self._budget.max_output_tokens - used)
            if type(requested_cap) is int and requested_cap > 0:
                cap = min(cap, requested_cap)
            if cap <= 0:
                state['denied'] += 1
                return Denial(BudgetCode.TOKEN_BUDGET_EXCEEDED)
            sequence = len(state['entries'])
            state['entries'].append({
                'sequence': sequence, 'phase': phase.value,
                'state': EntryState.RESERVED.value, 'max_output_tokens': cap,
                'input_tokens': None, 'output_tokens': None,
            })
            return Reservation(sequence, phase, cap)
        return self._mutate(change)

    def _finish(self, reservation, new_state, input_tokens=None, output_tokens=None):
        def change(state):
            entry = state['entries'][reservation.sequence]
            if entry['state'] != EntryState.RESERVED.value:
                return False        # a finished entry is never rewritten
            entry['state'] = new_state.value
            entry['input_tokens'] = input_tokens
            entry['output_tokens'] = output_tokens
            return True
        return self._mutate(change)

    def complete(self, reservation, input_tokens, output_tokens):
        if (type(input_tokens) is not int or type(output_tokens) is not int
                or input_tokens < 0 or output_tokens < 0):
            return self._finish(reservation, EntryState.UNKNOWN)
        return self._finish(reservation, EntryState.SUCCEEDED, input_tokens, output_tokens)

    def fail(self, reservation):
        """A complete non-2xx reply was received; the call counts, no tokens used."""
        return self._finish(reservation, EntryState.FAILED)

    def unknown(self, reservation):
        """Transport outcome unknown; never replayed."""
        return self._finish(reservation, EntryState.UNKNOWN)
