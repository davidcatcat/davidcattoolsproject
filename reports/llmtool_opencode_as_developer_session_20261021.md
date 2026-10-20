# Owner clarification: OpenCode is a Developer like Codex Developer (2026-10-21)

**Owner decision: ACCEPTED** (「同意」, 2026-10-21, in the Claude session; recorded by Claude).
**Owner's words** that framed it: 「我原本要給codex developer做的事,跟opencode是一樣的,我只是因為codex token用完了,才教opencode當第一線」.
This report records the decision and a proposed operating model for ChatGPT / PM review. The PM writes the concrete Task Contracts.

## 1. What went wrong

- **Owner intent:** when Codex quota runs out, OpenCode does the **same Developer work** that Codex Developer does: write code, test, open a PR, get reviewed.
- **What the team built instead:** "OpenCode as Developer" was turned into "llmtool must drive OpenCode automatically", meaning the D4 smoke, D8a credential handoff, isolated Host/Worker, receipts and delivery. That is the long-term **G3/M2 llmtool automation goal**. It was **never a prerequisite** for OpenCode to substitute for Codex Developer.
- **Effect:** about two days of work stalled on D4 (credential policy, ACP isolation, a symlinked executable, and now `provider.auth`). Meanwhile no OpenCode Developer work happened at all.

## 2. Decision

1. **OpenCode works as a Developer session, exactly like Codex Developer.** Each task gets its own registered D6 worktree. The PM assigns a Task Contract; OpenCode implements, tests, self-reviews and opens a PR; Claude formally reviews; CI must pass; the PM merges.
2. **D4 / llmtool-driven OpenCode becomes background, long-term work** (G3/M2). It **no longer blocks** day-to-day development.
3. **Routing:** Codex Developer is the default while its quota lasts. OpenCode (free DeepSeek/GLM) takes over when Codex quota is exhausted, or for tasks the PM routes to it. Routing is a PM technical choice and needs no Owner question (D8).

## 3. Proposed operating model (PM to finalize)

| Step | How |
|---|---|
| Workspace | The PM registers a D6 task worktree under `~/.local/share/llmtool-dev-workspaces/davidcattoolsproject/tasks/<task>`. **Never** the canonical checkout. That is where the earlier OpenCode scope incident happened: the TUI was opened in canonical with no permission config. |
| Permissions | Put an OpenCode project config in the task worktree that enforces scope in OpenCode itself; a prompt alone is not containment. Allow: read the non-sensitive repo (D8); edit only the Task Contract's files; run the task's test/git commands inside the worktree. Deny: external directories, other repos, credential/secret paths, web/network tools, subagents unless needed. The PM verifies the effective permissions before the first prompt. |
| Launch | The Owner (or PM, when it can attach) starts OpenCode **in that worktree** with a free model. One task per session or worktree; one writer per worktree. |
| Dispatch | The PM gives OpenCode the same Task Contract it would give Codex Developer: scope, files, acceptance tests, base SHA, branch. |
| Done | OpenCode commits on its task branch, pushes and opens a PR, and runs its own DeepSeek self-review and an optional free cross-model pre-review. **Claude does the formal review; exact-head CI is required; the PM merges.** |
| Data | D8: non-sensitive repo content may go to OpenCode free models. Never credentials, secrets, VPN/auth material or personal data. |

**What this does not need:** no D4 smoke, no D8a credential handoff (OpenCode uses its own normal login in its own session), no llmtool Host/receipt.

## 4. What stays the same

Formal independent Claude review, exact-head CI, D6 worktree lifecycle and hard Git limits, no paid fallback, no F2 llmtool-owned mutation, and the Owner escalation limits (wrong feature / root-sudo / macOS core).

## 5. D4 status (now background)

- First attempt: pre-launch config error (symlinked executable; PR #238 analysis).
- Second attempt: server and client started, then `provider.auth`. The likely cause is an expired native OpenCode login, which `opencode auth login` would refresh.
- PR #240 (realpath pin + failure stage) is under repair.

D4 continues when convenient. It is not on the critical path for development.

## 6. Next actions

1. ChatGPT / PM confirm this model. The PM writes the first OpenCode Task Contract, a small real bug or cleanup.
2. If OpenCode's login has expired, the Owner runs `opencode auth login` once.
3. The first OpenCode PR goes through Claude review and CI like any Codex Developer PR.
