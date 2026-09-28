# NLF Autonomous Session State

> 本次 autonomous session 唯一的正式 checkpoint。恢復工作時請先讀這份檔案，再確認 git 的實際狀態。
> 若 checkpoint 與 repo 不一致，以 repo 實際狀態為準，先更新這份檔案再繼續。

Status: COMPLETE
Last updated: 2026-09-28，session 完成，等待人類 review

## Session

- **Purpose：** audit nlf → gap analysis → master roadmap → 自選 Phase A1 → 多 agent 實作、QA、review → commit 並 push feature branch →（可選）PR → 停下來等人類 review。**不 merge main。**
- **Baseline main SHA：** `91ee959537230b60e253fa52d34dd6dc290bd85a`（origin/main，nlf v0.4.0）
- **Baseline version：** 0.4.0
- **Baseline test count：** 759 PASS / 0 FAIL / 0 SKIP
- **Feature branch：** `feat/nlf-roadmap-a1-20260928`，從 origin/main 建立。為避免誤 push 到 main，已執行 `git branch --unset-upstream`。
- **Current branch HEAD：** `10521a1`（A1）。本 checkpoint 的 COMPLETE 更新會再多一個 docs commit，確切 SHA 以 `git log` 為準
- **Worktree path：** `<session scratchpad>/nlf-a1-worktree`（Lead 的 session scratchpad 目錄；請用 `git worktree list` 查詢實際路徑）
- **Main checkout：** `~/git/davidcataiporject/davidcattoolsproject`，停在 main，乾淨，**沒有碰過**。
- **Isolation：** git worktree
- **Agent orchestration：** SUBAGENTS USED
- **Main touched by this task：** NO

## Phase / Milestone

- **Current phase：** COMPLETE。等待人類 review；A2 及後續項目不在本輪範圍，未開始
- **Current milestone：** A1 = Punctuation safety hardening，建議版本 0.4.1（patch）

## Completed work

1. **Git safety check：** main checkout 乾淨；沒有 merge、rebase、cherry-pick 進行中；沒有 stash；fetch 之後 local main 等於 origin/main。
2. **建立隔離環境：** 建立 worktree 與 feature branch，並取消 upstream。
3. **Baseline validation（在 worktree 中）：**

   | 項目 | 結果 |
   |---|---|
   | go fmt | PASS |
   | vet | PASS |
   | test | PASS |
   | race | PASS |
   | count=10 | PASS |
   | build | PASS |
   | make check | PASS |
   | make dist | PASS（`SHA256SUMS` 驗證 OK） |
   | staticcheck | 未安裝 |
   | CI | 不存在（沒有 `.github/`） |

4. **Architecture Agent audit（read-only，已完成）：** 主要結論如下。
   - 不需要 AST。只有 NOT、mixed-kind OR、括號才需要。
   - 輸出類 flag 只需要改 `main.go`：`parseArgs` 與 `run` 裡的 emit closure。
   - help 目前剛好 70 行，新增一行就必須合併或拿掉另一行。
   - `docExamples` 的 regex 只認得既有的 flag，新 flag 的 README 例子會被默默略過。
   - 已知風險：
     - 單字的 dict 詞會切壞沒有引號的中文值（例如 `包含 日誌`）。
     - 重複的 sort/limit 條件採「最後一個生效」，沒有報錯。
     - 錯誤訊息中仍有 "v0.1" 字樣。
     - 相對路徑沒寫 `./` 時，錯誤訊息很差。
     - root 底下的 symlink 會被默默略過。
     - sort 時會把所有結果放進記憶體。
     - `Query` 新增欄位時，要手動改大約 5 個地方。
