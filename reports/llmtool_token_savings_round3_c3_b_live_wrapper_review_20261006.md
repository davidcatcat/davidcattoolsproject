# llmtool PR #38 Round 3 — C3-B final live-wrapper review（2026-10-06）

## 範圍與方法

- **審查對象：**
  - Draft PR #38，exact review head `94dd567a4da2cf9b14c5c93942b5d74c07c65c85`；
  - exact tested implementation head `fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9`；
  - pinned preregistration `0593f70b…c276`。
- **逐行閱讀：**
  - `live_wrapper.py`（390 行）；
  - `test_live_wrapper.py`（709 行，77 個 test）；
  - `phase0_preregistration.json`；
  - `instructions/L02.txt` 和 `instructions/L03.txt`；
  - `format_probe.py`；
  - production 的 `openai_compatible.py`、`errors.py`、`diagnostics.py`，以及 entrypoint／cli 的 exception 路徑；
  - C3-A、C3-1 的 validation report 和 coordinator review。
- **執行：**
  - **在整個 process 層級封鎖網路**：用 scratchpad 中的 `sitecustomize`，讓 `socket.connect`、`create_connection`、`getaddrinfo` 都直接 raise。在這個條件下，重跑了兩個 test suite。
  - 用一個獨立的 probe script（只放在 scratchpad），重現 digest、manifest pin、gold 隔離、CE10 和 usage 的檢查。
- **沒有做的事：**
  - 沒有連線到 `home-llm.example:8000`；
  - 沒有呼叫 company 或 Codex；
  - 沒有修改 wrapper、test、instructions、preregistration 或 production；
  - 沒有 merge。

## 結論：BLOCKED。只有 1 個 blocker（B1：manifest pin 沒有在程式碼層級封住），其他項目都是 PASS 或 CLOSED

## 1. Revision integrity：PASS

- `fff00d3..94dd567` 之間只有兩個 docs 檔案：C3-1 validation 和 C3-B handoff。`git diff fff00d3 HEAD -- reports/token_benchmark/` 是空的。
- 現在的 SHA-256：
  - preregistration = `0593f70b…c276`（等於 pin）；
  - `live_wrapper.py` = `3429c09c…0360`；
  - `test_live_wrapper.py` = `a666e0d4…7378`；
  - `format_probe.py` = `500fb8bd…87a3`；
  - `test_format_probe.py` = `80e62df3…fd79`。

  以上全部和 manifest `artifacts` 中的值相同。
- C3-1（`fff00d3`）只修改了三個檔案：`failure_reason` 加入 `MODEL_RESPONSE_INVALID` 和 `MODEL_RESPONSE_TOO_LARGE`（+2 行）、manifest 中 wrapper 和 test 的 hash，以及新增 111 行 test。
- PR 相對於 `fecbdba` 的所有變更都在 `reports/` 底下。production、runtime、skill、fixture 和 gold 都沒有變更。

## 2. Offline regression：PASS

| suite | 結果 |
|---|---|
| `test_format_probe.py` | **88／88 ok**，exit 0 |
| `test_live_wrapper.py` | **77／77 ok**，exit 0，耗時 5.2 秒 |
| `git diff --check` | PASS |

- verbose log 中沒有任何 FAIL、ERROR 或 skipped。
- 用 `def test_` 靜態計算，數量也是 88 和 77。
- 兩個 suite 都是在 process 層級封鎖網路的情況下通過，所以**確實沒有任何 socket 連線**。

**我實際檢查過內容的重要 test：**

- `test_original_opener_and_socket_never_called`；
- `test_verified_observer_no_mutation_delegates_once`：第二次呼叫會產生 ValueError，delegation 的次數仍然是 1；
- 5 個 `test_provider_*_terminal_accounting`；
- `test_forged_consistent_evaluation_rejected` 和 `test_forgery_cannot_enter_accounting`；
- `test_registration_pin_checked_before_decoding`；
- `test_future_runner_*`。

## 3. Zero-network Request freeze：PASS

