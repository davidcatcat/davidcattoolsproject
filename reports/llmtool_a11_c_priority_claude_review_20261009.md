# llmtool A11 與 C 的重疊、依賴和優先順序：Claude 獨立審查

> 回應 [PR #68](https://github.com/davidcatcat/davidcattoolsproject/pull/68) 的審查請求
> （`reports/llmtool_a11_c_track_overlap_review_request_20261009.md`，head `452fb24`）
> 審查基準：`main@7fcf4f8015b1ebae1e956647b83f812589c3a8e4`
> 審查者：Claude Code（Opus 5.5），在 Mac canonical repo 上進行
> 證據類型：**一般審查（只讀文件和 Git 歷史）**。這不是 Security 或 QA gate，也不批准任何 Roadmap 變更。
> 方法：讀 Roadmap、MVP、WORKFLOW、`ai-team-delivery`、Issue #63 和 #66、PR #49 複審報告，
> 用 `git blame` 查各段文字是何時寫進去的，並用 `gh` 查 issue。
> 這次沒有執行產品程式，也沒有呼叫任何 Provider。

## 摘要

- **現況。** C 系列大部分是 A 規劃中已有關卡的重新命名和重新排序，不是新的架構。
  C1a（PR #55）已經 merge。A11b.2.2.2 還沒開始，也還沒授權。
- **必要關卡。** C1b.0 只擋「片段外送給任何 LLM」這一件事。A11b.2.2.2 的前提是先寫好設計規格並通過獨立安全審查，
  跟 C1b/C1c 沒有技術依賴。
- **建議排序（唯一主線）。**
  1. 先完成 C1b.0：已經有規格，是小切片。
  2. 主線推進 A11b.2.2.2 設計規格，審查之後才實作。
  3. C1b Phase 0 和 C1c 延到 C2 有實際使用者之後。

## 逐題回答

### Q1 歷史和重複性：**CORRECT**

協調者說「C 是 A 的 release slicing」，這個說法不夠精確。
`git blame tools/llmtool/ROADMAP.md` 顯示，C1 的需求在 C 系列出現之前就已經寫進 A 規劃：

| C 的分段 | A 規劃中原有的出處 | 寫入時間 |
| --- | --- | --- |
| C1a：test-format selector | ROADMAP 約第 635–640 行：「A future deterministic real-test-format selector is a separate work item」 | 2026-10-07（`8d6e25c`） |
| C1b：Company-only Phase 0 | ROADMAP「New-route/pipeline gate」，以及 A11c.1 第 969 行的新 route 前置 gate | 2026-10-07 之前 |
| C1c：paired premium-token 量測 | 同一個 gate 裡的「才允許 paired strong-model measurement」 | 同上 |
| C0：dogfood | ROADMAP 第 650 行「Development-time cost-aware dogfood — available now」 | 2026-10-06（`32f6406`） |
| C2：原生開發執行 | A11b（Action、Policy、Executor、containment、approval） | 既有 |
| C3：切換 | A11c.2 Real-world Agent Acceptance | 既有 |

**PR #50（2026-10-09）真正新增的只有兩件事：**

- 「成本優先」的排序：C1 在前，C2 在後。
- C3 的「取代 ai-loop」切換條件。

因此，使用者說「C 在 A 的時候就討論完了」，在需求層面基本上是對的。

**重複建置的風險：** 同一個關卡有兩個名字，例如 A11c 的新 route 前置 gate 和 C1b/C1c。
這可能導致兩份看起來各自獨立的待辦、驗收或報告。
建議只保留一張 A↔C 對照表（見 Q6），讓 C 只當作交付標籤。

### Q2 精確狀態：**CONFIRM，另有三處文字過時**

PR #68 第 2 節的狀態表和 Git 一致：

- C1a 已 merge 為 `27bee10`。merge 結果和審查過的 `ee8180b` tree 相同，main 在 Mac 上跑 1297 tests 為 OK。
- A11b.2.2.1 已 merge 為 `eb46462`。
- A11b.2.2.2 和 A11c 尚未開始。

報告中沒有暗示任何項目「已上線」。

以下文字已經過時，應修正：

| 位置 | 目前文字 | 實際狀態 |
| --- | --- | --- |
| `CLAUDE.md:74` | 「A11b.2 is NOT STARTED」 | A11b.2.1 和 A11b.2.2.1 已完成並 merge |
| `AGENTS.md:155` | 「A11b.2 is NOT STARTED」 | 同上 |
| `tools/llmtool/ROADMAP.md:550` | 「A11b.2.2 remains NOT STARTED / B5 design remediation」 | A11b.2.2.1 DONE；A11b.2.2.2 NOT STARTED |
| `tools/llmtool/ROADMAP.md:734` | 「A11b.2.2 B5 design remediation / NOT AUTHORIZED」 | 同上 |

這些都是文件問題，不阻擋產品前進。

### Q3 必要依賴：**CONFIRM，另有一條需要注意的出口**

- C1b.0（Issue #66）保護的是 **C1 輸出的片段跨越 Provider 信任邊界**，不論對象是公司 LLM、Claude 或 Codex。
  它與片段產生的方式無關，本機使用不需要它。
- 沒有任何證據顯示 A11b.2.2.2（本機的真實 Git ADD）必須先完成 C1b 或 C1c。
  A11b.2.2.2 的前提寫在 ROADMAP 第 856 行：B5 §14.1 設計、hook 隔離、host-thread/sandbox 和 approval。
- **隱藏的出口：** MVP C2 的 bounded loop 包含「independent Claude review of frozen diff and actual evidence」。
  Claude CLI 是外部 Provider。如果 C2 把測試輸出或 C1 片段放進 Claude 的審查內容，
  這個具體出口就需要 C1b.0（或相同等級的 redaction）加上明確授權。
  所以 **C1b.0 是 C2 審查步驟的前提，但不是 A11b.2.2.2 的前提**。

### Q4 設計和資安：**CONFIRM，另有一個追蹤缺口**

A11b.2.2.2 的下一步是**寫設計規格並送獨立審查**，不是直接實作。
[PR #49 獨立複審報告](llmtool_pr49_independent_rereview_20261009.md) 第 101 行已經列出規格必須處理的內容：

- B5 §14.1 的 post-state 完整分類。containment 無法確認時，一律判為 `STATE_UNKNOWN / USER_ACTION_REQUIRED`。
- P49-L1：把 fault hook 從 product engine 中分離。
- S49-3 和 QA49-N2：raw directory FD 的清理。
- QA49-N1：natural leader-exit 的永久回歸測試。
- B5 F4：product host 的單執行緒前提和支援的 runtime。

**缺口：** 用 `gh issue list --search 'S49-3 OR P49-L1 OR QA49'` 查不到任何 issue。
這些延後項目只出現在 README 第 151–152 行和 MVP 第 154–156 行。
建議開一個 issue（例如「A11b.2.2.2 design inputs」）集中追蹤。

此外：

- PR #49 的 synthetic containment PASS **不能**當作真實執行的授權。
- A11b.3 approval semantics 是 C2 真正使用前的必要條件，但不是 A11b.2.2.2 設計規格的前提。

### Q5 優先順序：**選「受控並行」，但先做完一個小切片**

| 選項 | 開發和驗收成本 | 風險 | 對使用者目標的交付效果 |
| --- | --- | --- | --- |
| 先完成完整的 C1b/C1c | 高。需要公司 VPN、LLM、預先登記的實驗，以及多輪 paired runs | 中 | 低。只優化 test-triage 這一條 route；PR #36 的小輸入 pilot 反而**增加了 104%** token |
| 先做 A11b.2.2.2 | 中。先寫規格、送審，再做一個小實作 | 這條路線本身風險較高，但有既有的 gate | 高。這是原生執行器的關鍵路徑，執行器是取代 ai-loop 的前提 |
| 受控並行 | C1b.0 只改一個檔案、規格已就緒，跟設計工作沒有衝突 | 低 | 高 |

**唯一建議主線：**

1. **先完成 C1b.0（Issue #66）。** 這是已上線程式（PR #55）唯一已知的安全缺口，規格和 allowlist 都已寫好。
2. **主線：A11b.2.2.2 設計規格**，內容依 Q4，送獨立 Security 審查之後才實作最小的受控切片。
3. **C1b Phase 0 和 C1c 延到 C2 有實際使用者之後**，屆時在真實的開發流程上量測。

**理由：** 使用者的目標是 llmtool 上線、取代 ai-loop、節省 token，並且不必手動轉貼提示。
今天實際觀察到最大的 token 和人力消耗來自 ai-loop 的繞路：
PR #55 的 QA Low 週期在約 10 小時內產生了 97 個控制分支 commit 和多輪 Claude 審查
（見 `reports/ai-loop/coordinator-judgement-retrospective-20261009.md`），而不是測試 log 太長。
只有原生執行器能從根本上消除這種消耗。

**不能擅自放寬的安全條件：**

- 沒有通過獨立審查的設計規格，就不做真實 Git ADD。
- 任何片段或測試輸出外送給 Provider 之前，必須完成 C1b.0 並取得明確授權。
- Synthetic PASS 不等於可以執行。
- Mac 的行為只能用 Mac 上的證據證明。
- 模型輸出不構成 approval。

### Q6 文件和 Issue 的最小修正清單：**NEEDS_DECISION**（由協調者在委託範圍內決定）

| 檔案 | 修正 |
| --- | --- |
| `tools/llmtool/ROADMAP.md:16` | 「Next engineering work is C1b.0」改為：「C1b.0 是已就緒的小切片；主線為 A11b.2.2.2 設計規格；C1b Phase 0 和 C1c 延到 C2 之後」 |
| `tools/llmtool/NATIVE_SELF_DEV_MVP.md:189–194` | 「C1 first / C2 next」改為：C1 = C1a（DONE）加 C1b.0；Phase 0 和 C1c **不是** C2 的前提；C2 的 Claude 審查出口需要 C1b.0 |
| `ROADMAP.md` 或 `NATIVE_SELF_DEV_MVP.md` | 加一張 A↔C 對照表（同 Q1），只保留一份 |
| `CLAUDE.md:74`、`AGENTS.md:155`、`ROADMAP.md:550,734` | 修正過時狀態（同 Q2） |
| 新 Issue | A11b.2.2.2 design inputs：B5 §14.1、P49-L1、S49-3/QA49-N2、QA49-N1、F4 |
| Issue #63 和 #66 | 不變 |

必須保留：

- A11 的原始里程碑編號。
- 所有歷史報告，**不要改寫**。
- PR #50 的設計原文，只修改「Decisions and sequencing」段落。

### Q7 AI 團隊責任：**CONFIRM，加一項說明**

`ai-team-delivery`、`CLAUDE.md`、`AGENTS.md`、`safe-git` 和 `WORKFLOW` 都已寫明：
使用者是需求方和驗收者，不是 PM，也不負責轉貼提示。
我沒有找到仍把使用者當技術 PM 的文字。

**應照實寫出的限制：** 在原生執行器上線之前，Codex 的實作任務仍然要靠使用者在 Mac 上執行 `run.sh`，
而且 `run.sh` 鎖定 SHA，新版本需要 `--approve`。
這是平台存取的限制，不是 PM 職責，但會影響每個切片的交付時間。

## 其他觀察（Low，不阻擋）

- **L1。** 同一條協作規則重複寫在 `CLAUDE.md`、`AGENTS.md`、`ai-team-delivery`、`safe-git` 和 `WORKFLOW.md`。
  repo 自己的規則是「Do not duplicate the same rule across multiple skills」。
  建議以 `ai-team-delivery` 為唯一來源，其他檔案只放連結。
- **L2。** PR #55 的 3/3 處理歸屬是一件歷史事實，目前寫在規則檔（`CLAUDE.md`、`ai-team-delivery`）裡。
  使用者已向 Claude 確認，當時確實回了「同意」，所以內容是正確的。
  但歷史事實比較適合放在報告裡，規則檔只保留「要區分提案者、確認者、執行者」這條原則。
- **L3。** PR #64 在 merge 前由協調者加了 commit `922dcec`，修改了 Claude 撰寫的 E8 段落。
  E8 原本說使用者沒有同意，這是 Claude 誤解使用者的回覆，所以修正後的內容在事實上是正確的。
  不過，修改他人署名的報告時，建議加上「Coordinator amendment」標註，或另外寫一段回應，
  讓稽核紀錄能看出是誰改的。

## 結論

- PR #68 的方向（主線 A11b.2.2.2 設計，C1b.0 保留）大致正確。
- 主要修正有兩點：
  - C 系列更接近「A11c 既有關卡的改名加上排序」，不只是 release slicing。
  - C1b.0 應該先完成，因為它小、已就緒，而且是已上線程式的安全缺口。
- 這份審查不批准任何 Roadmap 變更、真實 Git mutation、Provider 外送或 merge。
