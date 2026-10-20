# Repository AI collaboration guidance

## Current authority (D6/D8/D8a, 2026-10-20)

- **Roles:** Codex Integrator/PM owns scope, routing, evidence and integration. Codex Developer is resumed. OpenCode remains an authorized Developer; the PM admits qualifying OpenCode tasks without per-call Owner approval. Claude Code is the independent formal Reviewer; Claude Monitor owns its monitoring tool.
- **Owner escalation:** ask only when the requested feature is wrong, root/sudo is needed, or a macOS core/system change is proposed. A remote controller or persistent background agent has a separate technical threat boundary: require source/permission review, exact-SHA CI, applicable native checks and fail-closed behavior before installation; this is not by itself an Owner-approval category. Resolve routine choices within scope and report them.
- **OpenCode boundaries:** non-sensitive repository source, tests, docs and Git history may go to free OpenCode models. Do not send credentials, secrets, VPN/auth material, personal/customer data or restricted logs. Owner D8a authorizes the single active local OpenCode credential (`opencode`) in memory for an isolated TaskA native runner; never print, log, persist, export or copy it, enumerate credentials, or include it in evidence/GitHub. For the current D4 OpenCode-owned inference task, keep credentials inside the OpenCode native process: the Host must not obtain/export them, enumerate credentials or read Credential SQLite; the separately scoped native path must pass independent review before use ([PM task-boundary record](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6289947866)). If the supported path is unavailable or fails least-privilege review, stop.
- **Delivery order:** the still-unused one-time D4 Live Smoke is a precondition; after it succeeds, immediately start the first real OpenCode Developer package. Smoke and inference remain unverified until actual evidence exists. If the Owner reports Codex quota exhaustion, stop Codex Integrator/Developer/subagent inference and preserve work; this does not stop unrelated authorized work.
- **Required gates:** registered D6 task worktree and file allowlist; provider output cap and native retry stop (at most one extra provider request, then kill the job); distinct review of any OpenCode-authored Host/Worker update before use, formal Security review of admission/containment updates before use, and exact-head CI before merge. F2 llmtool-owned mutation is not authorized. No paid fallback, force push, history rewrite or destructive cleanup. Provider/profile/role/model identity never grants execution authority.
- **Data and frozen routes:** D8 egress covers only its listed non-sensitive repository data on OpenCode free models. Any spend or provider data export outside an already authorized route/data class is STOP. Do not send raw restricted logs or secrets. Interim controller release and frozen-route rules are in the [controller reference](skills/ai-team-delivery/controller-release.md).

## Repository scope and changes

The canonical Mac repository is `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`. Local Mac work uses that root or a registered, verified D6 task worktree. Codex Cloud uses only its task-configured checkout and does not inherit local Mac D6 provisioning, VPN or auth state; verify repository identity is `davidcatcat/davidcattoolsproject` and STOP if it cannot be established (never assume `/workspace`). Do not create independent clones or move project trees to temporary directories. Ephemeral provider runtime working directories under `/tmp` or `/var/tmp` are allowed when they contain no project copy/worktree; do not change `codex_cli.py` solely to remove that isolation.

Before repository changes, verify:

```sh
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
```

Before each mutation, confirm repository identity, task owner, root, branch, HEAD and working-tree data. Preserve unrelated work. Stage only task files and inspect the staged diff before commit or publication.

For authorized GitHub API documentation edits, pin reads and the change branch to a known base SHA, update only authorized files, then inspect the final remote diff. API edits do not prove local checkout, test, Mac or runtime evidence.

## Delivery and claims

Read [ai-team-delivery](skills/ai-team-delivery/SKILL.md) for product work, review, QA handoff and publication; read the relevant specialist skill for the gate. Use [safe-git](skills/safe-git/SKILL.md) for Git operations. Test-created temporary files may use normal temporary directories only when required by the test/runtime; never put a project clone or worktree there. For CI failures consult the [CI Knowledge Base](docs/ci/README.md) and [debug-ci](skills/debug-ci/SKILL.md). Documentation-only changes need documentation validation; report missing checks. Apply [Owner outcome reporting v1.1](docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) for milestone outcomes and G1–G4 checkpoints.

Independent review and exact-head CI are separate merge gates. A model's self-review is not independent acceptance. Mac behavior requires evidence from the actual Mac and tested revision. A GitHub comment is not proof of Worker execution, Session wake or Owner notification. Distinguish proposal, review, product acceptance, delegated authority and Git execution in reports.

Prior D1–D7 role and pilot rules are historical. See [policy history](docs/history/llmtool-agent-policy-legacy-20261020.md) only when historical context is needed.
