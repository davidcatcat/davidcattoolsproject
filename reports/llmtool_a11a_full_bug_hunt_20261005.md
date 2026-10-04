# llmtool A11a 全範圍 Bug Hunt（2026-10-05）

## 結論摘要

**CONFIRMED BUG COUNT: 7**

- 沒有 blocker。7 個都是 Low 或 Low–Medium。
- 主要的 primary task 契約都成立：
  - `ask` 的 stdout 不受影響；
  - exit code 0／1／2／130 正確；
  - provider 只被呼叫一次；
  - 不會 retry 或 fallback；
  - diagnostics 失敗不會讓 `ask` 失敗；
  - 預設不儲存；
  - 沒有隱私外洩。
- 7 個 bug 集中在四類：
  - broken pipe；
  - store 自己的清理中斷後留下無法回收的殘留；
  - verbose metadata 被整批丟掉；
  - 狀態或錯誤分類的標籤不正確。

| ID | 類別 | 嚴重度 | 一句話 |
|----|------|--------|--------|
| B1 | CONFIRMED | Low–Medium | stdout 是已關閉的 pipe、而且輸出小於 buffer 時，exit 是 120 並印出 Python 訊息，不是 0；儲存的 run 記成 `success`，不是 `output_closed` |
| B2 | CONFIRMED | Low–Medium | eviction（`_remove`）中途失敗或被 SIGKILL，留下半刪除、永遠無法回收的 run；`max_runs=1` 時儲存永久 FULL |
| B3 | CONFIRMED（regression） | Low | `ask --verbose` 只要 model 欄位驗證失敗，就連 elapsed 和 token 數一起丟掉 |
| B4 | CONFIRMED | Low | 使用 `--save-diagnostics`、但使用者的 diagnostics 設定不合法時，回報 `trace=not-saved`，而不是 `unavailable` |
| B5 | CONFIRMED | Low | symlink 的 lock 或目錄被回報成 `STORE_UNAVAILABLE`／`DIAGNOSTICS_STORE_UNAVAILABLE`，而不是 UNSAFE；symlink 的檔案則是 UNSAFE |
| B6 | CONFIRMED | Low | `runs` 對沒有 summary 的 run 顯示 `final_error_code=none`，即使 trace 裡有 error |
| B7 | CONFIRMED（只能用 fault injection 觸發） | Low | 已經 durable commit 之後，如果 `close()` 失敗，會把已儲存的 run 回報成 `diagnostics_persisted=false`、`trace=unavailable` |

## 1. 前置確認

| 項目 | 值 |
|------|-----|
| canonical repo | `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`（`git rev-parse --show-toplevel` 確認；`git worktree list` 只有這一個） |
| fetch 後的 `origin/main` | **`e3869828b0435ad79fe54c22701e914bc8a29cb0`**（PR #25 的 merge commit，MERGED 2026-10-04T16:34:18Z） |
| 目前分支／local HEAD | `feat/a11a7-final-acceptance-20261004` / `d782d6c` |
| ahead／behind（HEAD…origin/main） | 0／1（只差 merge commit） |
| tree | HEAD 與 origin/main 的 tree 都是 `f4aa2aac65824f08a4f3669bc806a3b0275d8fb5`，**完全相同**，所以直接在目前的 working tree review，沒有切換分支 |
| working tree | 開始時只有使用者原本就有的未追蹤檔案（`reports/llm_benchmark_*`、`reports/llm_reasoning_probe_*`），全部保留 |

## 2. 範圍與方法

### 檢查過的原始碼

- `tools/llmtool/llmtool/` 底下：
  - diagnostics：`diagnostics.py`、`diagnostics_runtime.py`、`diagnostics_settings.py`、`diagnostics_store.py`、`diagnostics_reader.py`、`diagnostics_cli.py`、`diagnostics_summary.py`
  - 其他：`doctor.py`、`errors.py`、`cli.py`、`config.py`、`configure.py`、`inputs.py`
  - provider adapter：`providers/`（openai-compatible、ollama、claude CLI、codex CLI）
