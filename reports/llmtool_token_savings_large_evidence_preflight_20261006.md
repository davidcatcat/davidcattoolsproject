# llmtool Round 2 size-feasibility preflight — 2026-10-06

**Size/capacity preflight PASS. Budget-adjusted selected tiers: 48 / 64 / 128 KiB.**
Stage-A forecast: **391,083** comparable Codex tokens; fixed 400,000 gate **PASS**.
This report is non-semantic feasibility evidence only. No formal fixture, gold,
Stage A/B, control/dogfood benchmark, or savings claim was produced.

## Contract, checkout and provenance

- Actual Mac canonical root:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-large-evidence-20261006`; existing Draft PR #37.
- `git fetch origin` verified remote HEAD
  `53869998f4a06fa3cdda76c579de7bb6a3e207fd`; local branch fast-forwarded safely from
  `ec9eb986e8afabcd84468de805dec3332335c546`. The delta only added the preflight handoff.
- All 14 actual probes ran at exact repository SHA
  **`53869998f4a06fa3cdda76c579de7bb6a3e207fd`**, with the helper source identified by
  **`c25325886dd64b7e4872156b08b63864691923a1dc2cf03ab06407293d4650dc`**. Helper/report additions were not production changes.
- Fetched `origin/main`: `65df4094df1737facc30f6b02d19bdd8f31f068c`.
- Direct execution contract:
  [size-preflight handoff](llmtool_token_savings_large_evidence_preflight_handoff_20261006.md),
  SHA-256 `8cc05974c7d499436783f7c1110e4e539446972013844e039afb56ecfbd28c2d`. The handoff's older expected head was inspected;
  current user-authorized head includes that handoff.
- Read the [Round 2 plan](llmtool_token_savings_large_evidence_plan_20261006.md),
  [review](llmtool_token_savings_large_evidence_plan_review_20261006.md),
  [PASS re-review](llmtool_token_savings_large_evidence_plan_rereview_20261006.md),
  accepted accounting mapping/review, AGENTS and development workflow.
- Installed llmtool: `tools/llmtool/.venv/bin/llmtool`, version `0.1.0`; module
  resolves inside this canonical checkout. Company invocations used this checkout's
  official `llmtool.entrypoint.main` through its venv Python and a request observer.

## Deterministic non-semantic filler

The [preflight-only helper](token_benchmark/20261006_round2/preflight_helper.py)
repeats a fixed 117-byte line composed of 8-byte slices of the lowercase alphabet
(start offsets 0,2,…,24), separated by spaces/newline, then truncates to exact size.
No randomness, fixture/gold input, record/failure/signature/answer, benchmark IDs,
secret or credential content is present. Exact length, determinism, permitted
alphabet and excluded substrings were checked before any request.

Every size uses identical filler bytes in both lanes. The common task envelope is
**190 additional UTF-8 bytes**, outside the reported exact filler size:

```text
Return exactly SIZE_PREFLIGHT_OK. Do not analyze or summarize the filler.
Do not use tools, read files, or perform any other task.
```

Actual prefix/suffix, prompt bytes and digests are preserved in results.json.
`SIZE_PREFLIGHT_OK` appears only in caller instructions, never inside filler.
No output was used to tune future semantic design, prompts, coverage, selectors or gold.

| Size KiB | Requested = actual filler bytes | Filler SHA-256 | Codex | Company | Joint |
|---|---:|---|---|---|---|
| 32 | 32,768 | `5d0a9fba519f334f1b8109fd5a1bc5ab358220b262cd435a83c8fc6e92fd22b3` | PASS | PASS | PASS |
| 48 | 49,152 | `85e9d203abb02b516e61b2c28bb28f74fcb65203062a55f02abb01f4434336cd` | PASS | PASS | PASS |
| 64 | 65,536 | `8a8b221b7cf1079e17dcd1740d09959e606f860f790d2e6ee348968180720671` | PASS | PASS | PASS |
| 96 | 98,304 | `27937570fe36a04cc278d8be5c111b000d69eaf7a19bf07f2381999bc946a890` | PASS | PASS | PASS |
| 128 | 131,072 | `50deabd8f0c525d99f4e621fc304295e458447463788a6d0530829bc0c07c600` | PASS | PASS | PASS |
| 192 | 196,608 | `a32aa35f4fef34b803bee7f4da201e48ab08facadab537271f679ec7b63cce8e` | PASS | PASS | PASS |
| 256 | 262,144 | `530f8587754ef52a400c87ea9f350af602e2c20d1af09856da093502ef3957b3` | PASS | PASS | PASS |

**Largest joint PASS: 256 KiB.** Both lanes completed their required ascending
32 → 48 → 64 → 96 → 128 → 192 → 256 sequence. The company lane ran first to prove
its temporary configuration, followed by the Codex lane. There were no capacity
rejections, transient failures, retries, or skipped candidate sizes.

## Codex lane

Every probe checked actual **`codex-cli 0.159.2`** before invocation and used:

```text
codex exec --json --ephemeral --ignore-user-config --ignore-rules
--model gpt-6.1-sol
--config model_reasoning_effort="high"
--config service_tier="default"
--config approval_policy="never"
--sandbox read-only --cd <canonical-root> -
```

Fresh independent threads; no resume. Each exit was 0, final output was exactly
`SIZE_PREFLIGHT_OK`, tool calls were 0, and counts were **thread.started=1,
turn.started=1, turn.completed=1**. No version/schema/turn-shape drift occurred.
Requested model/service settings are recorded; independent backend identity is not
attested by JSONL.

Exact usage schema on all seven probes:

```text
input_tokens
cached_input_tokens
cache_write_input_tokens
output_tokens
reasoning_output_tokens
```

Source: **`codex exec --json / turn.completed.usage`**.
Comparable total uses the accepted mapping **input_tokens + output_tokens**;
cache/cache-write/reasoning are recorded and never added again. No usage is guessed
from bytes, quota, credits or pricing. Every cache-write value was source-reported 0.

| Size KiB | Input | Cached input | Cache write | Output | Reasoning output | Comparable total | Wrapper seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| 32 | 28,397 | 12,288 | 0 | 8 | 0 | 28,405 | 8.927 |
| 48 | 32,523 | 12,288 | 0 | 8 | 0 | 32,531 | 7.466 |
| 64 | 37,984 | 12,288 | 0 | 8 | 0 | 37,992 | 7.241 |
| 96 | 50,244 | 12,288 | 0 | 8 | 0 | 50,252 | 11.615 |
| 128 | 59,830 | 12,288 | 0 | 8 | 0 | 59,838 | 8.057 |
| 192 | 81,676 | 59,648 | 0 | 8 | 0 | 81,684 | 12.405 |
| 256 | 103,521 | 12,288 | 0 | 8 | 0 | 103,529 | 7.941 |

All seven preflight comparable totals sum to **394,231**. These are preflight
consumption, **excluded from future Stage-A measured totals/KPI**.

## Company lane — temporary configuration and actual request proof

A new ordinary runtime directory contains an isolated `company-home` and
`.config/llmtool/config.yaml` (0600). Only company child processes receive this
HOME; the real user HOME/config was not changed. Ambient `LLMTOOL_*` overrides
were removed from these child environments; none of their values were printed.
The profile was explicitly resolved and checked before requests, without resolving
credentials. Actual Mac route to the gateway used **utun4**.

Exact temporary configuration:

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

Config SHA-256 on **all seven probes**:
`82a5bd737ffe5af2bf7bc280bb4f8dc12db8f281282fa17b36ee7751e83995e4`.
Observer source SHA-256:
`2e4611f44314085e42ac8e1837d68504d7bef7bb8d19fac632541dcbb3adf144`.

**Company enable_thinking=false verified: YES.** Proof is not merely config text:

1. The observer checks the real profile/generation passed into the existing
   `OpenAICompatibleClient`: endpoint/model/timeout, temperature=0, max_tokens=4096,
   thinking=false, and null key/key-env.
2. Immediately before the original llmtool transport's `open(req, …)` call, it
   checks the **actual Request** contains
   **`chat_template_kwargs.enable_thinking=false`**, model `gw`, temperature 0,
   max_tokens 4096, POST to the declared endpoint, and no Authorization header.
3. The real user-message digest matches that candidate's generated prompt digest.
   The observer records only allowlisted settings/digests, does not edit the Request,
   does not inspect response bodies or reasoning, and forwards the unchanged Request
   to the original network opener. Every invocation has exactly one audited request.

This is a live llmtool path with a read-only request observer, **not** a mock, a
replacement client, or a direct curl/API task. It proves what was sent; gateway
internal implementation of the extension was not independently inspected.
The normal no-proxy/no-redirect transport and generation bounds remain in force.

All seven exits were 0 and stdout was exactly `SIZE_PREFLIGHT_OK`. Provider-reported
model was **`qwen3.6-35b-a3b`** in every reply, separately from requested route `gw`.
Source: **llmtool `--verbose` / normalized Reply.metadata**.

| Size KiB | Prompt tokens | Completion tokens | Total tokens | Provider elapsed seconds | Wrapper elapsed seconds |
|---|---:|---:|---:|---:|---:|
| 32 | 11,652 | 5 | 11,657 | 0.748 | 1.184 |
| 48 | 17,394 | 5 | 17,399 | 0.796 | 1.186 |
| 64 | 23,135 | 5 | 23,140 | 0.802 | 1.129 |
| 96 | 34,619 | 5 | 34,624 | 1.217 | 1.522 |
| 128 | 46,101 | 5 | 46,106 | 1.370 | 1.666 |
| 192 | 69,067 | 5 | 69,072 | 2.499 | 2.828 |
| 256 | 92,033 | 5 | 92,038 | 3.103 | 3.413 |

Counts: **7 requests; 7 successes; 0 failures; 0 retries; 0 fallback**.
Company source-reported token total: **294,036**, excluded from future semantic
benchmark totals. No missing usage occurred; failed requests would retain unknown
values rather than zero. No skill/test-triage/log-triage/context-pack was used.

## Joint tiers and fixed Stage-A budget gate

Largest capacity-eligible tiers before the budget gate were **64 / 128 / 256 KiB**.
Their Codex totals sum to 201,359; **3 × S = 604,077**, exceeding the unchanged
400,000 ceiling. Thus capacity eligibility alone does not authorize those sizes.

Only declared tiers were considered:

```text
L01: 64 → 48 → 32 KiB
L02: 128 → 96 → 64 KiB
L03: 256 → 192 → 128 → 96 KiB
```

The budget fallback chooses the largest total declared joint-PASS payload size
that meets 3×S ≤ 400,000; ties favor larger L03, then L02, then L01. All 36 legal
combinations and forecasts are recorded for audit. This is only a deterministic
choice among authorized fallback sizes, not a semantic tuning rule or new tier.
The selected result can be reached by these descending legal steps:

| L01 / L02 / L03 KiB | 3×S forecast | 400k gate |
|---|---:|---|
| 64 / 128 / 256 | 604,077 | FAIL |
| 64 / 128 / 192 | 538,542 | FAIL |
| 64 / 128 / 128 | 473,004 | FAIL |
| 64 / 96 / 128 | 444,246 | FAIL |
| 64 / 64 / 128 | 407,466 | FAIL |
| 48 / 64 / 128 | 391,083 | PASS |

Final selected tiers/totals:

- **L01: 48 KiB → 32,531 Codex preflight tokens**.
- **L02: 64 KiB → 37,992 Codex preflight tokens**.
- **L03: 128 KiB → 59,838 Codex preflight tokens**.
- **S = 130,361; Stage-A forecast = 3 × 130,361 = 391,083**.
- **400k budget gate: PASS**; forecast headroom **8,917 tokens**.

This is the specified conservative execution-budget forecast, not a token-savings
KPI or guarantee. Different semantic content/token density, schema/task envelopes
and answer lengths may change actual usage. Future Stage A must still enforce its
pre-registered spending/acceptance gates; this step does not execute it.

## Deliverables, validation and stop point

[preflight_results.json](token_benchmark/20261006_round2/preflight_results.json)
contains every attempt, exact bytes/digests, versions, argv, usage fields/counts,
marker checks, failures/retries (empty because none occurred), request proof,
config/helper digests, selection and forecast calculations. Full Codex JSONL,
raw stderr and filler/prompt files remain in the ordinary runtime directory:

`/var/folders/y_/xqy1pg553h19l7z9zjmshvb80000gn/T/llmtool-round2-size-preflight-uiitrngk`

It contains no repository clone/worktree/copy. No full traces are committed.

Evidence validation: filler regeneration/digests, unchanged helper/config,
14 actual PASS attempts, 7 distinct fresh Codex threads, exact schema/turn counts,
actual request audit, recorded usage arithmetic, every permitted tier combination,
report/JSON consistency and `git diff --check`. Real user config existence/inode/
mtime/size/mode metadata was unchanged; its contents were not inspected.
Application builds/full suites are not required for this reports-only probe.

Production llmtool, tests, CI, true user config and historical reports are unchanged;
all unrelated benchmark/reasoning/case artifacts are preserved. No formal fixture
or gold, selector/coverage benchmark implementation, Stage A/B, router, budget
enforcement, A11b.2/A11c, or merge was performed. Final publication SHA/PR/checks
are recorded in the PR/final reply; tested SHA is fixed above.

**Fixture preregistration may proceed: YES, after preflight review/next-step
authorization.** This is gate readiness, not an instruction to generate anything
in the current turn. Stop after publication.

```text
Codex CLI version: codex-cli 0.159.2
Codex usage schema: input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens
Company enable_thinking=false verified: YES

Largest joint PASS: 256 KiB
32 KiB: PASS
48 KiB: PASS
64 KiB: PASS
96 KiB: PASS
128 KiB: PASS
192 KiB: PASS
256 KiB: PASS

Selected L01 tier: 48 KiB
Selected L02 tier: 64 KiB
Selected L03 tier: 128 KiB

Selected-tier Codex preflight totals:
L01: 32531
L02: 37992
L03: 59838

Stage-A forecast: 391083
400k budget gate: PASS

Fixture preregistration may proceed: YES — stop for review/next-step authorization
```
