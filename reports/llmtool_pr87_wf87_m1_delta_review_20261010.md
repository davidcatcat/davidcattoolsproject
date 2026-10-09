# PR #87 WF87-M1 repair (`1034169`) — Claude delta review and repo status (2026-10-10)

> 審查者：Claude Code（Opus 5.5），在 Mac canonical repo 上
> **獨立性聲明：** PR #87 的前兩個 commit（`f90d367`、`ba5cdb7`）是 Claude Code 寫的，所以本報告**不是**整個 PR #87 的獨立 workflow Security review。本報告只審查 Codex 撰寫的修正差異 `ba5cdb7..1034169`，作為獨立 reviewer 收尾確認時的輔助證據。

## 1. 範圍

| 項目 | 值 |
| --- | --- |
| PR | [#87](https://github.com/davidcatcat/davidcattoolsproject/pull/87)（Draft），base 為 PR #84 分支 `863fed0` |
| 審查差異 | `ba5cdb7..1034169`，只動 `.github/workflows/llmtool-ci.yml`（+85/−3） |
| 對應 finding | 獨立審查的 WF87-M1（MEDIUM：find/sha256sum 執行失敗時會被當成驗證成功） |

## 2. 結論：WF87-M1 已正確修正，修正差異沒有新的阻擋問題

- `inventory=$(find …) || fail`、`copy_hash=$(sha256sum < "$exe") || fail`、`source_hash=… || fail`：每個工具的 exit status 都個別檢查。在 bash 中，`var=$(cmd)` 的狀態就是 `cmd` 的狀態，所以不依賴 `set -e`，寫法正確。
- 比對兩個 hash 是否相等之前，先要求兩者都符合 `^[0-9a-f]{64}␠␠-$`（GNU `sha256sum` 讀取 stdin 時的輸出格式）。空字串或格式錯誤的輸出都會被拒絕。
- 12 個 verifier 探針從 workflow YAML 中取出**實際的** `fail()` 和 BEGIN/END 之間的區塊來執行，而不是另寫一份實作。這是好設計：之後若有人改了 guard，探針會一起被驗證。
- 探針遇到 root job 會拒絕執行，不會在無法建立權限失敗條件時靜默通過。
- WF87-L1 與 L2 的註解已改成符合實際的說法：不掃描外部 symlink 的目標，setup-python 與 cache 的函式庫（含 `LD_LIBRARY_PATH`）仍屬於信任範圍。這與 coordinator 接受的非阻擋處置一致。

## 3. 我額外執行的探針（PR 未涵蓋）

做法：在 Mac 上從 `1034169` 的 workflow 取出實際的 guard 執行。GNU 的 `find -perm /022` 用轉接器改成 BSD 的 `+022`，`sha256sum` 用 `shasum -a 256` 轉接成相同的輸出格式。

| 探針 | 結果 |
| --- | --- |
| 正常流程，關閉 errexit | exit 0 |
| hash 輸出改成大寫 | 拒絕：`invalid copy hash` |
| hash 輸出多一行 | 拒絕：`invalid copy hash` |
| 複本根目錄權限 0777（可寫的目錄，不只是檔案） | 拒絕：`writable file left in copy` |

## 4. CI 證據（exact head `1034169`）

push run 37987495943 與 PR run 37987501486：Python 3.9 與 3.14 四個 job **全部 PASS**。

## 5. 低優先觀察（不阻擋）

- 約 70 行的探針 Python 程式內嵌在 workflow YAML 裡，可讀性較差。之後若放寬 allowlist，可以移到 `tools/llmtool/tests/` 或 `scripts/`。不需要現在處理。

## 6. 對 coordinator 的建議（避免再一輪修 CI 循環）

- 修正作者是 Codex，原始作者是 Claude，WF87-M1 的提出者是另一位 reviewer。三方不同，所以只需要**原獨立 reviewer 做一次簡短收尾確認**：WF87-M1 是否關閉、整個 PR #87 能否 APPROVE。不需要再開新的修正項目。
- 確認之後的順序：
  1. 把 PR #87 併入 PR #84；
  2. 在 PR #84 的新 head 重跑 CI；
  3. 對 PR #84 做最終獨立 Security 與 Functional QA；
  4. 決定是否 merge。

## 7. Repo 狀態快照

| 項目 | 狀態 |
| --- | --- |
| PR #84（F1-r3 產品程式） | Draft，head `863fed0`；M2、M3 已修正，M1 依賴 #87；待最終獨立 QA |
| PR #87（M1 CI 修正） | Draft，head `1034169`；CI 全綠；待原獨立 reviewer 收尾確認 |
| PR #85（獨立審查報告） | 開啟中，屬於歷史證據 |
| PR #86（開發者證據報告） | 開啟中，§6 已含 #87 的結果 |
| PR #78（A11b.3 approval 對齊 P0） | 開啟中，待 coordinator 處理 |
| PR #68、#69（A11 與 C 審查） | 開啟中，結論已被 PR #71 採用；建議 coordinator 收尾（merge 或關閉） |
| PR #45（ai-loop 控制腳本 → main） | Draft，維持不動 |
| `automation/ai-loop` | 沒有新的控制腳本變更需要稽核 |

你需要做：無
