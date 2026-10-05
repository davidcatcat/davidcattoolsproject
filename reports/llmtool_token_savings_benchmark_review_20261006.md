# llmtool PR #35 paired Codex token-savings benchmark — review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #35 `docs(llmtool): add paired Codex token-savings benchmark`，branch `docs/llmtool-token-savings-benchmark-20261006`，**head `5b1c49d62f0858dbc63e5b60acc6b0190f0d1762`**。
- **Base：** `main @ 769e863`（PR #34 已 merge）；ahead 4、behind 0。
- **變更（4 個檔案，+280／−2）：**
  - `reports/llmtool_token_savings_benchmark_plan_20261006.md`（新增）；
  - `skills/company-llm-delegation/SKILL.md`（新增「Token-savings benchmark」段落）；
  - `tools/llmtool/ROADMAP.md`（新增 Token Savings Benchmark／Dogfood ROI 段落）；
  - `tools/llmtool/WORKFLOW.md`（新增「Token savings measurement」，並修改 cost evaluation 的一句）。
- **方式：** 閱讀完整的 diff 和 plan；確認 `tools/llmtool/llmtool`、`tests`、`.github` 相對 main 的 diff 是**空的**。這一輪沒有執行 benchmark。
- **CI：** run `37354427553` 在 review 時仍是 **pending**（3.9 和 3.14），**尚未有 exact-head 的 CI 結果**。

## 結論：APPROVE WITH CHANGES

- 方向正確，而且對「不得宣稱節省」的紀律很好：
  - 分開報告 Codex 的節省和整個系統的 token 差異；
  - unknown／null 會傳遞下去；
  - 不用 bytes、quota、credits 去推算 token；
  - 不重複加總 cached 和 reasoning；
  - 負的結果也是有效結果；
  - 品質下降就不算成功；
  - 不新增 router、budget enforcement 或 milestone。
- 但是作為**測量契約**，有一個會讓結果無效的定義缺口（M1），以及兩個會讓結論容易被誤讀的地方（M2、M3）。建議在 merge 前補上，至少在**實際執行 pilot 之前**必須補上。

## 確認沒問題的部分

- **只有文件**：沒有修改 production、tests、CI、config、provider 或 router。
- A11b.2 和 A11c 仍然是 NOT STARTED，也沒有新增 milestone。
- **公式：** `codex_tokens_saved = control − dogfood`；`whole_system = control − (dogfood_codex + company)`；百分比以 control 為分母。只在兩邊的 total 都已知、而且可比較時才計算，否則是 unknown。
- **控制變因：** 同一個 fixture 和 bytes、同一個 repo 和 test revision、同一個 Codex model、effort 和 speed、同樣的 acceptance；每個 variant 都用全新的 session，不能互相洩漏。
- **記錄欄位：** 測量來源和精度（exact／reported aggregate／unknown）、`fixture_sha256`、`repo_sha`、tool-call 數、延遲、schema 和 identifier validity、adopt／override。
- **pilot 的規模受控：** 3 個 fixture × 1 對 = 6 個 session，並明確寫出 n=1 不能推廣。
- **資料：** 只用 synthetic、預先遮蔽的 fixture，符合 PR #34 的 F1 規則。
- **解讀：** 必須在多次 paired 樣本中，品質相當、Codex 用量明顯下降、override 不多、延遲可以接受、資料政策也符合，才能成為 routing 的候選。

## Findings

### M1（Medium，必須在執行 pilot 之前補上；建議在 merge 前補上）：沒有定義 dogfood 的 Codex 能不能看到原始 fixture

- **位置：** plan 的「Variants → Dogfood」：「the derived result is returned to Codex. Codex still owns strong judgement and final verification.」；WORKFLOW 的「Token savings measurement」段落。
- **問題：** `company-llm-delegation` 的 After delegation 第 2 步要求「Compare it with the raw command/test/log evidence before using it」，第 3 步要求「Deterministically validate every returned test/file identifier against the bounded supplied evidence」。也就是說，**依照目前的 workflow，dogfood 的 Codex 也必須讀取原始的 evidence**。
- plan 沒有寫明 dogfood 的 Codex 拿到的是：
  - (a) 只有 company 的摘要；
  - (b) 摘要加上完整的原始 fixture；
  - (c) 摘要，加上需要時才讀取的原始 fixture，或者 deterministic validator 的結果。
