# llmtool PR #37 large-evidence token-savings Round 2 plan — methodology review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #37 `bench(llmtool): plan large-evidence token-savings round 2`，branch `bench/llmtool-token-savings-large-evidence-20261006`，**head `4260dea39db078a67735f501a17b5fe2e694095a`**。
- **Base：** `main @ 65df409`（PR #36 已 merge）；ahead 2、behind 0。
- **變更：** 只新增 `reports/llmtool_token_savings_large_evidence_plan_20261006.md`（401 行）。沒有 production、tests、CI、fixture，也還沒有執行任何 session。
- **對照：** Round 1 的 [pilot review](llmtool_token_savings_pilot_review_20261006.md)（P1 固定開銷為主、P2 company 的 thinking 沒關、P3 cache 和順序的影響、P4 gold 不具鑑別力），以及 PR #35 的測量契約。
- **另外確認：** PR #36 最後兩個 commit（`afa91ae`、`eea0bcb`）已正確把 blind grading 的結果併入 pilot report 和 `results.json`：6／6 PASS、quality-approved 的 aggregate 是 −57,295（−104.46%），`DOGFOOD_INCREASED_CODEX_USAGE`，literal miss 照實保留（**PR #36 的 P5 已關閉**）。
- 只讀；沒有產生 fixture，沒有呼叫 Codex 或 company，也沒有執行 benchmark。

## 結論：APPROVE WITH CHANGES

- Round 2 針對 Round 1 的問題做了正確而且大多充分的修正。
- 但有兩個會影響公平性和可執行性的定義必須在**產生 fixture 和執行之前**固定下來（M1、M2）。另外有 4 個小建議。

## 已正確處理的部分

| Round 1 的問題 | Round 2 的處理 | 判定 |
|---------------|---------------|------|
| P1 固定開銷蓋過小型 evidence | 64／128／256 KiB 的大型 fixture；selective read 的上限是 `min(32 KiB, 20%)`；只允許 1 次 tool call，一次取回所有範圍 | 方向正確（另見 M1） |
| P2 company 的 thinking | 固定 `enable_thinking: false`、temperature 0、max_tokens 4096；使用獨立的暫存 HOME，不修改使用者的 config；**無法證明設定生效就停止**；length 失敗時**不能**中途調高上限，只允許對 transport 失敗、以相同設定重試一次 | PASS |
| P3 cache 和順序 | **事先登記** secondary metric `(input − cached) + output`，明確寫出不能取代主要的 KPI；順序固定，Stage B 用相反的順序 | PASS |
| gold 洩漏 | gold 只用於評分；selector 只能使用 fixture、凍結的規則和 company 的輸出；selected ranges 先凍結並計算 digest，**之後**才比對 gold；漏掉的部分不得事後修補 | PASS |
| 大小的可行性 | 先對 Codex 和 company 兩邊做 non-semantic capability preflight（使用和 benchmark 無關的 filler，不計入，也不拿來調整）；事先宣告的 fallback tier；兩邊都通過才可以使用，否則標成 `TARGET_SIZE_NOT_FEASIBLE` | PASS |
| selector 的 determinism | exact-match anchor、前後各 ±2 行的 padding、間隔 ≤ 2 行就合併、依行號排序、合併前最多 32 個範圍，不允許語意或手動的擴展 | PASS |
| Stage B 的 winner's curse | 只在 Stage A 品質相當、契約有效，而且節省 ≥ 10% 時，才跑一次相反順序；**不能和 Stage A 合併**成無偏的估計，也不能拿來當門檻 | PASS |
| accounting | 沿用已接受的 `input + output`，包含 version、schema、turn 形態的 drift 停止條件 | PASS |
| 範圍 | 沒有 router、budget、A11b.2、A11c；不自動 merge | PASS |

## Findings

### M1（Medium，必須在執行前固定）：selected raw ranges 的內容是「直接放進 prompt」還是「只給 reference，再用 tool 讀取」，定義有歧義

- **位置：** Selective-read contract：
  - 「DOGFOOD initial context: … selected raw ranges/reference; … DOES NOT contain the complete raw fixture.」
  - 「DOGFOOD may perform one evidence-reader tool call that returns all preselected raw ranges」
- **為什麼重要：** Round 1 的主要成本（P1）是 **tool call 造成的第二次 Responses request，重送了大約 18k 的 base context**。selected ranges 已經是 deterministic、凍結、有上限的內容，所以有兩種做法：
  - **(i) 把 selected range 的內容直接放進 dogfood 的初始 prompt（0 次 tool call）：** 仍然符合「回查原始 evidence」的 M1 規則（Codex 在作答前看到的是原始 bytes，不是 company 的轉述），而且避免了第二次 request。
  - **(ii) 只給 reference，再用 1 次 tool call 讀取：** 會重現 Round 1 大約 18k 的固定開銷。
