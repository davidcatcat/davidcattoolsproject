# llmtool code review：bug 與潛在問題（2026-10-01）

## 範圍與方法

- 審查對象：`tools/llmtool`，`origin/main` `4acc783fda75bb1c4eff7af0f4b9a58a824260e7`（已含 A1–A6）。
- 方式：**唯讀審查**，沒有修改任何程式。分成三個面向，由三個獨立的 reviewer 同時進行：
  1. provider（HTTP 與 Claude／Codex CLI）
  2. config、CLI、inputs、chat
  3. context、git_intel、validator、skills

  最重要的幾項結論由 Lead 逐行對照原始碼再確認。
- 測試環境：
  - 所有重現都在 scratchpad 的暫存目錄裡進行，`HOME` 指向暫存目錄。
  - 只對 `127.0.0.1` 上的 stub server 發送請求，沒有連線到任何真實的外部服務。
  - 沒有讀取任何真實的憑證或設定。
- 測試結果：
  - Python 3.13.3：345 個測試通過（1 skipped）。
  - Python 3.9.6（隔離的 venv，最低支援版本）：345 個測試通過（1 skipped）。
- 「已驗證」代表有實際重現；「不確定」代表沒有證實。

## 總結

**沒有 critical 或 high 等級的問題。** 有 7 個 medium 等級的問題值得修，其餘是 low 與 nit。建議優先處理 M1、M4、M5、M6、M7：

- M1 是真的會讓 CLI 卡住的 bug。
- M4、M5 違反 validator「不需要批准就是安全的」這個前提。
- M6、M7 都只需要改一兩行，而且 `codex_cli.py` 已經有可以照抄的做法。

## Medium（建議修）

