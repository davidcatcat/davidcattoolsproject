# Design Note: Test-Failure Repair Unknown Outcome Deadlock (#2325)

## 1. Problem Statement

Under Issue #2325, when an `llmtool` test-failure repair Developer run terminates with an unknown outcome (`developer.state == 'DEVELOPER_OUTCOME_UNKNOWN'`), the registered D6 worktree remains clean and HEAD is unchanged. Subsequently:
- Every execution of `llmtool task advance` returns `step: REPAIR_OUTCOME_UNKNOWN` with exit code `1`.
- `llmtool task drive` immediately terminates without advancing.
- No CLI command exists or succeeds in transitioning or moving the Task forward.

Observed instances:
- PR #2323 (Task `07c7358d`)
- PR #2326 (Task `06a897a2`)

---

## 2. Root Cause and Behavior Tracing

### 2.1 State Normalization in `derive_repair_state`
- **File**: `tools/llmtool/llmtool/task_advance.py` (lines 2361–2395)
- During `_advance_step`, each claim in `record['review_repair_claims']` is inspected (lines 3229–3241):
  ```python
  repair_record = store.get(claim['repair_task_id'])
  repair_state = (derive_repair_state(repair_record)
                  if type(repair_record) is dict else 'RECORD_UNAVAILABLE')
  ```
- In `derive_repair_state(repair_record)` (lines 2388–2394):
  ```python
  if state == 'DEVELOPER_OUTCOME_UNKNOWN':
      execution = developer.get('execution') if type(developer) is dict else None
      if (developer.get('error_code') == 'CODEX_BLOCKED' or
              (type(execution) is dict and execution.get('status') == 'BLOCKED')):
          return 'BLOCKED'
      return 'UNKNOWN'
  ```
  Unless the developer record specifically carries `error_code == 'CODEX_BLOCKED'` or `execution['status'] == 'BLOCKED'`, `DEVELOPER_OUTCOME_UNKNOWN` normalizes strictly to `'UNKNOWN'`.

### 2.2 Remote Failure Routing in `_remote_fail_action`
- **File**: `tools/llmtool/llmtool/task_advance.py` (lines 2319–2339)
- When evaluating exact-head test failures:
  - In `_route_ci_failure` (lines 1040–1042):
    ```python
    action = _remote_fail_action(related, head)
    if action in ('WAITING_REPAIR', 'REPAIR_OUTCOME_UNKNOWN'):
        return action, [action], None
    ```
  - In `_pre_review_action` (lines 2408–2409) and in `decide` (lines 2453–2454):
    ```python
    if remote_status == 'FAIL':
        return _remote_fail_action(related, result_head)
    ```
- In `_remote_fail_action(related, result_head)` (lines 2324–2338):
  ```python
  if current_repair is not None and current_repair.get('state') == 'DISPATCHED':
      state = current_repair.get('repair_state')
      if state == 'START_FAILED':
          return 'DEVELOPER_START_FAILED'
      if state in ('UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED'):
          return 'REPAIR_RETRY'
      if state in ('RUNNING', 'PENDING'):
          return 'WAITING_REPAIR'
      if state == 'UNKNOWN':
          return 'REPAIR_OUTCOME_UNKNOWN'
      if state == 'RECORD_UNAVAILABLE':
          return 'DEVELOPER_ATTENTION'
      if state == 'RESPONDED':
          return 'PUBLISH'
  ```
  Note the contrast: `UNAVAILABLE`, `BLOCKED`, and `OUTPUT_REJECTED` map to `REPAIR_RETRY`, which triggers automatic re-dispatch via `repair_review(..., retry_claim_id=...)` (lines 3520–3543). However, `state == 'UNKNOWN'` maps directly to `REPAIR_OUTCOME_UNKNOWN`.

### 2.3 Deadlock in `advance()` Execution
- **File**: `tools/llmtool/llmtool/task_advance.py` (lines 3709–3710, 3738–3744)
  ```python
  elif step in ('REPAIR_OUTCOME_UNKNOWN', 'REPAIR_RESULT_NOT_PUBLISHED'):
      codes.append(step)
  ```
