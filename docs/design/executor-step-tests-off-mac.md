# Executor-step tests off the Mac (#1817)

Status: pending (design only; no code in this change)

Source decision: Owner decision on #1817, relayed by the PM: no local test runs
on the Owner's Mac, EC2 remote testing stays, TUI tests never run automatically.

Scope: the llmtool executor step that today runs the Task's registered test
modules inside the D6 on the Mac. This note does not change Mac CI, Linux CI,
formal review or merge gates.

Read scope of this note: `task_completion.py`, `task_advance.py`,
`task_drive.py`, `remote_test.py`. Every claim about code outside those files is
marked UNKNOWN.

## 1. Today

### 1.1 Where the local run happens

- `tools/llmtool/llmtool/task_completion.py:75-78` `_TEST_REGISTRY`: a path is a
  registered test if it matches `tools/llmtool/tests/test_[A-Za-z0-9_]+.py`
  (kind `unittest`) or `scripts/test_[A-Za-z0-9_]+.py` (kind `script`).
- `task_completion.py:1785-1791` `_registered_test(path)` classifies one path.
- `task_completion.py:1854-1922` `_run_registered_tests(root, source_sha,
  eligible, *, home, pycache=None)` is the only place that spawns tests. For
  each eligible path it starts `sys.executable -m unittest discover` with
  `cwd=root` (the D6 on the Mac), a reduced env, `start_new_session=True`, a
  600 s per-module limit (`_TEST_TIMEOUT`, `task_completion.py:51`, which the
  comment at `:49-50` sizes for a ~310 s Mac run of `test_task_completion`) and
  a 256 KiB output limit (`:52`).
- Callers in `task_completion.py`:
  - `run_bounded_tests` (`:1925-1958`): committed result. Checks the D6 is
    clean at `result_sha` (`:1937-1939`), derives changed paths (`:1940-1946`),
    returns `NOT_APPLICABLE_DOCS_ONLY` for docs-only (`:1947-1949`), stops
    `TEST_SCOPE_MISSING` if no registered test is in read/write paths
    (`:1950-1951`), runs `_run_registered_tests` at `:1954`, then stops
    `TEST_SOURCE_CHANGED` if the Git snapshot moved (`:1955-1957`).
  - `run_bounded_worktree_tests` (`:1961-1990`): uncommitted D6 diff bound by
    `diff_sha256`. Same docs-only and scope rules (`:1972-1979`), runs at
    `:1982` with a private pycache, adds `result_head: None` and `diff_sha256`
    to every item (`:1984-1985`).
- Callers in `task_advance.py` / `task_drive.py`: neither file calls
  `run_bounded_tests`, `run_bounded_worktree_tests` or `_run_registered_tests`
  directly. The executor step is reached through:
  - `task_advance.py:3493-3516` step `RUN_TASK_COMPLETE` calls
    `task_submission_cli.run_task_complete` (`:3496`) and maps non-CI
    `completion.missing_gates` to `WAITING_EXECUTOR` (`:3514-3516`).
  - `task_drive.py:14` lists `WAITING_EXECUTOR` as a polling step; the drive
    loop pauses and re-advances on it (`task_drive.py:187-201`).
  - The exact call sites of `run_bounded_tests` / `run_bounded_worktree_tests`
    are in a module outside this note's read scope (expected
    `task_submission_cli.py`; UNKNOWN). PR 2 must read and cite them first.

### 1.2 What evidence it records

Per module (`task_completion.py:1909-1921`):

| field | value |
|---|---|
| `path` | registered test path |
| `source_sha` | `result_sha` (committed) or task `source_sha` (worktree) |
| `exit_code` | process exit code |
| `output_sha256` | SHA-256 of captured output |
| `duration_ms` | wall time |
| `command_sha256` | SHA-256 of argv JSON |
| `status` | `PASSED` or `FAILED` (`script` kind also needs `Ran N` with N > 0) |
| `output_excerpt` | only on failure, screened by `select_test_excerpts` (`:1838-1851`) |
| `result_head`, `diff_sha256` | worktree path only (`:1985`) |

