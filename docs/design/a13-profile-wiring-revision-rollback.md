# A13.0 Profile Wiring: Rollout and Rollback Checklist

Date: 2026-11-28  
Status: Design checklist only — no drill, live run, verification, or A13.0 exit closure is claimed here; unexecuted or unimplemented items are marked `[PENDING]`.

## 1. Checkouts and Source Freshness Rules

Every step below names the checkout it acts on and how that checkout reaches the target revision:

- **Installed `llmtool` package checkout** (resolved from `llmtool.__file__` by `source_freshness.source_freshness`):
  - **Without an approved pin** (`source_freshness.read_pin` returns `None`): `source_freshness` fetches `origin/main` (`_fetch_origin_main`) and requires `git status --porcelain` to be empty and `HEAD == refs/remotes/origin/main` (else `DIRTY` or `STALE`). Bring this checkout to `main` with `git fetch origin main` and `git checkout main && git pull --ff-only origin main`.
  - **With an approved pin** (`source_freshness.pin_path`, `~/.local/state/llmtool/approved-install.json`, mode `0600`): `source_freshness` requires a clean tree and `HEAD == pin['approved_sha']` (returns `UNAPPROVED` if `HEAD` descends from `approved_sha`, `STALE` otherwise). Bring this checkout to the pinned SHA with `git fetch origin main && git checkout <approved_sha>`. Deleting the pin file restores the `origin/main` check.
- **Registered Task D6 worktree**: isolated per-Task branch worktree for a slice PR or revert PR. Developers edit only `write_paths` and run no Git writes or tests in D6; `llmtool task advance` commits, pushes, opens/reuses the PR, and merges.

## 2. Rollout Sequence (Per Slice)

1. `[PENDING]` **Pre-slice baseline:** Confirm the installed `llmtool` checkout is clean and fresh (`source_freshness.require_fresh_source` returns `OK`). Record `origin/main` (`git rev-parse refs/remotes/origin/main`) as informational reference only (do not require equality later, as other PRs may merge).
2. `[PENDING]` **Slice implementation and gates:** Dispatch the bounded slice Task in its registered D6 worktree, then advance via `llmtool task advance`:
   - Push branch from D6 (`task_advance._publish_task`).
   - Run EC2 remote-test (`llmtool task remote-test`, via `task_advance._pre_review_action`; waived only when `_pr_docs_only` proves docs-only).
   - Open PR and pass required Linux CI (`_required_linux_checks`), formal cross-family review `PASS`, and (when Mac paths are touched and not docs-only) `check (macOS 3.13)` via `mac-ci` label (`_merge_checks`, `label_mac_ci_for_skipped_check`).
3. `[PENDING]` **Merge:**
   - **Single PR** (`task_advance._merge_once`): checks `_merge_main_gate`, squash-merges via `gh pr merge <pr> --squash --match-head-commit <head>`, and posts `llmtool task advance merge provenance` (`_merge_success`).
   - **Merge train** (`merge_train_ops.merge_train`): checks `read_authenticated_main_sha` (`_main_moved`), train head, member claims (`_member_unchanged`, `_merge_security_claim`), and train CI, then squash-merges, records `merge_sha` (`_finish_merged`), and posts provenance on the train PR and member PRs (`_member_pr_comment` in `close_train`).
4. `[PENDING]` **Record slice merge commit and rollback target (from PR, not `branches/main`):**
   - Because other PRs may merge on `main` in between, never assume `branches/main` is the slice merge commit.
   - Read the PR from GitHub (`pulls/<pr_number>`) and verify `merged == true` and `head.sha == <slice_head_sha>`; record `merge_commit_sha` as the **slice merge commit** (for a merge train, read the train PR `pulls/<train_pr>` or `merge_sha` recorded by `merge_train_ops._finish_merged`).
   - Obtain the **rollback target** as the first parent of the slice merge commit (`git fetch origin main` in the package checkout, then `git rev-parse <slice_merge_sha>^1`).
5. `[PENDING]` **Refresh installed `llmtool` checkout:** Update the installed package checkout to the new `origin/main` (or update `approved-install.json` if pinned) before dispatching the next dependent slice.

## 3. Pre-Merge Failure vs. Post-Merge Rollback

- **Failure before merge (stop and repair; no revert):**
  - EC2 remote-test or required CI failure: `task_advance` / `review_repair.repair_review` routes to `CI_FAILED` / `REPAIR_REVIEW` (up to `test_fail_max`; `hosted_ci_repair.failure_for_task` handles eligible Linux CI failures after repeated EC2 timeouts).
  - Formal review failure: `review_repair.repair_review` routes to review repair (AI Debug via `llmtool task high-debug` after the 3rd effective review repair `FAIL`).
  - Merge gate refusal (`MERGE_CHECKS_NOT_GREEN`, `MERGE_REQUIRED_CHECK_MISSING`, `MERGE_MACOS_CHECK_REQUIRED`, `TRAIN_MAIN_MOVED`): fix on the PR branch (or rebuild train) or close/drop the unmerged PR/Task. Never run `git revert` for unmerged work.
- **Rollback of a merged slice (reviewed revert PR; no force ops or manual `TaskStore` edits):**
  - Open a new Task and registered D6 worktree on current `origin/main`.
  - Revert the recorded slice merge commit (`git revert --no-edit <slice_merge_sha>`). If multiple slices merged, revert newest first or in one verified revert PR if coupled.
  - Run the revert PR through full `llmtool task advance` gates (EC2 remote-test, exact-head CI, formal cross-family review `PASS`, squash merge). Never force-push, reset `main`, or manually edit `TaskStore`.
  - Refresh the installed `llmtool` checkout to the resulting `origin/main` (or delete `approved-install.json` if rolling back a pin) so `source_freshness` stays `OK`.

## 4. Pending Repository Procedure Gaps

- `[PENDING]` **Pin management CLI:** `source_freshness.read_pin` reads `approved-install.json`, but no `llmtool` CLI subcommand exists in the inspected modules to create, rotate, or delete the pin file.
- `[PENDING]` **Automated slice-revert command:** No `llmtool` subcommand automates creating and linking a revert Task from a recorded `merge_commit_sha`; rollbacks use a normal new Task + PR through `llmtool task advance`.
- `[PENDING]` **Runtime non-default profile wiring:** Callers currently resolve `ci_profile.default_profile` / `test_profile.default_profile` when `profile` is unset, and `review_repair.repair_review` refuses an explicit `profile` (`CI_REPAIR_PROFILE_INVALID`). Project-configured profile loading is not yet wired.
