# llmtool Round 3 Phase 0 — C3 live-wrapper offline freeze handoff (2026-10-06)

## Goal

Implement and freeze the **live Company-only Phase-0 wrapper contract** without sending
any live network/provider request.

This is C3-A: wrapper implementation + zero-network request freeze + offline self-tests.

Live Company Phase 0 remains forbidden until a later independent C3-B review passes.

## Repository / branch

Canonical repository only:

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:

`bench/llmtool-token-savings-round3-format-compliance-20261006`

Starting remote head before this handoff:
`0092c892b1a17aa3ab238f545aaa03efd41c6076`

Never use `/private/tmp/*` worktrees/clones.

## Frozen helper baseline

Exact tested helper implementation:
`c1830ef2c60fbda61777461ffd20b3dd312a84ec`

Fresh regression:
- Python 3.9.6
- 88/88 PASS
- 0 FAIL / 0 ERROR / 0 SKIP
- exit 0
- git diff --check PASS

C1 and C2 are CLOSED.

Do not modify `format_probe.py` or `test_format_probe.py` in C3-A unless an
independent reviewer first identifies a new helper blocker. C3 belongs in a separate
wrapper/test layer.

## Existing production path to reuse unchanged

Use the actual llmtool/openai-compatible path. Do not reimplement the provider.

Production provider:
`tools/llmtool/llmtool/providers/openai_compatible.py`

Frozen Company configuration:

```yaml
active_provider: company-round3-phase0
providers:
  company-round3-phase0:
    type: openai-compatible
    base_url: http://home-llm.example:8000
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

Requirements:
- isolated temporary HOME;
- never modify real user config;
- no API key;
- no Authorization header;
- no ambient proxy forwarding from the provider;
- no fallback;
- no retry;
- no max-token amendment.

## Frozen Phase-0 logical order / accounting

Use the existing `Phase0Accounting` only.

Order:
1. L02-1
2. L03-1
3. L02-2
4. L03-2
5. L02-3
6. L03-3

No retry.

Per-fixture early stop remains frozen.

Token admission:
- total ceiling 300,000;
- L02 pre-request bound 35,000;
- L03 pre-request bound 59,000.

If a potentially consumed/emitted request has unknown provider usage:
- usage stays null/unknown;
- global accounting becomes unknown;
- later requests do not run.

## Frozen fixture / grading digests

L02 fixture:
`859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da`

L03 fixture:
`44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce`

L03 grading metadata / gold:
`0e54eb372345ba64105512744c09a7cb60027fbebeebd04fd7834f51bbf2e8d8`

The L03 gold is grading-only and must never enter:
- instructions;
- user message;
- request freeze;
- anchor construction;
- retry/accounting decisions.

It may be loaded only after the L03 anchor result is frozen, and only after its SHA-256
is checked.

## Frozen benchmark instructions

Create two separate immutable text files under:

`reports/token_benchmark/20261006_round3/instructions/`

### L02 instruction contract

The exact file must tell the Company model to use the existing `log-triage` schema and:

- use only supplied material;
- in `evidence`, include every canonical line beginning
  `EVENT event_id=` exactly once, **verbatim as one complete array item**;
- in `timeline`, include the same canonical EVENT lines exactly once, verbatim, in
  observed chronological order;
- do not add prefixes, suffixes, commentary or annotations to canonical EVENT items;
- `error_signatures` lists the canonical observed signature names only, without prose;
- put explanatory prose only in `summary`, `repeated_patterns`, `unknowns`, or
  `strong_review_reasons`;
- do not place prose containing EVT-/REQ- identifiers or canonical field labels
  (`event_id`, `request_id`, `signature`, `ts` with ':' or '=') in
  `evidence` or `timeline`;
- no root-cause claim, recommendation, command, self-grading or external verification;
- return exactly one JSON object matching the existing skill schema, no fences.

This instruction exists to make the frozen deterministic L02 classifier unambiguous.

### L03 instruction contract

The exact file must tell the Company model to use the existing A4/context-pack schema and:

- analyze the upload-window topic from the supplied mixed evidence only;
- inventory records are background noise: do not create evidence citations from
  inventory records;
- keep facts / inferences / hypotheses / unknowns / verify_next distinct;
- every substantive category item must be supportable by one or more evidence entries;
- each unique fixture record may appear at most once in the evidence array;
- one evidence entry may support multiple semantic category items;
- every evidence item uses the **exact source and exact locator** present in the supplied
  RECORD line;
- do not fuzzy-normalize source/locator;
- do not invent provenance;
- record_id text in the statement is optional and ignored for anchoring;
- do not promote release/cache hypotheses into facts;
- do not self-grade;
- no fences, hidden reasoning, tools, commands, file reads or external verification;
- return exactly the existing six-field A4 JSON schema.

No target IDs or gold-derived facts may be added to this instruction.

## Exact user-message construction

For each lane:

`user_message_bytes = instruction_file_bytes + b"\n\n" + fixture_bytes`

No other separator or normalization.

The live request must contain exactly one user message whose UTF-8 bytes are exactly
those bytes decoded as UTF-8.

The instruction file and fixture bytes must be individually SHA-256 verified before
construction.

## Zero-network Request freeze

Before any live call, run the **real llmtool CLI path** through the real
`OpenAICompatibleClient`, but replace only `self.opener.open` with an observer that:

1. receives the actual constructed `urllib.request.Request`;
2. verifies/captures it;
3. **does not call the original opener**;
4. raises a dedicated `BaseException` sentinel that escapes provider exception
   handling and is caught by the dry-run harness.

This dry-run must create **zero sockets / zero network requests**.

It is not enough to manually construct a JSON body in test code.

For each lane, freeze:
- exact instruction SHA-256;
- exact fixture SHA-256;
- exact user-message SHA-256;
- exact config SHA-256;
- exact relevant runtime skill SHA-256;
- exact request body SHA-256;
- exact ordered message roles/count;
- request URL;
- HTTP method;
- exact allowed top-level body field set.

Allowed request-body top-level fields are exactly:

```text
model
temperature
max_tokens
messages
chat_template_kwargs
```

Forbidden:
- `stream`;
- tools/tool_choice;
- response_format;
- extra provider-specific fields;
- duplicate JSON keys;
- NaN/Infinity.

The body must prove:
- model == `gw`;
- temperature numeric 0 but not bool;
- max_tokens integer 4096 but not bool;
- chat_template_kwargs exactly `{"enable_thinking": false}`;
- no Authorization header;
- URL exactly `http://home-llm.example:8000/v1/chat/completions`;
- method POST.

