# NLF Autonomous Session State

> 本次 autonomous session 唯一的正式 checkpoint。恢復工作時請先讀這份檔案，再確認 git 的實際狀態。
> 若 checkpoint 與 repo 不一致，以 repo 實際狀態為準，先更新這份檔案再繼續。

Status: IN PROGRESS
Last updated: 2026-09-28，A5 QA PASS、Review APPROVE（NIT 已修正）；A5 報告已寫；準備 commit

## Session：A5（`--max-depth` / `--min-depth`）

- **Purpose：** 依 roadmap A5 實作 depth control：在走訪時真正剪枝，而不是走完再過濾。多 agent 流程；先寫測試再改 production；commit 並 push A5 branch，停下來等 review。**A5 絕不 merge 到 main；不做 A6。**
- **A4 merge：** 人類確認 run `36371257304` 雙平台 SUCCESS 並 APPROVE 後授權。main 從 `c891fcb` fast-forward 到 `76085f8`，checkpoint 記錄為 `0833f52`；merge 後 `make check`、`go test -race` 通過，working tree clean。
- **Baseline main SHA：** `0833f52054e528f75f39e8cff1e38d37a78b3026`
- **Baseline version：** 0.4.0
- **Feature branch：** `feat/nlf-roadmap-a5-depth-control-20260928`，從 origin/main（`0833f52`）建立，沒有 upstream。
- **Worktree path：** `<session scratchpad>/nlf-a5-worktree`（請用 `git worktree list` 查詢實際路徑）
- **Main touched by A5：** NO

### A5 design（Lead 草稿，待 design agent 確認）

- **Depth：** 搜尋位置 = 0，直接子項目 = 1，依此類推（與 `find` 相同）。symlink root（例如 macOS `/tmp`）一樣是 0。
- **Max-depth 剪枝：** 在 `WalkDir` callback 裡，目錄的 depth >= max 時回傳 `fs.SkipDir`，包括 root 本身（`--max-depth 0` 時 root 目錄不會被讀取）。檔案的 depth 永遠 <= max。深度檢查排在 exclude 比對、`Info()`、開檔之前。
- **Min-depth：** 檔案的 depth < min 時，在 `Info()` 之前就略過；目錄照常走訪。
- **Root 是檔案：** 維持既有行為（root 檔案本身就是 depth 0 的候選結果）。`--max-depth 0` 會輸出它；`--min-depth 1` 不會。和 `find <file> -maxdepth 0 -type f` / `-mindepth 1` 相同。
- **Grammar：** 採用 `--dir` 的風格：`--max-depth N` 與 `--max-depth=N`，不提供短旗標。
  - N 只接受十進位數字。
  - 空值、缺值、負數、非整數、overflow → exit 2。
  - 同一個 flag 重複 → exit 2。
  - `min > max` → 搜尋前 exit 2，不自動交換。
- **Flag interaction：** 可以搭配 `--dir`、`--explain`、`--confirm`、`-l`、`-0`；搭配 `--help` / `--version` 時為 conflict（依 A4 policy）。
- **Query：** 新增 depth 欄位，zero value 必須代表「沒有限制」。parser 不新增自然語言語法。
- **`--explain`：** Conditions 顯示深度；find 指令在 root 之後立刻放 `-mindepth N` / `-maxdepth N`（GNU find 在其他條件後面放 global option 會警告）。BSD 與 GNU 都支援，需實測確認。
- **Help：** 維持 ≤ 70 行：新增一行 `--max-depth N / --min-depth N`，並把 `-h, --help` 與 `--version` 合併成一行。
- **剪枝證明：** 在 boundary 以下放 `chmod 000` 的目錄，以及無法讀取的檔案當作 sentinel。如果被讀取，stderr 會出現權限警告，而測試要求 stderr 必須是空的。另外做 mutation 檢查。

### A5 design review 採納的修改（定稿）

