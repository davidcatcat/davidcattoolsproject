# WP-5 design: no-PR read-only `task high-debug` entry (#1943)

**Proposal only; not a review or acceptance.** Source: `main` at `a2b6f4d891d89f16f3411122e25f307a54538a01`. Code paths are under `tools/llmtool/llmtool/`; claims about today cite `path:line` at that SHA. Anything not read is UNVERIFIED. The entry is 待實作（WP-5）.

## 1. Today (現行)

- **CLI.** Required: `task_id`, `--executor-task`, `--pr` (int), `--family`, and `--failed-corrections` or `--early` (`task_submission_cli.py:1021-1030`), passed to `run_high_debug_with_fallback` (`:2417-2423`). Refusals print `high-debug refused: CODE (detail)` (`cli.py:736-746`).
- **No PR or no executor Task.** argparse rejects the call before any refusal code (`task_submission_cli.py:1023-1024`); how the usage error shows is UNVERIFIED. A bad `--pr` refuses `HIGH_DEBUG_ARGUMENT_INVALID` (`high_debug.py:1275-1281`).
- **No committed D6 head.** Completion evidence must name the executor Task and `common_dir`, else `HIGH_DEBUG_D6_UNAVAILABLE` (`high_debug.py:1364-1375`). The registration `result_sha` must be 40-hex and equal the D6 HEAD, else `HIGH_DEBUG_HEAD_MISMATCH` (`:1376-1377`). A Task with no completion and no commit gets no Debug.
- **Admission order** (`high_debug.py`): 2 failed corrections or an early reason (`:1282-1295`); screen block (`:1296`); PR repair-claim family (`:1299-1305`); evidence (`:1306-1334`); dispatch key (`:1336-1339`); account (`:1342-1358`); D6 and head (`:1364-1377`); no open dispute (`:1380-1382`).
- **Caps.** 8 entries per Task, else `HIGH_DEBUG_LIMIT` (`high_debug.py:1383-1384`); one AI Debug per lineage (`:1247-1264`).
- **Run.** Rule cards from authenticated `main` (`high_debug.py:1105-1130`). A new Debug D6 at the head (`:1392-1397`). Both routes run read-only (`:998-1028`); a dirty tree refuses `HIGH_DEBUG_WROTE_FILES` (`:1504-1508`).
- **Screen.** Evidence over 8192 bytes refuses; a hit writes a text-free record and refuses `HIGH_DEBUG_EVIDENCE_SENSITIVE` (`high_debug.py:1306-1333`). A report over 16384 bytes or with a hit refuses `HIGH_DEBUG_REPORT_INVALID` (`:1520-1538`). Both records bind `d6` to the executor Task, not the Debug D6 (`:1321-1323`, `:1532-1534`). Records clear by disposition or batch recovery (`:640-671`, `:866-908`); recovery needs the same `d6` and source digest (`:814-820`). A pending record blocks the Task (`:1205-1239`).
- **Report flow.** Status from Outcome/cost (`high_debug.py:1184-1202`, `:1539-1540`), then a Task `high_debug` entry (`:1541-1546`), a PR comment (`:1547`), a `HIGH_DEBUG` completion event (`:1549-1552`) and stdout metadata (`:1553-1555`).
- **Routes.** Codex first; Claude only on quota or auth failure, else `HIGH_DEBUG_WAITING` (`high_debug.py:942`, `:1563-1587`).
- **Repair link.** Repair #2 accepts only an `RCA_CONFIRMED` `high_debug` entry bound to its completion event (`task_submission_cli.py:7115-7203`, `:7346-7395`).
- **Drift.** `CLAUDE.md:85` marks the Debug keys 待實作（WP-B）; `high_debug.py:1336` uses them.

## 2. Requirements

Quoted verbatim as the Task contract supplies them.

