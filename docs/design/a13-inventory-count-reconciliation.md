# A13.0 Count Reconciliation and Missing Item-Level Mapping

One-page note for the PM: which verified counts exist, what counting units
they use, and why the aggregate sources still do not establish the missing
item-level mapping. Reference main is pinned at
`a402c1483974ee21b055f3d6d56baac8f0487e6e`.

## Verified aggregate counts

| Count | Source | Counting unit |
|---|---|---|
| 43 binding points: 9 trust anchors + 3 deny-list points | #396, comment 6552370022, at historical main `e1387d8` | inventory entries (binding points) |
| 27 deduplicated OSR findings: P0 5, P1 10, P2 12 | #396, comment 6552370022, at historical main `e1387d8` | OSR findings, deduplicated |
| 40 recheck rows: 18 resolved, 8 partial, 13 still hard-coded, 1 gone | #2793, issue body | recheck rows |
| 2 dry-run findings | #1230, reported separately in #2793 | dry-run findings |
| test-mode requirement (local / github / remote modes) | #2793, comment 7208490147; Coordinator comment 7210501920; recorded in `docs/design/a13-test-execution-modes.md` | execution-mode requirements |

The dry-run findings are reported separately in #2793 under the source
comment #396, comment 6723140835. They are a distinct counting unit and must
not be added to the 40 recheck rows or the 27 OSR findings.

These sources are different counting units (inventory entries, findings,
recheck rows, requirements). Overlapping observations must not be counted
twice: no sum of these numbers is a valid total, and none is treated as
one.

## What the sources do and do not establish

- The aggregate sources above do **not** establish a one-to-one mapping
  between any two counting units.
- They do **not** identify which rows are unmapped.
- The missing row-level mapping is therefore **unverified**. No IDs, counts
  or mappings beyond those listed above are inferred from the aggregates.

## Test-mode overlap note

The later test-mode requirement (#2793 comment 7208490147) is recorded in
`docs/design/a13-test-execution-modes.md` (execution modes, evidence binding;
`tools/llmtool/llmtool/test_profile.py`, `TestProfile.kind_for`,
`default_profile` is the pre-existing runner-kind boundary that note treats
as separate). That requirement may overlap the historical remote-execution
finding OSR-27. This note does **not** add OSR-27 to the 27 deduplicated OSR
findings or to any other total; it flags the possible overlap so it is not
counted twice once the row-level mapping exists.

## Minimum fields to complete the mapping

Each row of the item-level crosswalk needs:

| Field | Meaning |
|---|---|
| `source_id` | originating item ID, e.g. an OSR finding ID |
| `evidence_at_pinned_sha` | current evidence, verified at the pinned SHA |
| `consumer` | who consumes the item |
| `disposition` | how the item is handled |
| `target_milestone` | milestone the item lands in |
| `duplicate_of` | the canonical item if this row is a duplicate |

`duplicate_of` is how overlapping observations are collapsed to one count;
unfilled rows stay unverified until the PM completes them. No unprovided ID
or count is inferred here.

## Gates

- Exact-head hosted docs check: this file is verified against the exact
  commit it accompanies, at pinned main
  `a402c1483974ee21b055f3d6d56baac8f0487e6e`.
- Formal review by codex-reviewer.

Out of scope: code, settings, CI and external repository changes. This note
is docs only and records no A13.0 completion claim.
