# llmtool PR #39／#40 merge 後 — routing policy 可行性 review 與修正建議（2026-10-07）

## 範圍與方法

- **repo 狀態：** `main @ a6c3d79`。我上一輪 review 之後，有兩個 PR 已經 merge：
  - **PR #39（P1）：**
    - 加入了我要求的說明：`b1d4527` 在 validation report 中加入「Historical frozen-benchmark compatibility」，`653e998` 在 round3 README 中加入「Historical reproduction notice after P1」；
    - 兩者都明確寫出 Round 3 manifest 在 main 上會 fail closed、只在 `59ef2b7` 有效，以及不能重新產生 manifest。**我上一輪的文件修正要求已經關閉。**
  - **PR #40：** 依照 Round 3 的證據修改 routing policy。只修改文件：
    - 根目錄的 README；
    - `skills/company-llm-delegation/SKILL.md`；
    - `tools/llmtool/README.md`、`ROADMAP.md`、`WORKFLOW.md`。
- **方式：**
  - 逐行閱讀 PR #40 的 diff 和 merge 之後的 skill 全文；
  - 用以下資料檢查 policy 實際能否照著做：Round 1 pilot（PR #36）、Round 2 L01 的實際 pipeline、`llmtool` 的預設 config（`config.py`）和 CLI flag；
  - 在所有 guidance 檔案中搜尋殘留的舊用詞。
- **沒有做的事：** 只讀；沒有呼叫 company 或 Codex，沒有修改程式碼或 skill，沒有 merge。本報告放在新的 docs branch 上。

## 結論

**方向正確，但「`test-triage` = 已驗證的 token-saving route」這個說法，在目前的 skill 用法下無法重現。需要修正（F1）。**

PR #40 正確地把「company-first」改成「route-by-task evidence」：

- `log-triage` 和 `context-pack` 標為 EXPERIMENTAL；
- 新的 route 必須先通過 Company-only Phase 0；
- P1 只改善 telemetry；
- 兩個 L01 的觀測值不合併；
- L03 的「support」和「record-citation」語意不一致被寫進了 ROADMAP。

這些都和 Round 2、Round 3 的 review 結論一致。

## F1（必須修正）：「test-triage 已驗證」所指的 pipeline，和 skill 實際教的用法不同

**已驗證的 L01 pipeline**（Round 2 Stage A 和 Stage B，兩次分別省下 39.82% 和 37.55%）包含以下**全部**條件：

1. **大型的原始 test 輸出**（48 KiB）完整交給 company，而不是只給最少的 evidence；
2. **確定性的 coverage 檢查：** company 回傳的 failure ID 必須等於 fixture 中完整的 FAILURE 集合，不能多，也不能少；
3. **確定性的 selector：** 依 anchor 取 ±2 行，上限是 `min(32 KiB, 20%)`。實際選出了 1,768 bytes，占 3.6%；
4. Codex 收到的是 **company 摘要加上選出的原文**，直接放在 prompt 中，**0 次 tool call**，而且**不會再讀取完整的原始輸出**；
5. company 的設定是 `enable_thinking=false`、temperature 0、**max_tokens 4096**、timeout 120；
6. fixture 是 **synthetic 的 canonical 格式**（`FAILURE id=…` 行）。

**skill 和 WORKFLOW 目前教的用法：**

- 「Before delegation」：*Bound the input to the **minimum evidence** needed: … failing test names, selected stack trace/error lines … small necessary snippets.*
- 「Invocation」：`llmtool ask --provider PROFILE --skill test-triage --file …`。沒有 coverage 檢查，沒有 selector，也沒有指定 max_tokens。
- 「After delegation」步驟 2：*Compare it with the raw … evidence before using it.* 也就是說，Codex 仍然會讀取原始輸出。
- `llmtool` 的預設是 **`max_tokens: 2000`**（`config.py` 第 27 行），不是驗證時用的 4096。

**這正是已經被量測過、而且結果是負的那種配置：**

- **Round 1 pilot 的 B01**（`test-triage`、小型 bounded evidence、Codex 有 1 次 tool call）：control 18,452 對 dogfood 37,517，**−103.32%**。
- B01 第一次呼叫在預設設定下就因為 **`MODEL_OUTPUT_INCOMPLETE`** 失敗，之後用 `--max-tokens 4096` 手動重試才 PASS。
- preflight 的 linear fit 顯示，每個 Codex session 大約有 **16.9k tokens 的固定開銷**。原始輸出越小，dogfood 越不可能省錢。

**所以：** 照目前的 skill 指示操作（最小化輸入、沒有 selector、Codex 回頭讀原文、預設 2000 tokens），**最可能得到的是 Round 1 的負結果，而不是 Round 2 的 38–40%**。把它寫成「preferred company-delegation route」、「only evidence-backed token-saving path」，會誤導使用者。

**修正建議（只修改文件，由你決定是否開 PR）：**

1. **修正說法：** 改為「`test-triage` 的 token 節省**只在 benchmark pipeline 中驗證過**：大型（約 48 KiB）canonical synthetic test 輸出 + 確定性的 coverage + selector + 直接注入選出的原文。**一般的 `ask --skill test-triage` 用法沒有節省的證據**；Round 1 的小型 evidence 是 −103%」。
2. **在 skill 中加入適用條件**（作為 routing 的前提，不是新的功能）：
   - 原始 test 輸出必須夠大。已驗證的大小是 48 KiB；小型輸出應該直接交給 Codex 讀取；
   - company 必須回傳 failure ID；ID 必須經過確定性的成員檢查，而且數量必須完整（現在的「After delegation」步驟 3 已經有成員檢查，但沒有要求**完整性**）；
   - Codex 只能讀取「摘要加上依 ID 選出的原文片段」，**不能再讀取完整的原始輸出**，否則節省會消失。
