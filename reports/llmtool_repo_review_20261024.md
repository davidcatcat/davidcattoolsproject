# Repo 檢查、除錯與改善建議報告

- 日期：2026-10-24（Asia/Taipei），資料查到 13:51 為止
- 檢查者：Claude，在 Owner 直接開的 Claude TUI session 裡進行
- 依據：main `95f7790`、GitHub 開著的 PR 和 Issue、main CI、本機 D6 登記、Codex 和 Claude 的本機狀態紀錄、程序清單
- 本輪做了的修改：只有監控工具，開成 PR [#312](https://github.com/davidcatcat/davidcattoolsproject/pull/312)，還在等審查。其他項目只提建議，沒有改規則、派工設定、D6、PR 或 Issue。

## 1. 結論

- **main 是健康的**：最近 6 次 main CI 全部成功；#308（監控讀真實狀態）和 #309（PM 平行派工調查）已合併。
- **最大的問題還是 PM 不會自己接續**（見 [#309 報告](llmtool_pm_parallel_dispatch_investigation_20261024.md)）。今天又看到同樣情形：
  - #304 UI Developer 從 04:19 閒置到現在；Owner 12:24 指示關掉它，13:51 還開著。
  - #301 R4 Developer 從 13:25 起閒置。
- **PM 用的監控是舊版**：PM 自己開的監控程式是 02:41 啟動的，比今天所有修正都舊。PM 依規則要看監控，所以它看到的資訊有已知錯誤。
- **PM 呼叫審查的方式不穩**：今天有好幾次審查呼叫沒有結果、腳本出錯，或結果沒被讀取，等於白花 Reviewer 額度（見 §3.3）。
- **雜項累積**：9 個 1–3 天沒動的草稿 PR、121 個 D6 worktree（3.1 GB）、`/tmp` 裡 193 個 llmtool 資料夾（1.0 GB）。不急，但會拖慢 PM 和人工判讀。

## 2. 狀態快照

| 項目 | 狀態 |
|---|---|
| main | `95f7790`，最近 6 次 CI 全部 success |
| 開著的 PR | 11 個：#312（本輪，監控）、#310（R2 草稿，進行中），其他 9 個是 10/21–10/23 的草稿 |
| 開著的 Issue | 26 個，最舊從 10/08 起 |
| D6 登記 | 121 個 worktree，3.1 GB。狀態：ACTIVE 35、RETAIN_BLOCKED 32、INTEGRATED 23、RETIRE_READY 13、REPAIR 6、REVIEW_PENDING 5、REMOVED 7 |
| `/private/tmp/llmtool-*` | 193 個，合計 1.0 GB，其中 102 個超過 2 天；最大的 515 MB |
| 派工開關（main） | codex-developer、claude-reviewer、company-gw 是 ALLOWED；claude-developer 是 OWNER_ASSIGNMENT_ONLY；opencode、agy 是 PAUSED |
| 互動 Developer | #304 UI `2fa3ef77` 從 04:19 idle；#301 R4 `8cbb958d` 從 13:25 idle |
| 監控程序 | PM 的那個是 02:41 啟動（舊版）；Owner 終端機有一個從 10/23 起暫停在背景 |

## 3. 發現的問題（依影響排序）

### 3.1 PM 用的監控是舊版（P1，已有修正在 #312）

- **證據**：PID 78838 的監控 02:41 由 Codex app-server 啟動，到 13:51 還在跑。今天合併的 #307、#308 它都沒載入。舊版有幾個已知錯誤：Codex 開的互動 Claude 會被重複算成「單次呼叫」、權限等待被誤判成「待命」、看不出等的是誰。
- **影響**：PM 照規則用監控確認 Worker 狀態，拿到的卻是有錯的資訊。
- **處理**：
  - #312 讓監控標題列顯示「監控版本」。程式更新後，畫面會提醒「請按 q 後重開」。
  - 已經在跑的舊程序看不到這個提醒，因為提醒功能是新加的，**需要 PM 手動重開一次**。

### 3.2 互動 Developer 閒置無人處理（P1，根因見 #309）

- **證據**：
  - Claude CLI 的登記檔顯示 `2fa3ef77` 從 04:19:42 起 idle。Owner 12:24 指示用 `/exit` 正常結束，13:51 程序還在。
  - `8cbb958d` 從 13:25:40 起 idle。
- **處理**：照 #309 的方向（Developer 非互動執行，加上 Core 收到事件就叫醒 PM）解決。在那之前，PM 每一輪開始時要先查一次自己開的互動 session。

### 3.3 PM 的審查呼叫不穩，結果常沒被讀（P1）

- **證據**：從 PM 主對話紀錄比對 13:2x–13:4x 的審查呼叫：
  - #309：13:31 同時開了兩次審查，只讀了其中一次的結果，另一次結束了沒人看。
  - #311：一次是「PM 的呼叫腳本出錯，claude 沒有回覆」，一次是「回覆的 PR／版本跟呼叫時不同」，另一次結果沒被讀。#311 後來已合併，所以最後一項可能是監控判斷太嚴；這點還沒查證。
  - #310：一次「結束但沒有判定（可能呼叫失敗）」。
- **影響**：每次失敗或沒讀的呼叫都是白花的 Reviewer 額度，而且會讓 PM 重複開新的呼叫。
- **建議**：PM 不要每次手寫審查腳本，改用同一支固定的呼叫方式：輸出寫到固定檔案，用 exit code 回報成功或失敗，一次只對同一個 head 開一個呼叫。Roadmap 的 R1 已經有合併但還是 opt-in 的 Reviewer adapter（#285），可以優先拿它當這支固定呼叫。

### 3.4 company gw 一直失敗，但派工開關還是 ALLOWED（P2，要 Owner 決定）

- **證據**：監控顯示近 7 天 11 次全部失敗（輸出被拒：diff 或回應格式錯誤），Issue [#290](https://github.com/davidcatcat/davidcattoolsproject/issues/290) 也有記錄；`worker-dispatch.json` 裡 `company-gw` 仍是 `ALLOWED`。
- **建議**：#290 修好前，把 `company-gw` 改成 `OWNER_ASSIGNMENT_ONLY` 或 `PAUSED`，避免 PM 把低風險任務派過去後只拿回失敗。這是 Owner 的開關，本報告不改。

### 3.5 D6 的 ACTIVE 狀態和實際不符（P2）

- **證據**：監控的 D6 分頁顯示，近 24 小時有更新的 ACTIVE 任務，大多數「沒有 session 在跑」，例如 `a11c-r4-appserver-adapter-offline-20261024`、`issue267-env3-main-final-v2-wt1/2/3`。
- **建議**：PM 檢查一次 ACTIVE 但沒有 session 的任務，依實際情況改成 REVIEW_PENDING、RETAIN_BLOCKED 或 RETIRE_READY。另外，INTEGRATED 23 個加 RETIRE_READY 13 個，可以照 worktree lifecycle 的關卡分批收尾（這會釋放 3.1 GB 裡的一部分），但收尾一定要照 D6 規則，不能直接刪。

### 3.6 草稿 PR 和舊 Issue 堆積（P3）

- **草稿 PR**：#261、#265、#269、#272、#281、#286、#287 都是 10/21–10/22 的文件或報告。其中 #272 在 Roadmap 已記為被取代；#293（CI fail-fast）和 #300（R4 PoC 證據）可能還有用。
- **Issue**：#53、#74、#80、#81 是 10/08–10/09 的 ai-loop 項目，可能已被 A11c 取代。
- **建議**：PM 一次分類完：要關的關掉並寫明被哪個取代，要留的標上下一步。

### 3.7 `/tmp` 證據資料夾沒有保留規則（P3）

- **證據**：`/private/tmp/llmtool-*` 有 193 個、1.0 GB，102 個超過 2 天；最大的 515 MB，裡面有一個 114 MB 的 Claude CLI 安裝包。
- **建議**：訂一條保留規則，例如任務合併或收尾後 7 天，或證據已上傳 GitHub 後才清。macOS 每天清 `/tmp` 時也會改到資料夾時間，這曾經讓監控誤判舊任務（#307 已修）。本報告沒有刪任何檔案。

## 4. 本輪修正（PR #312，等審查）

| 問題 | 修正 |
|---|---|
| 看不出監控跑的是哪個版本 | 標題列顯示「監控版本 <commit> <時間>」，有沒提交的修改時顯示「本機修改」 |
| 程式更新後舊畫面不知道 | 檔案改變時，畫面和 log 提醒「請按 q 後重開」 |
| PM 沒讀結果的審查呼叫一直顯示「執行中」 | 比對實際程序：已經沒有程序的呼叫標成「程序已結束，PM 沒讀結果」；15 秒內的新呼叫不判斷 |
| 用 `gh pr diff N` 抓 diff 的審查呼叫顯示「呼叫裡沒寫 PR 和版本」，或錯標成提示裡提到的舊 PR | 改成優先採用呼叫實際抓取的 PR |

驗證：在 Owner 的 Mac 上跑 111 個監控測試全部通過；實際執行時，標題列有版本，審查列也正確顯示「審 PR #309 @0e3611b：PASS」。

## 5. 優化建議

1. **監控拆檔（P3）**：`scripts/llmtool_monitor.py` 約 3,300 行，全部在一個檔案裡。#308 被審查退回兩次，有一部分原因是 diff 大、範圍廣。建議依功能拆成程序、辦公室、任務、GitHub 幾個模組，之後每次審查只看相關模組。效能本身沒問題：讀程序 0.08 秒，辦公室畫面第一次 0.22 秒、之後 0.05 秒，D6 0.001 秒。
2. **監控已知限制（P3）**：
   - Codex 正在跑哪個指令，還是只能讀 session 紀錄推測。
   - 「回覆的 PR／版本跟呼叫時不同」的判斷可能太嚴（#311 的例子還沒查證）。
   - GitHub 動態只顯示時間、沒有日期。
3. **CI（P3）**：PR CI 每次跑 Python 3.9 和 3.14 兩組，要 5–8 分鐘。#293（一組失敗就停）可以優先評估合併。
4. **審查成本（P2）**：§3.3 的固定審查呼叫，搭配「同一 head 只審一次」的去重，是省 Reviewer 額度最直接的方法。

## 6. 本報告沒有做的事

- 沒有改 `worker-dispatch.json`、`pm-continuation.md`、`AGENTS.md`、Roadmap，或任何提示。
- 沒有關閉、合併或修改任何 PR 和 Issue（#312 除外，那是本輪的監控修正）。
- 沒有對任何 session 送指令，也沒有結束任何程序。
- 沒有刪 D6 worktree 或 `/tmp` 裡的檔案。
- 沒有跑整個 repo 的完整測試，只跑了監控的 111 個測試；整個 repo 的狀態以 main CI 為準。
