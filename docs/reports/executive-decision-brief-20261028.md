# Executive Decision Brief — 2026-10-28

- **Snapshot：** `139a62c0483560be4e7a0aadd76c82652a8a2cd9`　**審查者：** Independent Auditor（Claude Fable 5.1）
- 完整證據見 [Report A](full-repository-technical-audit-20261028.md)、[Report B](project-evolution-decision-retrospective-20261028.md)、[Report C](product-owner-playbook-ai-development-20261028.md)。本文只給結論。

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
5. 從未由使用者角度驗收：279 個 Task 只有 1 個 COMPLETED；374 個 worktree、15 GB、13 GB 磁碟剩餘無人管。

## 問題四：責任歸屬
- **技術（AI 實作）：** God module、TaskStore 內嵌狀態機、Codex 契約過嚴（31% 無 artifact）、無終態、無 retry、硬編碼路徑。
- **PM 管理：** merge train 雙 CI、每輪新 Reviewer worktree、docs PR 全 gate、手動串接而非驅動器、代記 73 則決策。
- **Owner 決策方式：** 新路線與 P0 插隊（EC2、AGY、gw）、接受 AI 的大型契約而不要求最小版本、以 PR／CI 判斷進度、沒有親手驗收。

## 問題五：過度工程？
**是，集中在治理層。** 五套授權模型、三套修復上限、Worker 描述五處、政策文件約 60 萬 bytes、Mac CI 驗證自己 guard 的 12 個案例、每個 Reviewer 一個 worktree。核心執行層（provider、store、D6、CI）不過度。

## 問題六：重來絕對不做
F1 Git 容器化；fake-only 契約基礎設施；ai-loop 第二編排器；30 份 token benchmark 報告；五套授權模型；在閉環未通前開第二條 Developer 路線；讓額度事件變成 Owner 決策。

## 問題七：最有價值的成果
TaskStore + D6 lifecycle + Codex direct + EC2 receipt + CI 隔離 + cross-family review 規則 + nlf。這些是 MVP 需要的全部零件。

## 問題八：最該停止
開新路線（AGY／OpenCode／gw 真實題／CliNest／Telegram／Web）；merge train；每輪新建 Reviewer worktree；docs PR 正式審；額度 policy PR；High Debug 與 GitHub events 自動化；再寫任何超過兩頁的規範。

## 問題九：最該優先
一條鏈：TUI 提交（#505／#510／#512）→ PM session（#506）→ Codex direct → EC2 測試 → Reviewer → 一次自動退修 → PR → merge → COMPLETED（#519 + 終態）；D6 批次清理；Codex retry 與契約放寬；Owner 親手跑三個真實任務。四週。

## 問題十：如果我是你的 CTO
1. 本週宣告 NOW 只有「一條鏈 E2E」，其餘 FROZEN；四週後以你親手完成三個任務為唯一驗收。
2. 把額度、runner、磁碟全部變成自動狀態，你不再為它們做決定。
3. 對 AI 的每個治理提案要求「一天可做的最小版本 + 真實執行日期 + 停損」；兩週 0 使用就刪。
4. 分級流程：docs／test-only 不正式審；Reviewer 復用 worktree；Mac CI 只跑 Mac 專屬；merge train 停用。
5. 一頁看板：Codex tokens、Reviewer 輪數、Mac／EC2 分鐘、PM 手動命令數、ACTIVE worktree、磁碟。沒有前後數字的「節省」不採信。
6. 文件瘦身到 60 KB 以內；ROADMAP 只留現況表與下一步。
7. 開源與 provider-neutral 是對的方向，但排在 MVP 驗收之後；先把硬編碼改成 config、加 LICENSE。

**一句話：** 你已經有一台引擎的所有零件和一本很厚的安全手冊；現在需要的是把車裝起來、自己開一圈，再決定要不要加第二顆引擎。
