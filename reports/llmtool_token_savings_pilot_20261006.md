# llmtool Token Savings / Dogfood ROI pilot — 2026-10-06

Status: **ACCOUNTING PREFLIGHT UNBLOCKED; B01–B03 PILOT NOT YET EXECUTED**.

This is a preflight blocker report. The planned pilot remains **NOT EXECUTED**;
there are no measured CONTROL/DOGFOOD pairs and no token-savings or quality claim.
Any eventual results would be **PRELIMINARY / DESCRIPTIVE PILOT**, with n = 1 pair
per fixture, rather than evidence of statistically proven savings.

## Repository and authoritative rules

- Canonical Mac repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- GitHub repository: `davidcatcat/davidcattoolsproject`.
- Fetched `origin/main` and preflight repository SHA:
  `f4d1019d984105431485ad364861ef07cc4dd887` (PR #35 merge).
- Benchmark branch: `bench/llmtool-token-savings-pilot-20261006`, created from that SHA.
- Read: `AGENTS.md`, `tools/llmtool/WORKFLOW.md`, `tools/llmtool/ROADMAP.md`,
  `skills/company-llm-delegation/SKILL.md`, and the benchmark plan/review/re-review.
- Rules: deterministic first → company LLM bounded triage → strong Codex judgement;
  raw evidence remains ground truth.

References:

- [Benchmark plan](llmtool_token_savings_benchmark_plan_20261006.md)
- [PR #35 review](llmtool_token_savings_benchmark_review_20261006.md)
- [PR #35 re-review](llmtool_token_savings_benchmark_rereview_20261006.md)

## Accounting preflight actually executed

`codex --version` reported **codex-cli 0.159.2**. `codex exec --help` was inspected.
One fresh accounting-only invocation ran from the canonical repository, with no
resume and no benchmark fixture. It is not one of the six planned measured sessions.

Explicit requested settings:

| Setting | Value |
|---|---|
| Codex model | `gpt-6.1-sol` |
| Reasoning effort | `high` |
| Speed/service config | `service_tier="default"` |
| Sandbox | `read-only` |
| Approval policy | `never` |
| Session/config isolation | `--ephemeral --ignore-user-config --ignore-rules` |
| Measurement source | `codex exec --json / turn.completed` |

The selected local model/effort configuration also specified `gpt-6.1-sol` / `high`.
These are configuration/request evidence, not attestation of the coordinator's
actual runtime model. The JSONL events did not report model identity or delivered
service tier; neither is inferred from configuration.

Exact preflight command:

```sh
codex exec --json --ephemeral --ignore-user-config --ignore-rules \
  --model gpt-6.1-sol \
  --config 'model_reasoning_effort="high"' \
  --config 'service_tier="default"' \
  --config 'approval_policy="never"' \
  --sandbox read-only \
  --cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject \
  'Reply with exactly TOKEN_USAGE_PREFLIGHT_OK. Do not use tools, read files, inspect configuration, change files, or call any other provider. This is only a token-accounting preflight, not a benchmark task.'
```

- Exit status: **0**.
- Wrapper-measured wall clock: **9.037 seconds**.
- Event types observed: `thread.started`, `turn.started`, `item.completed`,
  `turn.completed`.
- Actual `turn.completed.usage`, retaining original field names:

```json
{
  "input_tokens": 17473,
  "cached_input_tokens": 12288,
  "cache_write_input_tokens": 0,
  "output_tokens": 9,
  "reasoning_output_tokens": 0
}
```

Per-session input/output usage exists. **`total_tokens` is absent**; the field
named `reasoning_tokens` is also absent. The reported `reasoning_output_tokens`
and `cache_write_input_tokens` fields are retained as reported, not renamed.
Missing fields are **unknown**, not zero.

The original preflight correctly stopped because no accepted mapping had yet been established. A subsequent coordinator review inspected the exact upstream Codex CLI 0.159.2 source and OpenAI usage semantics and accepted a bounded mapping for fresh single-turn ephemeral sessions:

```text
codex_comparable_total = input_tokens + output_tokens
```

Cached input, cache-write input and reasoning output remain breakdown fields and must not be added again. For the observed preflight this yields `17473 + 9 = 17482`; this preflight is still excluded from B01–B03 totals. See [the accounting mapping decision](llmtool_codex_01592_usage_mapping_20261006.md).

Quota, credits, allowance, context bytes, and pricing remain prohibited as token substitutes. The mapping is version/session-shape scoped and must be revalidated if the Codex CLI version or usage schema changes.

Raw JSONL SHA-256:
`e61d3bed0e2308f56ed69f328a95b61dfa75470b3a04d09a15e88c686f04b7a4`.
Raw JSONL and stderr remain temporary runtime artifacts outside the repository;
neither full trace nor credentials are included in this report or publication.

## Planned execution state

The requested fixed order is retained below. No measured session has started, so
this report is not evidence of completed fixture/gold pre-registration.

| Fixture | Planned order | Execution |
|---|---|---|
| B01 test-triage | CONTROL → DOGFOOD | NOT RUN |
| B02 log-triage | DOGFOOD → CONTROL | NOT RUN |
| B03 context-pack | CONTROL → DOGFOOD | NOT RUN |

- Fixtures, gold, output schemas and fixture/gold digests: **NOT CREATED**.
- Configured company profile: **NOT TESTED**; no profile was created or changed.
- VPN readiness: **NOT TESTED** in this preflight.
- Company calls, including failures/manual retries: **0**.
- Company token usage and latency: **unknown / NOT RUN**.
- Benchmark Codex tool-call counts and latency: **NOT RUN**.
- Raw fixture bytes available/read and read ranges: **N/A**, no fixtures/sessions.
- Schema validity, identifier invalid count and coverage: **NOT TESTED**.
- Company claims adopted/overridden: **N/A**.
- Gold grading and anonymous grading outputs: **NOT CREATED**.
- Blind semantic review: **NOT STARTED**, no outputs available to review.

## Results

| Fixture | Control Codex total | Dogfood Codex total | Company total | Codex saved | Codex saving % | Mixed token-volume delta | Quality |
|---|---:|---:|---:|---:|---:|---:|---|
| B01 | unknown | unknown | unknown | unknown | unknown | unknown | NOT TESTED |
| B02 | unknown | unknown | unknown | unknown | unknown | unknown | NOT TESTED |
| B03 | unknown | unknown | unknown | unknown | unknown | unknown | NOT TESTED |

All rows are **NOT RUN**. The accounting preflight is excluded from benchmark
totals. Dependent results remain unknown; zero company calls is not a reported
company token total.

```text
B01 Codex saving: unknown — NOT RUN
B02 Codex saving: unknown — NOT RUN
B03 Codex saving: unknown — NOT RUN

Overall comparable Codex control tokens: unknown
Overall comparable Codex dogfood tokens: unknown
Overall Codex tokens saved: unknown
Overall Codex saving %: unknown

Company tokens consumed: unknown — NOT RUN, 0 calls
Mixed token-volume delta: unknown

Quality: NOT TESTED
Invalid pairs: N/A — 0 pairs executed
Blind review pending: NOT STARTED — no grading outputs

Can claim token savings: NO
```

Mixed token-volume delta, if eventually available, combines different tokenizers
and is descriptive only. It cannot establish cost, compute, or energy savings.
No production routing threshold or company-first policy follows from this report.

## Publication scope and remaining gate

Only this report is added. Production llmtool behavior, user configuration, and
historical reports are unchanged. Existing benchmark/reasoning artifacts and the
three pre-existing `tools/llmtool/case.*` directories are preserved. No clone,
worktree, retry/fallback implementation, automatic router, A11b.2 or A11c work
was performed. There is no merge-readiness assertion.

The comparable-total accounting blocker is now closed by the accepted Codex CLI 0.159.2 mapping documented above. Before resuming the pilot, pin the same explicit settings for all six sessions, prepare gold and digests before execution, validate company output mechanically, require raw evidence verification for adoption, and leave semantic quality to blind review. The mapping does not relax any other benchmark requirement.

Documentation validation checks the extracted usage/digest against the local
preflight evidence, repository file references, and whitespace. Application tests
and new benchmark/provider requests are not part of this report-only upload.
The publication commit/PR and exact-head check status are recorded in the PR/final
response rather than creating a recursive commit to embed this report's own SHA.

**ACCOUNTING PREFLIGHT UNBLOCKED — Pilot NOT RUN — Can claim token savings: NO.**
