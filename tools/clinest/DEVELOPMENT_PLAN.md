# CliNest — 初版軟體開發規劃書

> 版本：v0.1（草案）  
> 日期：2026-10-26  
> 狀態：**設計規劃／尚未實作，尚未完成真實登入與平行執行驗收**  
> 專案位置：`tools/clinest/`  
> 定位：獨立的本機 AI CLI 多帳號管理工具

## 1. 產品目標

**CliNest 讓多個 AI CLI 帳號各自保留原生登入狀態，使用者從 TUI 管理登入與預設帳號；任何新的 Terminal 可以直接使用原生 `codex` / `claude` 命令執行預設帳號，而其他本機程式能明確指定任一已登入帳號呼叫原生 CLI。**

關鍵不是「在全域目錄覆蓋同一份 Token」，而是「每個帳號有可獨立呼叫的原生 CLI 認證／設定環境」。CliNest **不是 AI 模型、代理框架、任務調度器或 API 代理伺服器**；不需要背景 Daemon，關閉管理 TUI 後仍可正常呼叫已保存的帳號。

## 2. 使用者場景與行為契約

### 2.1 依序登入，新的 Terminal 使用最新預設帳號

1. 在 `clinest` TUI 登入 `codex1`；成功後自動設為 **Codex Provider 的預設帳號**。
2. Terminal A 執行 `codex` → 使用 `codex1`，其既有 TUI 持續工作。
3. 在 CliNest 登入 `codex2`；成功後預設切到 `codex2`，**`codex1` 保留登入**。
4. 新開 Terminal B，執行 `codex` → 使用 `codex2`；Terminal A 中已啟動的 Codex TUI 不受到切換影響。
5. 在 CliNest 登入 `claude1`；成功後設為 **Claude Provider 的預設帳號**。
6. 新開 Terminal C，執行 `claude` → 使用 `claude1`；Codex 兩個 TUI 繼續工作。
7. 之後可隨時新增 `claude2`，不會登出 `claude1`。

**精確語義：帳號在「每次啟動原生 AI CLI 進程」時綁定，而不是在開啟 Terminal 時綁定。** 已開啟但尚未執行 `codex` 的舊 Terminal，在下一次執行 `codex` 時也讀取當時的最新預設值；已啟動的 AI 進程保持原綁定的 Profile。

兩個 Provider 的預設帳號獨立：改 Codex 預設值不得影響 Claude 預設值。

### 2.2 所有已登入帳號都必須可指定呼叫

`clinest run` 對預設帳號以外的 Profile 一樣有效；它不是只有「目前選中的帳號」才可用。

```bash
# 以下均為「規劃中的指令」，尚未實作
clinest                      # 開啟 CliNest 管理 TUI
clinest list                 # 列出 Profile
clinest add codex codex1     # 建立獨立 Codex Profile
clinest login codex1         # 使用原生 CLI 完成登入
clinest use codex2           # 切換 Codex 的預設 Profile
clinest status               # 本機狀態，不輸出任何憑證
clinest run codex1 -- codex  # 明確啟動 codex1 原生 TUI
clinest run codex2 -- codex exec "檢查這份程式碼"
clinest run claude1 -- claude
clinest run claude1 -- claude -p "摘要當前專案"
```

`clinest run <profile> -- <command> [args...]` 的目的為：直接呼叫指定帳號的真實原生 CLI（可以互動／非互動），**不是**讓 CliNest 解析或代替執行 AI 工作；CliNest 不做模型選擇或派工。

## 3. v0.1 明確範圍

### 必做（MVP）

- **Provider：** 首版正式支援 Codex CLI 與 Claude Code CLI，先以 macOS／zsh 驗收。
- **CLI + TUI：** 新增、列出、重新命名、選擇預設、原生登入、重新登入、登出／移除指定帳號、查看可確認的登入狀態。
- **獨立 Profile：** 各帳號持久化其原生認證與設定環境，不因新增或切換其他帳號而覆寫。
- **登入行為：** TUI 呼叫 Provider 官方 CLI 的登入流程（含必要的瀏覽器 OAuth）；CliNest 不自行蒐集密碼、不自行交換或刷新 Token。
- **可直接輸入原指令：** 使用者**明確啟用** Shell Shim 後，任一新 Terminal 的 `codex`、`claude` 使用各自 Provider 的預設 Profile。
- **可指定帳號執行：** `clinest run ... -- ...` 提供給 Terminal、Shell Script 與本機程式；不依賴管理 TUI 常駐。
- **並行與持續性：** 至少同 Provider 兩個帳號平行執行；管理 TUI 結束、終端機重開、本機重新開機後 Profile 可繼續使用（仍須以 Provider 憑證是否有效為準）。
- **安全與可回復：** 不破壞現有 `~/.codex`、`~/.claude` 登入與設定；可撤回 Shim 安裝；不輸出敏感認證資訊。

