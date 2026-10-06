# llmtool Round 3 — run1 offline diagnosis (2026-10-06)

**Phase-0 gate: FAIL — definitive. Accounting: INCOMPLETE / UNKNOWN.**

Frozen replay confirms L03-1 recalled 3/7 targets with clean structured provenance. Its three evidence items map only to the three fact target records; inference, hypothesis, unknown, and verify-next target records are missing from the validated evidence anchors. L02-1 passes the frozen canonical subset gate. L02-2 hit the production length-failure path; no successful output or complete usage survives for that trial. Nothing was repaired, rerun, or reclassified in the private state.

## Task Contract and provenance

- Task: Round-3 run1 offline diagnosis only, authorized by the [diagnosis handoff](llmtool_token_savings_round3_phase0_offline_diagnosis_handoff_20261006.md).
- Repository: `davidcatcat/davidcattoolsproject`.
- Canonical Mac root: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`.
- Exact diagnosis/replay HEAD: `6a62317d0b243ec3295ee90c471608b9255c8504`, equal to the fetched remote HEAD before report creation.
- Original live execution HEAD: `83f552005899f2ff931324e058c28cf814b6823b`.
- Frozen reviewed implementation: `3325f75e954a4099e31c5936412307320974cb4f`.
- Literal reviewed manifest pin: `37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`; sealed loader/artifact verification PASS.
- Python: `/usr/bin/python3`, **3.9.6**, inline diagnosis executed with `-B`.
- Read-only private input directory: `/Users/davidsun/.llmtool-round3-phase0-live/37f48c17-run1`.
- Required reading completed: handoff, original live results, coordinator review, independent results review, frozen format_probe/live_wrapper, A4 context parser, and pinned production provider.
- Scope: read existing private state/L02-1/L03-1 stdout; verify SHA before parsing; execute frozen replay; publish only counts, synthetic record IDs, stable status fields, and digests.
- Forbidden scope retained: Company/Codex benchmark requests, retry/fallback, implementation/instruction/manifest/fixture/gold/runtime/skill changes, Round 4, and merge.
- Acceptance: private hashes and both evaluation hashes match; exact mapping/counts, source-code length attribution, source-referenced ledger, conditional bounds distinguished from measurements, report-only publication.
- The handoff's older pre-diagnosis remote head `153caa0...` is historical. The user's current required head `6a62317...` contains that handoff and was verified exactly; no frozen artifacts changed.

Git fetch/push are repository synchronization/publication operations. The diagnosis itself ran under the frozen `offline_guard()`, which rejects sockets, DNS/connect attempts, provider opener delegation, and subprocess spawning. No CLI inference or benchmark executable was invoked.

## Private artifact integrity — verified before parsing

| Existing input | Bytes | SHA-256 | Result |
|---|---:|---|---|
| phase0_state.json | 3373 | `e65c0cec14b52ad933b4f36fa44a8e23d06905000a5f3f5e92c1c411694d66e9` | MATCH |
| L02-1.stdout | 4808 | `082a3cee5e5b97e112dc03fd1067d6f412824749461206fa682c8786def473f2` | MATCH |
| L03-1.stdout | 1241 | `66173d2092abf0298aed8d71d76f24d068d0f5b2f4b561da9f8cd2c503d7e28f` | MATCH |

`L02-2.stdout` is absent, as required. Its TrialRecord has null raw/evaluation hashes. No private files were written, deleted, repaired, renamed, or copied into Git.

The L03 evaluation SHA in the user's abbreviated message has 63 hex characters and omits its final `b`. This report uses the authoritative handoff's full 64-character SHA, which also equals the live TrialRecord:
`f27f2d0bf3c42e4dac422fef8f90e3ec980cca0d2bd495cb50163558b9669e3b`.
The full digest matched exactly; this is a transcription clarification, not acceptance of artifact drift.

## Frozen replay method and result

Both replay paths were run inside `live_wrapper.offline_guard()` after loading the literal pinned manifest.

1. Hash-check private state and stdout bytes against the handoff.
2. Run unchanged `live_wrapper.evaluate_raw("L03", raw)` and `live_wrapper.evaluate_raw("L02", raw)`.
3. Compare `sha256(evaluation.result_json.encode("utf-8"))` with both the handoff and the original TrialRecord; verify the raw stdout SHA as well.
4. For L03, use unchanged `parse_context_packet`, `parse_l03_fixture`, and the full-fixture `build_l03_multimap`; exact source+locator matching must yield exactly one record per evidence item.
5. Emit only the explicit sanitized field allowlist. Never emit raw statement/source/locator, category text, canonical line text, or packet JSON.
6. Verify pinned production source and recompute committed ledger usage from individual attempts/sessions.

| Replay | Evaluation SHA-256 | Result |
|---|---|---|
| L03-1 | `f27f2d0bf3c42e4dac422fef8f90e3ec980cca0d2bd495cb50163558b9669e3b` | MATCH |
| L02-1 | `e0668b133a264fe7e59317fe4dfba5fbd63c1e18e34f265b7be1e8594469a601` | MATCH |

Replay exit: **0**. Secret/publication scan passed for both immutable raw stdout inputs. The result_json hashes reproduce the original live evaluations; no trial state or output was mutated.

## L03-1 sanitized diagnosis

| Metric | Value |
|---|---|
| stage | phase0_l03_target_recall |
| parser_passed | true |
| evidence_item_count | 3 |
| target recall | 3/7 |
| recalled target IDs | REC-F01, REC-F02, REC-F03 |
| missing target IDs | REC-H01, REC-I01, REC-U01, REC-V01 |
| validated unique anchor IDs | REC-F01, REC-F02, REC-F03 |
| unknown structured anchors | 0 |
| ambiguous structured anchors | 0 |
| duplicate mapped record IDs | 0 |
| inventory evidence | 0 |
| primary_failure_reason | TARGET_RECALL_BELOW_7_OF_7 |
| secondary_failure_reasons | TARGET_RECALL_BELOW_7_OF_7 |
| raw stdout bytes | 1241 |
| failure classification | TARGET_OMISSION_WITH_CLEAN_PROVENANCE |

Evidence index is zero-based:

| evidence_index | uniquely mapped record_id |
|---:|---|
| 0 | REC-F01 |
| 1 | REC-F02 |
| 2 | REC-F03 |

| A4 category | Item count |
|---|---:|
| evidence_count | 3 |
| facts_count | 4 |
| hypotheses_count | 1 |
| inferences_count | 1 |
| unknowns_count | 3 |
| verify_next_count | 1 |

Full-fixture multimap cardinality: **668 records, 534 distinct source+locator pairs, 120 ambiguous pairs, 254 records in ambiguous pairs**. All three cited pairs are uniquely matched despite those background collisions.

**Provenance/anchor validation: CLEAN.** The mapped evidence IDs are exactly the recalled fact target IDs, with no wrong unique record selection. Non-fact categories contain items, but their counts and category text do not contribute to target recall; the frozen definition counts only target IDs reached through validated unique evidence anchors. This demonstrates target omission in the evidence set. It does not diagnose hidden model intent, establish semantic correctness of category text, or prove that statements are supported merely because provenance is valid.

No raw statement, source, locator, or category content is published.

## L02-1 successful output-size baseline

| Metric | Value |
|---|---:|
| raw_stdout_bytes | 4808 |
| status / primary failure | PASS |
| error_signatures_count | 4 |
| timeline_count | 22 |
| repeated_patterns_count | 4 |
| evidence_count | 4 |
| unknowns_count | 4 |
| strong_review_reasons_count | 3 |
| canonical validated event count | 4 |
| evidence_extras_count | 0 |
| timeline_extras_count | 18 |
| extra_signatures_count | 2 |

The complete timeline contains four validated canonical events and eighteen noncanonical extras; evidence contains four canonical items and no extras. The four error-signature items include two extra signatures. Those extras are nonfatal under the frozen subset gate and do not become canonical anchors or alter canonical counts. No signature strings or Company text are published. Byte length is an output-size baseline, not a completion-token estimator.

## L02-2: pinned provider mapping and diagnosis limit

The pinned [OpenAICompatibleClient source](../tools/llmtool/llmtool/providers/openai_compatible.py#L169) checks `finish_reason == "length"` at line 169 and raises `MODEL_OUTPUT_INCOMPLETE` at lines 170–174. Successful-path usage extraction starts later at line 209. An offline AST assertion verified that exact branch, safe code, and source order; exit **0**.

Provider source SHA-256:
`97c13ce3958027e267a9759a0f725480a47bca6b446a37ff8b0f61e5ddedfc9a`.
This file remains identical to the manifest-pinned implementation.

The original L02-2 TrialRecord says request_emitted=true, primary=MODEL_OUTPUT_INCOMPLETE, usage_total=null, and secondary diagnostics MODEL_OUTPUT_INCOMPLETE / COMPANY_TOKEN_ACCOUNTING_UNKNOWN. The observed frozen request had max_tokens=4096. Its body digest matches L02-1 exactly:
`cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`.

**L02-2 content diagnosis: NOT AVAILABLE — provider length failure occurred before successful raw-output persistence.**

No response envelope, raw content, token breakdown, or missing usage was reconstructed. The source mapping establishes the length failure; it does not reveal whether extras, repetition, or another output pattern caused it. The source also shows that even if a parseable response included usage, this failure path would raise before exposing it to the wrapper. Whether this particular lost response contained valid usage is NOT KNOWN.

## Accounting: measurement versus conditional budget bound

**Measured-known subtotal: 85,565 = 31,399 + 54,166. Complete run1 Company usage: UNKNOWN.**
The three later logical trials were not started; the third emitted request's usage is null. Nothing changes the original accounting result.

| Bound prerequisite | Offline evidence / status |
|---|---|
| Identical L02-1/L02-2 Request body bytes | VERIFIED via equal pinned actual Request body SHA in both TrialRecord proofs |
| Same tokenizer/input count for identical bytes on route gw | ASSUMPTION — gateway/tokenizer stability is not independently observable in these saved artifacts |
| max_tokens=4096 is a hard completion cap | Request setting VERIFIED; gateway enforcement remains an ASSUMPTION, not proven by the local client |
| L02-1 prompt count cannot exceed its valid total 31,399 | VERIFIED consequence of reviewed nonnegative integer usage validation and prompt+completion arithmetic; no prompt count is reconstructed |

Under the explicitly stated same-input-tokenizer and hard-cap assumptions only:

```text
L02-2 total <= 31,399 + 4,096 = 35,495
Run1 total <= 85,565 + 35,495 = 121,060
```

**ASSUMPTION-BOUNDED UPPER LIMIT — NOT MEASURED USAGE.**

This conditional result is below 300,000 and may be used solely for the Phase-0 budget discussion. An unconditional/measured upper limit is **NOT ESTABLISHED** by offline evidence. The bound must not enter savings calculations, price/cost measurement, or accounting completion, and is not substituted for the frozen 35,000 L02 admission bound. No new accounting rule is adopted.

## Source-referenced programme token ledger

Scope: the committed **Round-2 preflight, Stage A, Stage B, and Round-3 Phase 0** records requested in this handoff. This is a token-consumption ledger, not a complete monetary-cost or whole-workflow ROI measurement. Round-1, setup/coordinator/developer/reviewer sessions, and other unmeasured traffic are outside these verified totals. Company and Codex use different tokenizers and remain separate columns.

| Stage | Codex comparable tokens | Company measured tokens / known subtotal | Source |
|---|---:|---:|---|
| Round 2 preflight | 394,231 | 294,036 | [preflight_results.json](token_benchmark/20261006_round2/preflight_results.json): attempts; preflight_codex_usage_sum; preflight_company_total_tokens_sum |
| Round 2 Stage A | 196,116 | 105,389 | [Stage-A results.json](token_benchmark/20261006_round2/stage_a/results.json): codex_sessions; company_attempts; budget.measured_codex_total; company_total_tokens |
| Round 2 Stage B | 53,400 | 19,668 | [Stage-B results.json](token_benchmark/20261006_round2/stage_b/results.json): codex_sessions; company_attempts; stage_b_measured_codex_total; company_tokens |
| Round 3 Phase 0 | 0 | 85,565 known only; complete UNKNOWN | Hash-verified private phase0_state.json and [published live results](llmtool_token_savings_round3_phase0_live_results_20261006.md) |

Every Codex session/attempt total was recomputed as input_tokens+output_tokens and checked against stored comparable_total. Cached/cache-write/reasoning values were not added again. Every measured Company attempt was checked for nonnegative integer prompt/completion/total and total=prompt+completion. All attempts were included, regardless of pair validity.

Preflight seven sizes, in fixed ascending order:

| Size KiB | Codex | Company |
|---:|---:|---:|
| 32 | 28,405 | 11,657 |
| 48 | 32,531 | 17,399 |
| 64 | 37,992 | 23,140 |
| 96 | 50,252 | 34,624 |
| 128 | 59,838 | 46,106 |
| 192 | 81,684 | 69,072 |
| 256 | 103,529 | 92,038 |

Stage-A subtotals were rechecked:

- Codex: 34,235 + 20,604 + 21,691 + 40,253 + 59,152 + 20,181 = **196,116**.
- Company: L01 **19,675** + L02 **31,639** + L03 **54,075** = **105,389**.
- Stage-B Codex: 20,529 + 32,871 = **53,400**; Company **19,668**.

Derived ledger:

```text
Round-2 Codex = 394,231 + 196,116 + 53,400 = 643,747
Round-2 Company = 294,036 + 105,389 + 19,668 = 419,093

