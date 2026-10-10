# GitHub Actions Guide — 本 Repo 的 CI 行為契約

## 哪些 Workflow 是哪個範圍？

| Workflow | 使用情境 | 不能隨意更動的證據 |
| --- | --- | --- |
| [`llmtool-ci.yml`](../../.github/workflows/llmtool-ci.yml) | llmtool feature PR 與 main Push；手動 `workflow_dispatch` 作補救 | Python 3.9 + 3.14 各自完整測試，來源 HEAD／Base 檢查 |
| [`nlf-ci.yml`](../../.github/workflows/nlf-ci.yml) | nlf Go 測試與平台驗證 | 既有 Linux／macOS／Windows／race／fuzz 等驗證 |
| ai-loop helper CI（在 `automation/ai-loop` 控制分支） | 候選控制器 PR、正式可執行控制分支的 Push | Immutable candidate、獨立 Security/QA、Mac acceptance，promotion Push 不可隨意去除 |

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
