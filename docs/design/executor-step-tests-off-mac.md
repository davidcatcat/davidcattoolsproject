# Executor-step tests off the Mac (#1817)

Status: pending (design only; no code in this change)

Source decision: Owner decision on #1817, relayed by the PM: no local test runs
on the Owner's Mac, EC2 remote testing stays, TUI tests never run automatically.

Scope: the llmtool executor step that today runs the Task's registered test
modules inside the D6 on the Mac. This note does not change Mac CI, Linux CI,
formal review or merge gates.

Read scope of this note: `task_completion.py`, `task_advance.py`,
`task_drive.py`, `remote_test.py`. Every claim about code outside those files is
marked UNKNOWN or "review-reported, unverified".

## 0. Blocked current-state item (repair 1 of PR #2111)

The formal review (finding F2) states that `task_submission_cli.py` already
chooses between a `REMOTE_RECEIPT` and a `LOCAL` test path and is the caller and
consumer of the runners. It cites these sites, **review-reported and unverified
here** because the file is outside this Task's read scope:

| review-cited site | review's description |
|---|---|
| `task_submission_cli.py:3935-4029` | selects `REMOTE_RECEIPT` versus `LOCAL` |
| `task_submission_cli.py:4026` | invokes the worktree tests |
| `task_submission_cli.py:8540` | invokes the committed tests |
| `task_submission_cli.py:8546-8549`, `:9341-9346` | consume their results |

Acceptance item 1 (callers, binding writers and gate consumers with
`file:line`) cannot be completed without reading that file. This item is
**BLOCKED on read scope** (`tools/llmtool/llmtool/task_submission_cli.py`), not
deferred to a code PR. Until it is read:

- the existing `REMOTE_RECEIPT` path may already provide part of option (a);
  the evidence item name `DEFERRED_TO_REMOTE_TEST` used below is a placeholder
  and must be replaced by the existing remote-deferral shape if one exists;
- the remaining `LOCAL` fallback, the conditions that select it, the binding
  fields its caller adds (task, run, D6) and the writer of `TEST_FAILED` /
  `verification_state` are UNKNOWN;
- sections 1.2, 1.3 and 7 (PR 3) must be revised against that reading before
  this note leaves `pending`.

## 1. Today

### 1.1 Where the local run happens

- `tools/llmtool/llmtool/task_completion.py:75-78` `_TEST_REGISTRY`: a path is a
  registered test if it matches `tools/llmtool/tests/test_[A-Za-z0-9_]+.py`
  (kind `unittest`) or `scripts/test_[A-Za-z0-9_]+.py` (kind `script`).
- `task_completion.py:1785-1791` `_registered_test(path)` classifies one path.
- `task_completion.py:1854-1922` `_run_registered_tests(root, source_sha,
  eligible, *, home, pycache=None)` is the only place in the read scope that
  spawns tests. For each eligible path it starts `sys.executable -m unittest
  discover` with `cwd=root` (the D6 on the Mac), a reduced env,
  `start_new_session=True`, a 600 s per-module limit (`_TEST_TIMEOUT`,
  `task_completion.py:51`, which the comment at `:49-50` sizes for a ~310 s Mac
  run of `test_task_completion`) and a 256 KiB output limit (`:52`).
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
  - The direct call sites are in `task_submission_cli.py` (section 0,
    review-reported, unverified; BLOCKED on read scope).

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
binding is added by the caller in `task_submission_cli.py` (section 0,
BLOCKED).

### 1.3 Which gates read it

- `completion.evidence.tests` and `developer.direct_result_callback.evidence`
  carry the list. `remote_test.py:313-336` `_admitted_modules` reads
  `evidence.tests[*].path` when no module is declared, and shows the gate
  shape: callback state `WAITING_EXECUTOR` with `missing_gates ==
  ['TEST_FAILED']` and `verification_state == 'FAILED'`
  (`remote_test.py:321-326`). The writer of `TEST_FAILED` /
  `verification_state` is in the BLOCKED file (section 0).
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
  run, `:533-536`, on `ubuntu-24.04` only, `:36`). The receipt is validated
  (`:339-393`), extended with `task_id`, `run_id`, `executor_task`,
  `pm_session`, `command_digest`, and sealed with `receipt_digest`
  (`:575-578`).
