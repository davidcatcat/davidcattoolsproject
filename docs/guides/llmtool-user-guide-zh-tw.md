# llmtool 使用者指南（第二使用者）

> 本指南僅整理既有文件的要點，依序說明：安裝、設定、第一個 Task、審查與合併。細節以各來源文件為準。

## 1. 安裝

從本倉庫根目錄執行安裝指令，把 skills 以 symbolic link 安裝到 `~/.claude/skills`、`~/.agents/skills` 與 `~/.gemini/config/skills`：

```bash
chmod +x scripts/install-skills.sh
./scripts/install-skills.sh
```

- 因為是 symbolic link，執行 `git pull` 即可同步已安裝的 skills 內容。
- 安裝器不會覆寫目的地已存在的真實目錄。
- 新增 skill 目錄後，需再執行一次安裝器建立新的連結；同步前先檢查本機分支與 dirty 狀態。

（來源：[README](../../README.md)）

## 2. 設定 PM 帳戶

帳戶只能放在使用者設定檔 `~/.config/llmtool/config.yaml`，不可放在專案的 `.llmtool.yaml`。每個帳戶必須且只能有 `kind` 與 `config_dir` 兩個欄位：

- `kind` 為 `claude` 或 `codex`。
- `config_dir` 必須是絕對路徑，指向已存在、由目前使用者擁有、且群組與其他使用者皆不可寫入的真實資料夾；若可被群組或其他使用者寫入，會顯示 `folder not private` 且設定被拒絕。

`roles.pm` 可選擇 PM 使用的帳戶（舊設定 `pm_runtime.claude_config_dir` 仍受支援，兩者同時設定時必須一致）。不一致或無效的設定會回報 `PM_CONFIG_INVALID`。Claude 帳戶資料夾會作為 `CLAUDE_CONFIG_DIR` 傳給 Claude CLI；Codex 帳戶資料夾會作為 `CODEX_HOME` 傳給 Codex CLI。

可用 `/pm accounts` 列出帳戶名稱、種類與資料夾狀態（不顯示路徑），或以 `/pm accounts --check` 對各帳戶資料夾執行登入狀態檢查；若尚未設定帳戶，TUI 會顯示如何新增帳戶。

（來源：[llmtool-pm-setup-zh-tw](llmtool-pm-setup-zh-tw.md)）

## 3. 第一個 Task

在 `llmtool chat --tui` 中建立並連線一個 PM 工作階段（Claude 是預設 runtime；若要 Codex PM 使用 `--runtime codex`）：

```text
/pm new [--runtime claude|codex] [--account NAME] [CWD]
/pm use N|SESSION
/pm connect
```

- `CWD` 是該 PM 工作階段的目錄；連線時提供的目錄必須符合已建立工作階段的目錄。
- `/pm use` 只選取工作階段，選取後還要執行 `/pm connect` 才會連線。
- `/pm list` 顯示最新的 10 筆（最新在前），`/pm list --all` 顯示所有工作階段。

（來源：[llmtool-pm-setup-zh-tw](llmtool-pm-setup-zh-tw.md)）

## 4. 審查與合併

審查與合併的完整流程，請依 [llmtool development workflow](../../tools/llmtool/WORKFLOW.md) 辦理；該文件說明正常的交付流程，以及 Mac 必需驗證、證據移交與合併門檻。
