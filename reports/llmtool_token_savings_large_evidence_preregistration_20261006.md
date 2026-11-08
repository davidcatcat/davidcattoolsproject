# llmtool Round 2 fixture/gold preregistration — 2026-10-06

**Offline preregistration checks PASS. Stage A remains NOT STARTED and requires independent preregistration review plus explicit execution authorization.**
No semantic company call or measured Codex session was performed in this step. No token-savings or quality-parity result exists for Round 2.

## Contract and provenance

Canonical actual Mac repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Branch: `bench/llmtool-token-savings-large-evidence-20261006`; existing Draft PR #37.

Fetched and fast-forwarded from `7282456ebe12b712e8ed3d369d2144eb84b58ab8` to user-pinned remote HEAD **`41f267c5c538a79eefe2ade84b45e72543703ea4`**. That delta only adds the preregistration handoff. Tracked files were clean; all nine existing untracked benchmark/reasoning/case artifacts are preserved. No reset, stash, clean, alternate checkout, clone or worktree was used.

Direct Task Contract: [preregistration handoff](llmtool_token_savings_large_evidence_preregistration_handoff_20261006.md). Its older expected SHA is superseded by the explicit current user SHA; contents and published ancestry were inspected. Read the [plan](llmtool_token_savings_large_evidence_plan_20261006.md), [methodology review](llmtool_token_savings_large_evidence_plan_review_20261006.md), [PASS re-review](llmtool_token_savings_large_evidence_plan_rereview_20261006.md), [size preflight](llmtool_token_savings_large_evidence_preflight_20261006.md), [APPROVE preflight review](llmtool_token_savings_large_evidence_preflight_review_20261006.md), AGENTS and WORKFLOW. Historical reports are unchanged.

All new artifacts live under [stage_a/](token_benchmark/20261006_round2/stage_a/). Exact source/fixture/gold/schema/task hashes are frozen in [preregistration.json](token_benchmark/20261006_round2/stage_a/preregistration.json). Tests identify uncommitted bytes by those digests at the above base SHA; publication HEAD is recorded in the PR/final reply rather than a self-referential report-only commit.

## Frozen fixtures

| Fixture | KiB | Exact bytes | Seed | Fixture SHA-256 | Canonical target count | Raw cap bytes |
|---|---:|---:|---:|---|---|---:|
| L01 | 48 | 49152 | 2026100601 | `4daf6bb797a23ececd1baedaafc71e939c6c11527901fc8fbc37ea742485d956` | 4 failures | 9830 |
| L02 | 64 | 65536 | 2026100602 | `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da` | 4 events: 3 timeout + 1 recovery | 13107 |
| L03 | 128 | 131072 | 2026100603 | `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce` | 7 upload-window targets + 661 inventory records | 26214 |

Generator version 1 uses an explicit 32-bit LCG (`1664525`, `1013904223`) and fixed seeds, not wall time, ambient randomness or network. Targets are placed in separate equal-sized regions; ordinary progress/warnings, timestamped traffic and numeric inventory records dominate the fixtures. All data, paths, timestamps and identifiers are synthetic. Complete semantic lines are never truncated; final byte padding is one comment line.

Generator/helper SHA-256:

- `generator.py`: `86cd42f0a926a513f406b0965409cc48aa635fd34705cc82b5573be9fd864399`.
- `coverage.py`: `a222e0ca52441552962e4ffce9cb6bab46dd9270c26c2e34b6b442e8f1a2e671`.
- `selector.py`: `dac86deb765d360b867eef524b74b6ffddde963e3a99de688c95979ff4ecd24e`.
- `self_check.py`: `63ea7632968d332225655f6c76ce7503c4d944708c690904d2a9e391f1d8317b`.

Gold was authored as a separate grading artifact after fixture generation. It records exact targets, line locations, allowed identifiers, grouping/counts/citations, unknowns and forbidden claims. It is not an input to generation, coverage, selection or company prompting. Gold/schema/task digests for every fixture are in preregistration.json. None of the future semantic outputs is an input to this preregistration.

- **L01:** four target test IDs in three signature groups: two duplicate-acceptance assertion failures, one timeout, one collection import error. Exact files, FAILED/ERROR kinds, integer counts and causal unknowns are grading requirements.
- **L02:** three `UPSTREAM_TIMEOUT` events and one later `RECOVERY_OBSERVED`, exact event/request/timestamp tuples and occurrence counts. Repeated request labels and temporal ordering do not prove retry, identity, root cause or recovery cause. Inventory WARN/ERROR noise remains separate.
- **L03:** three facts, one supported inference, one unconfirmed hypothesis, one unknown and one verify-next record for upload-window. Source/locator references are exact. Required semantic content is bounded to seven targets; no structural need to emit all 661 inventory records. Actual company fit under 4,096 output tokens is NOT TESTED; a future length failure cannot raise that limit.