- **C1：** 有 exclude 時，`--explain` 不使用 `-mindepth`，改用 `-type f -path '<root>/*/*'`。原因：BSD 與 GNU find 的 `-mindepth` 都不會在較淺的層級套用任何測試，包括 `-prune`，所以較淺的排除目錄仍會被走訪；nlf 則在任何深度都 prune 排除目錄。BSD 實測：`find r -mindepth 2 -type d \( -iname vendor \) -prune -o -type f -print` 印出了 `r/vendor/v.txt`。沒有 exclude 時才用 `-mindepth N`；`-maxdepth N` 一律放在起點後面。
- **C2：** 深度上限 4096（任何可開啟的路徑都不會更深，也限制 `-path` 呈現的長度）；只接受十進位數字，允許前導 0。
- **C3：** permission trap 放在 depth == max（同時抓 max off-by-one）；另用 0o444 目錄證明 min-depth 在 `d.Info()` 之前檢查。
- **C4：** flag matrix 變成 9 個 flag、36 組、19 個 conflict。
- **C5：** `TestExplainParity` 允許空結果時 exit 1、接受額外 flag，並要求 find 的 stderr 為空（抓 GNU 的 global option 警告）。
- **C6：** `min > max` 檢查放在 `checkConflicts` 之後的 `checkDepthRange`，早於 help、version、explain、confirm 與搜尋。
- **Query：** `MinDepth int`、`MaxDepth int`、`HasMaxDepth bool`，zero value 代表沒有限制。
- **錯誤訊息：**
  - `--max-depth needs a number`
  - `--max-depth needs a whole number from 0 to 4096, got "-1"`
  - `--max-depth can be given only once`
  - `--min-depth 3 is greater than --max-depth 1, so no file can match`
- **Help：** 新增 `--max-depth N    files at most N levels deep; --min-depth N  at least N (1 = directly inside)`，並把 `-h, --help` 與 `--version` 合併成一行，維持 70 行。
- **刻意的差異（文件化）：**
  - `--max-depth 0` 搭配無法讀取的目錄 root 時，nlf 不開啟它（exit 1），BSD find 仍會回報 Permission denied。
  - 深度上限 4096，而 find 接受任意整數。
  - 未排序的 `top N` 搭配深度時，選到的檔案可能不同（沿用 O3）。

### A5 Current phase

- Git Safety → commit → push
- QA：PASS（約 900 組 BSD parity、26 個 mutant 全抓到、與 HEAD 比對 300 組 byte-identical）；Review：APPROVE；NIT 已修正。A5 報告 `reports/nlf/2026-09-28-depth-control.md` 已寫。
- Implementation 完成。先寫測試：在只有欄位的 stub 上，新測試因正確原因失敗（只有 empty tree 案例本來就會通過）。接著修改 production：`search.go` 的 `depthFunc` 與 callback、`main.go`、`explain.go`、`query.go`、help、README、smoke（42 checks）。Implementation agent 做了 14 個 mutant，全部被抓到。Lead 驗證：gofmt、make check、race、`-count=10 -shuffle=on`、make dist、smoke 全部 exit 0，0 SKIP；parser 與 workflow 沒有修改。

### A5 Remaining work

1. ~~Design review / audit~~ DONE（APPROVE WITH CHANGES）
2. ~~先寫測試（確認在舊程式上失敗）~~ DONE
3. ~~Implementation~~ DONE
4. ~~QA + Review（分開）+ mutation~~ DONE
5. Git Safety
6. ~~A5 report `reports/nlf/2026-09-28-depth-control.md`~~ DONE
7. commit + push A5 branch（不 merge main）
8. checkpoint COMPLETE

### A5 Exact next action

等 QA、Review 回報 → 修正（分開）+ mutation → Git Safety → report → commit → push。

### A5 Git state

- **Committed（A5）：** NO
- **A5 branch pushed：** NO
- **Merge A5 to main：** NOT PERFORMED

## 先前的 session：A4 explain parity（已 merge 到 main，`76085f8`）

- **Purpose：** 盤點 `--explain` 與實際搜尋行為的差異，以及默默忽略 flag 的 UX 問題；分類為 bug（修）／intentional approximation（文件化）／UX inconsistency（加 conflict/error）；最小修改 + regression tests；commit 並 push A4 branch，停下來等人類 review。**A4 絕不 merge 到 main。**
- **注意：** 使用者把 A4 重新定義為 explain parity hardening（roadmap 原本的 A4 是 `--count` / `-q`，不在本輪範圍）。
- **Baseline main SHA：** `c891fcb4405a6890c2d3d9bbdbbcabae27296228`（包含 A1、A2、A3 與 A3 merge 紀錄）
- **Baseline version：** 0.4.0
- **Feature branch：** `feat/nlf-roadmap-a4-explain-parity-20260928`，從 origin/main（`c891fcb`）建立，沒有 upstream。
- **Worktree path：** `<session scratchpad>/nlf-a4-worktree`（請用 `git worktree list` 查詢實際路徑）
- **Main touched by A4：** NO
- **原則：** read-only、deterministic、不執行 shell、不提權、看不懂就報錯；不為 parity 改搜尋核心語義（除非確定是 bug）；不碰 A5/A6。

