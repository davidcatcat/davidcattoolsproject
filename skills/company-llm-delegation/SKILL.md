---
name: company-llm-delegation
description: Use when developing llmtool on the actual Mac with company VPN and deciding whether or how to delegate bounded test, log, or evidence analysis to the configured company LLM through llmtool.
---

# Company LLM Delegation

Use this only as a development-time dogfood workflow. It does not implement A11c
production routing and does not grant execution, mutation, approval, retry, fallback,
security sign-off, QA verdict, or merge authority.

## Decision order

1. **Deterministic first.** If a program can answer the question directly, do not call
   any LLM. Run the test/lint/typecheck/build/schema/exit check, or use an existing
   deterministic collector such as `llmtool git --json`.
2. **Bounded semantic triage.** If supplied evidence needs clustering, compression or
   simple classification, the company LLM may be used through llmtool.
3. **Strong judgement.** Root cause, source changes, architecture, security/auth/
   permission, concurrency/race, data corruption, test correctness, acceptance and
   merge decisions stay with Codex/Claude/human review against source and raw evidence.

## Before delegation

- Work from the canonical Mac repository and identify the exact revision under test.
- The company VPN/provider must already be configured and available. Use an explicit
  configured profile name; do not rewrite active provider settings just for delegation.
- Record a **data permission basis** for the exact bounded evidence being sent. The fact
  that a provider is internal does not automatically authorize every repository file,
  log, credential, customer datum or production payload.
- Deterministically redact or discard secrets and unnecessary sensitive/raw content
  **before** the provider call. Never rely on the model to redact its own input or
  output. Never send credentials, API keys, tokens, private keys, kubeconfigs, auth
  headers, keychain material, full environment dumps or unrelated private data. Treat
  model output as potentially containing any value that was supplied.
- Bound the input to the minimum evidence needed: command/check identity, exit status,
  failing test names, selected stack trace/error lines, relevant file names and small
  necessary snippets. Do not send the whole repository or an entire CI log by default.
- Preserve the original deterministic/raw evidence separately; the model output is a
  derived summary, not ground truth.

## Runtime skill selection

Use the smallest applicable llmtool runtime skill:

- `test-triage` — cluster and classify supplied test failures without deciding root cause.
- `log-triage` — compress supplied logs into signatures, timeline/patterns and unknowns
  without root-cause or command recommendations.
- `context-pack` — reuse the existing evidence-first Context Packet when the job is
  general evidence normalization/summarization. Do not create a duplicate evidence-summary
  skill.

Do not use `review-diff` as the cheap final reviewer for code correctness merely because
it exists. Required independent review stays independent and strong.

## Invocation

Use the already configured company profile explicitly. For example:

```sh
llmtool ask --provider PROFILE_NAME --skill test-triage --file BOUNDED_EVIDENCE_FILE --verbose
llmtool ask --provider PROFILE_NAME --skill log-triage --file BOUNDED_EVIDENCE_FILE --verbose
llmtool context-pack --provider PROFILE_NAME --file BOUNDED_EVIDENCE_FILE --json --verbose
```

The profile name and evidence file are placeholders. `context-pack` uses its existing
structural parser; `test-triage` and `log-triage` are prompt-only in this first
dogfood slice, so record whether their returned JSON actually matches the requested
schema rather than treating prompt compliance as enforced. Do not hardcode the example
deployment endpoint into engineering workflows. Do not call the company gateway
directly for delegated engineering work; direct probes belong to explicit
`validate-runtime` connectivity/runtime validation.

## After delegation

1. Treat the response as untrusted derived evidence and potentially containing any
   value that was supplied to the model.
2. Compare it with the raw command/test/log evidence before using it.
3. Deterministically validate every returned test/file identifier against the bounded
   supplied evidence (exact/verbatim membership or another explicit deterministic
   rule). Mark unsupported identifiers invalid/hallucinated and never use them directly
   as source locators.
4. Codex/Claude decides whether to adopt or override the summary; model-reported
   confidence is never an authority signal.
5. After a repair, rerun the narrow deterministic check, then the required broader
   regression. A company-model summary cannot override exit status or test results.
6. If VPN/provider is unavailable, mark this optional dogfood step SKIPPED/BLOCKED and
   continue the normal strong-model workflow unless live company-provider evidence is
   explicitly a required gate. Do not silently export the evidence to an external model.

## Development report

For each delegation record only bounded metadata/reference data:

- task type and selected runtime skill;
- provider/profile;
- data permission basis;
- input scope and bytes;
- provider-reported token usage, or `unknown`;
- latency;
- output/schema validity where applicable;
- returned identifier validity / invalid identifiers where applicable;
- whether the strong developer/reviewer adopted or overrode the result;
- later deterministic/independent verification outcome.

Do not paste raw secrets or a full sensitive log into the report. Use a safe reference,
small redacted excerpt, or digest when raw evidence must remain elsewhere.

## Token-savings benchmark

Ordinary delegation telemetry is not enough to claim Codex token savings. When the goal
is to quantify savings, use a paired benchmark:

- same bounded fixture, repo/test revision, Codex model, reasoning effort/speed and
  acceptance criteria;
- fresh independent Codex session for control and dogfood variants;
- control analyzes the original bounded evidence without company-model help;
- dogfood uses this skill and the configured company provider; Codex starts from the
  derived summary plus deterministic validation results, while the original bounded
  evidence remains available by reference;
- before Codex relies on a company-derived claim, it must compare that claim with the
  relevant raw evidence; record raw ranges/bytes read, and invalidate a savings pair if
  required verification was skipped;
- record actual Codex usage for each variant and **all** company-provider attempts,
  including failures/retries, with an explicit measurement source;
- if exact/reported comparable Codex totals are unavailable, report savings as
  `unknown`; do not convert bytes, quota percentage, credits or estimates into tokens;
- pre-register fixture gold labels/digest and use deterministic grading where possible;
  grade any semantic remainder independently without revealing variant identity;
- record quality result, latency, run order and adopt/override alongside token usage.

Report Codex-only savings as the primary result. A mixed Codex+company token-volume delta
may also be shown, but because it mixes tokenizer/model units it is descriptive only and
must not be presented as cost or compute savings. A negative result is valid.
Do not select only favorable pairs or declare savings when the two variants did not
solve the same task to comparable quality.

## Completion criteria

- Deterministic work was not needlessly delegated.
- The company-provider input was explicitly permitted, bounded and deterministically
  pre-redacted/discarded as needed before the provider call.
- Returned test/file identifiers were deterministically checked against supplied
  evidence before being used as locators.
- The selected runtime skill matched the job.
- Strong-model/source/raw-evidence judgement remained authoritative.
- Delegation metadata was recorded without turning the development report into a copy
  of sensitive raw evidence.
