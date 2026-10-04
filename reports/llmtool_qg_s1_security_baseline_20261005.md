# llmtool QG-S1 — Security Baseline / Threat Model（2026-10-05）

## 1. Executive summary

**CONFIRMED SECURITY BUG COUNT: 0**

**QG-S1 RESULT: PASS WITH REQUIRED A11b DESIGN CONTROLS**

- 目前已實作的 A11a（加上 A0–A10 的既有 surface）**沒有發現可利用的漏洞**，也沒有 likely 漏洞。
- 以下各類攻擊都已經用 synthetic marker 和 local mock 實測過，現有控制都成立：
  - 憑證外洩到 stdout、stderr 或 diagnostics；
  - provider 錯誤內容或 reasoning 外洩；
  - redirect 或 proxy 把 Authorization header 帶到其他 host；
  - project config 改變 endpoint、key 或 executable；
  - CLI provider 的 argv injection；
  - 子 process 繼承環境變數中的 secret；
  - store 的 symlink、hardlink、forged eviction journal 刪除 store 以外的檔案；
  - terminal escape injection；
  - YAML 和 JSON 解析造成的資源耗盡；
  - 自動 fallback 把資料送出 trust boundary。
- 找到 **3 個 design gap**（DG-1～DG-3），都是 Low：
  1. 收到 SIGTERM 或 SIGHUP 時，CLI provider 的子 process 不會被結束；
  2. `ask`／`chat` 以外的指令遇到非預期例外時，會印出原始的 Python traceback；
  3. `LLMTOOL_HOME` 的上層目錄沒有檢查 owner 和權限。
- 另外有 3 個 theoretical risk，以及多項已寫明的限制。
- A11a 不需要先修任何東西。但 **A11b 開始之前，必須把 §17 列出的控制寫進 implementation spec 的驗收條件**，特別是：
  - process tree 在所有結束路徑上都會被終止（DG-1 的教訓）；
  - 所有入口都有 fail-closed 的例外處理（DG-2）；
  - 依據 profile 的 data-export／model 授權（DA-3）。

## 2. Repository / revision

| 項目 | 值 |
|------|-----|
| repository | `davidcatcat/davidcattoolsproject`，canonical repo `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`（`git worktree list` 只有這一個） |
| fetch 後的 `origin/main` | **`c77582a258d9ee388289127445aa4109f73ddc7b`**（PR #27 的 merge commit；PR #26 的 B1–B7 修正已 merge） |
| 審查時的 checkout | 開始時在已 merge 的 `fix/a11a-post-acceptance-bugs-20261005`（`3465990`，落後 main 27 個 commit，但 `tools/llmtool/llmtool` 和 `tests` 與 main 完全相同）。為了讀到 main 上的 skills 和文件，改為 **detached 在 `c77582a`**，沒有移動任何分支 |
| working tree | 只有使用者原本就有的未追蹤檔案，以及上一輪 bug hunt 留下的 `tools/llmtool/case.*`，全部保留 |

## 3. Scope

- **已實作的 surface：**
  - `ask`（normal、verbose、debug，以及 save／no-save）；
  - provider adapter：openai-compatible、openai、claude-cli、codex-cli；
  - config：user config、project `.llmtool.yaml`、環境變數、CLI flag；
  - diagnostics：store、reader、`runs`、`trace`、`doctor`、eviction journal、quarantine、staging；
  - 其他會輸出錯誤的既有指令：`analyze-log`、`review-diff`、`context-pack`、`orchestrate`、`retrieve-context`、`benchmark`、`config show`、`chat`；
  - A5 git inspection 的 hardening。
- **供應鏈：** `pyproject.toml` 和 `.github/workflows/llmtool-ci.yml`。
- **A11b 的 pre-execution threat model：** 依 ROADMAP 的 A11b.1–A11b.6 設計推導。
- **閱讀過的文件：**
  - `AGENTS.md`、`CLAUDE.md`、root `README.md`、`tools/llmtool/README.md`、`ROADMAP.md`、`WORKFLOW.md`；
  - `skills/security-review`、`bug-hunt`、`review-change`、`validate-runtime` 的 SKILL.md；
  - A11a implementation spec、A11a.7 final acceptance、A11a full bug hunt、B1–B7 bugfix validation 和 re-review。

## 4. Not in scope

- A11b、A11c、A12 的實作（尚不存在）：只做 design requirement。
- 真實的 provider binary 和網路 endpoint、TLS 的實際行為、原生 Windows、Linux 實機、另一個 OS user 的實際權限（沒有 sudo）。
- 正式 QA（QG-QA1）。
- 修正任何問題。

## 5. Security assumptions

1. **執行 llmtool 的本機 user 是 trusted user。** user config（`~/.config/llmtool/config.yaml`）、環境變數和 CLI flag 是 trusted input。
2. **project 內容是 untrusted**，包括 `.llmtool.yaml`、repo 檔案、log 和被 retrieve 的內容。
3. **model 和 provider 的輸出是 untrusted data**，不論模型品牌或 role。
4. 同一個 UID 的其他 process 可以修改這個 user 的所有檔案。這是 OS 層級的限制，已在文件寫明「same-UID namespace not isolated」。
5. `claude`、`codex`、`git` 執行檔由 trusted user 的 `PATH` 決定，視為 trusted（A1.1 起的設計）。
6. 公司 gateway 是 user 選擇的 trusted destination，但它可能被入侵；被入侵時 gateway 本來就會拿到 prompt 和 key。

