# llmtool 產品需求、操作入口與背景執行 — 討論會議紀錄

- **日期：** 2026-10-21（Asia/Taipei）；**2026-10-22 追加 Desktop GUI Roadmap（第九節）**
- **性質：** Owner 與 ChatGPT 的需求討論／決策紀錄，供後續正式規劃參考。
- **效力：** 下文「Owner 已確認」才是本次產品需求；「建議、待定」不等於 Owner 已接受。
- **本次工作範圍：** 只保存會議紀錄；不代表 Roadmap 已更新、功能已實作、任務已派送、預算／執行／資料外送權限已核准。
- **管理要求與後續授權：** 目前仍在定義產品階段，不得自行派工或修改 Roadmap。原本只授權建立會議紀錄；後來 Owner **另行要求** PM 將整體討論報告上傳 Git 供 ChatGPT review，因此產生僅含報告的 Draft PR #255；這不等於產品開發或合併授權。現在 Owner 明確要求**更新本既有會議紀錄**，本次直接更新此檔，不新增 PR／Issue、不派工。

## 一、產品定位（Owner 已確認）

llmtool 應是使用者直接操作的**通用本機 AI 協作工具**，不是某個 Codex／Claude／OpenCode CLI 的附屬設定，也不是只能裝在 Owner 自己 Mac 上的私人專案。

- **llmtool 本身沒有 AI 智能，也不做語意判斷或自主的模型／任務分配。** 它負責介面、已授權的工具與 Provider 連接、Task/Run/Session/事件狀態、執行與交接、記錄、監控及結果呈現；可以有確定性的輔助功能。
- **至少要連上一個使用者指定的 Integrator／PM AI，才能執行需要 AI 企劃與開發協作的流程。** PM AI 負責理解需求、拆解、決定 Developer/Reviewer 等角色、模型及分工；使用者也可以自行指定角色／模型。llmtool 不取代這些決策。
- **節省大型付費模型 Token 的方法由 Integrator／PM AI 或使用者決定。** llmtool 提供可用路線與可核對的用量／產出紀錄，不自行另造模型成本評分或智慧 Router。
- Codex、Claude、OpenCode 等可作為背後的 CLI／Agent runtime，並不意味著使用者要先進入其中一個 CLI 才能操作 llmtool。

## 二、六項使用者行為：決議結果

| 編號 | 主題 | Owner 決議 | 完成時應看到的行為（需求，不是已實作） |
| --- | --- | --- | --- |
| 1 | 原生 CLI 入口 | **是** | 使用者直接啟動 `llmtool`，輸入開發需求；不需先開 Codex／Claude／OpenCode CLI，也不需人工傳遞一般工作提示。 |
| 2 | PM AI 與角色／模型分配 | **是** | llmtool 至少連接一個**指定的 Integrator／PM AI**；其他 AI 角色與模型可以交給 PM 決定，或由使用者指定；llmtool 不自行做 AI 分工判斷。 |
| 3 | 多任務並行 | **可以** | 同時處理多個互不衝突的開發任務，各自追蹤狀態、負責 Agent、結果及阻塞；具體排程與衝突管理由 PM／既有安全機制處理。 |
| 4 | 關閉介面後是否繼續 | **採用 A：繼續執行** | 關閉 llmtool CLI／Web UI 介面後，已啟動的本機工作繼續背景執行；重新開啟可查詢任務與進度，不須重新送需求。 |
| 5 | PR 合併 | **是，由 PM AI 決定** | PM／Integrator 按已授權的 Git、測試、獨立 Reviewer、CI 等驗收規則決定合併；這不是跳過權限或自動合併所有 PR 的許可。 |
| 6 | 公司 LLM Developer | **是** | PM 或使用者可把適合的真實開發需求經 llmtool 交給公司 LLM；產出包含程式碼／測試提案，品質須通過必要的客觀測試與獨立審查。 |

### 第 4 項背景工作：明確的產品語意

Owner 原話：「**第④項決定採用A：關閉llmtool介面，已啟動的工作繼續在本機背景執行。**」

- **關閉介面 ≠ 取消 Task。** 背景執行程序須獨立於 CLI／Web UI 的顯示生命週期。
- 再次開啟介面應能看到可信的 Task/Run 狀態、歷史及最新結果；不得把程序退出或失聯直接宣稱為完成。
- 單一 Agent／Provider 異常終止時，留下正確中斷／未知證據，交由指定 PM 決定是否恢復、重新派送或停止。
- 電腦睡眠、斷網、關機期間**不宣稱本機仍持續執行**；之後先核對實際狀態，再依已驗證的恢復能力處理。
- 這個需求**不等同**要求首版就具備 24 小時無人值守、開機自啟、遠端常駐服務或雲端 Worker。前景／背景 Supervisor 的技術方案尚未決定。

