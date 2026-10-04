# llmtool PR #24 M1／M3 persistence hardening re-review（2026-10-04）

## 範圍與方法

- 審查對象：PR #24 `feat/a11a6-offline-doctor-cloud-20261004`，HEAD `cc017d7`（fix(llmtool): harden initialization and bound cleanup descriptors）。
  - 基礎是 `3d0d04c`，也就是上一輪 [A11a.6 review](llmtool_a11a6_offline_doctor_review_20261004.md) 上傳後的 commit。
  - 變更：`diagnostics_store.py`（+231／−90）、`tests/test_diagnostics_store.py`（+413，新增 12 個測試）、[validation report](llmtool_a11a6_persistence_hardening_validation_20261004.md)。
- 這一輪 Codex 只處理 M1 與 M3。M2、M4、M5、P1–P5 依 task contract 明確延後。
- 方式：
  - 在 canonical repo 進行，沒有使用 worktree、clone 或專案副本。
  - 逐行閱讀 diff，在本機執行完整測試，用 `gh` 確認 CI。
  - 用公司 gateway（`gw` = qwen3.6-35b-a3b）實測。修正前後的比較是在同一個 canonical repo 暫時 detach 到 `3d0d04c` 執行。
  - 每個情境都用 `mktemp -d` 建立獨立的 `HOME`／`LLMTOOL_HOME`，沒有碰使用者真正的設定和 store，也沒有使用 API key。

## 結論：APPROVE（M1、M3 確認修好）

- M1、M3 都確認修好，而且沒有發現 regression。
- 新發現 1 個 low 等級的問題（N1），不影響 merge。
- P1（doctor 遇到一個損壞的 run 就整個失敗）仍未修，依然建議在 merge 前或 A11a.7 處理。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 867 tests OK（1 skipped），比上一輪多 12 個 |
| Python 3.9.6（`/usr/bin/python3`） | 867 tests OK（1 skipped） |
| GitHub Actions（`cc017d7`） | push `37211480402`、pull_request `37211484050`，3.9 和 3.14 都是 success |
| 範圍 | production 程式碼只改了 `diagnostics_store.py`；doctor、reader、ask、config、provider 都沒有變 |

## 修正內容

### M1：staged initialization

- `start()` 不再直接在 `runs/` 裡建立 run，改成：
  1. 在 store root 建立私有的 `.run-init-<random>` 暫存目錄；
  2. 在裡面建立 `.run.lock`（並取得 EX lock）、完整寫入並 fsync `.reservation`、建立 trace；
  3. 驗證 reservation 和 trace、比對 snapshot；
  4. 用原生的「不覆蓋」directory rename 一次發布到 `runs/<id>`：macOS 用 `renameatx_np(RENAME_EXCL=0x4)`，Linux 用 `renameat2(RENAME_NOREPLACE=1)`。沒有會覆蓋的 `os.rename` fallback。
- 發布前失敗時，在仍持有 lock 的情況下，只 unlink 這次建立、而且 inode 相符的檔案。暫存目錄本身不 `rmdir`，原因是 `mkdir` 無法證明目錄是自己建立的。
- 發布後失敗時保留 run，交給既有的 1 小時 grace period 回收。
- 這樣一來，寫一半的 reservation 不會出現在 `runs/`，也不會佔用 run 名額。
- `runs/` 裡**原本就存在**的損壞 reservation，依設計仍然 fail-closed。這點已在 report 寫明，我同意這個取捨：只憑檔名、owner 和權限，不足以證明可以安全刪除。

### M3：清理時不再持有所有候選 run 的 fd

- 掃描時只記錄 metadata 和 snapshot（inode、mode、uid、nlink、size、mtime_ns、ctime_ns），每個候選 run 驗證完就關掉 fd。
- 要清掉 reservation 或刪除 run 時，才重新 open、lock，並完整驗證；snapshot 不一致就跳過。
- 刪除每個檔案前，以及 `rmdir` 前，都重新確認 inode。
- lock 的順序（先 root、再 run）、不等待的 active run 保護、容量計算和淘汰順序都沒有改變。

## 公司 LLM 實測（`cc017d7`）

| # | 情境 | 結果 |
|---|------|------|
| A | 一般 `ask --save-diagnostics --verbose` | rc=0，`diagnostics_persisted=true`。完成後 store 只有 `.store.lock` 和 `runs/<id>/{.run.lock,summary.json,trace.jsonl}`，**沒有殘留暫存目錄**；目錄都是 0700，檔案都是 0600；store 裡找不到 prompt 標記 |
| B | 模擬 SIGKILL 留下的暫存目錄：root 底下有 `.run-init-<hex>`，裡面是 `.run.lock` 和寫一半的 `.reservation` | **之後的儲存成功**（rc=0、`diagnostics_persisted=true`）。`doctor` rc=0、`run_count=2`，`runs` 正常。暫存目錄被保留（見 N1） |
| C | 修正前就存在的狀態：`runs/<id>` 裡有空的 `.reservation` | 仍然是 `DIAGNOSTICS_STORE_FULL`，回答和 rc=0 不受影響。這是設計上的 fail-closed，report 已寫明 |
| D | `max_runs=4`，同時執行 6 個儲存 | 6 個都是 rc=0，回答正確。2 個印出 `DIAGNOSTICS_STORE_FULL`（執行中的 run 不能被淘汰，屬於預期行為），最後剛好剩 4 個 run，沒有殘留暫存目錄 |
| E | fd 上限：store 有 150 個 run、`max_runs=150`，在 `ulimit -n` 為 64／48／32／24 時儲存 | 全部 `diagnostics_persisted=true`，run 數維持 150 |
| F | 淘汰順序：`ulimit -n 24` 時連續儲存 3 次，檢查最舊的 3 個 run | **`cc017d7`：最舊的 3 個都被淘汰（正確）**。**修正前的 `3d0d04c`：只淘汰了 2 個，第 3 舊的仍然存在**，代表有比它新的 run 被刪掉了 |

