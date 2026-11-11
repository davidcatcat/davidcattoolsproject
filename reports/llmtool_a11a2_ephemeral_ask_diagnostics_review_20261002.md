# llmtool A11a.2 ephemeral ask diagnostics 獨立 review（2026-10-02）

## 範圍與方法

- 審查對象：分支 `feat/a11a2-ephemeral-ask-diagnostics-20261002`，HEAD `cdc9b487dc64f11bf83710ae20e699c23100fbed`。
  - 以 `origin/main` `98f9d48` 為基礎：ahead 4、behind 0。
  - 4 個 commit：
    - `56377f4`：implementation
    - `0eb78ee`：portable test fixture
    - `dd78977`：metadata containment
    - `cdc9b48`：文件與報告
  - 共 20 個檔案、+1697／−112。
- 依據：[A11a implementation spec](llmtool_a11a_implementation_spec_20261002.md) 的 §1、§6、§12、§14、§17（A11a.2：in-memory run context、`ask --debug`、穩定的 code／run_id 行、整合既有的 `--verbose`、provider adapter，不寫任何檔案；**gate：stdout／exit code 完全相容，而且 provider 只呼叫一次**），以及 ROADMAP 在 A11a.1 加入的「diagnostic collection failure must never fail the primary read-only command」。
- 方式：
  - **唯讀**，在 canonical repo 進行。canonical repo 當時就 checkout 在這個分支，review 時沒有切換分支，也沒有使用 worktree、clone 或專案副本。
  - 逐行閱讀 `diagnostics_runtime.py`、`cli.py`、`errors.py`、`config.py`、`inputs.py`、`diagnostics.py`，以及四個 provider 的 diff；並和 `origin/main` 舊版處理 `ask` 的 `main()` 逐行比對。
  - 在本機執行完整測試，並用 `gh` 查詢 CI。
  - 實際執行 CLI：暫存 HOME 搭配公司 gateway profile，以及 `127.0.0.1` 上會計算請求次數的 stub server。

## 結論：APPROVE WITH CHANGES

