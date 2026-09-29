# llmtool — Generic LLMTool Core

llmtool is a read-only, provider-agnostic LLM CLI foundation.

這是可攜的通用 LLM CLI，也是未來 orchestration 的底座。目前 production providers
為 `openai` 與 `openai-compatible`。後者可連 self-hosted Qwen、vLLM、相容 gateway、
LM Studio／Ollama 的相容端點，以及其他符合此 request/response 契約的伺服器。
不同伺服器的參數支援仍需確認；不要求它們都支援 Qwen 的 thinking 擴充。

llmtool has no built-in model or deployment endpoint defaults. `openai` uses its fixed official
origin; users configure a model and explicit credentials before the first request.

通用 template 的 `base_url`、`model` 都是 `null`，不會自動連任何部署。
`openai-compatible` 使用者須設定 endpoint 與 model；`openai` endpoint 固定，使用者設定
model 與明確 credentials。公司 Qwen 設定僅保留為獨立 deployment example，不會自動載入。

| 概念 | 意義 | 狀態 |
|---|---|---|
| Provider | 如何連模型或 runtime：transport、endpoint、model、auth | `openai`、`openai-compatible` |
| Profile | 一組具名 provider 設定，例如 `office-qwen`、`home-vllm` | 支援多組及 active selection |
| Role | workflow 職責，例如 grinder、validator、coder、reviewer | 未實作；未來與 provider 分離 |
| Agent | 可使用工具或修改環境的執行實體 | 未實作；llmtool A1/A1.1 不是執行 agent |

目前尚未支援 Anthropic provider、Claude CLI provider、Codex CLI provider。llmtool 不執行
shell、Git 或模型回傳的指令。見 [Roadmap](ROADMAP.md)。

## Roadmap status

- **A0 — Model Validation:** DONE
- **A1 — Generic LLMTool Core:** DONE and merged to `main`
- **A1.1 — Configure Wizard + Live Smoke:** DONE and merged to `main`
- **A2.1 — Provider Capability Foundation:** DONE
- **A2.2 — OpenAI API Provider:** CURRENT; Responses API, pure text
- **A2.3–A2.6:** planned; Anthropic API and Claude/Codex CLI providers are not implemented

A1.1 code review and live smoke test are complete. It delivers interactive `llmtool configure`,
automatic user config path handling, hidden API
key input, `api_key`／`api_key_env` support, saving incomplete settings as null, atomic writes,
0600 permissions, existing-config protection, profile preservation, and no automatic provider
request during configuration. The live gateway result is recorded in the
[A1.1 smoke test report](../../reports/llmtool_a1_1_live_smoke_20260929.md).

## Provider capabilities

`llmtool providers` lists the capabilities declared by each production provider implementation;
`llmtool providers --json` prints the same metadata as deterministic JSON. Both commands are
offline introspection: they do not read user/project config or credentials, create a config file,
or connect to a provider.

Capabilities describe technical implementation/runtime ability. **Capability is not authorization,
policy, or approval.** In particular, `code_edit`, `shell`, or `git` metadata does not grant
permission or cause llmtool to modify files, execute commands, or operate Git. Capability metadata
is implementation-owned and is not stored in user/project config.

Both production providers declare `chat: yes`, `large_context: yes`, `reasoning_control: yes`,
`streaming: no`, `code_edit: no`, `shell: no`, and `git: no`.
`large_context` describes support for large-context workflows, not a fixed context window;
deployment/model limits vary. `reasoning_control` describes this implementation's optional
`provider_options.enable_thinking` extension, sent as `chat_template_kwargs.enable_thinking`.
That field is not a universal OpenAI-compatible standard, and endpoint support varies.
The dedicated `openai` provider instead maps `provider_options.reasoning_effort` to Responses API
`reasoning.effort`; supported values can vary by model.

## OpenAI API provider

The `openai` provider uses the fixed official HTTPS origin `https://api.openai.com` and sends
plain-text conversations to `POST /v1/responses`. It sends `max_output_tokens`, never sends
`temperature`, and does not enable streaming, tools, code editing, shell, or Git. Optional
`reasoning_effort` values are `none`, `low`, `medium`, and `high`; model support varies. The
provider returns only final assistant text and safe response metadata, never reasoning items.

Example user config (set the environment variable yourself; llmtool does not discover credentials):

```yaml
providers:
  openai-main:
    type: openai
    model: "your-model"
    api_key_env: OPENAI_API_KEY
    provider_options:
      reasoning_effort: medium
active_provider: openai-main
generation:
  max_tokens: 2000
```

