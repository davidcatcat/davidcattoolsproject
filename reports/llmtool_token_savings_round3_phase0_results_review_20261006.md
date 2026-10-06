# llmtool Round 3 Company Phase-0 live results — 獨立 review 與改善建議（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #38，head `ec50df6`。我上一輪的 B1 re-review（`b23c55a`）之後，新增了 5 個 commit，**都只是 report**：
  - live execution handoff；
  - state persistence 的 hardening；
  - execution review（PASS）；
  - **live results**（`02b3119`）；
  - **coordinator review**（`ec50df6`）。
- **確認：**
  - `git diff 3325f75 HEAD -- reports/token_benchmark/` 是空的，所以 wrapper、test、manifest、instructions 都沒有改變；
  - PR 中沒有任何 `reports/` 以外的檔案。
- **閱讀與交叉比對：**
  - live results；
  - coordinator review；
  - execution handoff 和 execution review；
  - production `openai_compatible.py` 在錯誤路徑上如何處理 usage；
  - Round 2 的實際用量（`stage_a/results.json`）。
- **沒有做的事：**
  - 沒有讀取 private raw 目錄；
  - 沒有呼叫 company 或 Codex；
  - 沒有修改任何檔案（只新增本報告）；
  - 沒有 merge。
- 以下凡是**推估**的數字，都會明確標示「推估」，並列出前提。

## 結論

**接受 live 結果。Phase 0 的 gate 確定 FAIL。**

- **執行面：** live 執行本身做得很好：使用字面值的 pin，第一個 request 之前沒有 smoke test，順序固定，沒有 retry，raw 輸出放在 repo 以外，報告中沒有任何 raw 文字，state 原子性寫入。
- **但是有三個地方應該修正或補充：**
  1. 結果的標籤混用了 INDETERMINATE 和 FAIL；
  2. 「無法確認是否在 300k 以內」的說法過於保守，其實可以算出上界；
  3. 「usage unknown → 全域停止」這個設計，加上 production client 在 length 錯誤時丟棄 usage，讓一次 length 失敗就讓 L03 少了兩次 trial 的資料。這是這次 run 最重要的設計教訓。

## 1. 結果本身（確認）

| trial | 結果 | usage | 我的解讀 |
|---|---|---:|---|
| L02-1 | PASS | 31,399 | **L02 第一次在新的 subset 契約下 PASS**。Round 2 的失敗原因（extras purity）確實已經修正 |
| L03-1 | FAIL：`TARGET_RECALL_BELOW_7_OF_7` | 54,166 | **secondary 只有 TARGET_RECALL**，也就是 anchor 這一關沒有 unknown、ambiguous、duplicate，而且非空 |
| L02-2 | FAIL：`MODEL_OUTPUT_INCOMPLETE` | unknown | 和 L02-1 的 request body **完全相同**（`cc3c79f8…`，proof SHA 也相同），卻因為 `finish_reason=length` 失敗 |
| L03-2、L02-3、L03-3 | NOT_STARTED：`COMPANY_TOKEN_ACCOUNTING_UNKNOWN` | — | 依照凍結的規則全域停止，正確 |

- 實際送出的 request 有 3 個，retry 0 次，Codex 0 次。數字和 accounting 規則一致。
- coordinator 說「不跑 paired benchmark」、「不在同一份 preregistration 下 retry」，我**同意**。

## 2. 從已發布的 metadata 就能得到、但兩份報告都沒有寫出的診斷

### 2.1 L03：structured provenance 修正確實有效，失敗的原因是內容的 recall

- `format_probe` 的 precedence 是 UNKNOWN > AMBIGUOUS > DUPLICATE > EMPTY > RECALL。L03-1 的 secondary **只有** `TARGET_RECALL_BELOW_7_OF_7`，這表示：
  - parse 成功；
  - 所有 evidence 都精確且唯一地對應到 fixture 中的 record；
  - 沒有引用 inventory；
  - 沒有重複；
  - anchor 非空。
