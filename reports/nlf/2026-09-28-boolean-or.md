# NLF Boolean OR Report

日期：2026-09-28
範圍：`tools/nlf`，新增 Boolean OR（單層、同類條件的 OR 群組）
狀態：已實作並驗證；review 後已 commit 並 push 到 main

## Summary

- 可以用「或 / 或者 / 或是 / or」連接**同一類**的條件：副檔名、內容、修改時間、檔案大小。
- OR 只把「或」前後相鄰的兩個條件組成群組，群組再和其他所有條件做 AND。沒有其他優先順序規則，不需要猜。
- 不同類的條件用 OR 連接時，一律報錯：「目前 OR 只能連接同類條件」/ `OR currently requires conditions of the same type`，並附上提示。
- 不完整的 OR 也報錯，包括前面沒有條件、後面沒有條件、連續出現兩次。
- 資料模型：在 `Query` 上新增有型別的 `AnyOf []OrGroup`，沒有導入完整的 AST。
- 搜尋層：每個檔案只 `Stat` 一次，內容只讀一次，所有 AND 條件與 OR 群組都在同一次走訪中判斷。
- `--explain` / `--confirm` 會明確標示 OR，近似的 find 指令用 `\( … -o … \)` 和 `grep -e A -e B` 表示。
- 所有既有測試都保留並通過。只有兩個原本期望「OR 不支援」的測試，依照新語意調整（見 Backward Compatibility）。

## Previous Behavior

Baseline（修改前）：fmt 乾淨、vet ok、test / race PASS、build ok、`make check` ok，共 **400** 個 test/subtest。

| 輸入 | 修改前 |
|---|---|
| `找今天或昨天修改的檔案`、`找包含 "ERROR" 或 "FATAL" 的 log`、`找 8 月或 9 月修改的 yml 檔`、`找大於 1GB 或小於 1KB 的檔案` | 報錯：「v0.1 還不支援「或」條件」 |
| `找 go 或 yml 或 json 檔`、`go or md files` | 可以用。這是副檔名清單，本來就是 any-of |
| `10MB or more`、`1GB or smaller`、`3 days or older`、`exclude vendor or build` | 可以用。`or` 在這些例子中是比較用語的一部分，或是排除清單的分隔詞 |

修改前的結構：

- `step()` 碰到獨立出現的 `或` / `or` 就報錯。
- `Query` 只有 AND 語意的欄位：`Contents` 全部都要包含、`Sizes` 全部都要成立、單一的 `ModifiedAfter`/`ModifiedBefore` 區間。唯一的例外是 `Extensions`，它是 any-of。
- `search.Run` 對每個檔案依序檢查副檔名、大小、時間、內容。內容由 `containsAll` 判斷，語意是 AND。

## Query Model

### 兩種方案比較

| | A. 完整 AST（`And{}`/`Or{}`/`ExtCond`…） | B. `Query` + 有型別的 `AnyOf []OrGroup`（**採用**） |
|---|---|---|
| 對既有程式的影響 | parser、search、explain、所有 `Query` 相關測試都要改寫 | 既有欄位與語意完全不變；沒有 OR 時 `AnyOf` 是 nil，400 個既有測試的 `Query` 形狀不變 |
| search 如何判斷 | 需要遞迴走訪 AST | 固定一層：AND 欄位，加上每個群組至少一個成立 |
| 能表達的語意 | 任意巢狀、NOT、混合類型的 OR | 單層、同類 OR，剛好是這一輪的範圍 |
| explain / confirm | 要做一般化的 AST 列印 | 每種群組一種固定的顯示格式，容易讀 |
| 風險 | 大幅重寫，容易出現 regression | 範圍小，可以逐項驗證 |

### 採用的模型（`internal/query/query.go`）

