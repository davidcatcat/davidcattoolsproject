# llmtool PR #34 company dogfood skills — targeted re-review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #34，branch `feat/llmtool-company-dogfood-skills-20261006`，**exact head `8120e0e9b81e1d24f394429c4cdb37d04f91fb60`**。
- **Base：** `main @ a8bbd6de837437fb8bd60a43460b6b8af62a88b8`（ahead 27、behind 0）。
- **對照：** 上一輪的 [review](llmtool_company_dogfood_skills_review_20261006.md)：APPROVE WITH CHANGES，F1 Medium／blocker、F2 Low、F3 Low。
- **本輪新增的 6 個 commit：** `ff8151c`、`442d182`、`63fcba6`、`20e340e`、`9303496`、`8120e0e`。
- **變更：** log-triage SKILL.md、company-llm-delegation SKILL.md、README、WORKFLOW、`test_skills.py`（+4）、validation report。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree 或 clone。
  - 逐行閱讀 `b78b68e..8120e0e` 的 diff。
  - 用 grep 檢查 AGENTS、CLAUDE 和其他 skill 有沒有較弱或矛盾的說法。
  - 執行完整測試，查核 CI。
  - **targeted live re-check 只使用經過 deterministic 預先遮蔽的 synthetic evidence**；這一輪沒有再把含有 credential 的原始輸入送進 model。

## 結論

**PR #34 TARGETED RE-REVIEW: PASS**

```text
F1: CLOSED
F2: CLOSED
F3: retained non-blocking
new blockers: 0
PR #34 may merge after exact-head CI PASS: YES
```

## 逐項檢查

### 1. `tools/llmtool/llmtool/skills/log-triage/SKILL.md`

舊的「replace such values with "[REDACTED]"」已經移除，改成：

- 「Do not intentionally repeat credential-like values …」（保留的是行為提示，不是保證）；
- 「model-side redaction is **best-effort only** and must never be treated as a **security control**」；
- 「The caller must **deterministically redact/discard secrets before the provider call**」；
- 「must treat this triage output as **potentially containing any supplied value**」。

**不再把安全遮蔽的責任交給 model。** schema、禁止事項和其他段落都沒有被削弱。**PASS**

### 2. `skills/company-llm-delegation/SKILL.md`

| 要求 | 文字 | 結果 |
|------|------|------|
| 呼叫前 deterministic 遮蔽或丟棄 | 「Deterministically redact or discard … **before** the provider call」 | PASS |
| 不依賴 model 遮蔽 | 「Never rely on the model to redact its own input or output」 | PASS |
| 輸出可能包含任何輸入值 | Before 和 After 兩處都寫明 | PASS |
| 用 deterministic 方式驗證 identifier | After delegation 第 3 步：「Deterministically validate every returned test/file identifier against the bounded supplied evidence (exact/verbatim membership or another explicit deterministic rule)」 | PASS |
| 不支援的 identifier 標成 invalid 或 hallucinated | 「Mark unsupported identifiers invalid/hallucinated」 | PASS |
| 不能直接當成 locator | 「never use them directly as source locators」 | PASS |
| development report 要有 identifier validity | 「returned identifier validity / invalid identifiers where applicable」 | PASS |
| completion criteria | 新增「deterministically pre-redacted/discarded … before the provider call」和「identifiers were deterministically checked … before being used as locators」 | PASS |

### 3、4. README 和 WORKFLOW 的一致性

- **README（A3 段落）：** 「deterministically pre-redacted/discarded before any provider call. Model-side redaction is best-effort only and must never be relied on. Returned test/file identifiers must be checked deterministically …」
- **WORKFLOW 的 dogfood 步驟：**
  - 第 2 步先 deterministic 遮蔽，並寫明「model-side redaction is not a security control」；
  - **新增第 4 步「Validate derived identifiers」**；
  - 第 6 步的 evidence 加上 identifier validity。
