# Round 3 C3-B B1 — sealed manifest execution validation (2026-10-06)

B1 implementation and offline checks PASS. Targeted independent C3-B re-review
is still required. **Company Phase 0 is NOT STARTED.**

## Contract and revision

- Authoritative [B1 handoff](llmtool_token_savings_round3_c3_b_b1_remediation_handoff_20261006.md)
  and historical [C3-B review](llmtool_token_savings_round3_c3_b_live_wrapper_review_20261006.md).
- Canonical actual Mac repository:
  `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Branch: `bench/llmtool-token-savings-round3-format-compliance-20261006`.
- GitHub: `davidcatcat/davidcattoolsproject`, existing Draft PR #38.
- Fetched starting head: `af2be76807ea7559c4ffaea1f6d25dcd6e99f722`.
- Exact tested implementation head: `3325f75e954a4099e31c5936412307320974cb4f`.
- Python: `/usr/bin/python3`, **3.9.6**.

The user's expected remote head supersedes the earlier starting SHA in the handoff.
Safe synchronization was fast-forward only. All nine existing unrelated untracked
benchmark/reasoning/case artifacts were preserved. Only wrapper, wrapper tests,
preregistration and this new report belong to B1; the historical review is unchanged.
This report's follow-up commit changes no tested implementation bytes.

## Sealed execution path

The constructor now accepts only:

```python
Phase0Wrapper(expected_manifest_sha256="<independently reviewed manifest SHA>")
```

There is no dict constructor or public alternate constructor. `load_preregistration`
requires a lowercase 64-character SHA string, reads the manifest's original bytes,
compares the supplied pin **before strict JSON decoding**, and runs full artifact,
instruction, fixture and configuration verification. Constructor stores the pin and
verified registration. The public pin property is read-only; the registration
inspection property returns a detached copy and cannot be assigned as execution input.

Every `run_next` reloads the manifest through the stored pin before starting a trial,
including calls that will later become NOT_STARTED. The freshly loaded registration
supplies prompt construction. `admitted_open` now also requires the pin, not a dict:
it reloads/verifies the manifest and artifacts before Request validation, and repeats
that verification after the reviewed non-mutating observer, immediately before
marking emission/delegating. Manifest, instruction and artifact drift injected at
these boundaries stops before delegation and leaves `request_emitted=False`.

This is scoped experiment admission revalidation. It does not introduce a generic
authorization/signal framework or an OS-wide filesystem lock. Future live handoff
must supply the independently reviewed pin; computing a new pin for unreviewed
artifacts does not grant authorization. The script still exposes only zero-network
`--freeze`, with no live executable entry command.

## B1 reproduction and tests

Before changing the wrapper, new dict-rejection and wrong-pin constructor regression
tests failed: the old constructor accepted both a dict and a wrong SHA string without
calling the pinned loader. The preferred keyword constructor did not exist on that
base. This confirms the missing execution boundary described by Claude.

`test_self_consistent_forged_manifest_blocked` independently reconstructs the bypass
inside the permanent suite:

1. Patch only in-memory reads of `instructions/L03.txt` with an unreviewed suffix.
2. Use the **real zero-network CLI/client dry run** to recompute instruction,
   user-message and full Request-body SHA values.
3. Put those values in a fully self-consistent forged registration; demonstrate that
   `verify_registration(forged)` accepts it.
4. Simulate a manifest file containing that forged registration while retaining the
   reviewed original pin. The sealed constructor raises `WrapperIntegrityFailure`
   before manifest JSON decode.

No real instruction, fixture, gold or production file is changed by these simulations.
Their intentionally different negative-test Request hashes do not replace frozen
normal Request identities.

The wrapper suite grows from **77 to 94 tests**, retaining prior C3-1, accounting,
replay, gold isolation and Request-validation assertions. New coverage includes:

- Dict construction/admission rejection, correct pin, wrong pin before decode,
  actual committed manifest versus fresh real Request freeze.
- Public pin immutability and detached registration inspection.
- Manifest, both instructions and runtime artifact drift after construction:
  fail before `invoke_cli`/Request construction, no new trial or output.
- Drift after successful run verification but before opener admission, including
  manifest, instruction and runtime artifact: zero delegation and no emission.
- Drift during Request observation, caught by final re-pin before emission.
- Manifest reloading on a later runner call after consumed usage becomes unknown.
- The fully self-consistent forged-manifest bypass and expanded artifact pin set.

Existing fake-success and provider-boundary tests now construct from a pin to the
actual manifest. The former altered-dict runner fault test instead corrupts a real
Request's bytes after constructing a correctly pinned runner; all original halt,
no-emission, no-output and subsequent-halt assertions remain.

## Expanded runtime pins and unchanged behavior

Added artifact pins, without modifying these production files:

```text
tools/llmtool/llmtool/errors.py
tools/llmtool/llmtool/diagnostics.py
tools/llmtool/llmtool/terminal.py
tools/llmtool/llmtool/providers/base.py
tools/llmtool/llmtool/providers/__init__.py
```

All eight previous runtime pins and four wrapper/helper/test pins remain. The manifest
therefore contains 13 runtime and four benchmark artifact digests. Relevant skill
digests remain in lane freeze facts.

Unchanged and regression-tested: INVALID/TOO_LARGE response failures map to
`TRANSPORT_OR_REQUEST_FAILURE`; length stays `MODEL_OUTPUT_INCOMPLETE`; unknown
consumed usage stops all later trials; no retry/fallback; six-trial order; 300k ceiling,
35k/59k bounds; per-fixture early stop; replay and gold isolation. Successful raw-output
creation and scan semantics are unchanged.

## Frozen digests

| Artifact | SHA-256 |
| --- | --- |
| Old preregistration (historical only) | `0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276` |
| New preregistration | `37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404` |
| `live_wrapper.py` | `5cd51bbf0f44d4c8d8fe652d6dabc644155b54bc33c08d47e056d16266ccdeaa` |
| `test_live_wrapper.py` | `3243c348d582ddda7e843290b5e89948d7d172af279a6d6c5d54d8a80c7be2be` |
| L02 user message | `e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac` |
| L02 Request body | `cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f` |
| L03 user message | `3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2` |
| L03 Request body | `6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0` |

Before writing the regenerated manifest, complete lane freeze facts and all non-artifact
values were required to equal the old manifest. Only wrapper/test artifact digests and
the five explicitly requested new runtime pins changed. No prompt/instruction semantics
or Request identities changed. After commit, each real CLI/client freeze was repeated
and matched the manifest exactly: **L02 one Request captured; L03 one Request captured;
zero network calls; zero socket opens**. Original opener is never called in the dry-run
path. The Company endpoint was not accessed.

## Exact-head offline validation

Executed from the canonical repository on `3325f75e954a4099e31c5936412307320974cb4f`:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_live_wrapper.py
git diff --check
```

