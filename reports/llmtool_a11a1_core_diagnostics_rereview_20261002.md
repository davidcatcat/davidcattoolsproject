# llmtool A11a.1 core diagnostics re-review（2026-10-02）

## 範圍與方法

- 審查對象：分支 `feat/a11a1-diagnostics-core-20261002`，HEAD `c36222e7b0f758dd8893cd764e4b4a5d158ba77d`。
  - 以 `origin/main` `624c33c` 為基礎：ahead 6、behind 0。
  - 本輪新增的 commit：
    - `274b689`：修正，改了 `diagnostics.py`、`errors.py`、`tests/test_diagnostics.py`。
    - `1b23da7`：remediation validation，更新 ROADMAP 與 validation report。
    - `c36222e`：記錄 remediation 的 CI 結果。
- 對照：上一輪的 [A11a.1 independent review](llmtool_a11a1_core_diagnostics_review_20261002.md)（R1–R6，REQUEST CHANGES），以及 Codex 更新後的 [A11a.1 validation report](llmtool_a11a1_core_diagnostics_validation_20261002.md)。
- 方式：
  - **唯讀**，在 canonical repo 進行。canonical repo 當時就 checkout 在這個分支，review 時沒有切換分支，也沒有使用 worktree、clone 或專案副本。
  - 逐行閱讀 `7522d5b..c36222e` 的 diff。
  - 在本機執行完整測試，直接呼叫 API 測試正、反例，並用 `gh` 查詢 CI。
  - 沒有呼叫任何 provider。

## 結論：APPROVE（可以 merge）

