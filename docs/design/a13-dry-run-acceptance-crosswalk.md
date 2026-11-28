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
- 2026-11-28 Roadmap amendment:
  [PR #2923](https://github.com/davidcatcat/davidcattoolsproject/pull/2923) and
  formal PM-relayed Owner record
  [#2793 comment 7236027900](https://github.com/davidcatcat/davidcattoolsproject/issues/2793#issuecomment-7236027900)
  (effective for implementation only after formal review).
- Linked notes in this directory:
  - [A13 external-repo dry-run baseline](a13-external-repo-dry-run-baseline.md) —
    historical run facts and the two root blockers.
  - [A13 test execution modes](a13-test-execution-modes.md) — `local` /
    `github` / `remote` modes per automated check, separate manual acceptance,
    the `NOT_TESTED` path, and conceptual evidence binding.
  - [A13 inventory count reconciliation](a13-inventory-count-reconciliation.md)
    — counting units and the minimum fields a crosswalk row must carry.

## Blocker crosswalk

| ID | Source | Failed observation (historical) | Consumer | Disposition | Milestone | Next observable evidence (pending) |
|---|---|---|---|---|---|---|
| A13.1 | #396, comment 6723140835 | Repo detection only partly worked and the PM Task was recorded but not scoped to the repo, so no repo-bound trust was established (downstream `D6_ORIGIN_INVALID` and `ASSIGNMENT_D6_NOT_ACTIVE` refusals are the cascade of this gap, not separate blockers) | PM; downstream origin/assignment checks | Pending | A13.1 — registration and trust | An authorized fixture repository resolves to the intended project, and its task and D6 are bound there: a rerun reports repo detection complete and the task/D6 checks bound to that repo, instead of partial detection and unscoped recording |
| A13.2 | #396, comment 6723140835; #2793, comment 7210501920; PR #2923 | llmtool tests did not run (`ADMISSION_DENIED`, `RECEIPT_HEAD_UNRECORDED`) and no fixture CI workflows existed, so no project-bound test/CI evidence could be produced per the modes note | PM; A13.2 implementers | Pending | A13.2 — project-bound test/CI evidence | For each configured automated check, a receipt for the intended project, at the exact commit, for the check's selected mode, with the configured test selection and each test's outcome (or `NOT_TESTED` with scope and reason when zero automated checks are configured, never automated PASS); evidence is accepted only when it matches all bindings and is never downgraded or substituted; independent formal PASS and merge gates enforced |

### A13.2 — execution mode and validation policy (revised 2026-11-28)

Per [PR #2923](https://github.com/davidcatcat/davidcattoolsproject/pull/2923)
and Owner record
[#2793 comment 7236027900](https://github.com/davidcatcat/davidcattoolsproject/issues/2793#issuecomment-7236027900),
once this crosswalk, [a13-test-execution-modes.md](a13-test-execution-modes.md),
and the A13.0 handoff references pass formal review, **only** the earlier
requirement of *exactly one automated mode per project* is superseded for
implementation. All other rules remain binding:

- **Explicitly selected checks:** a project may explicitly configure zero, one,
  or multiple required validation checks, with exactly one explicitly selected
  automated mode (`local`, `github`, or `remote`) per automated check. Multiple
  checks in different modes are not fallback or substitutes for one another. No
  mode may be inferred from failure or environment, and there is no silent
  fallback; if the selected mode cannot produce valid evidence for a requested
  check, the check fails.
- **Evidence binding:** for each automated check, the receipt must be bound to,
  and accepted only when it matches, all of: project or repository identity, the
  exact commit covered, the selected mode recorded at check time, and the
  configured test selection with each test's outcome. Missing, ambiguous,
  mismatched, or unverified required evidence fails the check and cannot be
  downgraded to `NOT_TESTED` or replaced by manual acceptance.
- **Separate manual acceptance and no-test path:** manual acceptance is
  separately recorded and is not an automated execution mode or a substitute for
  a required automated check. A project explicitly configured with zero
  automated checks may proceed with development, but records `NOT_TESTED` (or
  equivalent) with scope and reason, never an automated PASS.
- **Mandatory independent review and merge gates:** independent formal PASS of
  the exact head and valid evidence for every configured required check are
  required before any llmtool-mediated merge (manual or automatic); failed,
  pending, unavailable, stale, mismatched, or unknown required evidence blocks
  both. Automatic merge of a `NOT_TESTED` revision is prohibited; a manual
  merge of a `NOT_TESTED` revision through llmtool requires separately recorded
  explicit authorized human acceptance for that exact project and head,
  independent formal PASS, and all applicable mandatory checks, and is allowed
  only when no required automated check is outstanding.
- **No unreviewed scope or runtime claim:** this 2026-11-28 revision introduces
  no new product decision, interface, or scope beyond the Roadmap section
  "External project validation and CI policy (2026-11-28 product direction)",
  claims no implementation, runtime validation, or exit closure, and takes
  effect for implementation only after formal review.

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
- Gates: the exact-head hosted docs check runs against this document's exact
  PR HEAD — the pinned main
  `be913e781e8194fdd36e256d76e021f24a1454cb` is the crosswalk reference only,
  not the revision the docs gate checks; formal review by codex-reviewer.
