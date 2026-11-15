# #2098 addendum: no-work proof for the OpenCode and gw-agent routes

Proposal only; not a review or acceptance. Source: main at a863ace18f68216a69d9db43bdb4a623844918d5. Extends design #2103 (Claude/AGY, PR #2107). Claims cite path:line; anything not read is UNVERIFIED. The change is 待實作（#2098）.

## 1. Today

- **OpenCode attempted failures**:
  - In `tools/llmtool/llmtool/task_submission_cli.py:4993-5020`, when direct OpenCode execution fails after a process attempt (`exc.attempted is True`), the failure is persisted as `developer['state'] = 'DEVELOPER_OUTCOME_UNKNOWN'` (`task_submission_cli.py:5000`) with `error_code = exc.direct_opencode_code` (`task_submission_cli.py:5003`).
  - Unlike early Claude/AGY failure paths where `execution` was omitted or left `None`, the OpenCode delegate constructs and stores an `execution` dictionary immediately (`task_submission_cli.py:5004-5014`). This dictionary already records `cleanup_status = exc.cleanup_status` (`task_submission_cli.py:5009`).
  - In `tools/llmtool/llmtool/opencode_direct_developer.py:929, 931-932`, `exc.cleanup_status` is populated directly from lifecycle tracking: `cleanup = 'CONFIRMED' if lifecycle.cleanup_confirmed else 'UNKNOWN'`. It is attached to `DirectOpenCodeError` on process non-zero exit (`opencode_direct_developer.py:1001`), process exit missing (`opencode_direct_developer.py:986`), or event parsing failures (`opencode_direct_developer.py:1013`).
  - Thus, OpenCode execution records already affirmatively carry `cleanup_status == 'CONFIRMED'` whenever boundary cleanup completes successfully.

- **gw-agent route failures**:
  - In `tools/llmtool/llmtool/task_submission_cli.py:6229-6265`, `persist_failure(error_code, *, state='DEVELOPER_UNAVAILABLE', provider_evidence=None, interrupted=False)` persists gw-agent route failures. By default, it sets `state = 'DEVELOPER_UNAVAILABLE'` (`task_submission_cli.py:6229, 6235`).
  - When `developer.get('execution')` is already a dictionary (from an observed run), `persist_failure` retains what the run observed (`task_submission_cli.py:6237-6249`), setting `observed = {**developer['execution'], 'status': 'UNKNOWN', 'usage_source': 'UNKNOWN'}` (`task_submission_cli.py:6240-6241`).
  - For route quota refusals where no execution dict exists, `persist_failure` stores parsed condition evidence with `status = 'UNKNOWN'` (`task_submission_cli.py:6250-6254`).
  - In all normal post-run failure cases persisted by `persist_failure`, the persisted record retains `state = 'DEVELOPER_UNAVAILABLE'` with an error code, and execution status is kept as `'UNKNOWN'`.

- **Why `_launched_run_did_no_work` never treats either as ended**:
  - In `tools/llmtool/llmtool/task_submission_cli.py:7084-7128`, `_launched_run_did_no_work(developer, d6_unchanged=None)` evaluates whether a launched run has affirmatively proven it did no work.
  - The terminal "ended" predicate is evaluated strictly at `task_submission_cli.py:7096-7105`:
    ```python
    state = developer.get('state')
    error_code = developer.get('error_code')
    if state == 'BLOCKED':
        ended = True
    elif state == 'DEVELOPER_OUTCOME_UNKNOWN':
        ended = (type(error_code) is str and
                 re.fullmatch(r'[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:BLOCKED|INTERRUPTED)',
                              error_code) is not None)
    else:
        ended = False
    if not ended:
        return False
    ```
  - **OpenCode runs**: State is `DEVELOPER_OUTCOME_UNKNOWN` (`task_submission_cli.py:5000`). However, OpenCode error codes are `OPENCODE_*` strings (e.g. `OPENCODE_PROVIDER_FAILED` at `opencode_direct_developer.py:996`, `OPENCODE_EXIT_UNKNOWN` at `opencode_direct_developer.py:985`, or `OPENCODE_ADAPTER_UNKNOWN` at `task_submission_cli.py:5037`). None of these codes terminate with `_BLOCKED` or `_INTERRUPTED`. Consequently, `re.fullmatch` returns `None` (`task_submission_cli.py:7102-7103`), `ended` becomes `False` (`task_submission_cli.py:7105`), and `_launched_run_did_no_work` returns `False` (`task_submission_cli.py:7107`).
  - **gw-agent runs**: State is persisted as `DEVELOPER_UNAVAILABLE` (`task_submission_cli.py:6229, 6235`). Because this state is neither `'BLOCKED'` nor `'DEVELOPER_OUTCOME_UNKNOWN'`, evaluation falls through to `else: ended = False` (`task_submission_cli.py:7104-7105`). The error code is not matched against any pattern, and `_launched_run_did_no_work` immediately returns `False` (`task_submission_cli.py:7107`).
  - **Result**: Because neither route's failure records qualify under the `ended` state/error-code rule, `_launched_run_did_no_work` unconditionally rejects them at `task_submission_cli.py:7107`. Therefore, `_repair_run_stamp` always assigns a timestamp and counts these failed runs against the repair cap, even when the D6 worktree was unmodified and boundary cleanup was confirmed.