Do not set `base_url` for `type: openai`; alternate endpoints are rejected. OpenAI API live usage
requires separately configured OpenAI API credentials and billing. ChatGPT subscriptions and API
billing are separate; tests do not require live usage, billing setup, or credits.

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
llmtool configure                  # 互動式設定精靈，不需要手動找 config path
llmtool config path                 # ~/.config/llmtool/config.yaml
llmtool config init                 # 新檔權限 0600，既有檔案不覆蓋
llmtool config init --force         # 明確要求才取代既有檔案；拒絕 symlink
llmtool config show                 # resolved YAML，API key 遮罩
llmtool config show --max-tokens 4000
llmtool config show --provider home-vllm  # 先在 user config 建立該 profile
```

`configure` 會依 provider 詢問設定；OpenAI 使用官方 endpoint，不詢問 base URL，驗證後寫入
user config，不會連線測試 provider。API key 使用不回顯的終端輸入；也可只填 API key
環境變數名稱，兩者不可同時設定。留空的 optional 欄位會保持 unset；provider-specific
reasoning 設定留空時不會送出該參數。OpenAI API key env 預設提示 `OPENAI_API_KEY`，只是
config 欄位建議值，不會在 configure 時讀取環境變數。新檔自動建立目錄並以 `0600` 權限原子寫入。
若設定檔已存在，精靈先詢問是否更新，預設為 No；確認更新會保留其他 provider profiles。
精靈需要互動終端，確保 API key 不會退回可回顯輸入。

`config init` 仍只產生 generic template。`openai-compatible` 需設定 `base_url` 與 `model`；
`openai` 使用固定官方 endpoint，不接受自訂 `base_url`，需設定 model 和明確的 API key 或
key 環境變數名稱，再執行 `ask` 等模型命令。
`config show` 可以顯示尚未填妥的 null 設定；模型命令則在讀取 credential／發出 HTTP
之前失敗，訊息指出所選 provider 缺少必要的 endpoint、model 或 authentication 設定。
未選用的 profile 可保持 null，不影響已完整設定的 active provider。

User config 固定是 `~/.config/llmtool/config.yaml`（依 HOME；不使用 XDG_CONFIG_HOME）。
Project config 為**目前工作目錄**的 `.llmtool.yaml`；不搜尋父目錄、不讀 Git metadata。
兩者皆非必需。各來源只對允許的欄位套用優先順序：

```text
CLI option > environment > project config > user config > built-in defaults
```

Generic **user config template** 見 [config.example.yaml](config.example.yaml)，
內容與 built-in defaults 一致：

```yaml
providers:
  default:
    type: openai-compatible
    base_url: null
    model: null
    timeout_seconds: 120
    api_key: null
    api_key_env: null
    provider_options: {}
generation:
  temperature: 0
  max_tokens: 2000
active_provider: default
```

### 手動配置的 deployment examples

公司部署範例見 [config.example.company-qwen.yaml](config.example.company-qwen.yaml)。
只有確認要使用該 endpoint 時，才手動把相關欄位填入 user config；程式不會搜尋或讀取
這個範例檔。以下 IP／model 也只是範例，並非 generic 預設值：

```yaml
providers:
  office-qwen:
    type: openai-compatible
    base_url: "http://10.29.7.58:4000"
    model: "gw"
    provider_options:
      enable_thinking: false
  home-vllm:
    type: openai-compatible
    base_url: "http://192.168.1.20:8000"
    model: "qwen"
active_provider: office-qwen
```

新 profile 在發出模型請求前必須定義 endpoint、model；其他欄位使用中立預設（type 為
`openai-compatible`、timeout 120、無 auth、空 provider_options），**不繼承**
`default` 的 endpoint、credentials 或 thinking 選項。Provider 名稱長 1–64 字元，
以英數字開頭，可包含英數字、`-`、`_`。選擇不存在的 profile 會報錯。

`openai` profile 不使用 `base_url`，並在模型請求時要求 model 和明確設定的 API key 或
`api_key_env`。`OPENAI_API_KEY` 只會在使用者將它指定為 `api_key_env` 後讀取。

### Project config 的低信任邊界

`.llmtool.yaml` **只能**修改已由 user/default 定義的 provider 的 `model`、
`timeout_seconds`，以及 `generation.temperature`、`generation.max_tokens`。
不能新增 provider、切換 `active_provider`、指定 type、base_url、api_key、api_key_env
或 provider_options。禁止欄位即使值為 null、等於目前值、或被 CLI/env 覆蓋，也直接報錯。
完整 project schema 在讀取 credential 環境變數／建立 HTTP client 之前檢查，
錯誤不回顯 secret、原始值或攻擊者指定的欄位名稱。

合法 project config：

```yaml
providers:
  default:
    timeout_seconds: 180
generation:
  max_tokens: 4000
