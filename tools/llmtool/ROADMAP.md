# LLMTool Roadmap

目標是 generic, portable LLM CLI and future orchestration foundation。
Provider 決定如何連模型或 runtime；Role 決定 workflow 職責，例如 grinder、validator、
coder、reviewer；Agent 是可使用工具或修改環境的執行實體。三者分離，不用模型品牌
決定產品能力。A2 只擴充 Provider，不做 Role engine 或 Agent orchestration。

以下 A0 驗證成果沿用既有 benchmark 與已記錄的驗證結果，並非本輪重新連線測得。
A1.1、A2.1、A2.2、A2.4、A2.5、A2.6 已完成並合併至 main。A2.3 Anthropic API 仍規劃中且尚未實作。
目前 A3 Skill Framework 已在 `feat/a3-skill-framework` 實作並完成測試，等待 external review；A3.5 Interactive Chat Shell 排入下一個 milestone。Capability 描述
implementation/runtime 的技術能力，不是 authorization、policy 或 approval；不代表現在
已授予任何工具執行或 Git mutation 權限。

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

## A0 — Model Validation ✅ DONE

已完成：gateway connectivity、OpenAI-compatible endpoint、long-context tests、
8k／16k／32k／64k retrieval、約 119k prompt ingestion、no-thinking validation、
log diagnosis、diff review、multi-file diagnosis、context compression。
既有驗證腳本：[`llm-benchmark.sh`](../../scripts/llm-benchmark.sh)、
[`llm-reasoning-probe.sh`](../../scripts/llm-reasoning-probe.sh)。這些是 deployment
驗證，不將該 endpoint 或 backend 品牌變成 core 的識別名稱。

## A1 — Generic LLMTool Core ✅ DONE

Generic CLI、分來源 config schema、provider abstraction、單一 OpenAI-compatible
實作、stdin/file/direct input、安全 stdout/stderr contract、error handling、
read-only security boundary、offline tests。

Core 不含 deployment endpoint/model；generic template 以 null 表示尚未設定。
使用者須在首次模型請求前明確配置選定 provider，deployment examples 不會自動載入。

具名 profiles 支援不同 endpoint/model；project 只能改 model/timeout 和通用
generation 設定。只有 user config 或明確 CLI/env 可控制 endpoint/auth/selection。
任務模板仍內建；沒有 roles engine、agent orchestration 或 Git 操作。

## A1.1 — Configure Wizard + Live Smoke ✅ DONE

已完成 interactive `llmtool configure`、自動處理 user config path、hidden API key
input、`api_key`／`api_key_env`、未完整 provider 設定以 null 儲存、atomic write、0600
permissions、既有設定保護、profile preservation；configure 不會自動呼叫 provider。
Live gateway smoke test 已通過，報告見
[`llmtool_a1_1_live_smoke_20260929.md`](../../reports/llmtool_a1_1_live_smoke_20260929.md)。

Smoke test 另記錄 `analyze-log` 曾建議 `cat /etc/company/app/config.yaml`；該命令沒有
執行。此案例納入未來 A3、A6、A8 的安全要求與 regression。

## A2 — Provider Expansion

只擴充 Provider implementation 與其技術能力描述，不做 Skills、Validator、Git
Intelligence、Context Packet、Role engine、Agent routing／orchestration、automatic
execution、Git mutation 或 Production workflow。

### A2.1 — Provider Capability Foundation ✅ DONE

建立 typed、immutable、固定 capability 欄位，以及明確靜態 provider registry、純 metadata
lookup API 和離線 `llmtool providers`／`llmtool providers --json`。A2.1 完成時的 production
registry 只有 `openai-compatible`；A2.2 新增官方 `openai` provider。Capability 包含 `chat`、`large_context`、
`reasoning_control`、`streaming`、`code_edit`、`shell`、`git`；它描述 implementation/runtime
技術能力，不是 authorization、policy 或 approval。不得把 capability 寫入 deployment
config，也不得據此執行 shell、修改檔案或操作 Git。

`openai-compatible` 宣告 chat、large-context workflow、reasoning control 為 true，streaming、
code edit、shell、git 為 false。`large_context` 不代表固定 context window；實際限制取決於
model/deployment。`reasoning_control` 只代表此實作可使用可選的
`provider_options.enable_thinking` 擴充，並映射到 `chat_template_kwargs.enable_thinking`；
它不是 OpenAI-compatible 通用標準，endpoint 是否支援仍需確認。

### A2.2 — OpenAI API Provider ✅ DONE

增加獨立 `openai` provider，使用固定官方 HTTPS endpoint 與 Responses API；
純文字請求、reasoning effort mapping、output-token mapping 與安全 response normalization。
不引入 OpenAI SDK，不啟用 tools、streaming、code edit、shell 或 Git。Source review 與
mock/local HTTP tests PASS；OpenAI live API NOT-TESTED。Live paid API call 不是 A2.2 的
必要驗收條件，也不影響此 milestone 完成。

### A2.3 — Anthropic API Provider

未實作。增加獨立 Anthropic API implementation，並由 implementation 宣告 capabilities。

### A2.4 — Claude CLI Provider ✅ DONE