| # | 位置 | 問題 | 影響與重現 | 建議修正 |
|---|------|------|-----------|---------|
| M1 | `llmtool/inputs.py:28` | 即使已經給了文字或 `--file`，`read_input` 還是一律讀 stdin | stdin 是永遠不會關閉的 pipe 時（ssh、IDE、CI harness），`llmtool ask "q"` 會永遠卡住。`while read l; do llmtool ask "$l"; done < file` 的第一次呼叫會吃掉整個檔案。`echo \| llmtool ask hi` 會報 "provide only one of…"。`tests/test_openai_provider.py:281` 沒有替換 `sys.stdin`，`(sleep 100 \| python -m unittest …)` 會卡在這個測試。 | 只在 `text is None and filename is None` 時讀 stdin。只有空白的 pipe 視為空。測試裡替換掉 `sys.stdin`。 |
| M2 | `llmtool/cli.py:172`、`llmtool/chat.py:131`、`/history`（`chat.py:108`） | 模型的回覆原封不動寫到終端機 | 被 prompt injection 的 log 或 diff，可以讓模型輸出 OSC／CSI 序列：OSC 0 改視窗標題、OSC 8 藏連結、OSC 52 寫剪貼簿，或讓建議的指令顯示成別的樣子。重現：stub 回傳 `"ok \x1b]0;PWNED\x07 \x1b[31mred\x1b[0m"`，`ask` 與 `chat` 都照原樣輸出。 | stdout 是 TTY 時，把 `\n`、`\t` 以外的 C0／C1 控制字元（特別是 ESC、BEL、`\x9b`）去掉或跳脫。 |
| M3 | `llmtool/configure.py:74,132`；`llmtool/config.py:218-221` | `configure` 只驗證 active provider，其他 profile 的名稱不驗證 | 用 `bad name` 當 profile 名稱時會顯示 "saved"。之後 `ask`、`chat`、`config show`，甚至再跑一次 `configure` 都會失敗，只能手動編輯設定檔，或用 `config init --force` 清掉全部設定。 | 提示輸入之後立刻呼叫 `_provider_name()`；`_validate` 檢查每一個 key。 |
| M4 | `llmtool/validator.py:151-231` | `git status`、`git log --oneline`、`git diff --stat`、`git show --stat` 被判為 SAFE_READ（`requires_approval() == False`） | 在不受信任的 repo 裡，git 會執行 repo 設定的程式。實際重現：`git config core.fsmonitor "touch PWNED_fsmonitor; false"` 之後，`git status` 會建立 `PWNED_fsmonitor`；`core.pager` 在輸出到終端機時，會被 `git log` 執行。測試已經把明確寫出 `git -c core.pager=evil status` 的情況判為 UNKNOWN，但同樣的設定寫在 repo config 裡時沒有被考慮到。 | git 指令至少判為 UNKNOWN。或者在 README 和 SAFE_READ 的理由裡寫明「假設 repo config 可信」，並要求未來的 executor 套用 `git_intel.SAFE_CONFIG` 的 `-c` 覆寫。 |
| M5 | `llmtool/validator.py:148-149` | `sudo <cmd>` 的風險等級和 `<cmd>` 一樣 | `sudo ls /root`、`sudo git status`、`sudo kubectl get pods` 都是 SAFE_READ，不需要批准。和 M4 一起，就是以 root 身分執行 repo 的 fsmonitor。 | 指令以 `sudo` 開頭時，風險取內部指令的風險與 UNKNOWN 中較高的一個。 |
| M6 | `llmtool/providers/claude_cli.py:72-77,102-106` | 逾時或超量時只砍主 process，沒有砍整個 process group | CLI 已經輸出回答並 exit 0，但它啟動的子 process（MCP server、helper）還握著 stdout。select loop 等不到 EOF，正常的回答被當成 "Claude CLI timed out" 丟掉，子 process 也留下來。重現：假的 `claude` 在印出結果後執行 `(sleep 6)&`，結果是 3 秒後逾時，而且在 provider 回傳之後子 process 仍然存活。codex 有對應的測試（`test_descendant_pipe_handles_are_bounded_by_timeout`），claude 沒有。 | 照 codex 的做法：`start_new_session=True`，在 `_kill` 裡用 `os.killpg(process.pid, SIGKILL)`，並補上同樣的測試。 |
| M7 | `llmtool/providers/openai.py:156-163` | Responses API 的請求沒有帶 `"store": false` | Responses API 預設會保存回應，所以每一個 prompt（diff、log、事故內容）都會存在帳號的伺服器端紀錄裡，和 README「工具不寫 prompt／response log」的定位不一致。stub 收到的 body 是 `{"model": "m", "input": [...], "max_output_tokens": 10}`。 | 加上 `body["store"] = False`，並在 `test_openai_provider.py` 裡檢查它。 |

## Low

- **L1 validator 會低估一些破壞性指令**（`validator.py:175-182`）。這些指令仍然需要批准，所以不算繞過，但依風險排序的顯示會警告不足：
  - git 接受長選項的唯一前綴：實際確認 `git reset --har` 會丟掉修改、`git clean --forc` 會刪掉 untracked 檔案。validator 卻把前者判為 MUTATING、後者判為 UNKNOWN。
  - `git -C . reset --hard`、`kubectl -n prod delete ns prod`、`/usr/bin/sudo rm -rf /`、`git branch -Df x` 都被判為 UNKNOWN。
  - `sed -i s/a/b/ .env` 被判為 SENSITIVE_READ，但它實際上會寫檔。
  - `rmdir`、`unlink`、`shred`、`dd of=`、`find -delete`、`docker rm -f` 都被判為 UNKNOWN。
  - `git push --force`、`git push origin :main` 只被判為 MUTATING。
- **L2 context packet 允許 `\r`、C1 控制字元與 bidi 字元**（`context.py:31`）：
  - `"rm -rf ~ \r- git status          "` 在終端機上只會顯示 `- git status`。
  - `classify_command` 因為遇到控制字元而拒絕，`assess_verify_next` 退回 UNKNOWN，不是 DESTRUCTIVE。
  - 修正：比照 `format_git_snapshot` 跳脫這些字元，並用去掉控制字元後的文字來判斷風險。
