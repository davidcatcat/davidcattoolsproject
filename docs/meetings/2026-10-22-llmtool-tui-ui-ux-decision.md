# llmtool TUI — UI/UX 設計決策會議紀錄

- 日期：2026-10-22（Asia/Taipei）
- 性質：Owner 與 ChatGPT 討論後確認的產品介面設計決策
- 狀態：**Owner 已同意設計方向；僅規劃文件，尚非實作／驗收結果**
- 參考：2026-10-21 [產品需求會議](2026-10-21-llmtool-product-requirements.md)；[A3.5 Roadmap](../../tools/llmtool/ROADMAP.md#a35-ux-follow-up--codexclaude-style-interactive-terminal-owner-request-2026-10-20)
- 參考介面：[Claude Code interactive mode](https://code.claude.com/docs/en/interactive-mode)、[Codex CLI customization](https://developers.openai.com/codex/cli/)、[Codex slash commands](https://developers.openai.com/codex/cli/slash-commands)。參考的是 UI/UX 模式，不是要複製其代理執行功能。

## 1. Owner 已確認：TUI 只有使用者介面責任

**TUI 是 llmtool 的顯示及輸入介面，沒有獨立業務邏輯。** CLI／TUI／後續 Telegram／Web UI 都應面向同一套 llmtool Core 的公開、受權限約束之能力。

### 範圍內（UI-only）

1. **顯示：** 對話、Provider／Model、狀態、Task／Agent、證據、GitHub／Review／CI、用量和錯誤等**既有 Core 可提供**的資訊。
2. **互動：** 輸入、編輯、選單、命令補全、快捷鍵、焦點與畫面切換、顯示既有授權要求，並將使用者選定的操作傳給 Core。
3. **呈現體驗：** 文字排版、Markdown／程式碼／Diff 樣式、捲動、搜尋、收合、複製、狀態列、主題、終端機縮放與降級。
4. **資訊真實性：** 顯示來源、最後更新與 UNKNOWN／UNAVAILABLE／NOT_VERIFIED 等明確狀態；沒有資料不能自行推導「正在工作」「已完成」「還有額度」。

### 明確排除（不得以 TUI 工作包實作）

- PM／Integrator AI 語意判斷、路由選模、任務拆分或派工策略。
- Provider 連線能力、Agent/Worker 啟動、背景監督、Task/Run/Session 持久化、Session 恢復演算法。
- Git／GitHub 執行、審查與合併、權限決策、審核政策、資料外送、Token 計費與統計產生、額度查詢後端。
- 新的 API、資料庫、控制器、事件／監控子系統、執行與授權機制。

**規則：** UI 若想顯示 Core 尚未提供的功能／欄位，只記錄「UI 依賴 Core（尚不可用）」；不隨 TUI 需求擴張 Core 工作。UI 可隱藏不可用操作、顯示 disabled + 原因，或顯示 UNKNOWN；不可假造能力。

## 2. 主畫面佈局（Owner 已同意）

預設採 **聊天為主，不常駐四面板監控 Dashboard**：

1. 頂部：llmtool 標題、當前專案／會話、連線狀態（有可靠資料時）。
2. 中央：可捲動對話，使用者／AI 訊息區分清楚；詳細事件或來源可按需展開。
3. 底部：固定、多行輸入框；可顯示快捷鍵提示，輸入 / 可打開指令選擇器。
4. 最下方：短狀態列，可自行選擇要顯示的 Core 欄位；窄終端依優先度隱藏欄位。

Task／Agent／GitHub／Usage／Approval 的列表與詳情採 **需要時開啟的子畫面／選單**；不把相關執行、審查或權限引擎建在 TUI 裡。畫面內容僅取自 Core 或其授權提供的資料，不假設這些欄位現在均已可用。

## 3. 借鑑 Claude Code／Codex CLI 的純介面功能（Owner 已同意方向）

| 區域 | UI 行為 | 輸入／資料邊界 |
| --- | --- | --- |
| 對話區 | 格式化 Markdown、程式碼區塊與 diff，長輸出截斷提示，詳細事件展開／收合，保留捲動位置 | 原樣保留證據；輸出需 escape 終端控制序列 |
| 編輯區 | 多行輸入、穩定貼上、歷史搜尋、草稿保留、可選外部編輯器 | 不因切換焦點／退出而重送；外部編輯器只操作草稿 |
| 指令選單 | 輸入 `/` 即可搜尋、方向鍵移動與確認；命令說明與可用性提示 | **僅展示現有 Core 支援的操作**；不可用指令不得冒稱已實作 |
| 頁面切換 | Chat／Tasks／Agents／GitHub／Usage／Approvals 等資料檢視入口 | 只在 Core 真能供資料時可用；不另開發資料來源 |
| 狀態列 | 使用者挑選 Provider、Model、Task、Agent、Branch、用量、提醒等欄位 | 僅顯示可驗證數據，缺失為 UNKNOWN；未知訂閱額度不能推算 |
| 操作確認 | 顯示 Core 傳來的操作對象、範圍及結果 | UI 不自行判斷權限或自動核准 |

**快捷鍵提案（實作前檢查相容性及衝突）：** `Enter` 傳送；`Shift+Enter` 換行（保留已支援的 `Ctrl+J`／`Esc Enter` 替代）；`Ctrl+R` 搜尋輸入歷史；`Ctrl+G` 外部編輯草稿；`Ctrl+O` 展開詳細輸出；`F1` 求助；`Tab` 切焦點；`PageUp/PageDown` 捲動；`Esc` 返回。終端機無法辨識的組合應可退回替代鍵位，不破壞現有退出、貼上與非 TTY 降級。

UI 可以提供 `/tasks`、`/agents`、`/model`、`/github`、`/usage` 等**介面選單名稱提案**；未經 Core 介面確認之前不是可執行命令承諾。現有聊天指令及產品功能狀態以源碼／README 為準。

## 4. Roadmap 交付優先次序（不新增 A 系列里程碑）

全部沿用 **A3.5 TUI/UX follow-up**，與 A11c Core 驗收互相獨立。下列 P0/P1/P2 是 **TUI 內部 UI 工作排序**，不是新的安全 Gate 或產品里程碑：

| 優先 | UI-only 項目 | 驗收方向 |
| --- | --- | --- |
| **P0 — 常用互動** | Slash 命令搜尋、輸入/換行/貼上與草稿、歷史搜尋、快捷鍵說明、精簡可配置狀態列、窄視窗 | 用離線假資料與現有 ChatSession 檢查畫面和鍵盤；保留舊模式 |
| **P1 — 按需查詢** | Markdown／程式碼／diff 呈現、詳情收合、Core-backed Task／Agent／GitHub／Usage／Approvals 檢視、提示資料未知原因 | 用假資料 UI 測試；只有來源可用才啟用功能；UI 不直接執行後端動作 |
| **P2 — 進階美化** | 深淺主題、語法高亮、可選鍵位／編輯模式、必要時分割面板、複製與易讀性調整 | 對 Mac 終端機寬度、Unicode、降級與操作穩定性回歸驗證 |

**既有實作不是待重做項：** `llmtool chat --tui` 的全螢幕、捲動、多行輸入、F1、狀態列、Provider／Model／最近一回合 Token／時間顯示已有 source；目前 ChatSession 對話歷史僅在記憶體，且不是 Core-backed Task 重接。以上新項僅補差距，不複製既有功能。

## 5. TUI-only Definition of Done

- 無任何 Core、Provider、PM、Task runtime、Git、權限或資料持久化邏輯變更；只使用現有經批准的 API／資料結構。
- 真實資料與 fake／mock 明確區分；UNKNOWN 不顯示為 0、PASS、已連線或已完成。
- 鍵盤、輸入／貼上、捲動／縮放、錯誤、切換、非 TTY fallback、中文與 Unicode 渲染可重現測試。
- 未意外啟動任務／Provider／工具操作、未重送 prompt、未將未授權內容展示或寫入紀錄。
- 保留原有 `llmtool chat` 行為及現有權限模型；UI 變更需相應測試與獨立審查。
- 未提供 Core 資料的卡片與命令列為依賴，**不是 UI 完成或產品上線證據**。

## 6. 本次文件變更的授權範圍

Owner 要求**新增本會議紀錄，並同步 Roadmap／必要文件**。本次僅提交文檔分支與 PR，未授權藉此直接開發上述功能、創建獨立里程碑、派 Agent、修改 runtime 或直接合併未通過既有 Gate 的 PR。
