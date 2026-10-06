# llmtool PR #37 Round 2 Stage B（L01 reverse-order confirmation）— review 與兩位 blind grader 結果（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #37，branch `bench/llmtool-token-savings-large-evidence-20261006`，**head `367237b`**。
- **本輪新增的 commit：**
  - `bf7bc9f`：record round 2 blind quality review
  - `4597f04`：finalize round 2 Stage A quality result
  - `bfa314c`：authorize L01 Stage B confirmation
  - `367237b`：record L01 reverse-order confirmation
- **變更（只有 reports）：**
  - Stage A report 和 `results.json` 的 quality 結果更新；
  - Stage B report、handoff、`stage_b/execution_helper.py`、`stage_b/results.json`；
  - `stage_b/blind_grading/`（L01-C、L01-D 和 README）。
- **方式：**
  1. **在閱讀 Stage B report 之前**，先啟動兩位彼此獨立的 blind grader（subagent）。它們**只能讀取** Stage B 的 grading README、L01-C／D，以及 Stage A 凍結的 L01 `fixture.txt`、`gold.json`、`output_schema.json`、`task.txt`，並且要忽略 provenance。grader #2 先評 D 再評 C。
  2. 閱讀 Stage B report 和 Stage A 的更新 diff。
- 這一輪我沒有呼叫 Codex 或 company，也沒有執行任何 benchmark。

## 結論：APPROVE。Stage B 是有效、而且 quality-approved 的 reverse-order confirmation

```text
Stage B (DOGFOOD -> CONTROL): DOGFOOD 20,529 vs CONTROL 32,871 -> +12,342 Codex tokens (+37.55%)
Blind grading: 2 independent graders, full agreement — C PASS / D PASS, EQUIVALENT; no tie-break
Validity: QUALITY_APPROVED reverse-order confirmation (reported separately; NOT pooled with Stage A)
Direction consistent across orders: Stage A +39.82% (C->D order) / Stage B +37.55% (D->C order)
Can claim stable/generalized savings: NO (one fixture, n=1 per order, synthetic canonical 48 KiB)
```

## 1. Stage A 的 finalization（確認）

`4597f04`／`bf7bc9f` 已經把我上一輪的兩位 grader 結果正確地併入 Stage A report 和 `results.json`：

- 6／6 判定一致、3／3 parity 一致；
- **只有 L01** 是 `QUALITY_APPROVED_VALID_PAIR`（+13,631／+39.816%）；
- L02 和 L03 仍然是 INVALID；
- 寫明了 L02、L03 的 provenance 限制；
- 原始的 deterministic 結果照實保留；
- 「Can claim generalized Round-2 Codex savings: NO」。

**PASS**

## 2. 兩位 blind grader 的結果（Stage B）

| | Grader #1 | Grader #2 |
|---|---|---|
| L01-C | PASS（ID、群組、檔案、kind、count、原文 evidence、3 個 unknowns 都 MET；沒有禁止的 claim） | PASS |
| L01-D | PASS（結構和 C 相同；evidence 只引用 E 行，不算 miss） | PASS |
| pair parity | **EQUIVALENT** | **EQUIVALENT** |
| 差異 | C 多引用了 FAILURE 開頭的行（追溯性較好，但不影響正確性） | 同一個觀察 |
| provenance 字眼 | 兩個答案都沒有 | 兩個答案都沒有 |
| 遵守規則 | 只讀允許的檔案，**讀了整份 fixture**，沒有偏差 | 只讀允許的檔案，只讀目標附近的行，依 gold 的清單判斷完整性（已揭露的限制，沒有其他偏差） |

**一致度：** 判定 2／2、parity 1／1，**不需要 tie-break**。grader #1 讀了整份 fixture，確認只有 4 筆 FAILURE，補上了 grader #2 只讀局部範圍的限制。

## 3. Stage B 執行的確認（PASS）

| 項目 | 結果 |
|------|------|
| 凍結的 artifact | 16 個雜湊在執行前後都相同；`self_check` 在 Python 3.9 執行前後都是 20／20 |
| 順序 | 依事先登記，**相反順序**：DOGFOOD → CONTROL；只跑 L01、只跑一對 |
| Codex | 0.159.2；設定和 Stage A 完全相同；2 個不同的 thread；單一 turn；**0 次 tool call**；`cached = 0` |
| control 的 prompt | **SHA 和 Stage A 的 L01 CONTROL 完全相同** |
| company | 1 次 request，0 次重試；**實際的 Request body digest 和 Stage A 完全相同**；`enable_thinking=false` 等設定在 Request 層級得到證明 |
| coverage、selection、delivery | coverage PASS → 選出 1,768 bytes（3.6%），4 個範圍，和 Stage A 一致；原文注入、0 次 tool call |
| hard stop | 新的 adapter **只在 coverage 和 selection 都成功時才執行 dogfood 的 Codex**。這採用了我在 Stage A review 中的建議 5 |
| 預算 | Stage B 獨立的 200k 上限，用了 53,400，剩餘 146,600；沒有使用 Stage A 剩下的額度 |
| 解讀 | 明確寫出「不能平均、不能合併、不能宣稱穩定的 40%、不能推論到 48 KiB synthetic fixture 以外、不能設定 routing 門檻」，也寫明 winner's curse |

