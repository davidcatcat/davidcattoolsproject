# PR #13 review：A11 Production Workflow & Diagnostics roadmap（2026-10-02）

## 範圍與方法

- 審查對象：PR #13 `docs/a11-diagnostics-roadmap-20261002` → `main`。
  - HEAD `fb56e20`；以 `origin/main` `cb8a489` 為基礎，ahead 8、behind 0；MERGEABLE。
  - 8 個 commit、8 個檔案、+224／−16。
- 變更內容：
  - `tools/llmtool/ROADMAP.md`：A11 從 2 行擴充為 A11.0–A11.10，A12 加入「必須沿用 A11 模型」的規定。
  - `tools/llmtool/README.md`、根目錄 `README.md`：更新 milestone 摘要。
  - 5 個 skill 加入 diagnostics 相關的檢查：`debug-ci`、`implement-feature`、`inspect-repo`、`review-change`、`validate-runtime`。
- 方式：
  - 唯讀閱讀完整 diff，對照現有的 A0–A10 契約（README、ROADMAP、先前的 review 報告）。
  - PR 只改文件，沒有 production code，所以這次沒有執行功能測試。
  - CI：`fb56e20` 的 `llmtool CI` 是 success（中間的 `a635003` 被 cancel）。
- 事實確認：PR 宣稱 A10 已 merge。PR #12 確實在 2026-10-01T19:41:00Z MERGED，`21b9757` 也在 `origin/main` 裡，屬實。

## 結論：APPROVE（可以 merge），但建議在 A11 動工之前補上關鍵決策

這份 roadmap 的方向與順序正確。不過有幾個會決定 A11 架構的問題還沒定案：

- 第 2 點：預設寫 log 與現有的寫檔契約。
- 第 3 點：跨 provider fallback 的資料治理。
- 第 4 點：執行引擎的 sandbox 與 git hardening。

建議在 A11 寫程式之前補進 roadmap，最好連第 1 點（拆分 milestone）一起決定。

## 做得好的地方

- **可觀測性先於執行。** 「A11 在 observability／diagnostics foundation 完成之前，不得先開啟 autonomous production mutation」，順序正確。
- **「Model-reported success != task success」是硬規則。** A11.5、A11.10 與 Definition of Done 都要求用實際的 validator、test 與 Git 狀態驗證。
- **延續 A0–A10 的安全邊界：**
  - A6 classifier 不授權執行。
  - approval 綁定 task／run／action，不能只靠模型文字裡的 "approved"。
  - dirty worktree 或 unexpected state 預設 fail closed。
  - debug 模式不能關閉 redaction、approval 或 safety。
  - provider capability 不等於 authorization。
- **A11.10 的驗收標準可以實際驗證。** 另一個 reviewer 只靠 run metadata 與 trace，就要能找出失敗 run 的主要 failure layer。
- **A12 必須沿用 A11 的 task／run／event／error／approval 模型**，不能另外建立 logging、execution、approval 或 error 系統。
- **error code 數量有節制。** 第一版只建立實際需要的核心 error code，不預先發明大量沒有驗證過的錯誤。
- **歷史 validation report 不回寫。** 只同步目前的 README／ROADMAP 狀態。
- **skill 的修改措辭通用**（「如果系統提供 run／task ID、structured error、trace…」），toolbox 不會被綁定在 llmtool 上。

## 建議修改（依重要性排序）

### 1. A11 範圍過大：建議拆成 3 個可以各自 merge 的 milestone

A11.0–A11.10 共 11 個子項，涵蓋 diagnostics、persistent logging、tracing、execution／approval、recovery、CLI、redaction／retention、failure injection、integration 與 acceptance，比 A0–A10 任何一個 milestone 都大，很難一次 review 與驗收。建議：

| 建議 milestone | 內容 | 先套用在 |
|---|---|---|
| A11a Observability | A11.1 Diagnostics Foundation、A11.2 Persistent Run Logging、A11.3 Unified Tracing、A11.6 Diagnostics CLI、A11.7 Redaction／Retention | 現有的唯讀指令（ask、analyze-log、context-pack、retrieve-context、orchestrate） |
| A11b Execution & Approval | A11.4 Execution & Approval Engine、A11.5 Validation & Recovery、A11.8 Failure Injection | 有限的 executor |
| A11c Integration & Acceptance | A11.9 Production Workflow Integration、A11.10 Real-world Acceptance | 端到端 workflow |

每一個都有自己的 DoD、validation report 與外部 review，延續目前的節奏。

### 2. 預設持久化 log 會改變 llmtool 現有的核心契約，需要明確決定

- 目前的契約（`tools/llmtool/README.md:594`）：「明確的 config 操作及 A7 feedback capture/review 可寫入指定檔案；A9 orchestration 不寫檔」。
- 上一輪 A10 review 的 E（`--save-output`）就是以「唯讀 milestone 引入寫檔，會帶來路徑、覆寫、symlink 與資料保留契約」為理由被拒絕的。
- A11.2 卻讓每次 workflow 都寫 `LLMTOOL_HOME/runs/<run_id>/trace.jsonl` 與 `summary.json`。

roadmap 應該明確定義：

- **預設行為：** 唯讀指令要不要寫？建議唯讀指令預設不寫，或只寫 metadata（沒有 prompt、response、檔案內容）；有 execution 的 workflow 才預設寫。
- **opt-in／opt-out 的方式：** CLI flag 或 user config。
- **哪些內容永遠不寫：** prompt、模型回應、retrieved 的檔案內容，至少在 normal／verbose 模式。
- README 的寫檔契約要同步更新。

