# llmtool Round 3 — final synthesis and disposition (2026-10-06)

## Final decision

Round 3 is complete as an experiment record.

Do not run another paired Codex benchmark for L02/L03.

Do not retry the same Phase-0 preregistration.

The next engineering step is a separate production improvement:

**P1 — preserve safe usage metadata on non-success OpenAI-compatible responses, starting with finish_reason=length.**

Optional Round 4 must wait until P1 is implemented/reviewed and must begin with Company-only Phase 0.

## What the Codex offline diagnosis proved

L03-1:
- replay SHA matched exactly;
- recall = 3/7;
- evidence items = 3;
- mapped IDs = REC-F01, REC-F02, REC-F03;
- missing = REC-I01, REC-H01, REC-U01, REC-V01;
- unknown/ambiguous/duplicate/inventory anchors = 0;
- classification = TARGET_OMISSION_WITH_CLEAN_PROVENANCE.

Therefore the structured source+locator repair worked. The failure is no longer a
provenance/format failure.

L02-1:
- PASS;
- 4 canonical events;
- timeline extras = 18;
- evidence extras = 0;
- extra signatures = 2;
- raw stdout = 4808 bytes.

L02-2:
- finish_reason=length confirmed;
- max_tokens=4096;
- successful raw output is unavailable;
- content cause cannot be reconstructed.

## What Claude's review adds

The key L03 finding is not simply "the Company model missed four targets."

The L03 instruction says category items need **support** in evidence, while the gate
requires that the source RECORD for each relevant semantic category item itself appear
as evidence.

The model produced:
- facts = 4;
- inferences = 1;
- hypotheses = 1;
- unknowns = 3;
- verify_next = 1;
- evidence = 3 fact records only.

That output is plausibly consistent with the instruction's ordinary-language meaning
of "support", while still failing the gate's stricter record-citation contract.

This is a benchmark/instruction semantic mismatch, not sufficient evidence of model
incapability.

A future L03 experiment should explicitly require each used category item to cite the
fixture RECORD from which that item is drawn. This can be stated without revealing gold
target IDs because kind labels are fixture content.

## L02 interpretation

The 18 timeline extras in L02-1 match the prior Round-2 behavior and appear stable.

They do not by themselves explain L02-2's length failure. No L02-2 raw content survived,
so any claim of repetition/degeneration remains a hypothesis only.

Future L02 work should:
- bound timeline/evidence to canonical EVENT lines;
- cap prose-array lengths;
- record prompt/completion/total separately;
- preserve usage on length failure;
- retain 3 repeated Company-only trials before any paired Codex work.

## Accounting and evidence scope

Programme ledger within the committed benchmark scope:

- Round-2 Codex: 643,747
- Round-2 Company: 419,093
- Round-3 Company measured-known subtotal: 85,565
- Company complete Round-3 total: UNKNOWN

Through Round 3:
- Codex measured total = 643,747
- Company measured-known subtotal >= 504,658

The conditional run1 upper limit <=121,060 remains:
**ASSUMPTION-BOUNDED, NOT MEASURED USAGE**.

It must not be used in savings percentages or cost measurement.

## Only validated savings path

Only L01/test-triage has quality-approved evidence:

Stage A:
- control 34,235
- dogfood 20,604
- reduction 13,631 / 39.82%

Stage B reverse order:
- control 32,871
- dogfood 20,529
- reduction 12,342 / 37.55%

These are two separate observations on the same synthetic canonical fixture and must
not be generalized.

Current guidance should treat L01-type test-triage as the only validated delegation
path. L02/log-triage and L03/context-pack remain experimental/auxiliary for token-saving
claims.

## Research-spend implication

Against 643,747 measured Codex tokens spent in the benchmark programme scope, one
validated L01 use saves about 12.3k–13.6k Codex tokens.

Ignoring Company cost, that corresponds to roughly 47–52 L01-type uses to amortize the
measured Codex benchmark spend.

This is not a product ROI calculation; it is a useful stop-spending signal.

Therefore no further paired Codex benchmark should run until a Company-only gate passes.

## Next engineering step — P1

Implement separately from PR #38:

**Preserve safe usage metadata on non-success OpenAI-compatible responses.**

At minimum, when a response envelope is parseable and usage fields are present/valid,
retain:
- prompt_tokens
- completion_tokens
- total_tokens

before raising:
- MODEL_OUTPUT_INCOMPLETE;
- MODEL_RESPONSE_INVALID where parsing has progressed far enough safely.

Requirements:
- never infer/guess missing usage;
- preserve existing safe error codes;
- do not expose raw response content;
- add unit tests for length + valid usage, length + invalid/missing usage, invalid
  response with safe usage, and normal success;
- maintain backward compatibility for callers;
- document diagnostic metadata behavior.

This is useful for normal llmtool cost accounting independently of the benchmark.

## Round 4 policy

Round 4 is NOT authorized now.

If P1 is merged and a new experiment is later approved:
- Company-only Phase 0 first;
- no Codex paired benchmark preregistration up front;
- L03 instruction/gate semantics must be aligned;
- L02 output must be bounded;
- 3 trials per lane;
- exact prompt/completion/total recorded;
- paired Codex benchmark only after Company-only gate passes.

## Final disposition

```text
ROUND 3:
COMPLETE

PR #38 purpose:
EXPERIMENT / REVIEW RECORD

Phase-0 gate:
FAIL

Accounting:
INCOMPLETE / UNKNOWN

L03 root cause:
INSTRUCTION/GATE SEMANTIC MISMATCH + 3/7 EVIDENCE RECORD RECALL

L02:
ONE PASS + ONE LENGTH FAILURE; FAILURE CONTENT NOT DIAGNOSABLE

Paired Codex benchmark:
STOP

Validated token-saving path:
L01 / TEST-TRIAGE ONLY

Next:
P1 PRODUCTION USAGE PRESERVATION

Round 4:
DEFERRED UNTIL P1 + NEW EXPLICIT DESIGN DECISION
```
