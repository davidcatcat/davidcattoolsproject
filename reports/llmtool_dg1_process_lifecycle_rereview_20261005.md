# llmtool DG-1 process lifecycle round-2 re-review（2026-10-05）

## 範圍與方法

- **審查對象：** PR #29 `fix/llmtool-dg1-process-lifecycle-20261005`，HEAD `486149b`。本輪新增的 commit：
  - `506d4b6`：fix(llmtool): preserve inherited signal dispositions
  - `486149b`：更新 validation report
- **變更：** `providers/cli_lifecycle.py`（+20／−8）、README 的說明、`tests/test_cli_lifecycle_state.py`（+104）、`tests/test_cli_provider_lifecycle.py`（+99）。
- **對照：** 上一輪的 [DG-1 review](llmtool_dg1_process_lifecycle_review_20261005.md)（R1、R2、F1、F2，REQUEST CHANGES）。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree、clone 或專案副本。
  - 逐行閱讀 `b8fe3dd..486149b` 的 diff。
  - **用上一輪的同一組 probe 重跑**：`ign.py`（繼承 `SIG_IGN`）和 `drive.py`（signal 矩陣），另外補了 2 個情境。
  - probe 都在 scratchpad 內、以 scratchpad 為工作目錄執行，使用假的 claude／codex，沒有使用真實的 provider 或 credential。

## 結論：APPROVE（R1、R2 確認修好，可以 merge）

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | 928 tests OK（1 skipped），比上一輪多 10 個 |
| Python 3.9.6 | 928 tests OK（1 skipped） |
| GitHub Actions（`486149b`） | `37237912014`、`37237914186`，3.9 和 3.14 都是 pass |
| 範圍 | 只處理 R1、R2、F2；F1 依 task contract 延後到 QG-QA1 triage |

## 逐項確認

| # | 修正方式 | 上一輪（`a7426e0`） | 本輪實測（`486149b`） | 判斷 |
|---|---------|-------------------|---------------------|------|
| R1 | 記錄每個 signal 原本的 disposition；**原本是 `SIG_IGN` 的就不安裝 handler**，維持忽略 | nohup（HUP 被忽略）+ HUP → rc=129，沒有輸出；背景 job（INT 被忽略）+ INT → rc=130 | **nohup + HUP → rc=0，`CLI_REPLY_OK`；背景 job + INT → rc=0，`CLI_REPLY_OK`**（和 main 相同） | **修好** |
| R2 | SIGCHLD 是 `SIG_DFL` 時不動；是 `SIG_IGN` 時先記錄，**暫時改成 `SIG_DFL`**，清理結束後還原；只有自訂 handler（另一個 reaper）才拒絕 | 繼承 SIGCHLD=`SIG_IGN` → rc=1 `PROVIDER_RUNTIME_ERROR` | **rc=0，`CLI_REPLY_OK`**；SIGCHLD 被忽略時再送 TERM → rc=143，正確清理 | **修好** |
| F2 | 只修文件：README 改成「restore 之後的重複 signal 可能走一般的 OS disposition；在支援的清理路徑上，process tree 和 runtime 的清理會在 restore 之前完成」 | `TERM × 3` → rc=-15，和 README「retain the first cancellation」不一致 | 行為不變（`TERM × 3` 仍可能是 -15），但 README 已改成符合實際行為；清理完整、沒有殘留 | **可以接受** |
| F1 | 延後到 QG-QA1 triage，diagnostics 的生命週期沒有改變 | TERM／HUP 時，儲存的 run 沒有 summary | 不變（TERM／HUP 的 run 仍然只有 `.reservation`、`.run.lock`、`trace.partial.jsonl`） | **延後，可以接受**（不是 regression，已寫明） |

### 補充的情境

| 情境 | 結果 |
|------|------|
| HUP 被忽略的狀態下送 TERM（只忽略其中一個 signal，其他 signal 仍要處理） | rc=143，正確清理 |
| SIGCHLD 被忽略的狀態下送 TERM | rc=143，正確清理，SIGCHLD 之後還原 |

## DG-1 regression 檢查（signal 矩陣，`486149b`）

| provider | signal | rc | 之後還活著的 process | 新增的暫存目錄 | stderr |
|----------|--------|----|---------------------|---------------|--------|
| claude | TERM | 143 | 0 | 無 | 空 |
| claude | HUP | 129 | 0 | 無 | 空 |
| claude | INT | 130 | 0 | 無 | `llmtool: interrupted` |
| codex | TERM | 143 | 0 | 無 | 空 |
| codex | HUP | 129 | 0 | 無 | 空 |
| codex | INT | 130 | 0 | 無 | `llmtool: interrupted` |
| claude | TERM × 3 | -15（shell 看到的是 143，已寫明） | 0 | 無 | 空 |
| codex | HUP × 3 | 129 | 0 | 無 | 空 |

DG-1 的修正沒有退化：沒有孤兒 process，暫存的 cwd 都被清除，`/private/tmp` 也沒有新增 `llmtool-claude-*` 或 `llmtool-codex-*`。

## 程式碼審查

- **還原順序：** SIGCHLD 先寫進 `self.previous`，再改成 `SIG_DFL`，所以就算安裝其他 handler 時失敗，`_restore()` 也能把它還原成 `SIG_IGN`。`__exit__` 是先 `stop()`（kill 並 wait，回收 child），再 `_restore()`，所以把 SIGCHLD 改回 `SIG_IGN` 時已經沒有未回收的 child，PGID 的固定在整個呼叫期間都成立。
- **被忽略的 signal：** 原本是 `SIG_IGN` 的 signal 也會被記錄到 `previous`，`_restore()` 時再設回 `SIG_IGN`（等於沒有變），不會誤裝 handler。
- **自訂的 SIGCHLD handler 仍然拒絕：** 這是正確的。另一個 reaper 會讓「未回收的 child 固定住 PGID」不再成立，可能把 `killpg` 打到被重複使用的 PGID。
- **其他：** 沒有新的行為改變；HTTP provider 不受影響。

## 剩下的項目（不影響 merge）

| # | 內容 | 狀態 |
|---|------|------|
| F1 | TERM／HUP 時沒有把儲存的 run finalize 成 `interrupted`，run 會在 1 小時的 grace period 內佔用名額 | 已延後到 QG-QA1 triage |
| TR-3 | 刻意用 setsid 脫離的 trusted child 不會被結束；SIGKILL 或主機 crash 後無法清理 | README 已寫明，屬於 QG-S1 的 TR-3 和 A11b R-EXEC-6 的範圍 |

## 流程記錄

- 除了這份報告，repo 沒有其他變更。
- **這輪自己的 probe 失誤，如實記錄：**
  - 第一次執行 signal 矩陣時，`drive.py` 的清理邏輯會 `kill -9` 所有 command line 含有 `sleep 300` 的 process，而我那次的 shell 指令本身就含有這段文字，所以 shell 被自己殺掉，沒有任何輸出。
  - 下一次執行的前兩列因此受到前一次殘留 process 的干擾（`kids_before=0`，出現 `exited with status -9`），結果無效。
  - 我確認已經沒有殘留的 process 或暫存目錄之後，用全新的目錄、而且指令不含那段文字的方式重跑。**上表都是乾淨重跑的結果。**
- `/usr/bin/python3` 在 `env -i` 下找不到 PyYAML，所以 runtime probe 只用 3.13 執行；3.9 的行為由完整測試和 CI 涵蓋。
- probe 留在 scratchpad 的檔案沒有刪除（依照不使用 `rm` 的規則）。`tools/llmtool/case.*` 仍然保留。
