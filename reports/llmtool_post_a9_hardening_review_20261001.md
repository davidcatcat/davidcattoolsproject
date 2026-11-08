# llmtool post-A9 hardening 分支獨立 review（2026-10-01）

## 範圍與方法

- 審查對象：分支 `fix/post-a9-hardening`，HEAD `b196bb34c9b2ef6ee3efdfcbb104f3578b73a813`。
  - 以 `origin/main` `0ec1951` 為基礎：ahead 2、behind 0。
  - 2 個 commit：`4f7161d`（實作）、`b196bb3`（validation 文件）。
  - 共 22 個檔案、+834／−70，新增 `llmtool/log_safety.py` 與 `tests/test_post_a9_hardening.py`。
- 對照：[A0–A9 驗證報告](llmtool_a0_a9_verification_20261001.md)的 M1–M7，以及分支自己的 `reports/llmtool_post_a9_hardening_validation_20261001.md`。
- 方式：
  - **唯讀**，沒有修改、commit 或 merge 該分支。
  - Lead 自己跑測試、重現 M1–M7、用 gateway 實測；另外由一個獨立的 reviewer 唯讀審查整份 diff，並與 base 比對。
- 安全邊界：
  - CLI 一律以 `env -i HOME=<暫存目錄>` 執行，沒有讀取真實的憑證或設定。
  - 唯一的網路服務是公司 gateway（`http://home-llm.example:8000`，route `gw`，`enable_thinking: false`）。Lead 呼叫 7 次、reviewer 呼叫 5 次。
  - 沒有呼叫真的 Claude CLI、Codex CLI 或 OpenAI API。

## 結論：REQUEST CHANGES（先不要 merge）

