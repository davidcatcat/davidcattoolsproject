# llmtool PR #38 Round 3 format-compliance plan — targeted methodology re-review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #38，**exact head `345aa00d5828f15db50045c97b4ae0558fc47c5f`**（base `main @ fecbdba`）。
- **差異：** 和我第二份 review（`1133578`）相比，只多了 `345aa00`。這個 commit 只修改 plan 一個檔案：+204／−25。
- **方式：**
  1. **逐行閱讀修訂後的 plan 全文**。沒有依賴 remediation 的摘要。
  2. **用 Round 2 實際的 artifact 檢查新規則是否可行、是否處理到 root cause，而不是只檢查 plan 裡有沒有寫到那些字**。使用的資料：
     - `stage_a/results.json` 中的 company attempt；
     - `preregistration.json` 的 L03 instructions；
     - L02 和 L03 的 fixture 和 gold。
- **沒有做的事：**
  - 沒有呼叫 company 或 Codex；
  - 沒有實作 `format_probe.py`；
  - 沒有修改 plan、fixture、production 或 skill。

## 結論：M1–M3 和 F1–F3 在文字上都已經 CLOSED；另外發現 3 個 root-cause 等級的問題，必須在凍結 L03 instructions 和實作時處理（N1 是 live 之前的 blocker）

## 1. 逐項驗證

| 項目 | plan 中的位置 | 判定 | 說明 |
|---|---|---|---|
| **M1：L02 的 timeline 必須完整** | 第 97–117 行 | **CLOSED** | 涵蓋 evidence 和 timeline 各自 exactly once、timeline 等於完整的預期序列、timestamp 加 source-line tie-break、兩個陣列分別檢查 duplicate、signature 只從已驗證的 evidence 計算一次、完整 tuple（event_id、request_id、signature、timestamp）的驗證、拒絕捏造或欄位錯誤的事件、malformed 或 partial 的項目不能補足 coverage、三種分類。extras 不會影響 count 和 anchor（第 119–129 行） |
| **M2：L03 的 anchor 非空** | 第 143–152、182–188 行 | **CLOSED（附 nit）** | 每一個 trial 都要求：parser PASS、非空、unknown／ambiguous／duplicate 都是 0、source 和 locator 都非空、exact unique。free-text 的 `record_id` 不影響 anchor（第 154、198 行）。multimap 寫在 self-test 第 413 行（「actual frozen-fixture multimap collision counts are preserved (no last-writer-wins)」）。**nit：** 第 144 行仍然寫「an index from exact pair to fixture record」（單數）。建議實作時以第 413 行為準：在完整的 fixture universe 上建立 multimap，而不是只用 gold target 建立 index |
| **M3：6 個 trial、最多 6 個 request、不 retry** | 第 223–264 行 | **CLOSED** | 順序已凍結。每個 trial 最多 1 個 request，連 transport 失敗也不 retry。只有實際送出 request 時才記錄 Request proof。usage 不可以捏造。第一次失敗之後，其餘 trial 標成 `NOT_STARTED_EARLY_FORMAT_GATE_FAIL`。**沒有選擇偏差：** gate 要求 6／6，只要一次失敗，結果就不可能是 PASS；而且只在失敗時停止，從來不會在有利的時候停止，所以 early stop 只會省錢，不會讓結果偏向有利（但見 N2） |
| **F1：L03 的 recall 必須是 7／7** | 第 159–201、296 行 | **CLOSED（附 nit）** | 順序是 output → parse → digest → 從 fixture 推導 anchor → 凍結 → 才載入 gold，而且明確禁止 5 種回饋路徑。只有 fact 的 evidence 會 FAIL，有 self-test（第 414–417 行）。instructions 要求 5 個類別都要有 provenance，並且明確寫出這不是 production A4 的變更（第 201 行）。**Round 2 的實際輸出只有 F01／F02／F03 三個 evidence item，依新規則是 3／7，FAIL**，和預期一致。**nit：** (a) 第 170 行在 parse 之後才 digest，建議改為 stdout 一完成就對 raw bytes 取 digest（parse 不會改動內容，所以不影響結果）；(b) 沒有定義「target recall」的計算方式。建議定義為「已驗證的唯一 anchor 對應到的 record_id 與 7 個 target 的交集」，而不是 statement 中提到了哪些 record |
| **F2：保存 raw stdout，可以 replay** | 第 245–254、324–325、349–364、374 行 | **CLOSED** | 每個 request 都保存完整的 stdout 和 SHA-256，並記錄 logical trial ID。secret scan 失敗會阻止發布。不可以編輯 stdout 之後再拿來判定。replay 不能回頭改變這次的判定，也不能取代新的 live 驗證 |
| **F3：company 300k 的上限** | 第 266–286 行 | **CLOSED（附精確度 note）** | 已凍結。每次 request 之後累加，unknown 不會當成 0，而且 unknown 會讓後續停止。plan 也明確寫出這不是 production 的門檻。**note：** 第 275 行只要求「開始下一個 trial 之前，累計值低於 300k」，所以最後一次 request 理論上可能讓總數超過 300k。第 283 行提到的「separately preregistered hard ceiling」沒有定義。用 Round 2 的實際用量計算最壞情況：L02 是 (29,450 + 4,096) × 3 ≈ 100.6k，L03 是 (53,703 + 新 instructions 大約 0.3k + 4,096) × 3 ≈ 174k，合計大約 **275k，小於 300k**，所以這次實際上不會超過。建議實作時把 pre-check 改為「已知累計值 + 這次的上限 ≤ 300k」，上限 = 該 fixture 在 Round 2 的 prompt tokens + instruction 差額的保守值 + 4,096 |
| **離線 self-test 的規格** | 第 386–432 行 | **PASS** | prompt 要求的 L02、L03 和 accounting／isolation 的每一項都有對應。另外建議補上 N1 的 counterexample |
| **gold 隔離** | 第 57、166–180、347、428–429 行 | **PASS** | |
| **未來的真實格式 fixture** | 第 469–481 行 | **PASS** | 已經從 should 改為 **must**。要求 log 和 mixed-evidence 各一組，不能只靠 canonical 行作為 anchor 的來源，並且要包含多行或 prose、重複或有歧義的 source-locator、negative case，而且 gold、coverage、locator 都要在 live 之前凍結 |
| **經濟決策的 gate** | 第 450–465 行 | **PASS** | 分開列出 Codex 減少的量、company 的用量、mixed 的總量和實際成本。必須先取得單價，或者明確宣告 company 是內部固定成本；也必須寫明目標（成本、配額、延遲或其他）。不同的 tokenizer 不能直接當成成本比較 |

