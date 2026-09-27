# NLF Date & Time Search Report

日期：2026-09-28
範圍：`tools/nlf`，新增日期與時間區間搜尋
狀態：已實作並驗證，**沒有 commit、沒有 push**，等待 review

## Summary

- nlf 現在可以用中文或英文描述「某年、某月、某日、某個日期區間、某段時間」內修改過的檔案，也可以用「今天／本月／去年」這類日曆簡稱。
- 所有日期與時間寫法都轉成同一個模型：`ModifiedAfter <= mtime < ModifiedBefore`。
- 實作集中在 parser 的一個新檔案 `internal/query/datetime.go`，只透過既有的 `after()` / `before()` 設定邊界。search 層沒有任何修改，它原本就實作 `after <= mtime < before`。
- 搜尋仍然只用 Go 讀取 `FileInfo.ModTime()`，和作業系統無關，不依賴 `find -newermt`、`date` 或 shell。
- `--explain` / `--confirm` 會把區間顯示成 `2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00`，並多一列時區（例如 `CST (+08:00)，本機時間`）。
- 看不懂或不合法的日期、時間、區間一律報錯（exit 2），不會自動修正、交換或忽略。

## Baseline（修改前）

| 項目 | 結果 |
|---|---|
| `go fmt` / `gofmt -l` | 乾淨 |
| `go vet` | ok |
| `go test ./...` | PASS（210 個 test/subtest） |
| `go test -race ./...` | PASS |
| `go build ./...` | ok |

修改前，這次 17 個 acceptance case 中只有 3 個能用：

- `找今天改過的 yml 檔`：可以用，但只有下界，沒有上界。
- `找昨天修改的 conf 檔`：可以用。
- `找最近 30 分鐘修改的 yml`：可以用。

其他 14 個都失敗。例如 `2026 年` 被當成「2026 年的時間長度」並報「時間範圍太大」；`9 月` 報「時間需要說明方向」；`2026-09-28`、`23:00`、`凌晨`、`本月`、`從` 都是「看不懂這個詞」；`conf` 在沒有「檔」的情況下無法辨識。

## Time Model

```
start <= mtime < end        （時區：執行 nlf 當下的本機時區，也就是 Query.Now 的 location）
```

| 單位 | 計算方式 | 例子 |
|---|---|---|
| 年 | `time.Date(y,1,1,0,0,0,0,loc)` 到 `AddDate(1,0,0)` | `2026 年` 為 `[2026-01-01, 2027-01-01)` |
| 月 | `time.Date(y,m,1,…)` 到 `AddDate(0,1,0)` | `2026 年 9 月` 為 `[2026-09-01, 2026-10-01)` |
| 日 | `time.Date(y,m,d,…)` 到 `AddDate(0,0,1)` | `2026-09-28` 為 `[2026-09-28, 2026-09-29)` |
| 時刻 | `time.Date(y,m,d,h,min,0,0,loc)` | `01:00` |
| 相對時間 | `now - duration`（沿用既有邏輯） | `最近 6 小時` 為 `[now-6h, 無上限)` |

- 所有日曆單位都用 `time.Date` 或 `AddDate` 計算，從不使用「天數 × 24 小時」，所以夏令時間切換日的長度會是 23 或 25 小時，但邊界仍然在午夜（有測試）。
- 不使用 `23:59:59.999999999` 這種「一天結尾」的寫法；上界一律是「下一個單位的開始」，而且不包含上界本身。
- 區間的規則：
  - `A 到 B`：從 A 的開始，到 B 的結束。
  - B 是**日期**時，包含 B 那一整天：`9/1 到 9/15` 為 `[9/1 00:00, 9/16 00:00)`。
  - B 是**時刻**時，不包含那一刻：`1 點到 5 點` 為 `[01:00, 05:00)`。
  - B 是 `現在` / `now` 時沒有上界，和 `since` 相同。
- 相對時間與 `since` 只設下界，沒有上界，和既有行為一致。
- 同一個查詢裡有多個時間條件時，取交集；交集為空時報錯。這沿用既有的 `after()` / `before()` 與矛盾檢查。

## Supported Syntax