#### A4 Current phase

- **A4 已 merge 到 main。** 人類確認 GitHub Actions run `36371257304` 在 `ubuntu-latest` 與 `macos-latest` 都 SUCCESS，external review 結論 APPROVE 後授權。
- merge 前：`origin/main` = merge base = `c891fcb`，A4 branch ahead 2 / behind 0。
- main 從 `c891fcb` fast-forward 到 **`76085f83a567bb43349c51269d187c32337c67b5`**，一般 push，沒有 force，也沒有改寫 history；`origin/main` 包含 `62f1672` 與 `76085f8`。
- merge 後 final validation（在 main `76085f8` 上）：`make check` PASS、`go test -race ./...` PASS、working tree clean。
- GNU 相關的假設（`-newermt` 小數秒、`! -size -0c`、root guard、`%.9Y`）已由 ubuntu-latest 上的 `TestExplainParity` / `TestGNUSortPipeline` 實際驗證通過。
- A4 完成，等待人類 review。QA：PASS WITH CHANGES；Review：APPROVE WITH CHANGES；修正已套用。報告：`reports/nlf/2026-09-28-explain-parity.md`。

#### A4 Audit 結果與 Lead 決定

分類：(a) bug → 修；(b) intentional approximation → 文件化；(c) 默默忽略 flag → 明確 error。**不改 production search 語義**；修改只在 `internal/query/explain.go`（find 指令的呈現）、`main.go`（flag 解析與互斥）與文件。

