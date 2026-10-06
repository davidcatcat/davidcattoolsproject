# Round 3 format probe — offline implementation validation (2026-10-06)

Status: **OFFLINE IMPLEMENTATION COMPLETE / INDEPENDENT IMPLEMENTATION REVIEW PENDING**.
No live compliance or token-savings result is claimed.

## Task contract and exact provenance

- Repository: `davidcatcat/davidcattoolsproject`; actual Mac canonical checkout `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`; existing Draft [PR #38](https://github.com/davidcatcat/davidcattoolsproject/pull/38).
- Safely fetched/fast-forwarded starting head: `e2c4890f936292fdaa39e0103837580e5ec12fdb`. No reset, clean, stash, clone, or worktree was used.
- Authority: latest [plan](llmtool_token_savings_round3_format_compliance_plan_20261006.md), prior methodology reviews/re-review, [implementation handoff](llmtool_token_savings_round3_format_probe_implementation_handoff_20261006.md), and the user's latest offline-only contract. The handoff's historical expected head predates the explicitly supplied starting head.
- Plan SHA-256: `df4e7bfa859c637336a49008c8d5eac7783c7a29416024e824162f420b425a9a`.
- Handoff SHA-256: `9ce575a1750d4f7bd8ce3df245fdf2436536fc5b64cd7b832fc8fcd094a97fcb`.
- **Exact tested implementation head: `29bd0dd92db203532f119a3b4c77be476454ab87`.** Tests were rerun after committing the helper/tests/README, with tracked source clean. This validation report is a separate documentation commit; it does not change tested helper bytes.
- Scope: pure deterministic adapters, frozen output/anchor results, post-freeze recall, raw stdout publication guard, fake Request observation, fixed-order accounting, and offline tests. No provider runner, prompt construction, production/runtime/skill change, or formal benchmark-fixture generation.

## Artifacts

| Artifact | SHA-256 |
|---|---|
| [format_probe.py](token_benchmark/20261006_round3/format_probe.py) | `99cebcb417bc50f9162ff195091ffbb412d69734c08bf42a4cc400545848c66b` |
| [test_format_probe.py](token_benchmark/20261006_round3/test_format_probe.py) | `f8a756b49f7450f582fe3146eddd5bb2755221b7f64eb3f802fff59736762fe5` |
| [README.md](token_benchmark/20261006_round3/README.md) | `d0307ba1c9905d438997a2f9bb82b2c0c525fa4d808300827b7db4d181c05edd` |

Only these three implementation artifacts and this report belong to this task. The amended plan and historical reports are unchanged.

## Actual Python 3.9 execution

Python: **3.9.6**, `/usr/bin/python3`, Clang 17.0.0 build.

Command run from the canonical repository, at the exact implementation head above:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
```

Result: **79 tests; 79 PASS; 0 FAIL; 0 errors; 0 skips; exit 0**. The committed-head run reported `Ran 79 tests in 0.082s` followed by `OK`.

| Test group | Passed / total | Coverage |
|---|---:|---|
| L02 | 23 / 23 | Full universe, complete ordered timeline, per-array duplicates, tuple mismatch/fabrication, malformed claims, inert extras, signatures, schema and timestamp/source-line tie-break |
| L03 | 21 / 21 | Actual multimap, unique/unknown/ambiguous/duplicate citations, empty/null provenance, no normalization/free-text override, 3/7 vs 7/7 recall, reusable evidence across categories, inventory rejection |
| Accounting | 18 / 18 | Fixed order, one request maximum/no retry, per-fixture early stop, unknown consumed usage, 35k/59k admission bounds, exact ceiling, honest overrun retention, invalid/out-of-order transitions, failure precedence |
| Raw/observer/isolation | 17 / 17 | Original-byte hash before scan/parse, immutable replay, secret publication block, unchanged fake Request and forbidden fields, source/runtime gold-I/O guards, post-freeze loader and non-mutation |

Test-runner guards prohibit socket creation and subprocess launch. In-memory negative cases are unit-test data, not new benchmark fixtures. The L02 Round-2-shaped counterexample uses existing recorded extra items plus frozen fixture EVENT lines; it is not replay of unavailable historical raw stdout and does not retroactively change the Round-2 verdict.

## Frozen fixture verification

| Fixture | Bytes | SHA-256 |
|---|---:|---|
| L02 | 65,536 | `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da` |
| L03 | 131,072 | `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce` |

Actual L03 full-fixture multimap, recalculated from immutable fixture bytes:

```text
records: 668
distinct pairs: 534
ambiguous pairs: 120
records in ambiguous pairs: 254
```

These equal both methodology-review counts. No drift was found; no fixture was edited to obtain this result.

## Implementation decisions and isolation evidence

**L02:** Each item is an exact validated EVENT, invalid canonical-looking claim, or unrelated extra. A complete EVENT must occupy the complete array string; partial, embedded or malformed EVENT text is rejected. Required evidence/timeline coverage and duplicates are independent; counts use validated evidence once. Anchors use exact fixture IDs only. Extras/signatures are retained without affecting counts or anchors.

**L03:** Uses the unchanged A4 parser and a full-fixture source+locator multimap. Free-text statement IDs never resolve ambiguity or add recall. Exact unique records are mapped once; two evidence items for the same record fail. A single evidence item may support multiple category items without requiring duplicate citations.

The frozen `INV-` record namespace is excluded from usable benchmark evidence without loading gold. Ambiguous inventory remains `AMBIGUOUS_STRUCTURED_ANCHOR`. A unique inventory citation is retained in `inventory_evidence` and fails usable-anchor eligibility under `UNKNOWN_STRUCTURED_ANCHOR`; this is a benchmark eligibility rejection, not a false assertion that the fixture record is absent. This secondary field is explicit for independent review.

**Gold isolation: PASS.** Adapter function signatures accept only immutable stdout/fixture-derived records, never target lists or gold paths/objects. Source guards exclude file/network/process I/O; runtime file-open guards permit both adapters to finish while gold/file access is blocked. Only the separate recall function invokes the metadata loader after a `FrozenEvaluation` exists. It returns a new immutable result, preserves stdout and prior anchors, and cannot mutate prior trial state. Recall intersects validated unique record IDs with the seven evaluation targets; statement text contributes nothing.

**Accounting state machine: PASS.** Order remains `L02-1 → L03-1 → L02-2 → L03-2 → L02-3 → L03-3`. No retry API/state exists. Same-fixture early stop preserves the other fixture's opportunities. Admission uses known cumulative usage plus the current 35k/59k bound, against 300k. Unknown consumed/emitted usage makes the complete total unknown and stops all remaining slots. Skipped slots have null usage, not zero. Unexpected actual overruns are retained, never clamped; the overall gate cannot pass above the ceiling.

**Raw stdout helper: PASS.** Original immutable bytes are SHA-256 hashed before scan/parse. Synthetic credential-marker cases block publication without redaction or replacement. Replay produces another diagnostic result and cannot overwrite the original immutable verdict. Marker categories omit matched values; the scanner is a conservative synthetic-output publication guard, not a universal secret detector.

**Fake Request observer: PASS.** Checks thinking=false, model=gw, temperature=0, max_tokens=4096, exact endpoint/POST and absent Authorization; validates without mutation. This is offline fake-object evidence, not observation of any actual outgoing Request. Actual live proof remains pending and cannot be inferred from these tests.

## Checks and protected state

- Python 3.9 syntax parsing: PASS.
- README/report local references: PASS.
- `git diff --check` / staged whitespace check: PASS before publication.
- Required test behavior and complete intended change reviewed by the implementing coordinator; **independent implementation review has not yet occurred**.
- Existing production/runtime/skills, plan, gold, fixtures and historical reports: unchanged.
- Existing nine unrelated untracked paths remain present and unstaged: four benchmark report/artifact paths, two reasoning report/artifact paths, and the three pre-existing `case.*` directories. They were not inspected, removed, reset or stashed.
- No test caches/live stdout/results artifacts were created in the new directory; `-B` was used.

## Remaining gates / NOT TESTED

- Independent implementation review: PENDING. This publication stops for that review.
- Live Phase 0: NOT RUN / NOT AUTHORIZED in this step. A later reviewed wrapper must freeze exact instructions/config/input/metadata digests, collect actual Request proof and complete usage/latency, preserve secret-scanned verbatim stdout, and apply final post-freeze recall before recording an L03 PASS.
- An anchor-stage PASS alone is not a Phase-0 L03 PASS; final recall must be 7/7. Mechanical membership/recall does not grade prose semantics or establish future Codex quality parity.
- No selector/live runner or measured Codex path is included. No Stage A/B, realistic new fixture, routing, production budget enforcement, A11b.2 or A11c work occurred.
- Application canonical suite and other Python versions: NOT RUN; no application source/resource changes in this offline-only step.
- Application CI: not claimed. The llmtool workflow is path-filtered to `tools/llmtool/**` and its workflow file; these `reports/**` changes do not trigger that application matrix.

```text
Exact tested head: 29bd0dd92db203532f119a3b4c77be476454ab87
Python: 3.9.6 (/usr/bin/python3)
format_probe.py SHA256: 99cebcb417bc50f9162ff195091ffbb412d69734c08bf42a4cc400545848c66b
test_format_probe.py SHA256: f8a756b49f7450f582fe3146eddd5bb2755221b7f64eb3f802fff59736762fe5

Tests: PASS 79 / 79; FAIL 0; exit 0
L02 fixture SHA: 859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da
L03 fixture SHA: 44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce

L03 multimap:
records: 668
distinct pairs: 534
ambiguous pairs: 120
records in ambiguous pairs: 254

Gold isolation: PASS
Accounting state machine: PASS
Raw stdout helper: PASS
Company live calls: 0
Codex calls: 0
Production/runtime/skill changes: NONE
Ready for independent implementation review: YES
```
