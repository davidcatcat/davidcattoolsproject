# NLF Punctuation Safety Report

日期：2026-09-28
Milestone：Roadmap Phase **A1 — 標點安全（punctuation safety hardening）**
Branch：`feat/nlf-roadmap-a1-20260928`（只在 feature branch，**沒有 merge 到 main**）
狀態：已實作、完成 QA 與 review，等待人類 review

## Summary

v0.4.0 在解析時會把引號外的 `( ) ! ?` 等標點**默默丟掉**，而這會改變查詢的意思：

- `log files !containing "ERROR"` 回傳的**正是使用者想排除的檔案**。已經用真實搜尋驗證過：結果列出 `err.log`。
- `larger than 1GB or (smaller than 1KB and modified today)` 被重新分組成 `(>1GB OR <1KB) AND today`。
- 沒有引號的檔名值會被剝掉 `?` 或 `.`：`core.?` 變成 glob `"core"`，`filename is core.` 變成 `"core"`。

這違反了 nlf 的核心契約：不默默擴大範圍、不自動修正語法。

A1 的做法：

- 引號外的括號和 `!` 一律報錯。
- `?` 只允許出現在整個查詢的最後。
- 標點直接黏在沒有引號的值後面時報錯，並要求加引號。
- 修正三處點號處理：黏在前面的點號不再被當成路徑；`exclude ./`、`exclude /` 不再默默不做事；`in ../..` 不再少一層。
- 移除錯誤訊息裡過時的 "v0.1" 字樣。

所有變更只會讓**以前被接受的查詢改成 exit 2**，或把錯誤的解讀改正。**沒有任何查詢因此得到更多檔案。**
Query 結構、search、explain、`main.go`、help 都沒有修改。

## Baseline

| 項目 | 值 |
|---|---|
| Baseline commit | `91ee959`（origin/main） |
| Baseline version | **0.4.0** |
| Baseline tests | 759 PASS / 0 FAIL / 0 SKIP |
| Feature branch | `feat/nlf-roadmap-a1-20260928`，從最新的 origin/main 建立，並取消了 upstream |
| Isolation | git worktree（main checkout 完全沒有碰過） |

## Why This Feature Was Chosen

Product Agent 在 roadmap（`reports/nlf/2026-09-28-roadmap.md`）中評估了 47 個 candidate，並建議 A1 做標點安全。Lead 也獨立重現了問題。

- **Correctness 與 safety 優先：** 它修的是現有的契約違反，而不是缺少的功能。`!containing` 回傳的是和使用者意圖相反的結果，比任何新 flag 都嚴重。
- **使用頻率高：** 熟悉 find 和 shell 的維運人員很自然會寫 `!`、括號，以及沒有引號的 glob。
- **風險只朝保守的方向：** 只會讓原本被接受的查詢改成報錯，或修正錯誤的解讀。
- **範圍小：** 不需要 AST、不需要重寫 tokenizer 或 search。
- **是之後的前提：** 先把這些符號保留下來並明確報錯，之後才能安全地賦予 NOT 和括號語意（Phase C/D）。
- **次佳選擇：** A3（`-0`）。兩者互相獨立。

## Agent Orchestration

**SUBAGENTS USED**

| Agent | 工作 |
|---|---|
| Product / Roadmap | audit 程式碼、報告與 git history；用 binary 在 fixture 上實測；做 DevOps gap analysis；撰寫 roadmap（24 個章節、47 個 candidate、Phase A–D）；建議 A1 並提出 spec |
| Architecture / Parser | read-only 的架構 audit（資料流、風險、不需要 AST 的結論、各 candidate 的擴充點、測試基礎設施）；review A1 design，回覆 **APPROVE WITH CHANGES**，指出 4 項修改（單獨的 `.` 會被當成路徑、規則的優先順序、會被破壞的測試 substring、錯誤訊息的 token） |
| Implementation | 實作 A1 與 QA 修正，加入測試；沒有任何 git 寫入 |
| QA / Adversarial | 獨立設計 hostile input；與 HEAD 做 1,415 個查詢字串的 differential 對照；真實搜尋；結論 **PASS WITH ISSUES**，找到 8 類問題 |
| Review | 第一次執行時因 usage limit 中斷，沒有產生結果；重新執行後檢查實際 diff、對照 main 探測約 110 個查詢、跑 48 個 README/help 例子；結論 **APPROVE WITH CHANGES**（S1、S2 兩項 should-fix） |
| Release / Git Safety | commit 與 push 前檢查 staging、secrets、binaries、本機路徑、main 狀態（見 Validation Results） |
| Lead | 選定 A1、定稿 design、判斷 QA 問題的處理方式、修正 `../..` 路徑 bug 與 review 的 S1、S2、full validation、撰寫報告與 checkpoint |