- **已修好：** M1（kubectl `--server` 等）、M3（configure）、M4（orchestration 的 profile 隔離）。
- **大致修好，但各自帶出小的 regression：** M2（stdin）、M5（Unicode 空白）。
- **M6** 分支明確標為 DEFERRED，標示正確。
- **M7 的新檢查（`log_safety.py`）有嚴重的副作用：** 用公司 gateway 實測時，**正常的 analyze-log 回答大多會被整份丟掉**（rc=1，stdout 為空）；同時，仍然有一些顯示憑證的指令可以通過。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 536 tests OK（1 skipped） |
| Python 3.9.6（隔離的 venv） | 536 tests OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`，PyYAML 在使用者 site-packages） | 536 tests OK（1 skipped） |
| GitHub Actions `llmtool CI` | `36884162120`（`4f7161d`）、`36884845909` 與 `36885141598`（`b196bb3`）都 success |
| 新增的 21 個測試 | 逐一針對各項修正，在 base 上會失敗（依程式碼判斷；base 沒有 `log_safety`，所以無法直接 import） |

## M1–M7 驗證

| # | 結果 | 證據 |
|---|------|------|
| M1 kubectl 連線覆寫 | **FIXED** | `kubectl -s URL get pods`、`--server=URL`、`--token=t`、`--kubeconfig k`、`--as admin`、`--server X -n a get pods` 都是 UNKNOWN（base 是 SAFE_READ）。仍保留較高的風險：`--server X get secret` 是 SENSITIVE_READ，`--server X delete ns` 是 DESTRUCTIVE，`--kubeconfig=x apply` 是 MUTATING。`kubectl get pods`、`-n prod get pods` 依照「kubeconfig 可信」的前提，仍然是 SAFE_READ。 |
| M2 pipe 被默默丟掉 | **PARTIAL** | `echo hi \| llmtool ask q` 會 rc=1 並報衝突。空的 pipe、EOF、`/dev/null` 都不會卡住，也沒有 busy-wait。但只在啟動時檢查一次，所以寫入較慢的 pipe 仍然會被默默丟掉，例如 `(sleep 2; echo late) \| llmtool ask q`（文件有寫，但沒有修）。另外帶出 regression R2、R3。 |
| M3 configure 重設設定 | **FIXED** | 新增 profile 後，temperature 0.7、max_tokens 4000、`active_provider` 都保留。留空的回答會保留 base_url、model、enable_thinking、api_key_env；secret 留空時保留原本的字面 key；改用環境變數時會清掉字面 key；改變 transport type 時會重設舊的設定。小事：沒有 `generation` 區塊的既有檔案，現在會被寫入 0.0／2000。 |
| M4 orchestration 被環境變數覆寫 | **FIXED** | 新的 `resolve_profile_config` 只讀存起來的使用者設定，`LLMTOOL_MODEL`、`LLMTOOL_BASE_URL`、`LLMTOOL_PROVIDER`、`LLMTOOL_MAX_TOKENS` 與專案的 `.llmtool.yaml` 都不影響每一步的 profile。先檢查 readiness 再讀 key，未知的 profile 會立刻失敗；一般 `ask` 的優先順序不變。 |
| M5 Unicode 空白 | **FIXED**（但 A4 有 regression） | feedback、context、benchmark 現在接受 U+3000、NBSP、U+2028；bidi 與 ESC 仍然拒絕。超過上限的候選回答改為 fail 的結果，不是錯誤。A4 見 R5。 |
| M6 A8 preferred evidence | **NOT FIXED（已標為 DEFERRED）** | benchmark 的程式碼沒有變更。 |
| M7 analyze-log 顯示 Secret | **PARTIAL，有嚴重副作用** | 擋住了明顯的情況：`kubectl get secret(s)`（含 flag、`secret/NAME`、逗號、pipe），以及用 `cat`／`grep` 顯示憑證檔。見 R1、R4。 |

## 公司 gateway 實測：analyze-log

**Lead：** 4 種常見的 log，每種跑一次 `llmtool analyze-log --file <log>`：

| log | rc | 結果 |
|-----|----|------|
| 資料庫認證失敗 | 0 | 通過，但 NEXT_COMMANDS 是 `grep -r "password" CONFIG_FILE_PATH`、`cat CONFIG_FILE_PATH`：**正好是在顯示憑證**，只是因為 placeholder 看起來不像敏感路徑才通過 |
| OOM killer | **1** | `analyze-log NEXT_COMMANDS failed safety/format validation; output omitted` |
| Kubernetes（BackOff、缺少 Secret） | **1** | 同上 |
| nginx upstream refused | **1** | 同上 |

**被拒絕的原因**：用分支的 prompt 直接呼叫 provider、跳過檢查，取得模型原始的 NEXT_COMMANDS：

| 原始建議 | 判斷 | 是否合理 |
|----------|------|---------|
| `systemctl status app.service` | SENSITIVE_READ，拒絕 | 不合理：最標準的排查指令 |
| `kubectl describe pod api-7d9f-xk2 -n prod` | SENSITIVE_READ，拒絕 | 不合理：常見的排查指令 |
| `journalctl -u <SERVICE_NAME> --since …` | MUTATING，拒絕 | 不合理：`<…>` 被解析成重導向 |
| `` `cat /proc/4412/status` (if process is still alive …) `` | 格式不符，拒絕 | 不合理：只是後面多了說明 |
| `kubectl get secret api-db-credentials -n prod` | SENSITIVE_READ，拒絕 | 可以接受：雖然沒有 `-o`、只會顯示 metadata，但擋下來偏保守 |

**reviewer**：2 種 log（postgres 認證、k8s 缺少 Secret），共跑 3 次，**3 次都是 rc=1 而且 stdout 為空**，原因同上。

只要有一條建議不符合規定，**整份分析（包括正確的 root cause）都會消失**，也沒有 fallback 或 opt-out。

## 問題清單

| # | 嚴重度 | 位置 | 問題與重現 | 建議修正 |
|---|--------|------|-----------|---------|
| R1 | **High** | `cli.py:182-184`、`log_safety.py:27,34,53,61` | **正常的 analyze-log 回答被整份丟掉**（見上方實測）。其他誤擋（都是驗證過的）：<br>- 直譯器的名稱出現在任何位置都會被擋：`ls -la /usr/bin/python3`、`kubectl logs POD -c python`、`grep -n sh app.log`<br>- `NEXT_COMMANDS: (none)` 寫在同一行、`4. pwd`、結尾多一句說明<br>- 回答的**任何地方**出現 ZWJ emoji 或 ANSI ESC（但輸入的 log 是允許 ANSI 的）<br>測試只用很短的合成回答，所以完全沒有測出來。 | 不合格時**保留分析內容**，只移除或標註有問題的建議（或改成一則提示），維持 rc=0。先去掉結尾的 `(…)` 說明再判斷；只把行首的指令名稱（或 wrapper 之後的那個）當成直譯器；控制字元檢查只套用在 NEXT_COMMANDS 段落；決定 `systemctl status`、`kubectl describe` 在這裡的處理方式；加上用 gateway 實際輸出產生的多段落 regression fixture。 |
| R2 | Medium | `inputs.py:19,55` | **stdin 被關閉時，明確給的輸入也會失敗**：`llmtool ask q <&-` 回報 "cannot read input as UTF-8 text"，rc=1（base 可以正常執行）。原因是 `sys.stdin` 為 None 時，`None.isatty()` 會丟出 AttributeError。 | `_stdin_has_data` 開頭加上 `if stream is None: return False`，並補測試。 |
| R3 | Medium | `inputs.py` | **`while read q; do llmtool ask "$q"; done < qs` 第一圈就報衝突**（base 可以執行）。`… \| while read f; do llmtool review-diff --file "$f"; done` 也一樣。`/dev/zero`、`/dev/urandom` 與非 POSIX 的 pipe（Windows）會直接失敗。 | stdin 不是 pipe 時改為警告或忽略；在文件中說明可以用 `</dev/null` 避開。 |
| R4 | Medium | `log_safety.py:66-74` | **顯示憑證的指令仍然可以通過**（都驗證過）：<br>- `kubectl get --raw /api/v1/namespaces/NS/secrets/NAME`（以及 `--raw=`、`-o yaml --raw …`）<br>- `oc get secrets -o yaml`<br>- `echo secrets \| xargs kubectl get -o yaml`<br>- `kubectl get pod POD -o jsonpath='{…env}'`<br>- `kubectl exec POD -- env`<br>- `od -c ~/.ssh/id_rsa`<br>- `git config --list`<br>- `helm get values R`、`vault kv get secret/x`、`aws secretsmanager get-secret-value`<br>- `kubectl --server https://evil get pods`（M1 的那個指令）<br>- 以 placeholder 顯示設定檔：`cat CONFIG_FILE_PATH` | 檢查每一段路徑是否含 `secrets`（可以擋住 `--raw`）；`oc` 比照 `kubectl`；display tool 加上 `od`；帶連線覆寫的 UNKNOWN kubectl 指令一律拒絕；secret manager 指令建立小型的 denylist，或在文件中說明限制。 |
| R5 | Low | `terminal.py:15` | **A4 改成拒絕一般的 Unicode format 字元**：ZWJ emoji、soft hyphen U+00AD、BOM U+FEFF、ZWSP U+200B（base 接受，分支拒絕），所以含 emoji 或從網頁複製的文字的 packet 會讓 context-pack 失敗。 | 除了 bidi 之外，允許 category Cf，或至少允許 ZWJ／ZWNJ。 |
| R6 | Low | `log_safety.py` | **分層與重複**：import validator 的私有 `_sensitive_path`，又另外寫了一套 kubectl Secret 偵測與 display tool 清單，而且已經和 validator 不一致（例如 `get secret NAME --template={{.data}}`：guard 拒絕，validator 判為 UNKNOWN）。 | 把 Secret 與 display 規則公開放在 `validator.py`，兩邊共用。 |
| R7 | Low | `cli.py:172` | stdin-only 模式現在會先讀完 stdin，才檢查設定。永遠不結束的 pipe 會在出現「沒有設定 profile」的錯誤之前就卡住（以前錯誤會先出現）。 | 提早做衝突檢查，但把完整讀取 stdin 留在 `resolve_config` 之後。 |
| R8 | Nit | 文件 | README 說 probe 失敗時會給「整理過的錯誤」，但 stdin 被關閉時給的是誤導性的 UTF-8 錯誤；README 沒有說明 analyze-log 被拒絕時會隱藏全部輸出，也沒有說明 `while read`／`</dev/null` 的情況；分支的 validation report 把「Optional company gateway smoke」標為 NOT-TESTED，而 R1 正是實測時才會出現的問題。 | 更新 README 與 validation report。 |

