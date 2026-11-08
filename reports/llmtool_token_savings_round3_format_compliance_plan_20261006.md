# llmtool token-savings Round 3 — company format-compliance gate (2026-10-06)

Status: **PLAN / NO LIVE CALLS YET**

Base: `main @ fecbdbae928b63de2661ed4b8faf6dbfff964400` after Round-2 PR #37 merged.

## Why Round 3 starts here

Round 2 proved one useful thing and exposed one bottleneck:

- L01 produced two quality-approved observations with the same direction under reversed order:
  - Stage A: CONTROL 34,235 vs DOGFOOD 20,604 -> 39.816% fewer Codex comparable tokens.
  - Stage B: CONTROL 32,871 vs DOGFOOD 20,529 -> 37.547% fewer Codex comparable tokens.
- L02 final Codex quality was equivalent, but the pair was invalid because the frozen
  log-triage coverage contract rejected noncanonical extra log items.
- L03 failed because the benchmark depended on a literal `record_id=` token inside
  free-text evidence statements; the existing A4 packet already exposes structured
  `source` and `locator`, but Round 2 did not use them as anchors.

Round 3 therefore does **not** start with another expensive Codex benchmark.

It first tests whether the company model can produce output that a deterministic adapter
can consume reliably.

## Phase 0 goal

Company-only format-compliance probe. **No measured Codex calls.**

Question:

> Can the existing company model, with thinking disabled and frozen bounded prompts,
> reliably produce outputs from which deterministic code can recover the required raw
> evidence anchors?

Only after this gate passes may a later Round-3 benchmark be preregistered.

## Scope

Phase 0 covers only the two Round-2 failure modes:

- R3-L02: log-triage subset coverage.
- R3-L03: structured A4 source+locator anchors.

L01 is not rerun in Phase 0; it already passed twice.

No production llmtool behavior changes are authorized by this plan.

## Inputs

Reuse the exact immutable Round-2 fixture bytes as probe inputs, referenced by digest:

- L02 fixture SHA-256:
  `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da`
- L03 fixture SHA-256:
  `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce`

The Round-2 gold files remain evaluation-only and are never supplied to the company
model.

Using the old fixtures here is deliberate: Phase 0 isolates adapter/format reliability
without introducing a new evidence-content confound.

## Company configuration

Exactly the reviewed Round-2 company path:

- llmtool only;
- isolated temporary HOME;
- openai-compatible;
- base_url `http://home-llm.example:8000`;
- requested model `gw`;
- timeout 120;
- null api_key/api_key_env;
- `provider_options.enable_thinking=false`;
- temperature 0;
- max_tokens 4096.

Actual outgoing Request must again be observed unchanged and prove
`chat_template_kwargs.enable_thinking=false`.

No direct curl, fallback, semantic retry, max-token increase or real-user-config
mutation.

## R3-L02 contract — subset coverage, not whole-output purity

Round 2 incorrectly made every evidence/timeline item canonical.

Round 3 instead separates:

1. **required canonical subset**, and
2. **extra noncanonical model output**.

Required canonical records are still parsed only from exact complete EVENT lines.

For each company output:

- parse all exact canonical EVENT lines from evidence/timeline;
- require every required canonical EVENT exactly once in evidence;
- require **complete canonical timeline coverage**: after unrelated extras are separated,
  the required canonical timeline sequence must equal the full fixture-derived expected
  EVENT sequence exactly once per required event, ordered by timestamp with source-line
  tie-break;
- apply duplicate checks separately to evidence and timeline; one required occurrence in
  each array is expected and is not a cross-array duplicate;
- compute signature counts **once from the validated evidence subset only**; never
  double-count timeline copies or prose/extras;
- require all required canonical signatures/counts;
- validate the entire canonical tuple (event_id, request_id, signature, timestamp)
  against the frozen fixture before counting or anchoring;
- reject fabricated, unknown, wrong-field or duplicate required canonical EVENT claims
  in either evidence or timeline;