- 和 A11a 之前的 main `624c33c` 比對 `git diff`，確認 A11a 改了哪些既有程式碼。
- 對照的契約：`tools/llmtool/README.md` 的 diagnostics、doctor 和已知限制章節，以及 `reports/llmtool_a11a_implementation_spec_20261002.md`。

### 執行方式

- **baseline（只是起點，不是 bug hunt 本身）：**

  | Python | 結果 |
  |--------|------|
  | 3.13.3（`tools/llmtool/.venv/bin/python`） | 867 tests OK（1 skipped） |
  | 3.9.6（`/usr/bin/python3`） | 867 tests OK（1 skipped） |

  兩次都設定 `PYTHONDONTWRITEBYTECODE=1`。
- **bug hunt：** 分成三個方向平行進行，每個候選問題我都再親自重跑 probe 確認。
  1. store writer：初始化、發布、rollback、lock、清理與容量、crash。
  2. reader 與 `runs`／`trace`／`doctor`。
  3. `ask` 生命週期、錯誤分類、provider adapter，以及對 A0–A10 的 regression。
- **probe 的位置與執行環境：**
  - 都放在 session scratchpad：`/private/tmp/claude-501/-Users-davidsun-git-davidcataiporject-davidcattoolsproject/88ee90aa-8e62-4eeb-a67b-e373aeba2609/scratchpad/bughunt/`（下文簡稱 `$B`），每個 probe 各用一個 `mktemp -d`。
  - 用 `env -i` 執行，`HOME` 和絕對路徑的 `LLMTOOL_HOME` 都是獨立的。
  - provider 一律用替身：127.0.0.1 上會計算 request 數的 mock HTTP server、絕對路徑的假 `claude`／`codex` 執行檔、monkeypatch 和 fault injection（EIO、SIGKILL、`os._exit`）。
- **network／provider：** **沒有使用公司 LLM、外部 provider，也沒有使用 Claude 或 Codex 的真實 session**；只連 127.0.0.1。沒有使用任何 credential。

## 3. CONFIRMED BUG

### B1（Low–Medium，信心 High）：buffered 的 broken pipe 回傳 exit 120，儲存的 run 狀態錯誤

- **位置：**
  - `cli.py` `_run_ask` 的成功路徑（約 228–232 行：先 `sys.stdout.write`，再 `observe('finish','success')`，最後 `return 0`）；
  - `cli.py` `main`（約 364–367 行）；
  - `diagnostics_cli.py` `run_diagnostics`（64–70 行）；
  - `doctor.py` `run_doctor`（46–52 行）。
- **觸發：** stdout 是讀取端已經關閉的 pipe，而且輸出小於 stdout buffer（約 8 KiB）。`write` 只寫進 buffer、不會丟出例外，`BrokenPipeError` 要到 interpreter 結束、flush 時才發生，那時 `except BrokenPipeError` 早就不在作用範圍內。
- **預期：**
  - spec §12：「Broken pipe remains exit 0 with no new noisy traceback. If a persisted run exists, its status is `output_closed`.」
  - README 寫明 runs、trace、doctor 的 "broken pipe 0" 不變。
- **實際：**
  - exit 120，stderr 出現 `Exception ignored on flushing sys.stdout: BrokenPipeError`（3.9 是 `Exception ignored in: <_io.TextIOWrapper …>`）。
  - `ask --save-diagnostics` 的 `summary.json` 是 `"status":"success"`，verbose 還印出 `diagnostics_persisted=true`，但輸出其實沒有送達。
  - `runs`、`trace`、`trace --json`、`doctor` 在 3.13 和 3.9 上都是 exit 120。
  - 輸出大於 buffer（例如 200 KB）時，會走到既有的處理：exit 0、`output_closed`，正確。
