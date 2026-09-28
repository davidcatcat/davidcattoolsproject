# NLF Autonomous Session State

> 本次 autonomous session 唯一的正式 checkpoint。恢復工作時請先讀這份檔案，再確認 git 的實際狀態。
> 若 checkpoint 與 repo 不一致，以 repo 實際狀態為準，先更新這份檔案再繼續。

Status: COMPLETE
Last updated: 2026-09-28，A2 完成並 push 到 feature branch；等待人類 review

## 先前完成的 session：A1（已 merge）

- A1 = 標點安全，報告見 `reports/nlf/2026-09-28-punctuation-safety.md`；roadmap 見 `reports/nlf/2026-09-28-roadmap.md`。
- 人類 review 通過後才 merge 到 main：從 `91ee959` fast-forward 到 **`4db4ec0`**，一般 push，沒有 force，也沒有改寫 history。
- 合併前的 final validation（在 `4db4ec0` 上）：
  - go test：PASS（936）
  - race：PASS
  - make check：PASS
- 版本號仍是 0.4.0。建議版本 0.4.1，但沒有 bump、沒有打 tag，等人類決定。
- 已 push 的 tags 維持 v0.2.0、v0.3.0、v0.4.0，沒有變動。

## Session：A2

- **Purpose：** 依 roadmap A2 建立 GitHub Actions CI 基礎，採用多 agent 流程，完成後 commit 並 push A2 feature branch，然後停下來等人類 review。**A2 絕不 merge 到 main。**
- **Baseline main SHA：** `4db4ec0cf596403dc44c27d9a1540e6cc5082135`（包含 A1）
- **Baseline version：** 0.4.0
- **Baseline tests：** 936 PASS / 0 FAIL / 0 SKIP
- **Feature branch：** `feat/nlf-roadmap-a2-ci-20260928`，從 origin/main（`4db4ec0`）建立，並已 `--unset-upstream`。
- **Worktree path：** `<session scratchpad>/nlf-a2-worktree`（請用 `git worktree list` 查詢實際路徑）
- **Isolation：** git worktree
- **Agent orchestration：** SUBAGENTS USED
- **Main touched by A2：** NO

## Decisions already made

- **Workflow 檔案：** `.github/workflows/nlf-ci.yml`（放在 repo 根目錄；目前 repo 還沒有 `.github/`）。
- **Pin 第三方 action 到 commit SHA：** 以 `git ls-remote` 查證，兩個都是 lightweight tag，所以列出的 SHA 就是 commit：
  - `actions/checkout` v7.0.1 → `3d3c42e5aac5ba805825da76410c181273ba90b1`
  - `actions/setup-go` v7.0.0 → `b7ad1dad31e06c5925ef5d2fc7ad053ef454303e`
- **Go 版本：** 使用 `go-version-file: tools/nlf/go.mod`（go 1.23）。沒有任何依賴，也沒有 `go.sum`，所以不使用 cache。
- **Repo 是 private：** 沒有驗證的情況下，GitHub API 回傳 404，本機也沒有 `gh`。因此 **Lead 無法讀取 Actions 的執行結果**。push 之後 workflow 會在 GitHub 上跑，但 Lead 能提供的 CI 結果只有「本機模擬」與「已觸發但未驗證」。不會去讀 keychain 裡的憑證。
- **Linux runtime：** 本機 Docker daemon 沒有啟動，所以 Linux 的實機驗證交給 CI 的 ubuntu job。

## A2 CI design（已定稿；CI design agent 回覆 APPROVE WITH CHANGES，Lead 採納全部修改）

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

## Current phase

- A2 完成，等待人類 review。

## Completed work

1. CI design review：APPROVE WITH CHANGES（全部採納）
2. Implementation：workflow（14 steps）、`scripts/ci-smoke.sh`（19 checks）、README CI 小節、roadmap 修正
3. QA：PASS
4. Review：APPROVE WITH CHANGES（修正已套用）
5. Git Safety：GO
6. commit `e5cb8b5`（ci: add nlf GitHub Actions workflow）並 push
7. **事故紀錄：** Lead 的 pre-commit 指令用了 `| tail`，吃掉了失敗的 exit code，所以 `e5cb8b5` 在測試失敗的狀態下被 commit 與 push。失敗原因：README 的 `bin/nlf "$v"` 被 `TestDocsMatchBehavior`／`TestDocExamplesRun` 當成 nlf 查詢範例。下一個 commit 已把 `$v` 改回不加引號，沒有放寬測試；go test、race、make check、`-count=10 -shuffle=on`、make dist、smoke 全部重新以 exit code 驗證為 0。`e5cb8b5` 的 CI 預期會失敗，之後的 commit 預期會通過。沒有 amend，也沒有 force push。
8. PR：沒有 `gh`，請使用 compare 連結 `https://github.com/davidcatcat/davidcattoolsproject/pull/new/feat/nlf-roadmap-a2-ci-20260928`

## Exact next action

無。等待人類 review A2 branch 與 GitHub Actions 結果。**不要 merge A2 到 main**，除非人類明確要求。

## Git state

- **Committed（A2）：** YES
- **A2 branch pushed：** YES（`origin/feat/nlf-roadmap-a2-ci-20260928`）
- **Main touched by this task：** NO（A2 只在 feature branch；A1 的 merge 是先前人類明確要求的）
- **PR URL：** 未建立（沒有 `gh`）；compare 連結見上
- **GitHub Actions 結果：** Lead 無法讀取（private repo，沒有 `gh`，不讀 keychain）
- **Merge to main：** NOT PERFORMED
