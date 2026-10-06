# llmtool Round 3 — C3-1 provider-response accounting remediation handoff (2026-10-06)

## Goal

Remediate the single before-live blocker found in the C3-A coordinator review:

> an emitted Company request that later fails with a provider/model response protocol error
> must still receive a terminal logical-trial record, with usage unknown and global
> token accounting unknown.

No live Company request is authorized in this step.

## Canonical repository

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Expected remote head before remediation:
`935c3666cefb2b69cc914eb11b01c5124d242603`

Never use `/private/tmp/*` worktrees/clones.

## Read first

- `reports/llmtool_token_savings_round3_live_wrapper_coordinator_review_20261006.md`
- `reports/token_benchmark/20261006_round3/live_wrapper.py`
- `reports/token_benchmark/20261006_round3/test_live_wrapper.py`
- production `tools/llmtool/llmtool/providers/openai_compatible.py`
- production `tools/llmtool/llmtool/diagnostics.py`

## Scope

Allowed:
- modify only the benchmark wrapper/test layer under `reports/token_benchmark/20261006_round3/`;
- add/adjust C3-1 offline tests;
- update preregistration only if wrapper/test bytes change and therefore artifact digests must change;
- publish a fresh remediation validation report.

Forbidden:
- live Company/network request;
- Codex benchmark call;
- production/runtime/skill changes;
- instruction changes unless strictly required by this blocker (not expected);
- fixture/gold changes;
- retries/fallback;
- merge PR #38.

## Required classification

Production `OpenAICompatibleClient.complete()` can raise after Request emission:

- `MODEL_RESPONSE_INVALID`
- `MODEL_RESPONSE_TOO_LARGE`

These include invalid JSON/envelope/choices/content/finish_reason/tool-call response
and >16 MiB response.

Under the existing frozen Phase-0 failure taxonomy, classify both as:

`TRANSPORT_OR_REQUEST_FAILURE`

Rationale:
- no valid semantic model output reached the benchmark adapter;
- the request may already have consumed Company tokens;
- the frozen taxonomy has no separate provider-response-protocol bucket;
- usage is not trustworthy/available from the failed provider path.

Do not add a new failure category.

## Required run_next behavior

For an emitted request followed by either safe code:

- logical trial becomes terminal FAIL;
- primary failure reason = `TRANSPORT_OR_REQUEST_FAILURE`;
- usage_total = null/unknown;
- complete company token total becomes unknown;
- all subsequent logical trials become
  `COMPANY_TOKEN_ACCOUNTING_UNKNOWN`;
- no retry;
- no max-token amendment;
- wrapper may remain halted or accounting-stopped, but the emitted trial must no longer
  remain STARTED.

Do not fabricate usage from partial response bytes.

## Safe-code policy

Keep strict allowlisting.

Expected mapping after remediation:

- `MODEL_OUTPUT_INCOMPLETE` -> `MODEL_OUTPUT_INCOMPLETE`
- `MODEL_RESPONSE_INVALID` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `MODEL_RESPONSE_TOO_LARGE` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `NETWORK_PROVIDER_UNREACHABLE` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `PROVIDER_TIMEOUT` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `PROVIDER_RUNTIME_ERROR` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `PROVIDER_HTTP_ERROR` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `PROVIDER_RATE_LIMITED` -> `TRANSPORT_OR_REQUEST_FAILURE`
- `AUTH_PROVIDER_REJECTED` -> `TRANSPORT_OR_REQUEST_FAILURE`

Unknown/unexpected safe code must remain fail-closed with `WrapperIntegrityFailure`.

## Required tests

Add direct mapping tests for:
- MODEL_RESPONSE_INVALID;
- MODEL_RESPONSE_TOO_LARGE.

Add real production-provider-boundary offline tests using fake in-memory responses, with
no original network opener:

1. invalid JSON response -> production safe code MODEL_RESPONSE_INVALID;
2. valid JSON but invalid finish_reason -> MODEL_RESPONSE_INVALID;
3. valid JSON with tool_calls/function_call or empty/invalid content -> MODEL_RESPONSE_INVALID;
4. response-too-large path -> MODEL_RESPONSE_TOO_LARGE where practical without allocating
   an unsafe amount; otherwise directly exercise the production safe code plus runner/accounting
   behavior with a synthetic provider-boundary exception.

For every emitted-request failure case assert:
- request_emitted == True;
- terminal record status == FAIL;
- primary failure reason == TRANSPORT_OR_REQUEST_FAILURE;
- usage_total is None;
- complete_total is None;
- next trial is COMPANY_TOKEN_ACCOUNTING_UNKNOWN;
- no retry/delegation after the failing attempt.

Also preserve:
- MODEL_OUTPUT_INCOMPLETE semantics;
- per-fixture early-stop tests;
- unknown/unrecognized safe code fail-closed behavior.

## Preregistration / artifact digest rule

Because `live_wrapper.py` and/or `test_live_wrapper.py` will change, regenerate/freeze
the preregistration artifact digests with the zero-network freeze path.

Do not alter the frozen L02/L03 user-message/request-body digests unless the actual real
CLI Request bytes change. If they change unexpectedly, STOP and report drift rather than
accepting it.

Expected Request/body semantics should remain identical to C3-A.

After regeneration record the new:
- `phase0_preregistration.json` SHA-256;
- live_wrapper.py SHA-256;
- test_live_wrapper.py SHA-256.

## Validation

Run from canonical Mac repo with Python 3.9:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py
git diff --check
```

Requirements:
- helper remains 88/88 PASS;
- wrapper suite fully PASS;
- zero live Company calls;
- zero Codex calls;
- zero production/runtime/skill changes.

Also rerun zero-network freeze and confirm:
- L02 request-body SHA unchanged unless justified;
- L03 request-body SHA unchanged unless justified;
- user-message SHA unchanged;
- network/socket calls remain zero.

## Fresh report

Create:

`reports/llmtool_token_savings_round3_live_wrapper_c3_1_remediation_validation_20261006.md`

Include:
- exact tested head;
- Python version;
- helper test result;
- wrapper test result/count;
- C3-1 new tests;
- provider-boundary invalid-response tests;
- terminal accounting behavior;
- zero-network proof;
- old vs new preregistration SHA;
- whether L02/L03 request-body and user-message hashes changed;
- Company live calls = 0;
- Codex calls = 0;
- production/runtime/skill changes = NONE;
- readiness for independent C3-B review.

## Stop point

Commit/push:
- C3-1 wrapper/test remediation;
- regenerated preregistration if needed;
- fresh validation report.

Then STOP.

Do not run live Phase 0.

Independent C3-B review remains required before the first real Company request.
