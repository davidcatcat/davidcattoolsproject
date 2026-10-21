# llmtool 產品 Roadmap、階段驗收與開發規則整合報告

- 日期：2026-10-21（Asia/Taipei）。
- Repository：`davidcatcat/davidcattoolsproject`。
- 分析基準：`303b96dbe312303607ecfbe01d13f6332568d5c9`；發布前已透過 GitHub API 核對 main。
- 作者：目前 Codex Integrator／PM。
- 用途：彙整本輪 Owner／PM 討論，交給 ChatGPT review；不是正式修改 Roadmap、規則或產品程式碼。
- 狀態：**報告候選，待 ChatGPT review**。Owner 明確回答的事項才列為「Owner 已確認」；PM 的階段細分、技術建議與 DoD 均另行標示。
- 本次新增授權：Owner 要求「上面所有的討論，整理成一篇報告，上傳 git，我給 chatgpt review」。此授權僅用於本報告的 Git 發布，不視為開發、修改現行規則或合併授權。
- PR #249：**CLOSED／NOT MERGED**；不恢復、不合併，不把其草案當成已接受政策。

## 一、摘要

產品要先交付 **Owner 這台 Mac 上真正可用的 llmtool CLI**。使用者直接輸入開發需求，由使用者指定的 Integrator／PM AI 理解與派工；llmtool 執行已授權計畫、管理 Task／Run／Session、背景程序、事件、交接及結果，不自行做語意派工或模型選擇。

Owner 最新確認的順序為：

**Mac CLI → A12.1 Telegram（需求待討論）→ A12.2 本機 Web UI → 其他人的電腦及其他平台支援。**

A11c 已有可重用元件，也有歷史真實 Host／Worker 成功及公司 gw 程式碼採用證據；但完整原生 CLI、多任務背景生命週期、可信自動交接及恢復仍未完成驗收。不能把外部 Codex／OpenCode CLI 的成功協作當成 llmtool 產品已完成。

規則曾精簡，但主要文件仍混有歷史契約、路線專屬限制與現行指令。建議以既有 AGENTS／Skills／Roadmap／README 分工整合，先修正矛盾和適用範圍，再評估繁重門檻；不藉整理取消有效安全控制。

## 二、Owner 已確認的產品需求與最新決策

### 2.1 六項產品需求

| 項目 | Owner 已確認的要求 |
| --- | --- |
| 原生入口 | 使用者直接開啟 `llmtool` CLI、輸入開發需求；不需先進入 Codex／Claude／OpenCode CLI，不需傳遞一般工作提示。 |
| 指定 PM | 至少連接一個使用者指定的 Integrator／PM AI。llmtool 沒有 AI 智能，不負責理解後自行拆需求、選模型或分派角色；由 PM AI 或使用者決定。 |
| 多任務 | 支援互不衝突的開發任務同時執行，個別追蹤 Agent、狀態、成果與阻塞。 |
| 背景與重接 | 關閉 CLI／Web UI 不取消已啟動的本機工作；重新開啟可查看、接回原工作，不必重送需求。 |
| PR 合併 | 由 PM AI／Integrator 按既定授權、測試、獨立審查、CI 與必要原生驗收決定；不是無條件自動合併。 |
| 公司 LLM | 經 llmtool 讓公司 gw 承擔適合的實際程式開發，降低大型付費 AI 消耗；產出須有測試及獨立審查品質證據。 |

安全、資料外送、Git、付費及 Provider 限制，仍依各自實際授權範圍執行。

### 2.2 首次交付與公司 gw 不可用

Owner 回答：「web版往後排，我的mac cli的版本先交付」。

Owner 同意公司 gw 暫時不可用時，先交付**清楚標明限制的 CLI 預覽**；不得宣稱六項需求全部驗收，也不得宣稱已證明節流成功。公司路線不可用不應拖停其他 Developer。

這項例外不取消公司 gw 實際開發的產品目標，不代表其他未驗收的 CLI 核心能力都可以自動標示完成。

### 2.3 指定 PM 無法使用

Owner 原話：「原本指定的integrator/pm無法使用的時候，其他ai正在進行的工作就先繼續做，做到需要pm在停止，新的派工項目也先暫停」。

操作語意：

