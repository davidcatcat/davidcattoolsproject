# NLF Filename Search Report

日期：2026-09-28
範圍：`tools/nlf`，新增檔名搜尋（完全相同、包含、開頭、結尾、glob），並支援檔名的同類 OR
狀態：已實作並驗證；review 後已 commit 並 push 到 main（連同先前尚未 push 的 0.2.0 release commit；`tools/nlf/v0.2.0` tag 仍只在本地，未 push）

## Summary

- 新增「檔名本身」這個搜尋維度，只比對 `filepath.Base`（`d.Name()`），不比對路徑。
- 支援五種比對方式：exact（完全相同）、contains（包含）、prefix（開頭）、suffix（結尾）、glob（Go 的 `path.Match`，不是 regex）。
- 一定要寫出明確的主詞（`檔名` / `名稱` / `名字` / `filename` / `name`）加上比對方式，或是本身就代表檔名的比對詞（`named` / `名為`）。因此 `包含 nginx` 仍然是內容搜尋，`go 或 md 檔` 仍然是副檔名。
- 比對**區分大小寫**，值保持使用者輸入的原樣；副檔名比對仍然不分大小寫。
- 檔名加入 Boolean OR（`KindName`）。「或」後面可以只寫值，這時沿用左邊的比對方式；不同比對方式之間也可以 OR。
- search 在 `d.Info()` 之前就先比對檔名，檔名不符的檔案不會 Stat，也不會開檔。
- 不合法的 glob、缺值、缺比對方式、檔名與不同類條件的 OR，全部在 parse 階段就 exit 2。
- 安全模型不變：唯讀、不執行 shell、不使用 sudo。

## Previous Behavior

Baseline（修改前）：fmt 乾淨、vet ok、test / race PASS、build ok、`make check` ok，共 **517** 個 test/subtest。

所有檔名形式的查詢都會失敗。雖然不會被默默誤解，但就是沒有這個功能：

| 輸入 | 修改前 |
|---|---|
| `找檔名是 application.yml` 等所有中文檔名語句 | 看不懂這個詞：「名」 |
| `find files named kubeconfig` | cannot understand: "named" |
| `filename matches "*.conf"` | cannot understand: "filename" |

git 狀態：`main` 上有尚未 push 的 0.2.0 release commit（`d953962`）和本地 tag `tools/nlf/v0.2.0`。這一輪的修改疊在它們之上，沒有動到 tag。

## Query Model

`internal/query/query.go`：

```go
type NameMatchKind int // NameExact, NameContains, NamePrefix, NameSuffix, NameGlob

type NameCond struct {
    Kind    NameMatchKind
    Pattern string
}
func (c NameCond) Match(name string) bool // 所有比對都集中在這裡

type Query struct {
    ...
    Names []NameCond      // AND：每一個都必須成立
    AnyOf []OrGroup       // 新增 KindName：OrGroup.Names 至少一個成立
}
```

- `Query.Names` 是 AND：`檔名包含 config 且檔名以 app- 開頭`。
- `AnyOf` 中 `Kind == KindName` 的群組是 OR：`檔名包含 config 或 backup`。
- 既有欄位的語意都沒有改；沒有檔名條件時，`Names` 是 nil。
- parser 集中在新檔案 `internal/query/filename.go`：`nameExpr`、`nameOperator`、`nameValue`、`nameAlternative`、`addName`。其餘程式只透過 `step()` 中的一行呼叫，以及 `boolean.go` 的 `classify` / `merge` 接上。

## Supported Filename Matching

主詞：`檔名`、`檔案名稱`、`檔案名`、`文件名`、`名稱`、`名字`（含簡體字）、`filename`、`file name`、`name`、`names`。

本身就代表檔名的比對詞：`named`、`called`、`名為`、`名叫`。

## Exact / Contains / Prefix / Suffix / Glob

| 比對方式 | 語意 | 中文 | 英文 |
|---|---|---|---|
| Exact | `base == p` | `檔名是`、`檔名叫`、`叫做`、`等於`、`為`、`名為`、`名叫` | `filename is`、`name equals`、`is exactly`、`file(s) named`、`called` |
| Contains | `strings.Contains` | `檔名包含`、`包括`、`含有`、`名稱有` | `filename contains`、`containing`、`includes` |
| Prefix | `strings.HasPrefix` | `檔名以 X 開頭`、`名稱開頭是 X`、`名字從 X 開始` | `starts with`、`begins with`、`starting with` |
| Suffix | `strings.HasSuffix` | `檔名以 X 結尾`、`名稱結尾是 X` | `ends with`、`ending with`、`ending in` |
| Glob | `path.Match` | `檔名符合`、`匹配`、`吻合` | `filename matches`、`match`、`glob` |

