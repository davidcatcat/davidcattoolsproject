# nlf A7：Fuzz 與 hostile-input hardening

日期：2026-09-28
分支：`feat/nlf-roadmap-a7-fuzz-hardening-20260928`（從 main `97b6bd3` 建立；**未 merge 到 main**）
版本：維持 0.4.0（沒有 bump、沒有 tag）

A7 不新增功能。它用 Go fuzz、hostile filename matrix、NFC／NFD、parser strictness、AND monotonic、determinism 與 pathological input，系統性檢查 robustness。找到的 bug 都先找出 root cause，再做最小修正，並附上 regression test。**沒有用 recover 掩蓋錯誤，沒有放寬 parser，也不會自動修正輸入。**

**唯一的使用者可見行為變更：** 查詢含有無效的 UTF-8 位元組時，一律 exit 2。其餘差異都是 bug 修正（見下方的對照）。

## Audit

探索性 fuzz 的結果：

| Target | 次數 | 結果 |
|--------|------|------|
| FuzzTokenize | 81 萬 | clean |
| FuzzParse | 43 萬 | clean |
| FuzzShellQuote | 620 萬 | clean |
| FuzzScanTerms | — | 1.45 秒內失敗（B2／B3） |

另外做了三項檢查：

- **Strictness insertion：** 約 4.5 萬組。沒有任何一組插入後還解析成原本的 Query。
- **AND monotonic：** 400 組。加上條件後，結果都沒有擴大。
- **Pathological input：** 量了輸入長度對時間與記憶體的成長曲線，找出 B4–B8。

## 修正的 bug

| # | 問題 | 從哪一版開始 | 修正 |
|---|------|--------------|------|
| B1 | **Silent rewrite：** tokenizer 用 `[]rune(s)`，無效位元組變成 U+FFFD。例如 `exclude "\xff"` 實際排除的是 `"�"`，使用者想排除的目錄沒有被排除。 | v0.1 | `Parse` 一開始就用 `utf8.ValidString` 拒絕，exit 2。`--dir` 不經過 `Parse`，仍接受原始位元組。 |
| B2 | **ignore-case 在 64 KiB 讀取邊界漏找：** 有些字元小寫後長度會變（K U+212A、İ、Ω、Å、ẞ），但保留的 tail 是用原始長度計算的。 | v0.1 | 對串流做 lower-case，不對 window 做。保留的 tail 在 lower-case 之後才計算長度。 |
| B3 | **被讀取邊界切開的 rune 變成 U+FFFD：** 會造成誤判。 | v0.1 | 把不完整的 UTF-8 尾巴（`incompleteTail`，最多 3 bytes）帶到下一次讀取。到 EOF 時一次送出，結果和整個檔案一起 lower-case 相同。 |
| B4 | **指數時間：** `timeSatisfiable` 對重疊的時間 OR 群組建立完整的笛卡兒積。747 bytes 的查詢要 54 秒。 | v0.2.0 | 改用 candidate-start 演算法，O(R²)。非空的半開區間交集，一定包含所選區間中最大的起點，或是負無限大。 |
| B5 | **二次方：** tokenizer 逐一用 `+=` 串接未知的非 ASCII 字元，64K 個字元配置 12.7 GB。 | v0.1 | 改用 `strings.Builder`，並用 `hanRunEnd` 一次取出整段。 |
| B6 | **二次方：** `extNounAhead` 對排除清單裡的每個名稱都重新掃描整條鏈。64 KB 要 85 秒、配置 23 GB。 | v0.2.0 | 依 token 位置 memoize（只有 `list=true` 會用 cache）。 |
| B7 | **二次方：** 連續的中文數字在每個位置都重新解析到結尾。 | v0.1 | 用 `chineseNumberSuffixes` 一次算出所有後綴。 |
| B8 | **二次方：** 連在一起的 token 逐一用 `+=` 串接。 | v0.2.0／v0.3.0 | 改用 `strings.Builder`。 |

## 文件化的限制（不修）

- **L1：** 檔名含換行時，預設輸出與 `-l` 輸出會被拆成兩行。要安全處理請用 `-0`。
- **L2：** NFC／NFD 依位元組比對，不做 normalization。README 有說明，也說明了 APFS 與 Linux 的差異。
- **L3：** 外觀相似的標點會併入值。
- **L4：** `--explain` 的近似指令裡，含換行的搜尋文字會被 `grep -F` 當成兩個 pattern。README 有說明。
- **L5：** 大量 `，` 的配置是線性的。
- **L6：** parser 產生不出空字串 term。

