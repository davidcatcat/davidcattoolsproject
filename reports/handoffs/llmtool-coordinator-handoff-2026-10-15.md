# llmtool Coordinator — 對話交接與協作標準（2026-10-15）

> 狀態快照以 GitHub Issue #120、PR #147、CI 與 main 即時查詢為準。此文件是 Coordinator handoff，不是 Native 授權、不取代原 CLI 身分或 local baseline 證據。新對話首先重新 review repo，不應把本快照當永久最新狀態。

## 0. One-line executive status
A11c.1: B1 fake-only 已由 PR #146 合併；Native Delta A1 已完成受控 C3、十檔 source/fake 實作、Task-Final、Draft PR #147、雙版本 CI。PR #147 等待**原 Claude Independent Reviewer 正式 Security + Functional 審查**；未經正式審查不合併。真實原生跨 CLI pickup 仍 **NOT_VERIFIED / NATIVE_PICKUP_GRANT NOT_AUTHORIZED**。**NEXT_AI = 原 Claude Independent Reviewer**。

## 1. 最新確認狀態（GitHub live）
- Repo: https://github.com/davidcatcat/davidcattoolsproject
- Tracker: https://github.com/davidcatcat/davidcattoolsproject/issues/120
- main: `e526c6145a0fb77de6af56a653f15e50f57c74eb`（已含 #146）
- 唯一 open PR: **Draft #147** https://github.com/davidcatcat/davidcattoolsproject/pull/147
- PR #147 HEAD: `102e1b76237e926e7cc1103f33181dc4302358e1`；base `e526c6145a0fb77de6af56a653f15e50f57c74eb`；十檔（2 ADD/8 MODIFY）。
- Local relevant regression: **429 PASS, 0 failures/errors/skips**；same-session read-only advisory 發現三個 NOW root families，兩輪有界修復後 NOW0；此預審不是獨立 Claude 批准。
- Exact-head GitHub CI: https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38603255335；Python 3.9/3.14 **SUCCESS**，各 1849 tests / 32 skips / 0 failures / 0 errors；verifier 12/12，clean-tree PASS。
- PR Claude review request: https://github.com/davidcatcat/davidcattoolsproject/pull/147#issuecomment-6173820479 。原獨立 Claude 正式 Security/Functional verdict **PENDING**（截至建立本報告的 repo review）。
- Latest Developer evidence: https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6173902839
- Publication matching RELEASE: https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6173802065
- Task-Final: `/private/tmp/issue120-native-delta-a1-publication-088kxe85/task-final-candidate.json`; SHA256 `0a5f6e3b2015b772ba97e93852db1b9f361b473c0d8f7e812e04513ac80e87a9`; inventory index536/tracked536/untracked0/ignored1917。**作者 preflight PASS；舊任務/新任務獨立 Final Gate 不能自動冒稱 PASS**。
- Source manifest SHA256 `122dc773608108c27e22ba0645207715a7cc7a3d2de1de89301bd3efcda85166`。
- No live queue/RPC/model inference/company LLM/Host installation/T1/F2/S2; fallback ai-loop 保留。

## 2. 這段對話完成的時間線
1. 原 Codex Developer B1 Source Package 完成、Draft #146、Python 3.9/3.14 exact-head CI PASS。
2. 原 Claude Reviewer 對 #146 Security APPROVE、Functional APPROVE、NOW0，列 Native 前 SEV-3 P1～P5。Coordinator 將 #146 轉正式、SHA-guarded squash merge，main = `e526c614...`。
3. 原 Codex Integrator 盤點 Native Readiness：已有原 CLI/server/queue protocol 線索，真正 exact queue/list/delete、turn/interrupt、事件可信度、Claude receipt、用量與 Full Access 的限制仍未原生實測。
4. Developer 完成 Native Delta A1 v1 十檔契約；Integrator ACCEPT Scope；Claude 非作者 ACCEPT Security+Functional，要求 Q1 明確工具類型與 N1～N4。
5. 一次提示誤貼到 Developer Session，被正確地 STOP/PRESERVE；改由真正原 Integrator 接續。Q1/N2/N3 最終 pins、old-final baseline 独立 PASS、NO_OVERLAP、受控 branch turnover。
6. Developer 建立 `feat/codex-issue120-native-delta-a1-20261014`、new Task-Start；原 Integrator NEW_C3_BASELINE_ACCEPTED / PASS、解除 SOURCE_WRITE HOLD： https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6172847952
7. Developer 一次完成 Delta A1 十檔、fake regression、advisory 雙輪修復、Task-Final、publish Draft #147、exact-head CI SUCCESS。**停在原 Claude 正式非作者 source review。**