## 缺少的測試

- `sys.stdin` 為 None。
- `while read` 迴圈（普通檔案作為 stdin）。
- A4 的 ZWJ／BOM。
- **用實際 gateway 輸出產生的多段落 analyze-log 回答**（包含 `systemctl status`、`kubectl describe`、`<PLACEHOLDER>`、結尾的說明文字）。
- `--raw`、`oc` 等繞過方式。

## 驗證程度

- **重現過**：M1–M5 的狀態、R1–R5、R1 的 gateway 實測（Lead 跑 4 種 log，再加 3 次取得原始輸出；reviewer 3 次，再加 2 次取得原始輸出）。
- **只看程式碼、沒有重現**：R7、R3 在 Windows／非 POSIX pipe 的情況；R6 是設計上的判斷。
- **不確定**：其他模型（Claude／Codex／OpenAI）遇到 R1 的頻率；這次只測了 gateway `gw`。

## 建議

1. 先修 **R1**：不合格時保留分析，只處理有問題的建議，加上用實際 gateway 輸出產生的 regression fixture。
2. 再修 **R2、R3**：stdin 為 None、普通檔案作為 stdin。
3. **R4、R5** 建議一併處理，或記為 follow-up；R6–R8 列為 follow-up。
4. 修正後用公司 gateway 再跑一次多段落的 analyze-log 實測，再 merge。

## 清理

- 用過的 worktree 都是 detached 而且是乾淨的，都留在 scratchpad：`pa9.WmLA/wt`、`rev.DgBt/wt`、`rev.DgBt/base`。
- 沒有還在跑的背景 process，也沒有刪除任何檔案。
