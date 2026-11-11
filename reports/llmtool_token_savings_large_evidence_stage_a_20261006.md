# llmtool Round-2 Stage-A measured benchmark — 2026-10-06

**STAGE A EXECUTED / INDEPENDENT BLIND QUALITY REVIEW COMPLETE.** Six Codex sessions and three company requests completed. L01 is the only quality-approved valid pair: CONTROL 34,235 vs DOGFOOD 20,604, a descriptive n=1 reduction of 13,631 Codex tokens (39.816%). L02 and L03 remain **INVALID_FOR_SAVINGS_CLAIM**. This does not establish a generalized Round-2 or production-routing savings claim.

## Execution contract and provenance

- Repository: `davidcatcat/davidcattoolsproject`; canonical actual Mac root `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-large-evidence-20261006`; existing Draft PR #37.
- Exact runtime/fixture revision tested: `6858ad60780b280cbc2c3f619b8aee7380145b73`. Fetched and fast-forwarded from `7ba13e4af19b1db8b841168de06753fb0d44368b`; the fetched delta was the Stage-A handoff. Expected remote SHA matched.
- macOS 15.7.9 (24G830); offline/measurement extraction Python 3.9.6; canonical llmtool venv Python 3.13.3. Company VPN route to `192.0.2.10` used `utun4`; no route/VPN changes.
- Direct contract: [Stage-A handoff](llmtool_token_savings_large_evidence_stage_a_handoff_20261006.md) and [frozen preregistration](token_benchmark/20261006_round2/stage_a/preregistration.json). Independent [preregistration review](llmtool_token_savings_large_evidence_preregistration_review_20261006.md) preceded this execution; it is not a review of these results.
- Frozen preregistration SHA-256: `6ee3ddc5a9b015510bc4d5051a050bcd714d4771af6512c429b50a8de73d9683`. All 16 registered artifact hashes matched before live calls and again after execution. No frozen artifact was modified.
- [Execution helper](token_benchmark/20261006_round2/stage_a/execution_helper.py) SHA-256: `30e279993e1fba6f60447b64d0d5a4e156a9d050dbe1ea879a61d9ba1f08f483`; unchanged across all six measured sessions. It reads gold bytes only to check registered digests, never their content for prompt/coverage/selection decisions.
- [Post-pair deterministic grader](token_benchmark/20261006_round2/stage_a/grading_helper.py) SHA-256: `6b8b72f8297d03676c00831077a35307c6028dff53516790d864ae4c94a97684`. Gold content is grading-only, after measured pair completion and frozen selection. Executing Codex sessions did not self-grade.
- Raw prompts, full Codex JSONL, company stdout/stderr, narrow request audits and private label mapping remain in a temporary local measurement directory outside Git. Only extracted metrics, digests and unchanged anonymous final answers are published.
- Publication SHA is the commit containing this report, verified against final remote PR head at delivery; no report-only commit is created just to embed its own SHA.

## Frozen fixtures

| Fixture | Bytes | Seed | Fixture SHA-256 | Gold SHA-256 |
|---|---:|---:|---|---|
| L01 | 49,152 | 2026100601 | `4daf6bb797a23ececd1baedaafc71e939c6c11527901fc8fbc37ea742485d956` | `63994d36ff33d42636f286b5fa134d09472f35e9fff48d20cce1790b1ba12c67` |
| L02 | 65,536 | 2026100602 | `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da` | `38d38a7f5246f1ee7a39f6d4ab6bb53ef076c36956e1fc36799325d01a47a1b4` |
| L03 | 131,072 | 2026100603 | `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce` | `0e54eb372345ba64105512744c09a7cb60027fbebeebd04fd7834f51bbf2e8d8` |

Schema/task/generator/coverage/selector/self-check hashes are also pinned in `results.json` and the unchanged preregistration manifest. No post-result amendments, gold-driven repairs, widened ranges, output edits, nonce or cache manipulation.

## Measured configuration and accounting

