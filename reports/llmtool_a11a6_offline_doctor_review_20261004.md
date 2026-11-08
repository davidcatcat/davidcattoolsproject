# llmtool A11a.6 offline doctor review，加上 A11a.3–A11a.5 持久化的公司 LLM 實測（2026-10-04）

## 範圍與方法

- 審查對象：
  - PR #24 `feat/a11a6-offline-doctor-cloud-20261004`，HEAD `525f981`（以 main `da027f0` 為基礎，3 個 commit：`c09e832` 實作、`909d390` Windows 憑證 key 正規化、`525f981` 文件）。
  - main `da027f0` 上已 merge 的 A11a.3–A11a.5 持久化（`diagnostics_store.py`、`diagnostics_reader.py`、`runs`／`trace`）。
- 方式：
  - 在 canonical repo 進行，沒有使用 worktree、clone 或專案副本。review 時暫時 checkout PR 分支，完成後切回原本的分支。
  - 逐行閱讀 diff 與 store／reader 程式碼。
  - 本機執行完整測試，用 `gh` 確認 CI。
  - 用公司 gateway（`http://home-llm.example:8000`，route `gw` = qwen3.6-35b-a3b，`enable_thinking: false`，不需要 key）實測。所有測試都用 `env -i` 加上獨立的 `HOME`／`LLMTOOL_HOME`（scratchpad 裡用 `mktemp -d` 建立），沒有碰使用者真正的設定和 store。

## 結論

| 對象 | 判定 |
|------|------|
| PR #24（A11a.6 offline doctor） | **APPROVE WITH CHANGES**：沒有阻擋 merge 的問題。P1 建議在 merge 前修，其餘可以放到 A11a.7 |
| main 上的持久化（A11a.3–A11a.5） | **安全性 APPROVE**；**可用性 APPROVE WITH FOLLOW-UPS**（M1–M3） |

