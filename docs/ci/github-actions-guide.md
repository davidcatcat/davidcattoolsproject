# GitHub Actions Guide — 本 Repo 的 CI 行為契約

## Issue #99 Mac 遷移候選（2026-10-25，尚未驗收）

Owner 決定保持 Private repository、不再使用付費 GitHub-hosted Runner。正式舊 workflow `llmtool CI`、`nlf CI`、控制分支 `ai-loop helper CI` 已在 GitHub 設為 `disabled_manually`；[最後一筆 llmtool Ubuntu run 40113971497](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/40113971497) 已取消。這是止費狀態，並非新 CI PASS。Mac smoke [40113489740](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/40113489740) 只證明 Runner 連線與 Python 3.13.3 可啟動，未證明安全隔離。

本候選新增 `llmtool-mac-ci.yml`，刪除舊 `llmtool-ci.yml` 檔案；舊 workflow ID 必須保持 disabled，絕不可為了測試新候選而重啟。新 job `check (macOS 3.13)` 指定 `[self-hosted, macOS, X64, local-mac-ci]`，預設還受 repository variable `MAC_CI_ENABLED == 'true'` 阻擋。即使有人先啟用新 workflow，未設變數時 skipped job 是 **NOT_RUN，不是 PASS**，task completion 亦不接受。僅在 Runner 專用非管理員帳號、個人憑證／SSH／VPN／其他 Agent worktree 隔離，以及正式 Security 審查通過後，PM 才可啟用新 workflow 和該變數並驗證 PR source HEAD。原 Runner 已下線；它以個人帳號執行、可能共用 VPN 的問題仍未解除，不可用來跑 PR 程式碼。

| 驗證範圍 | 遷移候選狀態 | 驗收解讀 |
| --- | --- | --- |
| macOS X64，Python 3.13 | 本機靜態／步驟測試中；GitHub PR CI **NOT_RUN** | 只有新 workflow 在已隔離 Runner 上對確切 PR HEAD 成功，才可視為 PASS |
| Linux Python 3.9／3.14 | **NOT_RUN** | 不繼承舊 workflow 的歷史 PASS |
| Windows CI | **NOT_RUN** | 不繼承舊 nlf Job 的歷史 PASS |
| nlf Linux／macOS／Windows、ai-loop 控制分支 | 已停用，**NOT_RUN** | 後續須分別審查；不可影響 WP3 的正在執行工作 |

新 llmtool job 保留原 PR source SHA／事件 Base 祖先驗證、12 個安全 verifier 負向測試、完整 `make -C tools/llmtool check`、monitor 測試及 clean-tree gate。`task_completion.py` 只接受新 workflow 的確切 PR HEAD、GitHub Actions app／check-run ID、`davidproject-self-action` runner 名稱與單一 Mac job 成功；Linux／Windows 以 `NOT_RUN` 記錄。Private repository 的 Rulesets／Branch Protection／Required Checks API 目前回傳方案限制 403，因此 PM／Integrator 必須以程式內的 fail-closed gate、獨立 Claude Reviewer、實機 Mac CI 與確切 SHA 人工核對阻擋不合格合併，不可把 API 不可用說成保護規則已配置。

回滾時先停用新 workflow 並將 `MAC_CI_ENABLED` 取消或設為 false，保留舊 workflow disabled；修復候選經新 PR／審查／Mac 驗收後再啟用。回滾不得恢復 Ubuntu、Windows 或付費 macOS Job。切換後逐筆檢查所有 active workflow、每個新 run 的 runner 名稱與 GitHub Usage／Billing；帳單 USD 在取得官方資料前記為 **UNKNOWN**。

以下章節保留 PR #100 當時的歷史契約，不能當作 2026-10-25 遷移完成證據。

## 哪些 Workflow 是哪個範圍？

| Workflow | 使用情境 | 不能隨意更動的證據 |
| --- | --- | --- |
| [`llmtool-mac-ci.yml`](../../.github/workflows/llmtool-mac-ci.yml) | 遷移候選：llmtool feature PR 與 main Push；手動 `workflow_dispatch` 作補救 | Mac Python 3.13、來源 HEAD／Base 檢查；正式 CI 尚 NOT_RUN |
| [`nlf-ci.yml`](../../.github/workflows/nlf-ci.yml) | 歷史 nlf Go 測試與平台驗證；目前 disabled | Linux／macOS／Windows／race／fuzz 均 NOT_RUN |
| ai-loop helper CI（在 `automation/ai-loop` 控制分支） | 歷史控制器 PR／Push CI；目前 disabled | WP3 的既有驗收不得沿用舊綠燈，須另行處理 |

