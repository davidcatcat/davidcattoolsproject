# Codex 報告 review — PR #49 coordinator review、Functional QA，以及 PR #45 coordinator requirement（2026-10-09）

## 範圍與方法

- **repo 狀態：**
  - `main @ ceae425` 沒有改變；
  - 在我上一輪 re-review（`eebf61a`）之後，**沒有任何新的 commit**；
  - Codex 新增的報告都以 **PR comment** 的形式發布：
    1. PR #49「Coordinator repo review — PR #49 remediation assessment」（2026-10-08T16:17Z）；
    2. PR #49「獨立 Functional QA Acceptance：PASS」（2026-10-08T16:45Z，review HEAD `eebf61a`）；
    3. PR #45「Coordinator requirement — eliminate manual Codex/Claude prompt relay」（2026-10-08T16:42Z）。
- **方式：**
  - 閱讀三份報告的全文；
  - **抽查其中可以重現的主張**：SHA、CI、我自己報告中的錯誤、以及 QA49-N2 的 reproducer；
  - 對照我上一輪的 Mac acceptance 結果。
- **沒有做的事：** 沒有修改 production 或 test；沒有 merge；沒有執行真正的 `git add`。

## 1. PR #49 coordinator review：正確

| 主張 | 我的查核 |
|---|---|
| committed 的 `mac-native.json` 有 14 個情境，全部 PASS 而且 skipped=0；兩種 SIGKILL 各 20 次 | 和我上一輪的查核一致 |
| 9 個 tested 檔案的 SHA 和 PR HEAD 完全一致 | 和我上一輪的結果一致（9／9） |
| `_last_act` 改為 `getpid`／`getpgrp` 加上 `killpg(0)` | 正確 |
| S49-1、S49-2、P49-M2 → CLOSED；S49-3、P49-L1、L2、L3 → OPEN 或 deferred | 正確，**沒有把未修正的項目標成已修正** |
| 「`eebf61a` 的 CI run 37807172850 還在執行，不能當成 PASS 引用」 | 當時的處理正確。**我現在查詢的結果是：headSha `eebf61a…`，conclusion = success** |
| 「Functional QA 仍然 pending，不能 merge」 | 依照流程是正確的。之後 QA 已經發布（見第 2 節） |

這份 review 的優點是**沒有把報告類的 commit 當成 tested code**，也明確區分了 security re-review 和 functional QA 這兩個 gate。

## 2. PR #49 獨立 Functional QA：PASS，品質高

**我認為可以接受的理由：**

- **測試計畫在執行之前就訂好：** 7 個 REQUIRED 的列，而且「沒有在失敗後放寬」。
- **在真正的 Mac 上、sandbox 以外獨立執行：**
  - containment 的 81／81 test（3.13 和 3.9）；
  - **native runner 的 14／14**（SIGKILL 各 20 次，最大延遲約 0.012 秒）。這和我上一輪的 0.0122／0.0124 秒獨立吻合；
  - 兩次完整的 suite，各 1,247 個 test；
  - extra_native 的自然完成情境；
  - **runner 的 negative challenge**：空的 check、SkipTest、AssertionError、只有 19 次 repetition、錯誤的 contract，**都會讓 runner 回傳 exit 1**。這直接測試了 runner 會不會產生「假的 PASS」，**是我之前沒有做的有價值的補充**。
- **誠實地保留失敗紀錄：** 第一次執行 3.9 的完整 suite 是 **FAIL**（1,210 個 test，failures=5、errors=2）。原因是 discovery 指令沒有設定 package path，導致 `ModuleNotFoundError: llmtool`。QA 保留了失敗的 log，用明確的 package path 重新執行 → PASS，並另外跑了受影響的 42 個 import regression test。**沒有把失敗改標成 PASS。**
- **明確寫出沒有測試的範圍：** 真實的 PID 重複使用、自然發生的 PPID race、uv 的 ctypes fd、惡意的 administrator 或 foreign reaper 等等。

**我抽查了三個 finding：**

