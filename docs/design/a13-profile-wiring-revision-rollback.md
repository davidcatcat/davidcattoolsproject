# A13.0 Profile Wiring: Rollout and Rollback Checklist

Date: 2026-11-29. Checklist only: no drill, run, verification or A13.0 exit
closure is claimed. Steps without an existing procedure are marked `[GAP]`.
Companion: [verification checklist](a13-profile-wiring-verification.md).

## 1. Checkouts

- **Installed llmtool checkout**: the repository holding `llmtool.__file__`.
  `source_freshness.require_fresh_source` refuses unless
  `source_freshness.source_freshness` returns `OK`.
  - *No pin* (`read_pin` returns `None`): `OK` needs a clean tree and
    `HEAD` equal to `refs/remotes/origin/main` after the built-in fetch;
    otherwise `DIRTY`, `STALE` or `UNKNOWN`.
  - *Pin present* (`pin_path`, `approved-install.json`): `OK` needs a clean
    tree and `HEAD` equal to `approved_sha`. `HEAD` past the pin gives
    `UNAPPROVED`; `HEAD` behind or beside it gives `STALE`.
- **Task D6 worktree**: the Developer edits only the Task write paths and runs
  no Git writes or tests. `llmtool task advance` commits those changes,
  pushes, opens the PR and merges (Owner S4, #1019).

## 2. Rollout, per slice

1. Before the slice: the installed checkout returns `OK`.
2. Dispatch the slice Task and drive it with `llmtool task advance` through
   all gates (order in the companion, section 1).
3. Before merge, record Task ID, PR number and exact head SHA.
4. Merge as a single PR. A merge train squashes all members into one commit,
   so one slice cannot be isolated from it later (`[GAP]` 4).
5. After merge, read `pulls/<pr_number>`: `merged` is true, `head.sha` is
   the recorded head; record `merge_commit_sha` as the slice merge SHA. Its
   first parent is the pre-slice main. Never take `main` itself as the slice
   commit; other PRs may have merged since.
6. Refresh the installed checkout before the next dependent slice:
   - *No pin*: fast-forward it to `origin/main`.
   - *Pin*: both move the checkout to the new approved SHA and set
     `approved_sha` to that SHA. Either alone gives `UNAPPROVED` or `STALE`.
     No llmtool command writes the pin (`[GAP]` 1).

## 3. Failure before merge

Nothing is reverted; the PR is not on main.

- EC2 or CI test failure: test repair through `llmtool task advance` /
  `review_repair` (hosted CI repair via `hosted_ci_repair.failure_for_task`).
- Formal review FAIL: review repair, with AI Debug and split/REPLAN under the
  AGENTS.md repair rules.
- Merge gate refusal: fix on the PR head, or end the Task and close the PR
  with a recorded reason.

## 4. Rollback of a merged slice

Applies only to a slice with a recorded merge SHA. No force push, no reset of
main, no hand edit of the Task store.

1. Decide what to undo: the slice's changed files and any later merged change
   that depends on it (undo newest first, or together in one PR).
2. Produce the undo change. There is no llmtool step that creates a
   `git revert` commit (`[GAP]` 2). Existing permitted ways:
   - a normal Developer Task on current main whose write paths are the
     slice's files and whose goal is to restore the pre-slice behaviour; the
     Developer edits the files, and `task advance` commits and publishes;
   - when llmtool itself is broken by the slice, the independent line
     (AGENTS.md, Independent development line) may revert in an owner
     worktree with ordinary Git, without force operations.
3. The undo PR passes the same gates as any code PR: EC2, exact-head Linux
   CI, formal cross-family review, Mac CI when required, then merge.
4. Refresh the installed checkout as in section 2 step 6. With a pin, set
   `approved_sha` to the undo merge SHA; deleting the pin file is not a
   rollback, it turns the approval check off.

## 5. Gaps

1. `[GAP]` No llmtool command creates, rotates or removes the install pin.
2. `[GAP]` No llmtool step produces a revert commit from a recorded merge SHA.
3. `[GAP]` Project-configured profiles are not loaded at runtime; callers use
   `default_profile()` and `review_repair.repair_review` refuses an explicit
   profile (`CI_REPAIR_PROFILE_INVALID`).
4. `[GAP]` No procedure isolates one slice from a multi-member train merge.
