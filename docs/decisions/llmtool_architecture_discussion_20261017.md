# llmtool 架構逐項討論與決策紀錄（2026-10-17）

> 討論主紀錄：[GitHub Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172)
>
> 性質：**Discussion / Decision Log Only**。此文件記錄需求所有者已確認的架構方向，**不是實作完成聲明、Live 驗收、額外 Git/Provider 權限或開發任務授權**。
>
> 變更慣例：每項議題逐一討論；使用者明確同意後才從 `OPEN` 轉為 `ACCEPTED`。已接受的決策若被修正，保存原版本與 `SUPERSEDED` 理由。具體實作仍沿用既有 A11b/A11c Roadmap、Issue #120、#97，不建立平行 Milestone/Controller。

## D-001 — CLI 統一入口與公司 LLM API 分層

- **決策狀態：ACCEPTED（使用者於 2026-10-17 明確表示同意）**
- **實作狀態：PARTIAL / 端到端未驗證**

### 決定內容

1. 使用者操作的是 **`llmtool chat` CLI 或其他未來整合的 CLI**，不是直接操作公司 LLM API。
2. **公司 LLM 僅提供 API**，例如既有公司 `gw` 服務。llmtool 透過 `openai-compatible` Provider / Profile 與它通訊；API 端負責模型推理、文字建議等，不能直接取得操作本機 Repo/Shell/GitHub 的權限。
3. 在使用者入口之下，**llmtool 負責 Provider 連線與可用的工具／任務流程**。其他 CLI 如何與 llmtool 整合是待驗證的實作問題，不宣稱 Codex/Claude/AGY 已能經 llmtool 完整互通。
4. 任何實際檔案操作、Git 變更、Commit、Push、PR，都應由 **本機受授權的 llmtool 執行／Git 層**負責，並遵守既有 Task Contract、Permissions、Git Policy 及審查／驗收關卡；模型輸出的指令或文字提案不得直接視為可執行權限。
5. **對應的概念圖（目標狀態）**：

```text
使用者
  │
  ├─ llmtool chat CLI（已有文字 CLI）
  └─ 其他整合 CLI（具體適配與完成程度待議）
       │
       ▼
  llmtool（Provider / Profile / Context / Task）
       ├─ HTTP API → 公司 LLM（推理與文字建議；不直接控制本機）
       └─ Action/Policy/Executor（規劃中的正式 Repo 寫入路徑）
                  │
                  ▼
            本機 Git Repository → GitHub
```

### 當前可確認範圍與缺口

- **已有**：`llmtool chat`；`openai-compatible` Provider，可用具名 Profile 設定公司 API；`llmtool git --json` 為唯讀本機 Git snapshot。
- **已有的有限驗證**：A11b F1 只在隔離、可棄置測試環境執行 Git ADD。
- **尚未提供／未經 Live 驗收**：一般真實 Repo 的 F2 寫入、`git commit`/`git push`／PR 自動發布；經 llmtool 完成的跨 CLI 自動開發閉環；其他 CLI 作為統一入口的端到端整合。
- **執行前置**：公司 API 需具備實際連線條件（例如 Mac 連 VPN）與該資料路徑的授權；設定範例不是當前可連線的 Live 證明。
- **不延伸解讀**：本決策不表示已同意新的 PM Agent 完整方案、公司 LLM 直接做 Git 寫入或改動 Roadmap；其後同意的有限『簡單任務免 PM』分流原則另記於 D-002a。