Every Codex invocation checked `codex --version` = **codex-cli 0.159.2** and used a fresh ephemeral single-turn session:

```sh
codex exec --json --ephemeral --ignore-user-config --ignore-rules \
  --model gpt-6.1-sol --config 'model_reasoning_effort="high"' \
  --config 'service_tier="default"' --config 'approval_policy="never"' \
  --sandbox read-only --cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject \
  --output-schema <frozen per-fixture output_schema.json> -
```

Model/effort/tier are explicit requested settings, not backend identity attestation; JSONL did not expose a backend model attestation. No resume or shared conversation. Each pair used the exact same frozen final schema.

Usage source: **`codex exec --json / turn.completed.usage`**. All six sessions emitted the same five fields: `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`. Exactly one thread.started, one turn.started and one turn.completed per session; six distinct thread IDs. No missing usage/schema drift.

Primary = **input + output**. Cached/cache-write/reasoning fields are not added again. Secondary = **(input − cached) + output**, descriptive only. The observed cached and cache-write fields were zero in all six sessions; this observation does not establish a guaranteed cold cache.

| Order | Session | Input | Cached | Cache write | Output | Reasoning output | Primary = secondary | Wall s | Exit / tool calls |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | L01 CONTROL | 33,545 | 0 | 0 | 690 | 114 | 34,235 | 20.121 | 0 / 0 |
| 2 | L01 DOGFOOD | 19,974 | 0 | 0 | 630 | 59 | 20,604 | 18.717 | 0 / 0 |
| 3 | L02 DOGFOOD | 21,048 | 0 | 0 | 643 | 100 | 21,691 | 21.673 | 0 / 0 |
| 4 | L02 CONTROL | 39,702 | 0 | 0 | 551 | 37 | 40,253 | 18.184 | 0 / 0 |
| 5 | L03 CONTROL | 58,124 | 0 | 0 | 1,028 | 407 | 59,152 | 26.974 | 0 / 0 |
| 6 | L03 DOGFOOD | 18,855 | 0 | 0 | 1,326 | 1,034 | 20,181 | 35.164 | 0 / 0 |

Full thread IDs, source field sets, shape counts, exact argv, timing epochs and cache background are preserved in [results.json](token_benchmark/20261006_round2/stage_a/results.json). Elapsed cache intervals are measured since the preceding measured Codex session **ended**; first prior fields are null.

| Session | Prior measured session | Elapsed since prior end (s) | Prompt bytes | Prompt SHA-256 | JSONL SHA-256 |
|---|---|---:|---:|---|---|
| L01 CONTROL | null | null | 52,333 | `99fb388b2960c1228c70d214c4c50f72d41c4616c31d1e31f2fb781e30d6b6f4` | `83dcf7a37b2f21969ae882878cc928d377233c93e02dcda975ea5605d7fcc410` |
| L01 DOGFOOD | L01 CONTROL | 59.393 | 9,709 | `3306a024146f04597a0ccf6246f8d5f264e0177344d99ba20a8528c6277e92cd` | `c148e661d6815ccd13bf7021cb3800e3a911330c0529415944131cb3b6f63983` |
| L02 DOGFOOD | L01 DOGFOOD | 50.838 | 16,383 | `c13e102e467f3cd318ad8baf6ef7e436b9f2d641d195c7c23145901a6afa9e82` | `654ed7b1068a89ed186d979b80b4b82f156a30a768fb489c70c40b17f56a2acb` |
| L02 CONTROL | L02 DOGFOOD | 7.505 | 68,361 | `350d586ef1055c64963a62eb537cbc560c2934d717a5e9cfc87d3f4483787e29` | `29a04159656ce60f8a450c1a7ddfacebfde0fb425b9d6770bd864f6405b77cc7` |
| L03 CONTROL | L02 CONTROL | 10.546 | 133,623 | `bdd091244416f138eb206bfb2f7a29b240b7a5f74f50494332d95f4d1fbdc2a5` | `574672b5bd483cb3f3175e48bf643a8f70c094e22cf595dbaea5a769ed4a66e8` |
| L03 DOGFOOD | L03 CONTROL | 164.78 | 5,001 | `760b542a5d05389d22a711978c2d94de9b01d406635136e84544d30c8942fa79` | `c4627b4f82bf99393090b3479eb0dc8f6b23184c1a363f4d440a095d5b559369` |