## 3. 四個原始 CLI 角色、根目錄與身分
- **Codex Integrator / Reviewer**：Session `01a12e4b-1ac5-7c11-9071-6c01c3aafb5f`，canonical `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`。接受真正獨立 baseline、Git gate、統一派工；不跨root冒做開發。
- **Codex Developer**：Session `01a12f8c-6a46-71c2-a7b3-3e6211ec2e9c`，`/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`。唯一 Codex source writer；本 PR #147 作者。
- **Claude Independent Reviewer**：Session `081de22e-d514-4288-9d1f-48f488979271`，canonical root，與 Claude Developer 不同的 Session。**現在 NEXT_AI，唯讀審 PR #147**。
- **Claude Developer / author**：Session `5e05868e-9b7c-41f3-b29b-de3ed351aef4`，`/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/claude`。未來原 Session receipt waiter 應由本人擁有，不能用新的同 ID 假 Session 或跨 root writer。
- 不按 ttys/PID 當永久身分；CLI 必須自行核對 `CODEX_THREAD_ID` / session metadata、pwd/toplevel、worktree、HEAD。將提示貼錯 session 時 STOP 而非冒名。

## 4. 標準「review repo」協作流程／回覆格式（新的 ChatGPT Coordinator 必須沿用）
先使用**連接的 GitHub**查 `main`、open PR、PR HEAD/draft/CI/comments/formal independent review、Issue #120 最新留言；必要時看 diff 和查 source。以前的 ChatGPT 結論不是新證據。先回答**這次比上次真正變了什麼**，避免冗長重複歷史。

給 Owner 的固定精簡格式：
1. **結論一句白話**：DONE / BLOCKED / PENDING，是否可合併/實作/實測。
2. **證據與版本**：Issue comment / PR URL、exact HEAD、必要 tests/CI、獨立 reviewer verdict；作者 advisory 不能冒充獨立 PASS。
3. **NOW vs LATER**：SEV-1/2 功能/安全阻塞優先處理；SEV-3/4 與非阻塞問題記錄，不因 Low 一直開修復迴圈。已通過 Gate 不重跑、不重設計。
4. **唯一 NEXT_AI**：明確哪一個**原 CLI、角色與 Session、正確 root**，為何是它。若無新事，不要求無意義重貼。
5. **完整一次性可複製提示**：集中足夠任務讓 Developer 自己預審、最多有界修復、一張 PR、完整測試和 CI；除真正獨立身分/授權/前後 gate，**不要每個小步就叫 Owner 在 Claude/Codex 間搬運**。
6. **Owner 需做什麼**：能由 Coordinator GitHub 工具執行（如在正式雙審+CI通過後的合併）就直接完成並回報；僅在 CLI 本機執行或未授權 live 行動才要 Owner 將提示貼到正確的終端。GitHub comment 不會自動喚醒 CLI；不偽稱後台自動開發。

