# llmtool Round 3 — C3-B independent live-wrapper review handoff (2026-10-06)

## Purpose

Perform the final independent review of the exact frozen Round-3 Company-only Phase-0
wrapper before any real Company request is authorized.

This is a review-only gate.

Do not run live Company Phase 0.
Do not call Codex.
Do not modify wrapper/tests/instructions/preregistration in the review.

## Repository / PR

Repository:
`davidcatcat/davidcattoolsproject`

PR:
`#38`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Current review head:
`d12dbb0dff5e6e927708a76d67c2346624195dc0`

Exact tested C3-1 implementation head:
`fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9`

The delta from tested implementation to current review head must be report-only:
`reports/llmtool_token_savings_round3_live_wrapper_c3_1_remediation_validation_20261006.md`.

## Exact reviewed preregistration pin

The exact current `phase0_preregistration.json` SHA-256 to independently verify and
pin for any later live handoff is:

`0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276`

The historical pre-C3-1 manifest SHA:
`81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece`

must NOT authorize the remediated wrapper.

## Read directly

- `reports/token_benchmark/20261006_round3/live_wrapper.py`
- `reports/token_benchmark/20261006_round3/test_live_wrapper.py`
- `reports/token_benchmark/20261006_round3/phase0_preregistration.json`
- `reports/token_benchmark/20261006_round3/instructions/L02.txt`
- `reports/token_benchmark/20261006_round3/instructions/L03.txt`
- `reports/token_benchmark/20261006_round3/format_probe.py`
- `reports/llmtool_token_savings_round3_live_wrapper_offline_validation_20261006.md`
- `reports/llmtool_token_savings_round3_live_wrapper_coordinator_review_20261006.md`
- `reports/llmtool_token_savings_round3_live_wrapper_c3_1_remediation_validation_20261006.md`
- production `tools/llmtool/llmtool/providers/openai_compatible.py`
- production `tools/llmtool/llmtool/diagnostics.py`

## Revision integrity

Verify:
- exact tested head is `fff00d31...`;
- current head differs only by the C3-1 validation report;
- all PR changes remain under `reports/`;
- production/runtime/skill/fixture/gold bytes are unchanged;
- `format_probe.py` and `test_format_probe.py` remain the reviewed 88/88 versions.

## Regression evidence

Independently inspect/re-run offline only:

- format-probe: 88/88 PASS;
- live-wrapper: 77/77 PASS;
- git diff --check PASS.

No network/provider live call.

Do not trust counts alone; inspect new C3-1 provider-boundary tests.

## Request freeze / zero-network review

Verify the freeze path really executes:
- real llmtool CLI;
- real OpenAICompatibleClient;
- real urllib Request construction;
- instance opener intercepted before original opener;
- dedicated BaseException sentinel;
- zero original opener calls;
- zero socket/DNS/connection calls.

Verify Request shape:
- endpoint exactly `http://home-llm.example:8000/v1/chat/completions`;
- POST;
- no Authorization;
- allowed top-level fields exactly:
  - model
  - temperature
  - max_tokens
  - messages
  - chat_template_kwargs
- no stream/tools/tool_choice/response_format/extras;
- model = gw;
- temperature numeric 0, not bool;
- max_tokens integer 4096, not bool;
- chat_template_kwargs exactly enable_thinking=false;
- exactly system + user messages.

Frozen hashes must be independently reproduced:

L02:
- user_message:
  `e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac`
- request_body:
  `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`

L03:
- user_message:
  `3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2`
- request_body:
  `6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0`

These hashes must be unchanged from pre-C3-1.

## Manifest/live authorization review

This is mandatory.

The eventual live execution must NOT do:

```python
registration = json.load(...)
runner = Phase0Wrapper(registration)
```

from an arbitrary caller-provided registration.

The live handoff must require:

```python
registration = load_preregistration(
    "0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276"
)
runner = Phase0Wrapper(registration)
```

or an equivalent sealed path with the same independently reviewed manifest digest.

Verify `load_preregistration(expected_sha256)` checks the raw manifest digest before
JSON decoding, then verifies all artifact hashes and lane/user-message/config/skill
contracts.

If this requirement cannot be guaranteed by the next live handoff, verdict is BLOCKED.

The current script's CLI should still expose only `--freeze`, not a live command.

## C3-1 provider-response accounting review

Inspect the production provider and wrapper directly.

Verify:
- MODEL_RESPONSE_INVALID -> TRANSPORT_OR_REQUEST_FAILURE;
- MODEL_RESPONSE_TOO_LARGE -> TRANSPORT_OR_REQUEST_FAILURE;
- MODEL_OUTPUT_INCOMPLETE remains MODEL_OUTPUT_INCOMPLETE;
- known network/http/rate-limit/auth codes retain the prior mapping;
- unknown codes fail closed.

