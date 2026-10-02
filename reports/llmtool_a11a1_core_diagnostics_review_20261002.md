# llmtool A11a.1 core diagnostics 獨立 review（2026-10-02）

## 範圍與方法

- 審查對象：分支 `feat/a11a1-diagnostics-core-20261002`，HEAD `9712bf3818e024e2d49ce0ca18bb5ad44f6cacde`。
  - 以 `origin/main` `624c33c` 為基礎（PR #14 的 A11a implementation spec 已 merge）：ahead 2、behind 0。
  - 兩個 commit：`8b2148e`（實作）、`9712bf3`（validation 文件）。
- 變更內容：
  - 新增 `tools/llmtool/llmtool/diagnostics.py`（379 行）與 `tools/llmtool/tests/test_diagnostics.py`（453 行）。
  - `tools/llmtool/llmtool/errors.py` +20 行。
  - 更新 `tools/llmtool/ROADMAP.md`，新增 `reports/llmtool_a11a1_core_diagnostics_validation_20261002.md`。
- 依據：[A11a implementation spec](llmtool_a11a_implementation_spec_20261002.md) 的 §2–§5、§12–§13、§17（A11a.1：core models 與 coded errors；no persistence、no CLI behavior change、no provider call change）。
- 方式：
  - **唯讀**，在 canonical repo 進行。canonical repo 當時就 checkout 在這個分支，review 時沒有切換分支，也沒有使用 worktree、clone 或專案副本。
  - 逐行閱讀 `diagnostics.py` 與 `errors.py` 的 diff。
  - 在本機執行完整測試，直接呼叫 API 測試邊界情況，並用 `gh` 讀取 CI log。
  - 沒有呼叫任何 provider，也沒有使用網路服務（GitHub API 除外）。

## 結論：REQUEST CHANGES（暫時不要 merge）

**final-head CI 在 Python 3.14 上失敗**，原因是一個實際存在、跨 Python 版本的 timestamp 驗證 bug（R1）。另外有兩個設計問題（R2、R3），會直接影響 A11a.2 的 error path 與 `ask` 的穩定性，建議在這個 slice 一起修正。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 651 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`） | 651 tests OK（1 skipped） |
| GitHub Actions `llmtool CI` run `36925256634`（`9712bf3`） | `check (3.9)` success；**`check (3.14)` failure**（步驟 "Offline canonical checks"） |
| 範圍 | production 程式碼只有 `errors.py` 引用 diagnostics（lazy import）；CLI、provider、config 都沒有修改，**符合 A11a.1 的範圍** |
| `LLMToolError` 的相容性 | 不帶 `diagnostic` 時，`str`、`args` 與 pickle 都和以前一樣，`.diagnostic` 是 `None`；帶 `diagnostic` 時，`str(e) == diagnostic.message`、`args == (message,)`，同時傳入 positional args 會丟出 `TypeError` |
| 跑完測試後 canonical repo 的工作目錄 | 沒有變更（只有原本就有的 benchmark／reasoning 未追蹤報告） |

## 符合 spec 的部分

- 不可變的 typed values（frozen dataclass，nested mapping 用 `MappingProxyType`）。
- `run_id` 由 `secrets.token_hex(16)` 產生，`span_id` 由 `secrets.token_hex(8)` 產生，兩者都嚴格驗證是小寫 hex。
- timestamp 是 UTC RFC 3339、精確到毫秒、結尾為 `Z`；duration 是有上限的整數毫秒。
- error code 只有 spec 列出的 16 個加上 2 個 `DIAGNOSTICS_*`，沒有預先建立完整的 taxonomy。
- `fallback_allowed=True` 直接拒絕（「A11a does not permit fallback」）。
- event `data` 依 type 使用白名單；`provider.*` 只接受 provider_type／profile／model／duration／token counts；`run.start` 只接受 `command="ask"` 與 mode。
- `diagnostic_json` 只接受 mapping、str、bool、None、int：拒絕 float（含 NaN／Infinity），絕不使用 `default=str`，使用 `ensure_ascii=True`、`sort_keys=True` 與緊湊分隔符，並有深度、項目數與位元組上限。
- `safe_cause` 是封閉的 mapping，只接受 `http_status`（100–599）、`process_status`、`category`（固定的分類）。
- 測試用 `str`／`repr` 會丟例外的物件與 `SUPER_SECRET_A11A1_MARKER`，證明任意物件不會被轉成字串寫進 JSON。

## 問題清單

### R1（High，阻擋 merge）：Python 3.14 上 timestamp 驗證行為不同

- **CI log：**
  ```
  FAIL: test_timestamp_requires_exact_format_and_real_date (test_diagnostics.IdentityTests.test_timestamp_requires_exact_format_and_real_date) (value='2026-10-02T24:00:00.000Z')
    File ".../tools/llmtool/tests/test_diagnostics.py", line 88, in test_timestamp_requires_exact_format_and_real_date
      with self.subTest(value=value), self.assertRaises(ValueError):
  AssertionError: ValueError not raised
  ```
- **原因：** `validate_timestamp`（`diagnostics.py:157-165`）的 regex 允許任何兩位數的小時，再用 `datetime.fromisoformat` 判斷日期是否合法。Python 3.14 的 `fromisoformat` 開始接受 ISO 8601 的 `24:00:00`（代表隔天 00:00），所以 `T24:00:00.000Z` 在 3.9／3.13 上被拒絕，在 3.14 上卻被接受。
- **為什麼本機沒測到：** 本機的兩個版本是 3.13 與 3.9，CI 是 3.9 與 3.14。validation report 寫了 "Final-head remote CI is not claimed by these local results"，但沒有確認 CI 的實際結果。
- **建議：** regex 直接限制欄位範圍，例如 `T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z`；或改用 `datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ')` 驗證日曆值。不要依賴 `fromisoformat` 在不同版本之間的行為。

