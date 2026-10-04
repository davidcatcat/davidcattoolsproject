# llmtool A11a.7 final acceptance review，加上公司 LLM 驗收實測（2026-10-05）

## 範圍與方法

- **repo 最新狀態：**
  - main `6b7809a`：PR #24 已 merge，包含 A11a.6 offline doctor 與 M1／M3 hardening。
  - 開著的 PR #25 `feat/a11a7-final-acceptance-20261004`，HEAD `7e9194b`，只有 1 個 commit。
- **PR #25 的內容：** 只改文件。
  - `README.md`、`tools/llmtool/README.md`、`tools/llmtool/ROADMAP.md` 的狀態與已知限制。
  - 新增 [A11a.7 final validation report](llmtool_a11a7_final_validation_20261004.md)。
  - 已確認 `tools/llmtool/llmtool/` 與 `tests/` 和 main 沒有差異；main 和 `cc63277`（上一輪 re-review 的 commit）之間也沒有差異。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree、clone 或專案副本。canonical repo 當時已經 checkout 在 PR #25 的分支。
  - 逐行閱讀文件 diff 與 validation report。
  - 檢查報告引用的測試是否存在、本機連結是否正確，並執行 `git diff --check`。
  - 在本機執行完整測試。
  - **用公司 gateway 做驗收實測：** `gw` = qwen3.6-35b-a3b，`enable_thinking: false`，不需要 key。A11a.7 report 把這項列為 NOT-TESTED，這次補上。
  - 實測用 `mktemp -d` 建立獨立的 `HOME`／`LLMTOOL_HOME`，用 `env -i` 執行，沒有碰使用者真正的設定和 store，也沒有使用 API key。

## 結論：APPROVE