- 用 grep 檢查 AGENTS、CLAUDE 以及 debug-ci、qa-validation、implement-feature 這 3 個 skill：**沒有任何較弱或矛盾的 redaction 說法**（這 3 個 skill 都只引用 delegation skill；qa-validation 裡的「verify promised redaction」指的是產品的 QA，和這裡無關）。
- `test-triage` 從來沒有承諾遮蔽，和 delegation skill 的規則一致。

**PASS**

### 5. `tools/llmtool/tests/test_skills.py`

- 只對 `log-triage` 新增 3 個 `assertIn`，分別固定「best-effort only」「deterministically redact/discard secrets before the provider call」「potentially containing any supplied value」。
- 原本的 4 個邊界 assertion 都保留；**沒有刪除或放寬任何測試**。
- 這是防止退化的靜態測試，合理。**PASS**

### 6. Validation report

- 新增「Independent review/live dogfood update」，**如實保留**上一輪的觀察：
  - 「model-side credential redaction was unreliable (3/3 credential-bearing log-triage samples reproduced at least one supplied secret)」；
  - 「one test-triage scenario fabricated unsupported file identifiers and over-grouped distinct failures」。
- 明確寫出這次修正是「contractual/deterministic at the caller boundary, **not a claim that the model became more reliable**」。
- 沒有被改寫成「model 現在不會發生」。

**PASS**

## Targeted live re-check（公司 gateway `gw`，HTTP 200）

| Case | 方法 | 結果 |
|------|------|------|
| log-triage + **deterministic 預先遮蔽** | 用 3 條 regex（Bearer、URL 中的密碼、`key=value` 型的 secret）把上一輪的 auth log 遮蔽後再送出；**送出的輸入中 `QA34` marker 為 0** | 2／2 rc 0；schema 完全符合；**輸出中沒有任何 secret**；`strong_review_reasons` 有 1–2 項；2.2 秒、約 1160 tokens |
| test-triage + **deterministic identifier 驗證** | 上一輪的 race 加權限 case；把回傳的每個 `tests` 和 `files` 和輸入做 verbatim 比對 | 2／2 rc 0。model **仍然**產生 `bank/account_test.go`、`bank/auth_test.go`，但被 deterministic 比對**每次都抓到並標成 INVALID**，不會被當成 locator。這次分成 2 組（比上一輪細） |

**解讀：** 修正有效，但它是 caller 端的控制。model 本身的限制沒有改變：仍然會捏造路徑，遮蔽也不可靠。也正因為如此，必須用 deterministic 的方式處理。上表證明了這套 workflow 實際可用。

## 邊界確認

- 沒有修改任何 Python production code：`tools/llmtool/llmtool`（skills 資源除外）相對 main 的 diff 是空的。
- 沒有 production router、executor、自動 retry 或 fallback，也沒有 mutation 或 approval 的權限。
- A11b.2 和 A11c 仍然是 NOT STARTED。
- provider ≠ skill：triage skill 仍然是 provider-agnostic 的 prompt 資源，由 `--provider` 明確選擇。

## F3

保持 **known non-blocking limitation**：`ASK` 的基本 prompt 和 triage 的「只輸出 JSON」要求之間可能有衝突。這一輪 4／4 都是乾淨的 JSON，仍然沒有觀察到衝突。

## Regression 與 CI

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | **1021 tests OK**（1 skipped） |
| Python 3.9.6 | **1021 tests OK**（1 skipped） |
| GitHub Actions | PR run `37351608678`（headSha `8120e0e`）**success** |

## Merge 建議

- F1 和 F2 都已關閉，沒有新的 blocker。
- **exact-head CI PASS 之後就可以 merge。**
- **注意：** 本報告 commit 到 PR #34 的 branch，所以 head 會從 `8120e0e` 變成新的 commit（只新增 `reports/` 底下的一個檔案）。merge 前請以新的 head 確認 CI。
- 這一輪沒有 merge，也沒有開始 A11b.2。
