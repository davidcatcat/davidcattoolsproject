# llmtool Round 3 run1 offline diagnosis — 獨立 review 與建議（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #38，head `84296ef`。我上一輪的 results review（`153caa0`）之後，新增了兩個 commit：
  - `6a62317`：diagnosis handoff；
  - `84296ef`：offline diagnosis report。

  兩個都只是 report。`git diff 3325f75 HEAD -- reports/token_benchmark/20261006_round3/` 是空的，PR 中也沒有 `reports/` 以外的檔案。
- **獨立驗證：**
  - **ledger：** 從 committed 的 JSON 重算：
    - preflight：394,231／294,036；
    - Stage A 的 Codex 6 個 session 合計 196,116，company 合計 105,389；
    - Stage B 的 Codex 20,529 + 32,871 = 53,400，company 19,668；

    三個來源檔案的 SHA 前綴（`5ecc2d63`、`9153bba0`、`ab5ac662`）都和報告一致。衍生的總數（643,747、419,093、504,658、540,153）也都算對了。
  - **provider：** `openai_compatible.py` 第 169–174 行是 length → `MODEL_OUTPUT_INCOMPLETE`，usage 從第 209 行才開始解析，和報告一致。
  - **instructions：** 對照 `instructions/L02.txt` 和 `L03.txt` 的實際文字。
- **沒有做的事：**
  - 沒有讀取 private raw 目錄。這一點依賴 diagnosis report 中的 hash 和 replay 證據；兩個 evaluation SHA 都和 live TrialRecord 的值相同，這是 replay 有效的強證據。
  - 沒有呼叫 company 或 Codex，沒有修改任何檔案，沒有 merge。

## 結論：接受 diagnosis

- diagnosis 品質很高：
  - 先比對 SHA 再 parse；
  - replay 的結果完全相同；
  - 只發布 allowlist 中的 sanitized 欄位；
  - 明確區分測量值和假設下的上界；
  - 修正了我上一輪「可以證明上界」的說法，改為 **ASSUMPTION-BOUNDED**。這個修正是對的，我接受。
- 但是從它發布的數字，可以得到**一個它沒有寫出的 root cause**（§2）。這會直接影響下一步要怎麼設計。

## 1. 確認的事實

| 項目 | diagnosis 的結果 | 我的確認 |
|---|---|---|
| L03-1 | recall 3／7；evidence 3 個，**剛好對應 F01、F02、F03**；unknown、ambiguous、duplicate、inventory 都是 0；分類為 `TARGET_OMISSION_WITH_CLEAN_PROVENANCE` | 和我上一輪從 metadata 推論的結果一致：provenance 的修正有效，失敗的原因是 omission |
| L02-1 | PASS；4 個 canonical 事件；**timeline extras 18**；extra signatures 2；raw 4,808 bytes | 18 個 timeline extras **和 Round 2 完全相同** |
| L02-2 | length，沒有任何內容可以診斷 | 同意，不應該推測 |
| ledger | 到目前為止 Codex 643,747；company ≥ 504,658，總數 unknown | 已重算確認 |
| 有效的節省 | 只有 L01：Stage A 39.82%，Stage B 37.55%，不合併 | 同意 |

## 2. 新發現：L03 的 root cause 是 instructions 和 gate 對「evidence」的語意不一致

**數字：**

- L03-1 的輸出：facts 4、**inferences 1、hypotheses 1、unknowns 3、verify_next 1**、evidence 3（F01、F02、F03）。
- 也就是說，company **有**產生 inference、hypothesis、unknown 和 verify-next 的內容，只是**沒有把這些內容的來源 record**（I01、H01、U01、V01）列成 evidence。

**instructions 的實際文字**（L03.txt）：

> "Every substantive category item must have **support** in one or more evidence entries."

**gate 實際檢查的是：** 每一個 target **record**（包括 `kind=inference`、`hypothesis`、`unknown`、`verify-next` 的 RECORD 行）是否出現在 evidence 中。

**兩者不一致：**

