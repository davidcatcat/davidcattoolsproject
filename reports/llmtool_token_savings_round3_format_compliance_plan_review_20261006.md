# PR #38 Round-3 format-compliance plan — independent methodology review (2026-10-06)

**Verdict: APPROVE WITH CHANGES.** The proposed adapters address the actual Round-2 failures. Offline probe implementation/self-tests may proceed. Live probes must wait for the three contract clarifications below, frozen implementation/instructions, and passing offline checks. This review does not authorize live execution.

## Review contract and provenance

- Repository: `davidcatcat/davidcattoolsproject`; actual local Mac checkout: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- PR: [Draft #38](https://github.com/davidcatcat/davidcattoolsproject/pull/38).
- Exact reviewed head: `d08bb1e7f0aae40c2f7ccf6391c67ad19e0ee190`.
- Fetched base/main: `fecbdbae928b63de2661ed4b8faf6dbfff964400`; merge base equals this SHA.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`; local/remote ahead/behind at inspection: `0 / 0`.
- Complete reviewed diff: one new 317-line [plan](llmtool_token_savings_round3_format_compliance_plan_20261006.md), SHA-256 `9d4a2cbbd0b18eb13a9635a1a74dd4d45fd5763564ef64c9600b2957dfbaad1d`.
- Evidence inspected: repository guidance/WORKFLOW, Round-2 Stage-A and Stage-B independent review reports, frozen `stage_a/coverage.py`, existing L02/L03 fixture bytes, A4 `context.py`, and its optional-locator tests.
- Review type: source/document methodology review, not new benchmark execution, live validation, or blind answer grading. Historical outcomes below remain attributed to their existing reports.
- Publication scope: this review report only. The plan, production, runtime skills, fixtures, gold, and frozen helpers remain unchanged. Existing nine untracked paths are protected and excluded from staging.

## Round-2 facts preserved

| Observation | Historical result | Round-3 interpretation |
|---|---|---|
| L01 Stage A | CONTROL 34,235; DOGFOOD 20,604; 39.816% reduction; quality parity | Valid observation, reported separately |
| L01 Stage B | CONTROL 32,871; DOGFOOD 20,529; 37.547% reduction; quality parity | Valid reverse-order confirmation, not pooled with Stage A |
| L02 | All four required canonical events found; final quality EQUIVALENT; pair INVALID | Frozen whole-output coverage rejected additional noncanonical evidence/timeline/signatures; no retroactive validity change |
| L03 | A4 packet parsed; evidence omitted the required free-text `record_id=` token; zero validated anchors and zero selected raw bytes; DOGFOOD quality failed | Structured anchoring and an empty-anchor hard stop directly address these failures |

Sources: [Stage-A review](llmtool_token_savings_large_evidence_stage_a_review_20261006.md) and [Stage-B review](llmtool_token_savings_large_evidence_stage_b_review_20261006.md). No new saving, quality, or reliability result is claimed here.

## Required clarifications before live company calls

These are gaps in a proposed contract, not observed bugs in an implemented Round-3 adapter. None requires a production refactor. Negative self-tests can be written first; the rules must be explicit and frozen before live output is observed. This review leaves the author's plan untouched.

### M1 — Require complete canonical timeline coverage, not merely relative order

[Plan lines 97–103](https://github.com/davidcatcat/davidcattoolsproject/blob/d08bb1e7f0aae40c2f7ccf6391c67ad19e0ee190/reports/llmtool_token_savings_round3_format_compliance_plan_20261006.md#L97-L103) require every event exactly once in **evidence**, but only say the timeline subset must preserve order. An empty timeline or an ordered proper subsequence can preserve order while omitting required timeline events. The frozen Round-2 helper compared the complete expected timeline sequence; removing whole-output purity should retain that completeness requirement.

Before live probes, specify:

- After separating genuine unrelated extras, the timeline's required canonical sequence must equal the full fixture-derived expected sequence, exactly once per required event, ordered by timestamp with source-line tie-break.
- Duplicate checks apply separately within evidence and timeline. One occurrence in each array is expected, not a cross-array duplicate.
- Compute signature counts once from the validated evidence subset; do not double-count timeline copies or extra prose/signatures.
- Validate each canonical object's entire tuple against the frozen fixture before counting/anchoring. An unknown ID or wrong request/signature/timestamp in a canonical EVENT claim is a failure, not an extra to discard. Partial/prose mentions cannot satisfy coverage.
- Define the parser boundary for malformed canonical-looking entries. Such entries cannot hide a missing/wrong required event by being treated as evidence for that event; exact membership remains mandatory.

Offline counterexamples: complete evidence plus empty/partial timeline must FAIL; duplicate timeline and wrong-field timeline must FAIL; correct complete evidence/timeline plus unrelated extras must PASS. Extras must never repair a missing event, inflate counts, or enter the anchor set.

**Severity/when:** required before live calls; no blocker to writing these offline tests now.

### M2 — Make nonempty anchors an explicit Phase-0 condition

[Plan lines 184–191](https://github.com/davidcatcat/davidcattoolsproject/blob/d08bb1e7f0aae40c2f7ccf6391c67ad19e0ee190/reports/llmtool_token_savings_round3_format_compliance_plan_20261006.md#L184-L191) require three structured-contract passes but do not explicitly require a nonempty anchor set for each Phase-0 invocation. The empty-anchor rule at lines 147–154 is scoped to future paired benchmarks.

The actual A4 parser accepts empty evidence and permits an absent/null locator. Thus a structurally valid empty packet has no bad individual mapping but yields no usable anchors. A vacuous all-items-valid check must not count toward 3/3.

Before live probes, require each L03 invocation to have at least one validated, unique structured anchor, with zero unknown/ambiguous/duplicate mapped items under the proposed strict anchor gate. Omitted/null locators fail that probe-level gate while remaining valid A4 syntax. An empty validated anchor set must fail Phase 0 even if parser validation succeeds. Include a valid A4 empty-packet negative self-test.

Nonempty anchors prove only usable syntactic references. They do not prove that all seven semantic targets were recovered. Report post-output target recall separately, including misses, without using gold to manufacture or expand anchors. Any future minimum target-recall acceptance threshold must be preregistered; it cannot be inferred after seeing the three outputs.

**Severity/when:** required before live calls; no blocker to offline implementation. Keep the product A4 contract unchanged.

### M3 — Distinguish six logical trials from up to twelve actual requests

[Plan lines 168–180](https://github.com/davidcatcat/davidcattoolsproject/blob/d08bb1e7f0aae40c2f7ccf6391c67ad19e0ee190/reports/llmtool_token_savings_round3_format_compliance_plan_20261006.md#L168-L180) declare six calls and allow one same-config transport retry per invocation. That is six initial/logical trials and at most **twelve actual request attempts**, not a hard six-request budget. The proof requirement at line 188 also needs to cover retries.

Before live probes, freeze which interpretation applies:

- If retries remain allowed, declare six logical trials, maximum twelve request attempts, and the per-trial retry limit. Keep 3/3's logical-trial denominator distinct from first-attempt success and total request counts. A transport retry does not erase the initial failure; describe any successful compliance result as transport-retry-assisted.
- If six physical calls is the intended strict ceiling, remove retries for that execution contract. Do not silently drop later planned trials to accommodate earlier retries.
- For every actual outgoing attempt, preserve unchanged Request observation/proof, attempt linkage, configuration/prompt digest, failure category, elapsed time, and usage. Record a failure before Request construction distinctly rather than inventing Request proof.
- Retain all failed attempts. Missing usage is unknown; a complete company-token total is unknown if any consumed attempt's usage is unavailable. A known subtotal may be reported separately.
- Length, schema, format, coverage, unknown/ambiguous-anchor failures never permit another semantic attempt. Report these against the fixed trial denominator.

**Severity/when:** required before live calls; no blocker to writing bounded retry/accounting self-tests.

## Assessment by design component

### L02 required subset: sound with M1 clarified

Allowing unrelated log observations is consistent with `log-triage` and fixes the Round-2 whole-output purity mismatch. The proposed exact-event membership, field checks, required counts/signatures, duplicate rejection, and canonical-only anchors are the right boundary. Removing extra-item rejection must not remove required correctness.

The adapter should classify an item before counting it: validated required canonical object; invalid canonical claim; or genuinely noncanonical extra. Only the first category contributes required coverage/counts/anchors. Extra `error_signatures` remain visible but cannot substitute for a missing required signature. Additional prose is not verified truth merely because it is tolerated syntactically; semantic grading still applies later.

Subject to M1, unrelated extras cannot satisfy required coverage, alter canonical counts, become selector anchors, or hide fabricated/missing required events.

### L03 exact source+locator: PASS; ambiguity is already present in the fixture

The proposal uses existing [EvidenceItem fields](../tools/llmtool/llmtool/context.py) correctly: `statement`, mandatory nonempty `source`, and optional `locator`. A4 validates structure/text safety, not provenance membership or uniqueness. Requiring a usable locator for this benchmark adapter is a narrower anchoring rule, not a change to A4.

Read-only counting using the existing frozen `parse_fixture('L03', ...)` confirmed:

| Existing L03 fixture property | Count |
|---|---:|
| Canonical RECORD objects | 668 |
| Distinct exact source+locator pairs | 534 |
| Pairs matching more than one record | 120 |
| Records belonging to ambiguous pairs | 254 |
| Seven non-inventory target records | Each matches exactly one record |

For example, existing pair `('inventory-11', 'row-3')` maps to both `INV-0-00002` and `INV-1-00002`. Non-uniqueness is an actual fixture property, not only a real-world possibility. Reused source names/locators across snapshots can create the same issue in practice.

Build a **multimap over the entire fixture universe**, retaining every record per pair. A last-writer-wins dictionary or an index restricted to gold targets would incorrectly conceal ambiguity. Exact equality means no case folding, trimming/fuzzy matching, or text-based disambiguation. Zero matches must be UNKNOWN; multiple matches AMBIGUOUS; repeated evidence mapping to the same unique record must fail the duplicate/uniqueness gate. Free-text `record_id` must never select between ambiguous matches or override structured fields.

The plan explicitly handles these risks. M2 makes the invocation-level success condition complete. If a live output cites ambiguous inventory, the planned strict gate should fail and retain the evidence; do not weaken it after observing that result.

### Pre-Codex hard stop: PASS; freeze future accounting/order policy

Coverage failure, empty anchors, or failed selector/cap validation must prevent DOGFOOD Codex execution. This eliminates the specific Round-2 L03 waste. Retaining company usage and not inventing zero Codex usage are correct.

Before any later paired benchmark, also preregister CONTROL behavior under both orders. If CONTROL already ran before company preparation failed, retain its actual tokens; if it did not run, record NOT STARTED/null with reason. Count every assigned pair and all failed preparation attempts; never replace failed pairs with fresh successes or report only survivors as overall effectiveness. Savings on admitted valid pairs must be labeled conditional on passing preparation, separately from the preparation success rate and total experiment consumption. This is a future paired-benchmark gate, not a reason to run CONTROL during Phase 0.

### Three repetitions: reasonable format-readiness gate, not reliability estimation

Three unchanged fresh invocations per fixture are a reasonable inexpensive smoke gate for the known interfaces. A 3/3 observation establishes that those three trials passed on these fixed synthetic inputs. Fresh requests do not establish statistical independence: shared backend state and identical temperature-zero prompts can yield correlated results. It cannot establish a general reliability rate, quality parity, or savings.

The plan's exclusions are appropriate. Interpret “reliably” in lines 31/284 only as observed 3/3 fixture-specific compliance, not a statistical reliability claim. Freeze the six-trial order before calls, preserve all outcomes, and do not revise prompts between trials. M2/M3 complete the gate's operational definition.

### Gold isolation: PASS as a boundary; enforce it in offline checks

The fixture, schemas, and fixed task instructions are allowed inputs. Gold is evaluation-only. Required L02 events and the full L03 source+locator index must be derived mechanically from fixture bytes, not from gold target lists.

Freeze the output, adapter decision, validated anchors and any selected ranges before loading gold for recall evaluation. Evaluation must be unable to change company output, prompt, anchor membership, selection, trial outcome, or retry decisions. Low recall may be reported or fail a previously registered acceptance threshold; it must never trigger repair or semantic retry. A4/source membership alone does not justify semantic acceptance.

### Cost control: appropriate sequencing, with M3 and additional offline checks

Keeping Codex out of Phase 0 is appropriate. The plan already requires committed rules/self-tests before live calls. Add the following to that gate; no new formal benchmark fixtures or production changes are needed to write in-memory counterexample tests:

1. **L02:** M1 cases; correct extras that cannot cover a missing event; altered timestamps/requests/signatures; fabricated IDs in either array; duplicate checks per array; canonical counts unaffected by extra signature/prose; deterministic source-line tie-break; precise handling of embedded/partial canonical lines.
2. **L03:** empty A4 evidence; absent/null locator; unknown/ambiguous pair; duplicates; misleading free-text IDs; unique matches independent of statement token; actual fixture index cardinalities above. Verify no dictionary overwrite hides collisions.
3. **Hard stop/selector:** fake Codex-launch counter remains zero for invalid coverage, empty anchors, selector/range/cap failure. Retain failed-company metrics and null/not-started Codex metrics. Do not treat an empty selection as successful usable evidence. Reuse frozen selection rules rather than widening ranges after output.
4. **Transport/accounting:** injected failure permits at most the registered retry; format/length failures permit none; every attempt remains recorded, unknown usage stays unknown, and Request proof covers each emitted attempt. Use an offline fake transport to verify observation leaves the Request unchanged and checks thinking=false, model=gw, temperature=0, max_tokens=4096, and absent Authorization.
5. **Isolation/freezing:** prove prompt/adapter/selector/retry paths cannot read gold; freeze exact company instruction bytes, helpers, tests, fixture digests, config, and trial order in a pre-live commit. Recheck digests after execution. Evaluate gold only through a separate post-output path.

No live calls are needed to establish these properties. This review did not implement or run those new tests; they are prerequisites for the next authorized step.

### Real-format follow-up: strengthen the preregistered requirement

[Plan lines 296–303](https://github.com/davidcatcat/davidcattoolsproject/blob/d08bb1e7f0aae40c2f7ccf6391c67ad19e0ee190/reports/llmtool_token_savings_round3_format_compliance_plan_20261006.md#L296-L303) say the later benchmark “should” include at least one noncanonical/realistic-format fixture. Make that mandatory before later benchmark approval. One realistic fixture is a minimum, but does not cover both repaired interfaces.

Recommendation: preregister at least one independently constructed realistic-format log fixture **and** one realistic mixed-evidence fixture. They may remain synthetic/non-sensitive, but cannot rely on canonical EVENT/RECORD lines as the sole route to anchors. Include multi-line/prose evidence and repeated/ambiguous source-locator cases; freeze their locators, negative cases, gold, coverage expectations, and evaluation before any live benchmark. Unsupported causal or hypothesis-to-fact promotions remain semantic failures even when mechanical coverage passes.

Also fix repetition/order/sample-size rules before observing effects, rather than deciding to repeat only when observed savings are small. Until then, Phase 0 only diagnoses these two known canonical fixtures. This is a follow-up preregistration condition and non-blocking recommendation for current offline/Phase-0 work; no new fixtures are authorized by this review.

## Gate classification

| Gate | Disposition |
|---|---|
| Blocker before writing offline probe/self-tests | NONE; sound approach, counterexample-driven offline work may proceed |
| Blockers before live company calls | M1 full canonical timeline sequence; M2 nonempty Phase-0 anchors; M3 logical-trial/request-attempt definition and accounting; then frozen rules/prompts and passing offline checks |
| Before future paired benchmark | Separate authorization/preregistration; CONTROL hard-stop/order policy; failed-pair denominator and consumption; mandatory realistic-format coverage; independent quality review |
| Non-blocking recommendations | Broader realistic fixtures for both domains; preregistered repetitions; avoid statistical use of “reliably” |

## Validation actually performed

- PASS: fetched remote identity/base/head verification and complete exact-head diff review; PR confirmed Draft.
- PASS: read-only fixture hashes/sizes: L02 65,536 bytes, SHA-256 `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da`; L03 131,072 bytes, SHA-256 `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce`.
- PASS: existing frozen parser used read-only under `/usr/bin/python3 -B` to count L03 exact pair multiplicity; no fixture generation, file repair, gold-assisted anchor selection, or provider request.
- PASS: documentation checks for report references, unchanged plan/fixture hashes, report-only diff, and `git diff --check` before publication.
- NOT RUN: new probe/self-tests, company calls, measured Codex sessions, blind grading, application suite, and live/runtime validation. They are not evidence claimed by this planning review.
- CI: `.github/workflows/llmtool-ci.yml` filters changes to `tools/llmtool/**` and itself. This report-only change does not satisfy that trigger; no application-CI success is claimed for it.
- Publication commit is separate from the reviewed head. The reviewed revision remains `d08bb1e7f0aae40c2f7ccf6391c67ad19e0ee190`; adding this report does not constitute review of a revised plan.

## Strict verdict

```text
PR #38 ROUND-3 FORMAT-COMPLIANCE PLAN REVIEW:
APPROVE WITH CHANGES

L02 subset coverage:
finding — M1: explicitly require the complete ordered canonical timeline subset

L03 source+locator anchors:
PASS — exact unique mapping is A4-compatible; reject existing ambiguous pairs

Pre-Codex hard stop:
PASS — retain all failed attempts; freeze future CONTROL/order accounting before pairs

3x company-only compliance gate:
finding — M2: explicit nonempty anchors per invocation; 3/3 is not reliability proof

Gold isolation:
PASS — enforce evaluation-only access after output/anchors/selection are frozen

Cost-control design:
finding — M3: six logical trials may produce twelve requests with transport retries

Real-format follow-up requirement:
finding — make at least one mandatory; recommend separate log and mixed-evidence fixtures

New blocker:
No architectural blocker; M1/M2/M3 must be resolved and offline checks pass before live calls

May implement offline probe/self-tests:
YES

May run six live company probes:
NO
```

Stop here. No plan edit, live probe, Codex benchmark, production/skill modification, fixture generation, or merge was performed.