- **重現：**
  - `PYTHONPATH=$PWD:$B/harness.bTjJ .venv/bin/python $B/brokenpipe.xSfU/p.py`：建立 `os.pipe()`、關掉讀取端，把寫入端當成 stdout。
  - `bash $B/pipe.BVQl/run.sh`：runs、trace、doctor，兩個 Python 版本。
- **在 origin/main 重現：** 是。
- **是否為 regression：**
  - exit 120 在 A11a 之前就存在（`analyze-log`、`skills`、`git` 都一樣）。
  - **把未送達的輸出記成 `success`／`persisted=true`，是 A11a 新增的錯誤。**
  - 現有測試只 mock `write` 丟出例外，沒有用真實的 pipe。
- **和 deferred 重複：** 否。

### B2（Low–Medium，信心 High）：eviction 中途失敗會留下永遠無法回收的 run

- **位置：** `diagnostics_store.py` 的 `DiagnosticsStore._remove`（510–522 行），從 `_capacity` 的 `remove()` 呼叫（約 551–609 行）。
- **觸發：**
  - `_remove` 用 `set` 的順序逐一 unlink run 裡的檔案，這個順序會隨 `PYTHONHASHSEED` 改變。
  - 在第一次 unlink 之後，只要遇到 OSError（例如 EIO）或 SIGKILL，就會留下例如 {`summary.json`,`trace.jsonl`}、{`.run.lock`,`trace.jsonl`} 或 {`.run.lock`,`summary.json`} 這樣的目錄。
- **實際：**
  - 這個目錄之後一定會被 `_candidate` 拒絕（缺 `.run.lock`，或沒有 summary 也沒有 reservation），所以**永遠不會再被清掉**，但 `usage()` 仍然把它算進 `max_runs` 和容量。
  - `max_runs=1` 時，之後每次 start 都是 `DIAGNOSTICS_STORE_FULL`（永久停用）。
  - `max_runs=2` 時會一直佔掉一個名額，reader 把它顯示成 `damaged=1` 或 `incomplete`。原本已經成功完成的 run，被顯示成損壞或未完成。
  - 如果是 process 內的失敗，那次 start 本身也會失敗（`DIAGNOSTICS_STORE_UNAVAILABLE`）。
- **預期：** spec §10 的 cleanup 只淘汰合格的 run；store 自己的清理動作不應該讓容量永久外洩、或讓儲存永久停用。
- **重現：**
  - `PYTHONHASHSEED=1|3 .venv/bin/python $B/probeA.EsoB/probe_evict_partial.py $(mktemp -d …)`：在第 2 次 unlink 注入 EIO。
  - `/usr/bin/python3 $B/probeA.EsoB/probe_evict_sigkill.py $(mktemp -d …)`：在第 2 次 unlink 時用真的 SIGKILL 殺掉 child。
  - 我自己重跑的輸出：seed 1 的殘留是 `['summary.json','trace.jsonl']`，seed 3 是 `['.run.lock','trace.jsonl']`；SIGKILL 的 child rc 是 -9，殘留是 `['.run.lock','summary.json']`；之後每次 start 都是 `DIAGNOSTICS_STORE_FULL`。
- **在 origin/main 重現：** 是（3.9 和 3.13 都是）。
- **是否為 regression：** 否，從 A11a.3 就存在。
- **和 deferred 的關係：** 和 M2（writer 失敗留下的殘留）是同一類，但**不是同一個路徑**：M2 只涵蓋 writer 的 partial trace、`.summary-*`、link 和 unlink 之間、空的 run 目錄；這裡是 store 自己清理到一半，而且會把完成的 run 變成損壞。

### B3（Low，信心 High，**regression**）：verbose 只要有一個欄位不合格，就丟掉所有 provider metadata

