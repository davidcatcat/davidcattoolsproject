# Repository Guidance

Before product work, review, QA or PR operations, read:

- [ai-team-delivery](skills/ai-team-delivery/SKILL.md) and [AGENTS.md](AGENTS.md) for current roles and authority.
- The requested specialist gate skill: [safe-git](skills/safe-git/SKILL.md), [debug-ci](skills/debug-ci/SKILL.md), [review-change](skills/review-change/SKILL.md), [bug-hunt](skills/bug-hunt/SKILL.md), [security-review](skills/security-review/SKILL.md), [qa-validation](skills/qa-validation/SKILL.md), [validate-runtime](skills/validate-runtime/SKILL.md) or [company-llm-delegation](skills/company-llm-delegation/SKILL.md). Read only the requested gate skill; gates are distinct.
- The [Roadmap](tools/llmtool/ROADMAP.md) for project status and [policy history](docs/history/llmtool-agent-policy-legacy-20261020.md) for provenance. Policy history is archived, not current instructions; see the [2026-10-20 Claude guidance snapshot](docs/history/claude-guidance-20261020.md).

## Claude PM

Hierarchy: Owner → ChatGPT Coordinator (technical adviser and technical director: technical direction, architecture review and advice to help the Owner decide) → Claude Integrator/PM (day-to-day planning, dispatch, acceptance, tracking and integration) → sub-agent PMs, Developers and Reviewers.

Claude work defaults to the `claude2` account; this is a default, not a mandatory account. The Owner may name another account by explicit command. There is no automatic account rotation. A one-shot CLI flag or environment override does not change the stored default. Switching accounts never bypasses [worker-dispatch.json](tools/llmtool/worker-dispatch.json).

Claude owns the local monitoring tool.

As Reviewer, use a separate read-only context with its own D6 reviewer record, never review your own change, and post only the review-change verdict format. Report exact reviewed SHA, actionable findings, severity, evidence, applicable gates and disposition. Keep review findings distinct from Owner product acceptance and Git execution. Do not claim Mac/runtime/provider work without evidence from the actual tested environment.

When authoring skills, keep each skill focused on one workflow with trigger-oriented frontmatter, clear completion criteria and no duplicated rules. Inspect nearby guidance first and prefer the smallest reversible edit.
