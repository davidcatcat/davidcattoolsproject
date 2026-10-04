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
| Role | workflow 職責，例如 grinder、validator、coder、reviewer | A9 DONE and merged：declarative role registry，與 provider 分離 |
| Agent | A9 AgentStep = role + explicit provider profile + bounded text handoff | A9 DONE and merged；目前無 shell/Git/file/code execution |

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
- **A2.4 — Claude CLI Provider:** DONE; local CLI, pure text; historical pre-hardening live E2E PASS; current status below
- **A2.5 — Codex CLI Provider:** DONE and merged to `main`; external host validation and direct/live E2E PASS
- **A2.6 — Provider Integration / Smoke Tests:** DONE and merged to `main`; host canonical validation 189/189 PASS
- **A3 — Skill Framework:** DONE and merged to `main` (`c9159f2`); external review PASS
- **A3.5 — Interactive Chat Shell:** DONE and merged to `main` (`021456b`); external review PASS
- **A4 — Evidence-first Context Packet:** DONE and merged to `main` (`38b1a67`); external review PASS
- **A5 — Git Intelligence:** DONE and merged to `main` (`7de6a99`); external review PASS
- **A6 — Safety & Validator Layer:** DONE and merged to `main` (`63b5bae`); external review PASS
- **A0–A6:** DONE and merged; A0–A7 bugfix/hardening merged to main (`643caa2`).
- **A7 — Skill Trainer / Feedback Loop:** DONE and merged to main (`d61ca6a`); post-hardening validation and external ChatGPT review PASS
- **A8 — Regression / Benchmark System:** DONE and merged to `main` via PR #9 (`d968161`); external ChatGPT review PASS; A8-R1 FIXED. Claude live model safety benchmark remains FAIL as measured.
- **A9 — Agent Orchestration:** DONE and merged to `main` via PR #10 (`8776476`); A9-R1 FIXED; updated external ChatGPT review PASS; final-head Python 3.9/3.14 CI PASS.
- **A10 — Dynamic Context Retrieval:** DONE and merged to `main` via PR #12 (`21b9757`); independent Claude re-review APPROVE, external ChatGPT final review PASS, final VPN live acceptance PASS, final-head Python 3.9/3.14 CI 605/605 PASS.
- **A11 — Production Workflow & Diagnostics:** A11a.1 core models DONE/merged (`98f9d48`); A11a.2 ephemeral ask diagnostics DONE/merged via PR #16 (`0d32403`); A11a.3 settings/standalone POSIX store DONE/merged via PR #18 (`b9ac538`). A11a.4 persisted ask integration DONE/merged through [PR #19](https://github.com/davidcatcat/davidcattoolsproject/pull/19) (`2bade6a`); post-merge main CI `37167325001` passed Python 3.9/3.14. Overall **A11a Observability & Diagnostics remains incomplete**. A11a.5 offline `runs` / `trace` is resumed on its feature branch with review/publication/CI gates pending; A11a.6–7 are not started or merged; **A11b Execution/Approval/Recovery** and **A11c Integration/Real-world Acceptance** remain unstarted planning. A11a must land before production mutation is introduced.
- **A12 — Remote Frontends / Telegram Agent:** future optional frontend; must reuse A11 task/run/event/error/approval semantics and must not create a second execution/logging/error system.

A1.1 code review and live smoke test are complete. It delivers interactive `llmtool configure`,
automatic user config path handling, hidden API
key input, `api_key`／`api_key_env` support, saving incomplete settings as null, atomic writes,
0600 permissions, existing-config protection, profile preservation, and no automatic provider
request during configuration. The live gateway result is recorded in the
[A1.1 smoke test report](../../reports/llmtool_a1_1_live_smoke_20260929.md).

## A11 delivery checkpoint

A11a → A11b → A11c 各自需要 validation report、external review 與 merge gate。
A11a 先做 diagnostics，既有指令預設不新增持久化寫入；只有 explicit CLI / trusted user
config opt-in 才保存 bounded safe metadata。Debug 不保存 prompt/response、retrieved
content 或 raw stderr，也不改既有 stdout/JSON schema。A11a.3 提供 validated user settings、
LLMTOOL_HOME resolver 與獨立 POSIX store API，已透過 PR #18 合併。A11a.4 已透過 PR #19
合併 ask metadata persistence 與互斥 save/no-save flags；預設仍 off，CLI 明確選擇
優先於 trusted user setting。`LLMTOOL_HOME` 或 `--debug` 本身都不啟用保存。
`runs` / `trace` 在 A11a.5 feature branch 恢復實作；review/CI 尚待完成。`doctor` 留待 A11a.6。

A11b 才加入有限 executor；每個 action、repair 與 Git mutation 都先經 policy/required
approval，不能先執行再批准。A9/A10 原有 fail-fast、no-retry/no-fallback 與 text-only
provider 契約保持。A11c 才整合真實任務驗收。詳細 bounds、安全邊界與 DoD 見
[Roadmap](ROADMAP.md)。A11a.1–3 已合併；A11a.3 的 historical company live 與 review
evidence 保留在原報告。A11a.4 的 R1 precedence finding 經 correction round 1 修正後，
independent Reviewer 對 `852f510` PASS；Developer/Reviewer 各自 canonical 784/784 PASS，
該已發布 SHA 的 Python 3.9/3.14 CI 各 784/784 PASS。External ChatGPT source review
對 `bf37bb86` PASS；該 head 的 final documentation-head CI 37156216279 亦已 SUCCESS，
兩個 Python jobs 各 784/784 PASS。上述為歷史 review evidence。PR #19 已於
2026-10-04 合併為 `2bade6aa95963d3f2c3d9332aa3939407512da7e`；post-merge main
[CI 37167325001](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37167325001)
SUCCESS。A11a 整體仍未 DONE；A11a.5 恢復實作，後續 slices 尚未啟動。
See [A11a.4 validation](../../reports/llmtool_a11a4_persisted_ask_validation_20261003.md).

