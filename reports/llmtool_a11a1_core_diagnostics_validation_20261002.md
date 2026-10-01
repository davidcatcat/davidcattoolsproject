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


## External review remediation — 2026-10-02

This section supersedes the original implementation's pending-review status, not
its historical validation evidence. The [independent Claude review](llmtool_a11a1_core_diagnostics_review_20261002.md)
returned **REQUEST CHANGES**. Its report is unchanged. Reviewed implementation
HEAD was `9712bf3818e024e2d49ce0ca18bb5ad44f6cacde`; historical Actions run
[36925256634](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/36925256634)
passed Python 3.9 but **FAILED Python 3.14** in Offline canonical checks at
`test_timestamp_requires_exact_format_and_real_date`, for
`2026-10-02T24:00:00.000Z`. The original local results did not establish remote CI
success and are not relabeled as such.

Preflight: canonical root and existing branch verified; fetched origin/main is
`624c33c6f7850ea281bce9fe0ef9a806d89e4548`, including the required base commit.
Starting branch HEAD was `7522d5bd78fc00f82a60b4623bfe8c99018836a0`, identical to
its remote, three commits ahead of main and zero behind. No tracked modifications
or conflicts were present. Known unrelated untracked reports were preserved.

Production fix commit: `274b689fb045797de423e343e966b2337c83cc94`
(`fix(llmtool): address A11a.1 external review findings`). Subsequent report/roadmap
commit(s) do not change tested production bytes; exact final HEAD and its CI gates
are recorded in the final delivery checkpoint. Remediation completion requires
exact-head Python 3.9 and 3.14, Offline canonical checks and clean-tree PASS; local
results alone cannot satisfy that gate.

### Review disposition

| Finding | Disposition | Decision and evidence |
| --- | --- | --- |
| R1 — version-dependent timestamp parsing | ACCEPTED / FIXED | Grammar explicitly restricts hour 00–23, minute/second 00–59, exactly three fractional digits and final Z. datetime checks calendar dates only after grammar acceptance. Tests reject 24/25 hours, minute/second 60 without even calling the calendar parser; 23:59:59.999 and a valid leap day pass, invalid dates fail. The original failing regression remains in the suite. |
| R2 — credential false positives | ACCEPTED / FIXED | Bare authorization vocabulary is allowed. Bearer/Authorization Basic require token-shaped value evidence (at least eight characters), excluding fixed status tokens. Credential assignments reject non-status RHS values, including short passwords. Quoted/unquoted missing, unset, redacted, none, null, not configured and placeholder states are accepted. OpenAI/AWS/private-key markers remain rejected. Positive and negative fixtures verify fixed rejection messages never echo credential markers. |
| R3 — model ID / collection failure boundary | ACCEPTED WITH BOUNDARY | Model IDs now permit @ in bounded ASCII `A-Za-z0-9._:/@-`, retaining the initial alphanumeric rule and 128-character limit. Dot/dot-dot path segments, whitespace, controls and recognizable credentials fail. Claude @version, Qwen, OpenAI and Codex fixtures pass. Diagnostics-only failure containment is required of A11a.2, not implemented in this foundation. |
| R4 — event invariants | ACCEPTED / FIXED | error requires ERROR, warning requires WARNING; both require data containing code and message. run.start/provider.request/provider.response require INFO. Invalid combinations and absent/empty/partial data are explicitly tested. run.end status-to-severity mapping is deferred to lifecycle policy rather than guessed. |
| R5 — coded pickle/deepcopy | DEFERRED BY SCOPE | MappingProxyType representation is retained. Coded DiagnosticError/DiagnosticEvent/LLMToolError pickle/deepcopy are NOT SUPPORTED. JSON remains the supported transfer format; no multiprocessing/cross-process transport requirement exists. Legacy uncoded exception pickle compatibility test is retained and passes. Existing production paths still raise uncoded errors; no direct production dependency on coded pickle/deepcopy was found. |
| R6 — report completeness | ACCEPTED / DOCUMENTATION FIX | This append preserves failed CI and original local evidence, records all dispositions and explicitly defers persisted RunSummary to A11a.3. |

### Required future lifecycle boundary (not implemented)

**Diagnostic collection failure must never fail the primary read-only command.**
A11a.1 strict constructors may raise ValueError for programmer/invalid diagnostic
input. A11a.2 lifecycle must catch diagnostics-only construction/validation
failures, drop the affected event, increment future dropped-event accounting and
emit at most one fixed `DIAGNOSTICS_EVENT_DROPPED` warning while primary `ask`
continues with unchanged result/provider-call semantics. No RunContext/drop engine
or instrumentation is introduced here.

Persisted **RunSummary is not implemented**. Its planned slice is
**A11a.3 — User diagnostics settings and secure POSIX store**, alongside summary
publication and persistence validation. No evidence requires moving it into
A11a.2; this remediation does not change milestone sequencing.

### Actual remediation validation

All commands launched from the canonical repository. Counts are new observations,
not reused baseline values. Production bytes were unchanged after these checks.

| Check | Python | Discovered | Passed | Failed | Errors | Skipped | Elapsed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Focused test_diagnostics | 3.13.3 | 55 | 55 | 0 | 0 | 0 | 0.118 s |
| Full canonical make check + compileall | 3.13.3 | 660 | 659 | 0 | 0 | 1 | 47.827 s |
| Full canonical make check + compileall | actual 3.9.6 (/usr/bin/python3) | 660 | 659 | 0 | 0 | 1 | 76.513 s |

The sole inherited skip remains the non-UTF-8 filename filesystem limitation in
`test_git_intel.GitFixture.test_unusual_filename_bytes_roundtrip`. Logs:
`/private/tmp/llmtool-a11a1-remediation-current.log` and
`/private/tmp/llmtool-a11a1-remediation-py39.log`; these are test artifacts, not
project checkouts or diagnostics-store files. Commands are the same canonical
commands listed above; nine new test methods raise the focused count from 46 to 55.

Full review of diagnostics.py, errors.py and test_diagnostics.py found no additional
merge blocker within this slice. Credential checks remain defense in depth, not a
secret detector for arbitrary data: callers must minimize collection. State-value
exceptions are intended for application-controlled messages. No arbitrary str/repr
or default=str serialization is added; bounded, deterministic JSON remains intact.

Legacy constructor/args/str/catching/pickle tests pass. CLI, provider, config,
A9/A10, terminal, feedback and runtime skill bytes are unchanged by remediation.
All inherited tests remain unchanged; only diagnostics fixtures were corrected to
supply valid event data/severity under the stricter contract, with explicit
negative tests for the old invalid combinations.

Security/side-effect tests still trap filesystem/network/provider/process calls
and assert empty stdout/stderr; AST review finds no execution or persistence.
No store, trace, runtime logs, provider calls, live/VPN tests, retry/fallback,
CLI integration or A11a.2 implementation was added. GitHub CI queries are repository
validation, not live provider tests. `git diff --check` passed.

Changed files in this remediation: diagnostics.py, errors.py, test_diagnostics.py,
ROADMAP.md and this report. Claude's review and unrelated reports remain unchanged.
External re-review remains required; A11a is not DONE or merged.
**A11a.2 NOT STARTED.**