Inspect real-provider-boundary tests for:
- invalid JSON;
- unexpected finish_reason;
- tool_calls/function_call;
- empty/invalid content;
- too-large response branch;
- length response.

For every emitted invalid/too-large response confirm:
- Request admission and mark_request_emitted occurred;
- safe code came from the actual production provider boundary;
- record becomes terminal FAIL;
- primary = TRANSPORT_OR_REQUEST_FAILURE;
- usage_total = null;
- complete_total = null;
- secondary includes COMPANY_TOKEN_ACCOUNTING_UNKNOWN;
- every later logical trial run through runner is NOT_STARTED /
  COMPANY_TOKEN_ACCOUNTING_UNKNOWN;
- opener/delegation count stays exactly one;
- no retry/max-token increase.

For length confirm primary remains MODEL_OUTPUT_INCOMPLETE with unknown accounting/global
stop.

## Usage accounting review

Verify successful usage is accepted only when:
- prompt_tokens int >=0;
- completion_tokens int >=0;
- total_tokens int >=0;
- total == prompt + completion.

Any missing/bool/negative/inconsistent usage after emitted request must become unknown,
not zero, and globally stop remaining Phase-0 execution.

## Gold isolation review

Verify:
- freeze/request construction cannot read L03 gold;
- user message/instructions do not depend on gold;
- anchor evaluation has no gold loader;
- only after anchor FrozenEvaluation exists does metadata loader run;
- metadata bytes' SHA is checked before JSON decode;
- loader returns target_ids only;
- target metadata cannot alter prompt/anchors/retry.

Frozen grading metadata SHA:
`0e54eb372345ba64105512744c09a7cb60027fbebeebd04fd7834f51bbf2e8d8`.

## Replay / CE10 review

Try to reproduce the prior hand-forged internally-consistent FrozenEvaluation path.

Verify finalization:
- reads preserved raw stdout bytes;
- reruns RawStdout + lane adapter;
- L03 reruns verified post-freeze recall;
- requires byte-identical result_json;
- requires raw stdout digest consistency;
- requires evaluation digest consistency;
- reconciliation runs before accounting completion.

A forged evaluation must not enter accounting.

Changed raw stdout, fixture, or metadata digest must fail closed.

## Instruction review

L02:
- exact/verbatim canonical EVENT lines in evidence/timeline;
- exactly once;
- timeline chronological;
- no annotation/prefix/suffix;
- ID/canonical-field explanatory prose kept out of evidence/timeline;
- no root cause/recommendation/self-grade.

L03:
- one fixture record -> at most one evidence item;
- one evidence item may support multiple semantic categories;
- exact source + locator;
- no inventory evidence;
- no fuzzy/invented provenance;
- facts/inferences/hypotheses/unknowns/verify_next remain distinct.

Neither instruction may contain L03 gold target IDs.

## State-machine / live sequencing review

Verify:
- order L02-1,L03-1,L02-2,L03-2,L02-3,L03-3;
- no retry;
- max one Request per started trial;
- per-fixture early stop after semantic/format failure;
- global stop after unknown consumed usage;
- 300k ceiling;
- 35k L02 / 59k L03 admission bounds;
- skipped slots never fabricate request proof or usage.

## Publication/raw stdout review

Verify:
- successful CLI stdout bytes are preserved unedited with O_EXCL private creation;
- RawStdout hashes before scan/parse;
- secret-marker publication guard blocks publication;
- no redacted bytes are substituted into gate/replay.

## Required verdict

Finish with:

```text
PR #38 ROUND-3 C3-B LIVE-WRAPPER REVIEW:
PASS
or
BLOCKED

Exact review head:
d12dbb0dff5e6e927708a76d67c2346624195dc0

Exact tested implementation head:
fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9

Pinned preregistration SHA256:
0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276

Revision integrity:
PASS / finding

Zero-network Request freeze:
PASS / BLOCKED

Frozen Request/user-message digests:
PASS / BLOCKED

Pinned manifest loader requirement:
PASS / BLOCKED

C3-1 provider-response accounting:
CLOSED / BLOCKED

Usage accounting:
PASS / BLOCKED

Gold isolation:
PASS / BLOCKED

Replay reconciliation / CE10:
PASS / BLOCKED

Instruction contracts:
PASS / BLOCKED

Phase0 sequencing/no-retry/budgets:
PASS / BLOCKED

Offline regressions:
88/88 helper; 77/77 wrapper / finding

New before-live blockers:
NONE / details

May prepare exact live Phase-0 execution handoff:
YES / NO

May run live Company Phase 0 now:
NO — exact live execution handoff must pin manifest SHA and request one explicit live authorization step
```

Commit only the independent C3-B review report.
Do not modify implementation in this review.
