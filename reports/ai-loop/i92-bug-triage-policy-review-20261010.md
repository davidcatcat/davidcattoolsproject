# Issue #92 Claude Review：使用者優先的 Bug 分級與持續交付規則

> **產品決策討論、尚未採納。** 這是書面審查，不修改任何程式或文件，也不 merge。
> 審查者：Claude Code（Opus 5.5），2026-10-10。依據：Issue #92 主文與 coordinator 補充、`main` 上的 `tools/llmtool/ROADMAP.md`、PR #84 的獨立審查（R84-S1、S2、L1）。
> 獨立性：Claude 不是提案作者。不過 Claude 寫過 F1 的 M3 與 C2 測試，所以本文對 R84-* 的判斷只供 coordinator 參考，不是 gate。

## 總結：**AGREE，附 6 項必要修改（CHANGE）**

方向正確：讓使用者先能用、新發現先分級再決定、F1 不阻擋無關的功能、修復有上限。但以目前的文字，有三處**容易被誤用來放行安全問題**，必須在採納前補上（見 C-1 到 C-3）。

## 1. 五個角度的回答

### Q1 哪些可以延期或 Won't Fix，哪些即使沒有使用者回報也必須擋下？

**可以延期或 Won't Fix**（全部都要記錄理由與 owner）：
- 測試註解或文件寫得不精確，而且已有較強的獨立證據（例如 R84-L1）；
- 不影響結果的效能或可讀性問題；
- 只出現在未啟用或未交付路徑、且有程式強制不可達證明的問題；
- 測試工具中不影響任何必要斷言的整理工作。

**即使沒有任何使用者回報，也必須擋下該功能的交付：**
- 讓**必要安全檢查 fail-open**（把失敗當成通過）的缺陷，例如 R84-S1；
- 讓結果分類**錯誤宣告成功**的缺陷，例如 R84-S2 可能錯誤回報 APPLIED；
- 可能破壞或遺失資料、錯改真實資源、外洩秘密或擴大權限的缺陷；
- 必要驗收證據缺漏、或被 mock/skip 取代。

使用者回報只是**優先序的訊號，不是存在性的證據**。這一點提案第 1 節已寫，必須保留。

### Q2 Severity、Priority、Release Blocker 分開是否可行？哪裡容易被誤用？

可行，而且必要。但有三個漏洞要補：

- **C-1 安全類 fail-open 自動成為 Release Blocker。** 只要某個缺陷會讓「當次要交付且實際可達」的路徑上，任何必要安全或正確性檢查變成 fail-open 或錯誤成功，**不論標成哪個 Severity，Release Blocker 一律為 YES**。這樣可避免「標成 S2 Medium、有 workaround，所以排到後面」的誤用。R84-S1 和 S2 都屬於這一類。
- **C-2 降級要由非作者決定。** Severity 或 Release Blocker 從 YES 降成 NO，必須由**非作者**（獨立 reviewer 或 coordinator）書面裁定並附理由。開發者不得自己降級自己程式的缺陷。
- **C-3 「停用或隔離」必須由程式強制。** 「停用受影響功能」要有程式層級的不可達證明，例如不匯出、沒有 CLI 入口，並附上證明不可達的測試。不能只在文件寫「請勿使用」。workaround 也必須寫明步驟並驗證過，否則不能當成降級理由。

另外有一個**命名衝突**：提案的 Severity 等級叫「S1 High」，審查 finding 的編號叫「R84-S1」。建議 Severity 改名為 `SEV-0` 到 `SEV-3`，避免混淆。

### Q3 產品 bug 與測試 bug 的判定，以及「獨立等效驗證」有沒有證據漏洞？

有。補上以下兩點：

- **C-4 歸類為「測試 bug」需要證據。** 必須用一個**不依賴該測試的 oracle**，在同一個 SHA 上重現，證明產品行為正確、錯的是測試。例如 PR #84 的 FD 測試：單獨執行會通過，和其他測試一起跑才失敗。沒有這種證據，就一律先當產品 bug。
- **C-5 「等效驗證」必須真的等效。** 要使用**相同的斷言**、在**相同的 SHA** 與平台上（例如 Mac 原生不能用 Linux 或 mock 取代），由**非作者**執行並保存證據。比原本弱的驗證只能算 NOT_VERIFIED，不能算 PASS。

### Q4 F1 HOLD 時，哪些功能能繼續？（已核對 ROADMAP，沒有猜測）