## 4. 數字（分開列出，不合併）

| | 順序 | Control | Dogfood | Codex 差額 | Company | mixed delta（描述） |
|---|---|--------:|--------:|-----------:|--------:|--------------------:|
| Stage A L01 | C → D | 34,235 | 20,604 | **+13,631（+39.82%）** | 19,675 | −6,044 |
| Stage B L01 | D → C | 32,871 | 20,529 | **+12,342（+37.55%）** | 19,668 | −7,326 |

**觀察：**

1. **在兩種順序下方向一致，大小也接近**（39.8% 對 37.5%）；兩次的 `cached` 都是 0。所以至少在這個 fixture 上，順序和 cache 的影響不明顯。
2. **dogfood 非常穩定**（20,604 對 20,529，差 0.4%），company 也非常穩定（19,675 對 19,668）。
3. **control 的 prompt 完全相同，input tokens 卻差了 1,336**（33,545 對 32,209，大約 4%）。這表示 **Codex 本身的固定開銷就有大約 ±4% 的變動**（可能來自 runtime 注入的環境或指令內容），是這套量測的**雜訊下限**。
   - 和大約 38–40% 的效果相比，這個雜訊很小，不影響方向。
   - 但如果將來要比較只差幾個百分點的設計，必須用多次重複才能分辨。
4. **mixed volume 兩次都是負的**：token 是從 Codex 轉移到 company，而不是總量下降。

## 5. 建議

1. **Round 2 可以收尾：** 結論應該寫成：「在 synthetic、canonical 格式、48 KiB 的 test 輸出上，selective injection 的 dogfood 在兩種執行順序下都讓 Codex 的 comparable tokens 大約減少 38–40%，品質經兩位 blind grader 判定相當；company 加上 Codex 的總 token 量增加。L02、L03 因為 company 的輸出格式不符合 coverage 契約而無效。」
   - 不能延伸到 log、混合 evidence、真實格式或其他大小。
2. **PR #37 可以考慮 merge：** 這是只有 report 的 PR，Stage A 和 Stage B 都已經完成兩位 blind grader 的評分，而且 report 已經更新。建議在 PR 描述加上最終狀態和上面的結論，以及 grader #2 只讀局部範圍、Stage A 中 grader 執行過一次 shell 指令這兩項揭露，然後依 repo 的慣例 merge。`reports/` 不會觸發 CI，是預期的行為。
3. **是否要繼續投入（決策建議）：**
   - **價格比：** 用可信的定價資料，計算「Codex 節省 12–14k tokens」對「company 多用大約 20k tokens」的實際成本差。如果 Codex 和 company 的單價差距很大，L01 這類情境就值得採用；否則只是把負擔轉移。
   - **company 格式遵守度是主要瓶頸：** L02、L03 的失敗都來自這一點。Round 3 應該優先處理（建議見 Stage A review 的 3–6：L02 的 subset coverage、L03 用結構化的 source／locator 作為 anchor、company 的格式遵守率測量），而不是增加更多 fixture。
   - **真實格式的 log：** 在推論到實際的開發使用之前，需要至少一組非 canonical 的真實格式 fixture（N1）。
4. **把 benchmark 的成本控制寫進下一輪：** Round 2 的量測花費（Codex 大約 64 萬，含 preflight、Stage A、Stage B；company 大約 42 萬）遠大於單次的節省。下一輪建議重用 preflight 的資料，並優先使用較便宜的 deterministic probe 驗證 company 的格式遵守，再決定是否執行昂貴的 paired session。
5. **雜訊下限：** 如果將來要比較只差幾個百分點的設計，把 control 的重複次數加入事先登記（例如同一個 prompt 跑 2–3 次），以量化 ±4% 的固定開銷變動。

## 判定

```text
PR #37 STAGE-B REVIEW: APPROVE
Stage-A finalization (blind result recorded): CORRECT
Stage-B blind grading: 2/2 PASS for C and D, EQUIVALENT, full agreement (grader #1 full-fixture read)
Stage-B validity: QUALITY_APPROVED reverse-order confirmation; reported separately, not pooled
Observed: Stage A +39.82% / Stage B +37.55% (same direction, both orders); mixed volume negative both
Measurement noise floor: identical CONTROL prompt varied 1,336 input tokens (~4%)
Can claim stable/generalized savings: NO
Recommendation: close Round 2 with the scoped conclusion; merge report-only PR #37 after final PR-description update;
  Round 3 should target company format compliance (L02 subset coverage, L03 structured anchors) and real-format fixtures
```

- 本報告 commit 到 PR #37 的 branch。這一輪沒有執行任何 benchmark，沒有 merge，也沒有開始 A11b.2 或 A11c。