- **位置：** `diagnostics_runtime.py` 的 `RunContext.response()`（187 行起）。所有欄位都在同一個 `try` 裡驗證，任何一個欄位丟出例外，`self._metadata` 就維持 `{}`。
- **觸發：**
  - model 名稱含有 provider 允許、但 diagnostics 不允許的字元，例如 claude-cli 的 `claude-sonnet-é`（provider 用 `isalnum()` 檢查）；
  - 或 model 名稱被憑證 regex 誤判，例如 openai-compatible gateway 回傳的 `my-token:latest`。
- **預期：**
  - spec §6：「Existing `--verbose` stays supported and keeps current provider metadata.」
  - spec §13：「drop/redact the field」。
  - README：「`--verbose` keeps validated provider model/token/elapsed keys … Invalid/credential-looking metadata is omitted」。
- **實際：**
  - `{'elapsed_seconds':0.1,'model':'my-token:latest','prompt_tokens':1,'total_tokens':3}` 的結果是 `{}`，dropped=1。
  - `claude-sonnet-é` 也一樣。
  - CLI 的 `ask --verbose` 只印出 `run_id`、`provider_type` 和 `diagnostics_persisted`，elapsed 和 token 數都消失。
  - 同樣的設定下，`review-diff --verbose`（沿用舊程式碼）仍然會印出 elapsed 和 model。
  - 對照組：`qwen3.6-35b-a3b`、`llama3:8b-instruct-q4` 正常保留。
- **重現：** `PATH=$B/f1_verbose_drop.n1Oz/bin:$PATH .venv/bin/python $B/f1_verbose_drop.n1Oz/direct.py`，以及我自己用 `RunContext().response({...})` 對 4 個 model 名稱做的直接測試。
- **在 origin/main 重現：** 是。
- **是否為 regression：** **是**。A11a 之前的 `ask --verbose` 會原樣印出 provider metadata。
- **和 deferred 重複：** 否。

### B4（Low，信心 High）：明確要求儲存、但設定不合法時，回報 `trace=not-saved`

- **位置：** `cli.py` 的 `setup_persistence()`（160–174 行）。`primary_error=False` 時直接 re-raise，不會設定 `requested_without_observer`，所以 `failure()` 的 fallback 印出 `not-saved`。
- **觸發：** 使用者 config 設定 `diagnostics: {max_runs: 0}`，再執行 `ask hi --save-diagnostics`。
- **預期：** README：「Failure metadata reports … `unavailable` after requested persistence fails, and `not-saved` when persistence is off」。這裡明確要求了儲存。
- **實際：** `code=CONFIG_INVALID … trace=not-saved`，verbose 顯示 `diagnostics_persisted=false`。同樣的設定，如果先遇到 skill 錯誤（`--skill nope --save-diagnostics`），則回報 `trace=unavailable` 加上 `DIAGNOSTICS_STORE_UNAVAILABLE`，兩條路徑的結果不一致。exit 1 和 0 次 provider request 都正確。
- **重現：** `PYTHONPATH=$PWD:$B/harness.bTjJ .venv/bin/python $B/badsettings.IMhU/p.py`。
- **在 origin/main 重現：** 是。**regression：** 否（A11a.4 的新程式碼）。**和 deferred 重複：** 否。

### B5（Low，信心 High）：symlink 的錯誤分類不一致

- **位置：**
  - reader：`diagnostics_reader.py` 的 `_directory`（104 行起）和 `_lock`（145 行起）用 O_NOFOLLOW 開啟時得到 ELOOP 或 ENOTDIR，但只有 `FileNotFoundError` 被轉成 UNSAFE；`trace`、`inspect_store` 把其他 OSError 都歸成 `STORE_UNAVAILABLE`。
  - writer：`diagnostics_store.py` 的 `_warning`（52–58 行）把 ELOOP 歸成 `DIAGNOSTICS_STORE_UNAVAILABLE`。
