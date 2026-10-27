# Executive Decision Brief — 2026-10-28

- **Snapshot：** `139a62c0483560be4e7a0aadd76c82652a8a2cd9`　**審查者：** Independent Auditor（Claude Fable 5.1）
- 完整證據見 [Report A](full-repository-technical-audit-20261028.md)、[Report B](project-evolution-decision-retrospective-20261028.md)、[Report C](product-owner-playbook-ai-development-20261028.md)。本文只給結論。
- **v3 註記（2026-10-28 Owner 身份重判）：** Owner 是需求方與出資者，不是 PM／CTO。v1／v2 用專業管理標準評價 Owner 的部分已撤銷；責任重判見問題四，AI 失職與能力限制見「問題十之前」，Owner 只需做的四件事見文末。
- **v2 註記（2026-10-28 自我校正）：** 本文所有「應停止／應優先／建議」都是審查者建議；凡涉及 Gate、Reviewer 配對、Dispatch 政策或 Roadmap 者為 **OWNER_DECISION_REQUIRED**，不取代 AGENTS.md。本機 TaskStore 統計（279 Task、COMPLETED 1、review claim 0）只代表 llmtool 子命令在 Owner Mac 上的使用情況，不代表專案整體產出（Report A 2.4）。Snapshot 後 main 已合併 #513（TUI Task mode／Task list／事件／pm_runtime／developer route 契約），AUDIT-001 部分改善但核心未變（Report A 第 26 章）。

## 問題一：整體方向正確嗎？
**正確，但順序倒置。** 「llmtool 無 AI 智能、PM 做語意、確定性執行、使用者直接操作、他人可安裝」是對的，而且程式碼遵守這些原則。錯的是先建了 3.4 萬行的管理框架，第 28 天才開始做使用者入口；到第 31 天，從 TUI 提交需求後仍然什麼都不會發生。

## 問題二：哪些地方做得很好？
1. 執行層品質：TaskStore 原子寫入、D6 身份綁定、行程清理、exact-head CI、敏感資料篩選。
2. 誠實文化：MERGED／VERIFIED／NOT_RUN 分得清楚；拒絕假 PASS。
3. 止損能力：關 Web-first、封存 F1、瘦身 AGENTS、延後 A13。
4. 成本底線：no paid fallback、self-hosted 止費、Mac／EC2 runner 隔離。
5. nlf：一個真正完成的產品。

## 問題三：最嚴重的五個錯誤
1. MVP 到第 24 天才定義，之前每個 milestone 由 AI 自驗即標 DONE。
2. 先建安全／治理框架（F1 Git 容器化、#120 fake-only 二十個 PR）後封存；同樣模式在 review-dispatch／repair／High Debug 重演（本機使用 0 次）。
3. 五條 Developer 路線並行，主鏈未通。
4. 額度驅動的角色反覆（9 天內至少 8 次），每次都變成 policy PR 與文件。
5. 流程裡沒有使用者操作驗收的環節（Owner 私下是否操作無法觀察，不主張「從未」）：本機 store 推導 279 個 Task 只有 1 個 COMPLETED（受無終態影響）；374 個 worktree、15 GB、13 GB 磁碟剩餘無人管。

## 問題四：責任歸屬（v3，以 Owner 為 Sponsor 的標準）
- **技術（AI 實作）：** God module、TaskStore 內嵌狀態機、Codex 契約過嚴（31% 無 artifact）、無終態、無 retry、硬編碼路徑、容量無人管。
- **AI PM 管理：** 需求不釐清直接展開（D6、AGY）、無影響分析、手動串接而非驅動器、merge train 雙 CI、每輪新 Reviewer worktree、docs PR 全 gate、把額度事件呈現為 Owner 決策並代記 73 則、收到容量警告（PR #322）不處理、從未安排 demo。
- **AI CTO／Architect（Coordinator）：** F1、fake-only、五套授權、三套修復上限；12 項關鍵決策中 11 項無替代方案與停損紀錄；閉環未通時批准多路線。
- **AI QA／Reviewer：** 只審 diff，從未驗使用者可用性。
- **Owner：** 三項：「省錢」與「可用」未定先後；全權委託與逐條指令切換；未要求定期 demo。**沒有找到 AI 明確警告後 Owner 仍堅持的案例。** v1／v2 歸給 Owner 的「規格不完整、接受大型契約、無停損、未親手驗收、以 PR 判斷進度」已撤銷，改歸 AI 角色（Report B 第 6、9A 章）。

