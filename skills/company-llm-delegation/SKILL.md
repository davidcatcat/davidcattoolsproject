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

- **Web search for gw (Owner 2026-10-28, [AGENTS.md](../../AGENTS.md)):** gw has no tools of its own. The PM or a Developer may search the web for the Task and add the relevant, non-sensitive results to gw's context (`--context-stdin`), labelled as untrusted reference material with their source URLs. Keep the excerpt short. The data rules below apply to it.

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
- For ordinary development triage, bound the input to the minimum evidence needed:
  command/check identity, exit status, failing test names, selected stack trace/error
  lines, relevant file names and small necessary snippets. Do not send the whole
  repository or an entire CI log by default. **Do not infer token savings from this
  ordinary bounded-input mode**; the positive L01 result used a different large-input
  benchmark pipeline described below.
- Preserve the original deterministic/raw evidence separately; the model output is a
  derived summary, not ground truth.

## Availability classification

This classification applies to the triage use above and to the company `gw` Developer
route in [AGENTS.md](../../AGENTS.md). Check the four layers separately. Record each
layer as `PASS`, `FAIL` or `UNKNOWN`, with its time and evidence, in the
[Worker availability table](../../tools/llmtool/ROADMAP.md#worker-availability--live-status):

1. **TCP:** can a connection to the configured endpoint open? Example: a bounded
   `nc -z` connect, with no request body.
2. **API authentication:** does an authorized llmtool call get past auth? llmtool
   reports a rejection as `AUTH_PROVIDER_REJECTED`; an unreachable endpoint is
   `NETWORK_PROVIDER_UNREACHABLE`.
3. **Inference:** does the call return a valid model result? A malformed result is
   `MODEL_RESPONSE_INVALID`; a timeout is `PROVIDER_TIMEOUT`.
4. **Task execution:** did the route produce output for that task class that passed
   its deterministic checks and independent review?

Do not infer a layer from an earlier layer, and do not infer it from the VPN service
list (`scutil --nc list`). Use that list only as a hint for the Owner reminder in
[WORKFLOW](../../tools/llmtool/WORKFLOW.md). A TCP `PASS` authorizes nothing. Layers
2–4 require an llmtool call that the existing data-permission basis already covers,
and C1B live calls also need the Phase 0 grant below. Without that authorization the
layer stays `UNKNOWN`. Never bypass a gate because TCP is reachable.

## Evidence status by route

Routing evidence is task-specific; a provider PASS in one skill does not authorize
another skill.

- **`test-triage` — only the L01 benchmark pipeline is VALIDATED for savings.** Two
  independent run orderings preserved equivalent quality while reducing Codex tokens by
  39.82% and 37.55%. The validated pipeline used ~48 KiB canonical `FAILURE id=` raw
  output, complete deterministic failure-ID coverage, a ±2-line excerpt selector capped
  at `min(32 KiB, 20%)`, and direct injection of selected raw text so Codex made 0 tool
  calls and did not re-read the full output. Plain `ask --skill test-triage` has no
  positive savings evidence; Round-1 B01's small-evidence configuration measured
  −103.32% and first hit a length failure at the default output budget.
- **`log-triage` — EXPERIMENTAL for savings/reliability claims.** Round 3 produced one
  PASS and one `finish_reason=length` failure. It may still be used as bounded
  development assistance, but do not call it a validated savings route.
- **`context-pack` — EXPERIMENTAL for savings/reliability claims.** Round 3 replay had
  clean structured provenance but only 3/7 target-record recall, and exposed an
  instruction/gate semantic mismatch.

P1 provider accounting is merged and can preserve a valid usage triplet on parseable
failure responses. That improves telemetry only; the original model failure remains a
failure.

For a new or redesigned route, require a pre-registered **Company-only reliability
Phase 0** before authorizing any paired Codex benchmark. If the Company-only gate fails
or accounting is incomplete, stop; do not spend Codex tokens trying to measure savings.

## L01 token-saving pipeline preconditions

The positive 39.82% / 37.55% observations apply only to the frozen research pipeline,
not to ordinary runtime skill invocation. A future reproducible/productionized version
must preserve all of these conditions before inheriting the savings claim:

1. Raw test output is large enough to justify delegation. The measured fixture was
   approximately 48 KiB and used canonical `FAILURE id=` records. Small evidence is
   not assumed beneficial; Round-1 B01 measured −103.32%.
2. The company model receives the complete bounded raw test output needed for the
   triage task, after deterministic permission/redaction checks.
3. Returned failure IDs are checked for **complete set equality** against all expected
   FAILURE IDs in the bounded fixture—no missing and no extra IDs.
4. A deterministic selector extracts only the anchored original text (historically
   ±2 lines), with a hard cap of `min(32 KiB, 20%)` of the original raw input.
5. The strong model receives the company summary plus the selected original excerpts
   inline and does **not** re-read the full raw output. Re-reading the full raw input
   invalidates the historical savings comparison.
6. Historical generation settings were temperature 0, `enable_thinking=false`,
   `max_tokens=4096`, timeout 120. Ordinary llmtool defaults are not equivalent.
7. Current coverage/selector code is frozen benchmark code and only supports the
   canonical research format. Real pytest/unittest output has not passed this gate.

Until a deterministic real-format selector and Company-only Phase 0 are implemented,
ordinary `test-triage` remains useful for bounded triage but is **UNVALIDATED for
token-saving claims**.

## C1B test-triage Worker (source/fake only)

`llmtool test-triage-worker --file … --exit-code … --profile … --json` (C1B, Issue #120)
is a bounded advisory Worker over production `test-excerpts`. Status: **source and fake
tests only; the public live route is BLOCKED** unless `--phase0-grant FILE
--phase0-grant-sha256 HEX` name a private 0600 grant whose pin, profile, origin, model,
capture digest and UTC window all match. It does not reuse the frozen L01 selector or
its savings claims.

- **The grant is not authorization.** A same-UID process can write a grant file and
  compute its pin. The seam only stops accidental or implicit live calls. A real Phase 0
  run needs the owner's prior #120 grant record (field-for-field equal to the file), the
  VPN step and a non-author check of the per-invocation evidence afterwards.
- **Owner-only Live grants.** AI agents must not create, modify or fabricate any
  grant file that could be used for real Live execution. Only the Owner supplies
  real grants. Before each real Phase0 invocation a non-author must confirm that
  authorizer_ref points to the Owner's actual #120 authorization comment, and
  reconcile the exact grant hash, route/data/capture/time/call limits. URL-format
  and SHA checks alone do not prove Owner authority. Isolated synthetic grants
  are permitted only with fake openers/local synthetic servers, never real routes.
- **Off-repo only.** Grant files inside any Git worktree are rejected, including
  canonical/fixed roots and unrelated repositories, even in private0700 folders.
  Bounded physical ancestor/.git checks are cooperative, not same-UID race isolation.
- **Worker schema v2 (breaking).** Fixture and grant routes both return
  schema_version2. digests.request_body hashes actual HTTP wire bytes; the old
  compact-message digests.prompt key is removed. Grant DATA remains schema_version1.
- **One serialization.** The provider's own request bytes are capped (64 KiB),
  re-scanned, digested and sent once through its own opener (no proxy, no redirect).
- **`max_calls` is informational.** There is no store, so it cannot be enforced across
  runs. Capture and max_calls limits are1..7. The run report must list every invocation
  with its request-body digest; no cross-process replay/call cap is claimed.

- **Deterministic first.** The selector must be COMPLETE and every failure ID must have
  a selected excerpt. Otherwise there are zero requests.
- **Counts come from the selector.** Records, occurrences and unique IDs are computed
  there; the model only classifies each ID with refs bound to that ID, and any model
  totals or summaries are rejected.
- **One call.** Explicit `openai-compatible` profile only (no active/default fallback),
  one request, no retry or fallback.
- **Usage.** It is a complete gateway-reported triplet or `UNKNOWN`. Unknown is never
  replaced by 0; a reported, valid `(0,0,0)` is known.
- **Advisory.** Output is `ADVISORY_WORKER_OUTPUT`: not review, QA, Security, merge,
  egress or budget authority. It never turns a failed capture into a pass.
- **Not measured.** Quality, fidelity and savings stay UNKNOWN until Phase 0.
- **Exit status.** While the live route is blocked the command exits 1 after local
  checks. With a valid grant it keeps the test-excerpts rule. A failed capture is never
  reported as a pass.
- **Phase 0 data.** `reports/company_phase0/` holds the C0–C6 generator and the
  preregistration candidate (digests, gold labels, verdict and stop rules). That
  candidate is not a grant.

## Runtime skill selection

Use the smallest applicable llmtool runtime skill:

- `test-triage` — bounded development aid for test failures. Do not call the ordinary
  runtime skill a validated token-saving route. The savings claim applies only when the
  L01 benchmark-pipeline preconditions below are reproduced.
- `log-triage` — bounded development aid for log compression only; EXPERIMENTAL for
  token-saving/reliability claims.
- `context-pack` — existing evidence-first Context Packet for general evidence
  normalization/summarization; EXPERIMENTAL for token-saving/reliability claims. Do not
  create a duplicate evidence-summary skill.

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
schema rather than treating prompt compliance as enforced. The plain example above is
ordinary development triage, **not** the validated savings pipeline. Historical L01
measurement used an explicit 4096 output-token budget plus temperature 0 and
`enable_thinking=false` in the resolved company profile; the normal default
`max_tokens=2000` is not equivalent. Do not hardcode the example
deployment endpoint into engineering workflows. Do not call the company gateway
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

## Token-savings benchmark

Ordinary delegation telemetry is not enough to claim Codex token savings. Existing
positive savings evidence is limited to the full L01 research pipeline described above;
the ordinary test-triage skill invocation itself is not validated for savings.
For any **new or redesigned route**, do not start with a paired benchmark: first pass a
pre-registered Company-only reliability Phase 0. Only after that PASS may a paired
benchmark be authorized. When a paired benchmark is actually allowed:

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
