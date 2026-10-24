# 清理清單與文件 token 成本提案（只查不刪）

- 日期：2026-10-24（Asia/Taipei），約 17:30
- 作者：Claude（Owner 直接開的 Claude Code session `d4fdda6e`）
- 依據：main `6317b11`；D6 登記檔 `.git/llmtool-worktrees/*.json`；GitHub PR/Issue 狀態；本機 `/private/tmp`
- **本報告沒有刪除、關閉、移除或改動任何東西。** 每一項只寫建議，由 PM 照 D6 lifecycle 與 safe-git 處置；牽涉 Owner 權限的另外標出。

## 1. 結論

- **D6 登記檔有 125 筆，worktree 共約 3.2 GB。** 其中有 **13 筆**標成「進行中」或「等審查」，但它們的 PR 早已合併或關閉，登記檔狀態過期。另有 **24 筆 ACTIVE 從來沒有 PR**，大多是 #267 的量測或驗收用 worktree。
- **RETIRE_READY 有 13 筆（0.45 GB）**，已經可以照 lifecycle 退場。**RETAIN_BLOCKED 有 32 筆（0.62 GB）** 還沒完成分類。
- **草稿 PR 有 9 個停了 1–3 天**，其中 #300 和 #286 已經有 Claude 審查 PASS，但一直沒合併。
- **舊 Issue 有 4 個 ai-loop 的 P0/P1（10/08–10/09）還開著。** Roadmap A11 已經寫明「llmtool 取代暫時的 ai-loop」，這些 Issue 應該由 PM 判斷是否已被取代。
- **`/tmp` 約 2.4 GB，而且沒有保留規則。** llmtool 開頭的有 205 個、1.0 GB，其中 114 個超過 3 天。
- **文件成本。** `tools/llmtool` 的 README、WORKFLOW、ROADMAP 合計 **481 KB，約 12 萬 token**。三份中最大的段落多半是已完成里程碑或已取代的決策。建議拆成「現行」和「歷史」兩部分，詳見 §6。

## 2. D6 登記檔狀態

| 狀態 | 筆數 | 目錄仍在 | 大小 | 最後驗證（最舊～最新） |
|---|---:|---:|---:|---|
| ACTIVE | 36 | 36 | 1.14 GB | 10-19～10-24 |
| RETAIN_BLOCKED | 32 | 32 | 0.62 GB | 10-19～10-23 |
| INTEGRATED | 23 | 23 | 0.55 GB | 10-20～10-22 |
| RETIRE_READY | 13 | 13 | 0.45 GB | 10-19～10-23 |
| REVIEW_PENDING | 7 | 7 | 0.25 GB | 10-22～10-24 |
| REPAIR | 6 | 6 | 0.21 GB | 10-22～10-23 |
| REMOVED | 7 | 0 | 0 | 10-20～10-23 |
| NATIVE_HELPER_REPAIRED_READY | 1 | 0 | 0 | — |

把每筆的分支對到 GitHub PR 狀態後得到：

| 登記狀態 | PR 已合併 | PR 已關閉 | PR 開著 | 沒有 PR |
|---|---:|---:|---:|---:|
| ACTIVE | 6 | 4 | 2 | 24 |
| REVIEW_PENDING | 3 | 0 | 2 | 2 |
| REPAIR | 0 | 0 | 0 | 6 |
| RETIRE_READY | 11 | 0 | 0 | 2 |
| RETAIN_BLOCKED | 13 | 1 | 0 | 18 |
| INTEGRATED | 23 | 0 | 0 | 0 |

### 2.1 狀態過期：PR 已結束，登記卻還是進行中（13 筆）