- The remote script runs each module with a 180 s cap inside a 540 s session
  (`remote_test.py:81`, `:221-235`), as user `nobody`, from `git archive <sha>`.
- `task_advance.py:2235-2274` `_pre_pr_remote_gate` runs EC2 on the pushed
  branch head before a PR opens; `task_advance.py:3280-3306` step
  `RUN_REMOTE_TEST` runs it on `refs/pull/<n>/head`; `task_advance.py:2346-2362`
  `_pre_review_action` blocks review until the exact head has `PASS` (or the
  `ADMISSION_DENIED`/no-module case).
- `task_advance.py:2118-2170` `_remote_test_status` picks the newest receipt for
  `(task_id, run_id, executor_task, head)` with a valid digest.

### 1.5 What a remote `PASS` proves today, per module

The receipt does not record per-module execution:

- The script adds every module's `Ran N` into one aggregate `ran_total`
  (`remote_test.py:229`). It keeps one final result line per module
  (`:227-228`), stored as `finals[-len(mods)*2:]` (`:256`), with no module
  name and no per-module test or skip count.
- A module's result counts as passing on `OK` or `OK (skipped=N)`
  (`passing_final`, `:164`); the validator accepts the same lines
  (`:387-389`) and needs only `ran_total > 0` overall (`:385`) and
  `len(final) >= len(modules)` (`:386`).

So a mixed set where one module skips all of its tests on Linux and another
module runs real tests yields a valid `PASS` receipt. The review (F1) names
`test_chat_tui_pty` as such a module (its class skips on Linux,
`test_chat_tui_pty.py:68-69`, review-reported, unverified here: outside read
scope). A `PASS` receipt is therefore **not** proof that every module in it
executed a test.

### 1.6 Gaps found while reading

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
5. No per-module execution evidence (section 1.5): an all-skipped module
   inside a mixed set is indistinguishable from an executed one.

## 2. Options

### (a) The exact-head EC2 receipt is the executor test evidence (recommended)

The executor step no longer spawns tests. It records a deferral item and the
test gate reads the exact-head remote-test receipt that the existing advance
steps already produce (`_pre_pr_remote_gate`, `RUN_REMOTE_TEST`).

- Pro: no Mac run at all; no second EC2 run (the pre-PR gate already runs
  one); one source of test truth for executor, review and completion.
- Pro: the receipt already binds task, run, executor D6, head, modules and the
  command digest, sealed by `receipt_digest`.
- Con: the receipt must first gain per-module execution counts (gap 5);
  without them it cannot prove each module ran.
- Con: the executor step cannot report PASS before publish; the gate moves to
  "waiting for remote evidence", which is honest.

### (b) The executor step calls `llmtool task remote-test` itself

- Con: makes the executor step an external action (needs the `guard()` used at
  `task_advance.py:2281`, `:3609`), duplicates the pre-PR EC2 run, and needs a
  pushed ref that may not exist yet at executor time. It adds EC2 load and
  retry logic for no new evidence, and still needs gap 5 closed.

### (c) Keep a local run behind an explicit opt-in flag

- Con: contradicts the Owner decision (no local test runs on the Mac). Even
  opt-in, its result could be mistaken for gate evidence.
- Not recommended. If ever added, its output is diagnostic only and never
  gate evidence; no PR in this plan adds it.

### Recommendation: (a), fail closed

#### Per-module execution evidence (new receipt field)

PR 1 extends the fixed remote script and its validator with one field,
`module_results`: one entry per admitted module, in admitted order, each
`{module, ran, skipped, expected_failures, final}` taken from that module's
own unittest output. The validator requires: entries equal the `modules` list
one-to-one; integer counts, `0 <= skipped + expected_failures <= ran`; for
`PASS`, every entry's `final` is a passing line and `ran_total` equals the sum
of `ran`. For each module, `executed = ran - skipped - expected_failures`.

Receipts without `module_results` (all receipts written before PR 1) stay
valid for the existing pre-review gate as today but **never** satisfy the
executor test gate below (`TEST_REMOTE_EVIDENCE_INVALID`); the executor must
get a fresh exact-head run.

