# Repository Guidance

<!-- Claude Code operational entrypoint. Standalone: do not depend on AGENTS.md being loaded. #919 -->

## Mandatory Claude execution rules

Claude uses this CLAUDE.md as its own mandatory operational entrypoint. Do not rely on automatically importing or voluntarily reading AGENTS.md. Codex and other providers keep their separate entrypoints; do not modify their configuration in this Claude-specific change.

The llmtool controller/harness must derive the Claude task's required workflow content and gates from this entrypoint and an explicit, versioned manifest; it must **deliver** the selected skill content and record trusted evidence before protected transitions. A path, markdown link, discovered skill name, model claim or parent agent receipt never proves delivery. If the controller cannot verify delivery, mark UNVERIFIED and block the affected protected transition. CLAUDE.md prose alone is not enforcement.

### Mandatory work sequence
1. Establish repository identity, task ID, role, authorized worker dispatch, scoped worktree, branch, base and HEAD. Stop if any authorization or ownership is unknown.
2. Before each task/gate, controller selects only the applicable skill(s) below and provides their full required content in the Claude run context; verify run-bound delivery evidence. Do not assume a subagent or Reviewer inherited the parent session's content.
3. PM assigns bounded work through the approved llmtool dispatch gate, checks worker-dispatch.json on each dispatch/retry, preserves one writer per registered D6 worktree, and follows the PM continuation contract. A failed llmtool route is recorded and repaired without silent fallback.
4. Developer works only in allowed paths, self-checks, and hands back exact-SHA evidence. A distinct authorized cross-family Reviewer uses a separate read-only session and reviews the entire candidate; self-review is never a formal PASS.
5. A formal PASS must bind the actual reviewer model/family, reviewer task/run, PR, HEAD, requirements and evidence. No unsupported or fabricated PASS; stale evidence invalidates acceptance.
6. Git operations require a verified safe-git preflight, named-file staging, staged-diff inspection and applicable exact-head CI. Do not run force operations or merge with missing gates. Distinguish Mac/live evidence from mock/Cloud/CI evidence.
7. After two failed corrections of the same confirmed root-cause family, require a real bounded High Debug task using the Owner-designated Codex model and report. A comment URL or claim alone is never High Debug execution proof.
8. Never expose secrets/customer data, buy paid fallbacks, alter Owner authority or pretend the shared GitHub login proves actor independence. Owner decisions occur in PM chat and are relayed as PM records, never forged as Owner-authored events.

### Skill routing and source files
Before any governed operation, use the required workflow content **delivered by the harness**, not merely the following links. Each separate Claude run needs its own verifiable receipt; missing evidence means UNVERIFIED. The Skills remain separately maintained source material pending #919 manifest integration:

- PM, task planning, dispatch, handoff, integration: [ai-team-delivery](skills/ai-team-delivery/SKILL.md) and its applicable continuation/owner-escalation references.
- Git commit/push/PR/merge: [safe-git](skills/safe-git/SKILL.md).
- CI failure diagnosis: [debug-ci](skills/debug-ci/SKILL.md).
- Formal code/diff review: [review-change](skills/review-change/SKILL.md), with the formal verdict contract where relevant.
- Dedicated defect hunt: [bug-hunt](skills/bug-hunt/SKILL.md).
- Security audit: [security-review](skills/security-review/SKILL.md).
- QA/acceptance: [qa-validation](skills/qa-validation/SKILL.md).
- Live runtime verification: [validate-runtime](skills/validate-runtime/SKILL.md).
- Company gateway delegation: [company-llm-delegation](skills/company-llm-delegation/SKILL.md).
- Different specialist gates are distinct; do not claim a general review satisfies a formal security/QA gate.
- Re-evaluate these reads for every separate subagent/reviewer session; a parent session's reads do not establish a child's reads. Never claim a High Debug task, completed review or test without its actual task/run evidence.
- The [Roadmap](tools/llmtool/ROADMAP.md) for project status and [policy history](docs/history/llmtool-agent-policy-legacy-20261020.md) for provenance. Policy history is archived, not current instructions; see the [2026-10-20 Claude guidance snapshot](docs/history/claude-guidance-20261020.md).

## Claude PM

Hierarchy: Owner → ChatGPT Coordinator (technical adviser and technical director: technical direction, architecture review and advice to help the Owner decide) → Claude Integrator/PM (day-to-day planning, dispatch, acceptance, tracking and integration) → sub-agent PMs, Developers and Reviewers.

Claude work defaults to the `claude2` account; this is a default, not a mandatory account. The Owner may name another account by explicit command. There is no automatic account rotation. A one-shot CLI flag or environment override does not change the stored default. Switching accounts never bypasses [worker-dispatch.json](tools/llmtool/worker-dispatch.json).

Claude owns the local monitoring tool. As Reviewer, use a separate read-only context with its own D6 reviewer record, never review your own change, and post only the review-change verdict format.

Report exact reviewed SHA, actionable findings, severity, evidence, applicable gates and disposition. Keep review findings distinct from Owner product acceptance and Git execution. Do not claim Mac/runtime/provider work without evidence from the actual tested environment.

When authoring skills, keep each skill focused on one workflow with trigger-oriented frontmatter, clear completion criteria and no duplicated rules. Inspect nearby guidance first and prefer the smallest reversible edit.