Docs-only returns one item `{'status': 'NOT_APPLICABLE_DOCS_ONLY',
'changed_paths', 'source_sha', 'docs_only': True}` (`:1948`, `:1973-1975`).
Stops (no evidence, `_stop` code): `TEST_SOURCE_MISMATCH`, `TEST_PATH_INVALID`,
`TEST_SCOPE_MISSING`, `TEST_RUNNER_UNAVAILABLE`, `TEST_RUNNER_TIMEOUT`,
`TEST_OUTPUT_TOO_LARGE`, `TEST_SOURCE_CHANGED`, `TEST_BINDING_INVALID`.

The per-module item does not itself carry task ID, run ID or D6 identity; that
binding is added by the caller (UNKNOWN, outside read scope).

### 1.3 Which gates read it

- `completion.evidence.tests` and `developer.direct_result_callback.evidence`
  carry the list. `remote_test.py:313-336` `_admitted_modules` reads
  `evidence.tests[*].path`, and shows the gate shape: callback state
  `WAITING_EXECUTOR` with `missing_gates == ['TEST_FAILED']` and
  `verification_state == 'FAILED'` (`remote_test.py:321-326`). The code that
  writes `TEST_FAILED` / `verification_state` is outside read scope (UNKNOWN).
- `task_advance.py:3508-3516`: any non-CI missing gate (including a test gate)
  holds the Task at `WAITING_EXECUTOR`.
- `remote_test.py:475-481` `run_remote_test` takes `common_dir` and
  `result.result_head` from `completion.evidence` (or the callback evidence) to
  admit an EC2 run, so the same evidence record also binds the EC2 head.

### 1.4 The EC2 path that already exists

- `remote_test.py:452-581` `run_remote_test`: admits task, executor D6
  (`read_d6_registration`, `:493`), exact head (`:523-525`, recorded
  `result_sha` must equal `sha` and the fresh PR head), modules (`:528-532`,
  only declared `tools/llmtool/tests/test_[a-z0-9_]{1,80}.py`, at most 8,
  `_MAX_REMOTE_MODULES` `:57`), then runs one SSH session (or a GitHub-hosted
  run, `:533-536`). The receipt is validated (`:339-393`), extended with
  `task_id`, `run_id`, `executor_task`, `pm_session`, `command_digest`, and
  sealed with `receipt_digest` (`:575-578`).
- The remote script runs each module with a 180 s cap inside a 540 s session
  (`remote_test.py:81`, `:221-235`), as user `nobody`, from `git archive <sha>`.
- `task_advance.py:2235-2274` `_pre_pr_remote_gate` runs EC2 on the pushed
  branch head before a PR opens; `task_advance.py:3280-3306` step
  `RUN_REMOTE_TEST` runs it on `refs/pull/<n>/head`; `task_advance.py:2346-2362`
  `_pre_review_action` blocks review until the exact head has `PASS` (or the
  `ADMISSION_DENIED`/no-module case).
- `task_advance.py:2118-2170` `_remote_test_status` picks the newest receipt for
  `(task_id, run_id, executor_task, head)` with a valid digest.

### 1.5 Gaps found while reading

1. `_remote_test_status` (`task_advance.py:2128-2143`) does not check that the
   receipt's `modules` equal the declared set. `run_remote_test` admits any
   non-empty subset of the declared modules (`remote_test.py:529-532`), so a
   manual `llmtool task remote-test --module <one>` PASS can satisfy the
   pre-review gate for a partial set.
2. `scripts/test_*.py` modules and module names with upper-case letters are
   registered locally (`task_completion.py:75-78`) but cannot run on EC2
   (`remote_test.py:31`, `:224`, `:309`).
3. A Task with more than 8 declared modules is refused on EC2
   (`MODULE_NOT_ADMITTED`).
