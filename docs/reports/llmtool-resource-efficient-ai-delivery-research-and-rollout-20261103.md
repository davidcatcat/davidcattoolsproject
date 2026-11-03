# llmtool — 資源感知、證據驅動 AI 自主開發：討論總結、技術調研與分階段上線報告

> **文件性質：** Owner 需求與技術規劃彙整／架構評估／上線方案（PROPOSAL；非執行授權、非正式 Reviewer verdict）
>
> **日期：** 2026-11-03（Asia/Taipei）  
> **Repository：** davidcatcat/davidcattoolsproject  
> **作者角色：** ChatGPT Coordinator，依 Owner 當日完整討論整理；非獨立安全審查  
> **證據基準：** 2026-11-03 讀取 GitHub main 的 ROADMAP、PM Continuation、Worker Dispatch、現有報告與 Issues/PRs；歷史數據保留原始量測日期和限制。未執行本機程式、沒有量測最新 Token/CI、沒有啟動 Worker。  
> **建議狀態：** 先提供 Claude PM 與獨立 Reviewer 研究／挑戰；不得把本報告解讀成更改角色權限、CI 必要檢查、Owner approval、Git 寫入範圍或自動付費 fallback 的授權。

---

## 0. Executive Summary — 先回答 Owner 真正關心的事

### 0.1 一句話目標

**在不降低必要 Review、CI、QA、安全和授權品質的前提下，減少每個「成功交付且通過驗收的開發任務」所消耗的高階 AI Token、CI Runner 運算、等待時間和人工催促次數，把節省的資源轉移到真正的產品開發。**

這不是另一個 llmtool 產品定位，也不是為了讓五種 AI 每輪全部出場，更不是增加一套新的流程管理平台。

### 0.2 為什麼需要這份報告

Owner 的原始產品需求一直是：**提交產品規劃書 → AI PM 自主管理 Roadmap／Developer → 自動 Review／CI／QA／修復 → 交付產品由 Owner 驗收。** Owner 不應日常擔任排程器、PM、CI operator 或 prompt 搬運工。

真正造成不滿的是：開發過程反覆 Review、CI 重跑、Reviewer/Runner 等待與流程協調耗用資源；Claude/Codex 額度寶貴，於是才尋找公司 LLM、OpenCode 免費模型與 AGY 分擔工作。**原先建議的「機器原生」架構，若只是自動執行同樣次數的 Review 和 CI，無法解決這個核心問題。**

正確方案必須同時研究：
1. **避免工作**：不做無效預審、不重複整理同一份 Context、不重跑已證明可重用的計算。
2. **選擇資源**：在品質、權限、可用性和總成本約束下，選最適合的 Developer／分析模型／Reviewer。
3. **選擇時間與順序**：決定何時測試、何時審查、何時並行；不讓關鍵路徑被無效等待拖慢。
4. **保留證據**：任何節省都不得冒用舊 Commit 的 Review PASS 或 GitHub Required Check。
5. **驗證結果**：以成功交付任務的端到端總成本為單位，證明節省；不以「少跑多少次 Review」單獨當成功。

### 0.3 目前建議決議

- **保留 llmtool 現有 A11b／A11c、TaskStore、Task Advance、D6、Worker Dispatch、Review、CI 核心架構。** 優先補既有缺口，**不另建第二套 Orchestrator／ai-loop／長駐 daemon**。
- **正在進行的 PR／工作不因本研究而暫停**；參照既有 #714 Mac CI 最佳化、#120 A11c 協作、#97 A11b 授權、#763 PM 派工缺陷，避免重複開發。
- 第一階段為唯讀成本稽核和 **Shadow Mode**，尚不修改正式派工、Review 或 Merge Gate。
- 新技術採納採 **增量、可回退、以數據為準**；無法證明效益則不要引入。
- Owner 尚未授權新的公司 LLM 真實 Repo 寫入、未授權付費 fallback、未授權 F2 廣泛變更；此報告亦不創造授權。

---

## 1. 討論脈絡及需求演進（保留最初思路）

| 討論階段 | Owner 追問／修正 | 真正的結論 |
| --- | --- | --- |
| 人類 AI 團隊式流程 | Review／CI 太多，先分析優點與缺點 | 品質、跨模型獨立審查、可追溯性有價值；冗餘與等待要處理 |
| 「不能犧牲優點」 | 消除缺點，不可降低品質 | 安全與必要驗證是**硬約束**，不是可用 Token 交換的目標 |
| 「不要用人的思路」 | 何必讓 PM 像主管一樣反覆交接 | Task/Evidence Graph、事件、確定性程式、資源調度比口語交接更適合機器 |
| 「這跟原始需求差不多」 | 新架構似乎只是在重新描述原目標 | **原始需求不變**；要比較的是執行方式帶來的可量化差異 |
| Owner 指出真正痛點 | Review／CI 頻繁、花 Token、等太久，才找公司／免費 LLM | **單純流程自動化不足**，必須同時做成本與增量運算最佳化 |
| 機器原生與文獻研究 | 哪些新 AI 技術真能省資源 | Stripe Blueprints、模型路由、增量運算、證據依賴、成本觀測更直接；Symphony 偏調度 |
| AI 角色整合 | Claude／Codex／OpenCode／AGY／公司 LLM 怎麼合作 | 共用任務／證據介面，由能力、權限、成本、風險選路由；不是固定五段流水線 |
| 現有工作如何處理 | 不希望重新設計又拖慢上線 | **雙軌但不重複**：既有工作照走；研究先唯讀、再影子、再小範圍上線 |
| 本報告請求 | 將思路、規劃、上線方案放進 Git 報告 | 本文件是持久化決策輸入；**不直接等同採納或自動執行** |

### 1.1 要避免的誤解