### 3. 跨 provider fallback 是資料治理問題，目前沒有規範

- A11.1 的 error 欄位有 `fallback_allowed`，A11.3 的事件有 `fallback`，A11.10 的驗收要求 "provider timeout／fallback"。
- 如果從公司的 Qwen gateway（`openai-compatible`）fallback 到外部的 Claude CLI、Codex CLI 或 OpenAI，**公司的 log、程式碼與資料就會被送到公司以外的服務**。
- 另外，A9、A10 都是刻意設計成 fail-fast、沒有 fallback，A11 改變這個設計的條件需要寫清楚。

建議：

- **預設不允許跨信任邊界的 fallback。**
- 只能在 user config 裡明確宣告為同一信任等級的 provider 群組之間 fallback；專案設定不能宣告。
- 每次 fallback 都寫入 audit event，包含從哪個 provider 到哪個 provider，以及原因。
- auth、config、safety 類的錯誤，永遠不觸發 fallback（A11.5 已有類似的規定）。

### 4. A11.4 執行引擎缺少關鍵的設計約束

- **sandbox 與執行環境：** 由誰執行 shell？
  - 工作目錄要限制在 repo 或授權範圍內。
  - 環境變數使用 allowlist（比照 claude-cli、codex-cli、git_intel 的做法）。
  - 每個 action 有 timeout，要定義網路政策。
  - 永遠不能用 sudo 或提權。
- **執行 git 時必須沿用 A5 的 hardening。** A5 已經實測證實：在不受信任的 repo 裡，一般的 `git status`、`git diff` 會執行 repo 設定的 `core.fsmonitor`、`core.pager`、filter、textconv 等。A11 的 executor 執行任何 git 指令時，都必須套用 `git_intel` 的 `SAFE_CONFIG`（`-c` 覆寫）、`GIT_CONFIG_NOSYSTEM` 與環境變數清理，否則 A5 的防護在 A11 就失效了。
- **非互動環境的 approval：** 在沒有 TTY 的環境（CI、Jenkins、背景執行）裡，需要 approval 的 action **預設拒絕**，不是自動通過；要寫明 approval 的介面（CLI 的 TTY 提示、未來 A12 的 `/approve`）。
- **patch：** 套用前先用 `git apply --check`；失敗或驗證不通過時要能 rollback，不能留下改到一半的工作目錄；patch 套用前後的 Git 狀態都要寫入 audit。

### 5. 缺少成本與資源的上限

用 Claude、Codex 跑 production workflow 可能很貴，A11.5 的 bounded repair 也沒有具體數字。建議明確定義：

- 每次 run 的 token、成本、wall-clock 上限，以及最多 provider 呼叫次數。
- bounded repair 的最大次數（例如 2 次），以及判斷「重複同樣失敗的 action」的規則。
- A11.7 retention 的具體預設值，例如最多保留幾個 run、總共多少 MB、保留幾天，以及清理的時機。

### 6. `LLMTOOL_HOME` 的安全細節

- 目錄 0700、檔案 0600，比照 user config。
- 拒絕 symlink 的 `LLMTOOL_HOME`、`runs/`、`<run_id>/`，比照 A7 feedback 與 config 的寫檔規則。
- 多個 run 同時寫入時的處理：run_id 不能碰撞，寫入要是原子的。
- **專案設定（`.llmtool.yaml`）不能修改 `LLMTOOL_HOME`**，延續專案設定的低信任模型。
- 和 Windows 的 user-data 慣例一起寫清楚。目前 A10 retrieval 是 POSIX-only，要說明 A11 在 Windows 上的範圍。

### 7. exit code 的向後相容

- 目前的 exit code 契約是 0（成功）、1（一般錯誤）、2（用法錯誤）、130（中斷）。
- A11.1 的「穩定的 CLI exit-code families」如果改動既有的值，會讓已經依賴 exit 1 的 shell script 或 Jenkins job 出錯。
- 建議保留現有的值與意義，細的分類放在 stderr 的穩定 error code，以及 `--json` 的 `code` 欄位；真的需要新增 exit code 時，只用目前沒有使用的值，並在 README 列出完整的對照表。

### 8. 小問題

- 5 個 skill 的 commit（`770a6fc`、`7701778`、`a635003`、`e8f2dc8`、`fb56e20`）都叫 `docs(skills): align workflows with A11 diagnostics`，建議 squash，或改成能分辨的 message（例如註明是哪個 skill）。
- `implement-feature` 與 `review-change` 都加入了「模型說 done 不算完成證據」以及 redaction／retry 相關的規則。repo 的 CLAUDE.md 有一條是「不要在多個 skill 重複同一條規則」，可以考慮只保留在一個 skill，另一個用引用。
- `tools/llmtool/README.md` 的 A11 與 A12 摘要和 ROADMAP 一致，沒有問題。

## 建議的下一步

1. merge PR #13，或先補上第 2、3、4 點再 merge。
2. A11 動工之前，在 ROADMAP 補上：
   - 寫檔契約與預設 logging 行為（第 2 點）
   - fallback 的信任邊界（第 3 點）
   - executor 的 sandbox、git hardening、非互動 approval 與 patch rollback（第 4 點）
   - 成本與資源的上限（第 5 點）
3. 決定是否把 A11 拆成 A11a／A11b／A11c（第 1 點）。
4. 第 6、7、8 點可以在各子 milestone 的設計階段處理。
