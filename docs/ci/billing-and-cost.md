# Actions Billing & Cost — 計費及節費決策

## 2026-11-09 Public repository 與 GitHub-hosted CI

本 repository 已是 Public；依 Owner [#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253) 與 [#1256](https://github.com/davidcatcat/davidcattoolsproject/issues/1256)，llmtool Linux 與 Mac CI 重新在 GitHub-hosted Runner 執行（見 [GitHub Actions Guide](github-actions-guide.md#github-hosted-ci-2026-11-09)）。下方 2026-10-25 章節是歷史紀錄。實際費用在核對 GitHub Billing 資料前記為 **UNKNOWN**。

## 2026-10-25 Owner 決策與止費狀態

Private repository 保持不變，付費 GitHub-hosted Runner 不再使用。`llmtool CI`、`nlf CI` 與 `ai-loop helper CI` 的 GitHub workflow 狀態已設為 `disabled_manually`；最後已啟動的 [llmtool run 40113971497](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/40113971497) 已取消。新的 Mac self-hosted workflow 在 Runner 隔離及正式審查前不執行 PR 程式碼。Linux Python 3.9／3.14、Windows 與停用的 nlf／ai-loop 正式 CI 均為 **NOT_RUN**。止費後的「沒有新付費執行」及實際 USD 仍須用 GitHub Runs/Jobs 與 Usage/Billing 逐筆確認；目前 USD 為 **UNKNOWN**，不能從 workflow disabled 推算為零費用。

以下節費推算與保留 Matrix 的討論是 2026-10-11 的歷史決策；2026-10-25 Owner 決策已取代當時繼續使用 GitHub-hosted Matrix 的前提。

> 本頁區分 Runner 用量、分鐘取整的**工程估算**與 GitHub 帳單的實際 USD 費用。以 GitHub Billing 頁面、當時的 SKU 價格和帳期為準。

## 2026-10 事件背景

- GitHub Free private repository 曾遇到 Actions 用量／預算限制；使用者在 Issue #99 的決策背景中配置額外 **US$10／月 Actions budget（Stop usage）**。這是當時的設定紀錄，不保證日後仍相同。
- [Issue #99](https://github.com/davidcatcat/davidcattoolsproject/issues/99) 中 Claude 對 **2026-09-27 至 10-10** 的歷史匯出報告：`749 runs`、`1,339 jobs`、約 `2,461 Linux-equivalent minutes`；該數量為**審查者統計／推算，不是官方 invoice CSV**。
- 同一匯出報告有 `99` 個 llmtool SHA 同時觸發 Push 與 PR，約 `300` Linux-equivalent 分鐘重複；另有 `161` 次非 main branch Push（約 `490` 分鐘），其中一部分沒有後續 PR，所以不能全部認定是無用 CI。
- 修正後的預估區間（引用 Codex [PR #100 Review](https://github.com/davidcatcat/davidcattoolsproject/pull/100#issuecomment-6101637492)）：**llmtool 約 24–34%、全 Repo Actions 約 14–20%** 的歷史用量潛在下降。這是有前提的 `ESTIMATE`，**尚非已實現節費**。

## Job 分鐘取整：少一個 Job 未必更便宜

GitHub-hosted Actions 的用量需依每個 Job 和適用的計費規則衡量；完整驗證應以 Billing 的實際 SKU／USD 資料為準。以下是實際 Job elapsed 做的**向上取整模型**：

| 實際觀察的樣本 | Runner Job elapsed | 模型計費分鐘 | Run 整體時間 |
| --- | --- | ---: | ---: |
| 舊 3.9 + 3.14 Matrix，[run 38070628943](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38070628943) | 約 106s + 161s | `2 + 3 = 5` | 約 165s |
| 實驗性單 Job，[run 38078689450](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38078689450) | 約 317s | `6` | 約 319s |
| 最終雙 Job，[run 38081677441](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38081677441) | 約 153s + 118s | `3 + 2 = 5` | 約 157s |

**結論：** 單 Job 在這幾筆樣本裡**沒有證明節省分鐘**，卻讓 Python 兩版本共用 `HOME`、`/tmp`、部分環境變數／動態函式庫搜尋路徑，並使 Workflow 變複雜。選擇保留 Matrix；不要因 14 秒的 ai-loop Job 理論上合併有利，就照搬到 llmtool 的 130 秒測試。

GitHub 官方計費參考：[Actions runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing)。

## 節費時必須保留的門檻

- main Push、PR source HEAD 與事前 Base 相容性、每版本完整 canonical tests／安全負面驗證、必要 native Mac 等候，以及原本的 OS/platform 測試，不得為省錢隨意省略。
- 對功能等價但費用不確定的最佳化：**先算原始 job 時間、再小範圍驗證、最後用真實數據決定保留與否**。
- 獨立計費高的 macOS／Windows runners 不應只因歷史估算就刪除。`nlf` 沒有在 2026-09-27～10-10 樣本中觀察到相同 SHA 的 push+PR 重複，因此 PR #100 **沒有修改 nlf**。
- `ai-loop` 的正式控制分支 Push CI 是 executable-tip promotion 所需證據，不能將它當作可刪除的「重複 CI」。相關優化要走獨立的安全／Mac 驗收流程。

## 合併後七天量測表（尚待完成）

基準合併：`2026-10-11`，main SHA `2cd337e082a3b152d862e16346b9e191a5570d25`。合併後 [main CI 38082328745](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/38082328745) 已 `success`。

請在**至少完整七天**後按同口徑補齊（避免只挑低使用量的日期）：

| 指標 | Before：2026-09-27～10-10 | After：連續七天 | 狀態 |
| --- | --- | --- | --- |
| Run／Job 數 | 749／1,339（全 repo，14 天） | PENDING | 區間長度不同，須先按每日標準化 |
| llmtool 非 main Push 和 PR 重複率 | 99 個重複 SHA（14 天） | PENDING | 依事件與 SHA 分組 |
| OS SKU 分鐘、取消、rerun 次數 | 2,461 Linux-equiv 分鐘（14 天，估算） | PENDING | 應用同口徑＋實際 Billing SKU |
| 實際 USD／included credit／budget 額度 | UNKNOWN（需帳單） | PENDING | **不可將估算當已收費／省下的金額** |
| 每版測試覆蓋／main 和 PR 必要驗收 | PR #100 新 HEAD 兩版 PASS | PENDING | 持續保留門檻 |

核對資料來源：GitHub Actions Runs／Jobs API、run events 與精確 SHA、Billing dashboard／Usage report。保留分析日期與資料來源；如事件量不同，拆分**減少重複執行**與**測試需求下降**的影響。

**變更時點以 PR #100 為界，不要在未重新量測之前宣稱達成特定百分比或節省美元。**
