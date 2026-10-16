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

- **已由 D-003b 確定**：現有 Codex Integrator 兼任 PM，不另設固定 PM CLI；是否在該 Integrator 內使用子代理，以及其他模型的替代策略仍待技術驗證。
- PM Agent 何時啟動／停止、誰能啟動、如何保留跨 Session Task/Decision、如何處理 Codex Quota 耗盡？
- PM 能否提出 Git 提交／合併建議、如何保持真正的獨立驗收與可追溯責任？
- 如何在不加入第二套 Controller 或額外永久 Background AI 的前提下完成 M2？

## D-003b — 現有 Codex Integrator 兼任 PM，自主判斷是否需要多 Agent

- **決策狀態：ACCEPTED（使用者 2026-10-17 明確同意，並補充「如果需要多 agent，請該 Integrator 自己判斷」）**
- **實作狀態：ROLE DECISION ONLY / llmtool M2 自動多 AI 閉環未 Live 驗收**

### 已確認的角色與工作方式

1. **現有的 Codex Integrator CLI Session 同時擔任 PM Agent / Integration Manager**，通常使用既有 canonical 主 Repository 作為工作環境；**不另外啟動一個固定的 Codex PM CLI**，也不為了 PM 再建一個 worktree 或獨立 Controller。
2. 對大型／多階段需求，**Integrator／PM 自己判斷是否需要多 Agent**、是否拆成子任務、需要哪些 Agent／Worker、依賴順序與何時呼叫其他 AI；使用者不必逐項做技術分工，不必負責傳遞提示詞。
3. **單一 Agent 足以完成的工作就不強制開多 Agent**；多 Agent 是因工作性質與審查需求而按需使用，不是預設越多越好。任務簡單且符合 D-002a 時可直接走 llmtool 已授權流程，無須喚起 PM。
4. Integrator／PM 可規劃及協調 Codex Developer、Claude Reviewer、公司 LLM 和其他已驗證的 Workers；llmtool 只負責依**已授權的計畫**執行、路由、狀態／成本／證據記錄與交接，不自行做跨 Agent 語意派工決策。
5. Integrator／PM 同時是 Integration Manager：收取並檢查工作成果、核對測試／PR 證據、安排已授權整合；**不能用 PM 身分跳過獨立的 Security/Functional 審查**，也不得將其自行編寫或修改的內容視為已被獨立核准。
6. Integrator／PM 的「自行判斷需不需要多 Agent」是**工作規劃與分工權**，不是新授予的檔案／Git mutation、Provider egress、憑證、額外付費、無限重試或正式上線權限；仍需遵守既有 Task Contract、預算與授權，已無可用額度的 Provider 應等待或按預先核准的路徑處理。
7. **PM 可從現有 Integrator Session 負責任務，也可研究在該 Session 按需使用子代理**；具體 CLI Subagent 能力、呼叫機制與跨 Session 協作仍待後續實測，不能把此架構決議當成已完成的產品功能。

### 目標工作流

```text
Owner 大型需求
  → Codex Integrator (= PM Agent + Integration Manager)
       ├─ 判斷單一 Agent 是否足夠
       ├─ 必要時拆工與挑選多 Agent
       └─ 交付工作計畫／授權範圍
  → llmtool（確定性執行與追蹤，依實際可用的整合階段）
       ├─ Codex Developer（實作）
       ├─ Claude Reviewer（獨立驗證）
       ├─ 公司 LLM API（已驗證且授權的輔助工作）
       └─ 其他已驗證 Agent / deterministic tools
  → llmtool 回傳證據
  → 同一 Codex Integrator／PM 整合／回報或 STOP
```

### 仍未決定／未完成

- 以何種最小整合方式完成 Codex Integrator → llmtool → Workers 的實際指派、喚醒、會話保留及失敗恢復；M2 Live E2E 仍未驗證。
- Integrator 自主多 Agent 的具體預算、任務上限、額度耗盡替代與防止無限重試；不因本決策自動提高花費。
- 若將來要把 PM 角色從 Codex Integrator 移轉到別的模型，需另行討論；**此階段的已選方向是現有 Integrator 兼任 PM**。

## D-003c — ChatGPT → Integrator/PM → llmtool → Worker → Integrator 的單次交接持續閉環

