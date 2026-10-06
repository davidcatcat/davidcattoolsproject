# llmtool PR #37 Round 2 Stage A — review、兩位 blind grader 結果與建議（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #37，branch `bench/llmtool-token-savings-large-evidence-20261006`，**head `a63a84b`**（base `main @ 65df409`）。
- **本輪新增的 commit：**
  - `6858ad6`：Stage A execution handoff
  - `a63a84b`：record round 2 Stage-A measurements
- **變更：** Stage A report、handoff、`execution_helper.py`、`grading_helper.py`、`results.json`、`blind_grading/`（6 個匿名答案和 README）。沒有修改 production。
- **方式：**
  1. **在閱讀 Stage A report 之前**，先啟動**兩位彼此獨立的 blind grader**（subagent）。它們**只能讀取** `blind_grading/` 的 README、6 個匿名答案，以及 L01–L03 的 `fixture.txt`、`gold.json`、`output_schema.json`、`task.txt`；不能讀 results、report、preregistration、helper、git 或 PR。兩位都被要求**忽略答案中的 provenance 字眼**，不推測是哪一種方法產生的答案。grader #2 以相反的順序評分（L03→L01，B 在 A 之前）。
  2. 閱讀 Codex 的 Stage A report，並對照 plan、preregistration 和我先前的 review。
- 這一輪我沒有呼叫 Codex 或 company，也沒有執行 Stage B。

## 結論

**Stage A 的執行符合事先登記的契約。兩位 blind grader 的結果完全一致：L01 是有效而且 quality-approved 的 pair。**

```text
Quality-approved valid pairs: 1 (L01)
L01: CONTROL 34,235 vs DOGFOOD 20,604 -> codex_tokens_saved +13,631 (+39.82%), n=1, descriptive only
L02: quality EQUIVALENT but INVALID_FOR_SAVINGS_CLAIM (COMPANY_TRIAGE_COVERAGE_MISS)
L03: INVALID_FOR_SAVINGS_CLAIM (COMPANY_TRIAGE_COVERAGE_MISS + QUALITY_PARITY_FAIL)
Stage B trigger (pre-registered): MET for L01 only — requires separate explicit authorization
Can claim generalized Round-2 Codex savings: NO (n=1, one valid pair, synthetic canonical fixtures)
```

## 1. 兩位 blind grader 的結果

| Fixture | Grader #1 | Grader #2 | pair parity（兩位一致） |
|---------|-----------|-----------|------------------------|
| L01 | A PASS／B PASS | A PASS／B PASS | **EQUIVALENT** |
| L02 | A PASS／B PASS | A PASS／B PASS | **EQUIVALENT** |
| L03 | A PASS／**B FAIL** | A PASS／**B FAIL** | **A BETTER** |

- **一致度：** 6 個答案的判定 6／6 一致，3 對的 parity 3／3 一致，**不需要 tie-break**。
- **L01：** 兩個答案的結構完全相同：3 組、4 個 ID 各出現一次、kind、count、原文 evidence、3 個 unknowns。唯一的差異是 unknowns 有沒有「UNKNOWN」前綴，兩位都判定語意等同。
- **L02：** 4 個 event tuple、次數、request 集合都相同；3 個 gold unknowns 都有；沒有 causal claim 或指令。A 有一句提到 fixture 以外的資料（provenance 字眼），兩位都依指示忽略，不影響判定。
- **L03：**
  - A：7 個目標的引用（record_id、source、locator）完全正確，3 個 fact、inference、hypothesis、unknown、verify_next 都 MET；多出的一筆 `INV-0-00000` 是真實的紀錄，被判定不違規。
  - B：facts 和 evidence 都是空的（0／7 引用），把 fact 降級成「未驗證」，並且出現不符合 fixture 的「no record_id supplied」敘述，以及自我評分的句子 → FAIL。
  - 這和 deterministic 的結果一致（DOGFOOD 的結構化 gold FAIL）。
- **blind 的限制：**
  - provenance 字眼出現在 L02-A 和 L03-B（兩位都指出，並忽略）。這兩對本來就是 INVALID，所以不影響結論。**L01 的答案沒有任何 provenance 字眼，blind 完整**。
  - grader #2 揭露自己對允許的 L03 fixture 執行了 1 次唯讀的 shell 指令（`wc`／`head`／`grep`），沒有讀取其他檔案。grader #1 沒有任何偏差。

