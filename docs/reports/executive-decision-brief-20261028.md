# Executive Decision Brief — 2026-10-28

- **Snapshot：** `139a62c0483560be4e7a0aadd76c82652a8a2cd9`　**審查者：** Independent Auditor（Claude Fable 5.1）
- 完整證據見 [Report A](full-repository-technical-audit-20261028.md)、[Report B](project-evolution-decision-retrospective-20261028.md)、[Report C](product-owner-playbook-ai-development-20261028.md)。本文只給結論。
- **v5 註記（2026-10-28 Owner 三輪答覆）：** Owner 回答了審查者 29 項質疑（Report B 9C）。結果：Owner 主要責任歸零，只剩 Owner 自述的「新東西一直加」為次要項；gw 改為「以量測為條件的必做路線」；AGY／OpenCode 可後接；第一版＝TUI 即時 PM 對話＋能用公司 LLM 搭配其他 AI 開發其他專案；預算每月 USD 300；不要排程報告，卡住要當 issue 主動找 Owner。查證：worktree 已清理（374→32、磁碟可用 28 GB）、`task advance` 與 TUI 即時 PM 已合併、gw thinking 開啟後 2/2。
- **v4 註記：** 補入 Owner 對省 token 路線的說明（Report B 9B）；DEC-008／029 改評 REASONABLE AT THE TIME。
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
5. 流程裡沒有使用者操作驗收的環節（Owner 私下是否操作無法觀察，不主張「從未」）：本機 store 推導 279 個 Task 只有 1 個 COMPLETED（受無終態影響）；374 個 worktree、15 GB、13 GB 磁碟剩餘無人管（2026-10-28 Owner 下令後已清理：32 個 worktree、可用 28 GB）。

## 問題四：責任歸屬（v3，以 Owner 為 Sponsor 的標準）
- **技術（AI 實作）：** God module、TaskStore 內嵌狀態機、Codex 契約過嚴（31% 無 artifact）、無終態、無 retry、硬編碼路徑、容量無人管。
- **AI PM 管理：** 需求不釐清直接展開（D6、AGY）、無影響分析、手動串接而非驅動器、merge train 雙 CI、每輪新 Reviewer worktree、docs PR 全 gate、把額度事件呈現為 Owner 決策並代記 73 則、收到容量警告（PR #322）不處理、從未安排 demo、從未監看額度（F15）、用術語而非白話告知（F16）。
- **AI CTO／Architect（Coordinator）：** F1、fake-only、五套授權、三套修復上限；12 項關鍵決策中 11 項無替代方案與停損紀錄；閉環未通時批准多路線。
- **AI QA／Reviewer：** 只審 diff，從未驗使用者可用性。
- **Owner（v5）：** 主要責任無。次要一項，Owner 自述：「新東西一直加，做不完」（10-28 同日多項新路線，Owner 確認是自己提的）；以 Sponsor 標準這是正常行為，AI 應回影響分析並排到 TUI 驗收後。v4 的兩項已處理：「目標先後未定」改寫（Owner 已定序：先省 token 工程再功能）；「委託模式切換」撤銷（AI 從未監看額度，Owner 是被迫補位，改列 F15）。「未要求 demo」v4 已撤銷。**沒有找到 AI 明確警告後 Owner 仍堅持的案例，也沒有找到 AI 給過替代方案或白話說明而 Owner 選錯的案例。** v1／v2 歸給 Owner 的「規格不完整、接受大型契約、無停損、未親手驗收、以 PR 判斷進度」已撤銷，改歸 AI 角色（Report B 第 6、9A 章）。

## 問題五：過度工程？
**是，集中在治理層。** 五套授權模型、三套修復上限、Worker 描述五處、政策文件約 60 萬 bytes、Mac CI 驗證自己 guard 的 12 個案例、每個 Reviewer 一個 worktree。核心執行層（provider、store、D6、CI）不過度。

## 問題六：重來絕對不做
F1 Git 容器化；fake-only 契約基礎設施；ai-loop 第二編排器；30 份 token benchmark 報告；五套授權模型；在閉環未通前開第二條 Developer 路線；讓額度事件變成 Owner 決策。

## 問題七：最有價值的成果
TaskStore + D6 lifecycle + Codex direct + EC2 receipt + CI 隔離 + cross-family review 規則 + nlf。這些是 MVP 需要的全部零件。

## 問題八：最該停止（全部 OWNER_DECISION_REQUIRED）
開可後接的路線（AGY／OpenCode／CliNest／Telegram／Web；Owner 2026-10-28 確認可後接）；merge train；每輪新建 Reviewer worktree；docs PR 正式審；額度 policy PR；High Debug 與 GitHub events 自動化；再寫任何超過兩頁的規範。**gw 不在停止之列**：Owner 定為必做路線，但條件是「不能節省就沒必要」，所以先量再決定。

