# llmtool PR #38 Round 3 format-compliance plan — 第二份獨立 review 與建議（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #38，branch `bench/llmtool-token-savings-round3-format-compliance-20261006`，**head `adda139`**（base `main @ fecbdba`，即 PR #37 merge 之後）。
- **審查內容：**
  - `d08bb1e` 的 Round 3 plan，317 行；
  - `adda139` 中已經存在的 methodology review。這份 review 判定 APPROVE WITH CHANGES，提出 M1、M2、M3 三點。
- **方式：**
  1. 閱讀 plan 和既有的 review。
  2. **用 Round 2 實際的 artifact 檢查可行性：**
     - `stage_a/results.json` 中 L03 的 company attempt 紀錄；
     - `preregistration.json` 中 L03 的 company instructions；
     - L03 的 fixture 和 gold；
     - A4 的 `tools/llmtool/llmtool/context.py`。
  3. 在 scratchpad 中，用 `/usr/bin/python3 -B` **獨立重算** L03 的 source+locator 多重對應。
- **沒有做的事：**
  - 沒有呼叫 company 或 Codex；
  - 沒有修改 plan、fixture、production 或 skill。

## 結論：同意既有 review 的 APPROVE WITH CHANGES，另外補充一個 blocker 等級的 L03 gate 缺口（F1）

```text
Existing review (adda139) M1/M2/M3: AGREE, independently spot-checked
New F1 (before live probes): L03 Phase-0 gate must include a preregistered target-recall floor;
  A4 only lets `evidence` carry source/locator, and Round-2's actual output cited 3 of 7 targets
New F2 (before live probes): retain raw company stdout (synthetic only) for offline adapter replay
New F3 (before live probes): declare a company-token ceiling for Phase 0
May implement offline probe/self-tests: YES
May run six live company probes: NO (M1–M3 + F1–F3 first)
```

## 1. 既有 review 的抽查（AGREE）

- **L03 歧義數字，獨立重算的結果完全一致：**
  - 668 個 RECORD；
  - 534 個不同的 `(source, locator)`；
  - 120 個 pair 對應到多筆 record，共涉及 254 筆 record；
  - 7 個 target（F01／F02／F03／I01／H01／U01／V01）各自只對應到 1 筆。

  這也證實「必須用 multimap，不能用 last-writer-wins 的 dict」是必要的。
- **M1（timeline 要完整，不能只看順序）：** 同意。少了這一條，空的 timeline 也能通過。
- **M2（每次 probe 都必須有非空的 anchor）：** 同意。A4 允許空的 evidence 和 null locator。
- **M3（6 個邏輯 trial 和最多 12 次實際 request 的區別）：** 同意。這必須在執行前凍結。
- **未來 paired benchmark 的條件：** 包括 control 的 hard-stop 和順序政策、失敗的 pair 要計入分母、必須有真實格式的 fixture。同意，而且和我在 Stage A／B review 中的建議一致。

## 2. 新發現

### F1（live probe 之前必須處理）：L03 的 gate 可能 PASS，但下游必然失敗

**事實：**

1. A4 的 `ContextPacket` 中，**只有 `evidence` 的 item 帶有 `source`／`locator`**。`inferences`、`hypotheses`、`unknowns`、`verify_next` 都是純字串（`context.py` 第 54–60 行和第 141–145 行）。所以 structured anchor 只能從 evidence item 取得。
2. Round 2 L03 實際的 company 輸出只有 **3 個 evidence item**，內容對應 F01、F02、F03 三個事實。這發生在 instructions 已經要求「cite the records supporting substantive statements, uncertainty and verify-next」的情況下（見 `results.json` 中 L03 attempt 的 `unknown_citations`，以及 `preregistration.json` 中 L03 的 instructions）。
3. gold 要求 7 個 target 全部被引用（`minimum_unique_target_citations: 7`）。I01、H01、U01、V01 分別位於第 383、479、575、670 行，彼此相隔大約 96 行。selector 的 ±2 行不會順帶涵蓋它們。

**推論：**

- 如果 company 在 Round 3 的行為和 Round 2 相同，只是改用 source+locator 作為 anchor，那麼這次 probe 會符合 plan 和 M2 的全部條件：
  - parse 成功；
  - 3 個唯一的 anchor，沒有 unknown、ambiguous 或 duplicate；
  - 非空。
- 但是 **target recall 只有 3／7**。selected raw 中不會有 inference、hypothesis、unknown、verify 的 record，dogfood Codex 仍然很可能重複 Round 2 中 L03-B 品質 FAIL 的結果。
- 也就是說，**Phase 0 的 3／3 PASS 無法預測後續 paired benchmark 能否成功**。這違背了 plan「fail cheaply」的目的。

**建議（在 live probe 之前凍結）：**

- 把 **L03 的 target recall 下限**列為 Phase 0 的 PASS 條件。
  - gold 要求全部 7 個，所以建議 **7／7**。至少也要事先登記一個明確的數字。
  - 評估仍然在 output 和 anchor 凍結之後進行，只用來判定，不用來修補。
  - 既有 review 的 M2 只說「如果要設定門檻，必須事先登記」。我認為**必須設定**，否則 L03 的 gate 只能測量語法。