- classify each returned item before counting it as exactly one of:
  1. validated required canonical EVENT;
  2. invalid canonical-looking claim; or
  3. genuinely noncanonical extra;
- malformed/partial/prose EVENT-looking items cannot satisfy or repair required
  coverage.

Noncanonical evidence/timeline items:
- are recorded in `company_extra_items`;
- do not contribute to canonical counts;
- do **not** automatically fail the format gate.

Extra `error_signatures`:
- are recorded separately;
- required canonical signatures must be present;
- extras do not automatically fail the gate.

Only exact validated canonical event IDs become selector anchors.

This changes Round-3 evaluation only. It does not retroactively change Round-2 validity.

## R3-L03 contract — structured source+locator anchors

Do not depend on `record_id=` appearing inside free-text statements.

Use the existing validated A4 EvidenceItem fields:

`(source, locator)`

Deterministic mapping:

1. Parse the frozen fixture RECORD universe.
2. Build a **multimap over the complete fixture universe** from exact
   `(source, locator)` to all matching fixture records; never use last-writer-wins and
   never build the index from gold targets only.
3. A company evidence item is anchorable only if:
   - source is nonempty;
   - locator is nonempty;
   - the exact pair matches **one and only one** fixture record.
4. Zero matches -> `UNKNOWN_STRUCTURED_ANCHOR`.
5. More than one match -> `AMBIGUOUS_STRUCTURED_ANCHOR`.
6. Duplicate mapped records are recorded as duplicates.
7. Only unique exact matches become selector anchors.

The evidence statement text may contain or omit record_id; it is irrelevant to anchor
resolution.

Mechanical structured anchoring still does **not** prove semantic completeness.

### L03 Phase-0 semantic-readiness floor

A nonempty set of valid structured anchors is necessary but not sufficient.

For this frozen L03 fixture, Phase 0 additionally preregisters a post-output target
recall requirement of **7/7 frozen target records**.

The company model never receives the gold/target list. The sequence is fixed:

1. company output completes;
2. immediately SHA-256 the **raw stdout bytes** before parsing;
3. parse/validate A4 output;
4. derive structured source+locator anchors from fixture bytes only;
5. freeze the adapter decision/anchors;
6. only then load grading metadata to compute target recall.

Target recall is defined exactly as:

`validated_unique_anchor_record_ids ∩ frozen_target_record_ids`

divided by the seven frozen target record IDs. Statement text and free-text record_id
mentions do not contribute to recall.

Target recall is evaluation-only. It may fail the logical trial, but it must never:
- repair the company output;
- add or replace anchors;
- change selection;
- modify the prompt/instructions;
- trigger a semantic retry.

Each L03 logical trial passes only if:
- parser/schema PASS;
- validated structured anchor set is nonempty;
- unknown structured anchors = 0;
- ambiguous structured anchors = 0;
- duplicate mapped anchors = 0;
- **target recall = 7/7**.

Round-3 L03 company instructions are frozen before live execution and explicitly
require evidence provenance for every substantive category item, with a non-duplicating
evidence contract:

- every fact, inference, hypothesis, unknown and verify-next item must be supportable by
  one or more entries in the A4 evidence array;
- **each unique fixture record may appear at most once in the evidence array**;
- the same single evidence item may support multiple semantic category items;
- each evidence item must use the exact source and locator text present in the supplied
  fixture;
- do not invent/fuzzy-normalize source or locator;
- the evidence statement need not contain record_id and free-text record_id is ignored
  for anchoring;
- do **not** create facts/inferences/hypotheses/evidence entries from the inventory
  background records; they are fixture noise for this benchmark;
- if the model wishes to mention inventory irrelevance, it may do so only in summary
  prose and without creating an inventory evidence citation.

This keeps duplicate mapped anchors = 0 compatible with complete provenance: one record,
one evidence item, reusable across categories. It also prevents the fixture's known
ambiguous inventory source+locator pairs from becoming a benchmark-artifact failure.

