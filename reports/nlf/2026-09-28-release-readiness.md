# NLF Release Readiness Report

日期：2026-09-28
範圍：`tools/nlf` 文件補強（Installation、Linux 私有網路部署）、`make dist`、release 前驗證、commit 與 push

## Summary

- README 的 `## Build` 改寫成 `## Installation`，分成 macOS、Linux、解除安裝三個部分。
  - macOS：從原始碼 build、`make build`、`sudo install -m 755 bin/nlf /usr/local/bin/nlf`，以及 Apple Silicon 與 Intel 的差異。
  - Linux：以「私有網路、目標機器沒有 Go、只能把 binary 帶進去」為主要情境，分 6 個步驟：build → 確認架構 → 傳入 → checksum → 安裝 → 驗證。另外列出 Linux 的使用注意事項。
- 新增 `make dist`：用 `CGO_ENABLED=0 -trimpath -ldflags "-s -w"` 產生 linux/darwin × amd64/arm64 共 4 個靜態執行檔，並產生 `SHA256SUMS`，放在 gitignored 的 `bin/`。
- 同一個 commit 也包含之前三輪都還沒 commit 的變更：fix round 1、`--confirm`、`--help`。

## 關於需求範圍

這次收到的需求訊息在 macOS 安裝範例之後就中斷了。Linux 部分我依據訊息開頭的目標撰寫：私有網路、沒有 Go、只能帶 binary 進去。如果原本還有其他具體要求（例如指定的發行格式、目錄慣例、commit message 格式），請再告訴我。

## Files Changed（這一輪）

| 檔案 | 變更 |
|---|---|
| `tools/nlf/README.md` | 新增 `## Installation`（macOS、Linux 6 步驟、Linux 注意事項、解除安裝）；更新 Known limitations 的平台說明；Development 一節提到 `make dist` |
| `tools/nlf/Makefile` | 新增 `VERSION`、`PLATFORMS` 變數與 `dist` target |
| `tools/nlf/main_test.go` | `TestDocExamplesRun` 的 sandbox 判斷改為以搜尋 root 為準（見下方） |
| `reports/nlf/2026-09-28-release-readiness.md` | 本報告 |

## Linux 教學涵蓋的內容

| 項目 | 內容 |
|---|---|
| 目標機器不需要 Go | 執行檔是靜態連結的（已用 `file` 確認：`ELF 64-bit LSB executable … statically linked`），也不依賴 glibc |
| 選擇架構 | `uname -m`：`x86_64` 對應 `linux-amd64`，`aarch64` 對應 `linux-arm64` |
| 傳入私有網路 | 透過 jump host 用 `scp -J`；或用 USB、內部檔案伺服器。只需要執行檔和 `SHA256SUMS` 兩個檔案 |
| 完整性檢查 | `sha256sum -c --ignore-missing SHA256SUMS` |
| 安裝 | 用 `sudo install` 裝到 `/usr/local/bin`；不需要 sudo 的話，裝到 `~/.local/bin` 並設定 `PATH` |
| 常見問題 | `/tmp` 或家目錄掛載成 `noexec` 時無法執行 |
| 驗證指令 | `--version`、`--explain`、`/var/log`、`/etc`、`/opt/app`、`--confirm --dir /home` |
| Linux 差異 | 搜尋 `/` 時要排除 `/proc`、`/sys`；ext4/xfs 區分大小寫；macOS 的「完整磁碟取用權限」提示不適用；近似指令中的 `stat -f` 在 GNU 系統要改成 `stat -c` |
| 解除安裝 | 刪除執行檔即可，nlf 不會留下其他檔案 |

README 新增的每個 `nlf …` 範例都會被 `TestDocsMatchBehavior` 解析檢查，也會被 `TestDocExamplesRun` 檢查。

## 測試調整

`TestDocExamplesRun` 原本只看**查詢內容**裡的位置，來判斷範例會不會跑到 sandbox 外面。README 新增了 `nlf --dir / …`、`--dir /var/log …` 這類範例之後，這個判斷沒有攔住它們，第一次執行時測試開始唯讀走訪真實的 `/`，逾時後我把它停掉了。這個走訪只有讀取，沒有寫入任何東西。

修正後的規則改以搜尋 root 為準，root 可以來自 `--dir` 或查詢內容：

- 沒有 root：加上 `--dir <sandbox>` 後實際執行。
- root 是 `~/…`：HOME 已指向 sandbox，所以實際執行。
- 其他 root：拿掉 `--confirm`，改用 `--explain` 檢查，只確認能被解析、不實際搜尋。

測試的嚴格度沒有降低：每個範例一樣必須得到 exit 0 或 1。

## Validation Results

| 項目 | 結果 |
|---|---|
| `gofmt -l .` / `go fmt ./...` | 乾淨 |
| `go vet ./...` | ok（darwin，以及 `GOOS=linux` 的 amd64、arm64） |
| `go test ./...` | PASS（201 個 test/subtest，0 fail） |
| `go test -race ./...` | PASS |
| `go test ./... -count=10` | PASS |
| `go build ./...` | ok |
| `make check` | PASS |
| `make dist` | 4 個執行檔；`shasum -a 256 -c SHA256SUMS` 全部 OK |
| Linux 測試編譯 | 3 個套件在 `GOOS=linux` 的 amd64、arm64 下都能用 `go test -c` 編譯 |
| darwin-amd64 dist 執行檔 | `nlf 0.1.0` |
| staticcheck | skipped（未安裝） |

## Release Readiness

**已就緒：**

- 所有 regression、confirm、help 與文件測試都通過。
- 各平台都能產生靜態執行檔，並附有 checksum。
- 安裝與部署流程已寫成文件，文件中的範例由測試持續檢查。

**尚未完成，需要決定或人工驗證：**

1. **版本號**：`main.go` 的 `version` 仍是 `0.1.0`，repo 裡也沒有任何 tag。前面的報告建議把這批變更標為 `v0.1.1`。是否調整版本、是否打 tag，由你決定；這次沒有更動。
2. **Linux 實機執行（manual verification）**：本機的 Docker daemon 沒有啟動，所以 Linux 執行檔還沒有實際跑過。建議在 Linux 主機上執行 `nlf --version`、README 第 6 步的驗證指令，以及 `go test ./...`（在有 Go 的 Linux 上）。
3. macOS TCC 與 iCloud 的人工驗證，以及前幾輪報告的 Remaining Known Issues（heap top-N、Unicode folding、EACCES/EPERM 提示、`-0` 輸出），都還沒處理。

## Git

- 同步：`git fetch origin` 後，`main` 與 `origin/main` 相同（0 ahead / 0 behind），不需要整合。
- commit 內容：只包含 `tools/nlf/` 與 `reports/nlf/` 底下這次任務相關的檔案。`bin/` 是 gitignored 的，不會被 commit。
- 推送：以一般 `git push origin main` 推送，沒有使用 force。