## 6. Assets

| Asset | 存放 / 流向 | 機密性 | 完整性 |
|-------|------------|--------|--------|
| API key、token（`api_key`、`api_key_env` 的值） | user config、環境變數 → HTTP adapter 的 Authorization header | 高 | 中 |
| 其他環境變數中的 secret（AWS、OPENAI、ANTHROPIC 等） | process 環境 | 高 | — |
| user config／provider profile | `~/.config/llmtool` | 中 | **高**：決定 endpoint 和 key 的去向 |
| prompt、task 內容、retrieve 的檔案、log | stdin、`--file`、repo → provider | 高（公司資料） | 中 |
| model 回應、reasoning | provider → stdout | 中 | 低（untrusted） |
| diagnostics metadata（run、trace） | `LLMTOOL_HOME`（0700／0600） | 中（不應含內容） | 中 |
| source tree、Git 狀態 | repo | 中 | **高**（A11b 之後） |
| 未來的 approval、execution、filesystem、network 權限 | A11b 尚未存在 | — | **最高** |
| audit evidence（validation report、trace） | repo `reports/`、store | 低 | 高 |

## 7. Actors / trust levels

| Actor | Trust | 能力 / 說明 |
|-------|-------|------------|
| trusted user | trusted | 設定 user config、環境變數、CLI flag |
| user config | trusted | 可以設定 endpoint、key、provider type 和 diagnostics |
| project `.llmtool.yaml` | **untrusted** | 只能設定既有 profile 的 `model`、`timeout_seconds`，以及 `generation.temperature`、`max_tokens` |
| repository／retrieved content | **untrusted** | 被當成資料送給 provider；不能下指令 |
| local environment（`LLMTOOL_*`、`PATH`、`HOME`） | trusted（由 user 控制） | `LLMTOOL_BASE_URL`、`LLMTOOL_HOME` 等 |
| 同一個 UID 的其他 process | 等同 user（OS 限制） | 可以修改 store 和 config |
| 其他本機 user | untrusted | 只能影響 world／group 可寫入的路徑 |
| 公司 LLM gateway | trusted destination，但輸出 untrusted | 會收到 prompt 和 key |
| 外部 LLM provider（OpenAI） | 需由 user 明確選擇 | 資料出境 |
| Claude CLI／Codex CLI | trusted executable，但輸出 untrusted | 有自己的工具能力，llmtool 用參數和環境限制它們 |
| model 產生的輸出 | **untrusted** | 不能被當成 approval、command 或 policy |
| CI（GitHub Actions） | 半信任 | `permissions: contents: read`，`persist-credentials: false`，actions 以 SHA pin |
| Git repository（hostile `.git/config`） | **untrusted** | A5 已中和 |
| 未來的 Telegram frontend、A11b executor | 尚未存在 | 見 §17 |

## 8. Trust boundaries

```text
[trusted user] ──CLI args/env──► llmtool CLI ◄──(untrusted) project .llmtool.yaml (allowlist: model, timeout, temperature, max_tokens)
                                   │  ▲
                 user config ──────┘  │ (untrusted) stdin / --file / retrieved repo content / logs
                                   │
                                   ▼
                       provider adapter  ── TB1: secrets leave the process (Authorization header / child env allowlist)
                                   │
                    ┌──────────────┴───────────────┐
                    ▼                              ▼
     HTTP gateway / OpenAI (network)      claude / codex CLI (child process, isolated /tmp cwd,
                    │                       --safe-mode --tools "" / codex llmtool-text profile)
                    └──────────────┬───────────────┘
                                   ▼  TB2: untrusted model text enters
                        returned model text ──► validator / log_safety (A6, classify only)
                                   │
                         ┌─────────┴─────────┐
                         ▼                   ▼
                stdout (TTY: escaped;    diagnostics (allowlisted metadata only)
                pipe: raw by contract)       │  TB3: filesystem persistence
                                             ▼
                                   LLMTOOL_HOME store (0700/0600, O_NOFOLLOW, dir-fd relative)
                                             ▲
                                   runs / trace / doctor (read-only, bounded, fixed output)
```

**未來（A11b）必須新增的 boundary：**

```text
model output ──(proposal only)──► typed action ──► policy (contract/scope) ──► approval (bound to digest/state/expiry)
     ──► executor (sanitized env, argv, sandbox, timeout, process-tree containment) ──► filesystem / tests / Git / network
     ──► audit trail
```

**原則：** model 和 provider 的輸出、retrieve 的內容、repository 內容一律是 data。模型品牌、role、provider capability、成功解析、debug mode，都**不能**把它們變成 authority。

## 9. Data / action flow（目前）