R1–R4 已修正，R5 以範圍為由延後（理由合理、已寫入文件），R6 已補齊文件。final-head CI 在 Python 3.9 與 3.14 上都通過。剩下 2 個 low 等級的 follow-up，不影響 merge。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 660 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`） | 660 tests OK（1 skipped） |
| GitHub Actions `llmtool CI` | `36925256634`（`9712bf3`）failure，也就是上一輪的 R1；`36928125467`（`1b23da7`）success；**`36955916282`（`c36222e`，final head）success** |
| 範圍 | production 程式碼仍然只有 `errors.py` 引用 diagnostics；CLI、provider、config 都沒有修改 |
| 跑完測試後 canonical repo 的工作目錄 | 沒有變更（只有原本就有的 benchmark／reasoning 未追蹤報告） |

## R1–R6 逐項確認

| # | Codex 的處理 | 程式碼 | 實測 | 判斷 |
|---|-------------|--------|------|------|
| R1 timestamp 在 Python 3.14 上的行為不同 | ACCEPTED / FIXED | `validate_timestamp` 的 regex 改為 `T(?:[01][0-9]\|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z`，通過 grammar 之後才用 `fromisoformat` 檢查日曆值 | `2026-10-02T24:00:00.000Z`、`2026-02-30T00:00:00.000Z`、`2026-10-02T12:60:00.000Z` 都被拒絕；`2026-10-02T23:59:59.999Z` 通過；3.14 CI success | **確認修好** |
| R2 憑證規則誤擋正常訊息 | ACCEPTED / FIXED | 單獨的 `authorization` 不再算是憑證；`Bearer`／`Authorization: Basic` 的值要至少 8 個字元，而且不是固定的狀態詞；assignment 的右側如果是 `missing`／`unset`／`redacted`／`none`／`null`／`not configured`／`placeholder` 就放行；OpenAI `sk-`、AWS `AKIA`、PEM private key 仍然拒絕 | 見下方的表格 | **確認修好**（另見 F1、F2） |
| R3 model 的 `@`，以及 diagnostics 失敗時不能影響主要的指令 | ACCEPTED WITH BOUNDARY | `_MODEL` 改為 `[A-Za-z0-9][A-Za-z0-9._:/@-]*`，另外拒絕 `.`／`..` 路徑片段。ROADMAP 新增：「**Diagnostic collection failure must never fail the primary read-only command.** A11a.2 lifecycle must catch diagnostics-only construction/validation failures, drop the event, count dropped events, and emit at most one fixed `DIAGNOSTICS_EVENT_DROPPED` warning while the primary `ask` continues.」 | `claude-3-5-sonnet@20240620`、`qwen3.6-35b-a3b`、`org/model:tag` 都通過；`models/../etc`、`a/./b` 被拒絕 | **確認修好**；containment 屬於 A11a.2 的責任，切分合理，A11a.2 review 時必須驗證 |
| R4 type 和 severity 的一致性 | ACCEPTED / FIXED | `_EVENT_SEVERITIES`：error→ERROR、warning→WARNING、run.start／provider.request／provider.response→INFO；error／warning event 必須有 `code` 與 `message`；`run.end` 的 status 與 severity 對應延後到 lifecycle slice | 測試涵蓋錯誤的組合，以及空的或缺欄位的 data | **確認修好** |
| R5 coded error 的 pickle／deepcopy | DEFERRED BY SCOPE | `LLMToolError` 的 docstring 新增 "Coded diagnostics transfer via bounded JSON; pickle/deepcopy are unsupported. Legacy uncoded exception pickle behavior is preserved." | — | **可以接受**：目前沒有跨 process 傳遞的需求，舊的、不帶 code 的錯誤 pickle 相容性仍有測試 |
| R6 延後清單不完整 | DOCUMENTATION FIX | ROADMAP 與 report 明確寫出 RunSummary 不在這個 slice 實作，屬於 A11a.3「User diagnostics settings and secure POSIX store」 | — | **確認修好** |

### R2 實測：`DiagnosticError(code=AUTH_CREDENTIAL_MISSING, message=…)`

**應該接受的一般訊息：**

| 訊息 | 結果 |
|------|------|
| provider authorization rejected | ACCEPT |
| Authorization header missing | ACCEPT |
| api_key_env secret: not configured | ACCEPT |
| token=missing for profile | ACCEPT |
| password: none | ACCEPT |
| bearer token missing | ACCEPT |
| HTTP 401: authorization failed | ACCEPT |
| increase max_tokens to 4000 | ACCEPT |
| input_tokens=12 output_tokens=5 | ACCEPT |
| 設定檔格式錯誤 | ACCEPT |
| api_key: &lt;redacted&gt; | **REJECT**（F2） |
| token=*** | **REJECT**（F2） |

**應該拒絕的憑證值：**

| 訊息 | 結果 |
|------|------|
| Authorization: Bearer abcdefgh12345678 | REJECT |
| bearer eyJhbGciOiJIUzI1NiJ9.payload.sig | REJECT |
| token: abc123xyz789 | REJECT |
| password=hunter2 | REJECT |
| api_key='sk-proj-abcdefghijklmnop' | REJECT |
| AKIAABCDEFGHIJKLMNOP leaked | REJECT |
| Authorization: Basic dXNlcjpwYXNzd29yZA== | REJECT |
| -----BEGIN OPENSSH PRIVATE KEY----- | REJECT |
| client_secret: shhh | REJECT |
| basic dXNlcjpwYXNzd29yZA==（沒有 `Authorization:` 前綴） | **ACCEPT**（F1） |
| ghp_abcdefghijklmnopqrstuvwxyz0123456789 | **ACCEPT**（F1） |
| xoxb-1234567890-abcdefghij | **ACCEPT**（F1） |
| eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig（JWT） | **ACCEPT**（F1） |

## 剩下的 follow-up（不影響 merge）

| # | 嚴重度 | 問題 | 建議 |
|---|--------|------|------|
| F1 | Low | **偵測不到幾種常見的 token 格式**：GitHub（`ghp_`／`gho_`／`ghu_`／`ghs_`／`ghr_`／`github_pat_`）、Slack（`xox[abprs]-`）、JWT（`eyJ…\.eyJ…\.…`）、沒有 `Authorization:` 前綴的 `basic <base64>`。spec 是「collection minimization first, redaction second」，這個檢查只是第二道防線，所以影響有限。 | 在 `_CREDENTIAL` 補上這幾種前綴與格式，並加入正、反例測試。 |
| F2 | Low | **已經遮蔽的值被誤擋**：`api_key: <redacted>`、`token=***` 會被拒絕。 | 把 `***`、`<redacted>`、`[redacted]`、`****`（以及只由 `*` 組成的值）加進 `_CREDENTIAL_STATUSES`，或在比對前先去掉括號。 |

## 給 A11a.2 review 的提醒

A11a.1 的 core model 依設計維持嚴格，遇到不合格的輸入會丟出 `ValueError`。A11a.2 review 時必須驗證 ROADMAP 新增的這條規定：

- 建立或驗證 diagnostic 失敗時，只 drop 該 event。
- 計入 `dropped_event_count`。
- 每次 run 最多發出一筆固定的 `DIAGNOSTICS_EVENT_DROPPED` warning。
- **`ask` 的結果、exit code 與 provider 呼叫次數完全不變。**

## 清理

- 這次 review 沒有建立任何 worktree、clone 或專案副本，canonical repo 除了新增這份報告之外沒有任何變更。
- 沒有呼叫任何 provider，也沒有啟動背景 process。