使用者授權/偏好：
- Owner 明確選擇 Codex `danger-full-access`、`approval_policy=never`、trusted 工作區；AI 不代 Owner 撤銷或自動改權限。Full Access **不是** Native egress/模型資料 route grant，也不等同強制 no-tools。
- Coordinator 負責準確風險資訊與工程驗收，不否決 owner 的本機操作授權。Native `AUDIT_ONLY_TRIAL` 僅能事後認證完整可觀測的那次 turn 無工具，無法預防工具作用，不能偽稱 ENFORCED_NO_TOOLS。
- 目標是 Codex + Claude + 公司 LLM + llmtool 自動協作、減少 Token 用量與人工轉貼；`ai-loop` 是 fallback，不能宣稱已停用。
- 使用公司 LLM（VPN HTTP `10.29.7.58:4000/v1/chat/completions`，model `gw`）時要提前提醒**接公司 VPN**；沒用時不必提醒；VPN 有時會擋對外官方文件。
- 程式碼主要讓 CLI 開發，不由 ChatGPT 對話擅寫 source；Coordinator 負責路線、Gate、審查、整合/合併及 GitHub 交接。
- 尊重 original Session/worktree 與 shared refs，單 writer，真 CLAIM/RELEASE、獨立 C3、Task-Final 保存；不可用自家 subagent 冒稱不同 Session 的 formal independent reviewer。不要為省事任意清理 stash/ignored/venv、重寫 baseline、跨 root 寫入或更改 owner 配置。
- GitHub Actions 額度/成本有限：合併/必要版本測試採聚焦本地回歸＋exact-head 3.9/3.14 CI，避免無限重跑 full suite；必要例外依 Gate 證據決定。

## 5. 現行 Native Delta A1 產品界線與後續
已實作 fake-only native adapter/policy/notification：
- P1/Q1：僅 UserMessage、AgentMessage、Reasoning、Plan 是確定 non-tool；其他/未知/子代理/other-thread fail closed，完整 message/turn coverage。
- P3/N3：區分 Reviewer 完成與 Claude 作者 receipt；Completed vs Failed/Interrupted/InProgress，PARTIAL 或 UNKNOWN/pending，精確 terminal/child exit 及禁止重送。
- P5：llmtool provider calls 必須 delta 0；Reviewer、Claude inference 分域。
- N2：完整 JSON/ASCII echo 的64KiB cap (queue/add、list、UserMessage)、精確 queue/client/item/turn IDs。
- NOTIFICATION layout v2 與 D1/provider ledger 隔離，bounded synthetic socket/child fake acceptance。Public native entry 預設 BLOCKED。
- 真 Native **尚未授權或驗證**：沒有 original server endpoint real queue/delete/interrupt proof、Host 原生完整事件 attestation、原 Claude owned bounded receipt waiter、原 context/data route 與模型 inference/cost grant。
- Native 授權前：N1 inline synthetic packet 留在原 Full Access context 的殘留/注入風險與後續 Session 處置；N4 experimental queue API initialize opt-in/client-local 證據；L2 Audit-only vs enforced no-tools、L4 使用量/成本 UNKNOWN。P2/P4 one-shot STOP/PRESERVE，不宣稱 production recovery。
- T1 HOLD、F2/S2 不在 #147 範圍，不因 PR fake PASS 自動放行；company LLM 不在這輪。
- 初次 Native Trial 應限明確 synthetic、固定原 Session、明確資料 route+context、至多一個 request turn、獨立 Claude 如需模型 turn 另授權、一次 queue/add、0 retry/fallback、限時/精確取消、完整五維證據/作者原始收件，無證據即 UNKNOWN/STOP。**沒有 live grant 前不得執行**。

## 6. 當下唯一 NEXT_AI 與交付準則
**原 Claude Independent Reviewer Session `081de22e-d514-4288-9d1f-48f488979271`。**
依 https://github.com/davidcatcat/davidcattoolsproject/pull/147#issuecomment-6173820479 直接審 PR #147 exact HEAD `102e1b76237e926e7cc1103f33181dc4302358e1` 的十檔：
- Security: APPROVE / CHANGES_REQUIRED。
- Functional: APPROVE / CHANGES_REQUIRED。
- NOW blockers 文件/行號/重現與嚴重度；LATER獨立列示。
- 仔細檢查 Q1/P1/N2/N3/P3/P5、queue-ID/cursor/withdrawal、owned exit、ledger、counter、Full Access/Audit-only、來源與 Native NOT_VERIFIED 區分。
- 唯讀原 Reviewer 不直接合併/改 developer worktree，不需重開 Source Contract/C3，不需重跑已有 exact-head CI。
- 若 NOW0 + 雙 APPROVE 且 exact PR HEAD unchanged/CI成功：Coordinator 可將 #147 從 Draft 轉正式、**SHA-guarded merge**，之後安排下一個 Native readiness/approval gate。若有 NOW，交原 Codex Developer **一次性有界修復＋新 exact-head CI＋Claude 聚焦複審**，避免拆成多個往返。

