# Report C — Owner Playbook：以最少管理負擔取得可用產品（v6）

- **日期：** 2026-10-28　**Snapshot：** `139a62c0483560be4e7a0aadd76c82652a8a2cd9`
- **對象：** 本專案的 Owner（需求方、出資者、最終決策者）與 AI 團隊。
- **依據：** [Report A（技術稽核）](full-repository-technical-audit-20261028.md)、[Report B（演變與決策回顧，含 9A AI Management Failure Register 與 9C Owner 答覆紀錄）](project-evolution-decision-retrospective-20261028.md)。
- **版本：** v6（2026-10-28 依 ChatGPT 顧問審查（advisory，非正式 verdict；Owner 2026-10-28 轉交）五項修正：等待逾時不得視為授權、`task advance` 實際範圍、gw 整題成本含修復、現行優先表統一、Codex PM 適配只保留 Owner 自述）；v4（2026-10-28 依 Owner 三輪答覆更新：Owner 已給 NOW、預算、聯絡方式、第一版 PM；週報改為隨時可答的狀態頁；issue 與 report 分開；新增白話規則與 gw 量測條件）。v3 把 Owner 定位為 Sponsor；v1／v2 把需求分析、驗收標準、Scope、停損等寫成 Owner 責任，是錯誤前提，已撤銷。本版核心原則：**讓 Owner 以最少的管理負擔，取得高品質的產品交付；日常產品管理、工程管理、技術品質與交付管理由 AI 團隊主動承擔。**
- 本文全部為建議。涉及既有 Gate、Reviewer 配對、Dispatch 政策或 Roadmap 的項目標 **OWNER_DECISION_REQUIRED**，採用與否由 Owner 決定；AI 不得據此直接改政策。文中數字除 Owner 已給者外皆為待驗證目標。

---

## 1. 責任分工總表

| 類別 | 誰 | 負責什麼 | 不負責什麼 |
|---|---|---|---|
| **A. Owner 必須親自決定** | Owner | 產品方向與定位；一個 NOW；預算上限；安全與資料外送邊界；新帳號／新付費服務；看 demo 後說符合／不符合；AI 明說能力不足時決定替代方案 | 技術架構、模組設計、工作包拆解、PR 順序、CI 策略、worktree、Agent 交接、催 PM、找架構缺陷、寫技術規格、QA／CI 驗證、監看額度 |
| **B. AI PM 主動完成** | PM session | 把 Owner 的每句話轉成需求確認單；MVP 與範圍；新想法影響分析；Roadmap；驗收標準；隨時可 demo；一頁狀態隨時可答；卡住時主動開 issue；可平行的工作先平行 | 不得把需求原樣展開成實作；不得把技術狀態呈現為 Owner 決策；不得主動推送定期報告 |
| **C. AI CTO／Architect 主動完成** | 同一 PM session 或獨立 session | 每個架構／治理提案附三選項（含不做）、最小版本、停損、一句白話；過度工程預警；技術債、容量、成本與額度看板；對 PM 與 Owner 說「不建議，因為…」 | 不得只回答「可以實作」；不得用術語代替說明 |
| **D. AI Engineering 自動完成** | 驅動器＋Developer＋Reviewer＋PM 的手動步驟 | 派工、測試、審查派送、修復重派、結案記錄、發布與合併、worktree 退場、Developer／Reviewer 額度改派、CI 選擇、事件消費（目前 `task advance` 只涵蓋審查派送到結案，其餘仍是 PM／Worker 手動，見第 6 章） | 在既有授權、派工政策、Reviewer、CI 與合併 gate 之內自動進行，不需 Owner 逐件經手；任何政策或 gate 變更仍須 Owner 決定 |
| **E. 只有 issue 才通知 Owner** | PM | 預估超過預算；安全事件；同一問題兩次修復失敗；卡住超過文件定義的天數；需要新帳號／新支出；AI 能力不足以完成某項工作；Owner 定義的方向與現況衝突 | 其餘不通知；進度 report 等 Owner 問 |

不一定需要四個獨立 AI。最小配置：一個 PM session 同時承擔 B 與 C，但 C 的輸出格式（第 4 章）是硬性要求；D 由確定性程式承擔；Reviewer 加「可用性驗收」。

**責任類型區分：** 專業建議責任（B、C）、決策權（A）、執行責任（D）、品質保證責任（D 的 Reviewer＋B 的驗收標準）、最終授權責任（A，僅限 A 列出的事項）。

---

## 2. Owner 要做的四件事（全部；Owner 2026-10-28 已給其中兩件）