```go
type CondKind int // KindExtension, KindContent, KindTime, KindSize

type TimeRange struct{ After, Before time.Time } // After <= mtime < Before；零值代表沒有這一邊的邊界

type OrGroup struct {
    Kind     CondKind
    Contents []string    // KindContent：至少包含其中一個
    Times    []TimeRange // KindTime：mtime 至少落在其中一個區間
    Sizes    []SizeCond  // KindSize：至少符合其中一個
}

type Query struct {
    ...                  // 既有欄位：語意不變，彼此之間是 AND
    AnyOf []OrGroup      // 每個群組都必須成立（AND），群組內任一個成立即可（OR）
}
```

副檔名的 OR 不需要群組，因為 `Query.Extensions` 本來就是 any-of。`go 或 md 檔` 和 `go 檔或 md 檔` 都合併進 `Extensions`，表示方式和以前完全一樣。

### 之後升級成 AST 的 trade-off

- `OrGroup` 的欄位對應到將來 AST 的葉節點（`ContentCond`、`TimeCond`、`SizeCond`），`AnyOf` 對應到頂層 `And` 底下的 `Or`。之後要支援「不同類的 OR」或 NOT 時，可以把 `Query` 轉成 AST，現有的 `Query` 就是「AND of leaves + AND of same-kind ORs」這個特例。
- parser 已經改成「每個條件先 parse 到獨立的 sub-query，再合併」（見下節）。這個結構本身就是產生 AST 的基礎，升級時 parser 不需要大改。
- 代價是屆時 search 與 explain 要改成遞迴判斷與遞迴顯示；現在的固定一層結構會成為其中一種情況。

## OR Semantics

- 文法：`條件 (或 條件)*`。OR 的兩邊都必須是**完整的單一條件**，而且是**同一類**。
- 實作（`internal/query/boolean.go`）：
  - `parseAll` 把每個條件 parse 到一個全新的 sub-query（`parseInto`），依照出現順序收集成 item。
  - 碰到 OR 詞時，把**前一個** item 和**下一個**條件一起放進同一個 OR 群組。
  - `classify` 判斷每個 sub-query 的類型。兩邊類型不同，或其中一邊不是可以 OR 的條件（排除、排序、數量、搜尋位置、不分大小寫）時，就報錯。
  - 最後依序把所有 item 合併進 `Query`。一般條件的合併沿用既有的 `after()`/`before()` 交集與 append，所以 AND 的行為和以前完全相同。
- 比較用語中的 `or`（`or more`、`or smaller`、`or older`、`或以上`、`或以下`）、副檔名清單（`go 或 md`），以及排除清單（`exclude vendor or build`），都在條件內部用最長比對先吃掉，所以不會走到 OR 的處理。
- 內容 OR 的右邊可以是沒有引號的單字：`包含 ERROR 或 FATAL`。數字不算，例如 `包含 ERROR 或 404` 會報錯，要加引號。
- 時間 OR 是多個區間的**聯集**，從不合併。`1 月或 9 月` 就是兩個區間。
- 沒寫日期的時刻，會沿用前一個區間的日期：`昨天 1 點到 3 點或 5 點到 6 點` 的兩段都在昨天。

## AND / OR Composition

規則只有一條：**OR 只綁定「或」兩旁的條件，其他全部是 AND。**

| 查詢 | 解析結果 |
|---|---|
| `找今天或昨天修改的 log` | `.log AND (today OR yesterday)`，不會變成 `today OR (yesterday AND .log)` |
| `找包含 "ERROR" 或 "FATAL" 的 log` | `.log AND ("ERROR" OR "FATAL")` |
| `找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log` | `(today OR yesterday) AND ("ERROR" OR "FATAL") AND .log` |
| `找 8 月或 9 月修改，大於 10MB 的 log` | `(Aug OR Sep) AND size > 10MB AND .log` |
| `find log files containing "ERROR" or "panic" modified today or yesterday` | `.log AND ("ERROR" OR "panic") AND (today OR yesterday)` |
| `包含 "A" 或 "B" 且包含 "C"` | `"C" AND ("A" OR "B")` |
| `包含 "A" 且包含 "B"`、`包含 "A" 和 "B"` | `"A" AND "B"`（不變） |
| `找今天或昨天修改，9 月的檔案` | `(today OR yesterday) AND September` |
| `找今天或昨天修改，8 月的檔案` | 沒有交集，報「修改時間條件互相矛盾」 |

