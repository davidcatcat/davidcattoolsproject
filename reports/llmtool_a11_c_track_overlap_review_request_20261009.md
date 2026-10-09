# llmtool A11 與 C 交付分期：重疊、依賴、優先順序審查請求

> **狀態：供 Claude 獨立審查的提案／問題整理，不是已核准 Roadmap 變更。**
> 日期：2026-10-09
> 撰寫：AI Coordinator（ChatGPT）；**未經 Claude 審查**
> 查核基準：`davidcatcat/davidcattoolsproject` 的 `main@7fcf4f8015b1ebae1e956647b83f812589c3a8e4`
> 變更類型：僅新增此報告，**不修改功能、Roadmap、Skills、Issue 或執行腳本**。

## 1. 使用者原話及協調者的兩種排序（需要嚴格分開）

以下保留可以在專案對話紀錄中直接核對的**使用者逐字發言**。
它們是需求／問題來源，**不是**使用者指定的技術工作排序。

**2026-10-08，使用者對 llmtool 上線與成本的原始需求：**

> 這個協同腳本是目前臨時的, 後面等llmtool能上線跟codex cli, claude cli協作開發自己llmtool, 我要改成用llmtool來實作, 所以我的問題是什麼時候可以把llmtool上線, 目的是為了節省token, 這點

**2026-10-08，使用者對人工轉交提示的抱怨：**

> 因為你又跟之前一樣叫我手動貼提示給claude和codex

**2026-10-09，使用者先問實際開發進度：**

> 所以現在是做到A11的哪裡了

**2026-10-09，使用者隨後質疑 A/C 規劃的重複：**

> 我以為C在A的時候就討論完了

這些原話的要求是：**說清楚 A11 到哪裡、既有 C 討論與 A 的
關係、何時可讓 llmtool 自己協調 AI 開發並節省 Token**，
而不是要求額外啟動 C 專案，也不是親自排定 C1b／A11b 先後。
目前未在可核對的上述原話中找到使用者明確說
「先做 C1b.0、後 A11b」或「立即暫停 C1b、先做 A11b.2.2.2」。
**不可把協調者的排序直接當成使用者指令。**

**協調者（ChatGPT）自己的排序變動，而非使用者原話：**
同一段後續回答中，協調者先主張「優先 C1b.0，再推進 A11b」，
之後改為「優先恢復 A11b.2.2.2，C1b 不整條阻擋 A11」。
這反映協調者的規劃未一致，應由 Claude 對照技術依賴、風險、
既有 Roadmap 及使用者原始目標做獨立校正。
兩個版本**都不能因為最後一次聊天答覆而自動成為正式 Roadmap 決議**。

**文字來源限制：**引文取自可見的專案歷史對話摘錄及本次對話；
本報告並未取得可重放的完整歷史聊天匯出，
不得據此斷言使用者過去從未在其他對話要求不同排序。

### 原始產品目標

- 使用者提出功能／痛點並進行使用驗收；AI 擔任 PM／Tech Lead／開發／獨立 QA。
- `llmtool` 逐步能協調 Codex CLI 開發、Claude CLI 審查、測試與有限修復，
  然後由協調者依既有授權、安全與 CI 門檻做 Git/PR 管理；
  使用者只在必要時協助操作 Mac，而非傳遞 Codex／Claude 提示。
- 希望減少付費模型 Token，能讓公司 LLM 在**被允許的資料邊界內**
  協助初步分析，但不能為了節省 Token 犧牲品質、安全或使上線無限延後。
- `ai-loop` 是過渡工具，**不是**應持續擴充的第二套永久 orchestration 產品。
- 通用 Git Merge 委託**不等於**允許 Mac 執行新版控制器、
  新供應商資料外送、無限制 shell／Git mutation 或略過必須的獨立門檻。

## 2. 已查核的實際狀態（與產品願景分開）

