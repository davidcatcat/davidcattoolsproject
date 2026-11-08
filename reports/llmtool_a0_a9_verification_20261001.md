# llmtool A0–A9 全面驗證（公司 VPN／gateway 實測，2026-10-01）

## 範圍與方法

- 驗證對象：`tools/llmtool`，`origin/main` `68d189d87320938fabb376c94e78b9c7e1e53823`（A0–A9 都標示 DONE 且已 merge；A10 之後尚未開始）。
- 依據：`tools/llmtool/ROADMAP.md`（A0–A9，A10–A12 只用來確認範圍之外的項目）、`tools/llmtool/README.md`，以及各 milestone 的 validation report。
- 方式：**唯讀**，沒有修改任何程式或 branch。依 milestone 分成三組，各由一個獨立的 reviewer 進行：
  - A0–A3.5
  - A4–A6
  - A7–A9

  最重要的結論由 Lead 再自行重現確認。每一組都檢查：
  - 程式邏輯：程式碼與測試
  - 功能邏輯：實際執行 CLI
  - 架構邏輯：模組邊界、依賴關係與資料流
  - 流程：README 描述的使用流程

- 安全邊界：
  - CLI 一律以 `env -i HOME=<暫存目錄> PATH=/usr/bin:/bin` 執行，所有檔案都放在 scratchpad 的暫存目錄。
  - 沒有讀取真實的 `~/.config/llmtool`、憑證、keychain、`.env` 或 SSH key。
  - **唯一呼叫的網路服務是公司 gateway**（`http://home-llm.example:8000`，route `gw`，模型回報為 `qwen3.6-35b-a3b`，openai-compatible，`enable_thinking: false`，不需要 API key）。
  - 真的 Claude CLI、Codex CLI 與 OpenAI API 會使用個人帳號，所以**沒有呼叫**，只用假的執行檔和 `127.0.0.1` 上的 stub server 驗證。
- 清理：三個 worktree 的 `git status --short` 都是空的。啟動過的 stub server 都已停止，沒有刪除任何檔案。

## 基本檢查

| 項目 | 結果 |
|------|------|
| Python 3.13.3（`tools/llmtool/.venv`） | 全部測試 OK（1 skipped）；在暫存 HOME 下共 514 tests |
| Python 3.9.6（隔離的 venv） | OK（1 skipped） |
| Python 3.9.6（`/usr/bin/python3`，PyYAML 在使用者 site-packages） | OK（1 skipped） |
| GitHub Actions `llmtool CI`（main） | run `36857184601`（`68d189d`）success |
| 公司 gateway `/v1/models` | HTTP 200，約 0.25 秒 |
| 實際呼叫 gateway | 共 29 次：A0–A3.5 11 次、A4–A6 6 次、A7–A9 12 次，每次 0.3–5.6 秒 |

## 總結

**A0–A9 的核心功能與架構都符合 roadmap。** 確認了以下幾點：

- provider／role／skill／context packet／git／validator 的分層。
- 唯讀契約：沒有任何會執行模型建議指令的路徑。
- 設定的信任模型；strict YAML；錯誤訊息經過整理；exit code 與 stdout／stderr 分離。

沒有 critical 或 high 等級的問題。有 **1 項安全相關的 medium**（M1 kubectl `--server`）建議優先修，另外 6 項 medium 是功能邏輯問題，還有一些 low 與過時的文件。

## 各 milestone 結果

### A0 — Model Validation

| 項目 | 結果 | 證據 |
|------|------|------|
| 驗證腳本存在 | PASS | `scripts/llm-benchmark.sh`、`scripts/llm-reasoning-probe.sh` |
| gateway 是 openai-compatible，可以關閉 thinking | PASS（實測） | `ask` 回傳 `LIVE_OK_1`，completion_tokens=6，0.30 秒 |
| 長文 needle | PASS（抽測） | 201 KB 的檔案，needle 在第 1777 行，正確找到 `ZEBRA-4471`，prompt_tokens=44347，2.6 秒 |
| 64k／119k 完整 benchmark | NOT VERIFIABLE | 只有歷史紀錄，roadmap 也註明沒有重測 |

