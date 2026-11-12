# llmtool GUI Control Center — 產品與架構設計草案 v1

- 日期：2026-11-11
- 狀態：**DESIGN DRAFT / FOR CLAUDE REVIEW**；Owner 在對話中確認的是產品方向與「先做靜態流程圖」的範圍，不代表此文件已經過獨立技術審查。
- 交付性質：**docs-only**。不修改 runtime、dispatch policy、權限、CI、GitHub 操作或目前工作流程；本文件不構成直接派工、實作、合併的額外授權。
- 專案：davidcatcat/davidcattoolsproject / llmtool。
- 主要追蹤：[#1164 Monitor GUI](https://github.com/davidcatcat/davidcattoolsproject/issues/1164)、[#1230 GUI D3](https://github.com/davidcatcat/davidcattoolsproject/issues/1230)、[#304 UI Agent](https://github.com/davidcatcat/davidcattoolsproject/issues/304)、[#683 TUI UX](https://github.com/davidcatcat/davidcattoolsproject/issues/683)。
- 目的：供 Claude 從產品、現有程式、工程量、相容性、測試與風險角度提出具體建議；**不要將提案視為已完成的功能或已核定的技術選型**。

## 1. 產品目標與 Owner 本次決定

llmtool 已有 PM 對話/TUI、監控終端（含 Office、Board 及其他四個分頁），又有 Task / Developer / Reviewer / GitHub / CI 的工作資料。新 GUI 的價值是將這些**同一套真實資料與 Core 能力**變得好理解、好使用，而不是建立新的 PM、Scheduler、TaskStore 或監控真相來源。

**Owner 已確認的方向（本次 2026-11-11 對話）：**

1. GUI 整合成一個應用程式，**不同功能分頁，不把所有東西堆在同一個畫面**。
2. **PM 對話**獨立一頁。
3. **監控 + AI 辦公室**合成一頁。Office **先做卡片式監控**，保留目前監控 Office 的功能與語義；**暫不做像素角色、2D/3D 辦公室動畫**。
4. **PR / Issue 動態白板**獨立一頁，採 Trello 式卡片/欄位 UI，卡片依真實狀態**自動**移動。它要兼容現有 monitor Board 的資訊和判斷，不只是表面上的新看板。
5. **流程圖獨立一頁，v1 先做靜態架構/流程圖**；不做即時節點動畫、不允許拖拉連線修改實際工作流程、不建 Workflow Engine。未來有需求再評估升級。
6. 現有監控其他較少用的 Log、Tasks、Processes、Activity **不刪除**，安置在相關詳情或「進階診斷」。
7. 保留現有 CLI、TUI、curses monitor。GUI 是新增 frontend，不是替換或重寫既有產品。
8. 功能優先、分小步交付、用實際資料/證據判斷完成與否；避免因美術效果或超前的流程引擎拖慢主線。

**本次明確不做：** 動畫辦公室、拖拉改變流程、動態流程圖、任意拖拉卡片改變 Task 狀態、遠端/區網開放、獨立 Agent 執行器、另造權限/審查/CI 體系。

## 2. 已核對的現況與相容性基線

### 2.1 現有程式/規劃

- §scripts/llmtool_monitor.py§ 的 curses UI 有六個 view：§log§、§tasks§、§table§（Processes）、§activity§、§office§、§board§。
- 目前 §office§ 在繪製時使用 §office_view§ / §office_top§，綜合 Office rows、Owner waiting、PR scope/pending、PM progress、stuck、quota、rule/install alerts 等來源。新 GUI **不得只展示一排 AI 卡片而遺失這些資訊**。
- 目前 §board§ 使用 §work_board§ + §board_view§，涉及 GitHub、PM board、CI/測試 head、reviewing 與 Office；新白板須在等價資料上建立，不可只拿 GitHub PR title 推算。
- §tasks§ 包括 D6、Task records、llmtool Task 資訊；§activity§ 可區分背景活動與雜訊；§table§ 包括真實程序狀態；§log§ 包括完整近期事件。
- §tools/llmtool/llmtool/chat_tui.py§ 已有 prompt-toolkit 聊天介面。Web Chat **不能直接把 TUI 畫面嵌入網頁**；應透過既有 Chat/PM Core 協定/適配器接入，先做能力缺口盤點。
- [#1164](https://github.com/davidcatcat/davidcattoolsproject/issues/1164) 已有 Monitor GUI G1/G2/G3 路線。原規劃將 Office 與白板放在同一頁，**新的 Owner UI 分頁決定取代該佈局部分**，但不推翻可重用的監控輸出。
- 對應 G1 的拆分 PR：[#1399](https://github.com/davidcatcat/davidcattoolsproject/pull/1399) loopback server、[#1403](https://github.com/davidcatcat/davidcattoolsproject/pull/1403) office_state JSON、[#1421](https://github.com/davidcatcat/davidcattoolsproject/pull/1421) Office rows，已於 2026-11-10 合併。原 [#1179](https://github.com/davidcatcat/davidcattoolsproject/pull/1179) 在檢查時仍是 open；**不等於整套 GUI 已上線**。
- 2026-11-06 的 [llmtool 中樞架構文件](../reports/llmtool-hub-architecture-final-20261106.md) 提供角色/流程設計參考，但其中 Mac/EC2 runner 拓樸可能落後於 11-08～11-11 的 GitHub-hosted CI 決策，**靜態圖必須以實際最新程式、設定及 Owner 決策修正，不能原封照搬歷史流程圖**。

### 2.2 Source of Truth 不變

- Task / D6 / Session、Agent runtime、PM board、GitHub Issue/PR、Reviewer verdict、CI checks 各自以**現行授權來源**為準。GUI 只顯示經確認或明確標示推導的結果。
- 同一工作的 Task、Issue、PR、Commit/SHA 等識別應關聯；有多對多情況（拆單、多 PR、重修）不能強制捏成一對一。
- §UNKNOWN§、§UNAVAILABLE§、§STALE§、§NOT_VERIFIED§、§BLOCKED§ 不得偽裝為 0、PASS、已完成、模型正在輸出。
- 監控現有重要語義：「**工作中 ≠ 模型正在產生內容；程序消失 ≠ 任務完成**」在 GUI 中原樣保留。

## 3. GUI 資訊架構（IA）與共通 UX

主導覽四頁，不常駐四面板：

| 主頁 | 目的 | 主要內容 |
| --- | --- | --- |
| **PM 對話** | Owner 與目前 PM/Session 溝通 | 聊天、歷史/Session、串流、草稿/中斷/錯誤、授權狀態 |
| **AI 監控辦公室** | 看「誰在做什麼，有沒有卡住」 | Agent/Session 卡片、告警、Owner 等待、最近活動、資源/額度可驗證摘要 |
| **PR／Issue 動態白板** | 看「事情做到哪一階段」 | Trello 式欄位、Issue/Task/PR 關聯卡、Reviewer/CI/merge 條件、卡住原因 |
| **流程圖** | 理解目前 llmtool 怎麼運作 | **靜態**、可讀的現行架構及任務流程圖（可縮放/平移） |

**次級入口：**「進階診斷」不是日常第五個同等級主頁；用工具選單/側邊次選單進入完整 Logs、D6/Task records、Processes、Activity 原始表格、監控來源/版本/更新時間，必要時可返回原 CLI/TUI 使用方式。

共通 UX：全站有目前專案/PM/監控資料新鮮度、重大告警入口；各頁獨立滾動與篩選狀態，切頁不丟聊天草稿或執行中的 Session；卡片點擊可在辦公室、白板與來源詳情互相跳轉；支援繁中、英文、窄視窗、鍵盤操作；外部連結開啟 GitHub 原始 Issue/PR/CI run。

### 3.1 功能相容性矩陣（現有六 Tab → 新 GUI）

| 現有 monitor view | 新 GUI 位置 | 功能保留重點 |
| --- | --- | --- |
| 1. §log§ | 進階診斷 / Logs | 時間、事件類別、最新/歷史、錯誤、篩選/追尾 |
| 2. §tasks§ | 白板卡片詳情 + 進階診斷 / Tasks | TaskStore、D6、工作紀錄、路由、完整錯誤碼及來源 |
| 3. §table§（Processes） | 辦公室 Agent 詳情 + 進階診斷 / Processes | PID/程序/啟動狀態，未知/無權限明示 |
| 4. §activity§ | 辦公室活動時間軸 + 進階診斷 / Activity | 事件時間、來源、背景/雜訊選項，關聯 PR 可跳白板 |
| 5. §office§ | **AI 監控辦公室主頁** | Office rows/樹狀關係、PM、Owner waiting、progress、stuck、quota、alert |
| 6. §board§ | **動態白板主頁** | work_board 狀態、PR/Review/CI/merge/阻塞等所有目前有的判斷 |

**驗收策略：** Claude/PM 必須列出實際 Office/Board 每一欄位的「現行來源 → GUI 欄位 → 測試案例」，缺任何欄位先標示差距，不用新卡片外觀掩蓋功能倒退。

## 4. AI 監控辦公室：卡片化，不做動畫

### 4.1 主畫面

- 上方：監控總覽（執行/等待/異常/未知數）＋規則告警、Owner 待決策、版本過舊/狀態來源失效；**計數只用有據可查的資料**。
- 中央：每一個有可辨識身分的 PM、Sub-PM、Developer、Reviewer、High Debug 等 Agent/Session 以卡片顯示，並按角色或群組分區。相同模型不同 Session 不誤合併。
- 卡片最少：顯示名稱/角色、Session 或可靠識別、狀態、正在處理的 Task/PR、最近活動時間、資料來源、更新時間；缺資料顯示未知。
- 下方或側欄：Activity 時間軸、PM progress、等待 Owner、stuck/error、quota/CI 摘要（若原 Office 有該區塊，必須有對應位置）。
- 詳情：點卡片可展開工作履歷、程序資訊、D6/Task/PR 關聯、失敗碼；敏感內容依現有遮蔽/輸出政策處理。
- 不用像素人物、走路、坐下等動畫。狀態可透過色標、字樣與輕量變更提示呈現；**不能只有顏色**。

### 4.2 狀態映射

以 Office 目前程式真實狀態為準，保留原始狀態與 raw code；GUI 標籤映射應有測試。對 §WORKING§ / §STALLED§ / §STOPPED§ / §ATTENTION§ 與其他狀態禁止亂合併。程序存在不等於模型正在輸出；程序消失不等於成功結案。資料中斷時顯示「上次看到的狀態（時間）」與 STALE，不繼續假裝即時。

## 5. PR / Issue 動態白板：Trello 式外觀，llmtool 狀態驅動

### 5.1 使用場景

這是**動態工作進度白板**，不是手動維護的 Trello 複製品。卡片會因真正的 Task、Reviewer、CI、PR 合併與阻塞狀態自動更新、移動；不可只根據 PR 標題、Issue label 或流程時間猜進度。

**候選欄位**（最後以目前 work_board 實際定義與 Owner 核可為準）：

1. 待規劃 / 待派工（Queued）
2. 開發中（Developing）
3. 測試 / 驗證中（Testing）
4. 審查 / CI（Review / CI：兩者**可能平行**，卡片內各自有狀態）
5. 待整合 / 待合併（Integration / Merge）
6. 完成（Merged / Done）
7. 例外群組（Blocked / Waiting Owner / Unknown）：可作為篩選或側欄，**不要只把卡住卡片移走造成主流程看不到**。

欄位是**UI 投影**，不是替代 TaskStore 的新狀態機。若既有 Board 的 stage 定義不同，要先完成對照矩陣，再決定畫面呈現；不可偷偷改判定。

### 5.2 每張卡

- 顯示 Issue / Task / D6 / PR 關聯、標題/摘要、Developer 路線、Reviewer、最近活動與等待時間、CI/Review/merge 狀態；卡片內並列多個驗證狀態，不錯誤宣稱單線性。
- 點卡片有具來源的事件時間線、commit/Head SHA、測試 run 連結、阻塞碼、Owner 待決策事項、下一步負責人（有依據才顯示）。
- 一個 Issue 可拆多 Task，一個 Task 可能有多次 PR/修復；必須顯示父子關係及追蹤 ID，不把同一工作無依據地去重；沒有關聯證據時明示「未連結」。
- 明確標示資料時間與來源，避免 GitHub API 延遲或 local TaskStore stale 造成畫面瞬移/反覆跳動。
- 新增 Issue 全量入口須查核 GitHub API 支援與成本：**現有 Board 偏 PR / PM 工作，不能聲稱已具備所有未開 PR 的 Issue 自動收納能力**。

### 5.3 動態邏輯

- v1 **唯讀**：不允許拖動看板卡片來改 Issue/Task 狀態，也不由 GUI 直接派工、合併、核准、重試。
- 建議低頻快照刷新（例如 5 秒作為可量測起點，最終由現有 monitor 快取/更新頻率決定），以 stable ID 及明確的階段轉移事件更新卡片位置；失聯不跳到 Done。
- 是否動畫只指卡片位置的短暫 UI 過渡，**不是 AI 辦公室模擬動畫**；可關閉動態效果。
- 必須能對照同時刻、同來源的原 monitor §board_view/work_board§，至少重要狀態及計數一致。若 JSON 輸出缺重要欄位，補的是**共用的唯讀資料契約**，不是第二套 Collector。

## 6. 流程圖：先靜態，延後動態與可編輯

### 6.1 v1 內容

獨立「流程圖」頁先提供至少兩張**靜態版本**：

- **角色/系統架構圖**：Owner ↔ llmtool Core ↔ PM / Developer / Reviewer / Debug / GitHub / GitHub Actions / 外部監控；註明哪些路徑由 Core 代理操作。
- **Task 生命週期流程圖**：需求 → PM/Task Contract → Worktree/Developer → 測試與 PR → Reviewer/CI 必要驗證 → 整合/合併；包含測試失敗/審查失敗的修復迴圈、等待 Owner、未知/阻塞分支。須註明這是通用示意，**不同工作可並行或依條件跳過可選步驟**。

**圖形建立規則：** 依 main 最新 §task_advance.py§、§task_drive.py§、§pm-continuation.md§、§worker-dispatch.json§、CI workflow 等實際來源繪製，禁止直接沿用已過期的 EC2/本機 Mac runner 歷史拓樸；在圖旁註明基準 commit/date、來源連結和「靜態、不代表當前 Task 即時位置」。

### 6.2 技術建議

v1 優先用版本化靜態 SVG（或 repo 可維護的 Mermaid 來源轉成靜態輸出），提供縮放、平移和清楚的分段內容；**不為了靜態圖導入 React Flow、workflow 編輯器、排程控制器或新的外部 CDN 依賴**。若 Claude 認為某前端元件更便宜，須列出 bundle、license、build/packaging、維護代價。

**延後項目**：流程節點即時亮燈、依 Task 執行事件動畫、拖拉連線/插入節點、修改 runtime 流程定義、版本遷移與 workflow executor。這些都不屬於本次 v1 完成條件。

## 7. PM 對話頁：共用既有 Session，先做缺口評估

- 目標 UX：完整對話、多行輸入、串流回覆、訊息佇列/中斷、歷史/Session 切換、來源與失敗狀態、Markdown/code/diff；切頁不丟草稿或正在執行的 PM turn。
- **不把 §prompt-toolkit§ TUI 或 CLI 程序當作可直接嵌進網頁的 Web API**。先辨識可重用的 Chat/PM Core adapter、Session lifetime、取消/中斷、輸出串流與權限限制，列出缺口。
- 目前 #1164 的 monitor loopback server 是**唯讀狀態接口**；不得將它隨手擴充為可任意執行 PM 指示的無認證 HTTP 入口。
- 聊天可以提出需求，但是否構成可執行授權，繼續遵守現有 PM/Owner 通道與 Core 規則；普通討論不能被 UI 自動轉為已授權派工。
- **Web PM 聊天的寫入 API、Session 控制/認證、敏感內容過濾、CSRF/localhost threat model，需要獨立的 Core contract 設計/審查**；本文件不是實作授權。
- 不在這次 docs-only 審查擅自承諾與 TUI 完全同等能力已具備。先出 evidence-backed gap matrix。

## 8. 共用資料契約與前後端邊界

§scripts/llmtool_monitor.py§ 已有唯讀 JSON 與 loopback server 基礎。可沿用，但要確認目前 merged code 已提供哪些欄位，不假設 Office/Board 全部輸出齊全。

**建議狀態快照的邏輯欄位（提案，非宣稱已實作）：**

- §schema_version§、§snapshot_id§、§generated_at§、§last_success_at§、§source_age§、§errors§ / §degraded_sources§。
- §office§：可穩定識別的 Agent/Session、role、status/raw_status、task_id/pr_id、activity timestamp、source。
- §board§：Task/Issue/PR 關聯、工作階段（UI projection）、各驗證狀態、blocking reason、來源/證據/最後更新。
- §alerts§ / §owner_attention§ / §activity§ / §diagnostics§：若來源合法提供才加入；不可擅自從公開 PR 推斷 Owner 已授權。
- 所有 UI 不可直接消費憑證、private prompt、模型原文、環境變數、敏感命令列、未經篩檢的 log。JSON 層屏蔽僅是其中一道保護；輸出白名單更佳。

§Monitoring View Adapter§ 概念：

1. 原有 monitor collectors / TaskStore / GitHub reader 負責取得事實與快取。
2. 同一份唯讀 schema 供 curses 監控與 GUI（必要時透過小型 adapter），**不複製 GitHub 輪詢器**。
3. GUI 視圖負責分類、排序、標籤、動態卡片位置和鑽取詳情，不做 Task 狀態決策。
4. PM 聊天另走**經過 Core 授權的互動適配器**，不得混用唯讀 monitor snapshot 作為控制通道。
5. 來源失效時提供有時間戳的 STALE/UNKNOWN 狀態，安全降級而非假成功。

### 8.1 安全與部署邊界

- 初期只允許本機 browser + §127.0.0.1§，**不啟用外網/區網/手機遠端存取**。
- 沿用現有 monitor server 的 loopback/Host/token 防護設計，審查是否有本機惡意頁面、DNS rebinding、快取、URL token 外洩、跨來源請求與敏感資料暴露風險。
- 狀態頁預設唯讀；任何可寫交互先經 PM/Core 授權路徑設計與單獨授權。既有敏感檔/權限/跨模型 Reviewer/GitHub merge gate 不變。
- 不新增第三方雲端 telemetry/CDN；UI 靜態檔與依賴應可離線打包。考慮 A13 發行後不綁 Owner 的 Mac 路徑。

## 9. 交付階段與依賴：小工作包，不重造 Core

| 階段 | 建議交付 | 驗收重點 |
| --- | --- | --- |
| **D0 — Claude review / gap matrix** | 審閱本文件、比對 #1164/#1230/#304/#683、確認現有 main 程式與資料契約 | 實證不足處標記 UNKNOWN；列出要補的欄位與 API、沒有重工 |
| **G1 — 既有 GUI 資料基礎整合** | 確認已合併 #1399/#1403/#1421 可用；處理 #1179 原 PR 後續關係 | 唯讀 loopback、正確遮蔽、快照、測試；不再平行複製 server |
| **G2 — Office cards** | 卡片主頁＋活動/告警/Owner waiting＋詳情 | 對照 curses Office 功能矩陣、狀態/計數正確、Mac browser 手動驗收 |
| **G3 — Live Board** | 獨立 PR/Issue 看板、欄位/篩選/詳情、自動刷新、Task/PR/Issue 連結 | 重要資料與 curses Board 一致、無錯誤移卡、無重複 Poller、未知安全降級 |
| **G4 — Static Diagrams** | 獨立流程圖頁、現況角色圖＋任務生命週期圖、來源與基準 SHA | 圖對應最新 runtime/policy/CI；縮放/平移、手機窄畫面可讀；不引入 workflow engine |
| **G5 — PM Chat feasibility → implementation（獨立 scope）** | 先做與 TUI/PM Core 的能力缺口表及安全 contract，另依授權拆實作 | Session/串流/中斷/草稿不丟；不得繞過 Owner/PM 授權；先不承諾工期 |
| **G6 — Advanced diagnostics** | 所有剩餘 Tab 的完整資料入口 | 日誌/程序/Task/Activity 功能對照、故障診斷可用 |

**不是強制的單線依賴**：G4 靜態流程圖可與 G2/G3 平行；G5 的設計分析可提早進行，但正式具寫入能力的介面不得跟 G1 的唯讀範圍混單。PM 依正在進行的 PR、權限與成本重新拆單/排序。UI 美化不得阻塞 A11c 核心交付。

## 10. Definition of Done / 測試矩陣

- **功能對照**：Office、Board 現有所有資訊與 critical 狀態，形成「原欄位 → 新視圖 → 資料來源 → 測試」矩陣；未覆蓋項不可宣稱 parity。
- **資料一致**：同一 fixture/snapshot 的 curses/GUI 重要狀態和計數一致；Unknown 與 stale 正確標示；CI/Review 並行或 out-of-order 事件不誤判；多 Task/PR 連結無資料遺失。
- **自動刷新**：真實狀態變化會更新白板，無刷新時資料穩定；過期來源不靜默保持綠燈。測量刷新延遲、CPU、GitHub API 次數、UI 記憶體，避免因前端多頁而重複輪詢。
- **操作穩定**：切分頁、快速篩選、展開卡片、長列表與大量 Agent、中文/Unicode、鍵盤瀏覽、Mac 瀏覽器窄視窗，皆有實際測試。
- **無權限回歸**：唯讀 GUI 不能派工、merge、核准、改狀態；測試 loopback Host/token/非 GET 請求與敏感資料欄位輸出。聊天 API 另有專門安全驗收。
- **部署相容**：CLI、TUI、舊 curses monitor 照常使用；GUI 不依賴自架 CI runner、個人 Mac 固定路徑或外部網頁資源。
- **誠實完成**：各階段標記 IMPLEMENTED / TESTED / MAC_VERIFIED / NOT_VERIFIED；只有實測後才宣稱可用，docs/PR/綠 CI 不能代替真實 UI 驗收。

## 11. 請 Claude 特別審查的問題

請對每題給出「現有程式/測試/Issue 的可核對證據、問題嚴重性、最小修法、是否阻塞 v1」：

1. #1164 的 G1/G2/G3 與本次獨立分頁決定有哪幾個互相衝突？哪些原本的 PR/工作包可直接沿用、哪些重複？
2. Office 的每個欄位、狀態碼、折疊資訊、Owner waiting、stuck、quota/CI 告警如何映射到卡片？目前 JSON schema 具體缺什麼？
3. 原 Board 的每個欄位如何映射？「Issue 全量」「Task↔Issue↔PR」多對多關聯目前是否有可靠權威 ID？
4. Review 與 GitHub CI 可能並行，白板如何忠實顯示而不失去直觀欄位/錯誤移卡？
5. 瀏覽器 PM 對話如何重用現有聊天 Session、背景工作、串流/中斷語義？缺 API 時最小可行實作是什麼？有哪些未授權的寫入風險？
6. 「流程圖靜態」如何依最新 main 和 GitHub-hosted CI 現況產出不過時的圖？應用 Mermaid/SVG/vanilla JS 哪個最小且方便維護？
7. 目前 monitor JSON Server 的身份/安全設計可支援 GUI 哪些範圍？是否有本機跨來源、URL token 或資訊外洩風險？
8. 四頁共享刷新快照，如何減少 monitor/GitHub API 使用量與 CPU？有哪些測量方式和上限建議？
9. G2/G3/G4 哪些能小單並行，哪些一定要排在前面？請給每單可能改動檔案、測試、依賴與粗略工程量，**不要立刻實作**。
10. 有沒有更簡單但不犧牲 Owner 已確認功能的方案？哪些是過度設計，哪些是會真的造成返工的缺口？

## 12. 對照資料與版本注意事項

- [Monitor GUI #1164](https://github.com/davidcatcat/davidcattoolsproject/issues/1164) / [D3 #1230](https://github.com/davidcatcat/davidcattoolsproject/issues/1230)
- [UI Agent #304](https://github.com/davidcatcat/davidcattoolsproject/issues/304) / [TUI UX #683](https://github.com/davidcatcat/davidcattoolsproject/issues/683)
- [Monitor curses implementation](../../scripts/llmtool_monitor.py)
- [TUI chat](../../tools/llmtool/llmtool/chat_tui.py)
- [Task advance](../../tools/llmtool/llmtool/task_advance.py) / [Task drive](../../tools/llmtool/llmtool/task_drive.py)
- [PM continuation](../../skills/ai-team-delivery/pm-continuation.md) / [Worker dispatch policy](../../tools/llmtool/worker-dispatch.json)
- [既有 TUI UI/UX 決策](../meetings/2026-10-22-llmtool-tui-ui-ux-decision.md)
- [llmtool 中樞架構（2026-11-06，部分 runner 內容可能過期）](../reports/llmtool-hub-architecture-final-20261106.md)
- [G1 PR #1399](https://github.com/davidcatcat/davidcattoolsproject/pull/1399)、[#1403](https://github.com/davidcatcat/davidcattoolsproject/pull/1403)、[#1421](https://github.com/davidcatcat/davidcattoolsproject/pull/1421)、原 [#1179](https://github.com/davidcatcat/davidcattoolsproject/pull/1179)

**最終提醒：** 此設計文件只為 Claude 技術評審；Owner 目前明確要求「流程圖先做靜態，慢慢來」。請勿因看到未來可擴充性就自行加入流程編輯器、卡片拖拉控制 Task、動畫辦公室、新授權引擎或大規模架構重寫。
