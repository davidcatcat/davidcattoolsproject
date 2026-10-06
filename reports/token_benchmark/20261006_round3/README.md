# Round 3 Phase 0 — offline format rules

This directory contains deterministic rules and self-tests only. There is no company
transport, Codex launcher, live results file, prompt constructor, or automatic execution.
Live Phase 0 requires independent implementation review and separate authorization.

Authority: the [amended plan](../../llmtool_token_savings_round3_format_compliance_plan_20261006.md)
and [implementation handoff](../../llmtool_token_savings_round3_format_probe_implementation_handoff_20261006.md).
The expected head quoted inside the historical handoff predates the user's latest
instruction; this implementation starts from the fetched `e2c4890f936292fdaa39e0103837580e5ec12fdb`.

Run from the canonical repository:

```sh
/usr/bin/python3 -B reports/token_benchmark/20261006_round3/test_format_probe.py
git diff --check
```

Tests use Python 3.9's standard library and existing llmtool A4/terminal validation.
They read the frozen Round-2 fixtures; counterexamples and fake stdout/Requests exist
only in memory. Socket creation and subprocess launch are blocked in the test runner.
No new benchmark fixture or gold is generated.

## Pure adapter boundaries

`format_probe.py` performs no file I/O. Its public boundaries are:

| Function/type | Inputs and purpose |
|---|---|
| `verify_fixture_digest` | Raw fixture bytes and lane; reject frozen-digest drift |
| `parse_l02_fixture` | Full raw fixture; all exact canonical EVENT objects, no gold |
| `evaluate_l02_without_gold` | `RawStdout` and parsed EVENT universe; exact subset validation |
| `parse_l03_fixture` | Full raw fixture; all exact RECORD objects, no gold |
| `build_l03_multimap` | Complete fixture records; exact pair to every matching record |
| `evaluate_l03_anchors_without_gold` | `RawStdout` and complete records; A4 parsing and structured anchors |
| `evaluate_l03_target_recall_after_freeze` | Frozen anchor result and a deferred grading-metadata loader |
| `RawStdout` | Immutable raw bytes; immediate SHA-256, then secret-marker scan |
| `observe_request_without_mutation` | Already-built Request-shaped object; returns frozen `RequestObservation`; checks only, never sends |
| `Phase0Accounting` | Pure fixed-order admission/recording state; accepts only typed RequestObservation and final FrozenEvaluation, no transport or retry |

L02 company output follows the existing `log-triage` JSON schema. Required coverage
uses the **entire** parsed EVENT universe, not a signature/gold whitelist. A company
array item must itself be an exact complete EVENT line to count. An `EVENT` token or
canonical field assignment inside malformed/partial/prose text is classified as an
invalid canonical-looking claim. Other unrelated text is recorded as an extra.
Evidence and timeline duplicates are checked separately; timeline must equal the
complete timestamp/source-line ordered sequence. Signature counts come from validated
evidence only. Extra items/signatures never enter counts or anchors.

L03 keeps the existing A4 parser unchanged. Its optional/null locator can parse but
cannot anchor. Mapping uses exact structured source+locator over all fixture records;
there is no whitespace/case normalization or free-text record-ID disambiguation.
Ambiguity and duplicate mapping fail. The frozen fixture's `INV-` namespace identifies
background records without consulting gold. Ambiguous inventory is classified as
ambiguous; a unique inventory citation is recorded in `inventory_evidence`, excluded
from usable anchors, and fails the usable-anchor contract under
`UNKNOWN_STRUCTURED_ANCHOR`. This denotes forbidden benchmark anchor eligibility,
not a claim that the background record is absent from the fixture.

One evidence item may support several semantic categories. Two evidence items mapping
to one record remain duplicate failures. Category prose is not semantically graded by
this helper, and a mechanical pass is not quality parity.

## Freeze before recall

The required order is:

```text
complete raw stdout bytes
→ RawStdout: SHA-256 original bytes first, secret scan second
→ A4 parsing and fixture-only structured mapping
→ immutable FrozenEvaluation
→ invoke grading metadata loader
→ intersect validated unique record IDs with seven frozen evaluation target IDs
```

