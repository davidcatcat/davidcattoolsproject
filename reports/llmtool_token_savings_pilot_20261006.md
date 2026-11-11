# llmtool Token Savings / Dogfood ROI pilot — 2026-10-06

Status: **FIRST n=1 PAIRED PILOT COMPLETE — INDEPENDENT BLIND QUALITY REVIEW PASS**.

**No positive Codex token-savings claim.** Independent blind grading judged all six
anonymous outputs PASS and all three control/dogfood pairs quality-equivalent. Under
the pre-registered primary KPI, dogfood consumed **57,295 more Codex tokens** across
all three quality-approved pairs: CONTROL 54,849 vs DOGFOOD 112,144, a
**−104.46% saving / +104.46% consumption** result. This is a valid negative,
descriptive n=1 result, not a routing-policy conclusion.

## Current execution provenance and contract

- Actual Mac canonical repository:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-pilot-20261006`; existing Draft PR #36.
- Fetched starting remote HEAD: `1024ebf87dfadb93df3ce0b34e3cb915e0b81704`;
  canonical checkout fast-forwarded safely to that HEAD.
- Fetched `origin/main`: `f4d1019d984105431485ad364861ef07cc4dd887` (PR #35 merge).
- Fixture/gold/schema/task/helper pre-registration commit, also exact repository
  SHA for **all six measured sessions and all five company attempts**:
  `f85413b55ab4f92d0988fdfb39082afebf68a2c4`.
- Task Contract: `TOKEN-PILOT-20261006-v1`, fixed in
  [preregistration.json](token_benchmark/20261006/preregistration.json).
- Model requested: `gpt-6.1-sol`; reasoning effort `high`; service tier `default`;
  approval `never`; sandbox `read-only`; `--ignore-user-config --ignore-rules`.
- Each measured invocation: `codex exec --json --ephemeral`, fresh session, no
  resume, one thread/one turn, same flags and task/acceptance for paired variants.
  The same per-fixture `--output-schema` was used for both variants.
- Actual `codex --version` before **each** measured session: `codex-cli 0.159.2`.
  No version/schema/turn-count drift was observed. Model/service settings are
  requested configuration; JSONL provides no independent runtime model attestation.
- Accounting mapping is the independently accepted
  [Codex 0.159.2 decision](llmtool_codex_01592_usage_mapping_20261006.md) and
  [PASS review](llmtool_codex_01592_usage_mapping_review_20261006.md):
  **`input_tokens + output_tokens`**. Cache/cache-write/reasoning are not added.
- Source: `codex exec --json / turn.completed.usage`; reported fresh-thread aggregate
  including every Responses request within the turn. Tool-related request usage
  is retained, not discounted or treated as context size.

All fixture/gold/schema/task files were committed **before the first measured
session** and were not changed afterwards. See the immutable manifest for all
file/helper digests. Fixture/gold digests:

| Fixture | fixture.txt SHA-256 | gold.json SHA-256 |
|---|---|---|
| B01 | `d167062ecde0000ab8ece6a6050254eddec2ba28bd05c099c903e564c548712e` | `753f9f2c017335cce722912033bea08e3dc626f279cde593b07e875819bc96a6` |
| B02 | `d57a38f250d83c0bc4edfbc736c43643c6ddcab0e4f653a870d504ef9167ad5c` | `d7b5498eec78fd34a603d00e569053f2a9aa4d1c52aed1f8b041abe10c48ac0b` |
| B03 | `1cfb2f95c008f32c4b14c0d1cef79fe3449fd9d126195f556cfb16b794f848ce` | `d8dce3a1437f8495ada044d9ac35d53fa3e6fe1a896a7050b9477a633a8cc977` |

Fixed and actual session order: **B01 CONTROL → DOGFOOD; B02 DOGFOOD → CONTROL;
B03 CONTROL → DOGFOOD**. Company preparation occurred immediately before each
DOGFOOD session, after the preceding session in this sequence. No second paired
repetition was performed.

## Token results — provisional arithmetic, not a quality-approved claim

| Fixture | Control Codex | Dogfood Codex | Company, all attempts | Codex saved | Saving % | Mixed delta | Quality |
|---|---:|---:|---:|---:|---:|---:|---|
| B01 | 18,452 | 37,517 | unknown (known subtotal 3,698) | −19,065 | −103.32% | unknown | Blind PASS; pair EQUIVALENT |
| B02 | 17,582 | 39,294 | unknown (known subtotal 5,814) | −21,712 | −123.49% | unknown | Blind PASS; pair EQUIVALENT |
| B03 | 18,815 | 35,333 | 4,285 | −16,518 | −87.79% | unknown | Blind PASS; literal miss judged semantically equivalent |
| **All 3 quality-approved pairs** | **54,849** | **112,144** | **unknown** | **−57,295** | **−104.46%** | **unknown** | **6/6 PASS; 3/3 EQUIVALENT** |

All three pairs are accounting-comparable and quality-approved under the pinned
version/session/raw-verification rules. Independent blind grading, performed by a
reviewer that only read the anonymous grading package plus frozen fixture/gold/schema/
task files, returned **6/6 PASS and B01/B02/B03 pair parity EQUIVALENT**. The B03
CONTROL literal miss remains preserved in deterministic evidence but was judged
semantically equivalent to the frozen gold; the gold was not changed and the session
was not rerun. These numbers therefore support a quality-approved **negative** Codex
saving observation for this n=1 small-evidence pilot.

Company known usage subtotal for all successful calls is **13,797** tokens. The
all-attempt company total is **unknown**, because two length-rejected attempts did
not report usage. Missing values are not zero. Consequently the overall mixed
volume/delta is unknown. Mixed metrics combine different tokenizers and are only
descriptive; they are not cost, compute or energy savings.

## Six measured Codex sessions

| Session | input_tokens | cached_input_tokens | cache_write_input_tokens | output_tokens | reasoning_output_tokens | Comparable total | turn.completed | Tool calls | Wall seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B01 control | 17,984 | 0 | 0 | 468 | 94 | 18,452 | 1 | 0 | 18.263 |
| B01 dogfood | 37,000 | 18,048 | 0 | 517 | 82 | 37,517 | 1 | 1 | 24.300 |
| B02 control | 16,748 | 0 | 0 | 834 | 205 | 17,582 | 1 | 0 | 28.332 |
| B02 dogfood | 38,167 | 18,560 | 0 | 1,127 | 61 | 39,294 | 1 | 1 | 33.460 |
| B03 control | 18,026 | 0 | 0 | 789 | 117 | 18,815 | 1 | 0 | 25.397 |
| B03 dogfood | 34,412 | 29,184 | 0 | 921 | 127 | 35,333 | 1 | 1 | 28.988 |

For every row: exit 0; exactly one `thread.started`, one `turn.started`, and one
`turn.completed`; usage-field set exactly:

```text
cache_write_input_tokens
cached_input_tokens
input_tokens
output_tokens
reasoning_output_tokens
```

`total_tokens` is not reported; the accepted comparable total is derived from
input/output. Every `cache_write_input_tokens` is zero. Breakdown fields are
preserved in [results.json](token_benchmark/20261006/results.json), with per-session
version, flags, prompt bytes/digest, raw JSONL digest, usage, counts and latency.
No failed/incomplete Codex turn occurred; the unknown-usage invalidation rule was
therefore not needed for these six sessions.

CONTROL received only the shared task instructions, complete bounded fixture,
output requirements/schema and the common CLI context. It had zero tool calls and
no company call/output. DOGFOOD initially received company JSON, gold-independent
mechanical validation, the same task/acceptance, and a raw fixture reference; full
raw evidence was not injected in its initial prompt. All six sessions were fresh;
no prior conversation/session context or opposite-variant answer was supplied.

## Company attempts, selection and runtime evidence

Safe `llmtool config show` projection found only the existing `default` profile,
`openai-compatible`, with no configured endpoint/model. The user then explicitly
**authorized `default` plus CLI overrides**, `--base-url http://192.0.2.10:4000
--model gw`, without configuration mutation. This resolves the profile prerequisite
for this run; it does not pretend a persisted company profile existed.

Actual Mac route check showed endpoint traffic on **utun4**. This is VPN-route
evidence; the successful company inferences below establish runtime reachability.
Config readiness was checked without resolving/printing credentials. No credential
file, Keychain, auth/session file or environment dump was inspected.

All calls used the canonical checkout's installed
`tools/llmtool/.venv/bin/llmtool`; module provenance resolved to this checkout.
Only synthetic, non-sensitive fixture bytes were sent. No direct gateway curl,
provider fallback, active-profile switch, or config write was performed.

| Attempt | Exit | Prompt tokens | Completion tokens | Total tokens | Wrapper seconds | Provider elapsed seconds | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| B01 #1 initial | 1 | unknown | unknown | unknown | 8.934 | unknown | MODEL_OUTPUT_INCOMPLETE |
| B01 #2 manual_retry | 0 | 827 | 2871 | 3698 | 12.505 | 12.08 | PASS |
| B02 #1 initial | 1 | unknown | unknown | unknown | 17.530 | unknown | MODEL_OUTPUT_INCOMPLETE |
| B02 #2 manual_retry | 0 | 1052 | 4762 | 5814 | 20.201 | 19.723 | PASS |
| B03 #1 initial | 0 | 857 | 3428 | 4285 | 14.851 | 14.423 | PASS |

- B01 initial: `ask --skill test-triage --verbose --timeout 30`, existing default
  generation limit (`max_tokens=2000`, `temperature=0.0`). Safe error
  `MODEL_OUTPUT_INCOMPLETE`, run ID
  `0f9ddd77605ee2718a46c9569e36ac78`, `trace=not-saved`; stdout empty.
- B01 manual retry: same input/profile/model/skill, `--timeout 60 --max-tokens 4096`.
- B02 initial: `ask --skill log-triage --verbose --timeout 60 --max-tokens 4096`.
  Safe error `MODEL_OUTPUT_INCOMPLETE`, run ID
  `618c00958a0191aceaf88d394924b56c`, `trace=not-saved`; stdout empty.
- B02 manual retry: same input/profile/model/skill, `--max-tokens 8192`.
- B03 initial only: `context-pack --json --verbose --timeout 60 --max-tokens 8192`.
- All commands also used the explicitly authorized `--provider default --base-url
  http://192.0.2.10:4000 --model gw --file <same-fixture>`.
