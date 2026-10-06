# llmtool PR #37 Round 2 plan — methodology re-review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #37，branch `bench/llmtool-token-savings-large-evidence-20261006`，**head `aaddaad`**（base `main @ 65df409`）。
- **本輪新增的 commit：** `aaddaad` docs(llmtool): close round 2 methodology review findings（plan +193／−14）。
- **對照：** 上一輪的 [methodology review](llmtool_token_savings_large_evidence_plan_review_20261006.md)：APPROVE WITH CHANGES，M1、M2、S1–S4。
- **方式：** 逐行閱讀 `0946609..aaddaad` 的 diff。只讀；沒有產生 fixture，沒有呼叫 Codex 或 company，也沒有執行任何 session。

## 結論：PASS（methodology re-review）

```text
M1: CLOSED   M2: CLOSED   S1: CLOSED   S2: CLOSED   S3: CLOSED   S4: CLOSED
new blockers: 0
Round 2 may proceed to the size-feasibility preflight, then fixture/gold preregistration,
per the plan's stop conditions. No savings claim exists until measured.
```

另有 1 個 non-blocking 的解讀提醒（N1）。

## 逐項確認

| 項目 | 修正 | 判定 |
|------|------|------|
| **M1** selected range 的提供方式 | 「**Round 2 pins zero evidence-reader tool calls in measured DOGFOOD Codex sessions**」；凍結的 selected raw bytes **原文**直接放進初始 prompt，並記錄 `selected_raw_bytes_injected`、ranges、注入 bytes 的 SHA-256、manifest 的 SHA-256；任何額外的讀取都標成 `DELIVERY_CONTRACT_VIOLATION`；明確寫出「removes Round 1's extra-request confounder; tests selective injection, not tool-based retrieval」；停止條件也同步更新 | **CLOSED** |
| **M2** 不依賴 gold 的 coverage | 新增「Gold-independent deterministic coverage contract」：已 commit 而且凍結的 `coverage.py` **must not read, import, open or receive any gold**；fixture 的 canonical 語法在產生之前就固定。L01：用 anchored regex 從 `FAILURE test_id=…` 行取得 failure 的全集，和 company 回傳的 ID 比對（missing、duplicate、unexpected、complete）。L02：宣告可以機械涵蓋的 signature 類別，計算 event 和 request ID、次數、時間順序，**只代表語法上的涵蓋和順序**。L03：**只檢查引用是否存在、是否屬於 fixture**，明確不宣稱語意上涵蓋了所有內容。順序是 validate → coverage → selection → freeze → inject → 執行 → 之後才 gold grading | **CLOSED** |
| **S1** generator | generator 原始碼、seed、版本和 SHA、輸入設定都在執行前 commit；重新產生的輸出必須和 fixture 的 SHA-256 完全相同；generator 不讀取 gold | **CLOSED** |
| **S2** 預算 | 「Benchmark budget gate」：Stage A 最多 400,000、Stage B 最多 200,000 comparable tokens；執行前用 preflight 實測加上 dogfood 的 2 倍保守估計來預測，超過就在已宣告的較小 tier 中選擇；執行中**不因結果是否有利而停止**；每一對完成後累計，剩餘額度不夠就不開始下一對，並標成 `BUDGET_CEILING_NOT_STARTED`（不是 0） | **CLOSED** |
| **S3** gold 和評分 | 結構化的計數和 ID 比對，取代子字串比對；**2 位獨立的 blind grader**，記錄彼此的一致度；pair parity 意見不一致時，不計入 quality-approved，必須另外記錄 tie-break | **CLOSED** |
| **S4** 失敗分類 | `COMPANY_TRIAGE_COVERAGE_MISS`、`SELECTIVE_READ_CAP_INSUFFICIENT`、`DELIVERY_CONTRACT_VIOLATION`、`QUALITY_PARITY_FAIL`，彼此不可互換，分開報告 | **CLOSED** |

**沒有被改變的部分（確認）：**

- 主要的 KPI 仍然是 `input + output`；secondary metric 只作為描述。
- company 設定固定為 `enable_thinking: false`、temperature 0、max_tokens 4096，不能中途調高。
- Stage B 的 winner's curse 處理和觸發條件都不變。
- 沒有 router、budget enforcement、A11b.2 或 A11c。

## N1（non-blocking，解讀提醒）：canonical 語法讓 triage 比真實情況容易

- 為了讓 coverage 可以機械計算，目標紀錄採用 `FAILURE test_id=…`、`EVENT event_id=…`、`RECORD record_id=…` 這類 canonical 格式。這對 deterministic 驗證是必要的，但也讓 **company triage 和 selector 比真實的 pytest、CI log 更容易找到目標**。
- **影響：** Round 2 的結果代表的是「在可以機械解析的格式下，selective injection 的 token 經濟性」。即使結果是正的，也不能直接推論到格式混亂的真實 log。
- **建議：** 在 report 的 Interpretation 加上這個限制。真實 log 需要真正的 parser，或者另外設計一輪「非 canonical」的 fixture。

## 判定

```text
PR #37 METHODOLOGY RE-REVIEW: PASS
M1/M2/S1–S4: CLOSED
New blockers: 0 (N1 interpretation note only)
Next allowed step: size-feasibility preflight (non-semantic) -> preregistration commit -> Stage A
```

- 這是只有 report 的 PR（`reports/` 不會觸發 CI，是預期的行為）。
- 本報告 commit 到 PR #37 的 branch。這一輪沒有產生 fixture，沒有執行 preflight 或任何 session，沒有 merge，也沒有開始 A11b.2 或 A11c。
