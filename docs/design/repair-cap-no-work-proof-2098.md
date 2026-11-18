# #2098 design: proving a failed Developer run did no work

Proposal only; not a review or acceptance. Source: main at 9416bdd02439e09d94d3c5d78f5ce407c7593edd. Claims cite path:line at that SHA; anything not read is UNVERIFIED. The change is 已實作（#2098）; merged in PR #2107 with follow-up tests in PR #2144.

**Status (2026-11-17):** PR #2107 implemented this note's recommendation: post-run Developer route refusals carry and persist execution evidence. PR #2144 added the follow-up tests. The sections below are the original proposal, kept for history.

## 1. Today (現行)

- **Repair-cap gate and run counting**:
  - In `tools/llmtool/llmtool/task_submission_cli.py`, the repair count evaluator `_repair_run_stamp(task_id, developer, d6_unchanged)` (`:7324-7360`) inspects developer attempt records to determine if a run consumes a bounded repair attempt.
  - By default, every launched run is assigned a timestamp at `:7359-7360` and counted against the repair cap unless it satisfies one of two specific exemption paths:
    1. A known pre-launch refusal (`:7341-7356`); or
    2. A launched run whose record affirmatively proves it did no work via `_launched_run_did_no_work(developer, d6_unchanged)` (`:7357-7358`).

- **Pre-launch refusal list**:
  - Defined in `_PRELAUNCH_REPAIR_REFUSALS` (`tools/llmtool/llmtool/task_submission_cli.py:7068-7081`), the pre-launch list exempts runs refused before provider invocation:
    - `('codex', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_PROFILE_UNAVAILABLE')` (`:7069-7070`)
    - `('codex', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_CALL_BINDING_INVALID')` (`:7071-7072`)
    - `('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CONFIG_INVALID')` (`:7073-7074`)
    - `('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_DENIED')` (`:7075-7076`)
    - `('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_DISPATCH_DENIED')` (`:7077-7078`)
    - `('agy', 'BLOCKED', 'AGY_DISPATCH_DENIED')` (`:7079`)
    - `('claude', 'BLOCKED', 'CLAUDE_DISPATCH_DENIED')` (`:7080`)
  - When matching `_PRELAUNCH_REPAIR_REFUSALS`, `:7345-7356` verifies that `artifact`, `artifact_sha256`, `execution`, and `usage` are all `None`, `changed_files` is empty or absent, and `progress` is absent before skipping the run.

- **Criteria in `_launched_run_did_no_work`**:
  - Implemented in `tools/llmtool/llmtool/task_submission_cli.py:7084-7128`, a launched run is proven to have done no work only if all of the following conditions hold simultaneously:
    1. **Terminal state**: `state == 'BLOCKED'` (`:7098-7099`), or `state == 'DEVELOPER_OUTCOME_UNKNOWN'` with an `error_code` matching regex `^[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:BLOCKED|INTERRUPTED)$` (`:7100-7103`).
    2. **Null artifacts**: Both `artifact` and `artifact_sha256` keys are present in `developer` and both are strictly `None` (`:7108-7110`).
    3. **No file modifications**: If `changed_files` is present, it must be a list and must be empty (`:7111-7114`).
    4. **Confirmed boundary cleanup**: `execution` must be a dictionary and `execution.get('cleanup_status') == 'CONFIRMED'` (`:7115-7118`).
    5. **Clean working tree at source SHA**: `source_sha` must be a valid 40-character hexadecimal string, `d6_unchanged` callable must be provided, and `d6_unchanged(source_sha)` must return `True` (`:7119-7127`).
  - *Fail-closed guarantee*: If any check fails, or if any field is missing, malformed, or unverified, `_launched_run_did_no_work` returns `False` (`:7095`, `:7107`, `:7110`, `:7114`, `:7118`, `:7123`, `:7127`), causing `_repair_run_stamp` to count the run.

## 2. The #2098 case