1. **宣告一個 NOW。** Owner 已給：「必須要能使用公司 llm，搭配其他 ai，一起進行其他項目開發，省 token」，並說第一版的驗收是「在 TUI 提需求、PM 像 Claude TUI 一樣即時回答、拿到我要的結果」。AI PM 把它展開成第一版清單（Report B 9C.6），其餘標 FROZEN；Owner 不必再圈。（本身為 OWNER_DECISION_REQUIRED，也是唯一需要 Owner 的 Roadmap 決策）
2. **設預算上限。** Owner 已給：Codex／Claude／EC2 合計每月 USD 300。AI 每月列一張實際支出表（訂閱、EC2、API）放在狀態頁，Owner 自行對照；AI 不追問預算的組成。預估超過才以決策單找 Owner。
3. **想到就問，問到就要有 demo。** Owner 不要排程報告。AI PM 隨時維持一個可操作的東西；Owner 任何時候問「現在能用什麼」，當下就能操作並回答「符合／不符合／哪裡不符合」。拿不出來是 PM 的失敗。
4. **只接受決策單，聽不懂就退回。** AI 要 Owner 決定任何事，必須用第 5 章格式；任何提案必須先有一句白話「做完你能／不能做什麼」。不符格式或聽不懂的術語，Owner 直接退回「請用決策單」「請用白話」，不算 Owner 的問題。

---

## 3. AI PM 的硬性輸出格式

### 3.1 需求確認單（Owner 每說一個想法，PM 先回這個，不先排程）

```text
你說的是：<一句話重述>
我理解的情境：你在 <時刻> 想要 <結果>，現在只能 <現況>
這是：正式需求 / 想法（我先記著）/ 回報問題
驗收：你會 <操作>，看到 <結果> 就算完成
不做：<我判斷不在範圍的事>
影響：做它會延誤 <NOW 的哪一步> 約 <多久>；建議 現在做 / TUI 驗收後 / FROZEN
需要你決定嗎：否（我照建議做）/ 是（附決策單）
```

歷史對照：10-19「開放 worktree 吧，但是要判斷什麼時候做完改刪除了」若經此單，PM 應回「驗收：PR 合併後 worktree 自動消失；不做：不寫超過一頁的規範」，而不是 13 KB 規範。10-28「agy 納入 llmtool 吧」若經此單，PM 應回「影響：延誤 TUI 驗收約 N 天；建議 TUI 驗收後」，Owner 事後也確認 AGY 可後接。

### 3.2 一頁狀態（隨時可答；不主動推送）

Owner 問「現在怎樣」時，PM 第一句就是第一行；不等 Owner 問不送。

```text
NOW：<項目>；現在你能操作的東西：<有／無；怎麼操作>
demo：<現在就可以 / 還差什麼>
成本：Codex tokens、Reviewer 輪數、Mac／EC2 分鐘、PM 手動命令數、本月支出對照 USD 300
容量：ACTIVE worktree、磁碟剩餘
issue（需要你）：<決策單連結或「無」>
下一步：
你需要做：無 / <一個動作>
```

### 3.3 「這個 TUI 不好用」類回報的處理

Owner 只需說這一句。PM 必須自行：重現、對照已知缺陷、列出缺的使用者功能、對照 Roadmap、提出改進與驗收，再以需求確認單回 Owner。**不得要求 Owner 寫 UI 規格。**

### 3.4 白話規則（F16 的對策）

Owner 2026-10-28：「有時候你們講的技術術語，在使用者和 owner 來說並不是全部都知道的。」規則：

- 任何提案的第一句是白話的結果：「做完之後你可以 <操作>；還不能 <操作>，要到 <時間／條件> 才能。」
- 含 fake、offline、stub、contract、adapter、gate 等詞的方案，必須加一句「這個階段做出來的東西能不能真的用」。
- Owner 說「聽不懂」或「用白話」，PM 重寫到 Owner 能複述為止；重寫次數記入 PM 自己的品質指標。

歷史對照：#120 fake-only 若有這句（「這兩週做出來的東西不能真的叫 AI」），Owner 說他不會在不知情下核准。

---

## 4. AI CTO 的硬性輸出格式

### 4.1 方案比較單（任何架構／治理／路線提案都必須附）

| 選項 | 白話：做完你能做什麼 | 解決什麼 | 不解決什麼 | 成本（人日／token／CI） | 風險 | 對 NOW 影響 | 最小版本（可在幾天內看到真實執行） | 停損 |
|---|---|---|---|---|---|---|---|---|
| A（建議） | | | | | | | | |
| B | | | | | | | | |
| C 不做／延後 | | | | | | | | |

沒有「不做」選項、沒有最小版本、沒有停損、沒有白話欄的提案，PM 不得排程。

### 4.2 過度工程預警（CTO 主動觸發，不等 Owner 發現）

觸發任一即以 issue 通知 Owner 並附方案比較單：
- 新增一份超過兩頁的規範或契約。
- 新增第二套做同一件事的機制（授權、上限、Worker 描述）。
- 新增一條 AI 路線／模型／帳號而主鏈未通。
- 一個 runtime 功能上線後持續 0 使用。
- 單一模組行數、政策文件體積、worktree 數、磁碟用量超過 CTO 自訂且公告的基準。

