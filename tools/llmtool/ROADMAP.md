# LLMTool Roadmap

目標是 generic, portable LLM CLI and future orchestration foundation。
Provider 決定如何連模型或 runtime；Role 決定 workflow 職責，例如 grinder、validator、
coder、reviewer；Agent 是可使用工具或修改環境的執行實體。三者分離，不用模型品牌
決定產品能力。A2 只擴充 Provider，不做 Role engine 或 Agent orchestration。

以下 A0 驗證成果沿用既有 benchmark 與已記錄的驗證結果，並非本輪重新連線測得。
A1.1 已完成並合併至 main；A2.1 是目前工作，A2.2 之後仍是規劃。Capability 描述
implementation/runtime 的技術能力，不是 authorization、policy 或 approval；不代表現在
已授予任何工具執行或 Git mutation 權限。

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

### A2.1 — Provider Capability Foundation ← CURRENT

建立 typed、immutable、固定 capability 欄位，以及明確靜態 provider registry、純 metadata
lookup API 和離線 `llmtool providers`／`llmtool providers --json`。Production registry
目前只有 `openai-compatible`。Capability 包含 `chat`、`large_context`、
`reasoning_control`、`streaming`、`code_edit`、`shell`、`git`；它描述 implementation/runtime
技術能力，不是 authorization、policy 或 approval。不得把 capability 寫入 deployment
config，也不得據此執行 shell、修改檔案或操作 Git。

`openai-compatible` 宣告 chat、large-context workflow、reasoning control 為 true，streaming、
code edit、shell、git 為 false。`large_context` 不代表固定 context window；實際限制取決於
model/deployment。`reasoning_control` 只代表此實作可使用可選的
`provider_options.enable_thinking` 擴充，並映射到 `chat_template_kwargs.enable_thinking`；
它不是 OpenAI-compatible 通用標準，endpoint 是否支援仍需確認。

### A2.2 — OpenAI API Provider

未實作。增加獨立 OpenAI API implementation，並由 implementation 宣告 capabilities。

### A2.3 — Anthropic API Provider

未實作。增加獨立 Anthropic API implementation，並由 implementation 宣告 capabilities。

### A2.4 — Claude CLI Provider

未實作。先明確定義 CLI runtime 的程序執行與 approval 邊界，再增加 provider。

### A2.5 — Codex CLI Provider

未實作。先明確定義 CLI runtime 的程序執行與 approval 邊界，再增加 provider。

### A2.6 — Provider Integration / Smoke Tests

未實作。整合已完成的 providers，依各自明確定義的安全邊界執行測試與 smoke checks。

## A3 — Skill Framework

將技能移出 hard-coded prompt，建立可版本化的資源，例如：

```text
skills/
  analyze-log/
  review-diff/
  context-pack/
```

這是未來 llmtool 的 skill 資源，不是目前 repo 根目錄給 coding agent 的工程 skills。
Skills 應避免直接建議讀取可能含 secret 的檔案。唯讀指令仍可能把 credentials 或
private configuration 印出來：**READ-ONLY != SAFE-TO-DISPLAY**。

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

此為完整流程目標；目前 A1 只發送明確提供的文字並回傳內容，沒有 production change。

## 未來 config 方向（A1 不接受此完整 schema）

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