## 2. Options

### Option (a): Extend the 'ended' rule with an explicit allow-list of (provider_profile, state, error_code)

- **Mechanics**:
  - Keep delegate failure persistence exactly as implemented today.
  - In `tools/llmtool/llmtool/task_submission_cli.py:7084-7107`, expand the definition of `ended` to inspect a hardcoded, fail-closed set of allowed triples: `_NO_WORK_POST_RUN_REFUSALS = frozenset({(provider_profile, state, error_code), ...})`.
  - A record qualifies as `ended = True` if:
    1. It matches existing rules (`state == 'BLOCKED'`, or `state == 'DEVELOPER_OUTCOME_UNKNOWN'` with `_(?:BLOCKED|INTERRUPTED)`); OR
    2. `(developer.get('provider_profile'), developer.get('state'), developer.get('error_code'))` is an exact member of `_NO_WORK_POST_RUN_REFUSALS`.
  - Once `ended` is satisfied, the run must still pass all existing stringent checks in `_launched_run_did_no_work` (`task_submission_cli.py:7108-7127`): `artifact is None`, `artifact_sha256 is None`, `changed_files` is absent or empty, `execution.get('cleanup_status') == 'CONFIRMED'`, `source_sha` is a valid 40-character hex string, and `d6_unchanged(source_sha) is True`.
- **Security effect on the repair cap**:
  - Preserves strict fail-closed enforcement. Exemption is impossible without cryptographic and boundary proof: verified boundary cleanup (`CONFIRMED`), pristine D6 git working tree, and exact HEAD match to `source_sha`.
  - Confining exemptions to an explicit allow-list of discrete error codes guarantees that only known, non-mutating failure modes are exempt from counting.
- **Files touched**:
  - `tools/llmtool/llmtool/task_submission_cli.py` (definition of allow-list and check in `_launched_run_did_no_work`).
- **Schema impact on `task_store developer.execution`**:
  - Zero schema change. Uses existing fields of `developer` (`provider_profile`, `state`, `error_code`, `execution`) already stored in `task_store`.

### Option (b): Change how the delegates store those failures (e.g. BLOCKED) so the existing rule applies

- **Mechanics**:
  - Modify `run_task_opencode_developer` (`task_submission_cli.py:5000`) to persist `state='BLOCKED'` instead of `DEVELOPER_OUTCOME_UNKNOWN` for zero-work errors, or rewrite the error code to end with `_BLOCKED`.
  - Modify `persist_failure` in `run_task_gw_agent_developer` (`task_submission_cli.py:6229, 6235`) to store `state='BLOCKED'` for post-run refusals that yielded no diff.
- **Security effect on the repair cap**:
  - High risk of regression. In the llmtool architecture, `state='BLOCKED'` denotes an intentional dispatch, scope, or policy refusal, whereas `DEVELOPER_UNAVAILABLE` and `DEVELOPER_OUTCOME_UNKNOWN` signify provider availability or execution uncertainties.
  - Conflating runtime provider crashes with policy blocks degrades monitoring, retry policies, and availability classifications.
  - Furthermore, marking failures as `BLOCKED` too broadly could allow unanticipated failure modes to bypass the repair cap without thorough auditing of every error path.
- **Files touched**:
  - `tools/llmtool/llmtool/task_submission_cli.py` (delegates for OpenCode and gw-agent).
  - Potentially `tools/llmtool/llmtool/opencode_direct_developer.py` and gw-agent wiring modules.
