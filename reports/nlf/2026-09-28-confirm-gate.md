# NLF Confirm Gate Report

日期：2026-09-28
範圍：`tools/nlf`，新增 `--confirm`（human approval gate）
狀態：已實作，未 commit、未 push
前置：本次改動疊在 [fix round 1](2026-09-28-fix-round-1.md) 之上，那一輪的變更也還沒 commit

## Design

- `--confirm` 的流程和一般模式相同，會跑完 parse → `--dir` → `~` 展開 → 排除路徑正規化 → symlink root 判斷；只是在 `search.Run` **之前**先顯示執行計畫並停下來等回答。parse 錯誤、排除路徑錯誤會照常直接報錯（exit 2），不會出現確認提示。
- 執行計畫由 `executionPlan(q, search.RunEffects())`（`main.go`）產生，內容依序是：
  - Search root
  - Conditions：重用 `query.Conditions`，與 `--explain` 同一份資料，固定用英文標籤
  - Approximate macOS command：`query.FindCommand(q)` 的字串
  - Safety
  - Risk
- 安全性摘要不在 CLI 裡寫死，而是由 `search` 套件宣告（`search.Effects` / `RunEffects()` / `Risk()`）。這樣這份宣告和它所描述的程式碼放在一起。另外有 `TestProductionCodeIsReadOnly` 用 `go/ast` 檢查 production 原始碼，確保宣告不會和實作脫節。
- Risk 分級規則：
  - Delete → `DESTRUCTIVE`
  - Modify、Create、Execute shell 或 sudo → `WRITE`
  - 以上皆無 → `READ-ONLY`
  - 目前 `RunEffects()` = `{ReadFiles: true}`，所以結果是 `READ-ONLY`。
- 確認規則是嚴格的 allowlist：從 stdin 讀**一行**，去掉結尾的 `\n` 和 `\r` 後，必須剛好是 `y` 或 `Y`。其他輸入一律取消，包括：`n`、`N`、空行、`yes`、前後有空白的 ` y` / `y `、`yy`、其他文字、`是`、立即 EOF、讀取錯誤。
- 輸出位置：執行計畫、提示、`Cancelled.` 都寫到 **stderr**，stdout 只放搜尋結果。這樣 `nlf --confirm … | xargs …` 在確認後，pipe 裡仍然只有路徑。
- `--explain` 和 `--confirm` 同時使用時報錯（exit 2），不去猜使用者的意思。
- 刻意保持原樣的部分：
  - production code 沒有加入任何執行外部程式的能力
  - `FindCommand()` 只回傳字串，`--confirm` 只顯示它
  - 不加 `--confirm` 時完全不讀 stdin，行為和之前一樣

## Files Changed

| 檔案 | 變更 |
|---|---|
| `tools/nlf/main.go` | `--confirm` flag；`run` 多一個 `stdin io.Reader` 參數；`executionPlan`、`approved`；`--explain` 與 `--confirm` 互斥；help 的 Flags 和 Examples |
| `tools/nlf/internal/search/search.go` | `Effects`、`RunEffects()`、`Effects.Risk()` |
| `tools/nlf/internal/query/explain.go` | 從 `Explain` 抽出 `Condition`、`Conditions(q, lang)`、`FormatConditions`，`Explain` 的輸出完全不變 |
| `tools/nlf/main_test.go` | 新增 `--confirm` 相關測試；`nlf()` helper 改為傳入空的 stdin；新增 `nlfInput()`；`TestDocsMatchBehavior` 納入 `--confirm` |
| `tools/nlf/README.md` | 範例、Flags、「`--explain` 與 `--confirm`」一節 |
| `reports/nlf/2026-09-28-confirm-gate.md` | 本報告（新增） |

`make check` 會重新產生 gitignored 的 `tools/nlf/bin/nlf`。

## CLI Behavior

```text
$ printf 'y\n' | nlf --confirm --dir <S> 'go files excluding vendor'
# stderr:
NLF execution plan

Search root:
  <S>

Conditions:
  extension:     .go
  exclude dirs:  vendor

Approximate macOS command (for reference only; nlf does not execute it):
  find <S> -type d \( -iname vendor \) -prune -o -type f -iname '*.go' -print

Safety:
  Read files:        YES
  Modify files:      NO
  Create files:      NO
  Delete files:      NO
  Execute shell:     NO
  sudo required:     NO

Risk:
  READ-ONLY

Execute this search? [y/N]:
# stdout:
<S>/a.go
```