## Design

- Design 由 Architecture Agent 在寫程式之前 review。
- 定稿內容記錄在 `reports/nlf/autonomous-session-state.md` 的「Important semantic decisions」。

## Query / CLI Semantics

| 規則 | 內容 |
|---|---|
| 1 | 引號外的 `(` `)` `（` `）` `!` `！`，不論出現在哪裡都 **exit 2** |
| 2 | `?` / `？` **只允許**是整個查詢的最後一個 token（問句結尾），其他位置 exit 2 |
| 3 | `. ? ! ( ) ？ ！ （ ）` **直接黏在**沒有引號的值後面（檔名、內容、exclude、副檔名）時 exit 2，提示同時列出兩種寫法。規則 3 優先於規則 2，所以 `包含 錯誤？` 會報錯 |
| 4 | 前面沒有空白、全部由點組成的一段（`.`、`..`）是標點，不是路徑；前面有空白的 `.` / `..` 仍然是路徑 |
| 5 | exclude 的名稱是 `.`、`..`、`./`、`../`、`/` 或空字串時 exit 2 |
| 6 | 只有標點的查詢（`?`、`。`、`,`）視為空查詢，exit 2 |
| 7 | 路徑（開頭是 `/`、`~/`、`./`、`../`）結尾的 `.` / `..` 是目錄的一部分，會保留（`in ../..`）；內容或檔名的值以 `/.` 或 `/..` 結尾時 exit 2 |
| 8 | 分隔符號 `, ; 、 ， 。 ； ：` 維持原本的行為；引號裡的內容維持原樣（`filename matches "app-?.log"`、`containing "(x)"`） |

- 被拒絕的查詢在 `--explain` / `--confirm` 下同樣 exit 2，stdout 沒有任何輸出，也不印 plan 或提示。
- Exit code 0 / 1 / 2 的意義不變，沒有新增 flag。

## Previous Behavior

v0.4.0 實測結果：

| 查詢 | v0.4.0 |
|---|---|
| `log files !containing "ERROR"` | 內容包含 ERROR（意思完全相反） |
| `larger than 1GB or (smaller than 1KB and modified today)` | `(>1GB OR <1KB) AND today` |
| `filename matches core.?` / `app-?.log` / `starts with app?` / `is core.` | glob `"core"`、glob `"app-"` 加上副檔名 `.log`、`"app"`、`"core"` |
| `找 go 檔！`、`go files )` | 默默接受 |
| `containing "x".` | 搜尋位置被設成 `.`，搭配 `--dir` 時會產生衝突 |
| `找 go 檔..`（在子目錄中執行） | 搜尋位置被設成 `..`，改去搜尋**上層目錄** |
| `包含 錯誤.` vs `filename is core.` | 前者的值是 `"錯誤."`，後者是 `"core"`（行為依文字種類而不同） |
| `exclude ./`、`exclude /` | 被接受，但實際上什麼都沒排除 |
| `*.log?` | 副檔名 `.log`（`?` 被丟掉） |
| `?`、`。` | 搜尋全部檔案 |
| `in ../..` | 搜尋位置變成 `../.`，少了一層 |
| 否定條件的錯誤 | 「v0.1 還不支援否定條件」 |

## New Behavior

- 上表的每一列現在都是 exit 2，錯誤訊息為雙語並附提示；只有 `in ../..` 這一列不同：它改成正確搜尋 `../..`。
- 錯誤訊息的例子：
  - `"!" may mean NOT, which is not supported: "!"` / hint `containing "!important"`
  - `punctuation right after an unquoted value is ambiguous: "core.?"` / hint `filename matches "core" or filename matches "core.?"`
  - `punctuation right after an extension is ambiguous: "*.log?"` / hint `filename matches "*.log?"`
  - `標點緊接在沒有引號的值後面…：「錯誤？」` / 提示 `包含 "錯誤" 或 包含 "錯誤？"`
  - `檔名以 app.開頭` 的提示是 `檔名以 "app" 開頭 或 檔名以 "app." 開頭`