Architecture refinement 只更新設計與工程指引：固定且版本化的 Task Contract、公司 LLM
優先整理 context／摘要／分類／log normalization／初步規劃／第一輪 review，並以客觀
failure/evidence 事件決定是否升級。Schema 合格與模型自報 confidence 都不能證明內容
正確或完成。Contract 與原始驗證證據不可由摘要取代；摘要仍受公司資料外送邊界約束。
只有符合 contract、綁定 revision/patch digest 的 required evidence 可通過 verification；
tests/deterministic checks 有覆蓋限制，不能保證所有語意正確。A11b 規劃 enforcement／
execution gates；A11c 規劃 cheap-first workflow、獨立驗證與 bounded repair；context budget
與 cost routing 留待後續設計。詳見 [Roadmap](ROADMAP.md#architecture-refinement--planning-only)
及 [WORKFLOW](WORKFLOW.md#task-contract-company-llm-and-evidence)。沒有新增 runtime
schema、hooks engine、automatic routing、retry、fallback 或 escalation 實作。

## Canonical repository workflow

Local Mac work uses `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
When the user chooses Codex Cloud, use only the repository checkout configured for
that Cloud task; discover its actual root instead of assuming `/workspace` is a
Git repository. Verify repository identity, branch, HEAD and working-tree state.
This Cloud exception does not authorize worktrees, independent/milestone clones,
sibling/nested repositories, or alternate local Mac checkouts.

Follow the [llmtool development workflow](WORKFLOW.md) for Cloud development,
Mac-required verification, evidence handoff and merge gates. Complete independent
Cloud work before handing off checks that require the user's Mac. Mock/CI success
does not replace required live verification. Preserve unrelated user work and
stop at the milestone's review gate; do not automatically merge or start the next milestone.

If Git writes are blocked, leave changes in the authorized checkout and provide
exact commands for that environment; do not create another checkout.
Test-generated temporary files and the `codex-cli` provider's empty ephemeral
runtime cwd remain allowed security/test mechanisms, never project checkouts.
This development policy does not change llmtool provider capabilities or runtime isolation.

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
`temperature`, explicitly sets `store: false`, and does not enable streaming, tools, code editing,
shell, or Git. Optional
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
disabled. The subprocess uses an argv list, bounded output, a process-group timeout, an allowlisted
environment, and an empty temporary working directory; raw CLI stderr is never forwarded. Its
resolved executable must be an absolute path. It does not edit files, execute shell commands,
operate Git, stream, or expose reasoning controls. The CLI's default model is used unless a model is
configured. Generic temperature and
max_tokens settings are retained in config but are not sent or enforced by Claude CLI.

Claude CLI must already be installed and authenticated by the user. llmtool does not discover,
read, or manage Claude credentials and does not configure CLI authentication. `llmtool configure`
does not ask for a Base URL or API key and does not start Claude CLI. This runtime provider is
separate from the Anthropic API provider, which remains planned.
The provider resolves `claude` from the llmtool process `PATH`; PATH order determines which
installed executable and version are used. Project config cannot select or override the executable.
The historical pre-hardening Claude CLI provider's live end-to-end smoke passed in the normal authenticated user shell;
the Codex isolated runtime may resolve a different executable and does not establish the same
authentication state. See the [runtime validation report](../../reports/llmtool_claude_cli_runtime_diagnosis_20260929.md).
Current hardened implementation: offline/fake-runtime tests PASS; current marker-only
live E2E PASS with Claude CLI 2.1.286 (`CLAUDE_HARDENED_OK`, exit 0). This smoke
exercised executable resolution, the environment allowlist, isolated cwd, process lifecycle,
and final-text normalization. See the [Round-2 validation report](../../reports/llmtool_a0_a7_bugfix_validation_20261001.md).
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
環境變數名稱，兩者不可同時設定。初次設定的空 optional 欄位保持 unset；編輯時留空保留
既有 profile 欄位／provider_options、generation 和 active_provider。新增 profile 不自動切換 active provider。
optional 欄位可輸入 `-` 清除；hidden API key 留空保留、輸入 `-` 清除，既有 secret 不顯示為 default。
明確更換 provider type 會清除不相容的舊 transport 欄位。OpenAI API key env 初次設定預設提示 `OPENAI_API_KEY`，只是
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
`timeout_seconds` 必須大於 0 且不超過 3600 秒；這是 provider runtime/socket timeout，不是
保證的端到端牆鐘上限。`api_key` 必須是沒有空白的可列印 ASCII；`max_tokens` 為正整數；
temperature 為 0–2。
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
同時提供 positional 與 --file，或明確輸入加上 POSIX pipe/socket 當下已排入的非空資料，會在設定／provider 存取前報錯。
明確輸入遇到 TTY、closed/None stdin、regular-file stdin、/dev/null、空 pipe 或 EOF 不衝突；
`while read q; do llmtool ask "$q"; done < qs` 與 regular-file `--file` shell loops 可正常使用。
macOS/Linux 的 POSIX pipe/socket 使用 FIONREAD 探測，不讀取、不輪詢、不建立背景 thread；
不等待 writer 稍後產生資料或結束，也不探測已被其他程式碼緩衝的 bytes。
無法安全探測的 stream／非 POSIX 平台保留明確輸入行為，不因 probe 不可用而失敗。
stdin-only 模式先檢查 config/readiness，再讀至 EOF（有 16 MiB 上限）；實際測試含真實 pipe/socket/fd 與 shell loops。
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

ask/review-diff 不驗證模型推論；analyze-log（包括 ask --skill analyze-log）另有有限的 deterministic NEXT_COMMANDS guard。
最多顯示三個實際 NEXT_COMMANDS 建議；接受普通／numbered bullets、inline backticks 加括號說明、
fenced commands、`NEXT_COMMANDS: (none)` 與結尾說明文字。大寫 `<SERVICE_NAME>` 等 placeholders
只在分類時視為資料名稱，不替換成真實值。輸出上限 1 MiB、每個 command 8 KiB。
NEXT_COMMANDS 的已知 MUTATING／DESTRUCTIVE、credential/private-data display、connection/auth overrides
或不支援的 shell interpreter／expansions／控制字元建議，會以可見 suppression marker 取代；
stderr 只記錄數量，不回顯被抑制的命令。全部建議被抑制時顯示 `(none)`，不產生替代命令。
ROOT_CAUSE／EVIDENCE／DISTRACTORS／UNKNOWN_OR_UNPROVEN 保留；可用的分析仍 rc=0，不 retry、不換模型。
A6 SENSITIVE_READ 表示未來執行需 approval，並非此文字建議的執行授權；systemctl status、kubectl describe、
journalctl 等正常診斷不因 SENSITIVE_READ 自動隱藏。UNKNOWN 仍是未證明安全、未批准的文字。
analyze-log **從不執行 NEXT_COMMANDS**。窄 guard 偵測 literal Secret/raw API／env／config display 與
secret-store reads，不解析完整 shell、aliases、動態 expansions 或證明模型推論安全。
Credential/private-data sources 包括 `/etc/shadow`、`/etc/gshadow`、`.pgpass`、
`/proc/<pid>/environ`／`/proc/self/environ`，以及不帶參數的 shell 變數 dump `set`；內容顯示建議會被抑制。
只對 command text 使用嚴格 invisible/control 規則；分析文字仍走既有 human terminal escaping boundary。
chat 的 explicit skill 回覆仍未套用此 CLI guard。第二輪隔離公司 gateway smoke 見
[Post-A9 validation](../../reports/llmtool_post_a9_hardening_validation_20261001.md#second-round-independent-review-follow-up--2026-10-02)。
context-pack 從 A4 起驗證 structured JSON schema，再輸出 human text 或 JSON；不驗證 claim 真實性。

Live smoke test 曾發現 `analyze-log` 建議 `cat /etc/company/app/config.yaml`；該命令沒有
執行。**READ-ONLY != SAFE-TO-DISPLAY**：唯讀命令仍可能輸出 credentials 或 private
configuration。這個案例已列入 Roadmap A3 skills 安全要求、A6 command safety validator
及 A8 regression；目前有 A6 deterministic validator 及 analyze-log 窄 post-check，但從不執行建議命令。

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
Ask/analyze-log/review-diff、chat/history 與 context-pack human output 在 stdout 為互動 TTY 時會把 ESC、BEL、C1、
CR、其他 C0 控制字元及 Unicode bidi formatting controls 顯示為可見跳脫文字；一般 Unicode、
換行與 tab 保留。JSON 與非 TTY/piped stdout 保持原始機器資料，不因終端顯示安全而改寫。

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

## Ask diagnostics — ephemeral by default

Only `ask` has the instrumented lifecycle; A11a.2 is merged. Without CLI or
trusted user opt-in, no diagnostics store/trace file is created and `LLMTOOL_HOME`
is ignored. Other commands,
including A9 orchestration and A10 retrieval, retain their output/call contracts.

```sh
llmtool ask "Explain this supplied evidence" --verbose
llmtool ask "Explain this supplied evidence" --debug
```

Normal success keeps final-text stdout and quiet stderr. Expected ask failures
keep their original safe first line and append:

```text
llmtool: code=PROVIDER_TIMEOUT run_id=<32 lowercase hex characters> trace=not-saved
```

`--verbose` keeps validated provider model/token/elapsed keys in their previous
order, then adds run_id, provider_type (when available), and
`diagnostics_persisted=true|false`. `--debug` implies verbose and adds safe event
summaries: ID, type, component, severity, and available duration. It never displays
prompt/response, reasoning, raw stderr/body, environment, host paths, or Python
tracebacks. Invalid/credential-looking metadata is omitted; it cannot fail the
primary request. No tokens are estimated.
An event-only schema or construction failure preserves already validated legacy
verbose metadata; the rejected event is counted without suppressing that display.

A successful ask emits run.start → provider.request → provider.response →
run.end(success). Expected errors emit error then run.end(error), except incomplete
model output ends as incomplete. End severity is INFO for success/output_closed,
ERROR for error, WARNING for interrupted/incomplete. Exit status remains 0/1/2/130;
BrokenPipe keeps 0. Argparse failures do not create a run.

Events are bounded to 16 KiB each, 32 per run and 64 KiB total. Construction or
adapter failure drops observation, counts it, and attempts at most one fixed
DIAGNOSTICS_EVENT_DROPPED warning before finalization. Warning failure never
recurses; display failure is accounted without appending past run.end. Normal
success still prints no diagnostics warning. An exceptional ID generator failure
uses `run_id=unavailable` rather than inventing an insecure identifier.

Codes come from input/config stages and numeric/typed provider failure sites,
never message substring matching. All recovery flags are metadata only;
`fallback_allowed=false`. Every provider execution attempt remains one complete()
call, with no retry or fallback. Sanitized internal stack summaries are deferred
to the later A11a.6 failure-injection checkpoint; unexpected errors use fixed text.
JSON is the coded-diagnostics transfer format; coded pickle/deepcopy remain
unsupported. Diagnostics files are saved only by the explicit metadata opt-in
described below; this A11a.5 feature branch adds the offline `trace` reader.

See [A11a.2 validation](../../reports/llmtool_a11a2_ephemeral_ask_diagnostics_validation_20261002.md).

## Diagnostics settings and store — A11a.3 and A11a.4 merged

Only trusted user `~/.config/llmtool/config.yaml` may contain this optional mapping:

```yaml
diagnostics:
  persist_metadata: false
  max_runs: 100
  max_total_bytes: 67108864
  max_age_days: 14
  max_run_bytes: 1048576
```

`persist_metadata` must be a boolean. Limits require integers: max_runs 1–1000,
max_total_bytes 1–512 MiB, max_age_days 1–365, max_run_bytes 64 KiB–8 MiB and
no greater than max_total_bytes. Unknown fields, wrong types and out-of-range
limits fail configuration before provider access. Project `.llmtool.yaml` cannot
set diagnostics. `config show` keeps its existing provider/generation output;
`configure` preserves a validated diagnostics mapping without expanding it.

Ask accepts mutually exclusive `--save-diagnostics` / `--no-save-diagnostics`.
Precedence: explicit CLI save/no-save > trusted user `persist_metadata` > false.
The unspecified argparse value is `None`; no generic environment variable enables
persistence. `--no-save-diagnostics` disables even trusted user opt-in. Both flags
are ask-only; conflicts and flags on other commands remain usage errors (exit 2)
before observation or store access.

```sh
llmtool ask "Explain this supplied evidence" --save-diagnostics
llmtool ask "Explain this supplied evidence" --save-diagnostics --debug
llmtool ask "Explain this supplied evidence" --no-save-diagnostics
```

Legacy skill and input-source validation run first. Opt-in then resolves settings
and starts the store before readiness, blocking input reads and provider work.
The final store collision-resolved ID is assigned to buffered run.start and all
subsequent events before the first append; events, summary, directory and displayed
run_id agree. Valid settings still allow early input/skill errors to be recorded:
settings resolution after such an error is best effort and never replaces the
original error. Unusable settings plus explicit save report one secondary
unavailable warning without opening the store. When no earlier error exists,
invalid diagnostics settings fail configuration before any store/provider access.
Only validated metadata events and the versioned summary are saved, never prompt,
response, raw provider stderr/body, credentials, reasoning or host paths.

`LLMTOOL_HOME` selects a location without enabling persistence. It must be an
absolute path without controls or parent traversal; shell expansion is not
performed. Defaults: macOS `~/Library/Application Support/llmtool`; Linux
absolute `$XDG_STATE_HOME/llmtool`, falling back to `~/.local/state/llmtool`.
Windows `%LOCALAPPDATA%\\llmtool` is reporting-only; the persistence backend
rejects unsupported platforms. Ask resolves/creates this home only after opt-in;
unsupported persistence warns once and continues the existing ask unsaved.

The standalone `diagnostics_store.DiagnosticsStore` API is opt-in and POSIX-only.
It accepts validated typed events and a typed `RunSummary`; construction/default-off
start perform no filesystem access. An enabled start securely anchors traversal,
creates directories 0700/files 0600, rejects symlink components, unsafe existing
permissions/ownership and hardlinked lock/data files. It does not chmod unsafe
existing stores. File operations use directory descriptors and no-follow flags.
Store/run flock locks protect cleanup, no-clobber run creation, active writers and
full max-run capacity reservations. Run ID collisions have three bounded attempts.

Completion fsyncs and atomically publishes `trace.jsonl` and `summary.json`, then
releases the reservation. Readers must require the summary as the completion marker;
the two files are published individually. Ask interrupts finalize with status
`interrupted`; broken output pipes finalize with `output_closed`. A crash or store
failure can leave a partial trace or a trace without summary. Cleanup only occurs
on enabled start: age, count
and byte limits evict validated eligible runs, active locks prevent deletion, and
abandoned runs have a one-hour grace. Equal completion times use run_id as a
deterministic eviction tie-break. Unknown data remains and consumes capacity;
malformed reservations disable new persistence. Failed summary temp files are
retained conservatively when their ownership/schema cannot be proved.

The API returns unavailable/false plus one fixed bounded `StoreWarning` instead of
raw exception/path data. Specific store codes distinguish unsafe state, capacity,
collision and write failures. A11a.4 connects these results to ask: successful
stdout and exits 0/1/2/130 retain their contracts; each existing provider execution
still makes exactly one request. Creation/append/publication/close errors preserve
the task result and emit at most one fixed bounded store warning. On task errors,
the original safe first line and code/run_id line precede that warning. Failure
metadata reports `trace=saved` only after successful completion, `unavailable`
after requested persistence fails, and `not-saved` when persistence is off.
Verbose/debug report the actual completion result in `diagnostics_persisted`.
The integration attempts every remaining descriptor release even when one close
fails; it never retries the provider or store publication. Unknown partial state
is preserved conservatively. Offline runs/trace are resumed in A11a.5 on this
feature branch; doctor remains deferred to A11a.6.
See [A11a.3 validation](../../reports/llmtool_a11a3_secure_diagnostics_store_validation_20261004.md).
See [A11a.4 validation](../../reports/llmtool_a11a4_persisted_ask_validation_20261003.md)
for Cloud/offline evidence, R1 closure, external source review and reviewed-head CI.
PR #19 merged as `2bade6a`; post-merge main CI `37167325001` passed Python 3.9/3.14.
Historical exact Git objects were published through existing GitHub API access;
direct Git RPC push authentication failed at that checkpoint. No new live inference
is claimed by local mock HTTP or real Linux filesystem validation.

## Offline diagnostics readers — A11a.5 feature branch

```sh
llmtool runs
llmtool trace 0123456789abcdef0123456789abcdef
llmtool trace 0123456789abcdef0123456789abcdef --errors
llmtool trace 0123456789abcdef0123456789abcdef --errors --json
```

These commands read saved metadata from the same `LLMTOOL_HOME` resolver without
reading user/project config, credentials or provider session/auth files, or
calling a provider. They never create storage, clean old runs, repair permissions
or alter records. POSIX macOS/Linux support uses anchored no-follow read-only
descriptors, effective-user/private ownership checks and nonblocking shared
existing locks (root before run). Other backends fail safely. Missing lock files,
symlinks, unsafe modes/owners, hardlinks and nonregular artifacts are rejected.

`runs` renders up to 100 newest validated summaries, ordered by started_at then
run_id descending. Rows label summary status as `recorded_status`; the listing reports
that trace evidence is unverified. It checks summary metadata and artifact
security without reading trace bodies. A recorded success is not evidence of
a completed run; use `trace <run_id>` to verify completion. A completed-looking
active run is incomplete. The run lock is checked before mutable artifacts;
publication temporary files and transient hardlinks in a busy run are not scanned.
No-summary rows have unknown times and sort last. Damaged and unrelated entries
are summarized by generic counts; their names/content never appear. The scan
includes unrelated entries and fails above 1,000 entries; each run permits at
most 16 artifact names. Summary reads are bounded to 4 KiB each and 4 MiB total.
An absent store returns exit 0 with `No saved diagnostics runs.` and creates nothing.

`trace` accepts only 32 lowercase hexadecimal characters. Completed status
requires a valid summary and matching trace IDs, ordered event sequence, counts,
bytes, start/end times, mode, command, status and duration. An active run returns
incomplete with no events; a stable partial or published-without-summary trace
remains incomplete. Only complete valid JSONL lines are displayed; an unfinished
bounded final line is omitted. Completed corruption fails with empty stdout.
`--errors` selects structured error/warning event types after the entire trace
validates; severity alone does not select run.end or unknown types.

Trace file reads are bounded to 8 MiB, 16 KiB per line and 32 events; JSON depth
is at most 3 with 64 values per document. Duplicate keys, nonfinite numbers,
invalid UTF-8, dangerous controls, recognizable credential patterns and invalid
required fields fail safely. Version 1 extensions validate within bounds and are
ignored; unsupported versions return `FORMAT_UNSUPPORTED`. Unknown v1 event
types are generic metadata with type/component `unknown` and empty data.

Trace JSON is the explicit `llmtool.diagnostics.trace` schema, version 1, with
`run_id`, `state`, projected `summary`, projected `events`, `filtered` and
`omitted_partial_line`. Summary fields are run_id, command, mode, status,
started_at, ended_at, duration_ms, provider_type, final_error_code, event_count,
dropped_event_count and trace_bytes. Event fields are event_id, timestamp, type,
component, severity, span_id, parent_span_id and allowlisted data. Reader output
omits stored free-text messages/model/profile strings and arbitrary extensions;
closed codes/flags/numeric cause metadata identify failure layers. It never dumps
raw JSONL, prompts/responses, exception text, credentials or host paths.
Human output uses the same projection. Output is fully bounded to 256 KiB before
stdout; oversized output fails with no partial JSON. Reader errors use fixed
codes/messages on stderr and exit 1 (missing run: `NOT_FOUND`; invalid ID:
`ID_INVALID`). Usage 2, interrupt 130 and broken pipe 0 remain unchanged.

See [A11a.5 validation](../../reports/llmtool_a11a5_runs_trace_validation_20261004.md)
for exact bounds, offline/security/race evidence and pending review/CI gates.

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
明確的 config 操作及 A7 feedback capture/review 可寫入指定檔案；A9 orchestration 與 A10 retrieval 不寫檔。
不自動讀取 SSH、keychain 或任意 secret file；明確提供的文字、pipe、`--file`
內容會完整傳給 gateway，請自行移除不應傳送的秘密。
工具不自動寫 prompt／response log、cache 或 benchmark report；使用者明確 capture 的 feedback 是獨立資料。

## 開發與驗證

llmtool CI uses immutable action SHA pins, `persist-credentials: false`, read-only
permissions, a 15-minute timeout and clean-tree verification after offline tests.
`requirements-ci.txt` pins PyYAML 6.0.3 (the current validated runtime/test dependency);
it is an exact version pin, not a cryptographic hash lock. CI installs it first, then
uses `pip install --no-deps -e tools/llmtool`; package dependency compatibility is unchanged.

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
llmtool/feedback.py                    immutable feedback/proposal/regression data and offline evaluator
llmtool/feedback_files.py              explicit bounded reads and atomic case writes
llmtool/feedback_cli.py                offline capture/show/validate/review/regression adapter
llmtool/benchmark.py                   A7-derived quality, efficiency, runtime and safety measurement
llmtool/benchmark_cli.py               offline explicit files/suites and opt-in single live request
benchmarks/                           repository-local synthetic benchmark fixtures (not packaged)
feedback/                             repository-local curated regression fixtures (not packaged)
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
output validator. Current analyze-log CLI additionally uses the Post-A9 narrow
NEXT_COMMANDS guard described above; A3 resource loading itself stays declarative.
The samples `analyze-log`, `review-diff`, and `context-pack` use
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
automatic A4 packet extraction, A5 Git injection, or A6 validation. A9 is available separately through orchestrate.

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
| SAFE_READ | Recognized narrow metadata/read operation, without execution authorization | `pwd`, `ls -l`, `stat file`, `kubectl get pods` |
| SENSITIVE_READ | May display secrets or sensitive content even though read-only | `cat .env`, `env`, `printenv`, `kubectl get secret -o yaml`, raw `git diff` |
| MUTATING | Changes files, Git state, or services | `touch file`, `git commit -am test`, `git fetch`, `kubectl rollout restart deployment/api`, `terraform apply` |
| DESTRUCTIVE | Deletes data or disrupts systems/processes | `rm -rf /`, `git reset --hard`, `git clean -fd`, `git branch -D old`, `kubectl delete namespace production`, `terraform destroy`, `DROP TABLE users`, `kill -9 123` |
| UNKNOWN | Unsupported syntax/operation; safety has not been established | raw `git status`, `git log --oneline`, `git diff --stat`, `git show --stat`, `find /`, `grep -R /`, substitutions, custom commands |

Plain kubectl metadata reads assume a trusted kubeconfig/context and no connection/authentication/
impersonation overrides. --server/-s, token, kubeconfig, impersonation, TLS identity and related overrides
have at least UNKNOWN risk; recognized delete/apply/Secret reads retain their higher risk and require approval.

Sensitive patterns include `.env` files/globs, credential/secret/token names,
private key files (`.pem`, `.key`, `id_rsa`, `id_ed25519`), `~/.ssh`,
`~/.aws/credentials`, `~/.kube/config`, kubeconfig, credential stores/keychains,
`~/.netrc`, `~/.npmrc`, application.yml/yaml/properties and `/etc/.../config.yaml`.
`aws configure export-credentials` and secret YAML/JSON dumps are SENSITIVE_READ.
`kubectl describe pod`, full pod/deployment output, `systemctl status`, and all `ps`
forms are conservatively sensitive because they may expose environment, logs, or process
arguments. Recognized content-bearing raw Git reads (`git diff`, staged/cached diffs,
full `git log`, and narrowly recognized `git show`) are SENSITIVE_READ and require approval.

Recognized Git discard/delete forms are DESTRUCTIVE: default/worktree `restore`
(including `-W`), forced `checkout`, `switch --discard-changes`, and `stash drop`/`clear`.
Staged-only restore remains MUTATING. These are data classifications and grant no
execution permission. Unsupported/ambiguous restore syntax remains UNKNOWN.

Historical regression: `cat /etc/company/app/config.yaml` is **SENSITIVE_READ**.
Metadata alternatives `ls -l /etc/company/app/config.yaml` and
`stat /etc/company/app/config.yaml` are **SAFE_READ** within the narrow grammar.
Raw Git metadata reads such as `git status`, `git diff --stat`, `git diff --name-only`,
`git log --oneline`, and `git show --stat` are **UNKNOWN** and require approval because
repository Git configuration can run helpers even for apparent reads. Recognized
mutating/destructive Git operations retain MUTATING/DESTRUCTIVE. SENSITIVE_READ
preserves content-exposure warnings and requires approval; it does not claim execution safety. This differs from `llmtool git`,
which uses A5's fixed argv and safe Git configuration for explicitly scoped read-only collection.
This classification never authorizes execution or guarantees arbitrary file
contents/configuration are safe.

The classifier accepts at most **8 KiB UTF-8**. Controls and bidirectional formatting
characters cannot make a command safer: they are conservatively treated as separators with
an UNKNOWN risk floor, while recognized destructive components retain their higher risk.
It tokenizes simple commands and splits unquoted
`|`, `&&`, `||`, and `;`. Quoted separators remain argument data. Recognized risk
floors combine as DESTRUCTIVE > MUTATING > SENSITIVE_READ > UNKNOWN > SAFE_READ.
Unknown components never turn a compound into SAFE_READ. The result is the highest
recognized floor, not a complete analysis of unknown components. Output redirects
`>` / `>>` are MUTATING; `<` is sensitive for recognized secret targets, otherwise
UNKNOWN. Malformed quoting/operators are uncertain; recognized risky components
remain visible. Substitutions, variables, backticks, scripts, unsupported options,
and complex shell grammar remain UNKNOWN within that component. No expansion occurs.
Only conventional absolute binary locations and a plain `sudo <command>` form are
recognized; sudo applies an UNKNOWN approval floor, while sensitive/mutating/destructive
inner commands retain their higher risk. Other wrapper/global-option forms are unsupported. Glob matches are
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

A6 is DONE and merged to main in `63b5bae`; external ChatGPT review PASS.
40 targeted tests passed; canonical syntax and 345 tests (344 passed, 1 existing
A5 filesystem skip) completed with exit 0. Wheel and manual offline checks passed.
Exact commands, times, limits and security review are in the
[A6 validation report](../../reports/llmtool_a6_safety_validator_validation_20261001.md).

## A7 — Skill Trainer / Feedback Loop

A7 means **failure → human/external review → declarative skill proposal → regression
→ manual skill revision/version bump → validation**. It is not gradient training,
fine-tuning, RLHF, weight updates, embeddings training or automatic prompt mutation.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator != Feedback Case.
A7 is DONE and merged to main in `d61ca6a` via PR #6 after synchronization with
hardened main, complete local validation, external ChatGPT review and CI PASS. A3–A6 contracts and all packaged SKILL.md versions remain unchanged.

### Data models and strict format

`llmtool/feedback.py` exposes frozen, typed `FeedbackCase`, `SkillPatchProposal`,
`RegressionAssertion`, `RegressionCase`, `AssertionResult` and `RegressionResult`.
Lists supplied to models become detached tuples; public JSON uses arrays and
stable field order. `to_dict()`/`to_json()` return deterministic data; strict
`parse_feedback_case`, `parse_skill_patch_proposal` and `parse_regression_case`
reject missing/extra fields, invalid types/enums, duplicate object keys, NaN,
malformed JSON, unsupported controls and invalid UTF-8. JSON nesting is at most
6; serialized JSON/files at most 1 MiB; each model text at most 128 KiB UTF-8;
collections at most 128 items, regression assertions at most 64. Ordinary Unicode spaces (NBSP/U+3000)
are accepted; C0 except LF/CR/tab, DEL, C1, bidi controls and invalid UTF-8 remain rejected. A4 shares this rule. Versions are
positive integers up to 2,147,483,647; skill names use A3's validated naming rule.

FeedbackCase fields are `id`, `skill`, `skill_version`, `input`, `actual_output`,
`expected_behavior`, `failure_tags`, `validator_findings`, `review_notes`, `status`.
Input/expected behavior must be nonblank; actual output may be empty to capture
failed/empty replies. Original text, Unicode and whitespace are preserved.
Validator findings use A6's code/severity/message/category/nullable locator fields;
they are supplied explicitly through the reusable API, not inferred by the CLI.
No hidden reasoning, provider metadata/config, auth state or credentials are read.
Recognizable private-key headers, AWS access-key IDs, bearer tokens and OpenAI-style
keys and recognized credential assignments are rejected without echoing them.
Redacted placeholders (REDACTED, <redacted>, ***, null, none, placeholder) are allowed
for assignments. This is not comprehensive secret detection:
users must curate/redact all supplied material, including review notes, before
capture. `show` and case JSON intentionally display explicitly recorded text.

Explicit failure tags (no automatic inference/mapping):

```text
HALLUCINATION FACT_DROPPED UNSUPPORTED_CLAIM MISCLASSIFIED_FACT
MISCLASSIFIED_HYPOTHESIS MISSING_UNKNOWN MISSING_VERIFY_NEXT UNSAFE_COMMAND
SENSITIVE_READ_NOT_FLAGGED OVERLY_BROAD_COMMAND FORMAT_INVALID OTHER
```

IDs are the first 24 lowercase hexadecimal characters of SHA-256 over canonical
JSON (`sort_keys=True`, `ensure_ascii=True`, compact separators, `allow_nan=False`).
Feedback identifying fields are skill, skill_version, input, actual_output,
expected_behavior, ordered failure_tags and ordered validator_findings. Review
notes/status are excluded so review preserves ID; case edits to identifying fields
require a new ID. Regression IDs hash all fields except id, including ordered
source_feedback_ids/assertions. Parsed IDs must match content; IDs are never paths.

### Offline CLI and review workflow

```sh
llmtool feedback capture --skill context-pack \
  --input-file incident.txt --output-file model-output.txt \
  --expected-file expected.txt --tag FORMAT_INVALID --case-file case.json
llmtool feedback show --file case.json
llmtool feedback show --file case.json --json
llmtool feedback validate --file case.json --json
llmtool feedback review --file case.json --notes-file review.txt
llmtool feedback regression --case regression.json --output-file candidate.json --json
```

Capture loads the current packaged skill name/version and prints deterministic
case JSON. Omitting `--case-file` writes only stdout; selecting it creates a new
`.json` file, never overwriting an existing file. Tags may be repeated with distinct
values; duplicates are rejected. No automatic ask/chat/context-pack capture or
hidden storage exists. Repository-local curated fixtures are under `feedback/`,
separate from runtime skills and engineering skills; the CLI never scans them.

Show/validate require a known packaged skill and report CURRENT, OLDER_THAN_CURRENT
or NEWER_THAN_CURRENT. Older cases remain inspectable and valid historical data;
future versions are INVALID (exit 1). Cases are never silently upgraded.
Review records supplied human notes, advancing CAPTURED → REVIEWED only. External
ChatGPT review is human-mediated; the CLI does not contact ChatGPT or prove who
reviewed the case. `transition_feedback` permits exactly one forward step through
CAPTURED → REVIEWED → PATCH_PROPOSED → REGRESSION_READY → CLOSED, with nonblank notes
required after capture. Later statuses are explicit reviewer bookkeeping; no
automatic patch, regression creation or closure is triggered.

Explicit file operations use regular UTF-8 files only, nonblocking/no-follow opens,
anchored directory descriptors, and reject symlink components and `..` traversal.
Use physical paths (e.g. `/private/tmp`, not a symlink alias) when selecting temporary
data. Capture publishes atomically without overwrite and uses 0600 permissions.
Review atomically replaces only the explicit parsed case, preserving ordinary
permission bits and ID; content/identity checks detect intervening changes before
replacement. Concurrent writers are unsupported; this is not a transactional
multi-user store. Ownership/ACL preservation and cross-platform Windows support
are not provided by this POSIX file adapter. No directories are created implicitly.

### Proposals and version rule

`SkillPatchProposal` contains skill, from_version, proposed_version, rationale,
added_rules, changed_rules and linked_feedback_case_ids. A proposal requires at
least one rule change and unique nonempty case links. The proposed version must
be exactly from_version + 1; same/jump/downgrade versions are rejected.
`propose_skill_patch` requires a REVIEWED case matching the current packaged skill.
Proposal construction/parsing is a reusable API; it returns JSON data only:

```python
from llmtool.feedback import parse_feedback_case, propose_skill_patch

# case_json is explicitly supplied, already redacted case text.
reviewed = parse_feedback_case(case_json)
proposal = propose_skill_patch(reviewed, "Preserve missing evidence", added_rules=[
    "Keep missing previous exception in unknowns; never invent it."
])
print(proposal.to_json())
```

A7 never applies raw patches or modifies SKILL.md. A human separately edits/reviews
skills and increments the packaged version, then reruns tests/regressions. Linked
IDs record provenance; A7 does not search for linked cases or independently verify
review/approval artifacts. Proposal data alone does not authorize a change.

### Regression assertions and behavior

RegressionCase contains id, source_feedback_ids, skill, minimum_skill_version,
input and assertions. The recorded input is provenance, not sent to a provider.
Evaluation fails its precondition if the skill is missing or current version is
below the recorded minimum. Each assertion object requires exactly `type`, `value`
and `command` (nullable where unused). Unknown assertion types/fields are rejected.

| Type | Deterministic meaning |
|---|---|
| CONTEXT_VALID | value/command null; supplied output must parse as A4 packet and pass A6 without ERROR |
| FACT_CONTAINS / FACT_NOT_CONTAINS | Exact string element presence/absence in facts |
| HYPOTHESIS_CONTAINS / HYPOTHESIS_NOT_CONTAINS | Exact string element presence/absence in hypotheses |
| UNKNOWN_CONTAINS / VERIFY_NEXT_CONTAINS | Exact string element presence in the corresponding category |
| FINDING_PRESENT / FINDING_ABSENT | A6 finding code presence/absence |
| TEXT_CONTAINS / TEXT_NOT_CONTAINS | Literal substring presence/absence in supplied output |
| COMMAND_RISK_EQUALS | A6 risk equality; command null assesses the whole candidate after removing terminal CR/LF; otherwise requires the exact command as a candidate line or packet verify_next element |

Command selectors do not search arbitrary substrings or silently assess an absent
command. Commands remain data; classification grants no execution permission.
Malformed/unsupported structured candidates fail every structured assertion,
including negative assertions, with sanitized messages. Text/command assertions
can operate on plain output. Results preserve assertion order, index, type, boolean
and static message; they do not echo malformed output. JSON has case_id, passed,
results. Human regression output starts PASS/FAIL. Exit 0 means all assertions
passed; exit 1 means failed assertions or sanitized input/precondition/I/O error;
argument errors are exit 2. Malformed case/input errors leave stdout empty.

Curated [fixtures](feedback/README.md) cover historical
`cat /etc/company/app/config.yaml` = SENSITIVE_READ and metadata alternatives
`ls -l`/`stat` = SAFE_READ, plus HTTP 500 fact retention, configuration mismatch
remaining a hypothesis, missing previous exception retained as unknown, and
concrete verify_next/structural support. They reuse A4/A6, not duplicate rules.
Passing fixtures does not measure a live model or prove factual truth.

A7 has no provider/config/API-key resolution, network, subprocess, shell/Git
execution, eval/exec, arbitrary executable assertions, code loading, automatic
patch application or automatic skill writes. No live provider/VPN is needed.
A8 can later add benchmark/live-output collection separately; A7 adds no A8
scoring/analytics, A9 orchestration, retrieval or approval/execution engine.
Exact validation counts, packaging, environment limitations and manual CLI results
are recorded in the [A7 validation report](../../reports/llmtool_a7_skill_feedback_validation_20261001.md).
Historical pre-sync validation: 47 targeted tests; 392 discovered, 391 passed and
1 inherited skip. Current post-hardening synchronization: 47 A7 tests PASS; complete Python 3.13/3.9
suites each 423 discovered, 422 passed, one skip. Full results are recorded in
the validation report. No live provider call was made or required.

## A8 — Regression / Benchmark System

Benchmark measures outputs; it grants no authorization and is not an Agent.
Provider != Role != Agent != Skill != Context Packet != Git Intelligence != Validator
!= Feedback Case != Benchmark. A0–A8 and hardening are merged to `main`; external
ChatGPT review PASS and A8-R1 is FIXED. Claude live model safety benchmark remains
FAIL as measured. A9 is now merged to `main`; see the A9 section below.

```sh
llmtool benchmark evaluate --case regression.json --output-file candidate.json --json
llmtool benchmark evaluate --spec benchmark.json --output-file candidate.json
llmtool benchmark suite --manifest suite.json --json
# Explicitly opt in to ONE request using an already configured named profile:
llmtool benchmark live --spec benchmark.json --provider local-claude --json
```

Evaluate and suite are completely offline, including with an empty HOME and no
provider configuration. They read only explicit regular UTF-8 files, using A7's
bounded/no-follow file adapter; symlink components and `..` are rejected. No stdin,
glob, recursive discovery, repository scan or implicit database. No suggested
command, VERIFY_NEXT item, regression input or patch is ever executed or applied.
Human output separates QUALITY, EFFICIENCY, RUNTIME and SAFETY, uses the shared
terminal-safe boundary, and does not dump the candidate. Stable JSON includes
static per-assertion results and metrics, not raw candidate text or arbitrary metadata.

### Specifications and suites

`BenchmarkSpec` wraps exactly one validated A7 RegressionCase without changing its
ID, assertion language or semantics. Strict JSON requires `regression` (the whole
A7 case), `name`, `tags` (array), and `safety` (null or the exact fields below).
`--case` accepts an A7 case (and the exact wrapper schema); `--spec` requires the
wrapper. Assertions, expected facts and findings remain solely in A7.

Safety fields are `forbidden_command_risks` (unique A6 risk names) and
`preferred_verify_next_any` (unique exact command strings). A safety expectation
requires a supported packet with no A6 ERROR, no forbidden risk, and at least one
preferred command when that list is nonempty. Classification uses A6 only; it is
DATA, never command execution or permission. Prose/oversized command suggestions
remain UNKNOWN. Sensitive suggestions can pass an explicit expectation that only
forbids mutation/destruction; that does not authorize displaying sensitive data.

A suite manifest requires exactly `entries`, an ordered nonempty array of objects
with `case_file`, `output_file`, and `runtime` (null or normalized runtime object).
File paths are relative to the manifest's directory, or explicit absolute physical
paths. Cases can be plain A7 cases or benchmark wrappers. Duplicate regression IDs
are rejected. Order is preserved. All cases validate before any stdout is emitted;
no partial successful JSON on a later input error. Aggregate ratios sum raw counts,
never average case percentages. Byte totals produce the aggregate compression
ratio. Runtime summaries give sample count, minimum/maximum/mean wall and provider
latency separately, and token totals only across available samples.

Frozen typed models detach collections into tuples. A7's strict parser rejects
missing/extra fields, duplicate keys, NaN/Infinity, invalid UTF-8/controls and depth
above 6. Files, specification/manifest/result JSON and candidate text: 1 MiB;
A7 input: 128 KiB; assertions: 64; suite: 32 cases; tags/preferred commands: 16;
name: 256 bytes; paths: 4096 bytes; preferred commands: 8192 bytes; packet: A6's
1024 total items. Full serialized suite results also must fit 1 MiB. No executable
objects, dynamic imports from data, hidden reasoning fields or arbitrary metadata.

### Metrics

Each quality dimension reports numerator, denominator, ratio and `assessed`.
Zero denominator gives null, never NaN. Malformed/unsupported structured output
fails A7's structured assertions, including negative assertions. Content metrics
then have `assessed: false`, zero observed numerator and null ratio; those are not
claims of measured absence or presence. Aggregate content ratios remain unavailable
if any contributing case with a nonzero denominator was unassessable.

| Dimension | Numerator / denominator |
|---|---|
| assertion_pass_rate | passed assertions / all assertions |
| fact_retention | matched FACT_CONTAINS / expected fact assertions |
| hallucination_rate | violated FACT_NOT_CONTAINS / curated forbidden fact assertions |
| finding_recall | matched FINDING_PRESENT / expected finding assertions |
| false_positive_rate | violated FINDING_ABSENT / curated forbidden finding assertions |
| hypothesis/unknown/verify_next retention | corresponding matched positive assertions / corresponding assertions |
| command_risk_pass_rate | matched COMMAND_RISK_EQUALS / command-risk assertions |
| context_validity | passed CONTEXT_VALID / context-validity assertions |

**hallucination_rate measures violations of explicitly curated forbidden facts only.**
It is not open-world factual verification. False-positive rate likewise measures
explicitly curated forbidden findings only. Exact A7 matching remains unchanged;
no universal factual detection, provider ranking or weighted intelligence score.

Efficiency uses exact UTF-8 bytes: `compression_ratio = output_bytes / input_bytes`.
Lower means a shorter output relative to the benchmark input. Empty input is rejected.
No token estimate is derived from bytes, characters or words.

Normalized runtime input requires exactly `wall_latency_ms`, `provider_latency_ms`,
`model`, `input_tokens`, `output_tokens`, `total_tokens`; each may be null. Supply
via evaluate's `--runtime-file` or a manifest entry's `runtime`. Offline latency
is absent unless explicitly supplied by the caller. These values are supplied data,
not authenticated observations. Timing is integer milliseconds in 0..86,400,000;
tokens are integers in 0..10^12, never booleans/negative/nonfinite values. Explicit
usage requires all three consistent counts (`total = input + output`).

Only live resolves normal provider/config precedence and credentials. It reuses
A3 `messages_for('ask', case.input, explicit_case_skill)` and calls Provider.complete
exactly once, without retries, skill auto-selection, command execution or recording
files. Whole-request latency uses `time.monotonic()` and is separate from normalized
provider `elapsed_seconds`. Model identifiers and bounded elapsed/token fields are
the only metadata retained. OpenAI input/output tokens map directly; compatible
prompt/completion tokens map to input/output. The provider's valid total is retained.
Incomplete, malformed or inconsistent provider token triplets become entirely
unavailable. Claude/Codex elapsed-only metadata therefore gives null token counts
and `token_usage_available: false`, never fabricated zeros or cost estimates.
Zero counts from a complete valid triplet remain available. Token totals are not
monetary cost. Live provider failures remain sanitized and raw stderr is omitted.

Exit 0: all A7 assertions and explicit safety criteria pass. Exit 1: evaluated
failure or sanitized input/runtime/file error. Argparse usage: 2; interruption: 130.
Malformed input produces empty stdout. A malformed candidate can produce a failed
result when within the text bound; this distinguishes scoring failure from invalid
case/manifest/runtime data. Existing A7 text-only assertions can evaluate prose;
only an explicit safety expectation requires a structurally/semantically valid packet.

Synthetic historical permission-denied fixtures and examples are in
[benchmarks/README.md](benchmarks/README.md), excluded from wheels. The fixture
allows historical sensitive cat as classified data but requires narrower metadata
evidence and forbids MUTATING/DESTRUCTIVE suggestions. See the
[A8 validation report](../../reports/llmtool_a8_regression_benchmark_validation_20261001.md)
for exact current tests, packaging, optional live smoke and CI evidence.


## A9 — Agent Orchestration

A9 adds a bounded sequential text orchestration layer. **Role != Provider**: each
plan step explicitly binds one built-in role (`grinder`, `validator`, `coder`,
`reviewer`) to one named provider profile. Provider/model names never imply role.
An AgentStep is role + explicit profile + bounded text handoff; it is not a permission
grant and does not enable provider-native tools.

```sh
llmtool orchestrate --plan plan.json --dry-run --json
llmtool orchestrate --plan plan.json --file task.txt
llmtool orchestrate --plan plan.json "Review this supplied incident"
```

The A9 parser has a hard cap of 8 steps; with the current four built-in unique roles the effective MVP maximum is 4 steps. It is fail-fast:
no retries, fallback provider, hidden substitution, fan-out, voting, loops, branches,
or dynamic routing. Every live step resolves its explicit profile with
`resolve_profile_config(profile_name, require_configured=True)` before credential value resolution.
Only trusted persisted user-profile data applies: cwd project overrides and generic LLMTOOL_PROVIDER /
LLMTOOL_MODEL / LLMTOOL_BASE_URL / LLMTOOL_TIMEOUT / LLMTOOL_MAX_TOKENS cannot rewrite a plan binding.
The selected profile's configured api_key_env still reads its credential value after readiness. Normal ask/config precedence is unchanged.

Handoff is shallow and bounded: every step receives the original task plus only the
immediately previous role output. Earlier outputs are not accumulated and there is
no hidden long-term memory. Previous role output is marked as untrusted model text; delimiters reduce ambiguity but are not claimed to prevent prompt injection.
A10 is a separate explicit retrieval command; A9 does not automatically retrieve context.

A9 is text-only. It never executes shell/VERIFY_NEXT/Git, edits files or code, applies
patches, runs tests, or treats model words such as "approved" as authorization. The
LLM `validator` role is a reasoning/review responsibility and cannot override
deterministic A6 command-risk results. A8 remains the measurement layer; A9 does not
automatically run benchmarks or rank providers.

`--dry-run` validates only the explicit plan structure and is offline: it does not
read task input, config, credentials, instantiate providers, or access network.
Repository examples live under `orchestration/` and are not packaged.

Anthropic HTTP API remains future A2.3 work. A9 has no Anthropic special case; a
future registered provider can use the same explicit profile contract. A9 is DONE and merged to `main` via PR #10 (`8776476`). A9-R1 is FIXED; updated external ChatGPT review PASS and final-head CI PASS. A10 is DONE and merged via PR #12; see its section below.

A9 bounds: original task <= 1 MiB UTF-8; each output <= 128 KiB UTF-8; summed
step content <= 512 KiB; serialized result <= 1 MiB. Each completed prefix must
fit the result JSON budget (including escaping and repeated final_content) before
the next profile is resolved. Overflow stops the pipeline without truncation or
partial stdout. Human output escapes controls on TTYs; JSON and redirected text
retain generated/user-supplied material. Roles do not automatically select skills;
role + explicit skill composition remains future work. See the
[A9 validation report](../../reports/llmtool_a9_agent_orchestration_validation_20261001.md).

## Post-A9 Hardening — DONE and merged to main

A0–A9 remain DONE and merged. Post-A9 hardening is DONE and merged to `main` via PR #11
(`5f581b46968a2dd09d808b3d3125beb4597c0fc1`) after external ChatGPT review PASS and Claude
re-review APPROVE. Final reviewed branch head `566cdb518ce7c3a3b1b3bbff888fe25a2827178f`
passed Python 3.9 / 3.14 CI with 552/552 tests in each job. M1/M2/M3/M4/M5/M7, R1–R8,
and the final F1 credential-source gap are fixed within the documented bounded contracts.
Unsafe analyze-log NEXT_COMMANDS suggestions are visibly suppressed as text; no suggested command is executed.
M6 preferred_verify_next_any matching remains DEFERRED for a separate design review; exact A8 criteria and
the historical Claude model safety FAIL remain preserved. A10 status is documented below. Evidence:
[hardening validation report](../../reports/llmtool_post_a9_hardening_validation_20261001.md).

## A10 — Dynamic Context Retrieval — DONE and merged

A10 provides one bounded path-manifest retrieval round, independent of A9 roles.
Task → deterministic candidate manifest → planner → selected file reads →
synthesizer → strict A4 ContextPacket. Exactly two sequential Provider.complete
calls on success; no retry, fallback, recursive retrieval, parallel calls, hidden
profile substitution, or automatic A9 integration. Provider != Role != Retrieval Planner.

~~~sh
llmtool retrieve-context \
  --root . \
  --scope tools/llmtool/llmtool \
  --scope tools/llmtool/tests \
  --planner-provider office-qwen \
  --synthesizer-provider office-qwen \
  "Why can explicit orchestration profiles still be overridden?"

llmtool retrieve-context --root ./synthetic-evidence --scope auth.py --scope db.py \
  --planner-provider local-profile --synthesizer-provider local-profile \
  --file task.txt --json
~~~

Both profiles must explicitly exist in trusted persisted user configuration.
Generic LLMTOOL_* request overrides and project .llmtool.yaml cannot rewrite
either binding. Readiness precedes configured api_key_env value access. The
planner and synthesizer profiles are both readiness-validated before any input read
or provider request. No credentials are read during preflight. Synthesizer credential
access/client creation remain deferred until all retrieval succeeds.
Normal ask/config precedence remains unchanged. Explicit-input queued pipe/socket
conflicts are checked before config; stdin-only reading starts after BOTH profiles
pass readiness. Existing closed-stdin, regular-file shell-loop and empty-pipe behavior applies.

### Authorization and data sent to providers

Root and at least one scope are required; no default plan, HOME scan, repository
discovery, parent traversal, Git history or implicit extra evidence. Each scope is
a relative regular file or recursively scanned directory. Overlapping scopes are
deduplicated, and manifest paths are sorted, relative POSIX paths.

**Choosing root + scopes authorizes selected files in those scopes to be sent to
the configured synthesizer provider. Choose narrow scopes.** The planner sees
only the original task, candidate path names and byte sizes; it sees no candidate
contents. Its entire response must be exactly strict JSON of the form:

~~~json
{"files":["relative/path/a.py","relative/path/b.md"]}
~~~

The only key is files, containing 0..8 unique exact manifest paths. Empty selection
is valid and still proceeds to the synthesizer with the task. Duplicate JSON keys,
constants, extra fields, malformed/fenced/prose output, duplicate/unknown/noncanonical
paths and excessive selection fail before any selected content is read or any
synthesizer credential/client access. Model output never grants authorization.

The synthesizer receives structured JSON with the original task and the selected
documents' relative paths, normalized UTF-8 content and control_escaped flags.
Ordinary safe UTF-8 text is passed unchanged. Unsafe C0/C1/bidi controls are
deterministically escaped into visible text (ESC becomes a literal backslash + x1b),
never removed or interpreted as ANSI. LF/CR/TAB and normal Unicode formatting
including ZWJ/ZWNJ are preserved. NUL and invalid UTF-8 still fail. Filenames, task and documents
are untrusted data, not instructions. No absolute root or host paths are included
by A10. User-supplied task or file content may itself mention host paths.

### Filesystem policy and bounds

A10 uses POSIX anchored descriptors, no-follow/nonblocking opens and metadata
identity checks before/open/after reads. macOS/Linux support requires their
no-follow directory APIs; unsupported platforms fail closed. Root and every
ancestor must be physical directories (use /private/tmp rather than its symlink
alias /tmp). Symlink scopes are rejected; nested symlink files/directories and
nonregular entries are excluded, never followed. Detected file replacement,
changes, or symlink substitution fail before synthesis. This is not an atomic
snapshot or protection against every hostile concurrent filesystem change.

.git content is excluded, including a root inside .git. Obvious credential stores
(.ssh, .password-store, .aws, .kube, .docker, .gnupg, .azure,
.config/llmtool, .config/gh and .config/gcloud)
are excluded, including roots inside them. Credential files excluded even in
authorized scopes include .env/.env.*, .netrc, .npmrc, .pypirc, .pgpass,
.git-credentials, .vault-token, id_rsa/id_dsa/id_ecdsa/id_ed25519 and
.pem/.key/.p12/.pfx files. Known etc/shadow, etc/gshadow and proc/<pid>/environ
locations are excluded even when root points inside their parent directories.
Additional narrow exclusions: .cargo/credentials, .cargo/credentials.toml, .htpasswd,
kubeconfig, credentials.json, *.tfstate and *.tfstate.backup. No broad config/secret/
credentials substring rule is used. There is no allow-sensitive escape hatch.
Ordinary source names such as config.py, configuration_test.py and secret_manager.py
remain candidates. Path text rejects C0/C1/bidi controls and invisible format
characters; file contents allow ordinary Unicode formatting such as ZWJ/ZWNJ
while visibly escaping unsafe C0/C1/bidi controls; NUL and invalid UTF-8 are rejected.

| Bound | Maximum |
|---|---|
| Task UTF-8 | 1 MiB (shared source reader has an outer 16 MiB bound) |
| Explicit scopes | 32 before deduplication |
| Relative path UTF-8 / path depth | 4096 bytes / 64 components |
| Scanned directory entries, including excluded entries | 16,384 |
| Manifest regular files | 4096 |
| Compact serialized manifest UTF-8 | 256 KiB |
| Planner response UTF-8 / JSON depth | 64 KiB / 2 |
| Selected files | 8 |
| Each selected file / summed evidence (original bytes) | 256 KiB / 512 KiB |
| Normalized content UTF-8 | at most 4x original bytes |
| Structured provider user payload UTF-8 | 4 MiB including JSON escaping |
| ContextPacket | existing A4 1 MiB, depth 4; A6 1024 items |
| Public result JSON UTF-8 | 2 MiB including packet, metadata and escaping |

Manifest construction reads metadata only; it never reads initial bytes to identify
binary data. Regular files larger than 256 KiB are excluded, not truncated.
A narrow suffix exclusion covers .png/.jpg/.jpeg/.gif/.webp/.ico/.pdf/.zip/.gz/
.bz2/.xz/.7z/.woff/.woff2/.ttf/.otf/.mp3/.mp4/.mov/.sqlite/.sqlite3 (case insensitive).
Extensionless and ordinary source/data files remain eligible. Unknown/misnamed
binary files may still be selected and fail bounded UTF-8/NUL validation.
Planner instructions state at most 8 files, each candidate <=256 KiB, total selected
byte_count <=512 KiB, intended UTF-8 evidence and exact manifest paths only.
Selection's declared total is checked before read_selected; read-time checks remain.
Exceeding a scan/count/manifest budget
fails clearly and asks for narrower scopes; nothing is silently truncated.
Selected files are read at most 256 KiB + 1 each. Binary, oversize or changed files
fail before synthesizer credentials/client access. Message/result caps account for
JSON escaping and preserve the inherited packet bound.

### Packet provenance and output

The synthesizer must output strict A4 JSON; parse_context_packet and A6 structural
validation remain authoritative. Every evidence source must exactly equal task
or an actually retrieved relative path. Invented sources fail, without repairing
model output. This checks the supplied source set, not whether each claim is true
or properly supported by that source. VERIFY_NEXT remains text only, never executed.

Frozen result data includes planner/synthesizer profile names, scopes, selected
path/byte_count/SHA-256/control_escaped metadata, A8 allowlisted runtime metadata and the final
packet. byte_count and SHA-256 always describe ORIGINAL file bytes, even when
control_escaped=true records that model-facing content was transformed. Public
result never includes raw document contents. Hashes are audit/provenance only, not authorization. No raw retrieved
documents or raw provider objects are duplicated in result JSON; the generated
packet can quote evidence and therefore can contain selected material.
Unavailable token counts stay null, without estimation or quality/provider ranking.

Human output shows retrieved metadata and the packet through the existing
terminal-safe boundary. JSON preserves generated/user-supplied packet data.
All stages validate before stdout; failures emit sanitized stderr and nonzero
exit, with no partial result. A10 never executes shell/Git/commands/tools, edits
files, applies patches, runs tests, searches the web, or discovers credentials.

A10 MVP selects from paths/sizes, not semantic search. It may choose the wrong
files (the independent review observed missed log_safety.py selections). Use narrow
scopes and explicitly name likely relevant files in the task when known. There are
no embeddings, indexes, grep/ripgrep, AST search, vector DB,
recursive loops or file watchers. Explicit scope cannot prove that ordinary source
contains no secret. The narrow deny policy is defense in depth, not comprehensive
secret detection; it cannot detect secret content or hard-link aliases. Sent task
and evidence are subject to the selected provider's normal data handling.

A10 is DONE and merged via PR #12 (`21b9757`); Claude re-review APPROVE, final VPN acceptance PASS.
There is no --save-output option: A10 remains read-only, with no malformed-output
file writes, retention/overwrite contract or raw diagnostic dump. Errors remain
sanitized with no partial stdout. Output retention needs a separate future design.
A11a.1–4 DONE/merged; A11a.4 PR #19 merge `2bade6a` / historical independent and
external source review PASS / post-merge main CI `37167325001` PASS;
A11a.5 resumed on its feature branch, review/publication/CI pending; A11a.6–7 not started; overall A11a NOT DONE;
A12 remains future optional frontend; Anthropic HTTP API remains
future A2.3; M6 remains DEFERRED. Validation is recorded in the
[A10 validation report](../../reports/llmtool_a10_dynamic_context_retrieval_validation_20261002.md).
