# llmtool Round-2 Stage B — L01 reverse-order confirmation (2026-10-06)

**MEASURED PAIR COMPLETE / QUALITY-APPROVED REVERSE-ORDER CONFIRMATION.** L01 DOGFOOD then CONTROL ran once each. Mechanical gates and deterministic grading passed, and two independent blind graders both judged the pair EQUIVALENT. Stage B observed +12,342 Codex tokens saved / 37.547%, separately from Stage A's +39.816%. No Stage-A/Stage-B average, pooled estimate or stable/generalized savings claim.

## Task contract and provenance

- Repository: `davidcatcat/davidcattoolsproject`; actual Mac canonical root `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-large-evidence-20261006`; existing Draft PR #37.
- Runtime tested HEAD: **`bfa314c3d3b10587fac7e1adaf423c48ad61f44b`**. Fetched and safely fast-forwarded from `3c1cd009e33663836bf8846fa496397353f57e2d`; the inspected delta added/updated only three reports/benchmark metadata files. No runtime/skills/workflow changes since Stage-A execution SHA `6858ad60780b280cbc2c3f619b8aee7380145b73`.
- Direct contract: [Stage-B handoff](llmtool_token_savings_large_evidence_stage_b_handoff_20261006.md), with its older expected SHA superseded by the explicit user-pinned current head. Handoff SHA-256: `c56a8f8d3fd12bade2004c913899e1d38e255400211a9c6cca049dd223f1cb99`.
- [Stage-A independent review](llmtool_token_savings_large_evidence_stage_a_review_20261006.md) reports two independent graders agreeing PASS/PASS and EQUIVALENT for L01 only. This historical evidence establishes the frozen Stage-B trigger; it does **not** grade this new pair.
- Read [Stage-A report](llmtool_token_savings_large_evidence_stage_a_20261006.md), [Stage-A metrics](token_benchmark/20261006_round2/stage_a/results.json), [preregistration report](llmtool_token_savings_large_evidence_preregistration_20261006.md), [preregistration review](llmtool_token_savings_large_evidence_preregistration_review_20261006.md) and [immutable manifest](token_benchmark/20261006_round2/stage_a/preregistration.json). Historical evidence remains unchanged.
- All 16 frozen artifact digests verified before live calls and after execution. Preregistration SHA-256: `6ee3ddc5a9b015510bc4d5051a050bcd714d4771af6512c429b50a8de73d9683`.
- Only L01 measured, only in DOGFOOD→CONTROL order. No Stage-A rerun, L02/L03 measured call, blind grader, Round 3, production change, A11b.2/A11c or merge.
- Published final HEAD is verified through the containing commit and remote PR at delivery; no report-only self-SHA commit required.

## Measurement adapter and frozen delivery

The new [Stage-B adapter](token_benchmark/20261006_round2/stage_b/execution_helper.py) reuses the unchanged Stage-A execution and post-pair grading helpers. It changes only the runtime-tested SHA and in-memory two-session order, initializes independent 200k accounting, and requires successful complete L01 coverage/selection before measured DOGFOOD. The on-disk preregistration, task, schemas, fixture, coverage, selector and original helpers remain unchanged.

- Stage-B adapter SHA-256: `fc86a05d94fbca9ba1cbdc026bf57b2ba05e9e766b925704003960bdf4c0a004`; recorded before the first live call and verified unchanged throughout.
- Stage-A measurement helper SHA-256: `30e279993e1fba6f60447b64d0d5a4e156a9d050dbe1ea879a61d9ba1f08f483`.
- Stage-A deterministic grader SHA-256: `6b8b72f8297d03676c00831077a35307c6028dff53516790d864ae4c94a97684`.
- L01 fixture: 49,152 bytes; SHA-256 `4daf6bb797a23ececd1baedaafc71e939c6c11527901fc8fbc37ea742485d956`.
- L01 gold SHA-256: `63994d36ff33d42636f286b5fa134d09472f35e9fff48d20cce1790b1ba12c67`. Content read only for post-pair grading; digest verification never feeds gold into prompting/coverage/selection.
- CONTROL prompt = same frozen task/schema + full fixture, no company result/metadata or other answer. Its prompt digest is identical to Stage-A L01 CONTROL.
- DOGFOOD prompt = same frozen task/schema + company result + gold-independent coverage + frozen manifest + verbatim selected original bytes. No full fixture or extra evidence-reader call.
- No nonce/cache manipulation, semantic repair, widened selection, answer editing or answer-improvement rerun. Raw JSONL/prompts/company outputs/request audits/private mapping remain temporary local evidence outside Git.

