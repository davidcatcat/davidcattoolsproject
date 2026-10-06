# llmtool PR #38 Round 3 offline implementation — 獨立 review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #38，**exact review head `a662b6ed9efdd7f7c9fc267fe929f21eb28be626`**。實作的測試是在 `29bd0dd92db203532f119a3b4c77be476454ab87` 執行的。
- **逐行閱讀：**
  - `format_probe.py`（476 行）；
  - `test_format_probe.py`（678 行，79 個 test）；
  - README；
  - offline validation report；
  - 修訂後的 plan（`d678c6d` 的差異）；
  - 兩份 handoff。

  另外對照了 production 的 `openai_compatible.py`、`context.py`、log-triage `SKILL.md`，以及 Round 2 的 `coverage.py` 和 `results.json`。
- **重跑：** 在 scratchpad 用 `/usr/bin/python3 -B`（**Python 3.9.6**）執行，結果是 **79 個 test，OK**。
- **自行撰寫 counterexample：** 寫了 17 個離線的 counterexample（CE1–CE17）。只使用記憶體中的資料，沒有寫入 repo，也沒有使用網路。
- **沒有做的事：** 沒有呼叫 company 或 Codex，沒有修改實作、fixture、gold、production 或 skill，沒有 merge。

## 結論：APPROVE WITH CHANGES

- 核心的 adapter、multimap、freeze、recall 和 accounting 的邏輯都正確。
- 但是有 **2 個在 live 之前必須修正的問題**：
  - **C1：** accounting 和 adapter 的判定沒有綁定。我實際做出了一個 L03 只有 3／7 的情況，`gate_passed` 卻是 True。
  - **C2：** L02 判斷「canonical-looking」的 regex 兩個方向都會判錯。
- 另外還有幾個在 wrapper 階段必須滿足的條件（C3）。

## 1. Revision integrity：PASS

- `29bd0dd..a662b6e` 之間只有兩個 docs 檔案：validation report 和 review handoff。`git diff 29bd0dd HEAD -- reports/token_benchmark/` 是空的。
- 三個 artifact 的 SHA-256 和 validation report 記錄的值完全一致：
  - `format_probe.py`：`99cebcb4…`
  - `test_format_probe.py`：`f8a756b4…`
  - `README.md`：`d0307ba1…`
- PR #38 相對於 `fecbdba` 的所有變更都在 `reports/` 底下，沒有 production、runtime、skill 或 workflow 的變更。

## 2. L02 adapter：核心 PASS，但 classifier 需要修正（C2）

**正確的部分（逐行確認）：**

- **exact tuple：** 用 `EVENT.fullmatch` 並且要求和 fixture 的整行文字 `==`，所以 event_id、request_id、signature、ts 和空白都必須完全相同。
- **完整與順序：** evidence 和 timeline 都必須包含整個 fixture EVENT universe。這裡使用全部 4 個事件，而不是 signature 的白名單。
- **timeline：** `validated['timeline'] == expected`。這是**完整的 list 相等**，同時保證了完整、順序（timestamp + source-line）和沒有重複。
- **duplicate：** 兩個陣列分別檢查，而且連無效的 claim 也會計入 duplicate。
- **signature count：** 只從已驗證的 evidence 計算，並和 fixture 比對。
- **extras：** 不影響 count 和 anchor。extra signature 會記錄但不判 FAIL；缺少必要的 signature 則判 FAIL。
- **Round 2 extras 的形狀：** 6 個 evidence extras 加上 18 個 timeline extras 判定為 PASS。這和我上一輪 re-review 的推論一致。

**counterexample：應該 FAIL 卻 PASS，或者應該 PASS 卻 FAIL。**

