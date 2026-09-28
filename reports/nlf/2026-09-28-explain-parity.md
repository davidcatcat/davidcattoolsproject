# nlf A4：`--explain` / output behavior consistency hardening

日期：2026-09-28
分支：`feat/nlf-roadmap-a4-explain-parity-20260928`（從 main `c891fcb` 建立；**未 merge 到 main**）
版本：維持 0.4.0（沒有 bump、沒有 tag）

> 使用者把 A4 定義為 explain parity hardening；roadmap 原本的 A4（`--count` / `-q`）不在本輪範圍。

## 原則與範圍

- **沒有改 production search 的語義。** `internal/search` 與 parser（`parse.go`、`tokenize.go`、`filename.go`、`datetime.go`、`boolean.go`、`lexicon.go`）都沒有修改。QA 用 HEAD 與新版 binary 在同一個 fixture 上跑了 73 組（22 個查詢 × 預設／`-l`／`-0`，加上 7 個 `--dir`／`--`／hostile root），stdout、stderr、exit code 全部相同。
- 修改只在：
  - `internal/query/explain.go`：`--explain` / `--confirm` 顯示的近似 find 指令
  - `main.go`：flag 解析與互斥
  - 測試與 README
- help（與 golden）、workflow、版本都沒有變。

## 盤點與分類

Audit agent 在 mktemp fixture 上實際執行 nlf 與它印出的 find 指令（macOS 的 `/usr/bin/find`、BSD grep、BSD sort／stat，以及 `gstat`、`gsort`）後分類如下。

### (a) Bug：已修（只改近似指令的呈現）

| ID | 問題 | 修正 |
|----|------|------|
| X1 | exclude 名稱裡的 `* ? [` 沒有跳脫：`exclude "a[1]"` 的指令會 prune `a1` 而留下 `a[1]`；`exclude "*"` 會 prune 搜尋位置本身 | 新增 `globEscape`，用在 exclude 名稱的 `-iname` |
| X2 | exclude 路徑與搜尋位置裡的 glob 字元沒有跳脫（`--dir 'r[1]' "exclude src/gen"` 不會 prune） | `-path` 的 root 與相對路徑兩部分都跳脫 |
| X3 | 搜尋位置的名稱等於排除名稱時（`--dir vendor "exclude vendor"`），find 會 prune 起點本身，什麼都不輸出 | 只在這種情況加上 `! -path <root>`，其他情況輸出與先前 byte-identical |
| Z1 | `at least 0 bytes` 顯示 `-size +-1c`，GNU find 會拒絕 | N=0 時改成 `! -size -0c`；其他大小不變 |
| T1 | 時間邊界與文件的半開區間 `開始 <= mtime < 結束` 不一致（`-newermt` 是嚴格大於） | 兩個邊界都取「邊界前 1 奈秒」。GNU/Linux 以奈秒寫出（`'2026-09-27 23:59:59.999999999'`）。macOS 維持整秒格式，因為 BSD find 不接受小數秒，而且以整秒比較 |

### 近似改為精確（成本低，已修）

| ID | 問題 | 修正 |
|----|------|------|
| O1 | 最大／最新 N 在同分時，`sort -rn` 的順序和 nlf 相反；有數量上限時連選到的檔案都可能不同 | `LC_ALL=C sort -k1,1nr -k2`：同分時依路徑的位元組順序，和 nlf 相同 |
| O2 | 修改時間只用整秒，同一秒內的先後被當成同分 | macOS `stat -f '%Fm %N'`、GNU `stat -c '%.9Y %n'`（含奈秒） |
| E1 | nlf 不把只叫 `.go` 的檔案當成 go 檔（副檔名前面必須還有字），但 `-iname '*.go'` 會比對到 | 改成 `-iname '?*.go'`；README 寫明這條規則 |

### (c) UX inconsistency：改成明確 error（exit 2，stdout 為空）

`flagConflicts` 表格在既有 5 列之後新增 10 列；既有 5 列的順序與訊息 byte-identical。

| 組合 | 訊息 |
|------|------|
| `--explain` + `-l` | `--explain never searches, so -l/--long has no files to list; use one of them` |
| `--help` + `--version` | `-h/--help and --version both print text; use one of them` |
| `--help` + `--explain` | `-h/--help prints text, not a query explanation; use it without --explain` |
| `--help` + `--confirm` | `-h/--help prints text and never searches; use it without --confirm` |
| `--help` + `-l` | `-h/--help prints text, not a file list; use it without -l/--long` |
| `--help` + `--dir` | `-h/--help never searches, so -d/--dir has no effect; use it without -d/--dir` |
| `--version` + `--explain` | `--version prints text, not a query explanation; use it without --explain` |
| `--version` + `--confirm` | `--version prints text and never searches; use it without --confirm` |
| `--version` + `-l` | `--version prints text, not a file list; use it without -l/--long` |
| `--version` + `--dir` | `--version never searches, so -d/--dir has no effect; use it without -d/--dir` |

