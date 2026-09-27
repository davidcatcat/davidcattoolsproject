# NLF Absolute Date Comparators Report

日期：2026-09-28
範圍：`tools/nlf`，新增絕對日期的單邊比較：以前、以後、或以前、或以後、before、after、on or before、on or after
狀態：已實作並驗證；review 後已 commit 並 push 到 main。版本號仍是 0.3.0，沒有建立或移動 tag

## Summary

- 新增四種比較方式：`before`、`on or before`、`after`、`on or after`，以及對應的中文 `以前 / 之前`、`或以前 / 以前含當日`、`以後 / 之後`、`或以後 / 以後含當日`。
- 比較的對象可以是日期、月份或年份，三者共用同一套規則：先把日曆單位換成 `[start, end)`，比較用語只決定取哪一邊。
- 結果只寫進既有的 `ModifiedAfter` / `ModifiedBefore`。search 的時間模型（`ModifiedAfter <= mtime < ModifiedBefore`）沒有改，**`search.go` 一行都沒動**。
- `after Sep 1` 從 **Sep 2** 00:00 開始；`since Sep 1` 與 `on or after Sep 1` 從 Sep 1 00:00 開始。`since` 的行為維持不變。
- 比較的對象如果是時刻，或比較之後又接區間、缺少比較對象，一律 exit 2，不去猜。
- 可以和 Boolean OR、檔名、內容、大小、副檔名自然組合。`on or before X or on or after Y` 中，比較用語裡的 `or` 不會被誤當成 Boolean OR。

## Baseline

- 版本：**v0.3.0**（`nlf --version` 輸出 `nlf 0.3.0`）。`main` 位於 `102edff`，和 `origin/main` 同步；tag `tools/nlf/v0.2.0`、`tools/nlf/v0.3.0` 都已 push。
- fmt 乾淨、vet ok、test / race PASS、build ok、`make check` ok。
- 共 **641** 個 test/subtest。

## Previous Behavior

| 輸入 | v0.3.0 的行為 |
|---|---|
| `before Sep 1`、`before 2025` | 報錯：must be followed by a number and unit / number needs a unit（`before` 只被當成相對時間的比較詞） |
| `after Sep 1`、`on or before Sep 1`、`on or after Sep 1` | 報錯：看不懂 `after` / `on` |
| `2026-09-01 以前`、`9 月 1 日以前` | 報錯：看不懂「以前」 |
| `9 月以後`、`2025 年以前` | 報錯 |
| **`9 月以前`、`3 月以前`** | **默默被解讀成相對時間**「超過 270 / 90 天」（mtime < 2026-01-01 04:27），而不是「9 月之前」 |
| `before 30 days`、`3 天以前`、`3 月前` | 相對時間（這一輪維持不變） |

最後一類「默默誤解」正是這一輪要修正的，詳見 Backward Compatibility。

## Calendar Span Model

沿用 date/time 那一輪的 `span`：

| 單位 | span `[start, end)` |
|---|---|
| 日（`2026-09-01`、`9 月 1 日`、`Sep 1`、`yesterday`） | `[9/1 00:00, 9/2 00:00)` |
| 月（`9 月`、`September`、`2026-09`、`本月`） | `[9/1 00:00, 10/1 00:00)` |
| 年（`2025 年`、`2025`（跟在比較詞後面時）、`去年`） | `[2025-01-01, 2026-01-01)` |

span 一律用本機時區的 `time.Date` 與 `AddDate` 計算，不用 24 小時的倍數。比較用語只決定取 `start` 或 `end`，不另外針對日、月、年寫特例。

## Comparator Semantics

實作位置：`internal/query/datetime.go` 的 `absExpr`（英文前綴）、`dateExpr` 裡的中文後綴，以及 `applyAbs`。

| 比較 | 設定 | 以 Sep 1 為例 |
|---|---|---|
| before X | `ModifiedBefore = X.start` | mtime < 9/1 00:00 |
| on or before X | `ModifiedBefore = X.end` | mtime < 9/2 00:00 |
| after X | `ModifiedAfter = X.end` | mtime >= 9/2 00:00 |
| on or after X | `ModifiedAfter = X.start` | mtime >= 9/1 00:00 |

