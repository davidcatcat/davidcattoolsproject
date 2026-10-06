# llmtool Round 3 — C1/C2 targeted remediation re-review handoff (2026-10-06)

## Purpose

Perform a short independent re-review of the C1/C2 remediation after the second full
offline retest passed.

No live company or Codex call is authorized.

## Revision

Repository:
`davidcatcat/davidcattoolsproject`

PR:
`#38`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Current review head:
`8f32027580df8a0a15ba2729c17b5bc2f2303306`

Exact tested implementation head:
`c1830ef2c60fbda61777461ffd20b3dd312a84ec`

The delta from tested head to current review head must contain only:
`reports/llmtool_token_savings_round3_format_probe_offline_retest2_20261006.md`.

## Evidence

Fresh canonical Mac Python 3.9 retest:

- 88 / 88 PASS
- 0 FAIL
- 0 ERROR
- 0 SKIP
- exit 0
- git diff --check PASS

Historical 79/79 and failed 83/88 runs remain historical only.

## C1 re-review

Verify directly in current `format_probe.py` and tests:

- `RequestObservation` is frozen/typed;
- `mark_request_emitted()` rejects arbitrary dict proof;
- generic caller-supplied-reasons `complete()` no longer exists;
- `complete_evaluation()` accepts only real `FrozenEvaluation`;
- evaluation lane must match logical trial lane;
- L02 requires final `canonical_coverage` stage;
- L03 requires final `phase0_l03_target_recall` stage;
- L03 anchor-stage evaluation is rejected;
- failure reasons are derived from frozen evaluation, not caller input;
- 3/7 L03 final evaluation records FAIL and cannot produce `gate_passed=True`;
- evaluation/raw-stdout digests are recorded in TrialRecord;
- pre-output transport/model-incomplete failures use only the bounded
  `complete_pre_output_failure()` path.

Try to reproduce old CE11/CE12. They must now fail safely.

## C2 re-review

Verify actual regex/classification:

- canonical-looking:
  `(?i)(?:\bEVENT\s+event_id\b|\b(?:event_id|request_id|signature|ts)\s*[:=])`
- namespace:
  `\b(?:EVT|REQ)-...\b`

Confirm:

- `event_id: EVT-T99 signature: ...` -> FAIL;
- `EVENT event_id=EVT-FABRICATED` -> FAIL;
- `event_id=EVT-T01 was observed` -> FAIL;
- benign `Four EVENT lines were observed...` -> ordinary extra / PASS;
- frozen fixture `Canonical EVENT` comment -> ordinary extra / PASS;
- EVT-/REQ- prose in evidence/timeline -> invalid provenance reference / FAIL;
- wrong/fabricated canonical claims cannot silently become extras.

The stricter namespace rule is intentional for evidence/timeline. Live L02 instructions
must later tell the model not to put ID-bearing prose there.

## Regression suite

Inspect the 88 tests rather than trusting the count.

Confirm the new C1/C2 counterexamples are present and the historical regression tests
were not weakened merely to turn the suite green.

## C3 boundary

Do not require C3 implementation in this re-review, but confirm it remains mandatory
before live Phase 0:

- frozen user-message/prompt digest;
- exact allowed request-body top-level fields, no stream;
- transport vs finish_reason=length attribution;
- raw-stdout replay reconciliation against the frozen final evaluation;
- evaluation-metadata digest;
- L02 verbatim EVENT-line instruction;
- L03 one-record-one-evidence/no-inventory instruction.

## Verdict

Finish with:

```text
PR #38 ROUND-3 C1/C2 REMEDIATION RE-REVIEW:
PASS
or
BLOCKED

Exact review head:
8f32027580df8a0a15ba2729c17b5bc2f2303306

Exact tested implementation head:
c1830ef2c60fbda61777461ffd20b3dd312a84ec

Revision integrity:
PASS / finding

C1 accounting/evaluation binding:
CLOSED / BLOCKED — ...

C2 L02 classifier:
CLOSED / BLOCKED — ...

88-test regression:
PASS / finding

New helper-level blockers:
NONE / details

C3 live-wrapper requirements still pending:
YES / NO

May design/freeze live Phase-0 wrapper:
YES / NO

May run live company Phase 0 now:
NO — wrapper/instructions/config/digests/reconciliation must be implemented and reviewed first
```

Commit only the targeted re-review report.
Do not modify helper/tests in this review.
