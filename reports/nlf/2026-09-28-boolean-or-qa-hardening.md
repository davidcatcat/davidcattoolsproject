# NLF Boolean OR QA Hardening Report

- 日期：2026-09-28
- 範圍：`tools/nlf`，修正 Boolean OR 在 QA 中發現的 edge cases
- 基準：`main` 的 `c244507`（c2445078a61ff654e0fc49cadf69ae78c0388eab），以及 [Boolean OR report](2026-09-28-boolean-or.md)
- 狀態：已修正並驗證；review 後已 commit 並 push 到 main
- 限制：這輪沒有改 Boolean OR 的既有產品語意，沒有做 AST、NOT、不同類型的 OR，也沒有動 search layer

## QA Findings

Baseline：fmt 乾淨、vet ok、test / race PASS、build ok、`make check` ok，共 475 個 test/subtest。

三項 QA 發現都先用修正前的 binary 重現過：

| # | 輸入（now = 2026-09-28） | 修正前 | 應該 |
|---|---|---|---|
| 1 | `today or and yesterday`、`today and or yesterday`、`今天 或 且 昨天`、`今天 且 或 昨天`、`今天 或 而且 昨天`、`今天 並且 或 昨天` | 都被解析成 `today OR yesterday`，其中一個 connector 被默默吃掉 | exit 2 |
| 1 | `today or or yesterday`、`今天 或 或 昨天` | 已經會報「「或」重複了」 | 維持 |
| 2 | `找今天或昨天修改，而且 7 月或 8 月修改的檔案` | 通過 parse，實際搜尋後才回報 0 筆 | exit 2，時間條件互相矛盾 |
| 2 | `找 9/1 或 9/15 修改，而且 8 月或 10 月修改的檔案` | 同上 | exit 2 |
| 3 | `昨天 23:00 到今天 02:00 或 05:00 到 06:00` | 第二段默默當成**今天** 05:00–06:00 | exit 2，要求明確指定日期 |
| 3 | `8 月或 5 點到 6 點` | 第二段默默當成今天 | exit 2（同一個問題） |

## Root Cause

1. **Connector collision：**
   - `and` / `且` / `而且` / `並且` / `和` 等 AND 詞本身是 filler 或 list separator，parse 出來是空的條件。
   - `parseAll` 會丟掉空的條件；`alternative()` 在 OR 之後也會「跳過 filler」，所以 AND 詞不論出現在 OR 前面還是後面，都會被默默忽略。
2. **多個 time OR groups：**
   - `Parse()` 只檢查「全域的 `ModifiedAfter`/`ModifiedBefore`」和「每一個 time group」之間有沒有交集。
   - 它沒有檢查 group 和 group 之間，所以每個 group 單獨看都成立，但兩個 group 同時要成立時可能完全沒有交集。
3. **Cross-midnight：**
   - OR 右邊沒寫日期的時刻，會沿用左邊區間的日期（`sameDay()`）。
   - 但左邊不在同一個日曆日時（跨午夜的區間、整個月、相對時間），`sameDay()` 回傳零值，右邊就退回預設的「今天」。

## Connector Collision Fix

位置：`internal/query/boolean.go`

- 新增明確的 `andConnectors` 清單：`and`、`且`、`而且`、`並且`/`并且`、`並`/`并`、`同時`/`同时`、`和`、`跟`、`與`/`与`、`及`。
- 以 token 比對，不做字串特例。比對用的是 parser 既有的 `matchAt`，所以只會比到完整的 token。
- 兩個檢查點：
  - **OR 之前：** 在 `parseAll` 中，檢查左邊條件結束（`item.to`）到 OR 詞之間的 token。只要有 AND connector 就報錯（`today and or yesterday`）。
  - **OR 之後：** 在 `alternative()` 跳過 filler 之前，先檢查目前的 token 是不是 AND connector（`today or and yesterday`）。
- 錯誤訊息：
  - 中文：「OR 和 AND 不能連續使用」，提示「只用其中一個，例如 今天或昨天」。
  - 英文：`OR and AND cannot be used together without a condition between them`，hint 為 `use one of them, e.g. today or yesterday`。