- 這兩種做法的結果可能相差 1 萬 token 以上。如果不事先固定，執行者可能在現場臨時決定，結果就無法解讀。
- **最小修正：** 在 plan 中**明確擇一並事先登記**。**建議採用 (i)**，因為它最直接檢驗「大型 evidence 的 selective read」這個假設，並在 per-run record 記錄 `selected_raw_bytes_injected`。如果選擇 (ii)，請在 Interpretation 寫明「結果包含一次額外 request 的固定開銷」。
- 不建議在同一輪同時測兩種做法（成本會加倍），除非事先登記成兩個 arm。

### M2（Medium，必須在執行前固定）：不依賴 gold 的 deterministic coverage 規則沒有寫明

- **位置：**
  - 「DOGFOOD initial context: … deterministic validation/coverage result」
  - 「Completeness claims must use deterministic coverage evidence」
- **問題：** gold 被嚴格限定為只用於評分（這是對的），但給 Codex 的「coverage result」**必須用不依賴 gold 的規則**計算。plan 沒有寫出每個 fixture 的規則：
  - **L01（test output）：** 例如用 regex 列出 fixture 中所有 `FAILED`／`ERROR` 開頭的 test ID，並比對 company 回傳的群組是否每一個都涵蓋、而且只出現一次。這可以完全 deterministic，Round 1 的 `mechanical()` 就是這麼做的。
  - **L02（log）：** 例如用 regex 計數 timeout signature 的出現次數和 request ID 集合，比對 company 的 `error_signatures`／`evidence`。
  - **L03（mixed evidence）：** 最難。需要先定義「record」的語法（例如固定的 record ID 格式），coverage 只能檢查「company 引用的 source／record ID 是否存在」，**不能**宣稱語意上已經涵蓋所有 fact。這一點應該明確寫出。
- **後果：** 如果 coverage 的計算方式是現場決定的，或者不小心參考了 gold，就會造成 gold 洩漏，或者讓「completeness 已驗證」的說法沒有依據。
- **最小修正：** 為 L01、L02、L03 各寫出 coverage 的 regex 或規則，和 selector 一起凍結並計算 digest（建議放在已 commit 的 `coverage.py`）；明確寫出「coverage 規則不讀取 gold」；L03 寫明 coverage 只代表引用的存在性。

### 小建議（non-blocking）

- **S1：** fixture 的 generator 要**固定 seed 並 commit**（generator 原始碼加上 seed，以及輸出的 SHA-256），讓 fixture 可以重現。
- **S2：** 在 plan 加上**預估的 token 預算**：L03 的 control 在 prompt 中放入 256 KiB，大約 6–8 萬 tokens；Stage A 加上可能的 Stage B 合計可能數十萬 tokens。事先寫出上限，超過就停止，避免 benchmark 本身燒掉大量 token。
- **S3：** 延續 Round 1 的 P4：gold 的 literal check 要避免子字串就能滿足的情況（例如用結構化的計數）。如果可以，使用 2 位 blind grader，並記錄彼此的一致度。
- **S4：** 如果 company 漏掉了某個目標，selector 就不會選到它（plan 已經把這種情況定為 pair 無效）。建議在報告中把「company 漏掉」（triage 的品質）和「cap 不足」（`SELECTIVE_READ_CAP_INSUFFICIENT`）分成兩個 flag，方便解讀失敗的原因。

## 判定

```text
PR #37 METHODOLOGY REVIEW: APPROVE WITH CHANGES
M1: OPEN — pin selected-range delivery (recommend inject frozen range contents, 0 tool calls)
M2: OPEN — pre-register gold-independent deterministic coverage rules per fixture (L03 = citation existence only)
Round 1 P1/P2/P3 remediation: adequate; P4: carry forward (S3)
PR #36 P5 (blind result recorded in pilot report): CLOSED
New blockers for docs merge: 0; M1/M2 required before fixture generation / measured sessions
```

- 這是只有 report 的 PR（`reports/` 不會觸發 CI，是預期的行為）。
- 建議在產生 fixture 之前補上 M1 和 M2（只需要改文件），再請 coordinator 確認。
- 本報告 commit 到 PR #37 的 branch。這一輪沒有產生 fixture，沒有執行任何 session，沒有 merge，也沒有開始 A11b.2 或 A11c。