## 測試

先寫測試，再修正。所有新測試在舊程式上都會因為正確的原因失敗（見「驗證」）。

- **Fuzz target：** `FuzzParse`、`FuzzTokenize`、`FuzzShellQuote`、`FuzzScanTerms`。
  - `f.Add` seed：200／200／33／15。
  - `testdata/fuzz/` 有 22 個 corpus 檔案，包含已修正 bug 的重現輸入。
  - 一般的 `go test` 就會跑 seed corpus。
- **`hostile_test.go`：** 檔名含換行、`-` 開頭、無效 UTF-8（Linux 實際建立；darwin 確認 APFS 回傳 EILSEQ）、NFC／NFD matrix。
- **Property 測試：**
  - strictness insertion、AND monotonic、determinism
  - 新舊 tokenizer 差分
  - `extNounAhead` memo 與舊版在隨機位置的差分
  - `timeSatisfiable` 十萬組隨機差分
  - `chineseNumberSuffixes` 的全部短字串，加上兩萬組隨機字串
- **Pathological 與 growth 測試：** growth 用 allocation 比例，是確定性的，不受機器速度影響。每一組 pathological 輸入有 20 秒的 wall-clock 上限。
- **CI：** 新增「Fuzz smoke」步驟，4 個 target 各跑 5 秒。
- **QA 回饋後補上：** `TestScanTermsBoundaryCases` 新增 4 個「檔尾是不完整 UTF-8」的案例，並新增 2 個 FuzzScanTerms seed。案例涵蓋短檔、剛好在 64K 邊界、跨 64K 邊界、64K 之後。

## Mutation 結果

- **第一輪：** 19 個 mutant 全部被抓到。
- **QA 獨立做的 16 個 mutant：** 15 個被抓到，只有 `carry-eof-flush` 存活。這個 mutant 把 `if n > 0 || len(carry) > 0 && err != nil {` 改成 `if n > 0 {`。
  - 影響：`printf 'abc\xe2' > tail.txt` 之後，`nlf 'containing "�" ignore case'` 找不到 `tail.txt`。
  - 處理：補上上述測試後，`TestScanTermsBoundaryCases` 與 `FuzzScanTerms` 都會抓到它。
- **QA 抓到的 15 個：**
  - scan carry：關掉 carry、`incompleteTail` 只看 k<2、lower-case window 而不是串流
  - memo：key 錯（i+1）、`list=false` 也用 memo、只 memoize 鏈的第一個元素
  - time：少掉 group start、global After、負無限大這三種 candidate 之一；`in` 把 zero `t` 當成一定在區間內
  - UTF-8：拿掉 `utf8.ValidString`
  - tokenizer：`hanRunEnd` 少了 `longestCJK` 或數字的停止條件；`chineseNumberSuffixes` 少了 lone-zero 規則，或放寬了 unit-after 規則

## Baseline 與 A7 binary 對照

QA 對 base（`97b6bd3`）與 A7 的 binary 各跑 110 組指令：94 組完全相同，16 組不同，全部是預期中的差異。

- **12 組是無效 UTF-8 的查詢。** 現在一律 exit 2，訊息是 `the query contains bytes that are not valid UTF-8`，包括 `--explain`、`--confirm`，以及 `--dir raw\xffdir` 搭配無效查詢。之前會拿 U+FFFD 去搜尋，exit 0、1 或 2 都有可能。
- **4 組是 B2 修正。** ignore-case 的 `kk`／`AAAK` 現在會找到 Kelvin 符號剛好在 65536 前面的檔案。

以下全部相同：README 範例、`--explain`、`--confirm`（y／n／沒有輸入）、`-l`、`-0`、depth flag、NFC／NFD 名稱、`-` 開頭的目錄、含換行的目錄、`--help`／`--version`、錯誤的 flag、exit code 0／1／2。

`main.go`、help 文字、`explain.go` 都沒有修改；`--help` 輸出 byte-identical。

## 驗證

Lead（macOS，Go 1.27.1）在最後一次修改後重跑，每一項都以 exit code 確認為 0：