5. **Product/Roadmap Agent（已完成）：** 寫出 `reports/nlf/2026-09-28-roadmap.md`（24 個必要章節），評估 47 個 candidate。
   - Phase A：
     - A1 = 標點安全
     - A2 = CI
     - A3 = `-0` / `--null`
     - A4 = `--count` / `-q`
     - A5 = `--max-depth` / `--min-depth`
     - A6 = top-N heap
     - A7 = fuzz 與 hostile filenames
   - 用 binary 實測到的 contract violation（Lead 已獨立重現）：
     - `log files !containing "ERROR"` 會回傳 `err.log`，也就是 `!` 被默默丟掉，意思反過來了。
     - `larger than 1GB or (smaller than 1KB and modified today)` 被重組成 `(>1GB OR <1KB) AND today`。
     - `filename matches core.?` 變成 glob `"core"`。
     - `filename matches app-?.log` 變成 glob `"app-"` 加上副檔名 `.log`。
     - `filename starts with app?` 變成 `"app"`。
     - `filename is core.` 變成 `"core"`。
     - `找 go 檔！` 被默默接受。
     - 否定的錯誤訊息仍寫著 "v0.1"。
   - 其他觀察：
     - 大小條件矛盾時 exit 1，而不是 2。
     - `--dir` 指向一般檔案時會被接受。
     - `syscall` 被 `TestProductionCodeIsReadOnly` 禁止，這會卡住 `--xdev`、owner/group（roadmap B0）。
     - `make build` 沒有設 `CGO_ENABLED=0`。

## Decisions already made

- **A1 = punctuation safety hardening**（Lead 的決定）。
  - 理由：correctness 與 safety 優先；修的是 contract violation，而不是新功能。
  - 風險只朝更保守的方向：原本被接受的查詢改成 exit 2，不會有查詢得到更多或不同的結果。
  - 範圍：只動 tokenizer/parser 的標點處理與錯誤文字；`Query`、search、explain 的結構都不變；不需要 AST。
- A1 之後不做 A2 及後面的項目。本輪只做 roadmap 加 A1。
- 不改版本號、不打 tag。只提出建議版本 0.4.1。

## Important semantic decisions（A1 design，**已定稿**：Architecture Agent 回覆 APPROVE WITH CHANGES，Lead 採納全部修改）

1. **引號外的括號與驚嘆號一律報錯：** `(` `)` `（` `）` `!` `！` 不論出現在哪裡都 exit 2，在 `step()` 的 tPunct 分支檢查。Architecture 已確認這是唯一會默默吞掉這些字元的地方。錯誤訊息為雙語：括號寫「目前不支援括號」；`!` 寫「可能表示否定（NOT），目前不支援」。提示：拿掉，或用引號包住字面文字。
2. **`?` / `？` 只允許在整個輸入的最後一個 token：** 其他位置報錯「只能放在查詢最後」。
3. **標點黏在沒有引號的值後面時報錯：** 也就是 `. ? ! ( ) ？ ！ （ ）` 緊接在值後面、中間沒有空白。
   - 由一個共用 helper 檢查，放在 `nameValue` 與 `contentTerm` 內部，所以 soft-content、content OR、`nameAlternative` 都會自動套用；`excludeNames` 在 `p.pos++` 之後、`accept(excludeTail)` 之前檢查。只檢查沒有引號的值。
   - **規則 3 優先於規則 2：** `包含 錯誤？`、`files containing TODO?` 都會報錯，提示同時列出兩種寫法：`包含 "錯誤"` 或 `包含 "錯誤？"`。
   - 錯誤訊息的 token 包含整段黏在一起的標點，例如 `「core.?」`。
   - 分隔符號 `, ; 、 ， 。 ； ：` 維持原本行為。
   - 已知的不對稱：黏在值後面的 `。` 可以接受，`？` 則不行。要寫進文件。
4. **Tokenizer：單獨一個 `.` 且前面沒有空白時，變成 `.` 標點 token。**
   - 修正 `"x".` 被當成路徑 `.`、`錯誤.` 與 `設定.` 被併進值裡的問題；之後就會落入規則 3。
   - 前面有空白的 `.`（`in .`、`在 .`）仍然是路徑。
   - `excludeNames` 遇到名稱是 `.` 或 `..` 時報錯。
5. **移除 "v0.1" 字樣：**
   - 英文改成 "... not supported yet"，才能保留既有測試的 substring：`negative conditions are not supported`、`unclear whether this excludes a directory`、`can only exclude directories`、`looks like a file type`。
   - 要改的位置：`step` 的 NOT 錯誤、`excludeNames`、`parse.go` 約 236、241、265 行（"negation is not supported in v0.1"、「（v0.1 不支援否定條件）」）、README 約 369 行（「v0.1 會報錯」）、README 標題 "Known limitations (v0.1)"。`lexicon.go:168` 的註解可改可不改。
6. **不在本輪範圍：** 路徑值的結尾點（`在 ./src.`）、副檔名值、賦予 `!` 或括號任何意義。
7. **`--explain` / `--confirm`：** 這些都是 parse error，exit 2，不會印出 plan 或提示。