**更正我第二份 review 的 F3 估計：** 我原本寫 L02 每次大約 23k、6 次合計大約 235k。查證 Round 2 的實際值後，L02 是 **31,639**（prompt 29,450，completion 2,189），所以合計應該大約 **257k**，最壞情況大約 275k。300k 仍然足夠，但餘裕只有大約 9%，比我原先估計的少。

## 2. 是否處理到 root cause：額外查證

這份 re-review prompt 列出的檢查項目，**都可以用閱讀 plan 的方式驗證，所以是可行的**。但是這些項目主要確認「plan 有沒有寫到」，沒有確認「這些規則彼此之間、以及和 Round 2 company 的實際行為，是否一致」。所以我額外查證了下列幾點。

### 2.1 L02：放寬是修正量測的 bug，不是為了讓測試通過（OK）

Round 2 L02 的 company 輸出（`results.json`）：

- 4 個事件全部驗證通過；
- request ID 完整；
- 沒有 missing、unexpected、duplicate 或 mismatched；
- timestamp 的順序和 timeline ID 的順序都是 valid；
- canonical signature 的次數（UPSTREAM_TIMEOUT 3、RECOVERY_OBSERVED 1）和 fixture 一致。

FAIL 的原因只是 6 個 evidence extras、18 個 timeline extras，以及多出來的 signature 讓 `signature_count_match=False`。兩位 blind grader 也判定最終品質是 EQUIVALENT。

所以 root cause 是**量測契約錯誤，而不是 company 的錯誤**。新的 subset 契約在放寬的同時，加上了「extras 不影響 count 和 anchor、完整 tuple 驗證、timeline 必須完整」，**沒有放寬正確性的要求**。這次修正是有原則的，不是為了讓結果變綠。

**副作用：** 依 Round 2 的輸出推斷，L02 的 Phase 0 應該會 PASS。所以 Phase 0 在 L02 上主要是在 fresh sample 上**確認契約正確**，資訊量有限；真正有資訊量的是 L03。

另外，Round 2 L02 的 completion 是 2,189 tokens，相當於 max_tokens 的 53%，其中大部分是 noise timeline。這不構成 blocker，但如果 L02 的 Phase 0 出現 length failure，原因很可能是這些冗長的 extras。

### 2.2 L03：F1 處理到 root cause，但規則和 instructions 之間互相矛盾（N1）

F1 正確地把 Phase 0 的目標從「語法上可以解析」改為「**selector 的輸入涵蓋所有必要的 record**」。這才是 Round 2 L03 失敗的 root cause：selected raw 中缺少這些 record，dogfood Codex 就無法回答。

但是，修訂後的規則組合中有兩個衝突。它們會讓 Phase 0 **因為和格式遵守能力無關的原因而 FAIL**：

