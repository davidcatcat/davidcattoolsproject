# llmtool — A1 LLMTool MVP

本機唯讀 CLI，將明確提供的文字交給公司 Qwen gateway，輸出最終答案。
不連接 OpenAI 服務，不呼叫 Claude/Codex CLI，不執行 shell、Git 或模型回傳的指令。
`openai-compatible` 只是 gateway 的 HTTP 協定名稱。

## 安裝與直接執行

需要 Python 3.9+，支援 macOS/Linux。從 repo 根目錄：

```sh
python3 -m venv tools/llmtool/.venv
tools/llmtool/.venv/bin/python -m pip install -e tools/llmtool
tools/llmtool/.venv/bin/llmtool --help

# 啟用環境後可直接使用 llmtool
. tools/llmtool/.venv/bin/activate
llmtool --version
```

也能在安裝相依套件的 Python 環境直接執行：

```sh
python tools/llmtool/llmtool.py --help
# 或在 tools/llmtool 內執行 python -m llmtool
```

唯一執行期 dependency：`PyYAML>=6.0,<7`，使用 SafeLoader 解析 YAML，
避免自行實作不完整的 YAML parser。HTTP、CLI 與測試使用 Python 標準函式庫。
setuptools 僅用於打包。離線環境需事先備妥套件 wheel／內部套件來源。

## 設定

```sh
llmtool config path                 # ~/.config/llmtool/config.yaml
llmtool config init                 # 新檔權限 0600，既有檔案不覆蓋
llmtool config init --force         # 明確要求才取代既有檔案；拒絕 symlink
llmtool config show                 # resolved YAML，API key 遮罩
llmtool config show --max-tokens 4000
```

User config 固定是 `~/.config/llmtool/config.yaml`（依 HOME；不使用 XDG_CONFIG_HOME）。
Project config 為**目前工作目錄**的 `.llmtool.yaml`；不搜尋父目錄、不讀 Git metadata。
兩者皆非必需。欄位逐層合併，優先順序：

```text
CLI option > environment > project config > user config > built-in defaults
```

範例見 [config.example.yaml](config.example.yaml)：

```yaml
providers:
  company:
    type: openai-compatible
    base_url: "http://10.29.7.58:4000"
    model: "gw"
    timeout_seconds: 120
    # api_key: null
    # api_key_env: "COMPANY_LLM_API_KEY"
generation:
  temperature: 0
  max_tokens: 2000
  enable_thinking: false
```

Project config 可以只寫：

```yaml
providers:
  company:
    timeout_seconds: 180
generation:
  max_tokens: 4000
```

| CLI（放在子命令之後） | Environment | YAML |
|---|---|---|
| `--base-url` | `LLMTOOL_BASE_URL` | `providers.company.base_url` |
| `--model` | `LLMTOOL_MODEL` | `providers.company.model` |
| `--timeout` | `LLMTOOL_TIMEOUT` | `providers.company.timeout_seconds` |
| `--max-tokens` | `LLMTOOL_MAX_TOKENS` | `generation.max_tokens` |
| `--temperature` | — | `generation.temperature` |

`base_url` 只接受 HTTP(S) origin，例如 `http://host:4000`，不包含 `/v1`、
credentials、query 或 fragment。工具固定附加 `/v1/chat/completions`。
`timeout_seconds` 為正數 socket timeout；`max_tokens` 為正整數；temperature 為 0–2。
未知欄位、重複 key、YAML aliases、錯誤型別與 malformed YAML 均報錯。

公司 gateway 預設不需 auth。未設定 key 時不送 Authorization；需要時可擇一設定
`api_key` 或 `api_key_env`。後者只查明確命名的環境變數，缺值時請求失敗。
不讀 `.env`、credential store、keychain 或 SSH 檔案。不要把 key 寫進命令列。
合併後兩個 auth 欄位不能同時有值；覆蓋 auth 方式時將另一個明確設為 `null`。

`.gitignore` 排除 `.llmtool.yaml`、`config.yaml`、`config.yml`；repo 只追蹤無 secret
的 example。設定檔屬於信任邊界：project config 可改目的地或 key env 名稱，
不要在未信任的 project config 下傳送敏感輸入／key。

## 四個命令

```sh
llmtool ask "why did this fail?"
llmtool ask "解釋這個錯誤" --max-tokens 3000 --timeout 180

llmtool analyze-log --file app.log
cat app.log | llmtool analyze-log

git diff | llmtool review-diff > review.txt
llmtool review-diff --file change.diff

cat incident.txt | llmtool context-pack > context.txt
llmtool context-pack --file incident.txt --verbose
```

`git diff` / `cat` 是使用者在 shell 明確執行的上游程式；llmtool 只讀 pipe。
每次只接受一種來源：一個 positional argument、`--file` 或非空 stdin。
同時提供多種來源報錯；空 pipe 不與明確輸入衝突。若 pipe 尚未 EOF，會等待輸入結束。
互動終端不主動提示輸入；要用 stdin 可貼入 pipe 或重新導向。
輸入需為非空 UTF-8 文字，拒絕 NUL／二進位控制字元、非法 UTF-8、目錄與 FIFO 檔案。
接受一般 log 的 tab、換行、form feed 與 ANSI escape；大小上限 16 MiB。
`--file` 只讀你指定的檔案（可跟隨明確指定的 symlink），不遞迴、不自動補上下文。

