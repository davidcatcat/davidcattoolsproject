# 保留中的 35 個 worktree 逐一檢查報告（2026-10-28）

- 檢查者：Claude PM 的唯讀代理（PM session `c515bd64`）
- 對照基準：`origin/main` = `6a74b1a`（integ: merge train 15 — #536 #537 (#541)）
- 檢查方式全程唯讀：`git status`、`git diff --stat`、`git diff`、`git log`、`git merge-base --is-ancestor`、`git apply --check`（只在乾淨的 canonical checkout 上檢查能不能套用，不寫入）、`gh pr/issue view`，以及讀取 `.git/llmtool-worktrees/<name>.json` 的 D6 登記。沒有改、提交、stash、reset、刪除或鎖定任何東西，也沒有跑測試。
- 所有 worktree 都在 `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/<名稱>`（已用 `git worktree list` 確認）。

## 一、總結

| 建議 | 數量 | 說明 |
|---|---:|---|
| **SUPERSEDED**（已被取代或已完成，可先把變更存成 patch 再移除） | **29** | 程式都已經由合併的 PR 進到 main；剩下的只是本機產物（`opencode.json`、`.venv`、`.evidence/` 測試紀錄、pip 快取、`.d6-bootstrap-report.json`、已張貼到 Issue 的報告），或是根本沒有變更。 |
| **CONFLICTING**（跟現在 main 的方向衝突，只存檔不要套用） | **3** | 2026-10-23 的 R4「Codex 當 PM」設計草稿（Owner turn 掛 Codex hook、App-server PM 路線）。2026-10-25 起 PM 改由 Claude 擔任，後續流程改走 PM inbox／task status／task advance。 |
| **USEFUL**（main 上沒有，而且仍有關） | **3** | Mac 慢測試加速的小草稿、#63 的測試草稿、#99 的 nlf Mac CI 移轉草稿。三個都需要 Owner 決定要不要開成正式任務。 |
| **UNKNOWN** | **0** | — |

一句話結論：35 個裡有 32 個可以存檔後清掉（29 個已被取代，3 個是過時的設計）。只有 3 個裡面有 main 上還沒有的東西，值得 Owner 看一眼。

## 二、逐一明細（35 列）

說明：
- 「HEAD 在 main」= `git merge-base --is-ancestor HEAD origin/main` 為真。
- 「PR head 含本地 HEAD」= 本地 HEAD 是該 PR 最終 head 的祖先，表示本地 commit 都已經在合併的 PR 裡。
- 大小（MB）取自前一次 cleanup 的清單。

### A. 有未提交變更的 29 個

