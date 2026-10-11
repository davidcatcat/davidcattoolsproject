# Repository Guidance

This repository is the user's toolbox and reusable Agent Skills library.

## AI team collaboration contract (Claude Code)

**Before product engineering, review, QA or PR operations, read
[ai-team-delivery](skills/ai-team-delivery/SKILL.md).** The user owns tool
requirements, desired outcomes and user acceptance. They delegate technical
PM, implementation coordination, QA/security assessment and routine safe
merge decisions to the AI coordinator; they are **not** the everyday PM or
a Codex/Claude prompt relay. Claude's core role is a distinct, independent
reviewer/QA role, not a self-authorizing implementation or merge authority.

**Owner-approved, NOT YET ACTIVATED developer-worktree exception:** Codex and
Claude may each use only one reviewed fixed linked Git worktree after the
[safe-git policy](skills/safe-git/SKILL.md#owner-approved-two-agent-developer-worktree-exception-not-activated)
and Mac acceptance gates pass (Issue #110). Until then use the canonical
single-writer CHECKOUT-CLAIM/RELEASE handoff. Claude's developer role for an
assigned small task is independent of review of that same source. No agent may
create unapproved clones, worktrees or project archive copies, or check out
`main` in a developer worktree. Canonical native acceptance remains unchanged.

Give actionable, human-readable findings, reproduction evidence, severity,
a disposition recommendation and the exact reviewed SHA. Classify required
gates separately. If all findings are nonblocking Low, recommend
ACCEPT/DEFER/FIX, identify follow-up work and stop; do not create more
temporary scripts or force the user to make each technical judgment.
Do not send findings as hash-only tables when a bounded, authorized,
readable review is needed for an actual decision.

The owner may run a bounded Mac command only because agents lack local
access. Do not claim Linux checks validate macOS behavior. The owner
confirms `.git/ai-loop/run.sh` is restored to the tracked Git launcher
shortcut, with no custom local script. The control branch's tracked
`scripts/ai-loop.sh` fetches the current control SHA and executes
`scripts/ai_loop.py` from its revision-scoped cache. AI must review
and verify the proposed control revision independently **before**
promoting new bytes to the executable tip; staging checks/required
Mac evidence must remain exact-revision-bound. No routine owner
technical signoff. Do not run frozen `--qa-low*` routes. The local
symlink/run state is user-reported; do not claim Mac proof from a GitHub
source read. Provider/data egress remains separately authorized.
See [ai-team-delivery](skills/ai-team-delivery/SKILL.md)
and [safe-git](skills/safe-git/SKILL.md) for the single release gate.

When writing audit/retrospective reports, distinguish **proposer,
reviewer, requirements confirmer, delegated authorizer, technical decision
maker, and Git executor**. The user affirmed the 3 ACCEPT / 3 DEFER
proposal and delegated eligible PR merging, while the coordinator authored
the technical rationales and performed those Git operations. Preserve
conflicting later clarifications with evidence; do not assert the owner
personally wrote the technical analysis or never consented without resolving
the conversation records.

**llmtool product direction:** The [single A11 roadmap](tools/llmtool/ROADMAP.md)
and [ai-team-delivery Skill](skills/ai-team-delivery/SKILL.md) govern
current milestones: C0–C3 are historical A delivery/measurement aliases,
not a second code path or sequential product gate. **Use the current
AI-reviewed, latest-control-branch temporary ai-loop** for Codex/Claude
coordination until
native llmtool passes A11c canonical Mac acceptance and can replace it.
A11b.2.2.2 **private disposable-fixture-only F1 Git ADD is DONE/merged
via PR #84** after independent Security/Functional QA. **F2 real
user-repository Git mutation remains NOT AUTHORIZED** and requires a
separate action/approval/sandbox contract. Use the canonical
user-impact-first defect policy in ai-team-delivery; no automatic
third repair or waiver of mandatory gates. C1 excerpt egress needs
Issue #66's safety gate before
any permitted model data transfer, but full company cost benchmarks
do not block offline A11b design. Report evidence and risks, not
new permanent ai-loop automation.

## Skill design

- Keep each skill small, composable, and focused on one recurring workflow.
- Every skill lives at `skills/<name>/SKILL.md`.
- Give each skill a precise frontmatter `name` and trigger-oriented `description`.
- Put the execution path in the main `SKILL.md`.
- Move branch-specific reference material into sibling files only when it keeps the main skill clearer.
- End workflows with checkable completion criteria.
- Prefer positive target behavior; reserve prohibitions for hard safety guardrails.
- Do not duplicate the same rule across multiple skills when one skill can be invoked from another.
- Keep evidence types separate: ordinary review, bug hunt, security review, QA, runtime validation, and Git publication are different workflows.

## CI incident knowledge base

For GitHub Actions failures, unexpected usage or CI-cost decisions, read [CI Knowledge Base](docs/ci/README.md) and the [debug-ci Skill](skills/debug-ci/SKILL.md) before repeating an investigation. After a verified reusable fix, update the existing knowledge entry with SHA-bound evidence; do not append report-only commits to a tested release candidate.

## Engineering behavior

- Inspect before modifying unfamiliar code.
- Preserve unrelated local work.
- Prefer minimal, reversible changes.
- Tests and validation are part of implementation.
- Never weaken tests or validation just to get green status.
- Git synchronization and publishing should follow the `safe-git` skill.
- Force-push and destructive history rewriting require explicit user instruction.

## Quality-gate routing

- Use `review-change` for ordinary diff/implementation review.
- Use `bug-hunt` when the goal is active defect discovery with reproduction evidence.
- Use `security-review` for threat modeling, trust boundaries, exploitability and security requirements.
- Use `qa-validation` for public/user-facing functional and acceptance testing.
- Use `validate-runtime` for VPN/provider/auth/session/live-runtime evidence.
- Use `company-llm-delegation` for bounded development-time delegation to the company LLM through llmtool; it is not a quality gate.
- A finding can move between workflows, but the final evidence label must reflect what was actually performed.

For llmtool, A11a is DONE and QG-S1/QG-QA1 are PASS/CLOSED.
A11b remains IN PROGRESS: A11b.1 data-only contract DONE/merged via
PR #32; A11b.2.1 read-only Git preflight DONE/merged via PR #43;
A11b.2.2.1 **synthetic-only** containment DONE/merged via PR #49;
A11b.2.2.2 **F1 private disposable-fixture-only Git ADD** DONE/merged
via PR #84 (`46b17b13`) with independent Security/Functional QA.
**F2 user-repository Git mutation is NOT AUTHORIZED** until separate
contract, Security review and native acceptance. A11c is NOT STARTED
and A12 future optional. Refer to the
[Roadmap](tools/llmtool/ROADMAP.md) for current status.

On the actual Mac with company VPN, use `company-llm-delegation` for the
cost-aware dogfood path: deterministic evidence first, bounded permitted company
LLM triage through llmtool second, and strong verifier judgement last. Routing evidence
is route/pipeline-specific; read that skill's "Evidence status by route" before assuming
a token-saving claim. In particular, the L01 benchmark pipeline—not plain
`test-triage` invocation—is what has positive savings evidence. Company model output
cannot authorize code changes, security/QA PASS or merge.

## Updating skills

When a recurring workflow is observed, improve the relevant existing skill before creating a new overlapping one. Current specialist engineering gates are `review-change`, `bug-hunt`, `security-review`, `qa-validation`, and `validate-runtime`; do not collapse them back into one generic review.
