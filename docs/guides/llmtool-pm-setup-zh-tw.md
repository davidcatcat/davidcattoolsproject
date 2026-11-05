> 本指南依據 base main `47707e5b` 檢查。

# 設定與切換 llmtool PM

本指南說明如何在 llmtool TUI 建立及使用 Claude 或 Codex PM。設定檔中的帳戶只記錄 CLI 設定資料夾的位置；llmtool 只檢查資料夾中繼資料，不讀取其中的認證檔案。

相關文件：[Codex PM 離線驗收資料包](llmtool-codex-pm-offline-acceptance-zh-tw.md)；[llmtool README](../../tools/llmtool/README.md)。

## 帳戶設定

帳戶只能放在使用者設定檔 `~/.config/llmtool/config.yaml`，不可放在專案的 `.llmtool.yaml`。每個帳戶必須且只能有 `kind` 和 `config_dir` 兩個欄位。`kind` 是 `claude` 或 `codex`；`config_dir` 必須是絕對路徑，指向已存在、由目前使用者擁有的真實資料夾，而且群組及其他使用者都不能寫入。資料夾若可由群組或其他使用者寫入，狀態會顯示為「資料夾不私密」，且設定會被拒絕。

以下名稱和路徑都是示意佔位值，請換成自己的值：

```yaml
accounts:
  claude-example:
    kind: claude
    config_dir: /path/to/claude-account
  codex-example:
    kind: codex
    config_dir: /path/to/codex-account
roles:
  pm:
    account: claude-example
```

`roles.pm` 可選擇 PM 使用的帳戶。舊設定 `pm_runtime.claude_config_dir` 仍受支援；如果它和 `roles.pm` 都選了資料夾，兩邊必須一致。不一致或無效設定會導致 `PM_CONFIG_INVALID`。Claude 帳戶資料夾會作為 `CLAUDE_CONFIG_DIR` 傳給 Claude CLI；Codex 帳戶資料夾會作為 `CODEX_HOME` 傳給 Codex CLI。兩者皆由使用者設定中的帳戶解析。llmtool 只檢查資料夾中繼資料，不開啟認證檔案。

`/pm accounts` 會列出帳戶名稱、種類及資料夾狀態，不會顯示路徑。`/pm accounts --check` 會列出 `NAME: logged in yes` 或 `NAME: logged in no`。若尚未設定帳戶，TUI 會顯示如何新增帳戶。

## TUI 指令

在 `llmtool chat --tui` 中使用下列 PM 指令。若 `CWD` 路徑含空格，請用引號括住或加上跳脫字元。

```text
/pm new [--runtime claude|codex] [--account NAME] [CWD]
/pm list [--all]
/pm use N|SESSION
/pm accounts [--check]
/pm connect [SESSION [CWD]]
/pm disconnect
/pm history
/pm queue [clear|send]
/pm
```

Claude 是預設 runtime。建立 Codex PM 時使用 `/pm new --runtime codex --account NAME`；該帳戶必須是 `kind: codex`，否則指令會被拒絕。`/pm use` 只選取工作階段，不會連線；選取後還要執行 `/pm connect` 才會連線。`/pm list` 顯示最新的 10 筆，最新在前；`/pm list --all` 顯示其餘工作階段。

工作目錄（`CWD`）是該 PM 工作階段所在的目錄。建立工作階段時可指定它；連線時提供的目錄必須符合已建立工作階段的目錄。已儲存的工作階段會保留建立時綁定的帳戶；若設定選到不同帳戶，會回報 `PM_ACCOUNT_MISMATCH`。Codex PM 也會以該工作階段目錄作為執行目錄。

## 權限與使用界線

Claude PM 在 Claude Code 的 auto permission mode 執行。Codex PM（Owner Q1 A）使用 `workspace-write` sandbox、開啟網路、不顯示核准提示，且工作目錄是該工作階段的目錄。這代表 Codex PM 可以在該目錄內變更檔案，不必先詢問。停止條件是：任何超出 Owner 授權的動作都要回到只使用 Claude PM。

Owner Q2 C：程式碼沒有設定 Codex PM 費用上限；PM 會回報它觀察到的用量。

TUI 在切換 PM 時不提供 `llmtool task adopt`：未實作或未驗證。CLI 指令 `llmtool task adopt --from-pm-session X --to-pm-session Y` 存在。`/help` 不包含本指南。Owner 個人的 Codex TUI 不由 llmtool 驅動，且不在本指南範圍內。

## 常見錯誤

下表依據 `pm_runtime` 來源說明錯誤代碼；具體原因仍以當次錯誤訊息為準。

| 錯誤代碼 | 意義與處理方向 |
| --- | --- |
| `PM_CONFIG_INVALID` | PM 帳戶設定無效，或帳戶資料夾無法通過檢查。檢查使用者設定中的帳戶、`roles.pm` 與舊 `pm_runtime.claude_config_dir` 是否一致，以及資料夾是否符合私密性要求。 |
| `PM_ACCOUNT_MISMATCH` | 工作階段綁定的帳戶與目前解析出的帳戶不相符。使用建立該工作階段時綁定的帳戶設定。 |
| `PM_SESSION_INVALID` | 工作階段綁定或工作目錄無法驗證。確認使用有效的工作階段及其工作目錄。 |
| `PM_SESSION_BUSY` | 工作階段已有作用中的驅動程式，或無法取得工作階段鎖定。 |
| `PM_SESSION_LIVE_ELSEWHERE` | 工作階段看起來正在其他位置執行；先關閉其他位置的工作階段再使用。 |
| `PM_SESSION_NOT_DEDICATED` | 工作階段沒有符合要求的專用 PM 綁定；建立專用 PM 工作階段後重試。 |