| # | 名稱（建立日） | D6 狀態 | 原本任務 | 未提交變更 | 是否被取代或衝突（證據） | 建議 |
|---|---|---|---|---|---|---|
| 1 | a11c-appserver-r4-20261023（10-23） | RETAIN_BLOCKED | #120 R4：Codex App-server PM/Developer 路線（lease #120 comment 6363171899） | 改 4 個檔（cli.py、task_store.py、task_submission_cli.py 和它的測試），+1086/−65；另外新增 3 個檔：`codex_app_server.py`（463 行）、`codex_app_server_runtime.py`（669 行）和測試（604 行）。內容：App-server 子程序 client、TaskStore 的 app_server session 狀態、PM intake 與 `task resume` | HEAD `d886d79` 在 main，落後 132 個 commit。三個新檔 main 都沒有。App-server adapter 後來在另一個分支重寫成 **PR #314**（OPEN，最後更新 10-25，標題寫明「runtime gate blocked」），兩者的 `codex_app_server.py` 差了 659 行，是不同版本。main 現在的 PM 路線是 Claude PM 加上直接的 Codex Developer 路線（`ac741e8` #360），並由 PM inbox 推進（`8825a0a` #355、`403b06e` #458）。這份草稿對 cli.py 和 task_store.py 已經套不上（`git apply --check` 失敗）。 | **CONFLICTING** |
| 2 | a11c-darwin-reap-unknown-fixture-20261020（10-20） | RETAIN_BLOCKED | Darwin reap fixture 測試 | 只有未追蹤的 `.evidence/`（completion-report、full-suite.txt 330KB、pr-body、PR 指標） | PR #220 已合併（`c72fb6e`，在 main）。PR head 含本地 HEAD `8f699dc`。 | SUPERSEDED |
| 3 | a11c-g3-result-delivery-20261020（10-20） | RETAIN_BLOCKED | G3 result delivery（A11c Task A） | 只有未追蹤的 `.evidence/`（red/green 測試輸出 4 個檔） | PR #222 已合併（`9213ada`，在 main）。PR head 含本地 HEAD `ac6f46f`。 | SUPERSEDED |
| 4 | a11c-owner-turn-continuation-20261023（10-23） | RETAIN_BLOCKED | #120 R4 前身：Owner turn 的 A→B→A 接續 | 改 3 個檔，+260/−5；新增 `owner_turn_intake.py`（440 行）和它的測試。內容：在 TaskStore 記錄 Owner turn、分類、恢復 | main 沒有 `owner_turn` 的任何符號（`git grep` 0 筆）。#120 comment 6357886338 指出這份和 #5 互相重疊、基底不同；#120 comment 6362967741 記錄 R4 卡在 direct-D6 事件邊界（BLOCKED）。之後這條路改由 App-server（#1）接手，再由 Claude PM 方案取代（AGENTS.md：「Since 2026-10-25 Claude is the Integrator/PM」）。接續功能在 main 是以 PM inbox 實作：`403b06e` #458、`59bd3b5` #454、`2341fae`（task_advance）、`3621aa4`（pm_runtime）。這份草稿套不上 main。 | **CONFLICTING** |
| 5 | a11c-owner-turn-runtime-20261023（10-23） | RETAIN_BLOCKED | 同上（runtime 版，Codex hook 收 Owner turn） | 改 4 個檔，+975/−7；新增 `owner_turn_intake.py`（614 行）、`tools/llmtool/codex_hook.py`、`.codex/hooks.json` 和測試 | 跟 #4 相同。這是「Codex prompt hook 收 Owner 訊息」的設計，前提是 Codex 當 PM，和現在 Claude 當 PM 的角色規則衝突。另外，記憶中記載 Owner 的個人 Codex TUI 不得加 PM 規則。套不上 main。 | **CONFLICTING** |
| 6 | a35-chat-usage-status-20261020（10-20） | RETAIN_BLOCKED | A3.5 UX2：chat `/status` 顯示 token 用量（公司 gw 試做） | chat.py 和 test_chat.py，+58/−1 | registry 處置記載公司 gw 試做「repair exhausted… no new PR, no source adoption」。同一天 main 已經由 `6afba7a`（#218）加入 `last_usage` 和用量顯示，現在的 chat.py 有 `validate_provider_usage`／`last_usage`。草稿套不上 main。 | SUPERSEDED |
| 7 | a35-ux12-company-20261020（10-20） | RETAIN_BLOCKED | A3.5 UX1/2：chat TUI（公司 gw 試做） | 改 5 個檔，+14/−1；新增 `chat_tui.py`（222 行）和測試 | registry 處置記載「6/6 calls 2/2 repairs exhausted… regression, no source commit/PR」。main 由 `6afba7a`（#218）加入 `chat_tui.py`，現在有 1134 行，另外還有 `chat_tui_events/status/task.py`，`--tui` 也已在 cli.py。 | SUPERSEDED |
| 8 | ci-mac-slow-tests-20261027（10-27） | **ACTIVE**（沒有 writer） | #425 Mac 慢測試加速（Codex） | `test_action_contract_cli.py` +8/−8：兩處測試從 `self.launch()`（開子程序）改成 `self.invoke()`（同一程序內執行）來加速 | main 的這兩處仍然是 `self.launch(args)`；`invoke(args, tripwires=…)` 在 main 已經存在（第 73 行），而且草稿可以乾淨套上 main。#289 comment 6441807296 記載 Codex 回報 BLOCKED（sandbox 時間預算不夠），之後改派給 AGY（Task `d3b1e153…`，PR #473 只登記了派工）。在 #120、#289、#425 都找不到 AGY 的結果，main 也沒有對應的加速 commit。 | **USEFUL**（未完成的部分草稿） |
| 9 | issue176-r05-current-main-20261021（10-21） | RETAIN_BLOCKED | #176 R05 在 current main 上驗證 | 只有未追蹤的 `opencode.json`（任務本機 OpenCode 設定，未讀內容） | #176 CLOSED。處置記載「114/114 targeted tests passed… no tracked changes」。HEAD `fb2ff02` 在 main。 | SUPERSEDED |
| 10 | issue180-doctor-damaged-metadata-20261021（10-21） | RETAIN_BLOCKED | #180／#242 doctor 回報損壞的 metadata | 只有 `opencode.json` | PR #246 已合併（`8df013e`，在 main），PR head = 本地 HEAD `62573b9`。#180、#242 都 CLOSED。 | SUPERSEDED |
| 11 | issue209-monitor-first-successful-scan-20261021（10-21） | RETAIN_BLOCKED | #209 monitor 首次成功掃描的測試 | `opencode.json` 和根目錄 `.venv/` | PR #254 已合併（`f49fd22`，在 main），PR head = 本地 HEAD `9ae817f`。#209 CLOSED。 | SUPERSEDED |
| 12 | issue209-monitor-l2-baseline-20261021（10-21） | RETAIN_BLOCKED | #209 monitor L2 baseline | 只有 `opencode.json` | PR #250 已合併（`fb2ff02`，在 main），PR head = 本地 HEAD `95d2766`。 | SUPERSEDED |
| 13 | issue243-review-usage-budget-20261021（10-21） | RETAIN_BLOCKED | #243 review 用量預算 | 只有 `opencode.json` | PR #247 已合併（`afb1e1a`，在 main），PR head = 本地 HEAD `abf3788`。#243 CLOSED。 | SUPERSEDED |
| 14 | issue243-review-usage-telemetry-20261021（10-21） | INTEGRATED | #243 修正版：review 不被 telemetry 否決 | 只有 `opencode.json` | PR #248 已合併（`5dffad2`，在 main），PR head = 本地 HEAD `0c83e40`。 | SUPERSEDED |
| 15 | issue267-env0-measure-20261023（10-23） | ACTIVE（量測任務，沒有 writer） | #267 ENV-0 舊式 bootstrap 計時 | 只有未追蹤的 `tools/llmtool/.env0-pip-cache/`（2.4MB pip 快取） | 結果已張貼到 #267 comment 6346821240。後續 ENV-1/ENV-2 已合併（`2e6c5dc` #273、`3e2d046` #284）。 | SUPERSEDED |
| 16 | issue267-env2-final-1-20261023（10-23） | ACTIVE | #267 ENV-2 Mac bootstrap 最終量測（第 1 組） | 只有未追蹤的 `.d6-bootstrap-report.json`（自動產生） | 本地多出的 8 個 commit（`1ee2f21`…`986fba2`）都在 **PR #284** 的 commit 清單裡，已 squash 合併成 `3e2d046`（在 main）。 | SUPERSEDED |
| 17 | issue267-env2-final-2-20261023（10-23） | ACTIVE | 同上（第 2 組） | 同上 | 同上（PR #284 → `3e2d046`） | SUPERSEDED |
| 18 | issue267-env2-final-3-20261023（10-23） | ACTIVE | 同上（第 3 組） | 同上 | 同上 | SUPERSEDED |
| 19 | issue267-env2-mac-1-20261023（10-23） | REPAIR | ENV-2 Mac 量測（較早的 head `61be5de`） | 只有 `.d6-bootstrap-report.json` | `61be5de` 在 PR #284 的 commit 清單裡，已合併成 `3e2d046`。 | SUPERSEDED |
| 20 | issue267-env2-mac-2-20261023（10-23） | REPAIR | 同上（head `ac410bf`） | 同上 | `ac410bf` 在 PR #284 裡，已合併成 `3e2d046`。 | SUPERSEDED |
| 21 | issue267-env2-mac-3-20261023（10-23） | REPAIR | 同上 | 同上 | 同上 | SUPERSEDED |
| 22 | issue267-env2-mac-4-20261023（10-23） | REPAIR | 同上 | 同上 | 同上 | SUPERSEDED |
| 23 | issue267-env3-main-final-v2-wt1-20261024（10-24） | ACTIVE | #267 ENV-3 current-main Mac 驗收 | 未追蹤的 `docs/reports/a11c_env3_final_acceptance_20261024.md`（3.3KB） | 這份報告已去識別化、完整張貼到 #267（2026-10-23T18:58:13Z「Full sanitized ENV-3 current-main report」）。Claude 獨立審查 PASS：#267 comment 6372304886。Roadmap 更新已合併（PR #306，#267 comment 6375426281）。 | SUPERSEDED |
| 24 | issue267-env3-timeout-main-20261024（10-24） | ACTIVE | #267 歷史 REVIEW_TIMEOUT 在 current main 的單測檢查 | 未追蹤的 `docs/reports/a11c_env3_timeout_regression_main_20261024.md` | 報告已完整張貼到 #267（18:58:15Z「Full sanitized timeout regression report」），審查 PASS 在 18:56:38Z。根因仍是 UNKNOWN（#267 2026-10-23T20:37:10Z），但這個 worktree 裡沒有額外資訊。 | SUPERSEDED |
| 25 | issue267-env3-timeout-regression-20261024（10-24） | RETAIN_BLOCKED | 同上（較早的 base `79d0b44`） | 未追蹤的 `docs/reports/a11c_env3_timeout_regression_20261024.md` | 結果已張貼到 #267 comment 6371400209。 | SUPERSEDED |
| 26 | issue63-basic-benign-20261021（10-21） | RETAIN_BLOCKED | #63 C1b：某些普通英文句子被誤判成憑證（實際字句見 #63） | `test_test_output_excerpts.py` +65/−2：新增一個測試，並把 `benign-basic` 的預期從 REJECTED 改成 COMPLETE。只改測試，沒有改產品程式。另有 `opencode.json` | #63 仍然 **OPEN**。main 上同一行仍然預期 `REJECTED`（草稿可以乾淨套上，表示那段沒被改過），也就是說 main 還沒修這個誤判。處置記載 OpenCode 因隱私問題中止（工具輸出含本機路徑）。注意：只套這份測試草稿而不改產品程式，測試會失敗。 | **USEFUL**（可當 #63 的規格或測試參考） |
| 27 | issue99-nlf-mac-ci-20261025（10-25） | ACTIVE | #99 把 nlf CI 移到 Mac self-hosted runner | 已 staged：`nlf-ci.yml` 改成退役版（−383 行）、新增 `nlf-mac-ci.yml`（224 行）、改 `tools/nlf/README.md`；另有 1 行 unstaged 修正（awk 過濾 skip）。可以乾淨套上 main | main 的 `nlf-ci.yml` 仍是舊的 GitHub-hosted 版本，但 GitHub 上 `nlf CI` 工作流程狀態是 **disabled_manually**（`gh api …/actions/workflows`；#99 comment 6388155535 記載因成本停用）。main 沒有 `nlf-mac-ci.yml`，所以 nlf 目前**沒有任何 CI**。#99 OPEN。之後 #99 的工作轉向 llmtool 的 Mac/EC2 CI（`a8f4081` #335、`35a4b27` #346），nlf 這部分沒人接手。 | **USEFUL**（要 Owner 決定 nlf 還要不要 CI） |
| 28 | p0c-a-dedup-20261027（10-27） | ACTIVE | #425 P0c 對照實驗 A：remote-test 只重用 PASS/SHA_MISMATCH 的收據 | `remote_test.py` 加一個條件，測試加一個案例（+10/−1） | 同樣的變更已由 **`35d5bb4`（#435）** 合併，main 的 `remote_test.py` 第 416 行就是 `known.get('result') in ('PASS', 'SHA_MISMATCH')`。草稿現在套不上。 | SUPERSEDED |
| 29 | p0c-a-digest-20261027（10-27） | ACTIVE | #425 P0c 對照實驗 A：收據 digest 改用 TaskStore 的 `_json_bytes` | `remote_test.py`、測試（+13/−3） | 同樣的變更已由 **`cad2993`（#436）** 合併，main 第 446 行就是 `hashlib.sha256(_json_bytes(receipt))`。 | SUPERSEDED |

