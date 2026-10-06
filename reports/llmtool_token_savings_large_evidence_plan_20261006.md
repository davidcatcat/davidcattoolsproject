# llmtool Token Savings Round 2 — Large-Evidence Selective-Read Plan（2026-10-06）

## Goal

Round 1 established a valid negative result for small evidence: with 1.2–1.4 KiB
fixtures and mandatory raw re-read, company dogfood increased Codex usage by 104.46%
at equivalent quality.

Round 2 tests the actual intended use case:

```text
large raw evidence
  -> cheap company triage (thinking disabled)
  -> deterministic locator/coverage validation
  -> one bounded selective raw read
  -> strong Codex judgement
```

This plan asks whether selective reading of large evidence can reduce Codex primary
usage enough to overcome the fixed extra model-request/tool overhead observed in Round 1.

No routing policy, budget enforcement, A11b.2 or A11c work is authorized.

## Fixed Codex accounting

Same accepted mapping as Round 1:

```text
codex_comparable_total = input_tokens + output_tokens
```

Measured Codex runs must remain:

- Codex CLI 0.159.2;
- `codex exec --json`;
- fresh thread;
- exactly one turn;
- `--ephemeral`;
- no resume;
- requested model `gpt-6.1-sol`;
- reasoning effort `high`;
- service tier `default`;
- approval `never`;
- sandbox `read-only`;
- same flags across each control/dogfood pair.

Every run records version, usage field set, turn/thread counts, cache fields, tool calls,
wall time and JSONL digest. Any version/schema/turn-shape drift stops the experiment.

## Company-provider configuration

Round 1's company observations were confounded by provider-default thinking.

Round 2 MUST pin:

```yaml
provider:
  type: openai-compatible
  base_url: http://10.29.7.58:4000
  model: gw
  provider_options:
    enable_thinking: false
generation:
  temperature: 0
  max_tokens: 4096
```

Use an isolated temporary HOME/config for the benchmark. Do not mutate the user's
persisted llmtool config. The temporary profile contains no API key or secret.

Record the actual successful provider-reported model, usage and latency.

If the gateway/profile cannot prove that the request used the intended
`enable_thinking=false` benchmark configuration, STOP that fixture before its measured
DOGFOOD Codex session.

## Size-feasibility gate before fixture preregistration

The 64/128/256 KiB targets are hypotheses, not guaranteed runtime capacities.

Before generating/finalizing the measured fixtures, run a **non-semantic capability
preflight** for both lanes:

1. **Codex control-lane feasibility:** verify the pinned Codex CLI/model/session shape
   can accept a synthetic payload at each target byte size plus the fixed task/schema
   envelope without context/transport rejection.
2. **Company-lane feasibility:** verify the pinned company profile
   (`enable_thinking=false`, temperature 0, max_tokens 4096) can accept a synthetic
   non-benchmark payload at each target byte size without context/transport/length
   rejection.

These calls are capability preflights only:
- use generated filler distinct from all benchmark fixtures;
- do not contain benchmark identifiers/gold/answers;
- do not count in Stage A token totals;
- record version/config, byte size, success/failure and any reported context error;
- do not use their output to tune benchmark answers or selector behavior.

A target size is eligible only if **both** lanes pass.

If 256 KiB fails but a smaller target passes, do not silently choose an arbitrary size.
Use the largest pre-declared fallback tier below that passes both lanes:

```text
L01 tiers: 64 KiB -> 48 KiB -> 32 KiB
L02 tiers: 128 KiB -> 96 KiB -> 64 KiB
L03 tiers: 256 KiB -> 192 KiB -> 128 KiB -> 96 KiB
```

Record the chosen tier before fixture/gold generation and commit it in preregistration.
If no tier for a fixture passes both lanes, mark that fixture
`TARGET_SIZE_NOT_FEASIBLE` and do not run its measured pair.

This gate prevents a mid-experiment size change while avoiding unsupported assumptions
about deployment/model context limits.

## Large synthetic fixtures