**year**
- 中文：`2026 年`、`今年`、`去年`
- 英文：`in 2026`、`from 2025`、`during 2026`、`this year`、`last year`
- 英文的純數字年份必須跟在介系詞後面；`2026 files` 仍然是「最多 2026 個」。

**month**
- 中文：`2026 年 9 月`、`2026年9月`、`9 月`、`九月`、`十二月`、`今年 9 月`、`去年 9 月`、`本月`、`這個月`、`上個月`
- 數字格式：`2026-09`、`2026/09`
- 英文：`September 2026`、`Sep`、`September`、`September this year`、`September last year`、`this month`、`last month`
- 月份可以寫成 1–12、01–12、一月到十二月、January 到 December、Jan 到 Dec，另外也接受 Sept。

**date**
- 數字格式：`2026-09-28`、`2026/09/28`、`2026-9-8`、`9/28`（固定是月/日）
- 中文：`2026 年 9 月 28 日`、`2026 年 9 月 28 號`、`9 月 28 日`、`今天`、`今日`、`昨天`、`前天`
- 英文：`Sep 28 2026`、`Sep 28, 2026`、`September 28`、`today`、`yesterday`、`the day before yesterday`

**date range**
- 連接方式：`A 到 B`、`A 至 B`、`從 A 到 B`、`from A to B`、`A through B`、`between A and B`
- 例子：`9 月 1 日到 9 月 15 日`、`9 月 1 日到 15 日`（結束沿用開始的月份）、`9/1 到 9/15`、`2025 年到 2026 年`、`8 月到 9 月`

**time range**
- 例子：`今天 2 點到 4 點`、`今天 1:00 到 3:30`、`昨天 23:00 到今天 02:00`、`2026-09-28 01:00 到 03:30`、`9 月 28 日凌晨 1 點到 5 點`、`凌晨 1 點到 5 點`（沒寫日期時是今天）、`下午 1 點到 5 點`（結束沿用「下午」）、`晚上 10 點到 11 點半`、`昨天下午 3 點 15 分到 4 點`
- 英文例子：`today between 2am and 4am`、`between 11:30pm and 11:45 pm`、`yesterday from 23:00 to today 02:00`、`on 2026-09-28 between 01:00 and 03:30`
- 時刻格式：
  - `HH:MM`、`H:MM`
  - `2am`、`2 pm`、`11:30pm`
  - `凌晨`、`早上`、`上午`、`中午`、`下午`、`晚上` 加上 `N 點 [M 分]` 或 `N 點半`

**open-ended**
- `從 9 月 1 日到現在`、`從 9 月 1 日`、`9 月 1 日以來`、`從 9 月 1 日開始`、`since September 1`、`from Sep 1 until now`、`since yesterday 23:00`

**relative time**（沿用既有邏輯，這次確認可用）
- 中文：`最近 1 小時`、`最近 6 小時`、`最近 30 分鐘`、`過去 2 天`
- 英文：`last hour`、`last 6 hours`、`last 30 minutes`、`past 30 minutes`、`in the last 2 days`、`in the last month`、`last week`

**calendar shortcuts**
- `今天`、`昨天`、`前天`、`本月`、`這個月`、`上個月`、`今年`、`去年`
- `today`、`yesterday`、`the day before yesterday`、`this month`、`last month`、`this year`、`last year`

## DevOps Use Cases

以 now = 2026-09-28、時區 CST（+08:00）為例；每一列都有對應的測試，也實際用 CLI 跑過。

