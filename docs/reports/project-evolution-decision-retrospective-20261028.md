# Report B — Project Evolution & Decision Retrospective

- **日期：** 2026-10-28　**Snapshot：** `139a62c0483560be4e7a0aadd76c82652a8a2cd9`
- **審查者：** Independent Auditor（Claude Fable 5.1）。本報告與 [Report A（技術稽核）](full-repository-technical-audit-20261028.md) 共用證據索引；管理方法建議見 [Report C（Product Owner Playbook）](product-owner-playbook-ai-development-20261028.md)；精簡結論見 [Executive Decision Brief](executive-decision-brief-20261028.md)。
- **證據來源：** Git 歷史（899 commits）、GitHub PR（437）與 Issues（517）、Owner 決策留言（#120 31 則、#289 22 則、#352 12 則、#290 9 則、#304 7 則、#425 5 則，皆為 PM 代記）、`docs/meetings`、`docs/decisions`、`docs/history`、ROADMAP 124 個版本、AGENTS.md 30 個版本、`reports/` 186 份。
- **HISTORY_INCOMPLETE 聲明：** Owner 與 ChatGPT Coordinator、Codex PM、Claude PM 之間的私人對話**無法存取**。所有「Owner 決策」皆來自 PM／Coordinator 在 GitHub 或文件中的轉述（部分含 verbatim 引述）。凡未見 verbatim 引述者，本報告標為 INFERENCE 或 HISTORY_INCOMPLETE，不補故事。**Owner 在 GitHub 與 TaskStore 之外的個人操作（例如是否親手在本機試用 TUI）同樣無法觀察；凡以「紀錄中沒有」推得的 Owner 行為結論，一律標 INFERENCE（缺席證據），不當作已確認事實。**
- **版本：** v2（2026-10-28 自我校正：Owner 評估改以 FACT／INFERENCE／OPINION 逐項標示；涉及 Gate／Reviewer／Dispatch／Roadmap 的建議標 OWNER_DECISION_REQUIRED；數字型建議改為待驗證目標；新增 Post-Snapshot 註記）。
- 證據標記：**FACT**／**INFERENCE**／**COUNTERFACTUAL**／**OPINION**／**UNKNOWN**；建議標記 **OWNER_DECISION_REQUIRED**（不取代 AGENTS.md 或 Owner 決策）。

---

## 1. Executive Summary

這個專案從 2026-09-27 的「個人工具箱 + Agent skills」開始，31 天內經歷四次產品定位轉向：**公司 LLM 唯讀 CLI（09-28）→ 通用 LLM CLI 與 orchestration 基礎（09-28 Roadmap）→ llmtool 自我開發、以 Codex／Claude 為 Worker 並節省 token（10-08 PR #50）→ 使用者直接操作的通用 AI 協作工具（10-21 會議）**。每一次轉向都在前一輪基礎設施完成後發生，而不是在開始前釐清，於是累積了大量為舊定位服務的程式與文件（F1 Git 容器化、S1b fake-only、ai-loop、token benchmark 30 份報告、A7–A9）。

最關鍵的管理模式（FACT）：Owner 以「一句話決策」驅動（「開放 worktree 吧」「opencode 先停用吧」「agy 納入 llmtool 吧」），由 AI（ChatGPT／Codex／Claude）展開成數千字的契約、gate 與 Roadmap。Owner 對**方向**的判斷多數正確且誠實（堅持真實證據、拒絕假 PASS、及時關掉 Web-first），但對**規模與順序**缺乏停損：授權 AI 建設治理框架的速度，遠超過驗證框架是否被使用的速度。

結論（OPINION）：整體方向正確；最嚴重的錯誤不是任何單一技術選擇，而是「**先建框架、後定 MVP、從未由使用者角度驗收**」這個反覆出現的模式，以及由額度與 CLI 可用性驅動的角色反覆切換。

---

## 2. Original Product Vision

| 時間 | 來源（FACT） | 當時的產品定義 |
|---|---|---|
| 09-27 | 首個 commit、README「Personal AI-assisted engineering tools and reusable Agent Skills」 | 個人工具箱 + skills |
| 09-28 | `2fcfad7` llmtool MVP README：「本機唯讀 CLI，將明確提供的文字交給公司 Qwen gateway，輸出最終答案」 | 公司 LLM 唯讀 CLI |
| 09-28 | 第一版 ROADMAP（4.5 KB）：「generic, portable LLM CLI and future orchestration foundation；Provider／role／agent 三者分離」，A0–A12 全列 | 通用 CLI + 未來編排基礎 |
| 10-08 | PR #50：「llmtool itself should take over scoped Codex CLI development and independent Claude CLI review … with measurable token and cost savings」 | 自我開發的 AI 開發編排器 |
| 10-17 | #172 D-001～D-007：CLI 單一入口、公司 LLM 只是 API、PM 決定分工、Codex Integrator 兼 PM、llmtool 確定性執行 | 架構原則（仍正確） |
| 10-21 | 會議紀錄：「llmtool 本身沒有 AI 智能」、六項使用者行為、CLI 優先 → Telegram → Web → 他人電腦 | **使用者產品** |

**INFERENCE：** 09-28 的 Roadmap 已預告 A9「Agent Orchestration」與 A11「Production Workflow」，所以「編排」是早期構想；但「使用者直接操作、關閉介面後繼續、多任務」這些**產品級需求**直到第 24 天才被寫下。在此之前，llmtool 的「使用者」只有 PM AI 自己。

---

## 3. Complete Project Evolution Timeline

### 階段 0：工具箱與 nlf（09-27 ～ 10-01）

| 日期 | 需求 | 重大決策 | 技術變化 | 原因 | 結果 | 後續影響 |
|---|---|---|---|---|---|---|
| 09-27 | 建立個人工具 repo | monorepo + skills | README、skills、Claude 測試 commit | UNKNOWN（HISTORY_INCOMPLETE） | 5 commits | 之後所有產品共用一個 repo |
| 09-28 | 自然語言找檔 | nlf 以 Go、stdlib-only、單一執行檔 | A1–A8 全在 09-28～10-01 完成；三平台 CI | 可攜性（README 明示） | v0.4.0，CI 綠，之後無改動 | 證明「小而完整」的產品可行 |

### 階段 1：llmtool A0–A10（09-28 ～ 10-01）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 09-28 | 用公司 Qwen gateway 做 log／diff 分析 | Python、PyYAML 唯一依賴、唯讀 | `2fcfad7` MVP；同日 generalize provider | 公司環境、離線安裝 | A1 完成 | 技術棧固定 |
| 09-28 | 「未來編排基礎」 | A0–A12 Roadmap | — | INFERENCE：AI 提案、Owner 接受 | 野心定錨 | Roadmap 之後膨脹到 146 KB |
| 09-29～10-01 | provider 擴充、skills、chat、context packet、git intel、validator、feedback、benchmark、orchestration、retrieval | 每日 1–3 個 milestone | PR #1–#12 | 速度 | A1–A10 四天內全 DONE | **沒有使用者驗收任一 milestone**（FACT：無 QA 以外的使用紀錄） |
| 10-01 | 互動對話 | A3.5 chat（記憶體內 REPL） | `chat.py` | — | 完成 | TUI 延後到 10-19 |
| 10-01 | 遠端前端 | A12 Telegram roadmap | 文件 | — | 未實作 | 10-21 改為 Telegram → Web 順序 |