歷史對照：F1 Git 容器化、S1b／B fake-only、五套授權、High Debug 都會在此被攔下。

### 4.3 「不建議」的說法

AI 對 Owner 的技術想法，必須能說：「不建議，因為 <風險>；更好的是 <方案>；若你仍要做，代價是 <延誤什麼>。」只回答「可以實作」視為失職。

### 4.4 額度與支出看板（F15 的對策）

AI 從未監看額度，Owner 自己盯 usage 才發現用光（Report B F15）。CTO 必須：
- 讓 monitor 顯示 Codex／Claude 的額度狀態（目前沒有）。
- Developer／Reviewer 額度用盡時在 ALLOWED 路線內自動改派，狀態頁記一行（OWNER_DECISION_REQUIRED：dispatch 開關目前為 Owner 專屬）。
- PM 自己的額度是單點故障：先把 PM 每次載入的規則文件砍到最小；仍不足再提備援 PM 方案（Report B 9C.4）。
- 每個「省 token」工程附前後數字；gw 路線依 Owner 條件「不能節省就沒必要」，先量**整題**成本（含失敗與修復的每次呼叫、PM 準備 context、套 diff、Reviewer 審查）對「直接派 Codex」的整題成本，再決定去留。

---

## 5. Owner 決策單（AI 唯一可以向 Owner 要決策的格式）

```text
要決定：<一句話>
白話：選 A 之後你可以 <操作>；選 B 之後你可以 <操作>
為什麼需要你：預算 / 方向 / 安全或資料外送 / 新帳號或支出 / AI 能力不足（五選一）
推薦：<方案>，因為 <一句話>
替代：<方案 B> / 不做
優缺點與風險：A：<一行>；B：<一行>
不決定的後果：<一行；保留事項（預算／方向／安全／新帳號）未回覆就維持等待，不視為同意；只有 Owner 事先授權的預設方案才可自動採用>
請回答：是 / 否 / A / B
```

不符合此格式的決策請求，Owner 應退回。技術細節放附錄，不放決策單正文。

---

## 6. AI Engineering 自動完成的事（Owner 不參與）

- 審查派送、審查結果記錄、退修重派、結案記錄：由 `task advance` 驅動器推進（#519 已合併；原始碼自述「每次呼叫只做一個審查或結案動作」）。**驅動器目前不做的：** 第一次派工（PM 的 `task delegate`）、跑測試（由派工流程的 EC2 remote-test 觸發）、開 PR／發布（未發布時回 NEEDS_PUBLISH）、合併（未合併時回 WAITING_INTEGRATION，確認 GitHub 已合併才記 MERGED）。這些仍由 PM 或 Worker 執行；「全自動閉環」是目標，不是現況。PM 處理 BLOCKED 與上述手動步驟。
- Developer／Reviewer 額度／CLI 不可用：技術狀態，PM 在 ALLOWED 路線內改派（OWNER_DECISION_REQUIRED：dispatch 規則的使用方式）。
- worktree 退場：通過 D6 lifecycle gate 的自動退場；RETAIN_BLOCKED 由 PM 處理。
- CI 範圍、merge 方式、Reviewer 復用：由 PM／CTO 在現行授權內評估；改變 Gate 者走決策單（OWNER_DECISION_REQUIRED）。
- 容量、成本與額度看板：CTO 自動產出，狀態頁引用。
- 可平行的工作先平行：PM 自己判斷開 subagent，不等 Owner 提醒。

---

## 7. issue 與 report 的分別（E 類）

Owner 2026-10-28：「AI 卡好幾天我要知道，但是不應該是我去主動發現。這類叫做 issue，這跟定期報告 report 是不同一件事情。」找 Owner 的規則要寫在文件裡。

| 情況 | 類型 | 通知方式 |
|---|---|---|
| 預估超過每月 USD 300 | issue | 決策單 |
| 安全事件、資料外送疑慮 | issue | 立即，決策單 |
| 同一 root cause 兩次修復失敗 | issue | 通知 Owner 一行（AI 自行走 High Debug 等既有規則，不要 Owner 決定） |
| NOW 卡住超過 AI PM 在文件中定義的天數（審查者建議 24 小時為待驗證目標） | issue | 通知 Owner 一行，附原因與 AI 的下一步 |
| 決策單等 Owner 超過文件定義的時間 | issue | 提醒一次；保留事項維持等待（AGENTS.md：No response is not consent），只有相依的那一步停，其他已授權工作繼續；只有 Owner 事先授權的預設方案才可自動採用 |
| 需要新帳號／付費服務／root 權限 | issue | 決策單 |
| AI 能力不足（例如無法無人值守連續運作） | issue | 決策單，附替代管理機制 |
| NOW 與現況衝突 | issue | 決策單 |
| 進度、PR 數、CI 狀態、完成了什麼 | report | **不主動送**；Owner 問時以第 3.2 節狀態頁回答 |

