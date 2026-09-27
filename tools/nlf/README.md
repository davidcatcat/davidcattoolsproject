# nlf — Natural Language Find

用中文或英文描述你要找的檔案，不用記 `find`、`grep`、`awk` 的語法。

```bash
nlf "找出最近 7 天修改、大於 1MB 的 go 檔，排除 vendor，最多 10 個"
nlf "python files containing \"import os\" modified within 3 days, top 5"
nlf --explain "最大的 5 個圖片"
```

- 單一 binary，macOS 優先，只用 Go 標準函式庫
- 不呼叫任何 LLM API；解析規則是固定的（deterministic），相同輸入永遠得到相同結果
- 唯讀：不會修改、刪除或移動任何檔案，也不會自動使用 `sudo`
- 看不懂的詞會直接報錯，不會默默忽略而擴大搜尋範圍

## Build

需要 Go 1.23 以上（`brew install go`）。

```bash
cd tools/nlf
make build            # 產生 bin/nlf
make check            # gofmt 檢查 + go vet + go test + build
cp bin/nlf /usr/local/bin/   # 選用：放進 PATH
```

## Usage

```text
nlf [flags] "<query>"

--explain        顯示 nlf 怎麼理解查詢，以及等效的 find 指令；不實際搜尋
-l, --long       每行前面加上大小與修改時間
-d, --dir PATH   搜尋位置（預設為目前目錄）
-h, --help       說明與範例
--version        版本
```

輸出為每行一個路徑，可以直接接 pipe（例如 `| xargs open`）。
提示訊息都寫到 stderr，語言會跟著查詢語言（有中文字就用中文）。

Exit status：`0` 有找到、`1` 沒找到、`2` 錯誤（看不懂查詢、目錄不存在、根目錄沒有權限）。

### 支援的條件

所有條件以 AND 組合；多個副檔名之間是「任一符合」。

| 條件 | 範例 |
|------|------|
| 副檔名 | `.go`、`*.md`、`go files`、`pdf 檔`、`go 或 md 檔`、`圖片`、`影片`、`images`、`副檔名是 heic` |
| 內容 | `containing "TODO"`、`with TODO`、`包含 TODO`、`內容有「錯誤訊息」`、`ignore case`、`不分大小寫` |
| 修改時間 | `last 7 days`、`in the past 3 hours`、`older than 30 days`、`30 days ago`、`today`、`yesterday`、`最近 7 天`、`3 小時內`、`超過 30 天`、`3 天前`、`今天`、`昨天` |
| 檔案大小 | `larger than 10MB`、`smaller than 1KB`、`at least 1GB`、`> 2gb`、`大於 10MB`、`10MB 以上`、`小於 1KB`、`最大 10MB` |
| 數量 | `top 10`、`first 5`、`最多 10 個`、`前 10 個`、`只要十個` |
| 最大 / 最新 N 個 | `10 largest files`、`最大的 5 個檔案`、`前 3 大`、`newest 3`、`最新的 3 個`、`最近修改的 5 個檔案` |
| 排除目錄 | `exclude node_modules and .git`、`排除 vendor、build 目錄`、`除了 dist 以外`、`排除 src/generated` |
| 搜尋位置 | `in ~/Documents`、`under /tmp`、`在 ./src`，或 `--dir` |

`--explain` 範例：

```text
$ nlf --explain "找出最近 7 天修改、大於 1MB 的 go 檔，排除 vendor，最多 10 個"
解析結果：
  搜尋位置       .
  副檔名         .go
  修改時間       晚於 2026-09-20 22:39:05
  檔案大小       > 1 MB
  排除目錄       vendor
  數量上限       10

等效的 macOS 指令（僅供參考，nlf 不會執行它）：
  find . \( -iname vendor \) -prune -o -type f -iname '*.go' -size +1048576c -newermt '2026-09-20 22:39:05' -print | head -n 10
```

## Behavior details

- 大小用二進位單位：1 KB = 1024 bytes，`m` / `mb` 都是 MB（不是分鐘；分鐘請寫 `min` 或 `分鐘`）。
- 「月」= 30 天，「年」= 365 天；「7 天」是從現在往前推 7×24 小時。「今天」「昨天」以本地時間午夜為界。
- 「3 天前」「30 days ago」表示「修改時間早於 3 天前」。
- 內容搜尋是逐字比對（不是正規表示式），預設區分大小寫；前 64 KB 含 NUL 位元組的檔案視為二進位並略過（同 `grep -I`）。
- 副檔名比對不分大小寫；排除目錄以名稱比對（不分大小寫），含 `/` 時以相對於搜尋位置的路徑比對。
- 只回傳一般檔案，不回傳目錄；不跟隨目錄內的 symlink（搜尋位置本身是 symlink 時會跟隨，例如 `/tmp`）。
- 隱藏檔與 `.git` 會被搜尋，需要時請加 `排除 .git`。
- 沒有排序時依目錄順序輸出，達到數量上限就停止；有「最大/最新」時會先走完整棵樹再排序。
- 沒有權限讀取的目錄或檔案會略過，結束時在 stderr 列出並提示；nlf 不會提權。

## Known limitations (v0.1)

- 不支援條件之間的 OR、否定（「不包含 X」）、依檔名搜尋（例如 `.DS_Store`）、正規表示式、絕對日期（「2026-01-01 之後」）。
- 中文要搜尋的內容建議加引號：未加引號時，遇到 nlf 認得的關鍵字會被切開。
- 路徑中有空白時要加引號，例如 `在 "~/My Docs"`。
- 只保證 macOS（BSD `find` 語法用於 `--explain`）；Linux 大致可用但未測試。
- 未支援的說法會回報「看不懂這個詞」；詞彙表在 `internal/query/lexicon.go`，擴充時請一併加測試。

## Development

```
main.go                     CLI：參數、輸出、錯誤訊息
internal/query/tokenize.go  中英混合斷詞（字典最長匹配、中文數字）
internal/query/lexicon.go   所有認得的詞彙
internal/query/parse.go     詞 → Query
internal/query/explain.go   --explain 與等效 find 指令
internal/search/search.go   唯讀走訪與比對
```

`make check` 是完成標準：格式、vet、測試、build 全過。
