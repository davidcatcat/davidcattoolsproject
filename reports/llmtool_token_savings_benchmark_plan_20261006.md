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

The same bounded fixture is deterministically prepared/redacted and delegated through
`company-llm-delegation` to the configured company provider using the specified
runtime skill.

Codex initially receives:
- the company-derived summary/packet;
- deterministic schema/identifier-validation results;
- a reference to the original bounded fixture, which remains available for selective
  raw-evidence verification.

The complete raw fixture is **not** automatically injected into Codex context. However,
before Codex relies on any company-derived claim in its final answer/judgement, it must
compare that claim with the relevant original raw evidence. Codex may read only the
required referenced ranges or may read the whole bounded fixture; actual raw bytes/ranges
read must be recorded. If required verification is skipped, the pair is invalid for a
token-savings claim.

Identifier/schema checks should be performed deterministically outside model reasoning
and their result supplied to Codex. This keeps mechanical validation out of the Codex
reasoning budget without weakening the existing company-LLM delegation contract.

## Pilot fixtures

### B01 — test failure triage

Bounded synthetic test output containing repeated ordinary failures and one distinct
failure. No secrets.

Dogfood skill: `test-triage`.

Before execution, commit a gold manifest that fixes the expected group membership,
allowed test/file identifiers, required unknowns and forbidden unsupported root-cause
claims. Gold data and fixture digest must be fixed before either variant runs.

Acceptance: deterministic comparison to the gold manifest where possible; any semantic
remainder is graded by an independent reviewer blinded to control/dogfood identity.

### B02 — log triage

Bounded synthetic operational log with repeated timeout signatures and a later recovery
line. Pre-redacted before either variant. No real credentials.

Dogfood skill: `log-triage`.

Before execution, commit a gold manifest for required signatures/patterns/timeline facts,
required unknowns and forbidden unsupported root-cause/command claims.

Acceptance: deterministic comparison to the gold manifest where possible; semantic
remainder is graded blind to variant identity.

### B03 — evidence compression

Bounded mixed evidence requiring facts/evidence/inferences/hypotheses/unknowns/
verify-next separation.

Dogfood path: parsed `llmtool context-pack --json`.

Before execution, commit a gold manifest containing the required facts/evidence,
required unknowns, forbidden promotions to fact and minimum required source references.

Acceptance: A4 parser success plus deterministic gold comparison where possible; any
remaining semantic equivalence is graded by a blinded independent reviewer.

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
raw_fixture_bytes_available:
raw_fixture_bytes_read_by_codex:
raw_fixture_ranges_read:
input_bytes_to_company:
company_output_bytes:
codex_tool_call_count:
company_call_count_all_attempts:
company_retry_count:
run_order:
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

dogfood_mixed_token_volume
  = dogfood.codex_total_tokens + dogfood.company_total_tokens

mixed_token_volume_delta
  = control.codex_total_tokens - dogfood_mixed_token_volume

mixed_token_volume_delta_pct
  = mixed_token_volume_delta / control.codex_total_tokens * 100
```

If any required value is unknown, the dependent result is also unknown.

The mixed-token-volume calculation adds token counts from different model/tokenizer
families. It is a descriptive volume indicator only, **not** a cost, compute or energy
metric. Always show Codex and company totals separately. Any monetary comparison requires
trusted per-model pricing/version data and must be calculated separately.

## Pilot execution size and order

Start with one control/dogfood pair for B01–B03: six fresh Codex sessions total.
Pre-register and record the run order before execution. The pilot is descriptive only:
n=1 per fixture cannot establish a stable routing threshold or a statistically reliable
saving rate.

Record **every** company call for the dogfood variant, including failed attempts and
manual retries, plus Codex tool-call overhead. Do not report only the successful company
call.

If a fixture has a small apparent margin, quality mismatch, anomalous usage, order/cache
concern or inconsistent accounting source, do not generalize from it; add counterbalanced
paired repetitions later.

This pilot does not label any saving as "material". Any future routing threshold must be
pre-registered before the repeated benchmark used to evaluate that threshold; do not
choose the threshold after seeing the result.

## Summary table

| Fixture | Control Codex | Dogfood Codex | Company | Codex saved | Codex saving % | Mixed token-volume delta* | Quality parity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| B01 test triage | pending | pending | pending | pending | pending | pending | pending |
| B02 log triage | pending | pending | pending | pending | pending | pending | pending |
| B03 context pack | pending | pending | pending | pending | pending | pending | pending |
| **Overall comparable pairs** | pending | pending | pending | pending | pending | pending | pending |

*Mixed token-volume delta combines different tokenizers and is descriptive only, not a
cost/compute metric.

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