### A1 — Generic Core

| 項目 | 結果 | 證據 |
|------|------|------|
| 沒有內建 endpoint／model | PASS | 暫存 HOME 下 `config show` 顯示 `base_url: null`；`ask` 回報 "base_url is not configured"，rc=1，沒有連線 |
| 不會自動載入部署用的範例設定 | PASS | 沒有程式碼引用 `config.example*` |
| 優先順序 CLI > env > project > user > default | PASS | `--model` 蓋過 `LLMTOOL_MODEL`，`LLMTOOL_MODEL` 蓋過專案設定 |
| 專案設定是低信任 | PASS | 專案設定 `base_url`、`active_provider`、新的 profile、`api_key_env` 都會 rc=1；不讀上層目錄的 `.llmtool.yaml`，也忽略 `XDG_CONFIG_HOME` |
| strict YAML | PASS | 重複 key、alias、`!!python` tag、帶 `/v1` 或帳密的 URL、timeout 超過 3600 等都會 rc=1；不合法設定裡的 secret 不會出現在輸出 |
| 輸入：positional／`--file`／stdin 各自使用 | PASS（實測） | 目錄、FIFO、不存在的檔案、NUL、非法 UTF-8、空輸入都會 rc=1 |
| 「同時提供多種來源會報錯」（README:479-480） | **FAIL** | 見 M2 |
| stdout 只有最後的文字；`--verbose` 的 metadata 寫到 stderr | PASS | `od -c` 顯示 `LIVE_OK_1\n` |
| exit code 0／1／2／130 | PASS | 不合法的選項 rc=2 且不回顯；SIGINT 回傳 130 並顯示 "llmtool: interrupted" |
| HTTP 錯誤處理、redirect、proxy、length／tool-call | PASS | 500 回報 "response body omitted"；302 不跟隨；`finish_reason=length` 與 `tool_calls` 都 rc=1；`ProxyHandler({})` |
| TTY 跳脫、pipe 保留原始內容 | PASS | 在 pty 中 ESC 顯示為 `\x1b`；pipe 時保留原始位元組 |

### A1.1 — Configure Wizard

| 項目 | 結果 | 證據 |
|------|------|------|
| 互動式建立設定，檔案 0600、目錄 0700、原子寫入 | PASS（透過 pty 實測） | 建立 profile 後，`ask` 實際呼叫 gateway 成功 |
| 隱藏 key 輸入；非 TTY 時拒絕 | PASS | 用 pipe 執行回報 "requires an interactive terminal"，rc=1，沒有寫入（UX：問完 5 題才拒絕） |
| 既有設定預設為 No；EOF 不存檔；保留其他 profile | PASS | |
| 保留既有設定的其他內容 | **FAIL** | 見 M3 |
| `config init`、`--force`、不覆蓋既有檔案 | PASS | |

### A2 — Providers（A2.1–A2.6；A2.3 Anthropic API 尚在規劃中）

| 項目 | 結果 | 證據 |
|------|------|------|
| 靜態 registry、不可變的 capability、離線的 `providers [--json]` | PASS | 專案設定不合法時也能 rc=0，代表不讀設定 |
| openai：固定 origin、Responses API、`store:false`、拒絕 `base_url` | PASS（程式碼與 mock） | 沒有呼叫真的 OpenAI |
| claude-cli：argv、空的暫存 cwd、環境 allowlist、不轉送 stderr | PASS（假的執行檔） | `--print --output-format text --no-session-persistence --safe-mode --tools "" --model sonnet`；`OPENAI_API_KEY`、`NODE_OPTIONS`、`GIT_DIR` 都沒有傳下去 |
| codex-cli：權限 profile、`--cd` 與 cwd 隔離、`TMPDIR` | PASS（假的執行檔） | |
| 真的 CLI flag 是否有效（`--safe-mode`、sandbox） | NOT VERIFIABLE | 沒有呼叫真的 claude／codex |
| 混用多個 profile；`--provider`／env 切換不改寫存檔的 active provider | PASS | |