This is a benchmark-specific instruction, not a production A4 schema/runtime change.


## Hard stop before Codex

This rule is mandatory for all future Round-3 paired benchmarks:

If company coverage is invalid, or the validated anchor set is empty:

- do not execute DOGFOOD Codex;
- record `COMPANY_TRIAGE_COVERAGE_MISS`;
- retain company usage;
- no zero-valued Codex usage is invented.

If selector/cap validation fails:

- do not execute DOGFOOD Codex;
- record `SELECTIVE_READ_CAP_INSUFFICIENT`.

This prevents the Round-2 L03 failure mode where roughly 20k Codex tokens were consumed
despite zero selected evidence.

## Phase 0 repetitions, order and request accounting

Phase 0 uses **six logical trials and exactly six maximum physical company requests**.
There are **no retries** in Phase 0, including transport retries. This resolves the
logical-trial/request-attempt ambiguity and keeps the gate cheap.

Frozen order:

```text
1. R3-L02 trial 1
2. R3-L03 trial 1
3. R3-L02 trial 2
4. R3-L03 trial 2
5. R3-L02 trial 3
6. R3-L03 trial 3
```

Each logical trial may emit at most one company request.

Use the exact same fixture/instruction/config for repeats of a fixture.
Do not change prompts between repeats.

For every trial/request record:
- logical trial ID;
- whether a Request was actually emitted;
- exact prompt/input digest;
- config digest;
- actual-request observation/proof when a Request was emitted;
- exit/failure category;
- elapsed time;
- provider usage when available;
- raw stdout artifact/digest after secret-marker scan.

A transport failure counts as a failed logical trial. It does not permit a retry.
Failures before Request construction are recorded separately and do not fabricate
Request proof or usage.

Because the Phase-0 gate is strict 3/3 **per fixture**, early stop is also per fixture:

- the first failed L02 logical trial stops later L02 trials only;
- the first failed L03 logical trial stops later L03 trials only;
- the other fixture continues according to the frozen order, skipping stopped-fixture
  slots.

Skipped same-fixture trials are recorded as
`NOT_STARTED_EARLY_FORMAT_GATE_FAIL`.

The overall Round-3 gate still fails if either fixture is below 3/3. This stop policy
cannot make the result more favorable because a failed fixture can no longer recover to
3/3; it only avoids redundant requests while preserving diagnostic coverage of the
other failure mode.

### Phase-0 company-token ceiling

Hard ceiling for provider-reported company tokens: **300,000 total tokens** across all
emitted Phase-0 requests.

This ceiling is independent of later Codex budgets.

After each completed request:
- add its reported company total tokens.

Before starting each request, require:

`known_cumulative_company_tokens + preregistered_trial_upper_bound <= 300000`

Conservative per-request upper bounds are frozen as:

- L02: **35,000 company tokens**
- L03: **59,000 company tokens**

These bounds cover the Round-2 observed prompt sizes plus the current instruction
delta and the full 4,096 completion allowance. They are experiment-admission bounds,
not expected usage and not production thresholds.

If a consumed/emitted request may have usage but provider usage is unavailable:
- record usage as `unknown`, never zero;
- the complete company-token total becomes unknown;
- STOP remaining trials with `COMPANY_TOKEN_ACCOUNTING_UNKNOWN`.

If the next planned request would violate a separately preregistered hard ceiling based
on known accounting, do not start it and record `COMPANY_TOKEN_CEILING_NOT_STARTED`.

The 300,000 ceiling is a Phase-0 experiment budget, not a production routing threshold.

## Phase 0 pass gate

Proceed to an expensive paired benchmark only if:

- L02 adapter contract PASS = **3/3** fresh invocations;
- L03 structured-anchor contract PASS = **3/3** logical trials;
- every L03 trial has a **nonempty** validated unique anchor set;
- every L03 trial has zero unknown/ambiguous/duplicate mapped anchors;
- every L03 trial reaches the preregistered **7/7 target-recall floor** after output
  and anchors are frozen;