已確認本機 CLI 的非互動 print、text output、safe mode、no-session-persistence 與停用 tools
參數。第一個 implementation candidate 只做純文字 request/response，以 argv/stdin 啟動 CLI，
不宣告 large-context、reasoning-control、streaming、code-edit、shell 或 Git capability。
Fake executable tests、canonical suite 與 normal authenticated user shell 的 live E2E PASS。
人工驗證在 feature HEAD `cd55e88`、Claude CLI 2.1.285 執行：direct CLI marker 與
llmtool → claude-cli → Claude marker 均符合預期；smoke 後 repository status clean，無 filesystem
或 Git mutation。Codex isolated runtime 無法使用相同 session，且 PATH 解析到不同 executable/version。
兩種 runtime 解析到不同 executable/version：normal shell 使用 nvm 安裝的 Claude CLI 2.1.285，
Codex isolated runtime 使用 `/usr/local/bin/claude` 2.1.206。`claude-cli` 透過 process `PATH`
解析 executable，PATH 順序決定實際 CLI；project config 不可覆寫 executable。這是已記錄的
runtime dependency/minor follow-up，不代表 provider argv failure。
先前 Codex isolated-HOME 嘗試記為 SKIPPED-ENVIRONMENT；後續正常 shell 的 llmtool live E2E 已 PASS。
驗證細節見
[`Claude CLI runtime validation report`](../../reports/llmtool_claude_cli_runtime_diagnosis_20260929.md)。

### A2.5 — Codex CLI Provider ✅ DONE

新增 `codex-cli` pure-text provider、registry/config validation、configure wizard 與 fake CLI tests。
Codex CLI 0.159.2；PATH resolution、stdin、bounded subprocess、isolated temporary cwd、approval never。
使用 restricted read-only permission profile，禁止一般 filesystem read 與 tool network access；
不宣告 shell、Git、code-edit、streaming、reasoning-control 或 large-context capability。
不宣稱全部 tools 已停用；isolation + sandbox 是 execution boundary。
External host-terminal validation PASS：macOS sandbox probe、caller file/Git/AGENTS.md read denial、
limited HOME contamination probe、direct/E2E exact markers 與 cleanup。PATH executable 與 existing
Codex auth/runtime 仍為 trusted dependencies；不保證 arbitrary global state 或 future CLI versions isolation。
Provider != Role != Agent；external review 已完成，並已隨 A2.5 合併至 `main`。完整 tests/live evidence 見 [A2.5 validation report](../../reports/llmtool_a2_5_codex_cli_validation_20260930.md)。

### A2.6 — Provider Integration / Smoke Tests ✅ DONE

四個已實作 providers 的 shared integration coverage 已加入：registry/factory、mixed
config、selection precedence、explicit override 不修改 saved active profile、field isolation、
configure、capabilities、Reply normalization 與 CLI output/error contract。
修正 configure summary 以 active profile type 決定 endpoint 顯示。
Provider != Role != Agent；Anthropic API 仍 planned，沒有新增 roles/agents/orchestration。

11 integration tests PASS；expanded targeted suite 148 PASS。User-reported host canonical result:

```text
Ran 189 tests in 23.278s
OK
```

All 189 canonical tests passed. Previous sandbox loopback-binding failures were
environment-specific; host validation resolved the remaining canonical gate.
No implementation changes were needed after the host run.
A2.6 implementation、host canonical validation 與 external review 均完成，並已合併至 `main`。
Live smoke 狀態維持：openai-compatible NOT-TESTED；claude-cli attempted, unsuccessful；
codex-cli attempted, unsuccessful；openai NOT-TESTED。不將 unsuccessful attempts 改為 PASS。
Provider != Role != Agent。詳見
[A2.6 validation report](../../reports/llmtool_a2_6_provider_integration_validation_20260930.md)。

## A3 — Skill Framework — IMPLEMENTED; external review pending

Provider != Role != Agent != Skill. Declarative runtime skills are packaged under
`tools/llmtool/llmtool/skills/*/SKILL.md`, separate from root repository engineering
skills. Typed immutable skills contain name, description, positive integer version,
and instruction body. Strict bounded SafeLoader parsing rejects unsupported fields,
unsafe names/paths, duplicate keys/names, aliases, and symlink resources.

Offline `skills`, `skills --json`, `skill show <name>` and explicit
`ask --skill <name>` are implemented. Common system rules → task instructions →
selected skill → separate unchanged user message, before provider dispatch.
Existing analyze-log/review-diff/context-pack commands load versioned resources.
No saved skill config, automatic selection for ask, executable hooks, tools,
output execution, roles, retrieval, or orchestration. READ-ONLY != SAFE-TO-DISPLAY;
samples warn against exposing credentials/private configuration.

18 targeted A3 tests PASS; canonical syntax check and 207 tests PASS. Commands and limitations are recorded in the
[A3 validation report](../../reports/llmtool_a3_skill_framework_validation_20260930.md).
Implementation is on `feat/a3-skill-framework`, not merged to main. Stop for external
ChatGPT review; A4/A5/A6/A9 have not been started by this milestone.

## A3.5 — Interactive Chat Shell