### A3 — Skill Framework

| 項目 | 結果 | 證據 |
|------|------|------|
| 只在 package 內找 skill、清單排序固定 | PASS | |
| `skill show` 離線；不回顯不合法的名稱 | PASS | |
| strict frontmatter、拒絕 symlink、64 KiB 上限 | PASS（程式碼與測試） | |
| 組合順序 COMMON → task → skill（system），user 訊息不變 | PASS | stub log 顯示 user 就是原始的 diff 文字 |
| provider 裡沒有 skill 邏輯 | PASS | provider 不 import skills／prompts |
| wheel 打包包含 SKILL.md | PARTIAL | `pyproject.toml` 正確，但離線環境無法 build wheel |

### A3.5 — Interactive Chat

| 項目 | 結果 | 證據 |
|------|------|------|
| 所有斜線指令 | PASS（實測） | `/help`、`/status`、`/provider`、`/skill`、`/clear`、`/history`、`/exit`、`/quit`；錯誤只寫到 stderr，對話繼續 |
| 多輪對話記憶 | PASS（實測） | "My name is Mira"，下一輪問名字回答 "Mira" |
| 失敗的那一輪不寫進歷史；切換 provider 時警告 | PASS（stub） | |
| 送出時才讀取憑證；EOF／Ctrl-C exit 0；不寫檔、不執行 | PASS | |

### A4 — Evidence-first Context Packet

| 項目 | 結果 | 證據 |
|------|------|------|
| 不可變的資料模型、6 個分類 | PASS | `context.py:36-79` |
| strict JSON（fence、多餘或缺少欄位、重複 key、NaN、型別） | PASS | rc=1，stdout 為空，錯誤訊息經過整理 |
| 1 MiB 上限、深度 4、非法 Unicode | PASS | |
| 拒絕不安全的控制字元 | PARTIAL | C0／DEL 會拒絕，C1／bidi 會接受（見 L2） |
| 實際執行 `context-pack`（`--json` 與 human 輸出） | PASS（實測） | rc=0，約 5.6 秒 |
| 模型輸出格式錯誤時，在 stdout 之前失敗（含 `--verbose`） | PASS（實測） | 用 prompt injection 要求 Markdown fence：rc=1，stdout 0 bytes |
| 不執行、不擷取；chat 不擷取 packet | PASS | |

### A5 — Git Intelligence

| 項目 | 結果 | 證據 |
|------|------|------|
| 固定的 argv、`shell=False`、環境 allowlist、killpg | PASS | `git_intel.py:23-151` |
| 在惡意 repo 設定下不執行任何東西 | PASS | 設定了 fsmonitor、pager、diff.external、textconv、filter（含 include.path）、hook、gpg.program、credential.helper、sshCommand，marker 目錄仍然是空的。**對照組**：同樣的設定用一般的 git 跑，確實會建立 marker |
| 忽略繼承的 Git 環境變數 | PASS | `GIT_DIR`、`GIT_CONFIG_PARAMETERS`、`GIT_EXTERNAL_DIFF` 等都沒有效果 |
| 無法覆蓋的 filter、promisor 會 fail closed | PASS | |
| 沒有 repo、空 repo、detached、unborn、conflict、子目錄 | PASS | |
| diff 超過 2 MiB、metadata 超過 1 MiB 時整個失敗 | PASS | stdout 0 bytes |
| 非 UTF-8、ESC 被跳脫 | PASS | 磁碟上的非 UTF-8 檔名受 APFS 限制，無法測試 |
| 不進入 submodule；離線 | PASS | |
| 「相關檔案」排除已變更的路徑 | PARTIAL | 見 L3 |
| `llmtool git \| llmtool review-diff` | PASS（實測） | 找到寫死的 secret 與 `shell=True` |

### A6 — Safety & Validator

