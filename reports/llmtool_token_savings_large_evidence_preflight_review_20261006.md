# llmtool PR #37 Round 2 size-feasibility preflight — review 與建議（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #37，branch `bench/llmtool-token-savings-large-evidence-20261006`，**head `a7dd194`**（base `main @ 65df409`）。
- **本輪新增的 commit：**
  - `5386999`：add round 2 size preflight handoff
  - `a7dd194`：record round 2 size-feasibility preflight
- **變更（只有 reports）：**
  - `llmtool_token_savings_large_evidence_preflight_20261006.md`
  - preflight handoff
  - `token_benchmark/20261006_round2/preflight_helper.py`
  - `preflight_results.json`（1572 行）
- **方式：**
  - 閱讀完整的 preflight report，並對照 [Round 2 plan](llmtool_token_savings_large_evidence_plan_20261006.md) 和 [PASS re-review](llmtool_token_savings_large_evidence_plan_rereview_20261006.md)。
  - **用 preflight 公開的 Codex 數字做線性 fit**，估計固定開銷和 Stage A 的預期結果。這只是規劃用的估計，**不是節省的宣稱**。
  - 只讀；沒有呼叫 Codex 或 company，沒有產生 fixture，也沒有執行 Stage A。

## 結論：APPROVE（preflight 記錄正確，可以進入 fixture pre-registration）

- preflight 嚴格遵守 plan：
  - 7 個大小 × 兩個 lane 都通過；
  - **用 request observer 證明實際送出的 request 包含 `chat_template_kwargs.enable_thinking=false`**，而不只是 config 文字；
  - Codex 的 accounting、單一 turn、版本都沒有 drift；
  - filler 是 deterministic 的，不含任何 benchmark 內容；
  - preflight 的用量不計入 Stage A；
  - 依 400k 的預算，在**事先宣告的 tier** 中以 deterministic 的方式降到 48／64／128 KiB（forecast 391,083，PASS）。
- 下方是 4 個觀察和 6 個建議，供下一步參考。

## 確認沒問題的部分

| 項目 | 結果 |
|------|------|
| company 的設定 | 獨立的暫存 HOME（0600），7 個 probe 的 config SHA 相同；observer 在原本的 transport `open()` 之前檢查實際的 Request（`enable_thinking=false`、temperature 0、max_tokens 4096、沒有 Authorization），不修改 request，也不讀取回應內容；每次都只有 1 個 request。這是確認 Round 1 P2 已修正的強證據 |
| Codex lane | 每次都確認是 0.159.2；ephemeral、fresh、單一 turn；0 次 tool call；欄位集合不變；`cache_write` 都是 0；使用 `input + output` mapping |
| filler | 固定的 117-byte 字母行，不含 benchmark 的 ID、gold 或 signature；兩個 lane 使用同樣的 bytes；結果沒有被拿來調整設計 |
| tier 的選擇 | 只在已宣告的 tier 中選擇，36 種組合都有記錄；規則是 deterministic 的（總大小最大，L03 > L02 > L01 優先），不是新的 tier |
| 範圍 | 沒有產生正式的 fixture 或 gold，沒有執行 Stage A／B，沒有修改 production、tests 或使用者的 config；真實 config 的 metadata 沒有改變（也沒有讀取內容） |

## 觀察

### O1：固定開銷和 token 密度（由 preflight 推估）

- 用 7 個 Codex comparable total 做線性 fit：**截距約 16.9k tokens**（每個 session 的固定 system 和工具開銷），**斜率約 338 tokens／KiB**（filler 的密度）。
- 這和 Round 1 大約 17–18k 的 base 一致。

### O2：Stage A 的預期方向（估計，不是宣稱）

- 依 plan 的設計（dogfood 的 prompt 等於固定開銷，加上 company 摘要大約 2–4 KiB，加上 selected raw，上限 `min(32 KiB, 20%)`，**0 次 tool call**）推估：

  | Fixture | control | dogfood | 推估的 Codex 節省 |
  |---------|--------:|--------:|------------------:|
  | L01 48 KiB | ~33k | ~21k | **~35%** |
  | L02 64 KiB | ~39k | ~22k | **~42%** |
  | L03 128 KiB | ~60k | ~27k | **~55%** |

- 前提是：selector 能在上限內涵蓋目標、輸出的 token 數相近、品質相當。
- 也就是說，**在 Round 2 的設計下，Codex 的主要 KPI 很可能轉為正值**。Round 1 的負值主要來自小型 fixture 和額外的 tool request，Round 2 已經移除了這兩個因素。
- 但這只是規劃用的推估，**實際結果必須以 Stage A 的量測為準**。

### O3：mixed token volume 很可能是負的