## 三、公司 LLM 的原始目標與 Token 規則糾偏（Owner 已澄清）

Owner 要求的是：**透過 llmtool 讓公司 LLM 承擔適合的實際開發工作，減少 Codex、Claude 等大型付費 AI 的 Token 使用，但公司 LLM 的開發成果必須有品質。**

與此目的不符、**未由 Owner 提出或核准**的通用規則，不得歸因為 Owner 決策：

- 「Reviewer Token Usage UNKNOWN 就不得 APPROVE」。
- 「Token 超過自行設定的節流目標，就禁止有效的程式碼審查通過」。
- 「只因 Token 超過原定目標，就通用地限制之後的開發或停止任務」。

**品質驗收與 Token／成本統計須分開。** UNKNOWN 應照實記錄 UNKNOWN、不能改寫成零，也不能聲稱已驗證節流成功；但成本資料缺失本身不是 Code Review 品質不合格。其他確有獨立授權來源的安全、資料外送、金錢支出或 Provider 執行控制，不因本次澄清而自動取消，亦不得誤稱出自上述節流目標。

公司 `gw` 應是選用的 Provider／Developer 路線，而不是通用 llmtool 的必要依賴。實際程式碼套用、Shell/Git 執行和最終驗收由相應獲授權環境與角色執行；模型文字輸出不自帶檔案／權限。

## 四、llmtool CLI、Web UI 與其他人的電腦

### CLI（Owner 已確認為正式入口）

- 在 Terminal 啟動 `llmtool` 後，可直接提交實際開發需求，連接指定 PM，收到任務 ID 與進度、測試／Review／PR 結果，必要時作 Owner 決策。
- `llmtool chat`／TUI 已存在基本文字對話能力，但**不是**已驗收的多 Agent 自動開發入口；不能把 CLI 能聊天誤當成產品完成。
- 使用者需要能找到 llmtool 的下載／安裝及初次設定說明。下載來源、打包與發佈方式**尚未決定**；不得把未發佈的安裝管道說成已上線。

### Web UI（Owner 已提出並再次確認的產品功能）

**最新排序更新：本機 Web UI 已定為 A12.2，排在 Mac CLI 與 A12.1 Telegram 之後。**

Owner 在 2026-10-19 已討論透過 Web 觀看 llmtool 的 Agent 工作流、資料流、狀態與監控數據，並希望有更有未來科技感的動態視覺化。本次進一步確認 **Web 不只看監控，也必須可以輸入提示／需求、提交和追蹤工作、查看結果與必要的操作／授權**。

- 觀察需求：使用者需求 → PM 分工 → Developer（Codex／OpenCode／公司 LLM 等）→ 測試 → 獨立 Reviewer → 修復 → PR/CI；真實顯示等待、進行、阻塞、完成、交接結果與相關證據。
- 操作需求：從 Web 下需求、看 PM 回應、管理及追蹤任務，經**同一套既有 llmtool 後端與權限契約**提出必要操作。Web UI 不應另有自己的 PM 或協調引擎。
- 專案目前存在 Terminal Monitor／Activity 規劃，**不能因此宣稱 Web UI 已經交付**。
- 依賴、UI 架構、版面設計、實際第一版 Web 功能邊界及對外存取方式**尚待規劃與驗收**。

### 其他人的電腦（Owner 已確認為產品方向，實作時序尚待正式排定）

llmtool 將來應可分享給其他使用者安裝在**自己的電腦**，使用自己的 Provider／CLI 帳號與 Git Repo；不能要求拿 Owner 的 Mac 路徑、公司 VPN、帳號或憑證才能運作。這是**每個人獨立安裝的工具**，不等同於共用一台多人租戶 SaaS。