本文件以 **PR #100 合併後 main** 為基準（`2cd337e082a3b152d862e16346b9e191a5570d25`）。對後續版本應以該 SHA 的實際 YAML 和最新要求為準，不能只引用這份摘要。

## llmtool 的 Event 真相

- `push`：只跑 `main`，範圍限 `tools/llmtool/**` 和該 CI workflow；不再對 feature branch Push 自動跑產品 CI。
- `pull_request`：沿用原本 path filters。開 Draft PR 後取得候選 SHA 的 CI。
- `workflow_dispatch`：人工觸發，**正式版本無 `inject_failure` 輸入**；不可拿來讓本應失敗的 Release SHA 變成 PASS。
- `concurrency`：以 workflow、event 和 ref 分組；新的 PR 更新可取消舊 PR run，不應取消 main／dispatch 的不同事件。
- Python Matrix：`fail-fast: false`、`3.9` 和 `3.14` 各自獨立 VM，Job timeout 各 15 分鐘。既有 Job-owned runtime 完整性檢查、12 項 verifier 負向測試、`make check` 和 clean-tree 都維持。

## PR source HEAD、Merge Ref 與事件 Base

在 `pull_request` 事件中，GitHub 的 `GITHUB_SHA` 一般指向自動產生的 merge-ref；測試「原始 PR HEAD」需要顯式使用 PR 的完整 head SHA。

PR #100 的 `Source revision` gate：
1. 要求 expected SHA 是完整 40 位 hex，且 `git rev-parse HEAD` 完全相同。
2. 在 PR 事件要求 HEAD 不等於 `GITHUB_SHA`。
3. 用 `git merge-base --is-ancestor BASE_SHA HEAD` 驗證 PR Head 包含事件當下的 Base；非祖先或 Git 工具錯誤均使 CI FAIL。
4. 其他事件使用事件 commit SHA。為可查祖先關係，Checkout 使用 `fetch-depth: 0`。

**未覆蓋的時間競爭：** 主分支可以在 PR CI 完成後推進。因此在合併前仍須**重新讀取遠端 main**，驗證 live main 是已審查 HEAD 的祖先，檢查原 PR CI 和 review 都綁同一 HEAD，並以帶 expected HEAD 的受控合併操作避免 SHA 變動。GitHub CLI 可用 `gh pr merge <PR> --squash --match-head-commit <REVIEWED_HEAD>`；這只鎖 PR Head，**不原子鎖住 Base**。若 main 漂移，停止、更新候選並重跑必要 CI 和 Review。

合併後 main 的 Push CI 是「合併結果」確認，不能取代合併前相容性門檻。2026-10 的 [PR CI run 38081677441](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38081677441) 與 [main run 38082328745](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38082328745) 均成功。

## ai-loop 的 `wait_ci` 以及 fail-closed

控制器使用 `gh run list --commit HEAD --workflow llmtool-ci.yml` 取得與 SHA 相符的 runs；**有任何 matching run 失敗，不可因同 SHA 另一次成功就放行**。沒有 run 也不能假裝 PASS（控制器可等待並逾時 STOP）。

2026-10 曾在舊 release 候選 `1caa366d...` 的同 SHA 注入一次失敗，導致 `wait_ci` 正確拒絕；正式 PR #100 修復將人工故障注入移除，改用真實更新的 `0adbc2c...`，由正常 PR run 提供全部必要的成功證據。見 [Troubleshooting](troubleshooting.md)。

控制分支的設計、CI path 對稱及短 Job 分鐘優化，必須另開**非可執行**候選，獨立驗證，絕不在此文件更新時偷偷改變正在執行的 ai-loop 控制器。

## 收尾檢查

`問題的原始 SHA → 新候選的 SHA → PR run／每版本 Job → Review → live Base／Head → Merge SHA → 合併後 main run`，每一步都有真實證據。若有必要的 native Mac、公司 VPN／Provider 或安全檢查，必須另外以其要求的原生環境驗證；不接受 Linux 替代。
