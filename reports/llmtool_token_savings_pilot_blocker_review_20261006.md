# llmtool PR #36 token-savings pilot preflight blocker — review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #36 `docs(llmtool): record token savings pilot preflight blocker`，branch `bench/llmtool-token-savings-pilot-20261006`，**head `9a17ef5bd42a6181b8be4fe9cf828789ef9dd921`**。
- **Base：** `main @ f4d1019`（PR #35 已 merge）；ahead 1、behind 0。
- **變更：** 只新增 `reports/llmtool_token_savings_pilot_20261006.md`（176 行）。
- **方式：**
  - 閱讀完整的 report，並對照 [benchmark plan](llmtool_token_savings_benchmark_plan_20261006.md) 和 WORKFLOW 的「Token savings measurement」。
  - 確認 diff 只有這一個檔案。
  - 這一輪沒有執行 Codex 或 company 呼叫，也沒有執行 benchmark。

## 結論：APPROVE（作為 blocker 記錄）

- report 如實、保守，完全符合 PR #35 的測量契約：
  - 6 個 session 都標為 NOT RUN，company call 是 0；
  - 所有結果都是 unknown，沒有用欄位相加推算 total，沒有用 quota、credits 或 bytes 冒充 token；
  - 明確寫出「Can claim token savings: NO」；
  - preflight 不計入 benchmark；
  - 只把 config 當成 request evidence，不當成 runtime 的 model attestation。
- **blocker 本身成立**：契約要求「source-reported total，或明確記錄並被接受的 accounting mapping」，目前兩者都沒有。
- 下方提出解除 blocker 的建議（U1–U3），讓 coordinator 決定。

## 確認沒問題的部分

| 項目 | 結果 |
|------|------|
| 範圍 | 只有 1 份 report；沒有 production、config、tests、CI 的變更；沒有建立 profile；`case.*` 和既有的 artifact 都保留 |
| 測量來源 | `codex exec --json / turn.completed`，保留原始欄位名稱（`reasoning_output_tokens`、`cache_write_input_tokens` 沒有被改名），缺少的欄位是 unknown 而不是 0 |
| 隔離 | `--ephemeral --ignore-user-config --ignore-rules`、`--sandbox read-only`、`approval_policy="never"`，從 canonical repo 執行，不 resume |
| 證據 | 原始 JSONL 只保留 SHA-256，沒有 commit 完整的 trace 或 credential |
| 計畫的順序 | B01 C→D、B02 D→C、B03 C→D，已事先記錄（S2） |
| CI | report 如實寫出「no checks/runs reported for this report-only HEAD; no CI PASS claimed」。原因是 CI 的 path filter 只涵蓋 `tools/llmtool/**`，`reports/` 不會觸發 CI，這是正確的行為 |
| 範圍邊界 | 沒有 router、retry／fallback、A11b.2、A11c，也沒有 merge-readiness 的宣稱 |

## 解除 blocker 的建議（non-blocking，給 coordinator 決定）

### U1：用有文件依據的 mapping 定義 comparable total（需要先驗證，再寫進契約）

- OpenAI Responses API 的慣例是：`total_tokens = input_tokens + output_tokens`，cached input 是 `input_tokens` 的子集，reasoning 是 `output_tokens` 的子集。
- 如果能從 Codex CLI 的**原始碼或官方文件**確認 `turn.completed.usage` 遵循同樣的語意（`cached_input_tokens ⊂ input_tokens`、`reasoning_output_tokens ⊂ output_tokens`），就可以把 `codex_total := input_tokens + output_tokens` **以 PR 的方式**寫進測量契約，並附上出處。這正是 plan 允許的「explicitly documented and accepted accounting mapping」。
- **未經驗證之前不能直接採用**。PR #36 沒有自行假設，這是正確的做法。

### U2：不需要 total 的替代 KPI

- 契約的主要 KPI 是用 total 計算的。但 `input_tokens` 和 `output_tokens` 都是 exact 的 source 欄位，可以預先登記改成「分別比較 input 和 output（以及 non-cached input = input − cached，前提是確認 cached 是子集）」，不需要 total。
- 這需要**在執行前**修改契約，不能在看到結果之後才改。

### U3：注意固定的開銷

- preflight 只是一個簡單的 prompt，就用了 **17,473 input tokens**（其中 12,288 是 cached），代表 Codex 每個 session 有大量固定的 system 和工具描述開銷。
- B01–B03 如果用很小的 fixture，dogfood 能省下的部分會被這個固定開銷掩蓋，百分比會看起來很小。
- **建議：** 在 per-run record 裡記錄 baseline 的固定開銷（例如用相同設定執行一次空任務，並固定這個測量），並在解讀時另外列出「扣除固定開銷後的差異」作為描述性指標，不取代主要的 KPI。fixture 的大小應該接近實際的使用情境。

## 判定

```text
PR #36 REVIEW: APPROVE (accurate blocker record)
Blocker valid: YES (no source total, no accepted mapping)
Token savings claim: NO (correctly)
Unblock options: U1 verified Codex usage semantics -> documented mapping;
                 U2 pre-registered input/output-split KPI; plus U3 fixed-overhead note
New blockers in PR #36 itself: 0
```

- 這是只有 report 的 PR，CI 不會觸發是預期的行為。可以依 repo 的慣例 merge，或者保持 Draft，直到 U1 或 U2 決定之後一起處理。
- 本報告 commit 到 PR #36 的 branch（只新增 `reports/` 底下的檔案）。這一輪沒有執行 benchmark，沒有 merge，也沒有開始 A11b.2 或 A11c。