### 階段 2：診斷、品質門與公司模型節省（10-01 ～ 10-07）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 10-03 | 用 Codex Cloud 開發 | WORKFLOW.md「Cloud 開發 + Mac 驗證 gate」 | 流程文件 | Mac 資源有限 | Cloud lane 使用到 10-17 | WORKFLOW 膨脹到 145 KB |
| 10-02～10-04 | 可觀測性 | A11a 診斷（7 個子里程碑） | diagnostics_* 模組 | — | 完成 | 良好基礎，使用率中 |
| 10-04～10-05 | 安全與 QA | QG-S1 威脅模型、QG-QA1 正式 QA、B1–B7 修復 | 報告 + 修復 PR | 品質 | PASS/CLOSED | 建立「正式 gate 文化」 |
| 10-05～10-06 | 省 token | 公司 LLM dogfood、paired benchmark Round 1–3 | 30+ 份報告 | Owner 想用公司模型省錢（PR #50 轉述） | Round 1 +104%；單一路徑 −39.8%；Round 3 closed | 證明「低價模型不等於省錢」；但投入極大 |
| 10-05 | 執行與審批 | A11b.1 typed action contract | `action_contract.py` | 為 llmtool 自行執行做準備 | 資料層完成 | 後續 F2 從未授權 |
| 10-06～10-09 | llmtool 自己做 Git | A11b.2 Git hardening：F1 fixture-only Git ADD、containment supervisor | PR #43／#46／#49／#84 | 「native self-development」 | F1 DONE（私有 fixture）；F2 NOT AUTHORIZED | **最昂貴的錯誤路徑之一**（見 DEC-008） |

### 階段 3：ai-loop 與 C 路線（10-07 ～ 10-11）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 10-07 | 自動化實作與審查 | PR #45「automate implementation and PR review with mandatory Mac gates」→ CLOSED | ai-loop 控制器在 `automation/ai-loop` 分支 | 想讓 Codex／Claude 自動接力 | 未合併 main；10-09 多個 fix；10-11 後無活動 | 第二套編排器；文件至今仍寫「temporary coordinator」 |
| 10-08 | 定位再轉 | PR #50 C0–C3「cost-first native self-development MVP」 | NATIVE_SELF_DEV_MVP.md | Owner 澄清目標（PR 轉述） | 10-09 Claude review（#69）指出 C 多為 A11c 改名；#71 併回 A | 兩套編號並存至今 |
| 10-09 | Owner 不再當 prompt 快遞 | #77「take the requirements owner out of the relay loop (P1–P6)」、#79「P0 escalation only for goals, root, Mac core」 | 文件 | Owner 負擔 | 原則正確 | 之後仍需 Owner 轉述（#289 73 則） |
| 10-10 | Bug 分級 | #92 user-impact triage、C-1～C-6、兩次修復停損 | defect-triage.md | F1 修復無限循環 | 採納 | 良好；但 runtime 未落實（Report A AUDIT-036） |
| 10-10～11 | CI 費用 | #99、PR #100 exact-head、去重 | workflow | GitHub Actions 額度（USD 10/月） | 估省 24–34% | 10-25 全面 self-hosted |

### 階段 4：Worktree、#120 fake-only 基礎設施（10-11 ～ 10-16）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 10-11～13 | 兩個 AI 同時開發 | G1–G7 兩個固定 worktree 試點（#114／#117／#126）；Codex full-access 審計（#134／#138） | 政策文件、preflight | 分支競爭（#110） | 10-19 被 D6 取代 | 累積 G 系列文件 |
| 10-12～16 | Agent 狀態協調 | #120 S1a／S1b／B contract：fake-first foreground review、authority adapters、Codex pickup B1、Native Delta A1、HOST_ROLLOUT CR-K2-B、Phase0 grant、Step0 census | PR #127–#171（約 20 個） | 讓 llmtool 能「安全地」啟動 Worker | 全部 fake-only；10-20 PR #235「remove parked F1 and Codex pickup modules」封存 | **約一週的程式被封存**（DEC-014） |
| 10-14 | 公司 LLM 分流 | advisory test-triage worker（#152），live route BLOCKED | `test_triage_worker.py` | 省 token | 從未 live | 死碼 |
| 10-16 | AGY | Antigravity CLI 1.4.0 smoke、skill audit | 報告 | 備援模型 | 「backup only」 | 10-24 restore、10-27 OWNER_ASSIGNMENT_ONLY、10-28 納入 llmtool |

### 階段 5：架構原則與角色反覆（10-16 ～ 10-21）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 10-16～17 | 釐清架構 | #172 D-001～D-007（CLI 入口、PM 決定分工、Codex Integrator=PM、llmtool 確定性、mobile Codex Cloud lane） | PR #173（Draft, CLOSED）、#174 | Owner 逐項同意 | 原則至今有效 | **好決策** |
| 10-19 | Codex token 消耗高 | D1–D5 OpenCode secondary → **D7 OpenCode PRIMARY、Codex PAUSED** | PR #198／#203 | 「因為 Codex Developer token 消耗」（policy history 轉述） | 同日 D6 動態 worktree；隔日 D8 Codex 恢復 | 48 小時內三次角色反轉 |
| 10-19 | Owner 報告制度 | outcome reporting v1.1、G1–G4 | governance 文件 | 減少 Owner 負擔 | 採納 | 良好 |
| 10-19 | worktree 上限 | Owner「開放 worktree 吧，但是要判斷什麼時候做完改刪除了」→ D6 | PR #200、lifecycle 腳本 | 並行 | 489 筆 registry、330 ACTIVE | 「做完刪除」的一半至今未自動化 |
| 10-20 | OpenCode 每次要 Owner 核准 | D8「no per-call Owner approval」、D8a 憑證記憶體傳遞 | 政策 | AI 寫的 gate 讓工作停數小時 | 採納 | 好的修正 |
| 10-20 | 文件太多 | #234 slim guidance + #235 封存 | 刪除 | — | AGENTS 44 行 | 好；但 ROADMAP／WORKFLOW／README 未瘦身 |
| 10-20 | 「放到別人電腦會怎樣」 | PR #237 可攜性評估（建議先維持個人工具） | 報告 | Owner 提問 | 10-21 會議否定「先個人」為最終定位 | A13 延後 |
| 10-20 | Web-first | PR #249 → Owner 要求 CLOSED | — | 順序錯 | 10-21 重排 | 好的止損 |
| 10-21 | **產品定義** | 六項使用者行為；llmtool 無 AI 智能；CLI → Telegram → Web → 他人 | 會議紀錄、PR #255 報告 | Owner 與 ChatGPT 討論 | 定義清楚 | **最重要的正確決策，晚了 24 天** |
| 10-21 | TUI | #264 TUI-only UX 決策；#214 A3.5 follow-up | 文件 | — | UI Agent 10-24 才指派 | TUI 實作延至第 28 天 |