**N1a：「每個類別項目都要有 evidence」和「duplicate mapped anchor = 0」互相衝突。**

- 第 193 行要求每個 fact、inference、hypothesis、unknown、verify-next 都要有對應的 evidence item。
- 但是 L03 的 inference（例如「threefold increase」）在語意上同時由 F01、F02 和 I01 支持。verify-next 通常也會引用 F02 或 F03。
- 一個認真遵守 provenance 要求的模型，很自然會**對同一個 record 產生兩個 evidence item**（一個支持 fact，一個支持 inference）。第 187 行的 `duplicate mapped anchors = 0` 會讓這種情況 FAIL。
- 但是重複引用**對 Phase 0 的目的（selector 的輸入是否完整、是否有捏造的 provenance）完全無害**，因為 selector 本來就會合併重複的範圍。
- 這條規則是從 Round 2 gold 的 `duplicate_citations_forbidden` 沿用下來的。在 Round 2，那條規則的用途是保持 citation 的唯一性；在 Round 3 的 structured anchor 下，它的目的已經不同。

**N1b：「每個 fact 都要有 evidence」和 inventory 的歧義衝突。**

- Round 2 的 instructions 要求「summarize their irrelevance」（指 inventory）。如果 Round 3 沿用這個要求，模型會產生類似「inventory rows are background」的 fact，而依照第 193 行，這個 fact 必須引用某一筆 inventory record。
- 但是 **661 筆 inventory record 中，有 254 筆（38%）屬於有歧義的 pair**。只要引用到其中一筆，就會被判定為 `AMBIGUOUS_STRUCTURED_ANCHOR`，那個 trial 就 FAIL。
- 這是 **fixture generator 重複使用 source 和 locator 所造成的 artifact**，不是 company 的格式問題。

**為什麼這是 root-cause 問題：** 如果因為 N1a 或 N1b 而 FAIL，Phase 0 會報告 `ROUND3_FORMAT_COMPLIANCE_GATE_FAIL`。但是失敗的原因是 benchmark 自身規則的矛盾。這樣的結果會**錯誤地否定 structured anchor 這個方向**，正是「為了測試而測試」。

**建議（在凍結 L03 instructions 和實作之前，選一個並明確寫下來）：**

- **N1a：**
  - (i) instructions 明確寫出「每一個 record 最多只能有一個 evidence item；同一個 evidence item 可以支持多個類別的項目」，並保留 duplicate = 0；**或**
  - (ii) 把 duplicate mapped anchor 改為「記錄、合併、不判 FAIL」，因為對 selector 沒有影響。

  兩者都是在 live 之前凍結，不是看到結果之後才放寬。我建議 (i)，因為它不需要改 gate，只需要讓 instructions 和 gate 一致。
- **N1b：**
  - (i) instructions 寫出「inventory 的背景說明放在 `unknowns` 或其他不需要 evidence 的位置，或者不要說明；不要引用 inventory 的 record」；**或**
  - (ii) 事先登記：ambiguous 的**非 target** 引用只記錄、不作為 anchor、不判 FAIL（和 L02 對 extras 的處理方式一致）。如果是 target 的 pair 出現歧義，仍然判 FAIL；但 7 個 target 本身都是唯一的，所以實際上不會發生。

  兩個選項都可以。如果選 (ii)，必須加上 self-test。
- **加入 self-test：**
  - 同一個 record 被兩個類別的項目引用（依所選方案判 PASS 或 FAIL）；
  - 引用一筆有歧義的 inventory record（依所選方案判定）。
- **failure attribution：** 每個失敗的 trial 都要記錄**第一個失敗的條件**，例如 parse、unknown、ambiguous（target 或 non-target）、duplicate、recall（缺少哪些 target）、length、transport。這樣 Phase 0 FAIL 的時候可以分辨是「format 的能力」還是「benchmark 的規則」造成的。

**嚴重度：** 這是 live 之前的 blocker，但**不影響**現在開始實作 `format_probe.py` 和離線測試。N1 需要在 instructions 和實作的凍結中解決，然後由下一次的 targeted review 確認。

### 2.3 N2（建議）：全域的 early stop 會讓 L03 完全沒有資訊

- 凍結的順序是 L02-1 → L03-1 → …，而且第一次失敗就停止所有 trial。
- 如果 **L02-1** 失敗（例如 length），**L03 一次都不會執行**。但 L03 才是 Round 3 的主要不確定性。花了大約 32k tokens，卻沒有得到任何 L03 的資訊。
- **建議：** 改為**每個 fixture 各自 early stop**。L02 失敗只停止其餘的 L02 trial，L03 至少仍然執行第一個 trial；反之亦然。
  - 這樣整體的 gate 仍然判定為 FAIL，而且**仍然沒有選擇偏差**（只在失敗時停止，不會在成功時停止）。
  - 最多只多花大約 58k tokens（一次 L03），仍然在 300k 以內，但可以讓兩個 failure mode 都得到診斷資料。