- 也就是說，**Round 2 L03 的兩個失敗原因（free-text `record_id`、anchor 是空的）都已經解決**。現在的問題是 company **沒有把 7 個 target 都列為 evidence**，這是內容層面的 recall 問題，不是格式問題。
- coordinator 寫的「did not cover all seven targets through the required exact structured contract」並沒有錯，但應該明確區分：**格式和 provenance 的修正：PASS；內容的 recall：FAIL**。
- **推估（只用來指引診斷，不是事實）：**
  - L03-1 的總用量是 54,166，Round 2 是 54,075（prompt 53,703，completion 372）。
  - Round 3 的 L03 instructions 是 1,200 bytes，Round 2 是 1,085 bytes，只多了 115 bytes（大約 30 tokens）。依此推估，prompt 大約 53.73k，所以 **completion 大約 400–450 tokens**。
  - 這只比 Round 2 的 372 多一點，而 Round 2 那份輸出只有 3 個 evidence item。輸出長度和 Round 2 相近，**比較可能是 omission**（只列出了部分的 evidence），而不是列錯了 record。
  - 但是 400 多個 tokens 也勉強放得下 7 個簡短的 evidence，所以這個推估的信心有限，**必須用 §4 的離線診斷確認**。

### 2.2 L02：完全相同的輸入在 temperature 0 下，輸出長度差了 2 倍以上

- **推估：** L02-1 的總用量是 31,399。L02 的 prompt 大約 29.6k（Round 2 的 29,450 加上 instructions 多出的 579 bytes，大約 150 tokens），所以 completion 大約 **1.8k**。L02-2 的 request body 完全相同，completion 卻**超過 4,096**。
- 所以 temperature 0 **不代表** company backend 的輸出是確定性的。Round 2 的 L02 completion 是 2,189，而且有 18 個 timeline 和 6 個 evidence 的 noise extras，所以 L02 的輸出本來就偏長、而且容易變長。最可能的原因是**冗長的 extras，或者重複產生相同的內容**。
- 我在第一份 plan review（F4）中說過「temperature 0 的三次重複主要測量的是確定性」。這次的結果剛好相反：**backend 並不穩定**，所以 3／3 的 gate 確實有意義。這也表示 L02 的輸出必須設計成**有上限的長度**（見 §5）。

## 3. 應該修正或補充的地方

### 3.1 標籤：Phase 0 的判定應該是 FAIL，而不是 INDETERMINATE

- live results 的標題寫著「**Phase-0 verdict: INDETERMINATE**」。
- 但 plan 明確寫著「Phase 0 is about format/admission readiness only」。**Phase 0 本來就不是經濟或 token 節省的實驗**，所以不存在「economic result INDETERMINATE」這個類別。
- 建議（以 errata 或 coordinator 的最終狀態表示即可，不需要修改原本的 report）：

  ```text
  Phase-0 gate: FAIL (definitive)
  Accounting: INCOMPLETE (one emitted request has unknown usage)
  ```

  INDETERMINATE 這個詞只用來描述「company 的完整 token 總數」，不要放在 Phase 0 的判定上，以免被誤讀成「結果還不確定，可以再跑一次看看」。

### 3.2 是否在 300k 以內：可以證明上界，不需要寫成「無法確認」

- live results 寫著「Compliance with the 300,000 actual-total ceiling cannot be established」。
- **但這裡不需要 impute，也可以得到一個上界：**
  - L02-2 的 request 和 L02-1 的 bytes 完全相同，所以 prompt tokens 相同，而且小於 31,399；
  - `finish_reason=length` 表示 completion 被 `max_tokens=4096` 截斷。
  - 因此 **L02-2 ≤ 31,399 + 4,096 = 35,495**。
  - **完整總數 ≤ 85,565 + 35,495 = 121,060，遠小於 300,000。**
- 前提有兩個：tokenizer 對相同的 bytes 是確定性的；provider 會遵守 max_tokens。這兩個前提都很合理，但仍然是前提，所以應該寫成「**bounded total ≤ 121,060（under stated assumptions）**」，而不是一個測量值，也不能用在節省的計算上。
- 加上這個上界之後，可以明確寫出：「預算規則沒有被違反（上界）」。

### 3.3 最重要的設計教訓：production 在 length 錯誤時丟棄 usage，加上「unknown → 全域停止」

- **production 的事實：**
  - `OpenAICompatibleClient.complete()` 在讀到 `finish_reason == "length"` 時就直接 raise `MODEL_OUTPUT_INCOMPLETE`（第 169 行）；
  - **usage 是在成功路徑的最後才解析的**（第 209 行）。

  所以即使 provider 的回應中**有** usage，也會被丟掉。transport 錯誤之外，所有 `MODEL_RESPONSE_INVALID` 也一樣。