## 2. Stage A 執行的確認（PASS）

| 項目 | 結果 |
|------|------|
| 凍結的 artifact | 16 個雜湊在執行前後都相同；preregistration 的 SHA 已固定；沒有事後修補 |
| Codex | 6 個 session 都是 0.159.2、fresh、ephemeral、單一 turn；6 個不同的 thread；欄位集合一致；**0 次 tool call**（符合「直接注入」的 M1 規則） |
| company | 3 個 request 都**在實際的 Request 上證明 `enable_thinking=false`**、temperature 0、max_tokens 4096、沒有 Authorization；每個 fixture 1 次嘗試、0 次重試、沒有 length 失敗 |
| 預算 | 每一對完成後才扣除；全部 3 對都有開始；Stage A 實際用量 **196,116**（forecast 391,083，上限 400k，剩餘 203,884） |
| 標記 | L02 和 L03 依凍結的規則標成 `COMPANY_TRIAGE_COVERAGE_MISS`；L03 另外標成 `QUALITY_PARITY_FAIL`；沒有修改或重跑 |
| 誠實度 | report 明確寫出 L02 和 L03 的差額「只是為了透明而列出，不能用來宣稱節省」；mixed delta 只是描述；preflight 和 Stage A 的總花費都有揭露 |

## 3. 數字和解讀

| Fixture | Control | Dogfood | Codex 差額 | Company | mixed delta（描述） | 有效性 |
|---------|--------:|--------:|-----------:|--------:|--------------------:|--------|
| L01 | 34,235 | 20,604 | **+13,631（+39.8%）** | 19,675 | −6,044 | **有效，quality-approved** |
| L02 | 40,253 | 21,691 | +18,562（+46.1%） | 31,639 | −13,077 | INVALID（coverage contract） |
| L03 | 59,152 | 20,181 | +38,971（+65.9%） | 54,075 | −15,104 | INVALID（沒有選到 evidence，品質 FAIL） |

- **和 preflight review O2 的推估相符：** 推估 L01 約 35%、L02 約 42%、L03 約 55%；實測 Codex 的差額是 40%、46%、66%。Round 2 的設計（大型 evidence、selective injection、0 次 tool call）**確實能減少 Codex 的用量**。
- **但是只有 L01 是有效的節省證據。** L03 的「66%」是因為 dogfood **什麼 evidence 都沒拿到**，品質也失敗，所以這是**無效**的數字。
- **mixed volume 三對都是負的**（O3 的預測成立）：token 是從 Codex **轉移**到 company model。實際划不划算取決於兩邊的單價，必須另外用可信的定價計算。
- **實驗本身的成本：** Codex 端是 preflight 394,231 加上 Stage A 196,116，合計 **590,347**；company 端是 399,425。換到的是 **1 個有效的 n=1 pair，節省 13,631 tokens**。這是建立方法和基準的一次性成本；preflight 的資料在相同的版本和設定下可以重用。

## 4. 失敗原因分析（給下一輪設計參考）

### F1：L02 的失敗是 benchmark 契約和 skill 用途衝突，不是 company 的能力不足

- company 正確引用了 4 個 canonical event，次數也正確（3 + 1）。但 `log-triage` 這個 skill 的本意是「整理 log 中的 error signature」，所以它**同時列出了 inventory 的 WARN／ERROR**（6 個非 canonical 的 evidence、18 個非 canonical 的 timeline）。
- 凍結的契約要求「每一項都必須是完整的 canonical EVENT 行」，所以判定 `signature_count_match=false`，結果是 MISS。
- **dogfood 最後的答案品質和 control 相同**（兩位 grader 都判定 EQUIVALENT），表示 selector 選到的 4 個 event 已經足夠。
- 這次依規則判定為 INVALID 是正確的（不能事後放寬），但下一輪的契約應該修正。

### F2：L03 的失敗是「依賴 company 輸出特定的自由文字 token」，加上「空的 anchor 仍然繼續執行」

- company 的 A4 packet 可以被解析，但 evidence statement 裡**沒有**放入要求的 `record_id=` 字串 → 0 個 anchor → selector 接受空的集合，選出 0 bytes → dogfood 的 Codex 在沒有任何原始 evidence 的情況下作答 → 品質失敗。
- **正面的觀察：** dogfood 的 Codex 沒有捏造 fact（facts 和 evidence 都是空的，並把數值標成「未驗證」），保守的行為是對的。只是它寫出「no record_id supplied」這種只對它自己的輸入成立、但對 fixture 不成立的句子。
- **設計上的問題：** (a) 用自由文字中的 token 當 anchor 太脆弱，A4 packet 本身就有**結構化的 `source`／`locator` 欄位**可以使用；(b) coverage 已經失敗、anchor 是空的時候，仍然執行 dogfood 的 Codex session，**浪費了大約 2 萬 tokens**，也產生了一個必然無效的 pair。