1. 解析 CLI 參數（`SafeParser` 不 echo 輸入）。usage 錯誤回傳 exit 2，不建立 run 或 store。
2. resolve config：user config → project fragment（先驗證整個 allowlist 再 merge）→ 環境變數 → CLI flag。diagnostics 設定只能來自 user config。
3. 讀取輸入：stdin、`--file`、文字三選一；上限 16 MiB；拒絕 FIFO、`/dev/zero` 等非一般檔案。
4. provider 呼叫（只呼叫一次，沒有 retry 或 fallback）：
   - HTTP：不跟隨 redirect（`NoRedirect`），忽略 proxy（`ProxyHandler({})`），使用預設的 TLS 驗證，回應上限 16 MiB。
   - CLI：`shell=False`、argv 固定、環境變數只用 allowlist（PATH、HOME、LC_CTYPE、PWD，codex 另有 TMPDIR）、cwd 是私有的 `/tmp/llmtool-*`、輸出上限每個 stream 1 MiB。
5. 回應處理：只取文字。錯誤內容、stderr、reasoning、tool-call 一律丟棄，改用固定訊息。
6. 輸出：TTY 會 escape 控制字元；pipe 保持原始 bytes（契約）。
7. diagnostics（選擇儲存時）：只有 allowlist 內的 metadata，透過 staging 和不覆蓋的 rename 發布。

## 10. Current A11a attack surface

| Surface | 輸入來源 | 主要風險 |
|---------|---------|---------|
| config loading（YAML） | user config、project config | 解析炸彈、欄位提權、endpoint 或 key 被重新導向 |
| HTTP adapter | trusted endpoint、回應是 untrusted | key 外洩（redirect、proxy、錯誤內容）、超大回應 |
| CLI adapter | trusted 執行檔、輸出是 untrusted | argv injection、環境 secret 繼承、工具被啟用、process 殘留 |
| 輸出呈現 | model、錯誤、trace | terminal injection、traceback 洩漏 |
| diagnostics store | 同一個 UID 或其他 user 可接觸的檔案系統 | link following、TOCTOU、刪除 store 外的檔案、DoS |
| reader／doctor | store 內容（可能被竄改） | 惡意 artifact 的呈現、讀取 store 外的檔案 |
| retrieve-context、git inspection | repository（untrusted） | 檔名控制字元、hostile git config |
| CI／supply chain | GitHub Actions、PyPI | action 被劫持、dependency 被竄改 |

## 11. Security controls already present（已用 probe 驗證）