- **L3 claude-cli 把完整的環境與目前目錄傳給子 process**（`claude_cli.py:80-91,102`）：
  - 其他 provider 的 API key、`NODE_OPTIONS`、`ANTHROPIC_BASE_URL` 都會傳下去。`--safe-mode` 加上 `--tools ""` 可以減輕影響。
  - 修正：比照 codex，只傳 allowlist 裡的環境變數，並在空的暫存目錄執行。
- **L4 PATH 裡有空的或相對的項目時，會執行 repo 裡的同名 binary**（`claude_cli.py:196`、`codex_cli.py:265`）：
  - `PATH="/usr/bin:/bin:"` 加上 cwd 裡的 `./claude`，provider 就會執行它。
  - 修正：`shutil.which` 的結果不是絕對路徑時就拒絕。
- **L5 macOS 上 codex 的 `killpg` 遇到 zombie group 會丟 EPERM**（`codex_cli.py:85-89`）：
  - 逾時被誤報成 "isolated runtime failed"。
  - 修正：一起 catch `PermissionError`。
- **L6 API key 的檢查規則不一致**（`config.py:209-211` 與 `:266`、`configure.py:94`）：
  - 帶空白的 key（例如貼上時多了結尾空白）存檔時會通過，但每次請求都會被拒絕。
- **L7 stdin 被關閉時會噴出原始的 traceback**（`inputs.py:28`、`configure.py:18`）：
  - `llmtool ask hi <&-` 顯示 `AttributeError`，`llmtool configure <&-` 顯示 `RuntimeError: lost sys.stdin`。
- **L8 chat 的 exit code 與錯誤訊息**（`chat.py:134-136,141`、`:72-75`）：
  - 讀取輸入失敗時仍然 exit 0，但 README 寫的是 1。
  - 所有錯誤都被包成 "chat request failed"，使用者看不到缺少 key 或需要調高 max_tokens 這類原因。
- **L9 broken pipe 時 exit 120，並印出直譯器的訊息**（`cli.py:172,180`）。
- **L10 `timeout_seconds` 沒有上限**（`config.py:204-206`）：
  - 專案設定裡的 `timeout_seconds: 1e10` 會讓每一次請求都失敗，錯誤訊息只有 "gateway transport failed"。
- **L11 skills 目錄裡有雜檔時，所有 skill 都會失效**（`skills.py:82-85`）：
  - 例如 `.DS_Store`、`__pycache__`。另外，目錄名稱不必等於 frontmatter 的 `name`（`skills.py:95-98`）。
- **L12 測試環境依賴**（`tests/test_cli.py`）：
  - 子 process 的 `HOME` 被換成暫存目錄。在用 `pip install --user` 安裝 PyYAML 的機器上，30 個測試會以 "PyYAML is required" 失敗；這台機器的 `/usr/bin/python3` 3.9 就是這種情況。
  - 修正：子 process 的環境加上 `PYTHONUSERBASE=site.getuserbase()`。

## Nit

- `openai_compatible.py:105`：成功回應裡帶 `"error": null` 會被當成錯誤（`openai.py` 用的是正確的 `is not None`）。
- `openai_compatible.py:81-84`、`openai.py:178-181`：timeout 是單次 socket 操作的逾時，不是整體的時間上限，慢慢滴資料的 server 可以拖很久。README 已經寫明這一點。
- `claude_cli.py:115`：`prompt.encode()` 在 `Popen` 之後才執行，編碼失敗時子 process 不會被回收。CLI 本身不會走到這條路徑。
- `config show`：只設定 `api_key_env` 時也顯示 `api_key: '***REDACTED***'`。
- `LLMTOOL_PROVIDER=""`、`LLMTOOL_MODEL=""` 會被當成一個值，而不是當成沒設定。
- 沒有加引號的 YAML provider 名稱（例如 `2024:`、`on:`）會失敗，但 README 允許以數字開頭的名稱。
- `configure` 會先把所有問題（包括 secret）問完，最後才拒絕 symlink 的設定檔。
- chat 拒絕 `\x1b` 與 `\f`，所以貼上帶 ANSI 的 log 會失敗；以 `/` 開頭的訊息永遠送不出去。
- `skills.py:61`：帶 UTF-8 BOM 的 SKILL.md 會失敗。`skills.py:87-89`：先 lstat 再 open，有 race。
- `git_intel.py:199,338`：`GitDiff.truncated` 永遠是 False。

