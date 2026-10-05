# llmtool Codex 0.159.2 token-accounting mapping — Claude review handoff（2026-10-06）

## Purpose

This report asks for an independent review of the accounting decision that unblocks the
paired Codex token-savings benchmark.

It does **not** claim that company-LLM dogfood has saved any tokens yet. B01–B03 remain
NOT RUN.

## Current repository state

- Repository: `davidcatcat/davidcattoolsproject`
- PR: #36
- Branch: `bench/llmtool-token-savings-pilot-20261006`
- Base main at pilot start: `f4d1019d984105431485ad364861ef07cc4dd887`
- Accounting mapping decision:
  `reports/llmtool_codex_01592_usage_mapping_20261006.md`
- Pilot report:
  `reports/llmtool_token_savings_pilot_20261006.md`

The original pilot correctly stopped at preflight because `codex exec --json`
`turn.completed.usage` did not expose `total_tokens`.

Observed preflight usage:

```json
{
  "input_tokens": 17473,
  "cached_input_tokens": 12288,
  "cache_write_input_tokens": 0,
  "output_tokens": 9,
  "reasoning_output_tokens": 0
}
```

No total was guessed at that time.

## Exact-version evidence reviewed

Coordinator inspected upstream `openai/codex` at exact release tag:

`rust-v0.159.2`

### 1. Public exec JSON schema

File:

`codex-rs/exec/src/exec_events.rs`

The public `turn.completed.usage` `Usage` struct contains:

- `input_tokens`
- `cached_input_tokens`
- `cache_write_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`

It does not expose `total_tokens`.

### 2. Internal Codex usage model

File:

`codex-rs/protocol/src/protocol.rs`

Internal `TokenUsage` contains:

- `input_tokens`
- `cached_input_tokens`
- `cache_write_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`
- `total_tokens`

### 3. Responses usage parsing

File:

`codex-rs/codex-api/src/sse/responses.rs`

`ResponseCompletedUsage` contains:

- `input_tokens`
- input token details
- `output_tokens`
- output token details
- `total_tokens`

Its conversion to internal `TokenUsage` copies:

```text
input_tokens              <- provider input_tokens
cached_input_tokens       <- input_tokens_details.cached_tokens
cache_write_input_tokens  <- input_tokens_details.cache_write_tokens
output_tokens             <- provider output_tokens
reasoning_output_tokens   <- output_tokens_details.reasoning_tokens
total_tokens              <- provider total_tokens
```

Therefore the public exec JSON omits the already-existing total rather than using a
different accounting model.

## Accepted benchmark mapping

For this benchmark only:

```text
codex_comparable_total
  = usage.input_tokens
  + usage.output_tokens
```

Cached/reasoning/cache-write fields are breakdowns and are **not added again**.

For the accounting-only preflight:

```text
17473 + 9 = 17482
```

That 17,482 value is not part of B01–B03 benchmark totals.

## Why this is considered an accounting mapping rather than an estimate

The decision does not infer tokens from:

- bytes;
- context length;
- quota/allowance percentage;
- credits;
- pricing;
- tokenizer re-encoding.

It maps the two public top-level usage components from the same Codex usage object back
to the omitted total semantics used internally by the exact same Codex 0.159.2 release.

## Double-counting rules

Do **not** calculate:

```text
input
+ cached_input
+ cache_write_input
+ output
+ reasoning_output
```

That would double-count usage dimensions.

The benchmark records cached/reasoning/cache-write only as diagnostic breakdown fields.

## Scope restriction

This mapping is accepted only when all measured runs use:

- Codex CLI `0.159.2`;
- `codex exec --json`;
- fresh session;
- exactly one benchmark turn;
- `--ephemeral`;
- no resume/reuse;
- the same explicit model, reasoning effort, speed/service setting and isolation config
  across each control/dogfood pair.

It is **not** approved for resumed or multi-turn sessions.

If Codex CLI version or usage schema changes, the benchmark must stop and revalidate
the mapping before continuing.

## Benchmark formulas after mapping

Primary metric:

```text
codex_tokens_saved
  = control.codex_comparable_total
  - dogfood.codex_comparable_total

codex_savings_pct
  = codex_tokens_saved
  / control.codex_comparable_total
  * 100
```

Company-model usage remains separate.

The optional mixed-token-volume metric remains descriptive only because Codex and the
company model may use different tokenizers:

```text
dogfood_mixed_token_volume
  = dogfood.codex_comparable_total
  + dogfood.company_total_tokens
```

It must not be presented as cost, compute or energy saving.

## What remains unproven

This mapping only unblocks measurement.

Still NOT RUN:

- B01 test-triage control/dogfood pair;
- B02 log-triage control/dogfood pair;
- B03 context-pack control/dogfood pair;
- fixture/gold pre-registration;
- company-provider benchmark calls;
- blind quality grading.

Therefore:

```text
Can calculate future paired Codex totals: YES
Can claim company LLM saves Codex tokens now: NO
Measured saving percentage now: NONE / NOT RUN
```

## Requested Claude review

Please independently verify:

1. Exact-tag evidence really supports the mapping for Codex CLI 0.159.2.
2. `input_tokens + output_tokens` is a defensible comparable total for the specified
   fresh single-turn exec shape.
3. `cached_input_tokens`, `cache_write_input_tokens` and
   `reasoning_output_tokens` must not be added again.
4. The mapping does not rely on quota, bytes, credits, pricing or re-tokenization.
5. The scope restriction to fresh one-turn ephemeral sessions is sufficient.
6. The decision does not accidentally authorize resumed/multi-turn accounting.
7. The pilot still correctly makes no savings claim before B01–B03 are executed.
8. No A11b.2/A11c runtime work is introduced.

If acceptable, please output exactly:

`CODEX 0.159.2 TOKEN ACCOUNTING MAPPING: PASS`

Then list:

- mapping valid: YES/NO
- double-counting rule valid: YES/NO
- fresh single-turn restriction sufficient: YES/NO
- new blocker: 0 or details
- B01–B03 pilot may proceed: YES/NO

Do not run the benchmark.
Do not merge PR #36.
Do not start A11b.2 or A11c.