- all six calls have actual-request proof of thinking=false;
- no length failure;
- no parser/schema failure;
- no semantic/prompt amendment between repeats.

If either fixture is below 3/3:

**ROUND3_FORMAT_COMPLIANCE_GATE_FAIL**

Stop before any measured Codex pair.

The purpose is to fail cheaply.

### Phase-0 failure attribution

Every logical trial records exactly one primary failure stage plus all secondary
diagnostics.

Primary failure precedence:

1. `TRANSPORT_OR_REQUEST_FAILURE`
2. `MODEL_OUTPUT_INCOMPLETE`
3. `SCHEMA_OR_PARSE_FAILURE`
4. `INVALID_CANONICAL_COVERAGE` (L02)
5. `UNKNOWN_STRUCTURED_ANCHOR` (L03)
6. `AMBIGUOUS_STRUCTURED_ANCHOR` (L03)
7. `DUPLICATE_STRUCTURED_ANCHOR` (L03)
8. `EMPTY_VALIDATED_ANCHOR_SET` (L03)
9. `TARGET_RECALL_BELOW_7_OF_7` (L03)
10. `PASS`

The report also retains all secondary counters/details. This makes it possible to
distinguish provider/format failures from benchmark-contract failures without repairing
the output.

## Probe metrics

For every invocation record:

Common:
- prompt tokens;
- completion tokens;
- total tokens;
- provider model;
- elapsed time;
- schema/parser status;
- actual-request thinking=false proof;
- output digest;
- **raw company stdout** for synthetic-only replay, stored verbatim only after a
  deterministic secret-marker scan passes.

L02:
- required event recall;
- duplicate required IDs;
- mismatched canonical lines;
- canonical signature counts;
- canonical timeline order;
- count of noncanonical evidence items;
- count of noncanonical timeline items;
- extra error signatures;
- validated anchor count.

L03:
- evidence item count;
- uniquely mapped source+locator anchors;
- unknown structured anchors;
- ambiguous structured anchors;
- duplicate mapped anchors;
- validated anchor count;
- target-reference recall as **post-output evaluation only**.

Gold/target recall is never used to construct prompts or repair output.

## Raw company output retention and replay

Each emitted Phase-0 request retains the complete raw company stdout in a dedicated
probe artifact alongside its SHA-256.

Before commit:
- run a deterministic secret-marker/credential-pattern scan;
- because inputs are synthetic, any unexpected credential-like marker is treated as a
  publication blocker;
- never redact or edit the output and then use the edited version for the Phase-0 gate.

The live adapter decision must use the exact frozen pre-live implementation.

Retained raw stdout may later be replayed offline to diagnose or test a **future**
adapter version, but replay cannot retroactively alter this Phase-0 verdict and cannot
replace a new live validation when the prompt/model/config contract changes.

## Deliverables

Create under:

`reports/token_benchmark/20261006_round3/`

- `format_probe.py`
- `format_probe_results.json`
- `raw_outputs/` with one immutable stdout artifact per emitted request
- `README.md`

Create report:

`reports/llmtool_token_savings_round3_format_probe_20261006.md`

Before live probes, commit the adapter/probe rules and their self-tests.

After live probes, publish the results in a separate commit so rules cannot be changed
after seeing model output.

## Self-tests before live calls

The probe helper must have deterministic offline tests covering:

L02:
- required subset PASS with extra noncanonical items;
- complete evidence plus empty timeline FAIL;
- complete evidence plus proper-subsequence timeline FAIL;
- missing required event FAIL;
- duplicate required event in evidence FAIL;
- duplicate required event in timeline FAIL;
- fabricated canonical event FAIL;
- wrong request/signature/timestamp field FAIL;
- reordered canonical timeline FAIL;
- malformed/partial canonical-looking item cannot satisfy coverage;
- extra signature/prose recorded but not fatal and cannot alter canonical counts;
- correct full canonical evidence/timeline plus unrelated extras PASS.