issue 的具體天數與送達方式（GitHub `waiting_owner` 標記、TUI 提示或對話）由 AI PM 定義並寫進現行文件（OWNER_DECISION_REQUIRED：Owner 決策紀錄規則）。

---

## 8. 驗收：誰驗什麼

| 層 | 誰 | 驗什麼 |
|---|---|---|
| 程式正確 | Developer 自測＋Reviewer | diff、測試、CI |
| **使用者可用** | **AI QA／Reviewer（新增）** | 依需求確認單的「驗收」句實際操作一遍，附操作紀錄；未做此步不得標 DONE |
| 符合期望 | Owner | 看 demo，回答符合／不符合 |

「MERGED ≠ VERIFIED」由 AI QA 負責把 VERIFIED 做出來，不由 Owner 補。第一版的驗收句（Owner 2026-10-28）：「在 TUI 提需求，PM 即時回話，最後拿到我要的結果；而且能用公司 LLM 搭配其他 AI 開發其他專案。」

---

## 9. 避免重複過去錯誤的對照表（AI 自檢，不是 Owner 檢）

| 過去模式 | 觸發訊號 | AI PM／CTO 必須做 |
|---|---|---|
| 一句話變框架 | Owner 一句話 | 先回需求確認單與最小版本 |
| 先建框架後驗證 | 提案含 contract／gate／fake／offline | 方案比較單＋停損＋真實執行日期＋一句白話 |
| 術語當告知 | Owner 沒有複述出來 | 重寫到 Owner 能複述 |
| 額度變決策 | 「X 的上限到了」 | 自動改派，狀態頁記一行；PM 額度先砍文件 |
| 開新路線 | 「可以試 Y」 | 影響分析：主鏈通了嗎、延誤什麼、Owner 是否已說可後接 |
| PR 當進度 | 狀態頁只有 PR 數 | 重寫為「你能用什麼」 |
| 文件膨脹 | 新增超過兩頁 | CTO 預警，先刪再加 |
| 容量無人管 | worktree／磁碟上升 | 看板＋自動退場 |
| 修工具的工具 | 「需要 helper 檢查 helper」 | 問「擋住哪個已發生事故」 |
| 不平行 | 兩件互不相依的工作排隊 | 開 subagent，不等 Owner 提醒 |

---

## 10. 建議立即實施的五項（對 AI 團隊；Owner 已確認第 1 項的內容）

1. NOW 已由 Owner 給出；AI PM 展開成第一版清單（Report B 9C.6）並把其餘標 FROZEN（OWNER_DECISION_REQUIRED：Roadmap 順序）。
2. PM 自本週起用需求確認單、狀態頁、決策單三種格式，加白話規則；不符格式的輸出視為未完成。
3. CTO 自本週起對任何提案附方案比較單；建立容量、成本與額度看板；gw 省不省先量。
4. Engineering 以 `task advance` 驅動器取代審查到結案的手動串接（每次呼叫一個動作）。若要把它延伸到首次派工、發布與合併，屬 **OWNER_DECISION_REQUIRED**，而且延伸後仍受既有的派工政策、Git、審查、CI 與合併 gate 約束，不得繞過；Reviewer 加可用性驗收。
5. 第一個 demo：Owner 在 TUI 提一個真實需求，PM 即時回話，看到它被開發、審查、合併、顯示完成。Owner 什麼時候問，什麼時候就要拿得出來；做不到是 PM 的失敗，不是 Owner 的。

---

## 11. Owner 已給的條件（2026-10-28；Report B 9C）

| 項目 | Owner 的答案 |
|---|---|
| NOW | 能使用公司 LLM，搭配其他 AI，一起進行其他項目開發，省 token |
| 第一版驗收 | 在 TUI 提需求，PM 像 Claude TUI 一樣即時回話，拿到想要的結果 |
| 第一版 PM | 先接 Claude Code；PM 可設定，設定方式由 AI 提供 |
| gw | 必做路線；但「不能節省，那就沒必要了」，先量再決定 |
| OpenCode／AGY | 省 token 用途，開發階段有省再接入 |
| 關閉介面後繼續 | 本機第一版非硬需求；Owner 會開著 CLI／TUI |
| 預算 | 每月 USD 300（Codex／Claude／EC2）；AI 不追問組成 |
| 聯絡 | 不要定期報告，Owner 想到就問；卡住要當 issue 主動找 Owner |
| 獨立審查 | 不定期、不排程 |
| 公司工作 | 不在本專案討論範圍 |