4. The worktree path (`run_bounded_worktree_tests`) has no commit, so EC2
   (which runs `git archive <sha>`) cannot test it.

## 2. Options

### (a) The exact-head EC2 receipt is the executor test evidence (recommended)

The executor step no longer spawns tests. It records a deferral item and the
test gate reads the exact-head remote-test receipt that the existing advance
steps already produce (`_pre_pr_remote_gate`, `RUN_REMOTE_TEST`).

- Pro: no Mac run at all; no second EC2 run (the pre-PR gate already runs
  one); one source of test truth for executor, review and completion.
- Pro: the receipt already binds task, run, executor D6, head, modules and the
  command digest, sealed by `receipt_digest`.
- Con: the executor step cannot report PASS before publish; the gate moves to
  "waiting for remote evidence", which is honest.

### (b) The executor step calls `llmtool task remote-test` itself

- Con: makes the executor step an external action (needs the `guard()` used at
  `task_advance.py:2281`, `:3609`), duplicates the pre-PR EC2 run, and needs a
  pushed ref that may not exist yet at executor time. It adds EC2 load and
  retry logic for no new evidence.

### (c) Keep a local run behind an explicit opt-in flag

- Con: contradicts the Owner decision (no local test runs on the Mac). Even
  opt-in, its result could be mistaken for gate evidence.
- Not recommended. If ever added, its output is diagnostic only and never
  gate evidence; no PR in this plan adds it.

### Recommendation: (a), fail closed

The executor test gate is PASS only when one receipt in
`record.remote_test_receipts` satisfies all of:

1. `receipt_digest` recomputes (as `task_advance.py:2137-2141`).
2. `task_id`, `run_id` equal the Task; `executor_task` equals
   `completion.executor_task_id`; `read_d6_registration(executor_task, ...)`
   is readable and its `result_sha` equals the receipt `source_sha`.
3. `source_sha` equals the current D6 `result_head` and, when a PR exists, the
   fresh PR head.
4. `result == 'PASS'` (the validator already requires `exit_code == 0`,
   `ran_total > 0`, no failures, a final OK line per module,
   `remote_test.py:383-390`).
5. `modules` equals exactly the expected remote set from section 4
   (sorted list equality, not subset).

Otherwise the gate is not PASS and carries one code:

| condition | code | step |
|---|---|---|
| no receipt for this head | `TEST_REMOTE_EVIDENCE_MISSING` | `RUN_REMOTE_TEST` (existing) |
| only receipts for another head | `TEST_REMOTE_EVIDENCE_STALE` | `RUN_REMOTE_TEST` |
| receipt modules differ from expected set | `TEST_REMOTE_MODULES_MISMATCH` | `RUN_REMOTE_TEST` with the full set |
| `FAIL` | `TEST_FAILED` (existing) | repair route (existing) |
| `TIMEOUT`, `SHA_MISMATCH` | existing `REMOTE_TEST_*` | `NEEDS_ATTENTION` (existing) |
| registered test not runnable remotely (gap 2, 3) | `TEST_REMOTE_MODULE_UNSUPPORTED` | stop for PM |
| digest or binding invalid | `TEST_REMOTE_EVIDENCE_INVALID` | stop for PM |

`ADMISSION_DENIED`, `UNAVAILABLE` and `UNKNOWN` outcomes never satisfy the
executor test gate. Code changes with no registered test keep
`TEST_SCOPE_MISSING` (today `task_completion.py:1950-1951`). Docs-only keeps
`NOT_APPLICABLE_DOCS_ONLY`; that is a Git-derived classification, not a test
run, and needs no Mac process.

The worktree path (gap 4) records `NOT_RUN` with reason
`DEFERRED_TO_REMOTE_TEST`; its gate cannot pass until a commit exists and the
committed head has a receipt.

## 3. TUI

- TUI test modules are the paths `task_advance.py:450-452` already classifies:
  `tools/llmtool/tests/test_chat_tui*` and `tools/llmtool/tests/test_tui_*`,
  plus the helper `tools/llmtool/tests/tui_pty_driver.py`
  (`task_advance.py:443`, `:1272`).
