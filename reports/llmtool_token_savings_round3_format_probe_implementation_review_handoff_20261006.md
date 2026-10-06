# llmtool Round 3 Phase 0 — independent implementation review handoff (2026-10-06)

## Purpose

Independently review the frozen offline Phase-0 implementation before any live company
request is authorized.

This is a source/test review only.

Do **not**:
- call the company LLM;
- call Codex;
- modify production/runtime/skills;
- modify fixtures/gold;
- merge PR #38.

## Repository / revision

Repository:
`davidcatcat/davidcattoolsproject`

PR:
`#38`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Current review head:
`af9c2211f01cd550e79f48ea41c2ae9690c41965`

Exact implementation head tested by offline validation:
`29bd0dd92db203532f119a3b4c77be476454ab87`

The delta from the tested implementation head to current review head must contain only
the offline validation report.

## Primary files

Review directly:

- `reports/token_benchmark/20261006_round3/format_probe.py`
- `reports/token_benchmark/20261006_round3/test_format_probe.py`
- `reports/token_benchmark/20261006_round3/README.md`
- `reports/llmtool_token_savings_round3_format_probe_offline_validation_20261006.md`

Methodology authority:

- `reports/llmtool_token_savings_round3_format_compliance_plan_20261006.md`
- prior Round-3 methodology reviews/rereview
- implementation handoff.

## Review goal

Determine whether the implementation faithfully enforces the frozen methodology and
whether any bug could:

- falsely PASS an invalid L02/L03 trial;
- leak gold into prompt/anchor construction;
- hide ambiguous/duplicate/unknown evidence;
- violate no-retry or budget accounting;
- turn unknown/not-started usage into zero;
- mutate/corrupt raw stdout or Request observation;
- allow replay/post-freeze grading to retroactively change a live verdict.

## Required direct checks

### Revision integrity

Verify:
- implementation tested at `29bd0dd...`;
- current head differs only by the validation report;
- all PR changes remain under `reports/`;
- no production/runtime/skill/workflow change.

### L02

Directly inspect whether:

- every required EVENT must appear exactly once in evidence;
- every required EVENT must appear exactly once in timeline;
- timeline must be the complete timestamp/source-line ordered sequence;
- evidence/timeline duplicate checks are independent;
- complete EVENT tuple must byte/field-match frozen fixture;
- malformed/partial canonical-looking claims cannot count;
- canonical signature counts come only from validated evidence;
- extras cannot satisfy coverage;
- extras cannot enter anchors;
- extra signatures are recorded but nonfatal;
- a wrong/fabricated tuple cannot be silently reclassified as harmless extra.

Look for edge cases that the 23 L02 tests may still miss.

### L03

Directly inspect whether:

- full-fixture source+locator multimap is used;
- no gold target list participates in multimap/anchor construction;
- source/locator exact matching is strict;
- free-text record_id is irrelevant;
- zero match => unknown;
- >1 matches => ambiguous;
- duplicate unique-record evidence => duplicate failure;
- inventory evidence cannot count as usable target evidence;
- nonempty anchor requirement is enforced;
- final 7/7 target recall is a distinct post-freeze stage;
- 3/7 cannot accidentally PASS;
- a failed anchor stage cannot become PASS just because recall later reaches 7/7;
- target recall is computed from validated unique anchor record IDs only.

Pay special attention to `FrozenEvaluation`, `_freeze`, and the transition from anchor
evaluation to recall evaluation.

### Gold isolation

Review both source and tests.

Confirm no pre-freeze function can receive/read:
- gold paths;
- gold objects;
- target lists;
- evaluation metadata.

Confirm the only grading metadata entry point is the explicit post-freeze recall function.

Look for indirect imports/file paths/global variables that would defeat the test guards.

### Raw stdout

Check:
- SHA-256 is computed from original immutable bytes;
- no redaction occurs before gating;
- parsing happens after capture;
- secret scan cannot alter raw bytes;
- replay cannot overwrite earlier results.

Assess whether the secret-marker scan is appropriately described as a publication guard,
not a universal secret detector.

### Request observer

Check offline observer:
- exact URL/method;
- model=gw;
- temperature=0;
- max_tokens=4096;
- chat_template_kwargs exactly enable_thinking=false;
- Authorization absent;
- observer does not mutate request.

Check for Python type-coercion bugs such as bool==0/1.

### Phase0Accounting

Review all state transitions.

Confirm:
- fixed order L02-1,L03-1,L02-2,L03-2,L02-3,L03-3;
- one physical request maximum;
- there is no retry state/API;
- per-fixture early stop only;
- other fixture continues;
- emitted request + missing usage => accounting unknown and all future requests stop;
- skipped/unstarted usage remains null, not zero;
- pre-request admission uses cumulative + 35k/59k against 300k;
- unexpected actual overrun is retained and gate cannot PASS;
- gate_passed cannot become true with NOT_STARTED/FAIL/unknown accounting.

Look for impossible or unsafe states that tests do not cover.

### Failure attribution

Check that primary failure precedence is deterministic and that secondary reasons remain
available.

In particular ensure:
- parse failure wins over later deterministic stages because later stages are not run;
- unknown / ambiguous / duplicate / empty / recall ordering matches the frozen contract;
- accounting-only statuses are not misreported as model-format PASS.

### Tests

Do not trust 79/79 merely by count.

Inspect whether tests are tautological or overly coupled to implementation.

Verify at least the following are genuinely adversarial:
- wrong EVENT tuple;
- malformed canonical-looking log line;
- complete evidence + incomplete timeline;
- ambiguous L03 pair;
- duplicate L03 mapping;
- misleading record_id;
- 3/7 vs 7/7;
- inventory citation;
- per-fixture early stop;
- unknown usage;
- exact budget boundary;
- Request bool-coercion;
- gold-I/O guard;
- post-freeze immutability.

If useful, run additional read-only/offline counterexamples under Python 3.9.
Do not add live/provider tests.

## Required verdict

Finish with:

```text
PR #38 ROUND-3 OFFLINE IMPLEMENTATION REVIEW:
PASS
or
APPROVE WITH CHANGES
or
BLOCKED

Exact review head:
af9c2211f01cd550e79f48ea41c2ae9690c41965

Revision integrity:
PASS / finding

L02 adapter:
PASS / finding

L03 structured-anchor adapter:
PASS / finding

Post-freeze 7/7 recall:
PASS / finding

Gold isolation:
PASS / finding

Raw stdout / replay:
PASS / finding

Request observer:
PASS / finding

Phase0Accounting:
PASS / finding

Failure attribution:
PASS / finding

Offline tests:
PASS / finding

New blockers:
NONE / details

May prepare live Phase-0 execution wrapper/handoff:
YES / NO

May run live company Phase 0 now:
NO — live wrapper/instructions/config/digests/request observation and execution contract
must still be frozen and reviewed separately
```

Commit only the independent review report to the PR #38 branch.
Do not modify implementation in the review.
