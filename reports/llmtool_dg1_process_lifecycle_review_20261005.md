# llmtool DG-1 process lifecycle fix review（2026-10-05）

## 範圍與方法

- **審查對象：** PR #29 `fix/llmtool-dg1-process-lifecycle-20261005`，HEAD `a7426e0`，以 `origin/main` `e9f550c`（PR #28，QG-S1 報告已 merge）為基礎，共 2 個 commit。
- **變更：**
  - 新增 `providers/cli_lifecycle.py`（94 行）；
  - 修改 `claude_cli.py`、`codex_cli.py`；
  - 新增 `tests/test_cli_lifecycle_state.py`、`tests/test_cli_provider_lifecycle.py`；
  - README 加上 129／143 的說明；
  - 新增 [validation report](llmtool_dg1_process_lifecycle_validation_20261005.md)。
- **對照：** [QG-S1 security baseline](llmtool_qg_s1_security_baseline_20261005.md) 的 DG-1，以及 A11b 的 R-EXEC-6。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree、clone 或專案副本。repo 當時已經在 PR #29 的分支。
  - 逐行閱讀 diff。
  - 用假的 `claude`／`codex`（會 fork 出 `sleep 300` 孫 process）實際送出 signal。
  - 比對 PR 和 main（在同一個 repo 暫時 detach 到 `e9f550c`，結束後切回 PR 分支）。
  - probe 都放在 scratchpad，並以 scratchpad 為工作目錄執行。沒有使用真實的 provider 或 credential。

## 結論：REQUEST CHANGES

- **DG-1 本身已經修好：** SIGTERM、SIGHUP、SIGINT 都會結束整個 process tree，也會清除暫存的 cwd。
- **但這個修正造成 2 個 regression：**
  - **R1（Medium）：** 覆蓋了繼承來的 `SIG_IGN`，導致 `nohup` 和 script 中的背景執行被中斷。
  - **R2（Low–Medium）：** 繼承到 `SIGCHLD` 被忽略的環境時，CLI provider 完全無法使用。
- 另有 2 個 low 的觀察（F1、F2）。
- 建議修正 R1、R2 之後再 merge。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | 918 tests OK（1 skipped），比 main 多 25 個 |
| Python 3.9.6 | 918 tests OK（1 skipped） |
| GitHub Actions（PR #29） | `37233499396`、`37233557525`，3.9 和 3.14 都是 pass |
| HTTP provider | 沒有變更，signal handler 只在 CLI provider 呼叫期間安裝 |

## DG-1 修正驗證（PR `a7426e0`）

我用 driver 直接啟動 python（不經過 shell wrapper），在 CLI 執行 1.5 秒後送出 signal：

| provider | signal | rc | 之後還活著的 process | 新增的暫存目錄 | stderr |
|----------|--------|----|---------------------|---------------|--------|
| claude | TERM | 143 | 0 | 無 | 空 |
| claude | HUP | 129 | 0 | 無 | 空 |
| claude | INT | 130 | 0 | 無 | `llmtool: interrupted` |
| codex | TERM | 143 | 0 | 無 | 空 |
| codex | HUP | 129 | 0 | 無 | 空 |
| codex | INT | 130 | 0 | 無 | `llmtool: interrupted` |
| claude | TERM × 3 | -15（shell 看到的是 143） | 0 | 無 | 空 |
| codex | HUP × 3 | 129 | 0 | 無 | 空 |

**DG-1 確認修好：** 不再有孤兒 process 或孫 process，暫存的 cwd 也都被清除；沒有輸出 provider 的 stderr 或 traceback。

## 問題

### R1（Medium，信心 High，regression）：覆蓋繼承來的 `SIG_IGN`，破壞 `nohup` 和背景執行

- **位置：** `cli_lifecycle.py` 的 `CLIProcessLifecycle.__enter__`。它對 SIGINT、SIGTERM、SIGHUP 一律呼叫 `signal.signal(number, self._record)`，**沒有先檢查原本的 disposition 是不是 `SIG_IGN`**。
- **背景：**
  - `nohup` 讓 process 以 SIGHUP=`SIG_IGN` 啟動。
  - 沒有 job control 的 shell（例如 script 裡的 `cmd &`）會讓背景 job 以 SIGINT=`SIG_IGN` 啟動，避免它被前景的 Ctrl-C 波及。
  - Python 本身也遵守這個慣例：SIGINT 一開始就被忽略時，Python 不會安裝 `KeyboardInterrupt` handler。
- **重現**（`$SP/dg1b.*/ign.py`，用 `preexec_fn` 設定 `SIG_IGN` 後啟動 `ask`，1 秒後送出 signal；假的 CLI 3 秒後回答 `CLI_REPLY_OK`）：

  | 情境 | main `e9f550c` | PR `a7426e0` |
  |------|---------------|--------------|
  | HUP 被忽略（nohup），再送 HUP | **rc=0，`CLI_REPLY_OK`** | **rc=129，沒有輸出** |
  | INT 被忽略（背景 job），再送 INT | **rc=0，`CLI_REPLY_OK`** | **rc=130，`llmtool: interrupted`** |

- **影響：** 使用者用 `nohup llmtool ask …`，或在 script／CI 中把 llmtool 放到背景執行並使用 claude-cli 或 codex-cli 時，關閉 terminal 或對前景按下 Ctrl-C，會讓本來應該繼續執行的呼叫被中斷，答案也會遺失。
- **為什麼違反契約：** README 新增的文字是「stop their owned process group … on SIGINT, SIGTERM and SIGHUP」，並沒有說會覆蓋使用者刻意忽略的 signal；main 的行為是繼續執行。
- **測試缺口：** 測試中的 `SIG_IGN` 只設定在假的 CLI child 身上（用來模擬不聽話的 child），沒有測試 llmtool 本身繼承 `SIG_IGN` 的情況。
- **建議：** 在 `__enter__` 中，如果 `signal.getsignal(n) is signal.SIG_IGN`，就不要為那個 signal 安裝 handler，維持忽略。