| 命令 | Prompt 要求 |
|---|---|
| `ask` | Final answer only，明確區分證據與假設 |
| `analyze-log` | `ROOT_CAUSE`、`EVIDENCE`、`DISTRACTORS`、`UNKNOWN_OR_UNPROVEN`、`NEXT_COMMANDS`；最多 3 個唯讀建議指令 |
| `review-diff` | 只列 diff 支持的 findings：severity、file/snippet、failure/security mode、minimal fix |
| `context-pack` | `OBSERVATIONS`、`HYPOTHESES`、`UNKNOWN`、`NEXT_EVIDENCE_TO_COLLECT`；保留明確事實、移除重複雜訊 |

這些是 prompt 約束，A1 不驗證 section 結構、指令數量或模型推論的正確性；
不提供 A3 的 structured JSON Context Packet。

## No-thinking 與輸出契約

每次 request 固定含：

```json
"chat_template_kwargs": {"enable_thinking": false}
```

A1 拒絕將 `enable_thinking` 改為 true。Route 預設 `gw`；目前已知 backend
是 `qwen3.6-35b-a3b`，程式不將 backend 名稱寫死為路由。

模型命令成功時 stdout **只有** `choices[0].message.content.strip()` 加一個換行。
`message.reasoning` / `reasoning_content` 永遠不輸出。
預設成功時 stderr 為空；`--verbose` 額外印安全的 model、token usage 與 elapsed metadata。
Help/version/config show/path 是管理命令，stdout 分別是說明／版本／設定／路徑；
config init 的完成訊息在 stderr。

結果會完整驗證後才寫 stdout；任何 provider 錯誤不輸出部分答案。

| Exit code | 意義 |
|---|---|
| `0` | 成功 |
| `1` | 設定、輸入、HTTP、timeout、解析或輸出錯誤 |
| `2` | 無效 CLI 語法 |
| `130` | 使用者中斷 |

HTTP 非 2xx、API error body、malformed JSON、missing choices、空 content、
`finish_reason=length`、非 `stop` finish reason、tool-call response 都失敗。
錯誤只給分類／HTTP status，不印 raw response、輸入、URL credentials 或 exception body。
不自動 retry、不跟隨 HTTP redirect，不讀環境 proxy 設定，避免意外轉送內網資料。

## Troubleshooting 與已知限制

- `PyYAML is required`：確認啟用安裝 llmtool 的 virtualenv。
- `cannot connect to gateway`：檢查 VPN／內網連線和 `config show` 的 base URL。
  A1 不支援 proxy，HTTPS 使用系統 Python 的信任憑證，不略過 TLS 驗證。
- `HTTP 401/403`：gateway 若開始要求認證，設定明確的 `api_key_env`。
- `timed out`：以 `--timeout 180` 調整。這是 socket 操作 timeout，**不是整體 wall-clock deadline**。
- `finish_reason=length`：提高 `--max-tokens` 或縮小問題；截斷內容不會落到輸出檔。
- `malformed YAML`：檢查縮排、型別與重複 key；不會在錯誤訊息中回顯 YAML 原文。
- `only one ...`：不要同時 pipe 並給 `--file`／文字 argument。
- `config already exists`：保留現有設定，或明確以 `--force` 重建。

沒有 tokenizer／context window 自動切分；gateway 決定可接受的 token 數。
HTTP response 上限 16 MiB、config 上限 64 KiB。非串流模式會在記憶體保留完整輸入與答案。
A1 信任 gateway 將 reasoning 放在獨立欄位；不會修改／剝除 content 內的文字或標籤。
模型可能忽略 prompt 或產生錯誤建議，請 review 輸出；工具不會執行它。

## Safety boundary

模型命令唯讀本機資料並向設定的 gateway 發出 HTTP request；不執行任何程序、shell
或 Git 操作，不修改 production。唯一內建檔案寫入是明確的 `config init`。
不自動讀取 SSH、keychain 或任意 secret file；明確提供的文字、pipe、`--file`
內容會完整傳給 gateway，請自行移除不應傳送的秘密。
工具不寫 prompt／response log、cache 或 benchmark report。

## 開發與驗證

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

`check` 跑 compileall syntax check 與 unittest。測試使用暫存 HOME/project、mock
transport 和 `127.0.0.1` ephemeral port 的假 gateway；不使用真實 user config、
credentials，也不連公司 gateway。CLI 整合測試包含 `--help`、config init 防覆寫、
實際 HTTP payload、輸入來源、timeout、redirect、錯誤及 stdout/stderr 分離。

```text
llmtool/cli.py                         CLI 與輸出
llmtool/config.py                      YAML 合併、驗證、遮罩、init
llmtool/inputs.py                      明確輸入來源、文字驗證
llmtool/prompts.py                     四個內建模板
llmtool/providers/__init__.py          provider factory
llmtool/providers/openai_compatible.py HTTP transport 與 response 驗證
tests/                                config/provider/CLI 測試
```

A2 可擴充模板來源而不改 transport；A8 可增加獨立 provider。A1 不實作 Skill engine、
orchestration、retrieval、Git intelligence、validator 或外部模型整合。
