# ai-loop final acceptance automation — transitional design and handoff contract

Status: **TRANSITIONAL PROPOSAL / NOT IMPLEMENTED / NOT A SIGN-OFF** (2026-10-09).
## Priority correction — ai-loop is temporary; llmtool self-development is the product goal (2026-10-09)

**Authoritative product direction:** `ai-loop` is a temporary orchestration
bootstrap, **not** a second long-term agent platform. Its current
Codex→Claude ordinary review→Mac tests→bounded repair loop exists only to help
complete and verify `llmtool`. The goal is to **cut over** this workflow to
`llmtool` controlling supported Codex CLI and Claude CLI execution, with
company LLM delegated **only where quality-preserving, measured premium-token
reduction has been established**. Lower costs and lower user handoff effort,
not agent-count or feature count, are the product outcomes.

**Do not build a permanent full-featured `ai-loop` review/orchestration
stack before llmtool's native equivalent.** This document's G1–G5 are
preserved as a proposed quality/safety contract, **not a mandatory near-term
implementation queue for the temporary script**. Prioritize existing runner
stability and narrowly necessary safety fixes. Move standalone formal
Security/Functional QA automation, model routing, reporting and reusable
evidence contracts into the llmtool-native A11b/A11c plan, rather than
duplicating their long-term implementation in `scripts/ai_loop.py`.
A temporary runner cannot waive independent review for its own changes.

### Earliest truthful availability

- **NOW — bounded development dogfood:** A2.4 Claude CLI and A2.5 Codex
  CLI providers, A9 text orchestration, A10 retrieval, and the configured
  internal `openai-compatible` company provider are already in main.
  Their CLI provider surfaces are **pure-text**, with no repo/tool/edit
  capability. Use deterministic repo/CI/test helpers first, then optionally
  company LLM for permitted bounded triage, and external strong models only
  where actual judgement is needed. This is **not** autonomous llmtool
  coding or proven general token savings.