參考：
- [llmtool README](../../tools/llmtool/README.md)
- [llmtool Roadmap](../../tools/llmtool/ROADMAP.md)
- [llmtool Workflow](../../tools/llmtool/WORKFLOW.md)
- [A11c Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
- [F2 / Approval Issue #97](https://github.com/davidcatcat/davidcattoolsproject/issues/97)

## D-002a — 簡單 Git 任務的直接執行路徑（PM 免介入）

- **決策狀態：ACCEPTED（使用者 2026-10-17「先同意吧」）**
- **實作狀態：NOT_IMPLEMENTED / 正式 Git Write/Push 未 Live 驗收**
- **決策性質：僅確認目標路由原則；不代表 F2 寫入授權已核發，也不等於已批准完整 D-002 / D-003 方案。**

### 已同意的範圍

對於像「修改指定 README 安裝說明 → 執行必要檢查 → 建立 Commit → Push **自己的功能分支**」這類事先**以任務級授權**確認、目標明確、影響範圍已知且可驗證的工作，未來應容許 **llmtool chat 直接受理並完成工作，不必先經過 PM Agent**；模型只提出內容／建議，真正寫入、Git 與 GitHub 操作由具備獨立權限驗證的本機執行層處理。

候選「簡單直接路徑」的判準（非全域操作許可）：
1. 完成標準清楚。
2. 指定檔案／變更範圍已知。
3. 不需跨多個 AI 協調或規劃依賴。
4. 可由既有測試／比對或其他客觀證據驗證。
5. 已有該任務、操作、分支、資料路徑的必要授權，且 Repo 沒有未解的所有權、衝突或狀態不明。

滿足上述條件只代表**無須 PM Agent**，不代表自動具備檔案寫入、Git Push、Merge 或其他執行權。若只是少了資訊可直接補問／檢查；若需要拆分、跨 Agent 協調或重新規劃才升級給 PM。破壞性操作或改動 main，即使指令簡短，亦不因「簡單」而自動准許。

### 工作分類的初步共識

- **L0**：`git status`、branch/log/diff 等機械操作 → 本機確定性 Git 程式；不必 LLM/PM。
- **L1**：解釋 Diff、Commit Message、文字摘要 → 公司 LLM 等適當 Provider；不必 PM。
- **L2**：指定文件／單一低風險變更 → LLM 提案 + 已授權執行層；通常不必 PM。
- **L3/L4**：複雜修復、架構改造、跨 AI 相依及完整專案任務 → 視實際依賴、風險與執行證據決定是否交 PM；**PM Agent 具體架構仍在 D-003 討論中**。

### 保留的待決事項

- D-002：正式 Git Executor 的實作策略、授權契約、Commit/Push/PR 條件以及如何達成 F2/A11b 验收仍 OPEN。
- D-003：PM Agent 採用的模型、職責、啟動條件與是否作為跨 AI 唯一入口仍 OPEN。
- 尚未宣稱已具有經 llmtool 驗證的真實 Repo 寫入、Commit、Push 或 PR 全流程。

## D-003a — PM Agent 擁有 AI 指派決策；llmtool 僅執行既定計畫

- **決策狀態：ACCEPTED（使用者 2026-10-17 明確糾正原先 M2 角色描述）**
- **實作狀態：NOT_IMPLEMENTED / 尚無 PM→llmtool→跨 CLI Live 驗收**
- **詳細 PM Agent 設計 D-003：OPEN**

### 已接受的架構原則

1. **llmtool 不負責以 AI 推理決定**把複雜任務交給 Codex、Claude、AGY、公司 LLM 中的哪個、如何拆分、排依賴。這是 **PM Agent 的語意決策職責**；原先「llmtool 自動指派 Codex 修復、Claude 審查」的說法應更正為 **PM Agent 制定分工／審查計畫，llmtool 經權限與契約檢查後負責執行、派送、紀錄和回收結果**。
2. **PM Agent 可以由 Codex 啟動**，可研究使用 Codex 原生 Agent/Subagent 能力，而非預設需為 llmtool 另建一個獨立 AI 引擎。但啟動方式、Session 身分、生命週期、用量與受限時的替代模型，仍屬 **OPEN/NOT_VERIFIED**；「Codex 可啟動」不等於「只能由 Codex 啟動」。
3. llmtool 可使用**確定性的路由規則**執行已核准的工作計畫（或 D-002a 中已明確、已授權的簡單工作）；這是程序執行而不是 llmtool 自主做複雜 AI 決策。遇到需要改變目標／重新分工／判斷未知能力的情況，應交回 PM，而不是由 llmtool 猜測。
4. **決策權與執行權分離**：PM 的任務分派、模型輸出的命令、Codex 啟動 Subagent 都不授予 Git/檔案寫入、外部 Provider、預算擴張、正式 Release 或獨立審查的權限。既有 A11b/F2/Source Review Gate 繼續生效；同一 Developer 啟動的子代理不能僅憑角色名稱取代所需的獨立審查。
5. **M2 的正確目標表述**：PM Agent 規劃與決策，llmtool 以可驗證的 Task/Execution/Events/Policy 實際安排 Worker、測試、獨立審查和已授權 Git/PR 工作，最終得出 Draft PR 或明確 STOP；**整條 Live 閉環尚未驗證**。

### 概念資料流

```text
需求 →（簡單、已授權、預先明確的規則）→ llmtool 執行
     └→（需拆分、判斷、跨 AI 協調）→ PM Agent（可由 Codex 啟動）
                                         ↓ 已授權的計畫
                                        llmtool
                                         ↓
                          Codex / Claude / AGY / 公司 LLM / 本機工具
                                         ↓
                                   結果／證據 → PM
```

### 尚待討論

- PM Agent 是 Codex Developer 開啟的 Subagent、另一個 Codex PM Session，還是可替換 Provider 的獨立 Role？不預先選定。
- PM Agent 何時啟動／停止、誰能啟動、如何保留跨 Session Task/Decision、如何處理 Codex Quota 耗盡？
- PM 能否提出 Git 提交／合併建議、如何保持真正的獨立驗收與可追溯責任？
- 如何在不加入第二套 Controller 或額外永久 Background AI 的前提下完成 M2？

## OPEN：後續逐項討論，不預先授權或排程開發

| ID | 議題 | 決策狀態 | 備註 |
|---|---|---|---|
| D-002 | Git Executor：如何從 LLM 文字建議到本機檔案修改、Git Commit/Push、PR | OPEN | 需對照 F2/A11b 權限與現有能力 |
| D-003 | PM Agent 詳細設計、Codex 啟動方式／生命週期 | OPEN | D-003a 職責原則已接受；具體機制仍待討論 |
| D-004 | llmtool 可做的具體工作／最小可用自我開發案例 | OPEN | 区分既有能力、測試、Live 驗收 |
| D-005 | 任務分流圖、資料路徑、Token/Quota 成本控制與等待／恢復 | OPEN | 需訂驗收與資料證據 |
| D-006 | Pixel-Crew、codex-claude-code-config 的 PM、跨 Agent 設計是否採用 | OPEN | 避免第二套 Controller |

## 記錄規則

- 一次只確認使用者正在討論的一項，不將顧問建議自動升格為使用者決策。
- `ACCEPTED`（架構決議）與 `IMPLEMENTED/LIVE_VERIFIED`（產品事實）嚴格分開。
- Coordinator 更新 Issue 討論與此 Git 版決策紀錄；此文件本身不要求啟動 Codex、Claude、AGY 或 Company LLM，不代表新增實作授權。
