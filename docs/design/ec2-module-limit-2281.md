# Design Note: EC2 Remote-Test Module Limit (#2281)

## 1. Background and Goal

In llmtool, `llmtool task remote-test` runs an admitted D6 Task's test modules over SSH on a dedicated EC2 instance before opening a PR or entering formal review. Currently, EC2 remote-test hardcodes an admission cap of at most 8 test modules. When a PR touches or declares more than 8 test modules, `run_remote_test()` immediately rejects the run with `MODULE_NOT_ADMITTED`.

Because `task advance` allows a PR to be published when `_remote_test_status()` returns `ADMISSION_DENIED` with `MODULE_NOT_ADMITTED` (and empty modules list), PRs touching 9 or more test modules (such as PR #2332) never receive actual test execution results on EC2 (`MODULE_NOT_ADMITTED` twice).

Per Owner decision for #2281:
> 「EC2 測試模組數量的上限也列入 #2281，改成可調並放寬。」
> ("Include the EC2 test module count limit into #2281 as well, make it configurable and relax it.")

This investigation reports the concrete facts, file paths, line numbers, dependencies, and configuration mechanisms needed to implement this change safely.

---

## 2. Where the Module Limit is Set

The limit of 8 modules is set across three locations:

1. **Python module constant in `remote_test.py`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L57)
   - Code: `_MAX_REMOTE_MODULES = 8`
   - Role: Defines the default maximum number of test modules admitted for remote testing in a single session.

2. **Hardcoded limit in the remote shell script `_REMOTE_SCRIPT`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L84)
   - Code:
     ```python
     if not R.fullmatch(sha) or not F.fullmatch(ref) or not mods or len(mods)>8 or any(not M.fullmatch(x) for x in mods): sys.exit(40)
     ```
   - Role: Enforces `len(mods) > 8` inside the standalone Python script transferred and executed on the remote EC2 runner as user `nobody`. If more than 8 module arguments are supplied on the command line over SSH, the remote script exits with status 40 before creating the temporary workspace or running venv/pip steps.

3. **Hardcoded limit in GitHub-hosted remote test dispatch `_hosted_dispatch()`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L918-L919)
   - Code:
     ```python
     if not (1 <= len(modules) <= 8):
         raise ValueError('modules must contain 1 to 8 items')
     ```
   - Role: Validates workflow dispatch input parameters when falling back to the GitHub-hosted workflow (`llmtool-remote-test.yml`).

---

## 3. Where the Module Limit is Enforced

The 8-module cap is enforced at multiple stages of the task lifecycle:

1. **Local admission gate in `run_remote_test()`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L598-L602)
   - Code:
     ```python
     admitted = _task_path_modules(task)
     selected = tuple(sorted(set(modules or admitted)))
     if (not selected or len(selected) > _MAX_REMOTE_MODULES or
             any(type(x) is not str or not _MODULE.fullmatch(x) or x not in admitted for x in selected)):
         return denied('MODULE_NOT_ADMITTED')
     ```
   - Behavior: When `modules` is empty (the default in automated advance), all matching modules from the Task's `read_paths` and `write_paths` are selected (`selected = tuple(sorted(set(modules or admitted)))`). If `len(selected) > _MAX_REMOTE_MODULES`, admission is refused with `MODULE_NOT_ADMITTED`. Line 534 records this outcome in `TaskStore` with `result='ADMISSION_DENIED'` and `code='MODULE_NOT_ADMITTED'`.

2. **Main incident remote-test admission in `run_incident_remote_test()`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L771-L772)
   - Code:
     ```python
     if len(selected) > _MAX_REMOTE_MODULES:
         return _incident_denied(INCIDENT_MODULE_CAP, incident_id)
     ```
   - Behavior: When reproducing main incident failures on EC2, rejects runs exceeding `_MAX_REMOTE_MODULES` with `INCIDENT_MODULE_CAP`.

3. **Remote EC2 execution boundary in `_REMOTE_SCRIPT`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L84)
   - Code: `len(mods)>8` causes `sys.exit(40)` directly on the EC2 host.

4. **Hosted workflow dispatch in `_hosted_dispatch()`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L918-L919)
   - Code: Raises `ValueError('modules must contain 1 to 8 items')`.

