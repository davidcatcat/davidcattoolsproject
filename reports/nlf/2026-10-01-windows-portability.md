# nlf A8：Windows portability

日期：2026-10-01
分支：`feat/nlf-a8-windows-portability`（從 main `3d296a7` 建立；**未 merge 到 main**）
版本：維持 0.4.0（沒有 bump、沒有 tag）

**狀態：A8 在 CI 上完成驗證，等待 final review，尚未 merge。** GitHub Actions run `36759759377`（commit `787653e`）在 `ubuntu-latest`、`macos-latest`、`windows-latest` 上全部 SUCCESS。Windows job 的每一個步驟都通過：原生 `go test`、shuffle、fuzz、build、兩個 release build、PowerShell 7／Windows PowerShell 5.1／cmd.exe smoke、working tree clean。同步 main 之後的 CI 結果見「GitHub Actions」。Windows 10／11 桌面版與 `windows/arm64` 實機仍待人工驗證（見最後一節），完成之前 README 不宣稱 Windows 是第一級支援平台。

## 範圍

A8 讓同一份 Go 原始碼可以在 Windows 10／11 與 Windows Server（amd64、arm64）上 build 與執行。產品契約不變：

- single static binary（`CGO_ENABLED=0`）
- read-only、deterministic
- 不執行 shell 或其他程式
- 不提權
- 只用標準函式庫，沒有新增依賴（也沒有使用 `golang.org/x/sys/windows`）

## Production 變更