### R2（Low–Medium，信心 High，availability regression）：繼承到 `SIGCHLD=SIG_IGN` 時，CLI provider 完全無法使用

- **位置：** `cli_lifecycle.py` 的 `__enter__` 在 `SIGCHLD != SIG_DFL` 時直接 raise `PROVIDER_RUNTIME_ERROR`。
- **重現（同一個 probe）：**
  - main：rc=0，`CLI_REPLY_OK`；
  - PR：rc=1，`llmtool: CLI provider requires default child reaping`、`code=PROVIDER_RUNTIME_ERROR`。
- **說明：** validation report 第 78–79 行把這列為刻意的前提。理由是忽略 SIGCHLD 會讓 child 被自動回收，PID 和 PGID 就不再被「未回收的 child」固定住，`killpg` 可能打到被重複使用的 PGID。這個安全考量是正確的。但是：
  - `SIG_IGN` 的 SIGCHLD 可以透過 exec 繼承，某些 daemon、supervisor 或 runner 會用這種方式啟動子 process。這些環境在 main 上可以正常使用 claude-cli 和 codex-cli，在 PR 上會直接失敗。
  - 拒絕執行不是唯一安全的做法。llmtool 是單一執行緒、同步的 CLI，在呼叫期間把 SIGCHLD 暫時設回 `SIG_DFL`、結束後再還原，就能同時保住 PID 固定和可用性。這只影響自己的 process，也不需要另一個 reaper。
- **建議：** 將 `SIG_IGN` 暫時改成 `SIG_DFL`，結束後還原；只有在有「自訂 handler」（真的有另一個 reaper）時才拒絕。如果決定維持拒絕，至少在 README 的 provider 章節寫明這是對既有環境的 breaking change。

### F1（Low，觀察，不是 regression）：TERM／HUP 時，已儲存的 run 沒有被 finalize

- **重現：** 在 `persist_metadata: true` 的情況下送出 TERM 或 HUP，之後 store 裡的 run 只有 `.reservation`、`.run.lock`、`trace.partial.jsonl`，**沒有 `summary.json`**；INT 則正確 finalize 成 `interrupted`。
- **原因：** `SystemExit(128+n)` 是 `BaseException`，不會進入 `_run_ask` 的 `except KeyboardInterrupt`／`except Exception`；`finally` 只執行 `observe('close')`，沒有 `finish`。
- **影響：** 這個 run 會被當成 abandoned run，在 1 小時的 grace period 內佔用名額；`max_runs` 很小時，會暫時無法儲存。修正前 process 被直接殺掉，結果一樣，**所以不是 regression**。不過現在 llmtool 已經能接手 TERM／HUP，順便把它 finalize 成 `interrupted`，會和 README 寫的「Ask interrupts finalize with status `interrupted`」一致。

### F2（Nit）：重複的 TERM 可能讓 exit 變成「被 signal 殺掉」

- `TERM × 3`（間隔 50ms）時，rc 是 -15，而不是 `SystemExit(143)`。原因是 `__exit__` 先 `_restore()` 還原 handler，再 raise `SystemExit`，這中間到達的第 2 個 TERM 會用預設動作直接殺掉 process。
- 清理已經在 restore 之前完成（沒有孤兒，也沒有暫存目錄），而且 shell 看到的都是 143，所以影響很小。這和 README 的「Repeated signals retain the first cancellation」有些微出入。

## 確認沒問題的部分

- signal handler 只記錄 signum，不在 handler 裡 kill、wait、做 I/O 或 raise。`Popen` 前、讀取迴圈和等待迴圈都用 0.1 秒輪詢 `check()`，反應時間有上限。
- `terminate()` 在 `returncode is None` 時才 `killpg`，而且先 kill 再 wait。這是利用「未回收的 child 會固定住 PGID」的正確順序，不會打到被重複使用的 PGID。
- timeout 的路徑仍然是 `PROVIDER_TIMEOUT`，不會和 cancel 混淆；`_Cancelled` 是 `BaseException`，不會被一般的 `except Exception` 吃掉。
- 先清除暫存目錄，再還原 handler、raise 結束的 exception，順序正確。
- 不在 main thread 時拒絕執行，避免在其他 thread 安裝 signal handler。

## 建議

1. **修 R1**（保留 `SIG_IGN`），並加上 subprocess 測試：在 HUP／INT 被忽略的狀態下啟動，送出 signal，預期會正常完成。
2. **修 R2**（暫時把 SIGCHLD 設回 `SIG_DFL`），或至少明確寫成 breaking change。
3. F1 可以一起處理（TERM／HUP 時 finalize 成 `interrupted`）；F2 可以把 `_restore()` 放到 raise 之後，或維持現狀並修正 README 的說法。
4. 修正後重跑 CI 和這份報告的 probe，再 merge。

## 清理

- 沒有建立任何 worktree、clone 或專案副本；除了這份報告，repo 沒有其他變更。比對 main 時暫時 detach，之後已切回 PR 分支。
- 第一次的 probe 用 shell function 放到背景執行，signal 送到了 bash subshell，而不是 python，所以結果無效。我已經用 `pkill` 和 `TaskStop` 結束自己留下的 probe process，確認沒有殘留，再改用直接啟動 python 的 driver 重做。上表都是第二次的結果。
- probe 留在 scratchpad 的檔案沒有刪除（依照不使用 `rm` 的規則）。上一輪留下的 `tools/llmtool/case.*` 仍然保留。
