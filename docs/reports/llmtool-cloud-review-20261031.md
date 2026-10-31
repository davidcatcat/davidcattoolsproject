# llmtool Repo 審查（Claude Code Cloud，2026-10-31）

- 審查對象：`tools/llmtool`，HEAD `e1387d8`（main，#713 之後）。
- 審查者：Claude（Claude Code Cloud session），唯讀審查。
- 性質：**advisory**。這不是 AGENTS.md 定義的正式 Reviewer verdict，也不是 Owner 產品驗收；本報告沒有修改任何程式碼。
- 環境：Linux cloud container、Python 3.13、以 root 執行、shallow clone（最近 50 個 commit）。**這不是 Mac 開發環境**，Mac 行為以 Owner Mac 與 `check (macOS 3.13)` 的證據為準。
- 參考：[2026-10-28 完整技術稽核](full-repository-technical-audit-20261028.md)（AUDIT-001～037）。本報告不重複該稽核的條目。

## 1. 測試結果與環境差異

| 範圍 | 結果 |
|---|---|
| `python -m unittest discover -s tests`（3,201 個） | 依 CI 先安裝 `requirements-ci.txt` 並 `pip install -e tools/llmtool` 後，剩 14 個失敗或錯誤，**全部判定為 cloud 環境差異** |
| `scripts/docs_check.py` | 0 個問題 |

| 失敗的測試 | 原因 | 證據 |
|---|---|---|
| `test_test_runner` 的 10 個錯誤 | 測試寫死的歷史 commit `b0f3b3536490…` 不在 shallow clone 裡 | `git cat-file -e` 回應 `Not a valid object name` |
| `test_codex_direct_developer.test_timeout_kills_owned_process_group`、`test_opencode_worker.test_native_owned_group_descendant_cleanup_is_bounded_and_trusted`、`test_opencode_direct_developer` 的 2 個錯誤 | container 的 PID 1（`process_api`）不回收孤兒 process；被 kill 的子孫留下殭屍（`Z`），`killpg(pgid, 0)` 仍然成功，所以清理結果回報 `UNKNOWN` | 手動 `killpg` 一個 process group 後，`ps` 仍看得到 `Z` 狀態的 `sleep`。Mac 由 launchd 回收孤兒 process，所以 Mac 上不適用 |

第一次執行時另外還有 16 個失敗，原因是套件還沒安裝：測試用 `python -I -m llmtool` 啟動子 process，`-I` 會忽略目前目錄，所以找不到套件。照 CI 的做法安裝後都通過了。

## 2. 程式碼問題（與環境無關）

整體評價：subprocess 全部使用 argv，沒有 `shell=True`。狀態檔用 mkstemp、fsync 再 link/replace 寫入，並以 `O_NOFOLLOW` 拒絕跟隨 symlink。lock 檔會檢查 uid、權限 0600 和 nlink。LLM patch 的路徑必須在 `write_paths` 白名單內，`git apply` 只用 `--check` 或在暫存樹中執行。