### 階段 6：WP1–WP4、PM 接續、dispatch gate（10-21 ～ 10-25）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 10-21 | CLI intake | WP1 `task submit`（#256）、gw delegate（#257）、D6 completion（#258）、OpenCode adapter（#259） | runtime | A11c | 合併 | 零件出現 |
| 10-22 | PM 要能接續 | PM Continuation Contract（#275）；Reviewer handoff state（#276／#280） | skills + runtime | Owner 要「不用人工轉述」 | 文件化；runtime 手動 | R1–R5 缺口自述 |
| 10-22 | Owner 的 pause 被 PR 改掉 | #289 `worker-dispatch.json` + Python gate（#291） | runtime | PR #283 覆寫了 OpenCode PAUSED | 修正 | 好；但 URL 硬編碼 |
| 10-22～23 | Claude 固定 Reviewer（#283）；環境重用 ENV-1（#273）；測試分層（#274／#277／#282） | — | — | — | — | — |
| 10-23～24 | 監控 | monitor office view、Activity、runner rows（#288～#345，約 20 個 PR） | `llmtool_monitor.py` 4,667 行 | Owner 想看「誰在做什麼」 | 良好工具 | 第二個大型單檔 |
| 10-24 | UI | #304 Claude UI Agent（OWNER_ASSIGNMENT_ONLY） | — | TUI 進度 | 10-25 slash picker | — |
| 10-24 | 角色 | 恢復 AGY（#329）、恢復 OpenCode（#330） | policy | Owner | — | 10-26／27 再暫停 |
| 10-25 | CI 成本 | Mac self-hosted runner（#335）、EC2 兩台（#346）、停用 GitHub-hosted | workflows + runner 腳本 | 費用 | USD 歸零（UNKNOWN 實際）；Mac 31 分 | 新瓶頸 |
| 10-25 | **PM 換人** | #351 Claude 成為 Integrator/PM；Codex `gpt-6-luna` Developer 經 llmtool 優先 | AGENTS | Codex PM 用量上限中斷（#99 留言）；HISTORY_INCOMPLETE 其餘原因 | #352 列 G1–G8 缺口 | 之後 3 天 100+ PR |

### 階段 7：#352 缺口修復、EC2 offload、WP3 切片（10-25 ～ 10-28）

| 日期 | 需求 | 決策 | 技術變化 | 原因 | 結果 | 影響 |
|---|---|---|---|---|---|---|
| 10-25～26 | 讓 Claude PM → Codex 路線通 | G1–G6 修復（#354／#355／#356／#360／#374）、bg dispatch（#372） | runtime | #352 | 10 個新 bug（#378～#413） | 正常試錯 |
| 10-26 | 額度 | Codex 暫停 → 全部暫停 → 全部恢復（同日） | 無 | Owner | — | 角色反覆第 N 次 |
| 10-26 | 清理 | D6 retire（#391） | 腳本 | 10 個 dirty worktree | 115 REMOVED | 仍 330 ACTIVE |
| 10-26 | 「不要寫死 CLI 版本」 | 決策 B（#289） | #386 | — | 好 | — |
| 10-26 | CliNest | PR #392 Draft | 文件 | HISTORY_INCOMPLETE | 未合併 | 範圍擴張訊號 |
| 10-26 | A13 | #396／#397 DEFERRED | 文件 | — | 合理 | — |
| 10-26～27 | **EC2 P0** | #417／#425「Mac control + EC2 offload to cut AI token use」P0 override | remote-test（#433～#437）、3 shards（#446）、Mac subset（#447／#467） | Owner 想省 token、Mac 太慢 | 71 receipt；省 token NOT VERIFIED | TUI 同期仍未開始 Task mode |
| 10-27 | 修復升級 | High Debug `gpt-6.1-sol` 規則（#120 留言）→ `task high-debug`（#470）、`task repair`（#459）、`review-dispatch`（#440） | runtime | Owner 決策 | 本機使用 0／0／3 | IMPLEMENTED_ONLY |
| 10-27 | 合併擁塞 | merge trains（#453～#513，11 個） | 流程 | Mac CI 排隊 | 83 CLOSED PR 中多為 train 重複 | 雙重 CI |
| 10-27 | gw | thinking 預設開、16384 上限、壓測 | config | Owner A | 真實題 0/1 | — |
| 10-28 | 範圍 | AGY 納入 llmtool、開源模式 provider-neutral（#500）、全 AI 可 web search（#493）、UI Agent ALLOWED「要推 TUI 進度」 | — | Owner | PR #500／#505／#506／#510／#512／#519 全部 OPEN | 第 31 天：TUI Task mode 與 advance 仍未合併 |

---

## 4. Major Decision Register

| ID | 日期 | 決策 | 類型 | 事後評價 |
|---|---|---|---|---|
| DEC-001 | 09-27 | monorepo：工具箱 + skills | Owner | GOOD |
| DEC-002 | 09-28 | nlf：Go、stdlib、單一執行檔、完整 CI | Owner/AI | GOOD |
| DEC-003 | 09-28 | llmtool Python 唯讀 MVP；PyYAML 唯一依賴 | Owner/AI | GOOD |
| DEC-004 | 09-28 | A0–A12 全景 Roadmap；A1–A10 四天完成 | AI 提案／Owner 接受 | QUESTIONABLE（無使用者驗收即標 DONE） |
| DEC-005 | 10-01 | A3.5 chat 記憶體 REPL；TUI 不做 | AI | REASONABLE AT THE TIME |
| DEC-006 | 10-03 | Codex Cloud 開發 + Mac 驗證 gate | Owner/AI | REASONABLE；文件成本高 |
| DEC-007 | 10-04～05 | QG-S1／QG-QA1 正式品質門 | AI 提案 | REASONABLE |
| DEC-008 | 10-05～09 | 公司模型 token benchmark Round 1–3 | Owner 意圖／AI 執行 | QUESTIONABLE（量測正確；30 份報告過度） |
| DEC-009 | 10-06～09 | A11b F1 Git 容器化（fixture-only Git ADD、supervisor group） | AI 提案／Owner 接受 | **POOR**（為 F2 鋪路，F2 從未授權；Worker 自行 Git 使之無必要） |
| DEC-010 | 10-07～10 | ai-loop 臨時控制器（分支、6-Low disposition、Git launcher） | AI | QUESTIONABLE（第二套編排器；未合併；今仍在文件） |
| DEC-011 | 10-08 | PR #50：llmtool 自我開發 + token 節省為產品目標 | Owner（轉述） | REASONABLE AT THE TIME；但使「PM AI」成為唯一使用者 |
| DEC-012 | 10-09 | Owner 退出 relay loop；P0 升級只限目標／root／Mac | Owner | GOOD |
| DEC-013 | 10-10 | #92 兩次修復停損、SEV 分級 | Owner/AI | GOOD（runtime 落實不足） |
| DEC-014 | 10-11～16 | #120 S1a／S1b／B fake-only 基礎設施（~20 PR） | AI 提案／Coordinator 推動 | **POOR**（10-20 封存） |
| DEC-015 | 10-11 | #99 CI 去重、exact-head | Owner/AI | GOOD |
| DEC-016 | 10-13 | Codex danger-full-access 授權 | Owner | REASONABLE（有審計紀錄） |
| DEC-017 | 10-17 | #172 D-001～D-007 架構原則 | Owner 逐項同意 | **GOOD** |
| DEC-018 | 10-19 | D7 OpenCode PRIMARY／Codex PAUSED → D8 翌日恢復 | Owner（額度驅動） | QUESTIONABLE（反覆） |
| DEC-019 | 10-19 | D6 動態 worktree「做完刪除」 | Owner | GOOD 意圖／執行一半 |
| DEC-020 | 10-19 | Owner outcome reporting v1.1 | Owner | GOOD |
| DEC-021 | 10-20 | D8 取消 per-call 核准；文件瘦身 #234／#235 | Owner | GOOD |
| DEC-022 | 10-20～21 | 關閉 Web-first；產品六項需求；CLI → Telegram → Web | Owner | **GOOD**（但晚） |
| DEC-023 | 10-22 | TUI-only UX；UI Agent 10-24 才指派 | Owner | REASONABLE；排程晚 |
| DEC-024 | 10-22～23 | worker-dispatch.json + Python gate | Owner/AI | GOOD（硬編碼待修） |
| DEC-025 | 10-23～24 | monitor office view 大量投入 | Owner 需求 | REASONABLE（可見性有價值；但在閉環之前） |
| DEC-026 | 10-25 | self-hosted Mac + EC2、停用 GitHub-hosted | Owner | GOOD（費用）／新瓶頸 |
| DEC-027 | 10-25 | Claude 成為 PM | Owner | REASONABLE（HISTORY_INCOMPLETE 動機）；切換成本高 |
| DEC-028 | 10-26 | Codex 暫停／全部暫停／全部恢復（同日） | Owner | QUESTIONABLE |
| DEC-029 | 10-26～27 | EC2 offload 升 P0、優先於 TUI | Owner | QUESTIONABLE（省 token 未證；延後入口） |
| DEC-030 | 10-27 | High Debug `gpt-6.1-sol` 規則 + runtime | Owner | QUESTIONABLE（閉環未通前先做升級路徑） |
| DEC-031 | 10-27 | merge trains | PM | QUESTIONABLE（雙 CI、重複 PR） |
| DEC-032 | 10-28 | AGY 納入 llmtool、開源模式 provider-neutral | Owner | REASONABLE 方向／QUESTIONABLE 時機 |
| DEC-033 | 10-28 | UI Agent ALLOWED「要推 TUI 進度」 | Owner | GOOD（晚） |
| DEC-034 | 10-26 | CliNest 獨立計畫 | Owner？（HISTORY_INCOMPLETE） | INSUFFICIENT EVIDENCE |

