# EC2 offload、Mac CI、公司 LLM（gw）現況報告（2026-10-27）

- 作者：Claude PM（`claude-pm-20261027c`，session `c515bd64`）
- 目的：回答 Owner 的三個問題，供 Owner 和 ChatGPT Coordinator 檢視。
- 資料時間：2026-10-27 約 19:00 CST；main = `59bd3b5`（列車 2 #456）。
- 性質：PM 的量測與判斷，不是正式審查，也不是 Owner 的產品驗收。所有數字都附資料來源；樣本小的地方會直接寫出來。

## 摘要

| 問題 | 結論 |
|---|---|
| 1. EC2 有沒有分擔耗 token 的工作 | **有分擔測試執行，省 token 的效果還沒被證明。** EC2 跑測試，不跑 AI 模型。用 EC2 收據的 Codex Task 平均少約 15–20% prompt token，但樣本很小（6 和 10 個）。 |
| 2. Mac CI 有沒有大幅改善 | **排隊大幅改善**（中位數 8.1 → 0.5 分鐘），**執行時間沒有改善**（22.0 → 23.1 分鐘）。原因：最近 14 個 PR 有 13 個跑全套。 |
| 3. 公司 LLM（gw） | L1–L6 分級題庫已在 #452 合併，**但還沒拿來測 gw**。已知能力：4 行的測試修改可以一次完成；約 15 行的邏輯修改兩次都沒做對。 |

## 1. EC2 是否真的分擔了耗 token 的工作

### 先釐清

EC2 跑的是**測試**，不是 AI 模型推論。省 token 只能是間接的：Codex 不必在自己的 sandbox 跑測試、讀長篇測試輸出，改由 EC2 執行，llmtool 只拿回一張精簡、綁定 SHA 的結果收據（`task remote-test`）。

### 實際移到 EC2 的工作

- **PR 的 Linux 完整測試**：分 3 個 shard，在 `ec2-self-runner`、`-2`、`-3` 上跑。最近 9 次成功的 run，中位數 7.0 分鐘（3.2–15.0 分鐘）。
- **llmtool `task remote-test`**：TaskStore 裡共有 45 張收據（38 PASS、7 FAIL），另有 3 次被拒絕的紀錄（2 次 `MODULE_NOT_ADMITTED`、1 次 `SHA_MISMATCH`），全部存在原 Task 上。今天的修復流程（#459、#461、#462、#464）都用 EC2 收據驗證。

### Codex Developer 每個 Task 的 token 用量

資料來源：`~/.local/state/llmtool/tasks/*.json` 中 Codex Task 的 CLI 回報 usage（`developer.usage`）。分組依 callback 中的測試狀態：有 `DEFERRED_REMOTE_RECEIPT` 歸為「EC2 收據」，其餘有測試結果的歸為「本機測試」。這是 PM 的粗略分類，不精確。

| 日期／分組 | 類型 | n | 平均 prompt | 中位數 | cache 命中 | 平均耗時 |
|---|---|---|---|---|---|---|
| 10-26 本機測試 | 新任務 | 33 | 0.93M | 0.72M | 1% | 2.6 分 |
| 10-27 本機測試 | 新任務 | 18 | 0.65M | 0.43M | 92% | 1.8 分 |
| 10-27 本機測試 | 修復 | 12 | 0.33M | 0.28M | 89% | 1.2 分 |
| 10-27 EC2 收據 | 新任務 | 6 | **0.55M** | 0.33M | 91% | 1.7 分 |
| 10-27 EC2 收據 | 修復 | 10 | **0.27M** | 0.28M | 85% | 1.0 分 |

合計：10-26 共 72 個 Codex Task、prompt 61.3M；10-27 共 69 個、prompt 36.0M（其中 cache 32.7M）。

### 判讀

- **EC2 本身的效果小，而且未證實**：同一天比較，EC2 收據的 Task 大約少 15–20% prompt token，但樣本只有 6 和 10 個，分組也不精確。
- **主要改善來自其他地方**：
  - 派工規格變窄、只開放一個相關的測試模組：每個 Task 從 0.85M 降到 0.52M。
  - cache 命中從 1% 升到 92%：這會降低成本，但不會減少 token 數量。
- **反例**：之前完全不准 Codex 跑測試時，5 個 Task 全部 `TEST_FAILED`，總共用掉 6.5M。改成允許跑一個相關模組後，5/5 通過，每個 0.18–0.59M（#425 comment 6432826380）。完全不測反而更貴。
- **結論**：EC2 的實際價值是讓 Mac 不用跑重的測試，並提供可追溯 SHA 的測試證據。「省多少 token」仍是 **NOT VERIFIED**（與 Roadmap DET-2 一致）。要證明需要 A/B 量測：同類任務分別用 EC2 收據和本機測試，比較總 token，包含修復輪和 PM 的開銷。

## 2. Mac CI 執行情況