- Mac exclusion: after PR 2 the executor step spawns no test process at all,
  so no TUI/PTY module can run on the Mac from it. PR 4 adds a static test that
  pins this (no `unittest` argv in the executor step code).
- Replacement evidence, in order:
  1. The exact-head EC2 receipt, when the TUI module is in the expected remote
     set and runs headless on EC2 under the same rules as any module.
  2. For macOS-specific TUI behaviour, the exact-head required Mac check
     (`check (macOS 3.13)`, `task_completion.py:58`, `:61`), which completion
     already requires (`task_completion.py:2848-2855`). This is an existing
     gate, not a new one, and it is not relaxed.
- If neither applies, the TUI module is recorded `NOT_RUN` with reason
  `TUI_EXCLUDED` and the executor test gate is not PASS
  (`TEST_TUI_EVIDENCE_MISSING`); the PM decides.
- Open questions for the PM (UNKNOWN today):
  - Does "TUI never runs automatically" also exclude EC2? If yes, item 1 is
    dropped and TUI modules are always `NOT_RUN` plus the Mac CI check.
  - Does the Mac CI workflow run the TUI modules? The workflow file is outside
    read scope. If it does not, item 2 is not evidence for them.
  - The Mac CI runner names (`task_completion.py:63`) look self-hosted. Whether
    that host is the Owner's Mac, and whether the Owner decision covers CI, is
    UNKNOWN. This plan does not change CI.

## 4. Expected module set

From the Task's declared `read_paths` and `write_paths` (the same source as
`remote_test._task_path_modules`, `remote_test.py:300-310`, and
`task_completion.py:1935-1936`):

- remote set: every registered `tools/llmtool/tests/test_[a-z0-9_]{1,80}.py`,
  as module stems, sorted; TUI modules included or excluded per the PM answer
  in section 3;
- unsupported: any other registered path (scripts tests, upper-case names) or
  a remote set larger than 8; gives `TEST_REMOTE_MODULE_UNSUPPORTED`, never a
  silent drop;
- empty remote set with code changes: `TEST_SCOPE_MISSING`.

## 5. Invariants

1. Evidence binds task (`task_id`, `run_id`), D6 (`executor_task` plus the
   registration `result_sha`), exact head (`source_sha`) and the exact module
   list, sealed by `receipt_digest`.
2. No gate becomes PASS without a real run: only a validated `PASS` receipt
   from EC2 or the hosted remote-test run, or an existing exact-head CI check,
   counts.
3. `NOT_RUN` stays `NOT_RUN`. The deferral item is never rewritten to
   `PASSED`; the gate result references the receipt by `receipt_digest`.
4. The Mac never runs a test from the executor step; no opt-in path is added.
5. No existing gate is weakened: `TEST_SCOPE_MISSING`, the pre-review EC2
   gate, required Mac and Linux CI, formal review and merge gates stay as
   they are. Gap 1 is closed, which tightens the pre-review gate.

## 6. Threats

| threat | control |
|---|---|
| stale receipt (older head of the same Task) | `source_sha` must equal current D6 `result_head`, registration `result_sha` and fresh PR head |
| receipt for another head, task or D6 | `task_id`, `run_id`, `executor_task` and digest checks; head equality |
| partial module set | exact list equality with section 4; closes gap 1 |
| forged or edited receipt in the store | `receipt_digest` recompute; receipt field set validated at write (`remote_test.py:346-348`) |
| silently dropped modules (scripts, >8) | `TEST_REMOTE_MODULE_UNSUPPORTED` stop |
| module slower than the EC2 180 s cap | `TIMEOUT`, never PASS; existing `NEEDS_ATTENTION` route |
| `ADMISSION_DENIED` read as pass | excluded from the executor test gate |

## 7. PR plan