---

## 5. Decision Quality Retrospective（重點決策詳述）

### DEC-009 — A11b F1 Git 容器化（10-06～10-09）

- **要解決的問題：** 讓 llmtool 自己安全地執行 `git add`（為 F2 真實 repo mutation 鋪路）。
- **可行選項：** (a) llmtool 自行 Git（需沙箱、審批、rollback）；(b) 讓 Developer CLI 在自己的 worktree 做 Git，llmtool 只驗證結果；(c) 延後。
- **選擇：** (a)，先做 fixture-only F1。
- **理由：** 「native self-development」需要 llmtool 能改 repo。
- **已知限制：** F1 只在私有 fixture；F2 另需安全契約。
- **忽略風險：** (b) 更簡單且最終被採用（#174「F2 is not a blanket prerequisite」、Worker-owned Git）。
- **結果：** PR #43／#46／#49／#84 + 10 份報告；10-20 封存；snapshot 無 F1 程式。
- **成本：** UNKNOWN（約 4 天、數十個 PR／報告）。
- **達標：** 否。**評價：POOR**（當時已有明顯更簡單選項；#172 D-002a 後才釐清）。
- **重做：** 先決定「誰做 Git」，再決定要不要容器化。
- **啟示：** 當 AI 提出「為了未來 X 先做 Y」，先問「X 有沒有不需要 Y 的做法」。

### DEC-014 — #120 S1a／S1b／B fake-only 基礎設施（10-11～10-16）

- **問題：** 跨 CLI 自動交接（Codex Developer → Claude Reviewer → 回 Integrator）的安全契約。
- **選項：** (a) 先用最小真實呼叫驗證可行性；(b) 先寫完整契約與 fake 實作再授權真實呼叫。
- **選擇：** (b)：S1a offline inspect、S1b Product A fake-only、B1 authority adapters、Codex pickup B1、Native Delta A1、HOST_ROLLOUT、Phase0 grant、Step0 census…。
- **理由：** 安全優先、「fake source acceptance 與真實試驗分開」。
- **忽略風險：** fake-first 無法暴露真實 CLI 行為；真實 Codex JSON 形狀（#379）、新檔（#413）等問題在 10-26 才出現。
- **結果：** 10-20 PR #235 封存「parked F1 and Codex pickup modules」；10-25 後以 `codex exec --json` 直接重做（#360）。
- **評價：POOR**（分類 2：已有明顯風險卻沒評估；分類 4：AI 提出過度方案 Owner 未察覺）。
- **啟示：** 對「安全契約」也要設 PoC 停損：一週內沒有真實呼叫證據就停。

### DEC-018／028 — 額度驅動的角色反覆（10-19、10-26）

- **FACT：** 10-19 D7 OpenCode PRIMARY／Codex PAUSED → 10-20 D8 Codex 恢復；10-22 OpenCode／AGY 暫停 → 10-24 兩者恢復 → 10-26 Codex 暫停 → 全部暫停 → 全部恢復 → OpenCode 暫停 → 10-27 OpenCode 再暫停（free-limit）。
- **問題：** 5 小時／週額度用盡。
- **評價：QUESTIONABLE**（分類 7：原本合理但未建立常態規則）。每次切換帶來 policy PR、文件更新、D6 轉移、PM context 重建。10-27 才有「quota 是技術可用性觀察，不是 dispatch authority 變更」的規則（pm-continuation）。
- **重做：** 一開始就把「額度暫停」定義為自動的技術狀態（monitor 已能看），不走 Owner 決策與 policy PR。

### DEC-022 — 10-21 產品定義

- **評價：GOOD。** 清楚、可驗收、區分 Owner 已確認與 PM 建議。唯一問題是時間：第 24 天。
- **COUNTERFACTUAL：** 若在 10-01（A3.5 chat）時寫下六項使用者行為，A11b／#120 fake-only 大概不會被排在 TUI 之前。

### DEC-029 — EC2 offload P0（10-26～27）

- **問題：** Mac 太慢、想省 Codex token。
- **選項：** (a) EC2 remote-test + 3 shards；(b) 先把 Mac CI 縮到 Mac 專屬模組；(c) 先完成 TUI 閉環再優化。
- **結果：** 71 receipt、EC2 3 shards 7 分鐘；但「省 token NOT VERIFIED」（#466）；Mac 仍 31 分（SHARED_MODULES 退化）。
- **評價：QUESTIONABLE**（分類 3：目標「省 token」未定義量測方法就 P0）。
- **啟示：** P0 override 應附量測指標與期限。

### DEC-030 — High Debug 規則（10-27）

- 在閉環的 review-dispatch／repair 從未被實際使用（0 次）之前，就定義並實作第三層升級路徑。**QUESTIONABLE**（分類 6：方案合理、順序錯）。

### DEC-017 — #172 架構原則（10-17）

- **GOOD。** 「PM 做語意、llmtool 確定性、CLI 單一入口、公司 LLM 只是 API」至今是對的，而且程式碼遵守。缺的是把原則變成 MVP 範圍。

### DEC-004 — 四天完成 A1–A10

- **QUESTIONABLE。** 每個 milestone 由 AI 自審自驗；之後 A7／A8／A9（feedback、benchmark、orchestrate）幾乎無使用紀錄（FACT：WP3 不用 `orchestrate`）。分類 3：需求未定義就實作。

---

## 6. Product Owner Assessment

> 本章直接評估 Owner。所有判斷附證據；Owner 私下對話無法取得，凡 PM 代記皆標明。

### 6.1 十項能力

