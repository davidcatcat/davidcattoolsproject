# PR #84 F1-r3 — M1–M3 test-only repair and Mac evidence (2026-10-10)

> 撰寫：Claude Code（Opus 5.5），在需求方的 Mac canonical repo 上執行
> 依據：[PR #84 coordinator 授權](https://github.com/davidcatcat/davidcattoolsproject/pull/84)、[獨立審查 REQUEST_CHANGES（PR #85）](https://github.com/davidcatcat/davidcattoolsproject/pull/85)、[Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)
> 類型：**開發者修正與實測證據**。修正由 Claude Code 撰寫，因此本報告**不是**獨立審查，也不是 Security/QA PASS。最終 gate 必須由另一位獨立 reviewer 執行。

## 1. 版本與範圍

| 項目 | 值 |
| --- | --- |
| 分支 | `feat/a11b222-f1-private-git-add-20261010-r3`（PR #84，Draft） |
| 修正前 HEAD | `36f972e07465d0b5a8e984b8b766d06142178cfa` |
| 修正後 HEAD | `863fed0218dfc242904d1562a4cc90786947ce10` |
| 變更檔案 | `tools/llmtool/tests/test_git_add_authority.py`（修改）、`tools/llmtool/tests/test_git_add_product_failures.py`（新增） |
| 未變更 | 所有正式程式、CI workflow、ai-loop 控制腳本、launcher |

開工前檢查：
- canonical repo 與分支正確，HEAD 等於授權的 `36f972e`；
- 工作目錄乾淨；
- 沒有 Codex 或 ai-loop 正在執行。

## 2. 三項修正

### M1 — Python 3.14 CI 失敗：已確認原因

- **修正內容（只改測試）：** `runtime()` 拒絕直譯器時，測試仍然**失敗**，但錯誤訊息會附上被觸發的條件：是否為一般檔案、權限、是否群組或其他人可寫、擁有者是否為當前使用者、擁有者是否為 root、大小是否超過 64 MiB。訊息不含路徑。沒有 skip，`file_hash` 也沒有放寬。
- **CI 結果**（exact head `863fed0`，run 37980928198，job 113990929021）：

  ```
  interpreter rejected: regular=True mode=777 group_other_write=True uid_is_euid=True uid_is_root=False size_over_64MiB=False
  Ran 1340 tests … FAILED (failures=1, skipped=24)
  ```

- **原因：** GitHub runner 映像檔**預裝**的 CPython 3.14.8 直譯器權限是 `0777`，任何人都可以寫入。Python 3.9 則是在 job 執行時才下載解壓，權限正常，所以通過。
  - 官方 `actions/python-versions` 的 tarball 中，直譯器權限是 `0755`。差異來自 runner 映像檔，不是 tarball。
- **判斷：** 正式程式拒絕使用可被他人改寫的直譯器，這個行為是**正確的**，不應放寬。
- **建議的修正（尚未套用，需要另外的 workflow 審查）：** 在 `llmtool-ci.yml` 的 `setup-python` 之後加一步：

  ```yaml
  - name: Harden interpreter permissions (trusted-runtime premise)
    run: chmod go-w "$(python -c 'import os,sys; print(os.path.realpath(sys.executable))')"
  ```

  檔案擁有者是 job 使用者，所以不需要 sudo。
- **其餘 Linux 3.14 測試：** 全部通過。24 個 skip 是只能在 macOS 上執行的原生測試。

### M2 — top-level mismatch 測試偽通過：已修正

- 拆成兩個測試：`test_marker_symlink_refuses` 與 `test_toplevel_mismatch_reaches_query_and_refuses`。
- 第二個測試使用 setUp 產生的全新、未被動過的 fixture，依序驗證：
  1. 未 patch 時 `fixture_check` 成功；
  2. patch 之後，查詢被呼叫，而且只有一次 `[_Read.TOPLEVEL]`；
  3. 結果被拒絕。
- **Mutation 驗證：** 在記憶體中暫時拿掉正式程式的 top-level `reject()`，這個測試會**失敗**（`AdmissionRejected not raised`）。所以它現在確實在測這道防護。

### M3 — 正式產品流程的故障驗收：6 種故障情境，共 7 個測試

新檔 `test_git_add_product_failures.py`。測試鏈路是真正的 launcher → `_git_add_host` → `_git_supervisor` → `git`，在 launcher 建立的拋棄式 fixture 上執行。

**故障只從測試端注入：**
- 測試程序直接送 signal、SIGSTOP/SIGCONT，或修改 fixture 內容；
- driver 程序在自己身上包裝 `Popen`，用來回報 fixture root，並包裝 `Reader.receive`，用來回報 frame；
- 沒有任何正式程式的故障旗標。

**用什麼證明故障打在正確的階段：**
- 用 macOS `sysctl KERN_PROCARGS2` 讀取 argv，辨識出真正的 `git … add --` 子程序。它只會在 READY 和 supervisor 最終檢查之後才被產生。
- 用 SIGSTOP 凍結它，並確認 kernel 狀態是 `SSTOP`。
- 實作中發現：macOS 的 `/usr/bin/git` 會 shim → xcrun → git 連續 exec 兩次，SIGSTOP 如果剛好落在 exec 之間會遺失，所以會一直重送，直到確認狀態為 SSTOP。

| # | 情境 | 驗證結果 |
| --- | --- | --- |
| a1 | Host 啟動前內容漂移（同時跑一次對照組，結果 APPLIED） | `AUTHORITY_REJECTED`、containment confirmed；**從未產生 supervisor** |
| a2 | supervisor 一 exec 就凍結（在 READY 和它自己的 preflight 之前），然後漂移 | `MUTATION_START_FAILED`、`NOT_APPLIED`、`exit_code=None`；**沒有出現 `git add`** |
| b | git 被凍結後，對 Host 送 SIGTERM | `STATE_UNKNOWN`/`USER_ACTION_REQUIRED`、`containment_confirmed=false`；supervisor 與 git 被 supervisor 的 last act 清除；Host 與 launcher **保留現場**，沒有回傳 RESULT |
| b′ | 同上，改送 SIGINT | 與 b 相同 |
| c | READY 之後 launcher 死亡（status 讀取端消失） | git 與 supervisor 被清除；Host 的 UNKNOWN 回報送不出去，但仍然**保留現場** |
| d | READY 之後 supervisor 被 SIGKILL | `STATE_UNKNOWN`；孤立的 git **沒有被碰**（仍是 SSTOP）；Host 保留現場 |
| t | supervisor 逾時（git 被凍結，timeout=1） | `STATE_UNKNOWN`；整個程序群組被清除；Host 保留現場 |

每個測試結束時，harness 會清除被保留的 Host、launcher 和 git，並刪除 fixture。

**(e) 子程序逃出 PGID/SID：未實作，依授權範圍規則 STOP。** 原因有三：
- 真正的 `git add` 不會產生 helper，因為 F1 拒絕或中和了 filter、hook 和 fsmonitor；
- 外部程序無法把別人的子程序移出它的群組；
- inventory 檢查不是可以注入故障的產品路徑。

最小的額外範圍有兩個選項，由 coordinator 決定：
1. 新增一個只給測試用的假 `git`，透過明確限定在測試的 plan override 選用（需要修改 `git_mutation` 或 `_git_supervisor`，屬於正式程式）；
2. 接受現有的單元層級逃逸測試（`git_inventory` 會拒絕外部 PGID/SID）為 F1 的足夠涵蓋。

## 3. Mac 實測證據（最終版本 `863fed0`）

環境：macOS 15.7.9（Darwin 24.6.0）。

| 檢查 | 結果 |
| --- | --- |
| 完整測試 `.venv`（Python 3.13.3）`make -C tools/llmtool check` | **1340 tests OK**（skipped=3） |
| 完整測試 `/usr/bin/python3`（3.9.6） | **1340 tests OK**（skipped=4） |
| 原有的 native acceptance `run_containment_acceptance.py` | **14/14 PASS**；`parent_sigkill_plain` 與 `parent_sigkill_popen` 各 20 次，全部 containment confirmed，最大延遲 **0.013 s**（規格 ≤2 s） |
| M3 新測試 | 7/7 OK，連續 5 次執行；在 `/usr/bin/python3` 下也是 7/7 OK |
| 殘留程序（host/supervisor/probe/git） | 無 |
| 殘留 `llmtool-f1-*` fixture 目錄 | 0 |
| Exact-head CI Python 3.9 | PASS（push 與 PR 兩次 run） |
| Exact-head CI Python 3.14 | FAIL：只剩 M1，原因已確認，見 §2 |

**檔案 SHA-256（前 12 碼）：**
- `test_git_add_authority.py` `d463a34042fb`
- `test_git_add_product_failures.py` `a7fc8ae1e58c`

**原生證據檔**（只留在 Mac，不上傳）：`.ai-loop/20261009T164035Z-87238/mac-native-claude-m123.json`，SHA-256 前 12 碼 `43985718c6b1`。

## 4. 待決事項（皆為 AI 團隊內部決定，依 P0 不需要需求方）

1. **M1 的 CI 修正：** 依契約需要另外的 workflow 審查，見 §2 的建議。
2. **M3 (e) 的範圍：** 選項 1 或 2，見 §2。
3. **最終獨立 Security 與 Functional QA：** 必須由 Claude Code 以外的 reviewer，在最終 HEAD 上執行。
4. 不授權在真實使用者 repo 執行 Git ADD；F2 未授權。

## 5. 附帶觀察（與本 PR 無關，建議併入 Issue #83）

- 舊測試模組會累積沒關閉的資料夾 FD（已在 Issue #83 追蹤）。
- 在 Mac 上發現 6 個由假 `codex`（`--model test-model`）留下、已存活約 26 小時的孤兒程序，已被 launchd 收養。時間點與一次缺少相依套件的 `/usr/bin/python3` 全套測試吻合，可能是測試在失敗路徑上沒有清理。我沒有終止這些程序。

你需要做：無
