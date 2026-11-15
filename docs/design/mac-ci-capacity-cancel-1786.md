# #1786 design: Mac CI capacity cancel is not a test failure

Proposal only; not a review or acceptance. Source: `main` at `6091cda09b9a67b13ad8f3a2d6b254b53106733b`. Claims about today cite exact `path:line` at that SHA. Anything not read is marked UNVERIFIED. Implemented by PR #2082; section 7 records where the implementation differs from this proposal.

## 1. Today (現行)

- **Workflow structure**: In `.github/workflows/llmtool-mac-ci.yml`, the PR workflow runs across three staged jobs:
  - `select` (`.github/workflows/llmtool-mac-ci.yml:44-60`): targets `macos-15` (arm64) unless the PR carries the `mac-ci-intel` label (`:47`). When GitHub hosted capacity has no arm64 runner available, or runner provisioning times out, `select` is cancelled or aborted with `conclusion: cancelled` or `startup_failure`.
  - `shards` (`.github/workflows/llmtool-mac-ci.yml:159-170`): depends on `select` (`needs: select`, `:161`) with conditional `if: needs.select.outputs.mode == 'full' && needs.select.outputs.run != 'false'` (`:162`). When `select` cancels or fails at startup, GitHub Actions skips all shard matrix jobs (`conclusion: skipped`).
  - `check (macOS 3.13)` (`.github/workflows/llmtool-mac-ci.yml:373-430`): gates mergeability, depending on both jobs (`needs: [select, shards]`, `:375`) with an unconditional run gate `if: always() && ...` (`:376`).
  - In the `Shard results` step (`.github/workflows/llmtool-mac-ci.yml:415-425`), the job inspects `SELECT_RESULT: ${{ needs.select.result }}` and `SHARDS_RESULT: ${{ needs.shards.result }}` (`:418-419`). Because `select` cancelled and `shards` skipped, neither succeeded (`:422`), causing the step to execute:
    ```bash
    echo "::error::full mode requires select and shards to succeed"
    exit 1
    ```
    This non-zero exit registers the GitHub check run for `check (macOS 3.13)` with `conclusion: failure`, even though zero test suites ran or failed.
- **Advance routing and false test failure count**:
  - In `tools/llmtool/llmtool/task_advance.py`, `_MAC_CHECK = 'check (macOS 3.13)'` (`:775`) and `_CI_ENV_CONCLUSIONS = ('cancelled', 'timed_out', 'startup_failure')` (`:778`).
  - In `_route_ci_failure` (`tools/llmtool/llmtool/task_advance.py:867-928`), the function inspects `conclusion = run['conclusion']` (`:877`).
  - When a check run itself is cancelled or times out, lines 878-880 match `_CI_ENV_CONCLUSIONS` and return `('WAITING_ENV', ['CI_ENV_' + conclusion.upper()], ...)`.
  - However, because `check (macOS 3.13)` completed its execution and exited 1 at `:424`, GitHub sets its check run conclusion to `'failure'`.
  - `_route_ci_failure` proceeds past the environmental check (`:881`). It checks `_test_family_used_up(record, number, head, store)` (`:899`), which transitions to `STOPPED` with `TEST_FAIL_LIMIT_REACHED` (`:900`) if the retry limit was reached.
  - It then constructs `ci_failure = {'head': head, 'check_name': run['name'], 'check_run_id': run['id'], 'conclusion': 'failure', 'excerpt': _ci_excerpt(run)}` (`:906-907`) and dispatches repair to the developer via `review_repair.repair_review` (`:908-925`).
  - During PR integration and merge gating in `_advance_once` (`tools/llmtool/llmtool/task_advance.py:3086-3095` and `:3379-3388`), this false failure triggers a developer repair cycle, burning the Task's bounded test/repair failure quota.
  - *Unverified details*: The internal implementation of `review_repair.repair_review` and GitHub hosted runner queue scheduling outside the cited lines are UNVERIFIED.

## 2. Proposed classification

