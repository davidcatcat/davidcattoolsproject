# TaskStore lock repair — 2026-10-24

## Task and ownership

- Task: `a11c-taskstore-lock-20261024`; GitHub tracking/contract: [PR #315 discussion](https://github.com/davidcatcat/davidcattoolsproject/pull/315#issuecomment-6381125599).
- Developer session: `/root/taskstore_lock_developer`.
- Model/provider: `UNKNOWN`; the runtime did not expose a precise model identifier. Token and cost: `UNKNOWN`.
- D6 root: task-owned worktree, branch `fix/a11c-taskstore-flock-20261024`, base and current source HEAD `cf6eb6d92100dc684d49f1f28434d4fd053f3eb6`; registered `ACTIVE/LOCKED`; ENV-2 `PASS`.
- No real TaskStore data, Provider, credentials, production route, or dispatch policy was used. No commit, push, CI run, or formal review was performed by this Developer.

## Changes

Only these source/test paths changed, plus this report:

- `tools/llmtool/llmtool/task_store.py`
- `tools/llmtool/tests/test_task_store.py`
- `tools/llmtool/tests/test_task_submission_cli.py`
- `reports/llmtool_taskstore_lock_repair_20261024.md`

Per-task update locking now uses a persistent owner-only lock file and `fcntl.flock` with a bounded two-second wait. Lock files are opened without following symlinks, checked as regular owner-owned mode-0600 single-link files, and are never unlinked during updates. Kernel release after process death allows the next writer to proceed. Normal contention waits; expiry reports the safe `BUSY` category. A waiter reloads the record under the lock before checking the original CAS fields, preserving genuine `CONFLICT` outcomes.

TaskStore errors retain their safe generic display text and now expose bounded `task_store_code` and `task_store_error_code` attributes for `BUSY`, `INVALID`, `CONFLICT`, and `UNAVAILABLE` classification. No filesystem path, record content, or OS error text is included in the displayed message.

Regression coverage includes a SIGKILLed lock holder, synchronized child writers that observe actual lock contention and then both complete valid updates, simultaneous first creation of one persistent lock inode with a real CAS loser, bounded timeout, injected open/fstat failures with descriptor cleanup, the stale-delegation exactly-one-Provider-call path, and interruption after the persisted one-shot claim but before client/provider creation. The interrupted claim remains `DEVELOPER_PENDING` / `WAITING_EXECUTOR`; a second dispatch is refused and the fake client records zero calls.

## Focused verification

Commands ran from `tools/llmtool` in this D6 root using its Python 3.13 venv and `-B` for every Python process:

```text
.venv/bin/python -B -m unittest tests.test_task_store -v
Ran 17 tests in 1.081s — OK

.venv/bin/python -B -m unittest \
  tests.test_task_submission_cli.TaskSubmissionCLITests.test_concurrent_stale_delegations_claim_once_before_provider_call \
  tests.test_task_submission_cli.TaskSubmissionCLITests.test_interrupted_after_durable_claim_is_not_completed_or_replayed -v
Ran 2 tests in 1.243s — OK
```

The final deterministic first-lock test observed both child processes opening the same lock inode; one held the acquired flock while the other reported `WAITING`, then the loser re-read the updated task and returned `CONFLICT`. The separate waiting-writer test observed actual `BlockingIOError` before releasing the lock holder. SIGKILL, timeout, injected I/O failures and cleanup tests passed.

Before final focused verification, D6 B-min and `git diff --check` passed against a reviewed current per-path candidate. The test launches used `-B` to prevent additional bytecode files. Final PM B-min reconciliation is still required after adding this report.

## Unbound diagnostic observation and remaining gates

One intermediate deterministic-test attempt ended with a child classified as `TASK_STORE_UNAVAILABLE`; an in-memory diagnostic wrapper observed errno `2` (`ENOENT`) during that run's lock open. The occurrence did not reproduce in the subsequent instrumented handshake run or final uninstrumented focused run. Its cause remains **UNKNOWN** and no production cause or classification change is inferred from it. A separate earlier first draft of the test had a harness variable-name error; that was corrected before final verification.

The candidate remains uncommitted and unreviewed. PM exact-diff/D6 review, independent formal Claude review, required Python 3.9/3.14 exact-head CI and merge decision are pending. No full suite or GitHub Actions were triggered.

## Addendum — `ENOENT` root cause and bounded repair (Claude, Owner-delegated takeover)

Codex reached its usage limit at 15:08 after the Developer turn above had completed. The Owner delegated the PM role to Claude session `d4fdda6e`, which became the writer for this task. The file allowlist is unchanged. The handoff is recorded on [PR #315](https://github.com/davidcatcat/davidcattoolsproject/pull/315#issuecomment-6381499717).

- **PM gate on the candidate above.** The full `tools/llmtool` suite ran 2102 tests: 2101 passed and 1 errored. The error was `test_concurrent_process_cas_keeps_real_conflict`: one child exited before printing `OPENED`. Under CPU load, a harness that kept the child's stderr reproduced it in 27 of 150 runs. Each time, the child failed at the lock-file `os.open` with `TASK_STORE_UNAVAILABLE`.
- **Root cause (confirmed).** This happens on macOS 15.7.9 (APFS) when two processes run `openat(dir_fd, name, O_CREAT)` on the same new name at the same moment. One of them can get `ENOENT`:

  | Open method | Failures |
  | --- | --- |
  | Through `dir_fd`, 2 processes racing | 54 of 200 opens |
  | By absolute path, same race | 0 of 200 |
  | Single process | 0 of 60 |

  The result is the same with or without `O_NOFOLLOW`/`O_NONBLOCK`. This explains the "UNKNOWN" `ENOENT` noted above. The failure lives in the OS call, not in the TaskStore logic.
- **Repair.**
  - `_update` now retries the lock-file open on `FileNotFoundError`.
  - Retries share the existing `TASK_UPDATE_LOCK_TIMEOUT_SECONDS` deadline with the `flock` wait, so the whole lock attempt stays bounded.
  - When the deadline passes, the call raises `TASK_STORE_UNAVAILABLE`.
  - Every other `OSError` is handled as before.
- **New tests.**
  - `test_concurrent_lock_create_enoent_is_retried`: one injected `ENOENT` is followed by a successful update, and both attempts keep `O_CREAT|O_NOFOLLOW`.
  - `test_persistent_lock_create_enoent_is_bounded_and_unavailable`: with the deadline set to 0.1 s, the call returns `UNAVAILABLE` within 1 s, the message leaks no detail, and the record is unchanged.
- **Verification after the repair** (before review round 1).
  - `tests.test_task_store` and `tests.test_task_submission_cli` together: 66/66 OK.
  - The same stress harness: 0 failures in 150 runs.
  - The full suite was rerun; its result is in the PR.
- **Follow-up (outside this allowlist).** Any other store that creates its lock file through `dir_fd` may have the same race.

## Formal review round 1 (PR #316 head `faab373`): FAIL, and repairs

The PR base is main `a2c11a7`; main was merged in so CI tests the merge result. The focused counts above (17 + 2) predate the tests below.

Blocking QA findings, now fixed:

1. **Required test (5) only half-asserted.** The interrupted-claim test now also runs the real completion path, `run_task_complete`. It must return 1 and never call the PM. The task must stay `PM_RESPONDED`, the Developer claim `DEVELOPER_PENDING`, and the completion `WAITING_EXECUTOR`/`NOT_STARTED` with no PM response. The completion path records only the missing gate. A second dispatch is still refused.
2. **No tests for planted lock paths.** A new table test covers six cases: a symlink, a dangling symlink, a hardlink, mode 0644, a FIFO and a directory.
   - Each case must fail with `TASK_STORE_INVALID` in under 1 s.
   - The record, the outside target's content and mode, and the missing symlink target must all stay untouched.
   - A separate test covers a lock owned by another user (`fstat` patched).
   - Because of this test, the open now classifies `EISDIR`, `ENXIO` and `EOPNOTSUPP` as `INVALID` (planted state), the same as `ELOOP`.

Advisories taken:
- **Child lock deadline.** The two multi-process tests set the child's lock deadline to 30 s, so a stalled parent cannot turn the waiter into BUSY. BUSY keeps its own bounded test.
- **Descriptor cleanup.** If closing the lock fd raises, the directory fd is still closed.

Verification requests:
- **Nothing else deletes lock files.** No TaskStore code unlinks, renames or garbage-collects `.<id>.json.lock`. The only unlinks are of temporary record files.
- **No directory scans.** TaskStore has no `listdir`/`scandir`/`glob` over the store directory, so the now-persistent lock entries are never scanned.
- **One constructor form.** `TaskStoreConflict` is constructed only as `TaskStoreConflict()`.

**Rollout note (PM acknowledgement required).** The old `O_EXCL` protocol and the new `flock` protocol must not run against the same task store at the same time.
- An old llmtool treats the now-persistent lock file as permanently held, so its updates fail with BUSY or CONFLICT.
- A new llmtool can `flock` a file that an old process created under `O_EXCL`. The two then lose mutual exclusion.
- Every llmtool install and worktree venv that shares `~/.local/state/llmtool/tasks` must therefore be upgraded together, with no old-version writer running during the switch.
- Old zero-length 0600 lock files left behind by a dead old writer are simply reused by the new code. This unblocks tasks that were stuck.

Not taken (recorded):
- A umask that strips owner bits makes the lock INVALID. This fails closed; no `fchmod` was added.
- The CLI does not print `task_store_code`.
- The `select()`-on-text-pipe helper and the SIGKILL-while-waiting variant are unchanged.

Verification after the round-1 repairs: full `tools/llmtool` suite **2106 tests OK** (skipped 2), macOS, CPython 3.13, `-B`. The CAS stress repro under CPU load had 0 failures in 100 runs.