| # | 輸入（加在完整、正確的 log output 上） | 結果 | 判斷 |
|---|---|---|---|
| CE3 | evidence 加入 `event_id: EVT-T99 signature: UPSTREAM_TIMEOUT`（冒號形式、捏造的 ID） | **PASS**（被當成 extra） | **應該 FAIL**。因為 `CANONICAL_LOOKING` 只認得 `=`，這是 canonical 欄位格式的捏造 claim |
| CE1 | `EVT-T09 UPSTREAM_TIMEOUT on REQ-99 at 08:02:20Z`（prose 中捏造的 event／request ID） | PASS | plan 允許 prose 作為 extra，但它引用了 **fixture 中不存在的 ID**。建議至少產生 diagnostic |
| CE2 | timeline extra `EVT-T01 REQ-17 timed out at 09:30:00Z`（真實的 ID，錯誤的時間） | PASS | 同上：內容和 fixture 矛盾的 prose 被當成無害的 extra |
| CE5 | `event EVT-T01 repeated`（小寫） | PASS | 同上 |
| **CE16** | evidence 加入 `Four EVENT lines were observed; no later EVENT lines.`（正確、無害的 prose） | **FAIL**（`MALFORMED_OR_PARTIAL_EVENT`） | **不應該 FAIL**。`\bEVENT\b` 只要看到這個字就判定為 canonical claim |
| CE17 | 同一句話放在 `repeated_patterns` | PASS | 只檢查 evidence 和 timeline，所以同一句話放在哪一個欄位，會決定 PASS 或 FAIL |
| — | fixture 自己的註解行 `# Canonical EVENT records preserve …` | 如果 company 引用這一行就會 FAIL | 會因為 fixture 自己的文字而 FAIL |
| CE4 | canonical 行後面加上 `  (first timeout)` | FAIL | 依照「完整原文」的契約，FAIL 是合理的。但 **L02 的 instructions 必須明確寫出「逐字、一行一個 item、不要附加註解」** |

**C2（live 之前）：** classifier 是啟發式的，而且兩個方向都會判錯。

- 會漏掉的：CE3。
- 會誤判的：CE16，以及 fixture 的註解行。

這直接影響 Phase 0 失敗原因的判斷：CE16 這種 FAIL 是 benchmark 自身的 regex 造成的，正是上一輪 N1 想避免的情況。

**建議（在 live 之前凍結，並且加上 test）：**

1. canonical-looking 改為：出現任何 fixture 欄位名稱加上 `[:=]`（不分大小寫，例如 `event_id`、`request_id`、`signature`、`ts`），或者出現 `EVENT\s+event_id`。單獨的 `EVENT` 字樣當成 extra。
2. 新增一個 diagnostic：在 evidence 和 timeline 的 extras 中，如果出現 fixture namespace 的 ID（`EVT-…`、`REQ-…`），而那個 ID **不在 fixture 中**，就記錄為 `unknown_identifier_reference`。是否判 FAIL 必須事先登記；我建議判 FAIL，因為這是捏造 provenance，而且檢查成本很低。
3. 把 CE3、CE16、CE1 和 fixture 註解行，加入 test 作為 counterexample。

## 3. L03 structured-anchor adapter：PASS

- **multimap：** `build_l03_multimap` 使用 `defaultdict(list)`，以完整的 fixture universe 建立 `(source, locator) → tuple(all matches)`。沒有 last-writer-wins，也沒有 gold index。重算的 cardinality 是 **668／534／120／254**，和 frozen 的數字一致。
- **exact 比對：** `index.get((item.source, item.locator))` 是精確比對：
  - null 或缺少 locator 會變成 `(src, None)`，查不到，判為 UNKNOWN；
  - 結尾多一個空白也是 UNKNOWN（CE9）；
  - 沒有 trim、大小寫或模糊比對。