| 能力 | 評分（0–10） | 優點 | 缺點 | 案例 | 影響 | 改進 |
|---|---|---|---|---|---|---|
| Requirements Clarity | 5 | 10-21 會議六項需求清楚；verbatim 決策留言可追溯 | 前 24 天需求以一句話＋AI 擴寫；「省 token」「做完刪除」無驗收標準 | DEC-011、DEC-019、DEC-029 | AI 填補規格 → 過度工程 | 需求範本（Report C） |
| Product Vision | 7 | 方向一致：本機、唯讀安全、他人可裝、llmtool 無 AI | 定位四次轉向才定稿 | 第 2 章 | 舊定位程式堆積 | 一頁 vision 固定 |
| Technical Decision Discipline | 4 | 要求真實證據、拒絕假 PASS、問「放別人電腦會怎樣」 | 接受 AI 的 fake-first／容器化／五套授權而未問替代方案 | DEC-009、014 | 封存一週工作 | 「三個選項」規則 |
| Priority Management | 4 | 能止損（關 Web-first、封存、瘦身） | 額度驅動反覆；EC2 P0 插隊；High Debug 先於閉環 | DEC-018、028、029、030 | 主線（TUI／E2E）延到第 28 天 | 單一「現在只做一件事」 |
| Scope Control | 3 | A13 延後、Telegram 延後 | 4 條 Developer 路線、AGY 納入、CliNest、web search、Web UI、monitor 擴張 | DEC-032、034 | 第 31 天 6 個 OPEN 核心 PR | 凍結規則 |
| Risk Management | 6 | 安全邊界堅持、no paid fallback、D8a 憑證規範 | 磁碟／CI 容量風險無人管；流程風險未視為風險 | AUDIT-003、004 | 13 GB 剩餘 | 每週資源檢查 |
| Cost Awareness | 6 | 早期量測 token；停 GitHub-hosted；EC2 | 只看 provider token，不看 PM／Reviewer／CI 分鐘／PR 數；省 token 未驗證即 P0 | DEC-008、029 | 隱形成本未量測 | 成本看板 |
| AI Team Delegation | 5 | D8 取消 per-call 核准；Owner 退出 relay；PM 可自派 | 仍有 73 則 #289 代記；角色切換靠 Owner 一句話；驗收者是 PM 自己 | #289、#120 | Owner 成為決策快遞 | 週決策批次 |
| Acceptance Discipline | 2（INFERENCE，以缺席證據為主） | 堅持「MERGED ≠ VERIFIED」文字（FACT） | **審查未找到任何 Owner 親手走完「提需求 → 看到結果」流程的紀錄**（Issues、PR、TaskStore、會議紀錄皆無；Owner 私下操作 HISTORY_INCOMPLETE）。可確認的 FACT 只有：流程文件中沒有「使用者操作」這個驗收 gate；snapshot 的 TUI 本身也沒有可供驗收的閉環功能。「以 PR／CI 判斷進度」為 INFERENCE（可見的進度來源皆以 PR／CI／MERGED 為單位） | 本機 store 推導 COMPLETED 1/279（受 AUDIT-014 影響，見 Report A 2.4）；TUI Task mode 到第 31 天才合併（#513） | INFERENCE：若 Owner 未親手操作，就無法在早期察覺入口缺失 | 每週一次 Owner E2E（建議） |
| Long-Term Product Thinking | 6 | 開源、provider-neutral、他人可裝 | 在主鏈未通時啟動 | DEC-032 | 分散 | 順序鎖定 |

> 評分為 OPINION，證據等級已逐列標示；「Acceptance Discipline」一列的低分主要來自**沒有找到驗收紀錄**，不是來自觀察到 Owner 拒絕驗收。

### 6.2 A–E 問題直接回答（摘要）

**A 需求表達：** 通常是「想要什麼」而非「情境＋驗收」；10-21 之後明顯改善。前後矛盾的例子：10-19「因 token 消耗暫停 Codex」與 10-20「恢復 Codex」；10-20 Web-first 與 10-21 CLI-first。容易誤解的需求：「省 token」（gw、EC2 兩次被解讀為 P0 工程）。過度依賴 AI 補規格：是（S1b／B contract 數千字無 Owner 原文）。
**B 優先順序：** 經常改變；多數由額度／CLI 可用性／短期問題驅動；主線 TUI 因 F1、#120、EC2、High Debug 延後；過早開發非 MVP：A7–A9、F1、fake-only、High Debug、AGY route、gw 壓測。
**C 決策方法：** 證據意識強，但替代方案評估弱；過度相信 AI 的「安全契約」提案；低估複雜度（D6「做完刪除」）；缺停損條件（fake-only 一週、benchmark 30 份）。
**D AI 管理：** PM 職責文件化但分散於 5 份；PM 自主權在 10-20 D8 後充足；高低階模型配置：Reviewer 用高階審 2 行文件、gw 做真實題失敗後再派 Codex；缺客觀 Developer 品質指標（Report A 11.4 首次量化）。
**E 完整性：** 無 MVP 定義到 10-21（FACT：10-21 前無任何文件定義使用者行為）；無使用者驗收流程（FACT：文件中無此流程；Owner 私下是否操作 HISTORY_INCOMPLETE）；「以 PR 數／CI 判斷進度」是 INFERENCE：Owner 可見的進度來源（PM 週報、Roadmap 表、#466 狀態報告）皆以 PR／CI／MERGED 為單位，審查未找到以使用者操作結果為單位的進度紀錄；把 runtime 切片 MERGED 當進度（FACT：Roadmap R1–R5 標 MERGED）。

---

## 7. PM / Developer / Reviewer Responsibility Analysis

### 7.1 現況責任配置（FACT）

| 角色 | 文件規定 | 實際 |
|---|---|---|
| Owner | 產品、優先、重大成本／風險、Reviewer 配對例外 | 另加：額度暫停／恢復、每個 AGY Task、模型名、gw 上限、TUI session 選項（#304 S4）… |
| PM | 規劃、派工、接續、整合、報告 | 另加：手打 8 個 llmtool 子命令、建 D6、跑 remote-test、代記 Owner 決策、merge train、監控 |
| Developer | 實作、自測、自審、修復 | Codex：600 s 內完成；gw：只提案；AGY：直接 CLI |
| Reviewer | 正式審查 | 每輪新 D6；審 docs PR；為 2–3 行改動寫 verdict |
| llmtool | 確定性執行、狀態、事件、權限 | 做到單步；不做接續；也替 PM 寫 repair 決定（AUDIT-016） |

### 7.2 Responsibility Gap

| 類型 | 項目 |
|---|---|
| 沒有人負責 | Task 終態與結案；D6 容量；Mac 磁碟；PM／Reviewer token 量測；使用者 E2E 驗收 |
| 重複負責 | 修復上限（docs／review_repair／worker_dispatch）；Worker 描述五處；授權模型五套；PM loop 與 TaskStore 狀態 |
| 不必要 Owner 介入 | 額度暫停／恢復；每個 gw 參數；每個 AGY Task 的 Owner ref；Reviewer 帳號 |
| PM 缺自主 | 無（10-20 後已足）；問題是 PM 的自主被流程吃掉 |
| AI 越權 | #283 覆寫 Owner 的 OpenCode PAUSED（已修）；llmtool 以 PM 身份寫 repair 決定；PM 自己當驗收者 |
| 流程設計錯 | Reviewer 每輪新 worktree；docs PR 走完整 gate；merge train 雙 CI；事件無 consumer |

### 7.3 改良責任模型（建議；整表為 OWNER_DECISION_REQUIRED，不修改現行政策）