| 模式 / 輸入 | 行為 | exit |
|---|---|---|
| `--confirm` + `y` / `Y`（含 `y\r\n`；或 `y` 後直接 EOF） | 搜尋，結果寫到 stdout | 依一般規則：0 有找到 / 1 沒找到 / 2 錯誤 |
| `--confirm` + `n`、`N`、Enter、`yes`、` y`、`y `、其他字串、`是` | 不搜尋；stderr 輸出 `Cancelled.` | 0 |
| `--confirm` + 立即 EOF（例如 `</dev/null`） | 不搜尋；stderr 輸出 `Cancelled.` | 0 |
| `--confirm` + 第一行是 `n`、第二行是 `y` | 只讀第一行，所以取消 | 0 |
| `--confirm` + 查詢看不懂 | 直接報錯，不顯示計畫也不詢問 | 2 |
| `--explain` | 顯示解析結果與近似指令（stdout），不詢問、不搜尋，也不讀 stdin | 0 |
| `--explain --confirm` | 報錯：`--explain only shows the plan and --confirm asks before searching; use one of them` | 2 |
| 一般模式 | 直接搜尋，不讀 stdin（與之前相同） | 0 / 1 / 2 |

## Safety Model

- **Safety 區塊描述的是 nlf 自己的搜尋行為，不是那條近似的 find 指令。** nlf 用 Go 原生走訪檔案系統（`filepath.WalkDir`、`os.Open` 唯讀讀取內容），不會執行 shell 或任何外部程式。
- **這份宣告由測試強制維持。** `TestProductionCodeIsReadOnly` 會解析 `main.go` 和 `internal/*/*.go` 的所有非測試檔案（AST），以下任何一項出現都會讓測試失敗：
  - import：`os/exec`、`syscall`、`unsafe`、`plugin`、`io/ioutil`、`golang.org/x/sys/unix`
  - 呼叫：`os.StartProcess`、`os.Create`、`os.CreateTemp`、`os.WriteFile`、`os.OpenFile`、`os.NewFile`、`os.Remove`、`os.RemoveAll`、`os.Rename`、`os.Mkdir*`、`os.Chmod`、`os.Chown`、`os.Lchown`、`os.Chtimes`、`os.Symlink`、`os.Link`、`os.Truncate`、`os.Setenv`、`os.Unsetenv`、`os.Clearenv`
  - 沒有 `os.Create`、`os.OpenFile`、`os.WriteFile`，程式就無法以寫入模式開啟檔案。
  - 同一個測試也斷言 `RunEffects() == {ReadFiles: true}`，而且 `Risk()` 是 `READ-ONLY`。
  - 我在一份複製到 mktemp 的副本中注入 `import "os/exec"` 和 `os.Remove`，確認這個測試會失敗並指出檔案與行號。
- **近似 find 指令只供人閱讀，不會被執行。** 如果使用者自己把它貼到 shell 執行，那條指令的效果是 find、grep、stat 等工具的效果，不在上述保證範圍內；計畫中已註明 "nlf does not execute it"。
- **預設取消（default deny）。** 任何不明確的回答都取消，而且取消時 exit 0，把「取消」視為正常結果，不是錯誤。
- **沒有 sudo，也不提權。** 權限錯誤仍然依原本方式略過並回報。

## Tests Added

測試數：192 → 198 個 test/subtest，全部 PASS。