5. **Task advance integration and PR gate in `task_advance.py`:**
   - Location: [task_advance.py](../../tools/llmtool/llmtool/task_advance.py#L2254-L2288) (`_pre_pr_remote_gate`)
   - Location: [task_advance.py](../../tools/llmtool/llmtool/task_advance.py#L2185-L2188) (`_remote_test_status`)
   - Location: [task_advance.py](../../tools/llmtool/llmtool/task_advance.py#L2410-L2411) (`_pre_review_action`)
   - Behavior: `task_advance._pre_pr_remote_gate()` calls `_run_remote_test(..., module=[])` ([task_advance.py](../../tools/llmtool/llmtool/task_advance.py#L2234-L2235)). When `run_remote_test()` returns `ADMISSION_DENIED` with `MODULE_NOT_ADMITTED`, `_remote_test_status()` recognizes `(outcome.get('result') == 'ADMISSION_DENIED' and outcome.get('code') == 'MODULE_NOT_ADMITTED' and outcome.get('modules') == [])` and returns status `'ADMISSION_DENIED'`. Line 2278 permits PR creation (`if status in ('PASS', 'ADMISSION_DENIED'): return status, None`). Consequently, the PR opens without failing, but no EC2 test receipt is ever generated because no tests were run.

6. **Process documentation in `pm.md`:**
   - Location: [pm.md](../../.claude/skills/role-cards/pm.md#L41)
   - Rule: `4. **EC2 admits at most 8 modules, and only from tools/llmtool/tests.** Run anything else locally in a registered test D6.`

---

## 4. Which Tests Pin It

The 8-module threshold is directly pinned in unit tests:

1. **Eligibility cap test in `test_remote_test_gate.py`:**
   - Location: [test_remote_test_gate.py](../../tools/llmtool/tests/test_remote_test_gate.py#L72-L82)
   - Code:
     ```python
     def test_eligibility_caps_remote_modules_at_executor_limit(self):
         eight_module_task, eight_module_paths = task_with_module_count(8)
         self.assertEqual(task_submission_cli._remote_receipt_test_policy(
             eight_module_task, read_paths=eight_module_paths,
             config_loader=lambda: {}), 'REMOTE_RECEIPT')

         nine_module_task, nine_module_paths = task_with_module_count(9)
         self.assertEqual(task_submission_cli._remote_receipt_test_policy(
             nine_module_task, read_paths=nine_module_paths,
             config_loader=lambda: {}), 'LOCAL')
     ```
   - Helper definition: [test_remote_test_gate.py](../../tools/llmtool/tests/test_remote_test_gate.py#L27-L34) (`task_with_module_count(count)`).
   - Invariant tested: Exactly 8 modules qualifies for `'REMOTE_RECEIPT'`, whereas 9 modules exceeds the limit and falls back to `'LOCAL'`.

2. **Related test files verifying boundary and admission contracts:**
   - [test_remote_test_hosted_optin.py](../../tools/llmtool/tests/test_remote_test_hosted_optin.py#L200-L261): Tests admission denial cases (`MODULE_NOT_ADMITTED`, `ARGUMENT_INVALID`, `RECEIPT_HEAD_UNRECORDED`, `RECEIPT_HEAD_MISMATCH`).
   - [test_remote_test_artifact.py](../../tools/llmtool/tests/test_remote_test_artifact.py#L171-L188): Pins download payload and member limits (`_HOSTED_ZIP_LIMIT = 65536`, `_HOSTED_MEMBER_LIMIT = 32768`).

---

## 5. What Else Depends on It (Runtime and EC2 Load)

Relaxing the module cap directly affects execution timing, resource consumption, and concurrency on the EC2 host:

1. **Dynamic per-module time allocation in `_REMOTE_SCRIPT`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L85) & [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L233-L236)
   - Code:
     ```python
     # Line 85: overall script deadlines
     work_deadline=begin+480; deadline=begin+540
     ...
     # Lines 234-236: per-module test loop
     allowance=(deadline-time.monotonic())/(len(mods)-index)-3
     if allowance<=1: raise subprocess.TimeoutExpired(mod,0)
     test=nobody_run(work+"/tools/llmtool",[work+"/venv/bin/python","-m","unittest","discover","-s","tests","-p",mod+".py"],limit=min(180,allowance),...)
     ```
   - Impact:
     - Fixed setup overhead (workspace creation, `git archive`, `venv` creation, `pip install -r requirements-ci.txt`, `pip install -e tools/llmtool`, lines 207-231) consumes roughly 120-180 seconds.
     - The remaining time before `deadline` (540s, or 9 minutes) is divided evenly across remaining modules: `(deadline - time.monotonic()) / (len(mods) - index) - 3`.
     - With 8 modules, each module initially receives roughly ~40-50s (capped at `min(180, allowance)`).
     - If the limit is relaxed (e.g. to 16 or 24 modules) without adjusting `deadline` (540s) and `work_deadline` (480s), the per-module allowance shrinks significantly (e.g. to ~15-20s or less). If a few earlier modules consume 30-40 seconds, later modules will see `allowance <= 1` and raise premature `subprocess.TimeoutExpired(mod, 0)` failures.
     - Therefore, increasing the module limit requires evaluating or scaling `work_deadline` and `deadline`.

2. **Client-side SSH command timeout:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L56) (`_SSH_TIMEOUT = 1800`) and [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L622) (`timeout = _ssh_timeout()`).
   - Impact: Default client-side SSH timeout is 1800 seconds (30 minutes). As long as `deadline` remains well below 1800 seconds, the SSH transport timeout will not prematurely abort during normal execution.

3. **Receipt payload size and memory limits:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L53) (`_REMOTE_LIMIT = 32768`) and [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L268-L272)
   - Code:
     ```python
     # Line 268 & 270:
     final: finals[-len(mods)*2:], failures: failures[:10]
     if module_results: receipt["module_results"]=module_results[-len(mods)*2:]
     # Line 272:
     print(raw.decode() if len(raw)<=32768 else "{}")
     ```
   - Impact: The receipt JSON is truncated to `{}` if it exceeds 32,768 bytes. Each module adds an entry to `module_results` and `final`. A modest relaxation (e.g. 16-24 modules) will remain safely within 32 KB, but unbounded lists could risk empty receipt output (`RECEIPT_INVALID`).

4. **EC2 host concurrency and lock contention:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L177-L178) (`open_lock(os.path.expanduser("~")+"/.llmtool-remote-test.lock")`)
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L633-L635) (`exit code 75 -> code='REMOTE_BUSY'`)
   - Impact: EC2 remote testing is strictly non-concurrent; an exclusive non-blocking file lock is held for the entire duration of the remote script. Longer test suites running more modules will hold the lock longer, increasing the probability of `REMOTE_BUSY` (which triggers retry logic in `task advance`, [task_advance.py](../../tools/llmtool/llmtool/task_advance.py#L36-L38)).

5. **Disk space and log retention on EC2:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L179-L194) (`cleanup_stale()`)
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L256-L265) (log directory in `/tmp/llmtool-remote-log.XXXXXX`)
   - Impact: Stale workspaces older than 24 hours are removed, and only the newest 20 log directories are retained. More modules produce larger logs, but individual logs are capped at 20 MB during receipt validation ([remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L384)).

---

## 6. How Other Limits in #2281 Are Made Configurable

The existing precedent in #2281 for making remote-test limits configurable is the SSH timeout mechanism:

1. **Delegation pattern in `_ssh_timeout()`:**
   - Location: [remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L305-L311)
   - Code:
     ```python
     def _ssh_timeout():
         from . import developer_routes
         try:
             return developer_routes.remote_test_timeout()
         except developer_routes.RouteError:
             raise LLMToolError('ADMISSION_DENIED: remote test timeout config is invalid') from None
     ```
   - Key characteristics of this pattern:
     - **Dynamic query:** Instead of directly binding to the static constant `_SSH_TIMEOUT = 1800` (line 56), operational code queries a helper function `_ssh_timeout()`.
     - **Centralized route / developer policy:** `_ssh_timeout()` delegates to `developer_routes.remote_test_timeout()`, which handles reading the user/system configuration or environment options.
     - **Strict error encapsulation:** Invalid or malformed configuration raises a domain-specific `RouteError`, which is trapped and converted into a standard llmtool admission denial (`LLMToolError('ADMISSION_DENIED: ...')`).
     - **Separation of concerns:** The remote host connection configuration in `~/.config/llmtool/remote-test.json` ([remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L281-L302)) is strictly restricted to connection parameters (`host`, `user`, `repo_path`, `python`). Limits and operational quotas are decoupled from host connection details and resolved through `developer_routes`.

2. **Applying the pattern to the module limit under #2281:**
   To make the test module limit configurable and relaxed per the Owner's directive:
   - Provide a route resolver function in `developer_routes` (e.g. `remote_test_max_modules()`, with a relaxed default such as 16 or configurable value).
   - Replace the static `_MAX_REMOTE_MODULES = 8` check in `run_remote_test()` ([remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L600)) and `run_incident_remote_test()` ([remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L771)) with a helper (e.g. `_max_remote_modules()`) using the `developer_routes` resolver.
   - Parameterize or dynamically inject the configured maximum into `_REMOTE_SCRIPT` (line 84) instead of the hardcoded `len(mods)>8`, or pass the allowed limit as a script argument.
   - Scale or parameterize `work_deadline` and `deadline` in `_REMOTE_SCRIPT` (line 85) so larger module batches do not hit artificial per-module timeout cutoffs.
   - Update `_hosted_dispatch()` ([remote_test.py](../../tools/llmtool/llmtool/remote_test.py#L918)) and `task_submission_cli._remote_receipt_test_policy()` / `test_remote_test_gate.py` ([test_remote_test_gate.py](../../tools/llmtool/tests/test_remote_test_gate.py#L72-L82)) to use the configurable limit.