| # | 查詢 | 解析結果 |
|---|---|---|
| 1 | `找今天改過的 yml 檔` | `.yaml`/`.yml`，`[09-28, 09-29)` |
| 2 | `找昨天修改的 conf 檔` | `.conf`，`[09-27, 09-28)` |
| 3 | `找 2026 年 9 月修改的 pdf 檔` | `.pdf`，`[09-01, 10-01)` |
| 4 | `找 9 月修改的 yml 檔` | 今年，`[09-01, 10-01)` |
| 5 | `查 2026 年 9 月 28 日改了什麼檔案` | `[09-28, 09-29)` |
| 6 | `找 2026-09-28 修改的 yaml` | `[09-28, 09-29)` |
| 7 | `找 9 月 20 日到 9 月 28 日修改的 log` | `[09-20, 09-29)` |
| 8 | `找昨天 23:00 到今天 02:00 修改的 log` | `[09-27 23:00, 09-28 02:00)` |
| 9 | `找凌晨 1 點到 5 點修改的檔案` | 今天，`[01:00, 05:00)` |
| 10 | `找最近 6 小時修改的 conf` | `[now-6h, 無上限)` |
| 11 | `找最近 30 分鐘修改的 yml` | `[now-30m, 無上限)` |
| 12 | `找從 9 月 1 日到現在修改的檔案` | `[09-01, 無上限)` |
| 13 | `找 2026 年修改的 tf 檔` | `.tf`，`[2026-01-01, 2027-01-01)` |
| 14 | `找本月修改的 log` / `找上個月修改的 log` | `[09-01, 10-01)` / `[08-01, 09-01)` |
| 15 | `找今年修改的 yaml` / `找去年修改的 yaml` | `[2026-01-01, 2027-01-01)` / `[2025-01-01, 2026-01-01)` |

第 13 項：依照需求，**沒有**把 `terraform` 加成 `.tf` 的別名。`terraform 檔` 仍然依照「任意英數字 + 檔」的規則解讀成 `*.terraform`，有測試固定這個行為。`tf 檔` 本來就能用。

組合查詢（都有測試）：

- `找 9 月修改的 yml 檔，排除 vendor`
- `找昨天修改而且包含 "ERROR" 的 log`
- `找最近 6 小時大於 10MB 的 log`
- `找 9 月最大的 10 個 log`
- `找 2026-09-28 最新修改的 20 個檔案`
- `找 9/1 到 9/15 修改的 log，排除 archive 目錄`
- `10 newest log files from Sep 1 to Sep 15 excluding archive`
- `在 /var/log 找今天 2 點到 4 點修改的 log`

## Files Changed

| 檔案 | 變更 |
|---|---|
| `tools/nlf/internal/query/datetime.go` | **新增**。日期與時間解析：`dateExpr`、`point`、`calendar`、`chineseMonthDay`、`englishMonth`、`clock`、`to24`、`validDay`、`validMonth`、`daySpan`/`monthSpan`/`yearSpan`；時間模型寫在檔案開頭的註解 |
| `tools/nlf/internal/query/parse.go` | `step()` 原本的 today/yesterday 特例改成呼叫 `dateExpr()`；reserved 詞加入日期相關詞彙 |
| `tools/nlf/internal/query/lexicon.go` | 字典加入 `到`、`至`、`前天`、`本月`、`今年` 等連接詞與簡稱，以及 `下午`、`中午`；fillers 加入 `改了`、`什麼`；新增和副檔名同名的別名 `conf`、`cfg`、`ini`、`tf`、`tfvars`、`pem`、`crt`、`service`（沒有任何語意對應） |
| `tools/nlf/internal/query/explain.go` | 修改時間兩端都有時，顯示成一列 `start <= mtime < end`；只有一端時顯示 `after X (mtime >= X)` 或 `before X (mtime < X)`；多一列時區 |
| `tools/nlf/internal/query/datetime_test.go` | **新增**，見 Tests Added |
| `tools/nlf/internal/query/parse_test.go` | 更新 `today` 的預期結果（見 Known Limitations 的行為變更） |
| `tools/nlf/internal/search/search_test.go` | 新增 `TestDateRangeBoundaries` |
| `tools/nlf/main.go` | help：新增 Date & time 區塊；更新 `modified` 那一列；Safety 與 `--explain` 的說明各精簡一行，讓 help 維持在 70 行以內 |
| `tools/nlf/main_test.go` | 新增 `TestDateSearchEndToEnd`；`TestHelpContent` 加入日期相關的必要內容 |
| `tools/nlf/testdata/help.golden` | help snapshot 更新 |
| `tools/nlf/README.md` | 新增「日期與時間」一節（完整語法、區間規則、所有例子）；條件表格加入日期列；Behavior details 與 Known limitations 同步 |
| `reports/nlf/2026-09-28-date-time-search.md` | 本報告 |

