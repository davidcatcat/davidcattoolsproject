# CI Troubleshooting — 故障排查手冊

從**當前事件、Head SHA、run/job/step**開始定位，不只看 PR 顯示的綠色圖示。典型查詢：

```bash
gh run list --workflow llmtool-ci.yml --limit 15 --json databaseId,event,headSha,status,conclusion,url
gh run list --workflow llmtool-ci.yml --commit <FULL_SHA> --limit 10 --json databaseId,event,headSha,status,conclusion,url
gh run view <RUN_ID> --log-failed
```

上述命令僅供讀取；需要 GitHub CLI 已登入並具備 Repo 存取權。**不要因為診斷就大量重新觸發收費 CI。**

## 1. Workflow 無法啟動／Actions 用量被阻擋

**症狀：** GitHub Actions 因帳務／spending limit 無法派工、Job 沒開始，或使用量異常接近上限。

**根因可能是：** 計費額度／預算 Stop usage、帳務限制、沒有相符 trigger 或錯誤的路徑過濾。這些必須分別查證；「沒有 CI」並不等於 PASS。

**處理：** 查 Billing → Actions 使用量及 Budgets、最近 run 的排程狀態、實際觸發事件和 workflow `on` 條件。預算調整**不等於**增加測試覆蓋；不得默默加價或為了避開限額移除必要測試。Issue #99 記錄了 2026-10 的節費判斷與預算背景。

**防再發生：** 先減少真正重複的執行、不要在失敗迴圈連續 Push；保留 main／PR 的必要驗證。

## 2. 一次修改卻看到 Push + PR 兩套 CI

**症狀：** 同一 feature SHA 同時出現 `push` 與 `pull_request` 的 `llmtool CI`。

**曾經原因：** 舊 `llmtool-ci.yml` 同時監聽所有 branch 的 Push 與 PR，兩種 event 都會跑 3.9／3.14。

**解法（PR #100，已合併）：** `push` 限 `main`，保留 `pull_request` 與原有 path filters；必要時用無注入參數的 `workflow_dispatch` 做明確手動驗證。Feature branch **尚未開 PR 時不會自動跑 llmtool CI**，必須盡快開 Draft PR 或使用被授權的手動執行。不能因省錢而認定未測過的 HEAD 合格。

**驗收：** 比較同一 SHA 的 run events；正常 feature PR 改版只有 PR run，合併後 main 仍有 Push run。[PR #100 CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38081677441)。

## 3. CI 綠了，但不確定測試的原始碼版本

**症狀：** PR 頁面顯示成功，但在實際 checkout 中 `GITHUB_SHA` 可能是 GitHub 暫時的 Merge Ref，不是 PR 原始 Head。

**解法：** PR 以 `github.event.pull_request.head.sha` Checkout，並在測試中比對 `git rev-parse HEAD`。若 PR Head 未包含事件發生時的 Base SHA，應 FAIL，不是只有警告。

**注意：** 事件 Base SHA 是一個時間點的快照；合併前仍要取得**當下 live main**，驗證其為已審查 HEAD 的祖先，且 merge 綁定審查 SHA。詳見 [GitHub Actions Guide](github-actions-guide.md)。

## 4. `wait_ci` 找不到 PASS／出現同 SHA 紅綠並存

**症狀：** 成功的 PR run 存在，但 ai-loop 仍回報 `CIFailure`，或一直等待不到 run。

**曾經原因：** 2026-10 的實驗在**正式候選 SHA** 上用 `workflow_dispatch` 故意讓 Python 3.9 FAIL。控制器會查同一 SHA、同一 workflow 的匹配 runs；其中一個失敗，整體就拒絕。這是正確的 fail-closed 行為。

**處理：** 查 `gh run list --commit <SHA>` 的**所有匹配紀錄**。不得刪紅 run、只挑最後一次綠燈、洗掉舊紀錄或更改 controller 來假裝成功。PR #100 已移除常駐 `inject_failure`，並以真正的修復 Commit 取得新的 SHA，正常 CI 和 `wait_ci` 都通過。故障注入只能放在獨立、明確標記的非待合併測試 SHA；額外 run 亦須有費用與權限理由。

相關：舊 [負向 run 38079142443](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38079142443)、修正版 [正常 run 38081677441](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38081677441)。

## 5. Linux PASS，但 macOS 驗收仍有 Skips

2026-10 PR #100 的 Linux 3.9、3.14 每版 `1,391 tests OK (skipped=27)`；當時獨立 Review 分類為 **26 個 native Mac 必要測試 + 1 個檔案系統大小寫／別名類測試**。這是原本既有的 skip，**不是** 27 項 Mac PASS。

**處理：** 明確標示 `Linux PASS / native Mac PENDING`，只有規格要求 native Mac 的路徑在 Mac 上真正通過，才可關閉相應 release gate。切勿將模擬、Linux 或 Cloud 通過冒充 Mac 驗收。

## 6. 排查完成前的 STOP 規則

當缺少必需 CI、SHA 不一致、主分支已變動、存在未分類的產品安全失敗或無權限／預算時，保留原始證據，停止受影響的合併／發布，不更改門檻來取得綠燈。對應程序見 [safe-git](../../skills/safe-git/SKILL.md)。