| 測試 | 涵蓋 |
|---|---|
| `TestConfirmRunsOnlyOnYes` | `y`、`Y`、`y\r\n`、`y`＋EOF 會搜尋，且輸出和不加 `--confirm` 時完全相同；`n`、`N`、Enter、立即 EOF、`yes`、`YES`、` y`、`y `、`yy`、`ok`、`是` 都不搜尋、stderr 以 `Cancelled.` 結尾、exit 0 |
| `TestConfirmReadsOnlyOneLine` | 輸入 `n\ny\n` 時不搜尋 |
| `TestConfirmPlan` | 計畫包含：Search root、Conditions（extension、size、exclude、limit）、完整的近似 find 指令、`Read files: YES`、`Modify/Create/Delete: NO`、`Execute shell: NO`、`sudo required: NO`、`Risk: READ-ONLY`、提示字串；計畫中不得出現 `WRITE`、`DESTRUCTIVE`；取消時 stdout 必須是空的 |
| `TestConfirmAndExplainAreDistinct` | `--explain` 就算 stdin 有 `y` 也不詢問、不搜尋、不顯示執行計畫；`--explain --confirm` exit 2；一般模式就算 stdin 有 `n` 也照常搜尋，不詢問 |
| `TestConfirmParseErrorDoesNotPrompt` | 查詢看不懂時 exit 2，不顯示確認提示 |
| `TestProductionCodeIsReadOnly` | production code 不得 import `os/exec` 等套件，也不得呼叫任何會寫入的 `os` 函式；`RunEffects()` 必須是 READ-ONLY |
| `TestDocsMatchBehavior`（更新） | help 與 README 的 `nlf --confirm "…"` 範例必須能 parse；兩份文件都要列出 `--confirm` |

關於「`--confirm` 不得執行 `FindCommand()`」：`FindCommand()` 是純字串函式，沒有執行外部程式的能力；production code 中也不存在任何執行外部程式的路徑，這由 `TestProductionCodeIsReadOnly` 保證。`TestConfirmRunsOnlyOnYes` 另外確認，確認後的輸出和原生搜尋（一般模式）完全一致。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨，沒有產生任何變更 |
| `go vet ./...` | ok |
| `go test ./...` | PASS |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| staticcheck | skipped（未安裝） |

Runtime probes（binary build 到 mktemp；fixture 使用新的 mktemp 目錄；透過真的 pipe 和 `/dev/null` 提供 stdin）：

| 輸入 | 結果 |
|---|---|
| `printf 'y\n'` | stderr 顯示完整計畫；stdout 只有 `<S>/a.go`（vendor 已排除）；exit 0 |
| `Y\n` | 有搜尋（stdout 2 行） |
| `n\n`、`\n`、`yes\n`、`whatever\n`、`</dev/null` | stdout 0 行，stderr 最後一行 `Cancelled.`，exit 0 |
| `printf 'y\n' \| nlf --confirm … \| wc -l` | 2，stdout 沒有混入計畫內容 |
| `--explain --confirm` | exit 2，並說明兩者只能擇一 |
| `--explain` | 輸出和修改前相同 |

## Known Limitations

- **確認時才判斷，搜尋時不再檢查。** 計畫顯示的是當下的解析結果；如果使用者確認之前檔案系統有變化（例如 symlink 被換掉），實際搜尋的是確認之後的狀態。nlf 是唯讀的，所以最壞情況只是讀到不同的檔案。
- **只能擋住 nlf 自己的行為。** Safety 區塊不描述近似 find 指令被使用者手動執行後的效果。
- **提示後的換行。** 在終端機互動時，使用者按下 Enter 會自然換行；用 pipe 餵入 `y` 時，stderr 的提示後面沒有換行，後續的 stderr 訊息（例如權限提示）會緊接在同一行。這是顯示上的小問題，不影響判斷。
- **沒有偵測 TTY。** 在沒有終端機的環境（例如 CI）使用 `--confirm` 而且 stdin 是空的，會直接取消（exit 0）。這是 default deny 的預期結果，但批次腳本不應該使用 `--confirm`。偵測 TTY 需要 `syscall` 或 `x/term`，會牴觸「production code 不 import syscall」這條保護規則，所以沒有做。
- **執行計畫只有英文。** Conditions 固定使用英文標籤，以配合題目指定的英文標題；`--explain` 仍然跟著查詢語言。
- **`TestProductionCodeIsReadOnly` 是保守的原始碼檢查，不是 sandbox。** 它只檢查 `main.go` 和 `internal/*/*.go`，且只認得 `os.X` 這種直接呼叫。如果以別名 import `os`，或透過新的第三方套件寫入，就檢查不到。目前 nlf 只使用標準函式庫、沒有第三方依賴；未來加入依賴時需要擴充這個檢查。
- fix round 1 報告中列的 Remaining Known Issues（heap top-N、Unicode folding、binary 偵測、EACCES/EPERM 提示、`-0` 輸出等）不在這次範圍內，都還沒處理。