## Codex configuration and actual usage

Before execution and each measured call, Codex CLI was **0.159.2**. Explicit requested model/config matched Stage A exactly: `gpt-6.1-sol`, reasoning `high`, service tier `default`, approval `never`, sandbox `read-only`, `exec --json --ephemeral --ignore-user-config --ignore-rules`, fresh thread, one turn, no resume, same frozen L01 `--output-schema`, stdin prompt, canonical cwd. Backend model identity is not attested by the emitted JSONL.

Usage source: **`turn.completed.usage`**. Both sessions report the same five fields: input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens. Primary = input+output; secondary = (input−cached)+output. Do not add cached/cache-write/reasoning again.

| Order | Variant | Input | Cached | Cache write | Output | Reasoning output | Primary | Secondary | Wall s | Exit / tools |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | DOGFOOD | 19,968 | 0 | 0 | 561 | 122 | 20,529 | 20,529 | 19.088 | 0 / 0 |
| 2 | CONTROL | 32,209 | 0 | 0 | 662 | 101 | 32,871 | 32,871 | 21.419 | 0 / 0 |

Two distinct thread IDs; each thread.started=1, turn.started=1, turn.completed=1; no missing usage, version/field-set drift or timeout. Both started/completed item-event streams contain zero tools. Actual argv, field sets, timing epochs and digests are retained in [Stage-B results.json](token_benchmark/20261006_round2/stage_b/results.json).

| Variant | Session ID | Prompt bytes | Prompt SHA-256 | JSONL SHA-256 |
|---|---|---:|---|---|
| DOGFOOD | `01a10fca-1b4f-7c60-ad83-d865c25c2bdd` | 9,681 | `694e39452f98ab5dd9233d7948fe6c5ce77c4430b700dd0b89ce4858ee02df96` | `50d861382ab412f692a30bcfebfcdf0acd12c574d6ed95a5f3eef8988486b422` |
| CONTROL | `01a10fca-9f1f-76f0-b9ab-01697a6313e9` | 52,333 | `99fb388b2960c1228c70d214c4c50f72d41c4616c31d1e31f2fb781e30d6b6f4` | `ee478704ccafbd50631ba265455a94d658a66920386da40859a2f8c82ceb6ee9` |

## Cache background

| Variant | Prior recorded measured session | Prior stage / fixture / variant | Seconds since prior end | Cached input |
|---|---|---|---:|---:|
| DOGFOOD | `01a10fac-ce89-7b31-b9ed-bc5ebfe2627e` | A / L03 / DOGFOOD | 1885.051 | 0 |
| CONTROL | `01a10fca-1b4f-7c60-ad83-d865c25c2bdd` | B / L01 / DOGFOOD | 14.668 | 0 |

The first Stage-B background references the last recorded Stage-A session; this is metadata only and never enters model context. Its interval is derived from actual capture-start and previous end epochs. Intervening unmeasured model/coordinator traffic is unknown; fresh/ephemeral does not promise cold cache. Secondary equals primary because the runtime reported cached input=0, not because missing values were replaced with zero.

## Company phase and actual Request proof

One live llmtool `ask --skill test-triage --provider company-round2 --file <bounded synthetic input> --verbose`, invoked through its official `llmtool.entrypoint.main` under the canonical venv. Isolated temporary HOME/config exactly matches preregistration: openai-compatible, `http://home-llm.example:8000`, model gw, timeout 120, null key/key-env, enable_thinking=false, temperature 0, max_tokens 4096.

The unchanged Stage-A narrow observer checks the actual outgoing Request and delegates unchanged to the original opener. It proved **chat_template_kwargs.enable_thinking=false**, model=gw, temperature=0, max_tokens=4096 and **no Authorization**. No direct curl/mock transport, raw-response inspection or config/credential mutation. VPN route used utun4. Synthetic data permission is explicit in this benchmark task.