| 項目 | 檔案 | 內容 |
|------|------|------|
| Build | `Makefile` | `PLATFORMS` 加入 `windows/amd64`、`windows/arm64`，Windows 執行檔加上 `.exe`；`SHA256SUMS` 變成 6 行。`make build` 用 `go env GOEXE`，Windows 上會產生 `bin/nlf.exe`。 |
| 平台 | `internal/query/explain.go` | 新增 `query.Windows`，`PlatformFor("windows")` 回傳它。新增 `HasFindCommand()`：Windows 回傳 false。新增 `NoCommandNote(lang)`。 |
| `--explain` | `explain.go` | Windows 上只顯示條件摘要（和 macOS、Linux 逐字相同），最後附一行說明為什麼沒有指令，不輸出假的 `find` 指令。stdout 不變。 |
| `--confirm` | `main.go` | 執行計畫以同一行說明取代「Approximate … command」段落，安全性摘要不變；計畫仍寫到 stderr。 |
| 搜尋位置是 link | `internal/search/search.go` | `RootIsLink`：Lstat 不是目錄、Stat 是目錄，就是指向目錄的 link。涵蓋 symlink，以及 Windows 的 junction（Go 1.23 的 Lstat 不把 junction 回報成 symlink）。行為是加上結尾的分隔符號，讓 WalkDir 的 Lstat 跟隨它。在 Unix 上這個條件和原本的 `ModeSymlink` 判斷完全等價。 |
| 深度 | `search.go` `depthFunc` | 處理 drive-relative 的搜尋位置 `C:`（`Clean` 會變成 `C:.`，子路徑是 `C:name`）。修正前，它的子路徑深度被算成 0。UNC 與 `C:\` 原本就正確，新增測試固定下來。 |
| 排除路徑 | `search.go` `NormalizeExcludes` | 新增 `isRooted`：以分隔符號開頭（`\x`）或有磁碟代號（`C:x`）的路徑，先用 `filepath.Abs` 轉成絕對路徑，再檢查是否在搜尋位置之內。修正前，Windows 上的 `排除 /tmp/x` 會被默默當成相對路徑 `tmp/x`。在 Unix 上 `isRooted` 等於 `IsAbs`，行為不變。 |
| `~` | `main.go` `expandHome` | `~` 後面接 `os.IsPathSeparator` 的字元時展開，所以 Windows 也接受 `~\`。Unix 上 `\` 是一般的檔名字元，`~\x` 不變。 |
| `-` 開頭的路徑 | `main.go` `displayPath` | 前綴改成 `.` 加上 OS 的分隔符號：Windows 是 `.\-x`，Unix 仍是 `./-x`。 |
| 權限提示 | `main.go` `permissionHint` | 新增 Windows 分支：建議到「內容 › 安全性」或用 `icacls` 檢查，並改用有權限的帳號執行；nlf 不會以系統管理員身分執行。 |
| 查詢裡的 Windows 路徑 | `internal/query/errors.go`、`parse.go` | `C:\…`、`c:/…`、`\\server\share` 當作一個詞，或在位置詞（`in`、`在`、`under`…）後面以引號出現時，一律 exit 2，並提示改用 `--dir`。 |
| Help | `main.go`、`testdata/help.golden` | `--explain` 那一行改為 `(macOS, GNU/Linux; not on Windows)`。 |
| 換行 | `tools/nlf/.gitattributes` | `* text=auto eol=lf`；只有 `*.cmd` 用 `eol=crlf`。Git for Windows 預設 `core.autocrlf=true`，否則會把 Go 原始碼、help golden、README 與 fuzz corpus 轉成 CRLF，gofmt 與逐 byte 比對的測試都會失敗。所有既有檔案本來就是 LF，所以沒有內容變更。 |

### 路徑語意：logical 與 OS-native 分開

- **查詢文字裡的路徑是 logical path，在每個 OS 上都用 `/`**：`./src`、`~/Documents`、`../x`、`排除 src/gen`。這是既有的產品語意，沒有改變。在 Windows 上，Go 的 `path/filepath` 也接受 `/`。
- **`--dir` 是 OS-native path**，原樣交給 `path/filepath`：磁碟代號、反斜線、UNC、正斜線、結尾的 `\`、含空白與中文的路徑都可以。輸出的路徑是 WalkDir 產生的原生形式（使用 `\`）。
- **為什麼要把查詢裡的 Windows 路徑變成錯誤**：base 版本會把 `in "D:/data" find go files` 理解成「在目前目錄搜尋內容含 D:/data 的 go 檔」（exit 1，或找到錯的檔案），違反「看不懂就報錯，不默默改變意思」的原則。只有在位置詞後面、看起來像 Windows 路徑時才報錯；`files containing "C:\Windows"` 這種內容搜尋照常可以用。這是 A8 唯一的語意收緊，所有 OS 都一樣。

### 刻意不做

- 不為 Windows 產生 PowerShell 或 cmd 的「對應指令」：`Get-ChildItem` 或 `where` 的大小、時間、內容條件和 nlf 的語意（半開區間、binary 判斷、大小寫）差異太大，顯示出來反而容易誤導。
- 不改變查詢語法，也不接受查詢裡的反斜線路徑。
- 不處理 Windows 的「系統」屬性、ADS（alternate data streams）、長路徑登錄設定。

## Windows 檔案系統語意（有測試與原因）

| 情況 | 行為 | 原因 | 測試 |
|------|------|------|------|
| 搜尋位置是 junction 或 symlink | 跟隨 | 與 Unix 上 symlink 的搜尋位置（`/tmp`）一致 | `TestWindowsJunctions`、`TestRootIsLinkWindows` |
| 位置以下的 junction 或 symlink | 不跟隨、不列出 | 與 Unix 上不跟隨目錄 symlink 一致，並避免循環 | `TestWindowsJunctions` |
| 隱藏屬性的檔案與目錄 | 照樣搜尋 | 與 Unix 上 `.` 開頭的檔案一致（README 早已寫明隱藏檔會被搜尋） | `TestWindowsHiddenFilesAreSearched` |
| 權限不足 | 略過並列在 stderr，搜尋位置本身不可讀時 exit 2 | 既有行為；Windows 用 ACL | 既有權限測試改用 `denyAccess`：Windows 上是 `icacls /deny *S-1-1-0:(RD)`，先在測試 process 停用 `SeBackupPrivilege`／`SeRestorePrivilege`，並實際讀取確認存取真的被拒絕（見「GitHub Actions」） |
| 大小寫 | NTFS 不分大小寫：大小寫不同的排除路徑也會排除（用 `os.SameFile` 判斷） | 既有設計，與 APFS 相同 | `TestWindowsDriveAndUNCPaths`、`TestExcludePathCaseFollowsFileSystem` |
| NFC／NFD | 不做正規化，兩種拼法是不同名稱 | 與 Linux 相同 | `TestUnicodeNormalizationMatrix`（default 分支） |
| 磁碟根目錄、`C:`、UNC | 深度與輸出都正確 | `depthFunc` 修正 | `TestWindowsDriveAndUNCPaths`、`TestDepthFuncWindowsPaths` |
| 無效的 UTF-8 | 命令列是 UTF-16，位元組在傳到 nlf 之前就變成 U+FFFD；建立檔名時也一樣 | OS 的限制，不是 nlf 的行為 | `TestInvalidUTF8QueryCLI`、`TestHostileFilename*`（Windows 分支） |

`runtime.GOOS` 分支只出現在以下位置：

- `PlatformFor`、`permissionHint`（production）
- 測試裡 OS 本身行為就不同的地方：不合法的檔名、UTF-16、NTFS 的 100ns 時間精度、`xargs`、`/bin/sh`，以及查詢裡不能寫 Windows 路徑

其他 production 變更都靠 `path/filepath` 與 `os.IsPathSeparator` 達成，不用 GOOS 判斷。

## 測試

**各平台都會跑的新測試：**

- `TestExplainOnWindows`：Windows 的 `Explain` 有全部條件與說明，沒有任何 Unix 指令，條件和 macOS 逐字相同。
- `TestExecutionPlanOnWindows`：Windows 的 `--confirm` 計畫。
- `TestWindowsPathInQuery`：查詢裡的 Windows 路徑會報錯，而內容搜尋不受影響。
- `TestExpandHome`
- `TestPlatformFor`：新增 Windows。
- `TestPermissionHintByOS`：新增 Windows 那一行。
- `TestDisplayPath`：改用 OS 的分隔符號。

**只在 Windows 上跑的新測試（`//go:build windows`）：**