- **決策狀態：ACCEPTED（使用者 2026-10-17 明確描述所需的產品工作流）**
- **產品現況：TARGET / M2 跨 CLI Live 閉環未完成**
- **不表示授權實際寫入主 Repo、無限制 Token 使用或自動 Merge**

### 使用者體驗與責任

1. **需求入口：ChatGPT**。使用者與 ChatGPT Coordinator 先充分討論要做的項目；只有使用者確認執行方向時，Coordinator 才產出一份 Integrator/PM 能理解並執行的完整提示。
2. **人工交接限定一次**：使用者自行將初始提示貼到既有主 Repository 的 **Codex Integrator CLI**（兼 PM Agent）。之後一般的子任務分派、結果回傳及下一輪續工，不應再要求使用者重複說「review repo」或搬運 Agent 提示／報告。
3. **Integrator/PM 決策**：評估工作、拆解、判斷是否啟動多 Agent、選擇 Worker/Provider、依賴與驗收標準，透過 llmtool 交付可執行／已授權的工作計畫。
4. **llmtool 是確定性的執行與協作系統**：接收工作計畫、核對可用 Provider / 任務合約 / 資料邊界 / Git 權限 / 額度、啟動已批准工作、等待執行、保留狀態及事件、驗證工作身分與輸出。它不替 PM 進行複雜語意派工決策。
5. **Workers 執行與回報**：各自完成分派工作、測試；在各自授權的 Worktree 與分支上記錄 Commit，符合該任務 Git 權限時 Push 自己的分支／提交 Draft PR；提交可核實的完成事件與報告（至少 task_id、agent/role、branch、commit SHA、結果／測試／review evidence、失敗與未知），不是僅憑模型宣稱 DONE。
6. **事件驅動回到 PM**：llmtool 在 Worker 結束、需要審查、阻礙、超預算或完成時，可靠地通知／喚醒既有 Integrator/PM，由其閱讀原始可信證據及摘要，自主決定下一個步驟或修復；簡單的等待、狀態查詢與排程無需持續耗費 PM 模型 Token。
7. **閉環終止條件**：Integrator 持續推進到經證據支持的 `DONE`（完成並通知使用者）、`NEEDS_DECISION`（真正需要使用者產品／授權決策）、或 `STOP/BLOCKED`（安全、品質、額度、環境或有限重試無法滿足）。回報包含實際 Git/PR 引用、驗證與尚未完成項目，不捏造通過結果。
8. **保留原先接受的簡單路徑**：D-002a 仍可在事先明確授權／無跨 Agent 決策的情形直接由 llmtool chat 執行，不強制喚醒 PM。

### 概念回路（目標，不是已完成技術能力）

```text
使用者 ↔ ChatGPT 討論、確認需求
               │ 產生一份完整 Integrator/PM 提示
               ▼ 使用者只貼一次
Codex Integrator CLI (= PM / Integration Manager)
       │ 任務拆分、決策與多 Agent 選擇
       ▼
llmtool：Policy / Dispatch / Events / State / Evidence
       │ 依計畫與既有權限
       ├── Codex Developer
       ├── Claude Reviewer
       ├── 公司 LLM API（僅限通過品質與資料路由的工作）
       └── 其他合格 Worker / 確定性本機工具
       │
       ├── 已授權的 Commit / Push / Draft PR 與驗證證據
       └── 可靠完成／阻礙事件
               │
               ▼
llmtool 通知或喚醒 Integrator，保留 context / task identity
               │
               ├── 尚需工作：由 PM 再決策 → llmtool（循環）
               ├── 需要 Owner 决策：NEEDS_DECISION → 使用者
               └── 驗收完成：DONE → 使用者
```

### 重要的技術待議事項（不因本決議自動通過）

- 現有 Codex CLI Session 如何做到可靠喚醒／續接並證明同一 Task/Session 身分？不能假定已有可驗證的 callback 或 resume 功能。
- llmtool 事件格式、去重／replay、防冒用、Worker 工作目錄／Git SHA 綁定與阻礙恢復，盡量沿用現有 A11c state/event/controller，**不做第二套長期控制器**。
- Worker Push 的授權界線、獨立 Reviewer Gate、CI 與 Draft PR／Merge 的差別沿用 A11b/F2；是否 Push 不由 Agent 自稱完成決定。
- Token 不夠、VPN 斷線、Claude Reviewer 不可用時，PM 應等待、保留進度、視已授權的其他 Provider 能力判斷下一步，不能用未經品質驗證的低階替代模型偷換必要審查。

