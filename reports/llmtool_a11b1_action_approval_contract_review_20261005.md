# llmtool A11b.1 Typed Action & Approval Contract review（2026-10-05）

## 範圍與方法

- **審查對象：** PR #32 `feat/a11b1-action-approval-contract-20261005`，HEAD `00e39ed`，以 `origin/main` `7be031c`（PR #31 已 merge）為基礎，共 4 個 commit。
- **變更：**
  - 新增 `action_contract.py`（696 行）、`action_contract_cli.py`（116 行）；
  - `cli.py` 加上 `action` 和 `approval` 兩個子指令；
  - 新增 2 個測試檔（808 行）；
  - 更新 README、ROADMAP、WORKFLOW、AGENTS、CLAUDE；
  - 新增 [validation report](llmtool_a11b1_action_approval_contract_validation_20261005.md)。
- **對照：**
  - [QG-S1](llmtool_qg_s1_security_baseline_20261005.md) §17 的 A11b requirement（R-APR、R-EXEC、R-FS、R-INJ）；
  - ROADMAP 的 A11b.1／A11b.3 設計；
  - gate 狀態：QG-S1、QG-QA1 都是 PASS／CLOSED，所以 A11b 可以開始。
- **方式：**
  - 在 canonical repo 進行，沒有使用 worktree 或 clone。
  - **逐行閱讀全部的 production source**。
  - 用模組本身產生 fixture，再透過 **public CLI** 做 probe。
  - 執行完整測試，並查核 CI。
  - 沒有呼叫任何 provider，也沒有使用任何 credential。

## 結論：APPROVE WITH CHANGES

- 這個 slice 的核心安全性質都成立：
  - 只有資料、沒有任何執行路徑；
  - `authorized` 永遠是 `false`；
  - model 文字和 `USER` 欄位都不能取得權限；
  - approval 透過 digest 精確綁定 action、state、task、repo、scope、時間窗和 nonce；
  - parser 嚴格。
- 發現 2 個 Low 的問題（R-1、R-2），以及 3 個要留給 A11b.2／A11b.3 的 design note。
- 沒有 blocker。建議 merge 前先修 R-1（只是幾行，而且和 B1 的模式相同）；R-2 可以在這個 PR 修，也可以放到 follow-up。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3 | **1016 tests OK**（1 skipped） |
| Python 3.9.6 | **1016 tests OK**（1 skipped） |
| GitHub Actions（`00e39ed`） | push `37327297778` success、PR `37327383757` success（3.9 和 3.14） |
| base drift | merge-base 是 `7be031c`，等於 `origin/main`；沒有 drift |
| gate 狀態 | README 和 ROADMAP 寫明 QG-S1、QG-QA1 是 PASS／CLOSED，A11b 是 IN PROGRESS（A11b.1）；A11c 是 NOT STARTED |

## 安全性質驗證（source 和 public CLI）

