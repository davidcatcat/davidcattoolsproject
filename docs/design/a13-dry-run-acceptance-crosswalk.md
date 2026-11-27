# A13 Dry-Run Acceptance Crosswalk: Blocker to First Positive Check

Status: **documentation handoff only.** Both rows below are **pending**.
This crosswalk records no positive acceptance claim; no fixture was accessed,
no new tests were added, and no external writes, gate changes, or releases
occurred.

**Pinned main:** `be913e781e8194fdd36e256d76e021f24a1454cb`
(this crosswalk snapshot only). The historical run revision it describes is
distinct and remains as recorded in the baseline: llmtool main `f936b567`,
fixture `594c63a8`. Do not conflate the two.

## Sources

- Historical report: #396, comment 6723140835 (the dry-run evidence base).
- Coordinator review: #2793, comment 7210501920 (test-mode requirement and
  evidence binding).
- Linked notes in this directory:
  - [A13 external-repo dry-run baseline](a13-external-repo-dry-run-baseline.md) —
    historical run facts and the two root blockers.
  - [A13 test execution modes](a13-test-execution-modes.md) — `local` /
    `github` / `remote` modes and the conceptual evidence binding.
  - [A13 inventory count reconciliation](a13-inventory-count-reconciliation.md)
    — counting units and the minimum fields a crosswalk row must carry.

## Blocker crosswalk

| ID | Source | Failed observation (historical) | Consumer | Disposition | Milestone | Next observable evidence (pending) |
|---|---|---|---|---|---|---|
| A13.1 | #396, comment 6723140835 | Repo detection only partly worked and the PM Task was recorded but not scoped to the repo, so no repo-bound trust was established (downstream `D6_ORIGIN_INVALID` and `ASSIGNMENT_D6_NOT_ACTIVE` refusals are the cascade of this gap, not separate blockers) | PM; downstream origin/assignment checks | Pending | A13.1 — registration and trust | An authorized fixture repository resolves to the intended project, and its task and D6 are bound there: a rerun reports repo detection complete and the task/D6 checks bound to that repo, instead of partial detection and unscoped recording |
| A13.2 | #396, comment 6723140835; #2793, comment 7210501920 | llmtool tests did not run (`ADMISSION_DENIED`, `RECEIPT_HEAD_UNRECORDED`) and no fixture CI workflows existed, so no project-bound test/CI evidence could be produced per the modes note | PM; A13.2 implementers | Pending | A13.2 — project-bound test/CI evidence | A receipt for the intended project, at the exact commit, for the selected mode, with the selected tests and their outcome; evidence is accepted only when it matches all of those bindings and is never downgraded or substituted |

### A13.2 — execution mode (all alternatives carry the same evidence binding)

`local`, `github`, and `remote` are alternatives for producing the A13.2
receipt. Whichever is selected, the receipt must be bound to, and accepted
only when it matches, all of: project or repository identity, the exact
commit covered, the selected mode recorded at check time, and the configured
test selection with each test's outcome. No mode may be inferred from failure
or environment, and there is no silent fallback; if the selected mode cannot
produce valid evidence for a requested check, the check fails.

### A13.2 — GitHub pre-PR approach (open)

The pre-PR selection remains open and belongs to A13.2, per
[a13-test-execution-modes.md](a13-test-execution-modes.md). The exact-head
question the PM must settle: **does the chosen GitHub setup attest the pushed
head before the PR exists — a branch CI run over the committed push itself —
or is it PR-trigger-only, whose first run is tied to the PR and may check out
a different ref than the pushed head, requiring an explicit later gate design
(which PR event counts, which ref it attests, how the pre-PR state is
reconciled)?**

### Cross-reference, not an A13 blocker

The later test-mode requirement (#2793, comment 7208490147) and its possible
overlap with the historical remote-execution finding OSR-27 are carried in
[a13-inventory-count-reconciliation.md](a13-inventory-count-reconciliation.md).
This crosswalk flags the
overlap by reference only: it is **not** counted as another dry-run blocker,
and no OSR-27 count is added to any total here.

## Limits

- File references only, no line anchors; code references name file and
  function (the runner-kind boundary `TestProfile.kind_for`,
  `default_profile` in `tools/llmtool/llmtool/test_profile.py` stays the
  runner-kind question and acquires no mode and no evidence).
- The historical stage table is not repeated here; downstream refusals
  (`D6_ORIGIN_INVALID`, `ASSIGNMENT_D6_NOT_ACTIVE`, no Developer, no review)
  are the expected cascade of the two root blockers and are not counted as
  extra blockers. Merge was out of run scope, not a technical refusal.
- Unavailable raw inventory and recheck IDs from the historical run remain
  **unverified**; nothing beyond the linked sources is inferred.
- Gates: exact-head hosted docs check at pinned main
  `be913e781e8194fdd36e256d76e021f24a1454cb`; formal review by
  codex-reviewer.
