# A11a.2 — Ephemeral ask diagnostics validation — 2026-10-02

Status: implementation on feature branch; external review PENDING. Exact-head
CI is required before delivery. A11a overall is not DONE. A11a.3 NOT STARTED.

## Git and scope

- Canonical repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `feat/a11a2-ephemeral-ask-diagnostics-20261002`.
- Fetched base main: `98f9d48d8344ab1f005446fde1b77cf3e0df252f`, A11a.1 merge
  via PR #15; includes prior roadmap/spec checkpoint `624c33c`.
- Initial production/test commit: `56377f4d84b180bb85256e89577e65c0f096e384`.
- Portable Python 3.14 test fixture fix: `0eb78eeb33e2453514f26f443674b8c829a0920c`.
- Final production containment commit: `dd789770bf515ee25376bb58bca260e75e6dc1ae`.
- Final documentation/publication HEAD is the Git commit containing this report;
  its own exact-head CI must also pass. Final SHA/run are reported at delivery.
- No main merge, worktree, clone, alternate checkout, or gh command was used.
- Six known unrelated untracked benchmark/reasoning paths remain untouched.
- Claude's historical A11a.1 review/re-review reports remain unchanged.

Implementation follows merged A11a.1 source and section 17.2 of the
[A11a implementation spec](llmtool_a11a_implementation_spec_20261002.md).
ROADMAP's numbered architecture subsections are broader design topics; they are
not reinterpreted as new coding checkpoints.

## Implemented foundation integration

`diagnostics_runtime.RunContext` is a bounded, sequential, in-memory ask observer.
It retains only run ID, command/mode, monotonic start, validated metadata/events,
sequence/budget counters, final status and one-time drop-warning state. It retains
no task, answer, cwd, endpoint, credential, raw stderr/body or exception object.

Argparse succeeds first; only `args.command == 'ask'` enters the new path.
Help/usage errors create no run, produce no run ID and preserve exit 2. No
artificial spans are allocated: this slice has no nested workflow needing spans.
Accepted events receive per-run IDs 1, 2, ...; a rejected event creates no gap.
This is single-threaded ephemeral collection, not a store/locking implementation.

Success: run.start → input/config/readiness → provider.request → exactly one
complete() → provider.response → render → run.end(success).
Expected failure: error → run.end(error). Incomplete model output uses
run.end(incomplete). Interrupt ends interrupted; BrokenPipe ends output_closed.
Finish is idempotent; optional display failure does not append past run.end.

| Final status | Deterministic severity |
| --- | --- |
| success | INFO |
| output_closed | INFO |
| error | ERROR |
| interrupted | WARNING |
| incomplete | WARNING |

Core ERROR/WARNING and start/request/response severity invariants remain intact.
The ask runtime owns end-status mapping; the inert general event constructor has
not been expanded into lifecycle policy or an executor.

Bounds have one source in diagnostics.py: 16 KiB/event, 32 events/run,
64 KiB serialized event bytes/run, 4 KiB provider event metadata, existing
1 KiB error message/2 KiB diagnostic text bounds. Durations use monotonic elapsed,
bounded integer milliseconds; UTC timestamps retain strict RFC3339 milliseconds.

## Containment and metadata

Every observation method, Reply metadata access and observer initialization are secondary to ask.
Construction/adapter failure drops the event, increments dropped_event_count and
attempts at most one fixed DIAGNOSTICS_EVENT_DROPPED warning. Warning construction
failure is accounted without recursion. A post-finalization display failure is
accounted internally. Normal success never prints a drop warning.

Known metadata is selected before validation: elapsed_seconds, model, and the
existing input/output/prompt/completion/total token fields. The event uses integer
duration_ms and an evidence-based token_usage_available flag. Missing counts are
never estimated; explicit null remains null. Unknown fields are ignored without
stringification. Invalid selected metadata drops observation, not the answer.
The existing provider `[redacted]` model display marker remains usable without
pretending it is a model identifier. Request events collect type/profile only,
not the configured model or endpoint.

If entropy/observer initialization fails, primary ask still proceeds; the secondary
error line may say run_id=unavailable rather than inventing an insecure ID.
Normal generated IDs retain A11a.1's 128-random-bit/32-lowercase-hex contract.

## Output and exit contracts

- Normal success: previous final-text stdout plus newline; empty stderr.
- Expected ask error: exact legacy safe first line, then
  `llmtool: code=<CODE> run_id=<ID> trace=not-saved`; exit remains 1.
- Verbose: validated existing provider keys/order, followed by run_id,
  provider_type when available, diagnostics_persisted=false. No events/stack.
