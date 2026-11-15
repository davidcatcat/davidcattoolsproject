# #2084 design: path attribution for a stacked-Task PR after update-branch

Proposal only; not a review or acceptance. Source: main at 65a5c269168b9c6f5ae2f3eaaae0a1beca0967d2. Claims cite path:line at that SHA; anything not read is UNVERIFIED. The change is 待實作（#2084）.

## 1. Today (現行)

Readiness computes Developer-attributed changed paths from the PR head using `_developer_changed_paths` in `tools/llmtool/llmtool/task_completion.py:1084-1164`. The algorithm:
1. Lists commits between `source_sha` and `result_sha` via `git rev-list --reverse --topo-order source_sha..result_sha` (task_completion.py:1091-1092).
2. Identifies two-parent merge commits (`len(fields) >= 3`, task_completion.py:1120). When `_is_ancestor(root, second_parent, trusted_base_sha)` holds (task_completion.py:1123), `second_parent` is recorded in `merge_parents` (task_completion.py:1125).
3. Excludes upstream main commits reachable via `git rev-list second_parent ^source_sha` as `inherited` (task_completion.py:1126-1131, 1139-1140).
4. Extracts merge resolution and conflict paths via `_merge_resolution_paths` (task_completion.py:1134-1135, 1167-1180).
5. Diffs each non-inherited commit against its first parent with `git diff-tree --no-commit-id --name-only -r -z --no-renames first_parent commit --` (task_completion.py:1145-1147). For merge commits, line 1156 skips paths brought from main without developer changes:
   ```python
   if (second_parent is not None and
           path not in merge_resolution_paths.get(commit, set()) and
           _tree_blob(root, commit, path) == _tree_blob(root, second_parent, path)):
       continue
   developer_paths.add(path)
   ```
   If the merge commit's blob matches `second_parent` (main) and had no merge conflict resolution, it is skipped. Otherwise, the path is added to `developer_paths` (task_completion.py:1158).
6. Scope check: in `_collect_registered_result_diff` (task_completion.py:1378-1380), line 1378 tests `set(developer_paths).issubset(set(write_paths))`. If any attributed path falls outside `write_paths`, line 1380 raises `RESULT_SCOPE_MISMATCH`. A similar check raises `RESULT_SCOPE_MISMATCH` in `tools/llmtool/llmtool/task_submission_cli.py:2306-2307`.

## 2. The #2084 case

In stacked-Task delivery, sequential tasks build upon one another:
- Task 1 runs in D6_1, starts from `main`, modifies `path_A`, and commits C1 (`write_paths = {"path_A"}`).
- Task 2 runs in D6_2, starts from C1, modifies `path_B`, and commits C2 (`write_paths = {"path_B"}`).
- The resulting PR contains both C1 and C2, but llmtool binds the PR to the last task (Task 2) with `write_paths = {"path_B"}`.

While the PR is open, `main` advances with a commit M that touches `path_A`. When `gh pr update-branch` merges `main` into the PR branch, it creates merge commit M_PR (first parent C2, second parent M). In M_PR:
- `path_A` changed relative to C2 because M changed it.
- `_developer_changed_paths` inspects M_PR and checks whether `_tree_blob(root, M_PR, path_A) == _tree_blob(root, M, path_A)` (task_completion.py:1156).
- Because M_PR carries Task 1's changes from C1 merged with M's changes, its blob does not match M (`second_parent`).
- Line 1156 is false, so line 1158 attributes `path_A` to `developer_paths`.
- Line 1378 compares `developer_paths` (`{"path_A", "path_B"}`) against Task 2's `write_paths` (`{"path_B"}`).
- Because `path_A` is not in Task 2's `write_paths`, line 1380 raises `RESULT_SCOPE_MISMATCH`.

As noted in `skills/ai-team-delivery/pm-continuation.md:47`, the PM must currently discard the PR and reapply all changes from current `main` as fresh stacked Tasks in a new PR.

## 3. Options

### Option (a): Bind the PR to the union of the stacked chain's write paths, verifying each Task's own commits separately
- **Mechanism:** PR readiness validates the task chain (Task 1 -> Task 2). Each non-merge commit is verified against its own Task's `write_paths` (C1 against Task 1, C2 against Task 2). For merge commits where `second_parent` is trusted `main`, allowed scope is the union (`write_paths_1 ∪ write_paths_2`).
- **Integrity/security effect:** High. A developer cannot smuggle paths. Individual commits cannot exceed their own task scope. The union applies only to merge commits with verified `main`. Any path outside all chain tasks is rejected.
- **Files touched:** `tools/llmtool/llmtool/task_completion.py` (chain resolution and scope validation) and `tools/llmtool/llmtool/task_submission_cli.py`.
- **Test cost:** Medium. Requires unit tests for multi-task chain parsing, per-commit scope checks, union scope checks on merges, and broken-chain rejections.