- 依照 instructions 的字面意思，「inference：p95 是三倍」可以由 F01 和 F02 的 evidence 來 **support**；「unknown：traffic mix 未知」也可以被理解為「不需要 evidence，因為這是不知道的事」。
- 所以 company 只列出 3 個 fact 的 evidence，**在字面上幾乎符合 instructions**，但不符合 gate 的要求。
- 這不是 model 的能力問題，而是 **benchmark 的契約沒有用模型能理解的方式說清楚**。這和我在 plan re-review（N1）中警告的「因為 benchmark 自身的規則而 FAIL」屬於同一類問題。當時我只檢查了 duplicate 和 inventory 這兩個衝突，**沒有檢查「support」和「cite the source record」的差別**。這是審查的盲點，記錄在這裡。

**可以修正，而且不會洩漏 gold：**

- `kind=inference`、`hypothesis`、`unknown`、`verify-next` 這些標籤**本身就寫在 fixture 的 RECORD 行中**，不是 gold 的內容。
- 所以新的 instructions 可以寫成：「**每一個 category item 都必須引用它所根據的那一筆 RECORD 作為 evidence；fixture 中標示為 `kind=inference`、`kind=hypothesis`、`kind=unknown`、`kind=verify-next` 的 RECORD，如果和題目相關，就必須分別作為 evidence 列出**」。
- 這樣不需要列出任何 target ID，就能讓 instructions 和 gate 的語意一致。
- **注意：** 這只能讓 gate 和 instructions 一致，**不能證明模型做得到**，仍然需要新的 preregistration 和 live 驗證。另外也要注意不要讓 instructions 變成「把所有 kind 標籤的 record 都列出來」的機械規則，否則 benchmark 就不再測試 company 判斷「哪些和題目相關」的能力。建議寫成「題目相關的、而且你在類別中使用了的 record」。

## 3. L02 的觀察：extras 是穩定的模式，L02-2 很可能是不同的失敗模式

- L02-1 的 18 個 timeline extras 和 Round 2 的 18 個**完全相同**，所以這是 company 穩定的行為。L02 的 instructions 規定了 evidence 中只能放 canonical 行，**卻沒有禁止在 timeline 中放 noise log 行**，所以模型每次都會把同一批 noise 行放進 timeline。
- **推論（只是推論）：** L02-1 的輸出是 4,808 bytes，大約只有 1.2k–1.8k tokens。L02-2 必須產生超過 4,096 tokens，也就是**大約 2 到 3 倍以上**的輸出。如果只是同樣的 18 個 extras，不會到這個長度。所以 L02-2 **比較可能是重複產生相同內容（退化）**，而不是單純的 extras 太多。
- 這個推論無法驗證，因為沒有任何 L02-2 的內容。但它代表的意義是：**只把 timeline 限制成 canonical 行，可能不足以防止 length 失敗**。下一輪必須同時具備：
  - P1（就算 length 失敗也能取得 usage）；
  - 每個 lane 3 次以上的 completion tokens 紀錄。

  這樣才能判斷長度的變異來自 extras 還是退化。

## 4. 是否繼續：用 ledger 計算損益平衡

diagnosis 提供了完整的 ledger，但沒有和節省的效果做比較。用它的數字計算：

- 這個研究方向到目前為止花了 **Codex 643,747 tokens**。
- 唯一有效的節省情境（L01）每次大約省下 **12.3k–13.6k Codex tokens**，代價是 company **大約多用 19.7k tokens**。
- **只看 Codex 的話，損益平衡大約需要 47–52 次 L01 類型的 dogfood 使用**（643,747 ÷ 13,631 ≈ 47；643,747 ÷ 12,342 ≈ 52）。
- 這還沒有算入 company 的 tokens（≥ 504k），也沒有算入 review 和協作的 session（diagnosis 已經寫明這些不在範圍內）。

**建議：**