- 沒有禁止 OR 之後的其他 filler：`today or modified yesterday` 仍然合法。
- 不受影響（都有測試）：
  - 條件內部的比較用語與清單：`10MB or more`、`1GB or smaller`、`3 days or older`、`10MB 或以上`、`exclude vendor and build`、`exclude vendor or build`、`go and md files`、`between Sep 1 and Sep 15`。它們都在條件內部用最長比對先吃掉，根本不會走到 OR 的處理。
  - 單純的 AND：`today and yesterday`、`今天且昨天`。它們最後會報「時間條件互相矛盾」，但**不會**報 connector 錯誤。
  - 內容的 AND 與 OR：`包含 "A" 且包含 "B"`、`包含 "A" 或 "B"`、`包含 "A" 或 "B"，而且包含 "C"`。

## Time OR Group Contradiction Validation

位置：`internal/query/boolean.go` 的 `timeSatisfiable`，由 `Parse()` 呼叫，取代原本逐一 group 的 overlap 檢查。

```
可滿足  ⇔  [After, Before) ∩ (g1.r1 ∪ g1.r2 ∪ …) ∩ (g2.r1 ∪ …) ∩ … ≠ ∅
```

- 從全域區間開始，逐一和每個 time group 相交。
- 每一步都保留「目前可能成立的區間清單」，和 group 的每個區間兩兩求交集（`TimeRange.intersect`）。
- 任一步變成空集合，就報「修改時間條件互相矛盾，不可能有檔案符合」/ `modification time conditions contradict each other`。
- union 永遠保留成區間清單，不會 flatten。例如 `(1 月 OR 9 月) AND 5 月` 會正確判定為矛盾，不會把 1 月到 9 月當成一個大區間。
- 這只是 validation，search 的資料結構和判斷方式都沒有改。

| 查詢 | 結果 |
|---|---|
| `(today OR yesterday) AND (September OR August)` | 合法（今天、昨天都在 9 月） |
| `(today OR yesterday) AND (July OR August)` | 矛盾，exit 2 |
| `(Sep 1 OR Sep 15) AND (September OR October)` | 合法 |
| `(Sep 1 OR Sep 15) AND (August OR October)` | 矛盾，exit 2 |
| `(1 月 OR 9 月) AND 9 月`（全域界線） | 合法 |
| `(今天 OR 昨天) AND (9 月 OR 8 月) AND 7 月` | 矛盾（全域界線加上兩個 group） |

## Cross-midnight OR Handling

位置：`internal/query/boolean.go` 的 `alternative()`，以及 `internal/query/datetime.go` 的 `point()`。

- `point()` 在「區間的開頭只有時刻、沒有日期」時，設定 `p.clockOnly`。
- 在 `alternative()` 中，如果左邊的時間區間**不在同一個日曆日內**（`sameDay()` 為零），而右邊的開頭又只有時刻，就報錯，不再退回「今天」：
  - 中文：「前一個時間區間跨日，下一個 OR 時間區間需要明確指定日期」，提示「例如 昨天 23:00 到今天 02:00 或 今天 05:00 到 06:00」。
  - 英文：`the previous time range is not within one day, so the next OR time range needs an explicit date`，並附對應的 hint。
- 「不在同一日內」包括跨午夜的區間、整個月（`8 月`），以及沒有上界的相對時間（`最近 6 小時`）。這三種情況都沒有單一日期可以沿用，所以同樣拒絕，不去猜。

| 查詢 | 結果 |
|---|---|
| `昨天 1 點到 3 點或 5 點到 6 點` | 合法，兩段都在昨天（保留原本行為） |
| `昨天 1:00 到 3:00 或 5:00 到 6:00` | 合法，同上 |
| `今天或 5 點到 6 點` | 合法，今天是單一日，5–6 點在今天 |
| `昨天 23:00 到今天 02:00 或 05:00 到 06:00` | exit 2 |
| `昨天 23:00 到今天 02:00 或 今天 05:00 到 06:00` | 合法 |
| `昨天 23:00 到今天 02:00 或 昨天 20:00 到 21:00` | 合法 |
| `8 月或 5 點到 6 點`、`最近 6 小時或 1 點到 2 點` | exit 2 |