### B. 6 個 RETAIN_BLOCKED（工作樹是乾淨的）

| # | 名稱（建立日） | 原本任務 | 未提交變更 | 是否被取代（證據） | 建議 |
|---|---|---|---|---|---|
| 30 | a11c-d4-live-smoke-20261020（10-20） | A11c D4 OpenCode 實機 smoke（一次性授權） | 無 | HEAD `b4b035c` 在 main。處置記載收據為 `UNKNOWN / OPENCODE_RUNTIME_INVALID`，「do not replay」，`retention_paths` 是空的。現在的 AGENTS.md 寫明 OpenCode「not gated on D4/G3/M2」，D4 已經不是門檻。 | SUPERSEDED（見第三節第 4 點） |
| 31 | a11c-d4-live-smoke-20261021（10-21） | 同上，第二次 | 無 | HEAD `3f5dae9` 在 main。結果為 `OPENCODE_AUTH_REQUIRED`，已由 PR #238 診斷。D4 不再是門檻（同上）。 | SUPERSEDED（見第三節第 4 點） |
| 32 | a11c-g3-result-receipt-20261020（10-20） | G3 OpenCode result receipt | 無（只有 `__pycache__`） | PR #217 已合併（`f64d0f3`，在 main），PR head 含本地 HEAD `2dcd4ee`。 | SUPERSEDED |
| 33 | a11c-r1-production-reviewer-runtime-20261023（10-23） | #120 R1 正式 Reviewer runtime | 無 | PR #297 已合併（`57260bc`，在 main），PR head = 本地 HEAD `05eaf0c`。後續 Reviewer 派工由 `771190e`（#440 `task review-dispatch`）完成。 | SUPERSEDED |
| 34 | issue267-env3-main-final-wt1-20261024（10-24） | ENV-3 驗收第一版 wt1 | 無 | HEAD `79d0b44` 在 main。處置原文：「pinned to the superseded 79d0 base. Do not reuse」，已被 v2（#23）取代。 | SUPERSEDED |
| 35 | issue267-env3-main-final-wt2-20261024（10-24） | ENV-3 驗收第一版 wt2 | 無 | 同上 | SUPERSEDED |

