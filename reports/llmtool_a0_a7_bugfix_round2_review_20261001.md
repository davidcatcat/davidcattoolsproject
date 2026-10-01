# llmtool A0–A7 bug-fix 分支 Round 2 獨立檢查（2026-10-01）

## 範圍

- 檢查對象：分支 `fix/llmtool-a0-a7-review-bugs`。
  - 檢查時的 HEAD 是 `02af9d22a3d419c4cda0116b6e007f55d7a9c1a3`，也就是 Round 2 的 `c096e85`（實作）與 `02af9d2`（CI 證據）。
  - 寫報告時，分支已前進到 `f2335cf`。它只在 `reports/llmtool_a0_a7_bugfix_validation_20261001.md` 新增 28 行文件，程式碼、測試與 workflow 都和 `02af9d2` 相同，所以以下結論也適用於 `f2335cf`。
- 對照：[Round 1 獨立檢查](llmtool_a0_a7_bugfix_review_20261001.md)列出的 4 個 validator 瑕疵、`git checkout -- .`、L12，以及「沒有 CI」的觀察。
- 方式：
  - 唯讀。用 detached worktree checkout 分支，**沒有修改、commit 或 merge 該分支**。
  - 測試在 scratchpad 進行，stdin 指向 `/dev/null`。
  - CI 結果用 `gh` 直接向 GitHub 查詢。

## 結論

**可以 merge。** 分支回報的內容屬實：

- Round 1 的瑕疵全部修好。
- L12 修好。
- 補上了 llmtool CI，而且實際跑過。
- 先前的修正沒有退步。

剩下的是一組同類型的 low 等級不一致，以及 CI 設定可以更嚴格，都建議記成 follow-up。

## 分支狀態

- 相對 main（`5bb8b7e`）：ahead 4（`02af9d2` 當時）、behind 0。用 merge commit `f26cb6f` 同步了 main。
- 和 main 不同的檔案只有：
  - `tools/llmtool/**`
  - `.github/workflows/llmtool-ci.yml`
  - 根目錄的 `README.md`
  - `reports/llmtool_a0_a7_bugfix_validation_20261001.md`

  main 上的其他內容（包括兩份獨立審查報告）都保留著。

## 測試（獨立執行）

| Python | 結果 |
|--------|------|
| 3.13.3（`tools/llmtool/.venv`） | 376 tests OK（1 skipped） |
| 3.9.6（隔離的 venv） | 376 tests OK（1 skipped） |
| 3.9.6（`/usr/bin/python3`，PyYAML 裝在使用者 site-packages） | 376 tests OK（1 skipped） |

最後一列就是 Round 1 有 30 個 CLI 測試失敗的環境，這次全部通過，**L12 確認修好**。

## CI

- 新的 `.github/workflows/llmtool-ci.yml`：
  - 在 `ubuntu-latest` 上跑 Python 3.9 與 3.14。
  - 執行 `pip install -e tools/llmtool` 與 `make -C tools/llmtool check`。
  - 觸發條件是 `tools/llmtool/**` 或 workflow 本身有變更。
- run `36809882077`（`gh` 查詢結果）：event `push`、branch `fix/llmtool-a0-a7-review-bugs`、head `c096e85`，`check (3.9)` 與 `check (3.14)` 都 **success**。
- `02af9d2` 與 `f2335cf` 只改了報告，`tools/` 與 `.github/` 和 `c096e85` 完全相同，所以 CI 驗證過的程式碼就是目前 HEAD 的程式碼。

## validator 驗證（直接呼叫 `classify_command`）

**Round 1 的瑕疵：**

| 指令 | Round 1 | Round 2 |
|------|---------|---------|
| `git reset -- file.txt` | DESTRUCTIVE（誤判） | **MUTATING** |
| `git clean -- x` | DESTRUCTIVE（誤判） | **UNKNOWN** |
| `git push origin +main` | MUTATING | **DESTRUCTIVE** |
| `git push origin +refs/heads/main:refs/heads/main` | — | **DESTRUCTIVE** |
| `sed --in-place s/a/b/ .env`、`sed --in-place=.bak …` | SENSITIVE_READ | **MUTATING** |
| `git checkout -- .`、`git checkout -- src/a.go` | MUTATING | **DESTRUCTIVE** |

**regression（先前已修，仍然正確）：**

- 需要批准：`git status`、`sudo ls /root` 都是 UNKNOWN。
- DESTRUCTIVE：`/usr/bin/sudo rm -rf /`、`rm -rf ~ \r- git status`、`git reset --hard`、`git push --force-with-lease`、`git clean -fdx`。
- 一般修改：`git push origin main`、`git checkout main`、`git restore --staged a`、`git reset --soft HEAD~1` 都是 MUTATING。
- 內容讀取：`git diff`、`git show HEAD` 是 SENSITIVE_READ。
- 不誤判：`sed -n s/a/b/p f` 是 UNKNOWN，不會被當成會寫檔。
- `pwd`、`ls -l` 仍然是 SAFE_READ。

## 剩下的問題（low，建議 follow-up）

1. **同樣會丟掉資料的 git 指令，等級不一致。** `git checkout -- .` 已改為 DESTRUCTIVE，但下列指令效果相同或類似，卻仍然只是 MUTATING。這些指令都需要批准，所以不是繞過；但依風險排序的顯示會警告不足。建議用同一條規則處理：

   | 指令 | 目前 | 效果 |
   |------|------|------|
   | `git restore .`、`git restore src/a.go`、`git restore --worktree .` | MUTATING | 丟掉工作目錄的修改 |
   | `git checkout -f main` | MUTATING | 強制切換並丟掉修改 |
   | `git switch --discard-changes main` | MUTATING | 丟掉修改 |
   | `git stash drop`、`git stash clear` | MUTATING | 刪除 stash（`git stash clear` 也在原審查報告的 L1 裡） |

2. **`llmtool-ci.yml` 比 `nlf-ci.yml` 寬鬆：**
   - `actions/checkout@v6`、`actions/setup-python@v6` 沒有固定到 commit SHA；nlf 有固定，並在註解標明版本。
   - checkout 沒有設定 `persist-credentials: false`。
   - `pip install -e` 會從 PyPI 安裝沒有鎖定版本與雜湊的 PyYAML。
   - 沒有檢查工作目錄在 CI 執行後是否乾淨。

3. **小事：** Round 2 的兩個 commit 用了同一個 message（`fix(llmtool): close remaining review gaps`），但 `02af9d2` 只更新了報告，建議用 `docs(...)` 這類 message 區分。

## 建議

1. merge `fix/llmtool-a0-a7-review-bugs`。
2. 把上面第 1、2 項開成 follow-up：discard 類 git 指令的分級，以及 CI 的 SHA pinning、`persist-credentials`、依賴鎖定。