- 既有 [PR #237 移植性評估](https://github.com/davidcatcat/davidcattoolsproject/pull/237) 記錄了硬編碼路徑、固定 Repo、CLI 版本、Host 登錄及 Windows 限制。其「先保持個人 Mac 工具」是作者建議，**不是 Owner 最終產品定位決策**。
- **Owner 後續已正式決定優先順序：** 先完成自己的 Mac CLI，再做 A12.1 Telegram（需求尚待討論）、A12.2 本機 Web UI，最後才安排其他人的電腦和 macOS／Linux／Windows 支援；各平台的內部先後及發佈方式仍未決定。不得再將「其他人的 macOS 安裝先於 Web」記為最新 Owner 順序。

## 五、A11c 與後續階段：本次釐清的界線

- **A11c 核心協作目標：** 透過至少一個指定 PM AI 完成真實任務 → PM 分派 → 已授權 Developer 工作 → 測試、獨立 Reviewer 與必要修復 → PR-or-STOP／合併決策的可信閉環，且不需要 Owner 人工傳話。
- **A11c 的使用者入口需求：** Owner 已確認 llmtool 原生 CLI 必須直接接收需求，並在使用者關閉介面後繼續管理本機背景工作。只有「從 Codex CLI 手動啟動」不符合完整使用者入口的驗收。
- **Owner 後續確認 A12 的分項：** **A12.1 — Telegram**（需求範圍、操作／通知／授權及驗收待討論）、**A12.2 — 本機 Web UI**（監控整個工作流，也能下提示／提交任務、追蹤及必要操作）。目前優先交付 Mac CLI，A12.1 再於其後、A12.2 再後；不能把過去提出的 Web-first 排序當作最新決策。**「V1.0」等版號及詳細 DoD 仍未正式決定。**
- **其他人獨立安裝**仍是確定的產品方向，但 Owner 已決定排在 Mac CLI、Telegram、本機 Web UI 之後。具體版本與各平台順序待後續規劃。
- 保持既有 A-roadmap／安全驗收的可追溯性，避免為同一需求新建重複 PM、Controller、審查系統或臨時管理階段。

## 六、2026-10-21 後續決議與規劃審查

> 以下為首次會議紀錄之後，Owner 與 Codex Integrator／PM 持續討論，再由 ChatGPT 唯讀審查後補記的內容。**明確標示「Owner 已確認」與「PM 建議／尚待修正」；不得將兩者混為一談。** 參考 [PR #255 的彙整報告（Draft）](https://github.com/davidcatcat/davidcattoolsproject/pull/255)。該報告目前不是已合併的現行規則。

### 6.1 最新交付順序（Owner 已確認）

| 順位 | 階段／產品 | 決議及界線 |
| --- | --- | --- |
| 1 | **A11c — Owner Mac 原生 llmtool CLI** | **首先交付。** 使用者直接輸入真實開發需求、指定至少一個 PM AI；PM 或人決定角色／模型；llmtool 管理多任務、授權內的開發交接與背景生命週期。關閉介面後工作繼續，重開可接回。 |
| 2 | **A12.1 — Telegram** | Owner 指定優先於 Web，**實際需求尚未討論定案**。是否僅通知，或還要與 PM 對話、提交需求、操作／授權、顯示詳細結果，均不可預先當成 Owner 已接受。 |
| 3 | **A12.2 — 本機 Web UI** | 完整 AI 工作流的監控、視覺化資料流，並可輸入提示、追蹤／提交任務及必要操作；與 CLI／Telegram **共用同一 llmtool 核心與權限語意**，不另建 PM／Controller。細部界面、驗收與部署設計待後續討論。 |
| 4 | **其他使用者／其他電腦及平台** | 產品長期仍應可供他人以自己的帳號、憑證、Repo 獨立安裝；macOS／Linux／Windows 等細部排序及發佈方式留待後續，不阻擋第 1 階段。 |

**這是 Owner 最新確認的排序，取代之前「Web 先於 Telegram」以及「先讓其他人的 Mac 安裝」等 PM 初步建議。** 不擅自建立額外 A 系列 Milestone。

### 6.2 公司 gw 暫時無法連線（Owner 已確認）

Owner 同意：**公司 `gw` 暫時不可用時，可以先交付清楚標明限制的 Mac CLI 預覽版**。這是避免單一路線拖住其他已具備條件的功能，不代表公司 LLM 的開發與節流目標被取消。

- CLI 核心必要功能與各適用安全／品質驗收仍須具備；只有公司 `gw` 這條路線的實際開發／可用性可以標明 **UNAVAILABLE／NOT_VERIFIED**。
- 不得宣稱六項產品需求全部驗收、公司 LLM 已穩定自動寫程式，或已證明整體 Token 節省。
- 曾有經測試／獨立審查並被採用的公司模型程式碼案例，例如 [PR #213](https://github.com/davidcatcat/davidcattoolsproject/pull/213)；也有後續逾時、無 Patch 的試行。兩類紀錄都應保留，**有限成功 ≠ 完整自動閉環 PASS**。

### 6.3 指定 Integrator／PM AI 失聯時（Owner 已確認）

Owner 原話：「**原本指定的integrator/pm無法使用的時候,其他ai正在進行的工作就先繼續做,做到需要pm在停止,新的派工項目也先暫停**」。

- 已開始且**已獲授權、不需新 PM 判斷**的 Developer／Reviewer 工作可以繼續到其現有範圍的適當邊界，保存輸出、測試與證據。
- 遇到需要 PM 拆分、追加授權、跨 Agent 協調、合併、修正方向的節點時，該工作進入 **WAITING_PM／BLOCKED** 等真實等待狀態；**新派工暫停**。
- 不會自行換 PM、擴張存取範圍、使用未授權付費模型、跳過審查或冒稱已完成；PM 恢復時核對實際 Task／Agent／Revision 狀態，避免重送任務、產生第二個 Writer。
- 此條與「關閉 llmtool CLI 介面後工作繼續」相容；**關閉介面不等於 PM 失聯**，應以執行與連線實際證據判斷。技術實作與完整驗收尚未完成。

### 6.4 A11c、G3、M2、A11c.2 的界線（審查修正方向，Owner 已同意）

PM 在 [PR #255](https://github.com/davidcatcat/davidcattoolsproject/pull/255) 提出 A11c 交付條件：指定 PM 的原生 CLI 入口、Task／Run／Session 持久化、真實多任務並行、背景執行與重接、可信 Developer → Reviewer → PM 交接、自動退修與合併／STOP。**方向已由 Owner 同意；實際完成與驗收仍待實作證據。**

ChatGPT 唯讀審查指出：**不宜重新擴張既有 G3 的完成定義**，否則會把已安排的「三 Agent 真實閉環交接」驗收重做或變成更多新關卡。Owner 表示同意這項修正：

- **G3：** 保留現行 Owner outcome reporting 已核准的 **Integrator → Developer → 獨立 Reviewer → 必要修復／測試 → Integrator PR-or-STOP** 真實交接、ACK、喚醒與結果接收證據，不以新產品入口要求改寫 G3 原義。
- **M2：** 仍是原生 AI Agent 協作閉環的核心成果；不得以人工轉貼提示、假事件、外部 CLI 人工操作或 CI 綠燈單獨宣稱 M2 PASS。
- **A11c／A11c.2 Mac CLI 交付驗收：** 另外檢查**使用者直接操作 llmtool CLI、指定 PM、至少兩個適當隔離的真實並行任務、關閉 CLI 後仍背景執行、再次開啟接回、PM 失聯的有界等待**等最新產品需求，並搭配原有真實交接和獨立品質 Gate。
- G1–G4 仍是**成果回報檢查點，不是新產品 Milestone**；詳細 DoD、技術方案與正式 Roadmap 描述需在原有架構下整理，無須為此增建另一套管理系統。

### 6.5 開發規則整合與避免過度停工（方向獲 Owner 同意；規則尚未修改）

Owner 指出過往經常被分散在不同文件的規則擋住開發。PM 唯讀盤點發現 `AGENTS.md`／`CLAUDE.md` 已精簡，但 `ROADMAP.md`、`WORKFLOW.md`、`README.md` 仍混合現行指令、歷史契約和路線專屬的限制，容易互相誤用。

**Owner 已同意的原則：** 先把必要功能完成；遇到問題持續分析、修復與驗收；只阻擋**真正受影響的工作或危險動作**，不因無關 Bug、個別模型／VPN 不可用而停止整個專案。例行實作和技術修正由 PM／Integrator 在既有授權內處理，重大產品／權限／必要 Owner 決策才升級。此原則**不是**免除安全、資料保護、獨立審查、CI 或真實 Mac 驗收。

建議沿用現有文件而不另建治理系統：`AGENTS.md` 管現行共通授權，`ai-team-delivery` 管派工／品質流程，`safe-git` 管 Git／Worktree；`WORKFLOW` 管必要工程交接，`ROADMAP` 管產品路線與 DoD，`README` 管使用說明，舊版本移入決策／歷史文件。這是**整合提案，尚未實施**。

具體清理方向（不是今天的授權刪改）：

1. 公司 `gw` 已可被選為**提出真實程式碼／測試的 Developer 路線**；舊的 triage-only 文字不得擋住新決策，但公司模型仍不擁有檔案、Shell、Git、Review 或 Merge 權限。
2. OpenCode 一般直接 Developer Session 與特殊 D8 Host／Worker、C1B 試行路線的 cap、Grant、Retry、Host admission 不可混用；各路線保留各自實際授權。
3. Token UNKNOWN 僅表示統計未知，不得用自行設定的節流目標否決有效 Code Review；真正有獨立授權來源的費用上限、資料外送與 Provider 安全控制仍保持。
4. Worktree/單一 Writer、敏感資料、合法操作、獨立 Security／Functional／QA、適用 exact-head CI、Mac Live 證據與防重播等**必要保護繼續有效**；合理精簡重複前置檢查，不得直接跳過來源／檔案身分驗證。
5. 每條具阻擋性的規則應標明**授權來源、適用模式、真正阻擋的操作、由誰處理、何時可恢復**；已封存的 F1/Pickup 不能記成目前還有執行中的產品能力。

**ChatGPT 審查發現的第一項待修正處，Owner 已同意：** `AGENTS.md`、`WORKFLOW.md` 和 [Owner Outcome Reporting v1.1](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) 對 Owner 升級／授權界線的說法不完全一致。後續整合時應**逐條比對原始授權與適用範圍、建立單一現行入口**；不能僅憑 `AGENTS.md` 的三項問答條件便一概取消其他實質資料外送、危險權限、支出或正式 GO／NO-GO 所需的決策／授權。

Owner 同意以上報告方向和兩個審查修正點，也說明「**之後有問題我們再來修正，維持目前持續改善的做法**」。這不是放棄既定安全 Gate，也**不是**宣告 PR #255 已修好、已核准合併或正式修改規則。

### 6.6 GitHub 狀態與本次明確工作範圍

- [PR #249](https://github.com/davidcatcat/davidcattoolsproject/pull/249)：Owner 要求關閉，**CLOSED／NOT MERGED**。先前的 Web-first Roadmap 草案不具現行授權。
- [PR #255](https://github.com/davidcatcat/davidcattoolsproject/pull/255)：Owner 另外要求「整段討論做成報告上傳 Git」而建立的**文件 Draft**。ChatGPT 已完成**唯讀分析並提出 2 點 REVISE**（Owner 權限規則來源；G3 邊界），Owner 表示同意；**修訂 PR 內容與是否合併仍是另一個實際 Git 步驟，不由本會議紀錄直接執行或冒稱完成**。
- 目前只核准將上述追加決議寫入**這份既有會議紀錄**，不修改 `ROADMAP.md`、`WORKFLOW.md`、`AGENTS.md`、Skills、產品程式碼，不新增 PR／Issue 或派 Developer。

---

## 七、仍待討論／技術調查事項

這些是**待討論問題，不是已核准開發工作**：

1. `llmtool` 的直接輸入形式、命令模式、首次設定與安裝／下載發佈體驗。
2. 使用者指定 PM AI／Agent 角色或模型時，具體設定介面、覆寫優先順序及模型無法使用時如何告知。
3. 背景 Task Manager、程序生命週期、睡眠／重啟後的恢復證據與使用者停止操作的詳細設計。
4. 多任務／多 Worktree 的最大併發、排程、衝突解決與工作狀態介面。
5. Web UI 的第一批驗收功能、登入／本機存取安全、圖表／動畫設計及部署方案。
6. 已決定的交付順序如何落實在現有 A11c、A12.1、A12.2 的正式 Roadmap；確切版本命名、交付切片及逐項 Definition of Done 尚待定稿。
7. 「已驗證節流」需要的品質等價及模型消耗數據，與安全限制的獨立授權來源，須維持分離。

## 八、相關 GitHub 來源與本次限制

- [A11c 原有協調規劃：Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
- [llmtool Roadmap（目前仍以 main 為正式文件）](../../tools/llmtool/ROADMAP.md)
- [llmtool 初始產品定位／安裝與 Chat/TUI](../../tools/llmtool/README.md)
- [llmtool 跨電腦使用評估：PR #237](https://github.com/davidcatcat/davidcattoolsproject/pull/237)
- [現有 Activity 監控需求：Issue #225](https://github.com/davidcatcat/davidcattoolsproject/issues/225)
- [先前 Roadmap 草案：PR #249 — CLOSED / NOT MERGED](https://github.com/davidcatcat/davidcattoolsproject/pull/249)

**紀錄更新不等於工作授權。** 保留 Owner 決定與 PM 建議的區別，不因本檔而建立或重開 PR／Issue、派發 Developer、修改 Roadmap／現行規則、合併 PR #255，或聲稱產品功能已驗收通過。後續規劃正式修訂及開發仍須相應授權。

---

## 九、2026-10-22 Desktop GUI（非 Web）可行性與交付排序

> **本節是 2026-10-22 Owner 在 GUI 可行性討論後，明確要求「排 Roadmap，紀錄到會議記錄」的後續產品規劃。** 新增 Desktop GUI 是 Owner 本次要求；**GUI 插在 Telegram 與 Web UI 之間，是本次 Roadmap 的規劃排序**，既有 A11c／A12.1／A12.2 的相對先後不變。第 6.1 節保留 2026-10-21 原排序，正式 A12 編號及文件同步仍待後續修訂。此處記錄規劃，不表示 GUI 已實作／驗收，也**不代表**已修改正式 [ROADMAP.md](../../tools/llmtool/ROADMAP.md)、授權派工／費用／資料外送／Git 合併。

### 9.1 產品定位與架構邊界（Owner 已提出並要求納入）

- **Desktop GUI 是安裝於本機的圖形桌面應用程式（先以 macOS 的 `llmtool.app` 為目標），不是瀏覽器 Web UI，也不同於 Terminal 裡的 TUI。** 以對話為主，輔以 Tasks、Agents、Review/PR、狀態與設定的按需頁面；不預設一定要常駐全螢幕監控 Dashboard。
- GUI／CLI／TUI／後續 Telegram／Web 應共用 **同一 llmtool Core 的實際 Task、Run、Session、事件、權限與審計語意**。GUI 只負責輸入、展示、呼叫已授權操作與呈現確認；不得自建第二套 PM、智慧 Router、Agent 排程器、背景 Supervisor、資料庫、Git 執行器或權限判斷。
- **llmtool 本身不具 AI 語意判斷**；至少由使用者指定的 PM／Integrator AI 進行需要 AI 決策的任務分派與模型選擇，使用者也可自行指定。公司 gw 仍是可選 Provider，不是 GUI 或通用 Core 的必要依賴。
- 既有 Core 未提供的能力（持久化 Task/Run、回連、Agent 狀態、實際 Token 使用／餘額等）不得由 GUI 以假資料冒稱已可用；可顯示 UNKNOWN／UNAVAILABLE／NOT_VERIFIED 或停用入口。TUI 已有部分 UI 功能，不等於 GUI 或多 Agent Core 已經完成。
- **首選候選技術為 Python PySide6／Qt**，原因是可沿用目前 Python Core 並保留跨平台方向；Tauri、SwiftUI 是備選。技術選型、Qt 授權／打包方式、與核心通訊邊界仍須 GUI-P0 驗證，並非 Owner 最終框架定案。

### 9.2 更新後產品交付排序（Owner 指示納入 GUI；排序為本次規劃結果）

| 順位 | 現有里程碑／新增項目 | 交付目標與依賴 |
| --- | --- | --- |
| 1 | **A11c — Owner Mac 原生 CLI** | 保持最高優先：原生需求入口、指定 PM、真實 Developer/Reviewer 閉環、多任務、背景執行與重接、實機驗收。GUI 不得阻擋此項。 |
| 2 | **A12.1 — Telegram** | 依原定優先於其他非 CLI 前端；互動、通知、遠端授權範圍仍需另行定義。 |
| 3 | **Desktop GUI — 新增 A12 前端工作項目（正式編號待整理）** | **先完成 macOS GUI**，直接使用已交付、已授權的 Core，能在本機操作會話與真實任務；排在 Web UI 之前。 |
| 4 | **A12.2 — 本機 Web UI** | 維持原有名稱與編號，提供瀏覽器輸入／操作及工作流視覺化；不得複製 GUI 的控制與執行邏輯。 |
| 5 | **其他使用者／跨平台發佈** | 讓他人以自己的帳號、憑證、Repo 安裝；macOS／Linux／Windows 的具體先後、安裝器與發行通道待決。 |

**編號處理：** 本節先把 Desktop GUI 明確排在 Telegram 和 Web 之間，**不立即將既有 A12.2 Web 重新編號，也不擅自新增另一條獨立產品主線**。正式 A12 排程整合時，由 PM 在原有 [ROADMAP.md](../../tools/llmtool/ROADMAP.md) 做一次一致性修訂及審查；不得把本會議紀錄本身當作 ROADMAP.md 已修改或開發已開工的證據。

### 9.3 GUI 內部工作切片（規劃，不是新 A Milestone）

| 切片 | 工作內容 | 具體驗收／不做的事 |
| --- | --- | --- |
| **GUI-P0：技術驗證** | 比較 PySide6／Qt 與備選框架，驗證 Python Core 呼叫／必要 IPC、主執行緒不卡死、macOS 視窗和封裝最小原型 | 使用既有 Core 介面；無第二個 Core／PM／Executor；紀錄打包體積、安裝方式及技術風險。可在 A11c 核心契約穩定後以低干擾工作包預先進行，但**不阻擋 CLI**。 |
| **GUI-P1：桌面對話 MVP** | macOS 原生視窗、Chat、Profile／Provider／Model 顯示與選擇、輸入編輯、設定及錯誤呈現 | 確認真實 Core 回應與授權界線；未實作的 PM/Task 不得被模擬為 LIVE。不要重做 TUI 已有的業務邏輯。 |
| **GUI-P2：協作工作台** | 使用已可用的 Core Task/Run/Session 查看任務、Agent、執行紀錄、測試、Review、PR/CI、用量、必要操作／授權 | 同一任務在 CLI 和 GUI 的 ID、狀態、Revision、證據一致；無資料時忠實顯示 UNKNOWN；關鍵動作使用 Core 權限與審計。 |
| **GUI-P3：關閉及重新開啟** | GUI 只依附獨立的背景執行/Task Manager；退出 GUI、重開、切換對話或錯誤後從 Core 接回工作 | **關閉 GUI 不等於取消已授權任務**；不重送 prompt，不產生第二個 Writer；程序失聯、睡眠、斷網不顯示假完成。核心無此能力則標為阻擋依賴，不自行增建 GUI 私有 Supervisor。 |
| **GUI-P4：Mac 可安裝版** | `.app` 打包、設定保護、權限提示、可診斷錯誤、原生實機與安裝測試 | 在目標 Mac 實機驗證啟動/卸載、對話、背景重接、中文字元、敏感資訊保護；架構不綁死某一台 Mac 的路徑或 VPN。其他使用者與 Windows/Linux 正式發行仍在後續階段。 |

此處刻意使用 **GUI-P0～P4**，避免與既有 Owner outcome reporting 的 **G1～G4** 成果回報檢查點混淆；GUI 切片不是 G1～G4，也不是 G3/M2 的驗收替代。

### 9.4 共同驗收原則與風險

1. **先可用 Core，後完整 GUI。** GUI 可在穩定核心上加強操作；不能把缺少的 A11c 背景工作、工作交接、持久化與權限機制包裝成介面功能。GUI-P2/P3 對 Core 有硬依賴；P0 可在不干擾主線時平行。
2. **同一操作、同一證據。** CLI/TUI/GUI 觀察同一 Task 必須呈現一致的可靠來源、更新時間、錯誤與權限結果。GUI 不自行生成 Token 統計、Approval／Merge 或 Agent 成功狀態。
3. **閉 GUI 不停工，但不承諾電腦關機仍執行。** 由已驗證的 Core 背景生命週期處理，遵守原先的睡眠/重啟/失聯真實狀態規則。
4. **維持資料與安全邊界。** 只提交最小必要權限請求；不在 GUI/log 儲存憑證，不繞過既有 Provider/Host、資料外送、F2 Git、更改 Repo、CI、獨立 Reviewer、Mac Live 等 Gate。
5. **交付條件以可重現證據為準**，每個切片須有對應自動化與 Mac 手動驗證；UI-only 修改不得隱含 Core、模型路由、額度或執行權限變更。

### 9.5 此次執行授權邊界

- Owner 本次明確要求 **排定 Desktop GUI Roadmap 並記入既有會議紀錄**。這是規劃和文件工作授權，不是直接實作 GUI、修改其他規範、派 Agent 或合併未審核 PR 的授權。
- 原有 [2026-10-22 TUI UI/UX 決議](2026-10-22-llmtool-tui-ui-ux-decision.md) 仍只規範 Terminal UI；本節 Desktop GUI 另屬桌面前端，不重寫 TUI 既有決議。
- 尚未設定發布日、工期、人工數或成本承諾；由既有 Integrator/PM 按 A11c 的實際驗收結果再細化依賴。正式 ROADMAP.md 的文字與編號同步是**後續工作**，不是本次已完成事項。

### 9.6 統一 UI Agent — CLI／TUI／Desktop GUI／Web UI（Owner 2026-10-22 追加要求）

**Owner 最新要求：「需要 UI Agent，包含 TUI、GUI、CLI、Web。」** 因此新增的不是「GUI 專屬設計師」，而是**一個跨介面的 UI/UX 專業 Agent 角色**。CLI 包含非互動指令的人機介面，TUI 是 Terminal 互動畫面，Desktop GUI 是 macOS 桌面視窗，Web UI 是瀏覽器介面；四者各有媒介限制，但共用一致的產品術語、狀態語意及操作原則。

| 介面 | UI Agent 專責範圍 | 不得誤當成 UI 工作的內容 |
| --- | --- | --- |
| **CLI** | 命令探索／Help、旗標與參數提示、可讀錯誤和修復指引、輸出格式、非互動腳本相容性、初次設定體驗、成功／等待／阻塞狀態的清楚傳達 | 不自行增加底層命令權限、修改 Core 工作結果、改變 exit code／機器輸出契約而沒有正式相容性評估 |
| **TUI** | Chat-first 佈局、輸入／貼上／多行編輯、slash 選單、快捷鍵、狀態列、ANSI/Unicode、縮放和鍵盤無障礙體驗 | 不另建 Task/Session、Provider、背景 Worker、Token 查詢、Approval 等 Core 能力 |
| **Desktop GUI** | 視窗資訊架構、Chat、Task／Agent 列表、詳情、授權確認、設定、表格與狀態、桌面原生導覽／回饋、一致化視覺語言 | 不建立 GUI 專用 PM、資料庫、工作派送、第二套後端或私有 Supervisor |
| **Web UI** | 響應式資訊架構、互動工作流／任務圖、對話和操作、可用性、存取性、視覺化狀態、必要確認、瀏覽器錯誤／斷線體驗 | 不創造 Web 專用執行器、審核政策、偽造「即時」資料或複製 Desktop GUI 的後端 |

**組織與派工：**

1. **現有 Integrator／PM 繼續負責需求拆分、優先級、模型／Agent 選用與交付決策。** UI Agent 是 PM 按任務需要啟用的專門角色，不是第二個 PM，不是 llmtool 自帶的 AI 智能，不必 24 小時常駐，也不需要為四個介面各設一個永久 Agent。
2. **UI Agent 的主要交付物** 是共用 UX 規範、資訊架構、操作流程／線框圖、畫面與元件規格、關鍵狀態（Loading／WAITING／BLOCKED／UNKNOWN／FAIL 等）、錯誤文案、鍵位／可用性／存取性規則、可重現 UI 驗收案例。原型與示意資料必須標為 Mock，不得冒稱 Core 實際能力。
3. **UI 設計與程式實作分開授權。** UI Agent 可提出設計並檢查 UI 實作；若 PM 另行把某個 UI 工作包指派其擔任 Developer，才依一般 Developer 的 worktree、寫入權限、測試、PR 與資料邊界執行程式修改。未取得執行授權的 UI Agent 不直接寫入 Repo、執行 Git 或合併 PR。
4. **正式獨立審查仍由既有 Reviewer/QA 機制負責。** UI Agent 的自評或設計確認不能替代 Security／Functional／QA、CI、Mac Native 等必要 Gate；視變更風險可安排 UI 專項審查，但不要讓每個 Help 文案微調都額外通過一套重複審批。
5. **同一個狀態語意、一套操作與授權契約；呈現形式因媒介調整。** 例如 Task ID、來源、最後更新、Core 的 UNKNOWN 與是否需要 Approval 四處一致；CLI 以標準輸出，TUI 以快捷鍵選單，GUI/Web 以圖形元件呈現。UI Agent 不自行判定 Task 成功、不補造 Token 額度、不覆蓋 PM 決策。
6. **工作接入時機：** 自目前進行中的 CLI/TUI 可用性工作即可按 PM 分配開始；Desktop GUI 在規劃的 GUI-P0～P4 切片配合設計；Web UI 到 A12.2 時沿用相同 UX 規範。此角色**不改變**既有 A11c → Telegram → Desktop GUI → Web UI → 跨平台的交付順序，不使 GUI/Web 提前變成 Core 主線的阻擋條件。

**統一 UI Agent 首份推薦工作包（規劃、非即刻派工）：** 以目前 llmtool chat／llmtool chat --tui 和命令 Help 為實物基準，產出四介面共用的「UI/UX Contract v0.1」：導航／命名規則、真實 Task/Run/Agent 狀態矩陣、等待／錯誤／授權顯示、基本鍵盤／無障礙要求、CLI 機器輸出相容性、桌面／Web 視覺範例，以及哪些欄位尚依賴 Core。任何新增可執行能力留給 Core 及其原有安全／產品 Roadmap。

**文件與實作狀態：** 本節只記錄 Owner 指定的統一 UI Agent 角色及建議分工；目前尚未建立或啟動該 Agent、沒有選定實際 Provider/Model、沒有修改 Agent Registry/Skills，也沒有宣稱 UI Contract 已交付。正式落地時由既有 PM 按已核准的 Git/Agent 流程整合，避免重複建置管理機制。
