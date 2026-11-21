# Scope mismatch investigation (#2281): `WORKTREE_SCOPE_MISMATCH` root cause analysis

## 1. Summary

Developer results across multiple routes (AGY, OpenCode, and `gw-agent`) have intermittently been discarded with completion callback state `WAITING_EXECUTOR` and missing gate `WORKTREE_SCOPE_MISMATCH`, leaving a clean D6 worktree and no proposal artifact.

This investigation examined the implementation across [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py), [developer_routes/base.py](../../tools/llmtool/llmtool/developer_routes/base.py), [developer_routes/agy.py](../../tools/llmtool/llmtool/developer_routes/agy.py), and [developer_routes/write_boundary.py](../../tools/llmtool/llmtool/developer_routes/write_boundary.py).

The analysis reveals two primary root causes in `task_completion.py`:
1. **Empty diff conflation**: `_worktree_snapshot()` raises `WORKTREE_SCOPE_MISMATCH` when a Developer produces zero modifications (`not changed and not patch`), misattributing an empty result to a scope violation.
2. **Stale `source_sha` comparison across branch merges**: `_worktree_diff()` compares the working tree against `source_sha`. When upstream changes (e.g. `origin/main`) are merged into a PR branch or worktree, any files touched by the merge outside the Task's `write_paths` trigger `WORKTREE_SCOPE_MISMATCH` (or exceed the 128-file limit).

---

## 2. Code paths raising `WORKTREE_SCOPE_MISMATCH` (Acceptance A1)

All `WORKTREE_SCOPE_MISMATCH` stops occur in [task_completion.py](../../tools/llmtool/llmtool/task_completion.py). Three distinct code checks call `_stop('WORKTREE_SCOPE_MISMATCH')`:

### Path 1: Proposal file count limit in `_worktree_diff`
- **Location**: [task_completion.py](../../tools/llmtool/llmtool/task_completion.py) in function `_worktree_diff`.
- **Condition**: `if len(paths) > _PROPOSAL_FILE_LIMIT:` (where `_PROPOSAL_FILE_LIMIT = 128`).
- **What it compares**:
  - Computes `tracked` files via `git diff --name-only ... source_sha` and `untracked` files via `git ls-files --others --exclude-standard`.
  - Compares the total count of distinct changed and untracked files against the hard cap of 128.
  - Does **not** compare route identity, registered D6 state, or heads. Compares worktree filesystem state against `source_sha`.
- **Applicable evidence cases**:
  - Explains merge cases where a merge from `origin/main` into the PR branch introduced modifications across more than 128 repository files between `source_sha` and the worktree state (e.g. Tasks `39065512`, `d6482a29`, `716abbec`, `43a6832b` if upstream divergence was broad).

### Path 2: Empty diff check in `_worktree_snapshot`
- **Location**: [task_completion.py](../../tools/llmtool/llmtool/task_completion.py) in function `_worktree_snapshot`.
- **Condition**: `if not changed and not patch: _stop('WORKTREE_SCOPE_MISMATCH')`.
- **What it compares**:
  - `changed` (tuple of paths) and `patch` (diff string) returned by `_worktree_diff(root, source_sha)`.
  - Evaluates whether the diff between the worktree and `source_sha` is completely empty (zero changed files and empty diff).
  - Does **not** compare route identity, `write_paths`, registered D6 state, or heads.