- **free-text：** statement 的內容完全沒有用到，所以 free-text 的 `record_id` 不能決定 anchor，也不能覆蓋 provenance。
- **分類：** 0 筆符合判為 UNKNOWN，大於 1 筆判為 AMBIGUOUS（並列出所有 record），同一個唯一的 record 出現兩次判為 DUPLICATE，空的 anchor set 判為 EMPTY。
- **inventory 不能滿足 recall：** 唯一的 inventory 不會進入 anchor，有歧義的 inventory 也不會。CE8：7 個 target 加上 1 筆唯一的 inventory，結果是 FAIL。
- **一個 evidence 支持多個類別：** PASS。兩個 evidence 對應到同一筆 record：FAIL。這和修訂後的 plan 一致。
- **non-blocking（attribution）：** 唯一的 inventory 會以 primary `UNKNOWN_STRUCTURED_ANCHOR` 回報（CE8）。README 已經說明這代表「不符合 anchor 的資格」，而且 `inventory_evidence` 這個 secondary 資訊也有保留。但「捏造的 provenance」和「違反 instructions、引用背景資料」共用同一個 primary label，會讓失敗原因的判斷變得模糊。建議下一次修改 plan 時，加入獨立的 `INVENTORY_EVIDENCE_CITATION`。目前可以接受。

## 4. Post-freeze 7／7 recall：函式本身 PASS；整合的接縫見 C1

**函式內部的保證：**

- **順序：** `RawStdout.__post_init__` 先計算 SHA-256，再做 scan，parse 在其後 → `_freeze` 把結果序列化成不可變的 `result_json` → `evaluate_l03_target_recall_after_freeze` 先檢查型別、lane 和 stage，**之後才呼叫** loader。
- **recall 的計算方式：** `validated_unique_anchor_record_ids ∩ targets`。statement 的內容不計入。
- **3／7 一定 FAIL**（CE7）。7／7 PASS 的前提是 anchor stage 沒有失敗。
- **先前的失敗不會消失：** `reasons = list(secondary_failure_reasons)` 之後再加上 recall 的結果。我驗證了 UNKNOWN 加上 7／7 時，primary 仍然是 UNKNOWN（CE8、CE9）。
- **不能重複評分：** 有 stage 的檢查。target metadata 必須是 7 個不同的字串。

**有辦法把 FAIL 變成 PASS 嗎？**

- **CE10（low）：** `FrozenEvaluation` 是公開的 dataclass。直接建構一個偽造的 `result_json`（宣稱有 7 個 anchor），即使對應的 stdout 實際上只有 3／7，recall 也會 PASS。
  - 這只會發生在 wrapper 刻意繞過的情況。但「frozen」目前是靠慣例，而不是靠驗證。
  - **建議：** 最後的 reconciliation 要從保存的 raw stdout 加上 fixture，**重新執行 anchor stage**，並且確認 `result_json` 完全相同（也就是對 `anchor_result_sha256` 做 replay 驗證）。
- **CE11（見 C1）：** 這是更實際的路徑。

## 5. Gold isolation：PASS（附 test 強化建議）

- **實作：** 我逐行讀過 `format_probe.py`。只 import 了 `hashlib`、`json`、`re`、`collections`、`dataclasses`、`datetime`，以及在函式內 import 的 `llmtool.terminal`、`llmtool.context`、`llmtool.errors`。沒有任何檔案、網路或 process 的 I/O，沒有 global 的 gold，也沒有 import 的副作用。
- **函式參數：** 在 pre-freeze 階段的函式，參數都只有 stdout 和 fixture records。只有 `evaluate_l03_target_recall_after_freeze` 會收到 loader。
- **test 的缺口（non-blocking）：**
  - 靜態 guard 禁止的 import 只有 `os`、`pathlib`、`socket`、`subprocess`、`urllib`、`requests`、`http`；被檢查的呼叫名稱只有 `open`、`exec`、`eval`，以及 attribute 形式的 `read_*`、`write_*`、`send`。
  - **`import io; io.open(...)`、`codecs.open`、`importlib` 和 `__import__` 都能通過**。runtime 的 patch 只替換了 `builtins.open` 和 `Path.open`，不會影響已經綁定的 `io.open`。
  - 目前的程式碼沒有這樣的呼叫，所以這不是 defect。建議把禁止的 import 擴大為白名單（只允許上面列出的模組）。

## 6. Raw stdout／replay：PASS

