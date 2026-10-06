# Round 3 C1/C2 remediation — offline Python 3.9 retest (2026-10-06)

Status: **RETEST FAILED / NOT READY FOR TARGETED REMEDIATION REVIEW**.

This fresh run supersedes the historical 79/79 validation **for the current implementation**. The old result remains historical evidence for `29bd0dd92db203532f119a3b4c77be476454ab87`; it does not establish a PASS for this remediation head.

## Exact provenance and scope

- Repository: `davidcatcat/davidcattoolsproject`.
- Actual canonical Mac checkout: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`.
- Exact tested HEAD: **`afcf35cddea86ad3ffb53d4383e7f298df2115ef`**; matched fetched remote head and the user's expected SHA. Safe fast-forward from local `aa2443eb59eda1b773415c2c781e56259b84898a`; no reset, clean, stash, clone or worktree.
- Authority read: [independent implementation review](llmtool_token_savings_round3_format_probe_implementation_review_20261006.md), [remediation report](llmtool_token_savings_round3_format_probe_implementation_review_remediation_20261006.md), and [offline retest handoff](llmtool_token_savings_round3_format_probe_offline_retest_handoff_20261006.md). The handoff's historical expected SHA is superseded by the user's explicit latest head.
- Retest-only scope: one full offline suite run, whitespace check, read-only digest/cardinality verification, and this report. No implementation or test edits were made.

## Actual execution and count interpretation

Python: **3.9.6**, `/usr/bin/python3`, Clang 17.0.0 build.

Executed from the canonical checkout:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
git diff --check
```

Suite exit: **1**. Whitespace-check exit: **0**.

```text
Ran 88 tests in 0.123s

FAILED (failures=5, errors=5)
```

- Static test-method count: **88**, matching the contract.
- Executed test-method count: **88**.
- Successful test methods: **83 / 88** (83 `... ok` lines).
- Failed test methods: **3**; unittest failure entries: **5**, including three failing subtests in one method.
- Errored test methods: **2**; unittest error entries: **5**, including four errored subtests in one method.
- Skips: **0**.

The entries are not separate test methods: `83 + 3 + 2 = 88`. Subtracting all five failure and five error entries from 88 would incorrectly report only 78 successful methods.

## Failures/errors observed

| Test method | Outcome | Actual behavior |
|---|---|---|
| `test_colon_form_canonical_fields_are_not_harmless_extras` | FAIL, one entry | Appending `event_id: EVT-T99 signature: UPSTREAM_TIMEOUT` produced `passed=True`; expected coverage failure |
| `test_malformed_event_does_not_hide_among_extras` | FAIL, one entry | Appending partial `EVENT event_id=EVT-FABRICATED` produced `passed=True`; expected coverage failure |
| `test_namespace_identifier_reference_in_extra_fails` | FAIL, three subtest entries | Fabricated/contradictory EVT-/REQ- prose was accepted as harmless extras instead of failing |
| `test_missing_request_proof_rejected` | ERROR, one entry | Old assertion expects `ValueError`; remediated typed-observation check raises `TypeError` for `{}` |
| `test_partial_or_embedded_event_cannot_repair_coverage` | ERROR, four subtest entries | Missing-event coverage still failed, but malformed claims were not classified into `invalid_canonical_claims`; accessing `[0]` raised `IndexError` |

The three namespace subtests were:

```text
EVT-T09 UPSTREAM_TIMEOUT on REQ-99 at 08:02:20Z
EVT-T01 REQ-17 timed out at 09:30:00Z
event EVT-T01 repeated
```

The four partial/embedded subtests were: partial `EVENT event_id=EVT-T01`; a full canonical line prefixed by `quoted: `; that canonical line with appended prose; and `event_id=EVT-T01 was observed`.