**憑證與隱私（secrets agent，3.13 和 3.9）：**
- HTTP 401／500 的錯誤內容即使原樣包含 key，也不會顯示；只有固定訊息，例如 "gateway HTTP 401; response body omitted"。
- 301／302／307／308 redirect 不會被跟隨。指向第二個 local server 時，該 server 收到 **0 次 request**，Authorization header 沒有跨 host。
- 環境中的 `HTTP_PROXY`／`http_proxy`／`ALL_PROXY` 被忽略，proxy 收到 0 次 request。
- `reasoning_content`、`reasoning`、`encrypted_content`、未知欄位，都不會出現在 stdout、stderr、verbose、debug 或 store。
- store 裡沒有 prompt、回應、key 或錯誤內容；`runs`、`trace`、`trace --errors`、`trace --json` 在每種 mode 都乾淨。
- CLI 子 process 的 stderr、以及非零 exit 時的 stdout 都被丟棄。子 process 的環境只有 allowlist，`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、AWS 等變數都沒有被繼承。
- `config show` 會遮蔽 `api_key`；`doctor` 只回報 credential「是否存在」，不顯示值或路徑。
- `ask` 和 `chat` 遇到非預期例外時只印固定文字，也不會儲存例外內容。

**config trust（config agent）：**
- project config 嘗試設定以下欄位時，**全部在發出 request 之前被拒絕**，攻擊者的 server 收到 0 次 request：
  - `base_url`（包括 `null`）；
  - `api_key_env: AWS_SECRET_ACCESS_KEY`；
  - `type: claude-cli`；
  - `active_provider`、`provider_options`、新的 profile 名稱、大小寫變體、`diagnostics`。
- project config 只從 cwd 讀取，上層目錄裡的 `.llmtool.yaml` 會被忽略；FIFO、指向 `/dev/zero` 的 symlink、指向 user config 的 symlink 都被拒絕。
- config 無法指定 claude 或 codex 執行檔；`PATH` 中的相對路徑會被拒絕。

**provider adapter：**
- **沒有 argv injection：** project 設定的 model `sonnet --dangerously-skip-permissions` 仍然只是 `--model` 後面的單一 argv 元素；以 `-` 開頭的 model 會被拒絕，codex 使用嚴格的 regex。
- **實際的 claude argv：** `--print --output-format text --no-session-persistence --safe-mode --tools "" [--model X]`。`--safe-mode` 會停用 CLAUDE.md、skills、plugins、hooks 和 MCP。
- **codex：** 使用 `llmtool-text` permission profile，停用網路，`project_doc_max_bytes=0`，關閉 feature flag，prompt 從 stdin 傳入。
- timeout 和 Ctrl-C 時，會結束整個 process group（包括孫 process），並清除暫存的 cwd。

**輸入與輸出：**
- YAML 使用 SafeLoader；alias、duplicate key、`!!python` tag、`<<` merge、30000 層巢狀、超過 64 KiB 的檔案都會被拒絕。
- stdin、`--file` 超過 16 MiB，或無限輸入（`yes |`），都會被拒絕，也不會發出 request。
- 在真實的 pty 上，`ask`、`analyze-log`、`review-diff`、verbose 的 ESC、OSC、BEL、U+202E（bidi）、C1 0x9b 都會被顯示成可見的 escape。
- git inspection 面對 hostile repo config（fsmonitor、`diff.external`、pager、clean／smudge filter、textconv、`sshCommand`、gpg）時什麼都沒有執行。實作上使用 `GIT_CONFIG_NOSYSTEM`、`GIT_CONFIG_GLOBAL=/dev/null`、一組 safe `-c`、所有 protocol 都設為 `never`、sanitized 環境、`shell=False`。
- **沒有自動 fallback：** HTTP 500 或無法連線時都只有 1 次 request，備用 profile 和 CLI 都沒有被呼叫。orchestration 和 retrieval 只使用明確指定的 profile，也不套用 project config。

**diagnostics store（filesystem agent）：**
- 以下用 forged journal 或 quarantine 的嘗試，**全部回報 UNSAFE**，受害檔案和外部目錄都沒有受損：
  - 把 store 外的受害檔案 hardlink 進 run，journal 記錄真實的 nlink=2，或謊稱 nlink=1；
  - quarantine 裡放 hardlink 或 symlink；
  - `.evicted-*` 本身是指向 store 外相似目錄的 symlink；
  - `runs/` 裡的 run 是 symlink。

  所有刪除都是相對於 store 內 dir-fd 的 name unlink。journal 的 token 和 run_id 必須是 32 個 hex 字元，不可能做 path traversal。
- umask 000 時，建立的目錄仍是 0700、檔案仍是 0600。既有的 0755／0750 目錄會被拒絕，而且不會被 chmod。symlink 的路徑元件、其他 user 預先建立的目錄都會被拒絕。
- 偽造的 trace 含有 ESC、bidi、zero-width 字元時回報 `FORMAT_INVALID`；輸出使用 `ensure_ascii` 的 JSON；`doctor` 只輸出計數。
- store 的任何失敗都不會改變 `ask` 的結果或 exit code。

**CI／供應鏈：**
- actions 以 commit SHA pin；`permissions: contents: read`；`persist-credentials: false`；PyYAML 是唯一的 runtime dependency，CI 中 pin 在 `==6.0.3`。

## 12. Confirmed vulnerabilities

**無。CONFIRMED SECURITY BUG COUNT: 0**

## 13. Likely vulnerabilities

**無。**

## 14. Design gaps

### DG-1 — CLI provider 的子 process 在 SIGTERM／SIGHUP 後成為孤兒，暫存的 cwd 也不會被清除

- **Classification：** DESIGN GAP。**Severity：** Low。**Confidence：** High。**CWE：** CWE-404（Improper Resource Shutdown or Release）。**CVSS：** 不適用（不是可利用的漏洞）。
- **位置：** `providers/claude_cli.py` 的 `_invoke`（約 141 行）和 `providers/codex_cli.py` 的 `_invoke`（約 180 行）使用 `start_new_session=True`，但 llmtool 沒有任何 SIGTERM／SIGHUP handler（找不到 `signal.signal`、`atexit`）。
- **Actor／precondition：** 送出 TERM 或 HUP 給 llmtool 的任何東西，例如關閉 terminal、process supervisor、CI cancel。
- **路徑：** llmtool 收到 SIGTERM 就直接結束（rc=143）。子 process 在自己的 session 裡，不會收到 terminal 的 HUP，於是被 re-parent 到 PID 1 繼續執行，`finally` 裡的清理也沒有執行。
- **預期：** README 寫「Timeout/overflow kills the process group」「Temporary cwd cleanup runs on success/failure」。timeout 和 Ctrl-C 時確實成立，TERM／HUP 時不成立。
- **重現（我親自執行）：** 用假的 `claude`（fork 一個 `sleep 300`）執行 `ask`，2 秒後 `kill -TERM <llmtool>`：
  - llmtool rc=143；
  - `bin/claude` 的 PPID 變成 1，`sleep 300` 仍在執行；
  - `/private/tmp/llmtool-claude-*` 留在原地。
  - probe：`$Q/verify-term.FsAR/`。我之後已經結束自己建立的測試 process。
- **影響：** 已經被 sandbox、只能輸出文字的 model 呼叫會在沒有 deadline 的情況下跑完，造成成本和暫存目錄殘留；沒有資料外洩。**但 A11b 的 executor 如果沿用同樣的模式，被中斷的 test 或 command 可能在 policy 之外繼續修改檔案。**
- **是否阻擋 A11b：** 不阻擋 A11a，但這是 **A11b 的必要控制**（R-EXEC-6）。

### DG-2 — `ask`／`chat` 以外的指令遇到非預期例外時，輸出原始 traceback

- **Classification：** DESIGN GAP（defense in depth）。**Severity：** Low。**Confidence：** High。**CWE：** 只有在含有 secret 的例外真的可能發生時，才符合 CWE-209。**CVSS：** 不適用。
- **位置：**
  - `cli.py` 的 `main`（約 364–381 行）只 catch `LLMToolError`、`BrokenPipeError`、`OSError`／`UnicodeError`、`EOFError`、`KeyboardInterrupt`；
  - `doctor.py` 的 `run_doctor` 和 `diagnostics_cli.py` 的 `run_diagnostics` 只 catch `OSError`／`ValueError`／`TypeError`／`KeyError`；
  - 對照組：`_run_ask` catch `Exception`，並印出固定文字。
- **路徑：** 這些指令（`analyze-log`、`review-diff`、`context-pack`、`orchestrate`、`benchmark`、`retrieve-context`、`config show`、`doctor`、`runs`、`trace`）內部的非預期例外 → Python 預設的 traceback 印到 stderr。traceback 包含 host 路徑（`/Users/...`）和 `str(exc)` 的完整內容，而使用者常常把它貼到 issue、CI log 或聊天裡。
- **重現：**
  - secrets agent 把 `OpenerDirector.open`、`doctor.inspect_config`、`DiagnosticsReader.runs`、`cli.show_config` monkeypatch 成丟出 `RuntimeError("SYNTH_SECRET_QGS1_EXCMARKER key=<api key>")`，10 個指令都輸出了含 marker 和 key 的完整 traceback。
  - 我也親自用 `config show` 重現了（`$Q/verify-tb.*`）。
  - **目前找不到真實的觸發方式：** fuzz 了 1e309、NaN、IDNA URL、FIFO、traversal、控制字元，全部回傳固定訊息。
- **是否阻擋 A11b：** 否。但 A11b 的所有新入口都必須遵守「unexpected → fixed message」（R-AUD-3）。建議在 A11b 之前或之中，於 `main()` 補上 catch-all。

### DG-3 — `LLMTOOL_HOME` 的上層目錄沒有檢查 owner 和權限

- **Classification：** DESIGN GAP。**Severity：** Low。**Confidence：** High。**CWE：** CWE-732 部分符合。
- **位置：** `diagnostics_store.py` 的 `_home`（約 131–147 行）和 `diagnostics_reader.py` 的 `_home`。中間的路徑元件只用 `O_NOFOLLOW` 檢查不是 symlink，只有最後一層檢查 owner 和 mode。
- **Actor：** 可以寫入某個「沒有 sticky bit 的共用可寫上層目錄」的另一個本機 user，例如 `LLMTOOL_HOME=/shared/x/llmtool`。預設路徑和有 sticky bit 的 `/tmp` 不受影響；macOS 的 `/tmp` 本身是 symlink，會直接被拒絕。
- **路徑：** 攻擊者把 victim 的 store 改名移開，再把同一個上層目錄裡、victim 擁有的另一個 0700 目錄改名成 store 的名稱。victim 下次儲存時，就把 metadata 寫進那個目錄。probe：`$Q/p3.arl5/p.py`（單一 user 模擬，上層目錄 mode 0777）。
- **影響：** 0600 的 metadata 被寫進 victim 自己的另一個目錄，攻擊者也能讓 diagnostics 無法使用。**沒有機密性損失**：最後一層必須是 victim 擁有的 0700 目錄，攻擊者讀不到；也不會刪除任何檔案。
- **建議：** 拒絕沒有 sticky bit 的 group／world 可寫上層目錄，或 owner 不是 user／root 的上層目錄；至少在文件中寫明。
- **是否阻擋 A11b：** 否。但 A11b 的 filesystem policy 必須包含上層目錄的檢查（R-FS-3）。

## 15. Theoretical risks

| ID | 內容 | 為什麼只是 theoretical |
|----|------|----------------------|
| TR-1 | gateway 回傳的 `model` 欄位被印出和儲存。key 的遮蔽只比對「完整的 key 子字串」，所以惡意 gateway 回傳 `model = key[:-1]` 這類片段時，會出現在 verbose 輸出和 `trace.jsonl`（`case.modelkeypartial.*` 已實測）。`sk-`、`ghp_`、AKIA、JWT 形式的 key 另外有 pattern 攔截，但不透明的公司 key 不會被攔到 | 攻擊者是已經拿到完整 key 和 prompt 的 gateway，得不到新的資訊；只是「metadata-only」的 diagnostics 可能夾帶 key 片段。建議只在 echo 和設定的 model 相同時才儲存 |
| TR-2 | 同一個 UID 的 process 持有 `.store.lock`，會讓每次開啟儲存的 `ask` 多等最多 2 秒（`STORE_UNAVAILABLE`），結果不變 | 只有同一個 UID 才做得到；屬於 diagnostics availability，已寫明 |
| TR-3 | CLI 子 process 正常結束時，它 detach 出去的孫 process 不會被結束（`finally` 只在直接子 process 還活著時 killpg） | claude 和 codex 是 trusted 執行檔，它們本來就能用 setsid 逃脫。列為 A11b 的 hardening |

## 16. Accepted / documented limitations

| 項目 | 依據 |
|------|------|
| project config 可以在同一個 endpoint 上選擇 `model`、`timeout_seconds`、`max_tokens`、`temperature`（`pA1.LejT` 已實測：request 送到 user 自己的 endpoint，帶著 user 的 key，但 model 是 `attacker-model`）。在多模型的公司 gateway 後面，可能把資料導向 gateway 轉發的外部上游，或造成成本濫用 | README 寫明「不適合當作 model ACL」。**列為 DA-3（A11b／A11c 的 data-export requirement）** |
| key 會送到任何 `base_url`，包括明文 `http://`，以及 `--base-url`／`LLMTOOL_BASE_URL` 指定的位置 | 這些都是 trusted 來源（README 約 486 行）；公司範例沒有使用 key。建議對非 loopback 的 http 加上警告（hardening） |
| stdout 是 pipe 時，model 輸出不 escape | README 契約：redirect 的輸出保持原樣 |
| socket timeout 是每次讀取的上限，不是總時間上限 | README 寫明 |
| `claude`、`codex`、`git` 由 `PATH` 決定 | 假設 5 |
| 同一個 UID 可以操作 store：forged journal 可以刪除 store 內任何已完成的 run（`p6.jfBt`）；畸形 artifact、FIFO 或極深的目錄會讓儲存永久停用（`p2`、`p5`） | 「same-UID namespace not isolated」、N1、M2，以及修正前就存在的損壞 reservation 都已寫明；不影響 `ask` 的結果，也只限於 store 內 |
| `retrieve-context` 遇到含控制字元的檔名時整個 fail-closed | availability；不會 echo，也不會發出 request |
| PyYAML 在 CI 中只 pin 版本，沒有 hash | `requirements-ci.txt` 寫明「not a hash lock」。建議 A11b 的 release 流程考慮 `--require-hashes` |
| M2、M4、M5、P1–P4、N1、1 小時的 grace period、SIGKILL 不等於斷電 | 已在 README 和 ROADMAP 寫明，屬於可用性和容量限制，不是安全邊界 |