- 「CI 自動化」≠「CI 總時間／總運算降低」。
- 「更多平行 Agent」≠「更省 Token」；可能反而燒更多額度。
- 「免費模型」≠「成功交付成本最低」；失敗修復與強模型驗收要算進去。
- 「證據重用」≠「舊 SHA 的正式 Review／Required Check 可直接移植」。
- 「已授權 Worker」≠「目前可連線、可執行特定任務、可外傳資料」。
- 「AI PM 不再追逐流程」≠「取消 AI PM 的產品規劃和判斷功能」。
- 「Shadow Mode 推薦某模型」≠「取得新的實際執行授權」。

---

## 2. Repo 現況與已知量測（FACT／INFERENCE／UNKNOWN 分開）

### 2.1 可觀察實作與限制（FACT）

- [ROADMAP](../../tools/llmtool/ROADMAP.md)：A0–A10 已完成；**A11b 執行、授權、恢復仍在進行，F2 通用真實 Repo mutation 未授權**；**A11c 真實協作／完整 M2 尚未完成驗證**；A3.5 TUI 在增量改進；A13 分發規劃在後。
- [PM Continuation](../../skills/ai-team-delivery/pm-continuation.md)：Developer PASS、Reviewer FAIL/PASS、CI FAIL、BLOCKED 都有 PM 必須做的下一決策；Developer 完成後須取得 EC2 remote-test receipt，然後正式 Reviewer，再經必要 exact-head CI/merge gate。
- [task_store.py](../../tools/llmtool/llmtool/task_store.py)：已有持久化 Task／事件與證據欄位；它**不是**通用執行佇列或完整授權 ledger。
- [task_advance.py](../../tools/llmtool/llmtool/task_advance.py)：已有逐 Task 推進 Review／Closure 及合併檢查路徑。
- [worker_dispatch.py](../../tools/llmtool/llmtool/worker_dispatch.py)：已有拒絕優先的確定性派工政策；**不能為了成本最佳化繞過**。
- [AI team roster](../architecture/ai-team-roster.md)：兩台 Mac Runner 在**同一台實體 Mac**；EC2 Linux CI 是測試執行資源，不代表免費 AI 推論，也不構成 SSH Developer 授權。

### 2.2 已有成本與 CI 數據（FACT，歷史樣本而非最新全體值）

| 指標 | 可核對結果 | 限制 |
| --- | --- | --- |
| Mac CI 排隊中位數 | 8.1 → 0.5 分鐘 | 2026-10-27 報告中的前／後樣本；改善原因未被因果證明 |
| Mac Job 執行中位數 | 22.0 → 23.1 分鐘 | 後組 n=9；**排隊改善不代表執行縮短** |
| Mac 選擇 FULL | 14 個 PR 中 13 個 FULL | 先前選測效率低的觀察，不可外推所有變更 |
| EC2 remote-test Token | 同日粗分組約少 15–20% prompt tokens | n=6／10、小樣本且任務不等價，**不能宣稱已證明節省** |
| Mac 資源 | 兩個自架 Runner 共用一台資源有限的 Mac | Runner 併行可能爭搶 CPU/RAM，而非兩倍吞吐 |

