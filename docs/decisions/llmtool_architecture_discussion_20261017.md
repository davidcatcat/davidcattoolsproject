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
- **不延伸解讀**：本決策不表示已同意新的 PM Agent 方案、具體任務分流政策、公司 LLM 直接做 Git 寫入或改動 Roadmap。

參考：
- [llmtool README](../../tools/llmtool/README.md)
- [llmtool Roadmap](../../tools/llmtool/ROADMAP.md)
- [llmtool Workflow](../../tools/llmtool/WORKFLOW.md)
- [A11c Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
- [F2 / Approval Issue #97](https://github.com/davidcatcat/davidcattoolsproject/issues/97)

## OPEN：後續逐項討論，不預先授權或排程開發

| ID | 議題 | 決策狀態 | 備註 |
|---|---|---|---|
| D-002 | Git Executor：如何從 LLM 文字建議到本機檔案修改、Git Commit/Push、PR | OPEN | 需對照 F2/A11b 權限與現有能力 |
| D-003 | llmtool 輕量化：PM Agent 負責判斷與拆分，llmtool 負責執行 | OPEN | 不假定新 PM 架構已定案 |
| D-004 | llmtool 可做的具體工作／最小可用自我開發案例 | OPEN | 区分既有能力、測試、Live 驗收 |
| D-005 | 任務分流圖、資料路徑、Token/Quota 成本控制與等待／恢復 | OPEN | 需訂驗收與資料證據 |
| D-006 | Pixel-Crew、codex-claude-code-config 的 PM、跨 Agent 設計是否採用 | OPEN | 避免第二套 Controller |

## 記錄規則

- 一次只確認使用者正在討論的一項，不將顧問建議自動升格為使用者決策。
- `ACCEPTED`（架構決議）與 `IMPLEMENTED/LIVE_VERIFIED`（產品事實）嚴格分開。
- Coordinator 更新 Issue 討論與此 Git 版決策紀錄；此文件本身不要求啟動 Codex、Claude、AGY 或 Company LLM，不代表新增實作授權。
