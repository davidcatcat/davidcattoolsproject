# A13 Git Layout Boundary — differently named common directory

Status: **Verified subset** — a provenance note recording the existing boundary for Git layouts whose common directory is not named `.git`, and the handoff to project registration.
Pinned main: `d4380aed191a9bc534cce3adbd610563b55a8680`.
Evidence: historical OSR-26 (#396 comment 6552370022) and Coordinator #2793 (comment 7210501920). Source identifiers name file and symbol only, with no line anchors. All facts below were verified by Advisor at pinned main.

## Goal

Record where the current code refuses a Git layout whose common directory has a different name, on both sides of the boundary, and hand broader layout support off to the project-registration work.

## Boundary at D6 (worktree lifecycle)

In `d6_worktree_lifecycle.py`, `create_worktree` reads the absolute git common directory before creating a worktree. It requires:

- the common directory's basename to be `.git`, and
- a matching real path (no redirection away from the expected location).

On mismatch it refuses with `D6_COMMON_DIR_INVALID` before creating the worktree. `create_worktree` separately checks origin and the canonical root. A separately located git directory with a different name therefore fails the name condition at worktree-creation time, before any worktree exists.

## Boundary at review registration

In `review_authority.py`, registration validation checks that its common directory ends in `/.git` and refuses `REVIEW_REGISTRATION_INVALID` otherwise. The review side of the boundary therefore applies the same naming condition independently of the D6 side.

## Scope of the boundary: gitdir indirection vs. a different basename

The recorded boundary distinguishes two layouts:

- **gitdir indirection** (for example, a separate git file that points at a `.git` directory) is a mechanism for reaching the real git directory. If the resolved common directory is still named `.git` with a matching real path, the name condition is not the reason for refusal, and this note claims nothing about it.
- **a different basename** (a common directory that is not named `.git`) fails the name condition in `create_worktree` and the registration check in `review_authority.py`.

This boundary does **not** claim that all linked worktrees, or all separate git directories, are barred. It records only that the two checks above refuse the differently named common directory at pinned main.

## Owning record and verification

Source: Advisor verification at pinned main `d4380aed191a9bc534cce3adbd610563b55a8680`. Owning record: **A13.1** — the layout and identity boundary recorded here belongs to the A13.1 verification work and to its handoff to project registration.

Any broader Git layout support requires coordinated parity between the D6 checks (`d6_worktree_lifecycle.py`) and the review identity checks (`review_authority.py`), recorded as a future design. This note does not decide support, does not change any check, and does not equate static source inspection at pinned main with an executed runtime test.

## Limits and scope

- Docs only: no code changes, CI changes, Git operations, or repo mutations.
- Trust-schema and packaging material from `docs/design/a13-platform-assumptions.md` is intentionally not repeated here.
- Branch-name assumptions are outside this subset.

Gates: exact-head hosted docs check; formal review by codex-reviewer.