| Check | PASS | FAIL | ERROR | SKIP | Exit | Suite seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| format_probe | 88 / 88 | 0 | 0 | 0 | 0 | 0.115 |
| live_wrapper | 94 / 94 | 0 | 0 | 0 | 0 | 5.725 |
| `git diff --check` | PASS | 0 | 0 | 0 | 0 | n/a |

Tests prohibit socket construction, DNS, connection, original network opener and
subprocess escape. Real client responses are in-memory fakes. The unchanged original
opener/network zero-call tests also PASS. No real live results were created.

## Mandatory future live handoff conditions

**Raw output location:** the caller must choose `output_directory` outside the tracked
repository, such as a temporary private runtime directory (not a clone/worktree), or
an explicitly excluded private scratchpad. Unscanned Company bytes must not be written
into tracked `reports/`. Only after the original-byte secret-marker scan PASS may a
safe summary/report be copied into `reports/`. This is a future handoff condition;
raw-output implementation semantics were not changed in B1.

**Integrity halt accounting:** if a Request has been emitted and a later wrapper
integrity failure leaves its trial STARTED, execution must STOP and the live report
must state **ACCOUNTING INCOMPLETE / UNKNOWN**. That state is not valid complete
accounting, even if `known_cumulative` or the unfinalized state exposes a prior numeric
subtotal. Never claim a complete company token total from it. No accounting state
machine redesign is part of this remediation.

Pending/NOT-TESTED: targeted independent C3-B re-review, actual Company inference,
Phase-0 live compliance/usage, Codex benchmark and production full canonical suite
(no production changes; only the two required offline suites apply here). No merge,
live authorization, quality parity or savings claim is established by these tests.

```text
Exact tested head: 3325f75e954a4099e31c5936412307320974cb4f
Python: 3.9.6
format_probe: 88 / 88 PASS
live_wrapper: 94 / 94 PASS
FAIL: 0
ERROR: 0
SKIP: 0
git diff --check: PASS
Plain dict constructor rejected: PASS
Correct manifest pin: PASS
Wrong pin before decode: PASS
Mid-run manifest drift: PASS
Mid-run instruction drift: PASS
TOCTOU drift before opener: PASS
Self-consistent forged registration bypass: BLOCKED / PASS
Expanded runtime artifact pins: PASS
Old preregistration SHA: 0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276
New preregistration SHA: 37f48c1758e170f2bfdf7aef956a2dae4011458008b0911710591e8cd5189404
L02 user_message SHA: e0194460028b4d7a08b6607141285da5352f0c71a4cff0d8a4f57fe2686a22ac
L02 request_body SHA: cc3c79f8e9a55356b1f8c1a464ecef5dcf4a45646a1eb7c07cf59315ec4e360f
L03 user_message SHA: 3b94f0d8ba02b4e1d206beeb184a2d50553d2e1d48efcac87a069030513645e2
L03 request_body SHA: 6c1f7b77db218ae9962a78bae46190cab38c0c5277a4b952a967bc8eaac7cdf0
Request identity changed: NO
Zero-network freeze: PASS
Network calls: 0 (provider HTTP)
Socket opens: 0 (offline execution)
Company live calls: 0
Codex calls: 0
Production/runtime/skill changes: NONE
Ready for targeted C3-B re-review: YES
Company Phase 0: NOT STARTED
```