- **路徑：** `dry_run` → `invoke_cli`
  - 使用**真正的** `llmtool.entrypoint.main`，HOME 是臨時目錄，`clear=True` 清空環境變數；
  - 經過**真正的** `OpenAICompatibleClient.__init__`，再把 `client.opener.open`（只替換這個 instance 的 method）換成攔截器；
  - production 的 `complete()` 照常用 urllib 建立 `Request`；
  - 攔截器驗證完之後 raise `DryRunCaptured`。它是 **BaseException**，所以會越過 production 的 `except Exception` 和 `except (OSError, …, ValueError)`；
  - 原本的 opener **不會被呼叫**。

  `offline_guard` 另外把 `socket.socket`、`create_connection`、`getaddrinfo`、`subprocess.Popen`、`OpenerDirector.open` 都換成 fail-closed。
- **我重現的實際 Request（L03）：**
  - `POST http://home-llm.example:8000/v1/chat/completions`；
  - 沒有 Authorization；
  - `timeout` 是 120.0；
  - body 的欄位**完全等於** `{chat_template_kwargs, max_tokens, messages, model, temperature}`；
  - `model = 'gw'`；
  - `temperature = 0.0`（float，不是 bool）；
  - `max_tokens = 4096`（int）；
  - `chat_template_kwargs = {'enable_thinking': False}`；
  - messages 的角色依序是 `['system', 'user']`。

  L02 的結果相同。
- **`validate_request`：**
  - 要求 `set(body) == FIELDS`，所以 `stream`、`tools`、`tool_choice`、`response_format` 或任何額外的欄位都會被拒絕（有 test）；
  - 嚴格的 decoder 拒絕重複的 key 和 NaN；
  - 檢查 user message 的 bytes 必須完全相同；
  - 用 tuple 比對的方式確認 request 沒有被修改。
- **network_calls 和 socket_opens 都是 0：** manifest 這樣記錄，我在 process 層級封鎖網路的條件下也實際確認了。

## 4. Frozen Request 和 user-message 的 digest：PASS

在禁止讀取 gold、封鎖網路的條件下，我獨立執行 `dry_run`。結果和要求的值**完全相同**：

| lane | user message | Request body |
|---|---|---|
| L02 | `e0194460…22ac` ✓ | `cc3c79f8…360f` ✓ |
| L03 | `3b94f0d8…45e2` ✓ | `6c1f7b77…cdf0` ✓ |

C3-1 沒有修改 instructions、config 或 runtime，所以這些 digest 和 C3-1 之前相同。

## 5. Pinned manifest loader 的要求：**BLOCKED（B1）**

**`load_preregistration()` 本身是正確的：**

1. 讀取 raw bytes；
2. **在 decode 之前**比對 SHA（有 test 用 `_json` 的 side_effect 證明沒有 decode）；
3. 用嚴格的 decoder 解析；
4. 呼叫 `verify_registration`，它會檢查：
   - contract、config、gold SHA、order、ceiling、bounds 和 retry；
   - artifact 集合必須完全相等，而且每個 artifact 都和磁碟上的 SHA 一致；
   - 每個 lane 的 instruction、fixture、skill、config 和 user message 的 SHA；
   - Request facts（body SHA、user SHA、endpoint、method、fields、roles、count）在 admission 時會用 `validate_request` 再檢查一次。

我實際測試：用正確的 pin 可以載入；用錯誤的 pin 會產生 `WrapperIntegrityFailure`。

**但是在程式碼層級，任意的 registration 都能繞過 pin：**

- `Phase0Wrapper.__init__(self, registration)` 接受**任何 dict**。`run_next` 和 `admitted_open` 只呼叫 `verify_registration(self.registration)`。
- `verify_registration` 檢查的是「**dict 和現在磁碟上的檔案是否一致**」，而不是「**和 review 過的 manifest 是否一致**」。
- **重現**（只在記憶體中，沒有修改任何檔案）：
  1. 用 patch 模擬 `instructions/L03.txt` 被加上一句沒有 review 過的句子；
  2. 用新的內容重新計算 `instruction_sha256` 和 `user_message_sha256`，填入 dict；
  3. **`verify_registration(forged)` 接受了這個 dict**，`Phase0Wrapper(forged)` 也可以建構；
  4. 同樣的 drift 下，**只有 `load_preregistration(PIN)` 會拒絕**。

  如果再從 `dry_run` 取得新的 body SHA，偽造的 dict 也能通過 admission 時的 Request facts 檢查。
- 現在 live 的路徑（handoff）還沒有寫。依照本次 review 的規則（「If arbitrary registration can bypass the future live authorization path, verdict must be BLOCKED」），**能不能封住 pin 完全取決於將來 handoff 的文字，程式碼本身沒有強制**，而且 test `test_future_runner_integrity_failure_halts_before_transport` 本身就直接用 `Phase0Wrapper(altered_dict)`。所以判定為 **BLOCKED**。