- **Incident summary (Task dba5cc36)**:
  - Task `dba5cc36` was dispatched to Claude Developer and encountered a post-run refusal ending in `state='BLOCKED'`, `error_code='CLAUDE_STATUS_FAILED'`, and `execution=None`.
  - The Claude CLI process had executed, completed, and exited, leaving the D6 worktree completely clean and identical to `source_sha`. However, the run was counted against the repair cap because `execution` was `None`, failing condition 4 of `_launched_run_did_no_work` (`task_submission_cli.py:7116`).

- **Mechanism of execution data loss**:
  1. *Delegate initialization*: In `tools/llmtool/llmtool/task_submission_cli.py:5860-5871`, the initial `developer` dict is created with `'execution': None` (`:5867`).
  2. *Runner execution*: The delegate invokes `execute(route, ...)` at `task_submission_cli.py:5936-5941`. Inside `run_route` (`tools/llmtool/llmtool/developer_routes/base.py:843`), the provider process executes `code, stdout, stderr = execute(argv, value['root'], env, value['timeout'])`. The process terminates, and private temporary files are removed (`base.py:880`).
  3. *Validation failure*: In `base.py:949`, `run_route` calls `route.validate_status(parsed)`. In `tools/llmtool/llmtool/developer_routes/claude.py:166-169`, `validate_status` checks `if parsed.get('status') != 'DONE': raise RouteError('CLAUDE_STATUS_FAILED')`.
  4. *RouteError bubbling*: `run_route` does not catch `RouteError` around `validate_status` (`base.py:950-951`); the exception propagates directly out of `execute(route, ...)`. Consequently, `result` remains `None` in `task_submission_cli.py:5935`.
  5. *RouteError handling in delegate*: In `task_submission_cli.py:5983-6003`, the exception handler catches `RouteError as exc`.
     - Lines 5987-5998 only construct `execution` when `result is not None` and `exc.code` is one of `('CLAUDE_NO_CHANGES', 'CLAUDE_WRITE_SCOPE_VIOLATION', 'CLAUDE_OUTPUT_TOO_LARGE', 'CLAUDE_D6_BINDING_CHANGED')`.
     - Because `result is None`, execution falls to lines 5999-6003: `elif result is None and not owner_gate_denied: condition = _condition_execution(getattr(exc, 'provider_evidence', None), 'claude', 'BLOCKED')`.
     - For `CLAUDE_STATUS_FAILED`, `exc.provider_evidence` is `None`, so `condition` is `None` and `developer['execution']` remains `None`.
  6. *Unproven cleanup*: When `_repair_run_stamp` evaluates the record, `CLAUDE_STATUS_FAILED` is absent from `_PRELAUNCH_REPAIR_REFUSALS`, and `_launched_run_did_no_work` fails at `:7116` because `execution` is `None`. Even though cleanup occurred at the boundary, cleanup status is unproven in the task record, so the run counts.

- **Post-run refusals in `run_route` with the same effect**:
  All of the following post-run checks in `tools/llmtool/llmtool/developer_routes/base.py:860-977` execute after process termination (`:843`) and raise `RouteError` without attaching fixed execution facts, leaving `result is None` and `developer['execution'] = None` in the delegate:
  - `COLLECT_FAILED` (`base.py:866`): raised if post-run file collection from private temporary directory fails.
  - `MODEL_UNVERIFIED` (`base.py:873`, `875`, `957`, `959`): raised if raw log size exceeds 4 MiB or model verification fails.
  - `RUNNER_RESULT_INVALID` (`base.py:883`): raised if returncode, stdout, or stderr violate types or buffer limits.
  - `GIT_SNAPSHOT_FAILED` (`base.py:888`, `903`): raised if post-run git snapshot capture fails.
  - `GIT_IDENTITY_CHANGED`, `GIT_COMMIT_CHANGED`, `GIT_BRANCH_CHANGED` (`base.py:898`): raised if post-run git metadata or commits were altered.
  - `OUTPUT_INVALID` (`base.py:913`, `915`, `921`, `925`, `953`, `977`): raised if output parsing, exit validation, or result building raises an unexpected error or malformed structure.
  - `WRITE_SCOPE_VIOLATION` (`base.py:931`): raised if changed paths or git status paths exceed `write_paths`. (Carries `changed_files` in facts, but no execution facts like `cleanup_status`).
  - `QUOTA_EXHAUSTED` (`base.py:944`): raised when quota exhausted without positive exit evidence.
  - Status failure (e.g. `CLAUDE_STATUS_FAILED`, `AGY_STATUS_FAILED` via `route.validate_status` at `base.py:949`).
  - `MODEL_MISMATCH` (`base.py:961`): raised if verified model differs from requested model.
  - `INTERRUPTED` (`base.py:963`): raised if process exit code < 0.
  - `EXIT_NONZERO` (`base.py:965`): raised if process exit code != 0.

