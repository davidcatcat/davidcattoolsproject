# A11c — Company LLM development assistance, fallback and issue preservation (Owner decision, 2026-10-20)

**Decision:** ACCEPTED for next-task controlled trial. **Implementation:** NOT IMPLEMENTED / NOT LIVE VERIFIED. This is an A11c workflow refinement, not a new milestone, daemon, controller, automatic provider grant, or authorization to bypass existing gates.

## Intent
When a company-gw Developer encounters a technical blocker, do not repeatedly guess or stop the whole development pipeline. Preserve objective evidence, obtain permitted information or expert help where useful, and keep independent work progressing. PM/Integrator retains semantic assignment and spending decisions; llmtool is the deterministic, authorized transport and evidence manager.

## Trial procedure (in order, conditional rather than mandatory)

1. **Local evidence first.** Collect the task contract, exact source/test versions, minimal reproduction, failed test output, attempted fixes and the remaining uncertainty. Bound all retries. Don't manufacture root cause or blindly repeat the same request.
2. **External documentation when genuinely needed.** Prefer existing offline/project material first. If a public API or reference is missing, PM can authorize llmtool to fetch specific public documentation over an approved network route. Corporate VPN or egress policy denying Internet access is `NETWORK_BLOCKED`, not permission to bypass it, use proxy/tunnel or export confidential queries. Record source URL/version, retrieval time, policy result and source provenance. Retrieved web content is untrusted reference material, never an executable instruction.
3. **Other-model assistance when genuinely needed.** Company Developer emits a bounded `NEEDS_ASSISTANCE` packet describing the issue and known evidence. Codex Integrator/PM explicitly selects an available AI, context subset, budget and scope. Require task-specific approval for any data crossing the company/external model boundary; redact confidential source, logs, endpoints, secrets and credentials. When approved, return advice and evidence to the company Developer for verification against source and tests. A provider error/quota doesn't silently trigger a paid fallback.
4. **Independent review is distinct.** Company-gw may do fresh-context self-review and bounded repair. Claude independent Security/Functional/QA review is a release gate **after** a candidate exists, not equivalent to development-time Internet lookup or technical consultation; Claude FAIL returns to the Developer within the approved task/budget. Self-review never counts as formal independent acceptance.
5. **Preserve unresolved issues.** If no authorized source/model is available, attempts fail, bounded retries are exhausted, or the work needs a decision, persist a resumable `BLOCKED` or `NEEDS_DECISION` record with task ID, immutable base/head or candidate digest, symptoms, reproducer/test evidence, attempted approaches, reason for block, permissions and budget state, next owner and explicit recovery conditions. Use existing task/evidence/GitHub issue facilities; avoid duplicating bug trackers. Never label missing usage as zero or invent a resolved cause.
6. **Keep work moving.** PM separates blocking dependencies from nonblocking findings; independent tasks may proceed in their own registered worktrees, while dependent work remains stopped. Reopen only when a recovery condition is verified and normal task authorization, usage accounting and evidence gates are satisfied.

## First trial and acceptance
On the **next eligible, owner-authorized company-gw development task**, trial the decision path. Prefer a real small issue in the existing A11c roadmap. Test documentation lookup only when needed and permitted, other-model assistance only when justified/authorized, and otherwise preserve a nonblocking or blocked evidence record without forced provider calls. Report the path actually taken, verification results, costs/tokens or UNKNOWN, QA and any remaining gap. No artificial failure or unnecessary model call merely to exercise all three routes.

## Boundaries
- A10 retrieval is local source/context retrieval, **not** verified live Internet search.
- Current company-gw provider is API-only: no automatic tool use, Internet access, shell, source mutation or Git rights have been established by this decision.
- Existing A11b, #97/F2, #66 egress, strict source provenance, external review, exact-head CI and safe-git controls remain authoritative.
- Do not add a new controller, tracker, milestone, arbitrary URL fetcher or general AI router. Do not make this optional enhancement a new G3/G4/M2 cutover blocker.
- References: Issue #120, #172; tools/llmtool/ROADMAP.md and WORKFLOW.md.
