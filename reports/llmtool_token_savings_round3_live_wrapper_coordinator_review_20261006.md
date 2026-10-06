# llmtool PR #38 Round 3 — C3-A coordinator review (2026-10-06)

Verdict: **APPROVE WITH CHANGES — one before-live blocker (C3-1)**

## Revision integrity

Current PR head reviewed:
`1d2d15c032c0fe64bd1573bc49d6e4f28203517f`

Exact tested C3-A implementation head:
`3b44341cfc2cca8d902f92681cda593fb09dec7d`

The only delta from tested implementation head to current head is:
`reports/llmtool_token_savings_round3_live_wrapper_offline_validation_20261006.md`.

All PR changes remain under `reports/`; no production/runtime/skill/workflow file is
modified.

## Confirmed PASS areas

- existing helper regression: 88/88 PASS on Python 3.9.6;
- wrapper offline suite: 70/70 PASS;
- zero-network real CLI/provider Request freeze for L02 and L03;
- one captured Request per dry-run, original opener never called;
- socket/network count 0 in offline freeze;
- exact request-body and user-message SHA-256 frozen;
- exact body-field set enforced; stream/tools/tool_choice/response_format rejected;
- exact URL/method/model/temp/max_tokens/thinking=false/no-Authorization enforced;
- gold inaccessible to freeze/request construction/anchor construction;
- L03 grading metadata digest verified before decoding;
- replay reconciliation rejects forged internally-consistent FrozenEvaluation;
- usage completeness/arithmetic validation is fail-closed;
- no retry API;
- L02/L03 instruction contracts satisfy C3 requirements;
- preregistration contains runtime source digests and lane freeze facts;
- C1/C2 helper remains unchanged from reviewed 88/88 implementation.

## C3-1 before-live blocker — emitted request with provider-invalid response is not finalized

Production `OpenAICompatibleClient.complete()` can raise typed safe codes after an
actual Request has already been emitted, including:

- `MODEL_RESPONSE_INVALID`
- `MODEL_RESPONSE_TOO_LARGE`

Examples include invalid JSON response, missing/invalid choices, unexpected finish
reason, tool-call response, empty/invalid content, or >16 MiB response.

Current `failure_reason()` accepts:
- MODEL_OUTPUT_INCOMPLETE;
- network/timeout/runtime/http/rate-limit/auth provider codes.

It does **not** accept MODEL_RESPONSE_INVALID or MODEL_RESPONSE_TOO_LARGE.

Current live path is therefore:

1. `admitted_open()` validates Request and calls
   `state.mark_request_emitted(...)`;
2. original opener may consume/send the request;
3. production provider receives a response but rejects it as
   MODEL_RESPONSE_INVALID / MODEL_RESPONSE_TOO_LARGE;
4. `invoke_cli()` returns exit != 0 and the typed safe code;
5. `run_next()` calls `failure_reason(code)`;
6. `failure_reason()` raises WrapperIntegrityFailure;
7. wrapper sets `halted=True`, but the accounting trial remains STARTED with
   request_emitted=True and the complete token total remains the previous known value
   rather than unknown.

This is fail-closed for additional network requests, but it violates the preregistered
experiment accounting contract:

- every emitted logical trial must receive a terminal record;
- possibly consumed request with unavailable usage must record unknown, never zero;
- complete company-token accounting becomes unknown;
- later requests must stop as COMPANY_TOKEN_ACCOUNTING_UNKNOWN.

### Required remediation

Before live execution, freeze the classification of provider-envelope/output-protocol
failures. Recommended mapping under the existing frozen failure taxonomy:

- `MODEL_RESPONSE_INVALID` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `MODEL_RESPONSE_TOO_LARGE` -> `TRANSPORT_OR_REQUEST_FAILURE`

Rationale: no valid semantic Company output reached the benchmark adapter, and the
existing Phase-0 taxonomy has no separate provider-response-protocol category.

Then `run_next()` must call `complete_pre_output_failure(..., usage_total=None)`
for these emitted failures, causing accounting unknown/global stop.

Add real-provider-boundary offline tests for at least:
- invalid JSON response;
- invalid finish_reason / invalid model response;
- response-too-large classification (direct safe-code mapping plus provider-boundary
  test where practical);
- emitted request is terminal FAIL;
- usage_total is None;
- complete_total is None;
- all later trials are COMPANY_TOKEN_ACCOUNTING_UNKNOWN.

Do not add retries.

## C3-B live-authorization hardening

Not a C3-A helper defect, but mandatory before live:

The reviewed preregistration file SHA-256 is:
`81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece`.

The live execution handoff must pin this exact manifest digest (or the post-remediation
replacement digest) and must load it through:
`load_preregistration(expected_sha256)`.

Do not construct `Phase0Wrapper` from an arbitrary caller-provided JSON/dict without
the independently reviewed manifest pin. Otherwise instructions + registration could be
changed together and still satisfy internal consistency checks.

C3-B should either:
- enforce the pinned manifest loader in the live entry path, or
- add an equivalent sealed/typed authorization path whose reviewed digest is mandatory.

The current executable CLI exposes only `--freeze`, so no live call is currently
authorized or exposed from the script entry point.

## Status

Company live calls: 0.
Codex calls: 0.
Production/runtime/skill changes: NONE.

May run live Company Phase 0 now: **NO**.

Next:
1. remediate C3-1 in canonical Mac repo;
2. rerun 88 helper tests + full wrapper suite;
3. publish fresh C3-A remediation validation;
4. independent C3-B review pins exact wrapper/preregistration digests;
5. only then authorize Company-only Phase 0.