## In-progress work

- **Implementation 完成（尚未 commit，在 worktree 中）。** Implementation Agent 回報、Lead 也獨立驗證過：
  - 修改的檔案：`internal/query/{tokenize,parse,filename}.go`、`README.md`、`main_test.go`，以及新增的 `internal/query/punctuation_test.go`。
  - 沒有動到的檔案：`query.go`、`explain.go`、`internal/search/`。
  - 新增的 helper：`reservedPunct`（規則 1、2，在 `step()` 裡）、`rejectGluedPunct` 與 `gluedPunct`（規則 3，在 `nameValue`、`contentTerm`、`excludeNames` 裡）、`rawBetween`（產生提示文字）。
  - tokenizer 的修改：黏在前一個 token 的單獨 `.` 變成 tPunct。
  - 其他：exclude `.` / `..` 會報錯；v0.1 字樣已移除，英文改成 "... not supported yet"。
  - 與 design 的差異：錯誤訊息沿用既有的 `Error.Token` 格式，也就是「message：「core.?」」。
  - 已知的小瑕疵：
    - `檔名以 app? 開頭` 的提示缺少「開頭」。
    - `包含 錯誤。？` 會解析成內容 `錯誤`，因為 `。` 不在 glued 的字元集合裡，而 `？` 是最後一個 token。沒有測試固定這個結果。
  - 測試：863 PASS（759 個既有 + 104 個新增），0 FAIL，0 SKIP。既有測試檔被移除的行數是 0。
  - 驗證：gofmt、vet、test、race、build 都乾淨，由 Implementation Agent 執行，Lead 也重跑過 vet 與 test。
- **QA Agent 已啟動**（背景執行）。

## Remaining work

1. 依照 Architecture review 定稿 design，寫進 checkpoint。
2. Implementation Agent 實作 A1，並加入 unit、CLI 與 runtime 測試，以及 README 修改。
3. QA Agent 進行 adversarial 測試 → 修正 → 加 regression 測試。
4. Review Agent 檢查 `git diff origin/main...HEAD`。
5. Full validation。
6. 寫 A1 report：`reports/nlf/2026-09-28-punctuation-safety.md`。
7. Release/Git Safety Agent 檢查 staging。
8. commit：先 roadmap，再 A1，必要時再加 QA 修正。
9. push feature branch（`git push -u origin feat/nlf-roadmap-a1-20260928`）。**絕不 push main。**
10. 可選：建立目標為 main 的 PR，不 merge、不開 auto-merge。
11. 把 checkpoint 更新成 COMPLETE。

## Files changed（未 commit）

- `reports/nlf/2026-09-28-roadmap.md`（新增，Product Agent 撰寫；Lead 尚未完整 review 內容）
- `reports/nlf/autonomous-session-state.md`（本檔）

## Files not yet reviewed

- roadmap：**Lead 已 review**（Baseline、Principles、Phase A、Phase B、Rejected Ideas 皆正確，並與 A1 決策一致）。Review Agent 之後還會連同 A1 的 diff 再看一次。

## Latest test results

- Baseline：759 PASS。A1 實作完成後 863 PASS；QA 修正完成後 933 PASS；加上 Lead 的路徑修正後，全部 PASS（final count 在 full validation 時記錄）。0 FAIL，0 SKIP。

## Latest QA results

QA 第一輪完成。結論：**PASS WITH ISSUES**。與 HEAD 對照 1,415 個查詢字串，除了刻意新增的 reject 之外，沒有其他 regression。

**QA completed：**

- 規則 1 到 5 的所有位置與組合，包括引號、日期、OR、路徑、大小與數量、全形字元，以及 100k 字元的輸入。
- `--explain` 與 `--confirm` 遇到被拒絕的查詢時：exit 2，沒有 stdout，不印 plan 也不詢問。
- 以真實搜尋抽查既有功能：結果與 HEAD 相同。

**Bugs found → Lead 的決定：**