設定的方式和其他時間條件相同，都透過 `p.after()` / `p.before()` 取交集。因此 AND 組合、矛盾檢查、`timeSatisfiable` 全部自動適用。

## Before

- 中文：`2026-09-01 以前`、`2026-09-01 之前`、`9 月 1 日以前`、`9 月以前`、`2025 年以前`
- 英文：`before Sep 1`、`before September 1`、`before 2026-09-01`、`before 9/1`、`before September`、`before 2025`
- 語意：mtime < span.start，不含當天、當月、當年。

`before` 在 v0.3.0 本來就是相對時間的比較詞（`before 30 days`）。這一輪改成依上下文判斷，不靠解析順序碰運氣：

- `before` 後面接得出日曆單位（日期、月份、4 位數年份、`yesterday` 等）→ 絕對比較。
- 後面是其他數字（例如 `30 days`）→ 交回原本的相對時間 comparator，行為不變。
- 其他情況（`before`、`before foo`、`before now`）→ 報錯：`"before" must be followed by a date, month or year`。

## On or Before

- 中文：`2026-09-01 或以前`、`2026-09-01 或之前`、`2026-09-01 以前含當日`、`之前含當日`、`9 月或以前`、`2025 年或以前`
- 英文：`on or before Sep 1`、`on or before 2026-09-01`、`on or before September`、`on or before 2025`
- 語意：mtime < span.end，包含整個當天、當月、當年。

`on or before` 是一個完整的詞組，在條件內部用最長比對先吃掉，不會被拆成 `on` / `or` / `before`。`或以前`、`或以後` 是字典裡的單一詞，不會被切成 `或` 加上 `以前`。

## After

- 中文：`2026-09-01 以後`、`2026-09-01 之後`、`9 月 1 日以後`、`9 月以後`、`2025 年以後`
- 英文：`after Sep 1`、`after September 1`、`after 2026-09-01`、`after September`、`after 2025`
- 語意：mtime >= span.end，不含當天、當月、當年：`after Sep 1` 從 Sep 2 00:00 開始、`after September` 從 10/1 開始、`after 2025` 從 2026-01-01 開始。
- 沒有使用 `Sep 1 00:00 + 1ns`，也沒有把 after 當成 `>= Sep 1`。

## On or After

- 中文：`2026-09-01 或以後`、`2026-09-01 或之後`、`2026-09-01 以後含當日`、`之後含當日`、`9 月或以後`、`2025 年或以後`
- 英文：`on or after Sep 1`、`on or after 2026-09-01`、`on or after September`、`on or after 2025`
- 語意：mtime >= span.start。

## Since vs After

| 查詢 | 下界 |
|---|---|
| `since Sep 1`、`從 9 月 1 日`、`從 9 月 1 日開始`、`9 月 1 日以來` | Sep 1 00:00（不變） |
| `on or after Sep 1` | Sep 1 00:00（和 since 相同） |
| `after Sep 1` | **Sep 2** 00:00 |

README 用表格和一條規則明確寫出：`since Sep 1` 等於 `on or after Sep 1`，但不等於 `after Sep 1`。`--explain` 也會直接顯示實際的邊界：

- `after 2026-09-02 00:00:00 (mtime >= 2026-09-02 00:00:00)`
- `after 2026-09-01 00:00:00 (mtime >= 2026-09-01 00:00:00)`

## Day / Month / Year

三者走同一條程式路徑，差別只在 span（沒寫年份的月份使用今年）：

| | before | on or before | after | on or after |
|---|---|---|---|---|
| Sep 1 | < 9/1 | < 9/2 | >= 9/2 | >= 9/1 |
| September | < 9/1 | < 10/1 | >= 10/1 | >= 9/1 |
| 2025 | < 2025-01-01 | < 2026-01-01 | >= 2026-01-01 | >= 2025-01-01 |

## Boolean OR Interaction