lifecycle、output mode、錯誤分類與「只呼叫一次 provider」都符合 spec，成功路徑的輸出也與 main 相容。有 1 個 medium 等級的相容性問題（V1：diagnostics drop 時，`--verbose` 遺失 provider metadata），違反「diagnostics 失敗不得改變輸出」的原則。修正只需幾行，建議在 merge 前處理。其餘都是 low／nit。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 705 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`） | 705 tests OK（1 skipped） |
| GitHub Actions `llmtool CI` | `36960214391`（`0eb78ee`）、`36960532402`（`dd78977`）、**`36961281025`（`cdc9b48`，final head）** 都 success |
| 跑完測試後 canonical repo 的工作目錄 | 沒有變更（只有原本就有的 benchmark／reasoning 未追蹤報告） |

## 符合 spec 的部分

**lifecycle（`diagnostics_runtime.RunContext`、`cli._run_ask`）**

- `argparse` 成功之後才建立 run context，而且建立時沒有任何 filesystem、網路、credential 或 provider 存取；usage error 仍然是 exit 2，也不會產生 run ID。
- event 依序是 `run.start` → `provider.request` → `provider.response` 或 `error` → `run.end`。上限是每次 run 32 筆 event、64 KiB，超過就 drop。
- 所有 observer 呼叫都包在 `observe()` 裡；`RunContext` 建立失敗時 `run=None`；`drop()` 會先把 `emitted_drop_warning` 設好，避免遞迴；`run.end` 之後不再新增 event。
- `client.complete(messages)` 只呼叫一次，前後都沒有 retry 或 fallback。
- 沒有預期到的例外會轉成固定訊息的 `INTERNAL_UNEXPECTED`，**不印 traceback，也不把例外轉成字串**。

**output mode**

- normal 成功時 stdout 不變、stderr 沒有任何輸出。
- 失敗時第一行仍然是原本的 `llmtool: <message>`，第二行是 `llmtool: code=<CODE> run_id=<id> trace=not-saved`，exit code 不變。
- `--verbose` 先印出既有的 provider metadata，再加上 `run_id`、`provider_type`、`diagnostics_persisted=false`。
- `--debug` 另外印出 event 摘要（只有 id／type／component／severity／duration／warning code）與 `dropped_event_count`，不輸出 prompt、回應、body 或例外內容。

**錯誤分類**

- 依據「處理到哪一步」、例外本身帶的 diagnostic，以及數字形式的 HTTP 或子 process 狀態來分類，**不比對錯誤訊息的文字**（`http_error_code`：401／403 → `AUTH_PROVIDER_REJECTED`，429 → `PROVIDER_RATE_LIMITED`，其他 → `PROVIDER_HTTP_ERROR`）。
- `coded_error`：建立 diagnostic 失敗時，原本的 `LLMToolError` 文字照常保留，只標記 `diagnostic_dropped`，並記下已驗證過的 code；這就是 A11a.1 re-review 要求的降級行為。

**provider**

- 四個 provider 的修改**只有把 `raise LLMToolError(msg)` 換成 `raise coded_error(msg, code, component, …)`**，錯誤訊息的文字完全相同，所以 `ask` 以外的指令印出的錯誤不變。
- CLI provider 另外加上 `safe_cause={"process_status": returncode}`。sandbox、環境變數 allowlist、隔離的 cwd 與 raw stderr 的隱藏都沒有變更。

**不寫任何檔案**：實測後，暫存 HOME 底下除了 config 之外沒有任何檔案。

**F1／F2（A11a.1 re-review 的 follow-up）已處理**

- `_CREDENTIAL` 加入 GitHub（`gh[pousr]_`、`github_pat_`）、Slack（`xox[abprs]-`）與 JWT 的格式。
- `Basic` 後面的值會以 base64 解碼，含有 `:` 才算憑證。
- `<redacted>`、`[redacted]`，以及只由 `*` 組成的值，都視為狀態詞。

## 實測

### 本機 stub server（計算請求次數）

| 情況 | rc | stdout | stderr | provider 呼叫次數 |
|------|----|--------|--------|------------------|
| `ask --provider stub "x"`（model `m-ok`） | 0 | `STUB_OK\n` | 0 bytes | 1 |
| `ask --provider stub --verbose "x"` | 0 | `STUB_OK\n` | `elapsed_seconds=0.004`、`model=m-ok`、`prompt_tokens=3`、`completion_tokens=2`、`total_tokens=5`，接著 `run_id=…`、`provider_type=openai-compatible`、`diagnostics_persisted=false` | 1 |
| `ask --provider stub --debug "x"` | 0 | `STUB_OK\n` | 上述 metadata，加上 4 筆 event 摘要（`run.start`、`provider.request`、`provider.response duration_ms=3`、`run.end`）與 `dropped_event_count=0` | 1 |
| `ask --provider stub --verbose "x"`（model `_internal`） | 0 | `STUB_OK\n` | **只有** `run_id=…`、`provider_type=openai-compatible`、`diagnostics_persisted=false`（見 V1） | 1 |
| `ask --provider stub --debug "x"`（model `_internal`） | 0 | `STUB_OK\n` | event 3 是 `warning code=DIAGNOSTICS_EVENT_DROPPED`，`dropped_event_count=1`；回答與 rc 不受影響 | 1 |

### 失敗情況（暫存 HOME 的 profile）

| profile | rc | stderr |
|---------|----|--------|
| `unreachable`（`base_url: http://127.0.0.1:9`） | 1 | `llmtool: cannot connect to gateway; check network, base_url, and TLS` ／ `llmtool: code=NETWORK_PROVIDER_UNREACHABLE run_id=… trace=not-saved` |
| `needkey`（缺少 `api_key_env`） | 1 | `llmtool: configured API key environment variable is missing or empty` ／ `llmtool: code=AUTH_CREDENTIAL_MISSING run_id=… trace=not-saved` |
| `needkey --debug` | 1 | 同上，加上 `run.start`、`error component=config.auth`、`run.end severity=error` 三筆 event 摘要 |
| `broken`（`base_url: null`） | 1 | `llmtool: provider base_url is not configured` ／ `llmtool: code=CONFIG_PROVIDER_NOT_READY run_id=… trace=not-saved` |

### 公司 gateway

