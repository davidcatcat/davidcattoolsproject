> 本驗收資料包依據 base main `47707e5b` 檢查。

# Codex PM 離線驗收資料包

本文件整理離線測試、既有記錄及尚待完成的真實 Codex PM 驗收要求。假資料測試與 PTY 檢查都不能證明真實 Codex PM 的行為。

相關文件：[llmtool PM 設定指南](llmtool-pm-setup-zh-tw.md)；[llmtool README](../../tools/llmtool/README.md)。

## 1. 離線測試

| 測試模組 | 涵蓋內容 |
| --- | --- |
| `test_pm_runtime_codex` | Codex PM runtime 的工作階段綁定、CLI 呼叫與續接、事件及用量、錯誤、停止處理，以及不符合條件的工作階段拒絕。 |
| `test_pm_runtime` | Claude PM runtime 的設定與帳戶解析、事件和歷史、工作階段綁定與生命週期、輸入及輸出篩檢、工具描述，以及訊息交付狀態。 |
| `test_chat_tui_pm_management` | 只涵蓋帳戶列出、登入檢查，以及建立／列出／選取 PM 工作階段。 |

從 `tools/llmtool` 目錄執行以下命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:$PWD/tests .venv/bin/python -B -m unittest test_pm_runtime_codex test_pm_runtime test_chat_tui_pm_management
```

依專案規則，這些測試不在 D6 worktree 內執行；測試本身不會強制此限制。`test_pm_runtime_codex` 使用假的 `codex` 程式。`test_pm_runtime` 主要以程式內的假程序測試 Claude runtime，並在 Codex adapter／binding 案例使用假的 `codex` 程式。這兩個 runtime 模組不呼叫真實 provider。`test_chat_tui_pm_management` 使用的 provider 程式未驗證。

PM 觀察（2026-11-05，base main `47707e5b`）：這三個模組在一份獨立複本中、使用空的臨時 `HOME`，共執行 100 項測試，結果為 `OK`。`HOME` 必須是正規化路徑；若 `HOME` 含有 `..`，`test_tool_descriptions_display_paths_by_location` 會失敗。這是 PM 本機執行的觀察記錄，不是 CI 結果。

已提交的 `tests/tui_pty_driver.py` 和 `tests/test_chat_tui_pty.py` 沒有 Codex PM 情境。因此，下方所記錄的 PTY 檢查是 PM 使用臨時建立的假 `codex` 程式執行，沒有可重複執行它們的已提交腳本。

## 2. 已記錄的歷史證據

以下是歷史記錄，不代表本次重新執行：

- W64（PR 815）：Mac PTY 假 `claude` 與假 `codex` 自我測試，在 120x30 和 80x24 尺寸合計 18 項檢查通過。內容包括 `/pm new --runtime codex --account NAME`，以及拒絕種類不符的帳戶和權限為 `0777` 的資料夾。
- W65（PR 822）：6 項檢查中 6 項通過。
- Owner 於 2026-11-03 對 W64 和 W65 給予真實使用 OK；涵蓋的是 `/pm` 指令，不是真實 Codex PM。

## 3. 證據與狀態清單

證據等級分開記錄假離線測試、假程式 PTY、Owner 實機使用與真實 Codex PM。狀態表示目前記錄的情況。

| 項目 | 證據等級 | 狀態 |
| --- | --- | --- |
| 離線測試模組 | 假離線測試 | PM 本機執行：100 項測試 `OK`（獨立複本、空的臨時 `HOME`；非 CI） |
| W64 的 Mac PTY 檢查 | 假程式 PTY | 歷史記錄：18 項 PASS |
| W65 的 PTY 檢查 | 假程式 PTY | 歷史記錄：6 項中 6 項 PASS |
| Owner 對 W64 和 W65 的真實使用 OK | Owner 實機使用 | 歷史記錄：涵蓋 `/pm` 指令，不代表真實 Codex PM；使用的 provider 未驗證 |
| 使用真實帳戶執行真實 Codex PM | 真實 Codex PM | 未執行 |

**驗收規則：假程式或假資料執行永遠不能證明真實 Codex 行為。**

## 4. 尚未執行的真實驗收要求

依據 Owner 於 2026-11-02 的決策，要求進行一次真實 Mac PTY 執行，使用真實 Codex PM 和實際帳戶，並限制對話回合數及回報用量。這項執行會使用 Codex quota，必須事先告知 Owner。

PM 必須先測試；只有 PM 通過後，才請 Owner 測試，並附上證據。以上均為待完成要求，不是已執行的測試；本文件不指定回合數，也不執行這些測試。