## Tests Added

測試總數：475 → **517**，新增 42 個，全部 PASS，0 FAIL，0 SKIP。

| 測試 | 內容 |
|---|---|
| `TestOrQAHardening`（`internal/query/boolean_test.go`，39 個 subtest） | **應報錯（19 例）：** 中英文的 `or and`、`and or`、`or or`；`或 且`、`且 或`、`或 或`、`或 而且`、`並且 或`；`和或` 連寫；內容的 `且 或`；大小的 `and or`；三組 time group 矛盾（中文、英文、1 月或 9 月不 flatten）；全域界線加上兩個 group；三種跨日後接只有時刻的右邊（跨午夜、整個月、相對時間）。<br>**應合法（20 例）：** `today or yesterday`、`今天或昨天`、`today or modified yesterday`；兩組有交集的 time group；全域界線 + group；同日的只有時刻右邊（`點` 與 `:` 兩種寫法）；跨午夜後的右邊明確寫今天或昨天；`今天或 5 點到 6 點`；內容 AND、內容 AND+OR；`exclude vendor and/or build`；`go and md files`；`10MB or more`；`10MB 或以上`；`between … and …`。 |
| `TestOrQAHardening` 的額外斷言 | `today and yesterday` 與 `今天且昨天` 必須是時間矛盾錯誤，而**不是** connector 錯誤 |
| `TestTimeSatisfiable`（11 例） | 沒有 group、一個 group、只有一對區間相交、完全不相交、union 不 flatten、全域界線排除全部、全域界線保留一段、三個 group、沒有上界的區間相交或分開、size group 不影響時間判斷 |
| `TestOrQAHardeningExitCodes`（`main_test.go`） | 4 種不合法查詢經過 CLI 時 exit 2、沒有輸出、有錯誤訊息；單純的 `today or yesterday` 不是 parse error |

## Backward Compatibility

- 既有的 475 個 test/subtest **沒有任何修改**，全部通過。所有測試檔的變更都只有新增：`git diff` 中測試檔被移除的行數是 0。
- `search.go` 沒有改動。Size / Time / Content OR、多個 content group、AND+OR 組合、single-pass 內容掃描、chunk boundary、binary、ignore-case 的行為都和以前一樣。
- 產品語意沒有改變。唯一的行為差異，就是上述三類原本會被默默接受或默默猜測的輸入，現在改成 exit 2。
- Production 程式的變更：
  - `boolean.go`：`andConnectors`、兩處 connector 檢查、`errConnectors`、cross-day 檢查、`item.to`、`timeSatisfiable`、`TimeRange.intersect`（取代 `overlaps`）。
  - `parse.go`：改呼叫 `timeSatisfiable`；新增 `clockOnly` 欄位。
  - `datetime.go`：設定 `clockOnly`，新增 3 行。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨 |
| `go vet ./...` | ok |
| `go test ./...` | PASS（517 個，0 FAIL，0 SKIP） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| `make dist` | 4 個平台的執行檔都 build 成功；`SHA256SUMS` 驗證全部 OK |
| Linux build | `CGO_ENABLED=0 GOOS=linux GOARCH=amd64/arm64` 都 build 成功；沒有在 Linux 實機上執行 |
| staticcheck | skipped（未安裝） |

## Remaining Limitations

- `timeSatisfiable` 逐層兩兩求交集，區間數量最多是各 group 大小的乘積。實際查詢只會有少數幾個 group、每個 group 幾個區間，成本可以忽略，但沒有設上限。
- Cross-day 的判斷一律拒絕「左邊不在同一日、右邊只有時刻」，包括整個月和相對時間。如果之後希望 `最近 6 小時或 1 點到 2 點` 也能用，需要先定義右邊應該沿用哪一天。
- `andConnectors` 是手動維護的清單。之後新增 AND 詞時，要同步加進這個清單，否則那個詞在 OR 旁邊會被當成一般 filler 跳過。
- 錯誤訊息中的原文是由 token 以空白接起來的（例如 `「或 且」`），和使用者實際輸入的空白不一定相同。
- 前一份 Boolean OR report 列出的限制（單層、同類 OR；沒有 NOT 與括號等）仍然適用。