| 項目 | 結果 | 證據 |
|------|------|------|
| README 與歷史紀錄的範例 | PASS | 51 個指令全部符合，例如 `cat /etc/company/app/config.yaml` 是 SENSITIVE_READ，`git status` 是 UNKNOWN |
| 複合指令取最高風險；重導向、引號、指令替換 | PASS | `ls; rm -rf /` 是 DESTRUCTIVE；`ls $(rm -rf /)` 是 UNKNOWN；`ls -l > out` 是 MUTATING |
| 控制字元與 bidi 視為分隔符號、最低 UNKNOWN；8 KiB 上限 | PASS | |
| SAFE_READ 是一個很窄的類別 | **FAIL（1 例）** | 見 M1 |
| packet 規則（沒有 evidence、碰撞、未知項目、有風險的 verify_next、1024 項上限） | PASS | |
| 實際把 packet 交給 `validate` | PASS（實測） | VALID；文字敘述形式的建議標為 VERIFY_NEXT_UNKNOWN |
| 純資料、沒有執行路徑 | PASS | `requires_approval` 沒有任何呼叫者 |

### A7 — Skill Trainer／Feedback Loop

| 項目 | 結果 | 證據 |
|------|------|------|
| capture → show → validate → review 的流程 | PASS（CLI 實測） | 第二次 review 回報 "must advance exactly one lifecycle step"，rc=1 |
| 由內容決定的 24-hex ID，notes／status 不影響 ID | PASS | 兩次 capture 的輸出 byte-identical |
| 狀態只能往前一步；capture 之後必須有 notes | PASS | |
| proposal 需要 REVIEWED、版本號 +1、只接受 JSON | PASS | |
| 不修改 SKILL.md、不自動核准 | PASS | 前後 SHA-256 相同 |
| strict JSON、拒絕憑證 | PASS | `api_key: sk-…` rc=1 且不回顯 |
| 檔案 0600、不覆蓋、原子寫入、拒絕 symlink／`..`／FIFO | PASS | |
| 文字上限與控制字元 | PARTIAL | 見 M5 |

### A8 — Regression／Benchmark

| 項目 | 結果 | 證據 |
|------|------|------|
| `evaluate`／`suite` 完全離線、輸出固定 | PASS | 兩次 suite 的輸出用 `cmp` 比對相同 |
| QUALITY／EFFICIENCY／RUNTIME／SAFETY 分開、null 比例 | PASS | |
| 格式錯誤的候選回答標為 `assessed:false` | PASS | |
| 禁止 MUTATING／DESTRUCTIVE | PASS | `chmod 777`、`rm -rf` 都 fail |
| preferred evidence 比對 | PARTIAL | 見 M6 |
| manifest 規則 | PASS | 重複、`..`、symlink、33 項都 rc=1，stdout 0 bytes |
| live：先檢查 readiness 再讀憑證、只送一次請求、不執行建議 | PASS（實測 4 次） | 全部 rc=0，5/5 assertion，SAFE_READ=2，約 1.2–1.3 秒，tokens 636／234 |
| roadmap 的「remains FAIL as measured」 | 已釐清 | 指的是過去某一次 Claude CLI 的執行。用 gateway 實測不會重現；Claude 沒有重測 |

### A9 — Agent Orchestration

| 項目 | 結果 | 證據 |
|------|------|------|
| 4 個固定 role、parser 最多 8 步、拒絕重複 role 與未知欄位 | PASS | |
| dry-run 只檢查結構，不讀設定 | PASS | |
| 每一步只拿到原始 task 加上前一步的輸出 | PASS（fake client） | |
| 先檢查 readiness；第一次失敗就停止、不 fallback；失敗時 stdout 為空 | PASS | |
| 輸出 budget（每步 128 KiB、總共 512 KiB、JSON 1 MiB） | PASS | |
| 不執行模型的輸出 | PASS（實測） | task 裡被 injection 塞進 `rm -rf … && touch /tmp/zz_marker_file`，執行後 marker 不存在 |
| 2 個 role 與 4 個 role 的 pipeline | PASS（實測） | rc=0；final_content 等於最後一步的輸出 |
| 尊重每一步明確指定的 profile | PARTIAL | 見 M4 |
| ROADMAP 的 A9 狀態文字 | FAIL | 見文件問題 |

