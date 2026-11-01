# API Agent Gateway: design proposal (2026-11-02)

Status: **PROPOSAL for review**. Nothing here is implemented or authorized. Owner direction (Claude PM session, 2026-11-02): 「獨立工具通用gateway, llmtool是跟這個接口去跟公司llm溝通」. In other words, build a standalone, general gateway tool. llmtool talks to the company LLM, and to any other API-only model, through that tool's interface.

Evidence labels: **FACT** (with file or issue reference), **UNKNOWN** (not verified), **OPINION** (proposal).

## 1. Problem

- **FACT.** Company gw is an OpenAI-compatible chat API. It is used only in text-proposal mode:
  - llmtool packs at most 48 KiB of screened context into one prompt (`tools/llmtool/llmtool/task_submission_cli.py:3058`) and expects a unified diff back.
  - Replies containing `tool_calls` are rejected (`tools/llmtool/llmtool/providers/openai_compatible.py:454-456`).
  - Policy says gw has no shell, filesystem, Git or approval authority (`AGENTS.md`, Company LLM Developer; `skills/company-llm-delegation/SKILL.md`).
- **FACT.** Results so far:
  - Historical: issue #290's 10-22 headline was 11/11 rejected runs (`INVALID_DIFF` / `INVALID_ENVELOPE`).
  - Later, after #319, #363, #428, #334 and #671 merged, 8 of 11 real tasks were accepted, with 3 distinct failure classes and 1 fully delivered task (#290, Cloud audit 2026-11-01).
  - No real task has run since #671, so its effect on delivery is **UNKNOWN**.
  - 2026-11-02, #396 SF-1: gw proposal Tasks `556897e1…` and `f3395959…` in D6 `sf1-lsof-path-gw-20261102`. The first diff put new test methods inside an existing method (IndentationError). The second, given full test-file context, passed 20/21. A Codex Task finished the last test. The PM recorded this in the run; there is no PR yet.