資料來源：GitHub Actions `llmtool Mac CI` 最近 200 個 run；job 層級時間取自 `check (macOS 3.13)` 的 `started_at`/`completed_at`（共 60 個成功 run）。分界點：列車 2 合併時間 2026-10-27 15:44 CST（包含 #447 Mac 選測、#448 測試加速）。

| | 列車 2 合併前（n=51） | 合併後（n=9） |
|---|---|---|
| 排隊等待，中位數 | 8.1 分（最長 63.5 分） | **0.5 分**（最長 22.6 分） |
| PR 的 Mac job 執行時間，中位數 | 22.0 分 | **23.1 分**（14.2–23.6） |

### 為什麼執行時間沒有變短

`tools/llmtool/scripts/ci_mac_modules.py` 的 `SHARED_MODULES` 規則：只要 PR 改到 `llmtool/__init__.py`、`cli.py`、`task_store.py`、`task_submission_cli.py` 其中一個，就回傳 `FULL`，也就是跑全部 106 個測試模組，約 22 分鐘。

| PR | Mac 選測結果 |
|---|---|
| #439 #441 #444 #450 #454 #455 #458 #460 #461 #462 #464 #459 #465 | FULL |
| #457 | 選測 26 個模組 |

最近 14 個 PR 有 13 個是 FULL，因為幾乎每個 llmtool 功能都會改到這幾個共用檔（新增子指令必須改 `cli.py`／`task_submission_cli.py`，新增欄位必須改 `task_store.py`）。所以 #447 選測幾乎沒有發揮作用。#461 是因為改了 workflow 檔而跑 FULL，這是預期的。

例：#459 目前的 Mac job 已跑 22 分鐘。前面的準備步驟 36 秒就完成，之後都在 `Offline canonical checks` 跑全套，不是卡住。

### 排隊為什麼改善

Linux 完整測試移到 EC2，main push 不再重複跑全套（#447），Mac runner 不再那麼擠。

### 改善提案（待 Owner 決定）

改到共用檔時，不再直接跑 FULL，改成「Mac 相關測試（`ci/mac-modules.txt` + 偵測到 darwin／行程控制的測試）+ 有 import 被改模組的測試」，完整測試交給 EC2 Linux（每個 PR 本來就會跑）；全套 Mac 測試保留在 main 的定時 safety net（#461）。

- 預估：大多數 PR 可從約 22 分鐘降到 10 分鐘以內。這是估計，要實測才能確定。
- 風險：只在 macOS 上才會出現、但沒被偵測規則抓到的問題，會延到 main 定時全套才發現。
- 這等於縮小 Mac PR gate 的測試範圍，需要 Owner 同意。

## 3. 公司 LLM（gw）

資料來源：Issue #290、TaskStore 中 `company-gw` 的 Task。

| 日期 | 內容 | 結果 |
|---|---|---|
| 10-21 | 11 次 llmtool 派工 | 10 次 `DEVELOPER_OUTPUT_REJECTED`（diff／回傳格式錯誤）、1 次 unavailable；之後修了 llmtool 的接收與 normalize（#428） |
| 10-26 | Test B：4 行的測試修改 | **一次通過**，llmtool 接受，PM 套用後 `test_orchestration` 32 個 OK；usage 約 1.2K tokens、約 2 秒（PR #430） |
| 10-26 | Test C：約 15 行邏輯修改 + 測試 | **FAIL**：第 1 次 diff 格式錯誤；修復 1 次後格式被接受，但邏輯錯誤，gw 自己寫的測試也失敗；依規則停止，沒有開 PR |
| 10-27 | Owner 要求 Codex 建分級題庫 L1–L6（#290 comment，05:26） | 題庫（含隱藏測試、參考解答、評分器）已由 #452 隨列車 2 合併（15:44） |

### 目前狀態與下一步

- **分級題庫還沒拿來測 gw**：TaskStore 在 10-27 沒有任何 company-gw Task。
- 下一步：PM 透過 llmtool 從 L1 開始依序派工（`task delegate --provider company-gw --mode proposal`）。gw 只提出 diff，套用和隱藏測試由 PM 的執行環境負責。每級最多 1 次嘗試加 1 次修復，從第一個失敗兩次的級別開始就停止。每級記錄 Task ID、usage、llmtool 結果和隱藏測試結果。
- 前提：公司 VPN 和 gw 端點要連得上；連不上不會擋住其他工作。
- 目前判斷：gw 只適合範圍很小、有明確測試的工作。能做到哪一級要等分級測驗；現在不能宣稱省了多少錢，也不能說它具備一般 Developer 的能力。

## 限制

- token 分組是 PM 依 callback 狀態的粗略分類；EC2 那兩組樣本很小。
- Mac 時間只取最近的成功 run；合併後只有 9 個樣本。
- 沒有計入 PM（Claude）和 Reviewer 的 token；它們在 Claude 帳號上，沒有 llmtool usage 紀錄。
- 本報告沒有執行任何新的測試或模型呼叫，只讀取現有紀錄。