另外：

- `-d ""`、`--dir ""`、`--dir=` → `--dir needs a directory`（原本會默默搜尋目前目錄）。
- 重複指定 `--dir`（`--dir a --dir b`、`--dir=a -d b`）→ `-d/--dir can be given only once`（原本最後一個生效）。

{`--explain`、`--confirm`、`-l`、`-0`、`--help`、`--version`、`--dir`} 的全部 21 組兩兩組合都有測試：不是明確 error，就是有文件記載且可以正常運作。

允許的組合：

- `--confirm` 搭配 `-l`、`-0`、`--dir`
- `--explain` 搭配 `--dir`
- `-l` 搭配 `--dir`
- `-0` 搭配 `--dir`

**刻意保留：** `--help` / `--version` 前後的查詢文字不是 flag，仍然允許、不檢查（`nlf --help go files` 照樣印出說明）。README 已寫明。

### (b) Intentional approximation：保留並文件化

| ID | 差異 | 說明 |
|----|------|------|
| T1' | macOS 上非整秒的時間邊界（「最近 7 天」這類相對時間） | BSD find 只能以整秒比較，可能差不到 1 秒；GNU/Linux 完全相同 |
| O3 | 未排序的 `top N` | nlf 依名稱順序、數到 N 就停；find 依目錄讀取順序，所以選到的檔案也可能不同 |
| G1 | `[!a]` | nlf（Go `path.Match`）不把 `!` 當成否定，要用 `[^a]`；find 的 fnmatch 會否定 |
| C1 | binary 判斷 | nlf 看第一次讀取的 64 KB；BSD grep `-I` 只看前 32 KB，所以 NUL 在 32–64 KB 之間的檔案結果不同；GNU grep 的判斷方式又不一樣 |
| C2 | invalid UTF-8 內容 | 在 UTF-8 locale 下 grep 可能把它當成 binary，nlf 仍會比對到。刻意不在指令加 `LC_ALL=C`，因為那會破壞 `-i` 對非 ASCII 字元的比對 |
| C3 | Unicode 大小寫折疊 | 例如 `İSTANBUL` 與 `istanbul`，Go 與 grep `-i` 不同 |
| F6 | `--explain` 不檢查搜尋位置是否存在 | 實際搜尋時才會 exit 2 |

確認沒有差異的項目：

- hidden 檔案與 `.git` 都會被搜尋，和 find 相同
- 副檔名比對不分大小寫；多重副檔名依最後一段
- 目錄樹內的 symlink 不跟隨（和沒有 `-L` 的 `-type f` 相同）
- FIFO、socket 會略過
- `-size` 用 `c` 單位，沒有 512-byte 進位問題
- CJK 內容、超長行、以 `--` 開頭的內容、無法讀取的檔案

## 測試

更新的既有預期字串都是為了對應上面的修正（見 Review 逐行核對），沒有放寬任何斷言，也沒有新增 `t.Skip`：

- E1：`'*.ext'` → `'?*.ext'`
- T1：時間邊界取前 1 奈秒；`TestExplainDoesNotSearch` 依平台選擇預期字串
- O1／O2：排序 pipeline

新增或加強的測試：

- `TestFindCommand`：25 個新案例，涵蓋 glob 跳脫（名稱、路徑、root、symlink root）、root guard（`vendor`、`vendor/`、大小寫不同、`-x/vendor`）、以及 `.` root 與路徑排除時沒有 guard，另有 size 0（含 OR）、兩個平台的日期與 OR、次秒邊界、排序 pipeline、`?*.ext`。
- `TestExplainParity`：在 macOS 與 Linux 上實際執行印出的 find 指令，Linux 不 skip。
  - fixture 加入：`.hid`、`.git`、檔名就是 `.txt`、`a[1]`／`a1`／`star*`／`starX`、`x/a[1]`、0／1023／1024／1025 bytes 的檔案、三個同大小的檔案、`錯誤` 內容。
  - 邊界 mtime：在 2026-09-28 00:00 與 2026-09-29 00:00 前後 ±0.5 秒、−1 奈秒，並有同秒內差 0.5 秒的檔案。如果檔案系統丟掉奈秒，測試會直接失敗。
  - 排序查詢改成**依順序**比較，拿掉原本「同分順序刻意不同」的註解。
  - root 是 `vendor`、`vendor/`、symlink `sym/vendor` 時的 `exclude vendor`。