- Successful provider metadata reported model **`qwen3.6-35b-a3b`**, separately from
  requested route/model `gw`.
- Counts: **5 attempts = 3 successes + 2 failures; 2 manual retries; 0 automated
  retries; 0 fallback**. All attempts, not just successes, are in results.json.

Deviation from the planned zero manual retries is recorded explicitly: the two
length failures required higher completion bounds for usable company results.
No fixture, gold, acceptance instruction, or Codex setting changed. These are
manual company-phase repairs, not another measured Codex repetition or a production
retry feature. Company latency includes failures and retries: B01 **21.439 s**,
B02 **37.731 s**, B03 **14.851 s**, based on wrapper timings.

`llmtool --verbose` emitted usage only for successful normalized replies. It did
not expose tokens for rejected length responses; no raw response/body was inspected
and no missing failed-attempt token value was reconstructed. Known company usage is
therefore a partial subtotal, never labeled the complete total.

## Deterministic validation and actual raw verification

Before each DOGFOOD session, the company output passed the fixed output schema.
B01 exact test/file membership: zero invalid IDs, all four failures covered once.
B02 exact identifier/signature/timestamp membership: zero invalid IDs; all required
timeout request IDs, later success and recovery identifiers covered. Semantic
causal ordering/completeness remains a blind-review item. B03 passed the existing
**A4 ContextPacket parser** and source/locator membership, with zero invalid IDs.
No mechanical validation was delegated to measured Codex reasoning. Company output
bytes (successful attempts) were B01 1,444, B02 2,661, B03 1,353. Initial prompt
bytes were CONTROL/DOGFOOD: B01 2,033/3,175; B02 2,043/4,573; B03 2,187/3,136.
Thus delegation did not shorten the initial prompt in these particular fixtures;
bytes are context descriptors and were never converted into tokens.