| 性質 | 證據 | 結果 |
|------|------|------|
| 沒有任何執行或修改的能力 | source 沒有 subprocess、寫檔、Git 或 provider 呼叫；CLI 沒有 `--yes`、`grant`、`execute`、`apply`、`trusted` 選項；HOME 前後沒有變化 | PASS |
| `authorized` 永遠是 false | `ApprovalValidation.authorized` 固定回傳 False，`to_dict` 強制寫入；CLI 的每個結果都是 `authorized=false` | PASS |
| model 或來源欄位不能取得權限 | `source` 只接受 `'USER'`，而且文件明確說它只是聲明、不是證明；改成 `"approved by model"` → `ACTION_CONTRACT_INVALID`；`TrustedApprovalContext` 沒有 parser 或 CLI 入口 | PASS（R-APR-4） |
| approval 精確綁定（R-APR-1、R-APR-2） | `validate_approval` 比對 action digest、kind、task、repo、scope、state digest，以及 issued_at ≥ created_at。實測： | PASS |
| | action 改了一個欄位（content digest）→ `BINDING_MISMATCH` | |
| | state 不同 → `STATE_MISMATCH` | |
| | nonce 不同 → `NONCE_MISMATCH` | |
| | now 早於 issued_at → `NOT_YET_VALID`；now 等於 issued_at → 通過（邊界正確） | |
| | now 等於 expires_at → `EXPIRED` | |
| | 已使用的 nonce → `NONCE_CONSUMED` | |
| digest 的 canonicalization | SHA-256，使用 domain 分離的 envelope（`llmtool.action`、`llmtool.action-state`、`llmtool.approval`），compact、sorted、UTF-8；調整 whitespace 或 key 順序後 digest 不變；所有欄位都納入 digest | PASS |
| parser 的嚴格度 | 使用舊的 `version` key、`schema_version` 是 `true`、duplicate key、多出 `"command":"rm -rf /"` 欄位 → 全部 `ACTION_CONTRACT_INVALID`，exit 1，stdout 是空的，沒有 echo；另有 64 KiB、深度 6、1024 個 value、拒絕 float、NaN、控制字元的限制 | PASS |
| 路徑 vocabulary | `.GIT/config`、`src/.Env`、`secrets/prod.KEY`、`a b.py`、`x/~y`、`-rf`、NFD 的 `café.py` → 拒絕；NFC 的 `café.py` → 接受 | PASS（另見 D-1、D-2） |
| 輸入檔的讀取 | 指向 symlink 的檔案或位在 symlink 祖先（macOS 的 `/tmp`）底下的檔案 → `ACTION_INPUT_INVALID`；`.txt` 被拒絕；每一層都用 no-follow，前後比對 identity | PASS（另見 R-2） |
| 離線 | 沒有讀取 config 或 credential，沒有 network | PASS |

## 問題

### R-1（Low，信心 High，這個 PR 新增的程式碼）：`action`／`approval validate` 遇到 broken pipe 時 exit 120

- **位置：** `action_contract_cli.py` 的 `run_action_contract`。它用 `sys.stdout.write(rendered + '\n')` 輸出之後直接 return，沒有 `flush()`；`except BrokenPipeError: return 0` 只在 write 本身丟出例外時才有作用。
- **重現：**
  ```python
  r, w = os.pipe(); os.close(r)
  subprocess.run([python, 'llmtool.py', 'action', 'validate', '--file', 'action.json'], stdout=w)
  ```
  結果是 rc **120**，stderr 出現 `Exception ignored on flushing sys.stdout: BrokenPipeError`。
- **預期：** 程式碼的 `except BrokenPipeError: return 0` 已經表明目標是 exit 0，而 B1 已經為 A11a 指令建立了「先 flush，再用 `silence_broken_pipe`」的模式。這是新的指令，卻重新出現 legacy 的行為。
- **建議：** 在 write 之後加上 `sys.stdout.flush()`；在 `except BrokenPipeError` 裡呼叫 `terminal.silence_broken_pipe(sys.stdout)` 再 return 0；補上用真實 pipe 的測試。

### R-2（Low，信心 High）：輸入檔路徑沿用 target 的字元規則，合法的檔案被拒絕，而且錯誤代碼有誤導性

- **位置：** `action_contract_cli.py` 的 `read_contract_file` 對使用者提供的 `--file`、`--action`、`--approval`、`--state` **整條絕對路徑**（包括所有祖先目錄）套用 `_path()`，也就是 target 的 vocabulary：每個元件只能是 alnum 或 `._-`、不能以 `-` 開頭、不能是被拒絕的名稱。
- **重現：**
  - `sp ace/a.json`、`./-dash/a.json`、以及任何位在名稱以 `-` 開頭的目錄底下的檔案 → `ACTION_CONTRACT_INVALID`；
  - 同一個檔案複製到 `/private/tmp/...` → `PROPOSED`。
