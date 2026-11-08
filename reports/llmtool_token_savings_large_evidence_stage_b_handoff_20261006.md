# llmtool Round 2 Stage B — L01 reverse-order confirmation handoff（2026-10-06）

## Purpose

Run exactly one preregistered Stage-B confirmation pair for **L01 only**.

Stage A established one quality-approved valid pair:
- L01 CONTROL = 34,235
- L01 DOGFOOD = 20,604
- Codex saved = 13,631
- savings = 39.81597780049657%
- two independent blind graders: PASS/PASS, pair EQUIVALENT.

L01 alone meets the frozen Stage-B trigger.

This Stage-B run is confirmation evidence for a selected positive candidate.
It is subject to winner's-curse / selection bias and must remain separate from Stage A.
Do not pool Stage A + Stage B as an unbiased overall savings estimate.

## Repository

Canonical repository only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-large-evidence-20261006`

Expected remote head before local execution:

`4597f04d07c59b3ee5728c4b606fd6d439107564`

Never use `/private/tmp/*` worktrees/clones.

If remote head moved, fetch and inspect the delta before proceeding.

## Frozen artifacts

All Round-2 preregistration artifacts remain immutable:
- L01 fixture/gold/schema/task
- generator.py
- coverage.py
- selector.py
- preregistration.json

Before live calls:
- recompute all registered artifact SHA-256;
- rerun the preregistration self-check on Python 3.9;
- confirm Codex CLI = 0.159.2;
- confirm branch/repo identity.

Any mismatch -> STOP `PREREGISTRATION_DRIFT`.
Any Codex version/schema drift -> STOP `CODEX_USAGE_SCHEMA_CHANGED`.

## Scope

Run **L01 only**.

Do not run L02 or L03.
Do not modify Round-2 failure contracts based on Stage-A observations.
Do not run Round 3.
Do not merge PR #37.

## Reverse-order requirement

Stage A order for L01 was:

```text
CONTROL -> DOGFOOD
```

Stage B must use the preregistered reverse order:

```text
1. L01 DOGFOOD
2. L01 CONTROL
```

No reordering.

## Stage-B budget

Separate Stage-B ceiling: **200,000 comparable Codex tokens**.

The entire L01 confirmation pair must fit within this ceiling.

Do not reuse Stage-A's remaining 400k as the Stage-B ceiling.
Do not pool Stage-A and Stage-B budget accounting.

Record:
- Stage-B measured Codex total;
- Stage-B remaining budget;
- all company usage separately.

Never stop a pair halfway based on favorable/unfavorable results.

## Codex measured configuration

Use the exact same frozen Codex configuration as Stage A:

- codex-cli 0.159.2
- codex exec --json
- --ephemeral
- --ignore-user-config
- --ignore-rules
- model gpt-6.1-sol
- reasoning high
- service tier default
- approval never
- sandbox read-only
- fresh thread
- exactly one turn
- no resume
- frozen L01 output schema.

Usage source:
`turn.completed.usage`

Primary:
`input_tokens + output_tokens`

Secondary:
`(input_tokens - cached_input_tokens) + output_tokens`

Record:
- input/cached/cache-write/output/reasoning-output;
- comparable total;
- thread/turn counts;
- tool calls;
- wall time;
- prompt bytes/digest;
- JSONL digest;
- cache-background metadata.

No nonce/cache manipulation.

## L01 DOGFOOD company phase

Use the exact same frozen company configuration and L01 company instruction used in
Stage A:

- llmtool only;
- isolated temporary HOME;
- openai-compatible;
- base_url http://home-llm.example:8000;
- model gw;
- timeout 120;
- null key/key-env;
- enable_thinking=false;
- temperature 0;
- max_tokens 4096.

Narrow observer must again prove the actual unchanged outgoing Request contains
`chat_template_kwargs.enable_thinking=false`, model=gw, temperature=0,
max_tokens=4096 and no Authorization header.

Attempt policy:
- one initial attempt;
- at most one same-config manual retry for transient transport only;
- no retry for semantic/coverage/length failure;
- no raised max_tokens;
- no fallback.

Input is the full frozen L01 fixture plus the frozen L01 company instruction using the
existing test-triage skill.

## Gold-independent coverage and selector

Exactly as Stage A:

1. validate company output/schema;
2. run frozen L01 coverage.py without gold;
3. require complete canonical L01 failure/file membership;
4. use only validated anchors;
5. run frozen selector.py;
6. freeze selected manifest/raw bytes before gold grading;
7. do not repair/widen after seeing output.

If company coverage fails:
- flag COMPANY_TRIAGE_COVERAGE_MISS;
- do not use gold to repair;
- Stage-B confirmation pair becomes invalid.

If selector cap/range contract fails:
- flag SELECTIVE_READ_CAP_INSUFFICIENT.

## L01 DOGFOOD Codex input

Initial prompt only:
- company result;
- gold-independent coverage result;
- frozen selected-range metadata;
- selected verbatim raw bytes;
- frozen task;
- frozen output schema.

No full fixture.
Expected tool calls: 0.

Any additional raw/tool read:
- retain usage;
- DELIVERY_CONTRACT_VIOLATION;
- invalid confirmation.

## L01 CONTROL

CONTROL gets:
- full frozen L01 fixture;
- same frozen task;
- same frozen output schema.

No company result/metadata.
No other variant output.
Expected tool calls: 0.

## Deterministic grading

After both Stage-B L01 variants complete:
- schema validation;
- frozen structured L01 gold checks;
- preserve every miss;
- no rerun-to-improve;
- no artifact amendment.

Semantic remainder requires new independent blind grading.

## Stage-B blind package

Create separate Stage-B anonymous package, not mixed with Stage A:

`reports/token_benchmark/20261006_round2/stage_b/blind_grading/`

Use new anonymous labels, e.g.:
- L01-C
- L01-D

Privately randomize variant mapping.
Do not commit mapping.

Two independent blind graders must later see only:
- anonymous output;
- frozen L01 fixture;
- frozen L01 gold;
- frozen L01 output schema;
- frozen L01 task.

Do not run graders in the measured-execution step unless separately authorized.
Prepare package, then stop for review.

## Reporting

Create:

`reports/llmtool_token_savings_large_evidence_stage_b_20261006.md`

Create:

`reports/token_benchmark/20261006_round2/stage_b/results.json`

Report:

- DOGFOOD Codex total;
- CONTROL Codex total;
- Stage-B tokens saved / savings %;
- secondary non-cached totals;
- company tokens/attempts;
- selected raw bytes/%/ranges;
- coverage/selector/delivery validity;
- deterministic grades;
- semantic status = BLIND_REVIEW_REQUIRED;
- Stage-B budget total/remaining;
- exact order/cache metadata.

## Interpretation

Before blind grading:
- savings are provisional only.

After independent quality review:
- if L01 Stage-B is quality EQUIVALENT and validity gates PASS, describe it only as a
  **reverse-order confirmation** of the positive L01 direction;
- do not average/pool Stage A and Stage B as an unbiased estimate;
- do not claim a stable 40% savings rate;
- do not convert it into a production routing threshold;
- do not generalize beyond synthetic canonical L01.

Useful descriptive comparison after quality review:
- Stage A L01: +39.816%
- Stage B L01: report separately
- direction consistency: positive/negative
- approximate magnitude consistency: descriptive only.

## Final execution summary format

```text
Stage B L01 confirmation:

Order:
DOGFOOD -> CONTROL

DOGFOOD Codex:
CONTROL Codex:
Provisional Codex tokens saved:
Provisional savings %:

Company tokens:
Selected raw bytes/%:
Coverage:
Selector:
Delivery:
Deterministic DOGFOOD:
Deterministic CONTROL:
Blind review: PENDING

Stage-B measured Codex total:
Stage-B remaining 200k budget:

Confirmation validity:
PENDING BLIND REVIEW

Can claim stable/generalized savings:
NO
```

## Stop point

Allowed:
- execute this one L01 reverse-order pair;
- run required L01 company triage;
- create results/report and separate blind package;
- commit/push to existing Draft PR #37.

Forbidden:
- L02/L03 Stage B;
- Stage A rerun;
- Round-2 artifact amendments;
- Stage B blind grading in this execution step;
- Round 3;
- production changes;
- router/budget enforcement;
- A11b.2/A11c;
- merge;
- generalized/stable savings claim.

After publication, STOP for independent review.