| 角色 | 負責 | 不負責 |
|---|---|---|
| Owner | 每週一次：vision 確認、優先序、需求驗收（親自操作 TUI）、預算上限、重大風險 | 額度、模型名、Reviewer 帳號、每個 Task、參數 |
| PM（Claude session） | 拆解、record-pm、追蹤 advance 輸出、處理 BLOCKED、開 PR 與合併、週報 | 手打 runtime 子命令（交驅動器）、代記決策（Owner 在 TUI 核准） |
| Developer（Codex direct） | 實作＋自測＋自審＋一次自修；回報 DONE／BLOCKED | 跑全套測試（交 EC2）、審查自己 |
| Reviewer（cross-family） | 只審 runtime／安全相關 PR；復用 D6；只讀 delta | docs／test-only PR（CI + 抽樣） |
| llmtool Core | 狀態、終態、驅動、事件消費、retry 上限、D6 自動 retire、容量告警 | 語意判斷；替 PM 寫決定 |

---

## 8. Major Project Mistakes（逐項驗證）

| # | 錯誤 | 時間 | 原始需求 | 決策 | 錯在哪 | 責任 | 為何沒發現 | 影響 | 重複？ | 避免 | Owner 學到 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | MVP 定義太晚 | 09-28～10-21 | 「編排基礎」 | A0–A12 全景 | 無使用者驗收標準 | Owner＋AI | 每個 milestone CI 綠 | 舊定位程式堆積 | 是（C 路線、#120） | 先一頁 MVP | 「DONE」要我親手驗 |
| M2 | 內部框架先於入口 | 10-05～10-16 | 安全自我開發 | F1、fake-only | 建了之後封存 | AI 提案／Owner 未停損 | 看起來「安全」 | 一週工作封存 | 是（High Debug、AGY route） | PoC 停損 | 「安全契約」也要期限 |
| M3 | 缺使用者實測（INFERENCE：紀錄中無 Owner 操作驗收；私下操作 HISTORY_INCOMPLETE） | 全程 | — | — | 文件中的驗收者是 PM／Reviewer，沒有「使用者操作」這個 gate（FACT） | 流程設計（Owner 與 PM 共同） | 可見的進度單位是 PR／CI | 入口缺失到第 28 天才被當成優先事項 | 是 | 每週 E2E | 自己用產品 |
| M4 | 重要入口落後 | 10-01→10-27 | TUI | chat 記憶體版 | TUI Task mode 第 31 天仍 OPEN | PM 排程＋Owner 優先 | 其他事更「急」 | 產品不可用 | 是 | 入口第一 | 入口優先於優化 |
| M5 | 過早複雜框架 | 10-06 起 | 安全 | 五套授權、三套上限 | 無單一真實來源 | AI | 每個都有測試 | 維護成本 | 是 | 單一模組 | 問「哪個是唯一的」 |
| M6 | 同一功能多套實作 | 10-21 起 | Developer 路線 | Codex／OpenCode／gw／AGY | 4 條都半成 | Owner（開新路線）＋PM | 每條都「快好了」 | 分散 | 是 | 一條通再加 | 一次一條 |
| M7 | PR 碎分與重複 | 10-26～27 | 快速合併 | merge train | 雙 CI、83 CLOSED | PM | CI 排隊 | Mac CI 更擠 | 是 | 分級流程 | 流程是成本 |
| M8 | 只有文件沒 runtime | 10-11～10-22 | 接續 | PM Continuation 文件 | R1–R5 手動 | PM | 文件完成感 | Owner 仍轉述 | 是 | 文件附 runtime DoD | 文件≠能力 |
| M9 | 決策資訊不足 | 10-19、10-26 | 額度 | 角色切換 | 無規則 | Owner | 當下有效 | PR／文件風暴 | 是 | 自動技術狀態 | 技術狀態不是決策 |
| M10 | 無停損 | 10-05 benchmark、10-12 fake | 省 token／安全 | 持續投入 | 30 份報告、20 PR | Owner | 進度感 | 浪費 | 是 | 期限＋指標 | 兩週無真實證據即停 |
| M11 | 高階模型做低價值工作 | 10-23 起 | 審查 | Claude 審 docs PR | 每個 PR 完整審 | 流程 | 規則一刀切 | token | 是 | 分級 | 審查也分級 |
| M12 | 重複測試 | 10-25 起 | CI | Mac subset + EC2 full + train | Mac 退化 FULL | PM／CI 設計 | 「安全」 | 31 分 | 是 | Mac 只跑 Mac | 量測後再加 gate |
| M13 | 未量測 ROI | 全程 | 省 token | 多路線 | 只有 Codex 用量 | Owner | 無看板 | 不知道省了沒 | 是 | 成本看板 | 沒量測就沒省 |
| M14 | 資源容量無人管 | 10-19 起 | 並行 | D6 開放 | 374 worktree、13 GB | PM／llmtool | 看不見 | 風險 | 是 | 自動 retire＋告警 | 容量是功能 |

---

## 9. Repeated Failure Patterns

1. **「先建安全／治理框架，後驗證是否被用」**：F1 → fake-only → review-dispatch／repair／High Debug（0 使用）。
2. **「額度事件 → Owner 決策 → policy PR → 文件 → 反轉」**：10-19／20／22／24／26／27。
3. **「同一需求開新路線而非修好舊路線」**：gw 失敗 → OpenCode → Codex → AGY → provider-neutral。
4. **「文件先行、runtime 後補、兩者脫節」**：PM Continuation、High Debug、D6 retire、Roadmap DONE 標記。
5. **「以 PR／CI 綠為進度」**：437 PR、COMPLETED 1。
6. **「修工具的工具」**：monitor 4.7k 行、dispatch-check、bg dispatch receipt、marker 詞彙表。

---

## 10. Missed Opportunities

| 時間點 | 機會 | 證據 |
|---|---|---|
| 10-01 | A3.5 chat 完成時定義 TUI 任務流程 | 當時已有 chat、provider、config |
| 10-09 | #77「Owner 退出 relay」時把 record-pm + inbox 作為第一個 runtime 切片 | 10-26 才做（#355） |
| 10-17 | #172 原則確立時，直接以 `codex exec` 做 PoC（10-25 #360 證明兩天可成） | 反而做了 fake-only |
| 10-19 | D6 開放時同時做自動 retire | 10-26 才做半套 |
| 10-21 | 產品定義後凍結新路線 | 反而加 gw 真實題、AGY、EC2 P0 |
| 10-25 | self-hosted 時把 Mac CI 限 Mac 專屬 | 退化 FULL 至今 |

---

## 11. Counterfactual Analysis（COUNTERFACTUAL；非確定結果）

| 替代路線 | 當時條件 | 額外資源 | 可能節省 | 可能失去 | 對完成時間 | 對安全／可靠 |
|---|---|---|---|---|---|---|
| **CF-1 10-01 先做 TUI MVP**（chat → Task → 一次 Codex 呼叫 → 顯示 diff） | 有 chat、codex-cli provider | 無 | A11b F1、#120 fake-only 約 2 週 | 早期安全契約 | 可能提前 2–3 週有可操作版本 | 風險低（唯讀 + Worker 自行 Git 後才有寫入） |
| **CF-2 10-09 先 E2E 驗收**（Owner 每週親自跑一次） | 有 ai-loop／Codex Cloud | Owner 每週 1 小時 | 避免 M3／M8 | 無 | 問題會在第 2 週而非第 4 週暴露 | 提升 |
| **CF-3 10-17 簡化 PM workflow**（record-pm + advance 驅動器，無 claim 狀態機） | #172 原則已定 | 無 | 五套授權、三套上限 | 部分審計粒度 | 閉環可能在 10-24 前通 | 可靠性提升（單一真實來源） |
| **CF-4 10-11 就把 Linux 測試移 EC2、Mac 只跑 Mac 專屬** | #99 時已知費用問題；EC2 已存在（Owner 10-27 確認） | EC2 設定 1–2 天 | GitHub-hosted 費用、Mac 31 分 | 無 | 10-25～27 的 CI 工程可提早 | 不變 |
| **CF-5 10-01 一頁產品規格**（六項使用者行為） | Owner 已有想法（INFERENCE，從 09-28 Roadmap 的 A9／A11） | 一次會議 | M1、M2、M6 | 探索空間 | 最大 | 不變 |