## 不確定（沒有證實）

- 讀取使用者設定時沒有檢查權限或擁有者。如果其他本機使用者能寫入 `~/.config/llmtool/config.yaml`，就能改掉 `base_url`，拿到字面寫在設定裡的 key。README 沒有承諾這項檢查。
- `save_config` 在 `os.replace`／`os.link` 之前沒有 fsync。在不支援 hard link 的檔案系統上，第一次 init 可能會失敗。
- 較舊版本的 `claude` CLI（例如 2.1.206）是否支援 `--safe-mode`。如果不支援，預期會 fail closed，但沒有測試。
- openai-compatible 要求 `finish_reason` 剛好是 `"stop"`，有些 gateway 正常結束時會回傳其他值。
- `git_intel.py:326` 在 `rev-parse --show-toplevel` 之後會重新探索 repo。在特殊的 `core.worktree` 設定下，可能會描述到另一個 repo（測到的情況是 fail closed）。
- 沒有任何 secret redaction。這符合 README 的契約（not secret-redacted），但把 `llmtool git` 接給 `review-diff` 時，tracked 的 `.env` 內容會原樣送出。

## 確認沒問題的部分

- **HTTP provider：**
  - 不跟隨 redirect，307 會成為 "gateway HTTP 307"，Authorization 只送到原本的主機。
  - 用 `ProxyHandler({})` 關閉環境變數的 proxy，TLS 使用預設驗證。
  - 回應上限 16 MiB，NaN、深層巢狀、無效 UTF-8、非 object 的 JSON 都有處理。
  - `tool_calls` 會被拒絕，錯誤訊息不含 key。
- **CLI provider：**
  - 使用 argv list，沒有 `shell=True`；model 值不能以 `-` 開頭。
  - stdout／stderr 各有 1 MiB 上限，stderr 不會顯示。
  - codex 的暫存 cwd、環境 allowlist 與 `project_doc_max_bytes=0` 都正確。
- **設定：**
  - YAML 使用 SafeLoader，拒絕 alias、`<<` merge key、重複與非字串的 key。
  - 先檢查 64 KiB 上限再解析。
  - 專案設定只能修改既有 profile 的 model、timeout、temperature、max_tokens，不能改 `base_url`、key、type、`provider_options` 或 active provider。
  - 設定檔以 0600 寫入，不覆蓋既有檔案，也拒絕 symlink。
- **git_intel：** 在設定了 `diff.external`、textconv、filter、`core.fsmonitor`、`core.pager`、`core.sshCommand`、`gpg.program` 的惡意 repo 裡，`collect_git_snapshot` 一個都沒有執行。
  - 環境會先清理，system 與 global config 都停用。
  - 逾時時砍整個 process group，輸出有上限。
  - 非 UTF-8 與 ESC 位元組會被跳脫。
- **validator 的解析：** 引號、`$()`、反引號、括號、重導向、heredoc、`&`、非 ASCII 空白都判為 UNKNOWN 或更高。除了 M4、M5 之外，找不到其他 SAFE_READ 繞過。
- **skills：** 名稱以嚴格的 fullmatch 驗證，沒有路徑穿越。package data 包含所有 SKILL.md。
- **Python 3.9：** 沒有使用 3.10 以後的語法，在隔離的 3.9 venv 裡完整測試通過。

## 注意事項

- 這份報告只列出問題，沒有修改任何程式。
- llmtool 還有其他分支正在開發（`feat/a7-skill-feedback-loop` 等），修正前請確認以哪個分支為基礎，避免和進行中的工作衝突。
