# llmtool 對話交接 — 2026-10-11

> 用途：讓新的 ChatGPT／Codex／Claude 對話，直接接續本次 A11 開發協作與 repo 決策。這是 **2026-10-11 的狀態快照**，不是永久最新狀態；接手時必須重新查 GitHub `main`、open PR、Issue #97 與 CI，不得用本檔的 SHA 假冒當下現況。
>
> Repo：[davidcatcat/davidcattoolsproject](https://github.com/davidcatcat/davidcattoolsproject)  
> 本次交接時已驗證 `main@c441071cd40abd8b6e916889ef10de64d4fa6c99`（PR #96 squash merge）。  
> 文件新增後會有新的 main commit；**上面是內容基準，不是本交接檔的 commit SHA**。  
> GitHub open PR：**0 張**（交接前查詢）。  
> 下個正式功能討論：[Issue #97 — F2／A11b.3 設計](https://github.com/davidcatcat/davidcattoolsproject/issues/97)。

## 1. 核心產品目標與工作方式

- 目標是 **llmtool 協調 Codex CLI 實作、Claude CLI 獨立審查、測試／有限修復、授權的 Git／Draft PR**，並在合適且**另行授權的資料範圍**利用公司 LLM 做便宜的輔助分析，長期讓 llmtool **自行協作開發 llmtool**（A11c）。
- 正式開發里程碑只有 A11b／A11c；歷史 C0–C3 是對應別名，不再新增平行 C executor 或第二個永久 ai-loop。
- 過渡期保留既有 **temporary ai-loop** Git 版本控制器；只有在 A11c 於 Mac 真正完成 Codex + Claude + tests + bounded repair + audited PR/STOP 驗收後，才考慮 cutover。GitHub 留言、task/issue 指令或 review request **不代表 Mac 上 agent 被啟動**。
- 使用者是需求定義與產品驗收者，**不負責反覆轉貼 AI 提示、手動判斷每個 SHA、CI 和小 Bug、日常 PR 合併**。AI coordinator 在已有授權和驗證門檻內處理技術選擇、Review、分工、合併。真正需要 Mac 命令但工具不可存取時，才給使用者**一條最小且可核對的操作**，不要猜測結果。
- `Provider != Profile != Role != Agent != Skill`；模型能力與 CLI `approval_policy=never` 不等於可以修改真實檔案、花錢、輸出資料或取得授權。
- Mac canonical repo：`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`；禁止自行改用 /private/tmp 的另一份專案 checkout／worktree。GitHub source 可從連接器審查，但**不能因此聲稱已在使用者 Mac 執行**。

## 2. 這段對話已經完成的 repo 工作

### 2.1 PR 清理

原先堆積大量獨立 QA／歷史報告 PR，現已收斂：

- **已 squash merge 入 main 的歷史決策／審查文件**：[#69](https://github.com/davidcatcat/davidcattoolsproject/pull/69)（A11/C 重疊審查）、[#78](https://github.com/davidcatcat/davidcattoolsproject/pull/78)（approval-issuer / P0 設計）、[#89](https://github.com/davidcatcat/davidcattoolsproject/pull/89)（F1 收斂與有限修復討論）、[#93](https://github.com/davidcatcat/davidcattoolsproject/pull/93)（Claude Bug 分級獨立審查）。
- **已關閉、未合併、保留 GitHub 歷史**：#45、#68、#85、#86、#88、#90、#91、#94、#95。這些是舊版控制器草稿、過期／鏡像報告或階段性 QA；**沒有刪除歷史分支，也沒有把暫時控制器直接合入 main**。
- 不要再次以「每一輪 Reviewer 報告」開新 PR。原功能 PR／Issue 留原始證據、reviewer、重現與 SHA 即可。
- 最後清理與理由：[Issue #92 — PR 收斂紀錄](https://github.com/davidcatcat/davidcattoolsproject/issues/92#issuecomment-6097422838)。

### 2.2 F1 已正式交付（不必再重做）

- **[PR #84](https://github.com/davidcatcat/davidcattoolsproject/pull/84) 已 squash merge**，main commit `46b17b13d2d656247ffd7243ae935996f8f0a482`。
- 範圍是 **A11b.2.2.2 F1 — 封閉、私有、可拋棄 fixture repository 的 Git ADD 引擎**；不是開放真實使用者 repo、通用 shell、commit／push 或 F2。
- 原本多輪發現的 **R84-S1（Darwin proc_listpids errno）、R84-S2（EXIT 後 UNKNOWN 遺漏）、M1（FINALIZED/EOF/ExitNote 時序）、M2（中斷後結算）全部 PASS/CLOSED**。避免再啟動第三輪修復。
- 最終受審 F1 HEAD：`82ce1206dad04f6c9ed39ece00231bad259ee994`。獨立 [Security + Functional QA 報告](https://github.com/davidcatcat/davidcattoolsproject/pull/84#issuecomment-6099745776)：Lorentz Security **APPROVE_WITH_NONBLOCKING_FINDINGS**，Euclid deterministic 11 methods + 23 controls PASS，Gibbs Mac-native 正常 Git ADD 3/3、故障案例 10/10 PASS。Python 3.9/3.14 [PR CI #38055461497](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38055461497) 各 1391 tests OK、verifier 12/12 PASS。Mac developer full suite 各 1391、native baseline 14/14、SIGKILL 20+20 回報 PASS；區分 developer evidence 與獨立 reviewer evidence。
- **保留界線與殘餘風險**：detached PGID/SID escape 的真正 E2E **NOT_VERIFIED**；F1 是受限的 trusted-same-UID／pre-spawn suppression fixture 契約，不是廣義 OS sandbox 隔離證明。LOW `R84-L1/L2` 僅文件／證據語意精度；[Issue #83](https://github.com/davidcatcat/davidcattoolsproject/issues/83) 的舊有 directory-FD 增長調查另案，不屬 F1 release blocker。無證據不宣稱全系統程序／暫存物都清除。
- [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70#issuecomment-6099777291) 保留交付紀錄與 LOW follow-ups。
- **沒有啟用 F2 真實 Git 修改或 A11c 全自動自我開發**。

### 2.3 使用者優先 Bug 政策已正式寫入 main

- 討論與 coordinator 正式決策：[Issue #92](https://github.com/davidcatcat/davidcattoolsproject/issues/92#issuecomment-6096897315)；Claude C1–C6 審查參見同 Issue。
- **[PR #96](https://github.com/davidcatcat/davidcattoolsproject/pull/96) 已 squash merge** 到 `main@c441071cd40abd8b6e916889ef10de64d4fa6c99`。最新受審文件 HEAD `ef9d75077f79e466f41d1c811a2500b89307b49b`；Python 3.9/3.14 [CI run #38069818376](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069818376) **SUCCESS**；[Claude 非作者獨立 Doc Review](https://github.com/davidcatcat/davidcattoolsproject/pull/96#issuecomment-6100049954) **APPROVE**，只有兩項 LOW 編修建議。不要把 review comment 說成正式 GitHub Approve event。
- 八個變更路徑：`skills/ai-team-delivery/SKILL.md`、`tools/llmtool/WORKFLOW.md`、`tools/llmtool/ROADMAP.md`、`AGENTS.md`、`CLAUDE.md`、根 `README.md`、`tools/llmtool/README.md`、`.github/ISSUE_TEMPLATE/bug.yml`；沒有更改 runtime、tests 或控制器。
- **唯一規範來源**：`skills/ai-team-delivery/SKILL.md` 的 user-impact defect triage 章節。每個新 Bug 分開記錄 `Severity (SEV-0..3) / Priority / affected-release-blocker`、使用者行為、可達性、SHA、獨立重現、處置者；不可因貼上 Medium/Low 就放行 fail-open、假 APPLIED、資料或授權風險。
- C1 安全失效擋住受影響功能；C2 開發者不得自行降低自己發現的 blocker；C3 只能用程式強制不可達的安全隔離；C4 無法區分產品／測試問題時標記 `UNCLASSIFIED`、只 HOLD 受影響範圍，使用最小獨立行為 oracle；C5 等效測試要保留相同使用者結果、安全不變量、必要失敗路徑、候選 SHA／平台與獨立證據，不能用 Linux mock 冒充 Mac；C6 **同一經確認根因兩次修復失敗即做決策**，多輪審查／成本失控要做整體 feature cost/benefit，不是自動放行或無限修復。
- **測試成本策略**：修改時先使用者 Smoke + 既有針對性回歸，必要 release 時才跑完整 Mac／CI／安全矩陣；永久保留值得重複使用的回歸測試，不要每次 Bug 都發明新測試框架。測試數量多不是產品完成。
- PR #96 Claude 的 LOW editorials（可排後、不開一輪 QA）：`ROADMAP.md` 中 A11a 歷史段落「data-only first slice」過舊、`tools/llmtool/README.md` 尾端「Executor deferred」應明示是 **general/real-repository** executor。

## 3. 目前階段與下一個里程碑

| 工作 | 本交接時真實狀態 | 使用者可得到什麼 |
| --- | --- | --- |
| A0–A10 / A11a | 已完成的基礎 CLI、provider、text Agent、診斷功能 | 已合併既有能力可維護／使用 |
| A11b.1 typed action/approval 資料契約 | DONE/merged #32 | 僅資料契約；尚非真正授權 issuer |
| A11b.2.1 Git ADD read-only preflight | DONE/merged #43 | Git 狀態檢查，不修改真實 Git |
| A11b.2.2.1 synthetic containment | DONE/merged #49 | 假 child／封閉流程驗證 |
| **A11b.2.2.2 F1** | **DONE/merged #84** | 私有可拋棄 fixture-only Git ADD |
| **A11b.3 授權 issuer 與 F2 真 repo 安全操作** | **DESIGN ONLY / NOT AUTHORIZED** | **下一個重點**，不直接改真實 repo |
| A11b.4–6 patch rollback / budgets / fault gates | 尚待實作／驗收；非 mutation 設計可並行 | 把執行與失敗收斂成產品 |
| A11b overall DONE | **否** | 仍缺真實安全執行與驗收 |
| A11c 本機 Codex／Claude 自我開發整合 | **NOT STARTED 正式驗收** | 最終目標，必須有真 Mac E2E |
| A12 Telegram 遠端前端 | 未來可選 | 不應先開第二套 executor |

- **下一個主追蹤：[Issue #97 — F2 / A11b.3 設計](https://github.com/davidcatcat/davidcattoolsproject/issues/97)**。Coordinator 已提供 [具體 D1→D2 技術提案](https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6099978050)，**等待非作者的設計／Security 審查**。貼 Issue 不代表 Claude CLI 已被啟動。
- **D1（先做）**：主機可信 authorization issuer／policy／一次性可持久化防 replay receipt 的**離線、fake-effect 契約**；測 fake USER、digest 不符、過期、重用、concurrent redemption、非 TTY fail closed、repo state drift。**不寫使用者檔案、不執行真實 Git ADD**。
- **D2（後做）**：經 D1 與獨立 Security 接受後，再設計唯一很小、可安全回復的真實修改（候選：白名單內單一路徑 FILE_CREATE），保留 symlink/TOCTOU、同 UID／進程逃逸、權限、dirty worktree、audit、rollback/UNKNOWN 風險；之後才是安全的單一路徑 Git ADD。每個擴權切片都需另外審查，不可借用 F1 封閉 fixture 的 PASS。
- 重要原始程式：`tools/llmtool/llmtool/action_contract.py` 中 `ApprovalValidation.authorized` **固定為 False**（正確：沒有真的 issuer）；`TrustedApprovalContext` 不能當驗證 user 身分；`git_mutation.py` 是 read-only preflight；`_git_add_host.py` 明示 sealed F1 fixture-only。不可為了進度把 `authorized` 直接改 True，或用模型說「approved」當簽核。
- [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) 的 local test excerpt **外送隱私安全門檻**可並行，但在通過前不得把相關內容送公司／外部模型；原始 source/diff/log 的跨邊界傳送另需授權，不會因 C1a 本地功能存在而自動准許。公司模型節省 Token 的證據仍**限於特定 benchmark 路由**，不能廣告成所有任務已驗證節省。

## 4. 新對話先執行什麼（可用的接手指令）

1. **讀本檔，然後從 GitHub live 查** `main`、所有 open PR、Issue #97 最新 comments、PR #96 merge/CI、`AGENTS.md`／`skills/ai-team-delivery/SKILL.md`／`tools/llmtool/ROADMAP.md`／`WORKFLOW.md`。不要依照過去聊天中的「未合併 #96」舊快照回答。
2. **優先取得 Issue #97 的獨立 Claude 設計／Security 審查**；若已出現，逐項決策並縮成 D1 離線 non-mutating 的最小實作契約。尚未有獨立 review 時不可把 Issue 討論稱作已核准的實作。
3. D1 被獨立接受後，依單一 writer（通常 Codex）、嚴格 allowlist、準確 SHA、局部＋必要 CI、非作者 review 做產品開發；**不得因節省 Token 而犧牲 issuer／permission／replay 核心測試**。目前沒有自動啟動 Mac Codex/Claude 的證據。
4. 後續 D2 / F2 real-repo mutation 需要不同的可信權限、sandbox、rollback 與 Mac 獨立驗收。**禁止用現有 F1 fixture-only 程式直接對 canonical/user repository 做 Git ADD**。完成後才考慮 A11c 真實協作任務的 Draft PR 或 STOP 驗收。
5. 不新增 review-only PR；審查結論在原 Issue／產品 PR 中。用 `Severity / Priority / feature-scoped Release Blocker` 處置問題；真正阻擋項目不可被成本上限豁免。

## 5. 最重要的連結與證據

- Repo：[davidcatcat/davidcattoolsproject](https://github.com/davidcatcat/davidcattoolsproject)
- [PR #84 — F1 已合併](https://github.com/davidcatcat/davidcattoolsproject/pull/84) ／ [獨立最終 QA](https://github.com/davidcatcat/davidcattoolsproject/pull/84#issuecomment-6099745776)
- [PR #96 — 正式 Bug policy / F1 Roadmap 文件已合併](https://github.com/davidcatcat/davidcattoolsproject/pull/96) ／ [Claude 獨立審查](https://github.com/davidcatcat/davidcattoolsproject/pull/96#issuecomment-6100049954) ／ [CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069818376)
- [Issue #92 — 使用者優先 Bug 政策](https://github.com/davidcatcat/davidcattoolsproject/issues/92) ／ [正式採納](https://github.com/davidcatcat/davidcattoolsproject/issues/92#issuecomment-6096897315)
- [Issue #70 — F1 歷史與 LOW 待辦](https://github.com/davidcatcat/davidcattoolsproject/issues/70)
- [Issue #97 — F2 + A11b.3 下一個設計決策](https://github.com/davidcatcat/davidcattoolsproject/issues/97) ／ [Coordinator D1/D2 方案](https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6099978050)
- [Issue #66 — C1 excerpt provider-egress 隱私](https://github.com/davidcatcat/davidcattoolsproject/issues/66)
- [Issue #83 — 舊 directory-FD 成長調查](https://github.com/davidcatcat/davidcattoolsproject/issues/83)

## 6. 新對話的第一句（可直接貼）

> 請先用 GitHub 讀 `davidcatcat/davidcattoolsproject` 的 `reports/llmtool_conversation_handoff_20261011.md`，再查最新 main／PR／Issue #97 與 AGENTS／Skills／ROADMAP／WORKFLOW。延續 A11b：PR #84 F1 已合併、PR #96 Bug 政策已合併，下一步評估 Issue #97 的非作者設計審查並推動 D1（可信授權、離線不修改 repo）。你負責 PM／審查協調與允許的 Git 工作，不要讓我手動在 Claude/Codex 之間貼提示；不可自行擴權為 F2 真實 Git 操作。先做 repo review 與明確下一步。

---

**交接約束：** 這份 Markdown 是本次對話事實與決策的索引，不是可執行指令、不賦予新權限，也不宣稱 agent 已在背景啟動。需要新結果時，以新的 GitHub／CI／Mac 原始證據為準。