- `TestGNUSortPipeline`：加入同大小、同 mtime、次秒 mtime，比對精確順序。
- `TestFlagConflicts`：加入 `dir`，並列出所有應該允許的組合。
- `TestFlagConflictMatrixCLI`（新）：21 組 × 兩種順序，都透過 `run()` 執行。
- `TestDirFlagValidation`（新）。

**Mutation 檢查（QA）：** 15 個 mutant 都被測試抓到，包括：

- 拿掉 exclude 名稱、exclude 路徑、root 的跳脫
- 拿掉 root guard
- 還原 1 奈秒、還原 `%Fm`、還原 `%.9Y`、還原 `?*`、還原 `sort -rn`
- 拿掉 size 0 的特例

其中有幾個（root 跳脫、`.000000000`、size 0）在 macOS 上只由 unit 字串抓到。Linux 的 parity 測試會另外驗證。

## 驗證

**Lead（macOS，Go 1.27.1）：** 以下每一項都以 exit code 確認為 0。

| 步驟 | 結果 |
|------|------|
| gofmt | 0 |
| `make check` | 0 |
| `go test -race ./...` | 0 |
| `go test -count=10 -shuffle=on ./...` | 0 |
| `make dist` | 0 |
| `scripts/ci-smoke.sh` | 0 |
| `go test -v` | 972 個 PASS、0 個 SKIP（本機有 `gstat`） |

**Review Agent：** APPROVE WITH CHANGES。已採納的修改：

- README 的 macOS 時間邊界說明加上限定
- 說明 `--help` 前後的查詢文字都允許
- 註解用詞

**QA Agent：** PASS WITH CHANGES，建議的 README 修改已採納。GNU 行為的判斷依據與信心：

- **`-newermt` 小數秒（高）：** 以 `gdate` 驗證 gnulib `parse_datetime` 接受奈秒。
- **`! -size -0c`（高）：** GNU 會拒絕舊的 `+-1c`。
- **跳脫的 glob 與 `?` 比對開頭的點（高）：** GNU `-name` 沒有 FNM_PERIOD，fnmatch 沒有 FNM_NOESCAPE。
- **root guard（高）：** GNU 印出的起點與傳入的一樣。
- **`%.9Y`（高，限 coreutils 9.4）**
- **`LC_ALL=C sort -k1,1nr -k2`（高）**

**GitHub Actions：** 本機沒有 GNU find／grep，Docker daemon 也沒有啟動，所以上述 GNU 行為由 ubuntu-latest 上的 `TestExplainParity` 實際驗證。Lead 無法讀取 private repo 的 Actions 結果，**CI 狀態需要人類確認**。

## 風險與後續

- **GNU 假設只在 CI 驗證。** 如果 ubuntu CI 的 `TestExplainParity` 或 `TestGNUSortPipeline` 失敗，最可能的原因依序是：
  1. `-newermt` 小數秒
  2. root guard 的路徑形式
  3. `%.9Y`

  Linux 端的備案是改用 `-newermt '@<unix>.<ns>'`，或把該項改成只文件化。
- **`ubuntu-latest` 沒有固定版本，CI 也沒有記錄工具版本**（QA MINOR-2）。未來的 Ubuntu 若改用 uutils coreutils，`stat -c '%.9Y'` 可能不支援。建議另外（不在 A4）：在 workflow 的 toolchain 步驟印出 `find`／`stat`／`sort`／`grep --version` 與 `/etc/os-release`，或固定 `ubuntu-24.04`。本輪依指示沒有修改 workflow。
- **相容性：** 下面這些原本 exit 0，現在是 exit 2。這是刻意的，因為使用者的 flag 不應該被默默忽略；把 `--help` / `--version` 和其他 flag 一起傳的腳本會受影響。
  - `nlf --help -l`、`nlf --version --dir x`
  - `--explain -l`
  - `--dir ""`
  - 重複的 `--dir`
- **DST：** 時間邊界以本機時間寫出；在日光節約時間回撥的那一小時，本地時間字串本來就有歧義（A4 之前就存在）。
- **小項目：**
  - `TestFlagConflictMatrixCLI` 與 `TestFlagConflicts` 各有一份訊息表，可以改成共用（QA NIT-2）。
  - parity fixture 的 root 沒有 glob 字元，root 跳脫只由 unit 測試涵蓋（QA NIT-3）。
  - root guard 用 Go 的 Unicode 大小寫折疊，`-iname` 用 locale（只影響特殊的非 ASCII 名稱）。
- **既有行為，沒有改：** `--dir` 會把後面的 flag 當成目錄名稱（`nlf --dir --explain …` → 找不到搜尋位置 `--explain`，exit 2）；`smaller than 0 bytes` 可以解析，但不會比對到任何檔案。
- **沒有碰：** A5（`--max-depth`）、A6（top-N heap）。