- **修（explain 呈現錯誤，a）：**
  - X1／X2：exclude 名稱、exclude 路徑與搜尋位置裡的 `* ? [ \` 沒有跳脫（例如 `exclude "a[1]"` 的指令會 prune `a1`；`exclude "*"` 會 prune 搜尋位置本身）。
  - X3：搜尋位置的名稱等於排除名稱時（`--dir vendor "exclude vendor"`），find 會 prune 起點本身。
  - Z1：`at least 0 bytes` 顯示 `-size +-1c`（GNU find 不接受）。只修 N=0。
  - T1：時間邊界與文件的半開區間 `[a, b)` 不一致（`-newermt` 是嚴格大於；BSD 以整秒比較）。
- **修（近似改為精確，成本低）：**
  - O1：同分排序改成 `LC_ALL=C sort -k1,1nr -k2`。
  - O2：用次秒精度的 stat（macOS `%Fm`、GNU `%.9Y`）。
  - E1：`-iname '?*.ext'`，因為 nlf 不把 `.go` 這種檔名當成有副檔名。
- **明確 error（c）：**
  - F1：`--explain -l`
  - F2／F3：`--help`、`--version` 搭配 `--explain`、`--confirm`、`-l`、`--dir`，或兩者同時使用
  - F4：`--dir ""`、`--dir=`
  - F5：重複 `--dir`
- **只文件化（b）：**
  - O3：未排序的 `top N` 可能選到不同的檔案。
  - G1：`[!…]` 在 nlf 不是否定，要用 `[^…]`。
  - C1：BSD grep 只檢查前 32 KB 判斷 binary，nlf 檢查前 64 KB。
  - C2：invalid UTF-8。
  - C3：Unicode case fold。
  - F6：`--explain` 不檢查搜尋位置是否存在。
  - `--help` / `--version` 後面接查詢字仍允許並文件化（不是 flag）。
- **GNU 相關的假設本機無法驗證**（本機只有 `gstat`、`gsort`，沒有 GNU find／grep）：GNU find 拒絕 `-size +-1c`、`-newermt` 接受小數秒、`%.9Y`。由 ubuntu CI 上的 `TestExplainParity` 驗證。

#### A4 Completed work

1. Audit：DONE
2. Lead 決定範圍：DONE
3. Implementation：DONE
4. QA + Review：DONE
5. Git Safety（Lead 檢查）：OK
6. A4 報告：DONE
7. commit `62f1672`（nlf: harden --explain parity and reject ignored flags (A4)）並 push；commit 前以 exit code 確認 gofmt、make check、race 通過
8. PR：沒有 `gh`，請使用 compare 連結 `https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a4-explain-parity-20260928`

#### A4 Exact next action

無（A4 已 merge）。下一個項目：A5（`--max-depth` / `--min-depth`），在 `feat/nlf-roadmap-a5-depth-control-20260928` 進行。

#### A4 Git state

- **Committed（A4）：** YES
- **A4 branch pushed：** YES（`origin/feat/nlf-roadmap-a4-explain-parity-20260928`）
- **Main touched by this task：** A3 fast-forward merge 到 main（`4f51340`）與 checkpoint commit（`c891fcb`），都是人類明確要求；A4 沒有碰 main。
- **GitHub Actions 結果：** run `36371257304`：ubuntu-latest SUCCESS、macos-latest SUCCESS（人類確認並回報）。
- **Merge to main：** PERFORMED（A4，fast-forward 到 `76085f8`，人類明確授權）

## 先前的 session：A3 `-0` / `--null`（已 merge 到 main，`4f51340`）

- **Purpose：** 依 roadmap A3 實作 `-0` / `--null`，多 agent 流程，完成後 commit 並 push A3 feature branch，停下來等人類 review。**A3 絕不 merge 到 main。**
- **A2 merge：** 人類確認 GitHub Actions run `36366144449` 在 ubuntu-latest 與 macos-latest 都 success 後授權。main 從 `4db4ec0` fast-forward 到 **`c932fad`**，一般 push，沒有 force，也沒有改寫 history；merge 前 `make check` 與 `go test -race` 都通過。
- **Baseline main SHA：** `c932fad8b746d939f3dd8966ae61732234324b6e`（包含 A1、A2）
- **Baseline version：** 0.4.0
- **Feature branch：** `feat/nlf-roadmap-a3-null-output-20260928`，從 origin/main（`c932fad`）建立，沒有 upstream。
- **Worktree path：** `<session scratchpad>/nlf-a3-worktree`（請用 `git worktree list` 查詢實際路徑）
- **Main touched by A3：** NO
- **範圍：** 只讀 roadmap、A2 report、checkpoint 與 A3 相關的 CLI／output／search 檔案；不重新 audit roadmap。

#### A3 design（Lead 草稿）

- `-0` 與 `--null` 完全等價；每個結果路徑後面接一個 NUL（`\0`），不加換行；stdout 只有結果。
- stderr 內容、exit code（0 / 1 / 2）、read-only contract 都不變。
- 互斥（exit 2，錯誤訊息明確，不默默忽略）：
  - `-0` + `-l/--long`（`-l` 是給人看的）
  - `-0` + `--explain`（`--explain` 不搜尋，沒有結果可以輸出）
  - `-0` + `--help`、`-0` + `--version`（它們輸出的是文字，不是結果）
  - 未來 `--json`（B3）也與 `-0` 互斥；目前 `--json` 仍是 unknown flag。
- `-0` + `--confirm`：允許。計畫與提示照舊寫 stderr；確認後結果以 NUL 分隔寫 stdout；取消時 stdout 為空、exit 0。
- flag 互斥改成表格驅動（roadmap 的建議），既有的 `--explain` + `--confirm` 訊息保持不變。
- **Leading dash：** 目前搜尋位置是 `.` 時，`-rf` 這種檔名會原樣輸出成 `-rf`，交給 `xargs` 時會被當成選項。改成：相對路徑若以 `-` 開頭，一律輸出成 `./-rf`（所有輸出模式都一樣；指的仍是同一個檔案，和 `find .` 的慣例一致）。
- `--help` 必須維持 ≤ 70 行、≤ 100 欄：新增 `-0, --null` 說明時用精簡既有文字的方式騰出空間，不修改測試。
- 測試：unit（parseArgs、互斥表）、CLI（`run` 的 stdout bytes、stderr、exit code）、hostile filenames（空白、tab、newline、leading dash、`--help` 當檔名、引號、反斜線、CJK、尾端空白）、`nlf -0 … | xargs -0` 的 runtime 測試（在 `ci-smoke.sh` 與 Go 測試中；Go 測試不能 `os/exec` 於 production code，但測試檔可以）。
- CI skip guard：Linux 不允許 skip，macOS 只允許 `TestGNUSortPipeline`，新測試不可使用 `t.Skip`。
- 版本：維持 0.4.0（roadmap 建議 A3–A5 合併為 0.5.0，由人類決定）。

#### A3 design review 採納的修改（定稿）

- 互斥改成 `main.go` 的 `flagConflicts` 表格 + `checkConflicts`，依表格順序第一個符合的生效；訊息：
  1. explain+confirm：沿用既有訊息（byte-identical）
  2. null+long：`-0/--null prints paths for programs and -l/--long adds size and time for people; use one of them`
  3. null+explain：`--explain never searches, so -0/--null has no paths to print; use one of them`
  4. null+help：`-h/--help prints text, not NUL-separated paths; use it without -0/--null`
  5. null+version：`--version prints text, not NUL-separated paths; use it without -0/--null`
- `-l0`、`-0l`、`-00`、`--null=1`、`--json` 仍是 unknown flag；`--` 之後的 `-0` 是查詢字。
- leading dash：在 `main.go` 輸出時用 `displayPath` 加 `./`（所有模式），不改 `search.go`，排序與 stderr 列表不變。
- `-0` 每筆結果用一次 `io.WriteString(stdout, path+"\x00")`，不加 bufio。
- help：新增 `-0, --null` 一行，`--explain` 說明由 3 行縮成 2 行，維持 70 行。
- 測試：`TestMain` 以環境變數重新執行 `main()` 做子行程測試；`xargs -0 printf` 與 `xargs -0 cat` 的 runtime 測試；invalid UTF-8 在 darwin 允許 `EILSEQ`（`t.Logf`，不 skip），linux 必須建立成功。
- `ci-smoke.sh`：新增 hostile filename fixture，NUL 資料只經過檔案與 `cmp`，不經過 `$(...)`；不把空輸入送進 `xargs`。workflow 不需要修改。
- 既有測試只加強：`docExamples` regex 納入 `-0|--null`；`TestDocExamplesRun` 強制 `--explain` 時一併移除 `-0/--null`。
- 後續（非 A3）：`--explain -l`、`--help -l` 目前仍默默忽略 `-l`。

#### A3 Current phase

- **A3 已 merge 到 main。** 人類確認 GitHub Actions run `36368244400` 在 `ubuntu-latest` 與 `macos-latest` 都 SUCCESS，external review 結論 APPROVE 後授權。
- main 從 `c932fad` fast-forward 到 **`4f513401c53b5304426c759a3266534573ecd21b`**，一般 push，沒有 force，也沒有改寫 history；`origin/main` 包含 `5efb081` 與 `4f51340`。
- merge 後 final validation（在 main `4f51340` 上）：`make check` PASS、`go test -race ./...` PASS、working tree clean。

#### A3 Completed work

1. Design review：APPROVE WITH CHANGES（全部採納）
2. Implementation：`-0/--null`、`flagConflicts`、`displayPath`、9 個新測試、help、`ci-smoke.sh`（19 → 34 checks）、README
3. QA：PASS WITH CHANGES（修正已套用）
4. Review：APPROVE（MINOR 與 NIT 已採納）
5. Git Safety：GO
6. A3 報告：`reports/nlf/2026-09-28-null-output.md`
7. commit `5efb081`（nlf: add -0/--null NUL-separated output (A3)）並 push；commit 前以 exit code 確認 make check 與 race 通過
8. PR：沒有 `gh`，請使用 compare 連結 `https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a3-null-output-20260928`

#### A3 Exact next action

無（A3 已 merge）。下一個項目：A4（`--explain` / output behavior consistency hardening），在 `feat/nlf-roadmap-a4-explain-parity-20260928` 進行。

#### A3 Git state

- **Committed（A3）：** YES
- **A3 branch pushed：** YES（`origin/feat/nlf-roadmap-a3-null-output-20260928`）
- **Main touched by this task：** A2 fast-forward merge 到 main（`c932fad`），由人類在確認 CI run `36366144449` 通過後明確授權；A3 沒有碰 main。
- **GitHub Actions 結果：** run `36368244400`：ubuntu-latest SUCCESS、macos-latest SUCCESS（人類確認並回報）。
- **Merge to main：** PERFORMED（A3，fast-forward 到 `4f51340`，人類明確授權）

## 先前的 session：A2（已 merge 到 main，`c932fad`）

- **Purpose：** 依 roadmap A2 建立 GitHub Actions CI 基礎，採用多 agent 流程，完成後 commit 並 push A2 feature branch，然後停下來等人類 review。**A2 絕不 merge 到 main。**
- **Baseline main SHA：** `4db4ec0cf596403dc44c27d9a1540e6cc5082135`（包含 A1）
- **Baseline version：** 0.4.0
- **Baseline tests：** 936 PASS / 0 FAIL / 0 SKIP
- **Feature branch：** `feat/nlf-roadmap-a2-ci-20260928`，從 origin/main（`4db4ec0`）建立，並已 `--unset-upstream`。
- **Worktree path：** `<session scratchpad>/nlf-a2-worktree`（請用 `git worktree list` 查詢實際路徑）
- **Isolation：** git worktree
- **Agent orchestration：** SUBAGENTS USED
- **Main touched by A2：** NO

#### A2 Decisions already made

- **Workflow 檔案：** `.github/workflows/nlf-ci.yml`（放在 repo 根目錄；目前 repo 還沒有 `.github/`）。
- **Pin 第三方 action 到 commit SHA：** 以 `git ls-remote` 查證，兩個都是 lightweight tag，所以列出的 SHA 就是 commit：
  - `actions/checkout` v7.0.1 → `3d3c42e5aac5ba805825da76410c181273ba90b1`
  - `actions/setup-go` v7.0.0 → `b7ad1dad31e06c5925ef5d2fc7ad053ef454303e`
- **Go 版本：** 使用 `go-version-file: tools/nlf/go.mod`（go 1.23）。沒有任何依賴，也沒有 `go.sum`，所以不使用 cache。
- **Repo 是 private：** 沒有驗證的情況下，GitHub API 回傳 404，本機也沒有 `gh`。因此 **Lead 無法讀取 Actions 的執行結果**。push 之後 workflow 會在 GitHub 上跑，但 Lead 能提供的 CI 結果只有「本機模擬」與「已觸發但未驗證」。不會去讀 keychain 裡的憑證。
- **Linux runtime：** 本機 Docker daemon 沒有啟動，所以 Linux 的實機驗證交給 CI 的 ubuntu job。

#### A2 CI design（已定稿；CI design agent 回覆 APPROVE WITH CHANGES，Lead 採納全部修改）

- **Workflow：** `.github/workflows/nlf-ci.yml`
  - Triggers：
    - `pull_request`：paths 為 `tools/nlf/**`、`.github/workflows/nlf-ci.yml`
    - `push`：branches `main`、`feat/**`，同樣的 paths
    - `workflow_dispatch`
  - **不使用 `pull_request_target`。**
- **權限與安全：**
  - `permissions: contents: read`
  - 沒有 secrets、沒有 publish、沒有上傳 artifact、沒有 cache
  - checkout 設定 `persist-credentials: false`
  - `run:` 裡不使用任何 `${{ github.event.* }}`
  - concurrency：`group: ${{ github.workflow }}-${{ github.ref }}`，`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`
  - `timeout-minutes: 15`
- **Matrix：** `ubuntu-latest`、`macos-latest`，`fail-fast: false`，`working-directory: tools/nlf`，`shell: bash`；job env 設定 `GOTOOLCHAIN=local`、`GOPROXY=off`。
- **Pin：**
  - checkout `3d3c42e5aac5ba805825da76410c181273ba90b1`（v7.0.1）
  - setup-go `b7ad1dad31e06c5925ef5d2fc7ad053ef454303e`（v7.0.0），搭配 `go-version-file: tools/nlf/go.mod`、`cache: false`
  - `go 1.23` 會安裝 1.23.12
- **Steps（依序）：**
  1. go version / env
  2. `make fmt-check`
  3. `make vet`
  4. `go test -count=1 -v ./...` 並 tee 成 log，接著做 skip guard：
     - ubuntu：不允許任何 SKIP
     - macOS：只允許 `TestGNUSortPipeline` skip，並記錄 gstat 是否存在
  5. `go test -race ./...`
  6. `go test -count=10 -shuffle=on ./...`
  7. `make build`
  8. `make dist`
  9. `SHA256SUMS` 必須剛好 4 行，而且是預期的 4 個名稱；再執行 `-c` 驗證（Linux 用 `sha256sum`，macOS 用 `shasum -a 256`）
  10. 靜態 binary 檢查：
      - Linux 的兩個 binary：`file` 顯示 statically linked
      - 4 個 dist binary：`go version -m` 顯示 `CGO_ENABLED=0`
  11. Runtime smoke：`scripts/ci-smoke.sh <binary> <version>`，對 `bin/nlf` 與符合 runner OS/arch 的 dist binary 各跑一次（`uname` 對應到 GOOS/GOARCH，並和 `go env GOHOSTOS GOHOSTARCH` 交叉比對，不一致就失敗）
  12. `git status --porcelain` 必須為空
- **Smoke script：** `tools/nlf/scripts/ci-smoke.sh`
  - fixture 放在 `mktemp -d`，並用 `pwd -P` 取得真實路徑
  - 檢查 exit code：0（找到）、1（沒找到）、2（錯誤）
  - `--explain`
  - `--confirm`：輸入 n、y、EOF；取消時 exit 0
  - `--version` 必須與 `main.go` 的版本相同
  - 以 cksum snapshot 證明是 read-only
  - 不使用 `rm`；在 `set -euo pipefail` 下正確擷取 exit code
- **Roadmap 修正：** CI Strategy 中的檔名 `nlf.yml` 改成 `nlf-ci.yml`。
- **已知的後續事項（本輪不修）：** 在 `TZ=Pacific/Auckland` 下，`TestExplainDoesNotSearch` 與 `TestLongOutput` 會失敗，因為 `testNow` 剛好落在 DST 開始日，而字串是寫死的。CI 使用 UTC，不受影響。**不放寬 assertion。**
- **不做：** `ubuntu-24.04-arm`（repo 是 private）；在 macOS 上 `brew install coreutils`（改為接受 skip）。

#### A2 Current phase

- A2 完成，等待人類 review。

#### A2 Completed work

1. CI design review：APPROVE WITH CHANGES（全部採納）
2. Implementation：workflow（14 steps）、`scripts/ci-smoke.sh`（19 checks）、README CI 小節、roadmap 修正
3. QA：PASS
4. Review：APPROVE WITH CHANGES（修正已套用）
5. Git Safety：GO
6. commit `e5cb8b5`（ci: add nlf GitHub Actions workflow）並 push
7. **事故紀錄：** Lead 的 pre-commit 指令用了 `| tail`，吃掉了失敗的 exit code，所以 `e5cb8b5` 在測試失敗的狀態下被 commit 與 push。失敗原因：README 的 `bin/nlf "$v"` 被 `TestDocsMatchBehavior`／`TestDocExamplesRun` 當成 nlf 查詢範例。下一個 commit 已把 `$v` 改回不加引號，沒有放寬測試；go test、race、make check、`-count=10 -shuffle=on`、make dist、smoke 全部重新以 exit code 驗證為 0。`e5cb8b5` 的 CI 預期會失敗，之後的 commit 預期會通過。沒有 amend，也沒有 force push。
8. 依 run `36365761225` 結果更新 README（Linux 已由 CI 實際執行）與 A2 報告；沒有改 workflow 與 production code，沒有做 A3。
9. PR：沒有 `gh`，請使用 compare 連結 `https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a2-ci-20260928`

#### A2 Exact next action

無。等待人類 review A2 branch 與 GitHub Actions 結果。**不要 merge A2 到 main**，除非人類明確要求。

#### A2 Git state

- **Committed（A2）：** YES
- **A2 branch pushed：** YES（`origin/feat/nlf-roadmap-a2-ci-20260928`）
- **Main touched by this task：** NO（A2 只在 feature branch；A1 的 merge 是先前人類明確要求的）
- **PR URL：** 未建立（沒有 `gh`）；compare 連結見上
- **GitHub Actions 結果：** run `36365761225`：ubuntu-latest PASS、macos-latest PASS、Linux runtime PASS、macOS runtime PASS（人類確認並回報；Lead 無法直接讀取）。之後的 docs-only commit 會觸發新的 CI run，需由人類確認。
- **Merge to main：** NOT PERFORMED