**修正方式（很小，加上 targeted re-review）：**

1. 把建構方式改為 `Phase0Wrapper(expected_manifest_sha256)`（或者 `Phase0Wrapper.from_pinned(sha)`）：在內部呼叫 `load_preregistration(sha)`，並保存 pin 和 raw bytes。不再接受 dict。
2. 在每次 `run_next` 和 `admitted_open` 時，**重新讀取 manifest 檔案，並比對 pin 的 SHA**，然後才呼叫 `verify_registration`。這樣執行到一半時 manifest 或 artifact 被修改，也會被發現。
3. 加入 test：
   - 傳入 dict 會被拒絕；
   - 執行到一半時 manifest 被修改，會產生 `WrapperIntegrityFailure`；
   - 模擬 instruction 被修改並偽造自洽的 dict（就是上面的重現），必須 FAIL。
4. **同一次修正時順便處理（建議）：** `RUNTIME_FILES` 沒有包含實際會被 import 的 `errors.py`（`http_error_code`、`coded_error`）、`diagnostics.py`（safe code 的定義）、`terminal.py`、`providers/base.py`、`providers/__init__.py`。如果這些檔案改變，可能會改變錯誤碼的對應，卻不會讓 manifest 失效。修改 wrapper 本來就需要重新產生 manifest，所以建議一起加入 artifact pin。Request 和 user-message 的 digest 不會因此改變。

## 6. C3-1 provider-response 的 accounting：CLOSED

- **對應：** `MODEL_RESPONSE_INVALID` 和 `MODEL_RESPONSE_TOO_LARGE` 對應到 `TRANSPORT_OR_REQUEST_FAILURE`；`MODEL_OUTPUT_INCOMPLETE` 維持原本的值；不明的 code 會產生 `WrapperIntegrityFailure`，不會被猜測成其他類別。
- **provider boundary 的 test（不只是直接測試對應函式）：**
  - `assert_provider_failure_accounted` 經過**真正的 CLI、client 和 admission**（wrapper 的 `initialize` 把 test 的 fake opener 包在 `admitted_open` 裡面）。
  - L02 和 L03 都涵蓋：invalid JSON、意外的 `finish_reason`、`tool_calls`、`function_call`、空的 content、`content=[]`、太大的回應（只在 test 中把 `RESPONSE_LIMIT` 改為 64，並用 65 bytes 觸發真正的檢查），以及 `finish_reason=length`。
  - 每個案例都斷言：
    - `request_emitted=True`、`status=FAIL`、primary 正確；
    - `usage_total=None`、`complete_total=None`；
    - secondary 包含 `COMPANY_TOKEN_ACCOUNTING_UNKNOWN`；
    - 沒有寫出任何 raw 檔案；
    - opener **只被呼叫一次**，`max_tokens` 是 4096，`timeout` 是 120；
    - **後續每一個 trial 都透過 `runner.run_next()` 執行**，而且都是 `NOT_STARTED / COMPANY_TOKEN_ACCOUNTING_UNKNOWN`，usage 是 None，沒有送出 request；
    - delegation 的總次數仍然是 1；
    - `gate_passed=False`。
  - length 的 primary 維持 `MODEL_OUTPUT_INCOMPLETE`，同時 accounting 變成 unknown，並且全域停止。
- **production 確認：**
  - `OpenAICompatibleClient` 和 CLI 都**沒有 retry**；`retryable` 只是 diagnostic 的旗標。
  - `max_tokens` 只來自凍結的 config。

## 7. Usage accounting：PASS

`validated_usage` 要求三個欄位都存在、型別都是 `int`（排除 bool 和 float）、都 ≥ 0，而且 total = prompt + completion。我測試的 7 個情況：

- 正確 → 通過；
- bool、加總不符、缺少欄位、負數、None、float → 全部是 **None**。

在 `run_next` 中，已送出 request 但 usage 是 None 時，會記錄為 unknown 並且全域停止（`test_complete_success_unknown_usage_halts` 和 `test_future_runner_fake_inconsistent_usage_unknown`）。不會用 0 代替。

## 8. Gold 隔離：PASS

