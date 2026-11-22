# Repository Guidance (Claude)

@AGENTS.md

Claude's entrypoint. The line above imports [AGENTS.md](AGENTS.md), the shared rules for every AI (Owner decision E6, [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502)). This file adds only what is specific to Claude; where a rule below differs from AGENTS.md, the rule below wins for Claude. Skills are single-source under `.claude/skills/`; this file routes to them and does not copy them. Roles: PM, Developer, Reviewer. #919.

**Status marks.** `[NOT LIVE]` marks harness behaviour that is the #919 target and is not implemented today. Until it is live, the text rules bind by themselves, and the agent reads the routed skill itself. Never claim a harness check that has not run. #1502 adds `現行` (current behaviour), `待實作（WP-x）` (decided, ships with that WP) and `待後續` (decided, out of scope).

## Harness skill delivery (partly live)

- Live (#919, #1502 WP-F): Claude Developer, AGY and OpenCode runs get the `main`-pinned common-core and Developer cards, and llmtool records `CONTROLLER_DELIVERED` evidence. Formal Reviewer and AI Debug prompts carry `main`-pinned role cards whose digest is bound into the review claim or the Debug entry (#1822, #1877, #1878). `gw-agent` runs record `NOT_DELIVERED`. [NOT LIVE]: delivery to sub-agent and PM runs (待後續), and missing evidence blocking protected transitions (待實作（WP-F）). A link, a skill name, a model claim or a parent's receipt is not delivery. This prose alone is not enforcement.
- Hooks are defence in depth; the real gates run at the point of action: the dispatch gate, `task complete` and merge checks.

## Claude accounts and tools

- Claude work defaults to the `claude2` account; the Owner may name another account by explicit command. No automatic rotation. A one-shot CLI flag or env override does not change the stored default. Switching accounts never bypasses worker dispatch.
- Claude owns the local monitoring tool (live availability: [monitor](scripts/llmtool_monitor.py)). Static inventories never show runner health.

## PM rules (the PM is Claude)

- Read scope (every Developer route; [#2503](https://github.com/davidcatcat/davidcattoolsproject/issues/2503)): before every Developer dispatch or repair, go through `goal`, every `acceptance` item and, for a repair, every finding of the review verdict. List each repository file they name or that the Developer must read to do the work, and add any missing file to the spec `read` list. A Developer reads only `read`; when a needed file is missing it stops (AGY answers BLOCKED) or works from incomplete code. Evidence [#2494](https://github.com/davidcatcat/davidcattoolsproject/issues/2494): the #2425 repair (Task 17917aa9) needed `agy.py`, `gw_agent.py`, `review_result.py` and `task_submission_cli.py` outside `read`; with the original `read` 0 of 6 runs finished, with those files added 4 of 4 did.
- Quota: a quota FAIL blocks dispatch to that worker for 30 minutes or until a sooner provider reset; when the Owner reports recovery, relay it and run `llmtool worker-quota-recovered <worker> --owner-relay <URL>` ([details](.claude/skills/ai-team-delivery/pm-continuation-reference.md#quota-and-reviewer-continuation)). PAUSED still wins.
- Route notes in addition to AGENTS.md:
  - OpenCode: the Owner re-enabled dispatch (https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6560737617).
  - AGY: the AGY model comes from the configuration, and this file names none (Owner decision 2026-11-22, relayed https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-7066719380). Pause after two consecutive route failures. Require a formal Reviewer from a different model family (Owner #289: https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6624144132).
  - Home LLM `gw-agent`: Owner decisions [company `gw`](https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6530593665) and [`gw-agent`](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6760670167).

## Review and Git (in addition to AGENTS.md)

- A formal PASS binds the actual reviewer model/family, reviewer task/run, PR, exact HEAD, requirements and evidence. Never fabricate a PASS; stale evidence invalidates it.
- Reviewer: post only the review-change verdict format; report exact reviewed SHA, actionable findings, severity, evidence, applicable gates and disposition. Keep review findings distinct from Owner product acceptance and Git execution.
- Different specialist gates (security, QA, runtime, bug-hunt) are distinct; a general review satisfies none of them.
- `llmtool task advance` may commit, push, open a PR and merge one Task's change in this repository, under all gates, one step at a time ([Owner S4, #1019](https://github.com/davidcatcat/davidcattoolsproject/issues/1019#issuecomment-6665895546)).
- Use [safe-git](.claude/skills/safe-git/SKILL.md): verified preflight, named-file staging, staged-diff inspection before commit or publication.

## D6, privacy and evidence

- One registered D6 worktree has one writer. Verify identity and task ownership before any write; STOP if identity or ownership is unknown.
- Do not send secrets, personal/customer data or restricted logs to any provider.
- Mac or runtime behaviour needs evidence from the actual Mac and tested revision; distinguish it from mock, Cloud and CI evidence. Never claim a task, review, test or High Debug without its run evidence.

## Skill routing

Pick by task; one skill per gate. For product work, review, QA and publication, apply [ai-team-delivery](.claude/skills/ai-team-delivery/SKILL.md), [pm-continuation](.claude/skills/ai-team-delivery/pm-continuation.md) and [owner-escalation](.claude/skills/ai-team-delivery/owner-escalation.md). For formal review, use [review-change](.claude/skills/review-change/SKILL.md). A skill's reference to `AGENTS.md` means the imported AGENTS.md rule as amended by this file; this file wins if the texts conflict.

| Work | Skill |
|---|---|
| Git commit, push, PR, merge | [safe-git](.claude/skills/safe-git/SKILL.md) |
| CI failure ([CI KB](docs/ci/README.md)) | [debug-ci](.claude/skills/debug-ci/SKILL.md) |
| Formal review | [review-change](.claude/skills/review-change/SKILL.md) |
| Defect hunt | [bug-hunt](.claude/skills/bug-hunt/SKILL.md) |
| Security audit | [security-review](.claude/skills/security-review/SKILL.md) |
| QA, acceptance | [qa-validation](.claude/skills/qa-validation/SKILL.md) |
| Live runtime | [validate-runtime](.claude/skills/validate-runtime/SKILL.md) |
| Company gateway | [company-llm-delegation](.claude/skills/company-llm-delegation/SKILL.md) |
| Implement a feature or bug fix from an agreed requirement through tests and review. Use when the user asks to build, add, change, or fix code and expects a working result rather than only a plan. | [implement-feature](.claude/skills/implement-feature/SKILL.md) |
| Inspect a repository before making changes. Use when entering an unfamiliar codebase, planning a change, investigating architecture, or when the user says to read README, schema, existing examples, or git history first. | [inspect-repo](.claude/skills/inspect-repo/SKILL.md) |
| Verify JSON schemas, path constraints, role constants, external IDs, and evidence labels before publishing technical reports, runbooks, or handoff artifacts. Use when drafting or reviewing reports, HostPin/contract examples, or readiness handoffs. | [artifact-verification](.claude/skills/artifact-verification/SKILL.md) |
| Start a new software project with a small, testable plan and safe repository setup. Use when the user wants to create a new tool, app, service, CLI, or repository from an idea. | [start-project](.claude/skills/start-project/SKILL.md) |
| Use when an llmtool Developer Task is routed to AGY (Antigravity CLI `agy`) for direct execution in a registered D6 worktree. Covers dispatch authorization, model and quota preflight, scope checks and hand-back evidence. | [agy-direct-developer](.claude/skills/agy-direct-developer/SKILL.md) |

PM index: [PM Quick Reference](tools/llmtool/PM_QUICK_REFERENCE.md) (its linked sources win). Status: [Roadmap](tools/llmtool/ROADMAP.md). [Policy history](docs/history/llmtool-agent-policy-legacy-20261020.md) is archived provenance, not current instructions; see the [2026-10-20 snapshot](docs/history/claude-guidance-20261020.md). Each separate sub-agent or Reviewer run applies only the instructions and skills in its own context; a parent's reads do not establish that run's compliance. Authoring skills: one workflow per skill, trigger-oriented frontmatter, clear completion criteria, no duplicated rules, smallest reversible edit.