### 不做（v0.1 不得偷偷擴充）

GUI、網頁服務、雲端同步、遠端帳號池、配額／重置時間監控、自動輪替／故障切換、帳號共享、負載平衡、AI Agent／PM／工作包、模型路由、額外的背景服務。Antigravity、Gemini、OpenCode、Kilo 等作為日後 Adapter 擴充，不宣稱本版已支援。

## 4. CLI / TUI 介面

### 4.1 命令草案

| 命令 | 行為 |
| --- | --- |
| `clinest` | 進入互動管理 TUI |
| `clinest providers` | 顯示已安裝及受支援 Provider |
| `clinest add <provider> <alias>` | 建立隔離 Profile，不等同已登入 |
| `clinest login <alias>` | 在選定 Profile 中進入原生登入流程；成功後設為該 Provider 預設 |
| `clinest list` | 顯示所有帳號、Provider、預設值、登入狀態 |
| `clinest use <alias>` | 只更改該 Provider 的預設 Profile |
| `clinest status [alias]` | 查詢可觀察的憑證／登入狀態，不讀取並輸出 Token |
| `clinest run <alias> -- <command> ...` | 指定 Profile 啟動原生 CLI，完整透傳參數／訊號／Exit Code |
| `clinest logout <alias>` | 僅對指定 Profile 呼叫原生登出；不得影響其他 Profile |
| `clinest remove <alias>` | 二次確認後移除 Profile；對資料刪除與撤銷登入的差異要明確提示 |
| `clinest shell install / uninstall` | opt-in 啟用／撤銷原生 `codex` / `claude` 命令的預設帳號路由 |

命令名稱與細部參數可在實作時微調；**第 2 節行為契約不得因修改 CLI 語法而弱化**。

### 4.2 TUI 操作

- 依 Provider 分組，顯示 Profile 別名、是否為預設、登入狀態（`Unknown`、`Login required`、`Logged in / verified`、`Error`）；無法驗證時不可假裝已登入。
- 常用操作：新增 Profile、登入／重新登入、設預設、啟動對應原生 CLI、登出、移除。
- OAuth 與互動式登入交由**原生 CLI + 系統瀏覽器**執行；TUI 可暫停畫面並交接 TTY，不實作假登入頁。
- 成功登入新帳號後切換該 Provider 預設；登入失敗時保留先前預設。
- 清楚區分「已保存憑證」與「經官方 CLI 即時驗證有效」，避免誤導。

## 5. 技術架構（初選）

```text
                       +-----------------------------+
                       | CliNest TUI / CLI            |
                       | accounts / login / defaults  |
                       +--------------+--------------+
                                      |
                     +----------------+----------------+
                     | Core: Profile Registry          |
                     | atomic defaults / validation    |
                     +----------------+----------------+
                                      |
                +---------------------+---------------------+
                |                                           |
        +-------v--------+                          +-------v--------+
        | Codex Adapter  |                          | Claude Adapter |
        | CODEX_HOME     |                          | CLAUDE_CONFIG_DIR
        +-------+--------+                          +-------+--------+
                |                                           |
         native Codex CLI                              native Claude CLI
                |                                           |
          isolated login                                isolated login

    Direct use: clinest run <alias> -- <native-cli> [arguments]
    Convenience: opt-in shell shim 'codex' / 'claude' -> selected default
```

### 5.1 目錄草案

```text
~/.config/clinest/
    config.json                # Provider -> active alias 等非機密資料
~/.local/share/clinest/
    accounts/
        codex/codex1/          # Codex 原生專屬環境
        codex/codex2/
        claude/claude1/        # Claude 原生專屬環境
        claude/claude2/
~/.local/state/clinest/
    logs/                      # 僅必要非敏感診斷，預設關閉詳細記錄
```

配置與資料路徑遵守 macOS／XDG 慣例或相容解析；具體路徑以 P0 實驗結果確定。JSON 只保存 alias、Provider、預設選擇及非機密中繼資料；**原生 Token／Refresh Token 留在原生 CLI 支援的儲存機制內**。不得將原生憑證提交 Git、寫入一般應用 JSON 或打印到日誌。

### 5.2 Provider 驗證事項（實作前的 P0 Gate）

