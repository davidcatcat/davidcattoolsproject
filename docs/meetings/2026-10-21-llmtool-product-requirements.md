# llmtool 產品需求、操作入口與背景執行 — 討論會議紀錄

- **日期：** 2026-10-21（Asia/Taipei）
- **性質：** Owner 與 ChatGPT 的需求討論／決策紀錄，供後續正式規劃參考。
- **效力：** 下文「Owner 已確認」才是本次產品需求；「建議、待定」不等於 Owner 已接受。
- **本次工作範圍：** 只保存會議紀錄；不代表 Roadmap 已更新、功能已實作、任務已派送、預算／執行／資料外送權限已核准。
- **管理要求：** 目前仍在定義產品階段。Owner 已要求暫時**不要建立 PR、Issue、派發開發工作或修改 Roadmap**；本檔為 Owner 另行明確要求的單一會議紀錄例外。

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

Owner 在 2026-10-19 已討論透過 Web 觀看 llmtool 的 Agent 工作流、資料流、狀態與監控數據，並希望有更有未來科技感的動態視覺化。本次進一步確認 **Web 不只看監控，也必須可以輸入提示／需求、提交和追蹤工作、查看結果與必要的操作／授權**。

- 觀察需求：使用者需求 → PM 分工 → Developer（Codex／OpenCode／公司 LLM 等）→ 測試 → 獨立 Reviewer → 修復 → PR/CI；真實顯示等待、進行、阻塞、完成、交接結果與相關證據。
- 操作需求：從 Web 下需求、看 PM 回應、管理及追蹤任務，經**同一套既有 llmtool 後端與權限契約**提出必要操作。Web UI 不應另有自己的 PM 或協調引擎。
- 專案目前存在 Terminal Monitor／Activity 規劃，**不能因此宣稱 Web UI 已經交付**。
- 依賴、UI 架構、版面設計、實際第一版 Web 功能邊界及對外存取方式**尚待規劃與驗收**。

### 其他人的電腦（Owner 已確認為產品方向，實作時序尚待正式排定）

llmtool 將來應可分享給其他使用者安裝在**自己的電腦**，使用自己的 Provider／CLI 帳號與 Git Repo；不能要求拿 Owner 的 Mac 路徑、公司 VPN、帳號或憑證才能運作。這是**每個人獨立安裝的工具**，不等同於共用一台多人租戶 SaaS。

- 既有 [PR #237 移植性評估](https://github.com/davidcatcat/davidcattoolsproject/pull/237) 記錄了硬編碼路徑、固定 Repo、CLI 版本、Host 登錄及 Windows 限制。其「先保持個人 Mac 工具」是作者建議，**不是 Owner 最終產品定位決策**。
- 暫時的**規劃建議**是先完成自己 Mac 的真正可用版，再驗證其他人的 macOS／Linux 基本安裝和完整 Agent 協作；Windows 另行評估。這個相依排序與版本編號尚未完成 Owner 正式確認，不得變成新的上線強制門檻。

## 五、A11c 與後續階段：本次釐清的界線

- **A11c 核心協作目標：** 透過至少一個指定 PM AI 完成真實任務 → PM 分派 → 已授權 Developer 工作 → 測試、獨立 Reviewer 與必要修復 → PR-or-STOP／合併決策的可信閉環，且不需要 Owner 人工傳話。
- **A11c 的使用者入口需求：** Owner 已確認 llmtool 原生 CLI 必須直接接收需求，並在使用者關閉介面後繼續管理本機背景工作。只有「從 Codex CLI 手動啟動」不符合完整使用者入口的驗收。
- **Web 是已提出的正式產品需求，但「放在 A12」「V1.0 或下一版交付」等精確里程碑與版本切點，仍屬討論中的安排**；不可把 ChatGPT 提出的版本號或階段順序記為 Owner 已接受。
- **其他人獨立安裝**也是確定的產品方向，具體版本與優先順序待正式 Roadmap 規劃。
- 保持既有 A-roadmap／安全驗收的可追溯性，避免為同一需求新建重複 PM、Controller、審查系統或臨時管理階段。

## 六、尚未定案／下一次討論可處理

這些是**待討論問題，不是已核准開發工作**：

1. `llmtool` 的直接輸入形式、命令模式、首次設定與安裝／下載發佈體驗。
2. 使用者指定 PM AI／Agent 角色或模型時，具體設定介面、覆寫優先順序及模型無法使用時如何告知。
3. 背景 Task Manager、程序生命週期、睡眠／重啟後的恢復證據與使用者停止操作的詳細設計。
4. 多任務／多 Worktree 的最大併發、排程、衝突解決與工作狀態介面。
5. Web UI 的第一批驗收功能、登入／本機存取安全、圖表／動畫設計及部署方案。
6. CLI 完成、A11c 核心驗收、Web UI、其他使用者安裝各自如何定義產品版本與里程碑。
7. 「已驗證節流」需要的品質等價及模型消耗數據，與安全限制的獨立授權來源，須維持分離。

## 七、相關 GitHub 來源與本次限制

- [A11c 原有協調規劃：Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120)
- [llmtool Roadmap（目前仍以 main 為正式文件）](../../tools/llmtool/ROADMAP.md)
- [llmtool 初始產品定位／安裝與 Chat/TUI](../../tools/llmtool/README.md)
- [llmtool 跨電腦使用評估：PR #237](https://github.com/davidcatcat/davidcattoolsproject/pull/237)
- [現有 Activity 監控需求：Issue #225](https://github.com/davidcatcat/davidcattoolsproject/issues/225)
- [先前 Roadmap 草案：PR #249 — CLOSED / NOT MERGED](https://github.com/davidcatcat/davidcattoolsproject/pull/249)

**本次紀錄只是存檔。** 不把建議自動轉成 Owner ACCEPTED，不藉此建立或重開 PR／Issue、不指派 Codex/OpenCode/公司 LLM 開發、不修改 Roadmap、不宣稱功能已實作或驗收通過。未來要正式修改 Roadmap 或啟動開發，等待 Owner 另行確認。
