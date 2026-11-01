# API Agent Gateway: design proposal (2026-11-02)

Status: **PROPOSAL for review**. Nothing here is implemented or authorized. Owner direction (Claude PM session, 2026-11-02): 「獨立工具通用gateway, llmtool是跟這個接口去跟公司llm溝通」. In other words, build a standalone, general gateway tool. llmtool talks to the company LLM, and to any other API-only model, through that tool's interface.

Evidence labels: **FACT** (with file or issue reference), **UNKNOWN** (not verified), **OPINION** (proposal).

## 1. Problem

- **FACT.** Company gw is an OpenAI-compatible chat API. It is used only in text-proposal mode:
  - llmtool packs at most 48 KiB of screened context into one prompt (`tools/llmtool/llmtool/task_submission_cli.py:3058`) and expects a unified diff back.
  - Replies containing `tool_calls` are rejected (`tools/llmtool/llmtool/providers/openai_compatible.py:454-456`).
  - Policy says gw has no shell, filesystem, Git or approval authority (`AGENTS.md`, Company LLM Developer; `skills/company-llm-delegation/SKILL.md`).
- **FACT.** Results so far:
  - Issue #290 recorded 11/11 rejected gw runs (`INVALID_DIFF` / `INVALID_ENVELOPE`).
  - On 2026-11-02, #396 SF-1 produced a correct code change. It misplaced its test methods because it saw only part of the test file, and needed a repair plus a Codex finish.