- **實際（每種情況各用一個乾淨的 fixture）：**

  | symlink 的位置 | `trace`／`doctor` 的結果 |
  |---------------|-------------------------|
  | `summary.json`、`trace.jsonl` | `STORE_UNSAFE` |
  | `.run.lock`、run 目錄、`runs/`、`.store.lock` | `STORE_UNAVAILABLE` |
  | 檔名像 run ID 的一般檔案 | `STORE_UNAVAILABLE` |
  | `ask` 的 `LLMTOOL_HOME` 是 symlink | `DIAGNOSTICS_STORE_UNAVAILABLE` |

- **預期：** README 把 symlink 列為會被拒絕的不安全輸入，並說明「Specific store codes distinguish unsafe state …」。UNAVAILABLE 保留給非預期的 I/O 失敗，會讓使用者以為重試就好，而不是去檢查被竄改或不安全的 store。
- **影響：** 只是分類錯誤，**拒絕的行為本身正確**（不會跟隨 symlink，也不會寫入或讀取 symlink 指向的目標）。
- **重現：** `bash $B/symlinkcode2.pRV0/probe.sh`（我已重跑，輸出如上表）；`ask` 的部分是 `$B/llmhome.lLG7/p.py`。
- **在 origin/main 重現：** 是。**regression：** 否。
- **和 deferred 的關係：** 和 P4 相近，但 P4 只涵蓋 doctor 的 catch-all；這裡也影響 `trace` 和 `ask` 的 writer warning。

### B6（Low，信心 High）：`runs` 對未完成的 run 顯示 `final_error_code=none`

- **位置：** `diagnostics_cli.py` 26 行的 `(row['final_error_code'] or 'none')`；row 來自 `diagnostics_reader.py` 的 `_incomplete`（239 行）。
- **觸發：** 沒有 summary 的 run，trace 裡已經有 error event。例如真的 writer 寫完 `run.start` 和 `error PROVIDER_TIMEOUT` 之後，process 被 `os._exit` 結束。
- **實際：**
  - `runs` 顯示 `recorded_status=incomplete started_at=unknown provider_type=unknown final_error_code=none`。
  - 同一個 run 的 `trace --errors` 顯示 `"code":"PROVIDER_TIMEOUT"`。
  - 對已完成的 run，`none` 的意思是「沒有錯誤」；對未完成的 run 用同一個字，會誤導使用者。
- **預期：** 和同一列的其他欄位一致，顯示 `unknown`。README 對沒有 summary 的 row 寫的是 "unknown" times。
- **重現：** `.venv/bin/python $B/incomplete_fec.kDkC/abandon.py <home>/state`，再執行 `runs` 和 `trace cccc… --errors`（我已重跑）。
- **在 origin/main 重現：** 是。**regression：** 否。
- **和 deferred 的關係：** 接近「no-summary row 的時間為 null」，但那一項沒有涵蓋這個欄位。

### B7（Low，信心 High，只能用 fault injection 觸發）：commit 之後 `close()` 失敗，把已儲存的 run 回報成沒有儲存

- **位置：**
  - `diagnostics_store.py` 的 `StoredRun.finish`（約 676–699 行，`finally: self.close()`）和 `StoredRun.close`（705–710 行，失敗時設定 `self.warning`）；
  - `diagnostics_runtime.py` 的 `RunContext.finish`（約 281–285 行），要求 `writer.warning is None`。
- **觸發：** `summary.json` durable 發布之後，關閉 fd 時發生 EIO 或 EINTR（network filesystem 上比較可能發生）。
- **預期：** README（約 723–725 行）：「this durable publication commits persistence success … a root-lock timeout or cleanup failure does not report the committed trace as unsaved」。
- **實際：** `DIAGNOSTICS_WRITE_FAILED`、`diagnostics_persisted=false`、`trace=unavailable`；但磁碟上已經有完整的 `.run.lock`、`summary.json`、`trace.jsonl`，reader 也顯示 `success`。
- **重現：** `.venv/bin/python $B/probeB.Ly0f/probe_close_after_commit.py $(mktemp -d …)`（我已重跑）。
- **在 origin/main 重現：** 是（用 fault injection）。**regression：** 否。**和 deferred 重複：** 否。