- Debug: implies verbose; emits safe event ID/type/component/severity/duration
  summaries and drop count. No prompt/response, raw bodies/stderr, reasoning,
  environment, host paths, locals or exception reprs.
- Unexpected ask exception: fixed `internal error; details omitted`,
  INTERNAL_UNEXPECTED, no traceback or exception stringification.
- KeyboardInterrupt: existing `llmtool: interrupted`, exit 130, no retry.
- BrokenPipe: existing exit 0/stdout shutdown contract.
- Non-ask command error/stdout/JSON paths are unchanged.

Sanitized internal stack summaries are deferred to implementation slice A11a.6
(failure injection); no raw Python traceback is added here.

## Coded error boundaries

Classification uses known stages, actual raise sites and numeric transport
status/type evidence. No message-substring classification is used. The
containment-safe coded_error helper preserves legacy str()/args even if typed
annotation fails; only on that failure it retains a validated enum hint so the
known site code is not replaced by an unrelated stage code.

| Evidence/site | Code |
| --- | --- |
| source combinations, empty/binary/oversize input | USER_INPUT_ERROR |
| OS input read / local output I/O | LOCAL_IO_ERROR |
| ask configuration parsing/validation | CONFIG_INVALID |
| actual provider validate_ready failure | CONFIG_PROVIDER_NOT_READY |
| configured credential environment value missing | AUTH_CREDENTIAL_MISSING |
| HTTP 401/403 | AUTH_PROVIDER_REJECTED |
| HTTP 429 | PROVIDER_RATE_LIMITED |
| other non-success HTTP | PROVIDER_HTTP_ERROR |
| timeout / wrapped timeout | PROVIDER_TIMEOUT |
| connection/DNS/TLS URLError | NETWORK_PROVIDER_UNREACHABLE |
| transport/start/runtime / child nonzero | PROVIDER_RUNTIME_ERROR |
| CLI executable missing | ENV_PROVIDER_RUNTIME_MISSING |
| response/output bound overflow | MODEL_RESPONSE_TOO_LARGE |
| invalid JSON/shape/tool/UTF-8/empty final content | MODEL_RESPONSE_INVALID |
| compatible finish_reason=length / incomplete OpenAI status | MODEL_OUTPUT_INCOMPLETE |
| unexpected ask internal exception | INTERNAL_UNEXPECTED |
| diagnostic observation omitted | DIAGNOSTICS_EVENT_DROPPED (warning only) |

HTTP status and child process status are the only numeric causes added. Timeout,
connection and rate-limit retryability is descriptive metadata. All
fallback_allowed=false; there is no retry/recovery/provider substitution engine.
DIAGNOSTICS_STORE_UNAVAILABLE remains an unused foundation reservation.

## Claude Low follow-ups F1/F2

- F1 FIXED as narrow second-layer recognition: GitHub ghp_/gho_/ghu_/ghs_/ghr_/
  github_pat_ suffix length ≥20; Slack xox[abprs]- suffix length ≥20;
  JWT-looking eyJ header ≥11, payload/signature ≥8 base64url characters;
  standalone Basic ≥8 characters, strict base64 containing user:password shape.
  Ordinary Basic authentication/status prose and short product names are allowed.
- F2 FIXED: all-star redactions, <redacted>, [redacted], redacted (including quoted
  assignment values) are allowed; actual-looking tokens remain rejected with
  fixed errors that never echo the value.
- Collection minimization still comes first. These patterns are defense in depth,
  not general secret discovery or a guarantee about arbitrary text.

## Offline validation

All commands launched in the canonical repository. Test state is mocked or
isolated; HTTP integration uses localhost fake servers, CLI tests use synthetic
executables. No real user credentials/config/session/private logs were inspected.

| Suite / interpreter | Discovered | Passed | Failed | Errors | Skipped | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| focused diagnostics/core/runtime — Python 3.13.3 | 98 | 98 | 0 | 0 | 0 | 0.796 |
| affected diagnostics/CLI/input/config/configure/four-provider integration — Python 3.13.3 | 297 | 297 | 0 | 0 | 0 | 37.182 |
| full canonical compileall + unittest — Python 3.13.3 | 705 | 704 | 0 | 0 | 1 | 55.796 |
| full canonical compileall + unittest — actual Python 3.9.6 | 705 | 704 | 0 | 0 | 1 | 80.803 |
| production-head GitHub CI — Python 3.9 | 705 | 705 | 0 | 0 | 0 | 22.151 |
| production-head GitHub CI — Python 3.14 | 705 | 705 | 0 | 0 | 0 | 21.996 |

