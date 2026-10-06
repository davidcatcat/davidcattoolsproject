# Round 3 offline implementation review remediation (2026-10-06)

Status: **C1/C2 REMEDIATED / FRESH OFFLINE RETEST PENDING**

Independent review source:
`reports/llmtool_token_savings_round3_format_probe_implementation_review_20261006.md`

Reviewed implementation head:
`a662b6ed9efdd7f7c9fc267fe929f21eb28be626`

## C1 — accounting/adaptor verdict binding

Fixed in `format_probe.py`:

- added frozen `RequestObservation`;
- `observe_request_without_mutation()` returns the typed observation;
- `mark_request_emitted()` accepts only `RequestObservation`, rejecting arbitrary dicts;
- removed the generic caller-supplied-reasons completion seam;
- added `complete_evaluation()`:
  - requires a real `FrozenEvaluation`;
  - requires lane == logical trial lane;
  - L02 requires final `canonical_coverage` stage;
  - L03 requires final `phase0_l03_target_recall` stage;
  - derives primary/secondary reasons from the frozen result;
  - records evaluation and raw-stdout digests;
- added `complete_pre_output_failure()` only for
  `TRANSPORT_OR_REQUEST_FAILURE` / `MODEL_OUTPUT_INCOMPLETE`.

Therefore an L03 3/7 anchor-stage result cannot be recorded as PASS by supplying an
empty failure-reason list.

New counterexamples cover:
- anchor-stage L03 result rejected by accounting;
- wrong-lane final evaluation rejected;
- failed final evaluation cannot be overridden;
- dict-shaped request proof rejected;
- repeated 3/7 L03 results cannot produce `gate_passed=True`.

## C2 — L02 canonical-looking classification

Fixed in `format_probe.py`:

- standalone word `EVENT` is no longer sufficient to classify prose as a malformed
  canonical claim;
- canonical-looking detection now recognizes canonical field assignments using `:` or
  `=`, case-insensitive, and `EVENT event_id`;
- EVT-/REQ- namespace references inside otherwise-prose evidence/timeline items are
  separately diagnosed and fail as invalid provenance references;
- unrelated prose remains an inert extra.

New counterexamples cover:
- colon-form fabricated canonical fields -> FAIL;
- benign prose containing the word EVENT -> PASS as extra;
- the fixture's own "Canonical EVENT" comment -> PASS as extra;
- fabricated/contradictory EVT-/REQ- prose -> FAIL.

## Test-state boundary

Historical validation:
- tested head `29bd0dd92db203532f119a3b4c77be476454ab87`;
- 79/79 PASS.

After C1/C2 remediation:
- static test count: **88**;
- fresh Python 3.9 execution: **PENDING**.

Do not carry the old 79/79 PASS forward to the new implementation.

No Company or Codex call has run during remediation.
No production/runtime/skill/fixture/gold file was changed.

## C3 — still future wrapper requirements

Still mandatory before live Phase 0:
- frozen user-message/prompt digest;
- no stream / no unexpected request body fields;
- explicit transport vs finish_reason=length attribution;
- raw-stdout replay reconciliation against final frozen evaluation;
- evaluation-metadata digest verification;
- L02 live instruction requiring canonical EVENT lines verbatim, one line per item, no
  inline annotations;
- frozen L03 nonduplicating/no-inventory instruction.

Live Phase 0 remains unauthorized.