絕對比較就是一般的 `KindTime` 條件，所以可以直接組成時間的 OR 群組：

| 查詢 | 結果 |
|---|---|
| `找 9 月 1 日以前或 9 月 20 日以後修改的 log` | `.log AND (mtime < 9/1 OR mtime >= 9/21)` |
| `before Sep 1 or after Sep 20` | `mtime < 9/1 OR mtime >= 9/21`（after Sep 20 從 9/21 開始） |
| `on or before Sep 1 or on or after Sep 20` | `mtime < 9/2 OR mtime >= 9/20`；兩個比較詞裡的 `or` 都被比較詞吃掉，只有中間的 `or` 是 Boolean OR |
| `before September or after October` | `mtime < 9/1 OR mtime >= 11/1` |
| `9 月 1 日或以前或 9 月 20 日或以後` | `mtime < 9/2 OR mtime >= 9/20` |
| `today or before Sep 1` | 日曆日期和比較可以放在同一個 OR 群組 |
| `before Sep 1 or larger than 1GB` | 報錯：不同類型的 OR |
| `on or before Sep 1 or or after Sep 20` | 報錯：「或」重複了 |

既有的比較用語中的 `or`（`10MB or more`、`1GB or smaller`、`3 days or older`、`10MB 或以上`、`1KB 或以下`）都有 regression 測試，全部維持不變。

## Filename Search Interaction

v0.3.0 的檔名搜尋行為沒有改變，以下組合都有測試：

- `找 2026-09-01 以後修改且檔名包含 config 的 yml` → `.yaml/.yml AND name contains "config" AND mtime >= 9/2`
- `找 9 月以前、檔名包含 backup 的 sql`
- `找 2025 年以前、檔名以 .bak 結尾的檔案`
- `找檔名是 nginx.conf 或 httpd.conf，2026-09-01 以後修改` → 檔名 OR AND 比較

## Boundary Tests

`TestAbsoluteComparatorBoundaries`（search 層，使用真實檔案與 `os.Chtimes` 設定的奈秒 mtime）：

- 建立 11 個檔案，mtime 分別落在日、月、年邊界「剛好在邊界上」和「邊界前 1ns」。
- 測試會先確認檔案系統確實保留奈秒精度（APFS 會保留），所以這個測試有實際執行，沒有被 skip。
- 共 16 個查詢，全部 PASS：

| 查詢 | 邊界前 1ns | 剛好在邊界上 |
|---|---|---|
| before Sep 1 | 8/31 23:59:59.999999999 ✓ | 9/1 00:00 ✗ |
| after Sep 1 | 9/1 23:59:59.999999999 ✗ | 9/2 00:00 ✓ |
| on or before Sep 1 | 9/1 23:59:59.999999999 ✓ | 9/2 00:00 ✗ |
| on or after Sep 1 | 8/31 23:59:59.999999999 ✗ | 9/1 00:00 ✓ |
| before September | 8/31 last ✓ | 9/1 ✗ |
| after September | 9/30 last ✗ | 10/1 ✓ |
| on or before September | 9/30 last ✓ | 10/1 ✗ |
| on or after September（`9 月或以後`） | — | 9/1 ✓ |
| before 2025 | 2024-12-31 last ✓ | 2025-01-01 ✗ |
| after 2025 | 2025-12-31 last ✗ | 2026-01-01 ✓ |
| on or before 2025 | 2025-12-31 last ✓ | 2026-01-01 ✗ |
| on or after 2025（`2025 年或以後`） | — | 2025-01-01 ✓ |

另外還驗證了 `since Sep 1`（與 on or after 相同）、中文的 `以前` / `以後`，以及 `before Sep 1 or after Sep 30`。

`TestAbsoluteComparatorsEndToEnd`（CLI）使用需求中的 `before.txt`（8/31 23:59:59）、`boundary.txt`（9/1 00:00）、`during.txt`（9/1 12:00）、`after.txt`（9/2 00:00）：

