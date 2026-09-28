# LLMTool Roadmap

目標是 generic, portable LLM CLI and future orchestration foundation。
Provider 決定如何連模型；role 決定 workflow 職責；agent 是可使用工具／修改程式的
runtime。三者分離，不用模型品牌決定產品能力。

以下 A0 驗證成果沿用既有 benchmark 與已記錄的驗證結果，並非本輪重新連線測得。
A1 之後的項目是規劃，尚未實作，也不代表現在有工具執行或 Git mutation 權限。

## A0 — Model Validation ✅

已完成：gateway connectivity、OpenAI-compatible endpoint、long-context tests、
8k／16k／32k／64k retrieval、約 119k prompt ingestion、no-thinking validation、
log diagnosis、diff review、multi-file diagnosis、context compression。
既有驗證腳本：[`llm-benchmark.sh`](../../scripts/llm-benchmark.sh)、
[`llm-reasoning-probe.sh`](../../scripts/llm-reasoning-probe.sh)。這些是 deployment
驗證，不將該 endpoint 或 backend 品牌變成 core 的識別名稱。

## A1 — Generic LLMTool Core ← CURRENT

Generic CLI、分來源 config schema、provider abstraction、單一 OpenAI-compatible
實作、stdin/file/direct input、安全 stdout/stderr contract、error handling、
read-only security boundary、offline tests。

具名 profiles 支援不同 endpoint/model；project 只能改 model/timeout 和通用
generation 設定。只有 user config 或明確 CLI/env 可控制 endpoint/auth/selection。
任務模板仍內建；沒有 roles engine、agent orchestration 或 Git 操作。

## A2 — Provider Abstraction Expansion

增加專用 OpenAI API、Anthropic API、Claude CLI、Codex CLI provider，選配 local
providers，以及 provider capability metadata。Provider 實作與 task CLI 分離。
CLI runtime 的 execution/approval 邊界須在該階段明確定義；A1 不啟動程序。

## A3 — Skill Framework

將技能移出 hard-coded prompt，建立可版本化的資源，例如：

```text
skills/
  analyze-log/
  review-diff/
  context-pack/
```

這是未來 llmtool 的 skill 資源，不是目前 repo 根目錄給 coding agent 的工程 skills。

## A4 — Evidence-first Context Packet

結構化 FACTS、EVIDENCE、INFERENCES、HYPOTHESES、UNKNOWNS、VERIFY_NEXT。
保留事實來源與不確定性；不把假設升級為證據。

## A5 — Git Intelligence

唯讀取得 git status、git diff、git log、changed files、related files。
此階段不是 auto commit／push。

## A6 — Validator Layer

驗證 evidence exists、claim supported、hallucination、unsafe command、
missing obvious findings。以輸入證據支撐驗證結果。

## A7 — Skill Trainer / Feedback Loop

Qwen／model failure → ChatGPT review → Skill patch → regression test → versioned Skill。
把失敗案例轉為可追蹤的技能修正與回歸資料。

## A8 — Regression / Benchmark System

測量 fact retention、false positive、hallucination、finding recall、compression ratio、
latency、token usage；區分輸出品質與運算成本。

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
