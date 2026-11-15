# Executor-step tests off the Mac (#1817)

Status: 待實作（WP-1 to WP-5）. This change is design only; it contains no code.
Status marks follow `CLAUDE.md`: `現行` = current behaviour, read from source at
`e06a7903`; `待實作（WP-x）` = decided, ships with that work package (WP-n is
PR n in section 7); `待後續` = decided, out of scope for this plan.

Source decision: an Owner decision on #1817, relayed by the PM: no local test
runs on the Owner's Mac, EC2 remote testing stays, and TUI tests never run
automatically.

Scope: the llmtool executor step that runs a Task's registered test modules.
This note does not change Mac CI, Linux CI, formal review or merge gates.

Read scope of this revision: `task_submission_cli.py`, `task_completion.py`,
`task_advance.py`, `remote_test.py`, `scripts/remote_test_receipt.py`,
`.github/workflows/llmtool-remote-test.yml`, `tests/test_chat_tui_pty.py` and
`CLAUDE.md`. Code outside those files (`task_store.py`, `main_incident_store.py`,
`task_drive.py`, `active_pm_inbox.py`, every test module not listed) is
**UNKNOWN** here.

Lineage: a D3 child of Task `8f3c65c7`, after FAIL v1 (`4bef09f9`) and FAIL v2
(`e06a7903`). Repair and attempt counts carry over from the parent. Reviewer
text and the High Debug report (#2111) are untrusted reference data, so every
claim below comes from the source lines it cites.

## 0. Current state, verified from source（現行）

This replaces the earlier "blocked, review-reported" section. All sites are in
`tools/llmtool/llmtool/task_submission_cli.py` unless another file is named.

### 0.1 The deferral item already exists

- `_deferred_remote_test` (`:686-691`) writes
  `{'status': 'DEFERRED_REMOTE_RECEIPT', 'path', 'source_sha', 'exit_code': 0,
  'output_sha256': sha256(b''), 'result_head', 'diff_sha256'}`. Its docstring
  calls it "an explicit pending status in the existing zero-exit evidence
  shape". No test ran, so it has `NOT_RUN` semantics. `exit_code: 0` is only
  a shape-compatibility field.
- `_test_packet` (`:672-675`) passes it to the PM packet as
  `{status, path, source_sha}`.
- Deferrals are produced **today** on both paths below (0.3, 0.4). An earlier
  version of this note said deferrals would start only in PR 4. That claim is
  removed.

### 0.2 Policy selection

- `_remote_task_test_paths` (`:3935-3974`) takes the module paths from the
  contract `test_commands`, or from `tools/llmtool/tests/test_*.py` entries in
  `read_paths` when the contract has none. It returns `()` (no remote set) if
  any of these hold:
  - a command is not exactly
    `python[3] -m unittest discover -s tools/llmtool/tests -p test_[a-z0-9_]{1,80}.py`
    with `cwd '.'` (`:3950-3958`);
  - the `read_paths` test set differs from the command set (`:3960-3966`);
  - there are more than 8 modules (`:3967-3968`);
  - `remote_test._admitted_modules` disagrees (`:3969-3973`).
- `_remote_receipt_test_policy` (`:3977-3981`) returns `REMOTE_RECEIPT` when
  that set is non-empty and `LOCAL` otherwise. `config_loader` is unused.
- `_direct_result_callback` (`:4032-4055`) stores the policy as
  `evidence.test_policy`. The callers are:
  - `:4906` (OpenCode): fixed `LOCAL`;
  - `:5396` (AGY), `:5870` (Claude), `:6200` (route not identified in this
    reading) and `:6745` (Codex): computed by the policy;
  - `:9274-9278`, the lazy callback creation: computed only when
    `provider_profile == 'codex'`, otherwise `LOCAL`.
- The Codex packet also carries `test_policy` (`:5161-5171`, `:5201`).

### 0.3 Uncommitted callback verification

- `_direct_result_tests` (`:4020-4029`):
  - With `REMOTE_RECEIPT`, it returns one deferral item per remote path, bound
    to the task `source_sha` and the worktree `diff_sha256`
    (`result_head: None`).
  - With `LOCAL`, it calls `task_completion.run_bounded_worktree_tests`, which
    **runs tests on the Mac** (`task_completion.py:1961-1990`, spawning at
    `:1982`).
- Callback verification (`:9332-9346`) calls `_direct_result_tests`, then sets
  `verification_state: 'READY'`.
  - It writes `TEST_FAILED` / `verification_state: 'FAILED'` only for an item
    whose status is neither `NOT_APPLICABLE_DOCS_ONLY` nor
    `DEFERRED_REMOTE_RECEIPT` and whose `exit_code != 0` (`:9341-9346`).
  - A deferral-only callback therefore reaches `READY`, then `WAITING_PM`
    (`:9377-9378`), with no receipt check. That is correct: an uncommitted
    diff has no commit that EC2 (`git archive <sha>`) or the hosted job could
    test.

### 0.4 Committed completion

- At `:8525-8549`, if the developer used the Claude route (`:8430-8431`) or the
  callback policy is `REMOTE_RECEIPT`:
  - the remote paths are recomputed (`:8530`);
  - `_matching_remote_test_receipt` is called for `source_sha = result_head`
    (`:8531-8533`);
  - deferral items are recorded with `source_sha = result_head = result_head`
    (`:8534-8536`);
  - any returned gate code is appended to `missing` (`:8537-8538`).
- Otherwise `task_completion.run_bounded_tests` **runs tests on the Mac**
  (`:8540-8543`, which reaches `task_completion.py:1954`).
- `TEST_FAILED` is added under the same deferral and docs-only exemption as in
  0.3 (`:8546-8549`).
- When a Claude-route Task has an empty remote set, the completion adds
  `REMOTE_TEST_RECEIPT_MISSING` (`:3986-3987`) and records no test items.
  Whether a docs-only Claude Task reaches this line was not traced: UNKNOWN.
- `task_advance.py:3493-3516` maps any non-CI missing gate to
  `WAITING_EXECUTOR`.

### 0.5 Receipt matching today

`_matching_remote_test_receipt` (`:3984-4017`) scans every stored receipt,
oldest first. It accepts a receipt only when all of these hold:

- `result == 'PASS'`;
- `task_id`, `run_id` and `executor_task` match;
- `source_sha` equals the head;
- the required modules are a **subset** of the receipt's `modules`;
- aggregate `ran_total > 0`;
- `failures == []`;
- `len(final) >= len(modules)`;
- the digest recomputes.

It also has a skip gap:

- A receipt whose final lines include `OK (skipped=N)` is still accepted,
  because `:4011-4016` treats that line as passing.
- The `skipped` flag (`:4008-4010`) only changes the miss code to
  `MAC_EVIDENCE_REQUIRED` when **no** receipt matched.

So today a PASS receipt in which one module skipped every test satisfies the
committed completion gate (gap 5, section 1.5).

### 0.6 Where tests still run on the Mac today

The `LOCAL` fallback runs in each of these cases:

- every OpenCode Task (`:4906`);
- non-Codex lazy callbacks (`:9276`);
- every Task whose remote set is `()` (0.2): `scripts/test_*.py` modules,
  upper-case module names, more than 8 modules, or contract commands that do
  not match.

The local runner is `task_completion.py:1854-1922` (`_run_registered_tests`).
It has two callers, `run_bounded_tests` (`:1925-1958`) and
`run_bounded_worktree_tests` (`:1961-1990`). The `REMOTE_RECEIPT` path skips
both, so the docs-only classification (`NOT_APPLICABLE_DOCS_ONLY`) and the
`TEST_SCOPE_MISSING` check are not applied on it.

## 1. Today: runner, receipt and gates（現行）

### 1.1 Local runner

`task_completion.py`:

- `_TEST_REGISTRY` (`:75-78`): a path is a registered test if it matches
  `tools/llmtool/tests/test_[A-Za-z0-9_]+.py` (kind `unittest`) or
  `scripts/test_[A-Za-z0-9_]+.py` (kind `script`). `_registered_test`
  (`:1785`) classifies one path.
- `_run_registered_tests` (`:1854-1922`) runs `sys.executable -m unittest
  discover` with `cwd = root` (the D6 on the Mac). Limits: 600 s per module
  (`_TEST_TIMEOUT`, `:51`) and 256 KiB of output (`:52`).
- Each item carries `path`, `source_sha`, `exit_code`, `output_sha256`,
  `duration_ms`, `command_sha256` and `status`. The worktree variant also
  carries `result_head: None` and `diff_sha256`.
- Docs-only returns one `NOT_APPLICABLE_DOCS_ONLY` item (`:1947-1949`,
  `:1972-1975`). A code change with no registered test stops with
  `TEST_SCOPE_MISSING` (`:1950-1951`, `:1978-1979`).

### 1.2 Receipt producers

There are two producers. Both write the same 16-field receipt, plus
`failure_excerpt` on a non-PASS result.

1. **EC2: `remote_test.py` `_REMOTE_SCRIPT`** (`:75-261`).
   - It is a fixed program sent over SSH by `run_remote_test` (`:452-581`).
   - Each module runs under `unittest discover` with a 180 s cap inside a
     540 s session (`:221-235`).
   - `ran += first "Ran N tests" match` (`:229`); there is one `final` line per
     module, with no module name (`:227-228`, `:256`).
   - PASS requires no failures and aggregate `ran > 0` (`:235`).
   - `run_incident_remote_test` reuses the same script and validator
     (`:709-741`) for main incidents.
2. **Hosted: `tools/llmtool/scripts/remote_test_receipt.py`**.
   - The same per-module logic: `ran +=` the first match (`:502-503`), one
     `final` per module truncated to 512 chars (`:498-501`), and PASS on no
     failures with `ran > 0` (`:526-528`).
   - Its field set is `_RECEIPT_FIELDS` (`:28-32`).
   - **The hosted workflow** `.github/workflows/llmtool-remote-test.yml` runs
     it from a trusted checkout of `main` at `github.sha` (`:90-95`,
     `:164-179`, job gated to `refs/heads/main`, `:33`) on `ubuntu-24.04`
     (`:37`).
   - The workflow only passes arguments and uploads `receipt.json`
     (`:181-188`). It does not parse the receipt shape.
   - `remote_test._hosted_outcome` (`:1261-1321`) validates the artifact with
     the same `_validate_remote_receipt`. The trust checks in `_hosted_job`
     (`:934-982`) are:
     - exact job name;
     - labels `== ['ubuntu-24.04']` (`:36`, `:963`);
     - runner group `GitHub Actions`;
     - check-run app id `15368`;
     - head equality.

Validator: `_validate_remote_receipt` (`remote_test.py:339-393`) requires the
**exact** field set (`:346-348`). A new receipt field is therefore rejected
until the validator accepts it (see section 5 for the rollout order).

`passing_final` (`remote_test.py:164`, `remote_test_receipt.py:68-69`) and the
validator (`:387-389`) accept `OK` and `OK (skipped=N)`. `OK (expected
failures=N)` is not a passing final, so it already yields FAIL.

### 1.3 Receipt consumers

- `task_submission_cli._matching_remote_test_receipt` (0.5): the committed
  completion gate.
- `task_advance._remote_test_status` (`:2118-2170`): the pre-PR gate
  (`_pre_pr_remote_gate`, `:2235-2274`) and the pre-review gate
  (`_pre_review_action`, `:2346-2362`).
  - It takes the newest digest-valid receipt for
    `(task_id, run_id, executor_task, head)`.
  - It does **not** compare `modules` with the declared set (gap 1).
- `task_advance._remote_test_timeout_count` (`:2173-2210`) and
  `_remote_test_receipt_summary` (`:1087-`): display and timeout counting.
- `remote_test.run_remote_test` (`:543-549`) reuses a stored PASS or
  SHA_MISMATCH receipt only when its `command_digest` matches.
  `_command_digest` (`:442-444`) hashes the argv **and** `_REMOTE_SCRIPT`, so
  any script change forces a fresh EC2 run.
- `TaskStore.append_remote_test_receipt` (called at `remote_test.py:579`,
  `:1465`) lives in `task_store.py`. Whether it validates the receipt field set
  is **UNKNOWN** (outside read scope).

### 1.4 TUI module behaviour on Linux

`tests/test_chat_tui_pty.py:65-79`: `ChatTUIPTYTests.setUpClass` raises
`unittest.SkipTest` when `sys.platform != "darwin"`, and also when `pyte` or a
pseudo-terminal is missing. unittest records a class-level skip as **one skip
with no test run**, so the module reports `Ran 0 tests` and `OK (skipped=1)`:
`ran = 0`, `skipped = 1`. **Skip totals can exceed `ran`.**

On EC2 or hosted Linux this module passes `passing_final`. If the receipt also
lists a module that ran real tests, `ran_total > 0` and the receipt is PASS
(F1). This was read from source; it has not been run here.

### 1.5 Gaps

1. `_remote_test_status` accepts a PASS for any non-empty module subset.
   `run_remote_test` admits any subset of the declared modules
   (`remote_test.py:528-532`). `_matching_remote_test_receipt` accepts a
   superset.
2. `scripts/test_*.py` modules, upper-case names and more than 8 modules have
   no remote route and fall back to `LOCAL` (0.6).
3. The uncommitted worktree cannot be tested remotely. This is already handled
   by deferral plus the committed-head gate (0.3, 0.4).
4. The `LOCAL` fallback still runs tests on the Mac (0.6).
5. There is no per-module execution evidence. A module with `ran = 0,
   skipped = 1` inside a mixed PASS receipt is indistinguishable from one that
   executed (1.4, 0.5).

## 2. Options

- **(a) Recommended: the exact-head remote receipt is the executor test
  evidence.** This extends today's `REMOTE_RECEIPT` path to every Task and
  closes gaps 1, 2, 4 and 5. There is no Mac run, no second EC2 run (the
  pre-PR gate already runs one), and one source of test truth.
- (b) The executor step calls `llmtool task remote-test` itself. It
  duplicates the pre-PR EC2 run, needs a pushed ref that may not exist yet,
  and adds no evidence. Rejected.
- (c) Keep a local run behind an opt-in flag. This contradicts the Owner
  decision. Rejected; no PR adds it.

## 3. Per-module execution evidence（待實作（WP-1, WP-2））

New receipt field `module_results`: a list with one entry per executed module,
in `modules` order, each `{module, ran, skipped, final}`.

- `ran`: the integer from that module's **last** line matching
  `^Ran (\d+) tests? in `, or 0 when there is none. The last line is the
  unittest summary; an earlier match can come from nested subprocess output.
  `ran_total` becomes the sum of the `ran` values. Each producer switches its
  aggregation from first match to last match in the same change that adds
  `module_results` (WP-1 for EC2, WP-2 for hosted).
- `skipped`: the integer from `skipped=(\d+)` in that module's `final`, or 0.
- `final`: identical to the module's entry in `final`.

Validator rules (`_validate_remote_receipt`), when the field is present:

- integer counts `ran >= 0` and `skipped >= 0`. There is **no** constraint
  between `skipped` and `ran`: a class- or module-level `SkipTest` gives
  `ran = 0, skipped = 1` (1.4), and that receipt must stay *valid* so the gate
  can name the real cause;
- `module` values are a prefix of `modules`, in order. For `PASS` they are
  exactly `modules`, one to one;
- `final` equals the corresponding `final` element, `skipped` equals the count
  parsed from it, and `sum(ran) == ran_total`;
- for `PASS`, every `final` passes `passing_final` (unchanged).

## 4. The executor test gate（待實作（WP-3, WP-4））

### 4.1 Expected module set

The expected set comes from the Task's `read_paths` and `write_paths`, the
same source as `remote_test._task_path_modules` (`remote_test.py:300-310`):

- **remote set**: every registered `tools/llmtool/tests/test_[a-z0-9_]{1,80}.py`
  as a sorted module stem, TUI modules included;
- **unsupported**: any other registered path (`scripts/test_*.py`, upper-case
  names) or a remote set larger than 8. This gives
  `TEST_REMOTE_MODULE_UNSUPPORTED` and never a silent drop or a `LOCAL` run;
- an **empty remote set with code changes** gives `TEST_SCOPE_MISSING`, and
  docs-only gives `NOT_APPLICABLE_DOCS_ONLY`. Both are Git-derived
  classifications that spawn no process.

### 4.2 Predicate

The gate passes for head `H` only when a stored receipt `R` meets all of
these:

1. `receipt_digest` recomputes (as at `task_advance.py:2137-2141`).
2. `task_id` and `run_id` equal the Task's, `executor_task` equals
   `completion.executor_task_id`, and `source_sha == H`. `H` is the committed
   `result_head` and, when a PR exists, the fresh PR head.
3. `result == 'PASS'`.
4. `sorted(R.modules)` **equals** the expected remote set: no subset, no
   superset.
5. `R` carries `module_results`.
6. For **every** entry: `ran > 0` **and** `skipped == 0`.

An aggregate `ran_total > 0` is never enough on its own.

### 4.3 Codes

| condition | code | step |
|---|---|---|
| no receipt for `H` | `REMOTE_TEST_RECEIPT_MISSING` (existing) | `RUN_REMOTE_TEST` |
| receipt `modules` differ from expected set | `TEST_REMOTE_MODULES_MISMATCH` | `RUN_REMOTE_TEST` with the full set |
| legacy receipt for `H` (no `module_results`) | `TEST_REMOTE_EVIDENCE_LEGACY` | `RUN_REMOTE_TEST`, once (section 5.3) |
| an entry has `ran == 0` (any `skipped`, including class/module `SkipTest`) | `TEST_REMOTE_MODULE_NOT_EXECUTED` | stop for PM |
| an entry has `ran > 0` and `skipped > 0` | `TEST_REMOTE_MODULE_SKIPPED` | stop for PM |
| `FAIL` | `TEST_FAILED` (existing) | repair route (existing) |
| `TIMEOUT`, `SHA_MISMATCH` | existing `REMOTE_TEST_*` | `NEEDS_ATTENTION` (existing) |
| unsupported registered test (4.1) | `TEST_REMOTE_MODULE_UNSUPPORTED` | stop for PM |
| digest or binding invalid | `TEST_REMOTE_EVIDENCE_INVALID` | stop for PM |

When one receipt triggers several conditions, `NOT_EXECUTED` takes precedence
over `SKIPPED`. Every failing module is listed. `ADMISSION_DENIED`,
`UNAVAILABLE` and `UNKNOWN` outcomes never satisfy the gate.
`MAC_EVIDENCE_REQUIRED` (`task_submission_cli.py:4017`) is replaced by the two
module codes. Readers of that code outside the read scope are UNKNOWN: before
WP-4, the PM searches the repo for them.

### 4.4 TUI

- TUI paths are the ones `task_advance.py:443-456` classifies:
  `tests/test_chat_tui*`, `tests/test_tui_*` and the helper
  `tests/tui_pty_driver.py`.
- On Linux, `test_chat_tui_pty` gives `ran = 0, skipped = 1`, so the code is
  `TEST_REMOTE_MODULE_NOT_EXECUTED` and the PM decides. No gate is lowered:
  the result is a stop, never a PASS.
- The required Mac CI check is not module evidence. It does not show which
  modules ran, and it stays a separate, unchanged gate.
- 待後續: a verified GitHub-hosted **macOS** remote-test job for Darwin-only
  modules.
  - It would need the same trust checks as `_hosted_job` (`:934-982`), with
    macOS-hosted labels and never `self-hosted`, and would be dispatched only
    by an explicit PM step.
  - It needs a PM answer to two questions:
    1. Does "TUI never automatic" also exclude EC2 and hosted runs?
    2. Is a macOS hosted job acceptable?
  - It is not part of WP-1 to WP-5. Until it exists, every Task that declares a
    Darwin-only TUI module stops for the PM at completion.

## 5. Rollout and compatibility（待實作（WP-1 to WP-5））

### 5.1 Deferral item: reuse, no new shape

- `DEFERRED_REMOTE_RECEIPT` stays the only deferral status, with its existing
  fields (0.1). This plan adds no `NOT_RUN` status string and no
  `DEFERRED_TO_REMOTE_TEST` name.
- Its meaning is `NOT_RUN`. Every consumer treats it as "no test ran": its
  `exit_code: 0` is never read as a pass, and it is never rewritten to a
  passed item. The gate result references receipts by `receipt_digest`.
- The packet (`_test_packet`, `:672-675`) and `_admitted_modules`
  (`remote_test.py:313-336`, which reads `tests[*].path`) already handle it
  unchanged.

### 5.2 Order: producers, packet and consumers

- **WP-1**: the validator accepts optional `module_results` and the EC2 script
  emits it. Both live in `remote_test.py`, so producer and validator change
  atomically. The `_command_digest` change (1.3) means no pre-WP-1 EC2 receipt
  is reused.
  - `run_incident_remote_test` removes `module_results` before
    `_screen_incident_receipt` (`:734`), so the incident receipt shape does not
    change and `main_incident_store.py` (UNKNOWN) needs no change.
  - If `task_store.append_remote_test_receipt` checks the receipt field set
    (UNKNOWN), the `task_store.py` change and its test go in **this** PR (see
    7).
- **WP-2**: the hosted producer (`scripts/remote_test_receipt.py`) emits the
  same field.
  - The workflow runs `main`'s copy, so this goes live on merge. It merges
    only after WP-1 is on `main` **and** the llmtool build that validates
    hosted receipts includes WP-1. How the running build is confirmed is
    UNKNOWN; the PM confirms it.
  - The workflow file needs no change (1.2).
- **WP-3**: a pure predicate module with no behaviour change.
- **WP-4**: the consumers switch to the predicate. This is the first
  production behaviour change: a mixed PASS receipt with a skipped or
  unexecuted module no longer clears completion, and gap 1 closes.
- **WP-5**: the remaining `LOCAL` fallback is removed (0.6). Every executor
  path now produces either deferral items, a docs-only item or a stop.

Each step is backward compatible with the one before it. No release has a
producer emitting a shape that a merged consumer rejects, and no consumer
requires a field that a merged producer does not yet emit: the gate
(WP-4) treats a missing field as legacy and asks for a fresh run.

### 5.3 Legacy receipts

A receipt without `module_results` (every receipt written before WP-1, or
WP-2 for hosted runs) stays digest-valid and stays readable for display
(`_remote_test_receipt_summary`).

- It never satisfies the executor gate.
- For head `H`, the gate returns `TEST_REMOTE_EVIDENCE_LEGACY`, and
  `task_advance` routes the result to `RUN_REMOTE_TEST` for `H` **once**. The
  script digest changed, so the reuse check at `remote_test.py:543-549` does
  not short-circuit.
- If the fresh run is also legacy (an old build), the Task stops with
  `TEST_REMOTE_EVIDENCE_INVALID` instead of looping.
- `_remote_test_status` applies the same legacy rule, so the pre-review gate
  and the completion gate cannot disagree.

### 5.4 Uncommitted callback and committed acceptance

The current split stays.

- The uncommitted callback (0.3) with deferral items may reach `READY` /
  `WAITING_PM`, so the PM can review and publish the diff. No receipt can
  exist for an uncommitted diff.
- Executor acceptance (committed completion, 0.4) waits for committed-head
  receipts that meet section 4.
- WP-4 pins this split with tests.

## 6. Invariants and threats

1. Evidence binds task (`task_id`, `run_id`), D6 (`executor_task`), the exact
   head (`source_sha`) and the exact module list, and is sealed by
   `receipt_digest`.
2. No gate passes without a real run of **each** required module (`ran > 0`,
   `skipped == 0` in `module_results`). Aggregate counts, skipped modules and
   green CI checks never stand in for a module.
3. `DEFERRED_REMOTE_RECEIPT` means `NOT_RUN` and is never treated as passed.
4. After WP-5 the Mac never runs a test from the executor step, and no opt-in
   path exists.
5. No existing gate is weakened: `TEST_SCOPE_MISSING`, the pre-review remote
   gate, required Mac and Linux CI, formal review and merge stay. Gap 1
   tightens.

| threat | control |
|---|---|
| stale receipt | `source_sha == H` (committed head, fresh PR head) |
| receipt for another task, run or D6 | `task_id`, `run_id`, `executor_task`, digest |
| partial or extended module set | exact sorted equality (4.2 item 4) |
| class/module `SkipTest` hidden in a mixed PASS | `ran > 0` per module, `TEST_REMOTE_MODULE_NOT_EXECUTED` |
| partly skipped module | `skipped == 0` per module, `TEST_REMOTE_MODULE_SKIPPED` |
| validator rejecting a valid `skipped > ran` receipt and masking the cause | no `skipped <= ran` rule (section 3) |
| producer ahead of validator | WP-1 before WP-2 (5.2) |
| legacy receipt read as module evidence | `TEST_REMOTE_EVIDENCE_LEGACY`, one fresh run, then stop |
| forged or edited receipt | digest recompute; field set validated at write |
| silently dropped modules | `TEST_REMOTE_MODULE_UNSUPPORTED` |
| deferral `exit_code: 0` read as pass | explicit `NOT_RUN` handling, tested in WP-4 |

## 7. PR plan

Each PR touches at most 4 files and merges only after the one before it. Tests
follow the test data rule (Owner #763 A): fixtures use hex SHAs, hex IDs,
module names, counts and unittest summary lines only; there are no
credential-shaped strings, refusal flows inject the screen function, and no
production check is disabled. Test module names marked UNKNOWN are outside
this note's read scope; the PM confirms them before dispatch.

### PR 1: receipt field, validator and EC2 producer（待實作（WP-1））

Files:

- `tools/llmtool/llmtool/remote_test.py`;
- the existing remote-test test module (name UNKNOWN);
- only if `append_remote_test_receipt` checks the field set (UNKNOWN):
  `tools/llmtool/llmtool/task_store.py` and its test module (name UNKNOWN).

Changes:

- `_REMOTE_SCRIPT` emits `module_results` and uses last-match aggregation.
- `_validate_remote_receipt` accepts the field as optional and enforces the
  section 3 rules.
- The incident path strips the field.

Tests:

- `ran = 0, skipped = 1, final 'OK (skipped=1)'` beside `ran = 12,
  skipped = 0, final 'OK'` is a **valid** PASS receipt (no `skipped <= ran`
  rule).
- `ran = 5, skipped = 2` is valid.
- `sum(ran) != ran_total`, entries out of order or not one-to-one on PASS, a
  `skipped` that disagrees with `final`, and negative counts are each
  rejected.
- A pre-WP-1 field set is still accepted.
- The incident receipt has no `module_results`.
- Nested `Ran N tests` output earlier in a module's output does not change its
  `ran`.
- If `task_store.py` changes: a stored receipt with `module_results`
  round-trips, and an unknown extra field is still refused.

### PR 2: hosted producer（待實作（WP-2））

Precondition: PR 1 merged and the validating build confirmed (5.2).

Files:

- `tools/llmtool/scripts/remote_test_receipt.py`;
- its test module (name UNKNOWN).

`.github/workflows/llmtool-remote-test.yml` is unchanged; it was read and does
not parse the receipt.

Changes:

- `main()` emits the same `module_results` and uses last-match aggregation.
- `_RECEIPT_FIELDS` (`:28-32`) gains the field.

Tests (with an injected `runner`):

- a class-skip module plus an executing module gives `ran = 0, skipped = 1`
  for the first, and the receipt passes the PR 1 validator;
- the mismatch and checkout-failure receipts carry an empty list;
- a timeout gives a prefix.

### PR 3: pure evidence predicate（待實作（WP-3））

Files:

- `tools/llmtool/llmtool/executor_test_evidence.py` (new);
- `tools/llmtool/tests/test_executor_test_evidence.py` (new).

Functions:

- `expected_remote_modules(read_paths, write_paths)` returns the remote set,
  the TUI set and the unsupported paths.
- `remote_evidence_status(record, *, executor_task, head, expected)` returns
  `(status, code, failing_modules, receipt_digest)`, as defined in 4.2 and
  4.3.

Tests:

- **regression F1**: a TUI module with `ran = 0, skipped = 1` plus a non-TUI
  module with `ran = 12`, aggregate `ran_total = 12`, PASS: not PASS,
  `TEST_REMOTE_MODULE_NOT_EXECUTED`, failing module named;
- **regression skip**: one module with `ran = 5, skipped = 2`, the others
  clean: `TEST_REMOTE_MODULE_SKIPPED`;
- a module with `ran = 0, skipped = 0`: `NOT_EXECUTED`;
- both conditions in one receipt: `NOT_EXECUTED` with both modules listed;
- every module `ran > 0, skipped = 0`, exact set, current head: PASS;
- a subset or superset list: `TEST_REMOTE_MODULES_MISMATCH`;
- an older head: `REMOTE_TEST_RECEIPT_MISSING`;
- another task, run or executor: ignored;
- an edited field: `INVALID`;
- no `module_results`: `TEST_REMOTE_EVIDENCE_LEGACY`;
- `FAIL`, `TIMEOUT`, `SHA_MISMATCH` and `ADMISSION_DENIED`: never PASS;
- a scripts path, an upper-case name or 9 modules: `UNSUPPORTED`;
- TUI paths are classified.

### PR 4: consumers read the predicate（待實作（WP-4））

Files:

- `tools/llmtool/llmtool/task_submission_cli.py`;
- `tools/llmtool/llmtool/task_advance.py`;
- `tools/llmtool/tests/test_task_submission_cli.py`;
- the advance test module covering `_remote_test_status` (name UNKNOWN).

Changes:

- `_matching_remote_test_receipt` (`:3984-4017`) is replaced by
  `remote_evidence_status` at committed completion (`:8528-8538`). Its codes
  go into `missing`, and deferral items stay as they are (`:8534-8536`).
- The `TEST_FAILED` checks (`:8546-8549`, `:9341-9346`) keep exempting
  `DEFERRED_REMOTE_RECEIPT`, so a deferral is never `TEST_FAILED` and never
  passed.
- `_remote_test_status` (`task_advance.py:2118-2170`) applies exact module
  equality and the legacy rule.
- `task_advance` maps the codes from 4.3 to their steps: `RUN_REMOTE_TEST`
  for missing, mismatch and legacy (once), and a stop for the module codes,
  `UNSUPPORTED` and `INVALID`.

Tests:

- the committed completion with the F1 receipt stays `WAITING_EXECUTOR` with
  `TEST_REMOTE_MODULE_NOT_EXECUTED` (today it clears);
- with the skip receipt: `TEST_REMOTE_MODULE_SKIPPED`;
- with a clean exact-set receipt it clears;
- a subset PASS no longer clears `_pre_review_action`;
- a legacy PASS gives one `RUN_REMOTE_TEST`, then a stop;
- an uncommitted callback with only deferral items still reaches `READY` /
  `WAITING_PM` with no receipt (5.4);
- a deferral item's `exit_code: 0` is never counted as a pass;
- `FAIL` routes to repair as today.

### PR 5: remove the `LOCAL` fallback; no-local-run pin（待實作（WP-5））

Precondition: PR 4 merged.

Files:

- `tools/llmtool/llmtool/task_completion.py`;
- `tools/llmtool/tests/test_task_completion.py`;
- `tools/llmtool/llmtool/task_submission_cli.py`;
- `tools/llmtool/tests/test_task_submission_cli.py`.

Changes:

- `task_completion` gains a no-spawn classifier. It keeps every binding check,
  docs-only classification, `TEST_SCOPE_MISSING` and the source checks from
  `run_bounded_tests` / `run_bounded_worktree_tests`, and returns either a
  docs-only item, deferral paths or a stop. `_run_registered_tests` has no
  caller left and is removed.
- The `LOCAL` sites change: `:4906` (OpenCode), `:9276` (non-Codex lazy
  callback), and the `()` result of `_remote_receipt_test_policy`. Each one
  either defers or stops with `TEST_REMOTE_MODULE_UNSUPPORTED` /
  `TEST_SCOPE_MISSING`; none runs tests.
- The PR updates this note's status marks.

Tests:

- a monkeypatched `subprocess.Popen` is never called on either executor path,
  for every provider profile;
- the docs-only result is unchanged;
- `TEST_SCOPE_MISSING`, `TEST_SOURCE_MISMATCH` and `TEST_SOURCE_CHANGED`
  still stop;
- a scripts test or 9 modules gives `UNSUPPORTED`, not a local run;
- a static check confirms that `task_completion.py` and the executor paths in
  `task_submission_cli.py` build no `unittest` argv.

### Hosted macOS route（待後續）

See 4.4. This is a separate plan after the PM answers.

## 8. Not done here

- No code, no tests run, no Git.
- `task_store.py`, `main_incident_store.py`, `task_drive.py` and the test
  module names marked UNKNOWN were not read. PR 1's file list and PR 4's
  `MAC_EVIDENCE_REQUIRED` readers depend on them, and the PM resolves both
  before dispatch.
- The unittest class-skip accounting (`ran = 0, skipped = 1`) was read from
  source and reviewed reasoning. It has not been observed in a run here.
