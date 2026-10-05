# llmtool PR #33 development-time cost-aware dogfood — independent review（2026-10-06）

## 範圍與方法

- **審查對象：** Draft PR #33 `docs(llmtool): add deterministic-first company LLM dogfood workflow`，branch `docs/llmtool-cost-aware-dogfood-20261006`，**exact head `9290e822bbc1b0cf45abcdf5ba0afba7812776d5`**。
- **Base：** `main @ e5525f18afbb76d457868d18ee90d56bd06b4f66`（PR #32 A11b.1 的 merge commit）。
- **方式：**
  - fetch 之後確認 head 和 base；閱讀完整的 PR diff 和 planning report。
  - 用 `git show` 讀取 head 上的 AGENTS.md、CLAUDE.md、README、ROADMAP、WORKFLOW，檢查 status 是否一致。
  - 對照 `git_intel.py` 確認 A5 的實際能力。
  - 查核 exact-head 的 CI。
- 只讀；沒有修改任何 code，沒有 merge，沒有開始 A11b.2。

## 結論

**PR #33 INDEPENDENT REVIEW: PASS**

- 沒有 blocker。
- exact head `9290e82` 的 CI 已經通過，可以 merge。
- 建議 merge 前先修 F1（只是幾行文字），或者開一個 follow-up。

## 基本檢查

| 項目 | 結果 |
|------|------|
| diff 範圍（相對 `origin/main`） | 剛好 10 個檔案：AGENTS.md、README.md、`tools/llmtool/README.md`、ROADMAP.md、WORKFLOW.md、`skills/{implement-feature,debug-ci,inspect-repo,qa-validation}/SKILL.md`、`reports/llmtool_cost_aware_dogfood_planning_20261006.md` |
| production、tests、CI、config | `git diff --stat origin/main 9290e82 -- tools/llmtool/llmtool tools/llmtool/tests .github tools/llmtool/pyproject.toml` 是**空的** |
| 同步 main 的方式 | `ac0f2a4` 是正常的 two-parent merge；ahead 19、behind 0；沒有 rebase 或 force push |
| PR #32 是否被反向修改 | 沒有：A11b.1 的 source 和 tests 和 main 完全相同 |
| exact-head CI | PR run `37345667038`（headSha `9290e82`）：`check (3.9)`、`check (3.14)` 都是 success，Offline canonical checks 和 Working tree is clean 都是 success |

## Findings

### F1（Low，non-blocking，建議 merge 前修）：`tools/llmtool/ROADMAP.md` 還有 3 處舊的「A11b.1 current」status

- 頂部的 snapshot 表已經正確（「A11b.1 DONE/merged via PR #32 `e5525f18`; A11b.2 NOT STARTED」），但同一份檔案裡還有和它矛盾的地方：
  - 第 513 行，A11 milestone 表：`| A11b — IN PROGRESS; A11b.1 current |`
  - 第 685 行：`The current A11b.1 slice remains data-only; …`
  - 第 687 行，章節標題：`### A11b.1 — Typed Action & Approval Contract — CURRENT / IN PROGRESS`
- **原因：** 這個 PR 的目的之一就是同步 status，讀者會在同一份文件裡看到互相矛盾的狀態。
- **最小修正：**
  - 第 513 行改成 `A11b — IN PROGRESS; A11b.1 DONE/merged (PR #32); A11b.2 NOT STARTED`；
  - 第 685 行改成 `The merged A11b.1 slice remains data-only`；
  - 第 687 行改成 `— DONE / merged via PR #32`。

### F2（Low，non-blocking）：CLAUDE.md 沒有同步更新

- 第 36–38 行仍然寫「A11b.1 … as the current data-only slice」。
- AGENTS.md 新增了指向 dogfood workflow 的說明，CLAUDE.md 沒有，所以 Claude 這一側的指引不一致。
- **最小修正：** 把那一句改成「A11b.1 DONE/merged via PR #32; A11b.2 NOT STARTED」，並加上和 AGENTS.md 相同的 2–3 行 dogfood 指引。CLAUDE.md 不在這個 PR 預期的檔案清單內，也可以放到 follow-up。

### S1（建議，non-blocking）：delegation evidence 可以再補兩個欄位

- **data permission basis：** 這份 evidence 為什麼可以送給公司 LLM。
- **development report 的 redaction 規則：** 不能貼原始的 secret 或完整的 log，只記錄 reference 或 digest。

## 14 項檢查