## 7. 新對話啟動提示（給新 ChatGPT Coordinator）
「接手我的 llmtool 專案，延續上一個 ChatGPT 的 Coordinator 身分。先閱讀本交接報告、Issue #120 及 PR #147，**查 live GitHub** 的最新 comments/HEAD/CI/formal Claude Review，不假設本文件永遠最新。使用固定協作格式：一句結論、版本證據、NOW/LATER、唯一 NEXT_AI 和完整可貼 prompt、我需要做什麼；不要頻繁重做 Gate 或叫我小步搬運。尊重原四個 Session/worktree 和我已授權的 Codex danger-full-access/never；不將 fake PASS 當 Native PASS；公司 LLM 用到才提醒 VPN。現在優先處理 PR #147 的原 Claude formal review，若雙 APPROVE/NOW0/CI PASS 就以 exact SHA 合併，否則安排單一有界修復整包。不要替我改 Full Access 或額外啟動 Native Grant。」

---
此報告由 Coordinator 透過 GitHub 獨立 docs branch 發布；交接並不代替正式 CLI Git/Source/Native grants。

## 8. 固定新增：距離 llmtool 自我開發正式上線還剩多少（Owner 決策 2026-10-15）

**強制 Coordinator 回報規則：每次使用者說 `review repo`，報告最末尾都必須附上「距離 llmtool 上線、自動開發自己還有多少進度沒做」。** 不得只回報 PR/CI 或再次口頭承諾；這是持續使用的格式要求。所有完成狀態必須先依 live GitHub ROADMAP、Issue、PR、CI、獨立 reviewer 與 Native evidence 重新核對，不可沿用舊百分比或將 fake-only 當 Native PASS。

每次末尾固定列出以下八個里程與 DONE / IN_PROGRESS / BLOCKED / NOT_STARTED、證據及下一個 Gate：

| 里程 | 名稱 | 截至 2026-10-15 的參考狀態（每次必重新查） |
| --- | --- | --- |
| A | llmtool 基礎架構及 Provider 能力 | 基礎已具備；production routing 尚未完整驗收 |
| B | Codex/Claude 角色分工、Worktree 隔離 | 有實際平行作業證據；原生無人工接力尚未證明 |
| C | Native Delta A1 source/fake | PR #147 MERGED；非 Native PASS |
| D | Native Composition A1 | Issue #120 契約 v1 已獲非作者 Security/Functional ACCEPT（#6175025949），待 K1 final pins、新 C3、八檔實作/CI/獨立 source review |
| E | 真實跨 CLI Native Pickup | 未授權、未驗證；需要獨立 Gate B、可信 Host/queue/coverage/author receipt |
| F | 自動 task handoff、bounded repair、review、PR-or-STOP | 尚無原 Session 無人工轉貼的完整端到端 acceptance |
| G | 公司 LLM 自動低成本路由與品質/實際 usage 驗收 | Provider 可用能力不得冒稱 production routing；使用時先提醒 Owner 連 VPN，並確保 egress/敏感資料授權 |
| H | llmtool 以自身協作開發 llmtool（dogfooding） | 尚未正式驗收；須以實際自動開發/測試/獨立審查/修復/PR 證據證成 |

報告應同時給出：本輪完成/新增的 Gate、**尚未完成的主要階段數與關鍵 blocker**、可否開始部分 dogfooding、唯一 NEXT_AI + 原 Session + Worktree + 一份完整提示。不要把 8 格平均當可信百分比；只有存在可追溯權重/驗收證據才可報數值完成率。區分兩道目標：(1) llmtool+Codex+Claude 真實自動閉環 MVP；(2) 公司 LLM 參與、品質相當且確實降低 Token/成本的完整 self-development production 目標。不得以 fake-only、GitHub 留言或未驗證的上下文推斷 CLI 自動喚醒。

本規則只改 Coordinator 回報與交接記錄，不改 source、ROADMAP milestone 狀態、Native grant 或 Owner `danger-full-access`/`approval_policy=never`。
