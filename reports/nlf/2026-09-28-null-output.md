# nlf A3：`-0` / `--null`

日期：2026-09-28
分支：`feat/nlf-roadmap-a3-null-output-20260928`（從 main `c932fad` 建立；**未 merge 到 main**）
版本：維持 0.4.0（roadmap 建議 A3–A5 合併為 0.5.0，由人類決定；沒有 bump、沒有 tag）

## 行為

- `-0` 與 `--null` 完全等價。每個結果路徑後面接一個 NUL，不加換行；stdout 只有結果。
- stderr 內容與 exit code 都不變：0 找到（或 `--confirm` 取消）、1 沒找到、2 錯誤。沒找到時 stdout 為空，stderr 提示和沒有 `-0` 時完全相同。
- read-only contract 不變（`TestProductionCodeIsReadOnly` 通過；production code 沒有新增任何寫檔或執行程式的呼叫）。
- 推薦用法：`nlf -0 "找最近 7 天修改的 go 檔" | xargs -0 -r wc -l`。`-r` 讓 GNU xargs 在沒有輸入時不執行指令；macOS 的 xargs 接受 `-r`，本來就不會在空輸入時執行。

### Flag 互斥（exit 2，stdout 為空，不默默忽略）

在 `main.go` 集中成表格 `flagConflicts`，依表格順序第一個符合的生效；錯誤格式照舊為 `nlf: <msg>` 加上 `Run 'nlf --help' for usage.`。

| 組合 | 訊息 |
|------|------|
| `--explain` + `--confirm` | `--explain only shows the plan and --confirm asks before searching; use one of them`（與先前 byte-identical） |
| `-0` + `-l/--long` | `-0/--null prints paths for programs and -l/--long adds size and time for people; use one of them` |
| `-0` + `--explain` | `--explain never searches, so -0/--null has no paths to print; use one of them` |
| `-0` + `-h/--help` | `-h/--help prints text, not NUL-separated paths; use it without -0/--null` |
| `-0` + `--version` | `--version prints text, not NUL-separated paths; use it without -0/--null` |

- `-0` + `--confirm`：允許。計畫與提示照舊寫 stderr；回答 y／Y 後結果以 NUL 分隔寫 stdout；取消（n、Enter、EOF…）時 stdout 為空、exit 0。
- `--json`（B3）尚未實作，目前是 unknown flag（exit 2，有測試固定）；實作時在表格加上與 `-0`、`-l` 的互斥列。
- `-0l`、`-l0`、`-00`、`--null=1`、`--nul` 都是 unknown flag（不支援合併短旗標）。`--` 之後的 `-0` 是查詢字。

### Leading dash（使用者可見的變更）

相對路徑若以 `-` 開頭，在**所有**輸出模式（預設、`-l`、`-0`）都輸出成 `./-…`，例如搜尋位置是 `.` 時的 `-rf.txt` 會印成 `./-rf.txt`，`--dir -sub` 的結果印成 `./-sub/a.txt`。指的是同一個檔案，和 `find .` 以及 `--explain` 顯示的 `find ./-dir` 一致，交給 `xargs` 時不會被當成選項。這只在 `main.go` 輸出時處理，搜尋、排序、排除與 stderr 的權限列表都不變；絕對路徑不受影響。

### 檔名限制

- 檔名不可能包含 NUL，因此 NUL 分隔沒有歧義。預設的換行輸出遇到含換行的檔名時，一個結果會被拆成兩行，README 已說明並推薦 `-0`。
- invalid UTF-8 位元組原樣輸出。APFS 不允許這種檔名（`EILSEQ`），ext4 允許；測試在 macOS 記錄後略過該檔名（不是 `t.Skip`），在 Linux 必須建立成功並正確輸出。

## 變更檔案