The frozen whole-request-body SHA-256 from this dry-run becomes the live admission
contract. Live execution must reject the Request **before network I/O** if it differs.

## Live observer contract (implement, do not execute yet)

The wrapper's live observer must run immediately before delegating to the original
`opener.open`.

It must:
- recompute the full request-body SHA-256;
- recompute the exact user-message SHA-256;
- require both to equal the preregistered dry-run values;
- require exact allowed top-level fields;
- require no Authorization;
- require exact URL/method/config fields;
- build the frozen `RequestObservation` through the reviewed
  `observe_request_without_mutation()`;
- only then call the original opener exactly once.

If any check fails:
- do not call network;
- record pre-request contract failure;
- do not fabricate usage;
- do not retry.

## Transport / finish_reason attribution

Do not infer provider failures from prose.

Use the production llmtool safe error code.

Map:
- provider/network/timeout errors after an observed emitted request ->
  `TRANSPORT_OR_REQUEST_FAILURE`;
- `MODEL_OUTPUT_INCOMPLETE` (production path maps `finish_reason=length`) ->
  `MODEL_OUTPUT_INCOMPLETE`.

No length retry and no raised max_tokens.

If a Request was emitted and provider usage is unavailable:
- usage remains unknown;
- stop later Phase-0 requests under existing accounting rules.

On successful request:
- require provider-reported prompt_tokens, completion_tokens and total_tokens all present
  as nonnegative integers;
- require total_tokens == prompt_tokens + completion_tokens;
- otherwise treat usage as unknown (never zero).

## Raw stdout / publication

For each emitted successful request:
- preserve exact CLI stdout bytes before parsing;
- construct reviewed `RawStdout` from those bytes;
- its SHA-256 must be computed before parse;
- secret-marker scan must pass before publication/commit;
- never redact/edit stdout and then use the edited version for the gate.

Raw stdout artifacts are synthetic experiment output only.

## L02 final evaluation

Use the frozen L02 fixture bytes and reviewed:
`evaluate_l02_without_gold()`.

Do not provide gold.

Only the final `FrozenEvaluation` may be passed to
`Phase0Accounting.complete_evaluation()`.

## L03 final evaluation / grading metadata

Sequence is mandatory:

