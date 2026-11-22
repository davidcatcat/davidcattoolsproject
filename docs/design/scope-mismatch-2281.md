# Scope mismatch investigation (#2281): `WORKTREE_SCOPE_MISMATCH` root cause analysis

## 1. Summary

Developer results across multiple routes (AGY, OpenCode, and `gw-agent`) have intermittently stopped in completion callback state `WAITING_EXECUTOR` with missing gate `WORKTREE_SCOPE_MISMATCH`, leaving a clean D6 worktree and no retained change.

This investigation examined the implementation in [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py), [developer_routes/base.py](../../tools/llmtool/llmtool/developer_routes/base.py), [developer_routes/agy.py](../../tools/llmtool/llmtool/developer_routes/agy.py), [developer_routes/write_boundary.py](../../tools/llmtool/llmtool/developer_routes/write_boundary.py), and [task_advance.py](../../tools/llmtool/llmtool/task_advance.py).

From the code alone, `WORKTREE_SCOPE_MISMATCH` is raised in three places inside [task_completion.py](../../tools/llmtool/llmtool/task_completion.py):
1. **Proposal file count limit**: `_worktree_diff` stops with `WORKTREE_SCOPE_MISMATCH` when the union of tracked diff paths against `source_sha` and untracked paths exceeds `_PROPOSAL_FILE_LIMIT` (`128`).
2. **Empty diff conflation**: `_worktree_snapshot` stops with `WORKTREE_SCOPE_MISMATCH` when `_worktree_diff` returns zero changed paths and an empty patch (`not changed and not patch`), misclassifying an empty diff as a scope mismatch.
3. **Out-of-scope tracked diff**: `_worktree_snapshot` stops with `WORKTREE_SCOPE_MISMATCH` when any path in `changed` returned by `_worktree_diff` is not in `write_paths` (after untracked paths outside `write_paths` have already been rejected with `WORKTREE_UNTRACKED_FILES`).

Because the Task records under `~/.local/state/llmtool/tasks` are not readable and intermediate filesystem states and exact diffs at callback time were not observed, the exact case-specific causes for each of the historical incidents are **UNKNOWN** (see Section 2 and Section 3).

---

## 2. Code paths raising `WORKTREE_SCOPE_MISMATCH` (Acceptance A1)

All `WORKTREE_SCOPE_MISMATCH` stops occur in [task_completion.py](../../tools/llmtool/llmtool/task_completion.py). Three checks call `_stop('WORKTREE_SCOPE_MISMATCH')`:

### Path 1: Proposal file count limit in `_worktree_diff`
- **Location**: [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), function `_worktree_diff`.
- **Condition**: `if len(paths) > _PROPOSAL_FILE_LIMIT:` (where `_PROPOSAL_FILE_LIMIT = 128`).
- **What it compares**:
  - Computes `tracked` via `git diff --name-only -z --no-ext-diff --no-textconv --no-renames <source_sha> --` and `untracked` via `git ls-files --others --exclude-standard -z`.
  - Compares `len(paths)` (where `paths = tuple(sorted(tracked | set(untracked)))`) against `128`.
  - Does **not** compare `write_paths`, route identity, registered D6 state, or heads.
- **Which cases it can explain**:
  - Could explain any case where `source_sha` and the worktree at callback time differed across more than 128 files (for example, if a branch merge introduced broad upstream changes between `source_sha` and `HEAD` while `expected_result_head` matched `HEAD`). Whether this occurred in Tasks `39065512`, `d6482a29`, `716abbec`, or `43a6832b` is **UNKNOWN** from the supplied evidence.

### Path 2: Empty diff check in `_worktree_snapshot`
- **Location**: [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), function `_worktree_snapshot`.
- **Condition**: `if not changed and not patch: _stop('WORKTREE_SCOPE_MISMATCH')`.
- **What it compares**:
  - Evaluates `changed` (tuple of paths) and `patch` (diff bytes) returned by `_worktree_diff(root, source_sha)`.
  - Does **not** compare `write_paths`, route identity, registered D6 state, or heads (earlier in `_worktree_snapshot`, `snapshot.head != expected_head` stops with `WORKTREE_SOURCE_HEAD_MISMATCH`, and `untracked` paths outside `write_paths` stop with `WORKTREE_UNTRACKED_FILES`).
