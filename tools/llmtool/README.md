# llmtool — Generic LLMTool Core

llmtool is a read-only, provider-agnostic LLM CLI foundation.

這是可攜的通用 LLM CLI，也是未來 orchestration 的底座。目前 production providers
為 `claude-cli`、`codex-cli`、`openai` 與 `openai-compatible`。`claude-cli` 使用本機 Claude CLI runtime，
不是 Anthropic HTTP API provider。`openai-compatible` 可連 self-hosted Qwen、vLLM、相容 gateway、
LM Studio／Ollama 的相容端點，以及其他符合此 request/response 契約的伺服器。
不同伺服器的參數支援仍需確認；不要求它們都支援 Qwen 的 thinking 擴充。

llmtool has no built-in model or deployment endpoint defaults. `openai` uses its fixed official
origin and requires a configured model and explicit API credentials. `claude-cli` requires the
user's local Claude CLI to be installed and authenticated; llmtool does not inspect or manage its
authentication.

通用 template 的 `base_url`、`model` 都是 `null`，不會自動連任何部署。
`openai-compatible` 使用者須設定 endpoint 與 model；`openai` endpoint 固定，使用者設定
model 與明確 API credentials；`claude-cli` 使用已安裝 CLI 的預設 model，或選擇性指定 model。
公司 Qwen 設定僅保留為獨立 deployment example，不會自動載入。

| 概念 | 意義 | 狀態 |
|---|---|---|
| Provider | 如何連模型或 runtime：transport、endpoint、model、auth | `claude-cli`、`codex-cli`、`openai`、`openai-compatible` |
| Profile | 一組具名 provider 設定，例如 `office-qwen`、`home-vllm` | 支援多組及 active selection |
| Skill | 可版本化的 declarative instructions/context | A3 DONE and merged to main |
| Role | workflow 職責，例如 grinder、validator、coder、reviewer | 未實作；未來與 provider 分離 |
| Agent | 可使用工具或修改環境的執行實體 | 未實作；llmtool A1/A1.1 不是執行 agent |

目前尚未支援 Anthropic API provider。Claude CLI integration 只作
純文字 runtime request，不使用 CLI tools，不提供 shell、Git 或 code-edit capability。
llmtool 不執行模型回傳的指令。見 [Roadmap](ROADMAP.md)。

## Roadmap status

- **A0 — Model Validation:** DONE
- **A1 — Generic LLMTool Core:** DONE and merged to `main`
- **A1.1 — Configure Wizard + Live Smoke:** DONE and merged to `main`
- **A2.1 — Provider Capability Foundation:** DONE
- **A2.2 — OpenAI API Provider:** DONE; Responses API, pure text; live API NOT-TESTED
- **A2.3 — Anthropic API Provider:** planned
- **A2.4 — Claude CLI Provider:** DONE; local CLI, pure text; live E2E validation PASS
- **A2.5 — Codex CLI Provider:** DONE and merged to `main`; external host validation and direct/live E2E PASS
- **A2.6 — Provider Integration / Smoke Tests:** DONE and merged to `main`; host canonical validation 189/189 PASS
- **A3 — Skill Framework:** DONE and merged to `main` (`c9159f2`); external review PASS
- **A3.5 — Interactive Chat Shell:** DONE and merged to `main` (`021456b`); external review PASS
- **A4 — Evidence-first Context Packet:** DONE and merged to `main` (`38b1a67`); external review PASS
- **A5 — Git Intelligence:** DONE and merged to `main` (`7de6a99`); external review PASS
- **A6 — Safety & Validator Layer:** implemented and validated on feature branch; external review pending, not merged

A1.1 code review and live smoke test are complete. It delivers interactive `llmtool configure`,
automatic user config path handling, hidden API
key input, `api_key`／`api_key_env` support, saving incomplete settings as null, atomic writes,
0600 permissions, existing-config protection, profile preservation, and no automatic provider
request during configuration. The live gateway result is recorded in the
[A1.1 smoke test report](../../reports/llmtool_a1_1_live_smoke_20260929.md).

## Canonical repository workflow

