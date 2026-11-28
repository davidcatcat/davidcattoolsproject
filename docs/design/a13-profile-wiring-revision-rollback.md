# A13.0 Profile Wiring: Rollout and Rollback Checklist

Date: 2026-11-28  
Status: Design checklist only — no drill, live run, verification, or A13.0 exit closure is claimed here; unexecuted or unimplemented items are marked `[PENDING]`.

## 1. Checkouts and `source_freshness.py` Rules

Every step below names the checkout it acts on and how that checkout reaches the target revision:

- **Installed `llmtool` package checkout** (resolved from `llmtool.__file__` by `source_freshness.source_freshness`):
  - **Without an approved pin** (`source_freshness.read_pin` returns `None`): `source_freshness` runs `_fetch_origin_main`, requires clean `git status --porcelain` and `HEAD == refs/remotes/origin/main` (else `DIRTY`, `STALE`, or `UNKNOWN`). Bring this checkout to `main` with `git fetch origin main && git checkout main && git pull --ff-only origin main`.
  - **With an approved pin** (`source_freshness.pin_path`, `~/.local/state/llmtool/approved-install.json`, schema v1, mode `0600`): `source_freshness` requires a clean tree and `HEAD == pin['approved_sha']` (returns `UNAPPROVED` if `HEAD` descends from `approved_sha`, `STALE` otherwise). Bring this checkout to the pinned SHA with `git fetch origin main && git checkout <approved_sha>`. Deleting the pin file restores the `origin/main` check.
  - `source_freshness.require_fresh_source` fails closed unless `OK` (or `LLMTOOL_ALLOW_STALE_SOURCE=1` override).
- **Registered Task D6 worktree:** isolated per-Task branch worktree for a slice PR or revert PR. Developers edit only `write_paths` and run no Git writes or tests in D6; `llmtool task advance` (`task_advance.py`) pushes, opens/reuses the PR, and merges.

## 2. Rollout Sequence (Per Slice)

1. `[PENDING]` **Pre-slice baseline:** Confirm the installed `llmtool` checkout passes `source_freshness.require_fresh_source` (`OK`). Record `origin/main` (`git rev-parse refs/remotes/origin/main`) as informational context only (do not require equality later, as other PRs may merge).
2. `[PENDING]` **Slice implementation, gates, and pre-merge recording:** Dispatch the bounded slice Task in its registered D6 worktree, then advance via `llmtool task advance` (pushes D6 branch, runs EC2 `llmtool task remote-test`, opens PR, passes required Linux CI via `task_advance._required_linux_checks`, formal cross-family review `PASS`, and Mac CI `check (macOS 3.13)` when `task_advance._mac_ci_runs` requires it). Before merge, record `<task_id>`, `<pr_number>`, and `<slice_head_sha>` from the Task/PR record.
3. `[PENDING]` **Merge (single PR recommended):**
   - **Single PR** (`task_advance.py` `_merge_main_gate`, `_merge_checks`): squash-merges via `gh pr merge <pr_number> --squash --match-head-commit <slice_head_sha>` without syncing `main` when mergeable.
   - **Merge train** (`merge_train_ops.merge_train`, `_merge_locked`, `_finish_merged`, `close_train`): checks `read_authenticated_main_sha` (`_main_moved`), train head, member claims (`_member_unchanged`, `_merge_security_claim`), and train-head CI, then squash-merges the entire train into one `merge_sha` and closes unmerged member PRs (`_close_member_prs`, `_member_pr_comment`). A multi-member train's `merge_sha` does not isolate one slice; prefer single-PR merge for A13.0 slices.
4. `[PENDING]` **Record slice merge commit and rollback target:**
   - Never assume `branches/main` equals the slice merge commit (other PRs may merge in between).
   - For a **single-PR merge**, read `pulls/<pr_number>` (`merged == true` and `head.sha == <slice_head_sha>`); record `merge_commit_sha` as `<slice_merge_sha>`.
   - Obtain `<rollback_target>` in the installed package checkout: `git fetch origin main && git rev-parse <slice_merge_sha>^1`.
   - If merged in a **multi-member merge train**, per-slice isolation from `merge_sha` has no automated repository procedure (`[PENDING]` gap below).
5. `[PENDING]` **Refresh installed `llmtool` checkout:** Bring the installed package checkout to current `origin/main` (or update `approved-install.json` to the new approved SHA if pinned) so `source_freshness` returns `OK` before the next dependent slice.

## 3. Pre-Merge Failure vs. Post-Merge Rollback

- **Failure before merge (stop and repair; no revert):**
  - EC2 remote-test or Linux CI failure: `task_advance` / `review_repair.repair_review` routes to test/CI repair (`hosted_ci_repair.failure_for_task` handles eligible Linux CI failures after repeated EC2 timeouts).
  - Formal review failure: `review_repair.repair_review` routes to review repair (AI Debug via `llmtool task high-debug` after the 3rd effective review repair `FAIL`).
  - Merge gate refusal (`MERGE_CHECKS_NOT_GREEN`, `MERGE_REQUIRED_CHECK_MISSING`, `MERGE_MACOS_CHECK_REQUIRED`, `TRAIN_MAIN_MOVED`): fix on the PR branch (or rebuild train) or close/drop the unmerged PR/Task. Never run `git revert` for unmerged work.
- **Rollback of a merged slice (reviewed revert PR; no force ops or manual `TaskStore` edits):**
  - Applies only to a merged slice with a recorded `<slice_merge_sha>`.
  - Open a new Task and registered D6 worktree on current `origin/main`.
  - Revert only that slice (`git revert --no-edit <slice_merge_sha>`). If multiple coupled slices merged, revert newest first or together in one verified revert PR.
  - Advance the revert PR through full `llmtool task advance` gates (EC2 remote-test, exact-head CI, formal cross-family review `PASS`, squash merge). Never force-push, reset `main`, or manually edit `TaskStore`.
  - Refresh the installed `llmtool` checkout to the resulting `origin/main` (or delete/update `approved-install.json` if rolling back a pin) so `source_freshness` returns `OK`.

## 4. Pending Repository Procedure Gaps

- `[PENDING]` **Pin management CLI:** `source_freshness.read_pin` reads `approved-install.json`, but no `llmtool` CLI subcommand exists in the inspected modules to create, rotate, or delete the pin file.
- `[PENDING]` **Automated slice-revert command:** No `llmtool` subcommand automates creating a revert Task from a recorded `merge_commit_sha`; rollbacks use a normal new Task + PR through `llmtool task advance`.
- `[PENDING]` **Per-slice merge-train revert:** `merge_train_ops._merge_locked` and `_finish_merged` squash all train members into one `merge_sha` and `close_train` closes member PRs unmerged; no procedure exists to isolate or revert one slice from a multi-member train.
- `[PENDING]` **Runtime non-default profile wiring:** Callers resolve `ci_profile.default_profile` / `test_profile.default_profile` when `profile` is unset, and `review_repair.repair_review` refuses an explicit `profile` (`CI_REPAIR_PROFILE_INVALID`). Project-configured profile loading is not yet wired.