## 17. A11b security requirements（DESIGN REQUIREMENT FOR A11b）

A11b 尚未實作。以下是 A11b implementation spec **必須**轉成驗收條件和 failure test 的控制；它們不是目前的漏洞。

### Command execution（R-EXEC）
1. 不接受任意的 model shell string；只接受經過 review 的 typed action schema 或 allowlist，由 deterministic executor 組出 argv，`shell=False`。
2. argv 元素不能以 `-` 開頭變成 option（必要時加上 `--`），路徑參數要先 canonicalize。
3. 子 process 的環境變數採用 allowlist（延續 CLI adapter 的做法），不繼承任何 credential；需要 credential 的 action 必須在 policy 中明確宣告並受到限制。
4. 每個 action 都有 wall-clock timeout 和輸出上限。
5. cwd 限制在授權的 repo 或 scope 內；**cwd 本身不是 sandbox**。執行 repository code 或 tests 時，必須在 OS 層級限制 filesystem 和 network（macOS sandbox-exec／seatbelt、Linux namespaces／landlock 或同等機制）；無法提供隔離的平台必須拒絕執行。
6. **process tree containment 必須涵蓋所有結束路徑**：timeout、Ctrl-C、SIGTERM、SIGHUP、llmtool crash 和正常結束（DG-1、TR-3）。在所有 exit signal 時都要 killpg，或使用 parent-death 或 supervisor 機制，並且要有 failure test。
7. 不使用 sudo、setuid 或任何提權方式；以 root 執行時拒絕。
8. network 預設關閉，必須逐一明確授權。