Each DOGFOOD made exactly **one** successful bounded `evidence_reader.py` tool call:

| Fixture | Raw bytes available | Raw bytes actually returned to Codex | Inclusive lines | Byte range, end exclusive |
|---|---:|---:|---|---|
| B01 | 1,309 | 1,309 | 1–15 | [0, 1309) |
| B02 | 1,254 | 1,254 | 1–13 | [0, 1254) |
| B03 | 1,387 | 1,387 | 1–11 | [0, 1387) |

Read evidence comes from the actual completed `command_execution` items (exit 0),
not a model's self-report: metadata digest matched fixture bytes, and the complete
excerpt appeared in tool output. Exact commands and ranges are retained in
results.json. All relevant raw evidence was returned before final answers; no
DOGFOOD pair skipped required raw access. CONTROL's raw bytes were delivered in
its initial prompt; its tool-read count of zero does not mean no raw input.

Adoption/override is recorded conservatively as observable structural reuse:
B01 retained company group category/test/file structure but rewrote all top-level
fields; B02 retained exactly `error_signatures` and `strong_review_reasons`; B03
rewrote all top-level fields. This is not an independent semantic adoption verdict.
Final derived claims versus raw evidence, and whether rewrites corrected/introduced
errors, remain blind-review work. No reasoning transcript is used as evidence.