| 任務 | 登記狀態 | 最後驗證 | PR | 建議 |
|---|---|---|---|---|
| `a11c-taskstore-lock-20261024` | ACTIVE | 10-24 | #316 MERGED | 改成 INTEGRATED，照 lifecycle 釋放 |
| `a11c-worker-nonreaping-20261019` | ACTIVE | 10-19 | #211 MERGED | 改成 INTEGRATED 或 RETIRE_READY |
| `a11c-wp2-dispatch-20261021` | ACTIVE | 10-21 | #257 MERGED | 同上 |
| `a35-tui-fixed-bottom-layout-20261022` | ACTIVE | 10-21 | #262 MERGED | 同上 |
| `llmtool-portability-report-20261020` | ACTIVE | 10-20 | #237 MERGED | 同上 |
| `owner-d8-opencode-autonomy-20261020` | ACTIVE | 10-20 | #231 MERGED | 同上 |
| `issue225-office-roster-20261023` | REVIEW_PENDING | 10-23 | #288 MERGED | 同上 |
| `issue267-env2-cache-integration-20261023` | REVIEW_PENDING | 10-22 | #284 MERGED | 同上 |
| `issue289-runtime-dispatch-gate-r2-20261023` | REVIEW_PENDING | 10-23 | #291 MERGED | 同上 |
| `claude-repo-review-20261021` | ACTIVE | 10-20 | #244 CLOSED | 確認不需要後改成 RETIRE_READY |
| `claude-session-report-20261020` | ACTIVE | 10-20 | #232 CLOSED | 同上 |
| `llmtool-d4-smoke-analysis-20261021` | ACTIVE | 10-20 | #238 CLOSED | 同上 |
| `opencode-developer-session-20261021` | ACTIVE | 10-20 | #241 CLOSED | 同上 |

影響：監控的 D6 分頁、PM 的佇列檢查（pm-continuation「Queue and IDLE」）都會把這些當成還在進行的工作。

### 2.2 ACTIVE 但從來沒有 PR（24 筆）

#267 的量測或驗收 worktree 佔 16 筆。#306 已經合併並記錄了 ENV-3 的有限驗收狀態，所以這批多半只剩稽核用途。

| 任務 | 最後驗證 |
|---|---|
| `a11c-companygw-opencode-permission-test-20261022` | 10-21 |
| `a11c-opencode-direct-model-route-20261022` | 10-21 |
| `issue193-owned-group-cleanup-20261021` | 10-21 |
| `issue267-env0-measure-20261023` | 10-22 |
| `issue267-env3-review-timeout-20261023` | 10-22 |
| `issue267-env3-wt1/2/3-20261023`（3 筆） | 10-22 |
| `issue289-runtime-dispatch-gate-20261023` | 10-22 |
| `a11c-owner-turn-runtime-20261023` | 10-23 |
| `a11c-r4-appserver-adapter-offline-20261024` | 10-23 |
| `a11c-r4-appserver-claude-20261024`（#301 的 Claude Developer，閒置） | 10-23 |
| `a11c-ui-tui-p0-20261024`（#304 的 UI Developer，閒置） | 10-23 |
| `issue267-env2-final-1/2/3-20261023`（3 筆） | 10-23 |
| `issue267-env2-mac-final-run1/2/3-20261023`（3 筆） | 10-23 |
| `issue267-env3-main-final-v2-wt1/2/3-20261024`（3 筆） | 10-23 |
| `issue267-env3-timeout-main-20261024` | 10-23 |
| `pr283-doc-refresh-20261023` | 10-23 |

REPAIR 的 6 筆和沒有 PR 的 REVIEW_PENDING 的 2 筆也沒有 PR：

- REPAIR：`a11c-owner-turn-continuation-20261023`、`issue267-env2-mac-1..4-20261023`、`issue289-live-dispatch-acceptance-20261023`
- REVIEW_PENDING：`a11c-r2-stable-errors-codex-20261024`、`issue267-env2-flow-integration-20261023`

**建議（PM）：**

1. 對 #267 那 16 筆加上 REPAIR 的 4 筆 `issue267-env2-mac-*`，每筆決定 RETAIN（附稽核理由）或 RETIRE_READY，一次處理完。
2. `a11c-r4-appserver-claude` 和 `a11c-ui-tui-p0` 是 #301、#304 的閒置 Developer，跟著 PM 對那兩個 Issue 的決定處置。
3. 其餘的逐筆確認有沒有還在用的 session，沒有就改成 RETIRE_READY。

### 2.3 可以直接退場或還沒分類