### Filesystem mutation（R-FS）
1. 授權 scope 精確到 repo root 和路徑集合；路徑要先 canonicalize（不跟隨 symlink），拒絕 `..`、絕對路徑和跨 repo 的路徑。
2. symlink 和 hardlink 的 policy：以 `O_NOFOLLOW` 和 dir-fd 為基礎（延續 A11a store 的做法），要求目標是一般檔案而且 nlink=1，否則拒絕或明確處理。
3. 上層目錄的 owner 和 mode 檢查（DG-3 的教訓）。
4. 寫入時防止 race：寫 temp → fsync → 不覆蓋或 compare-and-swap 的 rename；寫入前比對預期的 inode 或 digest，偵測外部的並行修改。
5. 單檔、總量和檔案數的上限。
6. rollback 只能恢復這個 action 已知的範圍；偵測到外部修改時就停止，保留證據，絕不使用 `reset --hard` 或 `clean`。

### Git（R-GIT）
1. executor 的所有 git 呼叫都必須沿用 A5 的 hardening：`GIT_CONFIG_NOSYSTEM`、`GIT_CONFIG_GLOBAL=/dev/null`、safe `-c`（fsmonitor、hooksPath、pager、attributesFile、所有 protocol 設為 `never`）、`--no-ext-diff`、`--no-textconv`、filter 偵測、sanitized 環境、`shell=False`、timeout 和 process group。
2. mutation 類的命令（commit、apply、checkout）也必須中和 hooks、filters（clean／smudge／process）、credential helper、`core.sshCommand`、`gpg.program` 和 `safe.directory`。
3. dirty worktree、conflict、detached HEAD 等非預期狀態預設 fail closed。
4. 不 push；如果未來支援 push，不能假設 branch protection 存在，也不能 force。