## Quality grading and remaining gate

Frozen-gold deterministic comparison:

- B01 CONTROL and DOGFOOD: all schema/membership/group/once-only coverage/unknown
  literal checks PASS.
- B02 CONTROL and DOGFOOD: all schema/membership/signature/event coverage/timeline,
  repeated-pattern and unknown literal checks PASS.
- B03 both: schema/A4 parser/source membership PASS. DOGFOOD passes all gold literal
  checks; CONTROL fails `required_facts_terms_literal`. The three unmatched term
  conjunctions are `queue_depth + 12`, `queue_depth + 4`, and `consumer_count + 2`.
  That observation is retained exactly. Whether paraphrase is semantically equivalent
  is **BLIND_REVIEW_REQUIRED**; it is not repaired by changing gold or rerunning.

Causal claims, command recommendations, unsupported fact promotion and statement/
source equivalence cannot be established by these literal checks. Their verdicts
are **BLIND_REVIEW_REQUIRED**, rather than guessed PASS. Quality parity is unknown.

Six randomly labeled outputs **B01-A/B, B02-A/B, B03-A/B** are available in
[the blind grading package](token_benchmark/20261006/blind_grading/README.md).
The private variant mapping stays only in the local runtime directory. To preserve
blinding, give a reviewer only that package plus frozen fixtures/gold/schema/task,
not this report/results/token metrics/company outputs, until their verdict is fixed.
If a reviewer has already read variant-identifying evidence, disclose compromised
blinding rather than claiming a blind result. No independent semantic review has
been executed in this round.

## Independent blind quality review result

Independent review is recorded in
[the pilot review](llmtool_token_savings_pilot_review_20261006.md).

The primary reviewer had already seen variant-identifying PR/token information and did
not grade the anonymous outputs directly. A separate subagent was restricted to the
blind grading package plus frozen fixture/gold/schema/task files and stated that it read
only the permitted files.