- **plan 的規則：** emitted 的 request 只要 usage 是 unknown，就**全域停止**。
- **兩者結合的結果：** 只要有一次 length 失敗，**另一個 fixture 也會失去剩下所有的 trial**。這次 L03 本來還有兩次 trial 可以提供診斷資料（例如 recall 是否穩定、每次少的是不是相同的 target），結果全部沒有執行。
- 我在 plan 的 re-review 中支持了 per-fixture early stop，卻**沒有分析哪些失敗模式會產生 unknown usage**。這次 run 顯示：**length 是最常見的失敗模式之一，而它在目前的 production 中一定會產生 unknown**。這是審查流程（包括我）的盲點，應該記錄下來。

## 4. 下一步：離線診斷 run1（同意 coordinator，並具體化）

這一步不需要任何新的 company 推論，風險低。建議由新的 handoff 執行：

1. **L03-1：** 對 private raw 目錄中的 stdout 執行凍結的 `evaluate_raw('L03', raw)`，**只發布**下列 sanitized 的資料：
   - `target_recall_numerator`；
   - `missing_target_record_ids`；
   - evidence item 的數量；
   - 每一個 evidence 對應到的 record_id；
   - 每個類別的 item 數量。

   這些資料用來判斷失敗是「只列了 fact」（omission），還是「有列，但列在其他的 record 上」。replay 的結果必須和 `evaluation_sha256 = f27f2d0b…` 完全相同，這也順便驗證了 reconciliation。
2. **L02-1：** 統計已經 PASS 的輸出中 extras 的數量和 completion 的大小（例如每個陣列的 item 數），作為「正常長度」的基準。
3. **L02-2：** 因為 client 丟棄了回應，**無法診斷**。必須在報告中寫明「L02-2 沒有可診斷的內容」，**不要推測原因**。
4. **費用：** 完整列出到目前為止這個研究方向的總花費。

   | 階段 | Codex | company |
   |---|---:|---:|
   | Round 2 preflight | 394k | 294k |
   | Stage A | 196k | 約 105k |
   | Stage B | 53k | 約 20k |
   | Round 3 | 0 | ≥ 85.6k（上界 121k） |

   對照目前唯一有效的節省（L01 大約 38–40%，n = 1 + 1），作為是否繼續的依據。

## 5. 如果要進行 Round 4：改善建議

**任何修改都必須是新的 preregistration，不能當成 retry。**

1. **（llmtool production 改善，價值最高，需要另外的授權和 review）** 在 `finish_reason=length` 和其他 `MODEL_RESPONSE_INVALID` 的路徑上，**也從回應中解析 usage**，並放進 safe diagnostic 的 metadata。usage 只是三個整數，不涉及敏感內容。
   - 這樣 length 失敗就不會變成 unknown，這次 run 會有完整的 accounting，L03 也不會被全域停止。
   - 這對 llmtool 的日常使用（成本追蹤）本身就有價值，不只是 benchmark。
2. **（accounting 規則）** 對已送出但 usage 是 unknown 的 request，**改為以事先登記的上界計費**（L02 是 35k，L03 是 59k；這兩個值本來的定義就是 prompt 加上完整的 4096 completion），而不是全域停止。
   - 這不是 impute：上界是在 preregistration 中凍結的，而且是保守的。
   - 總數記錄為區間 `[known, known + Σ bounds]`。
   - 只有在連 request 是否送出都不知道，或者上界本身無法成立時，才全域停止。
   - 這樣可以保留 per-fixture 的診斷價值。
3. **（TrialRecord）** 保存 `prompt_tokens`、`completion_tokens`、`total_tokens` 三個欄位，而不是只有 total。completion 的大小就是「輸出是否冗長或被截斷」的直接證據。這次只能用推估代替，就是因為缺少這個欄位。
4. **（L02 輸出長度要有上限）** 在 instructions 中規定：
   - `evidence` 和 `timeline` **只能包含 canonical EVENT 行**，不能放任何 noise log 行；
   - `repeated_patterns`、`unknowns`、`strong_review_reasons` 各自最多 N 項（例如 5 項），每項最多一行。

   這樣輸出的上限是可以預測的（4 個事件 × 2 加上少量的文字，大約 1k tokens）。也可以考慮把 max_tokens 提高為事先登記的值（例如 8192），但**長度上限的設計應該優先**，否則只是把截斷的位置往後移。