- **值的解析方式：**
  - 有引號時，照原樣使用，保留大小寫、空白與中文。
  - 沒有引號時，一路讀到空白或標點為止，這一點和內容搜尋的規則相同。
  - 值後面如果直接黏著其他關鍵字，會被視為範圍不明而報錯，要求加引號。只有 `開頭`、`結尾`、`開始`、`的` 這類詞可以直接接在後面，例如 `檔名以app-開頭`。
  - 含中文或空白的檔名必須加引號。
- **Glob 的處理：**
  - 選用 `path.Match` 而不是 `filepath.Match`：檔名只是 base name，不需要依作業系統處理路徑分隔符號。
  - 在 parse 時就先用 `path.Match(pattern, "")` 驗證格式。Go 會驗證整個 pattern，已實測 `[abc` 不管比對什麼名稱都會回報錯誤。
  - 格式錯誤時報「檔名 glob pattern 不合法」/ `invalid filename glob pattern`，exit 2。
- **Exact / contains / prefix / suffix 都是一般文字比對，沒有萬用字元：**
  - `檔名是 name[1].txt` 就是字面上的 `name[1].txt`。
  - `結尾是 .tar.gz` 只是一般的結尾比對，和副檔名無關。
- **檔名條件和副檔名條件不會互相推導，組合時就是 AND：** `找檔名是 application.yml 的 yml` 雖然有點多餘，仍然是合法的兩個條件。

## Filename OR

- `KindName` 加入 OR 群組。
- **沿用比對方式：** 「或」後面如果只有值，就沿用左邊最後一個比對方式。
  - `檔名包含 prod 或 staging` → contains 或 contains
  - `檔名以 app- 開頭或 api-` → prefix 或 prefix
  - `檔名以 app- 開頭或以 api- 開頭` → 同上
  - `檔名符合 "*.conf" 或 "*.cfg"` → glob 或 glob
  - `filename is nginx.conf or httpd.conf` → exact 或 exact
- **明確寫出比對方式：** 如果「或」後面寫出完整的條件，就用它自己的比對方式，例如 `檔名包含 prod 或檔名是 staging.yml`。不同比對方式之間也可以 OR，因為都是檔名條件。
- **只在安全的情況下才沿用：** 「或」後面必須是引號字串、`.ext`、未知中文字，或不是保留字的英文單字，才會被當成檔名值。
  - 否則會當成一般條件解析，再由既有的「同類」檢查處理。
  - 例如 `檔名包含 prod 或今天`、`name contains prod or today`，會報「目前 OR 只能連接同類條件」。
- **沿用時的結尾詞必須一致：** `檔名以 app- 開頭或 api- 結尾` 會報錯，要求寫完整。
- **整體仍是 AND：**
  - `檔名是 nginx.conf 或 httpd.conf，今天修改` = `(nginx.conf OR httpd.conf) AND today`
  - `找檔名包含 prod 或 staging 的 yml` = `(prod OR staging) AND .yaml/.yml`
- 既有的 connector 檢查同樣適用：`檔名包含 prod 且 或 staging` 會報「OR 和 AND 不能連續使用」。

## Case Sensitivity

- 檔名比對一律區分大小寫：`Config.yml` 不會找到 `config.yml`。在 macOS 預設不分大小寫的 APFS 上也一樣，因為這是 nlf 自己在記憶體中的比對規則。
- 副檔名比對仍然不分大小寫（`-iname`），行為不變。
- `不分大小寫` / `ignore case` 仍然只影響內容搜尋：
  - 查詢中有檔名條件、卻沒有內容條件時，加上它會報錯：「「不分大小寫」只適用於內容搜尋；檔名比對一律區分大小寫」。這樣它不會看起來像在影響檔名比對。
  - 同時有內容條件時，它照舊只作用在內容上；`--explain` 會把檔名那一列標成「(區分大小寫)」，內容那一列標成「(不分大小寫)」。