- `type(raw) is bytes`，而 bytes 本身就不可變。SHA-256 在 scan 之前計算，parse 在兩者之後。
- scan 只回傳類別，不回傳匹配到的內容，也不修改 bytes。
- replay 會產生新的結果，原本的 `result_json` 不會改變，有 test 覆蓋。
- README 明確寫出 scanner 是「synthetic 輸出的 publication guard，不是通用的 secret detector」。範圍定義正確。

## 7. Request observer：helper 本身 PASS，但 wrapper 有缺口（C3）

**已經確認：**

- **endpoint：** 和 production `openai_compatible.py:80` 的 `base_url + "/v1/chat/completions"` 一致。
- **body 的檢查：**
  - POST；
  - model 必須是 `'gw'`；
  - `chat_template_kwargs` 必須**完全等於** `{'enable_thinking': False}`，**而且**值必須 `is False`。JSON 的 `0` 會被 dict 的相等比較視為等於 False，但 `0 is False` 不成立，所以會被拒絕；
  - temperature 必須是 int 或 float，而且等於 0。JSON 的 `false` 會被拒絕；`0.0` 和 `-0.0` 會被接受，數值上都是 0，可以接受；
  - max_tokens 必須是 int，而且等於 4096。`true` 會被拒絕；
  - 重複的 JSON key 會被拒絕，所以不能用重複的 key 遮蔽某個值；NaN 也會被拒絕。
- **Authorization：** 不分大小寫都會被拒絕。`header_items()` 也包含 unredirected 的 header。
- **不修改 request：** 只讀取 `data` 和 `header_items()`，有 deepcopy 的 test 確認。

**C3 的一部分（wrapper 階段）：**

- **CE13：** observer **不檢查 `messages` 的內容**，也不檢查 `stream` 等額外的欄位。任意的 prompt 加上 `stream: true` 都會被接受。Round 2 的 observer 有記錄 `user_message_sha256`，並且和凍結的輸入比對。live wrapper 必須補上：「user message 的 SHA 必須等於凍結的 instructions 加上 fixture 的 digest；沒有 stream，也沒有其他未預期的 top-level 欄位」。
- **CE12：** `mark_request_emitted` 接受**任何形狀正確的 dict**，沒有綁定到 observer 實際產生的結果（見 C1）。

## 8. Phase0Accounting：狀態機 PASS，但和判定沒有綁定（C1）

**已經確認：**

- **順序：** 依呼叫次數決定，順序固定。每個 trial 最多 1 個 request；呼叫兩次 `mark_request_emitted` 會被拒絕；沒有 retry 的 API。
- **per-fixture stop：** 一個 lane FAIL 只會停止同一個 lane，另一個 lane 繼續。有 test。
- **unknown：** 已送出 request 但 usage 是 None 時，`_accounting_unknown` 會被設定，該 trial 判為 FAIL，之後所有的 trial 都標成 `COMPANY_TOKEN_ACCOUNTING_UNKNOWN`。NOT_STARTED 的 usage 是 None。
- **上限：** 檢查方式是 `known + bound > 300000` 時 NOT_STARTED，所以剛好等於上限時允許。CE15：在 247k 時，L03（+59k = 306k）被判為 NOT_STARTED。
- **超出上限：** 實際的超出量會保留，不會被裁切；`gate_passed` 要求 `≤ 300000`。如果所有 trial 的實際用量都在各自的上限內，最壞情況是 L03-3 開始前已經用了 223k，加上 59k = 282k，所以不會被上限擋下。
- **未送出的 trial：** 只能記錄為 TRANSPORT，usage 只能是 None。
- **`gate_passed`：** 必須有 6 筆紀錄、沒有進行中的 trial、usage 不是 unknown、沒有超過上限，而且每一筆都是 PASS 並且已送出 request。

**C1（live 之前，最重要）：判定和 accounting 之間的接縫**