## 4. LIKELY BUG（還缺證據）

| ID | 內容 | 還缺什麼 |
|----|------|---------|
| L1 | writer 在 `finish()` 中途 crash 留下的狀態被 reader 回報成 damaged 或 unsafe，而不是 incomplete。三種狀態：(1) link 和 unlink 之間，`trace.jsonl` 和 `trace.partial.jsonl` 同時存在，nlink=2；(2) 已經寫出 `.summary-<hex>`、但還沒發布；(3) summary 的 link 和 unlink 之間。結果是 `runs` 顯示 `damaged=3`，`trace` 回報 `STORE_UNSAFE` 或 `FORMAT_INVALID`。spec §9 寫的是「A crash may leave `trace.partial.jsonl` and no summary. Readers label this `incomplete`」。重現：`$B/crash.p27j/crash.py <home> <rid> 1\|2\|3`。 | 需要作者決定：writer 自己的 crash 殘留算「損壞」還是「未完成」。README 也同時寫了拒絕 hard link。doctor 的部分是 P1，容量的部分是 M2 |
| L2 | wall clock 在 `ask` 執行期間往回調（NTP 或手動），會讓 `RunSummary` 拒絕 `ended_at < started_at`，結果是 `WRITE_FAILED`、`persisted=false`，run 以 incomplete 的狀態佔住一個 `max_run_bytes` 的 reservation，直到 1 小時的 grace period 結束。重現：`$B/probeC.BQ42/probe_clock_step_back.py`。 | spec 沒有定義時鐘回撥的情況。目前的行為是 fail-closed，並如實回報 |

## 5. THEORETICAL RISK

- **Linux 的 `renameat2`：** glibc 低於 2.28，或檔案系統回傳 EINVAL 時，儲存會被停用。這是文件已經寫明的原生發布平台限制。我只在 macOS 上執行，Linux 分支只讀了原始碼，並依賴 CI。
- **rename 的 EEXIST：** 如果是由沒有持有 lock 的外部 process 造成，會被回報成 UNAVAILABLE，而不是 RUN_COLLISION。只是分類問題。
- **run 目錄在讀取途中被同一個 UID 的外部 process 移走：** `trace` 回報 `NOT_FOUND`；如果是被替換，則回報 UNAVAILABLE。不會輸出過時的資料，屬於 fail-safe（`$B/vanish.fnWH/vanish.py`）。

## 6. 檢查過、判定不是 bug（包含已知限制）

- **已 documented 的限制，這輪重新確認後符合文件描述，沒有更嚴重：**
  - M2、M4、M5；
  - doctor 的 P1–P4（P1：一個損壞的 run 就讓 doctor 整個失敗；N3：3.9 在沒有 PyYAML 時 doctor 回報 `CONFIG_INVALID`，屬於 P4）；
  - N1（`.run-init-*` 不會被清理、doctor 也看不到）；
  - 修正前就存在的損壞 reservation 會 fail-closed；
  - 1 小時的 grace period、SIGKILL 不等於斷電、同一個 UID 的 namespace 限制；
  - `runs` 沒有 `--json`、no-summary row 的時間為 null。
- **`ask` 的錯誤分類與 exit code（3.13 和 3.9，每種情況都只有 1 次 request）：**

  | 情況 | 結果 |
  |------|------|
  | HTTP 401、403 | `AUTH_PROVIDER_REJECTED` |
  | HTTP 404、500、503 | `PROVIDER_HTTP_ERROR` |
  | HTTP 429 | `PROVIDER_RATE_LIMITED` |
  | connect 或 read timeout | `PROVIDER_TIMEOUT` |
  | connection refused | `NETWORK_PROVIDER_UNREACHABLE` |
  | 回應超過 16 MiB | `MODEL_RESPONSE_TOO_LARGE` |
  | 回應是空的、非 JSON、array、tool-call 或被截斷 | `MODEL_RESPONSE_INVALID` |
  | `finish_reason=length` | `MODEL_OUTPUT_INCOMPLETE`，狀態為 `incomplete` |

