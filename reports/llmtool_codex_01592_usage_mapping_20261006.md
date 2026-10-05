# Codex CLI 0.159.2 token accounting mapping decision — 2026-10-06

## Decision

For this benchmark only, a fresh single-turn ephemeral `codex exec --json` session may
derive the comparable Codex total as:

```text
codex_comparable_total = usage.input_tokens + usage.output_tokens
```

This is an **accepted accounting mapping**, not a guessed tokenizer estimate.

## Exact-version source evidence

Inspected upstream repository `openai/codex` at exact release tag
`rust-v0.159.2`.

1. `codex-rs/exec/src/exec_events.rs`

The JSONL `turn.completed.usage` public struct contains:
- `input_tokens`
- `cached_input_tokens`
- `cache_write_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`

It intentionally does not expose `total_tokens`.

2. `codex-rs/protocol/src/protocol.rs`

Internal `TokenUsage` contains the same token dimensions plus `total_tokens`.

3. `codex-rs/codex-api/src/sse/responses.rs`

`ResponseCompletedUsage` accepts upstream:
- `input_tokens`
- input token details
- `output_tokens`
- output token details
- `total_tokens`

and copies those fields into internal `TokenUsage`, including
`total_tokens: val.total_tokens`.

The mapping therefore reconstructs the omitted public total from the two public
top-level components of the same usage object. Cached input, cache-write input and
reasoning output remain breakdown fields and are not added again.

## OpenAI usage semantics

The OpenAI Responses usage contract defines:
- cached tokens as part of input tokens;
- reasoning tokens as part of output tokens;
- total tokens as the total input + output usage.

Therefore:

```text
DO:
  comparable_total = input_tokens + output_tokens

DO NOT:
  + cached_input_tokens
  + cache_write_input_tokens
  + reasoning_output_tokens
```

Adding those breakdowns again would double-count.

## Scope restriction

This acceptance applies only when all measured Codex variants use:

- Codex CLI 0.159.2;
- `codex exec --json`;
- fresh session;
- one benchmark turn;
- `--ephemeral`;
- no resume/reuse;
- identical model/effort/speed/session configuration across paired variants.

It must not be generalized to resumed/multi-turn sessions. Codex 0.159.2 has public
reports that resumed-turn usage can represent accumulated thread totals rather than
strict per-turn deltas. The benchmark avoids that path entirely.

If the Codex CLI version or usage schema changes during the pilot, stop and revalidate
the mapping before continuing.

## Preflight example

Observed fresh preflight:

```json
{
  "input_tokens": 17473,
  "cached_input_tokens": 12288,
  "cache_write_input_tokens": 0,
  "output_tokens": 9,
  "reasoning_output_tokens": 0
}
```

Accepted comparable total:

```text
17473 + 9 = 17482
```

This preflight remains excluded from B01-B03 benchmark totals.

## Benchmark consequence

The earlier `CODEX_TOKEN_MEASUREMENT_BLOCKED` state was correct at the time because
no accepted mapping existed. This decision closes that accounting blocker.

The pilot is still NOT EXECUTED. It may resume only after:
- fixture/gold pre-registration;
- fixed run order already recorded;
- same explicit Codex settings for all six sessions;
- company-provider/VPN preflight;
- deterministic identifier/schema/coverage validation;
- required raw-evidence verification;
- blind/independent semantic grading where required.

No token-saving claim exists until comparable B01-B03 pairs are actually measured.