提供類似 Codex CLI／Claude CLI 的互動式聊天入口，讓使用者可直接進入長駐 session，
而不是每次只能使用一次性的 `llmtool ask`。A3.5 是 CLI/UX 層，不做 Agent orchestration。

第一版至少包含：

- `llmtool chat` 進入 interactive session；驗證穩定後可評估讓單獨執行 `llmtool` 直接進入 chat。
- session 內保留多輪 conversation context，預設只存在記憶體，不自動寫入聊天歷史檔案。
- 顯示目前 provider、skill 與 session 狀態。
- 支援明確控制命令，例如 `/provider`、`/skill`、`/status`、`/clear`、`/history`、`/help`、`/exit`。
- provider 可在 session 中切換；skill 可顯式選擇或清除，且不得改寫 saved config。
- user message 必須保持原文；skill/context composition 維持 provider-agnostic common message contract。
- `llmtool ask` 保留作為 script、pipeline 與一次性非互動呼叫介面。

A3.5 不包含自動 role routing、agent loop、shell/Git/tool execution、automatic skill selection、
production mutation 或 conversation cloud sync。後續 A9/A11 再把 interactive shell 接上
Role/Agent orchestration 與完整 production workflow。

安全原則：interactive chat 仍遵守 **Provider != Role != Agent != Skill**；模型輸出的 command
預設只是文字，不因進入互動模式而獲得執行權限。

## A4 — Evidence-first Context Packet

結構化 FACTS、EVIDENCE、INFERENCES、HYPOTHESES、UNKNOWNS、VERIFY_NEXT。
保留事實來源與不確定性；不把假設升級為證據。

## A5 — Git Intelligence

唯讀取得 git status、git diff、git log、changed files、related files。
此階段不是 auto commit／push。

## A6 — Safety & Validator Layer

驗證 evidence exists、claim supported、hallucination、unsafe command、missing obvious
findings，並檢查模型建議的 command 是否會輸出敏感內容。分類不能只看 read-only 或
destructive；至少區分：

```text
SAFE_READ
SENSITIVE_READ
MUTATING
DESTRUCTIVE
UNKNOWN
```

高風險 sensitive-read pattern 至少涵蓋：`cat .env`、`cat *credentials*`、
`cat *secret*`、讀取 private key files、`cat kubeconfig`、讀取可能含 credentials 的
application config；`env`、`printenv`、`kubectl get secret`（包含
`-o yaml`／`-o json`）、`aws configure export-credentials`；讀取 `~/.ssh`、
`~/.aws/credentials`、keychain 或 credential store。無法判定時應標記 `UNKNOWN`，不可
因為 command 不修改系統就判為安全顯示。

## A7 — Skill Trainer / Feedback Loop

Qwen／model failure → ChatGPT review → Skill patch → regression test → versioned Skill。
把失敗案例轉為可追蹤的技能修正與回歸資料。

## A8 — Regression / Benchmark System

測量 fact retention、false positive、hallucination、finding recall、compression ratio、
latency、token usage；區分輸出品質與運算成本。新增 live smoke regression：輸入含
permission-denied evidence 的 log 時，若模型建議 `cat /etc/company/app/config.yaml`，
不得自動執行；建議 command 必須標記為 `SENSITIVE_READ` 或 `UNKNOWN`，並優先提出較窄
的安全 evidence collection，例如 `ls -l`、`stat`、只 grep 明確非敏感 key 名稱、
`kubectl describe`／metadata、確認 ownership／permissions／mounted path，或只確認
environment variable 是否存在而不輸出其值。

## A9 — Agent Orchestration

串接 Claude CLI、Codex CLI、OpenAI／Anthropic API。Roles 例如 grinder、validator、
coder、reviewer，與 provider 分離；不因 provider 名稱而隱含指派職責。

## A10 — Dynamic Context Retrieval

Task → first-pass model → identify relevant files → fetch missing evidence →
second-pass context packet。取得證據的權限與範圍需明確。

## A11 — Production Workflow

Git / Logs / Files → llmtool → provider / grinder → context packet → validator →
Claude/Codex → patch → tests → human approval where required → Git。

此為完整流程目標。目前 A0～A2.6 已完成並合併至 `main`，A3 Skill Framework 已實作並等待 external review，
A3.5 Interactive Chat Shell 已排入 roadmap。現階段仍沒有 Role engine、Agent orchestration 或 production mutation。

## 未來 config 方向（目前 schema 尚未接受此完整形式）

```yaml
providers:
  qwen-office:
    type: openai-compatible
    base_url: "http://llm.example.test:8000"
    model: "example-model"
  openai:
    type: openai
    model: "example-openai-model"
  anthropic:
    type: anthropic
    model: "example-anthropic-model"
  claude:
    type: claude-cli
    command: claude
  codex:
    type: codex-cli
    command: codex
roles:
  grinder: qwen-office
  validator: openai
  coder: claude
  alternate_coder: codex
```

A1 保留 named provider 與共用 response contract 作為擴充點，但拒絕未支援 type、
roles、command 等欄位。此 roadmap 不加入 RAG、vector DB、embeddings 或 fine-tuning。