1. capture immutable raw stdout;
2. parse/build L03 structured anchors from fixture only;
3. freeze anchor evaluation;
4. verify L03 gold/evaluation-metadata SHA-256;
5. load only `target_ids` from the verified grading metadata;
6. run `evaluate_l03_target_recall_after_freeze()`;
7. pass only that final FrozenEvaluation to accounting.

No target metadata may feed back to output/anchors/instructions/retry.

## Raw-stdout replay reconciliation (C3 / CE10)

Before a logical trial may be recorded as final PASS or FAIL:

- reconstruct `RawStdout` from the preserved raw stdout artifact;
- rerun the lane adapter from scratch with the frozen fixture;
- for L03, rerun post-freeze recall using the separately verified grading metadata;
- require the replayed final `result_json` to be byte-for-byte identical to the
  originally produced final `result_json`;
- require the resulting evaluation SHA-256 to match;
- require raw stdout SHA-256 to match.

If reconciliation differs:
- do not record a semantic PASS;
- stop with a wrapper integrity failure;
- do not retry.

This closes the hand-forged `FrozenEvaluation` CE10 path.

## Artifacts to create in C3-A

Under:
`reports/token_benchmark/20261006_round3/`

Create:
- `instructions/L02.txt`
- `instructions/L03.txt`
- `phase0_preregistration.json`
- `live_wrapper.py`
- `test_live_wrapper.py`

The preregistration must include all frozen digests and dry-run Request body hashes.

Do **not** create real live results/raw_outputs yet.

A directory placeholder/README for future raw outputs is allowed, but no fabricated
results.

## Required offline tests

All tests must prohibit sockets/subprocess network escape unless specifically invoking
the CLI dry-run subprocess whose opener is guaranteed to stop before network.

At minimum test:

### Request freeze
- actual CLI dry-run captures exactly one Request;
- original opener/network is never called;
- exact user-message digest frozen;
- exact whole request-body digest frozen;
- exact top-level field set;
- stream/extra field rejected;
- Authorization rejected;
- wrong URL/method/model/temp/max_tokens/thinking rejected;
- bool coercion rejected;
- request-body drift rejected before live opener;
- user-message drift rejected before live opener;
- L02 and L03 produce stable deterministic request-body hashes across repeated dry-runs.

### Gold isolation
- dry-run/freeze cannot access L03 gold;
- live request construction cannot access gold;
- adapter path cannot access gold;
- only post-anchor grading loader receives metadata;
- metadata digest mismatch stops before loader content is used.

### Response/accounting
- safe transport code maps correctly;
- MODEL_OUTPUT_INCOMPLETE maps correctly;
- no retry path exists;
- missing usage after emitted request => unknown/global stop;
- successful usage must be complete and arithmetically consistent;
- L03 anchor-stage cannot complete accounting;
- 3/7 final L03 cannot pass gate;
- per-fixture early stop remains intact.

### Replay reconciliation
- honest frozen evaluation replay matches;
- forged internally-consistent FrozenEvaluation is detected by replay mismatch;
- changed raw stdout detected;
- changed fixture digest detected;
- changed metadata digest detected;
- replay cannot mutate the original evaluation/accounting state.

### Instruction contract
- L02 instruction contains the verbatim-line / prose-placement constraints;
- L03 instruction contains one-record-one-evidence / no-inventory constraints;
- neither instruction contains any frozen target IDs from L03 gold;
- prompt bytes are instruction + exactly two LF bytes + fixture bytes.

### Existing helper regression
Re-run:
`/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py`

It must remain 88/88 PASS.

## Required offline validation

Run on canonical Mac with Python 3.9.

No live Company call.

Report:
`reports/llmtool_token_savings_round3_live_wrapper_offline_validation_20261006.md`

Include:
- exact tested head;
- Python version;
- helper regression result;
- wrapper test count/result;
- dry-run request-freeze count;
- zero-network proof;
- L02/L03 instruction/config/request/user-message digests;
- fixture/gold/skill digests;
- replay tests;
- confirmation Company live calls = 0;
- confirmation Codex calls = 0;
- confirmation production/runtime/skill changes = NONE;
- readiness for independent C3 review.

## Stop point

Commit/push C3-A implementation, tests, frozen preregistration and offline validation.

Then STOP.

Do not:
- run live company Phase 0;
- call Codex;
- modify production provider/runtime/skills;
- modify frozen fixtures/gold;
- create semantic retries;
- merge PR #38.

Live Company Phase 0 requires a separate independent C3-B review of the exact frozen
wrapper/preregistration/digests.