- When `step == 'REPAIR_OUTCOME_UNKNOWN'`:
  - No handler initiates a re-dispatch or claim recovery.
  - The advance step emits:
    ```python
    {'task_id': task_id, 'step': 'REPAIR_OUTCOME_UNKNOWN', 'result': None,
     'next': 'REPAIR_OUTCOME_UNKNOWN', 'codes': ['REPAIR_OUTCOME_UNKNOWN']}
    ```
  - In `tools/llmtool/llmtool/task_submission_cli.py` (lines 2107–2116, 2196–2197):
    `_advance_exit_code` returns `1` because `REPAIR_OUTCOME_UNKNOWN` is not an allowed zero-exit code.
  - The Task record is not mutated, leaving the claim in `review_repair_claims` marked `DISPATCHED` pointing to the failed child task indefinitely. Every subsequent invocation repeats the identical check and returns `REPAIR_OUTCOME_UNKNOWN`.

---

## 3. Involved Commands and Durable Records

### 3.1 Commands
1. **`llmtool task advance`** (`tools/llmtool/llmtool/task_submission_cli.py` lines 2143–2198; `tools/llmtool/llmtool/task_advance.py` lines 3102–3744):
   Evaluates the frozen claim and immediately exits with `1` and step `REPAIR_OUTCOME_UNKNOWN`.
2. **`llmtool task drive`** (`tools/llmtool/llmtool/task_submission_cli.py` lines 2200–2275):
   Calls `advance()`, receives `REPAIR_OUTCOME_UNKNOWN`, and halts immediately.
3. **`llmtool task repair`** (`tools/llmtool/llmtool/task_submission_cli.py` lines 2411–2452, parser lines 1079–1095):
   - Mandatory `--pr <int>` parameter (line 1084): cannot run on pre-PR EC2 failures.
   - Dispatches `repair_review`, which expects a failed review verdict on the PR (`outcome == 'REVIEW_FAIL'`); it does not accept EC2 remote-test receipts as the failure basis.
   - While `--recover-claim` exists (lines 2432–2439), it is strictly designed to transition an `UNKNOWN`-state review repair claim to `DISPATCHED` (line 1090). Here, the parent claim is already `DISPATCHED`, but its target repair Task is `DEVELOPER_OUTCOME_UNKNOWN`.
4. **`llmtool task retry-start`** (`tools/llmtool/llmtool/task_submission_cli.py` lines 894–903):
   Restricted to Claude Developer start failures (`DEVELOPER_START_FAILED`) verified by `start_failure.record_checks`. Inapplicable to non-Claude routes (e.g. AGY, OpenCode) and execution-phase unknown outcomes.
5. **`llmtool task delegate`** (`tools/llmtool/llmtool/task_submission_cli.py` lines 904–942, 5376–5377):
   Refuses with `duplicate dispatch refused` because `task['developer']` is already set on the root Task.

### 3.2 Records
1. **Parent Task Record (`TaskStore`)**:
   - `remote_test_receipts`: contains exact-head receipt with `result: 'FAIL'`.
   - `review_repair_claims`: contains active claim with `state: 'DISPATCHED'`, `head_sha: <head>`, and `repair_task_id: <child_id>`.
   - `completion`: holds executor task and D6 binding.
2. **Child Repair Task Record (`TaskStore`)**:
   - Identified by `<child_id>`.
   - `developer.state`: set to `'DEVELOPER_OUTCOME_UNKNOWN'`.
   - `developer.execution.status`: `'UNKNOWN'`.
3. **Registered D6 Worktree**:
   - Clean (`d6['dirty'] is False`).
   - HEAD unchanged (`d6['result_head'] == snapshot.head == source_sha`).

---

## 4. Candidate Fixes and Risk Analysis