- **claude-cli／codex-cli（假執行檔）：**
  - exit 3、SIGKILL → `PROVIDER_RUNTIME_ERROR`；
  - timeout → `PROVIDER_TIMEOUT`；
  - 輸出是空的或不是 UTF-8 → `MODEL_RESPONSE_INVALID`；
  - 輸出超過 1 MiB → `MODEL_RESPONSE_TOO_LARGE`；
  - 找不到執行檔 → `ENV_PROVIDER_RUNTIME_MISSING`；
  - child 的 stderr（含 `sk-` 字串）不會被輸出。
- **其他 `ask` 路徑：**
  - SIGINT → exit 130，狀態 `interrupted`；
  - stdout 編碼錯誤 → `LOCAL_IO_ERROR`，exit 1；
  - usage 錯誤 → exit 2，不建立 run 或 store；
  - 優先順序：CLI flag > 使用者設定 > 預設值；`--debug` 和 `LLMTOOL_HOME` 都不會開啟儲存；
  - `LLMTOOL_HOME` 是相對路徑、含控制字元、含 `..` 或空字串時，會發出 warning，`ask` 照常完成；Unicode 和結尾斜線的路徑可以正常儲存；
  - 其他指令（`analyze-log`、`review-diff`、`config show`、`chat`、`context-pack`、`git`）沒有 regression，provider 的錯誤訊息和 A11a 之前逐位元組相同，`configure` 會保留 diagnostics 設定。
- **reader：**
  - 約 35 種畸形的 artifact，例如 BOM、空檔、duplicate key、型別錯誤、超大整數、v2 版本、控制字元、深層巢狀、中間有空行、缺最後的換行、不存在的日期：在 3.13 和 3.9 上全部 fail-closed，回傳固定的 code，沒有 traceback。
  - 輸出只有 ASCII。
  - 執行 200 次後沒有 fd 洩漏。
  - 1000 個項目、16 個檔案、32 筆 event、16 KiB 的行、100 列截斷這些邊界值都正確。
  - 唯一的版本差異：5000 位數的 `schema_version` 在 3.13 回報 `FORMAT_INVALID`、在 3.9 回報 `FORMAT_UNSUPPORTED`，兩者都 fail-closed，而且只會出現在偽造的輸入。
  - CRLF 的 trace 只要 `trace_bytes` 相符就會被接受，但 writer 從不產生這種格式。
- **store writer：**
  - 預設不儲存時沒有任何 I/O；
  - 走訪路徑時不跟隨 symlink，也不 chmod；
  - stage 初始化、發布前後的 identity 比對；
  - macOS 和 Linux 的 rename flag 正確；
  - 短寫入的處理；
  - 每個錯誤路徑都會關閉 fd；
  - lock 的順序是先 root EX 再 run NB，`finish` 等 root 最多 2 秒，沒有 deadlock；
  - reservation 以完整大小計入容量；
  - 淘汰順序、grace period 和 reconcile 都符合 spec；
  - commit 後的 reservation unlink 或 root-lock timeout 仍然回報 `persisted=True`。

## 7. NOT-TESTED

- 公司 LLM 或任何真實的 provider：這輪刻意只用 localhost mock，已知的 live 結果請見之前的報告。
- Linux 的實機執行（`renameat2` 分支只靠 CI）；原生 Windows。
- 真實的 ENOSPC 或 EDQUOT（只用 EIO injection 模擬）；斷電後的 durability；network filesystem。
- Python 3.14：本機沒有，只靠 CI。

## 8. 使用的指令（不含 secret）