## 5. 建議

### 立即（PR #37）

1. **把兩位 grader 的結果併入 Stage A report 和 `results.json`：** L01 quality-approved（2／2 一致）、L02 品質 EQUIVALENT 但 INVALID、L03 A BETTER（DOGFOOD FAIL），並更新 quality-approved aggregate（**只有 L01**：+13,631／+39.82%）。引用本報告，以及兩位 grader 的「read only the permitted files」聲明（含 grader #2 揭露的偏差）。原始的 deterministic 結果照實保留。
2. **Stage B：** 依事先登記的觸發條件（品質相當、accounting 和 delivery 都有效、≥ 10%），**只有 L01 符合**。只能跑**一對**順序相反的 L01（上限 200k，預估大約 5.5 萬），結果**必須和 Stage A 分開報告**，不能合併成無偏的估計，也不能當成門檻。這需要**另外取得明確授權**。如果只是想確認 L01 的方向，Stage B 的成本效益合理；如果目的是建立 routing 政策，Stage B 也不夠，需要 Round 3。

### 下一輪（Round 3，所有修改都必須在執行前事先登記，不能套用到 Round 2 的結果上）

3. **L02 的 coverage 改成「子集合加上正確性」：** 要求所有 canonical event 都被**原文引用**而且正確，次數從 canonical 的子集合計算；非 canonical 的項目**另外記錄**（例如 `company_extra_items`），不讓它自動導致 MISS。這樣才符合 `log-triage` 本來的用途。
4. **L03 改用結構化的 anchor：** 用 A4 evidence 的 `source` 加上 `locator` 對應 fixture 的紀錄（deterministic exact match），不要依賴 statement 中的 `record_id=` 字串。
5. **在執行 Codex 之前加上 hard stop：** company coverage MISS 或 anchor 是空的時候，**不要執行 dogfood 的 Codex session**，直接把那一對標成 `COMPANY_TRIAGE_COVERAGE_MISS`（triage 失敗），control 是否執行也要事先決定，避免花了 token 卻得到必然無效的 pair。
6. **另外測量 company 的格式遵守率：** 這一輪的瓶頸不是 Codex，而是 company model 能不能遵守格式（L02 有多餘的項目、L03 缺少 token）。可以用不計入 benchmark 的 deterministic probe 測量 company 的 schema／format compliance，或者在 llmtool 端增加 deterministic 的 post-processing（屬於產品設計，需要另外 review）。
7. **重複次數和真實性：** 要建立 routing 政策，至少需要多個 fixture、多次 counterbalanced 的重複，並加入**非 canonical 的真實格式 log**（N1）。在那之前，L01 的結果只能描述為「在可以機械解析的 48 KiB test 輸出上，一對 n=1 的觀察」。
8. **報告時把三種數字並列：** Codex 的節省、company 的用量、benchmark 本身的花費，避免只看 Codex 的百分比。

## 判定

```text
PR #37 STAGE-A REVIEW: APPROVE (execution contract-compliant, honest invalidity flags)
Blind grading: 2 independent graders, full agreement (6/6 verdicts, 3/3 parity); no tie-break needed
Quality-approved valid pairs: L01 only -> +13,631 Codex tokens (+39.82%), n=1, descriptive
L02: EQUIVALENT quality, INVALID (coverage-contract design conflict, F1)
L03: INVALID + DOGFOOD quality FAIL (empty-anchor selection, F2)
Stage B: L01 qualifies; separate explicit authorization required; report unpooled
Round-3 recommendations: subset coverage for L02, structured source/locator anchors for L03,
  pre-Codex hard stop on coverage miss, company format-compliance measurement, real-format fixtures
Can claim generalized Codex savings: NO
```

- 這是只有 report 的 PR（`reports/` 不會觸發 CI，是預期的行為）。
- 本報告 commit 到 PR #37 的 branch。這一輪沒有執行 Stage B，沒有 merge，也沒有開始 A11b.2 或 A11c。
