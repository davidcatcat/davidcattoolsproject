# A13.0 Profile Wiring: Rollout and Rollback Checklist

Date: 2026-11-28  
Status: Design checklist only — no drill, live run, verification, or A13.0 exit closure is claimed here; unexecuted or unimplemented items are marked `[PENDING]`.

## 1. Checkouts and Source Freshness Rules

Every step below names the checkout it acts on and how that checkout reaches the target revision:

- **Installed `llmtool` package checkout** (resolved from `llmtool.__file__` by `source_freshness.source_freshness`):
  - **Without an approved pin** (`source_freshness.read_pin` returns `None`): `source_freshness` fetches `origin/main` (`_fetch_origin_main`) and requires `git status --porcelain` to be empty and `HEAD` to equal `refs/remotes/origin/main` (else `DIRTY` or `STALE`). Bring this checkout to `main` with `git fetch origin main` and a fast-forward update (`git checkout main && git pull --ff-only origin main`).
  - **With an approved pin** (`source_freshness.pin_path`, `~/.local/state/llmtool/approved-install.json`, mode `0600`): `source_freshness` requires a clean tree and `HEAD == pin['approved_sha']` (returns `UNAPPROVED` if `HEAD` descends from `approved_sha`, `STALE` otherwise). Bring this checkout to the pinned SHA with `git fetch origin main && git checkout <approved_sha>`. Deleting the pin file restores the `origin/main` check.
- **Registered Task D6 worktree**: isolated per-Task branch worktree used for a slice PR or a revert PR. Developers edit only authorized `write_paths` and run no Git writes or tests in the D6; `llmtool task advance` pushes, opens/updates the PR, and merges.

## 2. Rollout Sequence (Per Slice)

1. `[PENDING]` **Pre-slice baseline:** Confirm the installed `llmtool` checkout is clean and fresh (`source_freshness.require_fresh_source` returns `OK`), and record the current `origin/main` SHA (`git rev-parse refs/remotes/origin/main`) as a reference only (other PRs may merge before this slice lands).
2. `[PENDING]` **Slice implementation and gates:** Dispatch the bounded slice Task in its registered D6 worktree, then advance through `llmtool task advance`:
   - Push branch from the D6 worktree.
   - Run EC2 remote-test (`llmtool task remote-test`, unless docs-only/scripts-only).
   - Open PR and pass required Linux CI checks, formal cross-family review `PASS`, and (when Mac paths are touched) `mac-ci` / `check (macOS 3.13)`.
3. `[PENDING]` **Merge:**
   - **Single PR** (`task_advance._merge_once`): squash-merges via `gh pr merge <pr> --squash --match-head-commit <head>` and posts the `llmtool task advance merge provenance` comment on the PR.
   - **Merge train** (`merge_train_ops.merge_train`): rechecks `read_authenticated_main_sha` (`_main_moved`), train head, member claims, and train CI, then squash-merges and records `merge_sha` (`_finish_merged`) and posts provenance on the train PR and member PRs (`_member_pr_comment`).
4. `[PENDING]` **Record slice merge commit and rollback target (from PR, not `branches/main`):**
   - Because other PRs may merge on `main` in between, never assume `branches/main` is the slice merge commit.
   - Read the PR from GitHub (`pulls/<pr_number>`) and verify `merged == true` and `head.sha == <slice_head_sha>`; record `merge_commit_sha` as the **slice merge commit** (for a merge train, read the train PR or the `merge_sha` recorded by `merge_train_ops._finish_merged`).
   - Obtain the **rollback target** for that slice as the first parent of the slice merge commit (`git rev-parse <slice_merge_sha>^1` after `git fetch origin main`). This identifies the exact pre-slice `main` state for that slice even when other PRs merged before or after it.
5. `[PENDING]` **Refresh installed `llmtool` checkout:** Update the installed package checkout to the new `origin/main` (or update the approved pin if pinned) before dispatching the next dependent slice.

## 3. Pre-Merge Failure vs. Post-Merge Rollback

- **Failure before merge (stop and repair; no revert):**
  - If EC2 remote-test or required CI fails, `task_advance` / `review_repair.repair_review` routes to `CI_FAILED` / `REPAIR` (up to `test_fail_max`; `hosted_ci_repair.failure_for_task` handles eligible Linux CI failures after repeated EC2 timeouts).
  - If formal review fails, `review_repair.repair_review` routes to review repair (AI Debug via `llmtool task high-debug` after the 3rd effective review repair `FAIL`).
  - If merge gates refuse (`MERGE_CHECKS_NOT_GREEN`, `MERGE_REQUIRED_CHECK_MISSING`, `MERGE_MACOS_CHECK_REQUIRED`, `TRAIN_MAIN_MOVED`), fix on the PR branch or close/drop the unmerged PR/Task. Do not run `git revert` for unmerged work.
- **Rollback of a merged slice (reviewed revert PR; no force ops or manual `TaskStore` edits):**
  - Open a new Task and registered D6 worktree on current `origin/main`.
  - Revert the recorded slice merge commit (`git revert --no-edit <slice_merge_sha>`). If multiple slices merged, revert in reverse merge order (newest first) or in one verified revert PR if coupled.
  - Run the revert PR through the full `llmtool task advance` gates (EC2 remote-test, exact-head CI, formal cross-family review `PASS`, and squash merge). Never force-push, reset `main`, or manually edit `TaskStore` records.
  - Refresh the installed `llmtool` checkout to the resulting `origin/main` (or delete `approved-install.json` if rolling back a pin) so `source_freshness` stays `OK`.

## 4. Pending Repository Procedure Gaps

- `[PENDING]` **Pin management CLI:** `source_freshness.read_pin` reads `approved-install.json`, but no `llmtool` CLI subcommand exists in the inspected modules to create, rotate, or delete the pin file.
- `[PENDING]` **Automated slice-revert command:** No `llmtool` subcommand automates creating and linking a revert Task from a recorded `merge_commit_sha`; rollbacks use a normal new Task + PR through `llmtool task advance`.
- `[PENDING]` **Runtime non-default profile wiring:** Callers currently resolve `ci_profile.default_profile` / `test_profile.default_profile` when `profile` is unset, and `review_repair.repair_review` refuses an explicit `profile` (`CI_REPAIR_PROFILE_INVALID`). Project-configured profile loading is not yet wired.