## 3. Options

### Option (a): Post-run RouteErrors carry fixed execution facts, persisted by delegates on BLOCKED records
- **Mechanics**:
  - When `run_route` in `developer_routes/base.py` raises `RouteError` after runner execution (`:843`), it attaches fixed execution facts: `process_exit_code` (`code`), `cleanup_status` (`getattr(execute, 'last_cleanup_status', 'UNKNOWN')`), `elapsed_ms`, and `stdout_sha256`.
  - Route delegates in `task_submission_cli.py` (e.g. `run_task_claude_developer`, `run_task_agy_developer`) catch `RouteError`, extract these facts from `exc`, and persist `developer['execution'] = {'status': 'BLOCKED', 'process_exit_code': code, 'cleanup_status': cleanup_status, 'elapsed_ms': elapsed_ms, ...}`.
- **Security effect on repair cap**:
  - Preserves strict fail-closed enforcement while eliminating false penalties. The repair cap prevents infinite loops and unbudgeted compute. Exemption is granted only when backed by positive cryptographic and boundary proof (`cleanup_status == 'CONFIRMED'`, `d6_unchanged(source_sha) is True`, `artifact is None`). If boundary cleanup was incomplete or unknown, the run continues to be counted.
- **Files touched**:
  - `tools/llmtool/llmtool/developer_routes/base.py`: pass execution facts to `RouteError` in post-run checks.
  - `tools/llmtool/llmtool/task_submission_cli.py`: populate `developer['execution']` from `RouteError` facts when `result is None`.
- **Schema impact on `task_store developer.execution`**:
  - Zero schema breaking changes. `developer.execution` already accepts mapping objects containing `status`, `cleanup_status`, `process_exit_code`, and `elapsed_ms` (identical to `:5991-5998`). No version bump required.

### Option (b): Treat a post-run refusal with `execution None` as no work when D6 is unchanged
- **Mechanics**:
  - Modify `_launched_run_did_no_work` (`task_submission_cli.py:7115-7118`) to allow `execution is None` if `d6_unchanged(source_sha)` returns `True`.
- **Security effect on repair cap**:
  - Weakens security and violates fail-closed principles. Absence of execution data is not proof of cleanup. If a runner crashes or aborts without confirming boundary cleanup, background processes or temporary socket files may remain. Assuming "no work" merely from clean git status without verified cleanup status creates a bypass where corrupted runs evade the repair cap.
- **Files touched**:
  - `tools/llmtool/llmtool/task_submission_cli.py` (relaxing `_launched_run_did_no_work`).
- **Schema impact on `task_store developer.execution`**:
  - None, but leaves permanently empty `execution: None` records in the task store, destroying execution provenance.

### Option (c): Keep counting
- **Mechanics**:
  - Make no code changes; maintain existing behavior.
- **Security effect on repair cap**:
  - Strictly fail-closed, but penalizes benign CLI refusals. A run where the provider CLI cleanly refused or returned a non-DONE status with zero file edits burns a repair attempt, causing tasks to exhaust their repair limit prematurely.
- **Files touched**:
  - None.
- **Schema impact on `task_store developer.execution`**:
  - None.

## 4. Recommendation

- **Preferred approach**: Adopt **Option (a)**.
  - Conforms to repository AI collaboration guidance on evidence honesty (AGENTS.md rule 6 / `skills/role-cards/common-core.md` §6): execution facts must be observed and persisted from boundary evidence, never inferred from absence.