- `complete(trial_id, failure_reasons, usage_total)` 接受**呼叫端自行提供的 reasons**。它不接收 `FrozenEvaluation`，不檢查 lane 是否一致，也不要求 L03 已經經過 recall 的階段。
- **CE11：** 6 個 trial 中，L03 的 anchor 都只有 3／7。如果 wrapper 誤把 **anchor stage 的 `secondary_failure_reasons`**（空的）傳給 `complete()`，最後 **`gate_passed` 會是 True**。
- README 寫了「An anchor-stage PASS is not an L03 Phase-0 PASS. The caller must run the post-freeze recall」，但**這只是文件上的約定，程式碼沒有強制**。這正是 Round 2 L03 失敗的路徑（anchor 不完整，卻繼續往下執行），只是換到了 accounting 這一層。
- **修正方式（建議放在 `format_probe.py`，並且加上離線 test）：**
  1. `complete()` 改為接收 **final** 的 `FrozenEvaluation`：L02 接收 `evaluate_l02_without_gold` 的結果，L03 只接收 `stage == 'phase0_l03_target_recall'` 的結果。檢查 lane 必須和 trial 一致；reasons 一律從 evaluation 推導，不接受呼叫端提供；並且把 `raw_stdout_sha256` 和 result digest 寫入 TrialRecord。
     - transport 失敗或 length 失敗這種沒有 stdout 的情況，另外提供一個明確的入口，只允許 `TRANSPORT_OR_REQUEST_FAILURE` 和 `MODEL_OUTPUT_INCOMPLETE`。
  2. `observe_request_without_mutation` 回傳一個不可變的 `RequestObservation` 型別；`mark_request_emitted` 只接受這個型別（處理 CE12）。
  3. 加入 test：
     - anchor-stage 的結果傳給 `complete()` 會被拒絕；
     - L02 的 evaluation 用在 L03 的 trial 會被拒絕；
     - 呼叫端自行提供的空 reasons 不能覆蓋 FAIL 的 evaluation；
     - 自行建構的 dict 不能當成 proof。

## 9. Failure attribution：PASS

- `FAILURE_PRECEDENCE` 和 plan 中凍結的 9 個條件（加上 PASS）順序完全一致。`primary_failure` 會拒絕不在清單中的值。
- secondary 依照固定順序全部保留，recall 階段也會繼承先前的 secondary（CE8：primary 是 UNKNOWN，recall 的資訊仍然保留）。
- `TRANSPORT_OR_REQUEST_FAILURE` 和 `MODEL_OUTPUT_INCOMPLETE` 只能由 wrapper 判定（adapter 看不到 finish_reason）。這要納入 C3。
- 例外：唯一的 inventory 會被歸類為 UNKNOWN（見第 3 節，non-blocking）。

## 10. Offline tests：大致是真正的 adversarial test，但有缺口

- **確實有對抗性的部分：**
  - wrong tuple 的每個欄位，在兩個陣列都有測試；
  - empty、partial、reorder 的 timeline；
  - 兩個陣列分別測試 duplicate；
  - 真實 fixture 的 ambiguous pair，加上 free-text 無法解決歧義；
  - 3／7 對 7／7；
  - inventory 唯一或有歧義的情況；
  - per-fixture stop；
  - unknown usage；
  - 剛好等於上限；
  - observer 的 `0`／`False`／`True` coercion；
  - deepcopy 確認沒有修改；
  - post-freeze 的 loader 時序和 detached copy。

  這些 test 都使用真實的 frozen fixture，不是 tautology。
- **缺口：**
  - **C1 的接縫**完全沒有被測試（CE11、CE12）；
  - **C2 的 classifier 誤判**（CE3、CE16、fixture 註解行）；
  - 靜態 I/O guard 可以被 `io.open` 等方式繞過；
  - CE10 的 reconciliation（可以留到 wrapper 階段）。

## C3：live wrapper 階段必須滿足的條件（不是這次 helper 的 defect）