- **Sibling job inspection**:
  - When `_MAC_CHECK` (`check (macOS 3.13)`) finishes with `conclusion: failure`, `task_advance` inspects the sibling workflow jobs under the same GitHub Actions workflow run (e.g. via GitHub API `GET /repos/{owner}/{repo}/actions/runs/{workflow_run_id}/jobs`).
  - It examines the conclusions of all jobs in the workflow run (`select`, `shards (1/3)`, `shards (2/3)`, `shards (3/3)`, and other test jobs).
- **Capacity cancel detection criteria**:
  - The check failure is classified as an infrastructure capacity cancellation when:
    1. `select` or any `shards` job has a conclusion in `('cancelled', 'skipped', 'startup_failure')`; and
    2. No test job or shard concluded with `failure` (i.e. no actual test execution failed).
- **Resulting state and consequences**:
  - Classification: `WAITING_CI_CAPACITY` (`NOT_RUN`), distinct from `CI_FAILED`.
  - Code returned: `['CI_MAC_CAPACITY_CANCELLED']` (or `WAITING_CI_CAPACITY`).
  - No repair dispatch: `review_repair.repair_review` is not invoked.
  - No test-failure count: Neither `test_fail_count` nor `_test_family_used_up` is incremented.
  - The task safely waits for CI capacity recovery or a fresh workflow re-run without exhausting Developer repair attempts.

## 3. Evidence fields

- **Persisted evidence record fields**:
  - `check_name`: The check name (`check (macOS 3.13)`).
  - `workflow_run_id`: GitHub Actions workflow run integer ID.
  - `job_names_and_conclusions`: A bounded dictionary mapping sibling job names to their respective conclusion states (e.g. `{"select": "cancelled", "shards (1/3)": "skipped"}`).
  - `head_sha`: The 40-hex Git commit SHA validated against the PR and D6 `result_head`.
  - `runner_label_class`: The requested or assigned runner label class (e.g. `macos-15` or `macos-15-intel`).
  - `classification`: Explicit state string `WAITING_CI_CAPACITY`.
- **Strictly excluded fields (never recorded)**:
  - Workflow logs, step console outputs, stdout, stderr, or runner trace diagnostics.
  - Authentication tokens, API keys, credentials, or session cookies.
  - Local filesystem paths outside the repository (only repository-relative paths allowed).
  - User private data, personal identifiers, or unscrubbed external URLs.

## 4. Fail-closed cases

When sibling job evidence is ambiguous, incomplete, or corrupted, `task_advance` fails closed to prevent incorrect state transitions:

1. **Job list unreadable, partial, or paginated beyond limit**:
   - Condition: GitHub API fails to return the job list, the payload is malformed/truncated, `total_count` does not match, or jobs exceed the single-page limit (>100 jobs).
   - Behavior: Keeps today's `WAITING` (`WAITING_CI` with reason code `MAC_CI_UNAVAILABLE`).
   - Count impact: Does not spend the test-failure count; does not dispatch repair.
2. **Mixed cancel and real test failure**:
   - Condition: `select` or a shard was cancelled or skipped, but at least one test job or shard ran and concluded with `failure` (real test execution defect).
   - Behavior: Keeps today's `CI_FAILED`.
   - Count impact: Spends the test-failure count; dispatches repair via `_route_ci_failure`.
3. **Head mismatch**:
   - Condition: The check run or workflow run `head_sha` does not match the D6 task `result_head`.
   - Behavior: Keeps today's `WAITING` (`WAITING_CI` with stale head indication).
   - Count impact: Does not spend the test-failure count; does not dispatch repair.
4. **Unknown job conclusion**:
   - Condition: A sibling job reports an unrecognized conclusion (outside `success`, `failure`, `cancelled`, `skipped`, `timed_out`, `startup_failure`, `neutral`, `action_required`).
   - Behavior: Keeps today's `WAITING` (`WAITING_CI` with reason code `CI_JOB_CONCLUSION_UNKNOWN`).
   - Count impact: Does not spend the test-failure count; does not dispatch repair until a conclusive state is reached.