---

## 12. Scope Creep / Architecture Drift

| 系統 | 最初為何需要 | 直接支持產品目標？ | 更簡單方法？ | 實際收益 | 新依賴 | 延後了什麼 | 仍必要？ |
|---|---|---|---|---|---|---|---|
| Agent Team Management（roster、dispatch json、marker、monitor office） | 多 AI 並行、Owner 可見 | 間接 | 一個 JSON + `task status` | 可見性 | monitor 4.7k 行 | TUI | 部分（dispatch json 必要；roster／marker 可簡） |
| Worktree Lifecycle（D6） | 並行寫入隔離 | 是 | 每 PR 一個 worktree、自動 retire | 隔離 | 3 腳本 4.5k 行 | — | 是（需自動 retire） |
| PM Continuation（文件＋loop） | 不要 Owner 轉述 | 是 | advance 驅動器 | 低（仍手動） | — | 閉環 | 文件可縮為一頁 |
| TaskStore | 狀態持久 | 是 | — | 高 | — | — | 是（拆出狀態機） |
| Provider Routing（4 Developer 路線） | 省 token、備援 | 否（1 條即可） | 1 條 | 低 | 每條 CLI | 主鏈 | 1 條 |
| Approval Gates（action_contract、authorization_*、review_authority） | llmtool 自行執行 | 否（F2 未授權） | 無需 | 零 | — | — | 否（封存） |
| Reviewer Flow（cross-family、verdict 格式、D6 per review） | 獨立審查 | 是 | 復用 D6、分級 | 中 | 189 worktree | CI 容量 | 是（簡化） |
| Debug Escalation（High Debug） | 停止盲修 | 間接 | 文件規則即可 | 低（3 次） | gpt-6.1-sol | 閉環 | 延後 |
| CI Evidence（exact-head、receipt） | 真實證據 | 是 | — | 高 | EC2 | — | 是 |
| Monitoring | 可見性 | 間接 | TUI Tasks view | 中 | — | TUI | 併入 TUI |
| Skills／Policies（14 skills、600 KB） | AI 行為一致 | 間接 | 60 KB | 中 | 每 session 15 萬 tokens | — | 瘦身 |

### Complexity Added vs Product Value Delivered

| 項目 | 複雜度（LOC／文件／流程） | 產品價值（使用者可感知） | 比值判斷 |
|---|---|---|---|
| Provider 層 + TaskStore I/O + D6 身份 | 高 | 高（閉環基礎） | 值得 |
| Codex direct + EC2 receipt | 中 | 高 | 值得 |
| review-dispatch／result／repair／high-debug | 中高 | 目前零 | 待組裝 |
| 五套授權、三套上限、marker、roster | 高 | 低 | 過度 |
| F1、fake-only、ai-loop、A7–A9 | 高 | 零（封存／未用） | 浪費 |
| monitor | 高 | 中（Owner 可見） | 可併 TUI |
| 文件 600 KB | 高 | 負（token、誤讀） | 過度 |
| TUI | 低 | 應最高 | **投入不足** |

---

## 13. Historical Lessons Learned

1. 「DONE」必須由使用者親手驗，不是 CI 綠。
2. 任何「為了未來 X 先做 Y」的提案，先問 X 有沒有不需要 Y 的做法。
3. 安全契約也要 PoC 期限；fake-first 不能超過一週沒有真實呼叫。
4. 額度與 CLI 可用性是技術狀態，不是產品決策；不應觸發 policy PR。
5. 一條路線通了再開第二條。
6. 文件的 DoD 必須包含「runtime 做到了什麼」；MERGED 與 VERIFIED 分欄。
7. 流程是成本：每加一個 gate 要量它擋住了什麼。
8. 容量（磁碟、CI 分鐘、worktree 數）是產品功能的一部分。
9. 成本要含 PM／Reviewer／CI／Owner 時間，不只 provider token。
10. Owner 一句話會被 AI 放大成數千字；放大前要求 AI 回報「最小版本」。

---

## 14. Owner Strengths（Five Things Done Well，附證據）

1. **堅持真實證據、拒絕假完成。** 全部文件區分 MERGED／VERIFIED／NOT_RUN；#255「不得宣稱六項需求全部驗收」；D4 寫明「2 attempts, no inference」。
2. **願意止損與瘦身。** 關閉 Web-first（#249）、封存 F1／pickup（#235）、瘦身 AGENTS（#234）、A13 延後（#396）。
3. **正確的架構原則。** #172 D-001～D-007 與 10-21「llmtool 無 AI 智能」至今正確且被程式遵守。
4. **把自己移出 relay loop 的意識。** #77／#79／D8；10-28「我又不會親自去操作 github…pm 要有我下達指令的對話紀錄在 git 就好」建立了可追溯的決策紀錄規則。
5. **成本與安全底線。** no paid fallback、免費模型限制、D8a 憑證不落地、self-hosted 止費、Mac runner 隔離。

---

## 15. Owner Weaknesses（附證據）

1. **沒有建立使用者操作驗收的環節**（FACT：流程文件中無此 gate；Owner 是否私下操作為 HISTORY_INCOMPLETE，本報告不主張「從未」）。可觀察的結果是入口缺失到第 28 天才成為優先事項、TUI Task mode 第 31 天才合併（#513）。
2. **讓 AI 把一句話放大成框架而不要求最小版本。** D6「做完刪除」→ 13 KB 規範；「安全交接」→ 20 個 fake-only PR。（FACT）
3. **額度驅動的反覆決策。** 10-19～10-27 至少 8 次角色切換。（FACT，PM 代記）
4. **開新路線而非修舊路線。** gw → OpenCode → AGY → provider-neutral，主鏈未通。（FACT）
5. **可見的進度單位是 PR 與 CI，對流程成本不敏感。**（INFERENCE：437 PR、83 CLOSED、merge train 皆未被任何紀錄視為問題）

---

## 16. Five Changes the Product Owner Should Make

### 1. 每週親自用產品做一件真實的事，並以此定義「完成」
- 目前（INFERENCE）：可見的進度來源是 PM 報告、PR、CI；沒有「Owner 操作結果」這個驗收單位。
- 問題：產品入口缺失卻看似持續前進。
- 案例：snapshot 的 `/task submit` 只做 intake（AUDIT-001）；本機 store 推導 COMPLETED 1/279（範圍限制見 Report A 2.4）。
- 原因：流程中的驗收者是 PM／Reviewer，與開發者同為 AI。
- 新做法：每週一個「Owner 任務」：從 TUI 提交到 merge；失敗即為當週 P0。
- 決策時：任何功能「DONE」前問「我親手跑過嗎？」
- 判準（待驗證目標）：連續數週 Owner 任務多數成功；具體比例由前兩週實測後設定。