`make check` / `make dist` 會重新產生 gitignored 的 `bin/`。

## Tests Added

測試總數：210 → 392 個 test/subtest，全部 PASS，沒有 SKIP。其中日期相關的 subtest 有 174 個。

| 測試 | 內容 |
|---|---|
| `TestDateRanges`（111 例） | 年、年月、年月日、日曆簡稱、日期區間、開放區間、時間區間、相對時間、交集，全部以 `[from, to)` 精確比對，並檢查邊界使用 now 的時區。now 固定為 2026-09-28 10:30、時區 +08:00，不用 UTC，這樣時區錯誤才會被抓出來 |
| `TestDateCombinesWithOtherConditions`（11 例） | 月 + 副檔名 + 排除；日 + 內容；區間 + 排除；區間 + 大小；區間 + 排序 + 數量；ignore case；搜尋位置 |
| `TestInvalidDatesAndTimes`（42 例） | 不存在的日期與月份、不存在的時刻、結束早於或等於開始、單一時刻、不完整的區間、有歧義的格式、`may` 被當成動詞的情況；中英文錯誤訊息都有檢查 |
| `TestNotDates`（10 例 + terraform） | 不應被當成日期的輸入維持原本的行為：`2026 files`、`top 10`、`containing 2026-09-28`、`包含 帳號`、`包含 重點`、`conf` 別名、`terraform 檔` 不等於 `.tf` |
| `TestCalendarUnitsAcrossDST` | America/New_York：夏令時間開始的那天長 23 小時、結束的那天長 25 小時、3 月長 743 小時，邊界都在午夜。使用 `time/tzdata`，不依賴主機的 zoneinfo |
| `TestExplainShowsTimeRange` | 區間那一列與時區那一列；開放區間不會顯示成 `<=` 形式 |
| `TestDateRangeBoundaries`（search） | 在真實檔案上用 `os.Chtimes` 設定到奈秒的 mtime，驗證 6 個查詢的邊界 |
| `TestDateSearchEndToEnd`（CLI） | 11 個 CLI 日期查詢；4 個不合法查詢必須 exit 2 且沒有輸出；`--confirm` 的計畫中會顯示區間 |

## Boundary Tests

`TestDateRangeBoundaries` 在 mktemp（`t.TempDir()`）目錄裡建立 14 個檔案，mtime 精確到奈秒。它會先確認檔案系統保留了奈秒；APFS 上確實保留，所以測試有實際執行。

| mtime | `2026 年 9 月` | `9/1 到 9/15` | `9 月 28 日凌晨 1 點到 5 點` |
|---|---|---|---|
| 08-31 23:59:59 | ✗ | ✗ | ✗ |
| 08-31 23:59:59.999999999 | ✗ | ✗ | ✗ |
| 09-01 00:00:00 | ✓ | ✓ | ✗ |
| 09-15 23:59:59 | ✓ | ✓ | ✗ |
| 09-16 00:00:00 | ✓ | ✗ | ✗ |
| 09-28 00:59:59 | ✓ | ✗ | ✗ |
| 09-28 01:00:00 | ✓ | ✗ | ✓ |
| 09-28 04:59:59 | ✓ | ✗ | ✓ |
| 09-28 05:00:00 | ✓ | ✗ | ✗ |
| 09-30 23:59:59.999999999 | ✓ | ✗ | ✗ |
| 10-01 00:00:00 | ✗ | ✗ | ✗ |

另外也驗證了 `2026-09-16`、`8 月`、`10 月` 這三個查詢的邊界。全部 PASS。

## Invalid Input Handling