1. **A11 status：** snapshot 表、`tools/llmtool/README.md`、WORKFLOW 和 root README 都正確寫出 A11b.1 已透過 PR #32 `e5525f18` merge，A11b.2 和 A11c 都尚未開始。舊的文字只在 F1、F2 指出的地方。
2. **順序：** WORKFLOW、ROADMAP 和 report 都維持 **deterministic → 透過 llmtool 的公司 LLM → strong verifier**。**PASS**
3. **deterministic 工作優先：** tests、lint、typecheck、build、exit 和 schema check、A5 `llmtool git --json`、Git／GitHub／CI 的 structured query，都明確寫明不送 LLM。**PASS**
4. **A5 的能力邊界：**
   - 對照 `git_intel.py`：A5 只收集 `rev-parse`（root 和 HEAD）、`branch --show-current`、`status --porcelain=v1`（含 untracked）、`diff`／`diff --cached`（上限 2 MiB）、`log --max-count=10`，**沒有 upstream 或 ahead／behind**。
   - 文件寫的範圍是「branch/HEAD/status/diff/recent-commit」，而且 inspect-repo 明確寫出它「does not replace fetch/upstream/PR/CI checks」。
   - **PASS**
5. **公司 LLM 允許的工作：** summary、failure clustering、stack-trace extraction、simple classification、baseline comparison、checklist presence，而且限定為 bounded、permitted。**PASS**
6. **公司 LLM 禁止的事：**
   - 明確禁止：修改 code、把 root cause 當成事實、security／auth／permission 的判斷、concurrency／race／data corruption 的判斷、決定 test 是否正確、宣告 PASS／SAFE／DONE／merge-ready。
   - 「company-model confidence 不能授權升級、完成、修碼或 PASS」，A11c 的部分也寫明「不得以 model self-confidence 選 lane」。
   - **PASS**
7. **原始 evidence 是 ground truth：** 「original command/exit/raw evidence remains authoritative」「never overrides a command exit/test result」。**PASS**
8. **既有的邊界沒有被削弱：**
   - 多處明確寫出不加 executor、不自動 retry／fallback／repair、不做任何修改、不增加新的 agent authority。
   - 只能「through llmtool」呼叫，不能直接呼叫 gateway 來做委派的工作。
   - A5、A6、A9、A10 的邊界都沒有被削弱。
   - **PASS**
9. **A11c 的 routing 只是規劃：** 寫的是「A11c production router 應…」，並且要求在 routing 之前先用固定的 fixture 和獨立的 label 評估。沒有宣稱已經實作。**PASS**
10. **collector：** 「deterministic capability，不是新的 agent」，資料收集成功也不代表授權 Git mutation。**PASS**
11. **delegation evidence：** 8 個要求的欄位都有（task type、provider／profile、input scope／bytes、tokens 或 unknown、latency、schema validity、是否採用或覆寫、之後的 deterministic／independent verification 結果）。也寫明「被採用」不等於正確，而且不是新的 diagnostics schema。**PASS**（建議見 S1）
12. **沒有宣稱節省：** 只寫了「減少負擔」的目的，沒有量化的數字；也延續了「沒有成本資料時只是 routing preference，不是節省證據」的說法。**PASS**
13. **skills：** implement-feature、debug-ci、inspect-repo、qa-validation 這 4 個 skill 的修改都只是指向 workflow，並且重申 root cause、PASS／FAIL、acceptance 由 strong verifier 負責。quality-gate 的權限沒有改變。**PASS**
14. **PR #32 沒有被反向修改：** production 和 tests 的 diff 是空的。**PASS**

## 其他觀察（正面）

- VPN 或公司 provider 無法使用時，標記為 skipped／blocked、不當成產品的 failure。
- 文件明確寫出「不因公司 LLM 失敗就把資料改送到外部 provider」，符合 QG-S1 的 R-EXP。
- 「Never weaken acceptance criteria to make the cheap path usable」。

## Merge 建議

- exact head `9290e82` 的 CI 已經 PASS，可以 merge。
- 建議先修 F1（ROADMAP 的 3 處）。F2、S1 可以放到 follow-up。
- 補上 F1 之後，head 會改變，需要以新的 head 重新跑一次 CI。

**注意：這份 review report 本身是 commit 到 PR #33 的 branch 上**，所以 PR 的 head 會從 `9290e82` 變成新的 commit。這個 commit 只新增 `reports/` 底下的一個檔案，production、tests 和其他文件都不受影響；merge 之前請以新的 head 確認 CI。

這一輪沒有修改 production code，沒有 merge PR #33，也沒有開始 A11b.2。