| 查詢 | 結果 |
|---|---|
| `before Sep 1` | before |
| `on or before Sep 1` | before、boundary、during |
| `after Sep 1` | after |
| `on or after Sep 1`、`since Sep 1` | boundary、during、after |

## DST Tests

`TestAbsoluteComparatorsAcrossDST`（America/New_York，使用 `time/tzdata`，不依賴主機的 zoneinfo）：

- `on or before 2026-03-08` 與 `after 2026-03-08`：邊界是 3/9 的本機午夜。3/8 是 DST 開始日，只有 23 小時；測試也驗證了這個前提。
- `on or before 2026-11-01` 與 `after 2026-11-01`：邊界是 11/2 的午夜。11/1 是 DST 結束日，有 25 小時。
- `on or before March`：邊界是 4/1 的午夜。
- 每個邊界都必須是本機時間的 00:00，而不是「開始時間加 24 小時」。

## Invalid Input Handling

以下全部 exit 2，而且不會走訪檔案系統：

| 輸入 | 錯誤 |
|---|---|
| `before`、`after`、`on or before`、`on or after`、`after foo`、`before now` | `"<比較詞>" must be followed by a date, month or year` |
| `以前`、`之後`、`或以前`、`或以後`（前面沒有日期） | 看不懂這個詞 |
| `before 14:00`、`after 3pm`、`after 2026-09-01 14:00` | `before/after compare against a date, month or year, not a time of day` |
| `下午 3 點以前`、`今天 3 點以前` | 單邊的日期比較只支援日期、月份或年份，不支援時刻 |
| `before 2026-02-29`、`after 2026-13-01`、`on or before 2026-04-31`、`2026-02-29 以前`、`2026-13-01 以後`、`13 月以前` | invalid date / 日期不存在 / 月份不存在（不會被 `time.Date` 自動修正成其他日期） |
| `after Sep 1 to Sep 10`、`9 月 1 日以後到 9 月 10 日` | 比較之後不能再接時間範圍 |
| `9 月 20 日以後且 9 月 10 日以前`、`after 2025 and before 2025`、`before Sep 1 and after Sep 1`、`9 月 1 日以前且 9 月` | 修改時間條件互相矛盾（在解析時就報錯） |

「日期加時刻」的比較（例如 `after 2026-09-01 14:00`）是需求中的 optional 項目。這一輪**沒有**實作，會明確報錯，列為之後的強化項目。

## Files Changed

| 檔案 | 變更 |
|---|---|
| `internal/query/datetime.go` | 新增 `absCmp`、`absPrefix`、`absPostfix`、`applyAbs`、`absExpr`；在 `dateExpr` 中加入中文後綴；`postfixAhead` 不再把「以前 / 之前」當成月份、年份之後的相對時間後綴 |
| `internal/query/lexicon.go` | 中文比較詞加入字典（`或以前`、`以前含當日` 等），用最長比對確保不會被切開 |
| `internal/query/parse.go` | 英文比較詞加入保留字 |
| `internal/query/absolute_test.go` | **新增** |
| `internal/query/datetime_test.go` | 移除一筆舊的「不支援」期望（見 Backward Compatibility） |
| `internal/search/search_test.go` | 新增 `TestAbsoluteComparatorBoundaries` |
| `main.go` | help：新增一行（`找 2026-09-01 以前修改的檔案`、`files modified on or after Sep 1`）；兩條「加引號」的 tips 合併成一行，help 維持 70 行、最寬 100 欄 |
| `main_test.go` | 新增 `TestAbsoluteComparatorsEndToEnd`；`TestHelpContent` 加入新的例子 |
| `testdata/help.golden` | help snapshot 更新 |
| `README.md` | 「日期與時間」一節新增「絕對日期比較」小節，以及日期表格的「單邊比較」列；更新 Behavior details 與 Known limitations |
| `internal/search/search.go` | **沒有修改** |
| `reports/nlf/2026-09-28-absolute-date-comparators.md` | 本報告 |

## Tests Added

測試總數：641 → **759**，全部 PASS，0 FAIL，0 SKIP。新增 119 個，移除 1 個舊的期望。