Each PR touches at most 4 files. Status of every PR: pending. Tests follow the
test data rule (Owner #763 A): fixtures use hex SHAs, hex IDs and module names
only; no credential-shaped strings; no production check is disabled.

### PR 1: evidence validator (pure, no behaviour change). Status: pending

Files: `tools/llmtool/llmtool/executor_test_evidence.py` (new),
`tools/llmtool/tests/test_executor_test_evidence.py` (new).

- `expected_remote_modules(read_paths, write_paths)` returns remote set, TUI
  set and unsupported paths.
- `remote_evidence_status(record, *, head, expected_modules)` returns
  `(status, code, receipt_digest)` per section 2.

Tests: PASS for an exact-head, exact-set, valid-digest receipt; MISSING; STALE
(receipt for an older head); other `task_id`, `run_id` or `executor_task`;
subset and superset module lists give MISMATCH; edited field gives INVALID;
`FAIL`, `TIMEOUT`, `SHA_MISMATCH`, `ADMISSION_DENIED` never PASS; scripts path,
upper-case name and 9 modules give UNSUPPORTED; TUI paths classified.

### PR 2: executor step stops running tests. Status: pending

Files: `tools/llmtool/llmtool/task_completion.py`,
`tools/llmtool/llmtool/task_submission_cli.py` (needs read and write scope;
caller sites UNKNOWN today), `tools/llmtool/tests/test_task_completion.py`,
`tools/llmtool/tests/test_task_submission_cli.py`.

- `run_bounded_tests` and `run_bounded_worktree_tests` keep every binding,
  docs-only and scope check, and return `NOT_RUN` items with reason
  `DEFERRED_TO_REMOTE_TEST`, bound to head (or `diff_sha256`) and modules.
  They no longer call `_run_registered_tests`.

Tests: a monkeypatched `subprocess.Popen` is never called from either
function; docs-only result unchanged; `TEST_SCOPE_MISSING`,
`TEST_SOURCE_MISMATCH`, `TEST_SOURCE_CHANGED` still stop; the deferral item is
`NOT_RUN`, never `PASSED`.

### PR 3: gates read the exact-head remote evidence. Status: pending

Files: `tools/llmtool/llmtool/task_advance.py`,
`tools/llmtool/llmtool/task_submission_cli.py`,
`tools/llmtool/tests/test_task_advance.py` (or the existing advance test
module that covers `_remote_test_status`; UNKNOWN name),
`tools/llmtool/tests/test_task_submission_cli.py`.

- The completion test gate (where `TEST_FAILED` is set today, UNKNOWN site)
  uses `remote_evidence_status`.
- `_remote_test_status` requires exact module equality (closes gap 1).

Tests: completion stays `WAITING_EXECUTOR` with each section 2 code; a subset
PASS receipt no longer clears `_pre_review_action`; a PASS receipt for an
older head does not clear; an exact-head exact-set PASS clears; `FAIL` routes
to repair as today.

### PR 4: TUI handling and a no-local-run pin. Status: pending

Files: `tools/llmtool/llmtool/executor_test_evidence.py`,
`tools/llmtool/tests/test_executor_test_evidence.py`,
`tools/llmtool/tests/test_task_completion.py`, this note (status update).

- TUI modules follow the PM answer to section 3: in the remote set, or
  `NOT_RUN` `TUI_EXCLUDED` with `TEST_TUI_EVIDENCE_MISSING` unless the
  exact-head required Mac check succeeded.

Tests: a TUI-only Task never yields PASS without an exact-head run; a static
check that the executor-step functions in `task_completion.py` build no
`unittest` argv; mixed TUI and non-TUI sets need both kinds of evidence.

## 8. Not done here

- No code, no tests run, no Git. Callers of `run_bounded_tests` outside the
  read scope and the writer of `TEST_FAILED` are UNKNOWN until PR 2 reads them.
- The repo's status-mark convention was not readable within this note's read
  scope; this note uses a plain `Status: pending` line and a per-PR status.
