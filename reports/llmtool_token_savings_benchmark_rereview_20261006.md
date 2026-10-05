# llmtool PR #35 token-savings benchmark — targeted measurement re-review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #35，branch `docs/llmtool-token-savings-benchmark-20261006`，**exact head `2d38c584cc78343d55266e3be2500852371df373`**。
- **Base：** `main @ 769e863114d7d4abe3ab2b1b4f67d915029d2e47`（ahead 9、behind 0）。
- **對照：** 上一輪的 [review](llmtool_token_savings_benchmark_review_20261006.md)：APPROVE WITH CHANGES，M1、M2、M3 和 S1、S2、S3。
- **本輪新增的 4 個 commit：** `201e902`、`f47c009`、`b25f9d7`、`2d38c58`。
- **變更：** plan report、WORKFLOW、ROADMAP、`company-llm-delegation` SKILL.md（+135／−42）。
- **方式：**
  - 逐行閱讀 `7b7fb56..2d38c58` 的 diff。
  - 用 grep 檢查整個 repo 有沒有殘留「whole-system saving」的措辭。
  - 確認 code、tests、CI 相對 main 的 diff 是空的。
  - 查核 exact-head CI。
  - **沒有執行 B01–B03。**

## 結論

**PR #35 TARGETED MEASUREMENT RE-REVIEW: PASS**

```text
M1: CLOSED
M2: CLOSED
M3: CLOSED
S1/S2/S3: incorporated
new blockers: 0
PR #35 may merge after exact-head CI PASS: YES (exact-head CI already PASS on 2d38c58)
```

另有 4 個 non-blocking nit（N1–N4），可以在 merge 時順便修，或者放到 follow-up。

## 逐項檢查

### 1. M1：dogfood 的 Codex 和原始 evidence 的關係 — CLOSED

plan 的 Dogfood 段落、WORKFLOW、ROADMAP 和 delegation skill 都一致地定義了：

| 要求 | 結果 |
|------|------|
| Codex 一開始收到 company-derived summary 或 packet | ✓ |
| 同時收到 deterministic schema／identifier 驗證的結果 | ✓ |
| 原始 fixture 只提供 reference，**不自動放進 context** | ✓ 「The complete raw fixture is **not** automatically injected」 |
| 要採用 company-derived claim，必須先回查相關的原始 evidence | ✓ 「before Codex relies on any company-derived claim … must compare that claim with the relevant original raw evidence」 |
| 記錄實際讀取的 raw ranges 和 bytes | ✓ 新增欄位 `raw_fixture_bytes_available`、`raw_fixture_bytes_read_by_codex`、`raw_fixture_ranges_read` |
| 跳過驗證的 pair 不能用來宣稱節省 | ✓ 四份文件都寫明 |
| 機械性的驗證由 deterministic 工具執行 | ✓ 「performed deterministically outside model reasoning and their result supplied to Codex」 |

**判斷：** 這和 `company-llm-delegation` 的「Compare it with the raw … evidence before using it」一致，ground truth 沒有被削弱。同時，Codex 只讀取需要的範圍，而且有記錄，所以能公平地量出 context 和 token 的差異：dogfood 省下的是「不必讀取全部原始 evidence 並自己分群」，而不是「省略驗證」。

### 2. M2：不同 tokenizer 的指標 — CLOSED

- 公式和名稱已改成 `dogfood_mixed_token_volume`、`mixed_token_volume_delta`、`mixed_token_volume_delta_pct`（plan、WORKFLOW），ROADMAP 是「Mixed token-volume delta」，summary 表的欄位是「Mixed token-volume delta*」並加上註腳。
- 明確寫出：
  - 不同的 model 和 tokenizer；
  - 只是 descriptive volume indicator；
  - **不是 cost、compute 或 energy 的指標**；
  - Codex 和 company 的 total 必須分開顯示；
  - 貨幣比較需要另外使用可信的各 model 定價和版本。
- delegation skill 改成「Report Codex-only savings as the **primary** result … mixed … descriptive only and must not be presented as cost or compute savings」。
- grep 全 repo：只有 plan 第 6 行的 Goal 還留著「separately measuring whole-system token usage」（見 N1）；公式、表格和規則都已改名，沒有其他會被誤讀成節省的地方。

### 3. M3：品質評分 — CLOSED