- **Codex：** 研究／實測 `CODEX_HOME`、`codex login`、`codex login status`、`codex logout` 和 `codex exec` 在目標 CLI 版本的行為；注意 Config Profile（模型配置）不是登入帳號 Profile。確認憑證在檔案、Keychain、Token Refresh 下的隔離性。可能須明確採用 per-profile file credential store；不得預設所有儲存模式可並行。
- **Claude：** 研究／實測 `CLAUDE_CONFIG_DIR`、`claude auth login`、`claude auth status`、`claude auth logout`、`claude -p`；確認設定、憑證、Keychain 與 Session 存取在多 Profile 下真正隔離。
- **共同：** 不複製同一份 Refresh Token 到多份互相獨立的認證存放區；每個帳號都應在自己的 Profile 完成一次原生登入。處理 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等環境變數可能改變認證模式的衝突（顯示警告／安全拒絕，不可默默用錯帳號）。
- **共同：** 需確認把帳號環境變數設在子進程，與系統 Keychain／OS 身分是否可能跨帳號共用；若存在不可隔離的行為，先標記 `BLOCKED`，不宣稱 PASS。

參考官方文件（實作時需重新核對版本）：
- Claude Code 環境變數：<https://code.claude.com/docs/en/env-vars>
- OpenAI Codex 設定參考：<https://developers.openai.com/codex/config-reference>
- OpenAI Codex 基本設定：<https://developers.openai.com/codex/config-basic>

### 5.3 Shell Shim：原生命令不必改名

使用者 opt-in 安裝 `codex`、`claude` 的可執行 Shim／Shell Hook，且保留原生程式真正路徑。

- 呼叫 `codex ...` / `claude ...` 時，Shim 在**啟動瞬間**讀取對應 Provider 的目前預設帳號並轉交給隔離環境內的真正 CLI。
- `clinest run` 必須直接呼叫解析到的**真正** CLI，不得無限遞迴呼叫同名 Shim。
- 不取代／覆寫原生二進位檔；初次安裝顯示 PATH 優先序、對 Shell Alias／Function 的可能衝突；提供診斷、卸載和回復方式。
- 未安裝 Shim 時，`clinest run <alias> -- ...` 仍須正常工作。Shim 在執行時不需要 TUI 存活，也無需 Daemon。
- 若無預設帳號、該 Profile 遺失，**清楚報錯而不是默默退回其他身分**；允許使用者透過明確機制執行未受管理的原生 CLI。

### 5.4 子進程契約

- 使用 `exec` / 等效直接 spawn，保留工作目錄、環境（僅覆寫必要變數）、stdin/stdout/stderr、TTY 互動、終端機大小、Ctrl-C/SIGINT、Exit Code。
- 不把使用者提示語經 `shell=True` 拼接；參數採 argv 陣列。
- 預設值變更採原子寫入與必要鎖定；多個 Terminal 同時啟動時各自在啟動瞬間取得固定 Profile，期間不再切換。
- 若被呼叫的 CLI 不存在、登入失效、設定損毀，返回有意義的非零退出碼。

### 5.5 技術選型

暫定 **Python 3.11+ + Textual（TUI）+ argparse/Typer（CLI）**；以 `pipx` / 虛擬環境安裝，macOS zsh 優先。第一版不引入資料庫、常駐服務、網頁框架或 Rust 編譯鏈。依 P0 驗證結果再確定啟動器的最佳實作（Python launcher 或極薄 Shell Shim）。

## 6. 安全設計與資料生命週期

1. 預設不得自動讀取、複製或改寫使用者既有的 `~/.codex` / `~/.claude`；匯入舊帳號需另外明示與評估，不列為 v0.1 必做。
2. 專屬帳號目錄設為使用者可讀寫（例如 POSIX `0700`）；憑證檔案遵循原生 CLI 安全要求，不輸出到 Console／Git／一般 Logs／Crash Report。
3. Profile 別名做格式驗證與正規化，拒絕 `../`、路徑穿越、符號連結跳脫、不允許的執行檔／命令映射。
4. TUI 移除帳號需二次確認；移除 Profile 不等同遠端撤銷所有 Token，應區分本機刪除與原生登出。
5. 優先使用官方原生登入、登出和刷新機制；不自行偽造 OAuth／Refresh 流程；遵守各 Provider 帳號與服務條款。
6. 無管理工具常駐不代表 Token 永遠有效。若官方撤銷登入、網路失敗或要求再授權，回報 `LOGIN_REQUIRED` / `BLOCKED`，不要自行換用另一個帳號。
7. 卸載 CliNest 與撤銷 Shim，不可刪除使用者原本的預設 CLI 配置或覆蓋他的 Shell 設定。