- 3 次 `ask`（normal、`--verbose`、`--debug`）都在約 75 秒後回傳 `llmtool: gateway request timed out`、`code=PROVIDER_TIMEOUT`、rc=1，diagnostics 的分類與輸出格式正確。
- 隨後直接用 `curl` 檢查：VPN 路由仍然經由 `utun4`，但 `192.0.2.10:4000` 的 TCP 連線在 5 秒後逾時，`/v1/models` 與 chat 都無法連線。
- **gateway 的成功路徑在 review 時無法驗證（環境問題），不視為 llmtool 的問題。** 成功路徑的相容性改由上方的 stub server 驗證。

## 問題清單

### V1（Medium）：diagnostics drop 時，`--verbose` 遺失既有的 provider metadata

- **位置：** `diagnostics_runtime.py:100-131`（`RunContext.response`）。
- **原因：** 只有在 `provider.response` event 成功建立之後，才設定 `self._metadata = safe`；建立失敗時回傳 `{}`，`display()` 就沒有 provider metadata 可以印。
- **何時會發生：** provider 接受的 model 規則（`[A-Za-z0-9_./:-]{1,128}`）比 diagnostics 的規則（開頭必須是英數字、不允許 `.`／`..` 路徑片段，並套用憑證檢查）寬鬆。例如 gateway 回傳 `_internal` 或 `models/./x` 時，provider 會照常接受，diagnostics 卻會 drop 這筆 event。
- **實測：** stub 回傳 `model=_internal` 時，`--verbose` 只印出 `run_id`／`provider_type`／`diagnostics_persisted`；main 的版本會印出 `elapsed_seconds=…`、`model=_internal`、`prompt_tokens=3`、`completion_tokens=2`、`total_tokens=5`。
- **影響：** stdout 與 rc 不受影響，但違反 spec §6「Existing `--verbose` stays supported and keeps current provider metadata」，以及 ROADMAP「diagnostics failure must never fail/alter the primary command」的精神。
- **建議：** 先把 provider metadata（白名單上的 key）保存下來供 verbose 使用，再嘗試建立 event；event 建立失敗只影響 diagnostics。加上一個 model 名稱會被 diagnostics 拒絕的 regression 測試。

### V2（Low）：呼叫順序與 main 不同

- **main：** `create_client(config, api_key(config)).complete(messages_for(...))`，依 Python 的求值順序是 `api_key` → `create_client` → `messages_for`。
- **這個分支：** `messages_for` → `api_key` → `create_client`。
- **影響：** 只有在 `messages_for` 與 `api_key` 都會失敗時，使用者看到的錯誤才會不同（例如 skill／prompt 組裝的錯誤，與缺少 key 同時發生）。
- **建議：** 恢復原本的順序，或在 validation report 中寫明這是刻意的變更。

### V3（Nit）：錯誤分類的細節

- `OpenAI returned an API error; response body omitted`（HTTP 200 但 body 帶有 `error`）被分類為 `MODEL_RESPONSE_INVALID`。這比較接近 provider 端的錯誤，可以考慮 `PROVIDER_HTTP_ERROR` 或 `PROVIDER_RUNTIME_ERROR`。openai-compatible 對應的 `gateway returned an API error` 也一樣。
- 連線階段的 timeout 被分類為 `PROVIDER_TIMEOUT`，而不是 `NETWORK_PROVIDER_UNREACHABLE`（實測：gateway 無法建立 TCP 連線時，`ask` 回報 `PROVIDER_TIMEOUT`）。這符合 spec 的「timeout → PROVIDER_TIMEOUT」，但使用者無法分辨是連不上還是 provider 太慢。可以考慮之後用 `safe_cause.category` 區分 connect 與 read。

## 建議

1. merge 前修正 **V1**（只需幾行），並加上 regression 測試。
2. **V2**：恢復原本的順序，或寫入 report。
3. **V3** 記為 follow-up。
4. 公司 gateway 恢復後，補跑一次成功的 live `ask`（normal、`--verbose`、`--debug`）。

## 清理

- 這次 review 沒有建立任何 worktree、clone 或專案副本，canonical repo 除了新增這份報告之外沒有任何變更。
- 暫存的 HOME、config 與 stub server 都在 scratchpad 的 `a11a2.hT1B`，stub server 已經停止。
