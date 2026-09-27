# NLF Help Improvement Report

日期：2026-09-28
範圍：`tools/nlf` 的 `--help` 文字、README 對齊，以及文件測試
狀態：已完成，未 commit、未 push
前置：這次改動疊在 [fix round 1](2026-09-28-fix-round-1.md) 與 [confirm gate](2026-09-28-confirm-gate.md) 之上，兩者都還沒 commit

## Summary

`--help` 改寫成「第一次使用的人看完就會用」的說明，依序包含：一句話定位、Usage、中文範例、英文範例、Flags、`--explain`、`--confirm`、Safety、Supported conditions、Search root、Exit status、Tips。

- 長度：65 行，最寬 94 個 display column（CJK 字元算 2 格），一個終端機畫面可以讀完。
- 題目指定的 8 個真實案例都寫進 help，README 也有同樣一份。
- 範圍：只改了 help 字串（`main.go` 的 `usage` 常數）與 README；parser 和 search 的行為沒有任何改變。

## 案例驗證

加進 help 之前，先逐一用實際 binary 以 `--explain` 確認每個範例都會被正確理解：

| 範例 | 解析結果 |
|---|---|
| `nlf "找 ovpn 檔"` | 副檔名 `.ovpn` |
| `nlf --dir ~/Downloads "找 ovpn 檔"` | root `~/Downloads`、`.ovpn` |
| `nlf '找內容包含 "20.2.250.249" 的檔案'` | 內容 `"20.2.250.249"`（區分大小寫） |
| `nlf "找最近 7 天修改的 go 檔"` | `.go`、修改時間在 7 天內 |
| `nlf "找最大的 5 個檔案"` | 依大小排序、上限 5 |
| `nlf "排除 vendor 和 node_modules，找 go 檔"` | 排除 vendor 與 node_modules、`.go` |
| `nlf --confirm "找 ovpn 檔"` / `nlf --explain "找 ovpn 檔"` | 同第一列 |
| 英文：`find pem files under /etc`、`go files containing TODO modified in the last 7 days`、`10 largest files excluding node_modules and .git`、`newest 3 md files in ~/Documents` | 皆如字面意思 |

## Files Changed

| 檔案 | 變更 |
|---|---|
| `tools/nlf/main.go` | 只改 `usage` 常數（help 文字） |
| `tools/nlf/README.md` | Exit status 改為「`0` 有找到，或 `--confirm` 被使用者取消；`1` 查詢有效但沒找到；`2` 錯誤（…flag 錯誤…）」；新增「常用範例」一節，列出 8 個真實案例；開頭的唯讀說明補上「不建立」「不執行 shell 或其他程式」 |
| `tools/nlf/main_test.go` | 改寫 `TestDocsMatchBehavior`；新增 `TestDocExamplesRun`、`TestHelpSnapshot`、`TestHelpContent` |
| `tools/nlf/testdata/help.golden` | 新增 help snapshot |
| `reports/nlf/2026-09-28-help-improvement.md` | 本報告 |

`make check` 會重新產生 gitignored 的 `tools/nlf/bin/nlf`。

## Help 結構

```text
nlf — Natural Language Find (v0.1.0)
Find files by describing them in Chinese or English. Deterministic and read-only; no AI calls.

Usage / Examples (中文) / Examples (English) / Flags
--explain   nlf --explain "找 ovpn 檔"    只顯示、不搜尋、不詢問；近似指令 nlf 不會執行
--confirm   nlf --confirm "找 ovpn 檔"    execution plan + safety summary；只有 y / Y 會搜尋，其他輸入印出 "Cancelled."
Safety      Read-only；不修改、不建立、不刪除檔案；不執行 shell 或其他程式；不使用 sudo
Supported conditions   extension / content / modified / size / count / exclude；並註明不支援 OR、否定、regex
Search root   --dir ~/Downloads、in ~/Documents、在 ./src、under /tmp、在 "~/My Docs"；--dir 和查詢中的位置擇一
Exit status   0 有找到或 --confirm 被取消 / 1 查詢有效但沒找到 / 2 錯誤
Tips          確切內容加引號、有空白的路徑加引號、看不懂時會報錯而不是默默擴大範圍（用 --explain 檢查）
```