- 仍然可以用：`find go files?`、`找 go 檔？`、`files named "x"?`、`包含 "x"。`、`find go files.`、`go files modified today.`、`files changed on Sep 28, 2026`、`包含 錯誤訊息，大於 1KB`、`包含TODO且大於1MB`、`in .`、`在 .. 找 go 檔`、`exclude ./src/gen`、`exclude ./vendor/.`。

## Architecture

- **`tokenize.go`**
  - 前面沒有空白的點號（單獨一個或一段）→ 輸出 `tPunct` `.`。
  - `classifyASCII` 只有在**路徑**以 `/.`、`/..` 結尾時才保留結尾的點。
- **`parse.go`**
  - `reservedPunct`：規則 1、2，在 `step()` 的 tPunct 分支檢查。Architecture Agent 已確認這是唯一會默默吞掉這些字元的地方。
  - `gluedPunctRun`：共用 helper，找出黏在一起的一段標點。
  - `rejectGluedPunct`：規則 3，在 `contentTerm`、`excludeNames` 以及 `filename.go:nameValue` 中呼叫，所以 soft-content、內容 OR、檔名 OR 的 alternative 都會自動套用。
  - `rejectGluedExtPunct`：副檔名的規則 3。`extension()` 因此改為回傳 `(bool, error)`，它只有一個呼叫點。
  - `rejectDotSuffix`：值以 `/.` 或 `/..` 結尾的情況。
  - `checkExcludeName`：對 trim 之後的名稱做 `path.Clean`，擋掉 `.`、`..` 與空字串。
  - `Parse`：全部都是標點時回傳空查詢的錯誤。
- **`filename.go`：** `nameValue` 呼叫上述檢查；新增 `rawBetween`，把使用者自己的比對詞放進提示。
- **沒有修改：** `query.go`（Query 結構）、`explain.go`、`internal/search/`、`main.go`、help 與 `testdata/help.golden`。
- **不需要 AST。** Architecture Agent 的結論是：只有 NOT、不同類的 OR、括號才需要 AST。

## Files Changed

| 檔案 | 用途 |
|---|---|
| `tools/nlf/internal/query/tokenize.go` | 點號的處理；只有路徑保留 `/.` 與 `/..`；更新 tPunct 的註解 |
| `tools/nlf/internal/query/parse.go` | 規則 1–7 的 helper 與呼叫點；移除 "v0.1" 字樣 |
| `tools/nlf/internal/query/filename.go` | 檔名值的檢查；`rawBetween` |
| `tools/nlf/internal/query/punctuation_test.go` | **新增**：query 層級的所有測試 |
| `tools/nlf/main_test.go` | 新增 CLI 與 runtime 測試 |
| `tools/nlf/README.md` | 新增「標點符號（引號外）」規則區塊；移除 "v0.1"；標題改為 "Known limitations" |
| `reports/nlf/2026-09-28-roadmap.md` | 已在 docs commit；修正兩處事實（程式行數，以及「沒有修改 production code」的時間點） |
| `reports/nlf/2026-09-28-punctuation-safety.md` | 本報告 |
| `reports/nlf/autonomous-session-state.md` | session checkpoint |

## Tests Added

測試總數：759 → **936**（新增 177 個 test/subtest），0 FAIL，0 SKIP。既有測試**被移除的行數是 0**。