- **FACT.** Codex and Claude Developers instead run as agents inside a registered D6 worktree. They read and edit files themselves, and llmtool checks the result afterwards (changed paths ⊆ write paths, D6 binding).
- **FACT** (#290 probes). gw accepts very long prompts (a 200,087-token prompt returned HTTP 200). It returned one structured `tool_calls` response with `tool_choice: auto`. Thinking mode can use up a 4,096-token output budget.

The gap: no runtime turns an API-only model into a confined, auditable D6 agent.

## 2. Goal and non-goals

**Goal.** A standalone tool, the API Agent Gateway ("the gateway"). It runs a bounded agent loop against any API-only chat model, inside one working directory, with file tools confined to declared paths. Its CLI contract is stable, and llmtool launches it the same way it launches Codex direct.

**Non-goals.**
- No shell or command execution by the model. Tests stay with llmtool or the PM (EC2/Mac).
- No Git operations and no network tools.
- No PM or Reviewer role.
- No credential storage. Keys are read from named env vars at run time.

## 3. Placement

**OPINION.**
- Put it in its own directory, `tools/api-agent-gateway/`, with its own package, tests and README. This follows the "standalone tool" precedent of the ai-loop decision (#74).
- It does not import llmtool, and llmtool does not import it. The only coupling is the CLI/JSON contract in section 5. This lets other projects reuse it and keeps llmtool's trust boundary small.

## 4. Architecture

```
llmtool (PM side)                    API Agent Gateway (standalone)                 Model API
task delegate --provider api-agent   gw-agent run --workdir <D6> ...                company gw / others
  ├ D6 binding, dispatch policy ───▶   ├ contract + path policy (read/write sets)
  ├ prompt = goal/acceptance/paths     ├ agent loop (turn/byte/time budgets)
  │                                    │   ├ model adapter ─────────────────────▶ chat/completions
  │                                    │   └ tool executor: read/list/grep/edit
  │                                    ├ egress screen on every outgoing message
  ◀── JSON result + audit log ─────────┴ result: changed paths, usage, served model
  └ post-run scope check, tests, review
```

Components:

1. **Model adapters.**
   - `openai-chat-tools` uses native `tools` / `tool_calls`.
   - `text-tools` is a fallback for models without reliable tool calling. The model emits one JSON action per turn, which the gateway parses strictly.
   - Both are selected per model profile (base URL, model, auth env var name, context and output caps, thinking on/off).
   - No ambient proxies or redirects, the same as llmtool's client today.
2. **Tools** (the complete set for v1):
   - `list_dir`, `read_file` (ranged), `grep` (bounded results).
   - `apply_edit`: exact `old`→`new` replace, or create a file within write paths.
   - `finish(summary)`.
   - Paths are resolved against the workdir. Symlinks, `..`, absolute paths, `.git/` and anything outside the declared read/write sets are refused.
3. **Egress screen.** Every message sent to the model is screened before sending: the task prompt, every tool result and every file excerpt.
   - It uses the same credential patterns as llmtool. Matching content is replaced by a fixed placeholder, never sent.
   - This keeps the company rule that content is screened before it leaves (`skills/company-llm-delegation/SKILL.md`). The rule is enforced inside the gateway rather than by llmtool packing the context.
   - **OPINION:** the screen applies to egress, not to the model's own edits. Security-test fixtures in the repo therefore no longer block whole tasks, the problem that blocked AGY and gw on 2026-11-02. The fixture lines are redacted from what the model sees.
4. **Budgets.** Maximum turns, total input/output tokens, wall time, bytes per tool result and total bytes sent. Exceeding any budget ends the run with a distinct code. There is no automatic retry or fallback model.
5. **Audit log.** An append-only JSONL file outside the workdir. Each turn records:
   - turn number, tool name, path, byte counts and SHA-256 of what was sent;
   - redaction counts and the served model;
   - usage, or UNKNOWN when the API omits it.

   It never stores raw file contents or secrets.

## 5. Interface contract with llmtool

**Invocation (OPINION):**

```
gw-agent run --workdir <D6 root> --profile <name> --task-file <task.json> \
             --result-file <result.json> --audit-file <audit.jsonl>
```

**`task.json`:** `schema_version`, `task_id`, `source_sha`, `goal`, `acceptance[]`, `read_paths[]`, `write_paths[]`, `budgets`.

**`result.json`:**
- `status`: `DONE` / `BLOCKED` / `BUDGET_EXCEEDED` / `MODEL_ERROR` / `POLICY_REFUSED`.
- `changed_paths[]`, `summary`, `served_model`, `usage`, `turns`, `audit_sha256`.

**Exit codes** are distinct per status. stdout is not part of the contract.

**llmtool side** (a later, separate PR):
- A new Developer route `api-agent`, modelled on the Codex direct route. It reuses D6 binding, the dispatch policy, screening of PM-written text, the post-run `changed_paths ⊆ write_paths` check, and test policy and callbacks.
- A new `worker-dispatch.json` key, decided by the Owner.

## 6. Security

**Kept:**
- D6 confinement and the post-run write-scope check.
- No shell, no Git.
- Pre-send screening, now applied per message.
- Per-call budgets.
- Per-call egress records, now in the audit log.

**New attack surface:**
- The tool executor's path handling.
- The JSON action parser in `text-tools` mode.
- Prompt injection from repository content (mitigation: tools are capability-limited, so injected instructions can only read or edit inside the allowed paths).

**Required gates:**
- Formal Security review of the gateway and of the llmtool route.
- An AGENTS.md and `company-llm-delegation` SKILL.md change, because "gw has no tools" would no longer be true. This needs an Owner/Coordinator decision.
- Company data rules for egress basis per task stay unchanged.

## 7. Plan

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Live probes on the Mac with VPN, on synthetic code in a scratch directory (never a repo D6): multi-turn native tool calls (about 10 turns, 3+ tools, `tool_choice` auto and required); `tool_call_id` round-trip; tool calls while streaming; thinking plus tools within a 16K output budget; the `text-tools` fallback on the same scenarios | Probe report; decides the default adapter |
| 1 | `tools/api-agent-gateway/` MVP: one adapter, the five tools, path policy, egress screen, budgets, audit log, CLI contract, offline tests with a fake model server | Cross-family review + Security review + CI |
| 2 | Quality check: replay `tools/llmtool/eval/gw_ladder` L1–L6 in agent mode, compared with the proposal-mode baseline | Report with numbers |
| 3 | llmtool `api-agent` route plus `worker-dispatch.json` key plus the AGENTS/SKILL update | Owner decision + Security review + CI |
| 4 | Further profiles (other API-only models) as configuration only | Per-profile probe |

**Interim (optional, low risk).** Until phase 3, the current gw proposal mode could send full contents of small `write_paths` files instead of excerpts, and honour the `max_tokens` setting for thinking-on tasks. This fixes the partial-context failure seen on 2026-11-02 without any policy change.

## 8. Open questions

1. Owner: is a new top-level tool directory acceptable, or should it be a separate repository?
2. Owner/Coordinator: the wording of the AGENTS.md / SKILL.md change for "gw may use gateway-confined file tools".
3. **UNKNOWN:** gw reliability over many turns of tool calling, and code-edit quality of the served model (`qwen3.8-27b` per #290) in agent mode.
4. **UNKNOWN:** whether gw reports usage on every response. If not, usage is recorded as UNKNOWN.
5. Should the gateway also serve non-company API models (for example free API tiers) from day one, or start with gw only? **OPINION:** gw only, with adapters kept generic.

## 9. Recommendation

**OPINION.**
- Approve phase 0 now; it needs only the Owner's VPN session. Then build phase 1 as a standalone tool.
- Optionally run the interim proposal-mode fix in parallel.
- Keep llmtool's integration (phase 3) a separate decision after phases 0–2 produce evidence.
