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

### 3.8 Idempotence and concurrency

- **Invariant.** Each WP ever has at most one advice slot and each ISO week at most 10. A slot exists before its launch and is never removed, so a crash fails closed.
- **Two calls at once.** One link-create of `wp-<wp>.json` wins; the other refuses `HIGH_DEBUG_CONSULT_WP_USED` before launch. A lost week-slot race tries the next n.
- **Repeated call.** Refuses with the slot state and report digest; never relaunches.
- **D6 changed after pinning.** Refuses `HIGH_DEBUG_CONSULT_DIFF_MISMATCH` before reservation.

## 4. Implementation plan (待實作（WP-5）)

At most 4 files per PR; every test file is new. `_X` keeps the prefix of the code before it. Per #763 A, the screen function is injected, hits are rule/line metadata, and no test holds credential-shaped strings.

1. **Ledger** (2 files): `tools/llmtool/llmtool/high_debug_consult.py`, `tools/llmtool/tests/test_high_debug_consult_ledger.py`. Codes: `HIGH_DEBUG_CONSULT_WP_USED`, `_WEEK_CAP`, `_LEDGER_INVALID`. Tests: first slot passes; same WP again refuses; 11th in a week refuses; next week passes; a two-process race leaves one slot; corrupt or symlinked ledger refuses.
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
