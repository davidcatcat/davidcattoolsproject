# llmtool A0–A7 bug-fix 分支獨立檢查（2026-10-01）

## 範圍

- 檢查對象：分支 `fix/llmtool-a0-a7-review-bugs`，HEAD `237d62f1d7a548c69fb03a3d5ae80de587b6d417`。
  - 以 main `9c10fe4` 為基礎：ahead 1、behind 0。
  - 共 25 個檔案，只修改 `tools/llmtool` 與一份報告。
- 對照：[llmtool code review](llmtool_code_review_20261001.md)（M1–M7、L1–L12）與分支自己的 [bug-fix validation report](llmtool_a0_a7_bugfix_validation_20261001.md)。
- 方式：
  - 唯讀。分支以 detached worktree checkout，**沒有修改、commit 或 merge 該分支**。
  - 重現在 scratchpad 的暫存目錄進行，`HOME` 指向暫存目錄，只對 `127.0.0.1` 上的 stub server 發送請求。

## 結論

**修正與分支報告的說法一致，可以進入 review。** M1–M7 全部確認修好，大部分 low 也修好了。另外有 4 個 validator 的小瑕疵，都仍然需要批准，所以不是安全漏洞。建議在 merge 前一併修正，或記成 follow-up。

## 測試（獨立執行）

| Python | 結果 |
|--------|------|
| 3.13.3（`tools/llmtool/.venv`） | 371 tests OK（1 skipped） |
| 3.9.6（隔離的 venv，最低支援版本） | 371 tests OK（1 skipped） |

分支報告寫的是 "Python 3.9: runtime unavailable; grammar validation passed"。這次在真正的 3.9.6 上跑完整測試並且通過，補上了這個缺口。測試時 stdin 指向 `/dev/null`。

## M1–M7 驗證

| # | 方式 | 結果 |
|---|------|------|
| M1 stdin | 實際重現：`sleep 30 \| llmtool ask "q"`，並以 10 秒 alarm 監看 | **修好**：立刻 exit 0 並輸出回答，alarm 沒有觸發。`echo \| llmtool ask hi` 也 exit 0。程式碼在有 `text` 或 `--file` 時完全不讀 stdin。 |
| M2 終端機控制字元 | 實際重現：stub 回傳 `ok \x1b]0;PWNED\x07 \x1b[31mred\x1b[0m` | **修好**：stdout 是 TTY 時跳脫成 `\x1b]0;PWNED\x07…`；是 pipe 時保留原始位元組，給機器讀的輸出不變。 |
| M3 provider 名稱 | 程式碼與測試 | **修好**：所有 profile 名稱都會驗證，有測試 `test_invalid_inactive_provider_name_is_rejected_without_losing_valid_profiles`、`test_invalid_provider_name_fails_before_secret_prompt_or_config_write`。 |
| M4 git 讀取 | 直接呼叫 `classify_command` | **修好**：`git status`、`git log --oneline` 都是 UNKNOWN，需要批准。 |
| M5 sudo | 直接呼叫 `classify_command` | **修好**：`sudo ls /root`、`sudo git status` 是 UNKNOWN，`/usr/bin/sudo rm -rf /` 是 DESTRUCTIVE。 |
| M6 claude-cli process group | 程式碼與測試 | **修好**：`start_new_session=True` 加上 `os.killpg`。另外改成只傳 allowlist 的環境變數、在獨立的 cwd 執行、拒絕相對路徑的執行檔（也一併修了 L3、L4），有測試 `test_descendant_holding_pipes_is_killed_after_leader_exits` 等。 |
| M7 OpenAI `store` | 程式碼與測試 | **修好**：Responses 的 body 有 `"store": False`，測試也有檢查。 |

## Low 與 nit 抽查

**已確認修好：**

- 縮寫的長選項：`git reset --har`、`git clean --forc` 都是 DESTRUCTIVE。
- 全域選項：`git -C . reset --hard` 是 DESTRUCTIVE。
- 刪除遠端分支或強制 push：`git push --force`、`git push origin :main` 都是 DESTRUCTIVE。
- `sed -i …` 是 MUTATING。
- `\r` 偽裝：`rm -rf ~ \r- git status` 是 DESTRUCTIVE。控制字元與 bidi 字元改成「視為分隔符號再分析」，而且最低是 UNKNOWN。
- 其他項目：codex 的 `killpg` EPERM、API key 的空白、stdin 被關閉、chat 的 exit code、broken pipe、`timeout_seconds` 的上限、`"error": null`、claude 的 prompt encoding，都有對應的 regression 測試（共新增 28 個測試）。

**延後處理（分支報告寫明了理由）：**

- L11：skills 目錄裡的雜檔。
- L12：測試的 `PYTHONUSERBASE`。
- 數個 nit。

這些理由合理。不過 L12 不修的話，在用 `pip install --user` 安裝 PyYAML 的機器上，30 個 CLI 測試仍然會失敗。

## 剩下的瑕疵（validator，建議修）

| 指令 | 目前 | 應為 | 原因與修法 |
|------|------|------|-----------|
| `git reset -- file.txt` | DESTRUCTIVE | MUTATING | `'--hard'.startswith('--')` 成立，單獨的 `--` 被當成 `--hard` 的前綴（誤判為過度危險）。前綴比對時排除 `--`，或要求至少 `--h`。 |
| `git clean -- x` | DESTRUCTIVE | UNKNOWN | 同上，`--` 被當成 `--force` 的前綴。 |
| `git push origin +main` | MUTATING | DESTRUCTIVE | 以 `+` 開頭的 refspec 也是強制 push，沒有被辨識。push 的參數以 `+` 開頭時判為 DESTRUCTIVE。 |
| `sed --in-place s/a/b/ .env` | SENSITIVE_READ | MUTATING | 只認得短選項 `-i`，長選項 `--in-place` 沒有處理。 |

另外，`git checkout -- .` 會丟掉工作目錄的修改，仍然只是 MUTATING。原審查報告的 L1 有列出這一項，但分支報告把 L1 標為 FIXED，這一項其實沒有修。

## 其他觀察

- llmtool **沒有 GitHub Actions CI**，所有「通過」都來自本機執行（分支作者與這次的獨立檢查）。nlf 有 ubuntu／macos／windows 三個平台的 CI 可以佐證，llmtool 沒有。建議之後補一個最小的 CI（3.9 與最新版 Python，`make check`）。
- 檢查時，main repo 的 checkout 在 `fix/llmtool-a0-a7-review-bugs`，工作目錄裡只有既有的 benchmark／reasoning 未追蹤檔案，這次沒有動到。

## 建議

1. 先修上面 4 個 validator 瑕疵與 `git checkout -- .`，每項都只需要幾行，各補一個 regression 測試。
2. 或者先 merge，把它們記成 follow-up。
3. 之後補上 llmtool 的 CI。