- **Rejection of Option (b)**:
  - Option (b) is rejected because clean D6 git status does not prove boundary process termination or workspace resource cleanup. Without affirmative evidence that `cleanup_status == 'CONFIRMED'`, a run must not be treated as clean.
- **Fail-closed rules**:
  - Any missing, malformed, or unconfirmed fact keeps counting the run. If `cleanup_status` is missing, `'UNKNOWN'`, or `'FAILED'`, `_launched_run_did_no_work` must return `False`.
  - If D6 has any untracked or modified files, or if D6 HEAD differs from `source_sha`, the run must count.
  - If `artifact` or `artifact_sha256` is non-None, or `changed_files` is non-empty, the run must count.
- **Boundaries unchanged**:
  - *Pre-launch refusals*: The `_PRELAUNCH_REPAIR_REFUSALS` mechanism is untouched. Runs refused prior to launch require no execution facts and continue to be exempted based on dispatch validation.
  - *In-flight failures*: Hard process crashes, runner exceptions prior to exit, and unconfirmed timeouts that do not yield `cleanup_status == 'CONFIRMED'` must continue to count against the repair cap.

## 5. Tests to add later

All test cases must strictly follow the TEST DATA RULE (Owner #763 A): test cases must use synthetic/mock fixtures with zero credential-shaped strings. Screen functions and runner outputs are mocked or monkeypatched.

1. `test_repair_count_claude_status_failed_with_execution_facts_clean_d6_skipped`:
   - Claude Developer run fails with `CLAUDE_STATUS_FAILED`.
   - Persisted record has `execution` dict with `cleanup_status='CONFIRMED'`, `process_exit_code=1`, and `elapsed_ms=120`.
   - `artifact` and `artifact_sha256` are `None`; D6 is clean and HEAD equals `source_sha`.
   - Assert: `_launched_run_did_no_work` returns `True`; `_repair_run_stamp` returns `None` (run not counted).

2. `test_repair_count_claude_status_failed_with_dirty_d6_counted`:
   - Same as case 1, but D6 contains an unstaged file modification or untracked file (`d6_unchanged` returns `False`).
   - Assert: `_launched_run_did_no_work` returns `False`; `_repair_run_stamp` returns timestamp (run counted).

3. `test_repair_count_claude_status_failed_missing_cleanup_counted`:
   - Claude Developer run fails with `CLAUDE_STATUS_FAILED`.
   - Persisted `execution` record has `cleanup_status='UNKNOWN'` or `cleanup_status='FAILED'`, or `cleanup_status` key omitted.
   - Assert: `_launched_run_did_no_work` returns `False`; run counted against repair cap.

4. `test_repair_count_agy_post_run_refusal_skipped_when_clean_and_confirmed`:
   - AGY Developer run encounters post-run refusal (`AGY_EXIT_NONZERO` or `AGY_STATUS_FAILED`).
   - `execution` dict records `cleanup_status='CONFIRMED'`, `process_exit_code=1`, `artifact=None`, D6 clean at `source_sha`.
   - Assert: `_launched_run_did_no_work` returns `True`; run not counted.

5. `test_repair_count_unknown_error_code_or_corrupted_facts_counted`:
   - Task record has an unrecognized error code not matching `r'[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:BLOCKED|INTERRUPTED)'`, or `execution` is not a dict.
   - Assert: fails closed; `_launched_run_did_no_work` returns `False`; run counted.

6. `test_repair_count_prelaunch_list_unchanged`:
   - Verifies that all entries in `_PRELAUNCH_REPAIR_REFUSALS` (e.g. `CLAUDE_DISPATCH_DENIED`, `AGY_DISPATCH_DENIED`, `DEVELOPER_PROFILE_UNAVAILABLE`) continue to be skipped via `:7341-7356` without requiring execution records.

## 6. Out of scope

- Modifications to maximum repair attempt thresholds or repair-cap numeric limits.
- Changes to `policy.json` schema or policy dispatch configurations.
- Modifications to review repair chain sequencing, arbitrator selection, or reviewer re-evaluation rules.