1. user message 的 SHA 必須等於凍結的 prompt digest；不能有 stream，也不能有未預期的 top-level 欄位（CE13）。
2. 必須判定 transport 和 length（`finish_reason=length`），並對應到 primary 的前兩個條件。
3. 最後的 reconciliation：從保存的 raw stdout 加上 fixture 重新執行 adapter，確認和凍結的 `result_json` 完全相同（CE10）。
4. 先確認 evaluation metadata 的 digest，再呼叫 loader。
5. 凍結的 L02 instructions 必須要求「EVENT 行逐字、一行一個 item、不要註解，敘述放在其他欄位」（CE4、CE16）。L03 的 instructions 依照 plan 的 non-duplicating contract 和「不要引用 inventory」。

## 判定

```text
PR #38 ROUND-3 OFFLINE IMPLEMENTATION REVIEW:
APPROVE WITH CHANGES

Exact review head:
a662b6ed9efdd7f7c9fc267fe929f21eb28be626

Revision integrity:
PASS — 29bd0dd..a662b6e docs-only; artifact SHA-256 match validation report; all PR files under reports/

L02 adapter:
finding (C2) — exact tuple/full ordered timeline/per-array duplicates/evidence-only counts are correct, but the
  canonical-looking classifier misfires both ways: `event_id: EVT-T99 signature: ...` passes as a harmless extra (CE3),
  while benign prose containing the word EVENT fails coverage (CE16; also the fixture's own comment line);
  fabricated fixture-namespace IDs in prose (EVT-T09/REQ-99) pass silently (CE1/CE2)

L03 structured-anchor adapter:
PASS — full-universe multimap (668/534/120/254 re-verified), exact matching, UNKNOWN/AMBIGUOUS/DUPLICATE/EMPTY,
  free text ignored, inventory excluded; non-blocking: unique inventory reported as UNKNOWN primary

Post-freeze 7/7 recall:
PASS (function) — digest→parse→anchors→freeze→loader order, recall from validated anchors only, prior failures persist;
  low: public FrozenEvaluation can be forged (CE10) → wrapper reconciliation must replay from raw stdout

Gold isolation:
PASS — no I/O in adapters; only post-freeze loader receives metadata; test guard bypassable via io.open/codecs/importlib (harden)

Raw stdout / replay:
PASS

Request observer:
PASS (helper) — exact endpoint/POST/gw/0/4096/enable_thinking is False/no Authorization, bool/int coercion rejected, non-mutating;
  wrapper must add user-message digest and no-stream/extra-field checks (CE13)

Phase0Accounting:
finding (C1) — state machine correct (order, 1 request, per-fixture stop, unknown, 35k/59k admission, boundary, no clamp),
  but complete() trusts caller-supplied reasons and mark_request_emitted() any well-shaped dict:
  six L03 3/7 anchor-stage results recorded via complete() yield gate_passed=True (CE11/CE12)

Failure attribution:
PASS — precedence matches plan, secondary retained; transport/length attribution belongs to the wrapper

Offline tests:
finding — genuinely adversarial for most cases, but no coverage for the C1 seam, the C2 classifier misfires,
  or io.open-style guard bypass

New blockers:
C1 (before live) bind Phase0Accounting.complete()/mark_request_emitted() to final FrozenEvaluation / observer type;
C2 (before live) fix L02 canonical-looking classifier both directions + unknown-ID diagnostic, with tests;
C3 (wrapper stage) user-message digest, transport/length attribution, raw-stdout replay reconciliation, metadata digest,
  verbatim-EVENT L02 instructions

May prepare live Phase-0 execution wrapper/handoff:
YES — with C1 and C2 fixed and re-tested in format_probe.py first, and C3 built into the wrapper

May run live company Phase 0 now:
NO — live execution wrapper, instructions, configuration, digests and Request-observation
contract must still be frozen and reviewed separately
```

- 本報告 commit 到 PR #38 的 branch。這一輪沒有修改實作，沒有呼叫 company 或 Codex，沒有修改 fixture 或 gold，沒有 merge，也沒有開始 A11b.2 或 A11c。
- counterexample 的 script 只放在 session 的 scratchpad，沒有加入 repo。
