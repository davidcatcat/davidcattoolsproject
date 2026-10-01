# A11a.1 — Core Models and Coded Errors validation (2026-10-02)

## Git checkpoint and scope

Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `feat/a11a1-diagnostics-core-20261002`.
Fetched base `origin/main`: `624c33c6f7850ea281bce9fe0ef9a806d89e4548`.
The required spec commit was verified as an ancestor (and the current main HEAD).
The feature branch was created directly from origin/main; initial ahead/behind
was 0/0, with no tracked changes or conflicts. Six known unrelated untracked
benchmark/reasoning paths were preserved.

Validated production commit: `8b2148ec15db39304f73809cac3949a83072ab32`
(`feat(llmtool): add A11a.1 diagnostics core models`). Final branch HEAD is the
subsequent report/checkpoint-only commit, obtainable with
`git log -1 --format=%H -- reports/llmtool_a11a1_core_diagnostics_validation_20261002.md`;
its exact SHA is recorded in the final delivery checkpoint. This avoids claiming
that a document can embed the hash of its own containing commit.

Changed files:

- `tools/llmtool/llmtool/diagnostics.py` — new typed foundation.
- `tools/llmtool/llmtool/errors.py` — optional typed diagnostic on LLMToolError.
- `tools/llmtool/tests/test_diagnostics.py` — 46 offline contract tests.
- `tools/llmtool/ROADMAP.md` — minimal feature-branch checkpoint/status update.
- This report.

Design follows the [implementation spec](llmtool_a11a_implementation_spec_20261002.md),
[Claude roadmap review](llmtool_a11_diagnostics_roadmap_review_20261002.md), and
[roadmap refinement validation](llmtool_a11_roadmap_refinement_validation_20261002.md).
Those historical documents remain unchanged. External implementation review is
**PENDING**; A11a is not DONE or merged.

## Implemented foundation

Closed enums: `DiagnosticSeverity` (info/warning/error), `DiagnosticMode`
(normal/verbose/debug), `ErrorCode`, and `EventType`.
Frozen values: `DiagnosticError` and `DiagnosticEvent`; input mappings are
copied and frozen, including the sole permitted nested safe_cause mapping.
`to_dict()` returns detached plain data, and `to_json()` uses deterministic
ASCII-escaped compact JSON with sorted keys and `allow_nan=False`.

`new_run_id()` uses secrets.token_hex(16); `new_span_id()` uses token_hex(8).
Validators enforce exactly 32/16 lowercase hexadecimal characters. IDs encode
no time, host, user, process, repository or provider identity. Event IDs are
positive integers, not an allocator/lifecycle. UTC timestamp formatter/validator
uses RFC3339 millisecond precision with Z and validates calendar values. Aware
input datetimes normalize to UTC; duration fields are bounded integer milliseconds,
not calculations from wall-clock timestamps.

All 18 codes from the first-ask spec are structurally supported:

```text
USER_INPUT_ERROR
CONFIG_INVALID
CONFIG_PROVIDER_NOT_READY
AUTH_CREDENTIAL_MISSING
AUTH_PROVIDER_REJECTED
ENV_PROVIDER_RUNTIME_MISSING
NETWORK_PROVIDER_UNREACHABLE
PROVIDER_TIMEOUT
PROVIDER_RATE_LIMITED
PROVIDER_HTTP_ERROR
PROVIDER_RUNTIME_ERROR
MODEL_RESPONSE_INVALID
MODEL_OUTPUT_INCOMPLETE
MODEL_RESPONSE_TOO_LARGE
LOCAL_IO_ERROR
INTERNAL_UNEXPECTED
DIAGNOSTICS_STORE_UNAVAILABLE
DIAGNOSTICS_EVENT_DROPPED
```

These definitions are accepted by DiagnosticError and error/warning event data;
no existing raise site is migrated and no message substring classification exists.
Retry/recovery flags are descriptive only. `fallback_allowed=true` is rejected
because A11a has no fallback policy/engine.

Version-1 DiagnosticEvent enforces schema `llmtool.diagnostics.event`, integer
schema_version=1, positive event ID, validated run/span/parent identities,
timestamp, typed severity and type, and bounded component syntax. Supported
inert types are run.start, run.end, provider.request, provider.response, warning,
and error. No retry/fallback/tool/approval event is accepted or executed.

Event data uses type-specific allowlists: static ask/mode, run status/duration,
selected provider/profile/model and bounded integer/null token metadata, or
coded error/message/flags/safe cause. Provider types are the four existing
implementations; profile syntax matches the current 64-character config contract.
No provider Reply/metadata adapter or automatic event collection is implemented.

## Bounds and privacy