| QA finding | 我的查核 | 判斷 |
|---|---|---|
| **QA49-N1**（LOW）：「leader 自然結束」沒有成為永久的 native regression test（目前的 case 是在 zombie leader 加上活著的 descendant 之後，用 SIGTERM 觸發 cleanup） | 屬實。QA 的 extra_native 臨時覆蓋了自然完成的情況，但沒有加入 repo | 同意，LOW。建議在 S49-3 的 follow-up 中一起加入 test |
| **QA49-N2 = S49-3**（LOW）：`file_hash(directory)` 拒絕之後，raw fd 仍然存活 | **重現成功：** 在 scratchpad 中對一個目錄呼叫 `wire.file_hash()` 20 次，每次都是 `IsADirectoryError`，**fd 增加了 20 個**。原因是 `os.open` 成功之後，`os.fdopen(fd)` 在遇到目錄時才 raise，這時 fd 還沒有被 context manager 接管（`_git_containment_protocol.py` 約第 334–335 行） | 同意，LOW。這個 harness 是封閉的，只會檢查固定的 artifact path，所以實際影響很小。**修正很簡單：** 在 `fdopen` 之前先 `os.fstat(fd)` 確認是一般檔案，失敗時 `os.close(fd)`；或者用 `try/except` 包住 `fdopen`。必須在 product integration 之前修正 |
| **QA49-N3**（LOW）：我的 security re-review 中的 8 碼 SHA 寫錯了 | **屬實，是我的錯誤。** 我在報告的第 11 行和第 111 行寫成 `68744107`，實際的 SHA 是 **`68744100c2890d05ca0e565f4d246ae1c773e951`** | **已經在本次 commit 中更正**（兩處都改了，並註明原本的誤植） |

**觀察（NOTE，不影響判定）：**

- **N-a：** QA 的 raw evidence 放在 `/private/tmp/pr49-independent-qa-evidence/`，**沒有 commit 到 repo**，comment 中只有 digest。`/private/tmp` 會被系統清除，將來就無法驗證這些 digest。建議把 matrix、native.json 和各個 `*.json` 的摘要以 evidence 目錄的形式 commit（作法可以參考 `reports/pr49_security_remediation_20261008/`），或者附加到 PR 上。
- **N-b：** 這三份 Codex 報告和我的 review 都是用**同一個 GitHub 帳號**（davidcatcat 或 commit author david）發布的。GitHub 上**沒有任何正式提交的 review approval**（coordinator 也指出了這一點）。所謂的「獨立」目前是依靠流程上的紀錄（不同的 agent、不同的 session、事先訂好的 matrix），而不是平台層級的身分分離。這對個人 repo 是可以接受的，但如果將來要把這些 gate 自動化（PR #45），**需要更強的 provenance**（見第 3 節）。

## 3. PR #45 coordinator requirement（消除手動轉傳 prompt）：方向正確，但有三個風險需要寫進設計

這份 comment 是一份**需求和追蹤紀錄**，它本身也明確寫出「不代表這個模式已經存在，也不代表 PR #45 可以 merge」。內容包括：

- 新增 `final_acceptance_review` 這種唯讀的 task type；
- 自動擷取 security re-review 和 QA，並且**一般的 Claude code review 不能被算成這兩種 sign-off**；
- review 模式下禁止修改程式碼、push、merge 和真正的 Git mutation；
- hash 一致時可以重用 Mac evidence，但不能偽造；
- 只有一個命令、在 blocker 時停止、不會自動 merge；
- **PR #45 本身必須先經過獨立的 security review。**

這些要求是合理的，也和我前兩輪的建議一致。我補充三個應該寫進設計的風險：

1. **信任根是循環的。** 第 4 點寫的是「如果 9 個 hash 和目前的 HEAD 一致，就不需要重跑 Mac」。但 committed evidence 本身就是同一個 runner 產生的。如果 runner（PR #45）有 bug 或被篡改，hash 的比對只能證明「evidence 和檔案一致」，**不能證明 evidence 是真的執行出來的**。
   - **建議：** 每一個準備 merge 的 slice，至少要有一次**由 runner 以外的獨立執行**（例如這一輪的 QA 和我的 rerun），或者讓 evidence 帶有可以驗證的執行來源（例如 runner 版本的 hash、host 的資訊、在 sandbox 以外執行的證明）。