## D-005a — 公司 LLM 分流的最高原則：節省主模型 Token 且不明顯犧牲品質

- **決策狀態：ACCEPTED（使用者 2026-10-17 強調最重要的品質底線）**
- **產品現況：GOAL / 公司 LLM 全面生產路由與品質／成本 E2E 尚未驗證**

1. 公司 `gw` API 的主要產品價值，是**減少 Codex/Claude 等主模型不必要的 Token／額度消耗**，不是取代所有 AI 的能力，也不是為了使用公司 LLM 而使用。
2. **品質優先於節省**：公司 LLM 的錯誤不能離譜，不可因便宜就容忍關鍵邏輯錯誤、錯誤的 Git 變更／指令、不實根因或憑空捏造測試與審查證據。沒有足夠的任務品質證據時，公司路徑停留在候選／輔助；不能主張生產已可靠。
3. **按任務類型測量品質**：先從公司 LLM 比較擅長且允許資料外傳的低風險項目試做（如有界 log/test 分類、摘要、Commit Message、簡單文件建議）；以真實題目、客觀檢查及必要的獨立強模型抽查比對，評估誤判／漏判、修復成本和採用結果，再決定該 route 能否開啟。
4. **計算實際淨效益**：不能只計公司模型的報告；要把 Coordinator/PM、重試、跨模型驗證、錯誤修復、人工介入，以及來源中可觀測的 Codex/Claude Token/Quota 一起考慮。成本資料不完整就標記 UNKNOWN，不宣稱已經省錢或不虛構主模型訂閱額度與 API Token 的換算。
5. **失敗即回退，不盲目重試**：公司輸出缺證據、格式不符、品質不達標時，依已核准路徑升級給較強模型／PM，或明確 STOP／等待額度，不允許「低成本」資料直接支配正式 Git 修改、獨立 Review 或自動 Merge。
6. **資料與網路授權仍必要**：公司 LLM 只有 API；透過 VPN 連線，哪一類 Repo Diff／測試 Log／程式原始碼能送出必須分別確定允許範圍，不能因同屬公司就無條件外傳。

### 尚待量化

- 針對各種工作定義 **品質下限／容錯門檻、誤判等級、回退觸發器、抽樣規模及淨節省基準**。目前不預設某個百分比已達成。
- 已有實驗只支援特定 L01 大型 test-triage 路徑，**不能將其外推成所有公司 LLM 工作都節省 Token**。

## OPEN：後續逐項討論，不預先授權或排程開發

| ID | 議題 | 決策狀態 | 備註 |
|---|---|---|---|
| D-002 | Git Executor：如何從 LLM 文字建議到本機檔案修改、Git Commit/Push、PR | OPEN | 需對照 F2/A11b 權限與現有能力 |
| D-003 | PM／Integrator 自動喚醒、Worker event／handoff、生命週期實作 | OPEN | D-003a/b/c 角色、單次交接與持續閉環目標已接受；技術實作仍待驗證 |
| D-004 | llmtool 可做的具體工作／最小可用自我開發案例 | OPEN | 区分既有能力、測試、Live 驗收 |
| D-005 | 任務分流品質／Token 淨效益量化、資料路徑、額度控制 | OPEN | D-005a 品質優先的節省原則已接受；路由驗收標準仍待決定 |
| D-006 | Pixel-Crew、codex-claude-code-config 的 PM、跨 Agent 設計是否採用 | OPEN | 避免第二套 Controller |

## 記錄規則

- 一次只確認使用者正在討論的一項，不將顧問建議自動升格為使用者決策。
- `ACCEPTED`（架構決議）與 `IMPLEMENTED/LIVE_VERIFIED`（產品事實）嚴格分開。
- Coordinator 更新 Issue 討論與此 Git 版決策紀錄；此文件本身不要求啟動 Codex、Claude、AGY 或 Company LLM，不代表新增實作授權。