#### The executor test gate predicate

The executor test gate is PASS only when, for the current head, every module in
the expected set (section 4) has **verified execution**. A module has verified
execution only from a receipt R in `record.remote_test_receipts` that meets
all of:

1. `receipt_digest` recomputes (as `task_advance.py:2137-2141`).
2. `task_id`, `run_id` equal the Task; `executor_task` equals
   `completion.executor_task_id`; `read_d6_registration(executor_task, ...)`
   is readable and its `result_sha` equals the receipt `source_sha`.
3. `source_sha` equals the current D6 `result_head` and, when a PR exists, the
   fresh PR head.
4. `result == 'PASS'` (the validator also requires `exit_code == 0`, no
   failures and passing final lines, `remote_test.py:383-390`).
5. `modules` equals exactly the expected remote set from section 4 (sorted
   list equality, not subset), and R carries `module_results`.
6. For that module's `module_results` entry: `executed >= 1` **and**
   `skipped == 0`.

Aggregate `ran_total > 0` is never enough on its own (closes gap 5). A module
whose entry has `skipped > 0` (Linux skips, for example Darwin-only TUI
classes) gets no verified execution from that receipt even though the receipt
is `PASS`. Its coverage must come from a second exact-head receipt from a
**verified GitHub-hosted macOS** remote-test run (section 3) that meets items
1-5 for the same module list, in which that module has `executed >= 1` and
`skipped == 0`. If no such receipt exists, the module stays without evidence
and the gate is not PASS.

Otherwise the gate is not PASS and carries one code:

| condition | code | step |
|---|---|---|
| no receipt for this head | `TEST_REMOTE_EVIDENCE_MISSING` | `RUN_REMOTE_TEST` (existing) |
| only receipts for another head | `TEST_REMOTE_EVIDENCE_STALE` | `RUN_REMOTE_TEST` |
| receipt modules differ from expected set | `TEST_REMOTE_MODULES_MISMATCH` | `RUN_REMOTE_TEST` with the full set |
| a module has `executed == 0` (all skipped or none ran) | `TEST_REMOTE_MODULE_NOT_EXECUTED` | stop for PM |
| a module skipped tests and has no verified hosted macOS execution | `TEST_REMOTE_MODULE_SKIPPED` | stop for PM (macOS route, section 3) |
| `FAIL` | `TEST_FAILED` (existing) | repair route (existing) |
| `TIMEOUT`, `SHA_MISMATCH` | existing `REMOTE_TEST_*` | `NEEDS_ATTENTION` (existing) |
| registered test not runnable remotely (gap 2, 3) | `TEST_REMOTE_MODULE_UNSUPPORTED` | stop for PM |
| digest or binding invalid, or no `module_results` | `TEST_REMOTE_EVIDENCE_INVALID` | stop for PM |

`ADMISSION_DENIED`, `UNAVAILABLE` and `UNKNOWN` outcomes never satisfy the
executor test gate. Code changes with no registered test keep
`TEST_SCOPE_MISSING` (today `task_completion.py:1950-1951`). Docs-only keeps
`NOT_APPLICABLE_DOCS_ONLY`; that is a Git-derived classification, not a test
run, and needs no Mac process.

The worktree path (gap 4) records `NOT_RUN` with reason
`DEFERRED_TO_REMOTE_TEST` (placeholder name, section 0); its gate cannot pass
until a commit exists and the committed head has receipts meeting the
predicate. The deferral item keeps its `path` so the `_admitted_modules`
fallback (`remote_test.py:327-335`) still finds the modules.

## 3. TUI

- TUI test modules are the paths `task_advance.py:450-452` already classifies:
  `tools/llmtool/tests/test_chat_tui*` and `tools/llmtool/tests/test_tui_*`,
  plus the helper `tools/llmtool/tests/tui_pty_driver.py`
  (`task_advance.py:443`, `:1272`).
- Mac exclusion: after PR 4 the executor step spawns no test process at all,
  so no TUI/PTY module can run on the Owner's Mac from it. PR 4 adds a static
  test that pins this (no `unittest` argv in the executor step code).