原始資料：[EC2/Mac CI/company-gw status 2026-10-27](ec2-mac-ci-company-gw-status-20261027.md)、[Issue #714](https://github.com/davidcatcat/davidcattoolsproject/issues/714)、[Full Repo Technical Audit 2026-10-28](full-repository-technical-audit-20261028.md)。

**UNKNOWN：** 最近 30 個 PR 的正式 Reviewer 呼叫成本／次數、精確 CI Runner 分鐘、最近失敗成因分布、PM Token 與 Developer Token 的完整端到端關聯、五家模型當日即時額度，以及新方案的實際節省比例。不可拿論文節省率代填。

### 2.3 2026-11-03 核對的正在進行工作（GitHub snapshot）

- **Open PR #865**：TUI replay header 與 interrupted-turn UX；走原本必要的 Mac 實測／Owner UI 驗收。
- **Open PR #867**：A11b Review Handoff ledger recovery 修復；阻塞因素與環境測試相關。
- **Open PR #874**：為 #867 記錄已指定的 Claude Developer Task；不能視為 #867 已修好。
- **Issue #97**：F2 真實 Repo mutation 與 approval issuer 尚在權限設計／修補路線。
- **Issue #120**：A11c Agent 狀態協調與公司 LLM 有限 triage。
- **Issue #714 / #715–#717**：既有 Mac CI Impact Selection／Mac-native 覆蓋／Runner 爭用優化，不可另做平行 CI 系統。
- **Issue #763**：PM sweep 零派工與狀態未紀錄，屬自主交付連續性缺陷。
- **Issue #785**：AGENTS／Skills 多份設定與規則矛盾，應先解決權限與安全一致性。
- **Issue #396**：A13 使用者分發／安裝，原有順序保留。

**不變更這些 PR／Issue 的授權或目前狀態；PR Open 也不代表 Worker 當下仍在運行。**

---

## 3. 現行架構 vs 提案架構（不是新產品）

### 3.1 現行：由 PM 理解大部分結果並協調工作

~~~mermaid
flowchart TD
    A["Owner 產品規劃與授權"] --> B["Claude PM 規劃／拆解／派工"]
    B --> C["Worker Dispatch Policy / D6"]
    C --> D["Codex / OpenCode / AGY / 授權 Claude / 公司 gw"]
    D --> E["Commit + EC2 Remote Test Receipt"]
    E --> F["PM 判斷並安排跨模型正式 Review"]
    F --> G{"Review Verdict"}
    G -->|FAIL| H["PM 彙整 findings、安排修復"]
    H --> D
    G -->|PASS| I["PM 安排必要 Linux/Mac CI"]
    I --> J{"CI / QA Gate"}
    J -->|FAIL| H
    J -->|PASS| K["Exact-head / Merge Policy"]
    K --> L["Owner 產品驗收（適用時）"]
    B -.-> M["定期 sweep / Status / Issue / PR 追蹤"]
~~~

現行也有部分確定性程式控制，**不是全部依賴 PM 的口語推理**；但固定交接、資源選擇與狀態解讀仍有可減少的 LLM 工作。

### 3.2 提案：PM 只管真正需要推理的決策；機器管理執行與證據

~~~mermaid
flowchart TD
    O["Owner：規劃書、產品決策、驗收"] --> PM["Claude PM：架構、需求拆解、例外判斷"]
    PM --> T["既有 TaskStore + Task Contract"]
    T --> S["Resource-aware Scheduler（現有 runtime 增量擴充）"]
    POL["現有 Policy / D6 / Trust / Budget / Data Egress"] --> S
    S --> W["Worker Adapters：Codex / OpenCode / AGY / 限定 Claude / 公司 gw"]
    W --> EV["事件／實測用量／版本化產物"]
    EV --> VE["Evidence / Change-impact Evaluator"]
    VE --> FAST["快速確定性檢查／EC2 Receipt"]
    FAST --> REV["正式獨立 Reviewer（維持原門檻）"]
    FAST --> CI["必要 Linux / Mac CI（可策略性併行）"]
    REV --> G{"有效 Evidence + 權限 + 必要 Gate"}
    CI --> G
    G -->|FAIL / BLOCKED| R["受限修復／隔離／安全升級"]
    R --> S
    G -->|PASS| MERGE["既有安全整合／Merge Gate"]
    MERGE --> O
    VE -.-> TELE["成本與耗時 Metrics：Token、Runner-min、等待、重試"]
    TELE -.-> S
~~~

此圖是**設計草案，不是目前已完成的 runtime**。最後合併仍受現行 Policy、SHA、Required Checks、Owner approval 管轄。路由器不能因節省成本自行提升權限或改寫審查標準。

### 3.3 實質改變應該落在哪裡

| 痛點 | 純「流程自動化」能否解決 | 資源感知／證據驅動再增加什麼 |
| --- | --- | --- |
| 太多 Review Token | 不能保證 | 避免重複 Context／重複 advisory；在不影響正式 gate 下做增量準備 |
| 太多 CI Runner 分鐘 | 不能保證 | Impact Selection、依賴快取、作廢舊工作、早期偵測已知錯 |
| 等待 Reviewer／CI | 可減少交接等待 | 依預期失敗率、風險與成本動態選擇順序或併行 |
| PM 重複查進度 | 可以 | 結構化事件／確定性進度，減少 LLM 輸入 Context |
| 免費模型反而增加修復成本 | 不能 | 成功交付總成本的路由比較／失敗升級與停損 |
| 一個 Worker BLOCKED 全部停住 | 部分可以 | 任務依賴圖、資源與權限約束下續跑其他工作 |

---

## 4. 五種 AI：單一 Task／Result／Evidence 介面，不是固定五段流水線

### 4.1 當前角色與權限（以 main worker-dispatch.json 為準）

| Worker 路線 | 2026-11-03 靜態授權狀態 | 提案任務定位（不能取代實測） |
| --- | --- | --- |
| Claude PM | 現有 PM 角色 | 產品規劃、需求拆解、真正不確定性與 Owner 決策整合 |
| codex-developer | ALLOWED | 主力／困難程式開發、經驗證的複雜修復 |
| claude-developer | OWNER_ASSIGNMENT_ONLY | **僅已明確授權 Task IDs**；不能被 Scheduler 自由選派 |
| opencode-developer | ALLOWED（免費模型路線；不得付費 fallback） | 合格的一般開發／修復，先評估完成率與 Reviewer 成本 |
| agy | ALLOWED（無自動付費 fallback／未授權重試） | 其他合格 Developer／分工與平行任務 |
| company-gw | ALLOWED，受限提案 | 經資料許可的小型低風險 Patch 提案、bounded triage，不得自行 shell／Git 寫入 |
| company-gw-agent | **僅 synthetic-only 授權範圍** | 真實 Repo 寫入與跨越測試權限仍不允許 |
| claude-reviewer | ALLOWED | 符合家族隔離的正式唯讀獨立審查 |
| codex-reviewer | ALLOWED | 符合家族隔離的正式唯讀獨立審查 |

來源：[Worker Dispatch](../../tools/llmtool/worker-dispatch.json)、[AI Team Roster](../architecture/ai-team-roster.md)、[Company LLM Delegation](../../skills/company-llm-delegation/SKILL.md)。

**重要：** ALLOWED 不是模型連得上、符合任務品質或有足夠額度的證明；必須逐任務確認正式權限、真實可用性、隱私、D6 單 Writer、獨立 Reviewer 配對和預算。

### 4.2 Integration Contract（設計提案；先用既有介面，不急著引入 MCP/A2A）

~~~json
{
  "task_id": "<existing task-id>",
  "task_class": "bounded_bugfix",
  "contract_digest": "<sha256>",
  "repo_base_sha": "<commit>",
  "allowed_paths": ["tools/llmtool/..."],
  "candidate_routes": ["opencode-developer", "codex-developer"],
  "required_review": "independent_cross_family",
  "required_checks": ["linux", "mac_when_applicable"],
  "policy_ref": "<trusted policy digest>",
  "budget": {"max_attempts": 2, "max_paid_fallback": 0},
  "privacy": {"egress_scope": "approved_only"}
}
~~~

此 JSON **僅是 Schema 示意**，不是正式派工 Payload、也不應直接複製上線。實作前需核對既有 Task Contract／TaskStore schema、不可繞過的每個權限驗證點，並避免第二份重複角色設定。

Worker 產出的不是只給 PM 的長篇敘述，而是結構化的：Task ID、實際模型／Account、Session、HEAD SHA、修改範圍、耗時、Provider 回報 Usage、CI/Review 收據、未解 Findings、原因碼。模型的自述不等於可信系統證據。

### 4.3 Resource-aware 路由評分

僅在**已授權、可用、品質達標**的候選集合中選擇。候選方案成本至少計入：

Total Cost per Accepted Task = Developer 模型用量 + PM／Reviewer／Debug 模型用量 + 修復重試成本 + CI Runner-minutes + 交接／等待造成的時間成本（各項單位個別報告，若貨幣化需可核對費率）。

- CLI 訂閱額度 ≠ API 單次計費；不可直接把未知 Token 換成美元。
- 公司 LLM 若沒有用量資料，應標 UNKNOWN 而非免費或零成本。
- 使用免費路線但失敗／重試／強模型審查增加，可能比直接使用強模型更貴。
- 路由可按 task_class、測試覆蓋、歷史成功率、實際可用性、完整成本決策；低樣本時保守回退現行策略。
- **No budget laundering：** 不得因路由器估算更便宜就開啟付費 fallback 或額外資料外傳。
- 沒有可行合格的 Reviewer，就 BLOCK 該交付，不將 Developer 自審冒充正式 Reviewer。

---

## 5. 新 AI／工程技術調研：有證據的用途與邊界

| 技術／文獻 | 類型 | 可借鑑機制 | 能處理什麼痛點 | 採用建議 |
| --- | --- | --- | --- | --- |
| Stripe Minions Blueprints（2026） | 企業實作 | 確定性程式節點與 Agent 節點交織，前移快速 Lint／測試 | PM Token、早期發現錯誤、無效 CI | **P0 參考模式，不移植整套 harness** |
| RouteLLM（ICLR 2025） | 論文 | 品質約束下強／弱模型路由 | 省強模型 Token | **P0 方法，先影子比較** |
| FrugalGPT（2023） | 論文 | Cascade／成本意識模型升級 | 低成本先行、需要時升級 | **P1 參考；嚴格遵守授權／停損** |
| Agentless（2024） | 論文 | 簡化定位／修復／驗證，避免過度 Agent 化 | 減少無效步驟與 Context | **P0 簡化原則** |
| OpenAI Symphony（2026） | 規格／實作 | 任務持續調度、工作區隔離、重試與可觀測性 | 交接等待、派工空轉 | **P1 對照 A11c，不新增 daemon** |
| Anthropic Building Effective Agents（2024） | 工程方法 | Workflow 與 Agent 區分，routing／orchestrator／evaluator | 避免所有步驟都呼叫模型 | **P0 設計準則** |
| Bazel Action Cache / Dagger Cache | 工程實作 | 輸入／工具／環境版本綁定的增量計算 | CI 運算重複 | **P0 先套用思想到 #714，不導入整包依賴** |
| Temporal Durable Execution | 工程實作 | 持久化事件、可恢復流程、受控重試 | 故障從頭做、無效重送 | **P1 參考恢復語意，先重用 TaskStore** |
| OpenTelemetry GenAI conventions | 標準／演進中 | Usage／Cache／Reasoning／Latency 的欄位語意 | 成本量測、模型比較 | **P0 量測欄位參考** |
| SWE-bench（ICLR 2024） | 評測 | 真實 Issue→Patch 的驗證方法 | 路由品質與成功率基準 | **P1 自建小型 repo Benchmark** |
| OPA / Policy as Code | 工程實作 | 將 Policy 判斷與執行分離 | 不用 PM 記憶權限規則 | **P1 重用現有 worker_dispatch；目前不必裝 OPA** |
| AFlow（ICLR 2025）／ADAS | 探索研究 | 搜尋／自動設計 Agent 流程 | 可能找出更省的步驟次序 | **P2 離線研究，禁止直接改正式安全 gate** |
| MCP / A2A | 介面標準 | 跨工具／跨獨立 Agent 系統互通 | 擴充性 | **P2 暫不需要，先保留既有 CLI Adapters** |

**不可外推的結果：** Stripe、Symphony、RouteLLM、FrugalGPT 在原場景的生產力或成本數據不等於 llmtool 可以達到的效果；本案必須用自己的歷史工作包 A/B 驗證。

### 5.1 可核對的外部來源

1. Stripe Part 1 — https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents （2026-02-09；快速 lint、受限完整 CI 輪次、實際 PR 工作流）。
2. Stripe Part 2 — https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2 （2026-02-19；Blueprints 混合確定性與 Agent 節點）。
3. OpenAI Symphony 介紹 — https://openai.com/index/open-source-codex-orchestration-symphony/ （2026-04-27）。
4. OpenAI Symphony draft SPEC — https://github.com/openai/symphony/blob/main/SPEC.md （規格草案、非 llmtool 的產品驗收標準）。
5. Anthropic — https://www.anthropic.com/research/building-effective-agents （2024；簡單可組合模式與成本／複雜度取捨）。
6. RouteLLM — https://proceedings.iclr.cc/paper_files/paper/2025/hash/5503a7c69d48a2f86fc00b3dc09de686-Abstract-Conference.html 。
7. FrugalGPT — https://arxiv.org/abs/2305.05176 。
8. Agentless — https://arxiv.org/abs/2407.01489 。
9. AFlow — https://proceedings.iclr.cc/paper_files/paper/2025/hash/5492ecbce4439401798dcd2c90be94cd-Abstract-Conference.html 。
10. SWE-bench — https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html 。
11. Bazel remote cache — https://bazel.build/remote/caching 。
12. Dagger — https://docs.dagger.io/ 。
13. Temporal — https://docs.temporal.io/tasks 。
14. OPA — https://www.openpolicyagent.org/docs 。
15. OpenTelemetry GenAI Token Metrics — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-token-metrics.md （Status: Development；未必所有 CLI 都可回報每種指標）。

---

## 6. 三個節省資源的關鍵機制

### 6.1 Review 最佳化：去除重複負擔，不取消正式獨立審查

- 將 Lint、格式、已知 schema 問題、Path／SHA／Review 資格等**確定性規則**放在模型呼叫前處理。
- PM／Reviewer 採用 **bounded context packet**：需求契約、變更清單、完整關鍵 diff、真實測試證據、重要上下游與前輪 findings 的固定摘要；審查者仍可要求回到原始證據，不可只看二手摘要。
- 維持 Developer 自審、跨模型／跨 Session 的正式獨立 Reviewer，安全敏感變更額外專業覆核仍依原規則。
- 修復批次收斂後再重新正式審查當前 HEAD；新 HEAD 必須取得**有效正式 verdict**。局部分析可以協助 reviewer 聚焦，但不等於直接繼承原 HEAD verdict。
- 對每次 advisory/pre-review 的增量有效發現率、引發修復比例、Token 成本做實測；是否更改硬性 Gate 需要另外 Owner 決策／安全驗證。
- Reviewer 門檻相同時比較每成功交付 PR 的 Reviewer Token／重新提交輪數，而不是只比較呼叫次數。

### 6.2 CI 最佳化：Incremental Computation + 保守回退

- **重用 #714／#715–#717**，先讓小型 Python／config 改動不要無故全部選 FULL。
- 嚴格區分：**依賴／編譯產物快取** vs **非正式測試結果重用** vs **正式 GitHub Required Check**。三者不能混為一談。
- 每個可重用計算至少綁定：Commit／內容、測試定義、工具與依賴版本、OS／架構、選測規則版本、輸入配置，以及可信產物雜湊。
- Mac-native 專屬測試仍必須在 Mac 上驗證；Linux PASS 不代表 Mac PASS。
- 對安全／行程控制／Mac native／CI workflow 等高風險變更，按照現行規則做充分覆蓋；未知的 Impact Selection 一律 fail closed 或安全回退 FULL。
- Merge Train 後 main Mac FULL safety net 按既有授權執行，**不得擅自改成自動長駐週期任務**。
- 強制 CI 失敗不可「模擬 PASS」或以過去 SHA 結果洗過；正式 exact-head Required Checks 必須繼續滿足。
- 先用現有測試選擇器／workflow 改善，不先導入 Bazel 或 Dagger 套件與額外 infra。

### 6.3 調度最佳化：先最省資源還是先最快交付？

兩種策略都要有：

**A. 節省運算優先：** 便宜的確定性快速檢查 → 正式 Review → Full CI。適合 Reviewer 高機率要求大改，避免先浪費大套 CI。

**B. 降低牆鐘時間優先：** 確定性前置檢查通過、候選 HEAD 固定後，符合政策者可並行 Review 與必要 CI。適合歷史通過率高、CI 不擁塞、預算允許的任務。

排程器要估計：
- 預期返工機率、每輪修復成本；
- CI Runner 排隊與實際運算時間；
- Reviewer 等待／額度與被重新審查機率；
- 同實體 Mac 的 CPU/RAM 爭用；
- Owner 對速度與資源的當次優先設定。

**禁止以估計最省為由自行改變硬性 Gate。** 並行時若新 HEAD 產生，舊 HEAD 的正式證據不得用于新 HEAD Merge；舊 run 只有在政策允許且安全確認已無用途時才能取消。

---

## 7. Evidence Graph 與 Resource Scheduler：最小設計草案

此處的 Evidence Graph 是**提案的資料／相依模型，不是已通過驗證的現成 llmtool 功能**。

### 7.1 最小資料模型

| 物件 | 核心欄位 | 用途 |
| --- | --- | --- |
| Task | task_id、task_class、contract_digest、allowed_paths、base_sha | 任務與授權範圍 |
| Artifact | repo、commit_sha、changed_paths、parents、toolchain | 避免輸入版本混淆 |
| Evidence | evidence_id、kind、producer、head_sha、input_digest、policy_version、status、timestamp、source_uri | 測試／審查／CI 的來源與新鮮度 |
| Event | event_id、task_id、sequence、causation_id、idempotency_key、result | 一次性狀態交接與恢復 |
| Cost | provider、actual_model、role、usage_observed、cache_tokens、duration、runner_minutes、unknown_reason | 量測與比對 |
| Policy | authorized_actor、route、max_cost、data_egress、release_gate | 不可被預算優化器覆寫 |

### 7.2 失效與重用判定

- 程式碼、測試定義、政策、工具鏈、環境或相關輸入改變時，重算受影響節點。
- 同一 HEAD 下的相同 input digest 可考慮計算重用，但要驗證來源可信與環境等效；未經證明只能重新執行。
- 正式 Review 必須對應最終要求的 HEAD、合法 Reviewer 和當次有效契約。
- GitHub required check 必須以 GitHub／現有 merge gate 可接受的正常 check-run 驗證，不得因本地 Evidence PASS 而捏造綠燈。
- 對 UNKNOWN、缺少證據、已撤銷 Reviewer、Policy 版本改變，一律保守地要求再驗證。
- 對 Git 寫入／模型付費呼叫等非冪等副作用，使用 claim + receipt + recovery；EFFECT_UNKNOWN **不能盲目自動重送**。

### 7.3 Scheduler 最小決策介面（概念，不是即用程式）

~~~text
Inputs: Authorized Task + Current policy digest + Live route capability
        + Exact artifact/evidence status + Historical quality/cost + Budget
Hard filters: Writer lease / sandbox / source privacy / model allowlist
              / cross-family Reviewer / required CI / owner authority
Select: among permitted actions, lowest expected total cost
        subject to quality and delivery time constraint
Outputs: ALLOWED ACTION with idempotency key | BLOCKED with exact reason
         | OWNER_DECISION_REQUIRED
Feedback: actual result / actual usage / latency / findings / regression
~~~

Owner 設定應使用**單一事實來源**，不可把 claude2、各 Worker 角色、費用限制再散寫進多份 Skill；與 #785 的治理修復同步校正，避免本案增加配置重複。

---

## 8. 現有工作維持策略：不停止、不重複、不搶寫入

| 現有工作 | 本提案怎麼對接 | 禁止做什麼 |
| --- | --- | --- |
| A11b / #97 | 保留安全授權與恢復主線；本案只能增加觀測／設計輸入 | 不繞過 F2 尚未授權 |
| A11c / #120 | 將資源感知調度、證據／事件演算法**評估為其增量功能** | 不建立平行 PM 或第二個 ai-loop |
| #714 / #715–717 | 直接沿用已授權 Mac CI Impact Optimization | 不另外再做第二套 selector |
| #763 | 自動派工的零派與無原因紀錄屬 P0；以狀態機改善可稽核性 | 不透過「排程有啟動」冒充有實際派工 |
| #785 | 修正多份 Skills／Owner role／模型設定矛盾 | 不新增互相打架的設定檔 |
| #865 / #867 / #874 | 走現有 D6、Review、CI 和安全流程完成；可作**唯讀歷史測試樣本** | 不跨越現行 Writer lease 修改活躍分支 |
| A3.5 TUI | 未來僅展示現有真實 Core 的成本／狀態／Approval 資訊 | 不在 TUI 再造 Scheduler／TaskStore |
| A13 | 維持已排優先序，等核心通過真正 E2E 驗收 | 不為本研究提前開大型部署專案 |

如正在工作的 Task 已被阻塞，PM 按現有 Continuation Contract 繼續其他合法、獨立工作，**不能讓新研究成為停工理由**。

---

## 9. 分階段落地與上線（Stage Gate；每階段均可停止）

### Phase 0 — 基準盤點與失效模式稽核（READ-ONLY，P0）

**輸入：** 最近 30 個具代表性的已完成／未完成 PR、TaskStore 可合法取得的彙總、正式 Reviewer verdict／CI metadata，既有 #714 報告。

**交付：**
- 每 PR：Developer／Reviewer／PM call counts、可觀測 Token、未知用量原因、完整耗時、Runner-minutes、排隊 vs 執行、失敗重跑輪數、首次通過率、有效 Review finding。
- 同一 SHA 反覆執行的**候選冗餘清單**，逐筆附驗證條件；不是看到兩個 job 同名就宣稱重複。
- 可由 Python 處理的例行 PM 推理清單，標示每項改成程式是否會碰到權限／安全分界。
- 問題優先矩陣：實測成本 × 可節省比例 × 導入風險 × 開發成本。
- **不得新增 CI 跑次、不得呼叫高階模型只為收集現有 Metrics。**

**Gate：** 有足夠證據與樣本大小；UNKNOWN 必須如實呈現。若不足，先補最低限度觀測，不虛構節省率。

### Phase 1 — 既有低風險最佳化（P0）

**對接 #714、#763、#785：**
- 修正 CI 變更分類與安全選測；保留 exact-head Linux/Mac Required Check。
- PM sweep／Task Advance 的確定性下一步提示與零派警示，在既有 runtime 建立可核對紀錄。
- 用量紀錄標準化：不同 Provider 的 input／output／cache／reasoning token 依能力記錄，缺少就 UNKNOWN；Runner queue/execution 分開。
- Context Packet 結構化、重用既有公司 LLM delegation skill，避免整份長 Log 重複轉交強模型。

**Gate：** 功能／安全回歸全部通過；同等品質下小 PR 的 CI P50／P90 與 token 資料有可驗證改善，無則修正或停止。

### Phase 2 — Resource-aware Shadow Mode（唯讀建議，不派工）

對過往可重現工作包輸出：
- Eligible Workers（根據既有授權與可用性、品質成績）。
- 建議 Developer／分析模型／Reviewer 搭配。
- 預估 Token、期望返工成本、CI 成本、等待時間和 Policy 結果。
- 與實際既有路線的反事實比對及不確定性標記。

**不得：** 新增未授權 Worker、在 live Repo 執行提案、使用未授權公司 LLM 資料、付費 fallback、發布 PR 或 Merge。

**Gate：** Shadow 建議能以歷史資料回測；對失敗案例不隱藏；並證明在有足夠資料的類型上不是總比現行方案差。

### Phase 3 — 小範圍、可回退的正式 Pilot（需另循現有授權／安全審查）

- 對**新派工的合格、低風險、範圍小、可復原** Task，允許 Scheduler 從現有**已授權且合格**的 Worker 中選路線。
- 保留完整正式 Reviewer、remote-test receipt、必要 Linux/Mac CI、Owner approvals、D6 隔離和 Merge Gate。
- 公司 LLM 只處理當時已授權的低風險資料／任務；公司 gw-agent synthetic-only 不擴權。
- 每個 Pilot 有獨立 Task ID、事前預註冊成功條件、成本上限、重試上限、stop/rollback。
- 未取得安全審查或 Owner 要求的新權限時，只能 BLOCK／維持現行流程。

**Gate：** 端到端通過，成本與品質達標，且沒有未授權操作、漏檢、重送副作用或過期證據。

### Phase 4 — A11c 端到端整合與產品驗收

真正做到：產品規劃→PM→受控 Developer→必要 QA/Review/CI→修復→合法 PR／安全整合→Owner 驗收，中途無需 Owner 重貼提示或監督例行流程。

**Gate：** 在同等條件下，比現有流程有更少總成本與等待、無品質倒退；高風險用例與故障注入通過後，才把已驗證策略設為預設。

### Phase 5 — 逐步上線／回退

- 功能旗標（預設 OFF）→ 小樣本啟用 → 對照評估 → 有證據才擴大。
- 任何 Reviewer Gate、Owner Authority、來源隱私或 CI Required Check 不相容，立即退回既有調度。
- Rollback **只回退排程策略**，不覆蓋已完成的資料；TaskStore 和稽核證據保留。
- 不需要新增常駐 Mac 服務；沿用現有受控啟動／停止語意。
- A13 對外發行不能先於已宣稱能力的實際跨使用者驗證。

---

## 10. 驗收與量測：目標、公式、品質護欄

### 10.1 核心指標

| 指標 | 定義／統計口徑 |
| --- | --- |
| Strong-model usage per accepted task | Claude/Codex 等高成本路線的可觀測 Token／用量，含 Developer、PM、Reviewer、修復／Debug；未知另列 |
| End-to-end model total | 所有五種路線實際使用量，依模型／訂閱／API 的不同計價與額度單獨呈現，不能把 token 數直接相加當貨幣成本 |
| CI Runner-minutes | 各跑次 duration × 實際執行 Runner 數，與 queue wait 分開；避免兩台 Mac 自架 runner 誤算成兩台主機 |
| Review／CI lead time | Developer candidate 固定至全部必要 Gate 完成的牆鐘時間，P50／P90 |
| Repair cost | 每成功交付任務的修復輪數、模型 Token、重新跑 CI 次數與時間 |
| First-pass acceptance | 第一候選 HEAD 達到全部必要 Gate 的比例 |
| PM process overhead | 例行狀態整理／交接消耗的模型用量與需 Owner 人工介入次數 |
| Escaped defects／policy violations | 合併後回歸、錯誤接受、越權嘗試、錯誤復原、假綠燈；須逐案追溯 |

### 10.2 建議試驗目標（NOT VERIFIED；不承諾達成）

- 每個成功交付任務的強模型用量 **-30%**；
- CI Runner-minutes **-30%**；
- Review／CI lead time **-40%**；
- PM 例行流程推理用量 **-50%**；
- 必要獨立正式 Review 與 Required Checks **100% 保留**；
- **0** 未授權操作、0 假通過、0 過期證據冒用、0 已知被省略的必要測試。

百分比是**事前挑戰指標**，不是已測得成效。若品質降低即使節省 90% Token 也不算通過；若未達 30% 但證明更有效率，也應如實呈現再決策。

### 10.3 實驗設計防止假節省

- 同類型 Task 分層：docs/config、普通 Bug、跨平台變更、Security、TUI native；**不同風險等級不直接混算**。
- 優先採歷史 replay／matched pairs；實際 A/B 須有合法同等 sandbox／權限。不能偷偷讓兩個 Agent 同時寫同一個 D6。
- 同時計入首次失敗、修復和最終驗收成本；不能只拿「免費 Developer 首輪成本」對比「Codex 最終全部成本」。
- 模型版本、訂閱／API 用量口徑、CI Runner 負載、快取命中、樣本數與失敗/取消都記錄。
- 報告 P50、P90、分布和可信限制，不只報平均；有選擇偏差就註明。
- 所有縮小測試範圍的策略都需以故障注入／影響分析證明不漏掉既有已知回歸。

---

## 11. 上線前不可犧牲的安全／品質不變式

1. **Reviewer 獨立性：** Developer 不能自審變正式 PASS；跨模型家族、分 Session、唯讀與確切 HEAD 規則照舊。
2. **D6 單 Writer／Task scope：** 不允許 Scheduler 自行擴大可寫路徑、共用 Writer lease 或重放已知不安全操作。
3. **Owner authority：** 廣泛 Repo mutation、實質外傳範圍變更、付費升級、角色特例、必要 Gate 修改須依現行權限路徑獲得真實授權。
4. **可信證據：** 實際 GitHub HEAD／CI check-run／Reviewer actor／runtime receipt 必須來源驗證；模型描述只是提案。
5. **Fail closed：** UNKNOWN、缺少策略、模型不可用、Policy 或 Evidence 過期，不得當成 PASS。
6. **Privacy：** 公司 VPN 連線成功不等於獲得資料外傳權限，測試 Fixture 的授權不得外推真實原始碼。
7. **Bounded retries：** 限制無效重試與 Token，停止條件由已核准 Policy 定義；遇到 EFFECT_UNKNOWN 不盲目重做。
8. **不得用速度換安全：** 可優化排程、改良選測或降低無用審查 Context，但不擅自刪除必要驗證要求。
9. **Owner UX：** TUI 只是展示現有真實資料與必要決策；不為顯示進度而產生假狀態／估測 Token 額度。
10. **可回退：** 每次優化可獨立關閉、版本化、回溯；不讓 Pilot 的行為成為未經批准的新預設。

---

## 12. 故障注入與負面測試（Minimum Safety Test Matrix）

| 用例 | 預期 |
| --- | --- |
| Reviewer 對最新 HEAD FAIL | 不得合併；有 bounded findings 與修復責任 |
| 某 HEAD 的 Review PASS，但新 commit 已產生 | 舊 verdict 失效，新 HEAD 依政策重審 |
| Linux PASS、Mac required FAIL | 不得合併；不可用 Linux 取代 Mac |
| 快取輸入不同／來源不可信／policy version 改變 | 不重用錯誤證據，保守重跑／BLOCK |
| 公司 gw unavailable／429／未授權外傳 | 不發送未批准內容、不擅自付費 fallback，隔離 Task |
| OpenCode 免費額度耗盡 | 保存進度與 Lease，其他獨立任務續跑 |
| 重送同一事件／Worker crash／半完成 Git mutation | 冪等識別；未知副作用不自動重播 |
| 兩個 Worker 想改同一路徑 | D6 Lease 與授權拒絕衝突 |
| Shadow 建議 Claude Developer，但未匹配 Owner Task ID | 派工被拒絕，保留理由 |
| 兩個 Mac Runner 同時 FULL 導致退化 | 根據測得數據調度，不盲目提高並發 |
| 更改 CI Workflow／選測器本身 | 全量／適當跨平台專項驗證，不能偷跳過 |
| 三個連續修復仍失敗 | 依既有 Debug／Budget／停止規則，不無限消耗額度 |

---

## 13. 效益、成本、風險與可行性

### 13.1 預期優勢（尚待實測）

- 將 PM 重複讀取、交接與簡單判斷移到程式，減少 LLM 管理性 Token。
- 把公司 LLM、OpenCode、AGY 用在已證明可靠的工作，保留 Claude／Codex 給困難開發與正式獨立審查。
- 提早發現低成本錯誤、減少無價值完整 CI 輪次；縮短小型 PR 的牆鐘等待。
- 讓 Job、Reviewer、CI 狀態可觀測；失敗不必由 Owner 一再催促。
- 更好的跨模型接力與可稽核性，並能看見哪種模型路由是真的省錢。

### 13.2 主要風險（需要主動防禦）

- Scheduler 錯誤把低品質模型送去高風險任務，導致修復成本更高。
- Evidence/Cache 誤判，將不存在的審查／測試當成已完成。
- 為了省等待而無差別並行，反而燒更多 Runner／Reviewer Token。
- Agent 用量難取得、帳號切換與實際模型識別不一致，製造虛假的節省率。
- 資料外傳與 Owner 權限被「模型路由」抽象悄悄繞過。
- 研究／框架過度擴張，推遲 A11c 真實 Owner 任務完成。
- 複製 Symphony／Temporal／Dagger 的完整系統使維運成本大於節省。

### 13.3 反對方案：不建議做

- **不做**「砍掉 Reviewer／取消 Required CI」的廉價提速。
- **不做**每個任務都呼叫五種 AI，或總是「免費先跑」。
- **不做**將 AFlow／ADAS 自動設計流程直接接正式授權 Gate。
- **不做**新常駐本機 daemon；Owner 不希望 Mac 開機後另有無人管理背景程序。
- **不做**為了研究就重跑全部歷史 CI、再審一遍所有 PR。
- **不做**把已經存在的 A11c／#714 拆出成互不相干的第二套產品。

---

## 14. 待 PM／獨立 Reviewer 核對的開放問題

| 問題 | 驗證方式 | 判定 |
| --- | --- | --- |
| 目前重複 Review 中，哪些真的無效？ | 最近 30 PR 逐輪有效 findings、Token、受影響 SHA | 未量測 |
| Remote Test 與正式 CI 的重合程度？ | 同 SHA／環境／test-set／artifact 的逐項比較 | 未量測 |
| Mac FULL 測試哪些可以合法改成 Impacted？ | #715–#717 的故障注入與實際跨平台覆蓋 | 進行中 |
| 公司 gw 對哪類工作真的節省總 Token？ | 合法的同題配對實驗，計入強模型審查與修復 | 不可一概而論 |
| OpenCode／AGY 的成功交付總成本？ | 同類 Task 的最終合格成本與失敗率 | 未量測 |
| 目前 Task Advance 可涵蓋哪些連續事件？ | 程式路徑與 A11c E2E Trace | 部分存在，完整仍未驗證 |
| 需不需要新增外部框架？ | 比較以現有 Python 實作與額外元件的總維護成本 | **預設不新增** |
| 使用者感知是否改善？ | 每任務 Owner 催促次數、真正交付時間、產品驗收成功率 | 未量測 |

---

## 15. PM 下一步工作包與決策出口

**建議 PM 接收本文件後：**

1. 先確認這是 **Proposal**；核對 2026-11-03 最新 main、#714、#120、#97、#763、#785 是否已產生更近期變更。
2. 指派**唯讀** sub-agent 研究過往 PR、CI、Review 和用量，不衝突現行 D6／Writer；**不為研究重啟高成本正式 Reviewer**。
3. 產出「已做／缺口／不做」Gap Matrix，重用現有 task_advance、task_store、test_selection、ci_mac_modules；禁止重複造輪子。
4. 針對每個節省方案交付可測的 **Baseline → Proposed Change → Equivalent Safety Proof → Expected Cost Effect → Rollback**。
5. 對需要 Owner 另行授權的 Gate、資料外傳、模型付費、路由變更，逐項列明 **OWNER_DECISION_REQUIRED**，不得暗中執行。
6. 僅在明確確認不增加重複工單、沒有範圍衝突後，回填現有 A11c／#714 工作包與 Roadmap；不要因報告自動建立大量新 Issue。
7. 繼續處理現有正式 PR 與 A11b／A11c 核心 E2E，不應把本報告當成新的總前置阻塞。
8. 第一個實測 Pilot 通過品質、安全、成本三組 Gate 後，再提交擴大部署方案給 Owner 裁示。

### 最終 Definition of Done（此方案，不是報告撰寫）

- **產品面：** Owner 能提交規劃→AI PM 自主進行合法開發→經必要審查、CI、QA→提交可驗收產品，沒有例行人工催促。
- **效率面：** 相同風險等級、等價任務下，成功交付單位的高階模型用量和 CI Runner 分鐘有可核對下降，Review／CI 等待不變差或確實改善。
- **品質面：** 必要正式獨立 Reviewer、exact-head CI、授權和安全覆蓋完整保留，故障注入測試沒有新漏口。
- **工程面：** 不另建重複 Orchestrator，不增加 Mac 長駐背景程序，任何新策略可回退並可觀測。
- **治理面：** 未經 Owner 批准的高風險變更絕不自動啟用；需要批准的事項集中明確陳列，不把 Owner 拉回 PM 日常流程。

> **最後的判斷：** 如果新技術只是讓 AI PM、Reviewer 和 CI「自動做一樣多的工作」，就不值得改。只有**在同等品質下，真的節省完成每個產品功能所需的總資源**，才是這次改造有價值的證據。
