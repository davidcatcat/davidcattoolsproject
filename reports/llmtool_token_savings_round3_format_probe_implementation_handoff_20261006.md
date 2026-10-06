# llmtool Round 3 Phase 0 — offline probe implementation handoff (2026-10-06)

## Scope

Implement only the deterministic Round-3 company format-compliance probe and offline
self-tests.

**No live company call.**
**No Codex call.**
**No production/runtime/skill changes.**
**No new benchmark fixtures.**

Repository:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-round3-format-compliance-20261006`

Expected remote head before local work:

`d678c6d0723de0321d26a954a4698939dd4ab4b2`

Never use `/private/tmp/*` worktrees/clones.

## Read first

- `reports/llmtool_token_savings_round3_format_compliance_plan_20261006.md`
- prior Round-3 methodology reviews and targeted rereview
- Round-2 Stage-A frozen helpers/results for L02/L03
- A4 ContextPacket parser/types in `tools/llmtool/llmtool/context.py`

The remediated plan is authoritative.

## Deliverables

Create:

`reports/token_benchmark/20261006_round3/format_probe.py`

Create:

`reports/token_benchmark/20261006_round3/test_format_probe.py`

Create:

`reports/token_benchmark/20261006_round3/README.md`

Do not create live results yet.

A placeholder schema/template for future results is allowed only if it contains no
fabricated live values.

## Design requirements

### General

- deterministic;
- Python 3.9 compatible;
- no network;
- no provider imports/calls;
- no subprocess to Codex/company;
- no gold access in prompt/adapter/anchor paths;
- explicit typed/stable return structures where practical;
- never silently coerce invalid input to valid;
- never infer IDs from free text when structured fields are required.

### L02 adapter

Parse the frozen L02 fixture into the full canonical EVENT universe.

Returned company structures must be classified into:

1. validated required canonical EVENT;
2. invalid canonical-looking claim;
3. genuinely noncanonical extra.

Requirements:

- every required EVENT exactly once in evidence;
- every required EVENT exactly once in timeline;
- timeline must equal the complete expected canonical sequence;
- ordering by timestamp with source-line tie-break;
- full tuple validation:
  - event_id
  - request_id
  - signature
  - timestamp
- duplicate checks independently for evidence/timeline;
- canonical signature counts computed once from validated evidence only;
- extras never count toward coverage/signatures;
- extras never become anchors;
- extra error signatures recorded but not fatal;
- malformed/partial/prose canonical-looking entries cannot satisfy coverage.

Output should expose at least:

- pass/fail;
- validated event IDs;
- missing evidence IDs;
- missing timeline IDs;
- duplicate evidence IDs;
- duplicate timeline IDs;
- invalid canonical claims;
- evidence extras;
- timeline extras;
- canonical signature counts;
- extra signatures;
- timeline-order valid;
- validated anchors;
- primary failure reason.

### L03 adapter

Use the existing A4 parser to parse output.

Build a **full-fixture multimap**:

`(source, locator) -> [all matching RECORD objects]`

Never:
- use last-writer-wins;
- build from gold targets only;
- fuzzy match;
- trim/case-fold to manufacture matches;
- use free-text record_id to resolve ambiguity.

For each A4 evidence item:

- source nonempty;
- locator nonempty;
- exact pair zero matches -> UNKNOWN_STRUCTURED_ANCHOR;
- exact pair >1 matches -> AMBIGUOUS_STRUCTURED_ANCHOR;
- exact pair exactly one match -> candidate unique anchor.

Duplicate mapped unique records:
- record as duplicate;
- fail the gate.

Inventory/background evidence:
- must not be accepted as target evidence;
- benchmark instruction expects no inventory evidence;
- ambiguous inventory citations cannot satisfy recall.

Validated target recall must be calculated only after output and anchors are frozen:

`validated_unique_anchor_record_ids ∩ frozen_target_record_ids`

Required Phase-0 L03 pass:
- parser/schema PASS;
- nonempty unique anchor set;
- unknown=0;
- ambiguous=0;
- duplicate=0;
- target recall = 7/7.

The adapter must not load gold while constructing anchors.
Use a separate grading/evaluation function for target recall.

### Gold-isolation architecture

Implement explicit separation, for example:

- `parse_fixture_*`
- `evaluate_l02_output_without_gold(...)`
- `build_l03_multimap(...)`
- `evaluate_l03_structured_anchors_without_gold(...)`
- `evaluate_l03_target_recall_after_freeze(...)`

The no-gold functions must not accept gold paths/data.

Add a source-level/self-test guard proving adapter/anchor functions do not consume gold.

### Failure attribution

Primary failure precedence must match the plan:

1. TRANSPORT_OR_REQUEST_FAILURE
2. MODEL_OUTPUT_INCOMPLETE
3. SCHEMA_OR_PARSE_FAILURE
4. INVALID_CANONICAL_COVERAGE
5. UNKNOWN_STRUCTURED_ANCHOR
6. AMBIGUOUS_STRUCTURED_ANCHOR
7. DUPLICATE_STRUCTURED_ANCHOR
8. EMPTY_VALIDATED_ANCHOR_SET
9. TARGET_RECALL_BELOW_7_OF_7
10. PASS

Offline tests only exercise deterministic stages; transport/model-incomplete may be
represented by helper/state tests, not live calls.

### Trial/accounting helper

Implement deterministic Phase-0 planning/accounting logic:

Frozen logical order:

1. L02-1
2. L03-1
3. L02-2
4. L03-2
5. L02-3
6. L03-3

Rules:

- maximum one physical request per logical trial;
- no retries;
- per-fixture early stop:
  - L02 failure stops later L02 only;
  - L03 failure stops later L03 only;
- other fixture continues;
- skipped trials get `NOT_STARTED_EARLY_FORMAT_GATE_FAIL`;
- provider usage unavailable for a possibly consumed request -> unknown and halt further
  execution with `COMPANY_TOKEN_ACCOUNTING_UNKNOWN`;
- total company ceiling = 300,000;
- pre-request bounds:
  - L02 = 35,000
  - L03 = 59,000;
- before a request:
  `known cumulative + trial upper bound <= 300000`;
- otherwise mark `COMPANY_TOKEN_CEILING_NOT_STARTED`.

No live execution in this step; test only this state machine.

### Raw stdout helper

Implement deterministic helpers for future live use:

- SHA-256 raw stdout bytes before parsing;
- secret-marker/credential-pattern scan;
- return publication-blocking status for suspicious markers;
- do not edit/redact stdout and then use edited bytes for gating;
- replay uses immutable raw bytes.

Synthetic self-tests may use fake secret markers only.

## Required offline tests

### L02

Must cover at least:

- full required evidence + full required timeline + unrelated extras -> PASS;
- empty timeline -> FAIL;
- partial timeline -> FAIL;
- reordered timeline -> FAIL;
- duplicate evidence event -> FAIL;
- duplicate timeline event -> FAIL;
- wrong request_id -> FAIL;
- wrong signature -> FAIL;
- wrong timestamp -> FAIL;
- fabricated event -> FAIL;
- malformed/partial EVENT-like prose cannot satisfy coverage;
- extras cannot change counts;
- extras cannot become anchors;
- extra signatures recorded but nonfatal.

### L03

Must cover at least:

- exact unique source+locator -> PASS anchor;
- valid A4 empty evidence -> FAIL usable-anchor gate;
- null/absent locator -> FAIL;
- unknown pair -> FAIL;
- ambiguous pair -> FAIL;
- duplicate mapped anchor -> FAIL;
- missing free-text record_id with valid source+locator -> PASS;
- misleading free-text record_id cannot override source+locator;
- full fixture multimap collision counts preserved;
- 3/7 target recall -> FAIL;
- 7/7 target recall -> PASS;
- one evidence item may support multiple semantic categories;
- two evidence items for same unique record -> FAIL duplicate;
- no inventory evidence + seven unique target records -> PASS;
- ambiguous inventory citation cannot satisfy recall.

### Accounting/isolation

Must cover at least:

- exactly one request max per logical trial;
- no retry state exists;
- L02 failure stops later L02 while L03 continues;
- L03 failure stops later L03 while L02 continues;
- unknown consumed usage halts all later execution;
- 300k pre-request bound uses cumulative + 35k/59k;
- not-started reasons are explicit/null usage, never zero;
- raw stdout digest precedes parsing;
- secret scan blocks publication;
- request-observer contract fields can be validated without mutation using fake data;
- no-gold adapter path cannot access gold;
- post-freeze recall evaluation cannot mutate anchors/output/trial state.

## Validation

Run with Python 3.9.

At minimum:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
```

If pytest/unittest style is used, keep it runnable in the canonical repo without network.

Also run:

```sh
git diff --check
```

Record exact test count/pass/fail.

## Report

Create:

`reports/llmtool_token_savings_round3_format_probe_offline_validation_20261006.md`

Include:

- exact tested head;
- Python version;
- helper SHA-256;
- test SHA-256;
- test count/results;
- fixture digests checked;
- L03 multimap cardinalities;
- confirmation that no network/company/Codex calls happened;
- confirmation that no production/runtime/skill files changed;
- whether implementation is ready for independent review.

## Commit / stop

Commit/push implementation + offline tests + validation report to PR #38 branch.

Then STOP.

Do not:
- run six company probes;
- call Codex;
- create live results;
- change production/runtime/skills;
- generate realistic fixtures yet;
- merge PR #38.

Live Phase 0 remains blocked until independent implementation review passes.