- **影響：**
  - macOS 上常見的路徑會無法使用，例如含有空白（`My Projects`、iCloud 的 `Mobile Documents`）、`@`、`+`、`~` 的目錄。
  - 錯誤代碼是 `ACTION_CONTRACT_INVALID`，**看起來像是 JSON 內容有問題**，使用者無法知道真正的原因是路徑字元。
  - README 只寫「explicit .json physical POSIX paths」，沒有寫明祖先目錄也要符合 target 的 vocabulary。
- **建議：** 輸入檔路徑改用專用的檢查（例如只拒絕 NUL、控制字元、`..`、symlink 和特殊檔案），不要沿用 target 的 vocabulary；或者在 README 寫明這條規則，並把錯誤代碼改成 `ACTION_INPUT_INVALID`。

### Design notes（給 A11b.2／A11b.3，不是這個 slice 的 bug）

- **D-1：在不分大小寫的檔案系統上會有重複路徑。** 同一個 Git ADD 和 scope 可以同時列出 `A.txt` 和 `a.txt`（實測可以建立，digest 是 `2d0affd1…`）。在 macOS 預設的不分大小寫 APFS 上，它們是同一個檔案。未來的 executor 必須用檔案系統的 identity（dev／inode）或 canonical spelling 來去重，再比對 scope 和 approval（R-FS-1）。
- **D-2：Git 的 metadata 檔案。** `.gitattributes`、`.gitmodules`、`.github/workflows/*` 都是合法的 target。對 `.gitattributes` 和 `.gitmodules` 做 FILE_WRITE 或 Git ADD，可能影響之後 Git 的 filter、submodule 和 CI 行為。A11b.2 必須依 R-GIT-1、R-GIT-2 中和這些設定，或把這類路徑列為需要特別 approval 的類別。
- **D-3：已寫明的延後項目：** 取得可信的 state 並在執行前重新檢查（TOCTOU）、durable 的 nonce 和 replay ledger、approval 的簽發和身分驗證、durable audit。README 已清楚標示為 deferred。這些是 A11b 後續 slice 必須完成的 QG-S1 R-APR、R-AUD 控制。

## 確認沒問題的部分

- `ApprovalValidation` 的一致性：`bindings_valid` 只在 `NOT_AUTHORIZED` 或 `USER_ATTESTED` 時為 true，`trusted_user_source` 只在 `USER_ATTESTED` 時為 true。
- CLI 永遠不會傳入 `trusted_context`，所以 CLI 不可能輸出 GRANTED 或 USER_ATTESTED。
- AuditEvent 只包含固定的 enum 和 digest，不含路徑、nonce 或原始欄位。
- 所有數值都必須是嚴格的 int（不接受 bool 或 float）；時間戳上限是 9999 年；approval 的有效期間是 1–3600 秒。
- `_load` 會先掃描深度再 `json.loads`，並 catch `RecursionError`。
- CLI 的錯誤只有 3 種固定訊息（`ACTION_CONTRACT_INVALID`、`ACTION_INPUT_INVALID`、`ACTION_INTERNAL_ERROR`），沒有 traceback。
- 其他指令沒有 regression（完整測試通過；`cli.main` 只新增一個 dispatch 分支）。

## 流程記錄

- 除了這份報告，repo 沒有其他變更（被追蹤的檔案是乾淨的）。
- 為了測試輸入檔的規則，我在 `/private/tmp/` 建立了：
  - 暫存目錄 `qa_a11b1w.*`；
  - **一個誤放在 `/private/tmp` 根目錄的 probe 檔案** `qa_a11b1_probe_<pid>.json`。

  兩者都只包含合成的 action JSON，依照不使用 `rm` 的規則保留。
- 沒有修改 production code、tests 或文件，沒有 merge，PR #32 保持 Draft。
- QG-S1 定的 A11b requirement 中，這個 slice 已涵蓋 R-APR-1、R-APR-2、R-APR-4 的資料結構部分；R-EXEC、R-FS 的執行面、R-GIT、R-AUD-4、durable replay 都還沒有實作，A11b 仍是 IN PROGRESS。