All project development, tests, validation, documentation, and Git operations
must run from `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
Do not create or use worktrees, independent or milestone clones, sibling or nested
repositories, or alternate project checkouts. The previous isolated project
checkout workflow is deprecated and must not be reused. See [AGENTS.md](../../AGENTS.md).

If sandbox permissions block `.git` writes, leave changes in the canonical
repository and report exact host-terminal staging/commit/push commands; do not
create another checkout. Preserve unrelated user work. Stop for external review
when required; do not automatically start the next milestone or merge main.

All unit, targeted, canonical, smoke, provider integration, regression, Git, and
documentation validation must be launched from the canonical repository.
Test-generated temporary files are allowed only when required by the test/runtime.
The `codex-cli` provider's ephemeral cwd under `/tmp` or `/var/tmp` remains a
runtime security mechanism, not a project checkout. It must contain no Git clone,
worktree, or copy of this repository. This policy does not change provider behavior.

## A2.6 integration validation

All four implemented providers coexist as named profiles under the same registry,
configuration schema, selection, configure flow, CLI commands and plain-text `Reply`
contract. Provider != Role != Agent. Anthropic API, roles, agents, orchestration and
automatic command execution remain outside A2.6.

Shared integration tests cover mixed profiles, user → environment → explicit CLI
selection precedence, selection without saved-config mutation, provider-specific
field rejection, redacted config display, deterministic capability output, real
response normalization behind mocked transports, and configure without runtime I/O.
The configure summary now uses the selected active profile's type when displaying
an endpoint, including when a different profile was edited.

Current validation: 11 new integration tests PASS; expanded targeted suite 148 PASS.
User-reported host canonical result:

```text
Ran 189 tests in 23.278s
OK
```

All 189 canonical tests passed. The previous sandbox loopback-binding limitation
was environment-specific; host validation resolved the remaining canonical gate.
No implementation changes were needed after the host run. A2.6 implementation +
canonical validation are **complete**; A2.6 is merged to main. A2.7 remains outside this milestone; A3 status is documented below.
Live smoke remains: `openai-compatible` NOT-TESTED; `claude-cli` attempted,
unsuccessful; `codex-cli` attempted, unsuccessful; `openai` NOT-TESTED.
Historical provider smoke passes are not evidence of a new A2.6 live pass. See the
[A2.6 validation report](../../reports/llmtool_a2_6_provider_integration_validation_20260930.md).

## Provider capabilities

`llmtool providers` lists the capabilities declared by each production provider implementation;
`llmtool providers --json` prints the same metadata as deterministic JSON. Both commands are
offline introspection: they do not read user/project config or credentials, create a config file,
or connect to a provider.

Capabilities describe technical implementation/runtime ability. **Capability is not authorization,
policy, or approval.** In particular, `code_edit`, `shell`, or `git` metadata does not grant
permission or cause llmtool to modify files, execute commands, or operate Git. Capability metadata
is implementation-owned and is not stored in user/project config.

The `openai` and `openai-compatible` providers declare `chat: yes`, `large_context: yes`,
`reasoning_control: yes`, `streaming: no`, `code_edit: no`, `shell: no`, and `git: no`.
`claude-cli` and `codex-cli` declare only `chat: yes`; their `large_context`, `reasoning_control`, `streaming`,
`code_edit`, `shell`, and `git` capabilities are false.
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
billing are separate; tests do not require live usage, billing setup, or credits. The OpenAI
provider has not been validated against the live API; provider tests use a local mock server.

## Claude CLI provider

The `claude-cli` provider invokes the user's installed `claude` executable as a local runtime; it
does not call the Anthropic HTTP API. It sends the llmtool conversation over stdin using the CLI's
non-interactive print mode with text output, safe mode, no session persistence, and all tools
disabled. The subprocess uses an argv list, bounded output, and a timeout; raw CLI stderr is never
forwarded. It does not edit files, execute shell commands, operate Git, stream, or expose reasoning
controls. The CLI's default model is used unless a model is configured. Generic temperature and
max_tokens settings are retained in config but are not sent or enforced by Claude CLI.

Claude CLI must already be installed and authenticated by the user. llmtool does not discover,
read, or manage Claude credentials and does not configure CLI authentication. `llmtool configure`
does not ask for a Base URL or API key and does not start Claude CLI. This runtime provider is
separate from the Anthropic API provider, which remains planned.
The provider resolves `claude` from the llmtool process `PATH`; PATH order determines which
installed executable and version are used. Project config cannot select or override the executable.
The Claude CLI provider's live end-to-end smoke passed in the normal authenticated user shell;
the Codex isolated runtime may resolve a different executable and does not establish the same
authentication state. See the [runtime validation report](../../reports/llmtool_claude_cli_runtime_diagnosis_20260929.md).
For isolated llmtool configuration tests, `LLMTOOL_CLAUDE_CLI_HOME` can point the child process
at the user's existing Claude CLI home while llmtool itself uses a temporary `HOME`; it is a
runtime environment setting and cannot be set in project config.

Example user config (the model is optional; the CLI default is used when omitted):

```yaml
providers:
  local-claude:
    type: claude-cli
    model: sonnet
    timeout_seconds: 120
active_provider: local-claude
```

## Codex CLI provider

`codex-cli` is a pure-text runtime transport, separate from roles and agents. It resolves
`shutil.which("codex")` on every request; PATH determines executable/version. Neither user nor
project provider config can specify an executable. The inspected installation is Codex CLI
**0.159.2**. Authentication belongs entirely to the existing Codex CLI login/session: llmtool
never reads auth files, accesses keychain credentials, manages login, or copies credentials.

```yaml
providers:
  local-codex:
    type: codex-cli
    # model: your-model  # optional; Codex chooses its default when omitted
    timeout_seconds: 120
