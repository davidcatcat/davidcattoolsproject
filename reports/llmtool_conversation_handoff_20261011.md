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

## 4. 使用者與 AI 協作規則，以及回報給使用者的固定格式

> **本節是新對話需沿用的溝通／交付習慣。** 實際技術權限、P0 升級規則、安全驗收與 Git 操作仍以 [ai-team-delivery Skill](../skills/ai-team-delivery/SKILL.md)／[safe-git Skill](../skills/safe-git/SKILL.md) 為準；這裡**不創造額外授權**。

### 4.1 如何合作（使用者只負責目標、使用體驗與必要決策）

| 角色 | 預設職責 | 不該丟給使用者 |
| --- | --- | --- |
| **使用者（需求／產品驗收）** | 用白話描述要達成什麼、目前實際用起來哪裡不對、功能是否符合需求；必要時執行一條無法遠端代跑的 Mac 命令 | 轉貼 Codex／Claude 提示、判斷 test harness、看每個 commit/CI、處理日常 merge |
| **ChatGPT（coordinator／技術 PM）** | 先讀 repo，定義當前階段、依賴、工作範圍、驗收標準；安排開發／獨立 QA、分級 Bug、控制成本；在既有權限與完整 gate 下直接合併並記錄 GitHub | 自稱已啟動不能連的 Mac agent、無證據說測試通過、在真正安全問題上偷放行 |
| **Codex（預設 Developer／單一 writer）** | 照已核准 Task Contract 實作、加最小必要回歸、跑相關及 release 規定的測試、回報精確 SHA／證據 | 自己審查簽核自己、擴大實作／權限／成本、任意清理使用者 Git 變更 |
| **Claude（預設獨立 Reviewer）** | 對同一個精確 SHA 做設計、程式、安全／功能審查，具體列出可重現問題與 `APPROVE/REQUEST_CHANGES`，證據留原 PR／Issue | 每輪額外開 report-only PR、把一般 review 當成真正執行的 Mac QA、無授權改原作者的檔案 |
| **公司 LLM（可用時、經授權）** | 對允許輸出的最小資料做便宜分析／triage，將品質與 Token 成本如實記錄 | 取得不曾授權的 source/log/secret，因便宜而代替必要獨立驗證 |

- **先事實，後決策，最後執行**：查即時 GitHub 主幹／PR／Issue／CI，再告訴使用者目前做到哪裡；不要只重述上次記憶。檢查或發布 GitHub comment **不是** Mac 上的 Codex／Claude 執行。
- **少打擾、不中斷**：使用者常只說 `review repo`、`下一步`。應自行完成可做的 repo review／決策／允許的 Git 操作並回報結果，**不要自動要求使用者轉貼提示或對每次 Low 問題簽核**。不擅自觸發未授權的真 repo mutation、付費、provider 資料外送。
- **P0 唯一直接需求方升級**依 canonical Skill：功能方向與原需求有重大差異需決定；需要系統管理員／root 權限；可能修改 macOS 核心／系統狀態。用一個白話 yes/no 問題說明兩種後果；其他工程問題由 AI 自己處理。另有既存費用、資料外送授權邊界，欠授權就停止相應操作，不推定沉默同意。
- **Mac 無法由這段 ChatGPT 直接操作**時，只能在必要時提供**一條**已核對的最小操作命令，說明預期輸出與失敗 STOP；不要把整個 Agent 協作工作轉交使用者。關機時不能假裝 ai-loop/llmtool 繼續在本機執行。只有真正設定且能核對的遠端 executor／排程才能聲稱持續執行。
- **Security 和成本共同收斂**：使用者優先，能用的正常功能先交付；但仍要守住 fail-closed／正確結果／權限與必要 QA。Codex 兩次同根因失敗即升級技術決策，不做無限新測試框架，非阻擋 Low 留 backlog。保留有價值的回歸測試，優先重用。

### 4.2 每次報告給使用者的格式（繁體中文、先結論、短而具體）

**預設 5～7 行即可**；簡單問題只需一兩段，重大 review／安全決策才加表格與細節。不要把 CI 日誌、測試案例、整段 AI 提示直接傾倒給使用者。

1. **結論（第一句）**：直說 `已完成／可合併／不能合併／尚未啟動／目前在 A11b.X`，不要先鋪長篇故事。
2. **已做事項（實際動作）**：誰做了什麼；附精簡的 PR／Issue／Commit 網址。分開說 `已發布 comment`、`已推送程式`、`已合併 main`，不可把它們當同一件事。
3. **驗證與 QA**：列真正已執行的 CI、測試數量與平台、是否非作者、安全／功能驗收、exact SHA；未做寫 `NOT_VERIFIED / PENDING`，不要把自己讀 source 當成 Mac PASS。
4. **風險／阻擋**：只突出影響使用者或 release 的問題；寫明 **Severity／Priority／是否阻擋這個功能**，區分 blocker 與 nonblocking LOW。不要因 F2 Hold 就宣稱已合併 A0–A11a 都不可用。
5. **下一步（已安排或可立即執行）**：具體到 `誰／做什麼／在哪張 PR 或 Issue／完成門檻`；有實際工具就先做再回報，不要每輪丟回「要不要我做？」。若只能規劃而沒有 executor，明講尚未啟動。
6. **最後單獨一行**：嚴格用 `你需要做：無`，或 `你需要做：<一個真正必要的簡單動作或 P0 白話 yes/no 問題>`；這是 canonical Skill 已記載的規範。

**簡短實例（僅回報形式，不是現在的 live 狀態）：**

