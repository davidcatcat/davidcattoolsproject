# NLF Autonomous Session State

> 本次 autonomous session 唯一的正式 checkpoint。恢復工作時請先讀這份檔案，再確認 git 的實際狀態。
> 若 checkpoint 與 repo 不一致，以 repo 實際狀態為準，先更新這份檔案再繼續。

Status: COMPLETE
Last updated: 2026-09-28，A3 已 merge 到 main（`4f51340`）

## Session：A3（`-0` / `--null`）

- **Purpose：** 依 roadmap A3 實作 `-0` / `--null`，多 agent 流程，完成後 commit 並 push A3 feature branch，停下來等人類 review。**A3 絕不 merge 到 main。**
- **A2 merge：** 人類確認 GitHub Actions run `36366144449` 在 ubuntu-latest 與 macos-latest 都 success 後授權。main 從 `4db4ec0` fast-forward 到 **`c932fad`**，一般 push，沒有 force，也沒有改寫 history；merge 前 `make check` 與 `go test -race` 都通過。
- **Baseline main SHA：** `c932fad8b746d939f3dd8966ae61732234324b6e`（包含 A1、A2）
- **Baseline version：** 0.4.0
- **Feature branch：** `feat/nlf-roadmap-a3-null-output-20260928`，從 origin/main（`c932fad`）建立，沒有 upstream。
- **Worktree path：** `<session scratchpad>/nlf-a3-worktree`（請用 `git worktree list` 查詢實際路徑）
- **Main touched by A3：** NO
- **範圍：** 只讀 roadmap、A2 report、checkpoint 與 A3 相關的 CLI／output／search 檔案；不重新 audit roadmap。

### A3 design（Lead 草稿）

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

### A3 design review 採納的修改（定稿）

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

### A3 Current phase

- **A3 已 merge 到 main。** 人類確認 GitHub Actions run `36368244400` 在 `ubuntu-latest` 與 `macos-latest` 都 SUCCESS，external review 結論 APPROVE 後授權。
- main 從 `c932fad` fast-forward 到 **`4f513401c53b5304426c759a3266534573ecd21b`**，一般 push，沒有 force，也沒有改寫 history；`origin/main` 包含 `5efb081` 與 `4f51340`。
- merge 後 final validation（在 main `4f51340` 上）：`make check` PASS、`go test -race ./...` PASS、working tree clean。

### A3 Completed work

1. Design review：APPROVE WITH CHANGES（全部採納）
2. Implementation：`-0/--null`、`flagConflicts`、`displayPath`、9 個新測試、help、`ci-smoke.sh`（19 → 34 checks）、README
3. QA：PASS WITH CHANGES（修正已套用）
4. Review：APPROVE（MINOR 與 NIT 已採納）
5. Git Safety：GO
6. A3 報告：`reports/nlf/2026-09-28-null-output.md`
7. commit `5efb081`（nlf: add -0/--null NUL-separated output (A3)）並 push；commit 前以 exit code 確認 make check 與 race 通過
8. PR：沒有 `gh`，請使用 compare 連結 `https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a3-null-output-20260928`

### A3 Exact next action

無（A3 已 merge）。下一個項目：A4（`--explain` / output behavior consistency hardening），在 `feat/nlf-roadmap-a4-explain-parity-20260928` 進行。

### A3 Git state

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

### A2 Decisions already made

- **Workflow 檔案：** `.github/workflows/nlf-ci.yml`（放在 repo 根目錄；目前 repo 還沒有 `.github/`）。
- **Pin 第三方 action 到 commit SHA：** 以 `git ls-remote` 查證，兩個都是 lightweight tag，所以列出的 SHA 就是 commit：
  - `actions/checkout` v7.0.1 → `3d3c42e5aac5ba805825da76410c181273ba90b1`
  - `actions/setup-go` v7.0.0 → `b7ad1dad31e06c5925ef5d2fc7ad053ef454303e`
- **Go 版本：** 使用 `go-version-file: tools/nlf/go.mod`（go 1.23）。沒有任何依賴，也沒有 `go.sum`，所以不使用 cache。
- **Repo 是 private：** 沒有驗證的情況下，GitHub API 回傳 404，本機也沒有 `gh`。因此 **Lead 無法讀取 Actions 的執行結果**。push 之後 workflow 會在 GitHub 上跑，但 Lead 能提供的 CI 結果只有「本機模擬」與「已觸發但未驗證」。不會去讀 keychain 裡的憑證。
- **Linux runtime：** 本機 Docker daemon 沒有啟動，所以 Linux 的實機驗證交給 CI 的 ubuntu job。

### A2 CI design（已定稿；CI design agent 回覆 APPROVE WITH CHANGES，Lead 採納全部修改）

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

### A2 Current phase

- A2 完成，等待人類 review。

### A2 Completed work

1. CI design review：APPROVE WITH CHANGES（全部採納）
2. Implementation：workflow（14 steps）、`scripts/ci-smoke.sh`（19 checks）、README CI 小節、roadmap 修正
3. QA：PASS
4. Review：APPROVE WITH CHANGES（修正已套用）
5. Git Safety：GO
6. commit `e5cb8b5`（ci: add nlf GitHub Actions workflow）並 push
7. **事故紀錄：** Lead 的 pre-commit 指令用了 `| tail`，吃掉了失敗的 exit code，所以 `e5cb8b5` 在測試失敗的狀態下被 commit 與 push。失敗原因：README 的 `bin/nlf "$v"` 被 `TestDocsMatchBehavior`／`TestDocExamplesRun` 當成 nlf 查詢範例。下一個 commit 已把 `$v` 改回不加引號，沒有放寬測試；go test、race、make check、`-count=10 -shuffle=on`、make dist、smoke 全部重新以 exit code 驗證為 0。`e5cb8b5` 的 CI 預期會失敗，之後的 commit 預期會通過。沒有 amend，也沒有 force push。
8. 依 run `36365761225` 結果更新 README（Linux 已由 CI 實際執行）與 A2 報告；沒有改 workflow 與 production code，沒有做 A3。
9. PR：沒有 `gh`，請使用 compare 連結 `https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a2-ci-20260928`

### A2 Exact next action

無。等待人類 review A2 branch 與 GitHub Actions 結果。**不要 merge A2 到 main**，除非人類明確要求。

### A2 Git state

- **Committed（A2）：** YES
- **A2 branch pushed：** YES（`origin/feat/nlf-roadmap-a2-ci-20260928`）
- **Main touched by this task：** NO（A2 只在 feature branch；A1 的 merge 是先前人類明確要求的）
- **PR URL：** 未建立（沒有 `gh`）；compare 連結見上
- **GitHub Actions 結果：** run `36365761225`：ubuntu-latest PASS、macos-latest PASS、Linux runtime PASS、macOS runtime PASS（人類確認並回報；Lead 無法直接讀取）。之後的 docs-only commit 會觸發新的 CI run，需由人類確認。
- **Merge to main：** NOT PERFORMED