## Supported OR Types

| 類型 | 例子 | 表示方式 |
|---|---|---|
| 副檔名 | `go 或 md 檔`、`go or md files`、`go 或 yml 或 json 檔`、`go 或者 md 檔`、`go 檔或 md 檔` | `Extensions`（any-of） |
| 內容 | `包含 "ERROR" 或 "FATAL"`、`包含 "ERROR" 或包含 "FATAL"`、`containing "foo" or "bar"`、三個以上的詞、`包含 ERROR 或 FATAL` | `OrGroup{KindContent}` |
| 日期 / 時間 | `今天或昨天`、`8 月或 9 月（或 10 月）`、`本月或上個月`、`2026-09-01 或 2026-09-15`、`today or yesterday`、`in August or September`、`今天 01:00 到 03:00 或 05:00 到 06:00`（選做，已實作）、`最近 1 小時或昨天` | `OrGroup{KindTime}` |
| 大小 | `大於 1GB 或小於 1KB`、`larger than 1GB or smaller than 1KB` | `OrGroup{KindSize}` |

## Unsupported OR Cases

以下全部 exit 2，不會搜尋：

| 輸入 | 錯誤 |
|---|---|
| `找大於 1GB 或最近 1 小時修改的檔案`、`大於 1GB 或今天` | 目前 OR 只能連接同類條件（大小 vs 時間） |
| `找包含 "ERROR" 或 9 月修改的 log` | 同上（內容 vs 時間） |
| `pdf 或大於 10MB`、`go files or larger than 1k` | 同上（副檔名 vs 大小） |
| `排除 vendor 或 今天`、`最大的 5 個 或 最新的 3 個`、`top 5 or top 10`、`包含 "A" 不分大小寫 或 "B"` | 同上；排除、排序、數量、不分大小寫都不能用 OR |
| `今天 或`、`files modified today or` | 「或」後面需要條件 |
| `或 今天`、`or today`、`找 或 今天` | 「或」前面需要條件 |
| `今天 或 或 昨天`、`today or or yesterday` | 「或」重複了 |
| `包含 ERROR 或 404` | 數字需要單位（數字不會被當成搜尋文字，要加引號） |
| `找今天或 9/20 到 9/10 的檔案`、`包含 "ERROR" 或 ""` | OR 的每一邊仍然套用原本的規則（開始必須早於結束、不能是空字串） |
| NOT、括號、巢狀 OR | 不支援（這一輪不做） |

## Search Evaluation

`internal/search/search.go`，對每個檔案：

```
match = matchExt(name)                         // Extensions：any-of
     && matchSize(size, Sizes)                 // AND
     && matchTime(mtime, After, Before)        // AND
     && matchGroups(size, mtime, AnyOf)        // 每個大小 / 時間群組至少一個成立
     && matchContent(path, Contents, AnyOf)    // Contents 全部包含，且每個內容群組至少包含一個
```

- 走訪一次檔案系統。每個檔案的 `size`、`mtime` 只從 `d.Info()` 取一次，所有大小條件、時間條件和群組都用這兩個值判斷。有 N 個日期區間時，也只是在記憶體裡迴圈比對 N 次，不會多做 Stat。
- 內容：舊的 `containsAll` 改寫成共用的 `scanTerms`。
  - 一次開檔、一次 streaming，同時比對所有詞（AND 的詞和每個 OR 群組的詞），記錄每個詞是否出現過。
  - 條件一滿足就提早結束。
  - binary 判斷（第一個 chunk 出現 NUL 就略過）、不分大小寫、跨 chunk 保留尾端，全部沿用原本的邏輯。原本的內容測試都通過。
- 排序、數量上限、排除目錄、權限處理，都沒有改動。

