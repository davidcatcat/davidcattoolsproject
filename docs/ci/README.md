# CI Knowledge Base — GitHub Actions 維運知識庫

這是 `davidcattoolsproject` 的 CI **長期查詢入口**，記錄重複遇到的問題、證據、根因、修復、限制與決策。原始事件請保留在 GitHub Issue／PR／Actions；本目錄只沉澱可以重用的結論。

## 文件索引

| 文件 | 什麼情況下閱讀 |
| --- | --- |
| [Troubleshooting](troubleshooting.md) | Actions 被預算限制、重複觸發、驗證失敗或等待 CI 卡住 |
| [Billing and Cost](billing-and-cost.md) | Runner 分鐘計費、預算、節費實測／估算、成本追蹤 |
| [GitHub Actions Guide](github-actions-guide.md) | PR HEAD、Merge Ref、main、Matrix、`wait_ci` 與安全門檻 |
| [Lessons Learned](lessons-learned.md) | Issue #99／PR #100 的決策過程與反面案例 |

## 維護規則（供人及 Claude／Codex／ChatGPT 使用）

1. 處理新 CI 事故前，先在本目錄搜尋相同症狀，再核對**目前** workflow、事件與實際 Actions run；本文件不是當前狀態的替代品。
2. 問題經過驗證且具有**可重用根因或處理原則**時，更新對應舊文件，避免每個小問題都新增一篇。每次更新說明：日期、症狀／影響、根因、修復及如何驗收，附 Issue／PR／run 連結及**確切 SHA**。
3. 明確區分 `OBSERVED`（實際 Runner 或 API 證據）、`ESTIMATE`（推算）、`PENDING`（未驗證）與 `NOT APPLICABLE`；不可將作者自行檢查視為獨立 Review。
4. 不在文件加入 Token、密碼、VPN 主機、原始敏感診斷或私人帳務資料。連結可回溯的安全證據，避免複製過期長日誌。
5. 優先在既定修復完成後，以同一個有審查的文件批次更新；**不要在剛通過 CI 的候選 PR 後面追加純報告 Commit**，否則新 HEAD 須重新驗證。可另開文件專用 PR。
6. 不用文件宣告取代 Security／Functional／Mac 實機驗收；CI 事件濾除不等於測試通過，也不可用「最近一次綠燈」遮蔽相同 SHA 的紅燈。
7. 本目錄是經驗庫，不是另外一份 llmtool Roadmap 或可執行 ai-loop 控制器；技術規範以 [ai-team-delivery](../../skills/ai-team-delivery/SKILL.md)、[safe-git](../../skills/safe-git/SKILL.md) 與當前 Workflow 為準。

自 2026-11-09 起，llmtool Linux 與 Mac CI 改跑 GitHub-hosted runner（Owner [#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253)、[#1256](https://github.com/davidcatcat/davidcattoolsproject/issues/1256)）；詳見 [GitHub-hosted CI 2026-11-09](github-actions-guide.md#github-hosted-ci-2026-11-09)。下方 #99 案例保留為歷史。

## 第一個已驗證案例

2026-10-25 的 Issue #99 Mac Self-hosted Runner 遷移候選與目前 **NOT_RUN** 的平台覆蓋，見 [GitHub Actions Guide](github-actions-guide.md#issue-99-mac-遷移候選2026-10-25尚未驗收)；該候選尚未構成正式 Mac CI 或安全驗收。

- [Issue #99：CI 節費與設計審查](https://github.com/davidcatcat/davidcattoolsproject/issues/99)
- [PR #100：修復 Push/PR 重複執行與 source HEAD 驗證](https://github.com/davidcatcat/davidcattoolsproject/pull/100)
- 合併至 main：`2cd337e082a3b152d862e16346b9e191a5570d25`（2026-10-11）。
- [合併後 main CI 38082328745](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38082328745)：`success`。
- 成本下降幅度仍需 **合併後至少七天**的 Actions／帳單資料衡量；不要把歷史推估寫成已省下的美元。

## 新案例的最低記錄欄位

`日期｜影響範圍／使用者症狀｜觸發事件與 workflow｜commit SHA／run URL｜根因與反例｜修正 PR｜實際通過的驗收｜仍未驗證的限制｜防止再發生的原則`。