### 2. 一次只開一條路線；新路線必須等主鏈 E2E 通過
- 目前：Codex／OpenCode／gw／AGY／Claude dev 五條半成。
- 問題：PM、Reviewer、CI 容量被分散。
- 案例：10-28 AGY 納入 + provider-neutral + web search + UI Agent 同日。
- 新做法（OWNER_DECISION_REQUIRED：Roadmap 與 dispatch 切換屬 Owner）：Roadmap 只允許一個「NOW」；其餘「FROZEN until E2E」。
- 決策時：「這條路線解決的問題，主鏈通了之後還存在嗎？」
- 判準（待驗證目標）：OPEN 核心 PR 數與「服務 NOW 的 merged PR 比例」逐週改善；具體門檻由實測基準後設定（v1 的 ≤ 3／≥ 60% 為審查者估計）。

### 3. 把額度／CLI 可用性從決策降為自動狀態
- 目前：每次 5 小時上限 → Owner 一句話 → policy PR。
- 問題：PR／文件風暴、角色反覆。
- 案例：10-26 同日暫停／全停／恢復。
- 新做法（OWNER_DECISION_REQUIRED：改變 `worker-dispatch.json` 的使用方式與 Owner 決策紀錄規則）：monitor 判定技術不可用 → PM 在已 ALLOWED 的路線內改派；Owner 只在每週設定「允許的路線集合」。pm-continuation.md 10-27 已有「quota 是技術觀察，不是 dispatch authority」的規則，此建議是把它落實到不再產生 policy PR。
- 判準（待驗證目標）：因額度而修改 `worker-dispatch.json` 的次數趨近於零。

### 4. 對 AI 的「契約／安全框架」提案要求最小版本與期限
- 目前：接受完整契約（S1b／B、High Debug 報告格式、D6 13 KB）。
- 問題：建了不用、封存。
- 案例：DEC-009、DEC-014、DEC-030。
- 新做法：任何治理提案必須附「短期 PoC 版本 + 真實呼叫證據 + 若無證據即停」。
- 決策時：「最小能證明可行的版本是什麼？什麼時候可以看到真實執行？」
- 判準（待驗證目標）：上線後持續 0 使用的 runtime 功能應被列為檢討候選；「兩週」是審查者建議的觀察窗，實際窗口由 PM 依任務週期設定；是否移除為 OWNER_DECISION_REQUIRED。

### 5. 把成本看板從「provider token」擴到「PM／Reviewer／CI／Owner 時間」
- 目前：只有 Codex usage；省 token 宣稱無量測。
- 問題：EC2 P0、gw、merge train 的 ROI 不可知。
- 案例：#466「NOT VERIFIED」；Mac CI 31 分。
- 新做法：每 merged PR 記 Codex tokens、Reviewer 輪數、Mac／EC2 分鐘、PM 手動命令數、Owner 決策數；每週看趨勢。
- 判準：每個優化決策附前後數據。

---

## 17. Recommended Governance Improvements（建議；標示者需 Owner 決策，不修改現行政策）

- 單一入口文件（短）：vision、NOW、FROZEN、本週 Owner 任務、成本看板連結。（工程建議）
- Owner 決策改為每週批次（除安全／支出）。（OWNER_DECISION_REQUIRED：Owner 決策紀錄規則）
- 分級流程：docs／test-only／runtime。（OWNER_DECISION_REQUIRED：AGENTS.md 審查與 CI gate）
- Reviewer 復用 D6；docs PR 不需正式審。（OWNER_DECISION_REQUIRED：D6 lifecycle 與審查規則）
- 技術狀態自動化（額度、runner、磁碟）。（OWNER_DECISION_REQUIRED：dispatch 切換方式）
- 每個 runtime 功能上線附「使用計數」；持續 0 使用者標為檢討候選，移除需 Owner 決策。（計數為工程建議；移除為 OWNER_DECISION_REQUIRED）

---

## 18. Roadmap Reassessment

### 合理
- A11c 作為核心；A13 延後；Telegram／Web 延後；cross-family review；exact-head CI；EC2 測試。

### 不合理
- A3.5 標 DONE；WP1 標 MERGED 當入口；R1–R5「MERGED 但 NOT VERIFIED」並列；High Debug／AGY／gw 真實題與主鏈並行；A11b F2 仍列；ai-loop 仍列「temporary coordinator」。

### 建議（立即／延後／取消／Owner 重決）

**整表為審查建議；「延後」「取消」兩列改變 Roadmap 與 dispatch 政策，與「Owner 重決」一列同樣是 OWNER_DECISION_REQUIRED；「立即」一列中除 Owner E2E 外皆為現行 Roadmap 已有的 in-flight 工作。**

| 分類 | 項目 |
|---|---|
| **立即** | TUI Task mode + Tasks view（#505／#510／#512，**已於 snapshot 後經 #513 合併**）；`task advance` + 終態（#519，OPEN）；D6 經 gate 的安全退場；Codex retry；一個 Owner 任務 E2E；文件瘦身第一輪 |
| **延後**（OWNER_DECISION_REQUIRED） | provider-neutral route 接入 `task delegate`（#500 契約已於 #513 合併）、AGY、OpenCode、gw 真實題、High Debug 自動化、GitHub events inbox、Telegram、Web、A13、CliNest |
| **取消**（OWNER_DECISION_REQUIRED） | A11b F2、ai-loop 引用、A2.3、review_authority／review_exchange／review_bundle／test_triage_worker 封存、Mac CI verifier-of-verifier |
| **Owner 重決** | Reviewer 分級（docs／test-only 免正式審）；Mac CI 範圍縮小；merge train 停用；是否保留 gw 作為路線；CliNest 是否獨立 |

### Post-Snapshot 註記（main `3621aa4`，#513，2026-10-27 20:25 UTC）

Snapshot 之後 merge train 11 合併了 TUI Task mode／Task list／事件時間軸（#505／#510／#512）、`pm_runtime`（#506）、gw probe 與設定（#508／#509／#511）與 provider-neutral developer route 契約（#500）。這改變了上表「立即」一列的狀態，不改變本報告對歷史決策的評價；AUDIT-001 的核心（TUI 仍不驅動開發流程）依 Report A 第 26 章仍成立。

---

## 19. Final Conclusions

- **方向正確，順序錯誤。** 原則與零件品質高；入口與組裝最晚。
- **最嚴重的五個錯誤：** M1 MVP 太晚（FACT）、M2／M5 框架先於入口並封存（FACT）、M6 多路線分散（FACT）、M9 額度驅動反覆（FACT）、M3 流程中沒有使用者驗收環節（FACT 為流程缺口；Owner 個人行為為 INFERENCE）。
- **責任分佈（OPINION，依可見紀錄）：** 技術問題（God module、契約過嚴、無終態）屬 AI 實作；流程問題（merge train、每輪 D6、docs 全 gate）屬 PM 在既有規則下的選擇與規則本身；順序與範圍（新路線、P0 override、驗收環節缺失）與 Owner 的決策方式相關，但多數決策是在 AI 提案基礎上做出的，責任為共同。
- **本章所有「應停止／應優先」為建議；涉及 Gate、Reviewer、Dispatch、Roadmap 者為 OWNER_DECISION_REQUIRED。**
- **過度工程：** 是，集中在授權／治理／文件；核心執行層不過度。
- **絕對不會再做：** F1 容器化、fake-only 契約、ai-loop 第二編排器、30 份 benchmark 報告、五套授權。
- **最有價值的成果：** TaskStore＋D6＋Codex direct＋EC2 receipt＋CI 隔離＋nlf。
- **最該停止：** 開新路線、merge train、每輪新 Reviewer D6、docs PR 正式審、額度 policy PR。
- **最該優先：** 一條鏈 E2E，由 Owner 親手驗收。