`FrozenEvaluation.result_json` is immutable; `to_dict()` returns a detached copy.
Post-freeze evaluation returns a new result and retains the original stdout object
and anchor-result digest. It cannot repair the output/anchors or mutate accounting.
Replay likewise creates a new diagnostic result without changing an earlier verdict.

**An anchor-stage PASS is not an L03 Phase-0 PASS.** The caller must run the post-freeze
recall function and use its final verdict. Recall must be 7/7, anchors nonempty, and
unknown/ambiguous/duplicate counters zero; 3/7 fails. A later runner must pin and verify
the evaluation-metadata digest before calling this loader, without supplying metadata
to company prompts or the adapters.

Secret scanning is a conservative publication guard for synthetic output, not a
universal credential detector or permission to send sensitive material. It returns
marker categories, never matched secret values. `publication_allowed=False` blocks
publication even if the structural result passed. Nothing redacts/replaces bytes for
gate evaluation, and this offline helper writes no raw artifacts.

## Fixed accounting

Order is `L02-1, L03-1, L02-2, L03-2, L02-3, L03-3`. `begin_next()` admits or records
an explicit skipped slot; `mark_request_emitted()` accepts one frozen `RequestObservation`
at most. `complete_evaluation()` accepts only the lane-matching **final** `FrozenEvaluation`
(L03 must already be `phase0_l03_target_recall`) and derives failure reasons from that
immutable result; callers cannot supply or erase failure reasons. Pre-output transport or
model-incomplete failures use the separate bounded `complete_pre_output_failure()` path. A pre-construction
failure records no request/proof/usage. No retry method/state exists.

An L02 failure skips later L02 slots while L03 continues; an L03 failure skips later
L03 while L02 continues. Missing usage for an emitted/potentially consumed request
halts all remaining slots with `COMPANY_TOKEN_ACCOUNTING_UNKNOWN`. Skipped usage is
`None`, never an invented zero. Known subtotal stays available; complete total is
unknown after an unaccounted request.

Each admission uses known cumulative usage plus 35,000 (L02) or 59,000 (L03), against
300,000. Actual provider-reported usage is retained even if unexpectedly above an
admission bound/ceiling, never clamped to make the experiment pass. The admission
bounds are experiment rules, not guarantees enforced on an upstream provider.
The overall gate requires all six recorded trials to pass with request observations,
known total, and the ceiling satisfied.

Failure precedence is frozen in `FAILURE_PRECEDENCE`; all secondary format diagnostics
are retained. Budget/accounting/not-started states are separate experiment statuses,
not fabricated model or parser errors.

The future authorized live wrapper remains responsible for exact frozen company
instructions/config/digests, actual unchanged outgoing Request observation on every
emitted request, transport/length attribution before adapter evaluation, provider
usage/latency metadata, and secret-scanned verbatim stdout retention. Offline fake
Request checks prove helper behavior only; they do not prove a live Request was sent
with thinking disabled. No live runner is included here.


## Independent-review remediation status

The independent implementation review found two before-live blockers and both have now
been remediated in the helper/tests, pending a fresh Python 3.9 rerun:

- **C1:** accounting is now cryptographically/procedurally tied to final frozen adapter
  verdicts. L03 anchor-stage results are rejected; only post-freeze 7/7 recall results may
  complete an L03 logical trial. Request proof is a frozen `RequestObservation`, not an
  arbitrary dict.
- **C2:** L02 canonical-looking detection now keys on canonical field assignments
  (`event_id/request_id/signature/ts` with `:` or `=`) or `EVENT event_id`, not the
  standalone word EVENT. Fixture-namespace EVT-/REQ- references in evidence/timeline
  prose are treated as invalid provenance references rather than harmless extras.

New counterexamples cover colon-form fabricated claims, benign EVENT prose, fixture
comment prose, namespace-ID prose, anchor-stage/accounting seam, wrong-lane evaluations,
failed-evaluation override attempts, forged dict request proof, and the 3/7 accounting
false-PASS case.

The historical offline validation report remains valid only for its exact tested head
`29bd0dd92db203532f119a3b4c77be476454ab87`. The remediated implementation must be
retested before any new PASS claim or live wrapper authorization.