- **Interaction with route wrappers**:
  - In [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py), `run_task_agy_delegate` and `run_task_gw_agent_delegate` call `task_completion._worktree_diff(registration.root, snapshot.head)` and check `write_paths` and size limits, but do **not** reject an empty diff before setting `developer['state'] = 'DEVELOPER_RESPONDED'` (unlike `run_task_claude_delegate`, which checks `if not changed_paths or not raw` and raises `CLAUDE_NO_CHANGES`). When `run_task_direct_result_event` then calls `collect_executor_worktree_result` -> `_worktree_snapshot`, an empty diff reaches Path 2 and stops with `WORKTREE_SCOPE_MISMATCH`.
- **Which cases it can explain**:
  - Consistent with **Task `4865c6ff`** (#2285, AGY in a D6 from main `f9ad27f6`) and **Task `43a6832b`** (#2362, CI repair, `DONE`, no change kept), as well as **Task `122b2df9`** (#2199, AGY in an OpenCode D6), *if* the worktree had zero diff against `source_sha` at callback time. However, because the Task records and intermediate filesystem states are not readable, the actual diff at callback time for each case is **UNKNOWN**.

### Path 3: `write_paths` boundary check in `_worktree_snapshot`
- **Location**: [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), function `_worktree_snapshot`.
- **Condition**: `if any(path not in set(write_paths) for path in changed): _stop('WORKTREE_SCOPE_MISMATCH')`.
- **What it compares**:
  - Compares every path in `changed` (from `_worktree_diff(root, source_sha)`) against `set(write_paths)`.
  - Does **not** compare route identity, registered D6 state, or heads.
  - Because `collect_executor_worktree_result` and `_worktree_snapshot` both reject any untracked path outside `write_paths` **first** with `WORKTREE_UNTRACKED_FILES`, Path 3 is only reached by **tracked** paths in `git diff --name-only ... <source_sha> --` that are outside `write_paths`. (Additionally, `run_route` in [developer_routes/base.py](../../tools/llmtool/llmtool/developer_routes/base.py) and `run_task_agy_delegate` in [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py) reject out-of-scope changes before `run_task_direct_result_event` with `AGY_WRITE_SCOPE_VIOLATION`.)
- **Which cases it can explain**:
  - **Note on Task `122b2df9` (#2199, AGY in an OpenCode D6)**: Stray untracked files outside `write_paths` do **not** explain `WORKTREE_SCOPE_MISMATCH`, because `collect_executor_worktree_result` and `_worktree_snapshot` reject them with `WORKTREE_UNTRACKED_FILES` (and `run_route` / `run_task_agy_delegate` reject them earlier with `AGY_WRITE_SCOPE_VIOLATION`). The actual cause of Task `122b2df9` is **UNKNOWN** from the supplied evidence.
  - **Note on merge cases (`39065512`, `d6482a29`, `716abbec`, `43a6832b`)**: If `HEAD` differed from `source_sha` (and `expected_result_head` matched `HEAD`, since otherwise `_worktree_snapshot` stops earlier with `WORKTREE_SOURCE_HEAD_MISMATCH`), `git diff <source_sha>` would include tracked files changed between `source_sha` and `HEAD` (such as files brought in by an upstream `origin/main` merge) and trigger Path 3 if any such file was outside `write_paths`. Whether `expected_result_head` was set or whether `source_sha` differed from `HEAD` at callback time in those Tasks is **UNKNOWN** from the supplied evidence.

---

## 3. Lifecycle of the D6 worktree and evidence retention (Acceptance A2)

### Working tree state after stop
- Inspection of `_worktree_snapshot`, `_worktree_diff`, and `collect_executor_worktree_result` in [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), and `run_task_direct_result_event` in [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py), confirms that **none of these functions mutate, reset, revert, or clean the D6 worktree** when `WORKTREE_SCOPE_MISMATCH` occurs.
- No `git reset`, `git checkout`, `git clean`, or file deletion is executed by the completion callback on `WORKTREE_SCOPE_MISMATCH`.
- **Why the D6 is observed clean afterwards**:
  - From the read-scoped code, if the D6 was already clean at callback time (for example, if the Developer did not write changes to the worktree), it remains clean because the callback does not alter it.
  - If changes *were* present in the worktree at callback time, `run_task_direct_result_event` does not remove them. Whether an external process, publish/merge operation, or post-run cleanup outside the read-scoped files modified or cleaned the D6 afterwards is **UNKNOWN**.

### Evidence persistence and callback handling
- In [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py):
  - **Before** `run_task_direct_result_event` is called, `run_task_agy_delegate` (and similarly `run_task_opencode_delegate`, `run_task_gw_agent_delegate`, and `run_task_claude_delegate`) computes `changed_paths, raw = task_completion._worktree_diff(registration.root, snapshot.head)`, decodes `artifact = raw.decode('utf-8', 'strict')`, and persists `developer` and `completion` via `store.update` (or `store.compare_and_update`).
  - Therefore, the attempted diff (`task.developer.artifact` and `task.developer.artifact_sha256`) **is** persisted in TaskStore before `run_task_direct_result_event` runs (if the diff was empty, `task.developer.artifact` is persisted as `""`).
  - Raw provider console output (stdout/stderr) is **not** stored in `task.developer.execution`. Instead, `task.developer.execution` stores only bounded execution metadata: `status`, `native_session_id`, `process_exit_code`, `stdout_sha256` (a SHA-256 digest of stdout), `elapsed_ms`, `cleanup_status`, and `usage_source`.
  - When `run_task_direct_result_event` catches `LLMToolError` from `collect_executor_worktree_result`:
    - It updates `task.developer.direct_result_callback` to `state: 'WAITING_EXECUTOR'`, `evidence.verification_state: 'FAILED'`, and `missing_gates: ['WORKTREE_SCOPE_MISMATCH']`.
    - `direct_result_callback.evidence` has no `result` field because `collect_executor_worktree_result` raised before `evidence.update({'verification_state': 'READY', 'result': result, ...})`.
    - `task.developer.artifact` and `task.developer.execution` remain as persisted prior to the callback.
- **UNKNOWN**:
  - Whether external daemon monitors, worktree provisioning scripts, or remote runners perform asynchronous cleanup on failed worktrees cannot be determined from the read-scoped files and is **UNKNOWN**.
  - The contents of the Task records under `~/.local/state/llmtool/tasks` for the five cited incidents are not readable in this scope and are **UNKNOWN**.

---

## 4. Candidate fixes and risks (Acceptance A3)

### Candidate 1: Disentangle empty diff from `WORKTREE_SCOPE_MISMATCH`
- **Mechanism**:
  - In [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), function `_worktree_snapshot`, replace `_stop('WORKTREE_SCOPE_MISMATCH')` on `if not changed and not patch:` with a dedicated code such as `WORKTREE_DIFF_EMPTY` (or `RESULT_DIFF_EMPTY`, matching `_collect_registered_result_diff`).
  - In [task_submission_cli.py](../../tools/llmtool/llmtool/task_submission_cli.py), functions `run_task_agy_delegate` and `run_task_gw_agent_delegate`, reject empty diffs (`if not changed_paths or not raw:`) before persisting `DEVELOPER_RESPONDED` and calling `run_task_direct_result_event` (analogous to `run_task_claude_delegate` raising `CLAUDE_NO_CHANGES`).
- **Risks & Trade-offs**:
  - Low risk. Existing tests or callers that expect `WORKTREE_SCOPE_MISMATCH` on an empty worktree will need to be updated for the new error code.

### Candidate 2: Exclude trusted-main merges when checking uncommitted worktree scope
- **Mechanism**:
  - In [task_completion.py](../../tools/llmtool/llmtool/task_completion.py), functions `_worktree_snapshot` and `_worktree_diff`, when `HEAD` has advanced beyond `source_sha`, verify whether `HEAD` adds only clean merges of authenticated main (using the same trusted-main attribution pattern as `_developer_changed_paths` and `_developer_tip`) and validate `write_paths` and `_PROPOSAL_FILE_LIMIT` against Developer-attributed paths rather than raw `git diff <source_sha>`.
- **Risks & Trade-offs**:
  - Moderate risk. Misattributing a non-main commit or merge resolution as trusted main would mask out-of-scope edits, so any relaxation must reuse the existing strict trusted-main ancestry and merge-resolution checks in `_developer_changed_paths`.

### Candidate 3: Align `source_sha` / `expected_result_head` on publish-step merges
- **Mechanism**:
  - Ensure that when a PR branch or D6 receives an upstream `origin/main` merge during publish or repair sweeps, the Task's recorded `source_sha` (or the callback's `expected_result_head` together with trusted-main attribution) is updated consistently before the next Developer dispatch or callback verification.
- **Risks & Trade-offs**:
  - Low-to-moderate risk. Advancing `source_sha` must preserve earlier Developer commits on the branch and must not invalidate immutable base references bound to prior completed review records.