| 輸入 | 結果（都是 exit 2） |
|---|---|
| `2026-02-29`、`2026-04-31`、`2026-00-10`、`2026-09-00`、`2 月 30 日`、`9/31`、`Feb 30` | 「日期不存在」/ `invalid date` |
| `2024-02-29` | 合法（閏年） |
| `2026-13-01`、`13/01` | 「日期不存在」/ `invalid date` |
| `13 月`、`0 月`、`2026-13` | 「月份不存在」/ `invalid month` |
| `25:00`、`12:99`、`24:00`、`13pm`、`0am`、`晚上 12 點`、`下午 13 點`、`25 點` | 「時間不存在」/ `invalid time of day` |
| `9/20 到 9/10`、`today 04:00 to 02:00`、`今天 3 點到 3 點`、`2026 年到 2025 年` | 「開始時間必須早於結束時間」/ `start time must be before end time`，並附上 nlf 的解讀；不會自動交換 |
| `找 2 點修改的檔案`、`at 2am` | 「單一時間點不能當作條件，請寫成時間範圍」 |
| `找凌晨修改的檔案` | 「凌晨」後面需要時間 |
| `9/1 到`、`from Sep 1 to` | 時間範圍需要結束日期或時間 |
| `between Sep 1` | `"between" needs "and"` |
| `03/04/2026`、`04-03-2026`、`26-09-28`、`26/09/28`、`2026.09.28` | 「日期格式有歧義」，並提示可用的格式 |
| `9 月 1 日之前` | 看不懂「之前」（單邊的日期比較這次不支援） |

驗證方式：

- 不依賴 `time.Date` 的自動正規化。解析出的年、月、日都會先和 `daysIn(y, m)` 比對，才建立時間。
- `daysIn` 利用「下個月的第 0 天就是這個月的最後一天」這個性質計算當月天數。

**MM/DD 規則（固定、不依 locale）：**

- `MM/DD` 一律是「月/日」，年份用今年，例如 `03/04` 是 3 月 4 日。
- 三段式且不是 `YYYY` 開頭的寫法一律報錯，例如 `03/04/2026`。
- 兩位數年份、用點分隔的寫法也一律報錯。

## Runtime Verification

binary build 到 mktemp；fixture 放在新的 mktemp 目錄，用 `touch -t` 設定 mtime。執行時的 now 是 2026-09-28 03:17:30 CST。

| 查詢 | 結果 |
|---|---|
| `找 2026 年 9 月的檔案` | 6 個 9 月的檔案；不含 `08-31 23:59:59` 與 `10-01 00:00:00` |
| `找 2026 年 9 月的 pdf 檔` | 只有 `b-0901-000000.pdf`；9 月邊界外的兩個 pdf 不在結果中 |
| `找 9 月 1 日到 9 月 15 日修改的檔案` | 9/1 00:00 與 9/15 12:00 |
| `找昨天 23:00 到今天 02:00 修改的 log` | 09-27 23:30 與 09-28 01:30 |
| `找凌晨 1 點到 3 點修改的檔案` | 今天 01:30 與 02:30 |
| `找 10 月的檔案` / `找上個月修改的檔案` / `files modified on 2026-09-30` | 分別是 10-01 00:00 / 08-31 23:59:59 / 09-30 23:59:59 |
| `找 2026-02-29 的檔案` / `找 9/20 到 9/10 的檔案` | exit 2，錯誤訊息正確 |

`--explain` 必跑的 7 個查詢都已實際執行，每個 range 都符合 `start <= mtime < end`：

| 查詢 | 解析結果 |
|---|---|
| `找 2026 年 9 月的 pdf 檔` | `2026-09-01 00:00:00 <= mtime < 2026-10-01 00:00:00` |
| `找 9 月修改的 yml 檔` | 同上 |
| `查 2026 年 9 月 28 日改了什麼檔案` | `2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00` |
| `找 9 月 1 日到 9 月 15 日修改的 log` | `2026-09-01 00:00:00 <= mtime < 2026-09-16 00:00:00` |
| `找最近 6 小時修改的 conf` | `晚於 2026-09-27 21:12:10 (mtime >= …)`，沒有上界 |
| `找昨天 23:00 到今天 02:00 修改的 log` | `2026-09-27 23:00:00 <= mtime < 2026-09-28 02:00:00` |
| `找本月修改的 yaml` | `2026-09-01 00:00:00 <= mtime < 2026-10-01 00:00:00` |

`nlf --confirm --dir /etc '找 2026-09-28 修改的 yml 檔'` 的計畫顯示：