Output schemas are final Codex response contracts, with structured counts/IDs rather than substring-only grading. The company phase retains the production test-triage/log-triage/A4 schemas; frozen company formatting instructions are separate from final schemas and never use gold or sample answers.

## Gold-independent coverage

[coverage.py](token_benchmark/20261006_round2/stage_a/coverage.py) receives only raw fixture bytes and company-shaped output. No grading argument/path is accepted.

| Fixture | Mechanical contract | Interpretation limit |
|---|---|---|
| L01 | Anchored FAILURE universe; exact company test IDs and group file membership; missing/duplicate/unexpected IDs; coverage boolean | No root-cause/semantic judgement |
| L02 | Anchored EVENT universe; fixed timeout/recovery signature classes; exact tuples, parsed occurrence counts, request sets and timestamp order | Syntactic coverage/order only, never causality |
| L03 | Existing strict A4 parser; exactly one record_id reference per evidence citation, exact source/locator, no unknown/duplicate citation; selected ranges contain cited line | Citation existence only, never semantic completeness |

The existing log-triage schema has string arrays, not integer count fields. Its frozen adaptation requires each evidence/timeline item to quote an entire canonical EVENT line; counts are derived from parsed exact events, not interpreted prose numbers. Missing, duplicated, fabricated or mismatched events fail coverage. This is a benchmark prompt convention, not a production skill change.

L03 returns no containment verdict before selection; after the manifest is frozen it checks only cited-record containment. It does not widen ranges. Semantic completeness remains gold/blind grading after measured execution.

## Frozen selector and delivery

[selector.py](token_benchmark/20261006_round2/stage_a/selector.py): validated company canonical identifier anchors only; ±2 lines; source order; merge only overlaps or gaps of at most two lines; at most 32 pre-merge ranges; no manual/semantic expansion. Byte slices use exact original bytes with exclusive end offsets. Unknown/noncanonical anchors are rejected. Over-cap/whole-fixture selection is rejected, never silently clipped.

Raw cap is `min(32768, floor(fixture_bytes / 5))`, respecting the exact 20% bound:

- L01: 9,830 bytes (at most 9.6 KiB).
- L02: 13,107 bytes (at most 12.8 KiB).
- L03: 26,214 bytes (at most 25.6 KiB).

Manifest hash is SHA-256 of sorted compact UTF-8 JSON excluding its own hash field. Concatenated original byte slices have a separate SHA-256. Both are frozen before gold grading. No selected manifest for a real company output exists yet.

Offline syntax-only artificial company examples derived from fixture records selected 1,768 / 1,892 / 6,659 bytes for L01/L02/L03, in 4 / 4 / 7 pre-merge ranges. These demonstrate selector/cap mechanics only, **not live company coverage, semantic quality or benchmark results**. They are not fed into any measured session.

Future DOGFOOD initial prompt: company result + mechanical coverage + frozen range metadata + verbatim selected raw bytes + the same task/schema. Full fixture is not injected. Expected raw-evidence tool calls are zero; any additional read/tool call retains consumption and produces DELIVERY_CONTRACT_VIOLATION. CONTROL gets the complete fixture plus the same task/schema, with no company/other-variant output or tools.

## Execution/accounting contracts frozen, not executed

Run order:

```text
L01 CONTROL -> DOGFOOD
L02 DOGFOOD -> CONTROL
L03 CONTROL -> DOGFOOD
```

Requested Codex configuration: CLI 0.159.2; fresh ephemeral JSONL single-turn thread, no resume, gpt-6.1-sol/high/default, approval never, sandbox read-only, ignore user config/rules. The five accepted usage fields and thread/turn counts are pinned. Version/schema/turn-shape drift stops execution. These are preregistered settings, not a new model/runtime attestation in this step.

Company configuration: isolated temporary HOME, company-round2 profile, openai-compatible, `http://home-llm.example:8000`, requested model gw, timeout 120, null key/key-env, enable_thinking=false, temperature 0, max_tokens 4096. Future actual unchanged Request must prove the intended thinking flag. Only llmtool; no real user config mutation. One initial attempt and at most one same-config manual retry for transient transport, retaining all attempt usage/unknowns; no fallback or mid-fixture output-limit increase.

Primary KPI remains `input_tokens + output_tokens`; secondary is `(input_tokens - cached_input_tokens) + output_tokens`, descriptive only. Every future measured session records prior measured session ID, prior fixture/variant, elapsed time since prior Codex session and cached_input_tokens. First prior measured fields are null, with preflight background recorded separately. No nonce or cache manipulation; fresh session does not mean cold cache.

| Pair | Selected-size preflight total | Fixed 3× pair allowance | Start rule |
|---|---:|---:|---|
| L01 | 32,531 | 97,593 | remaining >= 97,593 |
| L02 | 37,992 | 113,976 | remaining >= 113,976 |
| L03 | 59,838 | 179,514 | remaining >= 179,514 |