| 測試 | 內容 |
|---|---|
| `TestPunctuationBugs` | 每一個已知 bug 的例子，同時檢查英文與中文錯誤訊息 |
| `TestReservedPunctuationPositions` | 8 個保留字元 × 開頭、中間、結尾、黏在值後面 |
| `TestPunctuationAllowed` | 必須繼續可用的查詢，以精確的 Query 比對 |
| `TestGluedPunctuation` | 檔名、內容、soft content、內容 OR、檔名 OR alternative、exclude 的 token 與提示 |
| `TestPunctuationEdgeCases`、`TestGluedDotToken`、`TestNoStaleVersionInMessages` | `?` 不在最後的各種情況、點號 token、訊息中沒有 "v0.1" |
| `TestGluedDotRuns`、`TestExcludeNothing`、`TestGluedExtensionPunctuation`、`TestPunctuationOnlyQuery`、`TestWrappedFilenameHints`、`TestReservedPunctuationHints` | QA 第 1、2、3、4、8a、6a、7 項的 regression |
| `TestPathDotComponents`、`TestDotComponentOnlyForPaths` | `../..` 路徑修正；review 的 S1、S2 |
| `TestPunctuationSafetyEndToEnd`、`TestPunctuationQARegressionsEndToEnd`、`TestParentPathRoot`（CLI） | exit 2、stdout 為空、`--explain` / `--confirm` 不印 plan；真實搜尋：`找 go 檔..` 不會搜尋上層目錄、`../..` 往上兩層、`"*.log?"` 加引號時是 glob |

## QA Findings

- QA Agent 結論：**PASS WITH ISSUES**。與 HEAD 做了 1,415 個查詢的 differential 對照，除了刻意新增的 reject 之外，沒有其他 regression。
- 規則 1–5 在所有位置都正確，也包括引號、日期、OR、路徑、大小與數量、全形字元，以及 100k 字元的輸入。

## Bugs Found During QA

| # | 嚴重度 | 問題 | 處理 |
|---|---|---|---|
| 1 | HIGH | 黏在前面的 `..`（`找 go 檔..`）變成搜尋位置 `..`，搜尋上層目錄（HEAD 就已存在） | **已修正**：tokenizer 對點號的處理 |
| 2 | MEDIUM | `包含 錯誤..` 繞過規則 3 | **已修正**（同第 1 項） |
| 3 | MEDIUM | `exclude ./`、`/` 被接受，但什麼都沒排除 | **已修正**：`checkExcludeName` |
| 4 | MEDIUM | `*.log?` 的 `?` 被丟掉 | **已修正**：`rejectGluedExtPunct` |
| 5 | LOW | 外觀相似的標點（`﹖ ‼ …`）會被併進值 | 沒有修正，列在 Known Limitations |
| 6 | LOW | wrapped 形式的檔名提示不合法 | **已修正**；OR 沿用比對方式時的提示沒有修正 |
| 7 | LOW | `!` 的提示用了括號的例子；中間的 `?` 一律給 glob 的提示 | **已修正** |
| 8 | LOW | 只有標點的查詢會搜尋全部檔案 | **已修正**；`exclude a, b, go files`、含換行的檔名沒有修正 |
| — | MEDIUM | `in ../..` 變成 `../.`，少一層（Implementation Agent 發現，HEAD 就已存在） | **Lead 修正** |
| S1 | should-fix | review 發現：`/.` 的保留規則套用到所有字，導致 `包含 a/.` 保留了句號 | **已修正**：只有路徑保留，加上 `rejectDotSuffix` |
| S2 | should-fix | review 發現：`exclude vendor/.` 的意思默默改變 | **已修正**：現在是規則 3 的錯誤，並有測試固定 |

## Runtime Verification

**macOS（本機）：** 用 worktree build 的 binary，在 mktemp fixture 上做真實搜尋。

| 查詢 | 結果 |
|---|---|
| `log files !containing "ERROR"` | exit 2（v0.4.0 會列出 `err.log`） |
| 在 `a/b/src` 執行 `go files in ../..` | `a/up.go`、`a/b/mid.go`、`a/b/src/x.go`（v0.4.0 會少掉 `up.go`） |
| `--dir <sub> 找 go 檔..` | 只列出子目錄中的檔案 |
| `find go files?`、`找 go 檔？` | 正常搜尋 |
| `"*.log?"`（有引號） | 只找到 `a.log1` |
| 被拒絕的查詢加上 `--explain` / `--confirm` | exit 2，沒有 plan、沒有提示 |

- QA Agent 也做了真實搜尋與 differential 對照；Review Agent 跑了 48 個 README 與 help 的例子，和 main 的 exit code 全部相同。

## Performance Notes

- 所有檢查都在 parse 階段、每個 token 做 O(1) 的判斷，不影響 search。
- search 的程式碼沒有任何修改。

## Backward Compatibility

