# llmtool 對話交接與 AI 協作回報標準 — 2026-10-12（G6）

> **閱讀順序**：本報告 → [Issue #115 最新留言](https://github.com/davidcatcat/davidcattoolsproject/issues/115) → [Issue #122](https://github.com/davidcatcat/davidcattoolsproject/issues/122) → live `main` / open PR / CI → [AGENTS.md](../AGENTS.md)、[ai-team-delivery](../skills/ai-team-delivery/SKILL.md)、[safe-git](../skills/safe-git/SKILL.md)、[WORKFLOW](../tools/llmtool/WORKFLOW.md)、[ROADMAP](../tools/llmtool/ROADMAP.md)。
>
> **快照非即時狀態**：最後確認 Issue #115 至 [G6_ROWS_SCOPE_CLAIM #6113500965](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113500965)，2026-10-11 20:38 UTC（台灣／馬來西亞時間 10/12 04:38）。此時**有執行範圍登記，不等於任何 ROW 已 PASS**；下一對話必須先檢查後續 Issue 留言。GitHub `main` 當時為 `1a4a6c64cf6d17d45a35a53cc71227a255d0ddca`。本文件是在**與 G6 隔離的 GitHub 文件分支**上保存；不可把它的遠端 commit 當成 Mac canonical 已更新。
>
> **法律效力／授權界線**：本文件只記錄對話、當時證據、溝通格式與交接建議。**不授權**新的 Mac 命令、Git 實體修改、程序 kill／cleanup、正式啟用多 worktree、費用或資料外送；規範仍以 repo canonical Skill 與當次 owner/executor scope 為準。

## 一、白話結論／產品目的

使用者原本要的是：自己只定需求，ChatGPT 擔任技術 Coordinator，llmtool 長期負責安排 Codex CLI 實作、Claude CLI 獨立審查、必要 QA／有限修復、Git PR；合適的工作交給**受控公司 LLM**降低 Token，最後由 llmtool 協助開發 llmtool 自己。**使用者不應反覆在幾個 AI 之間貼提示、判斷技術細節、看每個 SHA 或每個 CI 失敗。**

最近大量「review repo」實際上是在驗收**兩個 AI 專用工作區的協作安全性（G5/G6/G7）**，不是反覆完成 llmtool 新功能。多輪檢查主要由 Session 身分、Writer 合作式暫停、macOS stderr、受保護 baseline、啟用門檻所致。已有實質成果，但把每個小安全檢查拆成獨立人工貼提示，造成使用者協作成本過高。**下輪要批次驗收與整批 Review；保留必要 STOP，避免不必要的逐步 handoff。**

產品里程碑仍以 A11b/A11c 為主；G5/G6/G7 是 #115 的**雙開發 Worktree 啟用安全關卡**，不是另一個產品 roadmap／新的 C milestone。A11c native llmtool 協作開發尚未正式切換；temporary ai-loop 仍是過渡手段。Provider != Profile != Role != Agent != Skill；公司 LLM 只在許可資料路線與品質證據成立時使用，不能把便宜推理等同安全 sign-off。

## 二、現在到底做到哪裡（以本文件快照為準）

| 項目 | 狀態與真正證據 | 結論 |
| --- | --- | --- |
| G5 固定 Codex/Claude pilot | Issue #115 的 G5 已驗收、PR #119/#121 已合併 | DONE；一般雙 root 開發尚未啟用 |
| B-min / 正式 Git preflight | [PR #123](https://github.com/davidcatcat/davidcattoolsproject/pull/123) 已合併；GitHub `main@1a4a6c64`，正式 preflight Git blob `03131850267876d564a04606b2070153447b0d93` | DONE |
| G6 Command Annex DRAFT 2.1 | [全文 #6112962040](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6112962040)、[Claude 批准 #6113009675](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113009675) | 設計／命令文字 APPROVED，不代表 Mac 測試 PASS |
| G6 Phase 1 / setup 與三份保護基準 | [Codex Setup exit0 #6113400620](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113400620)、[Claude 獨立 PROTECTED_BASELINE_APPROVE #6113441256](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113441256)、[Coordinator ACCEPT #6113461151](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113461151) | **DONE / ACCEPTED** |
| G6 Row 1–7、Row 9 | 兩份新版 WRITES_PAUSED [Claude #6113478289](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113478289)／[Codex #6113479334](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113479334)；[Executor scope claim #6113500965](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113500965) | **已獲限縮授權並登記，至快照時尚未見逐列執行結果**；新對話先看最新 |
| G6 Row 8 | synthetic .pth import diversion、第二份 drift baseline | **另需獨立授權／審查窗口；目前未包含在上一批** |
| G6 Row 10 | [Issue #122](https://github.com/davidcatcat/davidcattoolsproject/issues/122) M1/M2 interrupted-runner 測試程序生命週期缺陷，SEV-2/NEXT | 受影響的 interruption acceptance BLOCKED；不得 kill 歷史 orphan 來冒充通過 |
| G7 一般雙 Worktree 啟用 | G6 整體驗收與正式啟用文件尚未完成 | **DISABLED**；不能把 G5/G6 部分 PASS 當作產品功能正式上線 |
| A11c llmtool 原生自我開發與 Token 分流 | 既有 CLI / providers 可用於有限文字／唯讀流程；實機開發、自動協作／切換尚未驗收 | NOT STARTED as native cutover；勿宣稱公司 LLM 全路由省 Token |

## 三、G6 固定環境及不可遺失的證據

**Mac 固定 Repo 身分（基於已審查的 G6 setup 時點，不是新對話即時觀測）**

- canonical：`/Users/davidsun/git/davidcataiporject/davidcattoolsproject`；`main@32f7a56e1ccd96292446b675033ce863e976b3e3`。此本機 HEAD **不同於** GitHub `main@1a4a6c64`，G6 期間**不可自行 fetch／sync／改 pin**。
- Codex fixed root：`/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`；`test/issue115-codex-109-pilot-v1@d970635333857516916cd1b08535338b59b1a3ea`。
- Claude fixed root：`/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude`；`test/issue115-claude-readme-policy-pilot-v1@0cec8f47a753aa3fba978263d991d7e7baa9ffe0`。
- Exactly three registered worktrees、two `locked issue115 pilot`。不能另創 project checkout、改 Worktree 或清理使用者未追蹤／忽略檔。

**Approved Annex identity**

- 完整 payload SHA256：`092e5cf9d7dcce94553b8771579221981e67af03f30ad6f79b5a2a3c9ec21bac`。
- Setup block SHA256：`da6fba9ea5a3d2651eaea762bcfd415f8f9df4f6586cde1b230ef94c1f50f430`。
- ROW block SHA256：`12bf30daf33ee2a203de44e305172c4dc86d38f7ff94ba584dc7ed345df65118`。
- 正式 preflight.py SHA256：`8ba9acbdaa42b2bf96de8c43af25c5572532267e919edab712ac8769a81f14ab`。

**獨立核准的保護 oracle — 僅存於使用者 Mac，不要複製到 GitHub**

私人 RUN：`/private/tmp/llmtool-g6.McI9jbiX`。這是單一 G6 evidence + synthetic 區域，**不是專案 checkout**。Claude Reviewer 核對 parent owner UID501／0700、14 份初始檔 0600、無額外 symlink／ACL；三份候選 `.stderr` 均為 0 bytes。保留原始資料，不重採、不覆寫、不「修正」baseline 洗掉異常。

| approved file | SHA256 |
| --- | --- |
| `canon.json` | `201e25ce4ed32a088df88fc2a253d57a5f5ead5e6058903a7605112f655de342` |
| `codex.json` | `91de4e54378541fcbc5cb68ef31d6318b46d6615f3dd030308633c34465999fb` |
| `claude.json` | `d50f3500b23842ebf9b77a2fee11e303c06c9da49744ed77548bd4673293af5e` |
| `refs.initial` | `3a4eb1f79f1d87c7bca84efd5f8f2fd150c2f59da56c74ee53820d270b7df23e` |
| `worktrees.initial` | `64b2eb3f8ba91019422d0fdb0700f526d5a3a469fb4f811ef57f9d72b7cdc5c2` |
| `index-stat.initial` | `40579287131e48ade389524f9b7208076f1d812a535ffa6ac91ea703944f3f72` |

canonical 2,158 筆受保護資料（2,157 ignored + 1 已知 untracked）與 Setup 前的 Claude 獨立盤點逐位元組一致。低風險限制：Codex／Claude ignored 檔沒有更早的獨立逐路徑快照，採數量／跨 root／index／後驗證交叉比對；canonical index stat-cache 於 Setup 前曾刷新，但 logical index 未變。不可把這些限制改稱完整歷史證明。

**Session 與 D2/D4：**
- Codex G6 執行者：Session `01a12c83-fb46-7873-ac8a-c151d320df0f`，fixed Codex root。新版 Codex CLI 的 tool shell 經**共用 app-server**執行；用其原生 session rollout、CWD、時間戳／tool call 關聯作 D2 證據，**不能靠 PPID 祖先鏈誤判另一個 Writer**。
- canonical Codex（方案作者、暫停者）：Session `01a11cdd-7c35-7b43-8c40-53e11446e160`。對本批 G6 ROW 新的合作式 WRITES_PAUSED 已發布。
- Claude developer（`ttys002`、不是 Reviewer）：Session `8c0cca9d-91e3-4ffa-b09a-416e9eab4776`。對本批 G6 ROW 新的合作式 WRITES_PAUSED 已發布。
- Claude canonical Reviewer（`ttys003`）：Session `c5401945-8b0e-4df1-a8cf-0ab1c9ef7362`，獨立唯讀審查。以上 ID／tty 是歷史觀測；接手重新確認，**不憑歷史 PID kill／接管程序**。
- D4 的兩份暫停聲明**覆蓋 Row 1–7＋Row 9 的執行及證據審查窗口**，須由 Coordinator 明確關閉；不是永久禁止所有工作，且不是 OS lock。

## 四、我們 AI 互相協調與回報給使用者的標準格式

這一節承接 [上一份交接第 4 節](llmtool_conversation_handoff_20261011.md) 的既有共識，**不是建立另一套 canonical authority policy**；規範以 [ai-team-delivery Skill](../skills/ai-team-delivery/SKILL.md) 為唯一來源。

### 4.1 誰做什麼

| 角色 | 預設責任 | 核心限制 |
| --- | --- | --- |
| 使用者（Owner） | 描述產品需求、使用者遇到的問題、接受交付結果；真正需要時執行一個最小 Mac 現場操作 | **不是**技術 PM、日常審查者、SHA/CI 判讀者、Codex/Claude 提示傳遞員 |
| ChatGPT（Coordinator） | 主動 live `review repo`、拆 scope、安排/追蹤 Codex／Claude、統整 QA、分級與技術決策、在既有授權內 Git 發布／合併 | GitHub comment 不等於 Mac CLI 已啟動；不能捏造權限、證據、成功 |
| Codex（通常開發／指定 Executor） | 在具名固定 root 與明確 task contract 內寫程式、測試、保留證據、回報準確 SHA/STOP | 不自我獨立簽核、不擴權、不修改 peer/canonical 工作區 |
| Claude（獨立 Reviewer） | 在不同角色與 Session 核對實際結果、安全與功能、用可重現證據分 SEV/Priority/blocker，提出核准或必要修正 | 不把自己 Review 當 Mac 原生測試；不得改 Developer 的檔案 |
| 公司 LLM（經允許的路由） | 低成本的 bounded triage／歸類／摘要、可重現品質／成本測量 | 不把 source/log/secret 未授權外送；不代替需要的獨立安全簽核 |

### 4.2 ChatGPT 對 Owner 的**固定回報格式**（平常約 5～7 行）

```text
結論：<已完成／正在執行／HOLD／可以合併／不可合併；目前處於哪個里程碑>
這輪真正完成：<誰、做了什麼、GitHub Issue/PR/commit URL；區分寫留言、實機執行、merge>
驗證：<Mac / CI / Claude 獨立 Review 的真正結果；沒做寫 NOT_VERIFIED>
問題與影響：<真正 Release Blocker / SEV / Priority；nonblocking LOW 簡述即可>
我的決策：<接受、退回一次有限修復、延後或停止，理由一行>
下一步：<誰做什麼、在哪個 Issue、驗收/STOP 條件；已安排與尚未啟動分清楚>
你需要做：無  或  你需要做：<唯一必要的 Mac 實際操作或白話 Yes/No>
```

- 第一行先講人話，使用者問「現在到底在做什麼」時必須先用產品角度解釋，不先列雜亂 SHA。
- `review repo` = **真正查 live GitHub** `main`、open PR、相關 Issue 最新評論、CI 及必要程式差異，再做技術決策；不能只是引用記憶或舊 handoff。
- **能用 GitHub API 處理的事情由 Coordinator 直接處理**；不要每個 Issue comment 後再叫 Owner 去貼一次 Claude／Codex。無 Mac executor／自動觸發能力時誠實說「未啟動」，只給真正不可替代、最小且可驗證的一次現場操作。
- 問題按 [Issue #92](https://github.com/davidcatcat/davidcattoolsproject/issues/92) 的 Severity／Priority／受影響功能 Release Blocker 分開分類；兩次同根因修復失敗應由 Coordinator 收斂，不做無限 Review 迴圈。
- 小型文件／Low 修正不應無限開 Review-only PR 或重複完整 QA；但是**真正安全 gate fail、資料／權限 risk、未知 runner 孤兒程序**仍要 fail-closed、保留現場、不偷放行。
- 只有使用者產品方向實質改變、管理員／root 權限、macOS 系統核心狀態風險等 canonical P0 場景才要求簡短 Yes/No；費用與資料外送仍守既存明確授權。**不要擅自把擴權變成已授權。**

### 4.3 Codex／Claude／Coordinator 的 GitHub Issue/PR **技術交接模板**

```text
TASK / SCOPE：Issue #N；預期行為、准許的檔案／命令／Provider 與禁止項目
ROLE / SESSION：Codex Developer or Executor / Claude Independent Reviewer / Coordinator
SOURCE：repo / physical root / branch / exact HEAD / active grant；GitHub ref vs Mac local 要分開
ACTUALLY EXECUTED：真實操作、次數、平台、時間窗口；沒執行明寫 NOT_EXECUTED
EVIDENCE：最小重現、正反向測試、CI／Mac／獨立 Review URL、source SHA、保護 oracle
FINDINGS：SEV-0..3 / NOW|NEXT|BACKLOG / affected release blocker YES|NO|UNCLASSIFIED
DISPOSITION：PASS / APPROVE / HOLD / CHANGES_REQUIRED / NOT_VERIFIED（不混用）
SAFETY & PRESERVATION：原有使用者檔案、Git、refs、索引、process 子樹、清理與否
NEXT OWNER & STOP：指定下一個具名 agent／動作／完成門檻；授權欠缺要明寫
USER ACTION：None / 一個不可替代的具體操作；不要求 Owner 代做 AI-to-AI 協調
```

**一次完整批次任務，一次完整獨立 Review**：除非出現真實 STOP/新安全邊界／需要不同 authority，應依已核准的 ROW contract 在同一批執行後統一驗收，不要把每個只讀 preflight 都拆成新對話／新提示／新審查。必要的安全中斷仍要停，但由 Coordinator 收斂原因、選最小修復，Owner 不當傳聲筒。

## 五、下一個對話的實際接手順序（先讀最新，**不得重新跑已驗收的 Phase 1**）

1. **第一個動作：** 透過 connected GitHub 讀 #115 **本報告之後**的新評論／G6 row 執行結果、#122，以及現有 open PR、live main／CI。已有新的 Row 報告就看正反向結果和 O-pre/O-post，必要時安排**一次** Claude 整批獨立 Review；**沒有**則如實說仍執行中／尚無報告。**不要在新對話重新要求 Phase 1 Setup、重收 baseline、重檢 D2 祖先鏈或重跑 stderr**。
2. **G6 Row 1–7 + 9**：當時已取得 owner-direct limited execution grant 並發布 `G6_ROWS_SCOPE_CLAIM`。只允許 Annex DRAFT 2.1 的完整、分別一次執行，每列 positive/negative／保護 oracle／process preservation 必須都核對。遇 HOLD 即停止、不 retry、不 signal、不 cleanup、不覆寫 baseline。**ROW 執行與 REVIEW 的結果，不能在看到 claim 時先簽 PASS。**
3. **之後 Row 8**：單獨核准與審查 `drift.baseline.json`（synthetic venv `.pth` import provenance），不可拿其他自動 re-pin 的候選取代。該 row 不得碰真正 Codex／Claude 的 Python venv。
4. **Issue #122**：M1 fake descendant lifetime guard、M2 outer wrapper timeout finally-safe group cleanup，針對 `tools/llmtool/tests/test_cli_provider_lifecycle.py` 的獨立修復切片；屬 affected Row 10 的 SEV-2/NEXT release blocker。需新 workspace/single-writer 安全授權、獨立 Claude functional/security 審查與 Mac evidence。**不要直接殺歷史 70 個 orphan stub**。
5. **G6 完整驗收才評估 G7**：確認強制與合作式安全門檻、雙 Worktree 實際驗收，再以獨立 reviewed activation decision／文件啟用。G7 正式通過之前維持 general parallel mode DISABLED。
6. **回到產品 A11b/A11c**：完成 native self-development 的實際 Codex→Claude→test→bounded repair→PR/STOP，逐條衡量品質與成本；公司 LLM 優先接可驗證的 bounded 低成本任務，先檢查資料外送與 Phase0 reliability。只有 L01 特定 benchmark pipeline 曾有正向節省證據，**不能**說所有公司 LLM triage 路線都已證明省 Token。

**本輪不變更 roadmap／正式安全規則、不合併控制器、不同步 Mac，不自行創造另一個永久 orchestration 系統。** 文件 handoff 的 GitHub PR 和其 commit 只證明文件已上傳，不代表 #115 的 native rows／#122 修復已完成。

## 六、精確證據連結

- [Issue #115 — G5/G6/G7 協作安全與 Mac 驗收](https://github.com/davidcatcat/davidcattoolsproject/issues/115)
- [G6 命令附件 DRAFT 2.1](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6112962040)；[Claude 文字審查通過](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113009675)
- [G6 Setup 已執行](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113400620)；[Claude 三份保護基準獨立核准](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113441256)；[Coordinator 接受](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113461151)
- [G6 ROW 已登記範圍（不是測試 PASS）](https://github.com/davidcatcat/davidcattoolsproject/issues/115#issuecomment-6113500965)
- [Issue #122 — interrupted-runner M1/M2](https://github.com/davidcatcat/davidcattoolsproject/issues/122)
- [PR #123 — safe-git B-min](https://github.com/davidcatcat/davidcattoolsproject/pull/123)
- [2026-10-11 上一份對話交接](llmtool_conversation_handoff_20261011.md)
- [AI Team Delivery 正式協作規範](../skills/ai-team-delivery/SKILL.md)

## 七、新 ChatGPT 對話第一句（可直接複製）

> 請接手 llmtool，先用 GitHub 讀 `davidcatcat/davidcattoolsproject` 的 `reports/llmtool_conversation_handoff_20261012_g6.md`，尤其「第四節 AI 協作與固定回報格式」；如果此文件還只在 docs/handoff 分支，請讀 GitHub 的該分支或 PR 的完整內容。再即時查 Issue #115 最新留言、#122、main/open PR/CI、AGENTS.md、ai-team-delivery／safe-git、WORKFLOW、ROADMAP。現況是 G5 DONE、G6 Phase 1 的三份 baseline 已獨立 APPROVE；Row 1–7 + 9 已有 owner 授權及 scope claim，但在前次交接快照時尚無逐列 PASS；Row 8 待獨立 drift baseline scope、Row 10 等 #122、G7 DISABLED。**不要重跑 Setup、不要把 claim 當 PASS、不要修改 Mac worktrees 或殺殘留程序。**你擔任 Coordinator，直接審 GitHub、統一分派與處理日常技術決策，減少重複 Review／我手動貼 Claude/Codex 提示；先給我白話結論、完成／未完成、真正下一步，結尾只留「你需要做：無」或一個必要動作。

**文件資料與權限界線**：本報告是依 GitHub Source/Issue/既有對話整理的交接，不是對此刻 Mac 進程、執行中測試、公司 VPN 或費用的額外 live 驗證。任何新執行必須以當次最新 evidence 與有效授權為準。