- **同時（可選）修改 Round 3 L03 的 company instructions：** 明確要求「每一個 inference、hypothesis、unknown、verify-next 都必須有對應的 evidence item，帶有該 record 的 exact source 和 locator」。
  - 這是 Round 3 新的 instructions，必須在 live 之前凍結，不算事後調整。
  - 如果不修改，F1 的門檻很可能讓 Phase 0 直接 FAIL。這也是合理的便宜結果，但應該事先預期。

### F2（live probe 之前）：保留 company 的原始輸出

- Round 2 的 `results.json` 只保留了 `stdout_sha256` 和部分的 coverage 診斷（evidence 的 statement），**沒有保留 evidence item 的 `source`／`locator` 值**。
- 所以現在**無法在離線狀態下，把新的 source+locator adapter 套用到 Round 2 實際的 L03 輸出**。原本這可以是一個不花錢的 Phase 0 資料點，可以直接回答「company 是否會照抄 fixture 的 `source=`／`locator=` 值」。
- **建議：**
  - Phase 0 把每次 company 的**原始 stdout 全文**存進 `format_probe_results.json` 或旁邊的檔案。內容是 synthetic fixture 的摘要，不含 credential；跑 secret-marker 檢查之後才 commit。
  - 這樣以後修改 adapter 時就能 replay，不必再呼叫 company。
  - 規則是：adapter 的判定仍然以 live 之前凍結的版本為準；replay 只用於診斷和下一輪的設計。

### F3（live probe 之前）：Phase 0 的 company token 上限

- plan 沒有 company 端的預算。依 Round 2 的實際用量估算：
  - L03 每次大約 54k prompt tokens（Round 2 是 53,703），L02 每次大約 23k；
  - 6 次 trial 大約 **23.5 萬 tokens**；
  - 如果 M3 允許 retry，最多大約 47 萬。
- **建議：** 事先宣告一個 company token 上限（例如 300k），並且規定「超過上限就標成 NOT STARTED，不可以因為結果有利就停止」。這和 Round 2 對 Codex 預算的處理方式一致。

### F4（解讀，non-blocking）：temperature 0 加上相同 prompt，3 次重複的資訊量有限

- Round 2 Stage A 和 Stage B 的 L01 company request body 完全相同，company 總用量也幾乎相同（19,675 對 19,668，差異小於 0.1%）。
- 預期 Phase 0 的 3 次重複會高度相關，所以 3／3 主要證明的是「穩定可重現」，而不是「可靠」。
- 既有 review 已經提到「3／3 不是可靠度的證明」，我同意。另外建議在報告中寫明「重複次數主要測量的是 backend 的非決定性」。
- 如果將來要評估格式遵守的**穩健度**，比較有用的是**多個 fixture 或 instructions 的變體，每個跑一次**，而不是同一個 prompt 跑三次。這可以留到 Round 3 的 benchmark 階段，配合真實格式的 fixture 一起做。

### F5（決策，non-blocking）：先取得單價資料

- Round 2 兩次 L01 的 mixed volume 都是負的（company 大約多用 20k，Codex 大約省 12–14k）。
- 在投入 Round 3 的 paired benchmark 之前，建議先用可信的定價資料，計算 Codex 和 company 每 1k tokens 的成本比。
- 如果比值不夠大，即使 Phase 0 PASS，Round 3 的 paired benchmark 也只能證明「token 轉移」，對實際成本沒有幫助。可以考慮在 Phase 0 之後直接收尾。

## 3. 建議的執行順序

1. 修訂 plan，或者追加一份 addendum，把 M1、M2、M3 和 F1、F2、F3 寫成凍結的規則；F1 中是否修改 instructions 也要明確決定。
2. 實作 `format_probe.py`，以及既有 review 列出的離線 counterexample。另外加入：
   - **L03 recall 低於門檻時 FAIL**；
   - **只有 fact 的 evidence 時 FAIL**；
   - **原始 stdout 被保存，且通過 secret-marker 檢查**。
3. 先把 rules、instructions 和 self-test 在 live 之前 commit 並凍結，再取得 live 授權，執行 6 個 trial，最後用另一個 commit 發布結果。
4. 依 Phase 0 的結果和 F5 的單價，決定要停在 Phase 0，還是事先登記 Round 3 的 paired benchmark。

## 判定

```text
PR #38 ROUND-3 PLAN — SECOND INDEPENDENT REVIEW: APPROVE WITH CHANGES
Existing review M1/M2/M3: AGREE (L03 multimap counts re-verified: 668 / 534 / 120 / 254; 7 targets unique)
F1 [before live]: add preregistered L03 target-recall floor (recommend 7/7); A4 anchors only via `evidence`,
   Round-2 output cited 3/7 targets -> format gate could pass while the paired benchmark is doomed
F2 [before live]: retain raw company stdout (synthetic, secret-scanned) for offline adapter replay
F3 [before live]: declare a Phase-0 company-token ceiling (~235k expected, up to ~470k with retries)
F4 [interpretation]: 3x identical temperature-0 trials mostly measure determinism, not robustness
F5 [decision]: obtain Codex vs company unit pricing before any Round-3 paired benchmark
May implement offline probe/self-tests: YES
May run six live company probes: NO — after M1–M3 + F1–F3 are frozen and offline checks pass
```

- 本報告 commit 到 PR #38 的 branch。這一輪沒有呼叫 company 或 Codex，沒有修改 plan、fixture、production 或 skill，沒有 merge，也沒有開始 A11b.2 或 A11c。