All fixtures/gold/schema/generator output are pre-registered and committed before the
first measured session. Raw data is synthetic and non-sensitive.

### L01 — large test output

Target size: **64 KiB ± 2 KiB**.

Content:
- realistic repeated pytest/unit-test progress/noise;
- multiple ordinary passing lines;
- four target failures distributed across widely separated regions;
- at least two failures in one class and two distinct failure classes;
- stable test/file identifiers;
- required unknowns;
- no real credentials.

Dogfood company skill: `test-triage`.

### L02 — large operational log

Target size: **128 KiB ± 4 KiB**.

Content:
- timestamped ordinary service traffic/noise;
- repeated timeout signature distributed across distant regions;
- later success/recovery evidence;
- distractor warnings/errors that must not be conflated;
- stable request/event identifiers;
- causal unknowns.

Dogfood company skill: `log-triage`.

### L03 — large mixed evidence/context

Target size: **256 KiB ± 8 KiB**.

Content:
- many ordinary evidence records;
- sparse required facts;
- inference candidates;
- unsupported hypotheses;
- unknowns;
- verify-next items;
- stable record/source identifiers.

Dogfood path: `context-pack`.

Exact byte size and SHA-256 are fixed in preregistration.

## Gold and deterministic locator design

Gold is committed before execution and contains:
- required semantic items;
- allowed identifiers;
- forbidden unsupported claims;
- required unknowns;
- expected target record/test/event identifiers;
- expected target raw line ranges.

**Gold is grading-only. It must never influence company prompting, identifier selection,
range selection, range expansion, merge decisions or cap allocation.** The selector may
use only the frozen raw fixture, frozen selector rules and the company output.

Company output does not need to invent byte/line locators.

Instead:

1. validate returned identifiers/signatures/sources against the raw fixture;
2. map **only those validated company-returned identifiers** deterministically to raw
   line ranges;
3. apply the pre-registered context-padding and merge rules;
4. produce one bounded evidence-reader request containing all selected ranges;
5. freeze the selected-range artifact and digest;
6. only **after selection is frozen**, compare it with gold to measure target coverage.

If gold later shows a missed target, the selector must not be repaired or widened for
that run. The miss remains evidence and can invalidate the pair.

This avoids answer leakage, avoids trusting company-generated locations and avoids one
Codex model round-trip per range.

## Selective-read contract

CONTROL:
- receives the full raw fixture in its initial prompt;
- no company output;
- no raw-reading tool.

DOGFOOD initial context:
- company summary/packet;
- deterministic validation/coverage result;
- selected raw ranges/reference;
- same task/output schema;
- DOES NOT contain the complete raw fixture.

DOGFOOD may perform **one evidence-reader tool call** that returns all preselected raw
ranges in one invocation.

Pre-registered selector rules must be frozen before any measured run:

- exact-match validated identifier anchors only;
- context padding: **2 lines before + 2 lines after** each matched anchor;
- merge ranges when their padded line intervals overlap or are separated by **<= 2
  lines**;
- order ranges by source line number;
- no semantic/manual range expansion;
- max selected ranges before merge: **32**;
- after merge, selected bytes must satisfy the raw-read cap below.

Pre-registered raw-read cap:

```text
max_selected_raw_bytes = min(32 KiB, 20% of fixture bytes)
```

Expected caps are approximately:
- L01 64 KiB -> <= 12.8 KiB;
- L02 128 KiB -> <= 25.6 KiB;
- L03 256 KiB -> <= 32 KiB.

If deterministic gold coverage cannot be achieved under the cap:
- do not silently widen after seeing Codex output;
- record the fixture as SELECTIVE_READ_CAP_INSUFFICIENT;
- the pair is invalid for a selective-read savings claim.

If Codex reads the whole fixture or exceeds the cap, retain the usage but mark the pair
invalid for the selective-read hypothesis.

Completeness claims must use deterministic coverage evidence, not require Codex to scan
the entire raw fixture.

## Run order

Stage A first-pass order is fixed before execution:

