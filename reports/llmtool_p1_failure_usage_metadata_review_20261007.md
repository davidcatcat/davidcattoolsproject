# llmtool PR #39 P1 failure usage metadata — 獨立 review（2026-10-07）

## 範圍與方法

- **repo 狀態：**
  - PR #38（Round 3）**已經 merge** 到 main（`59ef2b7`），最後加上了 `68a2576` 的 Round 3 synthesis；
  - 新增了 **Draft PR #39**（`feat/llmtool-p1-failure-usage-metadata-20261006`，head `d40ce61`）。這是我在 Round 3 review 中建議的 P1：在 provider 失敗時保留 usage。
- **PR #39 的變更：** 10 個檔案。
  - production 有 5 個：`openai_compatible.py`、`errors.py`、`diagnostics.py`、`diagnostics_runtime.py`、README；
  - test 有 4 個：+277 行，新增了 16 個 regression test；
  - validation report 有 1 個。
- **方式：** 這是一般的 diff／implementation review（`review-change`）。
  1. 逐行閱讀 production 的 diff 和新增的 provider test。
  2. 在**整個 process 層級封鎖網路**的條件下，用 Python 3.9.6 和 3.13 執行 4 個 focused 模組，結果都是 **173／173 OK**。PR 說 181 個，差的 8 個來自另外兩個模組 `test_diagnostics_metadata_regressions` 和 `test_diagnostics_failure_matrix`，所以數字是一致的。
  3. 自己撰寫邊界案例：bool、加總不一致、缺少欄位、負數、超過上限、唯讀、pickle。
  4. 檢查 merge 之後對 Round 3 凍結 artifact 的影響。
- **CI：** exact head 的 3.9 和 3.14 都是 SUCCESS（PR checks）。
- **沒有做的事：** 沒有呼叫 company 或 Codex，沒有修改任何程式碼，沒有 merge。

## 結論：APPROVE WITH ONE DOCUMENTATION CHANGE

- 實作正確、範圍窄、fail-safe。
- 但是 **merge 之後，Round 3 已經 merge 的凍結 wrapper 的 test 會在 main 上失敗**。這是 pin 機制應有的行為，但 PR 和 validation report 都沒有說明。建議在 merge 前補上說明（§3）。

## 1. 實作：PASS

| 項目 | 確認 |
|---|---|
| 只在安全 decode 之後才取 usage | `openai_compatible.py`：在 JSON decode 成功、`obj` 是 dict 之後，才用 `type(usage) is dict` 檢查，**只取三個欄位名**，然後交給 `validate_provider_usage`。invalid JSON、太大的回應、HTTP、network、timeout 的路徑都在這之前就 raise，不會解析 body（有 test：`…invalid_json_and_nondict…`、`…oversize_body_is_never_decoded`、`…http_network_timeout_do_not_parse_body`） |
| 涵蓋所有可以 parse 的失敗路徑 | API `error`、缺少 choices、**length**、非 stop 的 finish_reason、tool／function call、空的 content、Unicode 錯誤，總共 7 個 raise 點都加上了 `provider_metadata`。成功的路徑沒有改變 |
| 驗證 | `validate_provider_usage` 要求 key 剛好是三個、都是 `str`；每個值都用 `_integer`（`type is int`，所以 bool 被排除）並且在 0 到 10^12 之間；total 必須等於 prompt + completion；經過 JSON 大小的檢查之後，回傳 `MappingProxyType`（唯讀） |
| fail-safe | provider、`LLMToolError.__init__`、`RunContext.error` 三層都有 `try/except`。metadata 無效時**只會被丟掉**，不會改變原本錯誤的 message、code、exit 或 retryable |
| 不洩漏內容 | 只保存三個整數。不在 `args`、`str`、`repr` 中，也不在 `DiagnosticError` 的 schema 中。有 test：`…cannot_carry_raw_content_or_credentials` |
| diagnostics | `RunContext.error` 只對 `type(exc) is LLMToolError` 而且 metadata 存在的情況，先送出 `provider.response` event，再送出 error。這沿用了既有的 verbose 和 persistence 設定（預設是關閉的） |

**我自己執行的邊界案例：**

- 正確的三個值 → 唯讀的 mapping。試圖寫入會產生 TypeError。`str` 和 `args` 只包含 message。
- `prompt_tokens=True`、加總不一致、只有兩個欄位、負數、10^13 → 全部是 **None**，原本的錯誤不受影響。
- legacy 的 `LLMToolError('legacy')` 仍然可以 pickle 和 unpickle，`provider_metadata` 是 None。
- 帶有 metadata 的 coded error 無法 pickle（TypeError）。docstring 本來就寫明「coded pickle 不支援」，所以行為一致，不是 regression。

**non-blocking：**