```
modified:      2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00
time zone:     CST (+08:00), local time
```

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨 |
| `go vet ./...` | ok |
| `go test ./...` | PASS（392 個，0 FAIL，0 SKIP） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| `make dist` | 4 個平台的執行檔都 build 成功；`SHA256SUMS` 驗證全部 OK；Linux 版本是 statically linked ELF |
| Linux vet / 測試編譯 | `GOOS=linux` 的 amd64、arm64 都能 `go vet`，3 個套件的測試都能 `go test -c` 編譯 |
| Linux 實機執行 | **沒有執行**。本機沒有 Linux 環境，Docker daemon 也沒有啟動 |
| staticcheck | skipped（未安裝） |

## Known Limitations

**行為變更（請 review）：**

1. **`today` / `今天` 現在有上界。** 以前是「晚於今天 00:00」，沒有上界；現在是 `[今天 00:00, 明天 00:00)`，和 `yesterday` 一致。差別只影響 mtime 在未來的檔案：這類檔案以前算「今天」，現在不算。
2. **`last month`、`last year` 改成日曆意義。** 以前是「往前推 30 天」和「往前推 365 天」，現在是上一個日曆月和上一個日曆年，這是需求指定的。`in the last month`、`past month`、`過去一個月` 仍然是往前推 30 天；`last week` 仍然是往前推 7 天。英文使用者可能會混淆，README 裡有明確說明。

**這次不支援（都會明確報錯，不會悄悄忽略）：**

- 單邊的絕對日期比較：`9 月 1 日之前`、`before Sep 1`、`after Sep 1`。「after」到底含不含當天有歧義，建議之後明確定義再加。目前可以用 `since` 或 `從 … 到 …`。
- 週：`本週`、`上週`、`this week`（`last week` 是既有的「往前推 7 天」）。
- 日在前的寫法（`28 September`）、DD/MM、兩位數年份、`2026-09-28T01:00` 這種 ISO 格式、秒數、時區語法。
- `9/1~9/15` 這種中間沒有空白的寫法：tokenizer 會把它當成一個詞；`9/1 ~ 9/15` 或 `9/1 到 9/15` 可以用。

**已知的邊角情況：**

- DST 的「不存在時刻」（例如 America/New_York 3/8 的 02:30）會被 Go 往後調到存在的時刻，不會報錯。台灣與中國沒有 DST，不受影響。
- `may` 單獨出現時，只有在前面有介系詞（`in may`）或後面有日期、年份時才當成五月。`dec`、`mar` 等縮寫一律當成月份；如果有副檔名剛好是 `.dec` 或 `.mar`，要寫成 `.dec`、`*.dec`。
- 為了不把 `帳號`、`重點` 這類內容文字切開，`點`、`號`、`凌晨`、`早上`、`上午`、`晚上` 沒有加進字典，而是在日期情境中以「未知中文詞」比對。代價是它們和相鄰的未知中文字連在一起時就無法辨識，例如 `點半` 有特別處理，但 `點整` 沒有。
- 相對時間和「到現在」沒有上界。mtime 在未來的檔案也會被包含，和既有行為一致。
- `上午 12 點` 當成 12:00，`凌晨 12 點` 與 `12am` 當成 00:00，`晚上 12 點` 報錯（因為不清楚是哪一天的午夜）。

## Recommended Next Steps

1. **Review 語意決策**，特別是上面兩個行為變更（`today` 有上界、`last month` 改成日曆月），以及「日期結束日包含在內、時刻結束不包含」的規則。
2. **在 Linux 實機上執行** `go test ./...` 與 README 的 Linux 驗證步驟。
3. 如果需要，再支援單邊的日期比較，但要先定義語意，例如：`before X` = `mtime < X.start`，`after X` = `mtime >= X.end`。
4. 如果需要，再支援週（`本週`、`上週`），並先定義一週從週一還是週日開始。
5. 考慮支援 ISO 8601 的 `2026-09-28T01:00`，運維 log 常見這種格式。
6. 版本號仍然是 `0.1.0`。這一輪加上前面幾輪累積的變更，可以考慮在合併後標成 `0.2.0`。
