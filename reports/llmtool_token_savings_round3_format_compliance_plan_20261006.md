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
- base_url `http://10.29.7.58:4000`;
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
- require the canonical timeline subset to preserve fixture timestamp/source order;
- compute signature counts from canonical EVENT objects only;
- require all required canonical signatures/counts;
- reject fabricated or field-mismatched canonical EVENT lines;
- reject duplicate required canonical event IDs.

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
2. Build an index from exact `(source, locator)` to fixture record.
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

## Phase 0 repetitions

Run company-only probes:

- R3-L02: 3 independent fresh invocations.
- R3-L03: 3 independent fresh invocations.

Total planned company calls: **6**.

Use the exact same fixture/instruction/config for repeats of a fixture.
Do not change prompts between repeats.

Record every attempt, including failure.

No retry for format/coverage failure.
At most one same-config retry only for a transient transport error; both attempts remain
recorded.

## Phase 0 pass gate

Proceed to an expensive paired benchmark only if:

- L02 adapter contract PASS = **3/3** fresh invocations;
- L03 structured-anchor contract PASS = **3/3** fresh invocations;
- all six calls have actual-request proof of thinking=false;
- no length failure;
- no parser/schema failure;
- no semantic/prompt amendment between repeats.

If either fixture is below 3/3:

**ROUND3_FORMAT_COMPLIANCE_GATE_FAIL**

Stop before any measured Codex pair.

The purpose is to fail cheaply.

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
- output digest.

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

## Deliverables

Create under:

`reports/token_benchmark/20261006_round3/`

- `format_probe.py`
- `format_probe_results.json`
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
- missing required event FAIL;
- duplicate required event FAIL;
- fabricated canonical event FAIL;
- wrong field FAIL;
- reordered canonical timeline FAIL;
- extra signature recorded but not fatal.

L03:
- exact source+locator unique match PASS;
- absent locator FAIL;
- unknown pair FAIL;
- ambiguous pair FAIL;
- duplicate mapped anchor recorded/fails uniqueness gate;
- statement with no record_id still maps successfully;
- statement with misleading record_id cannot override source+locator.

No live call before these tests PASS.

## What Phase 0 does not prove

A Phase-0 PASS proves only:

- deterministic adapters can consume the current company output reliably on these frozen
  synthetic fixtures.

It does not prove:
- token savings;
- Codex quality parity;
- real-log performance;
- production routing safety;
- generalized company reliability.

## If Phase 0 passes

Create a **new preregistered Round-3 benchmark step**, not an automatic continuation.

That later benchmark should:
- include at least one noncanonical/realistic-format fixture;
- retain hard stop before Codex on coverage failure;
- keep Stage-A/confirmation results separate;
- use multiple repetitions if the expected design difference is only a few percent;
- continue reporting Codex reduction and company token usage side-by-side.

## Stop point for this planning commit

Allowed now:
- this plan;
- draft PR.

Not yet allowed:
- live company Phase-0 calls;
- measured Codex calls;
- production runtime changes;
- skill changes;
- Round-3 fixture changes;
- merge.