| 檔案 | 說明 |
|------|------|
| `tools/nlf/main.go` | `-0/--null`、`flagConflicts` + `checkConflicts`、`displayPath`；help 新增一行並把 `--explain` 說明縮成 2 行（仍是 70 行） |
| `tools/nlf/main_test.go` | 9 個新測試（見下）；既有 doc-example 與 help 測試只加強 |
| `tools/nlf/testdata/help.golden` | 以 `-update` 重新產生 |
| `tools/nlf/scripts/ci-smoke.sh` | hostile filename fixture 與 15 個新檢查（19 → 34） |
| `tools/nlf/README.md` | Usage、pipe 說明、Behavior details |

`.github/workflows/nlf-ci.yml`、`internal/search`、`internal/query` 都沒有修改。

## 測試

- 新測試：`TestParseArgsNull`、`TestFlagConflicts`（6 個 flag 的全部 15 組，恰好 5 組互斥）、`TestNullOutput`（精確 bytes）、`TestNullConflictsCLI`、`TestNullKeepsStderrAndExit`、`TestNullWithConfirm`、`TestDisplayPath`、`TestHostileFilenamesNullXargs`，以及 `TestMain`（讓子行程測試能從測試 binary 執行真正的 `main()`）。
- `TestHostileFilenamesNullXargs`：空白、tab、換行、`-rf.txt`、`--help.txt`、`-n`、開頭空白、結尾空白、引號、反斜線、CJK、invalid UTF-8、`-sub/a.txt`。驗證：
  - `-0` 輸出的集合與精確順序
  - `xargs -0 printf '%s\0'` 還原出相同的 bytes
  - `xargs -0 cat` 讀到每個檔案的內容（證明 `-n`、`-rf`、`--help` 沒有被當成選項）
  - `--dir -sub` 與 `--dir ./-sub`
  - 預設模式與 `-l` 模式都輸出 `./-rf.txt`
- 既有測試只加強：doc-example regex 納入 `-0|--null`（避免 README 的 `-0` 範例沒被檢查）；`TestDocExamplesRun` 強制 `--explain` 時一併移除 `-0/--null`（兩者互斥）；`TestDocsMatchBehavior` 要求文件提到 `--null`；`TestHelpContent` 要求 `-0, --null` 與 `for xargs -0`。
- `ci-smoke.sh`：NUL 資料只經過檔案、`cmp`、`od`、`tail -c`，不經過 `$(...)`（bash 會丟掉 NUL）；不把空輸入送進 `xargs`；相容 bash 3.2。

## 驗證

- Lead（macOS，Go 1.27.1），皆以 exit code 確認為 0：gofmt、`make check`、`go test -race`、`go test -count=10 -shuffle=on`、`make dist`、smoke（`bin/nlf`，bash 5；darwin dist binary，`/bin/bash` 3.2）、`GOOS=linux go vet`。`go test -v`：944 個 PASS、0 個 SKIP。
- Lead 另做 mutant 檢查：讓 `-l` 不經過 `displayPath`，新的 `-l` 斷言會失敗。
- QA Agent：**PASS WITH CHANGES**。另以 `GOTOOLCHAIN=go1.23.0` 跑 vet 與全部測試；用 GNU coreutils 放在 PATH 前面模擬 GNU 工具跑 smoke 與相關測試；9 個 mutant 中 8 個被既有測試抓到，剩下的一個（`-l` 沒有 `./`）已補測試。
- Review Agent：**APPROVE**；兩個 MINOR（`add` closure 錯誤處理一致、Go 測試也固定預設順序）與註解 NIT 已採納。
- **Linux 實機：** 本機沒有 Linux（Docker daemon 沒有啟動），Linux 的 runtime 驗證由 CI 的 `ubuntu-latest` job 負責（unit、race、`ci-smoke.sh` 對 Linux binary）。Lead 無法讀取 private repo 的 Actions 結果，需要人類確認。

## 後續（非 A3 範圍）

- `--explain -l` 與 `--help -l` 目前仍默默忽略 `-l`（A3 之前就是如此）；改成互斥會改變既有行為，建議另外決定後加進 `flagConflicts`。
- help 現在正好 70 行（上限 70，`TestHelpContent` 通過）；A4（`--count` / `-q`）加 flag 時需要再精簡既有文字。
- `nlf "在 -sub 找檔案"` 無法解析（A3 之前就是如此）；`--dir -sub` 可以使用。
