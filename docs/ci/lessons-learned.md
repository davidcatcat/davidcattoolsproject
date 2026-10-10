# Lessons Learned — CI 事故與技術決策

日期：**2026-10-11**。案例：[Issue #99](https://github.com/davidcatcat/davidcattoolsproject/issues/99)／[PR #100](https://github.com/davidcatcat/davidcattoolsproject/pull/100)。

這份記錄區分「當時想法」與「最後實證」。不要把探索性方案寫成已上線或已節費。

## L01 — 先驗證是否重複觸發，再調整 Trigger

- **症狀：** llmtool feature branch 每次 Push 及 PR 更新，都可能各觸發一套 Python CI。
- **證據：** Issue #99 的 14 天歷史分析發現 99 個 SHA 同時有 Push + PR（估計約 300 Linux-equivalent 分鐘重複）；樣本和計費價格有限制。
- **處理：** PR #100 將產品 CI 改成 `push: main` + `pull_request`，保留手動 `workflow_dispatch`。
- **驗收：** PR 最終候選 `0adbc2c...` 有正常 PR run，沒有同 SHA feature Push 重複 run；Merge 後 main Push run 仍成功。
- **教訓：** 省掉的是**重複觸發**，不是刪除必要測試。沒有 PR 的 feature SHA 需要 Draft PR 或明確的手動驗證；不能自行繼承舊 SHA 綠燈。

## L02 — 單 Job 比雙 Job 短，不等於成本更低

- **當時假設：** 將 Python 3.9 和 3.14 兩個 Matrix Job 串成單 Job，利用每 Job 向上取整可能省分鐘。
- **實驗結果：** 舊 Matrix 約 5 模型分鐘／Run；單 Job 約 6，且 Wall time 約 319 秒而非 165 秒。實驗期間觀察到 `LD_LIBRARY_PATH` 跨階段保留了先前 Python 的路徑，不能聲稱完整 VM 隔離。
- **決策：** Codex 獨立審查 `CHANGES_REQUIRED`，Claude 接受證據後恢復雙 Job Matrix；最終樣本亦約 5 分鐘。
- **教訓：** 用真實 Job duration 比較，別把短任務（例如 ai-loop 的十幾秒 Job）的節費模型直接套到長測試。計費是模型估算，非發票。

## L03 — 必須知道 GitHub CI 究竟測了哪個 SHA

- **症狀：** PR event 的 Merge Ref 和 source Head 不同；只看綠燈可能讓錯誤版本被視為原始碼證據。
- **處理：** 顯式 Checkout PR head SHA，檢查實際 Git HEAD；要求 event-time base 為 Head 祖先，否則 Fail。
- **驗收：** 新版 PR CI 的兩個 Job 分別列印並證明 `0adbc2c...`，Base `7c6f955...` 已包含。合併前 Coordinator 另外查 live main 並用 expected Head guard。
- **教訓：** SHA 之外，還要檢查**事件時 Base**和**合併當下 Base**。只用 main Merge 後 CI 無法提前阻擋錯誤整合。

## L04 — 故障注入不能污染待合併的正式 SHA

- **事件：** 舊候選 `1caa366...` 正常 PR run 成功，卻又在相同 SHA 用 `workflow_dispatch` 注入 Python 3.9 故意 FAIL。
- **影響：** `ai_loop.wait_ci` 看到同 SHA 成功與失敗，正確回報 `CIFailure`。這不是控制器錯誤。
- **處理：** 從正式 Workflow 移除常駐 `inject_failure`，保留舊紅 run 作為實驗證據。修復後形成新 HEAD `0adbc2c...`，重新完成 PR CI 和獨立 Review。
- **教訓：** 故障注入使用**非 Release 候選**和單獨的證據；不能刪除、忽略或用大量重跑把真正失敗的紀錄擠掉。

## L05 — Linux Test PASS 不是 Native Mac PASS

- **證據：** PR #100 的 Linux 3.9／3.14 各 `1,391 tests OK (skipped=27)`；獨立 Codex 報告將 skips 分為 26 個 native Mac required，1 個 case-alias filesystem。
- **決策：** 保留原有安全 verifier `12/12`、完整 Python 測試、其他平台與適用 Mac gate；不以節費名義繞過。
- **教訓：** 明確記錄每個平台的 `PASS / PENDING / NOT-TESTED`；沒有跑過的測試不能補寫成 PASS。

## 已完成與待追蹤

- **DONE：** Claude 修復、Codex 對新 HEAD 獨立 Review `APPROVE`、PR #100 合併 main `2cd337e...`，新 [main CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38082328745) `SUCCESS`。
- **PENDING：** 按至少七天、同口徑的 Billing 和 Runs/Jobs/OS 資料衡量實際節費；節費百分比目前只可稱 `ESTIMATE`。
- **另案：** ai-loop helper CI 的短 Job／控制分支路徑對稱，遵循獨立控制器發行與 Mac 安全驗收，**不是** PR #100 的延伸程式修改。