- `user_message`、`dry_run`、`build_preregistration`（gold 的 SHA 直接寫在程式中，不需要開檔）、`admitted_open` 和 L03 的 anchor 建立，都不會讀取 `GOLD`。
- 我用 patch 讓讀取 gold 時直接 raise，然後執行兩個 lane 的 `dry_run`，結果正常，digest 也正確。
- `load_target_metadata` 的順序是：先讀取 raw，**先比對 SHA**，再解析，最後只回傳 `target_ids`。
- 它只會在 `evaluate_raw` 的 L03 分支中被呼叫，而且是在 `evaluate_l03_anchors_without_gold` 產生 `FrozenEvaluation` 之後，以 loader 的形式傳給 `evaluate_l03_target_recall_after_freeze`。
- 有 test 涵蓋這些情況：freeze、construction、adapter 都無法讀取 gold；metadata 的 hash 在 parse 之前檢查；loader 只在 anchor 凍結之後執行。
- gold 不會修補 output 或 anchor（format_probe 已經 review 過）。

## 9. Replay reconciliation／CE10：PASS

- **`complete_success` 的流程：**
  1. 從磁碟讀回保存的 raw bytes；
  2. 呼叫 `reconcile`：
     - `evaluate_raw` 從頭再執行一次（`RawStdout`、adapter、L03 recall）；
     - 要求 `evaluation.stdout.raw == preserved`；
     - 要求 stdout 的 SHA 相同；
     - 要求 **`result_json` 的 bytes 完全相同**，SHA 也相同；
  3. 全部通過之後，才呼叫 `complete_evaluation`。
- **我重現的 CE10：** 先取得一個真正 FAIL 的 L02 evaluation，再把 `passed`、primary、secondary 全部一致地偽造成 PASS，然後經由 `complete_success` 送出。結果是 **`WrapperIntegrityFailure`**，accounting 沒有被寫入（record 仍然是 STARTED）。
- 修改 raw、fixture 或 metadata 都會 fail closed（有 3 個 test；fixture 和 gold 在讀取時都會比對 SHA）。

**附註（non-blocking）：** reconcile 或 secret scan 失敗時，`run_next` 會把 `halted` 設為 True，然後 raise。此時那個 trial 停在 **STARTED 加上 request 已送出，但沒有 usage** 的狀態。實際用掉的 token 不會被記錄（即使 metadata 中有）。這是 fail closed 的行為，可以接受，但 live 的報告必須把這種情況明確寫成「accounting incomplete」。

## 10. Instruction 的契約：PASS

- **L02：** 要求：
  - 每一個 `EVENT event_id=` 行都逐字、只出現一次，一整行作為一個 array item；
  - 沒有前綴、後綴、註解；
  - timeline 依時間排序，相同的 timestamp 用 source-line 排序；
  - error_signatures 只能是名稱；
  - 說明性的 prose 只能放在 summary、repeated_patterns、unknowns、strong_review_reasons；
  - **evidence 和 timeline 中禁止出現 EVT-／REQ- 的 ID，以及 `event_id:`、`request_id:`、`signature:`、`ts:`（或 `=`）**；
  - 不能有 root cause、建議、command 或 self-grade。
- **L03：** 要求：
  - 一筆 record 最多一個 evidence item，但同一個 evidence item 可以支持多個類別；
  - source 和 locator 必須完全相同，不能 trim、case-fold、fuzzy；
  - 不能有 inventory 的 evidence；
  - 不能把 hypothesis 提升為 fact；
  - `record_id` 的文字和 anchor 無關。
- **兩份 instructions 都沒有出現 `REC-` 或 `INV-` 的 ID**（我用 grep 確認是 0；也有 test）。
- **non-blocking：** L03 提到「release/cache hypotheses」。這些內容就在 fixture 中（`REC-H01 kind=hypothesis text=A cache policy change…`），所以不算 gold leakage，但屬於針對這個題目的提示。live 的報告應該揭露這一點，並且不要把結果推論到其他題目。

## 11. Phase0 的順序、不 retry 和預算：PASS

- 順序是 `L02-1, L03-1, L02-2, L03-2, L02-3, L03-3`。manifest 和 `verify_registration` 都檢查這個順序。
- 每個 trial 最多一個 request（第二次 `mark_request_emitted` 會產生 ValueError，delegation 仍然是 1）。沒有 retry 的 API；manifest 中 `retry=false`。
- format 或語意的失敗只停止同一個 fixture；usage unknown 會全域停止。
- 上限是 300,000，L02 每次預留 35,000，L03 每次預留 59,000（由 manifest 和 format_probe 一起檢查）。
- 被跳過的 trial 沒有 proof，usage 是 None。
- `run_next` 在任何 BaseException 之後都會停止，再呼叫也只會 FAIL。