Results:

- B01-A/B: PASS/PASS; pair EQUIVALENT.
- B02-A/B: PASS/PASS; pair EQUIVALENT.
- B03-A/B: PASS/PASS; pair EQUIVALENT.
- B03's frozen literal `required_facts_terms_literal=false` remains recorded. Blind
  semantic review judged the natural-language statements semantically equivalent to
  the frozen gold; no gold/output/session was changed.

Quality-approved primary-KPI aggregate:

```text
control Codex total:  54,849
dogfood Codex total: 112,144
codex_tokens_saved:  -57,295
codex_savings_pct:   -104.46%

Equivalent interpretation:
dogfood used 104.46% more Codex tokens than control in this n=1 small-evidence pilot.
```

This is not a positive savings claim. It is a quality-approved negative descriptive
result.

### Interpretation findings carried forward

- **P1 — fixed/tool-request overhead dominates small evidence.** Fixtures are only
  about 1.2–1.4 KiB. Every DOGFOOD session performed one required raw-evidence tool
  read, causing another Responses request and resending substantial base context. All
  three DOGFOOD sessions read **100% of the raw fixture**, so there was no selective
  raw-read compression benefit in this pilot.
- **P2 — company thinking was not pinned off.** The benchmark used the default company
  provider behavior rather than an explicit `enable_thinking=false`. Independent
  diagnostic calls on the same synthetic B01 fixture showed materially lower company
  completion usage/latency with thinking disabled. Therefore company token/latency/
  retry observations from this pilot are not representative of the intended cheap
  bounded-triage lane. This does not change the measured Codex primary KPI.
- **P3 — cache/order effects are material.** Cached input differed substantially across
  runs. Any future secondary non-cached/cost-relevant metric must be pre-registered and
  evaluated with counterbalanced repeated pairs; it cannot be retroactively substituted
  for the primary KPI here.
- **P4 — B02 literal gold check is weak.** A future round should use structured
  discrimination rather than ambiguous substring matching.

These findings motivate a separate large-evidence follow-up; they do not rewrite this
pilot's result.

## Scope, validation and interpretation limits

- Raw JSONL, prompt files, stderr logs and private A/B mapping remain only in the
  ordinary temporary measurement directory
  `/var/folders/y_/xqy1pg553h19l7z9zjmshvb80000gn/T/llmtool-token-pilot-20261006-pw8zyn1s`.
  No repository copy/worktree was created there. Only safe extracted metrics/digests
  and synthetic anonymous final outputs are committed.
- Production/config/tests/CI and historical review reports are unchanged. Existing
  unrelated benchmark/reasoning artifacts and `case.*` directories are preserved.
- Documentation/helper validation checks immutable digests, JSON/schema/parser
  acceptance, grade reproducibility, actual usage/turn/read evidence and whitespace.
  Validation result: **PASS** for the evidence/document checks above and
  `git diff --check`. The separate frozen quality literal miss is not hidden by
  that evidence-validation PASS. No application suite was required or run for
  this reports-only pilot.
- Preparation, orchestration, report-writing and future independent-review premium
  tokens are not exposed by these measured exec usage events and are **unknown**.
  Company calls were launched by the coordinator as separate llmtool invocations;
  their coordinator tool/token overhead is outside the six measured sessions.
  This is a bounded analysis-session comparison, not complete end-to-end ROI.
- Small 1.2–1.4 KiB fixtures and substantial Codex/tool context overhead limit
  generalization. The cache differences and fixed run order are reported, not
  corrected post hoc. Tool-caused requests remain actual counted consumption.
- n = 1 per fixture: **PRELIMINARY / DESCRIPTIVE PILOT** only. No material/statistical
  savings, cost/compute/energy claim, routing threshold, company-first production
  policy, budget enforcement, A11b.2 or A11c work is authorized or inferred.