## Files Changed

| 檔案 | 變更 |
|---|---|
| `internal/query/boolean.go` | **新增**。`parseAll`、`alternative`、`parseInto`、`classify`、`merge`、`sameDay`、`TimeRange.overlaps`，以及錯誤訊息 |
| `internal/query/query.go` | 新增 `CondKind`、`TimeRange`（含 `Match`）、`OrGroup`、`Query.AnyOf`；更新 `Query` 的語意註解 |
| `internal/query/parse.go` | `Parse` 改用 `parseAll`；新增 OR 時間群組和其他時間條件的交集檢查；移除 `step()` 裡的 OR 報錯分支；`parser` 新增 `clockBase` 欄位 |
| `internal/query/datetime.go` | 沒寫日期的時刻，在 OR 的右邊會使用 `clockBase` |
| `internal/query/explain.go` | 內容、大小、時間群組的顯示；多行的條件值；抽出 `rangeText`、`sizeArgs`、`timeArgs`、`anyOf`、`displayWidth`；find 指令產生 `\( … -o … \)` 與 `grep -e … -e …` |
| `internal/search/search.go` | `matchGroups`、`matchContent`、`scanTerms`（取代 `containsAll`）、`hasContent` |
| `internal/query/boolean_test.go` | **新增** |
| `internal/query/parse_test.go` | 兩個既有測試改成新語意（見 Backward Compatibility） |
| `internal/search/search_test.go` | 新增 `TestOrGroups`、`TestSizeOrGroup`、`TestContentOrScan` |
| `main.go` | help：新增 `or` 那一列，更新 Not supported，Search root 合併成一行（help 維持 70 行） |
| `main_test.go` | 新增 `TestOrEndToEnd`；`TestHelpContent` 加入 OR 的內容；`TestParseErrorExitCode` 改成期望新的錯誤訊息 |
| `testdata/help.golden` | help snapshot 更新 |
| `README.md` | 新增「Boolean OR」一節（例子、類型表、組合規則、限制）；更新條件說明與 Known limitations |
| `reports/nlf/2026-09-28-boolean-or.md` | 本報告 |

## Tests Added

測試總數：400 → **475**，全部 PASS，0 FAIL，0 SKIP。其中 OR 相關的 test/subtest 有 74 個。

| 測試 | 內容 |
|---|---|
| `TestParseOr`（44 例） | 副檔名 OR（5 例）；內容 OR（含三個詞、沒引號、`或包含`）；內容 AND 不變（`且`、`和`、`and`）；內容 AND + OR 混合；日期 OR（今天/昨天、8/9 月、三個月份、1 月或 9 月不合併、本月/上個月、兩個日期、兩段時刻、沿用昨天的時刻、相對時間 OR 日期）；大小 OR（中文、英文）；多個群組（acceptance 9、10、11）；群組只綁定相鄰的條件；群組 + 排除 + 數量；比較用語 regression（`10MB or more`、`1GB or smaller`、`500 bytes or less`、`3 days or older`、`10MB 或以上`、`1KB 或以下`）；排除清單 `or` / `and`；`between A and B` |
| `TestParseOrErrors`（22 例） | 不同類型（大小 vs 時間、內容 vs 時間、副檔名 vs 大小）；不能 OR 的條件（排除、排序、數量、不分大小寫）；前面沒有、後面沒有、重複；數字不當搜尋文字；群組和其他時間條件沒有交集；OR 的每一邊仍然套用原本的規則 |
| `TestExplainShowsOr` | 中文與英文的內容、時間、大小群組各只顯示成**一列** OR（不會拆成兩列看起來像 AND）；find 指令裡的 `grep -e`、`\( -size … -o … \)`、`\( \( -newermt … \) -o \( … \) \)` |
| `TestTimeRangeMatch` | 半開區間的邊界，以及沒有上界的區間 |
| `TestOrGroups`（search） | 需求第二十五節的 fixture：a–e 五個檔案，驗證 4 個指定組合的結果完全符合；另外驗證英文查詢、不分大小寫的 OR、AND + OR、兩個不連續的日期、不相鄰的日期不會被合併 |
| `TestSizeOrGroup`（search） | 大於 1MB 或小於 1KB（中文、英文）、全部都符合、OR 群組 AND 其他大小條件、都不符合 |
| `TestContentOrScan`（search） | OR 的詞跨 64KB 邊界、OR 的詞在第 3 個 chunk 之後、binary 檔略過、不分大小寫、AND 詞 + OR 群組、兩個群組都必須成立 |
| `TestOrEndToEnd`（CLI） | 5 個 CLI 查詢的結果；4 個不合法查詢 exit 2 且沒有輸出；`--explain` 與 `--confirm` 顯示 OR、只有一列 content、`-e ERROR -e panic`；Safety 仍是 READ-ONLY |

