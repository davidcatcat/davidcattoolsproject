# A11a Observability & Diagnostics — implementation spec (2026-10-02)

> Documentation checkpoint — 2026-10-04: the original status, implementation facts
> and recommended next A11a.1 checkpoint below describe 2026-10-02. A11a.1–4 have
> since merged; PR #19 merged A11a.4 as `2bade6aa95963d3f2c3d9332aa3939407512da7e`.
> Overall A11a is incomplete. This docs-only refinement does not start/resume
> A11a.5 or change sections 1–20, event schemas or implementation slice boundaries.
> Future Task Contract/cheap-first design is recorded in the additive appendix
> below and in the current ROADMAP/WORKFLOW; it is not A11a runtime functionality.

## Status and scope

Repository: `davidcatcat/davidcattoolsproject`.

Canonical base: `main` at merge commit
`c9b1945aae3aa0c4801275f9fd53b927c2afd676` (PR #13).

This document is a technical implementation specification only. It does **not**
implement A11a, change production/test/package bytes, enable persistence, add
retry/fallback behavior, or authorize execution. A11a remains **NOT STARTED /
NEXT** until a production implementation branch is created and validated.

The spec is subordinate to `AGENTS.md`, `CLAUDE.md`,
`tools/llmtool/README.md`, and `tools/llmtool/ROADMAP.md`.

## Current implementation facts

The design below is based on the current code, not only the roadmap.

- `cli.py` parses `ask` with the existing `--verbose` flag, resolves config,
  reads input, creates exactly one provider client, calls `complete()`, writes
  final content to stdout, and writes provider metadata to stderr only in verbose
  mode.
- User-facing expected failures use `LLMToolError`; its current class contains
  only a safe display string. The top-level CLI maps it to exit 1.
- Existing exit behavior includes 0 success, 1 expected/general error, 2 argparse
  usage error, 130 interrupt, plus existing command-specific behavior such as
  BrokenPipe/chat EOF handling.
- `Reply.metadata` is already intentionally narrow. HTTP providers retain only
  bounded model/elapsed/token metadata; Claude/Codex CLI retain bounded
  model/elapsed metadata. Raw HTTP response bodies and CLI stderr are deliberately
  omitted.
- User config is trusted relative to project config. Project config can override
  only existing model/timeout/generation fields and cannot create provider
  profiles. No diagnostics fields exist today.
- `configure.py` reconstructs the persisted provider/generation config, so a
  future user-level diagnostics mapping must be explicitly preserved or it would
  be lost during configure.
- `feedback_files.py` provides the closest existing model for secure explicit
  writes: anchored directory descriptors, no-follow operations, bounded files,
  atomic publication/replacement, and private permissions.
- A9 orchestration and A10 retrieval remain bounded/fail-fast. A11a must not add
  retries, fallback calls, shell/file/Git mutation, or extra provider requests.

## Architectural decision summary

A11a is implemented in small reviewable slices. The first instrumented flow is
`ask`. Diagnostics are in-memory by default. Persistence is metadata-only and
requires explicit user-level or CLI opt-in. `LLMTOOL_HOME` chooses a location;
it never enables persistence by itself.

The first persistent-store backend is POSIX-only because its required safety
properties depend on directory-relative no-follow operations and advisory file
locks. On Windows, ephemeral diagnostics remain available, but persistence opt-in
must fail closed until an equivalent secure backend is implemented and tested.

No shared `logs/llmtool.log` is implemented in the first A11a slice. Per-run
metadata is the initial persistence primitive. A shared operational log may be
added later only after a separate concurrency/redaction review.

## 1. First instrumented flow: `ask`

### Decision

Instrument the normal `ask` path, including an optional existing runtime skill.
Do not instrument `analyze-log`, `context-pack`, A9 `orchestrate`, or A10
`retrieve-context` in the first slice.

### Why `ask`

- It has one provider request and therefore makes provider-call-count regression
  easy to prove.
- It already has `--verbose`, so normal/verbose/debug compatibility can be
  defined without inventing a second output model.
- It does not have `context-pack` JSON/schema transformation or
  `analyze-log` suppression semantics.
- It does not introduce A9/A10 multi-stage provider behavior.

### Lifecycle seam

After argparse successfully recognizes `ask`, create an in-memory run context
before input/config/provider work. Argparse usage errors remain outside A11a v1
and keep exit 2 with no run ID.

The lifecycle is:

```text
argparse success
  -> create ephemeral RunContext
  -> input/config readiness
  -> provider request start
  -> provider result/error
  -> stdout/stderr rendering
  -> run end
```

The run context is observational only. Creating it must perform no filesystem,
network, credential, or provider access.

### Compatibility

Default `llmtool ask ...` success keeps stdout byte-for-byte compatible and adds
no success stderr. The provider is still called exactly once.

## 2. Core diagnostics data model

Implement a small module, tentatively `llmtool/diagnostics.py`, with immutable
typed values.

### IDs

- `run_id`: 128 random bits from `secrets.token_hex(16)`, 32 lowercase hex.
- `span_id`: 64 random bits from `secrets.token_hex(8)`, 16 lowercase hex.
- `event_id`: positive per-run sequence integer, allocated under the run lock.
- IDs are opaque. They never encode hostname, path, PID, username, time, provider,
  or repository identity.
- A persisted run directory is created with no-clobber semantics. Collision causes
  regeneration; after three unexpected collisions persistence fails safely.

### Timestamp

Serialized timestamps use UTC RFC 3339 with millisecond precision and `Z`.
Durations are measured with a monotonic clock and serialized as bounded integer
milliseconds.

### Event shape

Version-1 event JSON:

```json
{
  "schema": "llmtool.diagnostics.event",
  "schema_version": 1,
  "run_id": "32-lower-hex",
  "event_id": 1,
  "timestamp": "2026-10-02T00:00:00.000Z",
  "type": "provider.request",
  "component": "provider.openai-compatible",
  "severity": "info",
  "span_id": "16-lower-hex",
  "parent_span_id": null,
  "data": {}
}
```

`data` is event-type-specific and allowlisted. It is never a dump of an
exception, provider response, request body, environment, argv, prompt, source
file, or arbitrary Python object.

Initial actual events for `ask`:

- `run.start`
- `provider.request`
- `provider.response`
- `warning`
- `error`
- `run.end`

Names such as `retry`, `fallback`, `tool.exec`, and `approval.*` may be
reserved in the schema registry but A11a must not emit them unless such behavior
actually exists. A11a does not add that behavior.

### Run summary

Version-1 summary fields:

- schema / schema_version
- run_id
- command (static command name only)
- mode: normal / verbose / debug
- status: success / error / interrupted / output_closed / incomplete
- started_at / ended_at
- duration_ms
- persisted: true
- provider_type when known
- final_error_code or null
- event_count
- dropped_event_count
- trace_bytes

No task text, prompt, response, cwd, repository path, base URL, environment, or
credential material is allowed.

## 3. Structured error model

Extend diagnostics without breaking the current `LLMToolError` display contract.

### Error object

```text
code
message
component
severity
retryable
recoverable
fallback_allowed
user_action_required
safe_cause
```

`message` remains application-controlled text safe for users. `safe_cause` is
a closed mapping of safe scalar metadata such as HTTP status or child exit status;
it is never `str(exception)` from an unknown exception.

### Integration with `LLMToolError`

`LLMToolError` remains the expected user-facing base exception. Add optional
structured diagnostic attributes while preserving `str(exc)` exactly. Existing
untouched raise sites therefore keep current behavior.

Boundary helpers may add a default code/component to an uncoded
`LLMToolError` without parsing its text. Provider implementations touched by
A11a should raise coded errors directly for expected provider failures.

Never classify by substring matching an exception message.

### Initial A11a codes

Only codes needed by the first `ask` integration are implemented:

- `USER_INPUT_ERROR`
- `CONFIG_INVALID`
- `CONFIG_PROVIDER_NOT_READY`
- `AUTH_CREDENTIAL_MISSING`
- `AUTH_PROVIDER_REJECTED`
- `ENV_PROVIDER_RUNTIME_MISSING`
- `NETWORK_PROVIDER_UNREACHABLE`
- `PROVIDER_TIMEOUT`
- `PROVIDER_RATE_LIMITED`
- `PROVIDER_HTTP_ERROR`
- `PROVIDER_RUNTIME_ERROR`
- `MODEL_RESPONSE_INVALID`
- `MODEL_OUTPUT_INCOMPLETE`
- `MODEL_RESPONSE_TOO_LARGE`
- `LOCAL_IO_ERROR`
- `INTERNAL_UNEXPECTED`

Diagnostics-system warnings use a separate namespace:

- `DIAGNOSTICS_STORE_UNAVAILABLE`
- `DIAGNOSTICS_EVENT_DROPPED`

Do not pre-create the full roadmap taxonomy.

### Recovery flags

A11a does not execute recovery.

- timeout/rate-limit/network errors may have `retryable=true` as descriptive
  metadata.
- `fallback_allowed` is **false in A11a** because no explicit workflow fallback
  policy is active.
- auth/config errors set `user_action_required=true` as appropriate.
- no flag triggers a provider call.

## 4. Schema versioning and readers

Each persisted event and summary has both a stable schema name and integer
`schema_version`.

Version 1 rules:

- Unknown extra fields in a supported version are ignored by readers.
- Required v1 fields must exist and pass strict type/bound validation.
- Unknown event `type` in schema v1 can be displayed generically as metadata,
  but must never be interpreted as an executable action.
- A schema version greater than the maximum supported version returns a safe
  `FORMAT_UNSUPPORTED`-style diagnostics-reader error and does not guess.
- Writers use deterministic JSON: UTF-8, `ensure_ascii=True`,
  `allow_nan=False`, stable key ordering, compact JSONL for events.
- Readers reject duplicate JSON keys, non-standard numeric constants, unsupported
  controls, over-depth values, and over-limit files.

## 5. Event and field bounds

Initial hard bounds:

- one serialized event: 16 KiB maximum
- one diagnostic text/message field: 2 KiB UTF-8 maximum
- stable error message: 1 KiB maximum
- safe cause mapping: 4 KiB serialized maximum
- provider metadata mapping: 4 KiB serialized maximum
- debug stack summary: at most 16 frames
- module/function identifier: 128 ASCII characters each
- model identifier: existing provider validation bound, at most 128 characters

Events are built from allowlisted fields first. There is no general “serialize
then regex secrets” path.

If an otherwise valid event would exceed its bound:

1. truncate only fields explicitly documented as truncatable;
2. set `truncated=true`;
3. if still too large, drop the event and increment
   `dropped_event_count`;
4. emit at most one minimal `DIAGNOSTICS_EVENT_DROPPED` warning per run.

The warning itself has a fixed application-controlled payload and cannot recurse.

## 6. normal / verbose / debug contract

### normal

Success:

- stdout remains current command output exactly;
- no diagnostics success stderr by default.

Expected failure:

- preserve the current first stderr line:
  `llmtool: <existing safe message>`
- append one bounded stable line:
  `llmtool: code=<CODE> run_id=<RUN_ID> trace=<not-saved|saved|unavailable>`
- exit status remains the existing status.

If persistence was not enabled, do not suggest `llmtool trace` as if a saved
trace exists.

### verbose

Existing `--verbose` stays supported and keeps current provider metadata.
A11a may append safe diagnostics fields after existing metadata:

- `run_id`
- `provider_type`
- `diagnostics_persisted=true|false`

No prompt/response/body is added.

### debug

Add `--debug` to an instrumented command. It implies verbose diagnostics but
does not imply persistence.

Debug may emit safe event summaries and, for unexpected internal failures only, a
sanitized stack summary consisting of package-relative module/function/line
metadata. It never emits locals, object reprs, host absolute paths, raw exception
strings, provider bodies, raw CLI stderr, prompt, response, credentials, or
reasoning.

`--verbose --debug` is accepted; debug is the more detailed mode.

## 7. Persistence opt-in contract

Persistence is independent from display mode.

### CLI

For instrumented commands add a mutually exclusive pair:

- `--save-diagnostics`
- `--no-save-diagnostics`

Default is unspecified at argparse level so trusted user config can apply.

### User config

Add a **user-only** mapping:

```yaml
diagnostics:
  persist_metadata: false
  max_runs: 100
  max_total_bytes: 67108864
  max_age_days: 14
  max_run_bytes: 1048576
```

Hard accepted ranges:

- `max_runs`: 1..1000
- `max_total_bytes`: 1048576..536870912
- `max_age_days`: 1..365
- `max_run_bytes`: 65536..8388608 and not greater than max_total_bytes

Precedence:

```text
explicit CLI save/no-save
    > trusted user diagnostics.persist_metadata
    > false
```

No project diagnostics fields are accepted. Project `.llmtool.yaml` cannot
enable persistence, change the diagnostics home, increase retention, or change
redaction.

No generic environment variable enables persistence.

### Preserve existing config output

A11a must not silently alter the existing `config show` output contract.
Implement diagnostics settings through a dedicated
`resolve_diagnostics_settings()` path reading the same trusted user file while
keeping the legacy model/provider resolved object unchanged.

The strict user-config parser must recognize and validate the top-level
`diagnostics` mapping, but `show_config()` continues to show the existing
provider/generation view in A11a.

`configure.py` must preserve an existing validated `diagnostics` mapping
unchanged when it updates provider configuration. The configure wizard does not
prompt for diagnostics in A11a.

## 8. `LLMTOOL_HOME` resolver

`LLMTOOL_HOME` chooses the diagnostics location only. It does not enable
persistence.

Rules:

1. If `LLMTOOL_HOME` exists, it must be an absolute, control-free path. No shell
   expansion or interpolation is performed.
2. Otherwise:
   - macOS: `~/Library/Application Support/llmtool`
   - Linux: `$XDG_STATE_HOME/llmtool` when XDG_STATE_HOME is an absolute valid
     path; otherwise `~/.local/state/llmtool`
   - Windows: resolve `%LOCALAPPDATA%\\llmtool` for reporting only in the A11a
     MVP.
3. Persisted diagnostics backend support in the first A11a release is POSIX
   (macOS/Linux) only.
4. On Windows or an unsupported platform, normal in-memory diagnostics still
   work. A persistence request must reject the unsupported store backend before
   provider I/O, emit one bounded `DIAGNOSTICS_STORE_UNAVAILABLE` warning, then
   continue the read-only `ask` exactly once without persistence under the store
   failure policy in section 11. It must never fall back to a weaker/insecure
   storage backend. Merely setting `LLMTOOL_HOME` does not fail or perform I/O.
5. `runs`, `trace`, and `doctor` resolve the same location but do not create
   it just by being invoked.

The path itself is not included in normal/verbose persisted metadata.

## 9. Secure store design

Initial layout:

```text
LLMTOOL_HOME/
├── .store.lock
└── runs/
    └── <run_id>/
        ├── .run.lock
        ├── .reservation
        ├── trace.partial.jsonl
        ├── trace.jsonl
        └── summary.json
```

`trace.partial.jsonl` exists only while a run is active or abandoned.
`trace.jsonl` is the completed published trace.

### POSIX security properties

- store/run directories created mode 0700
- files created mode 0600
- existing store/run directories must be owned by the effective user and have no
  group/other permission bits; A11a does not silently chmod an unsafe existing store
- reject symlink components and non-directory path components
- all traversal after the trusted anchor uses directory-relative no-follow
  operations
- no parent traversal
- no broad `Path.resolve()` followed by ordinary path writes
- temporary files remain in the same directory as their publication target
- completed file publication uses same-directory atomic rename/link semantics
- directory metadata is fsynced at final publication where supported

Implementation should follow the proven `feedback_files.py` pattern rather than
loosening that code. Do not perform a broad refactor of feedback/config storage in
the first A11a code slice.

### Run creation

Under the store lock:

1. enforce cleanup/capacity rules;
2. reserve `max_run_bytes`;
3. create run directory with no-clobber semantics;
4. create/lock `.run.lock`;
5. create `.reservation` containing only bounded schema/version/run reservation
   metadata;
6. create `trace.partial.jsonl`.

Run ID collision retries at most three times; then persistence becomes
unavailable for that run.

### Event writes and completion

One process owns a run. Event writes are serialized under the in-process run lock.
Each JSONL event is one bounded encoded write plus newline.

At normal completion:

1. append `run.end`;
2. flush/fsync the partial trace;
3. atomically rename it to `trace.jsonl`;
4. write `summary.json` through a private temp file + fsync + atomic rename;
5. fsync the run directory where supported;
6. release reservation and run lock.

A crash may leave `trace.partial.jsonl` and no summary. Readers label this
`incomplete`; they never treat it as a completed trace.

## 10. Store locking, crash recovery, and cleanup

Use POSIX advisory `fcntl.flock`:

- one root `.store.lock` protects cleanup/capacity/reservations;
- one `.run.lock` identifies an active writer.

Cleanup never deletes a run whose run lock cannot be acquired non-blocking.

An unlocked run without a valid completed summary is abandoned. To avoid racing a
just-starting process, abandoned runs have a fixed one-hour grace period before
automatic cleanup eligibility.

Cleanup occurs only when starting a new **persisted** run. Read commands
(`runs`, `trace`, `doctor`) are non-mutating.

Policy order:

1. remove completed runs older than `max_age_days`;
2. if completed+active run count would exceed `max_runs`, remove oldest eligible
   completed runs;
3. calculate store-owned bytes plus active reservations;
4. remove oldest eligible completed runs until current bytes + the new
   `max_run_bytes` reservation fit within `max_total_bytes`;
5. if capacity still cannot be guaranteed because active/unrelated state consumes
   the budget, disable persistence for this run rather than delete unknown data.

Cleanup considers only directories/files that pass the diagnostics-store schema
and ownership checks. Unknown/unrelated files are never deleted and count against
available capacity if they occupy the managed store.

## 11. Store failure semantics

A11a diagnostics are optional observation for a read-only flow, so diagnostics
failure never replaces the original task result.

### Store fails before provider call

If persistence was explicitly requested but the store cannot be safely opened,
locked, cleaned, or reserved:

- emit one bounded `DIAGNOSTICS_STORE_UNAVAILABLE` warning to stderr;
- continue the original `ask` without persistence;
- keep the in-memory run ID;
- perform exactly one provider request;
- never claim a trace was saved.

### Provider succeeds, later diagnostics write fails

- preserve the provider success and stdout;
- exit 0;
- emit one bounded store warning;
- report persistence as unavailable/partial only in diagnostics metadata;
- do not retry the provider.

### Provider fails and diagnostics also fail

The provider/input/config failure is primary:

1. preserve the existing user-facing error line;
2. emit the stable task error code/run ID line;
3. emit at most one secondary diagnostics warning;
4. preserve the original task exit status.

### A11b difference

A11b mutation/audit is intentionally different: before any side effect it must
prove its required audit channel is writable, otherwise it fails closed. A11a
must not prematurely apply that rule to read-only `ask`.

## 12. Output and exit compatibility

A11a preserves:

- 0 success
- 1 expected/general error
- 2 usage error
- 130 interrupt
- existing documented command-specific exceptions

No new exit-code family is introduced in A11a.

Successful stdout is unchanged. Existing JSON output schemas of other commands
are unchanged. Error classification is carried by stderr and persisted
diagnostics, not by wrapping all command outputs in a new JSON envelope.

Broken pipe remains exit 0 with no new noisy traceback. If a persisted run exists,
its status is `output_closed`.

## 13. Redaction and safe collection architecture

A11a uses **collection minimization first, redaction second**.

### Never collect into an event

- prompt / user task text
- model final response
- retrieved source contents
- request bodies
- raw response bodies
- Authorization header
- literal API key
- credential environment values
- full environment
- arbitrary argv
- raw Claude/Codex stderr
- private provider/session/auth files
- model reasoning/internal reasoning
- arbitrary exception repr / locals

### Allowlisted collection

- static command name
- run/span/event IDs
- provider implementation type
- validated provider profile identifier when explicitly needed for routing
  diagnosis
- validated model identifier
- elapsed/duration
- bounded token counts already exposed in `Reply.metadata`
- numeric HTTP status
- numeric child exit status
- stable application error code/flags
- sanitized debug stack frame metadata

### Secondary redaction

Before display or persistence, every string passes a central bounded safe-text
function that rejects unsupported controls and recognizable credential patterns.
If a supposedly safe field fails this check, drop/redact the field rather than
saving the original.

Do not implement a “capture everything then regex secrets” logger.

## 14. Provider diagnostics adapters

Adapters normalize only data the provider already safely exposes or can classify
at the failure point.

### openai-compatible

Safe metadata:

- elapsed
- validated model
- prompt/completion/total token counts when valid
- numeric HTTP status on an HTTP failure

Expected typed failure mapping:

- timeout -> `PROVIDER_TIMEOUT`
- 429 -> `PROVIDER_RATE_LIMITED`
- 401/403 -> auth/provider-rejected classification
- other HTTP -> `PROVIDER_HTTP_ERROR`
- DNS/TLS/connect class -> `NETWORK_PROVIDER_UNREACHABLE`
- malformed response/shape/tool-call/empty final -> `MODEL_RESPONSE_INVALID`
- finish_reason=length -> `MODEL_OUTPUT_INCOMPLETE`
- > response limit -> `MODEL_RESPONSE_TOO_LARGE`

No body, URL, Authorization header, prompt, or reasoning field is retained.

### OpenAI

Same policy using the existing fixed official origin and existing
`Reply.metadata`. Do not persist request `input`, response output, refusal
text separately, or reasoning objects.

### Claude CLI

Safe metadata:

- elapsed
- configured validated model
- numeric child exit status for failure classification

Do not expose or persist raw stderr, cwd, HOME, executable path, login/session
files, prompt, or stdout content as diagnostics.

### Codex CLI

Safe metadata:

- elapsed
- configured validated model
- numeric child exit status

Do not weaken the existing ephemeral cwd, environment allowlist, permission
profile, reasoning suppression, or raw-stderr suppression. Do not read Codex
session/auth files to obtain extra identifiers.

## 15. Fallback boundary

A11a implements **no retry or fallback engine**.

The schema may contain descriptive fields such as `retryable`, but:

- no second provider call is made;
- `fallback_allowed` remains false without a future explicit workflow policy;
- no internal/company data is sent to an external provider automatically;
- A9 and A10 keep their current fail-fast/provider-count semantics;
- no fallback event is emitted unless fallback actually exists in a future
  milestone.

A11b must separately implement the roadmap trust-boundary policy before any
fallback can occur.

## 16. Diagnostics CLI staged design

These commands read only persisted metadata:

```sh
llmtool runs
llmtool trace <run_id>
llmtool trace <run_id> --errors
llmtool trace <run_id> --json
llmtool doctor
```

### `runs`

- offline
- no provider/config credential resolution
- no store creation or cleanup
- list bounded summaries newest first
- invalid/unknown store entries are reported generically, never executed

### `trace`

- strict run ID syntax
- no path input
- bounded read
- `--errors` filters structured error/warning events
- `--json` returns the stable diagnostics-reader schema, not raw arbitrary JSONL
- incomplete run is labeled incomplete

### `doctor`

First version is offline only:

- reports whether diagnostics persistence is supported on this platform
- reports configured persistence policy without credential values
- checks existing store structure/permissions only when it already exists
- checks provider executable availability only by the same safe readiness contract
  already used by the application
- does not log in, read provider auth/session files, resolve literal secrets, make
  model/network requests, create the store, or perform a write probe

## 17. Implementation slices

### A11a.1 — Core models and coded errors

Production files expected:

- diagnostics models/schema helpers
- structured attributes on `LLMToolError`
- first stable error-code definitions
- serialization/bounds/redaction primitives

No persistence, no CLI behavior change, no provider call change.

### A11a.2 — Ephemeral `ask` lifecycle and output modes

- in-memory run context
- `ask --debug`
- stable normal error code/run ID line
- existing `--verbose` integration
- provider boundary adapters
- still no filesystem writes

Gate: exact stdout/exit compatibility and exactly one provider call.

### A11a.3 — User diagnostics settings and secure POSIX store

- user-only diagnostics config parser
- configure preservation
- `--save-diagnostics` / `--no-save-diagnostics`
- home resolver
- store locks/reservations
- trace/summary publication
- cleanup and failure semantics

Gate: persistence default remains off.

### A11a.4 — Persisted `ask` integration

Connect the store to the already-instrumented `ask` lifecycle. Prove store
failure does not alter the task/provider result and does not add provider calls.

### A11a.5 — `runs` / `trace`

Add strict offline readers and human/JSON rendering for stored metadata.

### A11a.6 — `doctor` and failure injection

Add offline doctor and the complete store/provider/error failure matrix.

### A11a.7 — Final A11a validation

- Python 3.9 and current CI versions
- documentation
- exact-head CI
- external review
- dated validation report
- no A11b execution/retry/fallback code

A11a is complete only after this gate is merged.

## 18. Test matrix

### Core/schema

- run ID matches exact syntax and collision retry is bounded
- span/event IDs unique within a run
- event sequence remains ordered under concurrent emission
- UTC timestamp format
- schema name/version exact
- deterministic JSON
- duplicate/unknown/unsupported-version reader handling
- NaN/Infinity rejected
- event/text/cause/metadata bounds
- oversize event drop/truncation accounting

### Error/output compatibility

- `str(LLMToolError)` unchanged for legacy callers
- current success stdout unchanged
- normal error first line unchanged
- stable second error-code/run-ID line on instrumented ask
- exit 0/1/2/130 preserved
- BrokenPipe behavior preserved
- existing verbose metadata preserved
- debug adds only stderr diagnostics
- no global JSON envelope
- usage error still occurs before run creation

### Privacy/redaction

- API key-looking values never enter event JSON
- Authorization header never enters event JSON
- configured credential env value never enters event JSON
- prompt and response markers absent from persisted bytes
- raw HTTP body absent
- raw Claude/Codex stderr absent
- unsafe terminal controls escaped/rejected
- debug stack contains no absolute host path or locals
- provider model field containing a key is rejected/redacted by existing provider
  safety before adapter collection

### Provider behavior

- HTTP timeout/error/status mapping
- 429 mapping
- malformed response mapping
- incomplete length mapping
- CLI executable missing mapping
- CLI timeout/exit status mapping
- CLI output overflow mapping
- adapter sees only allowlisted `Reply.metadata`
- provider call count is exactly one with normal/verbose/debug/save enabled
- no retry/fallback event or request occurs

### Config

- no diagnostics mapping -> persistence false
- trusted user mapping parsed with bounds
- CLI save/no-save precedence
- project config with diagnostics rejected
- `LLMTOOL_HOME` does not enable persistence
- diagnostics mapping does not change legacy `show_config()` output
- configure preserves diagnostics mapping exactly
- malformed diagnostics config fails before provider I/O

### Store/security

- new dirs 0700/files 0600
- existing wrong-owner or unsafe-mode store fails safely
- symlink at home/runs/run/file rejected
- non-directory component rejected
- run dir no-clobber
- atomic completed trace/summary publication
- concurrent run IDs/reservations
- root/run locks
- active run never cleaned
- abandoned unlocked run grace period
- count cleanup
- age cleanup
- byte cleanup
- unknown file not deleted
- active reservations included in capacity
- max-run and total-byte limits
- permission-denied simulation
- full/capacity simulation
- partial trace after crash classified incomplete
- write failure warning bounded and emitted once
- persisted read-only task result unchanged on store failure

### Platform

- macOS default resolver
- Linux XDG resolver
- invalid relative XDG fallback
- explicit absolute `LLMTOOL_HOME`
- relative/control-containing home rejected for persistence
- Windows persistence backend is rejected before provider I/O, emits one bounded
  warning, and the original read-only provider call still occurs exactly once unsaved
- ephemeral diagnostics remain available on unsupported persistence platforms

### Diagnostics CLI

- `runs` does not create or clean store
- `trace` accepts only exact run IDs
- trace path traversal impossible
- trace bounded reader
- errors filter
- JSON reader output stable
- incomplete trace labeled
- doctor performs no network/provider request
- doctor does not create/write store
- doctor does not read auth/session files

## 19. Files intentionally not modified by the spec stage

This spec stage must not change:

- `tools/llmtool/llmtool/*` production modules
- `tools/llmtool/tests/*`
- packaged runtime skills
- provider behavior
- A9/A10 behavior
- historical validation/review reports

Only this new spec and a minimal roadmap cross-reference are appropriate.

## 20. Remaining non-blocking decisions

The following are deliberately deferred because they do not block A11a.1:

- shared `logs/llmtool.log` operational log: defer until after per-run store is
  proven; requires a separate concurrency/redaction contract
- raw prompt/response retention: out of A11a; requires a separate explicit data
  retention design
- Windows persisted-store backend: deferred until equivalent no-follow/ACL/lock
  guarantees can be tested
- cross-provider retry/fallback: A11b only
- monetary budget enforcement: A11b only and only with trustworthy explicit
  pricing data

There are no unresolved design decisions that should block **A11a.1 — Core
models and coded errors**.

## Recommended next coding checkpoint

After this spec is reviewed and merged, start only:

**A11a.1 — Core models and coded errors**

Do not implement persistence, `runs`, `trace`, `doctor`, retry/fallback,
executor, approvals, A11b, A11c, or A12 in that first coding checkpoint.

## Additive architecture refinement — 2026-10-04 (planning only)

This appendix preserves the historical spec and adds no milestone, production
code, test, config, provider call or diagnostics schema. Section 17 continues to
define A11a.5 as offline runs/trace readers with human/JSON rendering, A11a.6 as
offline doctor/failure injection, and A11a.7 as final validation. A11a must not add
an executor, hooks engine, automatic routing, retry, fallback or escalation engine.
Previously started unpublished A11a.5 work is paused outside this docs-only branch;
it is not merged or independently reviewed and is not resumed by this checkpoint.

The purpose of the future workflow is to use eligible company LLM work to reduce
expensive-model consumption while preserving fixed acceptance and evidence:

- **A11b Task Contract enforcement/execution gates:** pin task ID, version/digest,
  goal, authorized scope, forbidden scope, acceptance criteria and required evidence.
  No model can modify or lower these requirements. Only user authorization creates
  a new version; preserve the old version and re-evaluate affected approvals/evidence.
- **A11c company LLM first:** prefer authorized company profiles for context
  preparation, summaries, classification, log normalization, initial planning and
  first review. Retain source/revision references, unknowns, constraints, unresolved
  issues and necessary evidence. Valid schema is not correct content. Summaries
  cannot replace the exact Task Contract, original evidence or independent review.
- **Objective escalation:** schema failure, missing evidence, validator rejection,
  test failure, review disagreement and bounded repair exhaustion are objective
  triggers, not model confidence. Escalation changes the handling/review level;
  fallback is provider recovery. Each remains subject to data export/provider trust
  policy and required authorization. Summarized company data can still be sensitive;
  denied export stops that path rather than automatically selecting an external model.
- **Verification:** completion is a claim until contract-required evidence binds
  to contract version/digest, revision/patch digest, commands, exits, environment
  and necessary artifacts. Distinguish PASS, FAIL, INCOMPLETE and NOT-TESTED;
  required non-PASS checks block VERIFIED/DONE. DONE also requires the applicable
  review/delivery/merge gates. Tests/deterministic checks have finite coverage,
  not a guarantee of semantic correctness. These are future task gates, not new
  A11a run statuses, reader schemas or persistence fields.
- **Context and cost:** separate pinned contract, compressible working context
  and structured evidence. Compression must retain restrictions, unresolved issues
  and evidence references. Context budget/cost routing is later design; future
  accounting includes preparation, retries/repair, escalation and all review.
  Unknown tokens/cost remain unknown/null, never zero; partial totals cannot prove
  savings or budget compliance. No new cost or usage collection is required of A11a.

The current design details and engineering guidance live in
[ROADMAP](../docs/history/llmtool-roadmap-archive-20261028.md#architecture-refinement--planning-only) and
[WORKFLOW](https://github.com/davidcatcat/davidcattoolsproject/blob/cf3a1c4a306b3be9b058d32b0bf7047d33727f1c/tools/llmtool/WORKFLOW.md#task-contract-company-llm-and-evidence).
Enforcement/execution gates belong to A11b; cheap-first integration, independent
verification and bounded repair belong to A11c. Neither is implemented or started
here; the existing section 15 company/external fallback boundary remains intact.