1. **停止在大型 paired benchmark 上繼續花 Codex 的額度。** Round 3 證明 L02 和 L03 的 company 輸出目前不可靠。即使修正 L03 的 instructions，也必須先用 company-only 的 Phase 0（大約 260k company tokens，0 Codex）驗證，才值得考慮 paired benchmark。
2. **把 dogfood 實際限定在 L01 類型**（大型的 test 失敗輸出 → `test-triage` → 選取原始行）。在 `company-llm-delegation` skill 中，把這個情境標為唯一經過驗證的路徑；`log-triage` 和 `context-pack` 標為「尚未證明可以節省、只能作為輔助」。這是根據證據調整 skill，不是新增功能。
3. **P1（production：length 和 invalid response 時保留 usage）建議獨立推進。** 這不只是 benchmark 的需求，對 llmtool 日常的成本追蹤也有價值，風險也低（只是把三個整數放進 safe diagnostic）。它需要另外的授權，並且依照正常的 `review-change` 和 tests 流程。
4. **如果要做 Round 4，只建議做一個便宜的 company-only Phase 0：**
   - L03 使用 §2 修正後的 instructions；
   - L02 的 timeline 只能放 canonical 行，prose 欄位設上限；
   - 等 P1 完成之後再執行，每個 lane 3 次，記錄 completion tokens。

   在它通過之前，不要事先登記任何 Codex 的 paired benchmark。

## 5. PR #38 的處理建議

- PR #38 現在包含：plan、所有 review、offline implementation、wrapper、live 結果、coordinator review、diagnosis，以及我的各份 review。**全部都在 `reports/` 底下**，沒有修改 production。
- Round 3 已經有明確的結論（gate FAIL，原因已經診斷），建議比照 PR #37：
  1. 在 PR 描述中加入最終狀態，包括：Phase 0 FAIL、L03 是 instructions 和 gate 的語意不一致造成的 omission、L02 是 length 失敗、accounting 是 incomplete、ledger、損益平衡的計算，以及「不授權 paired benchmark」；
  2. 把 draft 轉為 ready；
  3. 依照 repo 的慣例 merge，作為紀錄。
- `reports/` 不會觸發 CI，這是預期的行為。**merge 由你決定，我不會自行 merge。**

## 判定

```text
ROUND-3 RUN1 OFFLINE DIAGNOSIS REVIEW:
ACCEPT

Verified independently:
ledger sources/sums (643,747 Codex; 419,093 Round-2 Company; ≥504,658 incl. Round-3 known), provider length branch L169 / usage L209,
  frozen implementation unchanged since 3325f75, report-only diff

Accepted diagnosis:
L03-1 3/7, evidence = F01/F02/F03 only, provenance CLEAN → TARGET_OMISSION_WITH_CLEAN_PROVENANCE;
L02-1 PASS with 18 timeline extras; L02-2 length, not diagnosable; run1 total UNKNOWN, ≤121,060 only under stated assumptions

New root cause (not in diagnosis):
L03 instruction says category items need "support" in evidence; gate requires the SOURCE RECORD of each category item.
  Output had inferences 1 / hypotheses 1 / unknowns 3 / verify_next 1 but cited only fact records — literal instruction
  compliance, gate failure. Fixable without gold leakage: require citing the RECORD each item is drawn from, incl. kind=
  inference/hypothesis/unknown/verify-next records used (kind labels are fixture content).

L02 observation:
18 timeline extras identical to Round 2 (stable behaviour; timeline noise not forbidden); L02-2 needed ≥2–3× L02-1 output ⇒
  degeneration more likely than extras alone (inference only) ⇒ P1 + per-trial completion tokens needed before redesign claims

Decision recommendations:
1 stop paired-benchmark Codex spend (break-even ≈ 47–52 L01 uses vs 643,747 Codex already spent)
2 scope dogfood guidance to L01-type test triage as the only validated path
3 pursue P1 (usage on length/invalid responses) as a standalone llmtool improvement, separately authorized
4 optional Round 4 = company-only Phase 0 only (fixed L03 instruction, canonical-only L02 timeline, after P1)
5 finalize PR #38 description and merge as a record when the user decides

Paired Codex benchmark / same-preregistration run2:
NOT AUTHORIZED
```

- 本報告 commit 到 PR #38 的 branch。這一輪沒有讀取 private raw 輸出，沒有呼叫 company 或 Codex，沒有修改實作，沒有 merge，也沒有開始 A11b.2 或 A11c。