- `TestWindowsDriveAndUNCPaths`：磁碟代號、正斜線、結尾反斜線、不同大小寫、`C:`、drive root、`\\localhost\C$\…` UNC、UNC share root、Windows 絕對路徑與目前磁碟上的 rooted 排除路徑、搜尋位置之外的排除路徑。
- `TestWindowsJunctions`
- `TestWindowsHiddenFilesAreSearched`
- `TestWindowsHomeAndSpacesAndUnicode`
- `TestDepthFuncWindowsPaths`
- `TestRootIsLinkWindows`

**既有測試改成可以在 Windows 上跑（macOS／Linux 的斷言沒有放寬）：**

- `paths()` 與 search 的 `run()` 用 `filepath.ToSlash`，預期值仍然寫成 `/`。
- `setHome` 同時設定 HOME 與 USERPROFILE。
- find 指令的斷言集中到 `checkCommand`。在 macOS／Linux 上比對的字串和原本逐字相同；在 Windows 上改為檢查說明文字，並且確認沒有任何 Unix 指令。
- 權限測試改用 `denyAccess`／`allowAccess`：Unix 上是 chmod，Windows 上是 icacls deny ACE。這個 ACE 會在 t.TempDir 刪除目錄之前移除。
- 時間邊界測試用 `mtimeTick`：Unix 是 1ns，Windows 上 NTFS 的精度是 100ns。沒有它，這些測試在 Windows 上會 skip。
- Windows 不能建立的檔名（控制字元、`" * ? | \`、結尾空白）只在 Unix 上測。
- 查詢裡的絕對路徑，Windows 上改為 chdir 之後使用相對路徑。

**Windows 上允許 skip 的測試（CI 的 skip guard 明確列出）：**

- `TestExplainParity`（BSD／GNU find）
- `TestGNUSortPipeline`（GNU stat）
- `TestShellQuoteShellRoundTrip`（`/bin/sh`）

它們都需要 Windows 上不存在的 Unix 工具，而且只驗證 Windows 不會顯示的 Unix 指令。

## CI

`.github/workflows/nlf-ci.yml`：

- **ubuntu／macos job：** `SHA256SUMS` 必須剛好是 6 個執行檔，`CGO_ENABLED=0` 的檢查涵蓋 6 個。其餘步驟不變，race detector 仍然執行。
- **新增 `test-windows` job（`windows-latest`）：**
  - 不用 make，直接執行對應的 `go` 指令。
  - 依序執行：換行檢查、gofmt、vet、`go test -count=1 -v` 加上 skip guard、`-count=10 -shuffle=on`、fuzz smoke、native build、兩個 Windows release 執行檔的 `CGO_ENABLED=0` build 與雜湊驗證。
  - smoke test：`scripts/ci-smoke.ps1` 在 PowerShell 7 上跑兩個 binary，在 Windows PowerShell 5.1 上跑 dist binary；`scripts/ci-smoke.cmd` 在 cmd.exe 上跑兩個 binary。
  - 最後確認工作目錄沒有被改動。
- **race detector 在 Windows 上沒有嘗試。** 它需要 cgo 與 C 編譯器（mingw-w64）。runner 的映像檔可能內建 gcc，但這個 job 刻意維持 cgo-free，不依賴映像檔剛好附帶的 C 編譯器（`GOTOOLCHAIN=local`、`GOPROXY=off`、只用標準函式庫）。workflow 有一個步驟明確印出這個原因。會被 race 檢查的程式碼（walker、內容掃描、heap）在所有 OS 上是同一份，仍在 ubuntu 與 macos 上執行 `-race`。

## 驗證

**本機（macOS，Go 1.27.1），以最後一次修改為準，全部以 exit code 確認為 0：**

| 步驟 | 結果 |
|------|------|
| gofmt | 0（沒有檔案需要格式化） |
| `make check` | 0 |
| `go test -race ./...` | 0 |
| `go test -count=10 -shuffle=on ./...` | 0 |
| `go test -count=1 -v ./...` | 0；1550 個 PASS、0 個 SKIP |
| fuzz smoke（4 × 5 秒） | 0 |
| `make dist` | 0；6 個執行檔，`SHA256SUMS` 6 行，`shasum -c` 全部 OK |
| Windows 執行檔 | `file`：PE32+ x86-64 與 Aarch64；`go version -m`：`CGO_ENABLED=0` |
| `scripts/ci-smoke.sh`（`bin/nlf` 與 darwin dist） | 0／0 |
| `GOOS=windows go vet ./...`、`GOOS=linux go vet ./...` | 0／0 |
| `GOOS=windows`／`linux` 的 `go test -c`（3 個 package） | 全部可以編譯 |

**base（`3d296a7`）與 A8 binary 在 macOS 上的對照：** 27 組指令中有 24 組 byte-identical。3 組不同，全部是預期的：

1. `--help` 的一行說明。
2. `in "D:/data" find go files`：從「內容搜尋，exit 1」變成「Windows 路徑錯誤，exit 2」。
3. `go files in C:\Users\me`：本來就是 exit 2，現在的訊息更明確。

以下都相同：`--explain`、`--confirm`、`-0`、`-l`、`./-dash`、排除路徑、`~/`、`~\x`（Unix 上不展開）、各種 exit code。

**CI 上的原生執行**（Windows 與 Linux），見下一節。

## GitHub Actions

| Run | Commit | ubuntu | macos | windows | 說明 |
|-----|--------|--------|-------|---------|------|
| `36755112741` | `274d12e` | SUCCESS | SUCCESS | FAILURE | 停在 `Line endings`，後面的步驟都沒有執行 |
| `36755846288` | `76b9fd7` | SUCCESS | SUCCESS | FAILURE | 第一次在 Windows 上實際執行 `go test`，6 個權限測試失敗 |
| `36759140100` | `db3bdc1` | SUCCESS | SUCCESS | FAILURE | 測試全部通過，Windows PowerShell 5.1 smoke 有 2 項失敗 |
| `36759759377` | `787653e` | SUCCESS | SUCCESS | **SUCCESS** | Windows 每一個步驟都 SUCCESS |

每次失敗都先讀 log 找出確切原因，才做修正。沒有把任何測試加進 skip 清單，也沒有放寬任何 assertion。

**1. `Line endings`（run `36755112741`）：錯在檢查方法，不是換行規則。**

- 這一步用 Git Bash 的 GNU grep 找 `\r`。但在 Windows 上，grep 會先把文字檔的 CR 拿掉再比對。
- 結果 `grep -q $'\r$' scripts/ci-smoke.cmd` 永遠找不到 CR：checkout 是正確的 CRLF，這一步卻失敗。反過來說，「其他檔案不能有 CR」那一項也永遠抓不到 CR，檢查形同虛設。
- 確認 `.gitattributes` 沒有問題：67 個 tracked 檔案在 index 裡都是 LF、沒有 CR，checkout 後只有 `.cmd` 是 CRLF。
- 修正（`76b9fd7`）：改用 PowerShell 的 `[IO.File]::ReadAllBytes` 直接看位元組。`*.cmd` 必須全部是 CRLF，其他 tracked 檔案不能有任何 CR，而且至少要檢查到一個 `.cmd`。同時印出 `git ls-files --eol`。
- run `36755846288` 的結果：`line endings ok: 1 CRLF .cmd file(s)`。

**2. 權限測試（run `36755846288`）：錯在測試的假設。**

- 失敗的 6 個測試：`TestPermissionDeniedMessage`、`TestDepthPruningCLI`、`TestPermissionDeniedIsSkippedAndReported`、`TestMaxDepthDoesNotReadBelow`、`TestMinDepthSkipsBeforeStatAndOpen`、`TestSortedLimitPermissionDenied`。
- 預期：被 deny 的目錄與檔案回報為 PermissionDenied；搜尋位置本身被 deny 時 exit 2。
- 實際：icacls 成功加上 deny entry，但目錄照樣列得出來、檔案照樣讀得到。所以 PermissionDenied 是空的，搜尋位置被 deny 時 exit 1。
- 原因：Go 在 Windows 上唯讀開啟檔案與目錄時，一律使用 `FILE_FLAG_BACKUP_SEMANTICS`。token 啟用了 `SeBackupPrivilege` 時，系統會略過 ACL 檢查，這就是 Windows 版的「root 不受權限限制」。
- 證據：run `36759140100` 的 log 印出 `whoami /priv`，runner 的 `SeBackupPrivilege` 與 `SeRestorePrivilege` 都是 **Enabled**。
- 修正（`db3bdc1`，只改測試）：`denyAccess` 先在測試 process 停用這兩個特權，並實際讀取一次，確認存取真的被拒絕。沒有被拒絕時，會附上 icacls 與特權清單直接失敗，不會默默跳過。nlf 本身從來不調整特權。
- run `36759140100` 的結果：Windows 上 1553 個 PASS，只有 skip guard 允許的 3 個測試被略過。

**3. Windows PowerShell 5.1 smoke（run `36759140100`）：錯在 smoke script 的 stdin。**

- 只有「回答 `y`」的兩項失敗：`--confirm answered y` 與 `-0 --confirm answered y`。n 與 EOF 的案例原本就會取消，所以看不出問題；PowerShell 7 全部通過。
- 原因：.NET Framework 的 `Process.StandardInput` 在 process 啟動時，就寫出 `Console.InputEncoding` 的 preamble。所以 nlf 讀到的是 `\ufeffy` 而不是 `y`，照規則取消，這是正確的行為。
- 修正（`787653e`）：smoke script 改用不帶 BOM 的 UTF-8 作為 `Console.InputEncoding`，失敗時印出 stderr。
- nlf 不改：`--confirm` 仍然只接受剛好是 `y` 或 `Y` 的一行。使用者在 5.1 用 `'y' | nlf --confirm` 時，PowerShell 用的是 `$OutputEncoding`，不會加 BOM。

**run `36759759377` 的 Windows 結果：**

- `go test -v`：1553 個 PASS，skip 的只有 `TestExplainParity`、`TestGNUSortPipeline`、`TestShellQuoteShellRoundTrip`。
- PowerShell 7：兩個 binary 共 84 項 PASS。
- Windows PowerShell 5.1：dist binary 42 項 PASS。
- cmd.exe：兩個 binary 共 28 項 PASS。
- line endings、gofmt、vet、`-count=10 -shuffle=on`、fuzz smoke、build、amd64／arm64 release build（`CGO_ENABLED=0`、`SHA256SUMS`）、working tree clean 全部 SUCCESS。

**同步 main：**

- 用 `git merge origin/main` 合併 `0db76b5`，得到 merge commit `b20eef7`。沒有 force push，也沒有 rebase。
- 沒有 conflict。main 新增的 16 個 commit 只改 `tools/llmtool` 與 `reports/llmtool*`，沒有碰 `tools/nlf`、`.github` 或 `reports/nlf`。merge 前後 A8 的檔案完全相同。
- 合併後的 CI 用 `workflow_dispatch` 在最新的 HEAD 上重跑。那次 run 的 ID 記在 roadmap 與最後的回報裡：這份報告無法記錄驗證它自己的那一次 run。

## Review

獨立的 Review agent：**APPROVE WITH CHANGES**。已經根據 Go 1.23.0 的原始碼確認以下幾點：

- Windows 的 `os.Lstat` 在路徑以分隔符號結尾時，會跟隨 name-surrogate（跟隨 junction 所需）。
- `go 1.23` 預設的 `winsymlink` 行為會把 junction 回報成 `ModeIrregular`。
- `depthFunc` 對 `C:`、`C:.`、`C:\`、UNC 都正確。
- Unix 上的行為與 base 等價。
- 測試在 Unix 上沒有被放寬。
- `.gitattributes` 不會改動任何既有檔案。

找到的問題全部已修正（這次的後續 commit）：

1. **major：** `ci-smoke.cmd` 的 `:code` 把名稱放在括號區塊裡。名稱含 `)` 時（例如 `pem files (no match)`），區塊會提早結束，錯誤的 exit code 可能被當成通過。現在改成不用括號區塊。
2. **README 和 workflow 對不上：** README 說 native build 是 `CGO_ENABLED=0`，但 workflow 沒有設定。現在 workflow 明確設定 `CGO_ENABLED=0`。
3. **race detector 的原因寫得不精確：** 原本寫「CI 不安裝 C 編譯器」，但 runner 可能內建。現在改成實際的狀況：刻意不依賴它，所以沒有嘗試。
4. **README 的 UNC 範例：** Windows 上的 `TestDocExamplesRun` 會 Lstat 這個路徑，而原本的主機不存在，會做網路查詢。範例改成 `\\localhost\logs`。
5. **`ci-smoke.ps1` 的比對不分大小寫：** PowerShell 的 `-eq` 不分大小寫，連 base64 的比對也受影響。現在改用 `-ceq`／`-cne`／`-ccontains`。
6. **小問題：** `~` 的 PowerShell 說明、「支援」字眼改成「目標平台」、`main.go` 註解裡的 equivalent。

Review 列出第一次 Windows run 可能失敗的地方：

- `\\localhost\C$` 是否可以連線
- `C:` 的 ReadDir
- 不加結尾分隔符號的 share root
- cmd 的 `find /c` 能否正確計算只用 LF 換行的輸出行數

這些如果出問題，都會明確失敗，不會默默通過。

## 使用者可見的變更

- 新增 Windows 執行檔與安裝說明。
- 在所有 OS 上，查詢文字裡的 Windows 路徑會 exit 2，並提示改用 `--dir`。
- `--help` 的一行說明。
- macOS 與 Linux 上的搜尋結果、`--explain`、`--confirm`、stdout／stderr 與 exit code 都沒有變。

## 已知限制與剩下的風險

- **Windows Server 已在 CI 上原生驗證**（run `36759759377`）。原本最不確定的幾項都已經在 runner 上實際通過：administrative share UNC、255 字元的檔名、PowerShell 5.1 的引數傳遞、cmd.exe 批次檔。icacls deny 需要先停用 backup 特權，才能真的拒絕存取，見上文。
- **只有 CI 的 Windows Server 會被驗證。** Windows 10／11 桌面版、`windows/arm64` 實機、長路徑（超過 260 字元）、網路磁碟機代號、OneDrive 檔案隨選、防毒軟體的影響都需要人工驗證。
- **race detector 沒有在 Windows 上嘗試**（原因見 CI）。之後如果要啟用，需要另外決定是否依賴 runner 的 C 編譯器。
- **Windows 上沒有 xargs。** `-0` 的輸出本身會逐 byte 驗證，但沒有測試接到其他程式的情況。
- **查詢裡以 `/` 開頭的路徑**（`under /tmp`）在 Windows 上指的是目前磁碟的根目錄。這是 `path/filepath` 的語意，已寫進 README。