Read-only source inspection explains the C2 failures: `CANONICAL_LOOKING` and `NAMESPACE_REFERENCE` contain doubled backslashes inside raw regex strings, such as `r'\\b'` and `r'\\s'`. These represent literal backslashes rather than the intended word-boundary/whitespace regex operators. This agrees with ordinary colon/namespace/partial inputs not being classified. No regex or assertion was changed in this retest.

The request-proof error is an existing test expectation inconsistent with the remediated typed API. The new arbitrary-dict test correctly expects `TypeError` and passes; the old missing-proof test remains an error. This is not reported as a provider failure or a successful full-suite result.

## Required C1/C2 cases

### C1 specified new cases: PASS (5/5)

- `test_accounting_rejects_anchor_stage_l03_evaluation`: PASS.
- `test_accounting_rejects_wrong_lane_evaluation`: PASS.
- `test_accounting_cannot_override_failed_evaluation_with_empty_reasons`: PASS.
- `test_request_proof_must_be_typed_observation_not_dict`: PASS.
- `test_three_of_seven_cannot_make_accounting_gate_pass`: PASS.

This is limited to the five requested new counterexamples. It does not close C1's full regression gate: the accounting suite still contains the old request-proof test ERROR above.

### C2 specified new cases: FAIL

- Colon-form fabricated canonical fields: **FAIL** (incorrectly accepted).
- Benign `Four EVENT lines were observed...` prose: PASS as extra.
- Frozen fixture's `Canonical EVENT` comment: PASS as extra.
- EVT-/REQ- provenance prose: **FAIL** (three inputs incorrectly accepted).

Additional existing malformed/partial classification regressions are listed above. C2 is not closed on this tested head.

## Digests and actual multimap

| Artifact | SHA-256 |
|---|---|
| `format_probe.py` | `9e12a04569c04b44821341dd0c3a893588defa51be0506eb75f3ec97cf7ed7f3` |
| `test_format_probe.py` | `74c54a8cf923a0bc1df410035e6b03057b9120defbc4b7476a0bb4bd8650da47` |
| L02 frozen fixture | `859effa1e4d6e978372f4914aca8c53e4af2953f3850ceaad2a68732060892da` |
| L03 frozen fixture | `44caecfb4fc77e639d471710b6fc190ca2e1145ae94a22396c5cdcc487dd88ce` |

Read-only recomputation with the exact-head helper:

```text
records: 668
distinct pairs: 534
ambiguous pairs: 120
records in ambiguous pairs: 254
```

Both fixture hashes and multimap counts match the frozen contract. They were not modified to obtain these values.

## Publication boundary and remaining gates

- Company live calls: **0**. Codex calls/benchmarks: **0**.
- Production/runtime/skills/fixtures/gold/helper/tests: no changes in this retest.
- All nine pre-existing unrelated untracked paths were preserved; no runtime artifacts or caches were generated by this `-B` suite run.
- This failed report was initially retained locally under the user's **all PASS before commit/push** condition. The user subsequently explicitly authorized uploading the failed test report for review. Publication is therefore report-only; the failure verdict and exact tested implementation SHA remain unchanged.
- No live wrapper was implemented. C3 future-wrapper requirements remain pending; live Phase 0 remains unauthorized.
- A further authorized remediation must address the classifier and old exception expectation, then repeat the offline suite. This retest does not perform those changes or claim independent remediation approval.

```text
Exact tested head: afcf35cddea86ad3ffb53d4383e7f298df2115ef
Python: 3.9.6 (/usr/bin/python3)

Tests:
PASS 83 / 88 test methods
FAIL: 5 failure entries across 3 methods
ERROR: 5 error entries across 2 methods
SKIP: 0
Exit: 1

git diff --check: PASS (exit 0)

C1 remediation tests:
PASS — five specified new cases; full accounting regression is not green

C2 remediation tests:
FAIL

L03 multimap:
records: 668
distinct pairs: 534
ambiguous pairs: 120
records in ambiguous pairs: 254

Company live calls: 0
Codex calls: 0
Production/runtime/skill changes: NONE

Ready for targeted remediation review: NO
```
