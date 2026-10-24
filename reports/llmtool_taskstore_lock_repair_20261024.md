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
- **Verification after the repair.**
  - `tests.test_task_store` and `tests.test_task_submission_cli` together: 66/66 OK.
  - The same stress harness: 0 failures in 150 runs.
  - The full suite was rerun; its result is in the PR.
- **Follow-up (outside this allowlist).** Any other store that creates its lock file through `dir_fd` may have the same race.