- company lane 會處理**完整的 fixture**。preflight 的 company prompt tokens：48 KiB 約 17.4k、64 KiB 約 23.1k、128 KiB 約 46.1k。
- 以 L03 為例，dogfood 的 Codex 約 27k 加上 company 約 46k，合計約 73k，**比 control 的 60k 多**。
- 也就是說，**token 是從 Codex 轉移到 company model**，而不是總量下降。實際的價值取決於兩邊的單價差距，必須另外使用可信的定價資料計算；mixed volume 只是描述，不是成本。
- 建議在 Stage A 的報告中，把這一點列為主要的解讀之一，避免只看 Codex 的節省。

### O4：「fresh session」不等於「cold cache」

- 192 KiB 的 probe 有 **59,648 cached input tokens**，其他大小都是 12,288。原因是 filler 是同一行重複，較小的 filler 是較大 filler 的 prefix，provider 端的 prompt cache **跨 session 命中**了前一個 probe 的內容。
- 這表示 cache 狀態在 ephemeral 的 fresh session 之間是共享的。
- 主要的 KPI（input 包含 cached）不受影響，但事先登記的 **secondary metric `(input − cached) + output` 會受到執行順序和先前內容的影響**。

## 建議

1. **（建議 pre-registration 時加入）記錄 cache 背景：** 每個 session 記錄前一個 Codex session 的 fixture 和時間間隔；如果要讓 secondary metric 比較有意義，可以事先登記「控制組和實驗組之間間隔相同的時間」或「每個 prompt 開頭加上不同的 nonce」（只會影響 cache，不改變任務）。nonce 的做法必須在執行前決定，不能事後再加。
2. **（解讀）報告實驗本身的成本：**
   - preflight 已經花了 **Codex 394,231 + company 294,036 tokens**，加上 Stage A 預估最多 391k，Codex 端合計可能接近 80 萬。
   - 最終報告請列出「benchmark 總花費 vs 量到的單次節省」，並寫明這些 preflight 數據在相同的版本和設定下**可以直接重用**，下一輪不需要再跑完整的 7 × 2 probe。
3. **（風險，低）預算的餘裕只有 8,917 tokens（2.3%）：**
   - 真實的 log 和 test 輸出（數字、timestamp、路徑）的 token 密度可能高於字母 filler，control 的實際用量可能超過 forecast。
   - 依 plan 的規則，如果剩餘額度不足，**排在最後的 L03（資訊量最大）**會被標成 `BUDGET_CEILING_NOT_STARTED`。
   - 依 O2 的估計，dogfood 實際上只會用掉大約 0.5 倍的 control，遠低於 2 倍的保守額度，所以實際的風險不高。但建議在 pre-registration 時**明確寫出每一對的 allowance 計算方式**（例如以 preflight 的 S_i × 3 為準，並在每一對完成後用實際值更新剩餘額度），讓「是否開始 L03」的判斷完全 deterministic。
4. **（設計）generator 的真實性（延續 re-review 的 N1）：** fixture 中的雜訊行可以使用比較接近真實的格式（timestamp、path、數字），讓 token 密度和真實情境接近。目標行仍然維持 canonical 語法，以便進行 coverage 檢查。
5. **（執行）company 的輸出長度：**
   - L01 和 L02 的 company 輸入是 17–23k，`max_tokens` 4096 依 Round 1 的經驗（thinking 關閉時，B01 只需要 385 completion tokens）應該足夠。
   - L03 用 `context-pack`，需要輸出六個 category，128 KiB 的輸入可能讓 packet 變長。如果出現 `finish_reason=length`，依 plan 標成 company triage failed，不能調高上限。
   - 建議在 gold 和 fixture 設計時，控制 L03 需要的 fact 數量，避免結構上必然超過 4096。
6. **（品質）兩位 blind grader：** 延續 Round 1 的做法，grader 只能讀取 grading package、fixture、gold、schema 和 task；記錄彼此的一致度。O2 預期會有正值，所以品質相當與否是判斷結果的關鍵。

## 判定

```text
PR #37 PREFLIGHT REVIEW: APPROVE
Size feasibility: PASS (32–256 KiB both lanes)
Company enable_thinking=false: VERIFIED at actual request level
Selected tiers: L01 48 / L02 64 / L03 128 KiB; Stage-A forecast 391,083 <= 400,000 (headroom 2.3%)
Fixture preregistration may proceed: YES (consider recommendations 1, 3, 4, 5 in the preregistration commit)
Estimated (not claimed) direction: positive Codex saving ~35–55% if selector/quality hold; mixed volume likely negative
New blockers: 0
```

- 這是只有 report 的 PR（`reports/` 不會觸發 CI，是預期的行為）。
- 本報告 commit 到 PR #37 的 branch。這一輪沒有呼叫 Codex 或 company，沒有產生 fixture，沒有執行 Stage A，沒有 merge，也沒有開始 A11b.2 或 A11c。