Through Round 3 (this ledger scope):
Codex measured total = 643,747
Company measured-known lower subtotal >= 419,093 + 85,565 = 504,658
Company complete total = UNKNOWN
Company conditional upper total <= 419,093 + 121,060 = 540,153
```

The last upper total inherits all run1 bound assumptions: **ASSUMPTION-BOUNDED UPPER LIMIT — NOT MEASURED USAGE**, relevant only to the 300k run1 budget discussion. It is not measured programme cost, a savings input, or a complete account.

Committed ledger source digests:

| Source | SHA-256 |
|---|---|
| reports/token_benchmark/20261006_round2/preflight_results.json | `5ecc2d632efa41e58c446351aad2d337e7283cc44534f2568e8abae9ce982b50` |
| reports/token_benchmark/20261006_round2/stage_a/results.json | `9153bba0b564c9c0190fff250cb26278bb0af9e64c518fe04293f319f981b66b` |
| reports/token_benchmark/20261006_round2/stage_b/results.json | `ab5ac6621e3de407ec2170a4427908c4db8d877c6de484a9023ac39897a460cf` |

### Only quality-approved evidence — separate L01 observations

| Stage / order | CONTROL | DOGFOOD | Codex reduction | Quality evidence |
|---|---:|---:|---|---|
| Stage A / CONTROL then DOGFOOD | 34,235 | 20,604 | 13,631 / 39.81597780049657% (39.82% rounded) | [Stage-A independent review](llmtool_token_savings_large_evidence_stage_a_review_20261006.md): 2/2 graders PASS, EQUIVALENT |
| Stage B / DOGFOOD then CONTROL | 32,871 | 20,529 | 12,342 / 37.5467737519394% (37.55% rounded) | [Stage-B independent review](llmtool_token_savings_large_evidence_stage_b_review_20261006.md): 2/2 graders PASS, EQUIVALENT |

Each remains one observation on the same synthetic canonical L01 fixture in its own order. Results are not pooled or averaged, and do not establish generalized/stable savings, routing thresholds, or whole-workflow ROI. Round-2 L02 remains coverage-contract INVALID despite EQUIVALENT final quality; L03 remains INVALID with DOGFOOD quality failure. No Round-3 paired benchmark occurred.

## Existing recommendations recorded, no implementation/design started

- **P1 production usage preservation:** independently recommended for a separately authorized/reviewed change. Preserve safe numeric usage when available before length or sufficiently parseable invalid-response failure. No production changes in this diagnosis; never invent absent/invalid usage.
- **P2 interval accounting:** design candidate from the existing review, **NOT ADOPTED**. Conditional bounds can support diagnostic budget discussion but do not replace exact comparable usage for savings claims. Any accounting change requires a new preregistration and review.
- The handoff's existing L02 bounded-output and L03 evidence-selection/category-support alternatives remain deferred decision topics. This report does not choose, specify, implement, or execute a Round-4 design.
- Recommended next decision: separately assess P1 authorization and review this observed omission pattern before deciding whether any new experiment is warranted. No live run2 or paired benchmark is authorized.

## Validation, limitations, and publication boundary

- Private state/stdout hashes: MATCH before parsing; both raw publication scans PASS.
- Literal manifest/artifact pins: PASS; no frozen source drift since reviewed implementation.
- L03 and L02 replay result SHA: MATCH; diagnosis/mapping/count/ledger/AST assertions all exited 0.
- Raw evidence-index mapping uses exact full-fixture source+locator and unique matches; no fuzzy normalization, free-text ID substitution, gold-based repair, or manual range changes.
- Replay uses the reviewed sequence: immutable RawStdout SHA/scan, fixture-only anchor construction/freeze, then verified gold target metadata for recall. Gold never enters request construction or repairs an answer.
- Provider requests **0**; Company model calls **0**; Codex benchmark calls **0**; retries **0** during this diagnosis.
- `git diff --check`: PASS before report creation; rechecked at publication.
- Report links/required conclusions, secret-marker screen, raw statement/source/locator exclusion, and post-diagnosis private-input hashes: PASS; exit 0.
- No application full suite or new live test required/run for this report-only diagnosis; previous 88/94 evidence remains historical, not claimed as a new retest.
- Private raw output stayed outside the repo. No statements/source/locator text, response JSON, state file, credentials, or helper implementation is published.
- Only this new sanitized report is staged/committed/pushed to Draft PR #38. Existing unrelated untracked artifacts remain untouched.
- No wrapper/instruction/manifest/fixture/gold/production/runtime/skill modifications. No retry, Round 4, or merge.

## Final status

```text
ROUND-3 RUN1 OFFLINE DIAGNOSIS:

