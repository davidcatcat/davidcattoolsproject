# Round 3 C1/C2 retest failure remediation follow-up (2026-10-06)

Status: **RETEST FAILURE ROOT CAUSE FIXED / SECOND FRESH RETEST REQUIRED**

Failed retest source:
`reports/llmtool_token_savings_round3_format_probe_offline_retest_20261006.md`

Failed exact tested head:
`afcf35cddea86ad3ffb53d4383e7f298df2115ef`

Observed result:
- 88 test methods executed;
- 83 methods successful;
- 3 methods had failure entries;
- 2 methods had error entries;
- suite exit 1.

## Root cause 1 — C2 regex escaping

The remediated raw regex strings accidentally contained doubled backslashes:

- `r'\\b...'`
- `r'\\s...'`

Therefore the regex engine looked for literal backslashes rather than word-boundary /
whitespace operators.

Consequences reproduced by the failed suite:
- colon-form canonical fields were accepted as extras;
- partial/malformed EVENT claims were not classified;
- EVT-/REQ- namespace prose was silently accepted.

Fix:
- restore actual raw-regex operators `\b` and `\s`.

Sanity expectations after fix:
- `event_id: EVT-T99 signature: ...` => canonical-looking / FAIL;
- `EVENT event_id=EVT-FABRICATED` => canonical-looking / FAIL;
- `Four EVENT lines were observed...` => ordinary extra;
- fixture `Canonical EVENT` prose => ordinary extra;
- EVT-/REQ- namespace prose => invalid provenance reference, not harmless extra.

## Root cause 2 — stale typed-API test expectation

After C1 changed request proof to frozen `RequestObservation`, passing a plain dict is a
type contract violation.

The old regression test still expected `ValueError`; the remediated API intentionally
raises `TypeError`.

Fix:
- update the stale old test expectation to `TypeError`.

The newer independent-review counterexample already expected TypeError and had passed.

## Boundary

This follow-up does not claim the full suite passes.

A second canonical Python 3.9 run of all 88 tests is required.

Company live calls: 0.
Codex calls: 0.
Production/runtime/skill/fixture/gold changes: none.

Live Phase 0 remains unauthorized.