沒有找到洩漏資料或破壞 store 完整性的路徑。但 writer 在預期內的失敗（磁碟滿、被 kill、fd 用完）之後，可能留下自己無法清掉的殘留，讓之後的儲存一直停用，直到有人手動清理。所有問題都**不會改變 `ask` 的結果、exit code 和 provider 呼叫次數**。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 855 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`） | 855 tests OK（1 skipped） |
| GitHub Actions | `37196831580`、`37196799856` success |
| 公司 gateway | HTTP 200 |

## 公司 LLM 實測（PR #24 `525f981`）

| # | 情境 | 結果 |
|---|------|------|
| 1 | 全新環境執行 `doctor`，沒有 store | rc=0；只輸出固定欄位（`persistence_supported=true`、`persist_metadata=false`、各項上限、`readiness=ready`、`credential=not-configured`、`store=absent`），沒有路徑 |
| 2 | 預設 `ask` | rc=0，stdout 是預期的標記，**沒有建立 store** |
| 3 | `ask --save-diagnostics --verbose` | rc=0，`diagnostics_persisted=true`，model=qwen3.6-35b-a3b，約 0.32 秒；`--debug` 印出 4 筆 event |
| 4 | 對無法連線的 provider 執行並儲存 | rc=1，`code=NETWORK_PROVIDER_UNREACHABLE … trace=saved` |
| 5 | store 權限 | 目錄都是 `drwx------`，檔案都是 `-rw-------`（`.store.lock`、`runs/<id>/{summary.json,trace.jsonl,.run.lock}`） |
| 6 | 在 store 裡搜尋 prompt、回答、gateway IP、路徑等標記 | **找不到** |
| 7 | `runs`、`trace`、`trace --errors`、`trace --json` | 輸出正確；trace 的 error data 不含 message；ID 不合法回 rc=1 `ID_INVALID`，找不到回 rc=1 `NOT_FOUND` |
| 8 | 有 store 時執行 `doctor` | `store=present run_count=3` |
| 9 | 使用者 config 設 `persist_metadata: true` | 不加 flag 會儲存；加 `--no-save-diagnostics` 不會新增 run |
| 10 | 專案 `.llmtool.yaml` 設定 `diagnostics` | rc=1，`CONFIG_INVALID`（"project config: forbidden or unsupported field"） |
| 11 | `max_runs=4`，同時執行 3 個儲存 | 全部 rc=0，最後剛好剩 4 個 run |
| 12 | `LLMTOOL_HOME` 是 symlink | 回 `DIAGNOSTICS_STORE_UNAVAILABLE`，`ask` 照常 rc=0，symlink 指向的目錄沒有被寫入；`doctor` 回 rc=1 `STORE_UNAVAILABLE` |
| 13 | `LLMTOOL_HOME` 是相對路徑 | 回 `DIAGNOSTICS_STORE_UNAVAILABLE`，沒有在 cwd 建立目錄 |
| 14 | store 權限是 0755 | 回 `DIAGNOSTICS_STORE_UNSAFE`，`ask` 照常；`doctor` 回 rc=1 `STORE_UNSAFE` |
| 15 | **M1 重現**：模擬 writer 在建立 `.reservation` 後、寫入前當掉（run 目錄裡有 `.run.lock` 和空的 `.reservation`） | 之後每次 `ask --save-diagnostics` 都是 `code=DIAGNOSTICS_STORE_FULL`、`diagnostics_persisted=false`（回答正確、rc=0）。**`doctor` 回 rc=0、`store=present run_count=2`**（P2）；`runs` 把它列成 `recorded_status=incomplete` |
| 16 | **P1 重現**：在一個已完成的 run 裡留下 writer 失敗時可能殘留的 `.summary-<hex>` 暫存檔 | **`doctor` 回 rc=1 `FORMAT_INVALID`**，而且不輸出任何計數；`runs` 則正常跳過，顯示 `damaged=1` |

## PR #24 的問題

### P1（Low，已實測重現）：只要有一個 run 損壞，doctor 就整個失敗

- **位置：** `diagnostics_reader.py` 的 `inspect_store`（約 452–464 行）直接呼叫 `self._run()`，`_run()` 遇到損壞會丟出 `FORMAT_INVALID` 或 `STORE_UNSAFE`。`runs()`（約 412–417 行）會逐一 catch 並計入 `damaged_count`，`inspect_store` 卻沒有。
- **會觸發的情況：** writer 在預期內的失敗會留下這些狀態：
  - `finish` 失敗時留下的 `.summary-<hex>` 暫存檔（`diagnostics_store.py` 約 536–557 行，程式註解說是刻意保留）；
  - 在 link 和 unlink 之間當掉，trace 的 `nlink=2`（約 184–185 行）；
  - 在 `mkdir` 和建立 lock 之間失敗，run 目錄裡沒有 `.run.lock`（約 281–289 行）。
- **影響：** 之後 doctor 一直回 rc=1，也不輸出計數。但這正是最需要 doctor 的時候。實測 #16 已確認。
- **建議：** 像 `runs()` 一樣逐一 catch `ReaderError`，新增 `damaged_count`；只有 root 或 `runs/` 這一層不安全時才直接失敗。

### P2（Low，已實測重現）：儲存已經永久停用，doctor 仍回報正常

- `inspect_store` 只檢查檔案大小。空的或寫一半的 `.reservation`（見 M1）會得到 `store=present`、rc=0，但每次 `--save-diagnostics` 都是 `DIAGNOSTICS_STORE_FULL`（實測 #15）。
- 輸出中已經寫明 "write access unverified"，所以不算錯誤的說法。建議修 P1 時一併新增 `blocked` 或 `damaged` 計數。

### P3（Nit）：憑證環境變數是空字串時，仍顯示為 present

- `config.py` 約 318–322 行對空值回報 `env-configured-present`，但 `ask` 的 `api_key()`（約 355–359 行）會把空值當成 `AUTH_CREDENTIAL_MISSING`。
- 這是刻意的設計（判斷是否為空就必須讀取值），文件也有寫明。可以考慮把標籤改成 `env-name-present`。

### P4（Nit）：`doctor.py` 51–55 行的錯誤分類太廣

- 這段 catch-all 把所有 `OSError`／`ValueError`／`TypeError`／`KeyError` 都歸成 `STORE_UNAVAILABLE`，連 store 以外的失敗也一樣。例如 cwd 已被刪除時，`Path.cwd()` 丟出的 `FileNotFoundError` 也會被歸成 `STORE_UNAVAILABLE`。

### P5（Nit）：README 和 ROADMAP 的文字 merge 後會過時

- 文件寫的是 "on this feature branch … publication/final-head CI/external review pending"，引用的 commit 是 `909d390`，不是 final head。

### PR #24 確認沒問題的部分

- **離線：** 不建立 client、不呼叫 `api_key()`，也沒有使用 socket、`Popen` 或 auth 檔案。`shutil.which` 和 `complete()` 使用相同的絕對路徑規則。
- **憑證：** 只走訪環境變數的 key 來判斷有沒有設定，`os._Environ.__iter__` 不會解碼值。碰撞檢查涵蓋 doctor 會讀的所有環境變數名稱。
- **Windows：** 只有在 `sys.platform == 'win32'` 時才把環境變數名稱轉成大寫。一般的 `resolve_config` 沒有改變。
- **輸出：** `provider_type` 經過 `_validate` 限制在 4 個 enum 值。所有輸出先存在 buffer、完成後才寫到 stdout；錯誤只使用固定的 code。
- **Exit code：** 檢查完成是 0；config 或 store 錯誤是 1；用法錯誤是 2；中斷是 130；broken pipe 是 0。
- **沒有 regression：** `resolve_config` 重構後順序和行為都不變。`ask`／`runs`／`trace` 唯一的改動是多了一個 import。
- **測試能抓到錯誤的實作：**
  - 只允許讀 key、不分大小寫的 Mapping tripwire，並 patch 了 `os._Environ.__getitem__`。
  - socket、`Popen`、`build_opener`、`create_client` 都 patch 成一呼叫就丟例外。
  - 執行前後對整個目錄樹做快照比對。
  - symlink、hard link、權限、owner、race 都有測試。
  - failure matrix：provider 失敗情況 × 4 個 store 故障點，確認 provider 只被呼叫 1 次、exit code 不變、最多一筆有上限的 warning。
- **覆蓋缺口（不算缺陷）：** failure matrix 沒有包含成功路徑和中斷時的 store 故障，但 A11a.4 的測試有涵蓋。

## main 上持久化（A11a.3–A11a.5）的問題

### M1（Medium，已實測重現）：reservation 寫入失敗後，儲存會被永久停用

- **位置：** `diagnostics_store.py` 約 293–303 行。`.reservation` 先用 `O_EXCL` 建立，之後才寫入和 fsync。
- **會觸發的情況：** `_write` 時遇到 ENOSPC／EDQUOT，或在寫入前被 SIGKILL，就會留下空的或寫一半的 `.reservation`。`start()` 在 except（約 311 行）不會回復。之後：
  - `_candidate` 回傳 None，這個 run 永遠不會被清掉（約 337–338、370–371 行）；
  - `usage()` 遇到任何不合法的 reservation 就丟出 `_Full`（約 439–450 行）。
- **影響：** 之後每次儲存都是 `DIAGNOSTICS_STORE_FULL`，就算磁碟空間已經恢復也一樣，直到有人手動刪掉那個 run。實測 #15 已確認。現有的測試 `test_malformed_reservation_and_symlink_trace_kept` 鎖定的是 fail-closed 行為，沒有涵蓋這種 writer 自己造成的情況。
- **建議：**
  - `start()` 在 `mkdir` 之後失敗時，趁還持有 store lock 和 run lock，只 unlink 自己建立的檔案，再 `rmdir`。
  - 或者：超過 grace period 後，如果一個 run 沒有被 lock，而且裡面只有已知名稱的私有檔案，即使 reservation 無法解析，也當成 abandoned run 清掉。

### M2（Low）：其他 writer 失敗會佔用容量，而且永遠不會被回收

- **trace 最後一行寫一半：** 短寫之後遇到 ENOSPC，trace 結尾沒有換行（約 147–153 行）。`_events` 會拒絕這種 trace（約 203–204 行），所以這個 run 永遠不會被清掉，`usage()` 卻一直按完整的 `max_run_bytes` 計算（約 439–446 行）。reader 已經能容忍最後一行被截斷（`diagnostics_reader.py` 約 297–304 行），cleanup 也應該容忍。
- **永遠不會被清掉的殘留：** `.summary-*` 暫存檔、在 link 和 unlink 之間當掉、空的 run 目錄。這些都會一直佔用 `max_runs` 或容量。
- **影響：** 累積約 64 次（預設容量）或 100 次（`max_runs`）這類事件後，儲存就會被永久停用。
- **建議：** `start()` 失敗時回復；cleanup 接受最後一行被截斷的 trace 和 `.summary-<32hex>` 檔名；link 成功但 unlink 失敗時，unlink 原始檔案。

### M3（Medium；程式碼已確認，是否會觸發取決於 RLIMIT_NOFILE）：cleanup 可能用完 file descriptor

- **位置：** `_capacity` 為每個候選 run 保留 2 個 fd，直到結束才釋放（約 367–369、404–406、467–470 行）。`usage()` 和 `_bytes` 的遞迴還會再開更多 fd。
- **會觸發的情況：** macOS 預設的 soft limit 常常只有 256。`max_runs` 最大允許 1000，設定超過約 120 時：
  - `_candidate` 遇到 EMFILE，該 run 被默默跳過（約 370 行）；
  - 接著 `usage()` 的 `_directory` 遇到 EMFILE，丟出 `_Full`（約 448–450 行）；
  - cleanup 永遠無法完成，儲存一直停用。
- **建議：** 不要為所有候選 run 保留 fd，只記錄 `(name, st_dev, st_ino)`，要刪除時再重新 open、lock、驗證。或者依 `getrlimit` 限制實際生效的 `max_runs`。

### M4（Low）：每次儲存都在 root lock 下重新讀取所有已完成的 trace

- **位置：** 約 343–344 行。每次儲存 `ask` 都會在持有 root 的 EX lock 時，重新讀取並解析所有已完成的 trace。
- **影響：** 成本隨 store 的總大小增加。在可設定的上限內，可能讓其他 writer 或 reader 超過 2 秒的 lock 等待，出現誤報的 `STORE_UNAVAILABLE`。
- **說明：** 實際的 trace 很小（最多 32 筆 event），影響不大。可以快取「已驗證」標記，或只在要淘汰時才驗證。

### M5（Nit）：reader 的掃描上限低於 store 能容納的數量

- reader 的 `RUN_SCAN_LIMIT` 是 1000（`diagnostics_reader.py` 第 17 行）。設定 `max_runs=1000` 再加上任何未知的項目，`runs` 和 `doctor` 就會回 `LIMIT_EXCEEDED`；writer 自己的掃描上限則是 10000。

### 持久化確認沒問題的部分

- **opt-in：** 預設不儲存。`persist_metadata` 為 false 時，`start()` 在任何 I/O、讀取環境變數或查找 home 之前就返回（約 260–261 行）。
- **設定與 `LLMTOOL_HOME`：** 設定只能來自使用者 config，專案 config 的欄位白名單會拒絕 `diagnostics`，也沒有可以開啟儲存的環境變數。`LLMTOOL_HOME` 只能來自環境變數，必須是絕對路徑，不能有控制字元或 `..`。XDG 的 fallback 正確。
- **會被儲存的內容：** 只有白名單內的純量值：
  - `run.start`：command 和 mode；
  - `provider.request`／`provider.response`：provider type、profile、驗證過的 model、時間和 token 數；
  - error：code、flag、只允許封閉分類的 `safe_cause`，以及 message。`ask` 路徑上每個 raise 的位置，message 都是固定的應用程式字串，而且仍會檢查憑證格式；
  - summary 和 reservation。
- **不會被儲存的內容：** prompt、回答、body、stderr、路徑和憑證。測試會檢查私有標記；實測 #6 也確認了。
- **檔案系統安全：**
  - 目錄建立為 0700、檔案為 0600；會檢查既有的權限、owner 和 `nlink == 1`，從不 chmod。
  - 每一層路徑都用 directory-relative fd 加上 `O_NOFOLLOW` 走訪。
  - 用 no-clobber 的 link 發布檔案，並 fsync 檔案和目錄。
- **lock 與生命週期：**
  - root 和 run 的 lock 都用 `flock`，等待時間有上限。
  - reader 使用 shared lock，遇到忙碌的 run 會跳過。
  - cleanup 不會刪除執行中的 run、未知的資料，或仍在 1 小時 grace period 內的 abandoned run。
  - 已完成 run 的 reservation 對帳是正確的。
- **reader 的防護：** 讀取前先檢查大小；拒絕重複的 key、NaN 和不支援的版本；每次讀取後重新檢查 snapshot 和 identity；從不輸出自由文字。
- **store 失敗的規則：** `ask` 裡每個 store 呼叫都包在 `observe` 和 try/except 裡。stdout、exit code 和唯一一次 provider 呼叫都不會被改變；最多只印一筆有上限的 warning，而且不包含外部例外的文字。實測 #12–#15 也確認了這點。

## 建議

1. **PR #24：** 建議 merge 前修 P1（doctor 逐一 catch 損壞的 run，並新增 `damaged_count`），順便更新 P5 的文件。P2–P4 可以放到 A11a.7。
2. **持久化：** 開一個 A11a.x 的 robustness follow-up，優先處理 M1（`start()` 失敗時回復，或清理無法解析的 abandoned run），再處理 M2、M3。每一項都要補上「writer 自己造成的殘留之後，儲存能自動恢復」的測試。
3. M4、M5 記為 follow-up。

## 清理

- 沒有建立任何 worktree、clone 或專案副本。canonical repo 除了新增這份報告之外沒有其他變更；review 結束後已切回 `feat/a11a3-secure-diagnostics-store-20261003`。
- 實測只使用 scratchpad 裡的暫存 `HOME`／`LLMTOOL_HOME`，沒有讀取或寫入使用者真正的 llmtool 設定或 store，也沒有使用任何 API key。