CONTROL initial prompts contained only frozen task/schema/full fixture. DOGFOOD initial prompts contained company result, gold-independent coverage, frozen selected-range metadata, selected original bytes and the same task/schema. Offline reconstruction matched every actual prompt byte and digest. Both variants made **0 tool calls**; no unplanned fixture reads. No other variant output was passed.

## Company requests and actual payload proof

All three requests used llmtool through its official `llmtool.entrypoint.main` under the canonical installed venv, with a local narrow observer around the existing OpenAI-compatible client. The observer checked the actual outgoing Request then passed it unchanged to the original opener; it did not replace transport, read raw response bodies, call curl or modify payloads.

Temporary HOME profile `company-round2`: openai-compatible; `http://192.0.2.10:4000`; requested `gw`; timeout 120; null api_key/api_key_env; temperature 0; max_tokens 4096; `provider_options.enable_thinking=false`. Each actual Request proved **`chat_template_kwargs.enable_thinking=false`**, model=gw, temperature=0, max_tokens=4096 and **no Authorization**. Resolved profile and full user-message digest matched.

Temporary config SHA-256: `17a21fe7fe23b8aa230732b4b272d725693ec5d3655fd58f2103aeea7afb1b1f`. Observer SHA-256: `f1a3a8fd063f90ded8b5218098a8f63ebbc25747b9f750660443673bcea33d20`. Actual request body/input digests and all request observations are in results.json. Real user config path was absent before and after; no config mutation or credential inspection. Only preregistered synthetic data was delegated.

| Fixture / llmtool path | Attempts / retries | Exit / requests | Prompt | Completion | Total | Provider s | Wrapper s | Returned model | Schema/parser |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| L01 `ask --skill test-triage` | 1 / 0 | 0 / 1 | 19,179 | 496 | 19,675 | 3.232 | 3.838 | qwen3.6-35b-a3b | PASS |
| L02 `ask --skill log-triage` | 1 / 0 | 0 / 1 | 29,450 | 2,189 | 31,639 | 11.07 | 11.544 | qwen3.6-35b-a3b | PASS |
| L03 `context-pack --json` | 1 / 0 | 0 / 1 | 53,703 | 372 | 54,075 | 4.72 | 5.313 | qwen3.6-35b-a3b | PASS |

Company input was exactly the frozen company instruction followed by the complete fixture. No final Codex schema, gold, answer/example or later repair was supplied. No transport failures, retries, fallback or wrapper timeouts occurred. All three completed responses followed the existing provider normal-stop validation; no length failure was reported. Company coverage failures below are distinct from transport/parser success. Company token source is reported llmtool verbose provider metadata, not estimated from bytes.

## Pair results — provisional arithmetic only

| Fixture | CONTROL Codex | DOGFOOD Codex | Company | Provisional difference | Provisional % | Descriptive mixed delta | Deterministic CONTROL / DOGFOOD |
|---|---:|---:|---:|---:|---:|---:|---|
| L01 | 34,235 | 20,604 | 19,675 | +13,631 | 39.816% | -6,044 | PASS / PASS |
| L02 | 40,253 | 21,691 | 31,639 | +18,562 | 46.113% | -13,077 | PASS / PASS |
| L03 | 59,152 | 20,181 | 54,075 | +38,971 | 65.883% | -15,104 | PASS / FAIL |

These are observed subtraction results, **not quality-approved savings**. L02/L03 differences are displayed for transparency only and excluded from any selective-injection savings claim. Mixed delta = CONTROL − DOGFOOD − company, combines different tokenizers, and is not a cost/compute/energy saving. Secondary totals equal primary here because reported cached input is zero.