| 測試 | 內容 |
|---|---|
| `TestAbsoluteDateComparators`（66 例） | 需求中所有中文與英文的寫法（日、月、年 × 四種比較）；`yesterday` / `today` / `本月` / `去年` 作為比較對象；兩個比較的 AND；比較和相對時間的交集；`since` 與 `on or after` 相同；不變的相對時間（`before 30 days`、`before 3 days`、`older than`、`ago`、`3 天以前`、`3 月前`、`3 days or older`、`from … until now`） |
| `TestAbsoluteComparatorsCombine`（16 例） | 四種 OR（包含 `on or before … or on or after …`）；日曆日期 OR 比較；與檔名、內容、大小、副檔名、檔名 OR 的組合；既有比較用語中 `or` 的 regression |
| `TestAbsoluteComparatorErrors`（30 例） | 缺少比較對象（中英文）；時刻（5 種）；`before now`；不合法的日期與月份；比較之後接區間；矛盾（4 種）；OR 類型不同、結尾的 OR、重複的 OR |
| `TestAbsoluteComparatorsAcrossDST` | New York 的 DST 開始日與結束日，以及月份 |
| `TestExplainShowsAbsoluteBound` | `--explain` 直接顯示 before / after / on or after / on or before 的邊界，以及兩個比較的 AND |
| `TestAbsoluteComparatorBoundaries`（search，16 個查詢） | 奈秒邊界：日、月、年 × 四種比較、since、中文、OR |
| `TestAbsoluteComparatorsEndToEnd`（CLI） | 11 個查詢；5 個不合法查詢 exit 2；`--explain` / `--confirm` 顯示邊界；Safety 仍是 READ-ONLY |

## Runtime Verification

binary build 到 mktemp；now 為 2026-09-28 CST。以下 `--explain` 查詢都已實際執行，結果完全符合規格：

| 查詢 | 修改時間 |
|---|---|
| `找 2026-09-01 以前修改的檔案` | `早於 2026-09-01 00:00:00 (mtime < 2026-09-01 00:00:00)` |
| `找 2026-09-01 以後修改的 yml` | `.yaml/.yml`；`晚於 2026-09-02 00:00:00 (mtime >= 2026-09-02 00:00:00)` |
| `找 2026-09-01 或以前修改的 log` | `mtime < 2026-09-02 00:00:00` |
| `找 2026-09-01 或以後修改的 conf` | `mtime >= 2026-09-01 00:00:00` |
| `before Sep 1` / `after Sep 1` | `mtime < 2026-09-01` / `mtime >= 2026-09-02` |
| `on or before Sep 1` / `on or after Sep 1` | `mtime < 2026-09-02` / `mtime >= 2026-09-01` |
| `找 9 月 1 日以前或 9 月 20 日以後修改的 log` | 任一區間（OR）：`mtime < 2026-09-01`；`mtime >= 2026-09-21` |
| `找 2026-09-01 以後修改且檔名包含 config 的 yml` | `.yaml/.yml`；檔名包含 `"config"`；`mtime >= 2026-09-02` |

acceptance 1–20 另外也全部執行過。例如：

- #15 `9 月 1 日或以後且 9 月 15 日或以前` → `2026-09-01 00:00:00 <= mtime < 2026-09-16 00:00:00`
- #16 → exit 2（矛盾）
- #18 `before September or after October` → `< 9/1` 或 `>= 11/1`
- #20 `找 2025 年以前且大於 1GB 的 log` → `mtime < 2025-01-01`、`> 1 GB`、`.log`

`--confirm` 顯示同一組 normalized 邊界，Safety 仍是 READ-ONLY。

## Backward Compatibility