L03:
- exact source+locator unique match PASS;
- valid A4 empty evidence FAILS Phase-0 usable-anchor gate;
- absent/null locator FAIL;
- unknown pair FAIL;
- ambiguous pair FAIL;
- duplicate mapped anchor recorded/fails uniqueness gate;
- statement with no record_id still maps successfully;
- statement with misleading record_id cannot override source+locator;
- actual frozen-fixture multimap collision counts are preserved (no last-writer-wins);
- 3/7 target recall FAIL;
- 7/7 target recall PASS;
- facts-only evidence that omits inference/hypothesis/unknown/verify-next support FAILS
  the 7/7 readiness floor;
- one evidence item reused as support for multiple semantic categories PASS;
- two evidence items mapping to the same unique record FAIL duplicate gate;
- ambiguous inventory citation is rejected by the benchmark instruction/gate and cannot
  be used to satisfy target recall;
- a compliant output with no inventory evidence and seven unique target evidence records
  PASS.

Accounting/isolation:
- one physical request maximum per logical trial;
- transport failure receives no retry;
- first failed trial stops later trials for the **same fixture only** and records
  preregistered not-started states while the other fixture can continue;
- raw stdout is retained byte-for-byte and secret-marker scanned;
- unknown consumed-attempt usage remains unknown and blocks further execution;
- 300,000 company-token ceiling accounting uses the frozen 35k L02 / 59k L03
  pre-request bounds;
- fake Request observer proves thinking=false/model/temp/max_tokens/no Authorization while
  leaving the Request unchanged;
- prompt/adapter/retry/gate paths cannot read gold;
- post-output target-recall grading cannot alter output, anchors, retry or trial result
  construction.

No live call before these tests PASS.

## What Phase 0 does not prove

A Phase-0 PASS proves only:

- the deterministic adapters achieved the preregistered observed compliance gate on
  these frozen synthetic fixtures. Three identical temperature-zero trials primarily
  probe reproducibility/backend nondeterminism; they are **not** a statistical
  reliability estimate.

It does not prove:
- token savings;
- Codex quality parity;
- real-log performance;
- production routing safety;
- generalized company reliability.

## Economic decision gate before any paired Round-3 benchmark

Phase 0 is about format/admission readiness only.

Before authorizing any new measured Codex pair, obtain a trustworthy unit-cost basis for
the Codex path and the company model path, or explicitly document that company-model
usage is treated as effectively internal/fixed-cost for the decision.

Round 2 showed negative mixed token-volume deltas despite lower Codex usage. Therefore a
future paired benchmark should proceed only if its decision purpose is clear:
- monetary/API cost reduction;
- scarce Codex quota reduction;
- latency/throughput;
- or another explicitly named objective.

Different tokenizers remain non-comparable as raw compute/cost without pricing.

## If Phase 0 passes

Create a **new preregistered Round-3 benchmark step**, not an automatic continuation.

That later benchmark **must**:
- include at least one independently constructed realistic-format log fixture **and**
  one independently constructed realistic mixed-evidence fixture;
- ensure those realistic fixtures do not depend on canonical EVENT/RECORD lines as the
  sole route to evidence anchors;
- freeze multi-line/prose cases, repeated/ambiguous source-locator cases, negative cases,
  locator rules, gold and coverage expectations before live execution;
- retain hard stop before Codex on coverage failure;
- keep Stage-A/confirmation results separate;
- use multiple repetitions if the expected design difference is only a few percent;
- continue reporting Codex reduction and company token usage side-by-side.

## Stop point for this planning commit

Allowed now:
- this plan;
- draft PR.

Not yet allowed:
- live company Phase-0 calls until this amended plan and offline probe/self-tests receive
  targeted independent review;
- measured Codex calls;
- production runtime changes;
- skill changes;
- Round-3 fixture changes;
- merge.