| Fixture | Full bytes | Selected original bytes / % | Ranges / pre-merge | Raw cap | Coverage | Selector | Delivery | Validity |
|---|---:|---|---|---:|---|---|---|---|
| L01 | 49,152 | 1,768 / 3.597% | 4 / 4 | 9,830 | PASS | PASS | PASS; 0 tools | MECHANICAL_GATES_PASS; BLIND_QUALITY_PENDING |
| L02 | 65,536 | 1,892 / 2.887% | 4 / 4 | 13,107 | FAIL | PASS | PASS; 0 tools | INVALID_FOR_SAVINGS_CLAIM |
| L03 | 131,072 | 0 / 0.000% | 0 / 0 | 26,214 | FAIL | PASS | PASS; 0 tools | INVALID_FOR_SAVINGS_CLAIM |

**L01:** company exact failure/file membership and all four failure IDs passed coverage. Four frozen five-line ranges supplied 1,768 original bytes. Both final answers match the exact three groups, four IDs, files/kinds and integer counts. Semantic unknowns, evidence fidelity and unsupported root-cause claims still require blind review.

**L02:** all four canonical events were quoted, and parsed canonical signature counts were 3 timeouts + 1 recovery. However the fixed company output contract failed: six noncanonical evidence entries and eighteen noncanonical timeline entries, with extra error signatures causing `signature_count_match=false`. This is not a claim that canonical events were omitted. Frozen coverage returned `COMPANY_TRIAGE_COVERAGE_MISS`; no output was repaired. Only four mechanically validated event anchors were selected (1,892 bytes). Both final Codex answers passed structured event/count/order grading, but that does not erase the company coverage-contract violation. **INVALID_FOR_SAVINGS_CLAIM**.

**L03:** A4 parser passed, but three company evidence statements omitted the required exact `record_id=` token. No citation anchor could be validated. Frozen selector accepted an empty anchor set and emitted **0 raw bytes / 0 ranges**, within cap; this is a coverage miss, **not** a cap failure. The subsequent fixed DOGFOOD session received the empty selection, was not repaired/rerun, and retained its actual usage. It failed gold grading: all seven required citations missing, facts empty and evidence empty. CONTROL passed. Flags: `COMPANY_TRIAGE_COVERAGE_MISS` and `QUALITY_PARITY_FAIL` (structured failure already sufficient; semantics remain pending). **INVALID_FOR_SAVINGS_CLAIM**.

Company adoption/override is only structurally observable here: L01 retained the company identifiers/groups; L02 final structured output omitted noncanonical extras and retained canonical events; L03 final facts/evidence were empty and company statements unverified. Whether derived claims are semantically acceptable is not coordinator self-grading. No silent adoption of unvalidated IDs, gold-driven range repair, widened cap, discarded failed attempt or rerun-to-improve-answer.

## Fixed budget and order

Order stayed L01 CONTROL→DOGFOOD, L02 DOGFOOD→CONTROL, L03 CONTROL→DOGFOOD. Budget updates occurred only after a complete pair, including invalid pairs.

| Pair | Remaining before | Fixed start allowance | Start gate | Actual pair usage | Remaining after |
|---|---:|---:|---|---:|---:|
| L01 | 400,000 | 97,593 | PASS | 54,839 | 345,161 |
| L02 | 345,161 | 113,976 | PASS | 61,944 | 283,217 |
| L03 | 283,217 | 179,514 | PASS | 79,333 | 203,884 |

Measured Codex Stage-A total: **196,116**. Remaining unchanged 400k ceiling: **203,884**. All three pairs executed; none budget-blocked or not started. Company consumed **105,389** reported tokens across three requests, zero retries. These totals include invalid pairs and are consumption totals, not a valid-pair savings aggregate.

## Deterministic checks and blind review gate