- B01、B02、B03 都要求**在執行前 commit gold manifest**，並固定 fixture digest：
  - B01：group membership、allowed test／file identifiers、required unknowns、forbidden unsupported root-cause claims；
  - B02：required signatures／patterns／timeline facts、required unknowns、forbidden root-cause／command claims；
  - B03：required facts／evidence、required unknowns、forbidden promotions to fact、minimum source references。
- 能 deterministic 判定的部分用 deterministic 方式；剩下的語意部分由 **independent reviewer blinded to control/dogfood identity** 評分（WORKFLOW、ROADMAP、skill 一致）。
- 執行 benchmark 的 Codex 自己不能判定 PASS：「independent reviewer」已經排除了自評；更明確的文字見 N2。

### 4. S1：計算是否完整 — incorporated

新增 `company_call_count_all_attempts`、`company_retry_count`、`codex_tool_call_count`；plan、WORKFLOW、ROADMAP、skill 都寫明「**every** company call … including failed attempts and manual retries, plus Codex tool-call overhead」。

### 5. S2：順序和 cache — incorporated

- `run_order` 欄位，並要求「Pre-register and record the run order before execution」。
- n=1 的 pilot 只是描述性的；如果有 order 或 cache 的疑慮，就增加 **counterbalanced** paired repetitions。

### 6. S3：門檻 — incorporated

- 「This pilot does not label any saving as "material"」。
- 「n=1 per fixture cannot establish a stable routing threshold or a statistically reliable saving rate」。
- 「Any future routing threshold must be pre-registered before the repeated benchmark … do not choose the threshold after seeing the result」。

### 7. 公式和計算 — PASS

- 主要 KPI 不變：`codex_tokens_saved = control − dogfood`；`codex_savings_pct = saved / control × 100`。
- 輔助指標：`mixed_token_volume_delta = control_codex − (dogfood_codex + company)`。
- 負值是有效的結果，必須報告；unknown／null 會傳遞下去（任何一項 unknown，結果就是 unknown，不填 0）。
- cached 和 reasoning 只作 breakdown，不重複加到 source 回報的 total。
- quota%、credits、bytes、pricing 估算不能冒充 exact token，原本的規則都保留。

### 8. 範圍 — PASS

- `tools/llmtool/llmtool`、`tests`、`.github` 相對 main 的 diff 是**空的**；只修改了 4 份文件。
- 沒有 runtime router、budget enforcement 或自動選擇 provider，也沒有執行 benchmark。
- A11b.2 和 A11c 仍然是 NOT STARTED。

## Nits（non-blocking）

- **N1：** plan 第 6 行的 Goal 仍然寫「separately measuring **whole-system token usage**」。建議改成「separately reporting company-model token usage and a descriptive mixed token-volume delta」，讓全文的措辭一致。
- **N2：** 在 plan 的 Acceptance 或 WORKFLOW 加上一句明確的「the executing Codex session for either variant must not grade its own quality result」。目前由「independent reviewer」隱含排除，但沒有直接寫出。B02 寫的是「graded blind to variant identity」，也可以補上「independent」以和 B01、B03 一致。
- **N3：** B02 和 B03 的 gold manifest 可以比照 B01，列出 allowed identifiers（例如 log 中的 request ID、host 或 source reference），以便 deterministic 地檢查捏造。
- **N4：** 如果 dogfood 的 Codex 採用了「完整性」類的 claim（例如「all failures are in these 2 groups」），只回查被引用的範圍並不足以驗證「沒有遺漏」。建議由 deterministic validator 提供 coverage 檢查（例如所有失敗的 test ID 是否都出現在某個群組），並把結果交給 Codex。

## CI

| Run | head | 結果 |
|-----|------|------|
| push `37355557669` | `2d38c58` | success（3.9 和 3.14） |
| pull_request `37355562610` | `2d38c58` | success（3.9 和 3.14） |

## Merge 建議

- M1、M2、M3 都已關閉，S1、S2、S3 都已納入，沒有新的 blocker。
- exact head `2d38c58` 的 CI 已經 PASS，**可以 merge**。
- **注意：** 本報告 commit 到 PR #35 的 branch，所以 head 會從 `2d38c58` 變成新的 commit（只新增 `reports/` 底下的一個檔案）。merge 前請以新的 head 確認 CI。
- 這一輪沒有執行 B01、B02、B03，沒有 merge，也沒有開始 A11b.2 或 A11c。
