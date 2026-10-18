# Claude context reference (moved from CLAUDE.md)

Moved verbatim from `CLAUDE.md` at main `cd7ee64` so the always-loaded file
stays short. Nothing here was rewritten. Two parts are dated: the worktree
paragraph predates the G7 merge (PR #126, `83be9f6`), and the milestone
snapshot predates A11c work. [ROADMAP](../../tools/llmtool/ROADMAP.md) and
[safe-git](../../skills/safe-git/SKILL.md) are authoritative for current state.

## AI team collaboration contract (Claude Code)

**Before product engineering, review, QA or PR operations, read
[ai-team-delivery](../../skills/ai-team-delivery/SKILL.md).** The user owns tool
requirements, desired outcomes and user acceptance. They delegate technical
PM, implementation coordination, QA/security assessment and routine safe
merge decisions to the AI coordinator; they are **not** the everyday PM or
a Codex/Claude prompt relay. Claude's core role is a distinct, independent
reviewer/QA role, not a self-authorizing implementation or merge authority.

**Owner-approved two-fixed-worktree exception (G7 activation is merge-gated):**
Codex and Claude may each use only their one reviewed fixed linked Git worktree
under the [single normative safe-git policy](../../skills/safe-git/SKILL.md#owner-approved-two-agent-developer-worktree-exception-not-activated).
General developer use becomes **ACTIVE only after** the independently reviewed
G7 activation-status PR is merged into `main`; while the PR is OPEN/Draft,
continue single canonical CHECKOUT-CLAIM/RELEASE for non-pilot work.
After activation, Claude's developer CLI must start with CWD set to its
registered Claude root and use a unique owned task branch, clean handoff,
pinned preflight and its own verified venv. A Claude reviewer remains separate
from development of the same source, with no unclaimed Git fetch in canonical.
Unknown writer, crash, dirty/ignored state, wrong-root import or shared-ref
conflict means STOP/preserve and fall back to canonical single-writer handoff.
No unapproved clone, extra worktree or project archive copy, no `main`
checkout in developer worktrees, no llmtool F2 mutation or automated cleanup
of historical processes. Canonical native F1 acceptance remains unchanged.

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
See [ai-team-delivery](../../skills/ai-team-delivery/SKILL.md)
and [safe-git](../../skills/safe-git/SKILL.md) for the single release gate.

When writing audit/retrospective reports, distinguish **proposer,
reviewer, requirements confirmer, delegated authorizer, technical decision
maker, and Git executor**. The user affirmed the 3 ACCEPT / 3 DEFER
proposal and delegated eligible PR merging, while the coordinator authored
the technical rationales and performed those Git operations. Preserve
conflicting later clarifications with evidence; do not assert the owner
personally wrote the technical analysis or never consented without resolving
the conversation records.

**llmtool product direction:** The [single A11 roadmap](../../tools/llmtool/ROADMAP.md)
and [ai-team-delivery Skill](../../skills/ai-team-delivery/SKILL.md) govern
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

## llmtool milestone snapshot (dated; see ROADMAP)

For llmtool, A11a is DONE and QG-S1/QG-QA1 are PASS/CLOSED.
A11b remains IN PROGRESS: A11b.1 data-only contract DONE/merged via
PR #32; A11b.2.1 read-only Git preflight DONE/merged via PR #43;
A11b.2.2.1 **synthetic-only** containment DONE/merged via PR #49;
A11b.2.2.2 **F1 private disposable-fixture-only Git ADD** DONE/merged
via PR #84 (`46b17b13`) with independent Security/Functional QA.
**F2 user-repository Git mutation is NOT AUTHORIZED** until separate
contract, Security review and native acceptance. A11c is NOT STARTED
and A12 future optional. Refer to the
[Roadmap](../../tools/llmtool/ROADMAP.md) for current status.

On the actual Mac with company VPN, use `company-llm-delegation` for the
cost-aware dogfood path: deterministic evidence first, bounded permitted company
LLM triage through llmtool second, and strong verifier judgement last. Routing evidence
is route/pipeline-specific; read that skill's "Evidence status by route" before assuming
a token-saving claim. In particular, the L01 benchmark pipeline—not plain
`test-triage` invocation—is what has positive savings evidence. Company model output
cannot authorize code changes, security/QA PASS or merge.
