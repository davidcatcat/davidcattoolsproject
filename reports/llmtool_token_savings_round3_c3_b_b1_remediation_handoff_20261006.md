# llmtool Round 3 — C3-B B1 manifest-pin sealing remediation handoff (2026-10-06)

## Goal

Close the only C3-B before-live blocker:

> the reviewed preregistration SHA must be enforced by the executable wrapper object,
> not merely by a future handoff convention.

No live Company request is authorized.

## Canonical repository

`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`

Branch:
`bench/llmtool-token-savings-round3-format-compliance-20261006`

Expected remote head before remediation:
`07ade3406d0eab3f032745ad688e1b3955c1833a`

Never use `/private/tmp/*` worktrees/clones.

## Read first

- `reports/llmtool_token_savings_round3_c3_b_live_wrapper_review_20261006.md`
- `reports/token_benchmark/20261006_round3/live_wrapper.py`
- `reports/token_benchmark/20261006_round3/test_live_wrapper.py`
- `reports/token_benchmark/20261006_round3/phase0_preregistration.json`

## B1 problem

Current:

```python
Phase0Wrapper(registration_dict)
```

accepts any self-consistent dict. `verify_registration()` verifies dict vs current disk,
not dict vs independently reviewed manifest bytes.

Therefore an instruction/artifact drift plus recomputed self-consistent dict can bypass
the reviewed pin.

`load_preregistration(expected_sha256)` itself is correct, but it is optional.

## Required sealed API

Replace arbitrary-dict construction with a pinned-manifest constructor.

Preferred:

```python
runner = Phase0Wrapper(
    expected_manifest_sha256="<PIN>"
)
```

or:

```python
runner = Phase0Wrapper.from_pinned("<PIN>")
```

The class must internally call:

`load_preregistration(expected_manifest_sha256)`

and store:
- expected manifest SHA;
- loaded verified registration.

Plain dict construction must fail.

Do not retain a public alternate constructor that accepts an arbitrary registration dict.

## Reverification during execution

Before **every** started request path:

- `run_next()` must reread `phase0_preregistration.json`;
- verify raw file SHA against the stored expected pin before decode;
- decode strictly;
- run full `verify_registration()`;
- use that freshly verified registration for request/user-message admission.

Before `admitted_open()` delegates to the original opener:
- repeat/reconfirm the same pinned manifest identity or pass only an immutable verified
  object whose provenance is tied to the pin from the same run_next call;
- instruction/artifact drift between runner construction and network admission must fail
  before original opener.

No network call on pin/artifact drift.

## Mandatory bypass tests

Add tests for:

1. `Phase0Wrapper(dict)` rejected.
2. Correct pin creates runner.
3. Wrong pin rejected before manifest decode.
4. Manifest bytes changed after runner construction -> next `run_next` fails before
   request delegation.
5. Instruction changed after runner construction -> next `run_next` fails before
   delegation.
6. Reproduce C3-B B1:
   - simulate drifted instruction;
   - recompute a fully self-consistent forged registration dict;
   - direct `verify_registration(forged)` may demonstrate the old weakness;
   - sealed runner must still reject because reviewed manifest pin is unchanged.
7. Artifact changed between run_next verification and admitted_open -> fail before
   original opener.
8. Correct pinned runner still executes fake-success/offline provider-boundary tests.

## Artifact-pin hardening

During this re-freeze, add all production modules that materially affect the observed
provider/error/request path to the manifest artifact set, including at minimum:

- `tools/llmtool/llmtool/errors.py`
- `tools/llmtool/llmtool/diagnostics.py`
- `tools/llmtool/llmtool/terminal.py`
- `tools/llmtool/llmtool/providers/base.py`
- `tools/llmtool/llmtool/providers/__init__.py`

Retain all existing pinned runtime files.

If adding these pins changes Request/user-message bytes, STOP and report unexpected drift.
Normally it must not.

## Raw-output handoff condition

Do not change successful raw-output semantics in this remediation.

For future live execution, the output directory must be outside the tracked repository
until secret-marker scan passes.

B1 remediation tests may use temporary directories only.

## Integrity-halt reporting condition

Do not redesign accounting in this B1 remediation.

However document that if a post-request wrapper-integrity failure leaves an emitted
STARTED trial, the live report must mark accounting as incomplete/unknown and execution
must stop. It must never be presented as a valid complete token total.

## Regression requirements

Run canonical Mac Python 3.9:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py
git diff --check
```

Requirements:
- helper remains 88/88 PASS;
- wrapper suite all PASS;
- zero Company live calls;
- zero Codex calls;
- no production/runtime/skill changes.

## Re-freeze preregistration

Because wrapper/tests/artifact list change, regenerate preregistration with zero-network
real CLI/provider freeze.

Previous manifest SHA:
`0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276`

Record the new exact manifest SHA.

These must remain unchanged unless a STOP-worthy drift is explained:

L02 user message:
`e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac`

L02 Request body:
`cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f`

L03 user message:
`3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2`

L03 Request body:
`6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0`

## Fresh validation report

Create:

`reports/llmtool_token_savings_round3_c3_b_b1_remediation_validation_20261006.md`

Include:
- exact tested head;
- Python version;
- helper test result;
- wrapper test result/count;
- dict-constructor rejection;
- correct/wrong pin tests;
- mid-run manifest drift;
- instruction/artifact TOCTOU drift before opener;
- self-consistent forged-dict bypass regression;
- expanded runtime artifact pin list;
- old/new preregistration SHA;
- request/user-message hash stability;
- zero-network proof;
- Company live calls 0;
- Codex calls 0;
- production/runtime/skill changes NONE;
- readiness for targeted C3-B re-review.

## Stop point

Commit/push:
- B1 wrapper remediation;
- tests;
- regenerated preregistration;
- validation report.

Then STOP.

Do not run live Company Phase 0.

A targeted independent C3-B re-review of the new exact wrapper + manifest SHA is still
required before preparing any live execution handoff.
