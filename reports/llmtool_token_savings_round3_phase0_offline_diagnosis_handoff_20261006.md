# llmtool Round 3 — run1 offline failure-diagnosis handoff (2026-10-06)

## Purpose

Diagnose the already-completed Round-3 Company Phase-0 run1 **offline only**.

This step sends:
- 0 Company requests;
- 0 Codex benchmark requests;
- 0 retries.

It must not modify the frozen benchmark implementation, instructions, manifest,
fixtures, gold, production runtime, or skills.

## Current decision state

Phase-0 gate: **FAIL — definitive**.

Accounting: **INCOMPLETE / UNKNOWN** because one emitted L02 request has unknown usage.

Do not label the Phase-0 gate itself INDETERMINATE. The uncertainty applies to the
complete Company token total, not to the gate verdict.

Paired Codex benchmark: NOT AUTHORIZED.

Same-preregistration live retry/run2: NOT AUTHORIZED.

## Canonical repository

Use only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-round3-format-compliance-20261006`

Expected remote head before diagnosis:

`153caa0102db92d4a751aa83c7e50157f4d67b28`

Never use `/private/tmp/*` worktrees or clones.

Preserve unrelated untracked files. Do not clean/reset/stash.

## Required reading

Read:

- `reports/llmtool_token_savings_round3_phase0_live_results_20261006.md`
- `reports/llmtool_token_savings_round3_phase0_coordinator_review_20261006.md`
- `reports/llmtool_token_savings_round3_phase0_results_review_20261006.md`
- `reports/token_benchmark/20261006_round3/format_probe.py`
- `reports/token_benchmark/20261006_round3/live_wrapper.py`
- `tools/llmtool/llmtool/context.py`
- `tools/llmtool/llmtool/providers/openai_compatible.py`

## Private run1 directory

Read-only diagnostic source:

`/Users/davidsun/.llmtool-round3-phase0-live/37f48c17-run1`

Do not copy raw stdout into the repository.

Do not quote or publish raw model text.

Expected private state:

`phase0_state.json`

Expected SHA-256:

`e65c0cec14b52ad933b4f36fa44a8e23d06905000a5f3f5e92c1c411694d66e9`

Expected L02-1 stdout:

`L02-1.stdout`

SHA-256:

`082a3cee5e5b97e112dc03fd1067d6f412824749461206fa682c8786def473f2`

Expected L03-1 stdout:

`L03-1.stdout`

SHA-256:

`66173d2092abf0298aed8d71d76f24d068d0f5b2f4b561da9f8cd2c503d7e28f`

L02-2 should have no preserved successful stdout/evaluation because the production
provider raised `MODEL_OUTPUT_INCOMPLETE` before the wrapper's successful-output
persistence path.

If private hashes or expected artifact presence differ: STOP and report the discrepancy.
Do not repair or regenerate anything.

## Network prohibition

During diagnosis, block/avoid network access.

Do NOT:
- call the Company endpoint;
- use curl against Company;
- run llmtool ask/context-pack against Company;
- call Codex;
- call Claude CLI;
- call Gemini;
- perform any benchmark inference.

This is local deterministic replay only.

## A. L03-1 exact offline diagnosis

Read the exact private L03-1 stdout bytes.

Before parsing, verify its SHA exactly.

Replay using the frozen implementation:

```python
evaluation = live_wrapper.evaluate_raw("L03", raw)
```

Require:

`sha256(evaluation.result_json.encode("utf-8")) == f27f2d0bf3c42e4dac422fef8f90e3ec980cca0d2bd495cb50163558b9669e3b`

This must match the live TrialRecord evaluation SHA exactly.

Publish only sanitized deterministic fields from `evaluation.to_dict()`:

- stage;
- parser_passed;
- evidence_item_count;
- target_recall_numerator;
- target_recall_denominator;
- recalled_target_record_ids;
- missing_target_record_ids;
- validated_unique_anchor_record_ids;
- count of unknown_structured_anchors;
- count of ambiguous_structured_anchors;
- count of duplicate_mapped_record_ids;
- count of inventory_evidence;
- primary_failure_reason;
- secondary_failure_reasons.

No raw statements.

### Evidence-index → record-id map

For diagnosis only, parse the packet locally with the frozen
`llmtool.context.parse_context_packet`.

Use the frozen fixture multimap and exact `(source, locator)` matching.

Publish only:

```text
evidence_index -> record_id
```

Do NOT publish:
- evidence statement;
- source text;
- locator text;
- raw packet JSON.

If any evidence no longer maps uniquely, STOP because that would contradict the frozen
live evaluation.

### Context category counts

Publish counts only:

- facts_count;
- evidence_count;
- inferences_count;
- hypotheses_count;
- unknowns_count;
- verify_next_count.

Do not publish the category text.

### L03 diagnosis classification

Based only on frozen replay facts, classify the L03 failure as one of:

- TARGET_OMISSION_WITH_CLEAN_PROVENANCE
- WRONG_UNIQUE_RECORD_SELECTION
- OTHER_FROZEN_ADAPTER_FAILURE

Do not infer hidden model intent.

Because the live secondary diagnostics contained only
`TARGET_RECALL_BELOW_7_OF_7`, the expected outcome is that parse/provenance validation
is clean; confirm rather than assume.

## B. L02-1 output-size baseline

Read and hash-check L02-1 stdout.

Replay:

```python
evaluation = live_wrapper.evaluate_raw("L02", raw)
```

Require evaluation result SHA:

`e0668b133a264fe7e59317fe4dfba5fbd63c1e18e34f265b7be1e8594469a601`

Publish only sanitized size/count metrics:

- raw_stdout_bytes;
- status / primary failure;
- error_signatures_count;
- timeline_count;
- repeated_patterns_count;
- evidence_count;
- unknowns_count;
- strong_review_reasons_count;
- canonical validated event count;
- evidence_extras_count;
- timeline_extras_count;
- extra_signatures_count.

Do not publish any string content.

This is the successful L02 output-size baseline.

Do not estimate exact completion tokens from byte counts.

## C. L02-2 diagnosis limit

Use only:
- `phase0_state.json`;
- published TrialRecord metadata;
- pinned production provider source.

Confirm:

`MODEL_OUTPUT_INCOMPLETE`

maps to the production branch:

`finish_reason == "length"`

and frozen `max_tokens == 4096`.

Do not attempt to reconstruct missing output or usage.

Do not infer whether the excessive length was caused by extras, repetition, or another
content pattern; there is no preserved successful raw output for L02-2.

Publish:

```text
L02-2 content diagnosis:
NOT AVAILABLE — provider length failure occurred before successful raw-output persistence.
```

## D. Accounting clarification

Keep these separate:

### Measured

Known valid-usage subtotal:

`85,565`

This is not the complete total.

### Bounded budget estimate

Claude's proposed bound may be included only as a **non-measured budget upper bound**,
and only after explicitly verifying the assumptions:

1. L02-2 Request body bytes are identical to L02-1;
2. identical request bytes use the same provider tokenizer/input count for this route;
3. `max_tokens=4096` is a hard completion cap;
4. L02-1 total 31,399 is therefore an upper bound on the identical request's prompt
   token count.

Under those assumptions:

```text
L02-2 total <= 31,399 + 4,096 = 35,495
run1 total <= 85,565 + 35,495 = 121,060
```

Label this:

`ASSUMPTION-BOUNDED UPPER LIMIT — NOT MEASURED USAGE`

It may be used only to discuss the 300k Phase-0 budget ceiling.

It MUST NOT be used:
- as exact Company usage;
- in token-savings percentages;
- as a price/cost measurement;
- to convert accounting status from INCOMPLETE to COMPLETE.

## E. Programme cost ledger

Build a source-referenced ledger from committed benchmark result files/reviews.

Verify every number before publication.

Expected source-of-truth values to check:

### Round 2 preflight
- Codex: 394,231
- Company: 294,036

### Round 2 Stage A
- Codex: 196,116
- Company:
  - L01 19,675
  - L02 31,639
  - L03 54,075
  - Stage-A Company total = 105,389

### Round 2 Stage B
- Codex: 53,400
- Company: 19,668

### Round 3 Phase 0
- Codex: 0
- Company measured-known subtotal: 85,565
- Company complete total: UNKNOWN
- optional assumption-bounded run1 upper limit: <=121,060

Derived totals, after verifying sources:

```text
Round-2 Codex total:
394,231 + 196,116 + 53,400 = 643,747

Round-2 Company total:
294,036 + 105,389 + 19,668 = 419,093

Through Round-3:
Codex measured total = 643,747
Company measured-known lower subtotal >= 504,658
Company assumption-bounded upper total <= 540,153
```

The last upper total inherits the Round-3 assumptions and is not measured.

Also record the only quality-approved savings evidence:

- Stage A L01: 34,235 control vs 20,604 dogfood;
  Codex reduction 13,631 = 39.82%.
- Stage B L01: 32,871 control vs 20,529 dogfood;
  Codex reduction 12,342 = 37.55%.

Do not pool the two into a generalized savings estimate.

## F. Design conclusions — no implementation yet

The diagnostic report should separate recommendations into:

### Production improvement P1 — recommended independently

Production `OpenAICompatibleClient` currently raises on length/invalid-response paths
before preserving safe usage fields.

Recommend a separate future change to preserve safe numeric usage metadata
(`prompt_tokens`, `completion_tokens`, `total_tokens`) when present even if the
response later fails with:
- MODEL_OUTPUT_INCOMPLETE;
- MODEL_RESPONSE_INVALID where the envelope is sufficiently parseable.

This is useful for normal llmtool cost accounting, not only benchmarks.

Do NOT implement P1 in this diagnosis step.

### Benchmark/accounting P2 — do not adopt automatically

Claude suggested replacing global stop with preregistered upper-bound interval
accounting.

Record it as a design candidate, not an accepted rule.

Coordinator position:
- interval bounds may be useful for diagnostic/budget admission;
- they are not equivalent to measured usage;
- any future paired savings benchmark should still require exact/comparable usage for
  claims;
- changing the accounting rule requires a new preregistration and review.

### L02 Round-4 candidate

Prefer bounded output design before increasing max_tokens:
- canonical EVENT lines only in evidence/timeline;
- cap prose arrays such as repeated_patterns/unknowns/strong_review_reasons;
- keep output size predictable.

Do not implement now.

### L03 Round-4 candidate

The strongest architectural candidate is a separate **evidence-select** task:
- Company model selects exact `(source, locator)` / record references;
- deterministic code validates them;
- Codex/strong model performs analysis/reasoning.

This better matches the intended cheap-model selector / strong-model reasoner split,
but it is a semantic change from production `context-pack`; therefore it needs a new
milestone/preregistration and explicit product decision.

Alternative: retain context-pack but require evidence supporting each semantic category.

Do not choose between them until the exact run1 L03 omission pattern is known.

## Output report

Create:

`reports/llmtool_token_savings_round3_phase0_offline_diagnosis_20261006.md`

Report only sanitized metadata/counts/record IDs/digests.

Never include raw Company text.

Required final section:

```text
ROUND-3 RUN1 OFFLINE DIAGNOSIS:

Network/model calls:
0

L03 replay SHA:
MATCH / BLOCKED

L03 target recall:
N / 7

L03 missing target IDs:
[...]

L03 provenance/anchor validation:
CLEAN / finding

L03 failure classification:
...

L02-1 replay SHA:
MATCH / BLOCKED

L02-1 output-size baseline:
...

L02-2:
finish_reason=length confirmed; raw content unavailable / finding

Run1 measured-known Company usage:
85565

Run1 complete Company usage:
UNKNOWN

Run1 assumption-bounded upper limit:
<=121060 / NOT ESTABLISHED

Phase-0 gate:
FAIL

Accounting:
INCOMPLETE

Paired Codex benchmark:
NOT AUTHORIZED

Same-preregistration run2:
NOT AUTHORIZED

Recommended next decision:
P1 production usage preservation + Round-4 design decision after reviewing this diagnosis
```

Commit/push only this sanitized diagnosis report, then STOP.

Do not modify implementation.
Do not run Company.
Do not run Codex benchmark.
Do not start Round 4.