- 既有的 759 個測試全部保留並通過，一行都沒有修改。
- **行為變更**：以前被接受、現在 exit 2 的查詢如下。這些都是刻意的，都附有提示：
  - 引號外的括號與 `!`，包括句尾的 `找 go 檔！`、`today or yesterday!`。
  - 不在最後的 `?`，以及黏在值或副檔名後面的 `?`：`包含 錯誤？`、`files containing TODO?`、`*.log?`、`在 ~/Downloads? 找`。
  - 句尾的點號直接黏在沒有引號的值後面：`containing TODO.`、`filename is nginx.conf.`、`exclude .git.`、`*.go.`。
    - 英文使用者可能會覺得意外，但這符合「不默默改變值」的原則。
    - 中文的 `。` 仍然可以用。
  - 黏在前面的 `..`、`在..`。
  - exclude 的名稱是 `.`、`..`、`./`、`/`、`""`；以及 `exclude src/..`、`exclude vendor/.`。
  - 只有標點的查詢。
- **解讀方式改正（不是新的 reject）：**
  - `in ../..`、`in ~/..` 現在是正確的上層目錄。
  - `in /tmp/.`、`in /.` 的搜尋位置在 `--explain` 中顯示成 `/tmp/.`、`/.`，實際位置相同。
  - `exclude "./."` 等的錯誤訊息，從「不能排除搜尋位置本身」改成 `cannot exclude "." or ".."`。
- **不變的部分：**
  - filler-only 的查詢（`find`、`找`、`and`）維持原本行為：exit 0、列出全部檔案，有測試固定。
  - README 與 help 的 48 個例子全部維持原本的 exit code。

## macOS Status

- **macOS runtime：PASS。** 全部測試、真實搜尋與 fixture 探測都在 macOS（APFS）上執行。

## Linux Build Status

- **Linux build：PASS。** `make dist` 產生 `linux-amd64` 與 `linux-arm64` 的靜態 ELF；`GOOS=linux` 的 vet 通過；3 個套件的測試都能 `go test -c` 編譯。

## Linux Runtime Status

- **Linux runtime：NOT RUN。** 目前沒有 Linux 環境，也沒有 CI（roadmap A2 會處理）。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | PASS（乾淨） |
| `go vet ./...` | PASS |
| `go test ./...` | PASS（936 個，0 FAIL，0 SKIP） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | PASS |
| `make check` | PASS |
| `make dist` | PASS：`nlf-0.4.0-*`，因為本輪沒有 bump 版本；`SHA256SUMS` 驗證全部 OK |
| Linux build | PASS |
| Linux runtime | NOT RUN |
| staticcheck | 未安裝，沒有執行 |
| Production 是否 read-only | PASS：沒有 `os/exec`，也沒有寫檔的 `os` 呼叫；`TestProductionCodeIsReadOnly` 通過 |

## Known Limitations

- 外觀相似的標點（`﹖ ﹗ ‼ ⁇ ⁉ ﹙ ﹚ …`）不在保留字元的集合中，黏在值後面時會被當成值的一部分。
- OR 沿用比對方式時，錯誤提示沒有列出比對方式（`filename is a.conf or b.conf?`）。
- `containing TODO..` 的提示只剝掉一個點（`classifyASCII` 只處理一個結尾的點）。
- 路徑值結尾的單一句號（`在 ./src.`）仍然會被當成句號剝掉，因為這是本輪範圍之外的既有行為。
- `exclude src, vendor, go files` 會把後面的名稱變成副檔名。這是既有的 parser 問題，列在 roadmap。
- 含換行的檔名會直接輸出；roadmap A3（`-0`）會處理。
- 「!」和括號沒有任何語意，只是被保留下來。NOT 與括號屬於 roadmap Phase C/D，需要 AST。

## Version Recommendation

- **Recommended version：0.4.1（patch）。** 這一輪修的是錯誤的接受行為與既有 bug，沒有新增能力。
- 這一輪**沒有**改版本號、沒有建立 tag、沒有 release，由人類 review 後決定。

## Recommended Next Milestone

**A2 — CI（GitHub Actions，ubuntu-latest 加上 macos-latest）。** 理由：

- 所有 Linux runtime 至今都是 NOT RUN。
- CI 是之後每個 milestone 的 safety net。

之後依 roadmap 做 A3（`-0` / `--null`）。