Amendment (PM_DECIDE #1786 c6889125309): cases 1, 3 and 4, and a missing or unmatched details_url, keep today's counted CI_FAILED routing instead of WAITING; no exemption without positive evidence.

## 5. Out of scope

To respect architectural boundaries and Owner authority, the following areas are strictly out of scope:
- No changes to GitHub Actions workflow trigger definitions, paths, or event specifications in `.github/workflows/llmtool-mac-ci.yml`.
- No modifications to the workflow YAML logic or step implementations.
- No changes to repository PR label rules (`mac-ci`, `mac-ci-intel`).
- No modifications to the Owner's `mac-ci-intel` fallback policy or manual dispatch requirements.
- No modifications to Mac FULL workflow scheduling cadence (`full-mac-due`, 30-minute quiet period).
- No changes to maximum test repair count limits or policy parameters (all CI governance changes remain reserved to the Owner).

## 6. Tests to add later

PR #2082 adds these unit test cases in `tools/llmtool/tests/test_task_advance_mac_capacity.py` (expectations as amended in section 7) covering fake sibling job lists:

1. `test_mac_ci_capacity_cancel_select_cancelled_shards_skipped`:
   - Setup: `select` has `conclusion: "cancelled"`, shards (1/3, 2/3, 3/3) have `conclusion: "skipped"`, `check (macOS 3.13)` has `conclusion: "failure"`.
   - Expectation: Returns `WAITING_CI_CAPACITY` (`NOT_RUN`), no repair dispatched, test fail count remains unchanged.
2. `test_mac_ci_capacity_cancel_select_startup_failure`:
   - Setup: `select` has `conclusion: "startup_failure"`, shards have `conclusion: "skipped"`, check has `conclusion: "failure"`.
   - Expectation: Returns `WAITING_CI_CAPACITY`, no repair dispatched, count unspent.
3. `test_mac_ci_capacity_cancel_shard_cancelled_no_test_failure`:
   - Setup: `select` succeeded, shard 1 has `conclusion: "cancelled"`, shard 2-3 skipped, no job has `conclusion: "failure"`.
   - Expectation: Returns `WAITING_CI_CAPACITY`, no repair dispatched, count unspent.
4. `test_mac_ci_fail_closed_mixed_cancel_and_real_failure`:
   - Setup: `select` succeeded, shard 1 has `conclusion: "failure"` (real test failure), shard 2 has `conclusion: "cancelled"`.
   - Expectation: Returns `CI_FAILED`, routes to repair via `_route_ci_failure`, spends one test failure count.
5. `test_mac_ci_fail_closed_jobs_api_unreadable_or_paginated`:
   - Setup: GitHub API returns HTTP error or payload with `total_count > 100` (exceeding page limit).
   - Expectation: Returns `WAITING_CI` with `MAC_CI_UNAVAILABLE`, does not dispatch repair, test failure count unspent.
6. `test_mac_ci_fail_closed_head_mismatch`:
   - Setup: Workflow run reports `head_sha` different from D6 task `result_head`.
   - Expectation: Returns `WAITING_CI`, does not treat as capacity cancel or test failure, count unspent.
7. `test_mac_ci_fail_closed_unknown_job_conclusion`:
   - Setup: A sibling job reports `conclusion: "unexpected_status"`.
   - Expectation: Returns `WAITING_CI` (or fail-closed waiting), no repair dispatch, count unspent.

## 7. Amendments (as implemented, PR #2082)

- **Result code:** a proven capacity cancel returns `WAITING_ENV` with code `CI_ENV_MAC_CAPACITY_CANCELLED` (not `WAITING_CI_CAPACITY`). It dispatches no repair and spends no test-failure count.
- **No exemption without positive evidence (PM_DECIDE #1786 c6889125309):** cases 1, 3 and 4 of section 4, and a missing or unmatched `details_url`, keep today's counted `CI_FAILED` routing instead of waiting.
- **Trusted evidence only (Codex security v91 on #2081):** the exemption applies only when the PR changes nothing under `.github/`, the workflow run is `.github/workflows/llmtool-mac-ci.yml` at this head, and the `details_url` job id is the check job's id.
- **Timeouts never qualify (Codex security v92, AI Debug #2082 c6890125782):** `timed_out` on `select` or any shard vetoes the exemption, because PR-controlled selector code can cause it. Only `cancelled` and `startup_failure` qualify, and they are interrupted CI evidence, not proof of capacity.
- **Subset mode:** `select` success with every shard skipped is a mac-subset run and stays a counted test failure (PM_DECIDE #1786 c6889094590).