### 更正上一輪對 M3 的描述

上一輪我寫 M3 的影響是「cleanup 永遠無法完成，儲存一直停用」，並標註觸發條件「不確定」。這次在 macOS 上用 150 個 run、`ulimit -n` 降到 24 實測修正前的版本，**沒有出現儲存停用**。實際觀察到的是：

- 超過 fd 上限的候選 run 在 `_candidate` 被默默跳過（遇到 EMFILE 回傳 None 時會先關掉自己開的 fd，所以通常還剩 1 個 fd 給 `usage()` 使用）。
- 結果是**淘汰的不是最舊的 run**（情境 F）。

會不會完全停用，取決於剩下的 fd 數量是奇數還是偶數，這次沒有重現。修正後兩種情況都不會發生，所以結論不變：M3 確認修好。

## 問題

### N1（Low，新發現）：暫存目錄永遠不會被清掉，doctor 也看不到

- **說明：** 在發布前失敗的 `.run-init-*` 會一直留在 store root，包括 rollback 後留下的空目錄，以及 SIGKILL 後留下的部分內容。程式碼刻意不 `rmdir`，也沒有任何清理機制。
- **為什麼影響小：** 它們會計入 root 的容量和掃描上限（`test_unknown_staging_bytes_remain_capacity_accounted`），所以不會造成容量的低估。
- **但是：**
  - 每次在 `mkdir` 之後發生、可處理的初始化失敗，都會多留一個目錄。例如 quota 或 inode 用完時，每次儲存都會重試。累積到 `SCAN_LIMIT`（10000）之後，就會變成永久的 `DIAGNOSTICS_STORE_FULL`。這和 M2 是同一類問題。
  - `doctor` 的 `unknown_count` 只計算 `runs/` 裡的項目，所以使用者看不到這些殘留（實測 B：`unknown_count=0`）。
- **建議（放到 M2 或 A11a.7 即可）：**
  - doctor 回報 root 層的 `staging_count`。
  - 或者：超過 grace period、沒有被 lock、而且只有已知檔名的暫存目錄，在持有 root EX lock 時可以清掉。這裡的條件比 `runs/` 寬鬆，因為暫存目錄從未發布，不是任何人的資料。

### 延後項目（維持上一輪的判斷）

| # | 狀態 | 說明 |
|---|------|------|
| P1 | 未修 | 只要有一個損壞的 run，doctor 就整個失敗（rc=1、沒有計數）。修正前就存在的損壞狀態仍會觸發。**仍建議 merge 前或 A11a.7 優先處理** |
| P2–P5 | 未修 | 都是 low 或 nit |
| M2 | 未修 | trace 最後一行寫一半、`.summary-*` 暫存檔等其他殘留仍不會被回收 |
| M4、M5 | 未修 | 效能與 reader 掃描上限 |

## 程式碼確認沒問題的部分

- 原生 rename 的 flag 正確：Darwin 的 `RENAME_EXCL` 是 0x4，Linux 的 `RENAME_NOREPLACE` 是 1。找不到 symbol 時丟出 `ENOTSUP`，fail closed。Linux 分支由 CI 驗證。
- 發布前後都會比對路徑和持有的 fd 是否是同一個 inode，存在的空目錄或 symlink 都不會被覆蓋（`test_native_directory_publication_never_overwrites_existing_targets`）。
- rollback 只在持有 run lock 時執行，只刪除 inode 相符、私有、單一 link 的檔案。遇到未知、被替換、hard link 或 symlink 的檔案就整個放棄（`test_start_rollback_preserves_unprovable_inventory`）。
- reopen 時比對完整的 snapshot，被修改、被替換或執行中的候選 run 都會被跳過（`test_capacity_reopen_preserves_mutated_replaced_and_active_candidates`）。
- 有用 subprocess 在低 `RLIMIT_NOFILE` 下測試清理，並檢查 fd 有沒有洩漏；也有 3 個 SIGKILL 中斷點的測試（寫入 reservation 途中、發布前、發布後）。
- `ask` 的 stdout、exit code、預設不儲存，以及只呼叫 provider 一次，都沒有改變（實測 A–F）。

## 清理

- 沒有建立任何 worktree、clone 或專案副本。修正前後的比較是在 canonical repo 暫時 detach 到 `3d0d04c` 執行，結束後切回 PR 分支；上傳報告後切回 `feat/a11a3-secure-diagnostics-store-20261003`。
- 實測只使用 scratchpad 裡的暫存 `HOME`／`LLMTOOL_HOME`，沒有讀取或寫入使用者真正的 llmtool 設定或 store，也沒有使用任何 API key。
