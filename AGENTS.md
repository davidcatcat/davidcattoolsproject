# Repository AI collaboration guidance

## Current authority (D6/D8/D8a, 2026-10-20)

- **Roles:** Codex Integrator/PM owns scope, routing, evidence and integration. Codex Developer is resumed. OpenCode remains an authorized Developer; the PM admits qualifying OpenCode tasks without per-call Owner approval. Claude Code is the independent formal Reviewer; Claude's monitoring tool remains separately owned by Claude.
- **Owner escalation:** ask only when the requested feature is wrong, root/sudo is needed, or a macOS core/system change is proposed. See the [escalation reference](skills/ai-team-delivery/owner-escalation.md) for the exact system boundary. Resolve routine choices within scope and report them.
- **OpenCode boundaries:** non-sensitive repository source, tests, docs and Git history may go to free OpenCode models. Screen history, read/search results, command/test output and subagent context for secrets/restricted data before egress; prompt wording is not a containment boundary. Do not send credentials, VPN/auth material, personal/customer data or restricted logs. Owner D8a authorizes reading the single active local OpenCode credential (`opencode`) in memory, handing it only to the isolated TaskA native runner, then immediately dropping the reference; never print, log, persist, export or copy it, enumerate credentials, or include it in evidence/GitHub. See the full [D8/D8a decision](docs/decisions/llmtool_opencode_secondary_developer_20261019.md). For the current D4 OpenCode-owned inference task, credentials stay inside the OpenCode native process: the Host must not obtain, log or export them, enumerate credentials or read Credential SQLite; this separately scoped path must pass independent review before use ([PM task-boundary record](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6289947866)). Verify the installed OpenCode version and effective read/search/shell/task/external-directory permissions; prompt text alone is not containment. If least privilege cannot be established, stop.
- **Delivery order:** follow the D4 Smoke precondition and post-success sequence in the [D8/D8a decision](docs/decisions/llmtool_opencode_secondary_developer_20261019.md); both live inference and Smoke remain unverified. If the Owner reports Codex quota exhaustion, stop Codex Integrator/Developer/subagent inference and preserve work; resume only when quota is usable and the task remains Owner-authorized under D8. Unrelated authorized work may continue.
- **Required gates:** registered D6 task worktree and file allowlist; D8 records Claude-authored [#229](https://github.com/davidcatcat/davidcattoolsproject/pull/229) and [#230](https://github.com/davidcatcat/davidcattoolsproject/pull/230) as requiring distinct non-Claude-author review before use—verify current PR review evidence; provider output cap and native retry stop (at most one extra provider request, then kill the job); distinct review of any OpenCode-authored Host/Worker update before use, formal Security review of admission/containment updates before use, and exact-head CI before merge. F2 llmtool-owned mutation is not authorized. No paid fallback, force push, history rewrite or destructive cleanup. Provider/profile/role/model identity never grants execution authority.
- **Data and frozen routes:** D8 egress covers only its listed non-sensitive repository data on OpenCode free models. Any spend or provider export outside an authorized route/data class is STOP. Do not send raw restricted logs or secrets. Interim controller release and frozen-route rules are in the [controller reference](skills/ai-team-delivery/controller-release.md).

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

Read [ai-team-delivery](skills/ai-team-delivery/SKILL.md) for product work, review, QA handoff and publication; use [bug-hunt](skills/bug-hunt/SKILL.md), [security-review](skills/security-review/SKILL.md), [qa-validation](skills/qa-validation/SKILL.md), [validate-runtime](skills/validate-runtime/SKILL.md) and [company-llm-delegation](skills/company-llm-delegation/SKILL.md) for their respective gates. These gates are distinct. Use [safe-git](skills/safe-git/SKILL.md) for Git operations. For Cloud roles and handoff, read [WORKFLOW](tools/llmtool/WORKFLOW.md). Test-created temporary files may use normal temporary directories only when required by the test/runtime; never put a project clone or worktree there. For CI failures consult the [CI Knowledge Base](docs/ci/README.md) and [debug-ci](skills/debug-ci/SKILL.md). Documentation-only changes need documentation validation; report missing checks. Apply [Owner outcome reporting v1.1](docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md) for milestone outcomes and G1–G4 checkpoints.

Independent review and exact-head CI are separate merge gates. A model's self-review is not independent acceptance. Mac behavior requires evidence from the actual Mac and tested revision. A GitHub comment is not proof of Worker execution, Session wake or Owner notification. Distinguish proposal, review, product acceptance, delegated authority and Git execution in reports.

D1–D7 role order is historical only where D8 changes roles/per-call approval or D6 changes fixed-worktree limits. Unchanged egress, containment, review, live-evidence and runtime safeguards remain current. See [policy history](docs/history/llmtool-agent-policy-legacy-20261020.md) for provenance and [ai-team-delivery](skills/ai-team-delivery/SKILL.md) for normative gates.