## 7. 工作階段與驗收（需真實 Mac 實測）

| ID | 驗收場景 | 預期 |
| --- | --- | --- |
| AC-01 | 在隔離環境登入 `codex1`、`codex2` | 兩者皆保留可用認證；無覆寫 |
| AC-02 | Terminal A 啟動 `codex`（codex1），改預設 codex2 後 Terminal B 啟動 `codex` | A 維持 codex1；B 使用 codex2 |
| AC-03 | 在 TUI 登入 claude1，再從 Terminal C `claude` | 使用 claude1，不影響 Codex 兩個 Session |
| AC-04 | `clinest run codex1 -- codex exec ...` 與 `clinest run codex2 -- codex exec ...` 同時跑 | 均成功且帳號身分正確，不需輪流登入 |
| AC-05 | 關閉管理 TUI、重開 Terminal、重開機後執行 `run` | 在憑證仍有效時照常使用，不需管理 Daemon |
| AC-06 | Codex／Claude 登入狀態失效 | 能辨識失效並只重登指定 Profile，不污染其他帳號 |
| AC-07 | 原生 CLI `~/.codex`、`~/.claude` 已存在 | 安裝、啟用及卸載 Shim 後，原有檔案及登入不被破壞 |
| AC-08 | 使用不同 CWD、參數、stdin、Ctrl-C | 正常透傳、退出碼與原生 CLI 一致 |
| AC-09 | 兩個 Terminal 同時切換或呼叫預設帳號 | 原子選擇，不產生損壞檔案或混用身分 |
| AC-10 | 錯誤環境變數／缺少 CLI／帳號不存在 | 明確報錯、不默默回退到錯誤帳號 |
| AC-11 | TUI 新增帳號失敗、登入失敗 | 不切換預設、不損壞現有 Profile |
| AC-12 | 檢查日誌與倉庫提交 | 不含 Credential、Token、Password 等敏感內容 |

驗收證據要記錄**測試命令、CLI 版本、退出碼、隱去敏感資訊後的身分驗證結果**。除驗證所需範圍外不應執行有成本或修改專案的 AI 工作；無法在實體 Mac 驗證的項目標記 `NOT TESTED`，不可寫 PASS。

## 8. 初版開發階段

| 階段 | 交付內容 | Gate |
| --- | --- | --- |
| P0 — 原生驗證 | Claude、Codex 的隔離登入、Token Refresh、Keychain、預設環境變數、並行 Session 的最小 PoC | AC-01 / AC-04 核心情境可實測，否則先修正設計 |
| P1 — Core CLI | Provider Adapter、Profile Registry、登入／登出／狀態／預設選擇、`clinest run` | 單元測試＋無全域破壞的隔離整合測試 |
| P2 — Shell + TUI | opt-in 原生命令 Shim、Textual 管理介面、安裝／卸載與恢復操作 | AC-02 / AC-03 / AC-07 |
| P3 — 本機驗收 | 同時開啟多 Terminal、腳本非互動執行、關閉 TUI／重啟／例外恢復 | AC-01 至 AC-12 有可追溯 PASS / FAIL / BLOCKED |

**第一個實作工作應是 P0 PoC，不是先雕琢 TUI 畫面。** 本文件為產品規劃，不等於任何功能已交付或已通過測試。

## 9. 預留擴充（v0.1 之後）

- Provider Adapter 擴展至 Gemini、Antigravity、OpenCode 等，但必須個別確認其原生帳號隔離與 Token 儲存行為。
- 可選的配額／重置時間顯示與本機提醒。
- 進階帳號用途標籤、診斷匯出、多平台安裝；維持「本機／獨立／不用 Daemon」定位。

## 10. 待研究／待決定事項

- Codex 在目標 macOS + CLI 版本下的官方憑證存放模式是否能達成真正雙帳號平行 Refresh；若使用 Keychain，共用項目是否產生衝突？
- Claude 的 Profile 路徑與系統 Keychain 是否會跨 Profile 共享可登入身分？
- Shell Shim 採 executable shim 還是 shell function 最適合，如何保證 `command -v codex` / `command -v claude` 指向可預期目標？
- 若帳號管理檔毀損，如何安全復原預設值而不觸碰原生憑證？
- 使用者明確要求先做 CLI + TUI，故 GUI、Web、額度監測、排程均不進 v0.1。

---

**設計決策：CliNest 是獨立工具，不依附其他 AI 專案；登入管理與本機 CLI 呼叫是唯一 v0.1 核心。**