## 問題清單

### Medium

| # | 位置 | 問題與重現 | 建議修正 |
|---|------|-----------|---------|
| **M1** | `validator.py:251-256` | **`kubectl -s/--server <url> get pods` 被判為 SAFE_READ**（Lead 已重現，`--server=` 寫法也一樣）。kubectl 會把目前 context 的 bearer token 或 client 憑證送到指定的 server，這是唯一一種不需要批准、卻會外洩憑證的情況。 | `-s`／`--server`／`--token`／`--kubeconfig`／`--as` 至少判為 UNKNOWN；加上測試。**建議優先修。** |
| M2 | `inputs.py:24-39`、README:479-480 | `cat app.log \| llmtool ask "為什麼失敗"` 會默默丟掉 pipe 的內容，只送出問題，rc=0，模型在沒有證據的情況下回答。README 寫的「同時提供多種來源報錯」與「會等待 pipe 結束」都和現在的行為不符；這是修 stdin 卡住（code review M1）時的取捨。 | 不阻塞地檢查 pipe 裡是否已經有資料，有就報錯；或印出警告並更新 README。 |
| M3 | `configure.py:128-132,149` | 新增 profile 並接受預設值後，既有的 temperature 0.7／max_tokens 4000 會被改成 0.0／2000，active provider 也會被換成剛編輯的 profile。更新既有 profile 時預設值是空白，base_url／model 會變成 null。 | 更新時以既有設定作為預設值；只有在使用者修改時才覆寫 `generation`；加上測試。 |
| M4 | orchestration 與 `config.resolve_config` | 全域的 `LLMTOOL_*` 環境變數與 cwd 的 `.llmtool.yaml` 會套用到每一步的 profile。實測 `LLMTOOL_MODEL=env-model` 讓 4 個 profile 都用 `env-model`。 | orchestration 忽略或拒絕 per-provider 的環境變數覆寫，或在文件中說明。 |
| M5 | `feedback.py:79`（`_text` 使用 `isprintable`） | U+3000 全形空白、NBSP 這類合法的 Unicode 空白被當成控制字元拒絕（Lead 已重現），但 A4 會接受。所以正常的中文 packet 會讓 `benchmark evaluate／live` 與 `feedback capture` 失敗，用 Qwen gateway 時很容易遇到。 | 只拒絕 C0／C1、DEL 與 bidi 字元，沿用 A4 或 terminal 的規則；超過上限的候選回答改為 fail 的結果，而不是錯誤。 |
| M6 | A8 `preferred_verify_next_any` | 一方面太窄：只接受完全相同的字串，`ls -la …`（SAFE_READ）或多一個空白都算 fail，而 roadmap 允許其他窄範圍的證據。另一方面太鬆：題目直接告訴模型要用哪些指令，通過只代表模型照著做。 | 對目標路徑接受 SAFE_READ 的 metadata 指令；另外保留一個不給提示的版本。 |
| M7 | analyze-log skill（prompt 契約） | 實測時，模型建議了 `kubectl get secrets … -o yaml \| grep …`，會顯示 Secret 的內容，而且在沒有證據的情況下假設是 Kubernetes，違反 skill 自己「不建議顯示憑證」的規則。這只靠 prompt 約束，沒有任何驗證。 | 在 skill 中明確禁止 `get secret(s)`／`-o yaml` 與顯示憑證檔；可選擇對 NEXT_COMMANDS 套用 A6 並標註；加上 regression case。 |

### Low

- **L1 validator 低估了一些指令的風險**（都需要批准）：
  - `git -c diff.external=… diff`／`git -c core.fsmonitor=… diff` 被判為 SENSITIVE_READ，理由沒有說明它會執行程式。
  - 判為 UNKNOWN：`env rm -rf /`、`FOO=1 rm`、`xargs rm`、`/usr/local/bin/rm`、`git rm -r`、`kubectl --kubeconfig x delete`、`kubectl --namespace=a --context=b delete`。
  - 判為 MUTATING：`git push --delete`、`git push --mirror`、`git checkout .`（但 `git restore .` 已經是 DESTRUCTIVE）。
