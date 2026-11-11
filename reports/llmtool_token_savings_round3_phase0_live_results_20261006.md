# llmtool Round 3 — Company-only Phase-0 live results (2026-10-06)

**Phase-0 verdict: INDETERMINATE (complete token accounting unavailable). PHASE0_GATE_PASS = NO.**

The format-compliance gate definitively failed: L03-1 missed the required 7/7 target recall and L02-2 returned MODEL_OUTPUT_INCOMPLETE. The frozen wrapper completed all six logical slots, emitted three physical requests, and globally skipped the remaining slots after consumed usage became unknown. No paired Codex benchmark is authorized by this result.

## Execution provenance

- Repository: `davidcatcat/davidcattoolsproject`.
- Canonical Mac path: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`.
- Exact execution HEAD / current offline-tested HEAD: `83f552005899f2ff931324e058c28cf814b6823b`.
- Reviewed implementation / exact tested implementation head: `3325f75e954a4099e31c5936412307320974cb4f`.
- Reviewed handoff revision: `7569a8f0f9dc175bcf56229d590e950c9cbdee6e`; final execution review: PASS.
- Literal manifest pin: `37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`.
- VPN manually confirmed: **YES**, explicitly by the operator before execution. No smoke inference preceded L02-1.
- One serial Phase0Wrapper instance; exact reviewed handoff driver executed unchanged.
- Private raw directory: `/Users/davidsun/.llmtool-round3-phase0-live/37f48c17-run1`, newly created with mode 0700, outside the repository.
- Report evidence: only `phase0_state.json` and its TrialRecord metadata. Raw Company stdout was not read or quoted for reporting.
- Private state SHA-256: `e65c0cec14b52ad933b4f36fa44a8e23d06905000a5f3f5e92c1c411694d66e9`.
- Driver exit: `0`; `run_status=COMPLETED`, `next_trial=null`, `halted=false`, `error_type=null`. Driver success does not mean Phase-0 gate success.

## Required preflight

- Fetch completed; execution HEAD equaled the remote branch and the requested HEAD.
- Canonical root/branch, tracked-clean state, report-only diff from base, unchanged benchmark implementation since reviewed implementation, and literal manifest digest checks: PASS.
- `/usr/bin/python3`: Python 3.9.6.
- `/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py`: 88/88 PASS; FAIL 0, ERROR 0, SKIP 0; exit 0.
- `/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py`: 94/94 PASS; FAIL 0, ERROR 0, SKIP 0; exit 0.
- `git diff --check`: PASS.
- Exact handoff sealed-loader check: `PINNED_MANIFEST_OK`, with the literal reviewed pin; no manifest regeneration.

## Logical trial results

| Logical trial | Status | request_emitted | usage_total | primary_failure_reason | secondary_diagnostics |
|---|---|---|---:|---|---|
| L02-1 | PASS | true | 31399 | PASS | NONE |
| L03-1 | FAIL | true | 54166 | TARGET_RECALL_BELOW_7_OF_7 | TARGET_RECALL_BELOW_7_OF_7 |
| L02-2 | FAIL | true | UNKNOWN / null | MODEL_OUTPUT_INCOMPLETE | MODEL_OUTPUT_INCOMPLETE, COMPANY_TOKEN_ACCOUNTING_UNKNOWN |
| L03-2 | NOT_STARTED | false | UNKNOWN / null | COMPANY_TOKEN_ACCOUNTING_UNKNOWN | NONE |
| L02-3 | NOT_STARTED | false | UNKNOWN / null | COMPANY_TOKEN_ACCOUNTING_UNKNOWN | NONE |
| L03-3 | NOT_STARTED | false | UNKNOWN / null | COMPANY_TOKEN_ACCOUNTING_UNKNOWN | NONE |

L03-1 proves target recall was below 7/7; the exact recall numerator is not available in the allowed TrialRecord metadata and is not inferred here. L02-2 was a consumed/possibly-consumed request with unavailable usage: null remains unknown, never zero. Global accounting-unknown admission took precedence for the final three NOT_STARTED records. Those slots were not replacement requests or retries.

## Request and output evidence digests

Every emitted request proof recorded model `gw`, temperature `0`, max_tokens `4096`, enable_thinking `false`, Authorization absent, and observer_changed_request `false`. The reviewed admission path verified the pinned body/user-message identity, exact field set, POST method, and endpoint before delegation.

Endpoint: `http://192.0.2.10:4000/v1/chat/completions`. Each invocation used the frozen isolated temporary HOME/config; real user config was not modified.