- **RETIRE_READY 13 筆（0.45 GB）**：已經標記可以退場，請照 lifecycle 走一般的 `git worktree remove`。
- **RETAIN_BLOCKED 32 筆（0.62 GB）**：大多卡在「ignored 的 `.venv` 是否保留」還沒分類。建議訂一條規則：PR 已合併，而且 `.venv` 可以從 `worktree-env.lock` 重建時，可以丟棄。這一條需要 PM 和 Coordinator 決定。

## 3. 草稿 PR（停了 1–3 天）

| PR | 內容 | 最後狀態 | 建議 |
|---|---|---|---|
| #300 | App-server offline PoC 測試與報告 | Claude 審查 PASS（test-only） | 判斷 #314 是否已取代它。沒被取代就轉 ready 並合併，否則關閉 |
| #293 | CI 失敗時快速中止 | PM 有成本審查，屬於 #99 和 #271 | 和 #99 一起決定；若採用，要先處理 exact-head CI 關卡 |
| #287 | A11c 核心工作狀態報告 | 沒有審查 | 內容可能已經過時；改寫成最新狀態或關閉 |
| #286 | Kilo CLI smoke 報告 | Claude 審查 PASS（docs） | 轉 ready 並合併（#292 的證據） |
| #281 | Claude session 審查報告 | 沒有審查 | 可能和 #309 的調查報告重疊；PM 比對後決定合併或關閉 |
| #272 | Roadmap 測試分層與省 token 流程 | CI PASS | 和 §6 一起決定；它會改 ROADMAP，要先 rebase |
| #269 | 補回歷史架構圖 | 沒有審查 | 做文件審查後合併，或關閉 |
| #265 | 原生桌面 GUI 路線（會議紀錄） | 沒有審查 | 等 Owner 確認產品方向後再處理 |
| #261 | ChatGPT 與 GitHubSync 交接決策、A12 路線 | 改了 AGENTS.md 和 ROADMAP | 牽涉角色規則，需要 Coordinator 和 Owner 決定；先 rebase |

## 4. 開了很久的 Issue

- **ai-loop（10/08–10/09）**：#53（P0）、#74、#80、#81。Roadmap A11 已經寫明「llmtool 取代暫時的 ai-loop」。建議 PM 逐一確認：已被 llmtool 取代的，就附上取代它的 PR 後關閉；仍然有效的，就重新排優先序。
- **10/10–10/18 的設計與討論 Issue**：#70、#97、#118、#92、#148、#135、#66、#161（Owner 暫緩）、#137、#99、#192、#191、#190、#172、#63。
  - 建議 PM 做一次分類，分成仍有效、已被取代、等 Owner 三種，並在每則留一行理由。
  - #161 是 Owner 暫緩的，不要動。

## 5. `/private/tmp`（沒有保留規則）

| 類別 | 數量 | 大小 | 超過 3 天 |
|---|---:|---:|---:|
| `llmtool-*`（llmtool 測試與證據） | 205 | 1,011 MB | 114 |
| `a11c-*` | 81 | 398 MB | 3 |
| `claude-501`（Claude Code 的 session 暫存，包含本 session） | 1 | 978 MB | — |
| `codex-*` | 11 | 2 MB | 10 |
| `opencode-*` | 7 | 0 MB | 6 |
| 其他 | 201 | 2 MB | 66 |

最大的單一項目：

- `llmtool-review-handoff-enter-e5m2rdct`：515 MB（10-20）
- `a11c-claude-cli-2.1.303`：397 MB（10-24）
- `llmtool-r1-cli-compat-20261024`：357 MB（10-24）

**建議：**

1. 由 PM 和 Coordinator 訂一條保留規則，寫進 ai-team-delivery 或 D6 lifecycle。例如：證據已經上傳到 GitHub、建立超過 7 天、而且沒有程序在用的 `llmtool-*` 和 `a11c-*` 目錄，可以刪除。
2. 刪除由 Owner 或 PM 依規則執行。Claude 不刪（記憶規則：不做 rm 或 cleanup）。
3. `claude-501` 是 Claude Code 自己的暫存，結束 session 後由 Claude Code 管理，不列入規則。