3. **在「Invocation」中寫出驗證時的設定：**
   - `--max-tokens 4096`（或者在 profile 中設定）；
   - profile 中設定 `enable_thinking: false` 和 temperature 0。

   否則會遇到預設 2000 tokens 造成的 length 失敗（Round 1 B01 已經發生過）。
4. **把落差列為工作項目：**
   - coverage 和 selector 目前只存在於 `reports/token_benchmark/20261006_round2/stage_a/` 的 frozen 研究程式碼中，而且只支援 canonical 的 `FAILURE id=` 格式；
   - 真實的 pytest／unittest 輸出（N1）**從來沒有驗證過**。

   如果要讓 dogfood 真的省 token，必須另外立項：一個確定性的、支援真實 test 格式的 excerpt selector，搭配 Company-only Phase 0 和一次小型的 paired 驗證。在那之前，A11c 不應該把 `test-triage` 列為 production 的 token-saving route。

## F2（建議）：殘留的舊用詞和沒有同步的 guidance

- `tools/llmtool/WORKFLOW.md` 第 180 行仍然寫著「assigns … **cheap-first integration**, independent verification and bounded repair to A11c」。PR #40 的 `830fa4b`（remove stale company-first A11c wording）**漏掉了這一處**，建議改成「route-by-task integration」。
- `CLAUDE.md` 第 43–45 行和 `AGENTS.md` 第 70–74 行，仍然只描述一般的「bounded company LLM triage」，沒有 route 的分級。它們本身沒有錯，但建議加一句「routing evidence 依 route 區分；見 `company-llm-delegation` 的 Evidence status by route」，以免新的 session 只看到 CLAUDE.md 就假設所有 route 都適用。

## F3（建議）：P1 的效果要寫進 skill 和 Round 4 的條件

- skill 的「Development report」寫著「provider-reported token usage, or `unknown`」。P1 之後，**parse 成功的失敗回應也可能帶有 usage**（`--verbose` 時會出現在 `provider.response` event 中）。建議改為：「成功或可 parse 的失敗都記錄 usage；只有 transport、invalid JSON、超過大小上限時才記為 unknown」。
- ROADMAP 對 Round 4 的要求是「complete/valid usage accounting」，但**沒有寫出 wrapper 必須讀取 `LLMToolError.provider_metadata`**。Round 3 的 wrapper 在失敗時只取得 safe code，如果沿用同樣的寫法，P1 對 benchmark 的 accounting 不會有任何幫助。建議在 Round 4 的前提中明確寫出這一點。

## F4（確認）：其他的內容正確

- `log-triage` 標為 EXPERIMENTAL，原因是 1 次 PASS、1 次 length。`context-pack` 標為 EXPERIMENTAL，原因是 recall 3／7，以及 support 和 citation 的語意不一致。兩者都和 diagnosis 一致。
- 「新的 route 必須先通過 Company-only Phase 0；FAIL 或 accounting 不完整就停止，不能跑 paired benchmark」：同意。
- 「Round 4 是 optional，不阻擋 A11b.2」：同意。我建議下一個工程的重點回到 **A11b.2**。token-savings 研究應該先暫停，等 F1 第 4 點的 selector 立項之後再繼續。
- A11c 不採 blanket company-first，改為每個 task class 都需要自己的證據：同意。

## 判定

```text
POST-PR#39/#40 ROUTING-POLICY FEASIBILITY REVIEW:
DIRECTION CORRECT — ONE FEASIBILITY CORRECTION REQUIRED

P1 documentation request (previous review):
CLOSED — b1d4527 validation note + 653e998 round3 README historical notice (fail-closed on main, valid at 59ef2b7, no regeneration)

F1 [correct before relying on it]:
"test-triage = validated/preferred token-saving route" is not reproducible with the skill's current instructions.
  Validated pipeline = large (48 KiB) canonical raw output + deterministic full-set coverage + ±2-line selector +
  inline selected raw (0 tool calls, no full-raw re-read) + thinking=false/temp 0/max_tokens 4096.
  Skill instructs minimum bounded input, plain `ask` (default max_tokens 2000), then raw re-reading — the Round-1 B01
  configuration measured at −103.32% (and a length failure at defaults).
  Fix: scope the claim to the benchmark pipeline; add preconditions (large raw output, complete ID coverage,
  excerpt-only consumption, validated generation settings); track a real-format deterministic excerpt selector as
  separate future work before any A11c token-saving routing.

F2 [recommended]: WORKFLOW.md:180 still says "cheap-first integration"; add route-tier pointer to CLAUDE.md/AGENTS.md
F3 [recommended]: skill report should record P1 failure usage; Round-4 wrapper must read LLMToolError.provider_metadata
F4: log-triage/context-pack EXPERIMENTAL, Company-only Phase 0 first, Round 4 optional — AGREE; prioritize A11b.2

Changes made by this review:
NONE (report only, on docs branch); no company/Codex calls; no merge
```

- 本報告 commit 到新的 branch `docs/llmtool-route-policy-feasibility-review-20261007`（從 `main @ a6c3d79` 建立），並且 push。沒有修改程式碼、skill 或 guidance，沒有 merge，也沒有開始 A11b.2、A11c 或 Round 4。