| 軌道 | 目前狀態（截至上述 SHA） | 來源／限制 |
| --- | --- | --- |
| A0–A10 | Roadmap 標記完成；A2.3 Anthropic HTTP provider 仍是規劃缺口 | [Roadmap](../tools/llmtool/ROADMAP.md) |
| A11a | DONE / merged；診斷與觀測能力已建立 | [Roadmap](../tools/llmtool/ROADMAP.md) |
| A11b.1 | DONE / merged；typed Action／Approval **資料契約**，不等於執行授權 | [PR #32](https://github.com/davidcatcat/davidcattoolsproject/pull/32) |
| A11b.2.1 | DONE / merged；唯讀 Git preflight | [PR #43](https://github.com/davidcatcat/davidcattoolsproject/pull/43) |
| A11b.2.2.1 | DONE / merged；**synthetic** supervisor／child containment | [PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49)，merge `eb464628dd6096811c659cfdf663f364107a6ef4`；不是 real Git ADD 授權 |
| **A11b.2.2.2** | **尚未實作、尚未授權真實 Git ADD**；先需獨立設計與安全審查 | [Roadmap](../tools/llmtool/ROADMAP.md)；[MVP](../tools/llmtool/NATIVE_SELF_DEV_MVP.md) |
| A11b 後續／A11c | A11b 未完成；A11c **NOT STARTED**，真實自主開發、修復與 cutover 尚未驗收 | [Roadmap](../tools/llmtool/ROADMAP.md) |
| C0 | 已有 A2/A5/A9/A10 可供唯讀 dogfood 的**基礎能力**；不能宣稱完整實測 ROI | [MVP](../tools/llmtool/NATIVE_SELF_DEV_MVP.md) |
| **C1a** | DONE / merged；明確啟用的本地 `unittest`／`pytest` failure excerpts | [PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55)，merge `27bee10a9ffbab90fd088172ef3aea03fde80556`；JUnit XML 不支援 |
| **C1b.0** | [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) 已建立、**未實作**；處理截斷後輸出重新檢查敏感指標 | [Issue #63](https://github.com/davidcatcat/davidcattoolsproject/issues/63) 的三個 DEFER 之一 |
| C1b Phase 0／C1c | **NOT STARTED**；公司 LLM 品質試驗與付費模型節省量測不能稱已達標 | [MVP](../tools/llmtool/NATIVE_SELF_DEV_MVP.md) |
| C2／C3 | 原生 Codex 開發執行、Claude 審查修復及 cutover 的**產品交付描述**；尚無正式完成／上線證據 | [MVP](../tools/llmtool/NATIVE_SELF_DEV_MVP.md) |
| `ai-loop` | 暫時性控制器 [PR #45](https://github.com/davidcatcat/davidcattoolsproject/pull/45) Draft；被凍結的 QA Low 修正 [PR #62](https://github.com/davidcatcat/davidcattoolsproject/pull/62) 已關閉未合併 | 不可偷換成已上線的 llmtool native executor |

**證據界線：**此表是 GitHub 文件、Issue 與 PR 的狀態核對；
未在本次報告實際執行 Mac CLI、VPN／公司 LLM、程式碼測試、
獨立 Claude Security/Functional QA 或 Token 成本實驗。
不要從過往 synthetic PASS 推論 real-mutation PASS。

## 3. A 與 C 的關係：不能完全畫等號

**較精確的初步判斷：A 是功能／架構與安全里程碑；
C 是 2026-10-09 在既有 A 功能之上提出的「cost-first native self-development」
交付切片及驗收順序。** 兩者大量重疊，但不是兩個全新架構，也不是純名稱替換。

| C 交付切片 | 使用哪些 A 能力 | 不能誤解的地方 |
| --- | --- | --- |
| C0 — read-only dogfood | A2.4／A2.5、A5、A9、A10、A11a | 可利用已有能力；並非已完成 token-savings 驗證或 real execution |
| C1 — deterministic excerpts + Phase 0 + ROI | 借用既有安全／日誌／成本證據機制；C1a 另新增實際產品功能 | C1a 已合併，不能說「C1 全部早已在 A 實作」；C1b／C1c 實驗尚未做 |
| C2 — one native coding vertical slice | **A11b** 的 Action、Policy、Executor、containment、audit、approval | 不是開另一套 C executor；也不是 A11b.2.2.1 synthetic PASS 就可 real Git ADD |
| C3 — cutover/real acceptance | **A11c** 的多 Agent 真實協作與驗收 | 還加入品質等價、成本效果、取代臨時 runner 的產品驗收要求 |

**需請 Claude 具體確認：**C 系列最初何時形成、是否真的有新的必要產品驗收範圍；
哪一些 C 指標已在既有 A Roadmap／Skills 記錄，哪些是 PR #50 後才提出的新切片。
若有重疊，應避免同一項測試或門檻在 A/C 建兩份看似獨立的待辦與程式碼。

## 4. 真正的依賴：阻擋的是資料出口，不一定是 A11b 開發

### 4.1 執行能力主線（建議的下個**設計**工作）

`A11b.2.2.1 synthetic containment DONE`
→ `A11b.2.2.2 real Git ADD 設計／獨立安全檢查`
→ `明確授權的受控 product executor 實作與驗收`
→ `A11b 後續 policy／recovery／audit`
→ `A11c 真實 Codex/Claude 測試、迭代與 cutover`.

**注意：「A11b.2.2.2 是下一關」不等於本報告核准立刻開啟
real Git ADD、任意 Codex shell，或越過既有 R-EXEC/R-FS/R-GIT/
R-APR/R-INJ/R-EXP/R-AUD 安全要求。** 下個應先核對設計與 gate。

### 4.2 成本／摘錄支線

`C1a local-only excerpts DONE`
→ `C1b.0 最終截斷後敏感字串檢查（Issue #66）`
→ `允許的資料出口與代表性 48KiB+ company-only Phase 0`
→ `C1c 品質等價／實際 paid-token 效益量測`.

**C1b.0 是「任何輸出摘錄進入公司／外部 LLM」的硬性安全前置，
不是不論資料流如何都普遍阻擋 A11b 的任意設計／純本地開發。**
然而，若未來 C2／A11b 的 Claude review、error triage、context packet
實際包含 C1 摘錄或其他敏感內容並跨越 Provider 信任邊界，
那個**具體出口**必須完成相應防護和授權；
不能以「A11 與 C 不同軌」繞過資安門檻。

### 4.3 成本證據

已知 [PR #36](https://github.com/davidcatcat/davidcattoolsproject/pull/36)
的小型 token dogfood pilot 沒有顯示普遍節省；
部分 L01 大型 log 的受限 pipeline 有正面節省案例，
但不能外推成普通 `pytest` 或 llmtool 全部角色已降成本。
成本主張需有相同工作量、品質等價、包含重試與實際 token 使用的證據。
**節省 Token 是重要產品目標，但目前不是任何模型可以直接宣告 PASS 的事實。**

## 5. Roadmap／工作排程中的矛盾

1. [Roadmap](../tools/llmtool/ROADMAP.md) 最新狀態段明寫
   「**Next engineering work is C1b.0**」；而同一份 Roadmap 的
   A11 進度下一個 gate 是 `A11b.2.2.2`。
   這不代表依賴關係必然矛盾，但**人力投入順序**沒有清楚分別。
2. [MVP](../tools/llmtool/NATIVE_SELF_DEV_MVP.md)
   「Decisions and sequencing」寫「**C1 first (selector + benchmark)，C2 next**」；
   若 C1 包括 C1b/C1c 的完整量測，可能會把 A11b/C2 原生 executor
   一直放在後面。這是產品優先順序選擇，不是技術必然依賴。
3. [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66)
   已被標為下一個產品實作切片；**Task READY 不等於 Codex 已開始，
   也不代表使用者決定暫停 A11b**。
4. 從使用者目標看，既有臨時 `ai-loop` 的大量修復與再審查
   曾延後原生 llmtool 上線；不宜重蹈「先完成一整套成本優化或
   第二套 controller，才許開發 A11b」的路線。
5. A11b／C2 安全門檻確有必要，不能因為使用者希望加速上線
   就簡化成只有 model role 分工／synthetic containment 的無權限執行器。

## 6. 協調者的**待審建議**（不要直接實施）

**建議主線優先：**恢復 `A11b.2.2.2` 的**設計與獨立審查**，
核准範圍後開發最小受控 real-execution vertical slice，
再推進 A11b 後續與 A11c 受控自我開發驗收。

**安全配套維持：**[Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66)
保留且不關閉；可以由獨立產品小切片處理
C1b.0，但不將完整 C1b/C1c 成本量測預設設為所有 A11b 開發的前置。
任何模型外送摘錄的路徑仍由 C1b.0／其餘資料政策 gate 阻擋。

**工程原則：**同一個 A11 核心、同一套 Skill／安全／QA 契約，
C 當交付與成效衡量；不重做已合併的基礎能力，
不擴充已凍結 `--qa-low*`，不要求使用者人工轉交兩家模型提示。
保留 PR #45 的 Draft／未驗收狀態，不以其功能取代 llmtool native 上線證據。

**此建議尚未授權以下事項：**不修改正式 Roadmap；
不重排或關閉 Issue #63/#66；不啟動 Codex/Claude、
不修改程式碼、不做真實 Git mutation、Provider 連線、
Mac launcher 控制 SHA 的修改／核准或 PR 合併。

## 7. 請 Claude 獨立回答（以證據校正，不要迎合提案）

請在同一份 review 中分別給每題 **CONFIRM / CORRECT / NEEDS_DECISION**，
附上路徑＋章節／必要行號、具體替代文字與是否阻擋產品前進：

1. **歷史／重複性：**C0～C3 是否確實只是 A 系列的 release slicing？
   哪些 C1a／C1b／C1c 是在 A 討論中已有明確需求但尚未實作，
   哪些是之後新增的 acceptance？是否有功能重複建置風險？
2. **精確狀態：**A11a、A11b.1、A11b.2.1、A11b.2.2.1、
   A11b.2.2.2、A11c 與 C1a/b/c 的 DONE、MERGED、NOT STARTED
   是否和實際 Git commit、測試證據一致？是否有錯誤的「已上線」暗示？
3. **必要依賴：**C1b.0 是哪一種 payload／Provider egress 的 gate？
   是否有證據證明 A11b.2.2.2 純本地 executor
   **必須先完成全部 C1b/C1c**？有沒有隱藏 cross-provider export？
4. **設計與資安：**A11b.2.2.2 下一步究竟是設計審核、
   實作哪個 bounded slice，或還缺哪項已知 A11b 安全補丁／gate？
   不得把 PR #49 synthetic containment 當 real execution authority。
5. **優先順序：**請比較「先完整 C1b/C1c」、
   「先 A11b.2.2.2」及「受控並行」的開發／驗收成本、
   風險與實際交付時效；選一個有理由的**唯一建議主線**，
   明列不能擅自放寬的安全條件。
6. **文件／Issue 最小修正清單：**若同意應調整優先順序，
   明列哪些 Roadmap／MVP／WORKFLOW／Issue 需要修正，
   哪些原始里程碑必須保留；**不要自行改寫歷史報告**。
7. **AI 團隊責任：**Codex 負責實作、Claude 獨立審查、
   協調者管理技術與安全 Merge；使用者是需求與成果驗收者，
   只在無本機存取途徑時協助執行 Mac 指令。
   是否有任何文件仍錯把使用者當技術 PM 或 prompt courier？

**審查方式：**本次是只讀 Roadmap／產品依賴 review；
請不要修改產品程式、控制腳本、Mac launcher，不要自動 Merge，
不要因為只有 Low finding 就創造新工具。
有不同判斷時請直接指出協調者原推論的錯誤，
並在最終摘要清楚分開「現況」「必要 gate」「建議排序」。

## 8. 驗收與交接

這份報告的交付成果是可供 Claude 審查的 GitHub 文件及 PR；
Claude 尚未提供審查結果，因此**不聲稱方案已核准**。
待獨立 review 後，由協調者比對本報告、實際 repo 和 Claude 證據，
再提出最小 Roadmap／Issue 調整；在此之前不更動正式優先順序。

### 來源（請優先核對固定基準，而非只看這份報告）

- [Roadmap（查核 SHA）](https://github.com/davidcatcat/davidcattoolsproject/blob/7fcf4f8015b1ebae1e956647b83f812589c3a8e4/tools/llmtool/ROADMAP.md)
- [Native self-development MVP（查核 SHA）](https://github.com/davidcatcat/davidcattoolsproject/blob/7fcf4f8015b1ebae1e956647b83f812589c3a8e4/tools/llmtool/NATIVE_SELF_DEV_MVP.md)
- [WORKFLOW（查核 SHA）](https://github.com/davidcatcat/davidcattoolsproject/blob/7fcf4f8015b1ebae1e956647b83f812589c3a8e4/tools/llmtool/WORKFLOW.md)
- [AI team delivery Skill](https://github.com/davidcatcat/davidcattoolsproject/blob/7fcf4f8015b1ebae1e956647b83f812589c3a8e4/skills/ai-team-delivery/SKILL.md)
- [PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49)，[PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55)，[PR #67](https://github.com/davidcatcat/davidcattoolsproject/pull/67)
- [Issue #63](https://github.com/davidcatcat/davidcattoolsproject/issues/63)，[Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66)
- [PR #36](https://github.com/davidcatcat/davidcattoolsproject/pull/36) / [token pilot report](https://github.com/davidcatcat/davidcattoolsproject/blob/7fcf4f8015b1ebae1e956647b83f812589c3a8e4/reports/llmtool_token_savings_pilot_review_20261006.md)