```text
L01: CONTROL -> DOGFOOD
L02: DOGFOOD -> CONTROL
L03: CONTROL -> DOGFOOD
```

No post-hoc order changes.

## Metrics

### Primary KPI

```text
codex_tokens_saved
  = control.total - dogfood.total

codex_savings_pct
  = codex_tokens_saved / control.total * 100
```

This remains the primary metric and includes all input/output usage from all Responses
requests in the fresh thread.

### Pre-registered secondary metric

To describe cache/order effects without replacing the primary KPI:

```text
codex_non_cached_comparable
  = (input_tokens - cached_input_tokens) + output_tokens
```

and paired delta/percentage are reported separately.

This secondary metric must never be substituted for the primary result.

### Evidence-efficiency metrics

Record:
- full fixture bytes;
- dogfood raw bytes read;
- raw-read percentage;
- selected range count;
- target coverage;
- company output bytes;
- initial prompt bytes;
- Codex tool-call count;
- company attempts/retries;
- company prompt/completion/total tokens;
- latency.

### Quality

As before:
- deterministic gold checks first;
- executing Codex cannot self-grade;
- remaining semantic parity uses anonymous blind review;
- original deterministic misses remain preserved.

## Company-call policy

Company thinking is disabled and temperature is zero.

One initial company attempt per fixture is expected.

If a request fails because of transport/provider/runtime:
- record usage as unknown if unavailable;
- one manual retry with the **same** generation bounds/config is allowed only for
  transient transport failure.

If it fails due to `finish_reason=length` under max_tokens=4096:
- do not raise the max token limit mid-fixture;
- mark company triage failed for that fixture.

This prevents Round 1's post-start output-limit changes from becoming another confounder.

## Stage B confirmation trigger

Do not automatically repeat every fixture.

A fixture qualifies for one reverse-order confirmation pair only if Stage A has:
- quality parity PASS/EQUIVALENT;
- accounting valid;
- selective-read contract valid;
- primary `codex_savings_pct >= 10%`.

For a qualifying fixture, run exactly one additional pair with reversed order and the
same immutable fixture/gold/config.

This trigger is pre-registered before any Stage A result is observed. It is an
experiment-efficiency rule, not a production routing threshold.

Because Stage B selectively confirms only positive Stage-A candidates, its estimates
are subject to selection bias / winner's curse. Stage-B results must therefore:
- be reported per fixture, paired with the original Stage-A observation;
- **not** be pooled with Stage A as an unbiased overall saving estimate;
- not be used to claim a stable saving percentage or threshold;
- be described only as reverse-order confirmation evidence for a selected candidate.

If no fixture qualifies, Stage B is skipped.

## Interpretation

Round 2 remains descriptive.

Possible results:
- positive primary savings with quality parity -> promising large-evidence candidate,
  requiring repeated confirmation before routing policy;
- negative primary savings -> evidence size/selective-read method still does not offset
  Codex fixed/tool overhead;
- mixed result by fixture size/type -> informs future routing boundary hypotheses.

Do not generalize a single pair to a stable threshold.

## Required report

Create:

`reports/llmtool_token_savings_large_evidence_20261006.md`

Report per fixture:
- fixture bytes/digest;
- control/dogfood primary total;
- primary savings tokens/%;
- non-cached secondary total/delta;
- company usage/latency;
- raw bytes read/%;
- quality verdict;
- validity flags;
- Stage B trigger status.

Overall aggregates include only comparable, quality-approved pairs and remain clearly
separated from company token totals.

## Stop conditions

STOP before measured execution if:
- Codex CLI version/schema changed;
- exact company `enable_thinking=false` configuration cannot be established;
- target-size feasibility gate has not passed for both Codex and company lanes;
- fixtures/gold are not committed first;
- selector rules/range artifact/digests are not deterministic and frozen before
  gold coverage grading;
- gold data is used to influence selected ranges;
- raw-read cap/coverage tooling is not deterministic;
- branch/repository identity is wrong.

Do not merge the Round 2 PR automatically.