## 三、請 Owner 特別看的項目

1. **#27 issue99-nlf-mac-ci：nlf 現在完全沒有 CI。** GitHub 上 `nlf CI` 已在 10-25 因成本手動停用，main 裡卻仍保留舊的 GitHub-hosted 設定。這個 worktree 有一份完整的 Mac self-hosted 版本（224 行）加上退役舊流程的修改，可以乾淨套上 main。需要 Owner 選一個：
   - (a) 交給 PM 開成正式任務，走 Codex 審查和 Mac runner 安全檢查；
   - (b) 不需要 nlf CI，存成 patch 後移除；
   - (c) 先保留。
   這會碰到 self-hosted runner 的安全範圍，所以不能由 PM 自行決定。
2. **#8 ci-mac-slow-tests：Mac 慢測試加速的任務看起來已經斷線。** Codex 回報 BLOCKED 後改派給 AGY（#289 comment 6441807296、PR #473），但查不到 AGY 的結果，main 也沒有這項加速。worktree 裡只有 8 行的小草稿，可以乾淨套上 main。D6 狀態還寫著 ACTIVE。請確認這個任務是要重新派工，還是結案。
3. **#26 issue63-basic-benign：#63 仍然 OPEN。** 草稿只有測試，而且把預期結果改成「普通文字不應該被擋」，main 上還沒有對應的產品修正。建議存成 patch 附到 #63 當規格參考，再移除 worktree，不建議直接套用。
4. **#30、#31 D4 live smoke：** worktree 本身沒有任何內容，但處置寫著「preserve private evidence and receipt」，而 `retention_paths` 是空的，表示證據應該在 worktree 外面（例如 llmtool state 目錄），這次沒有去確認。移除 worktree 不會動到那些證據，但如果 Owner 要連證據一起處理，需要另外確認存放位置。
5. **#1、#4、#5（CONFLICTING）：** 這三個加起來約 2000 行未提交的程式，是 10-23 Codex 當 PM 時期的 R4 設計。建議存成 patch 放進封存後移除，不要再套用。相關的 **PR #314**（App-server adapter，OPEN，從 10-25 起沒有更新）仍然掛著，Owner 可以順便決定要關掉還是保留。
6. **D6 登記狀態跟實際不符：** 有 14 個 worktree 的 registry 還是 `ACTIVE` 或 `REPAIR`（#8、#15–#24、#27–#29），但實際上已經沒有 writer，任務也已經結束或被取代。如果要用 `d6_worktree_lifecycle.py` 退役，可能要先把狀態改對；這需要 PM 依照 #391 的退役流程處理。這次是唯讀檢查，沒有改任何狀態。
7. **只有本機產物的 worktree**（`opencode.json`、`.venv`、`.evidence/`、pip 快取、bootstrap report 或報告檔，共 19 個：#2、#3、#9–#25）：程式都已經在 main 上。`opencode.json` 是任務本機的 OpenCode 設定，這次沒有讀內容。`.evidence/` 是當時的測試輸出（例如 #2 的 full-suite.txt 330KB）。如果要保留審計紀錄，建議移除前先打包到封存目錄。

## 四、限制

- 沒有跑任何測試，所以「USEFUL」只代表 main 上沒有、而且主題仍然有關，不代表草稿正確。
- 「AGY 沒有結果」是根據 #120、#289、#425 從 2026-10-27 起的留言搜尋，以及 main 的程式碼內容判斷；如果結果記錄在其他地方，這次沒有查到。
- `opencode.json` 和 `.codex/hooks.json` 沒有讀內容（D6 處置要求不讀設定值）。