- Replacement evidence is per module and must show actual execution
  (section 2 predicate, item 6). A `PASS` receipt in which the TUI module
  skipped is **not** evidence for it.
  1. EC2 / hosted Linux receipt: counts for a TUI module only when that module
     has `executed >= 1` and `skipped == 0` on Linux.
  2. Verified GitHub-hosted macOS remote-test receipt for Darwin-only
     coverage. "Verified" means the same trust checks the hosted Linux run
     uses today (`remote_test.py:934-981`: exact job name, exact labels,
     runner group `GitHub Actions`, GitHub Actions app id), with macOS-hosted
     labels and never a `self-hosted` label (as `task_completion.py:2276-2281`
     requires for `github-hosted` CI). The hosted labels today are only
     `ubuntu-24.04` (`remote_test.py:36`); the macOS job is new work (PR 5).
     It is dispatched by an explicit PM step, not automatically.
  3. The existing required Mac CI check (`check (macOS 3.13)`,
     `task_completion.py:58`, `:61`) is **not** TUI module evidence: a green
     check does not show which modules ran or skipped, the check is only
     selected for Mac-workflow paths (`task_completion.py:2452-2458`), and its
     runner names (`task_completion.py:63`) may be self-hosted. It stays an
     unchanged, separate gate.
- If neither 1 nor 2 gives verified execution, the TUI module is recorded
  `NOT_RUN` with reason `TUI_EXCLUDED`, and the executor test gate is not PASS
  (`TEST_REMOTE_MODULE_SKIPPED` or `TEST_TUI_EVIDENCE_MISSING`); the PM
  decides. No gate is lowered: the result is a stop, never a PASS.
- Open questions for the PM (UNKNOWN today):
  - Does "TUI never runs automatically" also exclude EC2 and hosted macOS? If
    yes, items 1 and 2 are dispatched only on an explicit PM step, and until
    then TUI modules stay `NOT_RUN` and the gate stays not PASS.
  - Is a hosted macOS remote-test job acceptable (cost, workflow change)? If
    not, Darwin-only TUI coverage has no evidence source and every Task with a
    Darwin-only TUI module stops for the PM.

## 4. Expected module set

From the Task's declared `read_paths` and `write_paths` (the same source as
`remote_test._task_path_modules`, `remote_test.py:300-310`, and
`task_completion.py:1935-1936`):

- remote set: every registered `tools/llmtool/tests/test_[a-z0-9_]{1,80}.py`,
  as module stems, sorted, TUI modules included;
- unsupported: any other registered path (scripts tests, upper-case names) or
  a remote set larger than 8; gives `TEST_REMOTE_MODULE_UNSUPPORTED`, never a
  silent drop;
- empty remote set with code changes: `TEST_SCOPE_MISSING`.

## 5. Invariants

1. Evidence binds task (`task_id`, `run_id`), D6 (`executor_task` plus the
   registration `result_sha`), exact head (`source_sha`) and the exact module
   list, sealed by `receipt_digest`.
2. No gate becomes PASS without a real run of **each** required module: only
   a validated `PASS` receipt whose `module_results` show `executed >= 1` and
   `skipped == 0` for that module (Linux, or verified hosted macOS) counts.
   Aggregate counts, skipped modules and green CI checks never stand in for a
   module.
3. `NOT_RUN` stays `NOT_RUN`. The deferral item is never rewritten to
   `PASSED`; the gate result references the receipt(s) by `receipt_digest`.
4. The Mac never runs a test from the executor step; no opt-in path is added.
5. No existing gate is weakened: `TEST_SCOPE_MISSING`, the pre-review EC2
   gate, required Mac and Linux CI, formal review and merge gates stay as
   they are. Gap 1 is closed, which tightens the pre-review gate.
6. No intermediate release state: consumers must handle the `NOT_RUN`
   deferral item fail closed (PR 3) before any runner produces it (PR 4).

## 6. Threats