- Final publication SHA/PR/exact-head checks are recorded once in the PR/final reply;
  runtime evidence remains bound to the immutable pre-registration SHA above.
- No merge and no second repetition. Stop for independent blind review.

```text
B01 Codex saving: -19065 tokens / -103.32% / quality-approved negative result
B02 Codex saving: -21712 tokens / -123.49% / quality-approved negative result
B03 Codex saving: -16518 tokens / -87.79% / quality-approved negative result

Overall comparable control Codex tokens: 54849
Overall comparable dogfood Codex tokens: 112144
Overall Codex tokens saved: -57295
Overall Codex saving %: -104.46%

Company LLM tokens: unknown total; 13797 known successful-call subtotal; 2 failed attempts unknown
Mixed token-volume delta: unknown

Valid pairs: 3/3 accounting-comparable and quality-approved
Invalid pairs: 0 after blind semantic resolution of B03 literal miss
Quality parity: 3/3 EQUIVALENT; 6/6 anonymous outputs PASS
Blind review pending: none for this pilot

Can claim positive preliminary Codex token savings: NO
Observed quality-approved result: DOGFOOD INCREASED Codex usage by 104.46% (n=1, descriptive)
```

## Historical preflight and accounting review checkpoint

The following sections preserve the earlier report and its cutoff state. Their
NOT RUN/zero-call statements describe that earlier checkpoint, not the executed
pilot above. The accepted mapping remains unchanged.


Status: **ACCOUNTING PREFLIGHT UNBLOCKED; B01–B03 PILOT NOT YET EXECUTED**.

This is a preflight blocker report. The planned pilot remains **NOT EXECUTED**;
there are no measured CONTROL/DOGFOOD pairs and no token-savings or quality claim.
Any eventual results would be **PRELIMINARY / DESCRIPTIVE PILOT**, with n = 1 pair
per fixture, rather than evidence of statistically proven savings.

## Repository and authoritative rules