- 既有的 641 個 test/subtest 中，有 640 個**沒有任何修改**，全部通過。
- 唯一被移除的是 `datetime_test.go` 的 `{"找 9 月 1 日之前的檔案", "", "看不懂這個詞"}`。它的註解就是「before/after a date are not supported yet」，這一輪正式支援這個語法，所以同一個輸入改成在 `TestAbsoluteDateComparators` 中測試正向結果（mtime < 9/1）。
- **行為變更（請 review）：** `N 月以前`、`N 月之前`（例如 `9 月以前`、`3 月之前`）從相對時間「超過 N×30 天」改成日曆比較「N 月開始之前」。
  - 理由：需求明確要求 `9 月以前` = before September。舊的解讀會默默把「9 月以前」變成「9 個月以前」，正是需要修正的 silent misread。
  - 相對時間仍然可以寫：`3 月前`（只有一個「前」）、`3 個月前`、`90 天前`，行為都不變，也都有測試。README 已說明這個差異。
- `N 年以前` 只有 4 位數年份（`2025 年以前`）是日曆比較；`2 年以前` 仍然是相對時間，因為年份必須是 4 位數。
- 特別確認沒有 regression 的項目（都有測試）：
  - today、yesterday、前天、2026-09-28、2026 年 9 月、9 月 1 日到 9 月 15 日、昨天 23:00 到今天 02:00、最近 6 小時
  - since、from … until now、last month、in the last month
  - OR（today or yesterday、ERROR or FATAL、大小、檔名、時間）
  - 檔名搜尋的 5 種比對方式
  - 比較用語中的 `or`
- `search.go` 沒有修改。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨 |
| `go vet ./...` | ok |
| `go test ./...` | PASS（759 個，0 FAIL，0 SKIP） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| `make dist` | 4 個平台的 **`nlf-0.3.0-*`** 都 build 成功（這一輪沒有 bump 版本），`SHA256SUMS` 驗證全部 OK，`bin/nlf --version` 輸出 `nlf 0.3.0`。`bin/` 裡另有先前 build 留下的 0.1.0 / 0.2.0 執行檔，`bin/` 已被 gitignore |
| Linux | `CGO_ENABLED=0 GOOS=linux GOARCH=amd64/arm64` 都 build 成功；`go vet` 通過；3 個套件的測試都能 `go test -c` 編譯。**沒有在 Linux 實機上執行** |
| staticcheck | skipped（未安裝） |

## Version Recommendation

建議下一版是 **0.4.0**：絕對日期比較是新的查詢能力，而且改變了 `N 月以前` 的解讀方式。

這一輪**沒有**修改版本號（仍是 0.3.0），也沒有建立、移動或 push 任何 tag。

## Known Limitations

- 不支援「日期加時刻」的單邊比較（`after 2026-09-01 14:00`），會報錯。
- 不支援只有時刻的比較（`before 14:00`、`下午 3 點以前`），會報錯，因為無法確定是哪一天。
- 單獨一個「前」加在完整日期後面（`9 月 1 日前`）沒有支援，會報錯；請寫 `以前` 或 `之前`。
- 英文不支援 `earlier than`、`prior to`、`or earlier`、`or later`、`until Sep 1`（當成單邊比較）等其他說法，會報錯。
- 比較之後不能接區間；區間請用 `A 到 B`，或兩個比較加上「且」。
- 英文的 `before` 後面如果接的是非年份的數字（`before 30 days`），仍然是相對時間。這是為了維持既有行為。
- 近似的 find 指令用 `-newermt` 表示邊界，find 的比較是「嚴格晚於」，和 nlf 的 `>=` 在剛好等於邊界時略有差異。這只影響近似指令，實際行為以 nlf 為準。

## Recommended Next Steps

1. **Review 行為變更：** `N 月以前 / 之前` 改成日曆比較，`N 月前` 與 `N 個月前` 仍然是相對時間。
2. **Review 語意：** `after` 不含當天、`on or after` 等於 `since`。
3. 通過後 release **0.4.0**：修改版本號、更新 help snapshot 與 README 的 binary 名稱，並建立 `tools/nlf/v0.4.0` tag。
4. 之後可以考慮支援「日期加時刻」的單邊比較。建議語意：`after 2026-09-01 14:00` 是 mtime >= 14:00，也就是把時刻當成單一瞬間，不再有「隔天」的問題。
5. 在 Linux 實機上執行 `go test ./...`。