- 既有 Agent 可繼續已獲授權且不需 PM 的工作。
- 到需要 PM 判斷、跨工作協調、新授權、Review 安排或整合決策時，保存成果並等待。
- 新派工暫停；需要 PM 的授權及合併暫停。
- 不因 PM 失聯自動換 PM、擴權、增加付費 fallback 或把任務視為完成。
- 恢復時先核對實際工作狀態，避免啟動第二個 Writer 或盲目重播。

睡眠、斷網及關機期間，不宣稱本機持續運算。24 小時無人值守、開機自啟及雲端 Worker 不是首版必要需求。

### 2.4 A12 拆分與產品順序

Owner 先指定：「我的mac cli優先，再來是telegram(需求要再討論)，本機web ui，其他往後」；隨後要求：「telegram和web ui在A12底下，分項目拆開吧」。

本報告記錄為：

| 順序 | 項目 | 已確認／未定範圍 |
| --- | --- | --- |
| 1 | Owner Mac CLI | 首次交付優先。 |
| 2 | A12.1 Telegram | 放在 A12 下獨立子項目；詳細需求、功能及 DoD 仍待討論。 |
| 3 | A12.2 本機 Web UI | 放在 A12 下獨立子項目；監控與提交需求等產品方向已確認，細部切片待定。 |
| 4 | 對外安裝、其他 macOS／Linux／Windows 等 | 保留通用工具方向，但往後安排，彼此精確順序尚未確定。 |

Telegram 與 Web 共用同一 llmtool 核心、Task／Session／事件及權限；不另建第二個 PM、Controller 或協調引擎。本機 Web 指在本機啟動、以瀏覽器操作，不自動包含公開網站或遠端 hosting。

先前 PM 曾建議 Web 先於 Telegram、獨立 macOS 安裝較早；**這些排序建議已由上述 Owner 最新順序取代**。

### 2.5 Token 政策

Owner 要求記錄實際消耗及品質，評估公司 gw 是否減少大型付費模型使用；沒有授權以下通用限制：

- Token Usage UNKNOWN 就不得 APPROVE。
- 超過 PM 自訂節流目標，就否決有效 Code Review。
- 只因超過節流目標，就停止後續正常開發。

UNKNOWN 應保持 UNKNOWN，不能寫零或虛構節省比例。Code Review 的 Security／Functional／QA 與成本統計須分開。

其他有獨立授權來源的呼叫限制、Provider cap、付費支出、資料保護或安全控制，不因 Token 澄清而自動解除。不得把 PM 自行推導的政策記成 Owner ACCEPTED。

## 三、目前能力與證據：完成、有限驗證、尚未完成

以下是對既有來源與驗收紀錄的整理，**本輪未重新執行測試、Provider 呼叫或 Mac Live 驗證**。