## 6. 文件 token 成本提案

| 檔案 | 大小 | h1/h2 段落數 | 最大的段落 |
|---|---:|---:|---|
| `tools/llmtool/README.md` | 197 KB | 45 | Issue #135 Codex 工具事件稽核（18 KB）、A10（10 KB，已完成）、A11b.1（10 KB） |
| `tools/llmtool/WORKFLOW.md` | 141 KB | 18 | Task Contract、company LLM 與證據（**79 KB**）、AI-led team 運作模式（24 KB）、PR #49 B5 拓撲修正（11 KB） |
| `tools/llmtool/ROADMAP.md` | 132 KB | 28 | A11c（31 KB）、A11 單一交付路線（23 KB）、A11 Production Workflow（17 KB，A11a 已完成） |

**為什麼值得做：**

- AGENTS.md 要求 Cloud 角色讀 WORKFLOW。CLAUDE.md 和 ai-team-delivery 把專案狀態指向 ROADMAP。
- 每次讀一份，就是 3–5 萬 token。整套讀完約 12 萬 token，接近一個 Codex 回合的上下文上限（258K）的一半。
- 今天 Codex 用完額度，PM 的主對話一個回合就用了 4,200 萬 input token（大多是快取）。文件越小，每個回合越便宜。

**提案**（做法和 `docs/history/` 現有的封存方式相同）：

1. 每份文件拆成「現行」（目標 15 KB 以內）和 `docs/history/<檔名>-archive-20261024.md`（原文搬過去，不刪任何字）。
2. 「現行」只保留四類內容：
   - 還有效的規則；
   - 目前進行中的里程碑；
   - 每個已完成里程碑一行摘要，附封存連結；
   - 現有外部連結會用到的錨點。例如 pm-continuation 連到 `ROADMAP.md#a11c-work-packages-and-continuation-runtime-gaps`，這個錨點要保留。
3. 拆分前先列出所有指向這三份文件的連結和錨點，拆完用連結檢查確認沒有斷。
4. 一次只拆一份，從 WORKFLOW 開始，因為它最大的那段 79 KB 大多是歷史決策。

**另一個省成本的點：CI paths。**

- 依 #315 報告的統計，近 14 天有 57 個只改 `.md` 的 commit，每個都跑了兩組約 8 分鐘的 matrix。
- 可以讓 CI 跳過只改最上層 `tools/llmtool/*.md` 的 PR。注意 `llmtool/skills/*/SKILL.md` 是打包的產品檔，不能跳過。
- 要先改 PM 的合併關卡（「exact-head CI」改成「文件 PR 走文件驗證」），否則 PM 會一直等一個不會出現的 CI。#99、#271、#293 都和這件事有關。

**誰決定：** ROADMAP 歸 PM 維護，角色規則歸 Coordinator。這是提案，Claude 不會自己動這三份文件。

## 7. 建議的下一步

| 優先 | 工作 | 誰 |
|---|---|---|
| 1 | §2.1 那 13 筆過期的登記狀態改正，照 lifecycle 釋放 | PM |
| 2 | RETIRE_READY 的 13 筆退場；#267 那批一次處置 | PM |
| 3 | 草稿 PR：#286、#300 轉 ready 合併或關閉；其餘依 §3 | PM（#261、#265 需要 Owner 或 Coordinator） |
| 4 | `/tmp` 與 `.venv` 保留規則 | PM 和 Coordinator 訂規則，Owner 或 PM 執行 |
| 5 | 文件拆分，從 WORKFLOW 開始；CI paths 和文件 PR 的合併關卡 | PM 排程，Coordinator 確認角色規則 |
| 6 | ai-loop 舊 Issue 分類 | PM |

## 8. 本報告沒有做的事

- 沒有刪除、移除、關閉或改動任何 D6 worktree、登記檔、PR、Issue、`/tmp` 目錄或文件。
- 沒有讀任何任務內容、模型輸出或證據檔的內容，只讀了狀態欄位、大小和日期。
- 沒有呼叫任何 provider 或模型。