**SOURCE 1**, Owner words, [#1928 c6877449289](https://github.com/davidcatcat/davidcattoolsproject/issues/1928#issuecomment-6877449289) item 4:

> **四、後續治理** 不需要為這次問題開放已暫停的 codex-developer，也不需要等待整套 WP-B 才能修正明確的狀態恢復缺陷。但請將「沒有 PR、沒有 committed D6 head 時，正式 Debug 無法啟動」列入 WP-B 的設計缺口，未來應提供受控的唯讀診斷入口。

Gloss: provide a controlled read-only diagnostic entry for when there is no PR and no committed D6 head.

**SOURCE 2**, PM text, not Owner, [#1943 c6879556133](https://github.com/davidcatcat/davidcattoolsproject/issues/1943#issuecomment-6879556133) WP-5 row:

> | #1943 WP-5 | **WP-B follow-up** | No-PR read-only `task high-debug` entry (Owner #1928 c6877449289 item 4). It stays read-only and screened, writes nothing, and creates no review claim. | Edits `high_debug.py`, which #1502 holds (WP-D phase 2). The Claude reason-persistence half is **already done** (#1824, PR #1875). |

**SOURCE 3**, Owner words, O6, [#1943 c6879415056](https://github.com/davidcatcat/davidcattoolsproject/issues/1943#issuecomment-6879415056):

> **O6：Claude 設計前置諮詢 — APPROVED WITH LIMITS。** 授權 claude-debug 在明確條件下用於實作前的設計技術諮詢。限制：每個 Work Package 最多一次。初期每週上限 10 次。適用複雜狀態機、claim 鏈、重要安全或資料完整性設計，以及有證據的重大技術分歧。普通技術問題不必啟動第二模型。不改變現有 RCA 的 Codex 優先與 Claude fallback 路線。不新增 Worker dispatch key。不取代正式跨家族 Review。技術諮詢只能提供建議和證據。PM 在授權範圍內裁決；正式 Reviewer 必須獨立審查實際程式碼與測試。如涉及正式 finding 爭議，仍使用既有 arbiter。

Gloss: design advice once per WP, at most 10 a week, listed cases only, no new dispatch key, advice only.

**Card rules** (`skills/role-cards/debug.md:27-30`): `type=debug_report` or `type=design_advice`; no `Verdict:` line, `review_verdict` marker, review claim or repair-count change; RCA keeps `codex-debug` first.

## 3. Design (待實作（WP-5）)

### 3.1 CLI

```text
llmtool task high-debug <task_id> --no-pr --kind debug_report|design_advice
    --wp <id> --question-file <path> [--evidence-file <path>]
    --source-sha <40-hex> [--executor-task <D6> --diff-sha256 <64-hex>]
    [--case <case>] [--disagreement-ref <URL>] [--route codex|claude]
```

- `--no-pr` excludes `--pr`, `--failed-corrections` and `--early`; their `required` flags move into the run function (`task_submission_cli.py:1024-1028`).
- **Pin, required.** `--source-sha` must be reachable from authenticated `main`. With `--executor-task`, that D6 must be registered and owned by this Task, based at `--source-sha`, and its uncommitted diff must hash to `--diff-sha256`.
- `--case` (advice only): `state_machine`, `claim_chain`, `security_design`, `data_integrity_design` or `disagreement` (needs `--disagreement-ref`).

### 3.2 Evidence bound

Bound: Task, WP, kind, case, route, source SHA, executor D6 and diff digest (or `none`), plus question, evidence, rules (`high_debug.py:1130`) and report digests. The diff is screened untrusted evidence, never applied. A separate scratch execution D6 is created at the source SHA (`high_debug.py:911-922`).

### 3.3 Output

- stdout: one JSON object with `type`, `advisory=true`, the bindings and the screened report, which starts "Advisory only; not a review or acceptance criterion."
- `debug_report` keeps `REPORT_SECTIONS` (`high_debug.py:22-24`). `design_advice` has Identity and source, Question, Options, Recommendation, Risks, Outcome/cost; outcome `ADVICE_GIVEN`, `ADVICE_UNSURE` or `BLOCKED`.
- A `Verdict:` line, `review_verdict` or `<!-- activity:` marker refuses `HIGH_DEBUG_CONSULT_REVIEW_CLAIM`, never reworded.

### 3.4 Never

- No `high_debug` entry or completion event, so repair #2 cannot use it (`task_submission_cli.py:7198-7202`); no GitHub post or unposted record (`high_debug.py:158-207`).
- No change to `_admit_debug`, the 8-entry cap or any repair count. No new dispatch key (`high_debug.py:1336-1339`). A write refuses `HIGH_DEBUG_WROTE_FILES`.

### 3.5 Screen

- `_require_screen_cleared` runs first, unchanged. Question, evidence and diff are each screened; the report gets today's checks (`high_debug.py:1520-1538`). Family: `consult-<wp>` for advice, `--family` for RCA.
- **Recovery binding.** Every record binds `d6` to the original executor D6, as today (`:1321-1323`, `:1532-1534`), never to the scratch execution D6. With only a base-SHA pin, `d6` is the label `base-<source-sha>` (fits `_SCREEN_D6`, `:70`). Recovery must name that `d6` and reproduce the source digest (`:814-820`).
- **Diff source.** New source `D6_DIFF`, code `HIGH_DEBUG_DIFF_SENSITIVE`. Its record adds optional `base_sha` (the pin); its `source_sha256` equals `--diff-sha256`; older records stay valid (`:544-548`). Recovery rebuilds the diff from the original executor D6, like `DEVELOPER_OUTPUT` (`:774-783`), after checking that D6 is still the Task's and based at `base_sha`, else `HIGH_DEBUG_SCREEN_RECOVERY_BINDING`. A changed diff refuses `HIGH_DEBUG_SCREEN_SOURCE_MISMATCH` (`:819-820`). The scratch D6 is never read for recovery.
- #981 disposition rules apply unchanged: metadata only; no retry, rewording or hint.

### 3.6 Routes

- `debug_report`: `codex-debug` first; `claude-debug` only on `DEBUG_FALLBACK_CODES` (`high_debug.py:1563-1587`).
- `design_advice`: the PM picks with `--route` the family that differs from the planned formal Reviewer. No automatic fallback (that would start a second model). A same-family pair is recorded.

### 3.7 O6 counts

- **Ledger.** Private, metadata only, beside `high-debug-screen` (`high_debug.py:359-360`): `high-debug-consult/wp-<wp>.json` and `week-<ISO week>-<n>.json`, n = 1..10.
- **Reservation.** After every local check, just before launch, by atomic create-once link (`high_debug.py:397-415`). `RESERVED` becomes `DONE` or `FAILED` with the report digest (`:418-433`); never deleted.
- **Refusals.** WP slot exists: `HIGH_DEBUG_CONSULT_WP_USED`. No free week slot: `HIGH_DEBUG_CONSULT_WEEK_CAP`. Bad ledger: `HIGH_DEBUG_CONSULT_LEDGER_INVALID`.

### 3.7a Ledger v2 (serialized)

Status: 待實作（WP-5）. PR1 v1 (file-per-slot ledger) was DROPPED on #1943 after five rounds of race and durability findings. This subsection replaces the §3.7 **Ledger** and **Reservation** bullets and the §3.8 **Two calls at once** bullet; the refusal codes and the other §3.8 bullets stay. The findings closed below are the six the Task contract lists from the #1943 drop record and PRs #2054, #2057 and #2071; those threads were not reread for this text. Citations here are at `5f2b50ac`.

**Why not reuse today's helpers.** `_private_directory` and `_write_screen_record` create parents and chmod by path (`high_debug.py:390-395`, `:483-487`). `_create_private` fsyncs the file but not the directory (`:398-416`). `_replace_private` neither fsyncs nor opens with `O_NOFOLLOW` (`:419-434`). The ledger gets its own fd-relative code in `high_debug_consult.py`; the screen helpers stay unchanged.

**Layout.**

- Directory `<root>/high-debug-consult/`. `<root>` is `Path(task_store.default_root()).parent` (as §3.7) or a root a test injects. It holds three names only: `ledger.json` (every slot), `ledger.lock` (lock only; never written, renamed or deleted) and `ledger.json.tmp` (the one temp name).
- **One ledger file, not one file per slot.** Why:
  - A reservation takes its WP slot and its week slot in one record and one rename, so no state has one without the other (findings 4 and 6).
  - The week count is counted from records under the lock, not probed over names `n = 1..10`.
  - There is one inode to vet for links, owner and mode, and no directory listing to trust (finding 1).
  - Cost: a whole-file rewrite per operation and one lock for all WPs. At ≤ 10 reservations and ≤ 10 finishes a week and about 500 bytes a record, the file grows about 260 KB a year, so the 4 MiB read limit lasts over 15 years. A full ledger refuses `LEDGER_INVALID`; rotation is 待後續.
  - A corrupt file blocks every consult. That is the intended fail-closed outcome.
- **Scope.** The ledger belongs to one Task store, so to one host. The caps hold only if every consult runs from one host. Open for the PM: run consults only from the EC2 Task store. Until decided, the caps are per host.

**Root confinement** (finding 2).

- The raw root string is checked before any `Path` normalizes it: absolute; split on `/`, no empty component (no `//`, no trailing `/`), no `.` and no `..`; at most 4096 bytes. Else `LEDGER_INVALID`.
- **lstat chain from `/`.** Open `/` with `O_RDONLY|O_DIRECTORY`, then each component with `os.open(name, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC, dir_fd=parent)` and `fstat` it. A symlink (`ELOOP`), a non-directory, a missing component (`ENOENT`) or any other `OSError` refuses. Each ancestor must be owned by uid 0 or the effective uid, and not group- or other-writable unless sticky. Nothing above the ledger directory is ever created (no `parents=True`).
- **Ledger directory.** Opened the same way from the root fd. It must be a directory owned by the effective uid with mode exactly `0700`. A wrong mode refuses; it is never chmod-repaired. Every later call is relative to this fd (`dir_fd=`), so renaming a path component after the walk cannot redirect a read or write. A platform without `dir_fd` support for `open`, `rename`, `unlink` and `stat` refuses.
- **First use** (root present, ledger directory absent). Build `.high-debug-consult.init-<random>` (`0700`) holding `ledger.lock` (`0600`, empty) and `ledger.json` (`seq` 0, no slots). Fsync both files and the staging directory, `rename` it to `high-debug-consult`, then fsync the root. If the rename fails with `EEXIST` or `ENOTEMPTY`, another process won: remove only this call's staging entries and use the winner's directory. So an existing ledger directory always holds both files. A missing `ledger.json` or `ledger.lock` there refuses `LEDGER_INVALID` and never reads as "empty", which would reset the caps. A crash leaves at most a staging directory, which is never read.
- **Files.** `ledger.lock` and `ledger.json` are each opened with `O_NOFOLLOW|O_CLOEXEC` through the directory fd, then `fstat`ed: regular file, owned by the effective uid, mode `0600`, `st_nlink == 1` (finding 1). Reads are bounded to 4 MiB.

**Lock.**

- `os.open('ledger.lock', O_RDWR|O_NOFOLLOW|O_CLOEXEC, dir_fd=d)` (no `O_CREAT`; first use created it), the file checks above, then `fcntl.flock(fd, LOCK_EX|LOCK_NB)`, polled every 50 ms for at most 10 s. A timeout refuses the new code `HIGH_DEBUG_CONSULT_LEDGER_BUSY`, before any launch; any `OSError` other than `EWOULDBLOCK` refuses `LEDGER_INVALID`. Once locked, `os.stat('ledger.lock', dir_fd=d, follow_symlinks=False)` must give the locked fd's `(st_dev, st_ino)`, else `LEDGER_INVALID`.
- The lock is a separate stable file because `ledger.json` is replaced by rename; a lock on it would lock the old inode.
- It is taken around **every** reserve and **every** finish, including the read that decides a refusal. It is the module's only lock. While holding it the code takes no other lock, writes no screen record, creates no D6, runs no subprocess and does no network I/O. It is released by `close`, or by the kernel when the holder dies.

**Record schema** (finding 3). `ledger.json` is UTF-8 JSON of at most 4 MiB:

```json
{"schema": "llmtool.high-debug-consult-ledger.v2", "seq": 3, "slots": []}
```

- Each slot has exactly these keys: `slot_id` (16 hex, random), `wp`, `week` (`YYYY-Www`), `task_id` (32 hex), `case`, `route` (`codex` or `claude`), `bindings_sha256` (the §3.2 bindings), `state`, `reserved_at`, `finished_at`, `report_sha256`.
- Parsing: strict UTF-8; `object_pairs_hook` refuses duplicate keys; `parse_constant` refuses `NaN` and `Infinity`. The top level must satisfy `type(value) is dict` with exactly the three keys, so `null`, a list, a number or `{}` refuses. Every field check uses `type(x) is`, so `true` is not an int and `null` is not a string.
- `null` is valid only in two fields, by state:

  | `state` | `finished_at` | `report_sha256` |
  |---|---|---|
  | `RESERVED` | `null` | `null` |
  | `DONE` | UTC time ≥ `reserved_at` | 64 hex |
  | `FAILED` | UTC time ≥ `reserved_at` | 64 hex or `null` |

- Ledger-wide: `slot_id` unique; `wp` unique; at most 10 slots per `week`; `week` equals the ISO week of `reserved_at` in UTC; `reserved_at` never decreases along the list; `seq` equals the slot count plus the count of terminal slots (each write is one append or one transition).
- Any failure refuses `LEDGER_INVALID` and writes nothing.

**Write** (finding 5). Under the lock, with `d` the directory fd:

1. Check the new content against the old: the same records in the same order, plus either one new `RESERVED` record at the end or one `RESERVED` record moved to `DONE` or `FAILED`; `seq` + 1. Anything else refuses `LEDGER_INVALID`.
2. `unlink('ledger.json.tmp', dir_fd=d)`, ignoring only `FileNotFoundError`. A leftover temp is from a writer that died holding the lock.
3. `os.open('ledger.json.tmp', O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0o600, dir_fd=d)`; write every byte (a short write continues); `fstat`: regular, `st_nlink == 1`.
4. `fsync` the temp fd, then `close` it.
5. `os.rename('ledger.json.tmp', 'ledger.json', src_dir_fd=d, dst_dir_fd=d)`.
6. `fsync(d)`.
7. Release the lock. Reserve returns its `slot_id`, and finish returns success, only now.

- Every `OSError` in steps 2-6, including from `fsync` and `close`, refuses `LEDGER_INVALID`. A failed `fsync` is never retried on the same fd. The on-disk outcome is then UNKNOWN, and the next call rereads the ledger under the lock.
- EC2 Linux uses `fsync`. On darwin `fsync` does not flush the drive cache, so step 4 uses `fcntl(fd, F_FULLFSYNC)`; its use on the directory fd in step 6 is UNVERIFIED, and PR1 must show it works or use `fsync` there and record the weaker guarantee.

**Reserve.** After every local check of §3.1-3.5, just before launch: lock, read and validate, then in this fixed order:

1. `now` (UTC, read under the lock) earlier than the last `reserved_at`: `LEDGER_INVALID`. A clock moved back could reopen a past week.
2. A slot with this `wp`: `HIGH_DEBUG_CONSULT_WP_USED`, with that slot's state and report digest as detail.
3. At least 10 slots in `now`'s ISO week: `HIGH_DEBUG_CONSULT_WEEK_CAP`.
4. Else append a `RESERVED` slot, write, unlock, then launch.

The WP check runs before the week check under one lock, so the refusal code depends only on the ledger state when the lock is taken, never on timing (finding 4).

**Finish.** After the run, whatever its result, with `slot_id`, `bindings_sha256`, outcome and report digest: lock, read and validate, then:

- no slot with this `slot_id` and `bindings_sha256`: new code `HIGH_DEBUG_CONSULT_SLOT_UNKNOWN`;
- the slot is `RESERVED`: write the transition;
- the slot is terminal with the same `(state, report_sha256)`: success, no write;
- the slot is terminal with another outcome: new code `HIGH_DEBUG_CONSULT_FINISH_CONFLICT`, no write.

The CLI exits 0 only after the finish write is durable. A finish that refuses leaves the slot `RESERVED`, which still counts; the PM may repeat the same finish (finding 6).

**Invariants, with the lock-order and crash argument.**

- **≤ 1 slot per WP, ever.** The WP check and the append are in one critical section. The next reserve of that WP takes the lock after the rename and reads the new file. Crash before step 5: the old file stands and the caller got no `slot_id`, so nothing launched. Crash between steps 5 and 6: after a power loss the new slot may or may not survive, but the caller still got no `slot_id` and launched nothing; a surviving slot without a launch still counts (fail closed). Records are never deleted, so "ever" holds.
- **≤ 10 slots per ISO week (UTC).** The same critical section and crash argument cover the week check. The week comes from `now` read under the lock, and a clock moved back refuses.
- **Records are never deleted.** Write step 1 allows only an append or one transition, and `seq` must match. A missing `ledger.json` in an existing directory refuses instead of reading as empty. Nothing in the module unlinks `ledger.json` or `ledger.lock`. Not covered: a same-uid actor deleting the whole directory, as for screen records today.
- **`RESERVED` → `DONE` | `FAILED`, once.** Finishes are serialized by the lock. The first moves the slot; every later one sees a terminal state. Crash: the rename is atomic, so the slot is either still `RESERVED` (repeat the finish) or terminal. A torn record cannot exist, because `ledger.json` is never written in place.
- **Finish is idempotent for the same outcome and refuses a conflicting one.** Same `(state, report_sha256)` succeeds with no write; any other refuses `FINISH_CONFLICT`. Both are decided under the lock.
- **WP and week records agree.** One record holds both, so there is nothing to diverge.
- **Lock order.** There is one lock, never nested and never held across a launch, so no deadlock. A dead holder's lock is freed by the kernel, and its half-written `ledger.json.tmp` is never renamed.

**v1 findings closed.**

| v1 finding | Closed by |
|---|---|
| Hard-linked slot files | One `ledger.json`; `O_NOFOLLOW` opens through the directory fd; `st_nlink == 1`, owner and `0600` checked on ledger, lock and temp; the temp is a fresh `O_EXCL` inode. |
| Root not confined (symlinks, `..`, empty components, missing dirs) | Raw-string check (absolute, no empty, `.` or `..` component); fd walk from `/` with `O_NOFOLLOW`; ancestor owner and mode check; nothing above the ledger directory created; a missing ancestor refuses; staged-rename first use; all I/O relative to the directory fd. |
| JSON `null` or wrong-schema records | Exact schema; `type(x) is` checks; `null` only per the state table; duplicate keys and `NaN` refused; `seq` cross-check; any failure `LEDGER_INVALID` with no write. |
| Same-WP vs `WEEK_CAP` refusal race | One lock around read, check and write; WP check before week check; one record per reservation. |
| Ignored file or directory fsync errors | Every `fsync`, `close`, `rename` and `unlink` `OSError` refuses; the directory is fsynced; success only after step 6; no retry on a failed fd. |
| Concurrent finish leaving WP and week records inconsistent | One record holds both slots; finishes serialized by the lock; once-only transition; same outcome idempotent, a conflicting one refuses `FINISH_CONFLICT`. |

### 3.8 Idempotence and concurrency

- **Invariant.** Each WP ever has at most one advice slot and each ISO week at most 10. A slot exists before its launch and is never removed, so a crash fails closed.
- **Two calls at once.** One link-create of `wp-<wp>.json` wins; the other refuses `HIGH_DEBUG_CONSULT_WP_USED` before launch. A lost week-slot race tries the next n.
- **Repeated call.** Refuses with the slot state and report digest; never relaunches.
- **D6 changed after pinning.** Refuses `HIGH_DEBUG_CONSULT_DIFF_MISMATCH` before reservation.

## 4. Implementation plan (待實作（WP-5）)

At most 4 files per PR; every test file is new. `_X` keeps the prefix of the code before it. Per #763 A, the screen function is injected, hits are rule/line metadata, and no test holds credential-shaped strings.

1. **Ledger v2** (4 files, the most a PR may have; replaces the DROPPED PR1 v1; design in §3.7a): `tools/llmtool/llmtool/high_debug_consult.py` (ledger only: root walk, first use, lock, schema, reserve, finish), `tools/llmtool/tests/test_high_debug_consult_ledger.py` (one process), `tools/llmtool/tests/test_high_debug_consult_ledger_races.py` (many processes), `tools/llmtool/tests/test_high_debug_consult_ledger_faults.py` (injected faults). Codes: `HIGH_DEBUG_CONSULT_WP_USED`, `_WEEK_CAP`, `_LEDGER_INVALID`, `_LEDGER_BUSY`, `_SLOT_UNKNOWN`, `_FINISH_CONFLICT`. The module keeps thin seams (`_fsync`, `_rename`, `_unlink`, `_write`, `_now`, lock poll interval and deadline) that tests patch; no check is skipped or weakened for a test. No screen function runs in PR1; digests are `hashlib.sha256` of fixed labels and WP ids are plain words, so no test holds a credential- or token-shaped string. Tests:
   - **Semantics:** the first reservation passes; the same WP refuses `WP_USED` with state and digest; 10 in a week pass and the 11th refuses `WEEK_CAP`; the next ISO week passes, across a year boundary (injected `_now`: 2027-01-03 is 2026-W53, 2027-01-04 is 2027-W01); a full week with a used WP refuses `WP_USED`; a clock moved back refuses `LEDGER_INVALID`; finish moves `RESERVED` to `DONE`, and to `FAILED` with and without a digest; the same finish twice succeeds with `seq` unchanged; a conflicting finish refuses `FINISH_CONFLICT`; an unknown slot or bindings digest refuses `SLOT_UNKNOWN`.
   - **Corrupt ledger** (each refuses `LEDGER_INVALID` and leaves the file byte-identical): `null`, `[]`, `{}`, truncated JSON, invalid UTF-8, a duplicate key, `NaN`, a wrong `schema`, an extra or missing key, `true` as `seq`, `null` in a non-null field, each state/field mismatch from the table, an unknown state, a duplicate `slot_id`, two slots for one WP, 11 slots in a week, `week` not matching `reserved_at`, a decreasing `reserved_at`, a wrong `seq`, a file over 4 MiB.
   - **Symlink, hard link and root:** `ledger.json`, `ledger.lock` or the ledger directory as a symlink; `ledger.json` or `ledger.lock` with a second hard link; ledger file mode `0644` or directory mode `0755` refuses and is not chmod-ed; a missing `ledger.json` or `ledger.lock` in an existing directory; roots that are relative or contain `..`, `.`, `//` or a trailing `/`; a symlinked ancestor; a missing ancestor (nothing created); a group-writable ancestor without the sticky bit. A leftover staging directory is ignored and a leftover `ledger.json.tmp` is replaced. A wrong-owner case needs root and is not tested (no `sudo`); it is covered by review only.
   - **Races** (separate processes from the `multiprocessing` spawn context, started on a `Barrier`, every child result collected): 8 reserves of one WP give exactly 1 success, 7 `WP_USED` and 1 slot; 12 reserves of distinct WPs in an empty week give exactly 10 successes and 2 `WEEK_CAP`; with 9 slots in the week, two reserves each for WPs A and B give 1 success, and each loser gets `WP_USED` if its WP is in the ledger, else `WEEK_CAP`; 8 concurrent finishes of one slot, half `DONE` and half `FAILED`, leave one outcome, every caller with that outcome succeeds, every other refuses `FINISH_CONFLICT`, and `seq` rose by exactly 1; two first uses at once leave one directory and both callers proceed; a child holding the lock makes a reserve refuse `LEDGER_BUSY` at the injected short deadline, and succeed after release.
   - **Faults and crashes:** an injected `OSError` from the temp `fsync`, `close`, `rename`, directory `fsync` or temp `unlink`, or a short write that then fails, refuses `LEDGER_INVALID`, returns no `slot_id`, and the next call reads the old file (fault before the rename) or the new one (after). A child that `os._exit`s while holding the lock, between write and temp `fsync` and again between rename and directory `fsync`, leaves the lock free and `ledger.json` parsing as the complete old or new version; a fresh reserve then returns the code that version implies. A power loss cannot be simulated; the directory-fsync ordering is argued in §3.7a, not tested.
2. **Base-SHA entry** (4 files): `tools/llmtool/llmtool/task_submission_cli.py`, `high_debug_consult.py`, `high_debug.py`, `tools/llmtool/tests/test_high_debug_no_pr_entry.py`. Codes: `HIGH_DEBUG_CONSULT_ARGUMENT_INVALID`, `_PIN_REQUIRED`, `_PIN_INVALID`, `_CASE_INVALID`, `_REVIEW_CLAIM`. Tests, with an injected runner:
   - both kinds pass; RCA falls back, advice does not;
   - refusals: missing pin; pin not on `main`; `--no-pr` with `--pr`; bad case; runner write; `Verdict:` line; WP used; week cap;
   - a repeated call refuses; two concurrent calls launch once;
   - no `high_debug` entry, completion event or post (injected) call; repair #2 still refuses.
3. **Diff pin** (4 files): `high_debug_consult.py`, `high_debug.py` (`D6_DIFF`, `base_sha`), `tools/llmtool/tests/test_high_debug_no_pr_diff_pin.py`, `tools/llmtool/tests/test_high_debug_diff_screen_recovery.py`. Codes: `HIGH_DEBUG_CONSULT_D6_MISMATCH`, `_DIFF_MISMATCH`, `HIGH_DEBUG_DIFF_SENSITIVE`.
   - Pin: valid pin passes; wrong or foreign D6, base not the pin, or changed diff refuses.
   - Recovery: a hit writes a record bound to the executor D6 and `base_sha`, and blocks the next call. **Overflow regression:** more hits than a record keeps; recovery naming the executor D6 rebuilds the diff, confirms every batch and clears. Negative: scratch D6 or moved base refuses `HIGH_DEBUG_SCREEN_RECOVERY_BINDING`; changed diff refuses `HIGH_DEBUG_SCREEN_SOURCE_MISMATCH`; a record without `base_sha` loads.
4. **Docs** (3 files, no tests): `skills/role-cards/debug.md`, `skills/ai-team-delivery/high-debug-report.md` (advice format), `CLAUDE.md` (status marks; the `:85` drift).

## 5. Open questions for the PM

- **Q1. "Writes nothing".** Scratch D6, O6 ledger and screen records are private tool state, not repo, Task or GitHub writes. Default: allow them; caps and screen need them.
- **Q2. Report keeping.** Default: stdout only, digest in the slot.
- **Q3. RCA cap.** Default: one no-PR `debug_report` per Task and pin, 2 per Task, no week cap.
- **Q4. Codex advice.** O6 names only `claude-debug`. Default: both caps apply to all advice unless the Owner relaxes this.
- **Q5. Week.** Default: ISO week in UTC, which keeps slot creation atomic; a rolling window is 待後續.
- **Q6. Diff size.** Default: the 8192-byte evidence limit (`high_debug.py:28`); larger refuses `HIGH_DEBUG_EVIDENCE_INVALID`.
- **Q7. Overflow, then the diff changes.** Recovery refuses `HIGH_DEBUG_SCREEN_SOURCE_MISMATCH`; disposition refuses `HIGH_DEBUG_SCREEN_HITS_OMITTED` (`high_debug.py:652-653`), as for `DEVELOPER_OUTPUT` today. Default: stay fail-closed, no bypass; the PM follows the #981 procedure.
