# llmtool Token Savings Benchmark / Dogfood ROI pilot plan — 2026-10-06

## Goal

Measure how many **Codex tokens** development-time company-LLM delegation actually
saves, while separately measuring whole-system token usage and preserving comparable
quality.

This plan does not assume savings. A negative result is valid.

Current evidence proves company dogfood feasibility and reports company-model usage
for the live PR #34 samples (roughly 850–1760 total tokens per sampled call), but it
does **not** contain a paired Codex baseline. Therefore no Codex token-saving percentage
is established yet.

## Measurement contract

Each benchmark pair must use:

- the same bounded fixture/input bytes;
- the same repository/test revision;
- the same Codex model;
- the same reasoning effort and speed setting;
- the same task wording and acceptance criteria except for the explicitly defined
  company-delegation step;
- a fresh Codex session for each variant;
- no answer/context leakage between variants.

Record the exact measurement source for every usage value. If the active Codex client
only exposes allowance/credit usage and not comparable per-session/per-task token
counts, keep token fields `unknown`; do not infer tokens from allowance percentage,
credits, context bytes or pricing.

## Variants

### Control

Codex receives the original bounded fixture and performs the requested analysis itself.
No company LLM call is allowed.

### Dogfood

The same bounded fixture is deterministically prepared/redacted, delegated through
`company-llm-delegation` to the configured company provider using the specified
runtime skill, and the derived result is returned to Codex. Codex still owns strong
judgement and final verification.

## Pilot fixtures

### B01 — test failure triage

Bounded synthetic test output containing repeated ordinary failures and one distinct
failure. No secrets.

Dogfood skill: `test-triage`.

Acceptance: identify the supported failure groups without inventing identifiers; final
answer must preserve unknowns and not claim unsupported root cause.

### B02 — log triage

Bounded synthetic operational log with repeated timeout signatures and a later recovery
line. Pre-redacted before either variant. No real credentials.

Dogfood skill: `log-triage`.

Acceptance: identify signatures/patterns/timeline supported by the log; no unsupported
root-cause or command recommendation.

### B03 — evidence compression

Bounded mixed evidence requiring facts/evidence/inferences/hypotheses/unknowns/
verify-next separation.

Dogfood path: parsed `llmtool context-pack --json`.

Acceptance: A4 parser success plus equivalent required facts/unknowns preserved for the
final Codex judgement.

## Per-run record

```text
benchmark_id:
fixture_id:
variant: control | dogfood
repo_sha:
fixture_sha256:
codex_model:
codex_reasoning_effort:
codex_speed:
fresh_session: true | false

codex_usage_source:
codex_usage_precision: exact | reported_aggregate | unknown
codex_input_tokens:
codex_cached_input_tokens:
codex_output_tokens:
codex_reasoning_tokens:
codex_total_tokens:

company_profile:
company_skill:
company_usage_source:
company_prompt_tokens:
company_completion_tokens:
company_total_tokens:

input_bytes_to_codex:
input_bytes_to_company:
company_output_bytes:
tool_call_count:
wall_clock_seconds:

schema_valid:
identifier_valid:
company_result_adopted: true | false | partial
codex_override_reason:
quality_result: pass | fail | incomplete
verification_reference:
notes:
```

Token subfields are descriptive breakdowns. Do not add cached/reasoning fields to a
source-reported `total_tokens` unless the accounting source explicitly states that
they are excluded.

## Pair calculations

Only calculate when the required totals are known and comparable.

```text
codex_tokens_saved
  = control.codex_total_tokens - dogfood.codex_total_tokens

codex_savings_pct
  = codex_tokens_saved / control.codex_total_tokens * 100

dogfood_system_total
  = dogfood.codex_total_tokens + dogfood.company_total_tokens

whole_system_tokens_saved
  = control.codex_total_tokens - dogfood_system_total

whole_system_savings_pct
  = whole_system_tokens_saved / control.codex_total_tokens * 100
```

If any required value is unknown, the dependent result is also unknown.

## Pilot execution size

Start with one control/dogfood pair for B01–B03: six fresh Codex sessions total.
This deliberately limits benchmark overhead. If a fixture has a small apparent margin,
quality mismatch, anomalous usage or inconsistent accounting source, do not generalize
from it; add paired repetitions later.

## Summary table

| Fixture | Control Codex | Dogfood Codex | Company | Codex saved | Codex saving % | Whole-system saved | Quality parity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| B01 test triage | pending | pending | pending | pending | pending | pending | pending |
| B02 log triage | pending | pending | pending | pending | pending | pending | pending |
| B03 context pack | pending | pending | pending | pending | pending | pending | pending |
| **Overall comparable pairs** | pending | pending | pending | pending | pending | pending | pending |

Do not aggregate pairs with unknown Codex totals into token-saving percentages.

## Interpretation

A delegation class is a strong candidate for future company-first routing only when:

1. quality/acceptance is comparable;
2. Codex premium-model usage is materially lower across repeated comparable pairs;
3. company output does not create excessive override/rework;
4. latency remains acceptable;
5. safety/data-policy boundaries remain satisfied.

This pilot is measurement evidence only. It does not start A11b.2/A11c or implement an
automatic token/cost router.