### Candidate Fix 1: Route `repair_state == 'UNKNOWN'` to `REPAIR_RETRY` in `_remote_fail_action`
- **Description**:
  In `tools/llmtool/llmtool/task_advance.py` (line 2328), include `'UNKNOWN'` among retriable states:
  ```python
  if state in ('UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED', 'UNKNOWN'):
      return 'REPAIR_RETRY'
  ```
  When `action == 'REPAIR_RETRY'`, `task advance` re-invokes `repair_review(..., retry_claim_id=repair_claim['repair_task_id'])` (lines 3520–3543).
- **Risks to Counts**:
  - *Risk*: An automatic retry of an unknown outcome could loop infinitely if the underlying failure persists (e.g., repeating unhandled route crash or unclassified quota exhaustion).
  - *Mitigation*: Gated by `_test_family_used_up(record, number, head, store)` (line 3528), so it cannot exceed the policy limit (`test_fail_max`). However, if `repair_review` reuses the claim without incrementing attempt count, it could spin within that attempt unless attempt bounds are enforced.
- **Risks to Review Gates**:
  - *Risk*: None. The worktree is verified clean before re-dispatch. If the retried Developer run succeeds, HEAD moves and passes through standard remote-test and formal cross-family review before merge.
- **Risks to Dispatch Gating**:
  - Must ensure worker dispatch policy in `worker-dispatch.json` is re-evaluated before re-dispatch. If a route was paused due to quota exhaustion, automatic retry must halt cleanly rather than bypassing dispatch policy.

### Candidate Fix 2: Provide an Explicit PM Recovery Command / Parameter for Stuck Repair Claims
- **Description**:
  Extend `llmtool task repair` (or add a dedicated CLI action such as `llmtool task repair --retry-claim <task_id>`) that admits test-failure repairs (without requiring `--pr` or a formal review FAIL verdict).
  The command verifies that the registered D6 is clean, marks the prior `DISPATCHED` claim with `state: 'ABANDONED'` or `state: 'FAILED'`, and triggers a fresh repair dispatch.
- **Risks to Counts**:
  - *Advantage*: Prevents automated retry loops because progress requires explicit PM intervention.
  - *Risk*: Counting must correctly reflect Owner decision D1 (maximum 2 repairs per test failure family). If an abandoned attempt is uncounted, it could violate the 2-repair ceiling; if counted, the PM must be informed that one repair attempt was spent on the unknown outcome.
- **Risks to Review Gates**:
  - *Risk*: None. Standard review separation and exact-head CI gates remain intact.
- **Risks to Dispatch Gating**:
  - Requires fresh dispatch evaluation against `worker-dispatch.json`, adhering strictly to dispatch rules.

### Candidate Fix 3: Map Clean-Worktree `DEVELOPER_OUTCOME_UNKNOWN` to Actionable Attention in `derive_repair_state` / `_remote_fail_action`
- **Description**:
  In `tools/llmtool/llmtool/task_advance.py`:
  - When `derive_repair_state()` observes `DEVELOPER_OUTCOME_UNKNOWN` on a clean, unchanged worktree, return `'DEVELOPER_ATTENTION'` or a distinct actionable state (e.g. `'REPAIR_FAILED'`).
  - In `_remote_fail_action()`, map this to `'DEVELOPER_ATTENTION'` with code `'REPAIR_OUTCOME_UNKNOWN'`, allowing `task advance` to present an actionable hold for PM decision (such as replan, route switch, or retry) rather than a dead-end `REPAIR_OUTCOME_UNKNOWN` code.
- **Risks to Counts**:
  - Low risk: Halts advancement cleanly under PM attention. The PM can choose to replan, drop, or retry, ensuring attempt counts conform to `repair_policy`.
- **Risks to Review Gates**:
  - None. No code is published or reviewed while the task waits for attention.
- **Risks to Dispatch Gating**:
  - Aligns with AGENTS.md policy: on quota exhaustion or unknown Developer outcome, bounded task progress is recorded and Writer ownership is released without automatic paid fallback.