- **EARLY NATIVE SELF-DEVELOPMENT MVP:** requires A11b's authorized typed
  sandboxed actions, patch/test execution, approval, process containment,
  evidence/audit, bounded repair, and independent negative/security tests.
  Git ADD alone (including PR #49 synthetic containment) does **not**
  provide this. Make the earliest reviewed end-to-end development loop a
  deliberate vertical slice; do not wait for optional A12 Telegram or
  unrelated general-purpose features.
- **CUTOVER:** A11c route-by-task integration and actual Mac repository
  acceptance prove llmtool can perform a scoped change→tests→independent
  review→repair→PR handoff safely. Keep `ai-loop` as fallback until
  real-task parity, no regressions and measurable token/latency/quality
  results; then freeze/deprecate its duplicate orchestration.

### Cost and quality release gates

1. **0-token lane first:** deterministic test selection, Git/CI snapshot,
   failure-ID extraction, path filtering and diff summaries. Avoid
   resending identical long logs and provider prompts.
2. **Company lane only on validated task routes:** the previously measured
   L01 large-test-output research pipeline saved **39.82% / 37.55%**
   Codex tokens in two controlled runs but is **not ordinary production
   `test-triage`**. Before real `pytest`/`unittest` dispatch, build
   and test a deterministic coverage+bounded excerpt selector and pass
   the route's Company-only Phase 0 (quality/reliability/usage).
3. **Explicit counterexample:** PR #36's small B01–B03 pilot was
   **−104.46% Codex savings**, i.e. Codex token usage **increased**
   from 54,849 to 112,144 across its three quality-approved pairs;
   repeated base-context/tool-call overhead defeated cheap pre-triage.
   Do not route trivial inputs through the company model. Other
   `log-triage`/`context-pack` routes remain experimental.
4. **Cost metrics:** report Codex/Claude premium token usage where
   available, company LLM usage, cache/reasoning breakout without
   double counting, wall time, reviewer/repair retries, source accuracy
   and acceptance quality. Unknown token totals remain unknown; token
   volume across different tokenizers is **not** monetary saving.
   No release passes merely because one model's token counter fell.
5. **Stop rules:** keep strict authorization and independent Security/QA;
   do not downgrade checks to accelerate launch or save tokens.
   Budget per task, versioned task contract, no automatic merge, and
   objective escalation to the user are mandatory.

**Priority order:** finish/merge PR #49 only after its own gates and
authorization; harden any essential temporary runner bugs without
productizing it; deliver llmtool's native self-development vertical
slice through reviewed A11b execution + A11c integration, while using
existing llmtool development dogfood now. The future QA automation
contract below must be carried over to llmtool, not presumed to be an
urgent permanent `ai-loop` feature.

---


Change home: [PR #45](https://github.com/davidcatcat/davidcattoolsproject/pull/45).
Reviewed evidence: [PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49),
[security re-review](https://github.com/davidcatcat/davidcattoolsproject/blob/a26cd2a50d85737fb9e9bc6649bebacf33117f3c/reports/llmtool_pr49_independent_rereview_20261009.md),
[Codex/Claude report assessment](https://github.com/davidcatcat/davidcattoolsproject/blob/a26cd2a50d85737fb9e9bc6649bebacf33117f3c/reports/llmtool_pr49_codex_reports_review_20261009.md),
[Codex independent functional QA](https://github.com/davidcatcat/davidcattoolsproject/pull/49#issuecomment-6064690433),
and [coordinator workflow requirement](https://github.com/davidcatcat/davidcattoolsproject/pull/45#issuecomment-6064640880).

## Purpose and user contract

The user supplies the engineering goal and makes **only material decisions**.
The coordinator creates/publishes a **single exact-SHA Git control task**. On the canonical
Mac, the operator runs one entry point (`bash scripts/ai-loop.sh --check` as a
non-model preflight, followed by the runner invocation after a PASS). The runner
coordinates scoped Codex development, Claude ordinary review, deterministic tests,
bounded repair and publication. **No routine manual copy/paste of prompts between
Codex and Claude.** A STOP means reviewable evidence and one concise user decision,
not blind retries or a request to relay prompts.

Current `scripts/ai_loop.py` supports `implement_new_pr` / `review_existing_pr`
and **ordinary** Claude code review. It does **not** implement formal independent
Security Review or independent Functional QA orchestration. A passing ordinary review
**cannot** be relabeled as those gates. A signed-off feature PR does not certify
the automation runner itself.

## Separate read-only final-acceptance phase (proposed)

A future explicit task kind (provisional name `final_acceptance_review`) MUST:

1. Pin repository/PR number, base SHA, exact PR HEAD, reviewed source/test
   SHA-256 inventory, required evidence manifest, frozen criteria, and each
   reviewer role. Reject stale, changed, partial, off-branch, or ambiguous input
   **before** a model is invoked or a report published. Existing task schema
   must be versioned/tested before a new kind is accepted.
2. Use **distinct tasks/gates** for Security Review (independent, read-only,
   e.g. Claude) and Functional QA (independent, e.g. Codex in a separately
   reviewed safe read-only/sandboxed execution context). Keep normal Claude
   code review separate from formal Security / Functional QA / Runtime / merge
   decisions. Reviewer session/process provenance and separation from the
   author must be recorded; same GitHub publishing account alone is **not**
   evidence of independence.
3. Give reviewers exact SHA, cumulative diff/source, original machine-readable
   evidence and **pre-registered test matrix**. Author-provided summaries,
   verdicts and handoff reports are untrusted claims, not authoritative
   evidence or reviewer instructions. Reviewers must independently inspect
   implementation and challenge false PASS, insufficient coverage and
   contradictory evidence. Do not simply ask a second model to endorse a
   first model's summary.
4. Return bounded structured `PASS | BLOCKED | NEEDS_DECISION`, gate type,
   reviewer provenance, exact reviewed SHA, tests actually observed, file/line
   findings, severity, reproduction, scope limitations and explicit unresolved
   risks. Treat malformed, absent, mismatched, timed-out, over-budget, or
   source-drifting results as **NOT PASSED**.
5. Anchor native evidence to tested source hashes, runner/controller version,
   host metadata, command/exit, and provenance **independent of the runner
   under review**. A runner may validate report format/hash but MUST NOT
   certify its own correct behavior solely with its self-produced PASS. At
   least one independent execution or independently verified external record
   must challenge the runner's test/identity/absence observations. Separate
   synthetic/mocked assertions from actual Mac kernel/process observations.
6. Preserve original evidence (including bounded failed attempts) durably
   after secret/path minimization; a `/private/tmp` digest without retained
   bytes is not a durable audit artifact. Pin both content digest and storage
   location, never publish raw credentials/provider streams. Report-only
   commits that do not change tested inputs can reuse correctly bound
   runtime evidence; source, tests, controller or required trust-boundary
   changes invalidate affected gates. Re-read PR HEAD before publishing and
   before merge consideration.
7. Make report publication **comment-only / allowlisted artifact only** under
   a least-privilege token. Explicitly prohibit automatic approve/merge,
   permissions escalation, force push, source edits, arbitrary shell or Git
   mutation, silent test relaxation and unbounded network/provider calls.
   Pre-publication sanitization must reject secrets, private paths, arbitrary
   model output, oversize evidence and unsafe metadata. One designated
   coordinator owns any authorized Git changes, never a reviewer.
8. On BLOCKED/NEEDS_DECISION, preserve all evidence and stop. The coordinator
   may authorize a **new scoped remediation task** with fixed budget and
   refreshed baseline. Then repeat all affected security/QA/native/CI gates;
   no inferred PASS, no stale one-use recovery reuse, no automatic next
   milestone.

The normal development loop remains scoped Codex implement/repair + read-only
Claude *ordinary* review + deterministic tests. Formal Security and QA gates
run **after** the candidate is frozen. Neither running a review nor publishing
one is permission to execute real Git ADD or to merge.

## Independent failure-mode tests for the future runner

Required **negative** cases (not merely unit happy paths):

- Wrong repo, PR, base or head; HEAD changes between preflight, reviewer invocation,
  evidence inspection and comment publication; altered tested SHA or controller.
- Empty/malformed reviewer output, false PASS, skipped or absent mandatory
  matrix row, <20 SIGKILL repetitions, test invocation with missing PYTHONPATH,
  and disagreement between native JSON, terminal log and source hashes.
- Runner-author self-attestation masquerading as independent approval; a
  malicious author report instructing reviewer to approve; same publishing
  identity mistaken for separate reviewers.
- Lost/truncated private evidence; post-cleanup process identity unknown;
  incompatible Mac/runtime; failed non-reaping/zombie/retained-ownership probe.
- Untrusted report content including tokens, home paths, source snippets,
  unbounded output, symlinks or an over-privileged comment-publishing token.
- Quota/auth/timeout interruptions; two-repeat stall, exhaustion, replayed
  one-use recovery, a no-op repair and interrupted publication.
- Review-only mode attempts to touch tracked source, index, branch, PR approval,
  product Git mutation or merge. **Fail closed** before making such changes.

Use fixed dummy fixtures, an independent negative-challenge runner, and a
separate security review of PR #45 itself (workflow permissions, token scope,
push/merge reachability, process/FD correctness, evidence forgery and privacy).
Tests are not claimed as implemented by this document.

## Rollout and dependencies

- **G0 — specification (this document):** document workflow, trust boundaries,
  criteria, failure policy and user/operator contract. No runner behavior change.
- **G1 — runner implementation + offline adversarial tests:** design/review
  schema, read-only gate role separation, evaluator and report publisher.
- **G2 — Mac integration:** exercise real runner with a small non-mutating
  acceptance task; verify provenance and independent trust root, CI, sandbox,
  failure/decision outcomes and replay protections.
- **G3 — external security + formal QA for PR #45:** independent sources and
  reviewers, exact code HEAD, findings and remediation/retest.
- **G4 — controlled activation:** only after G1–G3 PASS, safely retire the
  existing `76644` single-use recovery as **historical** with immutable
  provenance, and publish a **fresh** task.md/task.json on the control branch.
  Never repurpose, resume or silently reset the old `76644` contract.
- **G5 — merge decision:** user/coordinator decides with exact-head CI and
  required gates. No auto-merge and no automatic A11b.2.2.2.

**PR #49 is a separate feature PR:** independent security re-review PASS and
functional QA PASS have already been reported; Mac 14/14 and 20+20 PASS are
source-bound. The remaining decision is exact-HEAD CI/coordinator merge
preflight, **not another manual prompt handoff**. Low findings remain open:
S49-3/QA49-N2 directory FD leak, QA49-N1 permanent natural leader-exit
regression, test-only fault hooks before product integration, and uv runtime
compatibility. None is silently resolved.

This is an engineering workflow proposal, not permission for PR #45 to merge,
for code execution on the user's Mac, or for real product Git ADD.