- **後果：**
  - 如果實際做法是 (a)，省下的 token 只是因為**跳過了 workflow 要求的驗證**，和 control 比較的不是同一個任務，比較無效。
  - 如果是 (b)，節省可能很小甚至是負的。這本身是有效的結果，但必須事先定義清楚。
- **最小修正：** 在 plan 的 Dogfood variant 和 WORKFLOW 明確規定：
  - dogfood 的 Codex 會收到什麼（建議和 workflow 一致：摘要，加上依 workflow 所需的原始 evidence 存取）；
  - identifier 和 schema 的檢查由 deterministic script 執行，結果交給 Codex（不消耗 Codex 的推理），並分開記錄 tool-call 數；
  - 「沒有依 workflow 驗證的 dogfood 結果不能用於節省的比較」。

### M2（Low–Medium）：整個系統的 token 差異把不同 tokenizer 的 token 直接相加

- **位置：** ROADMAP、WORKFLOW 和 plan 的 `dogfood_system_total = dogfood_codex_total + company_llm_total`。
- **問題：** Codex（OpenAI 的 tokenizer）和公司 model（qwen 的 tokenizer）的 token 不是同一個單位，單價也完全不同。相加之後的「whole-system tokens saved」很容易被誤讀成成本或資源的節省。
- **最小修正：** 在這三處加上一句：「whole-system delta mixes different tokenizers/models; it is a descriptive volume indicator, not a cost or compute metric — cost comparison requires per-model trusted pricing (already required elsewhere)」。另外建議同時分開列出兩邊的 total，不要只列出相加後的結果。

### M3（Low–Medium）：「品質相當」沒有事先定義的標準答案，也沒有獨立、盲測的評分

- **位置：** plan 的 B01–B03 acceptance（例如「identify the supported failure groups」「equivalent required facts/unknowns preserved」），以及記錄中的 `quality_result: pass | fail | incomplete`。
- **問題：** 沒有規定由誰、用什麼標準判定 pass，也沒有規定評分者不能知道是哪一個 variant。如果由執行 benchmark 的 Codex 自己評，結果容易有偏差。
- **最小修正：**
  - 每個 fixture 在**執行前**寫好固定的 gold labels（應該分出的群組、signature、必須保留的 unknowns、不得捏造的 identifier 清單），用 fixture digest 一起固定；
  - `quality_result` 由 deterministic 比對 gold labels，或者由不知道 variant 的獨立 reviewer 判定，並記錄評分方法。

### 其他建議（non-blocking）

- **S1：** 記錄 dogfood 那一次**所有**的 company 呼叫，包括失敗或手動重試的，以及 llmtool 呼叫本身的 Codex tool overhead，避免低估 dogfood 的成本。
- **S2：** 記錄 control 和 dogfood 的執行順序（或交替、隨機），因為 provider 端的 prompt cache 可能讓後執行的一方 cached-input 比較多。
- **S3：** 為「material」的節省訂一個事先的門檻（例如在多次 pair 中，Codex 的節省超過 X% 而且品質相當），避免看到結果之後才決定門檻。

## 判定

```text
M1: OPEN (define dogfood Codex raw-evidence access/verification; required before running the pilot)
M2: OPEN (cross-tokenizer whole-system delta caveat)
M3: OPEN (pre-registered gold labels / blinded quality grading)
NEW BLOCKERS FOR CODE/RUNTIME: 0 (docs-only)
EXACT-HEAD CI: PENDING at review time
PR #35 RECOMMENDATION: APPROVE WITH CHANGES
```

- 建議 M1、M2、M3 在 merge 前補上（都只需要改文件），然後確認 exact-head CI PASS。
- 如果選擇先 merge，**至少 M1 必須在實際執行 B01–B03 pilot 之前修正**，否則量到的節省沒有效力。
- 本報告 commit 到 PR #35 的 branch，所以 head 會從 `5b1c49d` 變成新的 commit。
- 這一輪沒有 merge，沒有執行 benchmark，也沒有開始 A11b.2 或 A11c。