## 問題五：過度工程？
**是，集中在治理層。** 五套授權模型、三套修復上限、Worker 描述五處、政策文件約 60 萬 bytes、Mac CI 驗證自己 guard 的 12 個案例、每個 Reviewer 一個 worktree。核心執行層（provider、store、D6、CI）不過度。

## 問題六：重來絕對不做
F1 Git 容器化；fake-only 契約基礎設施；ai-loop 第二編排器；30 份 token benchmark 報告；五套授權模型；在閉環未通前開第二條 Developer 路線；讓額度事件變成 Owner 決策。

## 問題七：最有價值的成果
TaskStore + D6 lifecycle + Codex direct + EC2 receipt + CI 隔離 + cross-family review 規則 + nlf。這些是 MVP 需要的全部零件。

## 問題八：最該停止（全部 OWNER_DECISION_REQUIRED）
開新路線（AGY／OpenCode／gw 真實題／CliNest／Telegram／Web）；merge train；每輪新建 Reviewer worktree；docs PR 正式審；額度 policy PR；High Debug 與 GitHub events 自動化；再寫任何超過兩頁的規範。

## 問題九：最該優先
一條鏈：TUI 提交（#505／#510／#512，已於 #513 合併）→ PM session（#506，已合併，尚未接入 TUI）→ Codex direct → EC2 測試 → Reviewer → 一次自動退修 → PR → merge → COMPLETED（#519 + 終態，OPEN）；D6 經 lifecycle gate 的安全退場；Codex retry 與契約放寬；Owner 親手跑三個真實任務。四週為建議期程。

## 問題十之前：AI 團隊的失職與能力限制（v3 新增）
- **第 13 天（10-09）Owner 已說「你們 AI 自行協調好就好」「停下來要人審查什麼我也不懂」；同日 Claude 的回顧已診斷出 AI 自製 gate、把 Owner 拉進迴圈的模式；之後在 fake-only、D4 per-call 核准、High Debug 重複三次。** 10-17、10-20、10-21、10-28 四次是 Owner 先於 AI 發現方向偏離。
- **AI 能力限制（坦白）：** 沒有跨 session 的持續記憶（文件膨脹的根因）；預設順從與擴寫（一句話變十頁）；無人值守連續運作不可靠。對策是確定性驅動器、硬性輸出格式（三選項＋停損）、每週一次人類看 demo 的檢查點，不是把管理交回 Owner。
- **CTO 敢不敢負責：** 對架構判斷、計畫、品質標準與「下週給 Owner 能用的 E2E」敢；對無人值守連續運作不敢保證，需上述三個對策。

## 問題十：如果我是你的 CTO（建議；標示者需你決定）
1. 本週宣告 NOW 只有「一條鏈 E2E」，其餘 FROZEN；四週後以你親手完成三個任務為唯一驗收。（OWNER_DECISION_REQUIRED：Roadmap）
2. 把額度、runner、磁碟全部變成自動技術狀態，你不再為它們做決定。（OWNER_DECISION_REQUIRED：dispatch 規則）
3. 對 AI 的每個治理提案要求「短期最小版本 + 真實執行日期 + 停損」；持續 0 使用者列為檢討候選。
4. 分級流程：docs／test-only 不正式審；Reviewer 復用 worktree；Mac CI 只跑 Mac 專屬；merge train 停用。（OWNER_DECISION_REQUIRED：AGENTS.md 審查規則、D6 lifecycle、#425 Mac gate、合併流程）
5. 一頁看板：Codex tokens、Reviewer 輪數、Mac／EC2 分鐘、PM 手動命令數、ACTIVE worktree、磁碟。沒有前後數字的「節省」不採信。
6. 文件瘦身（目標體積先量再定）；ROADMAP 只留現況表與下一步。（AGENTS.md／ROADMAP 修改為 OWNER_DECISION_REQUIRED）
7. 開源與 provider-neutral 是對的方向，但排在 MVP 驗收之後；先把硬編碼改成 config、加 LICENSE。

**Owner 從今天起只做四件事（Report C 第 2 章）：** 宣告一個 NOW；設預算上限；每週看一次 demo 並說符合／不符合；只接受決策單格式。其餘由 AI PM／CTO／Engineering 承擔；AI 把技術細節丟回來就退件。

**一句話：** 你已經有一台引擎的所有零件和一本很厚的安全手冊；把車裝起來、開一圈給你看，是技術團隊的責任，不是你的。