Actual Python 3.9.6 command before and after execution:

```sh
PYTHONPATH=tools/llmtool /usr/bin/python3 -B \
  reports/token_benchmark/20261006_round2/stage_a/self_check.py
```

**20 tests PASS, exit 0** on each run, including byte-identical regeneration, frozen hashes/sizes/counts, schemas and positive/negative coverage/selector boundaries. Post-pair deterministic grading was an offline helper, not executing-model self-assessment. Its five in-memory negative controls (wrong failure count, wrong group count, fabricated ID, reversed events, omitted citations) all rejected; measured outputs were unchanged.

Raw JSONL usage/digests, fixed command settings/order, six distinct thread IDs, actual prompt bytes, company request observations, company inputs, frozen coverage, selector manifest/raw bytes and post-selection-before-grading chronology were reconciled offline. Full selection byte offsets and digests are retained in results.json. All six final schemas passed; only L03 DOGFOOD failed structured gold requirements. L01/L02 selected all canonical target lines; L03 selected none.

[Anonymous grading inputs](token_benchmark/20261006_round2/stage_a/blind_grading/README.md) contain six unchanged final JSON answers with privately randomized A/B labels. Mapping is local-only, mode 0600, outside Git; no public letter-to-variant table or per-variant final-answer digest is published. No grader was launched.

**Blinding limitation:** L02 DOGFOOD mentions the supplied company result; L03 DOGFOOD mentions company-reported values. Those original answer words can reveal provenance even with random filenames. They have not been edited to conceal identity. Therefore the anonymous package does **not** establish full blinding for these two already-invalid pairs. Independent reviewers must address this constraint before accepting a blind-review conclusion. L01 has no such identity terms in its answer.

Two independent semantic graders must receive only anonymous output + fixture/gold/schema/task, excluding metrics/company outputs/mapping/PR reports. Preserve every deterministic miss. Disagreement blocks quality approval until an independently documented tie-break. Independent blind grading is complete. Two graders agreed 6/6 on individual outputs and 3/3 on pair parity; no tie-break was needed. L01 is EQUIVALENT and fully blind. L02 is EQUIVALENT but remains mechanically invalid. L03 is A BETTER with the DOGFOOD-side answer failing quality, consistent with its deterministic failure. Provenance wording weakens blinding for L02/L03, but both are already invalid for a savings claim.

## Scope, publication and limitations

- No production, installed skill, tests, user config, frozen preregistration, Git history, routing or permission behavior changed. The task additions are measurement/grading helpers, extracted results, report and anonymous answer files.
- All nine pre-existing untracked paths preserved: the two 20260928 benchmark reports/directories, reasoning probe report/directory and three `tools/llmtool/case.*` artifacts. No cleanup/reset/stash/clone/worktree.
- Application canonical suite/new security/QA gate: **NOT TESTED this turn**, because runtime behavior was unchanged and the task is benchmark execution/documentation. Python 3.14 local suite not run. Exact live benchmark and offline Python 3.9 artifact checks above are this turn's evidence, not historical suites.
- llmtool CI is filtered to `tools/llmtool/**` and its workflow. This results-only change cannot claim a new application CI PASS; final-head GitHub check availability is verified at publication and reported separately. No old CI is substituted.
- Preflight consumption is separate background: 394,231 Codex + 294,036 company tokens. With Stage A, known measurement subtotal is 590,347 Codex + 399,425 company; coordinator/preparation/review usage is unknown. This is not complete workflow ROI.
- n=1 pair per fixture, fixed alternating order, synthetic canonical identifiers, differing prompt sizes, potential cache/order effects and company schema-following limits prevent statistical/general production conclusions. Byte reduction alone is not token or quality savings.
- Stage B **NOT STARTED**. Two blind graders/tie-break **NOT TESTED**. A11b.2/A11c/router/production budget enforcement **NOT STARTED**. Draft PR remains unmerged.

## Required execution summary