1. **HIGH：** 黏在前面的 `..`（`找 go 檔..`、`"x"..`）會變成 root `..`，改去搜尋上層目錄，屬於 silent widening。HEAD 就已經存在。→ **本輪修正**：tokenizer 把前面沒有空白、而且全部由點組成的一段字元，當成標點。
2. **MEDIUM：** `包含 錯誤..`、`檔名是 設定..` 繞過規則 3。→ 修正第 1 項時一併解決。
3. **MEDIUM：** `exclude ./`、`"./"`、`../`、`排除 ./`、`exclude /` 被接受，但實際上什麼都沒排除。原因是 `TrimSuffix` 在 `.` / `..` 檢查之後才執行。→ **修正**：對 trim 之後的名稱做檢查，空名稱也報錯。
4. **MEDIUM：** `*.log?`、`.log?`、`副檔名 heic?`、`.go or .md?` 的 `?` 被默默丟掉，像 glob 的副檔名因此比對出不同的結果。→ **修正**：副檔名值後面緊接 glued punctuation 時報錯，並提示改用 `filename matches "*.log?"`。
5. **LOW：** 外觀相似的標點（`﹖ ﹗ ‼ ⁇ ⁉ ﹙ ﹚ …`）會被默默併進沒有引號的值。→ **本輪不修**，寫進 Known Limitations。
6. **LOW：**
   - 6a：wrapped 形式的檔名提示會給出不合法的查詢（`檔名以 "app"` 少了「開頭」；`rest` 會吞掉 boundary word）。→ **修正**：`rest` 遇到 boundary 就停，並補上結尾詞。
   - 6b：OR 沿用比對方式時，提示裡沒有比對方式。→ 本輪不修，寫進文件。
7. **LOW：** 提示文字不精確。`!` 的提示用了括號的例子；位於中間的 `?` 一律給 glob 的提示。→ **修正文字。**
8. **LOW：**
   - 8a：只有標點的查詢（`?`、`？`、`,`、`。`、`.?`）會搜尋全部檔案，但空查詢會報錯。→ **修正**：全部都是標點的輸入，視為空查詢。
   - 8b：`exclude src, vendor, go files` 會把後面的名稱變成副檔名。這是既有的 parser 問題，與標點無關。→ 留給 roadmap（defer）。
   - 8c：含換行的檔名直接輸出。→ 屬於 roadmap A3（`-0`）。

**Bugs fixed（Implementation Agent，Lead 已用 binary 逐一 re-verify）：** 第 1、2、3、4、6a、7、8a 項。

- 第 1 項：tokenizer 把前面沒有空白、全部由點組成的一段字元，當成多個 `.` 標點。
- 第 3 項：新增 `checkExcludeName`，對 trim 之後的名稱做 `path.Clean` 檢查，空名稱也報錯。
- 第 4 項：`extension()` 改為回傳 `(bool, error)`，並新增 `rejectGluedExtPunct`。
- 第 8a 項：在 `Parse` 中，全部都是標點的輸入視為空查詢。filler-only 的查詢（`find`、`and`）維持原本行為：exit 0、列出全部檔案，測試已固定這個結果。
- 第 6a 項：`rejectGluedPunct` 新增 wrapped 參數，提示會附上結尾詞。
- 第 7 項：提示文字已修正。

**Lead 追加修正（Implementation Agent 回報的既有 bug）：** `classifyASCII` 會把路徑結尾的 `.` 剝掉，因此 `in ../..` 變成 root `../.`，少了一層；`in ~/..` 則變成 home。

- 修正：路徑以 `/.` 或 `/..` 結尾時，不剝結尾的點。
- 以真實搜尋驗證：從 `a/b/src` 搜尋 `../..`，找到 `a/up.go`。
- 新增測試：`TestPathDotComponents`、`TestParentPathRoot`。
- 副作用：`exclude src/..` 現在會報錯（Clean 之後就是搜尋位置本身），不再被默默接受。

**Regression tests added：**

- QA 修正（Implementation Agent 加入）：`TestGluedDotRuns`、`TestExcludeNothing`、`TestGluedExtensionPunctuation`、`TestPunctuationOnlyQuery`、`TestWrappedFilenameHints`、`TestReservedPunctuationHints`、`TestPunctuationQARegressionsEndToEnd`，共 70 個 PASS 項目。
- Lead 追加修正：`TestPathDotComponents`、`TestParentPathRoot`。

**Deferred（寫進 A1 report 的 Known Limitations）：**

- 外觀相似的標點。
- OR 沿用比對方式時，提示裡沒有比對方式。
- `exclude a, b, go files` 會把後面的名稱變成副檔名。
- 含換行的檔名直接輸出（roadmap A3）。
- `containing TODO..` 的提示只剝掉一個點。

