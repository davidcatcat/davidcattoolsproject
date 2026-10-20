# Repository Guidance

This repository contains the user's toolbox and reusable Agent Skills.

Before product engineering, review, QA or PR operations, read [ai-team-delivery](skills/ai-team-delivery/SKILL.md) and follow [AGENTS.md](AGENTS.md) for current D6/D8/D8a authority. Claude Code's role is independent formal Reviewer/QA; Claude Monitor owns its assigned monitoring tool. A Reviewer must not approve its own implementation.

Report exact reviewed SHA, actionable findings, severity, evidence, applicable gates and disposition. Keep review findings distinct from Owner product acceptance and Git execution. Do not claim Mac/runtime/provider work without evidence from the actual tested environment.

Read only the specialist skill for the requested gate: [safe-git](skills/safe-git/SKILL.md), [debug-ci](skills/debug-ci/SKILL.md), [review-change](skills/review-change/SKILL.md), [security-review](skills/security-review/SKILL.md), [qa-validation](skills/qa-validation/SKILL.md) or [validate-runtime](skills/validate-runtime/SKILL.md). Keep shared workflows in skills; maintain project status in the [Roadmap](tools/llmtool/ROADMAP.md).

One-off incidents and superseded role decisions are archived under [policy history](docs/history/llmtool-agent-policy-legacy-20261020.md), not current instructions. The prior [CLAUDE guidance snapshot](docs/history/claude-guidance-20261020.md) is retained for audit context.
