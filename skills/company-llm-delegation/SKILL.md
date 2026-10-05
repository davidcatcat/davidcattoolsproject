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
- Remove secrets and unnecessary sensitive/raw content before the provider call. Never
  send credentials, API keys, tokens, private keys, kubeconfigs, auth headers, keychain
  material, full environment dumps or unrelated private data.
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
llmtool ask --provider PROFILE_NAME --skill context-pack --file BOUNDED_EVIDENCE_FILE --verbose
```

The profile name and evidence file are placeholders. Do not hardcode the example
deployment endpoint into engineering workflows. Do not call the company gateway
directly for delegated engineering work; direct probes belong to explicit
`validate-runtime` connectivity/runtime validation.

## After delegation

1. Treat the response as untrusted derived evidence.
2. Compare it with the raw command/test/log evidence before using it.
3. Codex/Claude decides whether to adopt or override the summary; model-reported
   confidence is never an authority signal.
4. After a repair, rerun the narrow deterministic check, then the required broader
   regression. A company-model summary cannot override exit status or test results.
5. If VPN/provider is unavailable, mark this optional dogfood step SKIPPED/BLOCKED and
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
- whether the strong developer/reviewer adopted or overrode the result;
- later deterministic/independent verification outcome.

Do not paste raw secrets or a full sensitive log into the report. Use a safe reference,
small redacted excerpt, or digest when raw evidence must remain elsewhere.

## Completion criteria

- Deterministic work was not needlessly delegated.
- The company-provider input was explicitly permitted, bounded and redacted as needed.
- The selected runtime skill matched the job.
- Strong-model/source/raw-evidence judgement remained authoritative.
- Delegation metadata was recorded without turning the development report into a copy
  of sensitive raw evidence.