### Option (b): Attribute merge-commit paths to the earlier stacked commit that introduced them (blob ancestry)
- **Mechanism:** When a merge commit path differs from `second_parent`, trace blob history back along `first_parent` ancestors to find the commit that introduced the change, attributing the path to that ancestor rather than the merge commit.
- **Integrity/security effect:** Low. Tracing blob provenance across complex Git DAGs is fragile and prone to edge cases (e.g. evil merges, conflict adjustments, renames). A developer could craft a merge commit that smuggles unauthorized changes under the guise of an ancestor diff.
- **Files touched:** Primarily `tools/llmtool/llmtool/task_completion.py` (`_developer_changed_paths` and graph helpers).
- **Test cost:** High. Demands complex tests for Git graph topologies, merge resolutions, and blob ancestry edge cases.

### Option (c): Keep refusing and rely on the documented reapply workaround
- **Mechanism:** Maintain status quo. Whenever `update-branch` touches an earlier stacked task's file, readiness raises `RESULT_SCOPE_MISMATCH`, and the PM reapplies fresh stacked tasks from current `main` per `skills/ai-team-delivery/pm-continuation.md:47`.
- **Integrity/security effect:** Strictly fail-closed. No path smuggling possible, but causes high operational toil and token waste.
- **Files touched:** None (already documented in `skills/ai-team-delivery/pm-continuation.md:47`).
- **Test cost:** Zero.

## 4. Recommendation

We recommend **Option (a)** with strict fail-closed rules:
1. **Authoritative chain identification without trusting PR text:**
   Never infer stacked tasks from PR text, branch names, or commit messages. The chain must be identified exclusively from immutable, persisted llmtool task records:
   - Task `continues` links: each child task explicitly records `task['continues']`.
   - D6 registration base SHAs: each child task's registered `source_sha` matches the predecessor task's `result_sha`.
   - Contract digests: every task in the chain has a valid, verified contract digest.
   - Topological verification: Git history between the root `source_sha` and PR `head` forms an unbroken, linear sequence of verified task commits and trusted merge commits.
2. **Per-commit slice enforcement:**
   Each non-merge commit must strictly belong to its corresponding task's `write_paths`.
3. **Fail-closed rules:**
   Readiness raises `RESULT_SCOPE_MISMATCH` if:
   - Any link in the task chain is unresolvable or missing.
   - Any task in the chain is in an invalid or non-terminal state.
   - Any non-merge commit touches paths outside its bound task's `write_paths`.
   - Any merge commit path is not accounted for by the union of chain `write_paths`.
   - History contains force-pushes or unverified commits.

## 5. Tests to add later

At least 6 named test cases to add in `tools/llmtool/tests/test_task_completion.py`:
1. `test_stacked_two_step_update_branch_shared_file_ready`:
   A 2-step stack (Task 1 touches file A, Task 2 touches file B) after `update-branch` merges an upstream change to file A passes readiness (`ready=True`).
2. `test_stacked_path_outside_every_chain_task_mismatch`:
   A merge commit touches file C outside every chain task's `write_paths`; readiness raises `RESULT_SCOPE_MISMATCH`.
3. `test_stacked_broken_chain_link_mismatch`:
   Task 2 claims to continue Task 1, but Task 1's `result_sha` does not match Task 2's `source_sha`, or Task 1's record is missing; readiness raises `RESULT_SCOPE_MISMATCH`.
4. `test_single_task_unchanged`:
   Single-task PR readiness is unchanged and preserves exact current behavior and error codes.
5. `test_merge_main_only_change_not_attributed`:
   A file modified only on `main` matches `second_parent` in the merge commit and is not attributed to developer paths.
6. `test_stacked_force_pushed_history_mismatch`:
   A stacked PR branch with force-pushed or rewritten commits differing from registered D6 results raises `RESULT_SCOPE_MISMATCH` or `RESULT_HEAD_MISMATCH`.

## 6. Out of scope

- **Review and CI gates:** Modifying formal reviewer dispatch, cross-family pairing, or required status check gates (Linux 3.14, macOS 3.13).
- **Merge rules:** Altering `llmtool task advance --merge` or ruleset `protect-main` enforcement.
- **Update-branch policy:** Changing when or how `gh pr update-branch` is triggered.