active_provider: local-codex
```

Select it with `llmtool ask --provider local-codex 'your question'`, or make it active in user
config. `llmtool configure` skips HTTP endpoint and API-key prompts for this type. Non-null
`base_url`, `api_key`, and `api_key_env` are rejected. `provider_options` must be an empty mapping;
executable, cwd, approval, sandbox, and arbitrary CLI fields are rejected. Model identifiers
must match `[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`. Temperature/max_tokens are stored but neither
sent nor enforced. Only `chat` is true: large-context workflows, reasoning control, streaming,
code editing, shell, and Git are not exposed. Model window size alone does not establish
large-context workflow support. This version does not expose a tested reasoning-option mapping.

Each request creates an empty, private temporary cwd under `/tmp` (fallback `/var/tmp`), outside
the caller's repository. Inherited `TMPDIR` cannot redirect it into the project. Both subprocess
`cwd` and `--cd` select that directory; nothing from the caller repository is copied or mounted.
Prompts travel through stdin, never prompt files. `exec` uses `--no-daemon`, approval `never`,
`--ephemeral`, `--skip-git-repo-check`, `--ignore-user-config`, `--ignore-rules`, and strict config.
`project_doc_max_bytes=0` suppresses AGENTS.md instructions. The argv fixes a named permissions
profile, `llmtool-text`, extending `:read-only`, denying root/temp filesystem reads, reopening
only Codex's minimal system paths and the isolated workspace for reads, and disabling tool
network access. No `--sandbox` flag is added: it would override the stronger permission profile.
See [Codex permission profiles](https://learn.chatgpt.com/docs/permissions) for this precedence.

Documented shell/unified-exec, shell snapshots, multi-agent, hooks, apps, memories, remote-plugin,
and code-mode features are disabled; web search is disabled. These are defense in depth, not a
claim that every Codex tool is disabled. **Isolation plus the restricted read-only sandbox form
the execution boundary.** A malicious executable selected by PATH is outside this boundary:
the installed CLI itself is trusted and handles authentication/model networking outside its
tool sandbox. System/managed Codex policy and future CLI changes also remain runtime dependencies.
The external host validation passed for Codex CLI 0.159.2; deployment hosts and future
CLI versions still depend on their effective runtime policy.

The child receives an allowlisted environment: PATH, HOME, USER, LOGNAME, locale settings,
CODEX_HOME, and TLS certificate paths when present; TMPDIR becomes the isolated cwd. Other
variables, including API keys, Git overrides, Node startup injection, and remote endpoints,
are not inherited. HOME/CODEX_HOME preserve the user's existing CLI session location; when
using an isolated llmtool HOME for testing, explicitly set CODEX_HOME to the existing Codex
state directory without inspecting its contents. User-config-dependent custom gateways/proxies
are not supported by this first version. Global CLI-managed skill/runtime state may still be
loaded by Codex; the caller repository is not made available as context.

The POSIX subprocess uses an argv list, `shell=False`, closed extra descriptors, a separate
process group, nonblocking stdin/stdout/stderr, a deadline, and a separate 1 MiB bound on each
output stream. Timeout/overflow kills the process group. Errors omit stderr, prompts, secrets,
and environment values; invalid UTF-8, empty output, nonzero exit, and startup failure are
normalized. Default text mode returns only final stdout; internal stderr diagnostics are
captured and discarded, never forwarded. Reasoning summaries are suppressed. Temporary cwd
cleanup runs on success/failure; CLI-owned authentication/runtime state is not deleted.

Tests and observed live results are in the [A2.5 validation report](../../reports/llmtool_a2_5_codex_cli_validation_20260930.md).
**External host-terminal validation PASS:** macOS sandbox startup, absolute caller file/Git/AGENTS.md
read denial, direct `CODEX_DIRECT_OK`, and llmtool E2E `CODEX_CLI_OK`; cleanup left no inference-caused
mutation. The temporary HOME-level AGENTS.md did not contaminate the tested invocation with real
CODEX_HOME retained. This does not prove arbitrary CODEX_HOME runtime/auth state or every future
CLI version is isolated. Existing Codex authentication/runtime and the PATH executable remain
trusted dependencies. A2.5 is merged to main; A2.6 implementation and canonical validation are complete (see the A2.6 section).

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
key 環境變數名稱；`claude-cli` 使用已安裝並由使用者登入的本機 Claude CLI，不設定 HTTP
endpoint 或 llmtool API credential。之後再執行 `ask` 等模型命令。
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
llmtool context-pack --json --file incident.txt
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
| `context-pack` | A4 validated packet：`FACTS`、`EVIDENCE`、`INFERENCES`、`HYPOTHESES`、`UNKNOWNS`、`VERIFY_NEXT`；`--json` 輸出 JSON |

ask/analyze-log/review-diff 仍是 prompt 約束，不驗證模型推論的正確性或指令安全性。
context-pack 從 A4 起驗證 structured JSON schema，再輸出 human text 或 JSON；不驗證 claim 真實性。

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

模型命令唯讀本機資料並向設定的 API provider 發出 HTTPS/HTTP request；`claude-cli` 會以
固定 argv、無 shell 的方式啟動使用者已安裝的 CLI，並停用 CLI tools、session persistence
及自訂 hooks/plugins/MCP。llmtool 不執行模型回傳的 shell/Git 指令，也不協調檔案修改。
唯一由 llmtool 直接進行的檔案寫入是明確的 config 操作。
不自動讀取 SSH、keychain 或任意 secret file；明確提供的文字、pipe、`--file`
內容會完整傳給 gateway，請自行移除不應傳送的秘密。
工具不寫 prompt／response log、cache 或 benchmark report。

## 開發與驗證

```sh
make -C tools/llmtool check PYTHON="$(pwd)/tools/llmtool/.venv/bin/python"
```

`check` 跑 compileall syntax check 與 unittest。測試使用暫存 HOME/project、mock
transport、fake Claude executable 和 `127.0.0.1` ephemeral port 的假 gateway；不使用真實
user config 或 credentials，也不連公司 gateway、OpenAI API 或真實 Claude CLI。OpenAI
provider tests 將 official origin 替換為 loopback mock HTTP server；Claude CLI tests 驗證
argv、stdin、timeout、output limit 與 sanitized errors。Live runtime validation 另行記錄，
不由 mock/unit test 推定。

```text
llmtool/cli.py                         CLI 與輸出
llmtool/chat.py                        memory-only chat session and REPL
llmtool/context.py                     immutable packets, bounded parsing and rendering
llmtool/config.py                      YAML 合併、驗證、遮罩、init
llmtool/inputs.py                      明確輸入來源、文字驗證
llmtool/prompts.py                     common rules and skill composition
llmtool/skills.py                      typed declarative resource loader
llmtool/skills/*/SKILL.md               packaged runtime prompt resources
llmtool/providers/base.py              capability model 與 Provider.complete() / Reply contract
llmtool/providers/__init__.py          static provider registry、capability lookup 與 factory
llmtool/providers/openai_compatible.py HTTP transport 與 response 驗證
llmtool/providers/openai.py            固定官方 Responses API transport 與 normalization
llmtool/providers/claude_cli.py        local Claude CLI subprocess 與純文字 Reply normalization
llmtool/providers/codex_cli.py         isolated Codex CLI subprocess 與純文字 Reply normalization
tests/                                config/provider/CLI 測試
```

執行路徑為 task → prompt template → selected provider → `complete()` → final output。
Provider implementations 透過靜態 registry 宣告 type、capabilities、option/readiness
validation 與 `complete()`；A2.2 之後擴充 provider，A3 提供 declarative runtime Skill Framework。其餘
階段及 provider/role 分離方向見 [Roadmap](ROADMAP.md)。

## A3 — Runtime Skill Framework

A3 is DONE and merged to main in `c9159f2`; canonical validation and external review PASS. **Provider != Role != Agent != Skill.** A skill is
instruction/context data and grants no execution ability. Repository engineering
workflows in root `skills/*/SKILL.md` are separate and are never loaded by llmtool.

Runtime resources live at `tools/llmtool/llmtool/skills/<directory>/SKILL.md` and
are included as package data in installations. The fixed package-local root is
used regardless of cwd. There is no global search, plugin system, configurable
root, default selection, or saved skill configuration.

```yaml
---
name: analyze-log
version: 1
description: Analyze supplied logs and identify supported causes.
---
Instructions as Markdown text.
```

Frontmatter requires exactly `name`, `version`, and `description`. Names match
`[a-z0-9][a-z0-9-]{0,63}`; versions are positive YAML integers (not booleans,
strings, or semantic-version strings). Description and body must be nonempty
text without unsafe control characters. Unsupported fields, duplicate YAML keys,
aliases, unsafe YAML tags, duplicate declared names, symlink entries/files,
nonregular files, invalid UTF-8, and files over 64 KiB are rejected with sanitized
errors. Discovery reads immediate skill directories only and sorts by declared
name. Directory names follow the same safe naming rule; exact selection uses
the declared name. The installed resource tree is trusted and must not be
concurrently modified during loading.

```sh
llmtool skills
llmtool skills --json
llmtool skill show analyze-log
llmtool ask --skill analyze-log "supplied log evidence"
llmtool ask --skill review-diff --file supplied.diff
```

Listing/show are offline and bypass config, credentials, and providers. Text and
JSON listing are deterministic; show prints validated metadata JSON followed by
the body. Unknown names fail without echoing the supplied name. Selection does
not mutate config. Only `ask` accepts explicit `--skill`; existing `analyze-log`,
`review-diff`, and `context-pack` commands load their matching packaged resources.

Composition happens before provider dispatch: one system message contains common
llmtool rules, then task instructions, then (for explicit `ask --skill`) a labeled
skill name/version and its body. A separate user message preserves supplied text
exactly. All providers continue receiving the same list of role/content mappings;
provider clients contain no skill logic. The generic ask instruction remains in
code; the three reusable task instructions are now versioned resources.

Skills cannot define command/executable/hooks/pre_run/post_run/shell/Git/tools
fields. Loading uses strict SafeLoader-based text parsing, never imports skill
code or evaluates instructions. There is no shell/Git execution, model-directed
filesystem mutation, automatic tool use, or output execution added by A3. Existing
provider transports and explicit config writes retain their existing boundaries.
Model-returned command text remains only text; prompt instructions are not an
output validator. The samples `analyze-log`, `review-diff`, and `context-pack` use
only supplied evidence and warn against exposing credentials/private configuration.
READ-ONLY != SAFE-TO-DISPLAY.

Validation: 18 targeted A3 tests PASS; canonical syntax check and 207 tests PASS (exact commands
in the [A3 validation report](../../reports/llmtool_a3_skill_framework_validation_20260930.md)).
No live model inference is required or claimed. A3 does not add roles,
orchestration, retrieval, an A4 context schema, A5 Git collection, or A6 validation.

## A3.5 — Interactive Chat Shell

`llmtool chat` starts a persistent interactive process with memory-only conversation
history. Provider != Role != Agent != Skill. A3.5 is DONE and merged to main
in `021456b`; canonical validation and external ChatGPT review PASS.

```sh
llmtool chat
llmtool chat --provider office-qwen --skill analyze-log
```

Chat accepts the same explicit provider/generation options as ask and optional
`--skill`. Initial config resolution uses normal user → project → environment → CLI
precedence. The session copies the resolved config once. Provider-specific startup
overrides stay attached to that initial profile; switching does not reapply them
or reload config. Missing deployment settings do not prevent offline session
controls; requests and provider switches validate readiness normally. Credentials
are resolved only when sending a user turn, not during slash commands or startup.

| Command | Behavior |
|---|---|
| `/help` | Show commands without a request |
| `/status` | Show profile name, provider type, skill, and message count |
| `/provider` | Show current profile and sorted configured profiles/types |
| `/provider <profile>` | Switch for subsequent requests; preserve history |
| `/skill` | Show selected skill and available runtime names |
| `/skill <name>` | Select a runtime skill for subsequent requests |
| `/skill none` | Clear the skill; preserve conversation |
| `/clear` | Clear conversation only; keep provider and skill |
| `/history` | Display user and assistant messages, excluding system context/metadata |
| `/exit`, `/quit` | Exit with status 0 |

**Switching providers sends retained conversation history to the newly selected
provider on the next user turn.** The switch prints this notice. Use `/clear`
before the next message if that history should not be sent. Switching itself
makes no provider request and does not modify saved config. Unknown/incomplete
profiles and invalid skills leave selection and history intact.

`ChatSession` owns the in-memory config snapshot, active selection, successful
turns, and active flag. Each request builds one system message from common
llmtool rules plus the current optional labeled skill, followed by successful
user/assistant turns and the new user message. There is no repeated skill text
in historical turns. Changes to skills rebuild only system context; earlier user
text is unchanged. The conversation turn count counts individual user/assistant
messages: one completed exchange is two turns.

One input line is one user turn. Leading/trailing spaces and Unicode are preserved;
the terminal line-ending delimiter is removed by input(). Blank lines make no
request. Lines whose first non-space character is `/` are command syntax. Malformed
commands fail safely and the session continues. There is no multiline editor yet.
EOF or Ctrl-C at input or during a request exits cleanly with status 0 and no
traceback. Ctrl-C does not resume the shell. A failed request prints a generic
sanitized error, discards that attempted turn, preserves prior successful history,
and permits retry or provider switching. Provider stderr and metadata are hidden.

History has no disk persistence, transcript, restore, database, or cloud sync.
`/history` displays the explicit conversation itself; it does not redact secrets
users included in their own messages. `/clear` removes in-memory turns only.
Replies are display text: no shell/Git execution, code execution, file mutation,
tools, hooks, auto-approval, or routing is added. Existing provider transports
retain their established boundaries. Technical capabilities grant no permission.
Chat adds no orchestration, automatic skill/provider selection, retrieval, or
automatic A4 packet extraction, A5 Git injection, or A6 validation; A9 remains a future milestone.

Existing `ask` remains the one-shot/pipeline interface. Bare `llmtool` retains its
subcommand-required behavior; automatic interactive entry is a future UX option.
History grows in memory without token budgeting or automatic truncation; provider
context limits may eventually cause a recoverable failure. Live smoke is NOT-TESTED.
See the [A3.5 validation report](../../reports/llmtool_a3_5_interactive_chat_validation_20261001.md)
for exact commands and evidence: 30 targeted tests PASS; canonical syntax check and
237 tests PASS, plus packaging and manual fake-provider UX validation.

## A4 — Evidence-first Context Packet

A4 is DONE and merged to main in `38b1a67`; external ChatGPT review PASS.
**Provider != Role != Agent != Skill != Context Packet.** Context Packet is data.

| Category | Semantics |
|---|---|
| FACTS | Directly supported observations from supplied material |
| EVIDENCE | Specific supporting material and declared provenance |
| INFERENCES | Supported conclusions that are not directly observed |
| HYPOTHESES | Possible explanations requiring confirmation |
| UNKNOWNS | Important missing information |
| VERIFY_NEXT | Specific next evidence to collect/check, as text only |

`llmtool/context.py` exposes frozen `ContextPacket` and `EvidenceItem`,
`parse_context_packet(text)`, `format_context_packet(packet)`, `to_dict()`, and
`to_json()`. Categories become tuples internally (list/tuple inputs accepted);
evidence uses typed immutable items. JSON uses arrays and objects in stable field
order; textual content, duplicates, uncertainty, and category membership are
preserved. The parser never promotes hypotheses or merges inference/hypothesis
categories. Structural validation does not certify factual support or provenance.

```json
{
  "facts": ["api-7 returned HTTP 500"],
  "evidence": [{"statement": "HTTP 500 at 10:00Z", "source": "supplied log", "locator": "line 1"}],
  "inferences": ["Request processing did not complete successfully"],
  "hypotheses": ["Startup configuration may be unavailable"],
  "unknowns": ["Previous container exception is not provided"],
  "verify_next": ["Inspect previous container logs"]
}
```

All six fields are required JSON arrays. Non-evidence items must be nonempty
strings. Evidence requires nonempty `statement` and `source`; `locator` is optional,
a nonempty string when supplied, or null. Serialization always includes locator
(null when absent); no locator/source is invented. Source examples include
supplied log, supplied diff, and user-provided incident notes. Locators can be
line numbers, resource identifiers, or diff hunks. They are recorded declarations,
not externally verified provenance.

```sh
llmtool context-pack --file incident.txt
llmtool context-pack --json --file incident.txt
```

Existing input sources and provider selection remain. The model receives the
A3 version-2 context-pack skill through the common message contract; its final
Reply.content is parsed and validated before stdout is touched. Default output
shows the six uppercase sections with evidence source/locator; empty categories
show `(none supplied)`. `--json` prints the stable JSON representation. The old
OBSERVATIONS/UNKNOWN/NEXT_EVIDENCE_TO_COLLECT prose format is replaced; scripts
consuming that format need to migrate. Plain prose from a model is now an error.

Parsing accepts only a JSON object, without code fences or trailing prose. A
1 MiB UTF-8 output cap and maximum JSON nesting depth 4 apply before decoding.
Missing/extra fields, wrong types, blank/non-string items, malformed evidence,
duplicate object keys, NaN/Infinity, invalid Unicode, unsafe text control
characters, excessive depth, and oversized output are rejected. Errors are
sanitized: malformed output exits 1 with empty stdout, including with --verbose,
and never echoes raw malformed response or provider metadata.

The module has no transport, file discovery, retrieval, tools, or execution.
VERIFY_NEXT command-like strings remain display/storage text, never actions.
Explicit --file input remains the existing caller-selected read; A4 gathers no
new evidence. No automatic chat extraction or /context command was added;
A3.5 chat remains plain multi-turn text, even if the context-pack skill is
explicitly selected (then replies follow its prompt but are not parsed by chat).
A5 collects offline Git evidence separately; A6 now checks structural support and command risk;
A9/A10 may reuse packets for orchestration/retrieval. None is implemented by A4.
User-supplied or model-generated sensitive text in supported fields is not
redacted or truth-checked; do not supply secrets. No hidden-reasoning field exists.

Validation: 29 targeted A4 tests PASS; canonical syntax check and 266 tests PASS,
with approved loopback access. Packaging and manual fake-provider JSON/human/error
checks PASS. Live model inference NOT-TESTED. Exact commands, times, environment
limitations and review are in the
[A4 validation report](../../reports/llmtool_a4_context_packet_validation_20261001.md).

## A5 — Git Intelligence

`llmtool git` collects one read-only snapshot from the current working directory;
Git resolves its repository root. It is offline and bypasses provider/config/key
resolution. No evidence is automatically sent to a model or injected into chat.

```sh
llmtool git
llmtool git --json
```

`llmtool/git_intel.py` exposes `collect_git_snapshot(cwd)` and
`format_git_snapshot(snapshot)`. Frozen typed models are `GitRepositoryInfo`,
`GitChange`, `GitStatus`, `GitDiff`, `GitCommit`, and `GitSnapshot`.
`GitSnapshot.to_dict()` / `.to_json()` provide stable field order and JSON arrays.
The snapshot contains root, branch, HEAD, detached state, staged/unstaged/untracked/
conflicted paths, changed/staged files, recent commits, both diffs, and related files.
Unborn repositories have null HEAD; detached HEAD has null branch.

Status uses porcelain v1 NUL records. Rename/copy destinations appear in category
lists, with their original path preserved in `status.entries`. Conflicts have their
own category. Lists sort paths and remove duplicates; `changed_files` is the union
of all four categories, including untracked paths. A path may be both staged and
unstaged. Spaces, Unicode, tabs and newlines are preserved. UTF-8 surrogateescape
preserves non-UTF-8 filename bytes where the host filesystem permits; JSON escapes
those surrogate code points. Human output quotes metadata and escapes terminal
controls in diffs; JSON/API preserve original diff text.

The runtime runner accepts only fixed argv for:

- `rev-parse --show-toplevel` and `rev-parse --verify --quiet HEAD`;
- `branch --show-current`;
- `status --porcelain=v1 -z --untracked-files=all --ignore-submodules=all`;
- `diff` / `diff --cached`, always with `--no-ext-diff --no-textconv --no-color
  --ignore-submodules=all --no-renames --src-prefix=a/ --dst-prefix=b/ --unified=3`;
- `log --max-count=10 --format=%H%x00%s -z --no-decorate --no-show-signature --no-notes`;
- two fixed read-only `config --null --name-only --get-regexp` queries to identify
  filter execution keys and partial/promisor configuration;
- `ls-tree -z HEAD`, `ls-tree -z HEAD:<validated-directory>`, and
  `ls-tree -z HEAD -- <validated-literal-directory>` for related paths.

No arbitrary Git arguments are accepted. Each command has a 10-second timeout,
1 MiB metadata stdout limit, and 64 KiB discarded stderr limit. Each diff has a
2 MiB byte limit: exceeding it fails the entire snapshot before stdout, with a
sanitized error. Successful diffs are complete (`truncated: false`) and include
`byte_count`. Log is capped at 10 SHA/subject entries; bodies are omitted.
Related files are tracked HEAD blob siblings in the first 10 sorted changed
parent directories, excluding changed paths, sorted and capped at 20. New
untracked directories have no HEAD siblings. There is no content scan, recursive
retrieval, semantic matching, or submodule traversal.

Security: argv lists and `shell=False`; explicit cwd; no pager/editor, hooks,
fsmonitor commands, external diff/textconv, or clean/smudge/process filter
execution. Filter command keys are blanked and required filters disabled before
status/diff. Optional index writes, automatic maintenance, inherited Git overrides,
and system/global Git configuration are disabled. Protocol access is blocked;
partial/promisor repositories are rejected to prevent lazy fetching. The trusted
local Git executable comes from PATH. Repository config/includes/attributes are
assumed stable during inspection; this is not a hostile filesystem sandbox or an
atomic snapshot. Filter-backed files may appear modified because conversion is
disabled. Global/system ignore rules are omitted, and diff content is not
secret-redacted. Git may read tracked file bytes to calculate diffs, but llmtool
never separately opens changed-file contents.
See [Git diff controls](https://git-scm.com/docs/git-diff),
[Git filter configuration](https://git-scm.com/docs/git-config), and
[Git environment controls](https://git-scm.com/docs/git).

Provider != Role != Agent != Skill != Context Packet != Git Intelligence.
A4 packets remain supplied/model-structured data; A5 supplies reusable local Git
observations without generating inferences or hypotheses. No automatic A4
conversion is introduced. A6/A9/A10 may consume this data later. Model-like text
in filenames/diffs is data only; no mutations, routing, execution, or chat changes.

A5 is DONE and merged to main in `7de6a99`; external ChatGPT review PASS.
Targeted: 39 tests (38 passed, 1 host-filesystem skip); canonical syntax + 305 tests
(304 passed, 1 same skip), exit 0. Wheel and manual fixture checks PASS.
Validation and host limitations are recorded in the
[A5 validation report](../../reports/llmtool_a5_git_intelligence_validation_20261001.md).

## A6 — Safety & Validator Layer

A6 adds deterministic, offline assessment in `llmtool/validator.py`.
**READ-ONLY != SAFE-TO-DISPLAY.**
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator.
No commands are run; no provider, credentials, Git collection, network, shell
configuration, alias resolution, or variable expansion is used.

```sh
llmtool validate command 'cat /etc/company/app/config.yaml'
llmtool validate command 'git status' --json
llmtool validate context --file packet.json
llmtool validate context --file packet.json --json
```

| Risk | Meaning | Examples |
|---|---|---|
| SAFE_READ | Recognized narrow metadata/read operation, without execution authorization | `pwd`, `ls -l`, `stat file`, `git status`, `git diff --stat`, `git show --stat`, `kubectl get pods` |
| SENSITIVE_READ | May display secrets or sensitive content even though read-only | `cat .env`, `env`, `printenv`, `kubectl get secret -o yaml`, raw `git diff` |
| MUTATING | Changes files, Git state, or services | `touch file`, `git commit -am test`, `git fetch`, `kubectl rollout restart deployment/api`, `terraform apply` |
| DESTRUCTIVE | Deletes data or disrupts systems/processes | `rm -rf /`, `git reset --hard`, `git clean -fd`, `git branch -D old`, `kubectl delete namespace production`, `terraform destroy`, `DROP TABLE users`, `kill -9 123` |
| UNKNOWN | Unsupported syntax/operation; safety has not been established | `find /`, `grep -R /`, `cat arbitrary-config-file`, `sh -c pwd`, substitutions, custom commands |

Sensitive patterns include `.env` files/globs, credential/secret/token names,
private key files (`.pem`, `.key`, `id_rsa`, `id_ed25519`), `~/.ssh`,
`~/.aws/credentials`, `~/.kube/config`, kubeconfig, credential stores/keychains,
`~/.netrc`, `~/.npmrc`, application.yml/yaml/properties and `/etc/.../config.yaml`.
`aws configure export-credentials` and secret YAML/JSON dumps are SENSITIVE_READ.
`kubectl describe pod`, full pod/deployment output, `systemctl status`, all `ps`
forms, and full Git logs are conservatively sensitive because they may expose environment,
logs, or process arguments. A raw Git diff can contain credentials too.

Historical regression: `cat /etc/company/app/config.yaml` is **SENSITIVE_READ**.
Metadata alternatives `ls -l /etc/company/app/config.yaml` and
`stat /etc/company/app/config.yaml` are **SAFE_READ** within the narrow grammar.
This classification never authorizes execution or guarantees arbitrary file
contents/configuration are safe.

The classifier accepts at most **8 KiB UTF-8** and rejects unsupported control
characters (tabs allowed). It tokenizes simple commands and splits unquoted
`|`, `&&`, `||`, and `;`. Quoted separators remain argument data. Recognized risk
floors combine as DESTRUCTIVE > MUTATING > SENSITIVE_READ > UNKNOWN > SAFE_READ.
Unknown components never turn a compound into SAFE_READ. The result is the highest
recognized floor, not a complete analysis of unknown components. Output redirects
`>` / `>>` are MUTATING; `<` is sensitive for recognized secret targets, otherwise
UNKNOWN. Malformed quoting/operators are uncertain; recognized risky components
remain visible. Substitutions, variables, backticks, scripts, unsupported options,
and complex shell grammar remain UNKNOWN within that component. No expansion occurs.
Only conventional absolute binary locations and a plain `sudo <command>` form are
recognized; other wrapper/global-option forms are unsupported. Glob matches are
never resolved. These are conservative heuristics, not a full shell parser or
complete security policy.

Frozen `CommandAssessment`, `ValidationFinding`, and `ValidationResult` provide
stable JSON dictionaries/arrays. Findings use INFO/WARNING/ERROR, static messages,
category and optional deterministic index locator. `classify_command()` preserves
original command text in the API. Command CLI output only includes risk/reason,
without echoing the command; exit 0 means assessment succeeded, **not** command safety.
`requires_approval(risk)` returns false only for SAFE_READ, true for everything else.
It is a separate future-policy helper, with no executor or approval UI attached.

`validate_context_packet(packet)` reuses A4 structure/UTF-8/1 MiB/depth limits and
adds a 1024-total-item bound before semantic processing. It reports ERROR for facts/
inferences with no evidence, an inference with neither fact nor evidence, and exact
statement collisions across facts/inferences/hypotheses/unknowns. Text comparison
is exact: no whitespace/case normalization, paraphrase matching, embeddings, or
LLM judgment. Unknowns without VERIFY_NEXT generate WARNING. `assess_verify_next()`
preserves every suggestion and assesses it; ordinary prose/unsupported/long
suggestions become UNKNOWN with warnings. Sensitive/mutating/destructive suggestions
also warn. These suggestions remain text and are never followed.

**STRUCTURAL SUPPORT != FACTUAL TRUTH.** Evidence presence does not prove a claim,
per-claim evidence linkage, or provenance accuracy. VALID means no ERROR findings;
warnings can include dangerous suggestions, and a packet may still be factually
wrong. No hypothesis is promoted into a fact; classification and source text are
preserved. JSON includes valid/findings/command_assessments in stable order; human
output starts VALID/INVALID and shows severity/code/message. Context CLI exits 1
for semantic errors after printing findings; malformed/oversized input exits 1
with empty stdout and sanitized stderr. Only the explicit regular --file is read,
bounded to A4's limit; no other evidence is collected.

A5 GitSnapshot validation is not added in this MVP. Existing ask, chat, context-pack,
and Git snapshot behavior remain unchanged; no automatic model-output validation.
A7/A8 may reuse results for feedback/regressions; A9/A11 must add their own permission
and approval enforcement before any future execution. No trainer, agent, routing,
retrieval, Telegram, repair loop, or production workflow is introduced.

A6 is implemented on `feat/a6-safety-validator`, not merged; external review pending.
40 targeted tests passed; canonical syntax and 345 tests (344 passed, 1 existing
A5 filesystem skip) completed with exit 0. Wheel and manual offline checks passed.
Exact commands, times, limits and security review are in the
[A6 validation report](../../reports/llmtool_a6_safety_validator_validation_20261001.md).