| Value | Hard bound / policy |
| --- | --- |
| Serialized event | 16 KiB |
| Diagnostic text | 2 KiB UTF-8, nonempty single line |
| Stable error message | 1 KiB UTF-8 |
| Safe cause | 4 KiB serialized; only http_status, process_status, fixed category |
| Provider event metadata | 4 KiB serialized |
| Component / model | 128 ASCII characters, validated syntax |
| Profile | 64 ASCII characters, existing naming syntax |
| JSON structure | depth at most 3 below root, at most 64 visited values; no arrays/arbitrary objects |
| HTTP status | integer 100..599 |
| Process status | integer -2^31..2^32-1 |
| Token count | integer 0..10^12 or null |
| Duration milliseconds | integer 0..2^31-1 |
| Event sequence | integer 1..2^63-1 |

Bounds live in diagnostics.py. Stack-frame collection and its 16-frame limit are
not added prematurely; they belong to A11a.2. No truncation/drop accounting is
implemented: this core rejects overflow; lifecycle policy belongs to later slices.

Collection minimization precedes secondary validation. Safe causes reject URLs,
paths, prompts, responses, stderr, environments, arbitrary exceptions and nested
objects. General diagnostic text rejects all C0 (including LF/CR/tab), DEL/C1,
explicit bidi controls and invalid UTF-8/surrogates. Ordinary Unicode/ZWJ/ZWNJ
remain valid. This single-line diagnostic policy does not change terminal,
ContextPacket or feedback text rules.

Recognizable API-key/Bearer/Authorization/private-key/credential-assignment
patterns are rejected with fixed errors that omit input. Privacy fixtures use
SUPER_SECRET_A11A1_MARKER and objects whose str/repr raises, proving arbitrary
objects are never stringified into diagnostic JSON. Floats, including NaN and
Infinity, are rejected; there is no default=str. Validation is defense in depth,
not proof that arbitrary strings contain no secret: callers must supply only
application-controlled messages and explicitly selected metadata.

## Legacy and behavior compatibility

Existing LLMToolError positional construction retains Exception args, str,
catching behavior and legacy pickle behavior, including empty/multiple args.
Uncoded errors remain legal and diagnostic is None. Coded construction is
`LLMToolError(diagnostic=DiagnosticError(...))`; Exception.args derives its safe
message from the one structured object. Invalid/untyped diagnostics and a second
positional message are rejected. Lazy imports avoid diagnostics initialization
on every legacy raise path.

Byte diff against base confirmed CLI, config, all provider implementations,
A9/A10, terminal, feedback/feedback_files and packaged runtime skills unchanged.
All inherited tests remained unchanged. Existing CLI regressions verify output,
exit behavior, input/readiness ordering and provider request counts. No new CLI
flags, stdout/stderr lines, exit codes or JSON envelopes were introduced.

## Actual validation

All checks were launched from the canonical repository. Final production bytes
were tested before the production commit; that commit did not alter tested bytes.

| Check | Python | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Focused test_diagnostics | 3.13.3 | 46 | 46 | 0 | 0 | 0 | 0.088 s |
| Canonical make check, including compileall | 3.13.3 | 651 | 650 | 0 | 0 | 1 | 51.470 s |
| Canonical make check, including compileall | actual 3.9.6 (/usr/bin/python3) | 651 | 650 | 0 | 0 | 1 | 83.929 s |

The inherited skip in both canonical runs was
`test_git_intel.GitFixture.test_unusual_filename_bytes_roundtrip`:
`host filesystem/sandbox rejects non-UTF-8 filename bytes`. It is not a pass.
Final complete runs followed a daemon restart that interrupted an earlier run;
partial logs were not counted as successful evidence.

Commands:

```sh
tools/llmtool/.venv/bin/python -m unittest discover -s tools/llmtool/tests -p test_diagnostics.py -v
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

Focused runtime traps exercised import/reload, ID generation, timestamp creation,
model construction, coded exception use and serialization while forbidding
filesystem data I/O/writes, socket creation, provider creation and shell/process
calls. Stdout/stderr were empty. AST coverage confirms no persistence/provider/
network/execution imports or calls in the core. Canonical tests use only inherited
offline fake runtimes/loopback providers; no live provider, VPN or external network
test was performed. No diagnostics store, logs or trace files were created.

`git diff --check` and staged diff checks passed. Documentation UTF-8, Markdown
fences and local link targets were checked. Production/security diff review found
no additional execution/credential/retention scope. No package resources or
packaging configuration changed; a wheel build was not required by this checkpoint.
Final-head remote CI is not claimed by these local results.

## Intentionally deferred / limitations

A11a.2 NOT STARTED: no RunContext/lifecycle, event ordering/concurrency allocator,
ask integration, debug flag/stack collector or provider coded-error adapters.
A11a.3–A11a.7 NOT STARTED: no settings, diagnostics home, store, JSONL writes,
retention, readers, runs/trace/doctor, or final A11a gate. Schema readers and their
forward-compatible unknown-field/type handling are not part of this writer/model
checkpoint. Frozen mappings use mapping proxies; JSON is the supported transfer
format for new models, and pickle transport of coded models is not introduced.

No retry/fallback, executor, approval, A11b/A11c or A12 work. No private
Claude/Codex logs/session/auth files were read. No worktree/clone/alternate
checkout or gh command was used. The next recommended checkpoint after external
review is A11a.2, under its separate lifecycle/output/provider compatibility gate.