5. **（L03 只做 recall）** benchmark 實際用到的只有 anchor，也就是要選出哪些 record。完整的 A4 packet 同時要求分析和 provenance，company 很容易只列出 fact 的 evidence。兩個做法：
   - (a) 在 instructions 中，**明確要求按照類別各列出至少一筆 evidence**（fact、inference、hypothesis、unknown、verify-next），而且每個 evidence 的 statement 前面標上它支持的類別。不能列出 target，否則就是 gold leakage。
   - (b) **改為「evidence-select」的任務**：只輸出和題目相關的 `(source, locator)` 清單，不做分析；分析交給 Codex。

   (b) 和 dogfood 的實際分工（company 負責選擇，強模型負責判斷）比較一致，輸出也短。但這已經不是在測試 production 的 `context-pack` skill，必須在 plan 中明確寫出這個取捨。如果 (b) 有效，應該另外評估是否要在 llmtool 中正式加入「evidence-select」模式，而不只是 benchmark 專用的設計。
6. **（重複次數）** 這次證明了 backend 在 temperature 0 下並不穩定，所以 3／3 的 gate 是有意義的。Round 4 應該保留每個 lane 3 次，並記錄每次的 completion tokens，用來估計長度的變異。
7. **（是否繼續的決策點）** 如果費用和效益的比較（§4.4）顯示 L01 這一類「test 失敗的分類」是目前唯一驗證過的節省情境，可以考慮：
   - 先把 dogfood 限定在 L01 類的工作流程（`company-llm-delegation` skill 中只推薦 `test-triage`）；
   - L02 和 L03 等到 production 的 usage 修正（第 1 點）和 evidence-select（第 5 點）完成之後，再用便宜的 Phase 0 驗證；
   - 避免繼續在大型 paired benchmark 上花費 Codex 的額度。

## 判定

```text
ROUND-3 PHASE-0 RESULTS REVIEW:
ACCEPT RESULTS (execution faithful to reviewed handoff; implementation unchanged since 3325f75)

Phase-0 gate:
FAIL — definitive (L03-1 recall < 7/7; L02-2 finish_reason=length); recommend labeling FAIL, not INDETERMINATE

Accounting:
INCOMPLETE — known 85,565; provable upper bound ≤ 121,060 (identical L02 body ⇒ same prompt tokens < 31,399,
  completion ≤ max_tokens 4,096) ⇒ 300k ceiling not exceeded; bound is not a measurement or a savings input

Diagnostics derivable from published metadata:
L03-1 anchor stage clean (no unknown/ambiguous/duplicate/empty) ⇒ structured-provenance fix worked; failure is content recall
  (est. completion ~400–450 tokens, near Round-2's 3-evidence output ⇒ omission plausible; confirm offline)
L02-1 PASS ⇒ subset-coverage fix worked once; L02-2 byte-identical request hit length ⇒ temperature-0 output-length nondeterminism

Key design lesson:
production client discards usage on finish_reason=length/invalid responses + "unknown ⇒ global stop" turned one length
  failure into loss of all remaining L03 trials; reviews (incl. mine) did not analyze which failures yield unknown usage

Recommended next step:
OFFLINE diagnosis of run1 (L03 numerator/missing IDs/evidence→record map via frozen evaluate_raw, replay must equal f27f2d0b…;
  L02-1 output-size baseline; L02-2 not diagnosable); full programme cost ledger

Round-4 improvements (new preregistration only):
1 llmtool: parse usage on length/invalid responses (production change, separate approval) — highest value
2 charge preregistered upper bound for unknown usage instead of global stop; total as [known, known+bounds]
3 persist prompt/completion/total in TrialRecord
4 bounded L02 output (canonical-only evidence/timeline, capped prose arrays) before any max_tokens change
5 L03 per-category evidence requirement, or an evidence-select task (explicit trade-off vs production context-pack)
6 keep 3 trials per lane; record completion-length variance
7 decision: consider limiting dogfood to L01-type test triage until 1+5 are validated

Paired Codex benchmark / same-preregistration retry:
NOT AUTHORIZED (agree with coordinator)
```

- 本報告 commit 到 PR #38 的 branch。這一輪沒有讀取 private raw 輸出，沒有呼叫 company 或 Codex，沒有修改任何實作，沒有 merge，也沒有開始 A11b.2 或 A11c。