完整內容見 `tools/nlf/testdata/help.golden`。

## 與 README 的一致性

- Exit codes：help 和 README 都寫明「`--confirm` 被取消 = 0」，也都寫明 `1` 是「查詢有效但沒找到」。
- Safety：README 開頭補上「不建立」「不執行 shell 或其他程式」，和 help 的 Safety 一致。
- 範例：help 的中文範例，以及 `--explain`、`--confirm` 範例，都必須逐字出現在 README 的「常用範例」；由測試強制。
- `--explain`、`--confirm` 的描述沿用 confirm-gate 那一輪 README 的說法，沒有矛盾。

## Tests Added

測試數：198 → 201 個 test/subtest，全部 PASS。

| 測試 | 內容 |
|---|---|
| `TestDocsMatchBehavior`（改寫） | 範例擷取改用 `docExamples()`，支援 flag（`--dir X`、`--explain`、`--confirm`、`-l`）與單引號查詢。<br>help 的「Supported conditions」區塊逐一切出每個片段（以兩個空白或 ` / ` 分隔，共 32 個），每一個都必須能 parse。<br>README 表格的範例照舊檢查。<br>help 的中文與 `--explain`、`--confirm` 範例必須出現在 README；兩份文件對「`--confirm` 取消 = exit 0」必須一致；原本的「不得稱等效」與 flag 清單檢查保留。 |
| `TestDocExamplesRun`（新增） | help 與 README 的每一個 `nlf …` 範例都在 sandbox 裡實際執行：HOME 指向 temp dir，並建好 `Downloads`、`Documents`、`My Docs`、`work`。沒有指定位置的範例加上 `--dir <sandbox>`；指到 sandbox 以外（`/etc`、`/tmp`、`./src`）的範例只用 `--explain` 檢查。stdin 給 `n`，所以 `--confirm` 範例會被取消並回 0。每個範例的 exit code 都必須是 0 或 1，不能是 2。 |
| `TestHelpSnapshot`（新增） | `--help` 必須和 `testdata/help.golden` 完全相同；有意修改 help 時，執行 `go test -run TestHelpSnapshot -update .` 更新。 |
| `TestHelpContent`（新增） | 檢查必要內容：定位句、Usage、8 個真實案例、12 個段落標題、`--explain` 與 `--confirm` 的關鍵說明（never searches、execution plan、safety summary、Only y or Y、"Cancelled."）、Safety 的每一項、三個 exit code、三條 tips。另外限制長度：不超過 70 行、每行不超過 100 個 display column。 |

反向驗證：在 mktemp 複製出來的副本裡，把 help 的一個範例改成看不懂的 `找最大的 banana 個檔案`，並把 README 的 exit 說明改回舊版。結果 `TestDocsMatchBehavior`（parse 失敗、README 缺範例、exit code 不一致）、`TestDocExamplesRun`（exit 2）、`TestHelpSnapshot` 都會 FAIL，錯誤訊息也指得出問題所在。

調整紀錄：`helpConditionFragments` 的「格式變更防呆」門檻一開始設成 40，實際片段數是 32，所以改成 30。這個門檻只用來偵測 help 格式被改壞；32 個片段每一個都要能 parse 的檢查沒有放寬。

## Validation Results

| 項目 | 結果 |
|---|---|
| `go fmt ./...`（`gofmt -l .`） | 乾淨 |
| `go vet ./...` | ok |
| `go test ./...` | PASS |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| staticcheck | skipped（未安裝） |

## Known Limitations

- help 只有英文說明加上中英範例，沒有依語系切換；README 則是中文。
- help 列的是常用說法，不是完整詞彙表；完整規則在 README 的「Behavior details」和 `internal/query/lexicon.go`。
- `TestDocExamplesRun` 對指向 sandbox 以外的範例（`/etc`、`/tmp`、`./src`）只做 `--explain` 檢查，不實際搜尋，以免依賴本機檔案系統。
- snapshot 測試代表以後任何 help 文字的修改都要一起更新 golden 檔（執行 `-update`）。這是刻意的，讓 help 的變更在 review 時一定看得到。
- 前兩輪報告裡列的 Remaining Known Issues 都不在這次範圍內。