```text
Stage A execution status: EXECUTED; independent blind quality review complete

L01
CONTROL Codex: 34,235
DOGFOOD Codex: 20,604
Provisional Codex saving: +13,631 / 39.816% (arithmetic only)
Company tokens: 19,675
Selected raw bytes/%: 1,768 / 3.597%
Deterministic grade: CONTROL PASS / DOGFOOD PASS
Blind review: 2/2 graders PASS/PASS; pair EQUIVALENT
Validity: QUALITY_APPROVED_VALID_PAIR

L02
CONTROL Codex: 40,253
DOGFOOD Codex: 21,691
Provisional Codex saving: +18,562 / 46.113% (arithmetic only)
Company tokens: 31,639
Selected raw bytes/%: 1,892 / 2.887%
Deterministic grade: CONTROL PASS / DOGFOOD PASS
Blind review: 2/2 graders PASS/PASS; pair EQUIVALENT
Validity: INVALID_FOR_SAVINGS_CLAIM

L03
CONTROL Codex: 59,152
DOGFOOD Codex: 20,181
Provisional Codex saving: +38,971 / 65.883% (arithmetic only)
Company tokens: 54,075
Selected raw bytes/%: 0 / 0.000%
Deterministic grade: CONTROL PASS / DOGFOOD FAIL
Blind review: 2/2 graders: A PASS / B FAIL; pair A BETTER
Validity: INVALID_FOR_SAVINGS_CLAIM

Measured Codex Stage-A total: 196,116
Remaining 400k budget: 203,884

Pairs executed: 3 (L01/L02/L03)
Pairs not started: 0
Pairs invalid: 2 (L02/L03)

Quality-approved aggregate:
L01 only — CONTROL 34,235 vs DOGFOOD 20,604; +13,631 Codex tokens saved (+39.816%), n=1 descriptive

Can claim generalized Round-2 Codex savings:
NO — only one valid quality-approved synthetic pair; L02/L03 are invalid
```

Stop after normal publication to existing Draft PR #37. No Stage B, semantic reruns, artifact amendments, quality-approved claim or merge.


## Independent blind quality review result

Review source: [Stage-A review](llmtool_token_savings_large_evidence_stage_a_review_20261006.md).

Two independent graders agreed on all six anonymous output verdicts and all three pair
parity judgments; no tie-break was required:

| Fixture | Grader 1 | Grader 2 | Pair parity | Savings validity |
|---|---|---|---|---|
| L01 | A PASS / B PASS | A PASS / B PASS | EQUIVALENT | **QUALITY_APPROVED_VALID_PAIR** |
| L02 | A PASS / B PASS | A PASS / B PASS | EQUIVALENT | INVALID: COMPANY_TRIAGE_COVERAGE_MISS |
| L03 | A PASS / B FAIL | A PASS / B FAIL | A BETTER | INVALID: coverage miss + quality failure |

Only L01 enters the quality-approved aggregate:

- CONTROL: **34,235**
- DOGFOOD: **20,604**
- Codex tokens saved: **13,631**
- Primary saving: **39.816%**
- sample size: **n=1**
- interpretation: descriptive evidence for this frozen synthetic canonical 48 KiB
  test-output fixture only.

L02's semantic output quality was equivalent, but the preregistered company coverage
contract failed, so its 46.113% arithmetic difference remains invalid for a savings
claim. L03's 65.883% arithmetic difference is invalid because company citation coverage
failed, zero raw evidence was selected, and DOGFOOD quality failed.

Blinding caveat: L02/L03 answers contain provenance wording that can weaken anonymity;
those pairs were already invalid. L01 contained no provenance wording and retained the
intended blind comparison. Grader #2 disclosed one read-only shell inspection of the
permitted L03 fixture only; no forbidden files were read.

The preregistered Stage-B trigger is met for **L01 only**. Stage B remains NOT STARTED
and requires separate explicit authorization. Its result must remain separate from
Stage A and cannot be pooled as an unbiased estimate.