Fixed forecast is **391,083**; Stage-A ceiling stays **400,000**. Only after a complete pair, subtract its actual comparable usage. Never stop based on good/bad results. Insufficient next allowance means BUDGET_CEILING_NOT_STARTED, never zero consumption. Unknown usage blocks accounting/next start. Actual semantic density/output could exceed preflight forecast; this is a conservative deterministic start rule, not an actual hard per-session usage guarantee.

Distinct flags remain:

```text
COMPANY_TRIAGE_COVERAGE_MISS
SELECTIVE_READ_CAP_INSUFFICIENT
DELIVERY_CONTRACT_VIOLATION
QUALITY_PARITY_FAIL
BUDGET_CEILING_NOT_STARTED
```

Stage B is separately capped at 200,000 and requires Stage-A quality parity, valid accounting/delivery and >=10% primary savings; one reversed-order pair per qualifier with immutable artifacts. It requires separate authorization. Its selected-positive/winner's-curse results cannot be pooled as unbiased overall savings or routing thresholds.

## Blind grading contract

Future anonymous slots: L01-A/B, L02-A/B, L03-A/B; no outputs or variant mapping created now. Two independent blind graders get only anonymous final output, fixture, gold, schema and task. No control/dogfood mapping, token numbers, company output, preregistration metadata or PR/results reports. Future assignment is privately randomized; no variant-to-letter mapping is fixed here.

Parsed exact group/count/ID/citation checks precede semantic review. Executing Codex sessions cannot self-grade. Unknown preservation, causality, evidence fidelity and hypothesis-to-fact promotion need semantic judgement. Grader disagreement leaves quality unresolved and excludes the pair from quality-approved aggregates until a documented independent tie-break. Deterministic misses are retained.

## Offline verification actually performed

Actual Mac canonical venv Python **3.13.3** and system Python **3.9.6**. Existing system jsonschema **4.17.3** was used; no dependency/config installation or modification.

```sh
PYTHONPATH=tools/llmtool /usr/bin/python3 -B reports/token_benchmark/20261006_round2/stage_a/self_check.py
```

**20 tests PASS**, exit 0. Includes all three Draft 2020-12 metaschema validations and positive/negative structured example validation.

```sh
PYTHONPATH=reports/token_benchmark/20261006_round2/stage_a:tools/llmtool tools/llmtool/.venv/bin/python -B -m unittest self_check.GeneratorChecks self_check.CoverageChecks self_check.SelectorChecks self_check.ArtifactChecks.test_gold_targets_match_frozen_fixture_syntax self_check.ArtifactChecks.test_preregistration_hashes_and_fixed_budget
```

**19 tests PASS**, exit 0. The venv lacks jsonschema, so its metaschema test was not run there; that exact test passed on actual Python 3.9 above. No skip is promoted to PASS on the venv.

Evidence covered:

1. All fixtures regenerated in an ordinary temporary output directory; exact byte and SHA-256 equality, exact sizes and different-seed change checks PASS. No repository copy was created.
2. Canonical counts, sparse placement, EVENT order and grading artifact ID/count/reference coherence PASS.
3. Static helper signatures/imports/constants and runtime gold-file read guard PASS; generator/coverage/selector never receive/open grading input. Self-check reads gold only in its separate grading-artifact test, never to build helper inputs.
4. Missing/duplicate/fabricated IDs, file mismatch, EVENT tuple/order mismatch and prose-count rejection PASS.
5. Strict A4 parser/citation membership and post-selection containment PASS; no semantic completeness claim.
6. ±2-line padding, gap=2 merge versus gap=3 separation, source ordering, exact slices/digests, 32 accepted/33 rejected, cap exact-boundary accepted/one-byte excess rejected, full-fixture/unknown-anchor rejection and empty-anchor no-expansion PASS.
7. Artifact SHA-256, raw caps and fixed allowance arithmetic PASS.
8. `git diff --check` PASS; document links and JSON consistency checked before publication.

The small [self_check.py](token_benchmark/20261006_round2/stage_a/self_check.py) is committed for reproducible offline verification. It is not a company prompt or output, measured runner, production module, executor or live test.

Application suites, live Company LLM, measured Codex Stage A, Stage B, blind semantic grading and production QA are **NOT RUN** in this preregistration-only step. No production/skills/tests/CI/user-config changes. Canonical formatting can make triage easier than messy real evidence (N1), so later results remain descriptive for this synthetic mechanically parseable use case.

## Stop gate

```text
Fixture/gold preregistration: COMPLETE — pending independent review
Byte-identical regeneration: PASS
Coverage/selector offline self-checks: PASS
JSON schemas: PASS (actual Python 3.9, jsonschema Draft 2020-12)
400k fixed forecast gate: PASS (391083)
Stage A executed: NO
Company semantic calls: NO
Stage B executed: NO
Stage A may proceed now: NO — independent preregistration review and explicit execution authorization pending
Stage A may proceed after those gates: YES, subject to frozen start/validity rules
```

Stop after publishing to Draft PR #37. No merge, A11b.2, A11c, router, production budget enforcement or second benchmark iteration.
