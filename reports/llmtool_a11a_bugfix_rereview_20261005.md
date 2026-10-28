# llmtool A11a B1–B7 bugfix re-review（2026-10-05）

## 範圍與方法

- **審查對象：** PR #26 `fix/a11a-post-acceptance-bugs-20261005`，HEAD `a3ba359`，以 `origin/main` `e386982` 為基礎，共 8 個 commit。
- **變更：**
  - production：`cli.py`、`diagnostics_cli.py`、`diagnostics_reader.py`、`diagnostics_runtime.py`、`diagnostics_store.py`（+229）、`doctor.py`、`terminal.py`；
  - 新增 5 個 regression 測試檔，共 26 個測試；
  - 新增 [bugfix validation report](llmtool_a11a_bugfix_validation_20261005.md)。
- **對照：** [A11a full bug hunt](https://github.com/davidcatcat/davidcattoolsproject/blob/feddc7e/reports/llmtool_a11a_full_bug_hunt_20261005.md) 的 B1–B7。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree、clone 或專案副本。repo 當時已經在 PR #26 的分支。
  - 逐行閱讀 diff。
  - **用 bug hunt 時的原始 probe 重新執行**；B2 另外寫了一個新的 SIGKILL probe，涵蓋新的 eviction 流程。
  - 全部 probe 都在 scratchpad 內、以 scratchpad 為工作目錄執行，使用 `env -i` 和獨立的 `HOME`／`LLMTOOL_HOME`。
  - 最後用公司 gateway（`gw` = qwen3.6-35b-a3b）做一次實測。

## 結論：B1–B7 全部確認修好，APPROVE

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | 893 tests OK（1 skipped），比 main 多 26 個 |
| Python 3.9.6 | 893 tests OK（1 skipped） |
| GitHub Actions（PR #26） | `37224378938`、`37225261526`，3.9 和 3.14 都是 pass |
| 範圍 | 只處理 B1–B7；L1、L2、M2／M4／M5、P1–P4、N1 依 task contract 維持不變 |

## 逐項確認（原始 probe 重跑）

| # | 修正方式 | 修正前（bug hunt） | 修正後（實測） | 判斷 |
|---|---------|-------------------|---------------|------|
| B1 | `ask`、`runs`／`trace`、`doctor` 在寫入後立刻 `flush()`，在記錄成功之前就能偵測到 EPIPE；`silence_broken_pipe` 把 fd 1 `dup2` 到 `/dev/null`，避免結束時再 flush 一次 | exit 120，Python 印出 `Exception ignored`，`summary` 記成 `success` | `ask --save-diagnostics`（normal 和 verbose）：exit 0、stderr 沒有雜訊、`status=output_closed`。`runs`、`trace`、`trace --json`、`doctor`：3.13 和 3.9 都是 exit 0。公司 LLM 的 `ask --verbose \| true` 也記成 `output_closed` | **修好** |
| B2 | eviction 改成先寫 durable 的 journal（`.eviction-<hex>.json`，用不覆蓋的 rename 發布），再把整個 run 原子地移到 root 的 `.evicted-<hex>`，之後才逐一 unlink。每次 start 會先恢復未完成的 journal，並在每個步驟重新驗證 identity | 第 2 次 unlink 時 EIO 或 SIGKILL，留下永遠無法回收的半個 run，`max_runs=1` 時永久 FULL | **新的 SIGKILL probe**（`max_runs=1`），在 journal 發布後、quarantine rename 後、第 1／2／3 次 unlink 後、journal unlink 後各殺一次：**6 種情況下，之後的 2 次 start 都成功**，reader 顯示 `damaged=0`，只剩新的 run。EIO 的原始 probe：發生錯誤的那次 start 失敗，下一次 start 就恢復成功 | **修好** |
| B3 | `RunContext.response()` 改成逐一驗證每個欄位，只丟掉不合格的欄位；全部不合格時維持原本的整筆 drop | `my-token:latest`、`gpt-4oé` 會讓所有 metadata 變成 `{}` | `my-token:latest` 和 `gpt-4oé` 都保留 `elapsed_seconds`、`prompt_tokens`、`total_tokens`，只丟掉 `model`（dropped=1）；`qwen3.6-35b-a3b` 全部保留；全部不合格時仍是 `{}` | **修好** |
| B4 | `setup_persistence()` 遇到設定錯誤時，先標記「要求了儲存但無法使用」，再 re-raise | `CONFIG_INVALID … trace=not-saved` | `CONFIG_INVALID … trace=unavailable`，加上一筆 `DIAGNOSTICS_STORE_UNAVAILABLE`；exit 1、0 次 request，和 skill 錯誤的路徑一致 | **修好** |
| B5 | reader 的 `_directory`／`_lock` 和 writer 的 `_warning` 都把 ELOOP、ENOTDIR 歸類為 UNSAFE | symlink 的 lock、目錄、`runs/`、`.store.lock` 被回報成 UNAVAILABLE | 這 4 種 symlink 在 `trace`、`runs`、`doctor` 都回報 `STORE_UNSAFE`。`ask` 的 `LLMTOOL_HOME` 是 symlink（包括 macOS 的 `/tmp`）時回報 `DIAGNOSTICS_STORE_UNSAFE`。相對路徑、控制字元、`..` 等不合法的位置仍是 UNAVAILABLE，這是正確的 | **修好** |
| B6 | `runs` 對 `incomplete` 而且沒有 code 的 row 顯示 `unknown` | `final_error_code=none` | `recorded_status=incomplete … final_error_code=unknown`；已完成的 run 仍顯示 `none` | **修好** |
| B7 | 已 commit 之後，`RunContext` 不再把 `persisted` 改回 false；cleanup 失敗時改用另一個訊息 | `diagnostics_persisted=false`、`trace=unavailable` | `diagnostics_persisted=true`、`trace=saved`，warning 是 `code=DIAGNOSTICS_WRITE_FAILED diagnostics cleanup failed; saved metadata retained` | **修好** |

## 公司 LLM 實測（PR #26 `a3ba359`）

- **設定：** 使用者 config 設定 `persist_metadata: true`、`max_runs: 3`。
- **依序執行 5 次 `ask`：** stdout 全部正確；最後剛好 3 個 run，root 只有 `.store.lock` 和 `runs`，**沒有殘留的 journal 或 quarantine**。這代表新的 eviction 流程在實際使用中會正常清乾淨。
- **同時執行 4 次 `ask`：** 全部 rc=0、回答正確。其中 1 次是 `DIAGNOSTICS_STORE_FULL`（執行中的 run 不能被淘汰，屬於預期行為）。最後仍是 3 個 run，沒有殘留。
- **broken pipe：** `status=output_closed`，`runs` 顯示 `recorded_status=output_closed`。
- **`doctor`：** `store=present run_count=3`。
- **隱私：** 在 store 裡搜尋 prompt 和回答的標記，**找不到**。

## 剩下的觀察（不影響 merge）

| # | 內容 | 說明 |
|---|------|------|
| O1 | B1 只修了 A11a 的指令（`ask`、`runs`、`trace`、`doctor`）。`analyze-log` 等既有指令在 buffered broken pipe 時仍是 exit 120（原始 probe 的對照組） | A11a 之前就存在，不在 A11a 的範圍內。之後可以考慮在 `main()` 層級統一處理 |
| O2 | B2：如果 SIGKILL 剛好發生在 `.run.lock` unlink 之後、`rmdir` 之前，會永久留下一個**空的** `.evicted-<hex>` 目錄（實測 unlink#3）。不佔 run 名額，也不影響之後的儲存 | 已在 validation report 寫明（「preserve lockless empty quarantine」），理由是無法證明它是同一個 incarnation。和 N1 同類，只會累積掃描名額 |
| O3 | SIGKILL 發生在 journal 發布之前時，留下的 `.eviction-init-*` 不會被清理 | 和 N1 的 staging 一樣，屬於已寫明的保守行為 |
| O4 | README 沒有說明新的 eviction journal、quarantine，以及 B7 的新 warning 文字 | 建議 merge 時順便在 README 的 store 章節補一句；不是 bug |

## 清理

- 沒有建立任何 worktree、clone 或專案副本。這輪除了新增這份報告之外，repo 沒有其他變更。
- 上一輪 bug hunt 時誤建的 3 個未追蹤目錄 `tools/llmtool/case.*` 仍然保留，等使用者決定。這輪的 probe 都以 scratchpad 為工作目錄執行，沒有新增任何檔案到 repo。
- 實測沒有讀取或寫入使用者真正的 llmtool 設定或 store，也沒有使用任何 API key。