### Approval（R-APR）
1. approval 綁定 task ID、contract version／digest、action type、payload digest、repo／scope、預期狀態（HEAD、檔案 digest）、到期時間，並使用一次性 nonce。
2. 防止 replay、action 被替換，以及 approval 之後的 TOCTOU：執行前重新比對 digest 和狀態，任何改變都會讓 approval 失效。
3. 不能 approve 未來尚不知道的 action；不能 approve 整個 task。
4. **model 文字中的 "approved" 不是 approval**；approval 只能來自 TTY prompt 或經過驗證的 frontend 通道。non-TTY、CI 和背景執行預設拒絕，或停在 `waiting_approval`。
5. 防止 confused deputy：provider capability、debug mode 和 frontend transport 都不能自動授權。

### Prompt／model-output injection（R-INJ）
1. repository 的 README、註解、log、issue、retrieve 的文件、model 回應都是 data。其中出現「ignore policy」「run this command」「print credentials」「approve action」這類文字時，只能被當成內容。
2. policy 和 approval 的判斷不得以 model 輸出作為輸入條件；A6 classifier 只能作為參考，不能授權。
3. 要有 failure test：惡意的 repo 內容和 model 回應不能讓 action 繞過 policy 或 approval。

### Provider fallback／escalation／data export（R-EXP）
1. 沒有自動 fallback（維持現況）。任何 escalation 都必須在**送出前**重新檢查資料範圍、destination 和 provider 的 trust policy；未授權就停止。
2. summary、壓縮或 redaction **不等於** export permission。
3. **DA-3：** 依據 profile 的 data-export／model 授權。project config 選擇的 model 必須在 user config 宣告的允許清單內，或者讓 project config 不能再設定 `model`，避免 untrusted repo 透過多模型 gateway 把公司資料送往外部上游。
4. repair 或 escalation 不得放寬安全 policy 或驗收條件。

### Audit／diagnostics（R-AUD）
1. 每個 mutation 都有 audit event，綁定 contract digest、action digest、approval ID、前後狀態和 exit status。
2. A11a diagnostics 的 allowlist 原則延伸到 executor：不記錄 tool 的原始 stdout／stderr、環境變數或 secret；需要時改記 digest 或 reference。
3. 所有入口（包括 A11b 新增的指令）都有 catch-all 例外處理，只輸出固定訊息（DG-2）。
4. audit 必須在 action 之前就 durable；audit 寫入失敗時，mutation 必須 fail closed。這點和 A11a diagnostics 刻意採用的 fail-open 不同，必須明確區分。

### Remote frontend（A12，R-RMT）
1. Telegram 等 frontend 只是 approval 和 transport，必須走同一套 core policy；frontend 的身分驗證和 approval 綁定要沿用 R-APR。

## 18. OWASP／CWE／CVSS mapping

- **CVSS v4.0：** 沒有 confirmed 或 likely 漏洞，所以**沒有評分**。design gap 和 theoretical risk 不評分。
- **CWE（只在真正符合時列出）：**

  | ID | CWE |
  |----|-----|
  | DG-1 | CWE-404 |
  | DG-2 | CWE-209（條件式） |
  | DG-3 | CWE-732（部分） |
  | TR-1 | CWE-532（最多） |
  | TR-2 | CWE-400 |

- **A11b requirement 對應的 weakness class：**
  - R-EXEC → CWE-78、CWE-88、CWE-269
  - R-FS → CWE-22、CWE-59、CWE-367
  - R-APR → CWE-862、CWE-863、CWE-294（replay）
  - R-INJ → CWE-94、OWASP LLM01（Prompt Injection）
  - R-EXP → OWASP LLM02（Sensitive Information Disclosure）、LLM06（Excessive Agency）
- **OWASP ASVS 5.0：** 只參考適用於 CLI 的部分，例如 V5／V1 的 encoding 和 injection 預防、V8 的資料保護、V14 的設定、V7 的錯誤處理和 logging。web session、cookie、CSRF 等項目不適用，沒有硬套。
- **OWASP Top 10：** A03 Injection（argv、terminal：已控制）、A05 Security Misconfiguration（DG-3）、A08 Software and Data Integrity（CI pin）、A09 Logging Failures（R-AUD）。

## 19. Tests / probes executed

- **baseline：** 與 PR #26 的 runtime 相同（`tools/llmtool/llmtool` 和 `tests` 和 main 完全相同）。上一輪在這份程式碼上跑過 893 tests，3.13 和 3.9 都 OK（1 skipped）。**本輪沒有用全綠的測試推論安全**，結論完全依據下面的 probe。
- **probe 環境：** scratchpad `$Q=/private/tmp/claude-501/-Users-davidsun-git-davidcataiporject-davidcattoolsproject/88ee90aa-8e62-4eeb-a67b-e373aeba2609/scratchpad/qgs1/`，每個 probe 各用一個 `mktemp -d`，以 scratchpad 為工作目錄，`env -i`，獨立的 `HOME`／`LLMTOOL_HOME`。
- **三個平行方向，我親自重現了 DG-1 和 DG-2：**
  1. **secrets／privacy：**
     - 腳本：`mock.py`、`run_mode.sh`、`run_mode39.sh`、`run_openai.sh`、`probe_openai.py`、`run_cli.sh`、`run_exc.sh`、`probe_exc.py`、`run_ovr.sh`、`run_fuzz.sh`。
     - case 目錄：`case.*`、`oa.*`、`cli.*`、`exc.*`、`ovr.*`、`fuzz.*`。
     - 涵蓋：synthetic key、環境變數、prompt、檔案、reasoning、錯誤內容、子 process 的 stderr；redirect（兩個 local server）；proxy；例外 injection；所有輸出通道。
  2. **filesystem／store：**
     - probe：`h.py`、`p1`–`p6`。
     - 涵蓋：forged journal 和 quarantine、hardlink 或 symlink 的受害檔案、umask、權限、上層目錄 swap、lock 被持有、FIFO、深層目錄、惡意 trace 的呈現。
  3. **config／provider／parsing：**
     - kit：`kit.JQTw`（會記錄 header 的 mock server、會記錄 argv／env／cwd 的假 CLI）；probe：`pA*`、`pB*`、`pC*`、`pD*`。
     - 涵蓋：project config 的各種欄位、上層目錄的 config、argv injection、子 process 環境、timeout、Ctrl-C、SIGTERM、YAML 炸彈、輸入上限、pty 呈現、hostile git config、fallback。