- Canonical Mac repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- GitHub repository: `davidcatcat/davidcattoolsproject`.
- Fetched `origin/main` and preflight repository SHA:
  `f4d1019d984105431485ad364861ef07cc4dd887` (PR #35 merge).
- Benchmark branch: `bench/llmtool-token-savings-pilot-20261006`, created from that SHA.
- Read: `AGENTS.md`, `tools/llmtool/WORKFLOW.md`, `tools/llmtool/ROADMAP.md`,
  `skills/company-llm-delegation/SKILL.md`, and the benchmark plan/review/re-review.
- Rules: deterministic first → company LLM bounded triage → strong Codex judgement;
  raw evidence remains ground truth.

References:

- [Benchmark plan](llmtool_token_savings_benchmark_plan_20261006.md)
- [PR #35 review](llmtool_token_savings_benchmark_review_20261006.md)
- [PR #35 re-review](llmtool_token_savings_benchmark_rereview_20261006.md)

## Accounting preflight actually executed

`codex --version` reported **codex-cli 0.159.2**. `codex exec --help` was inspected.
One fresh accounting-only invocation ran from the canonical repository, with no
resume and no benchmark fixture. It is not one of the six planned measured sessions.

Explicit requested settings:

| Setting | Value |
|---|---|
| Codex model | `gpt-6.1-sol` |
| Reasoning effort | `high` |
| Speed/service config | `service_tier="default"` |
| Sandbox | `read-only` |
| Approval policy | `never` |
| Session/config isolation | `--ephemeral --ignore-user-config --ignore-rules` |
| Measurement source | `codex exec --json / turn.completed` |

The selected local model/effort configuration also specified `gpt-6.1-sol` / `high`.
These are configuration/request evidence, not attestation of the coordinator's
actual runtime model. The JSONL events did not report model identity or delivered
service tier; neither is inferred from configuration.

Exact preflight command:

```sh
codex exec --json --ephemeral --ignore-user-config --ignore-rules \
  --model gpt-6.1-sol \
  --config 'model_reasoning_effort="high"' \
  --config 'service_tier="default"' \
  --config 'approval_policy="never"' \
  --sandbox read-only \
  --cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject \
  'Reply with exactly TOKEN_USAGE_PREFLIGHT_OK. Do not use tools, read files, inspect configuration, change files, or call any other provider. This is only a token-accounting preflight, not a benchmark task.'
```

- Exit status: **0**.
- Wrapper-measured wall clock: **9.037 seconds**.
- Event types observed: `thread.started`, `turn.started`, `item.completed`,
  `turn.completed`.
- Actual `turn.completed.usage`, retaining original field names:

```json
{
  "input_tokens": 17473,
  "cached_input_tokens": 12288,
  "cache_write_input_tokens": 0,
  "output_tokens": 9,
  "reasoning_output_tokens": 0
}
```

Per-session input/output usage exists. **`total_tokens` is absent**; the field
named `reasoning_tokens` is also absent. The reported `reasoning_output_tokens`
and `cache_write_input_tokens` fields are retained as reported, not renamed.
Missing fields are **unknown**, not zero.

The original preflight correctly stopped because no accepted mapping had yet been established. A subsequent coordinator review inspected the exact upstream Codex CLI 0.159.2 source and OpenAI usage semantics and accepted a bounded mapping for fresh single-turn ephemeral sessions:

```text
codex_comparable_total = input_tokens + output_tokens
```

Cached input, cache-write input and reasoning output remain breakdown fields and must not be added again. For the observed preflight this yields `17473 + 9 = 17482`; this preflight is still excluded from B01–B03 totals. See [the accounting mapping decision](llmtool_codex_01592_usage_mapping_20261006.md).

Quota, credits, allowance, context bytes, and pricing remain prohibited as token substitutes. The mapping is version/session-shape scoped and must be revalidated if the Codex CLI version or usage schema changes.

Raw JSONL SHA-256:
`e61d3bed0e2308f56ed69f328a95b61dfa75470b3a04d09a15e88c686f04b7a4`.
Raw JSONL and stderr remain temporary runtime artifacts outside the repository;
neither full trace nor credentials are included in this report or publication.

## Execution rules added after independent accounting review

The independent Codex 0.159.2 accounting review returned PASS and added four
non-blocking execution notes which are mandatory for the pilot:

- **R1 — failed/incomplete Codex run:** if a measured run does not emit a usable
  `turn.completed.usage`, record the Codex usage as `unknown` and invalidate that
  control/dogfood pair for any savings claim. Never substitute zero or a partial count.
- **R2 — cache-write observation:** record `cache_write_input_tokens` exactly as
  reported. It remains a breakdown field and is not added to the comparable total.
  If it is non-zero, call it out in the report and verify the usage schema/version did
  not change before continuing.
- **R3 — version/schema/turn evidence:** every measured session must record
  `codex --version`, the exact set of usage-field names observed, and the number of
  `turn.completed` events. Any CLI version/schema drift or anything other than the
  expected fresh single-turn shape stops the benchmark pending revalidation.
- **R4 — multiple model requests inside one turn:** the fresh thread-level usage may
  include multiple Responses requests caused by tool use. Those tokens are real task
  consumption and remain part of the measured Codex total. Do not reinterpret them as
  context size or subtract them away.

Accepted comparable total remains:

```text
codex_comparable_total = input_tokens + output_tokens
```

Cached input, cache-write input and reasoning output remain diagnostic breakdowns only.

## Planned execution state

The requested fixed order is retained below. No measured session has started, so
this report is not evidence of completed fixture/gold pre-registration.

| Fixture | Planned order | Execution |
|---|---|---|
| B01 test-triage | CONTROL → DOGFOOD | NOT RUN |
| B02 log-triage | DOGFOOD → CONTROL | NOT RUN |
| B03 context-pack | CONTROL → DOGFOOD | NOT RUN |

- Fixtures, gold, output schemas and fixture/gold digests: **NOT CREATED**.
- Configured company profile: **NOT TESTED**; no profile was created or changed.
- VPN readiness: **NOT TESTED** in this preflight.
- Company calls, including failures/manual retries: **0**.
- Company token usage and latency: **unknown / NOT RUN**.
- Benchmark Codex tool-call counts and latency: **NOT RUN**.
- Raw fixture bytes available/read and read ranges: **N/A**, no fixtures/sessions.
- Schema validity, identifier invalid count and coverage: **NOT TESTED**.
- Company claims adopted/overridden: **N/A**.
- Gold grading and anonymous grading outputs: **NOT CREATED**.
- Blind semantic review: **NOT STARTED**, no outputs available to review.

## Results

| Fixture | Control Codex total | Dogfood Codex total | Company total | Codex saved | Codex saving % | Mixed token-volume delta | Quality |
|---|---:|---:|---:|---:|---:|---:|---|
| B01 | unknown | unknown | unknown | unknown | unknown | unknown | NOT TESTED |
| B02 | unknown | unknown | unknown | unknown | unknown | unknown | NOT TESTED |
| B03 | unknown | unknown | unknown | unknown | unknown | unknown | NOT TESTED |

All rows are **NOT RUN**. The accounting preflight is excluded from benchmark
totals. Dependent results remain unknown; zero company calls is not a reported
company token total.

```text
B01 Codex saving: unknown — NOT RUN
B02 Codex saving: unknown — NOT RUN
B03 Codex saving: unknown — NOT RUN

Overall comparable Codex control tokens: unknown
Overall comparable Codex dogfood tokens: unknown
Overall Codex tokens saved: unknown
Overall Codex saving %: unknown

Company tokens consumed: unknown — NOT RUN, 0 calls
Mixed token-volume delta: unknown

Quality: NOT TESTED
Invalid pairs: N/A — 0 pairs executed
Blind review pending: NOT STARTED — no grading outputs

Can claim token savings: NO
```

Mixed token-volume delta, if eventually available, combines different tokenizers
and is descriptive only. It cannot establish cost, compute, or energy savings.
No production routing threshold or company-first policy follows from this report.

## Publication scope and remaining gate

Only this report is added. Production llmtool behavior, user configuration, and
historical reports are unchanged. Existing benchmark/reasoning artifacts and the
three pre-existing `tools/llmtool/case.*` directories are preserved. No clone,
worktree, retry/fallback implementation, automatic router, A11b.2 or A11c work
was performed. There is no merge-readiness assertion.

The comparable-total accounting blocker is now closed by the accepted Codex CLI 0.159.2 mapping documented above. Before resuming the pilot, pin the same explicit settings for all six sessions, prepare gold and digests before execution, validate company output mechanically, require raw evidence verification for adoption, and leave semantic quality to blind review. The mapping does not relax any other benchmark requirement.

Documentation validation checks the extracted usage/digest against the local
preflight evidence, repository file references, and whitespace. Application tests
and new benchmark/provider requests are not part of this report-only upload.
The publication commit/PR and exact-head check status are recorded in the PR/final
response rather than creating a recursive commit to embed this report's own SHA.

**ACCOUNTING PREFLIGHT UNBLOCKED — Pilot NOT RUN — Can claim token savings: NO.**

## Current verdict after the historical checkpoint

The first six measured sessions are now executed. B01/B02 show negative provisional
Codex deltas; B03 is excluded pending the frozen literal-check/semantic review.
All company attempts are retained, including two unknown-usage failures.
**BLIND_REVIEW_REQUIRED — Can claim preliminary Codex token savings: NO.**
No second repetition, production change, A11b.2/A11c work or merge was performed.