### R2（Medium）：憑證規則誤擋正常的錯誤訊息，而且建立錯誤物件時丟出 `ValueError`

`_CREDENTIAL`（`diagnostics.py:87-92`）比對單獨的 `authorization`，以及任何 `token`／`secret`／`password`… 後面接 `:` 或 `=` 的形式。直接呼叫的結果：

| 呼叫 | 結果 |
|------|------|
| `DiagnosticError(code=AUTH_PROVIDER_REJECTED, message="provider authorization rejected", …)` | `ValueError: diagnostic text contains recognizable credential material` |
| `DiagnosticError(code=AUTH_CREDENTIAL_MISSING, message="Authorization header missing", …)` | 同上 |
| `DiagnosticError(code=CONFIG_INVALID, message="api_key_env secret: not configured", …)` | 同上 |
| `DiagnosticError(code=AUTH_CREDENTIAL_MISSING, message="token=missing for profile", …)` | 同上 |
| `DiagnosticError(…, message="設定檔格式錯誤", …)` | OK |

這些都是 A11a.2 為 `AUTH_*`／`CONFIG_*` 很可能會寫的、由應用程式控制的訊息。更重要的是：**建立 diagnostic 失敗時，丟出的是 `ValueError` 而不是 coded 的 `LLMToolError`**。A11a.2 在 raise site 建立 coded error 時，error path 本身就可能出錯，變成 traceback 或錯誤的分類。

**建議：**
- 只比對帶有值的形式：`authorization:\s*(bearer|basic)\s+\S+`、`bearer\s+[A-Za-z0-9._~+/=-]{8,}`、`sk-…`、`AKIA…`、PEM private key、`(api_key|password|token|secret)\s*[:=]\s*` 後面接看起來像值的字串（例如長度門檻）；不要比對單獨的 `authorization` 這個字。
- 在 spec 或程式中明確規定：**建立 diagnostic 失敗時，降級為固定的 `INTERNAL_UNEXPECTED`（或該 code）與固定的 application 訊息**，不能把 `ValueError` 丟到使用者面前。

### R3（Medium）：diagnostics 驗證失敗時，不能影響主要的指令

- model ID 的 regex `[A-Za-z0-9][A-Za-z0-9._:/-]*` 不允許 `@`：`DiagnosticEvent(type=PROVIDER_REQUEST, data={"model": "claude-3-5-sonnet@20240620"})` 會丟出 `ValueError: invalid diagnostic model identifier`（Vertex／Bedrock 常見的格式）。`qwen3.6-35b-a3b` 可以通過。
- component 只接受小寫（`Provider.OpenAI` 會被拒絕），是刻意的設計，沒有問題。
- 在 A11a.1 這些只是驗證規則；但 spec §5 只規定「超過上限」時要 drop event，**沒有規定欄位驗證失敗時怎麼處理**。到了 A11a.2，如果記錄 `provider.request` 時因為 model 名稱不合格而丟出例外，`ask` 就會因為 diagnostics 而失敗。

**建議：**
- 在 spec 中明確寫出：event 不合格（包含欄位驗證失敗）時要 drop，並計入 `dropped_event_count`，最多發出一筆 `DIAGNOSTICS_EVENT_DROPPED`，**主要的指令照常執行**。
- 考慮讓 model regex 接受 `@`。

### R4（Low）：event 的 type 和 severity 沒有互相檢查

下列都能成功建立：

- `type=error` 搭配 `severity=info`
- `type=run.end`、`status=success` 搭配 `severity=error`
- `type=error` 搭配空的 `data`（沒有 `code`）

建議加上一致性規則：`error` 必須是 error severity；`warning` 必須是 warning severity；error／warning event 的 data 必須有 `code` 與 `message`。

### R5（Low）：帶 code 的 `LLMToolError` 不能 pickle 或 deepcopy

`pickle.dumps(LLMToolError(diagnostic=DiagnosticError(...)))` 會丟出 `TypeError: cannot pickle 'mappingproxy' object`。validation report 有寫明 "pickle transport of coded models is not introduced"，但 `copy.deepcopy` 與 `multiprocessing`、`concurrent.futures` 的跨 process 回傳也會受影響。

建議：`safe_cause` 改用不可變的 tuple of pairs 保存，對外再轉成 dict；或為 `DiagnosticError` 實作 `__reduce__`。至少在 `LLMToolError` 的 docstring 註明這項限制。

### R6（Nit）：延後項目的清單不完整

- spec §2 的 **run summary model** 沒有實作，validation report 的延後清單也沒有提到它。
- spec §5 的截斷規則（`truncated`）與 drop 計數，report 有寫明延後到 A11a.2。

建議在 report 的延後清單補上 run summary，並說明是哪一個 slice 要實作。

## 建議

1. **修 R1**（timestamp regex），並在本機加上 Python 3.14，或至少確認 final-head CI 通過再要求 review。
2. **R2、R3 建議在這個 slice 一起修**：放寬憑證 regex，並在 spec 中寫明「diagnostic 建立或驗證失敗時降級、不影響主要的指令」。A11a.2 會直接建立在這些規則上。
3. R4–R6 可以一起處理，或記為 follow-up。
4. 修正後重跑 CI（Python 3.9 與 3.14），再 merge。

## 清理

- 這次 review 沒有建立任何 worktree、clone 或專案副本，canonical repo 除了新增這份報告之外沒有任何變更。
- 沒有呼叫任何 provider，也沒有啟動背景 process。