- **L2 context packet 接受 C1／bidi 字元**（`context.py:31`）。`context-pack --json` 用 `ensure_ascii=False`，而且不經過 `human_output`，會原樣輸出到終端機。這是先前 code review 的 L2，還沒修。
- **L3 git 的「相關檔案」包含 rename 前的原路徑**（`git_intel.py:333`），和「排除已變更的路徑」的規則矛盾。
- **L4 orchestration 不會先檢查後面步驟的 profile**，前面步驟已經付費的呼叫結果會被丟掉。
- **L5 live benchmark 失敗時不保留模型輸出**，無法事後診斷；建議提供明確 opt-in 的 `--save-output`。
- **L6 claude-cli／codex-cli 把訊息壓成 `ROLE:` 純文字**（`claude_cli.py:58`、`codex_cli.py:66`），提供的 log／diff 可以偽造 `SYSTEM:` 段落；建議使用無法偽造的分隔方式。
- **L7 skills 目錄裡有雜檔（例如 `.DS_Store`）時，所有 skill 都會失效**（`skills.py:83`；code review 的 L11，還沒修）。
- **L8 架構**：benchmark 與 orchestration 會 import A7 的私有 helper（`_parse`、`_schema`、`_items`、`_text`、`_json`）與 `config._provider_name`，orchestration 也依賴 benchmark 模組的 runtime 正規化；建議抽成共用模組。

### Nit

- 錯誤訊息：orchestration／benchmark 的錯誤說的是 "feedback JSON…"；`validate context --file` 遇到非 UTF-8 或不存在的檔案時，回報 "local I/O failed"。
- `feedback show` 原樣輸出 `\r`。
- 只設定 `api_key_env` 時，`config show` 也顯示 `api_key: '***REDACTED***'`。
- 在 `.git/` 目錄內執行 `llmtool git` 會出現籠統的錯誤。
- `configure` 問完 5 題才拒絕非 TTY；`skill <name>` 不是 `skill show <name>` 的別名。

### 過時的文件

- `ROADMAP.md:375` 寫「A9 NOT STARTED」，`ROADMAP.md:403` 寫「NOT merged」，但標題是 DONE and merged。
- `README.md:752` 寫「A9 remains a future milestone」。
- `README.md:499` 寫「沒有 command validator」，但 A6 已經存在。
- `README.md:569` 寫「只有 config 會寫檔」，但 A7 的 `feedback capture` 會寫 case 檔。
- ROADMAP 的 A2.6、A3.5 與 A4 仍然寫著 openai-compatible／chat／live inference NOT-TESTED，這次都已經實測 PASS。
- README 把 `kubectl get pods` 列為 SAFE_READ，但沒有說明它的前提是 kubeconfig 可信（kubeconfig 的 exec plugin 可以執行指令，和 `git status` 改成 UNKNOWN 的理由相同）。

## 無法驗證

- 真的 Claude CLI／Codex CLI 的 flag 語意與 sandbox 強度（會用到個人帳號）。
- OpenAI 實際的 API。
- wheel 打包（離線環境）。
- A0 64k–119k 規模的長文測試（只抽測了 44k）。
- 實際的 10 秒 git timeout 與 stderr 64 KiB 上限（只有 unit test）。
- 實測時模型是否會把破壞性指令放進 VERIFY_NEXT（這次 skill 的 prompt 抑制住了），以及 packet 的語意品質（例如把「未知」放進 facts、編造 locator）；結構 validator 依設計不檢查這些。

## 建議

1. 優先修 **M1**（kubectl `--server` 外洩憑證）。
2. 接著處理 M2–M7。
3. 更新過時的 ROADMAP／README 狀態。
4. Low 項目記為 follow-up。