- **Schema impact on `task_store developer.execution`**:
  - While values remain strings, altering state semantics changes the meaning of persisted task records in `task_store` and may impact external consumers.

### Option (c): Keep counting

- **Mechanics**:
  - Retain current behavior with no code changes.
- **Security effect on the repair cap**:
  - Strictly fail-closed, but penalizes benign zero-work refusals. A transient provider CLI error or rate limit where no disk mutation took place and cleanup was confirmed burns an entire repair attempt, causing tasks to exhaust repair budgets prematurely.
- **Files touched**:
  - None.
- **Schema impact on `task_store developer.execution`**:
  - None.

## 3. Recommendation

- **Adopt Option (a)**:
  - Add an explicit allow-list of `(provider_profile, state, error_code)` triples to `_launched_run_did_no_work` in `tools/llmtool/llmtool/task_submission_cli.py`.
  - Adheres to evidence honesty (`skills/role-cards/common-core.md` §6) by recording failures under their true observed states (`DEVELOPER_OUTCOME_UNKNOWN` for OpenCode, `DEVELOPER_UNAVAILABLE` for gw-agent) while exempting zero-work runs only upon positive proof of confirmed cleanup and clean D6.

- **Fail-closed rules**:
  1. **No wildcard codes**: Absolutely no regex patterns or prefix wildcards (e.g. no `OPENCODE_.*` or `DEVELOPER_GW_AGENT_.*`). Every exempt code must be explicitly enumerated.
  2. **Codes that cannot follow a provider write**: Only codes where the failure occurs prior to workspace mutation or where the process terminated without emitting a patch may be listed.
  3. **Candidate codes analysis**:
     - *OpenCode candidates (`provider_profile='opencode'`, `state='DEVELOPER_OUTCOME_UNKNOWN'`) shown by code*:
       - `OPENCODE_PROVIDER_FAILED` (`tools/llmtool/llmtool/opencode_direct_developer.py:996`): Raised when CLI process exits non-zero and error classification does not identify a diff. Safe before patch parsing.
       - `OPENCODE_EXIT_UNKNOWN` (`tools/llmtool/llmtool/opencode_direct_developer.py:985`): Raised when process return code is missing or uncollected. Safe only because `cleanup_status == 'CONFIRMED'` is enforced.
     - *OpenCode candidate codes (UNVERIFIED - not read in allowed code slices)*:
       - `OPENCODE_TIMEOUT` (UNVERIFIED): Process terminated due to execution timeout.
       - `OPENCODE_PARSE_FAILED` (UNVERIFIED): Event output could not be parsed into valid events.
       - `OPENCODE_STEP_LIMIT_EXCEEDED` (UNVERIFIED): Step budget exhausted before emitting diff.
       - *Prohibited*: `OPENCODE_CLEANUP_UNKNOWN` (`opencode_direct_developer.py:973`) and `OPENCODE_ADAPTER_UNKNOWN` (`task_submission_cli.py:5037`) must **NEVER** be allow-listed, as boundary cleanup was not confirmed.
     - *gw-agent candidates (`provider_profile='gw-agent'`, `state='DEVELOPER_UNAVAILABLE'`)*:
       - *Shown by code*: In the authorized code slice `tools/llmtool/llmtool/task_submission_cli.py:6225-6300`, `persist_failure` handles failure persistence for gw-agent, setting `state = 'DEVELOPER_UNAVAILABLE'` (`task_submission_cli.py:6229, 6235`). In-flight dispatch refusals are mapped to `DEVELOPER_` prefix at `:6290, 6300`, but pre-launch dispatch denials are already handled by `_PRELAUNCH_REPAIR_REFUSALS` (`task_submission_cli.py:7073-7078`). Specific post-run route error codes for gw-agent originate outside the authorized lines `6225-6300` and are therefore UNVERIFIED.
       - *gw-agent candidate codes (UNVERIFIED - not read in allowed code slices)*:
         - `DEVELOPER_GW_AGENT_HTTP_429` (UNVERIFIED): Upstream rate limiting refusal.
         - `DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED` (UNVERIFIED): Upstream model quota exhausted without patch.
         - `DEVELOPER_GW_AGENT_GATEWAY_TIMEOUT` (UNVERIFIED): Gateway timeout prior to response.
         - `DEVELOPER_GW_AGENT_D6_SESSION_REFUSED` (UNVERIFIED): Session refused post-execution.
         - *Prohibited*: Any codes representing potential workspace modifications (e.g. write scope violations, output limit overflows, or audit integrity failures [all UNVERIFIED - not read in allowed slices]) must **NEVER** be allow-listed.
  4. **Pre-launch and in-flight paths unchanged**:
     - The `_PRELAUNCH_REPAIR_REFUSALS` set (`tools/llmtool/llmtool/task_submission_cli.py:7068-7081`) remains untouched and continues to handle pre-launch dispatch and config refusals without requiring execution records.
     - In-flight runner crashes or unconfirmed cleanups continue to return `False` and count.
  5. **Claude/AGY path from #2107 unchanged**:
     - The post-run execution facts mechanism implemented for Claude and AGY in PR #2107 (matching `state == 'BLOCKED'` with confirmed execution facts) remains completely unaffected.