> **結論：** A11b.3 的設計已完成審查；F2 程式尚未授權開發。  
> **已完成：** Claude 非作者審查已貼在 Issue #N；我已接受 2 項必要修正。  
> **驗證：** 設計審查 PASS；尚無 Mac 實作測試（NOT_VERIFIED）。  
> **風險：** 真實 repo 修改仍為 Release Blocker，不影響現有 F1 fixture 功能。  
> **下一步：** Codex 只實作 D1 離線授權資料契約，完成後再獨立驗收。  
> **你需要做：無。**

### 4.3 使用者常見指令如何回覆

- **`review repo`**：主動查最新 main／open PR／Issue／CI／新 Claude 報告與必要 code diff → 一句結論 → 是否可 Merge、為何 → 可安全做的事情直接做 → 交付連結與下一步；沒獨立簽核要誠實標明。
- **`下一步`／`執行下一步`**：讀 Roadmap 和既有決策，不重做已完成項目；先處理依賴與當前 blocker，再產生小範圍工作成果，不停在空泛計畫或要求使用者選模型。
- **`有 bug`／`測試失敗`**：先確認真使用者影響，區分 production bug、test bug、環境限制；用獨立最小 oracle，再依 SEV/Priority/release blocker 決策。除非涉及真正 blocking safety，不自動打開更多輪測試框架。
- **`進度呢`／`做到哪了`**：報具體 A milestone、已合併、待驗收、到 A11c 還缺什麼；不要把 Draft／設計／GitHub Issue 當 DONE。
- **`我要關 Mac／開新對話`**：清楚說什麼不會繼續跑，保存 repo 中的交接 SHA、控制器執行與停機限制、下一個明確切片；不要承諾無實際排程／executor 的背景工作。

---
## 5. 新對話先執行什麼（可用的接手指令）

1. **讀本檔，然後從 GitHub live 查** `main`、所有 open PR、Issue #97 最新 comments、PR #96 merge/CI、`AGENTS.md`／`skills/ai-team-delivery/SKILL.md`／`tools/llmtool/ROADMAP.md`／`WORKFLOW.md`。不要依照過去聊天中的「未合併 #96」舊快照回答。
2. **優先取得 Issue #97 的獨立 Claude 設計／Security 審查**；若已出現，逐項決策並縮成 D1 離線 non-mutating 的最小實作契約。尚未有獨立 review 時不可把 Issue 討論稱作已核准的實作。
3. D1 被獨立接受後，依單一 writer（通常 Codex）、嚴格 allowlist、準確 SHA、局部＋必要 CI、非作者 review 做產品開發；**不得因節省 Token 而犧牲 issuer／permission／replay 核心測試**。目前沒有自動啟動 Mac Codex/Claude 的證據。
4. 後續 D2 / F2 real-repo mutation 需要不同的可信權限、sandbox、rollback 與 Mac 獨立驗收。**禁止用現有 F1 fixture-only 程式直接對 canonical/user repository 做 Git ADD**。完成後才考慮 A11c 真實協作任務的 Draft PR 或 STOP 驗收。
5. 不新增 review-only PR；審查結論在原 Issue／產品 PR 中。用 `Severity / Priority / feature-scoped Release Blocker` 處置問題；真正阻擋項目不可被成本上限豁免。

## 6. 最重要的連結與證據

- Repo：[davidcatcat/davidcattoolsproject](https://github.com/davidcatcat/davidcattoolsproject)
- [PR #84 — F1 已合併](https://github.com/davidcatcat/davidcattoolsproject/pull/84) ／ [獨立最終 QA](https://github.com/davidcatcat/davidcattoolsproject/pull/84#issuecomment-6099745776)
- [PR #96 — 正式 Bug policy / F1 Roadmap 文件已合併](https://github.com/davidcatcat/davidcattoolsproject/pull/96) ／ [Claude 獨立審查](https://github.com/davidcatcat/davidcattoolsproject/pull/96#issuecomment-6100049954) ／ [CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38069818376)
- [Issue #92 — 使用者優先 Bug 政策](https://github.com/davidcatcat/davidcattoolsproject/issues/92) ／ [正式採納](https://github.com/davidcatcat/davidcattoolsproject/issues/92#issuecomment-6096897315)
- [Issue #70 — F1 歷史與 LOW 待辦](https://github.com/davidcatcat/davidcattoolsproject/issues/70)
- [Issue #97 — F2 + A11b.3 下一個設計決策](https://github.com/davidcatcat/davidcattoolsproject/issues/97) ／ [Coordinator D1/D2 方案](https://github.com/davidcatcat/davidcattoolsproject/issues/97#issuecomment-6099978050)
- [Issue #66 — C1 excerpt provider-egress 隱私](https://github.com/davidcatcat/davidcattoolsproject/issues/66)
- [Issue #83 — 舊 directory-FD 成長調查](https://github.com/davidcatcat/davidcattoolsproject/issues/83)

## 7. 新對話的第一句（可直接貼）

> 請先用 GitHub 讀 `davidcatcat/davidcattoolsproject` 的 `reports/llmtool_conversation_handoff_20261011.md`（**尤其是第 4 節「使用者與 AI 協作＋固定回報格式」**），再查最新 main／PR／Issue #97 與 AGENTS／Skills／ROADMAP／WORKFLOW。延續 A11b：PR #84 F1 已合併、PR #96 Bug 政策已合併，下一步評估 Issue #97 的非作者設計審查並推動 D1（可信授權、離線不修改 repo）。你負責 PM／審查協調與允許的 Git 工作，不要讓我手動在 Claude/Codex 之間貼提示；不可自行擴權為 F2 真實 Git 操作。先做 repo review 與明確下一步。

---

**交接約束：** 這份 Markdown 是本次對話事實與決策的索引，不是可執行指令、不賦予新權限，也不宣稱 agent 已在背景啟動。需要新結果時，以新的 GitHub／CI／Mac 原始證據為準。