| # | 嚴重度 | 位置 | 問題 | 失敗情境 |
|---|---|---|---|---|
| R1 | Medium（已核對） | `llmtool/high_debug.py:227-268` | D6 名稱是 `{task}-hd{len(entries)+1}-{sha7}`，但只有成功時才會執行 `store.append_high_debug` | Codex 回傳非 0、逾時、`HIGH_DEBUG_WROTE_FILES` 或 `REPORT_INVALID` 之後，`entries` 不變，重試會算出同名的 D6 和 branch，結果永遠回報 `HIGH_DEBUG_D6_CREATE_FAILED`，並留下一個沒人清的 worktree。`_create_d6` 的 `timeout=180` 若撐不過 ENV-2 bootstrap，也會撞名。名稱很長的 `executor_task` 加上後綴後，可能超過 lifecycle 規定的 96 字元上限 |
| R2 | Low/Medium（已核對） | `high_debug.py:153-159` | `codex exec` 沒有帶 `env=`，也沒有 `--ignore-user-config`、`--ignore-rules` 或 `shell_environment_policy.inherit="none"`；`codex_direct_developer.py` 則都有 | prompt 會帶進 `--evidence` 的不可信文字（例如 CI log），又開了 live web search。agent 執行的指令看得到父 process 的所有環境變數，也會套用使用者的 `~/.codex` 設定 |
| R3 | Low | `scripts/gw_ladder.py:31-66` | 逾時時只 kill 直接的子 process，之後 `reader.join()` 沒有時間上限；PASS 只看 exit code 和 stdout 裡的 `Ran N tests` | 被測程式開出的子孫 process 會讓評測永遠卡住。被測程式只要印出 `Ran 3 tests` 再 `os._exit(0)`，就會被判定 PASS |
| R4 | Low | `llmtool/developer_routes/write_boundary.py:184` | sandbox 自我檢查把任何非 0 結束都當成「寫入被擋下」 | 目錄不存在或 Python 啟動失敗也會算通過；只有第一個目標有做「可以寫入」的正向檢查 |
| R5 | Low | `llmtool/config.py:280`、`llmtool/providers/openai_compatible.py:333` | `base_url` 可以是非本機的 `http://` | 驗證用的 Authorization 標頭會以明文送出。只有使用者自己的設定能改這個值 |
| R6 | Low | `llmtool/test_runner.py:38`；`scripts/claude_bg_dispatch.py:218`；`scripts/worktree_env.py:1246` | git 呼叫沒有 timeout；`message` 不是 dict 時會發生 `AttributeError`；`mkdtemp` 建立的暫存目錄沒有清掉 | 程式卡住、crash 而沒有產生 receipt、暫存目錄越積越多 |
| R7 | Low（測試） | `tests/test_test_runner.py:133` | 測試寫死歷史 SHA | shallow clone 或歷史被改寫時測試會失敗；建議改成在測試裡建立臨時 git repo |

## 3. 架構

稽核方案 B 建議拆分的熱點檔案，在 3 天內都變大了：

| 檔案 | 2026-10-28 稽核時 | 2026-10-31 |
|---|---|---|
| `llmtool/task_submission_cli.py` | 3,826 行 | 4,572 行 |
| `llmtool/chat_tui.py` | 732 行 | 3,220 行 |
| `llmtool/task_store.py` | 2,547 行 | 2,856 行 |
| `scripts/d6_worktree_lifecycle.py` | 1,560 行 | 2,025 行 |

## 4. 哪些工作 Claude Code Cloud 能獨立推進

這個 cloud session 沒有 `codex`、`opencode`、`agy` CLI，也沒有 Mac 上的 D6 registry 或 provider 憑證，所以不能用 llmtool 派工。依 AGENTS.md，Claude 開發的變更本來就不走 llmtool Task，而是在 PR 上依 Transition record 規則記錄正式 Codex 審查。

| 類別 | Cloud 能做到哪裡 | 需要什麼 |
|---|---|---|
| A. 純邏輯修正（R1～R7） | 寫程式和回歸測試、在 Linux 跑相關測試、push、開 PR | 正式 Codex Reviewer；PR 的 exact-head Mac CI 和 Linux CI |
| B. 測試強化（R7、process group 測試在沒有 init 回收的環境下的行為） | 可以完整完成 | 同 A |
| C. 重構（例如拆分 `task_submission_cli.py`） | 可以做不改變行為的拆分，並用 Linux 全套測試作為回歸 | 同 A；而且會觸發 Mac FULL suite |
| D. 文件和報告 | 可以完整完成 | 只需要 `check (docs)` 和正式審查 |
| E. 實際派工、provider 與 runtime 驗證、D6 worktree 生命週期、Mac sandbox（`write_boundary`）實測 | **不行** | 必須在 Owner Mac 上做 |

注意事項：

- Mac CI 和 Linux CI 都跑在 Owner 自架的 runner 上，而且會檢查觸發者是否為 Owner。cloud push 的 PR 是否會觸發，要以實際 run 為準，這次沒有驗證。
- cloud 跑 Linux 全套測試約 8 分鐘，已知會有第 1 節列的 14 個環境性失敗，這些失敗不能當成回歸。
- 合併需要正式 Codex 審查加上 exact-head CI，這部分仍由 Integrator 在有完整證據時執行。

## 5. 建議順序

1. R1：失敗時也寫入一筆記錄，或在 D6 名稱加上嘗試序號（cloud 可以做，類別 A）。
2. R2：比照 `codex_direct_developer` 的 env 白名單和旗標（cloud 可以做，類別 A；在 Mac 上實際跑一次 High Debug 驗證屬於類別 E）。
3. R7 和 R3～R6（cloud 可以做）。
4. 拆分 `task_submission_cli.py`（cloud 可以做，類別 C）。