Input is the exact frozen L01 company instruction plus full 49,152-byte fixture; 49,444 input bytes. User input SHA-256 `6372075dce2626ccfd96c0aea5ef07563a2f1ec3007fb1b24aa071f3011241d1`; actual request body SHA-256 `6b564d2976dec0665d9d175d0547f6f740b34722d7ab35f891629623f8384482`. Both match Stage-A L01 exactly, including full actual Request body digest.

Config SHA-256 `17a21fe7fe23b8aa230732b4b272d725693ec5d3655fd58f2103aeea7afb1b1f`; observer SHA-256 `f1a3a8fd063f90ded8b5218098a8f63ebbc25747b9f750660443673bcea33d20`. Original user config path was absent before and after. No real config contents or credentials inspected.

| Attempts / retries | Exit / actual requests | Reported model | Prompt tokens | Completion | Total | Provider s | Wrapper s |
|---|---|---|---:|---:|---:|---:|---:|
| 1 / 0 | 0 / 1 | qwen3.6-35b-a3b | 19,179 | 489 | 19,668 | 2.617 | 3.196 |

Company usage source is reported llmtool verbose provider metadata. No failed attempt, transport retry, length failure, coverage retry, semantic retry, fallback or max-token increase occurred. L01 company JSON schema passed; schema validity is not a semantic verdict.

## Coverage, selected evidence and deterministic grades

Frozen gold-independent coverage: exact four canonical test IDs, exact files, no missing/duplicate/unexpected ID or file-membership error. PASS. The frozen selector uses validated anchors only, ±2 lines, merge gap≤2, source order and max 32 pre-merge ranges.

Selected raw: **1,768 bytes / 3.597%**; four source-ordered ranges, four pre-merge ranges; cap 9,830 bytes. No manual widening/repair. Frozen before DOGFOOD capture and before post-pair gold grading. Selected raw SHA-256 `1c07ebe297fc3420928c85034e4b39b01692e43f11fba1e3735d8edea8fa87f4`; manifest SHA-256 `fc24d9fc57811f8bfeb0e196b0a1f1ce0c7795915d19bb24e1a0c57daededf8f`.

| Source lines | Start byte | Exclusive end byte |
|---|---:|---:|
| 150–154 | 11638 | 12080 |
| 306–310 | 23862 | 24311 |
| 462–466 | 36094 | 36518 |
| 618–622 | 48300 | 48753 |

Delivery PASS: original slices reconstructed exactly; all 1,768 bytes injected verbatim; no extra tool/read. CONTROL contains full fixture with no company/other-variant material. Actual prompts reconstructed byte-for-byte from the permitted inputs. Raw JSONL usage/hashes, actual request/input digest, frozen coverage/selection and chronology reconciled offline.

After both variants completed, the unchanged deterministic grader checked final schemas, exact signature groups/categories, all four test/file IDs and kinds, integer counts and required unknown/evidence field presence. **DOGFOOD PASS / CONTROL PASS**, no misses. All four gold target lines were selected. This is not semantic self-grading; executing sessions did not receive gold or evaluate their own quality.

## Independent Stage-B blind package

[Stage-B grading package](token_benchmark/20261006_round2/stage_b/blind_grading/README.md) contains new **L01-C.json / L01-D.json** and reviewer instructions. Fresh private random C/D assignment is kept outside Git with mode 0600. Neither mapping nor per-variant final-answer digest/length is published as a key to anonymous files. Both files are byte-identical to extracted final answers.

A limited mechanical screen found no Control/Dogfood/company/token/model identity terms in either answer. This is not a semantic blinding guarantee. Two independent graders must later see only anonymous answers + frozen L01 fixture/gold/schema/task, never metrics/company outputs/PR/reports/Stage-A answers or mapping. Preserve misses; disagreement requires independent tie-break. Independent Stage-B blind grading is complete: two graders both marked L01-C PASS and L01-D PASS, with pair parity EQUIVALENT. No provenance identity terms were present in either answer; no tie-break was required.

## Independent 200k accounting and interpretation

DOGFOOD 20,529; CONTROL 32,871. Provisional difference **+12,342 / 37.547%**. Pair usage **53,400**, separate Stage-B remaining **146,600**. Stage-A's remaining 203,884 was not used. The reused wrapper also records its unchanged 97,593 forecast start-check; Stage-B initialized at 200,000 and debits only this completed pair.

