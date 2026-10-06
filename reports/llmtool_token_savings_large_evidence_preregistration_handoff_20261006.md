# llmtool Round 2 fixture/gold preregistration — execution handoff（2026-10-06）

## Purpose

Prepare and freeze the formal Round-2 benchmark artifacts after the approved size
preflight. This step creates reproducible fixtures, gold, schemas, coverage/selection
helpers and preregistration metadata.

Do **not** run measured Stage-A Codex control/dogfood sessions in this step.
Do **not** run semantic company benchmark calls in this step.

## Repository

Canonical repository only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-large-evidence-20261006`

Expected remote head before local execution:

`7282456ebe12b712e8ed3d369d2144eb84b58ab8`

Never use `/private/tmp/*` worktrees/clones.

## Approved selected tiers

From the reviewed size preflight:

- L01 test output: **48 KiB**
- L02 operational log: **64 KiB**
- L03 mixed evidence: **128 KiB**

Preflight comparable totals:

- L01: 32,531
- L02: 37,992
- L03: 59,838

Pinned per-pair Stage-A allowances:

- L01: **97,593**
- L02: **113,976**
- L03: **179,514**

Total allowance: **391,083 <= 400,000**.

The measured execution order remains:

```text
L01: CONTROL -> DOGFOOD
L02: DOGFOOD -> CONTROL
L03: CONTROL -> DOGFOOD
```

## Required directory

Create:

`reports/token_benchmark/20261006_round2/stage_a/`

with:

```text
generator.py
coverage.py
selector.py
preregistration.json

L01_large_test/
  fixture.txt
  gold.json
  output_schema.json
  task.txt

L02_large_log/
  fixture.txt
  gold.json
  output_schema.json
  task.txt

L03_large_context/
  fixture.txt
  gold.json
  output_schema.json
  task.txt
```

All artifacts must be committed before any measured Stage-A session.

## Deterministic generator

Create one committed generator with explicit fixed seeds:

- L01 seed: `2026100601`
- L02 seed: `2026100602`
- L03 seed: `2026100603`

Requirements:

- deterministic byte-for-byte regeneration;
- no network;
- no time/random-state dependency beyond the explicit seed;
- no real secrets, credentials or personal data;
- output exact selected sizes within the plan tolerance;
- realistic noise formatting while keeping canonical target records;
- generator must not read gold.

After generation, regenerate each fixture in a clean temporary output path and prove
SHA-256 equality with the committed fixture.

## Fixture design

### L01 — 48 KiB large test output

Use realistic pytest/unit-test style noise:
- pass/progress lines;
- timestamps/durations/paths;
- repeated but non-target warnings;
- four target failure records distributed across distant regions.

Canonical target syntax:

```text
FAILURE test_id=<TEST_ID> file=<PATH> kind=<FAILED|ERROR> signature=<SIGNATURE>
```

Gold must require:
- exact four target test IDs;
- exact file IDs;
- grouping/classification expectations;
- required unknowns;
- forbidden unsupported root-cause claims;
- structured count checks.

At least two failures share one signature/class and at least two others are distinct.

### L02 — 64 KiB operational log

Use realistic timestamped service traffic:
- normal INFO traffic;
- unrelated WARN/ERROR distractors;
- repeated timeout events;
- later recovery/success evidence.

Canonical target syntax:

```text
EVENT event_id=<EVENT_ID> request_id=<REQUEST_ID> signature=<SIGNATURE> ts=<ISO8601>
```

Gold must require:
- exact timeout/recovery identifiers;
- exact structured occurrence counts;
- ordering/timeline expectations;
- required unknowns;
- forbidden causal claims;
- forbidden command recommendations.

Literal substring matching is insufficient; grade structured fields/counts.

### L03 — 128 KiB mixed evidence

Use realistic mixed evidence records with paths/timestamps/numeric values/noise.

Canonical syntax:

```text
RECORD record_id=<RECORD_ID> source=<SOURCE_ID> locator=<LOCATOR> ...
```

Include sparse:
- facts;
- supported inferences;
- unsupported hypotheses;
- unknowns;
- verify-next items.

Gold must require:
- required facts;
- required unknowns;
- forbidden promotions of hypotheses to fact;
- required source references;
- minimum citation requirements.

Keep required semantic item count bounded so company `max_tokens=4096` is sufficient.

## Gold-independence

Gold is grading-only.

The following must not import/read/open/receive gold:
- generator.py
- coverage.py
- selector.py
- company prompt construction
- selected-range construction

Add a simple static/runtime assertion proving those helpers do not accept a gold path.

## coverage.py

Freeze fixture-specific syntactic coverage rules.

L01:
- anchored regex parses only canonical FAILURE lines;
- extracts fixture failure-ID universe;
- compares validated company IDs;
- reports missing/duplicate/unexpected IDs and complete boolean.

L02:
- anchored regex parses canonical EVENT lines;
- computes coverable signature counts, event/request ID sets and timestamp order;
- compares company validated IDs/signatures;
- claims syntactic coverage/order only, never causality.

L03:
- parses canonical RECORD identifiers/source/locator;
- verifies cited identifiers exist and selected ranges contain them;
- claims citation membership only;
- must not claim semantic completeness.

## selector.py

Freeze exact deterministic selection rules:

- anchors are validated company-returned identifiers only;
- ±2 lines around each anchor;
- merge padded intervals if overlapping or separated by <=2 lines;
- sort by source line;
- maximum 32 pre-merge ranges;
- no manual or semantic range expansion;
- cap = min(32 KiB, 20% of fixture bytes).

Selector output must include:
- selected ranges;
- selected byte count;
- selected percentage;
- manifest SHA-256;
- concatenated selected raw bytes SHA-256.

Selection occurs before gold grading.

## DOGFOOD delivery contract

Measured DOGFOOD Codex will receive in its initial prompt:
- company result;
- gold-independent validation/coverage result;
- frozen selected-range metadata;
- verbatim raw bytes from frozen selected ranges;
- task/schema.

Expected raw evidence tool calls: **0**.

Do not implement/run the measured session now; only freeze the contract/artifacts.

## Cache metadata preregistration

Primary KPI remains:
`input_tokens + output_tokens`.

Secondary remains:
`(input_tokens - cached_input_tokens) + output_tokens`.

For every future measured session preregister fields:
- prior measured Codex session ID;
- prior fixture/variant;
- elapsed wall time since prior measured Codex session;
- cached_input_tokens.

Do not add nonce or other cache manipulation in Round 2. Record cache background rather
than changing prompts solely to influence cache.

## Budget start rule

Future Stage A must use the pinned pair allowances:

- before L01, remaining budget must be >=97,593;
- before L02, remaining budget must be >=113,976;
- before L03, remaining budget must be >=179,514.

After each **complete pair**, subtract actual measured comparable Codex usage from the
400,000 ceiling.

Do not stop a pair mid-run because usage looks unfavorable.

If remaining budget before the next pair is less than that pair's pinned allowance,
do not start it; record `BUDGET_CEILING_NOT_STARTED`.

## Failure flags

Freeze these distinct flags:

- `COMPANY_TRIAGE_COVERAGE_MISS`
- `SELECTIVE_READ_CAP_INSUFFICIENT`
- `DELIVERY_CONTRACT_VIOLATION`
- `QUALITY_PARITY_FAIL`
- `BUDGET_CEILING_NOT_STARTED`

Do not collapse them into one generic invalid flag.

## Blind grading package contract

Prepare anonymous output slots only; no measured outputs exist yet.

Future semantic grading must use two independent blind graders.

Graders may read only:
- anonymous final output;
- fixture;
- gold;
- output schema;
- task.

They must not read:
- variant mapping;
- token metrics;
- company output;
- PR description/results report.

Record agreement/disagreement; unresolved disagreement cannot enter quality-approved
aggregates.

## Preregistration metadata

`preregistration.json` must contain at least:
- selected tiers;
- seeds;
- generator/helper SHA-256;
- fixture/gold/schema/task SHA-256;
- exact run order;
- Codex version/model/effort/service/sandbox/session requirements;
- company profile requirements including `enable_thinking=false`;
- selector/coverage rules;
- raw-byte cap;
- primary/secondary metrics;
- pair allowances and 400k ceiling;
- Stage-B trigger;
- failure flags;
- blind-grading contract;
- statement that all artifacts were frozen before measured execution.

## Validation before commit

Before committing:
- regenerate fixtures and compare digests;
- validate exact selected sizes/tolerances;
- validate canonical target counts;
- run coverage.py against fixture syntax without gold;
- run selector unit/self-checks using synthetic company-ID examples only;
- prove cap calculations;
- validate JSON schemas;
- `git diff --check`.

Do not make live company calls.
Do not call Codex for measured semantic work.

## Required report

Create:

`reports/llmtool_token_savings_large_evidence_preregistration_20261006.md`

It must state:
- fixture sizes/digests;
- seeds;
- helper/digest reproducibility;
- target counts;
- coverage/selector self-check result;
- raw caps:
  - L01 <= 9.6 KiB;
  - L02 <= 12.8 KiB;
  - L03 <= 25.6 KiB;
- run order;
- pair allowances;
- blind grading contract;
- whether Stage-A execution may proceed after independent review.

## Stop point

Allowed:
- create/freeze generator, fixtures, gold, schemas, coverage/selector helpers;
- commit/push to existing PR #37 branch;
- update PR description.

Forbidden:
- live semantic company benchmark calls;
- measured Stage-A Codex sessions;
- Stage B;
- production code/config changes;
- router/budget enforcement;
- A11b.2/A11c;
- merge PR #37.

After publication, STOP for independent review.