| 能力 | 既有證據 | 可主張範圍／限制 |
| --- | --- | --- |
| 基本 CLI、設定、Provider／Profile、對話、唯讀 Git、檢索 | main 的 A0–A10 程式及 README | 能提供文字協助與確定性查詢；不是完整開發 Task runtime。 |
| A9 orchestration | `orchestration.py` 及 README | 有界、循序、文字交接；不執行 Patch、測試、Shell 或 Git，不代表多開發任務並行。 |
| Chat TUI、真實狀態 | [PR #218](https://github.com/davidcatcat/davidcattoolsproject/pull/218)，merge `6afba7a3` | UX-1／2 已有實作；歷史仍在記憶體，不是可恢復的開發 Task。 |
| A11a 診斷／metadata／trace／doctor | README、A11a 驗收 | 診斷持久化不等於 Task／PM Session 持久化，不能直接證明背景重接。 |
| Task 綁定及事件結構檢查 | [#128](https://github.com/davidcatcat/davidcattoolsproject/pull/128)、[#216](https://github.com/davidcatcat/davidcattoolsproject/pull/216) | 已有型別、來源及序列檢查；`inspect_task` 仍回報 `NOT_VERIFIED`／`CALLER_SUPPLIED`，不是可信 runtime 事件產生器。 |
| Review／Host authority 元件 | [#133](https://github.com/davidcatcat/davidcattoolsproject/pull/133) 等 | 元件與 fake-first 驗證已有交付；public factory 仍不可用，不能宣稱通用產品入口已串通。 |
| 歷史 Host-contained S3／M1 | [S3 證據](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6261806060)、[M1 獨立 Live 驗證](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6271475617) | 曾有真實推論、開發及自有程序清理成功；不等於目前 D8a 路徑或完整 M2 已通過。 |
| D8a／D4 最近狀態 | [原 UNKNOWN 更正](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6295069067)、[第二次失敗](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6295624044)、[#240](https://github.com/davidcatcat/davidcattoolsproject/pull/240) | 原 Job 仍保留 UNKNOWN；symlink 是最有證據的根因，不能回填原本未持久化的 Stage。第二次 auth 失敗。#240 已修復診斷，但未據此重跑 Live 或取得新 PASS。 |
| 公司 gw 程式碼 | [候選品質審查](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6272588436)、[#213 採用](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6273637132) | 有通過測試、獨立審查及採用的有限案例；也有失敗候選。不是「從未成功」，亦不是穩定閉環或整體節流已驗收。 |
| Activity Monitor | [#252](https://github.com/davidcatcat/davidcattoolsproject/pull/252)、[最新 #120 報告](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6303341913) | Terminal 可顯示可理解活動；自述來源仍須標示。#225 已有 Mac 驗收，不代表 Web 或可信自動完成事件。 |
| Token 事後否決修正 | [#248](https://github.com/davidcatcat/davidcattoolsproject/pull/248)，merge `5dffad2e` | 已移除 Review 的事後 Token veto；不要誤稱所有其他 prospective 控制都已取消。 |
| F1／Codex Pickup 舊實作 | [#235](https://github.com/davidcatcat/davidcattoolsproject/pull/235) | 先前驗收保留為歷史；相關 parked modules 已移除，不能列為 main 仍可執行的能力。 |

最新 #120 報告記錄：公司 gw 新一輪 Patch 請求逾時，UNAVAILABLE、無程式碼與用量；可信自動完成事件、PM 接收、Review 退修回交及完整 M2 仍未完成。這不抹除較早有限成功案例。

### 3.1 外部協作不等於產品功能

已證明 Integrator 可用現有 CLI 向 OpenCode Session 派工，Developer 可測試、自審、交付 PR；PM 也能用既有工具安排 Reviewer、GitHub 與合併。

但由外部 Codex 工具發起的上述操作，不證明使用者從 `llmtool` 入口即可取得相同閉環。PM 手動準備 Worktree、工具啟動 Reviewer、讀取回報及執行 GitHub 操作，都應記為當前工程流程，不能直接算成 llmtool 已內建。

## 四、A11c 修訂提案（PM 建議，尚待 review／正式採納）

### 4.1 功能歸屬與 DoD

| 既有階段 | 建議責任 | 建議完成標準 |
| --- | --- | --- |
| A11c.1：原生入口與指定 PM | CLI 提交需求、PM 設定／連接／啟動／恢復／失聯狀態 | 使用者不先開其他 AI CLI、不傳提示；能核對實際 PM／模型／Session，無靜默替換；確定性離線查詢可不需要 PM。 |
| A11c.1：Task 與背景生命週期 | Task／Run／Session／Worktree／Revision 持久關聯、核准計畫與最小必要證據索引 | 至少兩項獨立任務真實並行；關閉介面後工作持續，重開接回原 Task，沒有重送需求或第二 Writer。 |
| A11c.1：開發與審查交接 | 可信事件、測試、自審、獨立 Review、退修回原 Developer、PM 整合 | Task／SHA／測試與事件綁定；舊版 PASS、重播、自述完成不誤獲採用；一般提示與報告不經 Owner 傳遞。 |
| A11c.2：Mac CLI 實際驗收 | 把以上能力組成真實使用流程，沿用既有三工作包 Mac pilot | 覆蓋成功交付、Review 退修、並行、介面關閉重接、PM／Provider 中斷及 UNKNOWN；測試、獨立審查、exact-head CI、適用 Mac 證據齊備。 |

以上細分不新增 A 系列 Milestone。背景執行管理是同一核心的生命週期責任，不是額外語意 PM 或重複 Controller。具體程序／服務方案、儲存格式、CLI 指令名稱及最大併發值仍屬待驗證技術設計，不在本報告先定案。

Task 恢復需要的最小狀態與證據索引，不能直接等同於保留全部原始對話；不得因此將憑證或受限內容放進 diagnostics。優先沿用現有事件、Receipt 與執行介面，維持共同狀態來源。

### 4.2 G3／M2／A11c.2 的區分提案

- **G3：首次原生真實閉環證據檢查點**，從 llmtool 入口經指定 PM、Developer、測試、獨立 Review、必要修復，回到 PM 作合併或 STOP 決定。
- **M2：完整核心閉環驗收**，除單件成功，還包含多任務、持久化、背景繼續、重接、失聯處理與防重播。
- **A11c.2：真實產品驗收矩陣**，以真 Repo／工作包證明正常與失敗行為，形成可重查報告。
- G1–G4 保持既有報告檢查點；不變成額外 Milestone。G3 原先與 M2 重疊的描述需一起校正。
- 指定 PM 優先恢復原 Session；原生 Session 與邏輯 PM 身分如何映射、無法恢復時如何明確授權替換，仍需技術設計，不能靜默換 PM。

### 4.3 首次 Mac CLI 應能做什麼

使用者啟動 llmtool、確認指定 PM 與 Repo，直接輸入開發需求，取得 Task ID；PM 決定分工，已授權 Agent 執行；可看進度、測試、Review、修復及 PR／CI，PM 按授權整合或明確停止。

多任務及 UI 關閉後背景繼續／重接是已確認需求，不能延至 Web 才補。公司 gw 暫時不可用可以適用 Owner 確認的受限預覽例外，清楚列出尚未驗收項目；資料不足時不能聲稱節流成功。

## 五、A11b：首版安全關卡與不同模式的後續能力

### 5.1 首版啟用真實開發必須維持的控制

- Task、Agent、Worktree、來源 Revision、可寫範圍與實際權限綁定。
- 秘密保護、資料檢查、適用路線的外送授權與 Provider 控制。
- 一個 Worktree 一個 Writer；取消、逾時及僅回收自有程序。
- 真實測試證據、獨立正式 Review、exact-head CI 與必要 Mac 驗收。
- 重播防護、未知副作用保留、既有資料保存及授權內 Git 整合。
- UI 退出不取消 Task；需要決策而 UI 不在時保存等待狀態，不擅自批准。

### 5.2 不同操作模式不能混為一談

| 模式 | 必要界線 |
| --- | --- |
| 獲授權 Developer CLI 在自己的 Worktree 修改、測試及 Git 操作 | 不以完整 llmtool F2 為通用前置條件；但實際啟動與執行範圍仍需驗證。 |
| 公司 gw 產出 Patch，由既有獲授權 Executor 套用 | 必須有真實套用、測試及獨立驗收路徑；gw 文字不帶 Shell／Git／檔案權限。 |
| llmtool 自己直接執行 Repo／Git mutation | 在啟用前完成該模式的 F2、Action、Approval、Executor 與安全驗收。 |
| 泛用 Shell、廣泛 Patch／Rollback、更多 OS 執行模式 | 作為相應後續能力規劃；不能從首版需求推導出自動授權。 |

不能把「F2 不必阻擋 Developer CLI」解讀為執行器不需安全控制；也不應為首版強制恢復所有已封存 F1 程式。

## 六、A12、對外安裝與依賴

### 6.1 A12.1 Telegram（Owner 確認位置與順序，需求未定）

進入此項前需討論：

- 只有進度通知，還是可提交需求及與 PM 對話？
- 是否能暫停／取消、處理必要操作與授權？
- 顯示摘要，還是測試／Review／PR／CI 詳情？
- Mac 睡眠、關機、網路中斷時如何呈現未送達及離線？

這些是待討論產品問題，不能把原 Roadmap 的 Bot API long polling、指令及 approval 方式直接當成 Owner 新確認的需求。Telegram 不要求本輪加做，也不阻擋 Mac CLI。

### 6.2 A12.2 本機 Web UI

已確認方向：完整工作流監控、視覺化任務與資料流、模型、測試、修復、Review、PR／CI；可輸入提示、提交與追蹤需求、查看必要決策及授權。

PM 建議分批：先真實事件的監控與證據檢視，再互動提交及操作，最後持續改善視覺。這是交付切片建議，不把唯讀監控當成完整 Web 需求已交付。

前後端與 CLI／Telegram 共用核心及權限。動畫來自真實事件，明確標示自述、驗證、等待及 UNKNOWN。本機存取優先；是否遠端開放、登入與部署細節另行設計。

### 6.3 其他使用者與跨平台

保留其他人用自己的電腦、Provider 帳號／憑證、Repo 獨立使用的方向，但 Owner 已將它排在 Mac CLI、Telegram、本機 Web 後。

不把公司 VPN 或 Owner 私有路徑當作通用產品前提；也不在第一版要求跨平台實作或獨立電腦驗收。新設計盡量隔離環境設定，避免日後不必要的重做。

[PR #237](https://github.com/davidcatcat/davidcattoolsproject/pull/237) 的硬編碼路徑／Repo、版本 pin、Host 登錄、POSIX／Windows 限制分析仍有價值；「先保持私人工具」只是作者建議，不是產品定位。每個人獨立安裝不等於多人租戶 SaaS。其他 macOS、Linux、Windows 與發布管道的詳細先後尚未決定。

### 6.4 必要依賴與可並行項目（PM 建議）

Mac CLI 所需的 PM 接入、執行安全、Task 狀態、可信事件及背景生命週期是核心依賴；既有閉環接通後才可做 A11c.2 整體驗收。

資料診斷、相關 Bug 修復與核心內不衝突的元件可在將來授權開發後並行；公司路線不可用不阻擋其他 Developer。D4／G3／M2 背景工程不阻擋當前正常 OpenCode Developer。

Telegram 及 Web 依賴同一核心；不為 UI 另造調度系統。完整 F2 僅在啟用 llmtool 直接寫入模式時成為必要依賴。跨平台、遠端 hosting、自啟及完整節流比較，不是 Owner Mac CLI 受限預覽的通用門檻。

## 七、開發限制規則盤點

### 7.1 目前主要限制與處置建議

| 類別 | 目前限制 | 建議處置 |
| --- | --- | --- |
| 資料／憑證 | 不洩漏秘密、私人或受限資料；D8a 只在授權路線記憶體使用單一憑證 | 保留，集中標明適用來源、目的地與執行模式。 |
| Git／D6 | 登記獨立 Worktree、一個 Writer、保護他人資料、不強制破壞操作 | 保留，操作細節只放 safe-git 及其參考。 |
| 品質 | 開發測試、自審、獨立正式 Review、exact-head CI、適用 Mac 驗收 | 保留；每個 Task 明列所需 Gate，不重複要求無關驗收。 |
| 模型／費用 | 免費 OpenCode 路線、無隱藏付費 fallback、公司 gw 經 llmtool | 依各自授權執行，不推廣為所有路線同一限制。 |
| Host／Worker | Admission、cap、Retry stop、Receipt、執行檔驗證、清理 | 限定路線，不拿來阻擋正常 direct Developer Session。 |
| 公司 gw | 小型低風險工作、資料檢查、受控修復、獨立驗收 | 修正只准 triage 的舊說法；保留模型沒有直接執行權限。 |
| Owner 升級 | 現行 AGENTS 指定需求錯誤、root/sudo、Mac 核心系統修改 | 統一表述；例行技術處理由 PM 解決，未授權動作仍不得執行。 |
| Token | 實際使用量及 UNKNOWN | 成本統計與品質分離，不新增通用否決或停工規則。 |

### 7.2 本輪發現的具體風險

1. **Owner 升級範圍不一致。** AGENTS／owner-escalation 的現行界線，與 WORKFLOW 第 59 行列出的較廣範圍不一致。需分清例行授權內處置、授權外不得執行、產品接受與必要 Owner 決策，避免再製造逐次批准。
2. **公司 Skill 與最新 Developer 分工不一致。** `skills/company-llm-delegation/SKILL.md` 的 Decision order 仍偏向分析，WORKFLOW 第 438 行仍寫 company only triage。這不能覆蓋較新的 gw 真實開發決策。
3. **特殊路線規則容易被泛化。** C1B 的 Owner-only Phase 0 grant 屬該 Worker 路線；不能解讀成所有 gw Developer 呼叫都需逐次 Owner grant。D4 同樣不能阻擋正常 OpenCode Developer。
4. **Preflight 過度綁定環境。** `preflight-reference.md` 第 95 行固定 `tools/llmtool/.venv`，並嚴格比對 ignored inventory。合法替代 Python、預期產物變更可能導致重複 baseline 處理；應由技術審查決定適用範圍及改善，不能直接跳過或放寬來源檢查。
5. **歷史／現行混讀。** Roadmap／Workflow 還有固定 Session、固定 root、舊階段停止與已封存實作描述；需要明確限定歷史或模式。
6. **多層預審可能增加等待。** 目前 OpenCode 不同免費模型預審是有效要求，無路線時會阻擋該任務。是否對所有工作保留相同流程可另行評估，但本報告不取消此 Gate。
7. **C-6 不是品質豁免或通用 Token 停工。** 既有修復／審查收斂政策要求 PM 作成本效益及範圍處置；不等於超出節流目標就能否決有效 Review，也不能拿次數替代真正安全與功能驗收。

### 7.3 文件規模（main 基準，非 Token 估算）

| 文件 | 行數 | 位元組 |
| --- | ---: | ---: |
| AGENTS.md | 36 | 6,616 |
| CLAUDE.md | 13 | 2,341 |
| ai-team-delivery/SKILL.md | 93 | 9,781 |
| safe-git/SKILL.md | 67 | 6,953 |
| ROADMAP.md | 1,448 | 121,694 |
| WORKFLOW.md | 1,958 | 144,012 |
| README.md | 3,040 | 196,074 |

前三份大型產品／流程文件合計 **6,446 行**。AGENTS／CLAUDE 已精簡，不應繼續沿用舊的 275 行 AGENTS 或啟動必耗多少 Token 的說法。這些是文件大小，不代表每個 Session 實際載入量；本輪未量測 Input／Cached Input／Output，不能宣稱精簡節流比例。

## 八、規則整合方式（PM 提案，不是本次已修改）

沿用現有文件，不新增一套管理架構：

| 文件 | 唯一主要職責 |
| --- | --- |
| AGENTS.md | 現行共通角色、授權、安全底線、Owner 升級界線。 |
| CLAUDE.md | Claude 特有事項與共通入口，不重抄角色、Gate、政策。 |
| ai-team-delivery | Task Contract、開發／測試／Review／整合操作流程，不另定權限。 |
| safe-git 及參考 | Git、D6、preflight、保留及退役細節。 |
| ROADMAP | 產品範圍、優先序、依賴、DoD、驗收狀態，不新增開發禁令。 |
| WORKFLOW | 必要工程操作及跨環境交接，引用現行政策；歷史長契約移到對應歷史／規格。 |
| README | 現有功能、安裝、操作及限制，不複製治理規則。 |
| 決策／history | 原始 Owner 決策與歷史證據；被取代內容清楚標註，不再當現行指令。 |

每條限制建議寫清楚：**授權來源、適用模式、阻擋的動作、處理角色與恢復條件**。這些欄位可用簡短表格，不另建資料庫或 Controller。

例子：

| 情況 | 應阻擋什麼／由誰處理 |
| --- | --- |
| Token UNKNOWN | 不能宣稱成本／節流已驗證；不阻擋有效 Code Review。 |
| 公司 VPN／Provider 不可用 | 停該路線，記錄 UNAVAILABLE，PM 繼續其他工作。 |
| Worktree 所有權不明 | 停止該目錄寫入或刪除，PM 核對；不停止所有任務。 |
| 正式 Review FAIL | 阻擋該候選合併，原 Developer 修復；適用的重審及 CI 完成後再整合。 |
| PM 失聯 | 現有工作做到需要 PM，暫停新派工與需要 PM 的決策。 |
| 未確認的秘密／權限風險 | 保留證據，不執行受影響動作；不得因其他任務可繼續就忽略此限制。 |

先去重、修正衝突、隔離歷史，再逐條評估繁重門檻。仍有效的不同模型預審、安全／Provider cap、資料保護等要求，不得藉文件整理偷偷取消。

## 九、正式修訂文件時的變更清單（尚未執行）

- ROADMAP 開頭與 current snapshot：對齊 Owner 六項需求、最新交付順序與歷史 M1／公司採用證據。
- A11c.1／M2／A11c.2：補上指定 PM、原生 CLI、並行、持久化、背景及重接，分清元件交付與產品驗收。
- A12：拆 A12.1 Telegram、A12.2 本機 Web UI；Telegram 需求留待討論，不挪用舊假設冒充接受。
- A3.5 UX：校正 UX-1／2 已有實作；A9 明確標為循序文字交接。
- F1／Pickup：目前可用狀態改為歷史封存，保留驗收來源，不恢復 #235 移除內容。
- Token／budgets：成本紀錄與有獨立來源的控制分開，不整批刪除 run_budget 或各路線 cap。
- WORKFLOW／Skills：修正 triage-only、升級界線、歷史固定環境及模式範圍；必要技術改動另走相應 Review。
- README：分清現在能用、尚未驗收、未來目標；不宣稱安裝發布或 Web 已上線。
- AGENTS／CLAUDE：保留短入口，消除重複，引用單一現行來源；不重新堆疊長歷史段落。

## 十、待 review 的問題與真正未知

### 10.1 請 ChatGPT review

1. Owner 最新順序與 PM 失聯語意是否記錄正確？有無把 PM 建議冒充 Owner 決策？
2. A11c.1／M2／A11c.2 的分工，是否足以交付 Mac CLI 而不重複設 Milestone？
3. 是否正確區分歷史 S3／M1 成功、目前 D8a 未驗收、外部 CLI 流程和 llmtool 原生能力？
4. gw 不可用時的受限預覽，是否保留公司實際開發目標與品質要求？
5. A11b 是否正確依啟用模式保留安全 Gate，沒有把 F2 變成所有模式通用前提，亦沒有繞過執行權限？
6. 規則整合是否先解決來源與適用範圍，而非降低品質或新增治理？
7. 哪些 DoD 或順序需再調整，哪些可由 PM／Developer 以技術證據處理？

### 10.2 由技術調查回答，不要求 Owner 診斷

指定 PM 的實際啟動／恢復／結果接收，背景執行與重接、持久化及副作用對帳，多任務隔離，可信完成／Review 事件，公司路線品質與可用性，Mac 首次設定及啟動，仍需真正整合與測試證據。

既有單件成果不是完整閉環 PASS。未知不能填零、假造執行紀錄或由 PID 推定完成。PM 配置／儲存／程序／CLI 語法的例行選擇，由 AI 團隊在授權範圍內調查，不逐項上交 Owner。

### 10.3 後續產品決策

Mac CLI 的方向已足夠明確；目前沒有必須重新問 Owner 才能整理 Roadmap 的產品問題。Telegram 詳細需求在 A12.1 前討論；Web 的細部操作／存取、發布管道、其他平台精確次序仍留待相應階段。

本報告的詳細 DoD、規則整合方案尚待 review／正式採納。報告發布不代表已允許開始上述開發。

## 十一、來源與本次驗證界線

### Repo 基準文件

以下連結固定在分析基準，避免未來 main 漂移：

- [原產品需求會議紀錄](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/docs/meetings/2026-10-21-llmtool-product-requirements.md)。
- [ROADMAP](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/tools/llmtool/ROADMAP.md)、[WORKFLOW](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/tools/llmtool/WORKFLOW.md)、[README](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/tools/llmtool/README.md)。
- [AGENTS](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/AGENTS.md)、[CLAUDE](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/CLAUDE.md)。
- [ai-team-delivery](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/skills/ai-team-delivery/SKILL.md)、[safe-git](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/skills/safe-git/SKILL.md)、[公司委派 Skill](https://github.com/davidcatcat/davidcattoolsproject/blob/303b96dbe312303607ecfbe01d13f6332568d5c9/skills/company-llm-delegation/SKILL.md)。
- [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)、[Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172)、[歷史移植性評估 #237](https://github.com/davidcatcat/davidcattoolsproject/pull/237)、[#249 關閉狀態](https://github.com/davidcatcat/davidcattoolsproject/pull/249)。

### 新增決策的來源

第 2 節後續確認來自本輪 Owner 對話原文，沒有獨立既存 GitHub comment URL；不冒稱它們已在原會議檔、#249 或 main 上正式修訂。本報告是此次彙整與待審發布載體。

本輪只作來源／狀態讀取、文件內容整理及報告發布。應在 PR 記錄實際文件檢查與遠端 readback；不宣稱應用測試、CI、獨立正式 Review、Live 推論或合併完成。原先只讀規劃限制，僅由 Owner 最新指示對這份報告發布作例外，其餘保持不變。