Inherited skip: host filesystem/sandbox rejects non-UTF-8 Git fixture filename
bytes. This is not an A11a.2 skip. Initial full runs exposed six legacy ask stderr
assertions requiring only the original line. They were updated to require the
exact original first line AND the new full coded second line (not weakened to
substring checks). The final runs above are new evidence, not copied baseline.

Canonical commands:

```sh
make -C tools/llmtool check PYTHON="$PWD/tools/llmtool/.venv/bin/python"
make -C tools/llmtool check PYTHON=/usr/bin/python3
git diff --check
```

Call-count tests prove normal/verbose/debug/drop and provider-failure paths each
attempt exactly one complete(), request event precedes complete(), response event
follows it. Missing readiness/auth/input conflicts attempt zero. Fake CLI failure
cases spawn exactly one child; missing executable spawns zero. No fallback/retry.

Direct-text mock ask blocks builtins.open, os.open/mkdir/makedirs, temporary
creation, socket and subprocess calls. A guarded environment rejects any
LLMTOOL_HOME lookup. Runtime AST excludes filesystem/network/execution imports and
open/eval/exec calls. DiagnosticEvent, Reply metadata access and adapter/factory failure injections prove
primary answer/stdout/exit/call count survive, without recursive warning failure.
Privacy markers/arbitrary objects/raw exception strings are not rendered into
stderr/events. No diagnostics store, runtime log or trace file was created.
Inherited CLI-provider runtime isolation is unchanged and is not diagnostics
persistence.

## Compatibility and security review

Complete production diff reviewed. AST comparison against base after removing
only coded-error imports/raise annotations is identical for all four providers:
request body, argv, safe mode, timeout, cwd/environment, tools, reasoning handling,
and transport call flow are unchanged. Config resolve_config/resolve_profile_config
and input combination/probe/decode ASTs are unchanged. Non-ask main AST is identical
apart from the ask-only early gate. A9/A10 production modules and packaged skills
have no diff; inherited orchestration/retrieval/context/analyze-log/chat suites pass.

No new shell/Git/model-command execution, file mutation, raw stderr/body/secret
echo, environment mutation, extra provider call, retry/fallback, A11b/c or A12 code.
No live provider, gateway, VPN or paid API test was performed: NOT-TESTED by scope.
Legacy uncoded exception str/args/pickle and bounded diagnostic JSON remain tested.
Coded pickle/deepcopy remain unsupported; JSON is the transfer contract.

## GitHub CI

Historical first production run
[36959720851](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/36959720851)
for `56377f4`: 3.9 PASS (704/704); 3.14 FAIL (one new test fixture error),
clean-tree skipped for 3.14. Python 3.14 argparse color detection queried the
BrokenPipe test's bare Mock.fileno(), which returned a Mock instead of an FD.
This happened before ask lifecycle execution; production was not changed to
work around it. The fixture now uses a StringIO subclass with normal stream
semantics and write() raising BrokenPipe; the same complete CLI/exit assertion
remains. No coverage or schema was weakened.

Fixture-fix exact-head run
[36960214391](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/36960214391)
for `0eb78eeb33e2453514f26f443674b8c829a0920c`: both Python 3.9/3.14 SUCCESS,
Offline canonical checks SUCCESS, Working tree is clean SUCCESS.

Final production exact-head run
[36960532402](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/36960532402)
for `dd789770bf515ee25376bb58bca260e75e6dc1ae`: Python 3.9 SUCCESS and Python
3.14 SUCCESS; Offline canonical checks and Working tree is clean SUCCESS in
both jobs. Both logs ran 705 tests with OK (no skips on Linux), including the
BrokenPipe, Reply-metadata access containment and strict timestamp edge tests.

The final documentation/publication HEAD must also pass its own exact-head CI;
that SHA/run is verified and reported at delivery. A report cannot embed its own
Git commit hash; the commit containing this file is its publication checkpoint.
No pending CI is claimed as PASS.

## Files and remaining scope

Production: diagnostics_runtime.py (new), diagnostics.py, errors.py, cli.py,
config.py, inputs.py; providers/openai.py, openai_compatible.py, claude_cli.py,
codex_cli.py. Tests: test_diagnostics_runtime.py (new), test_cli.py, test_config.py,
test_configure.py, test_provider_integration.py, test_claude_cli_provider.py,
test_codex_cli_provider.py. Documentation: README.md, ROADMAP.md, this report.

Not implemented: diagnostics settings/home/store/files, persisted RunSummary,
trace/summary publication, locks/retention/cleanup, save flags, runs/trace/doctor,
non-ask instrumentation, debug stack, retry/fallback/recovery, executor/approval,
A11b/c/A12. A11a.3 will own user settings and secure POSIX persistence and must not
be started until authorized. External review PENDING; branch NOT merged.

**A11a.3 NOT STARTED.**
