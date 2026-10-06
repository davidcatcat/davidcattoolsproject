# llmtool Round 3 — targeted C3-B B1 remediation re-review handoff (2026-10-06)

## Purpose

Perform a short independent re-review of the single B1 manifest-pin blocker after
remediation. All previously PASS/CLOSED C3-B areas remain background regression context.

Review only. No live Company call. No Codex call. No implementation changes.

## Revision

Repository:
`davidcatcat/davidcattoolsproject`

PR:
`#38`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Current review head:
`e6425a9ff507c72f67142014aaeed9c564e65dc2`

Exact tested B1 implementation head:
`3325f75e954a4099e31c5936412307320974cb4f`

The only delta from tested head to current review head must be:
`reports/llmtool_token_savings_round3_c3_b_b1_remediation_validation_20261006.md`.

## New exact manifest pin

`37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404`

Historical pins must not authorize this remediated wrapper:
- `0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276`
- `81ad71b05c31c2cf858f29ec87027818252a40041edf4f0b363f133d22c09ece`

## Re-review B1 directly

Inspect:
- `reports/token_benchmark/20261006_round3/live_wrapper.py`
- `reports/token_benchmark/20261006_round3/test_live_wrapper.py`
- `reports/token_benchmark/20261006_round3/phase0_preregistration.json`
- B1 remediation validation report
- prior C3-B BLOCKED report

Verify:

1. `Phase0Wrapper` no longer accepts arbitrary registration dict authority.
2. Constructor takes the expected manifest SHA and internally calls
   `load_preregistration(expected_manifest_sha256)`.
3. Wrong pin fails before strict JSON decode.
4. Correct pin loads the reviewed manifest and full artifact verification.
5. Inspection of registration returns a detached copy and cannot be fed back as
   execution authority.
6. Every `run_next()` reloads/reverifies the pinned manifest before even deciding
   a later trial is NOT_STARTED.
7. `admitted_open()` receives the pin, not a registration dict.
8. `admitted_open()` reloads/reverifies the pinned manifest before Request admission.
9. It re-pins after the non-mutating Request observer and before
   `mark_request_emitted()` / original opener delegation.
10. Manifest, instruction, or pinned runtime artifact drift before request construction
    fails before `invoke_cli`.
11. Manifest, instruction, or runtime artifact drift between run verification and
    opener admission fails with:
    - original opener/delegate count 0;
    - request_emitted False;
    - wrapper halted.
12. Drift injected during Request observation is caught by the final re-pin before
    emission/delegation.
13. The old B1 bypass is explicitly reproduced:
    - drift instruction;
    - recompute self-consistent instruction/user-message/request-body hashes;
    - show `verify_registration(forged)` by itself can accept self-consistency;
    - show sealed runner with reviewed pin rejects forged manifest before decode.
14. `admitted_open(..., registration_dict, ...)` is rejected as authority.

## Expanded runtime pins

Verify manifest now additionally pins unchanged production files:
- errors.py
- diagnostics.py
- terminal.py
- providers/base.py
- providers/__init__.py

and retains all previous runtime/artifact pins.

No production file is modified.

## Frozen Request identity

Must remain exactly:

L02 user message:
`e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac`

L02 request body:
`cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`

L03 user message:
`3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2`

L03 request body:
`6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0`

## Offline regressions

Independently inspect/re-run offline only:

- format helper: 88/88 PASS;
- wrapper: 94/94 PASS;
- git diff --check PASS;
- zero-network freeze PASS.

Company live calls must remain 0.

## Scope notes to preserve for live handoff

These are conditions, not blockers if correctly documented:

- live raw output directory must be outside tracked repo until secret scan passes;
- if a post-emission integrity halt leaves a STARTED emitted trial, report accounting as
  INCOMPLETE / UNKNOWN and STOP; never claim complete token total;
- this wrapper pin is an experiment admission mechanism, not a security boundary against
  arbitrary hostile Python process mutation.

## Required verdict

Finish with:

```text
PR #38 ROUND-3 C3-B B1 TARGETED RE-REVIEW:
PASS
or
BLOCKED

Exact review head:
e6425a9ff507c72f67142014aaeed9c564e65dc2

Exact tested implementation head:
3325f75e954a4099e31c5936412307320974cb4f

Pinned preregistration SHA256:
37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404

Revision integrity:
PASS / finding

Arbitrary registration authority:
CLOSED / BLOCKED

Constructor pin-before-decode:
PASS / BLOCKED

Every-run re-pin:
PASS / BLOCKED

Pre-opener / observer TOCTOU drift:
PASS / BLOCKED

Self-consistent forged manifest bypass:
CLOSED / BLOCKED

Expanded runtime artifact pins:
PASS / BLOCKED

Frozen Request/user-message identity:
PASS / BLOCKED

Offline regressions:
88/88 helper
94/94 wrapper

New before-live blockers:
NONE / details

May prepare exact live Phase-0 execution handoff:
YES / NO

May run live Company Phase 0 now:
NO — exact live execution handoff still must pin
37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404
and explicitly authorize the live run
```

Commit only the targeted re-review report.
Do not modify wrapper/tests/preregistration.
