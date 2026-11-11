# llmtool Round 2 size-feasibility preflight — execution handoff（2026-10-06）

## Purpose

Execute only the non-semantic size-feasibility preflight required by the approved
Round-2 methodology.

Do **not** generate benchmark fixtures/gold yet.
Do **not** run Stage A measured control/dogfood pairs.
Do **not** modify production llmtool behavior.

## Repository

Canonical repository only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Expected branch:

`bench/llmtool-token-savings-large-evidence-20261006`

Expected remote head before local execution:

`ec9eb986e8afabcd84468de805dec3332335c546`

If the branch head moved, fetch and inspect the delta before continuing.

Never use `/private/tmp/*` worktrees/clones.

## Read before execution

- `reports/llmtool_token_savings_large_evidence_plan_20261006.md`
- `reports/llmtool_token_savings_large_evidence_plan_review_20261006.md`
- `reports/llmtool_token_savings_large_evidence_plan_rereview_20261006.md`
- Round-1 accounting mapping/review reports.

## Preflight constraints

This is a capability/size probe, not a semantic benchmark.

Use generated synthetic filler that:
- is different from every future benchmark fixture;
- contains no benchmark IDs, target failures, gold labels, answers or secrets;
- is deterministic and reproducible;
- has exact byte-size/digest recorded.

Candidate payload sizes:

- 32 KiB
- 48 KiB
- 64 KiB
- 96 KiB
- 128 KiB
- 192 KiB
- 256 KiB

These cover all declared fallback tiers.

Do not use preflight output to tune future semantic fixtures, prompts, selector rules
or gold.

## Codex lane

Required runtime:

- `codex-cli 0.159.2`
- `codex exec --json`
- fresh thread
- single turn
- `--ephemeral`
- no resume
- model `gpt-6.1-sol`
- reasoning effort `high`
- service tier `default`
- approval `never`
- sandbox `read-only`
- `--ignore-user-config --ignore-rules`

Prompt must be intentionally trivial and non-semantic, for example:

`Return exactly SIZE_PREFLIGHT_OK. Do not analyze the filler content.`

The filler itself is included only to test payload/context acceptance.

For each candidate size, record:
- requested bytes;
- actual bytes;
- filler SHA-256;
- exit code;
- thread.started count;
- turn.started count;
- turn.completed count;
- exact usage-field set;
- input_tokens;
- cached_input_tokens;
- cache_write_input_tokens;
- output_tokens;
- reasoning_output_tokens;
- comparable total = input + output;
- wall time;
- success/failure;
- exact safe failure code/message category if rejected.

If Codex version/schema/turn shape changes, stop the entire preflight.

Do not infer success from partial output.

## Company lane

Use isolated temporary HOME/config.

Profile must explicitly pin:

```yaml
active_provider: company-preflight
providers:
  company-preflight:
    type: openai-compatible
    base_url: http://192.0.2.10:4000
    model: gw
    timeout_seconds: 120
    api_key: null
    api_key_env: null
    provider_options:
      enable_thinking: false
generation:
  temperature: 0
  max_tokens: 4096
```

No persisted user config mutation.
No API key.
No direct curl.
Use llmtool only.

Task must be intentionally trivial/non-semantic and require a very small response.
Do not use `test-triage`, `log-triage` or future Round-2 benchmark skills/content for
this size probe.

For each candidate size, record:
- requested/actual bytes and SHA-256;
- exact temporary profile/config digest;
- exit code;
- provider-reported model;
- prompt tokens;
- completion tokens;
- total tokens;
- provider elapsed;
- wrapper elapsed;
- success/failure;
- safe failure category.

If usage is unavailable on a failed request, record `unknown`, never zero.

The preflight must prove that the request used the intended
`enable_thinking=false` configuration. If this cannot be established, stop.

## Candidate execution order

Use the same ascending order for both lanes:

```text
32 -> 48 -> 64 -> 96 -> 128 -> 192 -> 256 KiB
```

A smaller candidate failing means do not assume a larger candidate can pass. Stop that
lane after the first size-related capacity/context rejection.

Transient transport failure may be retried once at exactly the same size/config.
Record both attempts.

## Tier selection

After both lanes finish, select the largest tier for each fixture from the already
approved fallback sets that passed **both** lanes:

```text
L01: 64 -> 48 -> 32 KiB
L02: 128 -> 96 -> 64 KiB
L03: 256 -> 192 -> 128 -> 96 KiB
```

Do not invent a new tier.

If no declared tier passes both lanes:

`TARGET_SIZE_NOT_FEASIBLE`

for that fixture.

## Stage-A budget forecast

Using source-reported Codex comparable totals from preflight:

For the selected L01/L02/L03 sizes, record the observed Codex comparable total.

Then compute the pre-registered conservative forecast:

```text
stage_a_forecast
  = sum(selected control-size preflight comparable totals)
    + 2 * sum(selected control-size preflight comparable totals)
```

Equivalent:

`stage_a_forecast = 3 * sum(selected-size Codex preflight totals)`

This is only an execution-budget forecast, not the future benchmark KPI.

Gate:

`stage_a_forecast <= 400,000`

If forecast exceeds 400,000, step down only through the already-declared fallback tiers
and recompute before fixture generation.

Do not change the 400k ceiling after observing the forecast.

## Deliverables

Create:

`reports/llmtool_token_savings_large_evidence_preflight_20261006.md`

and:

`reports/token_benchmark/20261006_round2/preflight_results.json`

The JSON must include every attempted candidate for both lanes, including failures and
retries.

The report must state:

- Codex version/schema validity;
- company `enable_thinking=false` proof;
- candidate-by-candidate result table;
- selected L01/L02/L03 tiers;
- Codex preflight comparable totals for selected tiers;
- Stage-A forecast and 400k gate result;
- whether fixture preregistration is allowed next.

## Scope / stop point

Allowed:
- deterministic filler generator/helper under the Round-2 report directory;
- preflight report/results;
- commit/push to PR #37 branch.

Not allowed:
- benchmark fixture/gold generation;
- measured Stage A sessions;
- production code/config changes;
- router/budget enforcement;
- A11b.2/A11c;
- merge PR #37.

After publishing the preflight evidence, STOP for review.