## Runtime Verification

binary build 到 mktemp；fixture 放在新的 mktemp 目錄，用 `touch -t` 設定 mtime。執行時的 now 是 2026-09-28 03:39 CST。

| 查詢 | 結果 |
|---|---|
| `找今天或昨天修改的檔案` | a.log、b.log、big.bin、d.txt、e.log、tiny.bin（不含前天的 c.log、8 月的 mid.bin） |
| `找今天或昨天修改的 log` | a.log、b.log、e.log |
| `找包含 "ERROR" 或 "FATAL" 的 log` | a.log、b.log、c.log |
| `找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log` | a.log、b.log |
| `find log files containing "ERROR" or "panic" modified today or yesterday` | a.log |
| `找大於 1MB 或小於 1KB 的檔案` | big.bin（2 MB）與所有小於 1KB 的檔案；不含 mid.bin（5000 B） |
| `找 8 月或 9 月修改的 bin 檔` | big.bin、mid.bin（8/15）、tiny.bin |
| `找大於 1GB 或最近 1 小時修改的檔案` | exit 2，「目前 OR 只能連接同類條件」 |
| `今天 或` | exit 2，「或」後面需要條件 |

`--explain` 必跑的 5 個查詢都已實際執行，例如：

```
$ nlf --explain '找今天或昨天修改，而且包含 "ERROR" 或 "FATAL" 的 log'
解析結果：
  搜尋位置       .
  副檔名         .log
  內容包含       "ERROR" 或 "FATAL" (符合任一個即可，區分大小寫)
  修改時間       任一區間（OR），符合任一個即可：
                 - 2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00
                 - 2026-09-27 00:00:00 <= mtime < 2026-09-28 00:00:00
  時區           CST (+08:00)，本機時間

近似的 macOS 指令（僅供參考，nlf 不會執行它）：
  find . -type f -iname '*.log' \( \( -newermt '2026-09-28 00:00:00' ! -newermt '2026-09-29 00:00:00' \) -o \( -newermt '2026-09-27 00:00:00' ! -newermt '2026-09-28 00:00:00' \) \) -exec grep -qIF -e ERROR -e FATAL {} \; -print
```

其他 4 個查詢的結果：

- `找包含 "ERROR" 或 "FATAL" 的 log`：一列內容 OR，搭配 `grep -qIF -e ERROR -e FATAL`。
- `找 8 月或 9 月修改的 yml`：兩個月份區間分兩行列出。
- `找大於 1GB 或小於 1KB 的檔案`：`> 1 GB 或 < 1 KB`，搭配 `\( -size +1073741824c -o -size -1024c \)`。
- `找今天或昨天修改的檔案`：兩個日期區間分兩行列出。

## Backward Compatibility

- 原本的 400 個 test/subtest 都保留並通過。只有兩個測試的期望值改成新語意，而且都沒有放寬：
  1. `TestParseErrors`：`larger than 1mb or smaller than 10k` 原本期望「OR 不支援」的錯誤。現在它是支援的大小 OR，所以改成 `TestParse` 的正向案例，並精確比對群組內容。原本錯誤案例的位置，換成一個真正不同類型的 OR（`larger than 1mb or modified today`），仍然必須報錯。
  2. `TestParseErrorExitCode`：`大於 1MB 或 go 檔` 仍然必須 exit 2，只是期望的錯誤訊息從「不支援「或」條件」改成「目前 OR 只能連接同類條件」。