- **Applicable evidence cases**:
  - **Directly explains Task `4865c6ff` (#2285)**: In this task, AGY ran in a D6 created directly from current main (`f9ad27f6`) with allowed `write_paths` (`owner_ask_stop_hook.py` and its test). AGY exited cleanly without modifying either file. Because the worktree remained identical to `source_sha`, `changed` was empty and `patch` was `""`. Instead of classifying the result as no changes, the function stopped with `WORKTREE_SCOPE_MISMATCH`.
  - Explains Task `43a6832b` (#2362 CI repair) where the Developer process completed (11 min, `DONE`), but produced no retained file changes.

### Path 3: Allowed `write_paths` boundary check in `_worktree_snapshot`
- **Location**: [task_completion.py](../../tools/llmtool/llmtool/task_completion.py) in function `_worktree_snapshot`.
- **Condition**: `if any(path not in set(write_paths) for path in changed): _stop('WORKTREE_SCOPE_MISMATCH')`.
- **What it compares**:
  - Compares every path in `changed` (from `_worktree_diff(root, source_sha)`) against the Task specification's `write_paths`.
  - Does **not** check route identity, registered D6 state, or heads. It evaluates whether any tracked diff against `source_sha` or any untracked file in the worktree falls outside `write_paths`.
- **Applicable evidence cases**:
  - **Directly explains Task `122b2df9` (#2199, AGY in an OpenCode D6)**: Reusing an existing D6 workspace previously provisioned for OpenCode leaves untracked files, scratch files, or remnants outside the new Task's `write_paths`. `_worktree_diff` picks up these untracked files (`git ls-files --others`), triggering `path not in set(write_paths)`.
  - **Directly explains Tasks `39065512`, `d6482a29`, `716abbec`, and `43a6832b` (main merge into PR branch)**: When a PR branch receives a merge of `origin/main` (e.g. during a publish step or PM sweep), but `source_sha` remains pinned to the pre-merge base commit, `git diff source_sha` evaluates all files touched between `source_sha` and the merged commit. Because an upstream main merge modifies files outside the task's narrow `write_paths`, the function immediately flags a scope violation.

---

## 3. Lifecycle of the D6 worktree and evidence retention (Acceptance A2)

### Working tree state after stop
- Inspection of [task_completion.py](../../tools/llmtool/llmtool/task_completion.py) (in functions `_worktree_snapshot` and `_worktree_diff`) and [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py) (in function `run_task_direct_result_event`) confirms that **neither function mutates the D6 worktree**.
- There is no `git reset`, `git checkout`, `git clean`, or file deletion invoked upon encountering `WORKTREE_SCOPE_MISMATCH`.
- **Why the D6 is clean**:
  - When the D6 is found completely clean (as in Task `4865c6ff`), the changes were **never written to the filesystem** by the Developer process in the first place (e.g. the agent completed its conversation without invoking file edit tools, writes were blocked by sandbox permissions, or edits were staged elsewhere).
  - In cases where changes *were* written outside `write_paths` or where upstream files were merged, those files actually remain in the working tree; however, the task completion pipeline refuses to consume them as a proposal artifact.

### Evidence persistence and callback handling
- In [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py) in function `run_task_direct_result_event`:
  ```python
  except LLMToolError as exc:
      code = getattr(exc, 'completion_code', 'DIRECT_RESULT_EVIDENCE_UNAVAILABLE')
      waiting = {
          **callback,
          'state': 'WAITING_EXECUTOR',
          'evidence': failed_evidence,
          'missing_gates': [code],
      }
  ```
- **Where evidence is kept**:
  - The Task's `direct_result_callback` record in TaskStore (`~/.local/state/llmtool/tasks/<task_id>.json`) is updated with `state: 'WAITING_EXECUTOR'` and `missing_gates: ['WORKTREE_SCOPE_MISMATCH']`.
  - The Developer execution metadata (exit code, process duration, and raw console output) is stored under `task.developer.execution`.
  - The proposal artifact (`task.developer.artifact`) remains empty (`""`) because `collect_executor_worktree_result()` aborted before serializing a patch.
  - Route-specific agent logs (e.g. AGY conversation transcripts and logs in `~/.gemini/antigravity-cli/brain/`) persist independently on the host.
- **UNKNOWN**:
  - Whether external daemon monitors or remote runners perform asynchronous cleanup on failed worktrees cannot be determined from the read-scoped files and is classified as **UNKNOWN**.

---

## 4. Candidate fixes and risks (Acceptance A3)

### Candidate 1: Disentangle empty diff from scope mismatch
- **Mechanism**:
  - In [task_completion.py](../../tools/llmtool/llmtool/task_completion.py) in function `_worktree_snapshot`, replace `_stop('WORKTREE_SCOPE_MISMATCH')` on empty diff with a dedicated error code, e.g. `WORKTREE_DIFF_EMPTY` or `DIRECT_RESULT_EMPTY_DIFF`.
  - In [developer_routes/base.py](../../tools/llmtool/llmtool/developer_routes/base.py) (function `run_route`) and [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py) (function `run_task_direct_result_event`), detect zero-mutation results (`_changed_files(before, after) == ()`) before invoking `_worktree_snapshot()`, classifying the developer outcome as `DEVELOPER_NO_CHANGES` (analogous to Claude's `CLAUDE_NO_CHANGES` handling).
- **Risks & Trade-offs**:
  - Low risk. Downstream test assertions expecting `WORKTREE_SCOPE_MISMATCH` on an empty worktree will require updating to expect the new distinct code.

### Candidate 2: Diff against merge base rather than stale `source_sha`
- **Mechanism**:
  - In [task_completion.py](../../tools/llmtool/llmtool/task_completion.py) in function `_worktree_diff`, instead of running `git diff --name-only ... source_sha`, determine the merge base between the worktree `HEAD` and `source_sha` (or diff against `HEAD~1` / the pre-developer snapshot recorded in `developer_routes/base.py`).
  - Alternatively, restrict the scope validation in `_worktree_snapshot()` to paths captured by `_changed_files(before, after)` during the developer execution session, rather than inspecting the entire branch divergence against `source_sha`.
- **Risks & Trade-offs**:
  - Moderate risk. Diffing against merge-base or session snapshots requires robust handling when worktrees are dirty prior to execution. If an untracked file was introduced before the session, filtering it out might mask unintentional file contamination.

### Candidate 3: Strict D6 hygiene and base alignment on publish merge
- **Mechanism**:
  - Prevent reusing worktrees across heterogeneous routes (e.g. enforce clean provisioning when dispatching AGY into a worktree previously used by OpenCode).
  - When a PR branch receives an upstream merge of `origin/main` during publish or repair sweeps, automatically advance the Task's recorded `source_sha` to the merge commit before dispatching the next developer repair attempt.
- **Risks & Trade-offs**:
  - Low-to-moderate risk. Advancing `source_sha` requires ensuring that earlier developer commits on the branch are preserved and that the base reference remains immutable for completed review records.