## 問題九：最該優先
一條鏈：TUI 提交（已合併）→ PM 即時對話（pm_runtime 已接入 TUI，#544／#549）→ Codex direct → EC2 測試 → Reviewer → 一次自動退修 → PR → merge → COMPLETED（`task advance` #519 已合併）。零件已齊；**下一步是 Owner 親手在 TUI 跑一個真實任務**，這是第一版驗收（Owner：「提需求，到我想要的，對話要能即時回答我」）。之後依序：gw 接入同一條鏈並量測省不省；硬編碼→config 並在第二個專案目錄跑一次（Owner 要求第一版能開發其他專案）；Codex retry 與契約放寬。四週為建議期程。

## 問題十之前：AI 團隊的失職與能力限制（v3 新增）
- **第 13 天（10-09）Owner 已說「你們 AI 自行協調好就好」「停下來要人審查什麼我也不懂」；同日 Claude 的回顧已診斷出 AI 自製 gate、把 Owner 拉進迴圈的模式；之後在 fake-only、D4 per-call 核准、High Debug 重複三次。** 10-17、10-20、10-21、10-28 四次是 Owner 先於 AI 發現方向偏離。
- **AI 能力限制（坦白）：** 沒有跨 session 的持續記憶（文件膨脹的根因）；預設順從與擴寫（一句話變十頁）；無人值守連續運作不可靠；不會自動平行工作（Owner：Claude PM 仍要被提醒開 subagent；Codex PM 完全不平行）；從未監看自己的額度，而 PM 額度用盡時連溝通都停（單點故障，Report B 9C.4）。對策是確定性驅動器、硬性輸出格式（三選項＋停損＋一句白話）、隨時可 demo、卡住主動開 issue，不是把管理交回 Owner，也不是排程報告。
- **CTO 敢不敢負責：** 對架構判斷、計畫、品質標準與「下週給 Owner 能用的 E2E」敢；對無人值守連續運作不敢保證，需上述三個對策。

## 問題十：如果我是你的 CTO（建議；標示者需你決定）
1. NOW 你已經給了：「能使用公司 llm，搭配其他 ai，一起進行其他項目開發，省 token」。AI PM 把它展開成第一版清單（Report B 9C.6），其餘 FROZEN；驗收是你親手在 TUI 跑完真實任務。（OWNER_DECISION_REQUIRED：Roadmap）
2. 把 Developer／Reviewer 額度、runner、磁碟全部變成自動技術狀態，你不再為它們做決定；PM 自己的額度是單點故障，先砍 PM 每次載入的規則文件，不夠再談備援 PM。（OWNER_DECISION_REQUIRED：dispatch 規則）
3. 對 AI 的每個治理提案要求「短期最小版本 + 真實執行日期 + 停損」；持續 0 使用者列為檢討候選。
4. 分級流程：docs／test-only 不正式審；Reviewer 復用 worktree；Mac CI 只跑 Mac 專屬；merge train 停用。（OWNER_DECISION_REQUIRED：AGENTS.md 審查規則、D6 lifecycle、#425 Mac gate、合併流程）
5. 一頁看板：Codex tokens、Reviewer 輪數、Mac／EC2 分鐘、PM 手動命令數、ACTIVE worktree、磁碟、每月支出對照 USD 300。沒有前後數字的「節省」不採信；gw 省不省以此決定。
6. 文件瘦身（目標體積先量再定）；ROADMAP 只留現況表與下一步。（AGENTS.md／ROADMAP 修改為 OWNER_DECISION_REQUIRED）
7. 開源與 provider-neutral 是對的方向，但排在 MVP 驗收之後；硬編碼改 config 因你要求「開發其他專案」而提前到第一版，LICENSE 隨後。
8. 對你說話一律白話：任何提案先一句「做完你能／不能做什麼」；聽不懂是 AI 的問題。

**Owner 從今天起只做四件事（Report C 第 2 章）：** 一個 NOW（已給）；預算上限（已給，每月 USD 300）；想到就問、問到就要有 demo，看完說符合／不符合；只接受決策單格式。AI 卡住會以 issue 主動找你，不是你去發現。其餘由 AI PM／CTO／Engineering 承擔；AI 把技術細節或術語丟回來就退件。

**一句話：** 你已經有一台引擎的所有零件和一本很厚的安全手冊；把車裝起來、開一圈給你看，是技術團隊的責任，不是你的。