## 4. Tests to add later

Per the TEST DATA RULE (Owner #763 A, `skills/role-cards/common-core.md` §4), all test cases must use synthetic, mock fixtures with zero credential-shaped strings. Provider interactions and D6 checker functions must be mocked or monkeypatched.

1. `test_repair_count_opencode_allowlisted_clean_d6_skipped`:
   - OpenCode run fails with `state='DEVELOPER_OUTCOME_UNKNOWN'`, allow-listed code `error_code='OPENCODE_PROVIDER_FAILED'`.
   - `execution` dict has `cleanup_status='CONFIRMED'`, `artifact=None`, `artifact_sha256=None`.
   - `d6_unchanged(source_sha)` returns `True`.
   - Assert: `_launched_run_did_no_work` returns `True`; run is not counted against repair cap.

2. `test_repair_count_opencode_allowlisted_unknown_cleanup_counted`:
   - Same as Case 1, but `execution` dict has `cleanup_status='UNKNOWN'`.
   - Assert: `_launched_run_did_no_work` returns `False`; run is counted against repair cap.

3. `test_repair_count_opencode_non_listed_code_counted`:
   - OpenCode run fails with a non-allowlisted code (e.g. `error_code='OPENCODE_UNLISTED_ERROR'`).
   - `cleanup_status='CONFIRMED'`, D6 clean at `source_sha`.
   - Assert: `_launched_run_did_no_work` returns `False`; run is counted against repair cap.

4. `test_repair_count_opencode_allowlisted_dirty_d6_counted`:
   - Same as Case 1, but D6 contains an unstaged edit or untracked file (`d6_unchanged(source_sha)` returns `False`).
   - Assert: `_launched_run_did_no_work` returns `False`; run is counted against repair cap.

5. `test_repair_count_gw_agent_allowlisted_clean_d6_skipped`:
   - gw-agent run fails with `state='DEVELOPER_UNAVAILABLE'`, allow-listed candidate `error_code='DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED'` (UNVERIFIED candidate code).
   - `execution` dict has `cleanup_status='CONFIRMED'`, `artifact=None`, `artifact_sha256=None`.
   - `d6_unchanged(source_sha)` returns `True`.
   - Assert: `_launched_run_did_no_work` returns `True`; run is not counted against repair cap.

6. `test_repair_count_gw_agent_allowlisted_unknown_cleanup_counted`:
   - Same as Case 5, but `execution` has `cleanup_status='UNKNOWN'`.
   - Assert: `_launched_run_did_no_work` returns `False`; run is counted against repair cap.

7. `test_repair_count_gw_agent_non_listed_code_counted`:
   - gw-agent run fails with a non-allowlisted code (e.g. `error_code='DEVELOPER_GW_AGENT_UNLISTED_ERROR'`).
   - `cleanup_status='CONFIRMED'`, D6 clean at `source_sha`.
   - Assert: `_launched_run_did_no_work` returns `False`; run is counted against repair cap.

8. `test_repair_count_gw_agent_allowlisted_dirty_d6_counted`:
   - Same as Case 5, but D6 contains modified files (`d6_unchanged(source_sha)` returns `False`).
   - Assert: `_launched_run_did_no_work` returns `False`; run is counted against repair cap.

## 5. Out of scope

- Modifications to maximum repair attempt thresholds or numeric repair-cap limits.
- Changes to `policy.json` schema or policy dispatch configurations.
- Review repair chain sequencing, arbitrator selection, or reviewer re-evaluation rules.