| 步驟 | 結果 |
|------|------|
| gofmt | 0（沒有檔案需要格式化） |
| `make check` | 0 |
| `go test -race ./...` | 0 |
| `go test -count=10 -shuffle=on ./...` | 0 |
| `go test -count=1 -v ./...` | 0；1545 個 PASS、0 個 SKIP |
| CI Fuzz smoke（4 × 5 秒） | 0 |
| `make dist` | 0 |
| `SHA256SUMS` | 4 行，`shasum -c` 全部 OK |
| `scripts/ci-smoke.sh`（`bin/nlf` 與 darwin dist binary） | 0／0 |

QA 另外確認的項目：

- Go 1.23.0（`go.mod` 的最低版本）下，`go vet`、`go test`、Fuzz smoke 都通過。
- 每個 target 跑 60 秒 fuzz，沒有失敗：FuzzParse 30 萬次、FuzzTokenize 150 萬次、FuzzShellQuote 439 萬次、FuzzScanTerms 23 萬次。
- Linux binary 是靜態連結，4 個 dist binary 都是 `CGO_ENABLED=0`。
- 新測試放到 base 上，因為正確的原因失敗：
  - B1：`ExcludeDirs:[�]`
  - B2／B3：Kelvin／İ 找不到、split rune 被當成 `"�"`
  - B4：allocation 變成 523 倍，k=16 的查詢要 34.5 秒
  - B5–B8：每加倍一次成長約 3.9 倍，排除清單那組要 1 分 18 秒，超過 20 秒上限

Review Agent：**APPROVE**，只有 4 個 nit，都不需要改程式：

1. `tokenize.go` 裡接到前一個 Han token 的分支，看起來已經不會執行到。保留它是為了和舊 tokenizer 完全等價。
2. glued-* 的 growth 比例是 2.24–2.50 倍，上限是 3.0。如果之後 flaky，可以改成比較 4096 與 16384，上限約 6。
3. darwin 上 hostile test 預期 invalid UTF-8 的 `mkdir` 回傳 EILSEQ。TMPDIR 如果不在 APFS 上，會直接失敗而不是 skip。
4. CI job timeout 是 15 分鐘，新測試讓每個 `go test` 步驟多了幾秒。本機估算仍在上限內，但還沒有實際的 GitHub Actions 數字。

QA Agent：**PASS**。它找到的測試漏洞已經補上，見「Mutation 結果」。

**GitHub Actions：** push 之後會在 ubuntu-latest 與 macos-latest 執行。Lead 無法讀取 private repo 的 Actions 結果（沒有 `gh`），**run ID 與結果需要人類確認後補上**。

## 使用者可見的變更

- 查詢含有無效的 UTF-8 位元組時會報錯，exit 2。中文訊息是 `查詢含有無效的 UTF-8 位元組`，英文訊息是 `the query contains bytes that are not valid UTF-8`。之前會拿 U+FFFD 去搜尋，可能排除錯的目錄。
- ignore-case 內容搜尋在 64 KiB 讀取邊界的漏找與誤判已經修正（B2／B3）。
- 原本會花數十秒甚至耗盡記憶體的病態查詢，現在是線性或多項式時間（B4–B8）。
- README 新增：UTF-8 規則、NFC／NFD 說明、含換行的搜尋文字與 `grep -F` 的差異、Fuzzing 小節、CI 小節提到 Fuzz smoke。

## 剩下的風險

- APFS 不允許無效 UTF-8 的檔名，所以 raw-byte `--dir` 只能在 Linux 上實際測試。本機 macOS 沒有 Linux 環境（Docker 沒有執行），這部分要靠 CI 的 ubuntu runner。
- Review 提到的 growth 比例餘裕與 CI 15 分鐘 timeout，要看第一次 GitHub Actions run 才能確認。
- **事故紀錄：**
  - audit agent 曾對自己剛建立、repo 外的 `/tmp/x` 執行一次 `rm -f`，違反 no-rm 規則，沒有影響任何既有檔案。
  - QA agent 因為 `cd` 失敗，在 **main repo 根目錄**產生了未追蹤的 `hostile_test.go` 與 `internal/`（22 個 fuzz corpus 檔案的複本）。這些檔案不屬於 A7 commit，已經回報給人類處理，Lead 沒有移動也沒有刪除。
- **沒有做：** Phase B、新功能、版本 bump、merge 到 main。
- 產品原則不變：read-only、deterministic、production 不執行 shell、不 import `os/exec`、不連網路、不用 LLM、不用 sudo、沒有新增第三方依賴、macOS／Linux 語義相同。