**Next QA action：** 無，除非 Review 發現新的問題。

## Review results

**APPROVE WITH CHANGES**（Review Agent，重新執行的那一次；第一次因 usage limit 中斷）：

- **S1：** 保留 `/.` 與 `/..` 的規則套用到所有字，導致 `包含 a/.` 保留了句號。→ **Lead 已修正**：只有路徑保留，並新增 `rejectDotSuffix`。
- **S2：** `exclude vendor/.` 的意思默默改變。→ 現在是規則 3 的錯誤，並有測試固定。
- **Nits 已處理：** 把重複的 loop 抽成共用的 `gluedPunctRun` helper；更新 tPunct 的註解；修正 `TestParentPathRoot` 的註解；roadmap 第 5 行與行數（約 3,000 行）。
- **沒有處理的 nits**（在 A1 report 中列為限制或 cosmetic）：
  - `?` 的提示重複了主訊息。
  - 路徑中的 `?` 提示寫成 glob 的例子。
  - `app-?.log` 的提示提供了 `"app-"`。
  - `exclude /` 的訊息寫成「不能是空的」。
- 新增測試：`TestDotComponentOnlyForPaths`。

## Validation status（A1，final）

| 項目 | 結果 |
|---|---|
| go fmt ./... | PASS |
| go vet ./... | PASS |
| go test ./... | PASS（936，0 FAIL，0 SKIP） |
| go test -race ./... | PASS |
| go test ./... -count=10 | PASS |
| go build ./... | PASS |
| make check | PASS |
| make dist | PASS（nlf-0.4.0-*，SHA256SUMS OK，Linux 版本為靜態 ELF） |
| Linux build | PASS（另外 vet 與 test -c 也通過） |
| Linux runtime | NOT RUN |
| macOS runtime | PASS |

A1 report：`reports/nlf/2026-09-28-punctuation-safety.md`（其中引用的錯誤與提示文字都已用 binary 核對過）。

## Known failures / blockers

- 無。紀錄：第一次 review 因 usage limit 中斷。恢復後重新確認：HEAD `a1030cc`，A1 變更仍未 commit，go test PASS，`origin/main` 仍是 `91ee959`，main checkout 乾淨。

## Exact next action

**本輪沒有下一步。** 請人類 review：

1. 在 GitHub 開 PR（`gh` 沒有安裝，所以沒有自動建立）：https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a1-20260928 ，source 是 `feat/nlf-roadmap-a1-20260928`，target 是 `main`。
2. Review roadmap 與 A1。
3. 決定是否 merge，以及是否 release 0.4.1（建議版本）。

若要繼續做下一個 milestone，建議是 A2（CI），並且要從新的 origin/main 另外開 branch。

## Commands to run next

``bash
cd <worktree>
git status --short --branch
git rev-parse HEAD
git diff
``

完成實作後，在 `tools/nlf` 中執行：

``bash
gofmt -l .
go vet ./...
go test ./...
go test -race ./...
go test ./... -count=10
go build ./...
make check
make dist
``

## Git state

- **Committed：** YES。
  - `a1030cc docs: add nlf product roadmap and session checkpoint`
  - `10521a1 nlf: harden punctuation handling (A1)`
  - 另有一個把本 checkpoint 標為 COMPLETE 的 docs commit。
- **Branch pushed：** YES（`origin/feat/nlf-roadmap-a1-20260928`；`-u` 只追蹤 feature branch 自己）。
- **PR URL：** NOT CREATED（`gh` 未安裝）。Compare 連結：https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a1-20260928
- **QA status：** PASS WITH ISSUES → 所有接受的問題都已修正並加上 regression test；deferred 的項目列在 A1 report。
- **Review status：** APPROVE WITH CHANGES → S1、S2 已修正。
- **Final test results：** 936 PASS，0 FAIL，0 SKIP；race、count=10、make check、make dist 都 PASS；Linux build PASS；Linux runtime NOT RUN；macOS runtime PASS。
- **origin/main：** push 前後都是 `91ee959537230b60e253fa52d34dd6dc290bd85a`。
- **Tags：** 沒有新增或移動（v0.2.0、v0.3.0、v0.4.0）。
- **Main touched by this task：** NO
- **Merge to main：** NOT PERFORMED