- 這屬於 cost 和診斷價值之間的取捨，不是 blocker。如果維持全域停止，也是合理的選擇，但報告中應該寫明「沒有 L03 的資料」。

### 2.4 N3（note）：F3 pre-check 的精確度

見第 1 節的 F3 列。不構成 blocker；建議在實作時採用「已知累計值 + 這次的上限 ≤ 300k」的判斷方式。

## 3. 對這份 re-review prompt 本身的評估

- **可行性：** 所有檢查項目都可以從 plan 的文字和 Round 2 的 artifact 驗證。我已經逐項完成。
- **有沒有處理到 root cause：**
  - L02 的放寬是修正量測的錯誤，L03 的 F1 是針對 selector 輸入不完整這個真正的 root cause。這兩個方向都正確。
  - 但 prompt 的 checklist 主要確認「條文是否存在」，**沒有要求驗證條文彼此是否矛盾，以及和模型的實際行為是否一致**。N1 就是只看 checklist 會漏掉的問題：每個條文單獨看都正確，合在一起卻會讓 Phase 0 因為 benchmark 自身的規則而 FAIL。
  - 建議下一次 review 實作和 instructions 時，要求審查者**用 Round 2 的實際輸出形狀，以及「認真遵守 instructions 的合理輸出」作為 counterexample，跑過凍結的 gate**，確認 FAIL 只會來自真正的格式、能力或 provenance 問題。

## 判定

```text
PR #38 ROUND-3 TARGETED METHODOLOGY RE-REVIEW:
PASS

Exact head reviewed:
345aa00d5828f15db50045c97b4ae0558fc47c5f

M1 L02 complete timeline:
CLOSED — evidence+timeline exactly-once, full expected sequence, ts+source-line order, per-array duplicates,
  signature counts once from validated evidence, full tuple validation, 3-way classification, extras inert

M2 L03 nonempty structured anchors:
CLOSED — per-trial nonempty, unknown/ambiguous/duplicate = 0, nonempty source+locator, exact unique;
  free-text record_id ignored; multimap required by self-test (nit: line 144 wording is singular)

M3 six logical trials / six physical requests / no retry:
CLOSED — fixed order, <=1 request per trial, no transport retry, failures retained, proof only if emitted,
  early stop after first failure is bias-free because 6/6 is already impossible (see N2 for diagnostic cost)

F1 L03 7/7 recall floor:
CLOSED — gold loaded only after output/anchors frozen, five feedback paths forbidden; Round-2-like 3/7 now FAILS;
  5-category provenance instruction frozen pre-live as benchmark-only (nits: digest raw stdout first; define recall on validated anchors)

F2 raw stdout retention/replay:
CLOSED — verbatim stdout + SHA-256 + trial ID, secret scan blocks publication, no edited-output gating,
  replay diagnostic-only and never retroactive or a substitute for live validation

F3 300k company-token ceiling:
CLOSED — provider-reported, accumulated per request, unknown never zero and stops execution, not a routing threshold;
  note: pre-check is "below 300k before start"; worst case ~275k so not exceeded; use cumulative + per-trial bound

Offline self-test specification:
PASS — all requested L02, L03 and accounting/isolation cases present; add N1 counterexamples

Gold isolation:
PASS

Future realistic-format requirement:
PASS — now mandatory (log + mixed-evidence), non-canonical anchor routes, frozen before live

Economic decision gate:
PASS — Codex/company/mixed/monetary separated; pricing basis or declared objective required

New blockers:
N1 (before live; not blocking offline work) — L03 rule/instruction inconsistency:
  (a) "evidence for every category item" + "duplicate mapped anchors = 0" fails benign multi-use citations;
  (b) "evidence for every fact" + 254/661 ambiguous inventory records fails on a fixture artifact.
  Resolve in the frozen instructions/gate (one record = one evidence item; no inventory citations, or
  non-target ambiguous recorded-not-fatal) + counterexample self-tests + first-failure attribution.
Recommendations: N2 per-fixture early stop for diagnostic value; N3 cumulative+per-trial ceiling pre-check

May implement format_probe.py + offline self-tests:
YES

May run live six-company-call Phase 0:
NO — implementation/self-tests must still be frozen and independently reviewed first
```

- 本報告 commit 到 PR #38 的 branch。這一輪沒有修改 plan，沒有呼叫 company 或 Codex，沒有實作 probe，沒有 merge，也沒有開始 A11b.2 或 A11c。
