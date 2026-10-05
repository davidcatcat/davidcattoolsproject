# llmtool A11b.1 Action & Approval Contract re-review（2026-10-06）

## 範圍與方法

- **審查對象：** PR #32 `feat/a11b1-action-approval-contract-20261005`，HEAD `adb49a0`，以 `origin/main` `7be031c` 為基礎（ahead 7、behind 0，沒有 drift）。
- **本輪新增的 commit：**
  - `0d3669f`：fix(llmtool): harden action contract CLI input handling
  - `adb49a0`：R-1／R-2 validation report
- **變更：** `action_contract_cli.py`（+31／−11）、README 的輸入檔規則說明、`tests/test_action_contract_cli.py`（+114）。
- **對照：** 上一輪的 [A11b.1 review](llmtool_a11b1_action_approval_contract_review_20261005.md)：APPROVE WITH CHANGES，R-1 和 R-2。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree 或 clone。
  - 逐行閱讀 `3438304..adb49a0` 的 diff。
  - 用上一輪的 fixture，透過 **public CLI** 重跑。
  - **刻意把 fixture 放在含有 `-` 開頭元件的 scratchpad 目錄**，也就是上一輪無法讀取的位置。
  - 沒有呼叫 provider，也沒有使用 credential。

## 結論：APPROVE（R-1、R-2 確認修好）

## 修正方式（從 source 確認）

- **R-1：** `sys.stdout.write` 之後加上 `sys.stdout.flush()`，讓 buffered 的 EPIPE 在 return 之前就被偵測到；`except BrokenPipeError` 先呼叫 `silence_broken_pipe(sys.stdout)` 再 return 0。和 B1 為 A11a 指令建立的模式相同。
- **R-2：**
  - 新增 `_input_spelling()`，**和 Action target 的 vocabulary 分開**：只拒絕空字串、超過 1024 bytes 或 32 個元件、控制字元和 Cf、空的元件、`..`。原始的拼寫和 `abspath` 之後的路徑都要檢查。
  - 其他的保護都保留：`.json` 副檔名、不能在 `/dev`、`/proc`、`/sys`、每一層都 no-follow、只接受單一 link 的一般檔案、前後比對 identity。
  - 路徑、檔案系統、UTF-8 解碼的錯誤改成 raise `ValueError` → `ACTION_INPUT_INVALID`；JSON 和 schema 的錯誤（`LLMToolError`）→ `ACTION_CONTRACT_INVALID`。
- **Action target 的 `_path()` 規則沒有改變**（credential、Git 儲存、shell 字元依然被拒絕）。

## 重跑結果（public CLI）

| Case | 結果 | 判定 |
|------|------|------|
| scratchpad（祖先目錄有 `-Users-…` 元件），用相對和絕對路徑 | rc 0，`PROPOSED`（上一輪是 `ACTION_CONTRACT_INVALID`） | PASS |
| `My Projects/a.json`、`./-dash/a.json`、`a+b@c~d/a.json`、`中文目錄/a.json` | rc 0，`PROPOSED` | PASS |
| `.config/llmtool/a.json`、`.ssh/a.json`（輸入檔可以放在 target 被禁止的目錄） | rc 0，`PROPOSED`（README 有寫明；只做驗證，不會輸出原始內容） | PASS |
| `../<dir>/action.json`、`ok//a.json`、symlink、`.txt`、找不到檔案 | rc 1，`ACTION_INPUT_INVALID` | PASS |
| malformed JSON | rc 1，`ACTION_CONTRACT_INVALID` | PASS（代碼正確區分） |
| 不是 UTF-8 的 JSON 檔 | rc 1，`ACTION_INPUT_INVALID` | PASS |
| approval：相符 → `NOT_AUTHORIZED bindings=PASS`；action 被改過 → `BINDING_MISMATCH`；state 不同 → `STATE_MISMATCH` | rc 0／1／1 | PASS（binding 沒有退化） |
| **R-1 broken pipe**：`action validate`、`action validate --json`、`approval validate`，stdout 是讀取端已關閉的 pipe | **全部 rc 0，stderr 是空的**（上一輪是 rc 120 加上 `Exception ignored`） | PASS |
| `--file -dash/a.json`（沒有 `./`） | rc 2，usage | EXPECTED：argparse 把 `-` 開頭的值當成 option；用 `./-dash/...` 或 `--file=-dash/...` 即可 |
| 是否寫入 HOME | 沒有（HOME 是空的） | PASS |

## Regression 與 CI

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | **1020 tests OK**（1 skipped），比上一輪多 4 個 |
| Python 3.9.6 | **1020 tests OK**（1 skipped） |
| GitHub Actions（`adb49a0`） | push `37341227826` success、PR `37341233638` success（3.9 和 3.14） |
| working tree | 被追蹤的檔案是乾淨的 |

## 剩下的項目（不是 blocker，沿用上一輪）

- **D-1：** 不分大小寫的檔案系統上會有重複路徑（`A.txt` 和 `a.txt`），未來的 executor 必須用檔案系統的 identity 去重。
- **D-2：** `.gitattributes`、`.gitmodules`、`.github/` 這類 Git metadata 檔案，需要 A11b.2 的 Git hardening 處理。
- **D-3：** 可信的 state 和 TOCTOU、durable replay ledger、approval 的簽發和身分驗證、durable audit，都已寫明為延後項目。

這些都是 QG-S1 的 R-FS、R-GIT、R-APR、R-AUD 要求，屬於 A11b 後續 slice 的範圍。

## 判定

```text
R-1 (broken pipe): CLOSED
R-2 (input path policy / error code): CLOSED
NEW CONFIRMED BUGS: 0
PR #32 RECOMMENDATION: APPROVE
```

這一輪沒有修改 production code、tests 或文件，沒有 merge，PR #32 保持 Draft。A11b.2 依原計畫在 PR #32 merge 之後才開始。