Descriptive mixed-token-volume delta: **−7,326** (=32,871−20,529−19,668). Different model/tokenizer units; not cost, compute or energy savings. Company usage is separate from the 200k Codex ceiling. Preparation/coordinator/future-review usage is not fully measured; whole-workflow ROI remains unknown.

Stage A L01 was separately quality-approved +39.816% for its CONTROL→DOGFOOD n=1 pair. Stage B is a separately selected-positive **provisional reverse-order confirmation**, subject to winner’s curse and selection/order/cache effects. Do not average percentages, pool tokens into an unbiased savings estimate, claim stable 40%, generalize beyond this synthetic canonical 48 KiB fixture, or set production routing thresholds. New semantic approval cannot be inherited from Stage A.

## Actual verification and scope

```sh
PYTHONPATH=tools/llmtool /usr/bin/python3 -B \
  reports/token_benchmark/20261006_round2/stage_a/self_check.py
```

Actual Python 3.9.6: **20/20 PASS, exit 0 before and after execution**. Includes 16 frozen digests, byte-identical regeneration, schemas, positive/negative coverage/selector and gold-isolation guards. These offline checks touch L02/L03 artifacts as required; no L02/L03 semantic benchmark session was run.

All current Stage-A/preregistration reports/results/helpers and frozen artifacts remain unchanged. Only six Stage-B report/helper/metrics/anonymous-package files are added. All nine unrelated untracked artifacts preserved. No application production/skills/tests/CI changes, checkout copy, reset/stash/clean/force push/history rewrite or merge.

Application canonical full suite/new security/QA gate: **NOT TESTED** this turn (benchmark/report-only scope). Application CI filters reports-only paths; final-head checks are inspected at publication, and no historical/absent Python 3.9/3.14 CI is claimed as new PASS. Stage-B blind grading and Round 3 **NOT TESTED/NOT STARTED**.

## Required execution summary

```text
Stage B L01 confirmation

Order:
DOGFOOD -> CONTROL

DOGFOOD Codex: 20,529
CONTROL Codex: 32,871
Provisional tokens saved: +12,342
Provisional saving %: 37.547%

Company tokens: 19,668
Selected raw bytes/%: 1,768 / 3.597%
Coverage: PASS
Selector: PASS
Delivery: PASS; 0 tool calls
DOGFOOD deterministic grade: PASS
CONTROL deterministic grade: PASS

Blind review:
2/2 graders PASS/PASS; pair EQUIVALENT

Stage-B measured Codex total: 53,400
Stage-B remaining 200k: 146,600

Confirmation validity:
QUALITY_APPROVED_REVERSE_ORDER_CONFIRMATION

Can claim stable/generalized savings:
NO
```

Normal commit/push to existing Draft PR #37, then STOP for independent Stage-B review. No additional measured pair, blind grader, Round 3, production change, A11b.2/A11c or merge.


## Independent Stage-B blind quality review

Review source: [Stage-B review](llmtool_token_savings_large_evidence_stage_b_review_20261006.md).

Two independent blind graders evaluated the separate C/D package before reading the
Stage-B measurement report:

- L01-C: PASS / PASS
- L01-D: PASS / PASS
- pair parity: EQUIVALENT / EQUIVALENT
- tie-break: not required
- provenance identity terms in answers: none

Grader #1 read the full permitted L01 fixture and confirmed only the four canonical
FAILURE targets exist. Grader #2 read target-adjacent permitted fixture lines and
reached the same result. Both graders were limited to the anonymous answers plus the
frozen L01 fixture/gold/schema/task.

Stage-B confirmation is therefore quality-approved:

- DOGFOOD: **20,529**
- CONTROL: **32,871**
- Codex tokens saved: **12,342**
- primary saving: **37.547%**
- company tokens: **19,668**
- selected raw: **1,768 bytes / 3.597%**
- quality: **EQUIVALENT**

This confirms the positive direction observed in Stage A under reversed execution
order:

- Stage A L01 (CONTROL -> DOGFOOD): **39.816%**
- Stage B L01 (DOGFOOD -> CONTROL): **37.547%**

These values are deliberately not averaged or pooled. Stage B is a selected-positive
confirmation subject to winner's curse, and the evidence remains limited to one
synthetic canonical 48 KiB test-output fixture. It does not establish a stable 40%
rate, generalized savings, or a production routing threshold.
