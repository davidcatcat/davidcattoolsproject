# llmtool Round 2 Stage A — measured execution handoff（2026-10-06）

## Purpose

Execute the frozen Round-2 Stage-A benchmark exactly as preregistered.

This is the first measured semantic Round-2 execution.

Run only the six Stage-A CONTROL/DOGFOOD Codex sessions and the required company
triage calls that feed DOGFOOD.

Do not run Stage B in this step.
Do not modify frozen fixtures/gold/schema/task/generator/coverage/selector/preregistration.

## Repository

Canonical repository only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-large-evidence-20261006`

Expected remote head before local execution:

`7ba13e4af19b1db8b841168de06753fb0d44368b`

Never use `/private/tmp/*` worktrees/clones.

If remote head moved, fetch and inspect the delta before continuing.

## Frozen artifacts

Read and verify before any live call:

- `reports/token_benchmark/20261006_round2/stage_a/preregistration.json`
- `reports/llmtool_token_savings_large_evidence_preregistration_20261006.md`
- `reports/llmtool_token_savings_large_evidence_preregistration_review_20261006.md`
- plan/review/rereview/preflight reports.

Before execution:
- recompute every preregistered artifact SHA-256;
- run the preregistered offline self-check on Python 3.9 with jsonschema;
- confirm no frozen artifact changed;
- confirm Codex CLI is exactly 0.159.2;
- confirm branch/repository identity.

Any frozen-artifact digest mismatch: STOP `PREREGISTRATION_DRIFT`.

Any Codex version/schema drift: STOP `CODEX_USAGE_SCHEMA_CHANGED`.

## Fixed Stage-A run order

Exactly:

```text
1. L01 CONTROL
2. L01 DOGFOOD
3. L02 DOGFOOD
4. L02 CONTROL
5. L03 CONTROL
6. L03 DOGFOOD
```

No reordering.

## Fixed budget rules

Stage-A ceiling: **400,000 comparable Codex tokens**.

Pair allowances:

- L01: 97,593
- L02: 113,976
- L03: 179,514

Start rules:

- before L01: remaining must be >=97,593;
- after the complete L01 pair, subtract actual L01 CONTROL + DOGFOOD comparable usage;
- before L02: remaining must be >=113,976;
- after the complete L02 pair, subtract actual L02 pair usage;
- before L03: remaining must be >=179,514.

If remaining before the next pair is below its fixed allowance:
- do not start either session of that pair;
- record `BUDGET_CEILING_NOT_STARTED`;
- do not record zero usage.

Never stop a pair halfway because results look favorable/unfavorable.

## Codex measured-session configuration

Every measured Codex session must use:

- `codex-cli 0.159.2`
- `codex exec --json`
- `--ephemeral`
- `--ignore-user-config`
- `--ignore-rules`
- model `gpt-6.1-sol`
- reasoning effort `high`
- service tier `default`
- approval `never`
- sandbox `read-only`
- fresh thread
- exactly one turn
- no resume
- same per-fixture output schema for paired variants.

Usage source:

`turn.completed.usage`

Comparable total:

`input_tokens + output_tokens`

Record separately:
- input_tokens
- cached_input_tokens
- cache_write_input_tokens
- output_tokens
- reasoning_output_tokens
- comparable total
- thread.started count
- turn.started count
- turn.completed count
- tool call count
- wall time
- prompt bytes/digest
- JSONL digest
- exit/status.

Missing usage = unknown, never zero.

If failed/incomplete turn has no complete usage, pair is invalid for savings claim.

## Cache-background metadata

Before every measured Codex session record:
- previous measured Codex session ID;
- previous fixture;
- previous variant;
- elapsed seconds since previous measured Codex session;
- cached_input_tokens after completion.

No nonce and no cache manipulation.

Primary KPI remains input+output.
Secondary non-cached metric remains descriptive only.

## CONTROL construction

CONTROL receives:
- same task;
- same final output schema;
- full frozen fixture bytes.

CONTROL receives:
- no company output;
- no DOGFOOD/other variant output;
- no company metadata;
- no tool/raw-read request.

Expected tool calls: 0.

## Company DOGFOOD phase

Use isolated temporary HOME/config.

Required company profile exactly as preregistered:
- openai-compatible
- base_url `http://home-llm.example:8000`
- model `gw`
- timeout 120
- null key/key-env
- `enable_thinking=false`
- temperature 0
- max_tokens 4096.

Use llmtool only.
No direct curl.
No fallback.
Do not mutate real user config.

For every company invocation, narrow request observation must prove the actual outgoing
request contains `chat_template_kwargs.enable_thinking=false`.

If this cannot be proven, STOP before measured DOGFOOD Codex for that fixture.

Company paths:
- L01: existing `test-triage` skill + frozen company instruction.
- L02: existing `log-triage` skill + frozen company instruction.
- L03: existing `context-pack --json` + frozen company instruction.

Input is the entire frozen fixture.

Attempt policy:
- one initial attempt;
- at most one same-config manual retry for transient transport only;
- no retry for length;
- no increased max_tokens;
- no fallback.

Record all attempts including failed attempts.
Unknown failed-attempt usage remains unknown.

L03 known risk:
- if company output hits length limit, mark company phase failed;
- if it cites too many records or cannot fit frozen selector/cap contract, record the
  appropriate frozen failure flag;
- do not repair, reduce, widen or rerun semantically.

## Gold-independent coverage and selection

For each successful company result:

1. Validate company output against its existing runtime schema/parser.
2. Run frozen gold-independent `coverage.py`.
3. If required syntactic coverage fails:
   - flag `COMPANY_TRIAGE_COVERAGE_MISS`;
   - do not use gold to repair company output.
4. Feed only validated anchors to frozen `selector.py`.
5. If selector exceeds 32 ranges/cap/full-fixture rule:
   - flag `SELECTIVE_READ_CAP_INSUFFICIENT`.
6. Freeze selected manifest, selected raw bytes and SHA-256 **before gold grading**.

Do not read gold to decide anchors or selected ranges.

## DOGFOOD measured Codex construction

DOGFOOD initial prompt contains only:
- company result;
- gold-independent coverage result;
- frozen selected-range manifest/metadata;
- verbatim selected raw bytes;
- same task;
- same final output schema.

Do not include complete fixture.

Expected tool/raw evidence calls: **0**.

If Codex performs an additional raw/tool read:
- retain actual usage;
- flag `DELIVERY_CONTRACT_VIOLATION`;
- pair invalid for selective-injection savings claim.

## Post-session deterministic grading

After both variants in a pair exist:

- validate final output schema;
- run frozen structured deterministic checks against gold;
- preserve all failures;
- do not edit fixture/gold/schema/helper/company result/measured output;
- do not rerun a variant to improve quality.

Do not let executing Codex self-grade.

Semantic remainder stays `BLIND_REVIEW_REQUIRED`.

## Blind grading package

After all executable Stage-A pairs finish, prepare six anonymous output files for the
pairs that ran:

- L01-A / L01-B
- L02-A / L02-B
- L03-A / L03-B

Privately randomize variant-to-letter mapping.
Do not commit/publicize mapping in the grading package.

Graders may later receive only:
- anonymous output
- fixture
- gold
- output schema
- task.

Do not run blind graders in this step unless explicitly separately authorized.
This step should prepare the package and stop for review.

## Results and reporting

Create:

`reports/llmtool_token_savings_large_evidence_stage_a_20261006.md`

Create/update:

`reports/token_benchmark/20261006_round2/stage_a/results.json`

Create:

`reports/token_benchmark/20261006_round2/stage_a/blind_grading/README.md`

and anonymous output files for executed pairs.

For each pair report:
- CONTROL Codex comparable total;
- DOGFOOD Codex comparable total;
- provisional Codex saved and savings %;
- secondary non-cached totals/delta;
- company total tokens separately;
- company attempts/retries;
- selected raw bytes and percentage;
- selected range count;
- company coverage result;
- selector/cap validity;
- delivery-contract validity;
- deterministic grade;
- semantic quality status = BLIND_REVIEW_REQUIRED;
- budget usage/remaining;
- cache-background metadata.

Do not calculate a quality-approved aggregate before blind grading.

Mixed token volume:
- report Codex and company totals separately;
- descriptive mixed delta only where all company-attempt usage is known;
- never describe mixed-token delta as cost/compute/energy saving.

## Required final summary

```text
Stage A execution status:

L01:
  control Codex:
  dogfood Codex:
  provisional Codex saving:
  company tokens:
  selected raw bytes/%:
  deterministic grade:
  blind review:
  validity:

L02:
  ...

L03:
  ...

Measured Codex Stage-A total:
Remaining 400k budget:

Pairs executed:
Pairs not started:
Pairs invalid:

Quality-approved aggregate:
NOT AVAILABLE — blind review pending

Can claim Round-2 Codex savings:
NO — blind quality review pending
```

Even if provisional savings are positive, do not claim success before independent blind
quality review.

## Publication and stop point

Allowed:
- execute Stage A under frozen contract;
- generate extracted metrics/results/report and blind grading package;
- commit/push to existing PR #37 branch.

Forbidden:
- change frozen preregistration artifacts;
- run Stage B;
- merge PR #37;
- production code/config changes;
- router/budget enforcement;
- A11b.2/A11c;
- claim quality-approved savings before blind review.

After publication, STOP for independent review.