### L02-1

- Request proof SHA-256 (exact stored request_proof_json UTF-8 bytes): `b9e243d21cdacd0abf5575c2970da23f0aa7fbe989e1fe0d1adab7d75a45324e`.
- Request body SHA-256: `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`.
- Evaluation SHA-256: `e0668b133a264fe7e59317fe4dfba5fbd63c1e18e34f265b7be1e8594469a601`.
- Raw stdout SHA-256: `082a3cee5e5b97e112dc03fd1067d6f412824749461206fa682c8786def473f2`.

### L03-1

- Request proof SHA-256 (exact stored request_proof_json UTF-8 bytes): `4e22524f9d408dcbfa209ad5c853176bcf3aef8df70d362ad71a8cb52d695be2`.
- Request body SHA-256: `6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0`.
- Evaluation SHA-256: `f27f2d0bf3c42e4dac422fef8f90e3ec980cca0d2bd495cb50163558b9669e3b`.
- Raw stdout SHA-256: `66173d2092abf0298aed8d71d76f24d068d0f5b2f4b561da9f8cd2c503d7e28f`.

### L02-2

- Request proof SHA-256 (exact stored request_proof_json UTF-8 bytes): `b9e243d21cdacd0abf5575c2970da23f0aa7fbe989e1fe0d1adab7d75a45324e`.
- Request body SHA-256: `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`.
- Evaluation SHA-256: `NOT AVAILABLE / null`.
- Raw stdout SHA-256: `NOT AVAILABLE / null`.

### L03-2

- Request proof SHA-256 (exact stored request_proof_json UTF-8 bytes): `NOT AVAILABLE / null`.
- Request body SHA-256: `NOT AVAILABLE / null`.
- Evaluation SHA-256: `NOT AVAILABLE / null`.
- Raw stdout SHA-256: `NOT AVAILABLE / null`.

### L02-3

- Request proof SHA-256 (exact stored request_proof_json UTF-8 bytes): `NOT AVAILABLE / null`.
- Request body SHA-256: `NOT AVAILABLE / null`.
- Evaluation SHA-256: `NOT AVAILABLE / null`.
- Raw stdout SHA-256: `NOT AVAILABLE / null`.

### L03-3

- Request proof SHA-256 (exact stored request_proof_json UTF-8 bytes): `NOT AVAILABLE / null`.
- Request body SHA-256: `NOT AVAILABLE / null`.
- Evaluation SHA-256: `NOT AVAILABLE / null`.
- Raw stdout SHA-256: `NOT AVAILABLE / null`.

Only digests and metadata are published. The wrapper performed its reviewed raw-stdout scan, evaluation, and replay reconciliation for the two output-bearing trials. No raw Company response, private state file, temporary file, or credential is included in this commit.

## Accounting and verdict

```text
Logical records: 6
Physical Company request count: 3
Retry count: 0
known_cumulative: 85565
complete_total: UNKNOWN (state null)
gate_passed: false
Integrity halt: NO
Accounting: INCOMPLETE / UNKNOWN
Phase-0 verdict: INDETERMINATE
Format-compliance gate: FAIL
PHASE0_GATE_PASS: NO
Company live calls: 3
Codex benchmark calls: 0
```

The known subtotal is 31,399 + 54,166 = 85,565 tokens from two requests with reported usage. It excludes the third emitted request and is not a complete consumption total. Compliance with the 300,000 actual-total ceiling cannot be established from an unknown complete_total; admission retained the frozen 35,000/59,000 pre-request bounds. No unknown usage was guessed, imputed, or converted to zero.

The PASS condition is unmet: only one record passed, two failed, three did not start, and complete accounting is unavailable. Unknown consumption determines the overall INDETERMINATE label; independently, the recorded format/recall failures conclusively reject the Phase-0 gate.

## Scope and stop

- No retry, fallback, max_tokens increase, prompt/config change, or second run.
- No wrapper, instructions, preregistration, fixture, gold, production, runtime, or skill changes.
- Existing unrelated untracked artifacts preserved; tracked files were clean before report creation.
- Only this sanitized results report is authorized for staging/commit/push to Draft PR #38.
- STOP after report publication. No resume, run2, paired Codex benchmark, merge, A11b.2, or A11c.

Further diagnosis or a revised experiment requires a separate explicit decision; this report neither repairs Company output nor changes frozen trial verdicts.
