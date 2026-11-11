---
name: company-llm-delegation
description: Use when an llmtool Developer Task is routed to gw-agent (home LLM) through the API Agent Gateway.
---

# gw-agent (home LLM)

Use this only as a development-time dogfood workflow. It does not implement A11c
production routing and does not grant execution, mutation, approval, retry, fallback,
security sign-off, QA verdict, or merge authority.

Research findings and benchmark history are preserved in [the 2026-11-03 history](../../docs/history/company-llm-delegation-research-20261103.md).

Home LLM `gw` may act as an agent-mode Developer through `gw-agent` on real repository
code; Developer choice, mode and scope are PM decisions under the standing Owner
authorization (#120, 2026-11-10), which supersedes the 2026-11-03 synthetic-only limit.
Every result receives formal cross-family review. Pause after two consecutive failures
at the lowest tier, a write outside allowed paths, a credential or audit finding, or HTTP 429.

**Gateway only (Owner 2026-11-11, #120):** Home LLM (`gw-agent`) model work runs only through the API Agent Gateway. The direct and proposal routes (`company-gw`) are retired: llmtool refuses them (`COMPANY_GW_DIRECT_RETIRED`; worker `company-gw` is PAUSED), and a Gateway failure reports UNAVAILABLE without any fallback.

## Decision order

1. **Deterministic first.** If a program can answer the question directly, do not call
   any LLM. Run the test/lint/typecheck/build/schema/exit check, or use an existing
   deterministic collector such as `llmtool git --json`.
2. **Bounded semantic triage.** If supplied evidence needs clustering, compression or
   simple classification, gw-agent (home LLM) may be used through llmtool.
3. **Strong judgement.** Root cause, source changes, architecture, security/auth/
   permission, concurrency/race, data corruption, test correctness, acceptance and
   merge decisions stay with Codex/Claude/human review against source and raw evidence.

## Before delegation

- **Web search for gw (Owner 2026-10-28, [AGENTS.md](../../AGENTS.md)):** gw has no tools of its own. The PM or a Developer may search the web for the Task and add the relevant, non-sensitive results to gw's context (`--context-stdin`), labelled as untrusted reference material with their source URLs. Keep the excerpt short. The data rules below apply to it.

- Work from the canonical Mac repository and identify the exact revision under test.
- The gateway provider must already be configured and available. Use an explicit
  configured profile name; do not rewrite active provider settings just for delegation.
- Record a **data permission basis** for the exact bounded evidence being sent. The fact
  that a provider is internal does not automatically authorize every repository file,
  log, credential, customer datum or production payload.
- Deterministically redact or discard secrets and unnecessary sensitive/raw content
  **before** the provider call. Never rely on the model to redact its own input or
  output. Never send credentials, API keys, tokens, private keys, kubeconfigs, auth
  headers, keychain material, full environment dumps or unrelated private data. Treat
  model output as potentially containing any value that was supplied.
- For ordinary development triage, bound the input to the minimum evidence needed:
  command/check identity, exit status, failing test names, selected stack trace/error
  lines, relevant file names and small necessary snippets. Do not send the whole
  repository or an entire CI log by default. **Do not infer token savings from this
  ordinary bounded-input mode**; the positive L01 result used a different large-input
  benchmark pipeline described in the [L01 token-saving pipeline preconditions](../../docs/history/company-llm-delegation-research-20261103.md#l01-token-saving-pipeline-preconditions).
- Preserve the original deterministic/raw evidence separately; the model output is a
  derived summary, not ground truth.

## C1B Live Phase 0 safeguards

- **Owner-only Live grants.** AI agents must not create, change or fabricate grants for
  real Live execution; only the Owner supplies them.
- **A grant is not authorization.** A real Phase 0 invocation requires the Owner's
  authorization and a non-author check of its evidence.
- **Off-repo only.** Live grant files must be outside every Git worktree.
- **Gateway-only Phase 0 first.** A new or redesigned route must pass a pre-registered
  gateway-only reliability Phase 0 before any paired Codex benchmark.

See the [C1B test-triage Worker history](../../docs/history/company-llm-delegation-research-20261103.md#c1b-test-triage-worker-sourcefake-only) for the full grant safeguards and the [token-savings benchmark history](../../docs/history/company-llm-delegation-research-20261103.md#token-savings-benchmark) for the benchmark gate.

## Availability classification

This classification applies to the triage use above and to the home LLM `gw-agent` Developer
route in [AGENTS.md](../../AGENTS.md). Check the four layers separately. Record each
layer as `PASS`, `FAIL` or `UNKNOWN`, with its time and evidence:

1. **TCP:** can a connection to the configured endpoint open? Example: a bounded
   `nc -z` connect, with no request body.
2. **API authentication:** does an authorized llmtool call get past auth? llmtool
   reports a rejection as `AUTH_PROVIDER_REJECTED`; an unreachable endpoint is
   `NETWORK_PROVIDER_UNREACHABLE`.
3. **Inference:** does the call return a valid model result? A malformed result is
   `MODEL_RESPONSE_INVALID`; a timeout is `PROVIDER_TIMEOUT`.
4. **Task execution:** did the route produce output for that task class that passed
   its deterministic checks and independent review?

Do not infer a layer from an earlier layer, and do not infer it from network connection status
(`scutil --nc list`). Use that list only as a hint for the Owner reminder in
[WORKFLOW](../../tools/llmtool/WORKFLOW.md). A TCP `PASS` authorizes nothing. Layers
2–4 require an llmtool call that the existing data-permission basis already covers,
and C1B live calls also need the [Phase 0 grant safeguards](../../docs/history/company-llm-delegation-research-20261103.md#c1b-test-triage-worker-sourcefake-only).
Without that authorization the
layer stays `UNKNOWN`. Never bypass a gate because TCP is reachable.

## Runtime skill selection

Use the smallest applicable llmtool runtime skill:

- `test-triage` — bounded development aid for test failures. Do not call the ordinary
  runtime skill a validated token-saving route. The savings claim applies only when the
  [L01 benchmark-pipeline preconditions](../../docs/history/company-llm-delegation-research-20261103.md#l01-token-saving-pipeline-preconditions) are reproduced.
- `log-triage` — bounded development aid for log compression only; EXPERIMENTAL for
  token-saving/reliability claims.
- `context-pack` — existing evidence-first Context Packet for general evidence
  normalization/summarization; EXPERIMENTAL for token-saving/reliability claims. Do not
  create a duplicate evidence-summary skill.

Do not use `review-diff` as the cheap final reviewer for code correctness merely because
it exists. Required independent review stays independent and strong.

## Task sizing and ladder

Home LLM work grows by a ladder (Owner 2026-11-10, #120). The PM logs every call in `~/.local/state/llmtool/home-llm-ladder.jsonl` (time, Task, tier, first-try result, repairs, outcome).

| Tier | Size |
| --- | --- |
| T1 | One contiguous edit in one existing file |
| T2 | Tests, or up to 40 lines, in one file; at most 3 KB of context |
| T3 | Code and tests in two files; at most 6 KB of context |
| T4 | A new file, or up to 3 files; at most 10 KB of context |
| T5 | A small feature |

- Promote one tier after 3 first-try SOLVED in a row at the current tier. On failure step down one size tier; pause only after 2 consecutive failures at the lowest tier.
- Repairs follow D1: two in total, the second only after an AI Debug with a confirmed cause (O4).
- Until T2 is stable (3 first-try SOLVED in a row at T2), no Task above T2.
- One contiguous region per Task.
- In agent mode (gw-agent), name the line ranges to read; never ask for a whole large file. Put new tests in a new file.

## Invocation

Use the already configured gateway profile explicitly. For example:

```sh
llmtool context-pack --provider PROFILE_NAME --file BOUNDED_EVIDENCE_FILE --json --verbose
```

The profile name and evidence file are placeholders. `context-pack` uses its existing
structural parser; `test-triage` and `log-triage` are prompt-only in this first
dogfood slice, so record whether their returned JSON actually matches the requested
schema rather than treating prompt compliance as enforced. The plain example above is
ordinary development triage, **not** the validated savings pipeline. Historical L01
measurement used an explicit 4096 output-token budget plus temperature 0 and
`enable_thinking=false` in the resolved gateway profile; the normal default
`max_tokens=2000` is not equivalent. Do not hardcode the example
deployment endpoint into engineering workflows. Do not call the gateway
directly for delegated engineering work; direct probes belong to explicit
`validate-runtime` connectivity/runtime validation.

## After delegation

1. Treat the response as untrusted derived evidence and potentially containing any
   value that was supplied to the model.
2. For ordinary development triage, compare relevant claims with raw command/test/log
   evidence before using them. For the validated L01 savings pipeline, deterministic
   complete-ID coverage and excerpt selection happen before the strong model; the strong
   model may inspect the selected raw excerpts but must not re-read the full raw input.
3. Deterministically validate every returned test/file identifier against the bounded
   supplied evidence (exact/verbatim membership or another explicit deterministic
   rule). For a savings claim, test-triage FAILURE IDs require complete-set coverage,
   not membership-only validation. Mark unsupported identifiers invalid/hallucinated and never use them directly
   as source locators.
4. Codex/Claude decides whether to adopt or override the summary; model-reported
   confidence is never an authority signal.
5. After a repair, rerun the narrow deterministic check, then the required broader
   regression. A gw-agent summary cannot override exit status or test results.
6. If the gateway provider is unavailable, mark this optional dogfood step SKIPPED/BLOCKED and
   continue the normal strong-model workflow unless live gateway-provider evidence is
   explicitly a required gate. Do not silently export the evidence to an external model.

## Development report

For each delegation record only bounded metadata/reference data:

- task type and selected runtime skill;
- provider/profile;
- data permission basis;
- input scope and bytes;
- provider-reported token usage from success **or a safely parseable failure** when P1
  exposes a validated triplet; use `unknown` when usage is absent/invalid or the failure
  occurs before a safe response envelope (for example transport, invalid JSON or
  oversized response);
- latency;
- output/schema validity where applicable;
- returned identifier validity / invalid identifiers where applicable;
- whether the strong developer/reviewer adopted or overrode the result;
- later deterministic/independent verification outcome.

Do not paste raw secrets or a full sensitive log into the report. Use a safe reference,
small redacted excerpt, or digest when raw evidence must remain elsewhere.

## Completion criteria

- Deterministic work was not needlessly delegated.
- The gateway-provider input was explicitly permitted, bounded and deterministically
  pre-redacted/discarded as needed before the provider call.
- Returned test/file identifiers were deterministically checked against supplied
  evidence before being used as locators.
- The selected runtime skill matched the job.
- Strong-model/source/raw-evidence judgement remained authoritative.
- Delegation metadata was recorded without turning the development report into a copy
  of sensitive raw evidence.