- 這一輪沒有做檔名的不分大小寫比對。需要時可以用 glob 列出大小寫，例如 `"[Cc]onfig*"`。

## Search Evaluation

`internal/search/search.go`，每個檔案依照以下順序判斷，便宜的條件先做：

```
regular file && matchExt(d.Name()) && matchNames(d.Name())   // 不需要 Stat、不需要開檔
  → d.Info() 只取一次：size、mtime
  → matchSize && matchTime && matchGroups                    // 大小、時間，以及它們的 OR 群組
  → matchContent                                             // 最後才開檔，只讀一次
```

- `matchNames`：`Query.Names` 全部成立，而且每個 `KindName` 群組至少一個成立。比對都透過 `NameCond.Match` 進行。
- 檔名不符的檔案不會 Stat，也不會開啟。`TestFilenameBeforeContent` 用一個權限為 000、檔名不符的檔案驗證了這一點：沒有產生任何權限錯誤。
- 排序、數量上限、排除目錄、權限處理、內容掃描都沒有改動。

`--explain` / `--confirm`：

- 顯示方式：`檔名  包含 "config" (區分大小寫)`、`filename:  is "nginx.conf" OR is "httpd.conf" (any one matches, case-sensitive)`。
- 近似的 find 指令用 `-name`（不是 `-iname`），各比對方式的對應如下：
  - exact：`-name 'x'`
  - contains：`-name '*x*'`
  - prefix：`-name 'x*'`
  - suffix：`-name '*x'`
  - glob：直接使用 pattern
  - OR：`\( -name a -o -name b \)`