- 特別確認沒有 regression 的項目（都有測試）：
  - 副檔名清單：`go 或 md 檔`、`找出pdf和docx檔案`
  - 比較用語：`or more`、`or smaller`、`or less`、`or older`、`或以上`、`或以下`
  - 排除清單：`exclude vendor and node_modules`、`exclude vendor or build`、`排除 node_modules 跟 .git 目錄`
  - `between A and B`
  - 中文「和」清單、內容 AND（`且` / `和`）
  - 沒有引號的內容解析（`包含 設定檔` 仍然要求加引號）
  - 中文路徑
- 沒有 OR 時，`Query.AnyOf` 是 nil，所以 `Query` 的形狀和以前完全一樣。
- 一個訊息上的差異：同一個查詢指定兩個搜尋位置（例如 `in /a in /b`）時，以前報「看不懂這個詞：/b」，現在報「搜尋位置只能指定一個」。兩者都是 exit 2。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨 |
| `go vet ./...` | ok |
| `go test ./...` | PASS（475 個，0 FAIL，0 SKIP） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| `make dist` | 4 個平台的執行檔都 build 成功；`SHA256SUMS` 驗證全部 OK |
| Linux | `CGO_ENABLED=0 GOOS=linux GOARCH=amd64/arm64 go build` 成功；`go vet` 通過；3 個套件的測試都能 `go test -c` 編譯。**沒有在 Linux 實機上執行** |
| staticcheck | skipped（未安裝） |

## Known Limitations

- 只有單層、同類的 OR。不支援不同類的 OR、NOT、括號、巢狀 OR。
- OR 只綁定「或」兩旁的條件：
  - `今天修改的 log 或 txt` 會被當成副檔名清單，可以正常使用。
  - `今天修改的 log 或昨天` 則是「副檔名 OR 時間」，會報錯，不會自動調整成「今天或昨天」。
- 內容 OR 右邊沒有引號的數字（`包含 ERROR 或 404`）會報錯，要寫成 `"404"`。
- 不分大小寫套用到所有內容詞，包括 AND 的詞和 OR 群組裡的詞。不能只讓其中一個詞不分大小寫。這和既有行為一致。
- 同一個查詢裡的多個副檔名條件本來就是 any-of。`go 檔 ... md 檔` 就算沒有寫「或」，也是「go 或 md」。這是既有行為，沒有改變。
- 近似的 find 指令只是參考：`grep -e` 的 binary 判斷與大小寫比對，和 nlf 本身略有不同（見 README）。
- 錯誤訊息中的原文，是由 token 以空白接起來的，例如 `大於 1 GB 或 最近 1 小時`，和使用者輸入的空白不一定相同。

## Recommended Next Steps

1. **Review 語意決策：**
   - 「OR 只綁定相鄰條件」的規則。
   - `今天修改的 log 或昨天` 報錯，而不是猜測使用者的意思。
   - 沒寫日期的時刻沿用前一個區間的日期。
2. **版本：** 目前是 `0.1.0`，repo 沒有 tag，也沒有版本策略。Boolean OR 是 query model 的明顯功能增加，加上前幾輪的日期搜尋等變更，建議合併後標成 **`0.2.0`**。這一輪沒有更動版本號。
3. **之後支援不同類的 OR 或 NOT 時，** 把 `Query` 轉成 AST（見 Query Model 的 trade-off）。parser 的「sub-query 再合併」結構可以直接沿用。
4. **考慮讓 `今天修改的 log 或昨天` 這類「中間隔著其他條件」的情況，** 提示使用者改寫成 `今天或昨天修改的 log`。
5. **在 Linux 實機上執行** `go test ./...`。