- **使用 network／provider：** 只有 127.0.0.1 上的 mock，以及絕對路徑的假 claude／codex。**沒有使用公司 LLM、外部 provider 或真實的 CLI session；沒有使用任何真實 credential。**

## 20. Environment

macOS（Darwin 24.6.0），Python 3.13.3（`tools/llmtool/.venv`）和 3.9.6（`/usr/bin/python3`），PyYAML 6.0.x。只有一個 user，沒有 sudo。

## 21. NOT-TESTED

- 真實的 claude／codex binary：只用參數和 `--help` 推論它們的工具限制，沒有驗證實際 runtime 是否遵守，也沒有驗證 codex 是否讀取 `/tmp` 上層目錄的 `.codex` 或 `.git`。
- 真實的 TLS、網路 endpoint、公司 gateway 的多模型轉發行為（DA-3 的實際可行性）。
- 實際的跨 UID 行為（另一個 OS user 在沒有 sticky bit 的上層目錄做 rename）、root 或 sudo 下的執行、Linux（`renameat2`、XDG）、NFS 等沒有 flock 的 filesystem、原生 Windows。
- `configure` 的互動流程。
- 舊的 `tools/llmtool/build/lib` 副本：editable install 不會使用它，所以沒有檢查。

## 22. A11b go/no-go security recommendation

- **A11a：GO（不需要先修任何東西）。** DG-1～DG-3 都是 Low 的 design gap，沒有可利用的路徑。
- **A11b：CONDITIONAL GO**。前提：
  1. A11b implementation spec 把 §17 的 R-EXEC、R-FS、R-GIT、R-APR、R-INJ、R-EXP、R-AUD 轉成具體的驗收條件和 failure test，特別是 R-EXEC-5（OS 層級的 sandbox，無法提供就拒絕）、**R-EXEC-6（process tree 在所有 signal 下都會被終止）**、R-APR-1/2（approval 綁定 digest 和狀態）、**R-EXP-3（DA-3 依據 profile 的 model／export 授權）**、R-AUD-3/4。
  2. 建議在 A11b 實作之前（或做為 A11b 的第一個 slice）處理 DG-1（加上 TERM／HUP handler）和 DG-2（`main()` 的 catch-all），避免新的 executor 沿用這兩個模式。
  3. spec 先經過 security review，再開始實作。

## 23. 流程記錄

- 除了這份報告，沒有修改 repo（沒有改 production code 或 tests，也沒有 commit、push、merge）。HEAD 是 detached 在 `origin/main` `c77582a`。
- **暫存檔：** 任務要求 probe 完成後清理，但依照使用者先前定下的「不使用 `rm` 清理」規則，**我沒有刪除 scratchpad 裡的 probe**。它們在 session scratchpad 內，不在 repo 裡。
- **probe 期間 llmtool 自己留下的暫存目錄：** `/private/tmp/llmtool-claude-d8pthuzl`（我重現 DG-1 時產生）、`/private/tmp/llmtool-claude-i5vk_goy`、`/private/tmp/llmtool-codex-zreukwf9`（agent 的 SIGTERM probe 產生）。它們是 DG-1 的證據，內容是空的，同樣沒有刪除。
- **agent 偏離規則的地方，如實記錄：**
  - secrets agent 在 scratchpad 裡沒有偏離；
  - config agent 在 scratchpad 裡用 `/bin/rm -f` 刪過一個自己建立的 `.llmtool.yaml`；
  - 兩個 agent 結束了自己啟動的 mock server 和假 CLI process；
  - 我重現 DG-1 之後，也結束了自己建立的兩個測試 process。
- **subagent 輸出被 harness 標記：** config agent 的回報觸發了一次 instruction-shaped pattern 警告，原因是測試用的 model 字串 `sonnet --dangerously-skip-permissions`。那是 argv injection 的測試值，不是指令，也沒有被執行。
- 上一輪 bug hunt 留下的 `tools/llmtool/case.*` 仍然保留，等使用者決定。

---

**CONFIRMED SECURITY BUG COUNT: 0**

**QG-S1 RESULT: PASS WITH REQUIRED A11b DESIGN CONTROLS**