Network/model calls:
0

L03 replay SHA:
MATCH

L03 target recall:
3 / 7

L03 missing target IDs:
[REC-H01, REC-I01, REC-U01, REC-V01]

L03 provenance/anchor validation:
CLEAN

L03 failure classification:
TARGET_OMISSION_WITH_CLEAN_PROVENANCE

L02-1 replay SHA:
MATCH

L02-1 output-size baseline:
4808 raw bytes; signatures 4; timeline 22; repeated_patterns 4;
evidence 4; unknowns 4; strong_review_reasons 3;
canonical events 4; evidence extras 0; timeline extras 18; extra signatures 2

L02-2:
finish_reason=length confirmed; raw content unavailable

Run1 measured-known Company usage:
85565

Run1 complete Company usage:
UNKNOWN

Run1 assumption-bounded upper limit:
<=121060
ASSUMPTION-BOUNDED UPPER LIMIT — NOT MEASURED USAGE
Conditional only; unconditional bound NOT ESTABLISHED

Phase-0 gate:
FAIL

Accounting:
INCOMPLETE / UNKNOWN

Paired Codex benchmark:
NOT AUTHORIZED

Same-preregistration run2:
NOT AUTHORIZED

Recommended next decision:
P1 production usage preservation + future-design decision after reviewing this diagnosis;
separate authorization required, no Round 4 started
```
