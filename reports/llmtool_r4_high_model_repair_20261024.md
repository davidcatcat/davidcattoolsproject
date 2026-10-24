# R4 formal-review repair evidence — 2026-10-24

Status: focused offline checks PASS; fresh independent reviews, exact-head CI and
native acceptance remain pending. Gate 8 remains FAIL/UNKNOWN with 8/8 launches
exhausted. This report records implementation evidence, not acceptance or merge approval.

## Revision, task and publication binding

- Repository: `davidcatcat/davidcattoolsproject`; existing [Draft PR #314](https://github.com/davidcatcat/davidcattoolsproject/pull/314), linked to [#301 Task Contract v3](https://github.com/davidcatcat/davidcattoolsproject/issues/301#issuecomment-6380698920).
- Same Task `a11c-r4-codex-high-20261024`, sole Developer Session
  `/root/r4_high_developer`, assigned `gpt-6.1-sol`, High. Registered private D6
  root; local path withheld. PM transitioned this same tree to REPAIR/LOCKED;
  no new writer, worktree, ownership transfer or edits in the locked Claude tree.
- Branch: `feat/a11c-r4-codex-high-20261024`.
- Original task base: `cf6eb6d92100dc684d49f1f28434d4fd053f3eb6`.
- Published parent and checkout HEAD during repair validation:
  `6bebf52a9a45dc578500be03e27b85f844a430e3`. Its original 11-file candidate was
  committed/pushed and Draft PR #314 was created; earlier wording saying no
  publication occurred is superseded.
- Tests below ran with that parent HEAD plus the ten source/test modifications
  bound by the frozen manifest below. This is a pre-publication evidence snapshot:
  the enclosing repair commit / updated PR head supplies the final publication SHA.
  The report does not attempt to include its own circular commit/hash value.
- Reviewed B-min implementation SHA-256:
  `8ba9acbdaa42b2bf96de8c43af25c5572532267e919edab712ac8769a81f14ab`.
  Re-ran the PM-reviewed exact-parent baseline before source edits: exit 0,
  `PASS: read-only preflight`. Private inventory paths and contents stay local.

[Formal Claude review on parent HEAD](https://github.com/davidcatcat/davidcattoolsproject/pull/314#issuecomment-6381046305)
returned **CHANGES_REQUIRED**. The prior advisory PASS and 197/197 result apply
only to the older source manifest; neither accepts this repaired candidate.
Fresh exact-head formal Claude review is required after these changes.

## Finding disposition and focused coverage

| Finding | Repair and regression evidence | Remaining boundary |
| --- | --- | --- |
| S1 | Explicit item and notification allowlists; unknown methods/types, including non-string/unhashable types, fail closed. Reported error events abort with fixed text. Text-only items and known informational notifications remain supported. | Installed 0.163.0 event compatibility still unverified. |
| S2 / F1 | Persist actual usage as `CAP_EXCEEDED` when output exceeds the reservation. Never record it SUCCEEDED; deny further upstream admission. Proxy records `OUTPUT_CAP_EXCEEDED`; strategy raises `MODEL_RESPONSE_TOO_LARGE`. Tests cover 200 vs 128 and 5 vs a lowered cap of 4. | Reported usage is retained as observed, not silently clamped. |
| S3 | Match exact `(type, name)` identities. Reject same-name/different-kind tools, missing names and conflicting nested function names; execution-capable kinds remain forbidden even if listed. | Empty tool allowlist remains default. |
| S4 | Non-2xx replies become FAILED only after bounded, complete framing. Short fixed/chunked bodies and timeout become UNKNOWN and deny replay. Existing complete failure/redirect cases remain tested. | No redirects followed. |
| S5 | Explicit request-field allowlist before reservation/forwarding. Reject `background`, `previous_response_id`, `store`, `include` and unknown fields. Reject `Expect: 100-continue` before body read or interim success. | Native request compatibility is not inferred. |
| F2 | Sanitize home/stderr/listener/provider errors; guard cleanup and report/snapshot failures. Retain fixed phase/reason telemetry without raw exception/request contents. Tests include startup ledger shape, invalid snapshot return, setup, cleanup and observation failures. | Old Gate 8 failure data cannot be reconstructed. |
| F3 | Start settling before forwarding terminal event bytes; wait for actual framing/ledger completion. Explicit settling timeout returns 503 without new reservation/forwarding. Both timing paths are tested. | In-flight unknown transport never becomes retryable. |
| F4 | Deadline expiry before request send is known not-sent/FAILED, distinct `UPSTREAM_NOT_SENT`; after entering request send, uncertain outcomes remain UNKNOWN. | No-send count still consumes admission. |
| F5 | Validate exact ledger state/entry shape, phase, sequence, counters, finite timestamps, cap and usage consistency; malformed data is never reset. Atomic replacement fsyncs both data file and parent directory. | Cooperative local ledger, not an OS containment boundary. |
| Q1 | Rewrite stale report around published parent, repaired manifest, current results and blockers; remove the unrelated Owner-status line. | Final publication is bound by enclosing commit/PR head. |
| Q2 / Q3 | Added the missing protocol, cap, error-body, chunk-CRLF, Expect, tool-kind, settling and failure-shape cases. Changed both tests that previously accepted cap overruns; a legitimate tighter-budget exhaustion case uses 32 tokens against a cap of 32. | Offline tests do not satisfy Q4. |

## S6 / S7 and native gates

**S6: OS egress enforcement is NOT_VERIFIED.** A loopback-only proxy, synthetic
home, feature config and proxy environment are configuration controls. They do
not prevent a native child from opening another socket. This allowlist/task
provides no reviewed OS enforcement mechanism or new system authority. Source
comments and `last_report.containment.os_egress` now state this limit explicitly.
No native egress containment PASS is claimed.

**S7: complete descendant cleanup remains UNKNOWN.** Reuse the existing
`CLIProcessLifecycle` for signal ownership/cancellation and check it during pipe
operations. Owned-group observations and shutdown remain separate from full
process-tree containment. A live `setsid()` descendant regression proves the
owned group can be empty while an escaped child remains; shutdown reports UNKNOWN,
and only test-owned fixture cleanup terminates that escaped child. Runtime does
not claim authority over escaped PIDs. Even ordinary group CLEAN/SWEPT reports
carry `descendant_cleanup=UNKNOWN`; snapshots cannot prove no reparented/racing
child escaped. Process observation failure also keeps cleanup UNKNOWN while still
attempting owned-group teardown. Native descendant enforcement remains required.

**Q4 / Gate 8 remain unproven.** The [PM's actual Gate 8 attempt](https://github.com/davidcatcat/davidcattoolsproject/issues/301#issuecomment-6380917802)
failed and its failure branch did not retain per-gate telemetry. The task's
installed App-server allowance is exhausted at **8/8**. The original failing
sub-gate remains UNKNOWN; neither unit tests nor new phase/reason telemetry
retroactively establish it. Strict-config acceptance, built-in tool compatibility
and actual Codex 0.163.0 viability are not verified. This repair made **zero**
installed Codex/App-server launches and authorizes none.

## Validation and self-review

All commands ran in this same registered D6 tree using its own venv/import.
Only offline/fake Python children, pipe fixtures and loopback HTTP servers were
used. Fake ledgers do not consume the installed App-server launch allowance.

| Check | Command | Observed result |
| --- | --- | --- |
| Protocol slice, intermediate | `./.venv/bin/python -B -m unittest discover -s tests -p test_codex_app_server.py -v`, cwd `tools/llmtool` | exit 0; 36 tests, 2.387s |
| Ledger slice, intermediate | `./.venv/bin/python -B -m unittest discover -s tests -p test_codex_app_server_budget.py -v`, cwd `tools/llmtool` | exit 0; 28 tests, 0.106s |
| Proxy slice, intermediate | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_responses_proxy.py -v` | exit 0; 74 tests, 74.151s |
| Runtime slice, intermediate | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p test_codex_app_server_runtime.py -v` | exit 0; 42 tests, 52.759s |
| Combined before final malformed-shape guards, superseded | `./tools/llmtool/.venv/bin/python -B -m unittest discover -s tools/llmtool/tests -p 'test_codex_*.py' -v` | exit 0; 227 tests, 141.185s; intermediate |
| **Final combined frozen candidate** | `./.venv/bin/python -B -m unittest discover -s tests -p 'test_codex_*.py' -v`, cwd `tools/llmtool` | **exit 0; 228 tests, 140.804s; PASS** |
| Source and documentation validation | `git diff --check`; Python AST, whitespace, exact allowlist and SHA-256 manifest checks | PASS |

The final pattern selects exactly the five contracted test modules (37 protocol,
28 ledger, 45 runtime, 44 provider and 74 proxy tests). The former 197/197 result
and earlier 227/227 run are superseded for this manifest. No full suite or Actions
was requested during repair.

In a separate read-only self-review pass, checked the complete scoped source diff,
error/finalization ordering, known versus unknown usage, field/tool admission,
settling behavior, durability, cleanup limits and regression changes. That pass
found and fixed unhashable-item and malformed-snapshot edges before the final
run. No additional reproducible in-scope blocker was found; S6/S7/Q4 remain
explicit native blockers. Self-review is not formal acceptance. The original
CHANGES_REQUIRED verdict is not cleared without a fresh independent review.

Developer model/session: `gpt-6.1-sol` High / `/root/r4_high_developer`.
Actual Developer token/cost telemetry: **UNKNOWN**. No savings claim. No real
credential, external Provider, paid fallback, production enablement or D6
ownership change occurred.

## Exact changed paths and frozen hashes

Only these ten source/test files plus this report are changed. Source/test SHA-256:

| Path | SHA-256 |
| --- | --- |
| `tools/llmtool/llmtool/codex_app_server.py` | `5f43422d038b4ab123b86da3ff39a3afa3900d5dd1c7fb67626895e830920ea4` |
| `tools/llmtool/llmtool/codex_app_server_budget.py` | `29c82d48275814add83cbe1ec41d06902e05af6caa0405bb076a428a50696251` |
| `tools/llmtool/llmtool/codex_app_server_runtime.py` | `275bc5bfc28affc7bbcfb4e967f9035ba656063766273fbfbc478761e51405c2` |
| `tools/llmtool/llmtool/codex_responses_proxy.py` | `119f2746feabc56057f486948b07175d22077a5f9d18f9103b479deb3ff44c2d` |
| `tools/llmtool/llmtool/providers/codex_cli.py` | `f712d857039b008328fc69f2a4b8bd31e8709eb454c79d238191e5a1a055236e` |
| `tools/llmtool/tests/test_codex_app_server.py` | `f72dd1fcc31a0af28554d875e69b379b2be46eb59cf81429234c146c7239dba0` |
| `tools/llmtool/tests/test_codex_app_server_budget.py` | `6cb4812618acaa52f2db25a563e57ba753d587a8c17f853249fa6020f771cc53` |
| `tools/llmtool/tests/test_codex_app_server_runtime.py` | `48d57724b9730a4f383f38ef3cbed13f02d70725e02bea64843c80bd1253c566` |
| `tools/llmtool/tests/test_codex_responses_proxy.py` | `1f562417968def0bbfcd90d72a875c97f6e64947a5ddd1bdcc670de1ae70c572` |
| `tools/llmtool/tests/test_codex_cli_provider.py` | `49d4db69578ebd61bd5da8bb8bb6b34e2a151c08db31c3e4eb78f73cfb205371` |

Source/test manifest SHA-256: `6aeda99d15c07b938a9bcd7e07ee2ca14c2a08fc207cff58b31b123f5a2d2c9b`.
Encoding: lexicographically sorted UTF-8 records of relative path, NUL, lowercase
file SHA-256, newline. Report excluded to avoid a recursive hash.