- **FACT.** Codex and Claude Developers instead run as agents inside a registered D6 worktree. They read and edit files themselves, and llmtool checks the result afterwards (changed paths ⊆ write paths, D6 binding).
- **FACT** (#290 probes, [c6450627600](https://github.com/davidcatcat/davidcattoolsproject/issues/290#issuecomment-6450627600) and [c6451338332](https://github.com/davidcatcat/davidcattoolsproject/issues/290#issuecomment-6451338332)):
  - gw accepted a 200,087-token prompt with HTTP 200.
  - It returned one structured `tool_calls` response with `tool_choice: auto`. That is a single sample; whether it is reliable is **UNKNOWN**.
  - Thinking mode used up a 4,096-token output budget on real tasks.

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
   - **Confinement must be race-safe.**
     - Open the workdir once as a directory descriptor and walk every path component with `openat`-style `O_NOFOLLOW` opens relative to that descriptor.
     - Refuse symlinks, `..`, absolute paths, `.git/`, special files, hard-linked files (`st_nlink > 1`) and anything outside the declared read/write sets.
     - Write through a temp file plus `renameat` in the same directory descriptor, and re-verify identity after the write.
     - Before the tool may be called "confined", adversarial tests are required for traversal, symlink swaps between check and use, parent-directory replacement, hard links, FIFOs and out-of-scope writes.
3. **Egress authorization first, then screening.**
   - **Authorization.** Each run needs an existing per-task data-permission basis, the `egress_basis` from llmtool. The read scope must be the minimum needed: only listed files, never the whole repository (`skills/company-llm-delegation/SKILL.md:30-43`).
   - **Screening supplements authorization and never replaces it.** Before each send, the gateway screens every outgoing message: the task prompt, each tool result and each file excerpt. Credential-pattern matches are replaced by a fixed placeholder. Other content outside the authorized data class is discarded with a refusal code.
   - Redacting a fixture line does not make arbitrary repository content sendable. It only stops known secret shapes in files that are already authorized from leaving.
   - **OPINION:** this placement lets a task whose authorized files contain synthetic credential fixtures run with those lines redacted, instead of being refused entirely, as happened to AGY and gw on 2026-11-02. Whether that is acceptable is a company data-policy decision for the Owner.
4. **Budgets.** Maximum turns, total input/output tokens, wall time, bytes per tool result and total bytes sent. Exceeding any budget ends the run with a distinct code. There is no automatic retry or fallback model.
5. **Audit log.** A JSONL file outside the workdir.
   - **Creation:** created with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600.
   - **Header record:** binds `run_id`, `task_id`, `source_sha`, the workdir identity, profile, budgets and the SHA-256 of `task.json`.
   - **Each turn records:**
     - turn number and tool name;
     - path digest (not the raw path, when the path itself is sensitive);
     - byte counts, the SHA-256 of what was sent, and redaction counts;
     - the served model, and usage or UNKNOWN when the API omits it;
     - `prev_sha256`, chaining it to the previous record.
   - It never stores raw prompts, tool arguments, file contents, model text or secrets.
   - `audit_sha256` in the result is the digest of the final record of the chain.
   - **OPINION:** this detects tampering only after the fact by the same UID. It is not protection against that UID.

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

### 5.1 Sessions (Owner direction 2026-11-02)

Owner, verbatim: 「公司llm還可以透過這個gateway建立session,llmtool透過這個方式跟連接codex等ai是一樣的方式」. The gateway therefore gives an API-only model the same session semantics that llmtool already uses for Codex. Codex exposes `codex exec` and `codex exec resume <id>`. llmtool records the CLI-reported native session id and binds it to the D6 (`backend_session_semantics: CLI_REPORTED_SESSION_ID`; `d6_worktree_lifecycle.py --bind-native-session-task-id`).

- **Commands (OPINION):**
  - `gw-agent run …` starts a new session and prints `session_id` in `result.json`.
  - `gw-agent resume <session_id> --task-file …` continues it, for example a repair round with review findings.
  - `gw-agent sessions --json` lists sessions (id, created, last turn, workdir identity, status) without content.
  - `gw-agent end <session_id>` marks a session closed. Closed sessions cannot be resumed.
- **Binding:** a session is bound to one workdir identity (device and inode of the D6 root), `source_sha` at start, the profile and the `task_id`. Resume refuses a different workdir, profile or task, or a HEAD that is not a descendant of the recorded HEAD. This is the same rule llmtool applies to Codex repair rounds in one D6.
- **Storage:**
  - A per-user state directory outside any Git worktree, mode 0700, one directory per session, files 0600.
  - The conversation state holds model-visible messages, so it is sensitive.
  - **Retention:** state is kept only until the session ends or for a bounded time, then discarded. It is never copied into evidence, GitHub or the audit log; the audit log keeps only digests.
  - Each resume re-applies the egress screen to anything newly sent.
- **Context management:** when history approaches the profile's context cap, the gateway compacts older turns into a bounded summary. It first screens the summary, then records the compaction in the audit chain. There is no silent truncation, and on overflow it returns `CONTEXT_EXHAUSTED`.
- **Concurrency:** at most one active run per session, guarded by a lock file. A second `resume` while one is running is refused, not queued.
- **llmtool mapping:**
  - The `api-agent` route stores `session_id` as the native session.
  - Repair rounds use `resume` on the same D6, exactly like Codex direct.
  - The PM can inspect status through `sessions` without reading content.
- **Tests required:** resume continuity; refusal on a mismatched workdir, task or HEAD; lock contention; compaction keeps the task contract and the latest review findings; expired and ended sessions cannot be resumed; no content in `sessions` output or the audit log.

**llmtool side** (a later, separate PR):
- A new Developer route `api-agent`, modelled on the Codex direct route, including session resume for repair rounds. It reuses D6 binding, the dispatch policy, screening of PM-written text, the post-run `changed_paths ⊆ write_paths` check, and test policy and callbacks.
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
- Prompt injection from repository content. Capability limits do not stop an injected instruction from making the model read, and so send, any file inside the allowed read scope, or from making damaging edits inside the write paths. Mitigations:
  - least-privilege read scopes per task;
  - edit validation after the run: llmtool's write-scope check, the diff size cap and tests;
  - formal review of every diff;
  - adversarial tests with malicious repository content that tries to exfiltrate data or make unauthorized changes.

**Required gates:**
- Formal Security review of the gateway and of the llmtool route.
- An AGENTS.md and `company-llm-delegation` SKILL.md change, because "gw has no tools" would no longer be true. This needs an Owner/Coordinator decision.
- Company data rules for egress basis per task stay unchanged.

## 7. Plan

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Live probes, only after the gates required by `skills/company-llm-delegation/SKILL.md:64-69,130-145,211-213`: an existing data-permission basis; the Owner's own grant record for the probe (the C1B/Phase 0 rules: Owner-only grant, non-author verification of the per-invocation evidence afterwards); the `validate-runtime` gate; and the VPN step. Synthetic code in a scratch directory only, never a repo D6: multi-turn native tool calls (about 10 turns, 3+ tools, `tool_choice` auto and required); `tool_call_id` round-trip; tool calls while streaming; thinking plus tools within a 16K output budget; the `text-tools` fallback on the same scenarios | Probe report; decides the default adapter |
| 1 | `tools/api-agent-gateway/` MVP: one adapter, the five tools, path policy, egress screen, budgets, audit log, sessions (run/resume/sessions/end), CLI contract, offline tests with a fake model server | Cross-family review + Security review + CI |
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
- Approve the direction and phase 1 (offline MVP with a fake model server), which needs no live call.
- Phase 0 live probes need the Owner's own grant record, the data-permission basis, VPN and non-author verification, as listed in section 7.
- Optionally run the interim proposal-mode fix in parallel.
- Keep llmtool's integration (phase 3) a separate decision after phases 0–2 produce evidence.
