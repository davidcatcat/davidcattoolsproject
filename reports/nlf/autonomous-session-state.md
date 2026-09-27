# NLF Autonomous Session State

> 本次 autonomous session 唯一的正式 checkpoint。恢復工作時請先讀這份檔案，再確認 git 的實際狀態。
> 若 checkpoint 與 repo 不一致，以 repo 實際狀態為準，先更新這份檔案再繼續。

Status: IN PROGRESS
Last updated: 2026-09-28，A1 design 定稿（Architecture APPROVE WITH CHANGES）

## Session

- **Purpose：** audit nlf → gap analysis → master roadmap → 自選 Phase A1 → 多 agent 實作、QA、review → commit 並 push feature branch →（可選）PR → 停下來等人類 review。**不 merge main。**
- **Baseline main SHA：** `91ee959537230b60e253fa52d34dd6dc290bd85a`（origin/main，nlf v0.4.0）
- **Baseline version：** 0.4.0
- **Baseline test count：** 759 PASS / 0 FAIL / 0 SKIP
- **Feature branch：** `feat/nlf-roadmap-a1-20260928`，從 origin/main 建立。為避免誤 push 到 main，已執行 `git branch --unset-upstream`。
- **Current branch HEAD：** `91ee959`（尚未 commit）
- **Worktree path：** `<session scratchpad>/nlf-a1-worktree`（Lead 的 session scratchpad 目錄；請用 `git worktree list` 查詢實際路徑）`
- **Main checkout：** `~/git/davidcataiporject/davidcattoolsproject`，停在 main，乾淨，**沒有碰過**。
- **Isolation：** git worktree
- **Agent orchestration：** SUBAGENTS USED
- **Main touched by this task：** NO

## Phase / Milestone

- **Current phase：** A1 implementation（Implementation Agent）
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

- A1 design 已定稿。下一步：啟動 Implementation Agent。

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

- Baseline：759 PASS（見上）。A1 還沒有開始寫程式。

## Latest QA results

- 還沒開始。

## Validation status（A1）

| 項目 | 狀態 |
|---|---|
| go fmt ./... | NOT RUN |
| go vet ./... | NOT RUN |
| go test ./... | NOT RUN |
| go test -race ./... | NOT RUN |
| go test ./... -count=10 | NOT RUN |
| go build ./... | NOT RUN |
| make check | NOT RUN |
| make dist | NOT RUN |
| Linux build | NOT RUN |
| Linux runtime | NOT RUN（沒有 Linux 環境） |

## Known failures / blockers

- 無。

## Exact next action

在 worktree 中啟動 Implementation Agent，依照上方已定稿的 design 實作 A1，並加入測試與 README 修改；不可有 git 寫入。完成後交給 QA Agent。

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

- **Committed：** NO
- **Branch pushed：** NO
- **PR URL：** none
- **Merge to main：** NOT PERFORMED