```

不要把完整 `config.example.yaml` 複製為 project config，它包含被禁止的欄位。

| Source | 可控制欄位 |
|---|---|
| User config | Named providers、provider-allowed endpoint、auth、type、provider_options、active_provider、generation |
| Project config | 既有 profile 的 model/timeout，以及通用 temperature/max_tokens |
| Environment | 下表列出的明確變數，套用到選定 profile |
| CLI | 下表列出的明確 flags，套用到選定 profile |

| CLI（放在子命令之後） | Environment | YAML |
|---|---|---|
| `--provider` | `LLMTOOL_PROVIDER` | `active_provider` |
| `--base-url` | `LLMTOOL_BASE_URL` | `providers.<selected>.base_url` |
| `--model` | `LLMTOOL_MODEL` | `providers.<selected>.model` |
| `--timeout` | `LLMTOOL_TIMEOUT` | `providers.<selected>.timeout_seconds` |
| `--max-tokens` | `LLMTOOL_MAX_TOKENS` | `generation.max_tokens` |
| `--temperature` | — | `generation.temperature` |

`openai-compatible` 的 `base_url` 只接受 HTTP(S) origin，例如 `http://host:4000`，不包含
`/v1`、credentials、query 或 fragment；工具固定附加 `/v1/chat/completions`。`openai` 不接受
user/project/CLI/env 提供的 `base_url`，固定使用官方 HTTPS origin 與 `/v1/responses`。
`timeout_seconds` 為正數 socket timeout；`max_tokens` 為正整數；temperature 為 0–2。
未知欄位、重複 key、YAML aliases、錯誤型別與 malformed YAML 均報錯。

先以 CLI > env > user > default 決定 active profile，再把 CLI/env 的 model 等允許覆寫套到
這一組；不更動其他 profile。`openai` 會拒絕 endpoint override。尚未接受 roles／command／
agent routing 設定。

預設無 auth。未設定 key 時不送 Authorization；需要時可擇一設定
`api_key` 或 `api_key_env`。後者只查明確命名的環境變數，缺值時請求失敗。
不讀 `.env`、credential store、keychain 或 SSH 檔案。不要把 key 寫進命令列。
合併後兩個 auth 欄位不能同時有值；覆蓋 auth 方式時將另一個明確設為 `null`。

`config show` 遮罩**所有** profile 的 key，並不解析 api_key_env 的值；模型請求
只解析選定 profile 明確指定的 key env。未選定 profile 的環境 secret 不會讀取。
User config、環境變數與明確 CLI 是信任來源：`--base-url`／`LLMTOOL_BASE_URL`
會將選定 profile 的 key 傳到使用者指定的目的地，請只使用信任的 endpoint。
Project 雖不能改 endpoint/auth，仍可在同一 endpoint 選 model；不適合當作 model ACL。

`.gitignore` 保留 `.llmtool.yaml`、Python cache，其他 artifact 僅忽略
`tools/llmtool/` 的 `.venv/`、`build/`、`dist/`、`*.egg-info/`。
不再全 repo 忽略 `config.yaml`／`config.yml`；真實 user config 位於 repo 外，
不要將其複製進 repo。Repo 只追蹤無 secret 的 example。

### 從最初 A1 config 遷移

舊 `providers.company` 現在只是一般 profile 名稱，不再隱含選用。
可改名為 `default`，或明確設定 `active_provider: company`。
把 `generation.enable_thinking` 移到對應 profile 的
`provider_options.enable_thinking`；舊的通用欄位會報錯，不默默忽略。
將 project config 的 endpoint、auth、type 和 active selection 移至 user config。
不會自動改寫既有設定檔。
先前若依賴內建 endpoint/model 而沒有寫入 user config，現在需明確填入兩者；
`config init --force` 也只會重建 null template，不會恢復任何部署預設值。

## 四個命令