| threat | control |
|---|---|
| stale receipt (older head of the same Task) | `source_sha` must equal current D6 `result_head`, registration `result_sha` and fresh PR head |
| receipt for another head, task or D6 | `task_id`, `run_id`, `executor_task` and digest checks; head equality |
| partial module set | exact list equality with section 4; closes gap 1 |
| skipped module hidden in a mixed `PASS` set | per-module `module_results`; `executed >= 1` and `skipped == 0` per module; closes gap 5 |
| Darwin-only coverage skipped on Linux | only a verified hosted macOS receipt for that module counts; otherwise stop |
| self-hosted or untrusted runner posing as hosted macOS | hosted trust checks (`remote_test.py:934-981`), no `self-hosted` label |
| old receipt without `module_results` | `TEST_REMOTE_EVIDENCE_INVALID` for the executor gate |
| forged or edited receipt in the store | `receipt_digest` recompute; receipt field set validated at write (`remote_test.py:346-348`) |
| silently dropped modules (scripts, >8) | `TEST_REMOTE_MODULE_UNSUPPORTED` stop |
| module slower than the EC2 180 s cap | `TIMEOUT`, never PASS; existing `NEEDS_ATTENTION` route |
| `ADMISSION_DENIED` read as pass | excluded from the executor test gate |
| runners emit `NOT_RUN` before consumers understand it | PR order: PR 3 (consumers) merges before PR 4 (runners) |

## 7. PR plan

