---
name: implement-feature
description: Implement a feature or bug fix from an agreed requirement through tests and review. Use when the user asks to build, add, change, or fix code and expects a working result rather than only a plan.
---

# Implement Feature

For the requirements-owner/AI-team responsibility split, start with
[ai-team-delivery](../ai-team-delivery/SKILL.md). The Developer implements and
tests the approved product goal. The Claude PM dispatches the work, hands it off
for independent review, and merges eligible changes. The owner may be the Mac
command operator but is not the technical PM or agent prompt courier.

Deliver a working change, not just code generation.

## Process

1. Use `inspect-repo` to understand the relevant area.
2. Restate the requested behavior and the smallest acceptance criteria.
3. Identify the public seam where behavior should be verified.
4. Implement in small vertical slices.
5. Add or update tests through public interfaces. For runtime/execution features, derive
   failure-path acceptance cases from the provider/runtime checklist in
   [review-change](../review-change/SKILL.md); keep the safety review rules there.
6. Run the narrow tests after each meaningful slice.
7. Run required checks for the changed artifacts. Documentation-only work uses documentation
   validation (links, examples, schema/status consistency); run application tests/builds when
   production behavior or package resources change, or repository instructions require them.
8. Manually exercise the user-facing flow when practical.
9. Use `review-change` on the final diff.
10. Run any additional gate explicitly required by the Task Contract/milestone: `bug-hunt`, `security-review`, `qa-validation`, or `validate-runtime`. Do not assume ordinary review satisfies those gates.
11. Use `safe-git` only when the user asks to commit, synchronize, or push.

## Rules

- Match existing architecture and naming unless the requirement demands a change.
- Prefer the smallest correct implementation.
- Do not add speculative features.
- Do not weaken tests to obtain a green build.
- Follow the [test data rule](../ai-team-delivery/SKILL.md#test-data-rule-owner-763-a-2026-11-08) (Owner #763 A) in every test you write.
- Preserve unrelated work.
- Provider or agent integration must not treat technical capability as permission to execute actions.
- Do not add execution before the affected workflow has a diagnosable failure path. New mutations should emit enough structured diagnostics/audit evidence to identify what ran, what failed, and why without exposing secrets.

## Cloud and Mac verification

For llmtool work in Codex Cloud or on the actual Mac, follow
[the development workflow](../../tools/llmtool/WORKFLOW.md). On Mac + company VPN,
use [company-llm-delegation](../company-llm-delegation/SKILL.md) when bounded
development-time triage would help; do not duplicate its routing/data rules here.
Classify required checks before implementation; complete Cloud-capable work and
publish an exact-revision handoff for Mac-only checks. Treat implementation complete,
required verification complete and merge ready as separate claims.

## Completion criteria

- Requested behavior works.
- Relevant tests cover the behavior.
- Required quality gates pass.
- Final diff contains no unrelated changes.
- Known limitations and manual verification steps are reported.
- Required specialist gates are either complete or explicitly pending/blocked; implementation complete is not the same claim as security-reviewed, QA-passed, or merge-ready.
