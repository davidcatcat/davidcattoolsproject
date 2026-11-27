# A13 OSR Source-to-Evidence Crosswalk (4 rows)

Verification is at pinned main `4ffd749a95b313591c383b13e1148d80c9704cca`
(verified by the advisor). Source rows: #396, comment 6552370022 (at
historical main `e1387d8`) and #2793, Coordinator comment 7210501920. This
table covers only the four OSR findings below. It is **not** a full mapping
of the 43 binding points or the 40 recheck rows; no other point ID, count or
row linkage is asserted here. Field and count limits:
[a13-inventory-count-reconciliation.md](a13-inventory-count-reconciliation.md).

| source_id | evidence_at_pinned_sha | consumer | disposition | target_milestone | duplicate_of |
|---|---|---|---|---|---|
| OSR-09 | [a13-tool-lookup-boundary.md](a13-tool-lookup-boundary.md), PR #2850 | A13.4/A13.5 handoff | partial — lsof has guarded candidates; Git and gh lookup constraints remain | A13.4/A13.5 (handoff retained) | unverified — no historical matching-row linkage |
| OSR-10 | [a13-doctor-readiness-boundary.md](a13-doctor-readiness-boundary.md), PR #2849 | A13.4/A13.5 handoff | partial — doctor overall and strict addressed; live per-route readiness not proven | A13.4/A13.5 (handoff retained) | unverified — no historical matching-row linkage |
| OSR-17 | [a13-state-location-boundary.md](a13-state-location-boundary.md), PR #2848 | A13.1 project state; A13.3 isolation | partial — TaskStore and PM runtime directory defaults differ from diagnostics | A13.1 (project state); A13.3 (isolation) | unverified — no historical matching-row linkage |
| OSR-26 | [a13-git-layout-boundary.md](a13-git-layout-boundary.md) | A13.1 project registration verification | partial — D6 create and review registration impose the common-dir basename restriction; remaining guarded layout limit | A13.1 (project registration verification) | unverified — no historical matching-row linkage |

## Evidence limits

- All four rows are **partial**; no row claims completion.
- The four rows do not map all 43 binding points (9 trust anchors + 3
  deny-list points) or all 40 recheck rows; they are not added to or
  collapsed into those totals.
- `duplicate_of` is unverified for every row: no historical matching-row
  linkage is provided, so no point IDs are assigned or inferred and no
  counts are added.
- The linked evidence notes are the only evidence; references name files
  and PRs without line anchors.

## Gates

- Exact-head hosted docs check: this file is verified against the exact
  commit it accompanies, at pinned main
  `4ffd749a95b313591c383b13e1148d80c9704cca`.
- Formal review by codex-reviewer.

Docs only; no code, tests or state changes.