## 12. Raw output 的發布：PASS（附 1 個 handoff 條件）

- 寫入使用 `os.open(O_WRONLY|O_CREAT|O_EXCL, 0o600)`，接著 fsync，然後讀回並比對 bytes 完全相同。
- `RawStdout` 先計算 SHA，再做 scan，最後 parse。
- 如果 scan 命中，會 `require(publication_allowed)`，產生 WIF 並停止。沒有任何遮蔽之後再使用的路徑。
- replay 讀取保存下來的原始 bytes。
- **handoff 條件：** `output_directory` 由呼叫端指定。如果 scan 命中，raw 檔案**已經寫到磁碟上**了。live handoff 必須指定一個**不在 repo 追蹤範圍內**的目錄（例如 scratchpad），等 scan PASS 之後再複製到 `reports/…/raw_outputs/`，以免把含有疑似憑證的輸出誤 commit。

## 判定

```text
PR #38 ROUND-3 C3-B LIVE-WRAPPER REVIEW:
BLOCKED

Exact review head:
94dd567a4da2cf9b14c5c93942b5d74c07c65c85

Exact tested implementation head:
fff00d31f3a6de1ad18c6379cf8977e6fec4f7d9

Pinned preregistration SHA256:
0593f70be08bcd4952f1ab8376b13fa331221be5a26c42c67a218a9cd1aec276

Revision integrity:
PASS — docs-only after fff00d3; wrapper/tests/instructions/manifest/format_probe digests match manifest; all PR files under reports/

Zero-network Request freeze:
PASS — real CLI → real client → real urllib Request → instance opener intercept → BaseException sentinel;
  original opener never called; reproduced under process-wide socket block

Frozen Request/user-message digests:
PASS — L02 e0194460…/cc3c79f8…, L03 3b94f0d8…/6c1f7b77… independently reproduced with gold reads forbidden

Pinned manifest loader requirement:
BLOCKED (B1) — load_preregistration() is correct (raw bytes → SHA before decode → strict JSON → full verify),
  but Phase0Wrapper accepts any dict and verify_registration only checks dict-vs-current-disk self-consistency:
  a drifted instruction + recomputed dict passes verify_registration; only the pin rejects it

C3-1 provider-response accounting:
CLOSED — INVALID/TOO_LARGE → TRANSPORT_OR_REQUEST_FAILURE, length distinct; provider-boundary tests through real
  CLI/client/admission for all 8 cases, both lanes; usage unknown, global stop via run_next, one delegation, no retry

Usage accounting:
PASS

Gold isolation:
PASS

Replay reconciliation / CE10:
PASS — forged internally consistent evaluation rejected by byte-identical replay before accounting

Instruction contracts:
PASS (note: L03 mentions release/cache hypotheses present in fixture; disclose as topic-specific)

Phase0 sequencing/no-retry/budgets:
PASS

Offline regressions:
88/88 helper
77/77 wrapper

New before-live blockers:
B1 — seal the manifest pin in code: Phase0Wrapper(expected_manifest_sha256) loads via load_preregistration,
  rejects dicts, and re-verifies the pinned manifest bytes on every run_next/admitted_open; add tests
  (dict rejected, mid-run manifest drift, drifted-instruction + self-consistent dict). Recommended in the same
  re-freeze: pin errors.py/diagnostics.py/terminal.py/providers/base.py/providers/__init__.py in artifacts.
Handoff conditions: raw output directory outside tracked repo until scan PASS; report STARTED/emitted trials
  left by integrity halts as accounting-incomplete.

May prepare exact live Phase-0 execution handoff:
NO — after B1 is fixed, manifest re-frozen (new pinned SHA) and targeted re-review PASS

May run live Company Phase 0 now:
NO — exact live execution handoff must pin manifest SHA and request one explicit live authorization step
```

- 本報告是這一輪唯一 commit 的檔案。沒有修改實作，沒有連線到 company endpoint，沒有呼叫 Codex，沒有 merge，也沒有開始 A11b.2 或 A11c。