2. **reviewer 的輸入必須和作者的敘述隔離。** 如果自動化的 security review 和 QA 是讀取作者（Codex）寫的摘要或 handoff 來判斷，獨立性就會被稀釋。
   - **建議：** review mode 只提供 exact SHA、diff、原始 evidence 和固定的 criteria。作者的 validation report **只能作為待驗證的主張**，不能當成輸入的事實。QA 這一輪的做法（先訂 matrix、自己讀 source、自己執行）應該被寫成模式本身的硬性要求。
3. **自動發布 comment 的權限和隱私。** runner 會發布 review 報告或 comment，所以必須限制 token 的權限（最小 scope、不能 merge 或 approve），而且在發布之前必須經過 secret 和路徑的掃描。這些都屬於 PR #45 security review 的範圍。

## PR #49 目前的 gate 狀態

| Gate | 狀態 |
|---|---|
| 實作和作者的檢查 | 完成 |
| 獨立 security re-review（我，`6874410`） | **PASS** |
| 獨立 functional QA（Codex，`eebf61a`） | **PASS**（3 個 LOW 的 finding，都不阻擋） |
| Native Mac acceptance | **PASS**（committed evidence、我的 rerun、QA 的 rerun，三方一致） |
| exact-head CI | `eebf61a` **success**。本次 commit 只修改 report，會產生新的 head，需要等新一輪 CI 變綠 |
| coordinator acceptance 和 merge 的決定 | **待你決定** |

## 建議

1. **PR #49：** 所有技術 gate 都已經 PASS。等這次報告 commit 的 CI 變綠之後，由你決定是否轉為 ready 並 **squash merge**。建議在 squash message 中記錄 tested source head `68744100c…` 和 native evidence 的 digest。
2. **merge 之後的小型 follow-up PR（在 A11b.2.2.2 之前）：**
   - 修正 S49-3／QA49-N2 的 fd 洩漏，並加上 regression test；
   - 加入 QA49-N1 的「leader 自然結束」native regression test；
   - 如果可以，把 QA 的 evidence 摘要 commit 到 repo（N-a）。
3. **A11b.2.2.2：** 仍然是 **NO**。必須先寫 design spec（處理 P49-L1 的 fault hook 分離、完整的 post-state 分類，以及 product host 的單一 thread 前提），經過審查之後才能實作。
4. **PR #45：** 依照 coordinator requirement 的第 7 點，先做獨立的 security review。並且把上方的三個風險（循環的信任根、reviewer 輸入的隔離、發布的權限和隱私）納入設計和 review 的範圍。

## 判定

```text
CODEX REPORTS REVIEW (2026-10-09):

PR #49 coordinator repo review:
ACCURATE — claims re-checked; correctly withheld PASS for in-progress CI (now: eebf61a success)

PR #49 independent Functional QA:
ACCEPTED — pre-registered 7-row matrix, independent native 14/14 and 20+20 SIGKILL (max ~0.012 s, matches my rerun),
  runner false-PASS negative challenges, initial 3.9 FAIL retained and explained, untested areas stated
  QA49-N1 LOW — agree (natural leader-exit native regression missing)
  QA49-N2/S49-3 LOW — reproduced (20 calls → +20 FDs); fix before product integration
  QA49-N3 LOW — confirmed my own SHA typo; CORRECTED in this commit (68744107 → 68744100c2890d05ca0e565f4d246ae1c773e951)
  NOTE: raw QA evidence only in /private/tmp (not durable); all sign-offs share one GitHub identity

PR #45 coordinator requirement:
DIRECTION CORRECT — add to design/review scope: circular trust root of runner-produced evidence, reviewer-input
  isolation from author narratives, comment-publishing token scope/privacy

PR #49 gates:
security PASS / functional QA PASS / native PASS / CI PASS at eebf61a (new report-only head needs CI) —
  merge decision: USER

A11b.2.2.2:
NO — design spec + review first
```

- 這次 commit 只修改 `reports/`：新增本報告，並更正我上一份報告中的 SHA 誤植。沒有修改 production 或 test，沒有 merge。
- QA49-N2 的重現只在 scratchpad 中進行，留下了一個空的暫存目錄，沒有刪除，也沒有加入 Git。