- PR #25 的文件和程式碼、測試、已延後項目的實際狀態一致。我沒有發現錯誤的說法，也沒有把延後項目寫成已修好。
- 公司 LLM 的端對端驗收全部通過。A11a.7 report 列為 NOT-TESTED 的「company VPN/gateway inference」，可以用這份報告補上。
- 只有 3 個 nit，不影響 merge。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 867 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`） | 867 tests OK（1 skipped） |
| GitHub Actions（`7e9194b`） | push `37215669801`、PR `37215700424`，都是 success（已用 `gh run list` 確認是 `7e9194b`） |
| report 引用的測試 | 用 AST 解析，共 182 次引用（142 個不重複），**全部存在** |
| 4 份文件的本機 Markdown 連結 | 沒有壞掉的連結 |
| `git diff --check origin/main HEAD` | 通過 |
| runtime／測試和 main 的差異 | 沒有 |

## 文件審查

| 檢查項目 | 結果 |
|---------|------|
| 狀態描述 | A11a.1–6 已 merge；A11a.7 進行中；**A11a 整體 NOT DONE**；A11b／A11c NOT STARTED。和 PR 狀態一致，沒有提前宣稱完成 |
| 延後項目 | M2、M4、M5、P1–P4、N1、修正前就存在的損壞 reservation、原生 rename 的平台限制、grace period、`runs` 沒有 `--json`，全部列在 README、ROADMAP 和 report。內容和我前兩輪的 review 一致 |
| P5 | 只更新了狀態描述，沒有宣稱修好 doctor 的行為，正確 |
| Debug 的說明 | 新增的段落寫明 `ask --debug` 只輸出有上限的 event 摘要，不收集 traceback、路徑、locals。實測和這點一致（見下方 #3、#11） |
| §18 coverage matrix | 每一組需求都對應到實際存在的測試；也寫明這只是原始碼層面的對照，不是另外執行的證據，說法誠實 |

### Nit

| # | 問題 | 建議 |
|---|------|------|
| D1 | report 開頭的 Status 寫的是 "in progress"、"PENDING"，merge 後會過時。這和 P5 是同一種模式，PR 描述中有說明是刻意不再為此另外 commit | merge 時在 ROADMAP 加一行 post-merge 註記即可 |
| D2 | 證據檔案（log、manifest、smoke 結果）都放在 `/private/tmp/...`，重開機後就會消失；report 只保留了 SHA-256 | 可以接受（hash 能驗證，但無法重新取得原檔）。如果之後需要稽核，建議把小型的 `result.json`、`commands.json` 放進 `reports/` 的子目錄 |
| D3 | ROADMAP 在「Diagnostics modes:」和它的清單之間插入了一段英文說明，讓清單的標題和內容被隔開 | 把這段移到清單後面，或改成清單項目的註解 |

## 公司 LLM 驗收實測（PR #25 `7e9194b`，runtime 與 main `6b7809a` 相同）

設定：使用者 config 只有 `company` 和 `unreachable`（`127.0.0.1:9`）兩個 profile，**沒有** `diagnostics` 設定（也就是預設不儲存）。

| # | 情境 | 結果 |
|---|------|------|
| 1 | 空環境執行 `doctor`、`runs`、`trace <合法但不存在的 ID>`、`trace ../etc` | `doctor` rc=0、`persist_metadata=false`；`runs` rc=0，顯示 "No saved diagnostics runs."；trace 分別回 rc=1 `NOT_FOUND` 和 `ID_INVALID`。**沒有建立 store** |
| 2 | `runs --json`、`doctor --json`、同時使用 `--save-diagnostics --no-save-diagnostics` | 都是 rc=2 usage 錯誤，沒有建立 store |
| 3 | 預設 `ask`：normal、verbose、debug | 都是 rc=0，stdout 正好是要求的 token。normal 的 stderr 是空的；verbose 和 debug 顯示 model=qwen3.6-35b-a3b、token 數、`run_id`、`diagnostics_persisted=false`；debug 另外印出 4 筆 event。**沒有建立 store** |
| 4 | 同時給 `--file` 和位置參數的文字（我的測試寫法有誤） | rc=1 `USER_INPUT_ERROR`，沒有呼叫 provider。開啟儲存時，trace 是 `run.start,error,run.end`，`provider.request` 為 0 次，`provider_type=unknown`，正確 |
| 5 | 開啟儲存的 `ask`：normal、verbose、`--debug --file` | 都是 rc=0，stdout 和不儲存時相同；verbose 和 debug 顯示 `diagnostics_persisted=true` |
| 6 | 用 gateway 不存在的 model（`--model a7-nonexistent-model-zz`） | rc=1，`gateway HTTP 404; response body omitted`，`code=PROVIDER_HTTP_ERROR … trace=saved`。**gateway 的錯誤內容沒有出現在 stderr，也沒有出現在 store** |
| 7 | 無法連線的 provider | rc=1 `NETWORK_PROVIDER_UNREACHABLE … trace=saved` |
| 8 | `--max-tokens 1` | rc=1 `MODEL_OUTPUT_INCOMPLETE`（finish_reason=length），`recorded_status=incomplete` |
| 9 | `runs` | 列出 7 個 run，依時間由新到舊，`recorded_status` 和 `final_error_code` 都正確 |
| 10 | 對每個 run 執行 `trace --json` | 全部 `state=complete`。每個有呼叫 provider 的 run 都**剛好 1 次 `provider.request`**，沒有 retry 或 fallback 的 event。status 分別是 success、error、incomplete，error code 和上面一致 |
| 11 | human 格式的 `trace` | event 的 data 只有 `command`、`mode`、`provider_type`、token 數和時間；不含 profile 名稱、model 名稱以外的內容、prompt 或回答 |
| 12 | 在 store 裡搜尋 | prompt 和回答的標記（`A7_*`）、輸入檔名與檔案內容的標記、gateway IP、port、錯誤的 model 名稱、`Authorization`、`Bearer`、暫存路徑、`home/.config`：**全部找不到** |
| 13 | store 權限 | 8 個目錄都是 `drwx------`，19 個檔案都是 `-rw-------` |
| 14 | 有 store 時執行 `doctor` | rc=0，`store=present run_count=7 active_count=0 unknown_count=0` |
| 15 | store 權限改成 0755 後開啟儲存 | `ask` rc=0，stdout 正確，只有一筆 `DIAGNOSTICS_STORE_UNSAFE` warning；`doctor` rc=1 `STORE_UNSAFE`；store 仍然是 `drwxr-xr-x`，**沒有被 chmod** |

### 觀察（不算問題）

- 儲存下來的 `provider.request` event 只有 `provider_type`，`provider.response` 也沒有 model 名稱；ephemeral 的 verbose 輸出則有 model。這比 spec 允許的範圍更保守，在隱私上是加分。如果日後需要依 model 分析錯誤，要另外設計。
- 404 對應到 `PROVIDER_HTTP_ERROR`，而不是更細的分類。目前的 error code 清單本來就不細分 404，符合 spec。

## 建議

1. **PR #25 可以 merge。** D1–D3 可以在 merge 時順手處理，或忽略。
2. merge 後可以在 ROADMAP 引用這份報告，作為「company gateway inference」的實測證據。
3. 進入 A11b 之前，先決定 P1（doctor 遇到一個損壞的 run 就整個失敗）和 N1／M2（殘留檔案的回收）是否要在 A11a 之後另開一個 hardening slice。目前都已經清楚記錄為延後項目。

## 清理

- 沒有建立任何 worktree、clone 或專案副本。canonical repo 只新增了這份報告。review 前後都在 PR #25 的分支上，因為 review 開始時 canonical repo 就已經在這個分支。
- 實測只使用 scratchpad 裡的暫存 `HOME`／`LLMTOOL_HOME`，沒有讀取或寫入使用者真正的 llmtool 設定或 store，也沒有使用任何 API key。