- 一般文字裡的 `* ? [ \` 會先跳脫，例如 `name[1].txt` 變成 `'name\[1].txt'`；接著再用 `shellQuote` 處理引號。
- README 已註明：find 的 glob 和 Go glob 大致相同，但不完全一樣，實際行為以 nlf 為準。

## Files Changed

| 檔案 | 變更 |
|---|---|
| `internal/query/filename.go` | **新增**：主詞、比對方式與結尾詞的詞彙；`nameExpr`、`nameOperator`、`nameValueFor`、`nameAlternative`、`nameValue`、`addName`（glob 驗證） |
| `internal/query/query.go` | `KindName`、`NameMatchKind`、`NameCond`（含 `Match`）、`Query.Names`、`OrGroup.Names`，以及語意註解 |
| `internal/query/parse.go` | `step()` 優先解析檔名條件；reserved 詞；「ignore case 加上只有檔名條件」的檢查 |
| `internal/query/lexicon.go` | 把檔名相關的中文詞加入字典 |
| `internal/query/boolean.go` | `classify`、`merge`、`empty` 支援 `Names`；檔名 OR 沿用比對方式；錯誤提示中加入「檔名」 |
| `internal/query/explain.go` | 檔名那一列與 OR 群組的顯示；`nameText`、`namePattern`（跳脫）；find 的 `-name` |
| `internal/search/search.go` | `matchNames`，在 `d.Info()` 之前判斷 |
| `internal/query/filename_test.go` | **新增** |
| `internal/search/search_test.go` | 新增 `TestFilenameSearch`、`TestFilenameComposite`、`TestFilenameBeforeContent` |
| `main.go` | help：新增 `filename` 那一列；`--confirm` 說明濃縮成 2 行，help 維持 70 行、最寬 100 欄 |
| `main_test.go` | 新增 `TestFilenameEndToEnd`；`TestHelpContent` 加入 filename 的必要內容 |
| `testdata/help.golden` | help snapshot 更新 |
| `README.md` | 新增「檔名搜尋」一節；OR 表格加入檔名；Known limitations 移除「不支援依檔名搜尋」 |
| `reports/nlf/2026-09-28-filename-search.md` | 本報告 |

## Tests Added

測試總數：517 → **641**，新增 124 個，全部 PASS，0 FAIL，0 SKIP。

| 測試 | 內容 |
|---|---|
| `TestParseFilename`（92 例） | 五種比對方式的中英文寫法；19 個 DevOps acceptance cases；引號與特殊檔名（`foo bar.conf`、`中文設定檔.yml`、`Config.yml`、`--weird-name`、`name[1].txt`、`.env`、`.gitignore`、`application-prod.yml`）；glob（`*.conf`、`.*`、`*.env`、`[ab]*.conf`、沒有引號的 `core.*`）；AND 組合（副檔名、時間、大小、內容、排除、搜尋位置、兩個檔名條件）；檔名 OR（沿用比對方式、明確比對方式、不同比對方式、搭配時間 AND）；不變的行為（`包含 nginx`、`找包含 ERROR 的 log`、`找內容包含 ERROR 的 log`、`go 或 md 檔`、`find log files`、`在 /etc/nginx 找 conf`、`containing name`） |
| `TestParseFilenameErrors`（23 例） | 缺值（中英文、`files named`）；不合法的 glob（`[abc`、結尾的反斜線）；空字串；缺比對方式；`以 X` 缺結尾詞；沒加引號的中文檔名；結尾的「或」；檔名 OR 時間、檔名 OR 大小；沿用時結尾詞不同；connector 衝突；ignore case 加上只有檔名條件（中英文） |
| `TestNameCondMatch`（21 例） | 區分大小寫、點開頭的檔名、exact 是字面比對、中文、prefix / suffix、`.tar.gz`、glob 的 `*`（也會比對點開頭）、`?`、`[...]`、glob 中的 `name[1].txt`、不合法的 pattern 不會比對成功 |
| `TestExplainShowsFilename` | 中文與英文的檔名那一列；OR 只顯示成一列；`-name` 而不是 `-iname` |
| `TestFilenameFindPattern`（9 例） | 各比對方式對應的 `-name` pattern；`[`、`*` 的跳脫；單引號；空白 |
| `TestFilenameSearch`（search，19 例） | 需求第三十五節的 fixture（14 個檔名），另外加上 `sub/`、`config/` 目錄，驗證只比對檔名、不比對目錄；區分大小寫（`Case/Application.yml` 放在獨立目錄，避免 APFS 把兩個檔名當成同一個檔案）；各種比對方式與 OR；排除目錄 |
| `TestFilenameComposite`（search，6 例） | 檔名 + 今天；檔名 exact + 內容；glob + 大小；檔名 OR + 今天；檔名 OR + 副檔名；英文的 whose name |
| `TestFilenameBeforeContent`（search） | 權限為 000、檔名不符的檔案不會被開啟 |
| `TestFilenameEndToEnd`（CLI） | 7 個查詢的結果；5 個不合法查詢 exit 2；`--explain` / `--confirm` 顯示檔名那一列與 `-name`；Safety 仍是 READ-ONLY |

## Runtime Verification

binary build 到 mktemp；fixture 放在新的 mktemp 目錄。

`--explain` 必跑的 6 個查詢都已實際執行：

| 查詢 | 解析結果 |
|---|---|
| `找檔名是 application.yml` | `檔名  是 "application.yml" (區分大小寫)`，`find . -type f -name application.yml -print` |
| `找檔名包含 config 的 yml` | 副檔名 `.yaml 或 .yml`；`檔名  包含 "config"`；`\( -iname '*.yaml' -o -iname '*.yml' \) -name '*config*'` |
| `找檔名以 .bak 結尾` | `結尾是 ".bak"`，`-name '*.bak'` |
| `找檔名符合 "core.*"` | `符合 glob "core.*"`，`-name 'core.*'` |
| `找檔名是 nginx.conf 或 httpd.conf` | `是 "nginx.conf" 或 是 "httpd.conf" (符合任一個即可，區分大小寫)`，`\( -name nginx.conf -o -name httpd.conf \)` |
| `找今天修改，而且檔名包含 config 的 yml` | 副檔名、檔名、`2026-09-28 00:00:00 <= mtime < 2026-09-29 00:00:00` 三列 |

實際搜尋的結果：

| 查詢 | 結果 |
|---|---|
| `找檔名是 application.yml` | 只有 `application.yml`（不含 `Case/Application.yml`、`application-prod.yml`） |
| `檔名是 "Application.yml"` | 只有 `Case/Application.yml` |
| `找檔名包含 config 的 yml` | `app-config.yml` |
| `找今天修改，而且檔名包含 config 的 yml` | exit 1，沒有結果。預期如此，因為 `app-config.yml` 的 mtime 被設成 9/20 |
| `找檔名以 .bak 結尾` | `nginx.conf.bak` |
| `找檔名符合 "core.*"` | `core.1234`、`core.dump` |
| `找檔名是 nginx.conf 或 httpd.conf` | `etc/nginx/nginx.conf`、`httpd.conf` |
| `找檔名是 nginx.conf，而且內容包含 "listen 443"` | `etc/nginx/nginx.conf` |
| `找檔名符合 ".*"` | `.env`、`.gitignore` |
| `檔名是 "中文設定檔.yml"` | `中文設定檔.yml` |
| `檔名符合 "[abc"` | exit 2，「檔名 glob pattern 不合法」 |

## Backward Compatibility

- 既有的 517 個 test/subtest 全部通過，而且**一行都沒有改**：所有 `*_test.go` 在 `git diff` 中被移除的行數是 0。
- `testdata/help.golden` 依照 help 的修改更新：新增 filename 那一列，`--confirm` 的說明從 3 行濃縮成 2 行，內容相同。
- 特別確認沒有 regression 的項目（都有測試）：
  - `包含 nginx`、`找包含 ERROR 的 log`、`containing name` 仍然是內容搜尋。
  - `go 或 md 檔`、`find log files` 仍然是副檔名。
  - `在 /etc/nginx 找 conf` 仍然是搜尋位置加上副檔名。
  - 副檔名別名、日期與時間、OR、排除、排序、數量上限的測試全部通過。
- 新增的保留字：`filename`、`name`、`names`、`named`、`called`、`starts`、`begins`、`ends`、`matches`、`glob` 等。
  - 影響：這些英文字不能再當成沒有引號的「任意副檔名」（例如 `name files`），也不能當成 OR 右邊的內容值。
  - 它們在內容搜尋中仍然可以用：`containing name` 維持原本的行為。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨 |
| `go vet ./...` | ok |
| `go test ./...` | PASS（641 個，0 FAIL，0 SKIP） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| `make dist` | 4 個平台的 `nlf-0.2.0-*` 都 build 成功；`SHA256SUMS` 驗證全部 OK |
| Linux | `CGO_ENABLED=0 GOOS=linux GOARCH=amd64/arm64` 都 build 成功；`go vet` 通過；3 個套件的測試都能 `go test -c` 編譯。**沒有在 Linux 實機上執行** |
| staticcheck | skipped（未安裝） |

## Known Limitations

- 檔名只能區分大小寫比對，沒有 ignore-case 語法；需要時請用 glob 列出大小寫。
- 不支援 regex、`**`、`{a,b}`、路徑 pattern（例如 `nginx/*.conf`）。
- 沒有引號的中文檔名無法解析，必須加引號。原因是 `中`、`檔` 等字是既有的關鍵字。
- 中文「以 … 開頭／結尾」的結尾詞必須緊接在值後面：`檔名以 app- 或 api- 開頭` 會報錯；請寫成 `檔名以 app- 開頭或 api-`。
- `*` 會比對到點開頭的檔名。這是 Go glob 的行為，find 的 `-name` 也是如此，但 shell 的 glob 不是。README 已說明。
- 近似的 find 指令只是參考：`-name` 的 glob 和 Go 的 `path.Match` 大致相同，但不保證完全一致。
- 錯誤訊息中的原文，是由 token 以空白接起來的。

## Recommended Next Steps

1. **Review 語意決策：**
   - 必須寫出主詞才算檔名條件。
   - 「ignore case 加上只有檔名條件」會報錯。
   - OR 沿用比對方式的規則。
   - `*` 會比對點開頭的檔名。
2. **版本：** 目前已經是 **0.2.0**，而且本地有 `tools/nlf/v0.2.0` tag。上一輪完成時還沒 push，這一輪的檔名搜尋**不在**那個 tag 裡面。建議：
   - 如果 0.2.0 還沒發布，可以把 tag 移到包含檔名搜尋的 commit。這需要你明確同意，因為會移動 tag。
   - 如果 0.2.0 已經發布（或保留現在的 tag），把檔名搜尋併入下一版 **0.3.0**。檔名搜尋是新的查詢維度，屬於 feature milestone。
   - 這一輪沒有改版本號，也沒有動 tag。
3. 如果需要，另外設計檔名的 ignore-case 語法，例如 `檔名包含 config（檔名不分大小寫）`，並明確定義它的適用範圍。
4. 如果需要，另外設計路徑 pattern（`路徑符合 "*/nginx/*.conf"`），不要和檔名條件混在一起。
5. 在 Linux 實機上執行 `go test ./...`。
