# Dispatch gate audit — 2026-10-27

The Python `worker_dispatch.evaluate` function rereads and validates
`tools/llmtool/worker-dispatch.json` for each decision. `llmtool dispatch-check`
returns only the worker key, allowed flag, reason code, and policy SHA-256; it
does not expose policy contents. A refusal exits 3. The Claude background
launcher evaluates the mapped worker key before D6/session work or any
`claude --bg` launch.

| Dispatch or launch entrypoint | Gated | Gate call site | Remaining gap |
| --- | --- | --- | --- |
| `llmtool task delegate codex` | Yes | PM must call `llmtool dispatch-check codex-developer` immediately before dispatch. | Caller-side use is a PM operational requirement; this audit cannot establish every external PM script has been updated. |
| `llmtool task delegate opencode` | Yes | PM must call `llmtool dispatch-check opencode-developer` immediately before dispatch. | Caller-side use is a PM operational requirement; direct OpenCode sessions retain their separate D6 and permission checks. |
| `llmtool task delegate company-gw` | Yes | PM must call `llmtool dispatch-check company-gw` immediately before dispatch. | Caller-side use is a PM operational requirement; llmtool mediation and task budget remain separate gates. |
| `llmtool task review-dispatch` | Yes | `llmtool/review_dispatch.py` calls `worker_dispatch.evaluate` before claim/dispatch and rereads immediately before side effects. | Route currently supports Claude reviewer only for non-Claude developers. |
| `scripts/claude_bg_dispatch.py dispatch` | Yes | `dispatch()` maps the exact role name to a worker key and calls `worker_dispatch.evaluate` before D6/session checks and launch. | This helper can map `codex-reviewer` for policy evaluation, but provider/session implementation remains Claude-specific. |
| PM-side `codex exec` for `codex-reviewer` | Yes | PM must invoke `llmtool dispatch-check codex-reviewer --task TASK_ID --owner-ref OWNER_REF` immediately before `codex exec`. | The external PM runner must enforce the returned decision and independently verify Codex session permissions. |
| `llmtool/review_handoff.py` foreground Reviewer handoff | Yes | `worker_dispatch.authorize_dispatch(..., 'claude-reviewer', 'REVIEWER_HANDOFF')` before provider execution. | Host pin, receipt ledger, and bounded review authority remain additional gates. |
| OpenCode worker host invocation | Yes | Host must call `llmtool dispatch-check opencode-developer` immediately before each llmtool-owned worker invocation. | D8 host/worker limits and provider/session evidence remain additional requirements. |

## Audit limits

This table covers the dispatch and launch entrypoints in the Issue #120 audit
scope. PM-side codex exec, OpenCode host, and delegate routes are external to
the Python launcher and must consume the minimal `dispatch-check` result; the
local gate cannot prove an external caller obeyed it. Retries, recovery,
reassignment, and formal reviewer handoff remain subject to their own
`worker_dispatch` checks. The policy file remains the sole dispatch authority;
technical availability and D6, privacy, budget, permission, and independent
review checks are separate.
