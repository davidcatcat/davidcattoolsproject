# llmtool Round 3 — Company Phase-0 coordinator review (2026-10-06)

## Verdict

**ACCEPT RESULTS. PHASE-0 GATE FAILED. OVERALL ECONOMIC RESULT INDETERMINATE.**

The published live-results report is internally consistent with the reviewed wrapper,
accounting state machine, and production provider error semantics.

Do not run the paired Codex benchmark.

Do not retry Phase 0 under the same preregistration.

## Exact reviewed result

Live execution head:
`83f552005899f2ff931324e058c28cf814b6823b`

Live results commit / current result head:
`02b31195ba1f25dcb247169a4792e8fe9eccb1b5`

Frozen tested implementation:
`3325f75e954a4099e31c5936412307320974cb4f`

Manifest pin:
`37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`

Only the sanitized live-results report was added after authorization. There are no
changes outside `reports/`, and no wrapper/runtime/skill drift.

## Observed live trial sequence

```text
L02-1  PASS         emitted=yes  usage=31399
L03-1  FAIL         emitted=yes  usage=54166
L02-2  FAIL         emitted=yes  usage=UNKNOWN
L03-2  NOT_STARTED  emitted=no
L02-3  NOT_STARTED  emitted=no
L03-3  NOT_STARTED  emitted=no
```

Physical Company requests: **3**.

Retry count: **0**.

Known subtotal from requests with valid usage:
`31,399 + 54,166 = 85,565`.

This is NOT a complete total.

## Failure 1 — L03 target recall

L03-1 reached the final post-freeze grading stage and failed:

`TARGET_RECALL_BELOW_7_OF_7`

Therefore the Company output did not cover all seven frozen evaluation targets through
the required exact structured source+locator evidence contract.

This alone is enough to reject the Phase-0 3/3 reliability gate. Even if all token
accounting had been complete, Phase 0 could not pass.

The sanitized live report intentionally does not contain the exact recall numerator or
raw model output, so no narrower root-cause claim is made here.

## Failure 2 — L02 output truncation

L02-2 returned:

`MODEL_OUTPUT_INCOMPLETE`

The pinned production `OpenAICompatibleClient` emits this safe code specifically when:

`finish_reason == "length"`

The frozen request had:

`max_tokens = 4096`

Thus L02-2 was terminated by the provider/model output-length limit under the frozen
request contract. This is not classified as a transport retry condition and must not be
retried under the same preregistration.

The emitted L02-2 request did not provide valid complete usage metadata to the wrapper,
so its consumption is unknown.

## Accounting conclusion

After an emitted request has unknown usage, the reviewed accounting contract sets:

`complete_total = UNKNOWN`

and blocks all subsequent physical requests with:

`COMPANY_TOKEN_ACCOUNTING_UNKNOWN`

That is exactly what happened for L03-2, L02-3, and L03-3.

Therefore:

- `known_cumulative = 85,565` is only a lower-bound known subtotal;
- it MUST NOT be compared against the control as the complete Company Phase-0 cost;
- no exact Round-3 token-savings percentage can be calculated from this run;
- no missing usage may be guessed, reconstructed, or treated as zero.

## Gate interpretation

Two conclusions must remain separate:

### Reliability / format gate

**FAIL — definitive.**

Reason:
- L03-1 failed target recall;
- L02-2 failed completion;
- only 1/6 logical records passed;
- only 3 physical requests were emitted.

### Economic/token result

**INDETERMINATE.**

Reason:
- third emitted request has unknown usage;
- complete_total is null/unknown.

The economic indeterminacy does not rescue the reliability gate.

## What this run proves

It proves the Round-3 interface fixes were not sufficient to make the Company model
reliably satisfy the frozen large-evidence contracts.

Specifically, in one frozen Phase-0 run:
- L02 can pass at least once under the contract;
- L03 can produce a parseable/final evaluation but still miss required target coverage;
- L02 can also hit `finish_reason=length` at max_tokens 4096;
- current strict 3/3-per-lane admission is not met.

This does NOT prove the Company model is unusable generally. It proves the current
frozen Round-3 contract/configuration is not reliable enough to justify spending Codex
tokens on the paired benchmark.

## Next allowed step

Recommended next step is **offline-only failure diagnosis using the already preserved
private raw artifacts and/or frozen evaluation metadata**.

No new Company inference is needed.

A separate diagnostic handoff should:
- preserve raw Company text outside Git;
- derive only bounded sanitized diagnostics;
- identify the exact L03 missing target count/IDs from the frozen evaluation result;
- determine whether L03 failure is omission, duplicate evidence, wrong source+locator,
  or another already-defined adapter reason;
- inspect L02-2 only for provider metadata/error classification, not attempt to recover
  or guess missing usage;
- decide whether a new experiment should change output budget, output schema, or task
  decomposition;
- treat any changed prompt/config/max_tokens as a NEW preregistration, never a retry.

No live run2 is authorized by this review.

## Final status

```text
ROUND-3 COMPANY PHASE-0 COORDINATOR REVIEW:
ACCEPTED

Physical Company requests:
3

Retries:
0

Known token subtotal:
85565

Complete Company token total:
UNKNOWN

Reliability/format gate:
FAIL

Economic/token conclusion:
INDETERMINATE

PHASE0_GATE_PASS:
NO

Paired Codex benchmark:
NOT AUTHORIZED

Same-preregistration Phase-0 retry:
NOT AUTHORIZED

Recommended next step:
OFFLINE-ONLY FAILURE DIAGNOSIS OF PRESERVED RUN1 ARTIFACTS
```