| 項目 | 是否依賴 F1（A11b.2.2.2） | 依據 |
| --- | --- | --- |
| main 上已合併的功能（A0–A10、A11a 診斷、C1a 本地 excerpts、A11b.1 資料契約、A11b.2.1 唯讀 preflight） | **不依賴**，照常使用與維護 | 已合併，F1 沒有修改它們的公開路徑 |
| C1b.0 excerpt 隱私修正（#66）、C1 後續（#63） | **不依賴** | 屬於 excerpt 外送路徑，與 Git ADD 無關 |
| ai-loop 暫行項目（#74 CI gate、#80 P1、#81 P3–P5）、FD 洩漏調查（#83） | **不依賴** | 控制器與測試範圍 |
| A11b.3 approval semantics 的**設計與 policy 層**（含 PR #78 的 issuer 對齊 P0） | **不依賴**，可以先設計 | ROADMAP 第 880 行起是 policy 規則；接上 executor 時才需要 F1 |
| A11b.5 budgets / bounded repair、A11b.6 failure injection 中**不碰 mutation 的部分**（deterministic budget、claim→evidence 測試） | **部分不依賴** | A11b DoD 中「resource budgets 與 bounded repair 有 deterministic tests」可以獨立完成 |
| A11b.4 patch apply / rollback | **依賴 executor 基礎**（containment、audit），實際上被擋 | 需要受控的 mutation executor，F1 是第一個 executor 切片 |
| A11b Definition of Done | **被擋** | ROADMAP 第 955 行：「有限 executor 在 sandbox/policy 下可完成一個安全 mutation scenario」 |
| **A11c 整體** | **被擋**（正式依賴 A11b DONE） | ROADMAP 第 86、575 行：「A11b DONE + …」 |
| A11c.2 真實任務驗收（真 repo 的 bugfix） | **被擋**，而且還需要 F2 | 需要真實 repository mutation |

**結論：** F1 HOLD **不會**影響現有功能，也不會擋住 C1b.0、ai-loop 暫行項目、A11b.3 設計與 A11b.5/6 的非 mutation 部分。但它**會**擋住 A11b DONE，因此擋住整個 A11c。所以 F1 不能無限期 HOLD，否則 llmtool 自我開發的主線會停住。這是成本面要考慮的事。

### Q5 如何以使用者實測為核心，同時保留安全底線？

- 使用者回報走 P0 流程：需求方只描述症狀，由 AI 分級；只有「功能不對」需要需求方驗收。
- 每個使用者可見的功能，都要有一個**使用者旅程 smoke test** 作為 release gate，例如 `llmtool test-excerpts` 對真實 pytest 輸出的端到端執行。
- 發佈時附 **Known Issues** 清單，寫明限制與 workaround。
- 安全底線不是由使用者回報決定，而是由 C-1 自動觸發。

### Q6 成本與 Token

- **C-6 每個功能設時間或成本上限，觸發後檢查價值。** 例如同一個 PR 累計超過 4 輪獨立審查，或發現同類型缺陷第 3 次時，coordinator 必須提出「繼續修 / 縮小範圍 / 重新設計 / HOLD」的書面成本與效益判斷。若有 token 用量就附上，未知就寫未知，不能填 0。
- 「同一根因最多 2 次」需要定義誰判斷是否同一根因：由非作者 reviewer 判定，避免換個名字重新計數。

## 2. R84-S2 的實際判斷

同意 coordinator 的分級：**SEV-1 等級的正確性問題（可能錯誤宣告 APPLIED），F1 Release Blocker 為 YES，主線 Blocker 為 NO**。

關於「先 HOLD F1、不自動開始 S2 開發」：我**部分同意**。F1 HOLD 會擋住 A11c（見 Q4），而 S2 很可能有一個成本低、可驗證的最小修法，值得先評估一次。

**最小修法候選（供 coordinator 與 Security reviewer 評估，不是授權）：**
- 把「finalization boundary」定義為 **supervisor 已死亡，且 status pipe 讀到 EOF**。
- Host 收到 `MUTATION_GIT_EXIT` 後，**不要**立刻停止讀取。先照原本流程殺掉 supervisor 群組，然後**一直讀到 status pipe EOF**。這段期間只要收到任何 `MUTATION_STATE_UNKNOWN`（或任何非預期 frame），一律以 UNKNOWN 為準並保留現場。
- 為什麼這樣不會漏：supervisor 死亡前寫入 pipe 的資料會留在 kernel buffer，EOF 只會在所有寫入端關閉後出現；supervisor 死亡後也不可能再有新的寫入。所以不會像「一次性 drain」那樣漏掉。
- 監控、永久保留、所有權都不改；不新增解除機制，不修改 workflow。
- **成本上限：** 最多 2 次修復嘗試。每次都要有 Euclid 那種組合重現作為回歸測試，加上 Mac 驗證與非作者審查。超過上限就 HOLD F1，並把「A11c 被擋」這個成本記錄給 owner 參考。依 P0，這仍是 AI 的決定；只有在成本實際影響需求方的目標時才告知。
- **不能選的方案：** 停用 S2 所在的路徑。那就是 F1 的核心流程，停用就等於沒有 F1。

## 3. 最小落地清單（採納後由另一個 PR 實作，本次不做）

1. `skills/ai-team-delivery/SKILL.md`：新增一節「Bug triage」，內容為 SEV-0 到 SEV-3、Priority、Release Blocker 三欄，C-1 到C-6，以及非作者降級規則。這是**唯一完整來源**。
2. `tools/llmtool/WORKFLOW.md`：只加一段流程摘要和連結，不重複內容（呼應 PR #69 的 L1）。
3. `tools/llmtool/ROADMAP.md`：加上第 2 節 Q4 的依賴表，寫明哪些項目在 F1 HOLD 時可以平行推進。
4. `.github/ISSUE_TEMPLATE/bug.yml`：欄位為 user_impact、severity、priority、release_blocker、blocker_decided_by（非作者）、reproducer、affected_scope、reachability_proof、workaround_verified、target_milestone、decision_rationale。
5. 不新增 skill，也不修改 security-review 或 qa-validation 的內文；只在它們的報告模板加上這三個欄位的連結。

你需要做：無