1. 失敗路徑的 `provider.response` event 沒有 `elapsed_seconds`，所以沒有 `duration_ms`。這不影響 accounting，以後可以考慮補上。
2. **真實 gateway 在 `finish_reason=length` 時是否真的回傳 usage，NOT TESTED**（validation report 已經寫明）。在下一次有授權的 company 呼叫中順便觀察就可以，不值得為此單獨呼叫一次。
3. 目前沒有任何呼叫端讀取 `exc.provider_metadata`。Round 3 的 `live_wrapper.invoke_cli` 只在成功時取得 `reply.metadata`，失敗時只取得 safe code。**將來的 wrapper 必須明確讀取這個 property**，P1 才會對 benchmark 的 accounting 產生效果。

## 2. 測試：PASS

- focused：173／173（3.9.6 和 3.13），封鎖網路。
- 新增的 provider test 涵蓋了：length 保留三個值和原本的錯誤；缺少、部分或無效的 usage → 不可用；所有可以 parse 的 invalid 路徑；驗證失敗不影響原本的錯誤；invalid JSON 或非 dict；太大的回應不 decode；HTTP、network、timeout 不解析 body；不能夾帶內容或憑證。
- 這些 test 都是在 production 修改之前就先 FAIL 的 reproducer（依照 validation report 中 tests-first 的紀錄）。
- 完整 suite 1037 個（validation report），CI 3.9 和 3.14 都是 SUCCESS。我沒有重跑完整的 suite。

## 3. 必須補充的說明：merge 之後 Round 3 的凍結 manifest 會失效

- Round 3 的 `phase0_preregistration.json`（已經在 main 上）pin 了 17 個 artifact。**PR #39 修改了其中 3 個：** `diagnostics.py`、`errors.py`、`providers/openai_compatible.py`。
- **我在 P1 branch 上執行 main 上的 `reports/token_benchmark/20261006_round3/test_live_wrapper.py`：** 有 12 個以上的 test 是 **ERROR**，包括：
  - `test_correct_constructor_pin`；
  - `test_every_run_next_reloads_pinned_manifest`；
  - `test_future_runner_*`；
  - `test_drift_during_request_observation_before_emission`。

  原因是 `verify_registration` 檢查 artifact 的 hash 不符，產生 `WrapperIntegrityFailure`。
- **這是 pin 機制正確的行為**：凍結的實驗不能在新的 production code 上執行。CI 只執行 `tools/llmtool/**`，所以 CI 會是綠的。
- 但是 merge 之後，**main 上的 `reports/` 中會有一組永遠失敗的 test**。將來有人重跑時，可能會誤以為是 regression，或者想要「修正」manifest。validation report 第 181 行只寫了「frozen benchmark wrappers have not been migrated」，**沒有說明它們會失敗**。

**建議（merge 之前，只修改文件，不修改凍結的檔案）：**

1. 在 PR #39 的描述和 validation report 中加入：「merge 後，Round 3 Phase-0 manifest（pin `37f48c17…`）在 main 上會因為 artifact drift 而無法驗證，這是預期的行為；Round 3 的 wrapper 和 test 只在 `59ef2b7` 或更早的 commit 上有效，重現的方法是 checkout 那個 commit」。
2. 在 `reports/token_benchmark/20261006_round3/README.md` 加一段 **historical notice**，內容同上。這只是新增說明，不修改任何被 pin 的檔案；README 本身沒有被 pin。
3. **不要**為了讓這組 test 變綠而重新產生 manifest 或修改 test。那樣會破壞 Round 3 紀錄的完整性。如果將來要做 Round 4，應該建立新的 wrapper 和 manifest。

## 判定

```text
PR #39 P1 FAILURE USAGE METADATA REVIEW:
APPROVE WITH ONE DOCUMENTATION CHANGE

Implementation:
PASS — usage selected only after safe JSON decode; exactly 3 integer fields (bool excluded), 0..10^12, total=sum,
  read-only MappingProxyType; attached at all 7 parseable failure sites incl. finish_reason=length; never on invalid JSON,
  oversize, HTTP/network/timeout; original message/code/exit/retryability preserved; invalid metadata silently dropped;
  no text/credentials; success path unchanged

Tests:
PASS — focused 173/173 on Python 3.9.6 and 3.13 under network block (PR's 181 includes two extra modules);
  16 tests-first regressions; exact-head CI 3.9/3.14 SUCCESS; full suite not rerun by me

Required before merge (documentation only):
State that merging invalidates the Round-3 frozen manifest (diagnostics.py, errors.py, openai_compatible.py are pinned):
  reports/token_benchmark/20261006_round3/test_live_wrapper.py errors on main after merge by design; Round-3 artifacts are
  valid only at 59ef2b7 or earlier. Add to PR body/validation report and a historical notice in the round3 README.
  Do NOT regenerate the manifest or edit frozen tests.

Non-blocking:
failure provider.response event lacks duration; real gateway usage-on-length NOT TESTED (observe on next authorized call);
  no caller consumes exc.provider_metadata yet — a future benchmark wrapper must read it explicitly

Merge:
user decision after the documentation change; not performed by this review
```

- 本報告 commit 到 PR #39 的 branch。沒有修改程式碼，沒有呼叫 company 或 Codex，沒有 merge，也沒有開始 A11b.2、A11c 或 Round 4。