```sh
llmtool ask "why did this fail?"
llmtool ask "解釋這個錯誤" --max-tokens 3000 --timeout 180
llmtool ask "why did this fail?" --provider home-vllm

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
不提供 A4 的 structured JSON Context Packet。

Live smoke test 曾發現 `analyze-log` 建議 `cat /etc/company/app/config.yaml`；該命令沒有
執行。**READ-ONLY != SAFE-TO-DISPLAY**：唯讀命令仍可能輸出 credentials 或 private
configuration。這個案例已列入 Roadmap A3 skills 安全要求、A6 command safety validator
及 A8 regression；A1 目前不會自動執行建議命令，也沒有 command validator。

## OpenAI-compatible no-thinking 與共用輸出契約

`enable_thinking` 是 OpenAI-compatible 實作的**可選 provider-specific 行為**，
不是 generic generation 的共同能力。Generic template 的 `provider_options` 是空的。
公司 Qwen deployment example 明確設定 `provider_options.enable_thinking: false`，
手動採用該設定後，其 request 仍包含已驗證有效的：

```json
"chat_template_kwargs": {"enable_thinking": false}
```

一般相容伺服器若不支援此擴充，在 user profile 設 `provider_options: {}`，
或 `enable_thinking: null`，即可完全省略 `chat_template_kwargs`。
新 profile 預設省略。此選項只接受 boolean 或 null；true 會要求相應伺服器啟用 thinking，
但 reasoning 欄位仍不輸出，也可能更快用完 completion budget。
Core／CLI 不生成此 wire 欄位，未來 provider 不必實作它。

模型命令成功時 stdout **只有** normalized final assistant text 加一個換行。
`openai-compatible` 使用 `choices[0].message.content`；`openai` 只取 Responses API
assistant `output_text`/text item。兩者都不輸出 reasoning、raw response 或 tool payload。
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

HTTP 非 2xx、API error body、malformed JSON、missing/empty final text、Responses 未完成、
`openai-compatible` 的 `finish_reason=length` 或非 `stop`、以及不支援的 tool output 都失敗。
錯誤只給分類／HTTP status，不印 raw response、輸入、URL credentials 或 exception body。
不自動 retry、不跟隨 HTTP redirect，不讀環境 proxy 設定，避免意外轉送內網資料。

## Troubleshooting 與已知限制

- `PyYAML is required`：確認啟用安裝 llmtool 的 virtualenv。
- `provider ... is not configured`：依 active provider 設定 model、相容 provider 的 endpoint，
  以及明確的 API key 或 `api_key_env`；`config init` 本身不完成部署設定。
- `cannot connect`：檢查設定中的 provider destination 和網路。OpenAI 固定使用官方 HTTPS
  origin；compatible provider 使用自訂 origin。HTTP transport 不採用 ambient proxy。
- `HTTP 401/403`：確認明確設定的 API key 或 `api_key_env`；OpenAI API billing 與 ChatGPT
  subscription 分開。
- `timed out`：以 `--timeout 180` 調整。這是 socket 操作 timeout，**不是整體 wall-clock deadline**。
- `finish_reason=length` / incomplete Responses：提高 `--max-tokens` 或縮小問題；截斷內容不會輸出。
- `malformed YAML`：檢查縮排、型別與重複 key；不會在錯誤訊息中回顯 YAML 原文。
- `only one ...`：不要同時 pipe 並給 `--file`／文字 argument。
- `config already exists`：保留現有設定，或明確以 `--force` 重建。
- `project ... forbidden or unsupported field`：將 endpoint/auth/selection 等設定移到 user config。
- `active provider is not defined`：先在 user config 建立該 profile；project 不能建立它。

沒有 tokenizer／context window 自動切分；所選 model/service 決定可接受的 token 數。
HTTP response 上限 16 MiB、config 上限 64 KiB。非串流模式會在記憶體保留完整輸入與答案。
A1/A2.1 信任相容 endpoint 將 reasoning 放在獨立欄位；不會修改／剝除 content 內的文字或標籤。
模型可能忽略 prompt 或產生錯誤建議，請 review 輸出；工具不會執行它。

## Safety boundary

模型命令唯讀本機資料並向設定的 provider 發出 HTTPS/HTTP request；不執行任何程序、shell
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
credentials，也不連公司 gateway 或 OpenAI API。OpenAI provider tests 將 official origin
替換為 loopback mock HTTP server，驗證 Responses request/response、timeout、redirect 與
sanitized errors；沒有 live API call。CLI tests 亦涵蓋輸入、config、redirect 與輸出分離。

```text
llmtool/cli.py                         CLI 與輸出
llmtool/config.py                      YAML 合併、驗證、遮罩、init
llmtool/inputs.py                      明確輸入來源、文字驗證
llmtool/prompts.py                     四個內建模板
llmtool/providers/base.py              capability model 與 Provider.complete() / Reply contract
llmtool/providers/__init__.py          static provider registry、capability lookup 與 factory
llmtool/providers/openai_compatible.py HTTP transport 與 response 驗證
llmtool/providers/openai.py            固定官方 Responses API transport 與 normalization
tests/                                config/provider/CLI 測試
```

執行路徑為 task → prompt template → selected provider → `complete()` → final output。
Provider implementations 透過靜態 registry 宣告 type、capabilities、option/readiness
validation 與 `complete()`；A2.2 之後擴充 provider，A3 建立外部 Skill Framework。其餘
階段及 provider/role 分離方向見 [Roadmap](ROADMAP.md)。
