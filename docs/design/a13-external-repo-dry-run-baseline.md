# A13 Evidence Baseline: Historical #1230 D2(B) Dry Run

Status: **historical evidence only.** This table records one completed dry
run so A13.1 and A13.2 can start from a verified fact base. No new run was
performed. Current runtime behaviour and positive acceptance are
**unverified**; nothing here is acceptance evidence.

## Sources

- Historical report: #396, comment 6723140835.
- Inventory requirement: #2793, comment 7210501920.
- Test-mode note (merged): `docs/design/a13-test-execution-modes.md` —
  defines `local` / `github` / `remote` modes and the conceptual evidence
  binding (project/repo identity, exact commit, recorded mode, selected
  tests and outcomes).

## Run facts (advisor-verified, historical)

| Item | Fact |
|---|---|
| Tool head | llmtool main `f936b567` |
| Fixture | separately authorized private fixture at `594c63a8` |
| Repo detection | partly worked |
| PM Task | recorded, but **not scoped to that repo** |
| D6 origin check | refused, `D6_ORIGIN_INVALID` |
| Assignment | refused, `ASSIGNMENT_D6_NOT_ACTIVE` |
| Developer | none started |

## Evidence table (historical #1230 D2(B) dry run)

| Stage | Result / refusal | Classification | Hand-off |
|---|---|---|---|
| Repo detection | partly worked | Root blocker (partial) | A13.1 (registration and trust) |
| PM Task | recorded, not scoped to the repo | Root blocker | A13.1 (registration and trust) |
| D6 origin | refused `D6_ORIGIN_INVALID` | Downstream refusal of the trust gap | A13.1 (registration and trust) |
| Assignment | refused `ASSIGNMENT_D6_NOT_ACTIVE` | Downstream refusal of the same trust gap | A13.1 (registration and trust) |
| Developer | none started | Downstream refusal (assignment never active) | A13.1 |
| llmtool tests | did not run: `ADMISSION_DENIED`, `RECEIPT_HEAD_UNRECORDED` | Root blocker (no project-bound test evidence path) | A13.2 (project-bound test/CI evidence) |
| Fixture CI | no fixture CI workflows existed | Root blocker (no hosted exact-head evidence source) | A13.2 (project-bound test/CI evidence) |
| Review | did not launch: `REVIEW_PROMPT_BINDING_MISSING`, `REVIEWER_FAMILY_UNSUPPORTED` | Downstream refusal (no active assignment/Developer) | — (revisit once A13.1 items land) |
| Merge | not attempted | Downstream refusal (upstream stages never passed) | — |

## Reading the table

- **Root blockers**: (1) registration/trust — repo detection only partly
  worked and the PM Task was not scoped to the repo, so no repo-bound trust
  was established; (2) project-bound test/CI evidence — llmtool tests were
  not admitted and the fixture had no CI workflows, so no evidence could be
  bound per the modes note.
- **Downstream refusals** (`D6_ORIGIN_INVALID`,
  `ASSIGNMENT_D6_NOT_ACTIVE`, no Developer, no review, no merge) are the
  expected cascade from the root blockers. Repeated downstream refusals are
  **not** counted as distinct new portability blockers.
- **Not evidence**: the manually run fixture tests were not llmtool evidence
  and do not offset the missing llmtool run.

## Mapping to hand-offs

- **A13.1** — registration and trust: fix the partial repo detection and
  PM-Task scoping so repo-bound trust can be established; this clears the
  origin/assignment cascade.
- **A13.2** — project-bound test/CI evidence: provide a path to exact-head,
  mode-bound test/CI evidence per
  `docs/design/a13-test-execution-modes.md` (`TestProfile.kind_for`,
  `default_profile` in `tools/llmtool/llmtool/test_profile.py` remain the
  runner-kind boundary), and the intended authorized positive
  Issue-to-PR loop.

## Limits

- Docs only: no fixture changes or fixture access, no tests, no external
  writes or release.
- This document does not repeat platform assumptions and does not design
  anything new; it only records historical evidence and the hand-off split.
- Gates: exact-head hosted docs check; formal review by codex-reviewer.