```sh
git fetch --prune origin; git rev-parse HEAD origin/main; git rev-list --left-right --count HEAD...origin/main
git rev-parse HEAD^{tree} origin/main^{tree}; git worktree list
gh pr view 25 --repo davidcatcat/davidcattoolsproject --json state,mergedAt,mergeCommit
cd tools/llmtool; PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests
PYTHONHASHSEED=1 PYTHONPATH=$PWD .venv/bin/python $B/probeA.EsoB/probe_evict_partial.py $(mktemp -d $B/v.XXXX)
PYTHONPATH=$PWD /usr/bin/python3 $B/probeA.EsoB/probe_evict_sigkill.py $(mktemp -d $B/v.XXXX)
PYTHONPATH=$PWD .venv/bin/python $B/probeB.Ly0f/probe_close_after_commit.py $(mktemp -d $B/v.XXXX)
PYTHONPATH=$PWD:$B/harness.bTjJ .venv/bin/python $B/brokenpipe.xSfU/p.py
PYTHONPATH=$PWD:$B/harness.bTjJ .venv/bin/python $B/badsettings.IMhU/p.py
PATH=$B/f1_verbose_drop.n1Oz/bin:$PATH PYTHONPATH=$PWD .venv/bin/python $B/f1_verbose_drop.n1Oz/direct.py
bash $B/pipe.BVQl/run.sh; bash $B/symlinkcode2.pRV0/probe.sh
PYTHONPATH=$PWD .venv/bin/python $B/incomplete_fec.kDkC/abandon.py <tmp>/state   # 之後執行 runs、trace --errors
```

## 9. 流程上的偏差（如實記錄）

- **repo 裡多了 3 個未追蹤的目錄：** 我在 `tools/llmtool` 目錄下重跑 B1／B4 的 probe 時，harness 在目前的工作目錄建立了 `tools/llmtool/case.gnwz8ul9/`、`case.k8z3qd3e/`、`case.pvdqfxsv/`（2026-10-05 00:53）。
  - 內容只有合成的測試 config 和 store（localhost mock 的 run），沒有任何 credential。
  - 因為不能用 `rm` 清理，**我保留了它們，請使用者決定是否刪除**。
  - 除了這 3 個目錄和這份報告，repo 沒有其他變更；production code、tests 和文件都沒有改。
- **一個 agent 違反了 scratchpad 的規則：** reader 方向的 agent 在 scratchpad 裡的 `$B/symlinkcode.YziD/` 用 `unlink` 刪掉了一個自己建立的 symlink，違反「不清理」的規則。那個 probe 的結果沒有被採用，已在 `$B/symlinkcode2.pRV0/` 用全新的目錄重做。
- 這輪沒有修任何 bug，沒有 merge，沒有開 PR，也沒有 commit 或 push 這份報告。

## 10. 最終答案

**CONFIRMED BUG COUNT: 7**

- **blocker：** 無。
- **non-blocking 的真實 bug：**
  - B1（broken pipe：exit 120，並把未送達的輸出記成 success）
  - B2（eviction 中斷後留下永久殘留）
  - B3（verbose metadata 被整批丟掉，regression）
  - B4（`trace=not-saved` 的狀態錯誤）
  - B5（symlink 被分類成 UNAVAILABLE）
  - B6（`runs` 的 `final_error_code=none`）
  - B7（commit 後 close 失敗，被回報成沒有儲存）
- **需要更多證據的 likely bug：** L1（writer crash 殘留的分類）、L2（時鐘回撥）。
- **theoretical risk：** Linux `renameat2` 的平台限制、EEXIST 的分類、run 在讀取途中消失。
- **檢查過、不是 bug 的已知限制：** M2、M4、M5、P1–P4、N1、修正前就存在的損壞 reservation、1 小時 grace period、SIGKILL 不等於斷電、同一個 UID 的 namespace 限制、`runs` 沒有 `--json`、no-summary row 的時間為 null。