Each PR touches at most 4 files. Status of every PR: pending. Order is
binding: each PR merges only after the one before it. Tests follow the test
data rule (Owner #763 A): fixtures use hex SHAs, hex IDs, module names and
counts only; no credential-shaped strings; no production check is disabled.

### PR 1: per-module execution evidence in the receipt. Status: pending

Files: `tools/llmtool/llmtool/remote_test.py`, the existing remote-test test
module (name UNKNOWN, outside read scope), and
`.github/workflows/llmtool-remote-test.yml` only if it embeds or checks the
receipt shape (UNKNOWN, outside read scope).

- The fixed script records `module_results` per module (section 2); the
  validator requires it for new receipts and checks the per-module rules.

Tests: a mixed set (one module `OK (skipped=N)` with `ran == skipped`, one
module `OK` with real runs) yields a receipt whose skipped module has
`executed == 0`; per-module counts that do not add up to `ran_total` are
rejected; entries not one-to-one with `modules` are rejected; `PASS` with a
failing per-module final is rejected.

### PR 2: evidence validator (pure, no behaviour change). Status: pending

Files: `tools/llmtool/llmtool/executor_test_evidence.py` (new),
`tools/llmtool/tests/test_executor_test_evidence.py` (new).

- `expected_remote_modules(read_paths, write_paths)` returns remote set, TUI
  set and unsupported paths.
- `remote_evidence_status(record, *, head, expected_modules)` returns
  `(status, code, receipt_digests)` per section 2, including per-module
  verified execution and the hosted macOS route.

Tests: PASS for an exact-head, exact-set, valid-digest receipt where every
module has `executed >= 1` and `skipped == 0`; MISSING; STALE (receipt for an
older head); other `task_id`, `run_id` or `executor_task`; subset and superset
module lists give MISMATCH; edited field gives INVALID; receipt without
`module_results` gives INVALID; `FAIL`, `TIMEOUT`, `SHA_MISMATCH`,
`ADMISSION_DENIED` never PASS; scripts path, upper-case name and 9 modules
give UNSUPPORTED; TUI paths classified. Mixed-set skip cases:
- TUI module skipped on Linux, non-TUI module ran, aggregate `ran_total > 0`,
  no macOS receipt: not PASS, `TEST_REMOTE_MODULE_SKIPPED`;
- same, plus a verified hosted macOS receipt in which the TUI module executed
  with no skips: PASS;
- same, but the macOS receipt is for another head, a different module list,
  a self-hosted runner, or the TUI module skipped there too: not PASS;
- a module with `executed == 0` and `skipped == 0` (none ran): not PASS,
  `TEST_REMOTE_MODULE_NOT_EXECUTED`;
- non-TUI module partly skipped on Linux, no macOS receipt: not PASS.

### PR 3: consumers and gates read remote evidence, fail closed. Status: pending

Precondition: section 0 resolved (read scope for `task_submission_cli.py`
granted, current state documented, this note updated).

Files: `tools/llmtool/llmtool/task_submission_cli.py`,
`tools/llmtool/llmtool/task_advance.py`,
`tools/llmtool/tests/test_task_submission_cli.py`,
`tools/llmtool/tests/test_task_advance.py` (or the existing advance test
module that covers `_remote_test_status`; UNKNOWN name).

- Every consumer of runner results (the review-cited sites in section 0, to be
  verified) accepts a `NOT_RUN` deferral item only through
  `remote_evidence_status`: no matching evidence keeps the Task at
  `WAITING_EXECUTOR` with the section 2 code; a deferral item is never read as
  `PASSED` and never as `TEST_FAILED` (no false repair route).
- The completion test gate uses `remote_evidence_status` for deferral items.
  Local `PASSED` / `FAILED` items keep today's handling until PR 4 stops
  producing them; no existing check is removed in this PR.
- `_remote_test_status` requires exact module equality (closes gap 1).
- Runners are unchanged in this PR, so no deferral item is produced in
  production yet; the new path is exercised by tests only. The only production
  behaviour change is the gap 1 tightening.

Tests: a synthetic `NOT_RUN` deferral item with no receipt gives
`WAITING_EXECUTOR` and `TEST_REMOTE_EVIDENCE_MISSING`, not PASS and not
`TEST_FAILED`; with an exact-head, exact-set receipt where every module
executed, it clears; with a mixed-set receipt where the TUI module skipped and
no macOS receipt, it stays `WAITING_EXECUTOR`; a subset PASS receipt no longer
clears `_pre_review_action`; a PASS receipt for an older head does not clear;
`FAIL` routes to repair as today.

### PR 4: executor step stops running tests; no-local-run pin. Status: pending

Precondition: PR 3 merged.

Files: `tools/llmtool/llmtool/task_completion.py`,
`tools/llmtool/tests/test_task_completion.py`, this note (status update).

- `run_bounded_tests` and `run_bounded_worktree_tests` keep every binding,
  docs-only and scope check, and return `NOT_RUN` items with reason
  `DEFERRED_TO_REMOTE_TEST` (or the existing remote-deferral shape, section
  0), bound to head (or `diff_sha256`) and modules, keeping `path`. They no
  longer call `_run_registered_tests`.
- If section 0 shows a remaining `LOCAL` fallback in `task_submission_cli.py`,
  removing it moves into this PR or PR 3, keeping the 4-file limit.

Tests: a monkeypatched `subprocess.Popen` is never called from either
function; docs-only result unchanged; `TEST_SCOPE_MISSING`,
`TEST_SOURCE_MISMATCH`, `TEST_SOURCE_CHANGED` still stop; the deferral item is
`NOT_RUN`, never `PASSED`; a static check that the executor-step functions in
`task_completion.py` build no `unittest` argv.

### PR 5: verified hosted macOS remote-test route. Status: pending

Precondition: PM answer to the section 3 questions. If the answer is no, this
PR is dropped and Darwin-only TUI modules always stop for the PM.

Files: `tools/llmtool/llmtool/remote_test.py`,
`.github/workflows/llmtool-remote-test.yml` (UNKNOWN content, outside read
scope), the existing remote-test test module (name UNKNOWN).

- A hosted macOS job with exact macOS-hosted labels and the same trust checks
  as the Linux job; dispatched only by an explicit PM step.

Tests: a job with a `self-hosted` label, wrong runner group, wrong labels or
wrong app id gives `HOSTED_RUNNER_UNTRUSTED` and no receipt; a valid macOS
receipt carries `module_results` and binds the same task, run, executor D6 and
head.

## 8. Not done here

- No code, no tests run, no Git.
- Section 0 is BLOCKED: `tools/llmtool/llmtool/task_submission_cli.py` is
  outside this Task's read scope, so its callers, binding writers, the
  `REMOTE_RECEIPT`/`LOCAL` selection and the `TEST_FAILED` writer are not
  documented from source. The cited lines there are review-reported and
  unverified.
- The repo's status-mark convention was not readable within this note's read
  scope; this note uses a plain `Status: pending` line and a per-PR status.
