# llmtool native self-development MVP — cost-first release plan

> **Historical C-labelled delivery proposal, not an additional milestone system.** The
> authoritative implementation roadmap is [A11 in ROADMAP.md](ROADMAP.md).
> On 2026-10-09 the requirements owner confirmed **C0–C3 are consolidated
> back into the original A architecture and gates**. This file preserves the
> original C terminology for traceability; all new product task contracts and
> progress status use **A11b/A11c**, with C only as a legacy measurement alias.
> **ai-loop remains the temporary SHA-approved Codex/Claude coordination runner
> until native llmtool passes real Mac acceptance**, not a second product.
> No existing permission, security, QA, Mac or provider egress gate is waived.

Status: **PROPOSED / DESIGN ONLY / NO IMPLEMENTATION AUTHORITY** (2026-10-09).
**Status distinction (2026-10-09 update):** This document remains the proposed **native execution and C1b/C1c measurement plan**, not permission to enable provider/data egress or C2 execution. Its **C1a deterministic local unittest/pytest subset is already implemented** by merged [PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55); JUnit XML remains unsupported. The currently prepared C1b.0 privacy repair is a parallel, provider-egress-blocking follow-up [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) under [Issue #63](https://github.com/davidcatcat/davidcattoolsproject/issues/63); the **mainline next design task is A11b.2.2.2 / Issue #70**, not another C roadmap. Company-only C1b Phase 0, C1c cost/quality evaluation and native self-development C2/C3 are not yet implemented or authorized. Preserve the historical proposal below as design intent, not as shipped acceptance evidence.
Starting baseline: `main@ceae425eacaa8729a9eb8d796e43304918a8c908`.
This is a prioritized plan, not a claim that native autonomous code execution or
measurable general token savings has already shipped.

## Product objective and transition rule

**Product:** `llmtool` itself coordinates Codex CLI implementation, Claude CLI
independent review, deterministic testing, bounded recovery, Git evidence/PR handoff
and company-LLM delegation on quality-proven low-risk routes, **including developing
llmtool itself**, while minimizing premium-model tokens and human prompt relay.

**Transition tool:** `ai-loop` is a disposable bootstrapping runner. Maintain
critical safety, correctness and compatibility only. Do **not** first build a
full second orchestration/security/QA product in the temporary runner. Migrate
its good controls into llmtool's native A11b/A11c contracts. No manual
Codex↔Claude prompt copy/paste should be required for ordinary iterations.

### Truthful present capabilities

- A2.4 `claude-cli` and A2.5 `codex-cli` are already merged and live smoke
  has been performed. **Both are isolated pure-text providers**: no repository
  editing, shell, Git or provider-native tools through these abstractions.
  Codex's llmtool provider intentionally uses isolated temporary cwd, not the repo.
- A9 sequential `orchestrate` runs up to the four built-in unique roles with
  bounded *text* handoffs, no execution, branch, loop, retry or routing.
- A10 `retrieve-context`, A5 Git Intelligence, A6 Validator, A11a
  diagnostics and A11b.2.1 read-only Git preflight are available now.
  These are building blocks, **not** an autonomous coding executor.
- PR #49 `A11b.2.2.1` is a **merged** synthetic-only containment harness (squash `eb464628dd6096811c659cfdf663f364107a6ef4`)
  with independently reported Security/Functional QA PASS and native Mac
  acceptance. A successful fake containment probe is **not** permission to
  perform actual Git ADD or run arbitrary commands. A11b.2.2.2 design and approval are separate.
- PR #45 remains a Draft temporary-runner change with its own outstanding
  security/QA gate. The existing control task is an old one-use `76644`
  recovery: do **not** replay or reinterpret it for this plan.

## Priority 0 — useful read-only dogfood **NOW**

Use today's llmtool against its own build/test process without waiting for
A11b.2.2.2 or A12:

1. Run deterministic test commands; collect exit, failure IDs, exact Git/CI
   state and artifact refs without model calls. Prefer A5 `llmtool git --json`
   where it covers the evidence.
2. Redact/limit sensitive input **before** any provider call. When Mac VPN
   and configured company provider are genuinely available, use llmtool to
   try narrowly scoped, quality-checked triage of *permitted* test data.
3. Reserve Codex/Claude for interpretation of checked source/raw ranges,
   coding, architectural risk and independent final review. Company model
   is an untrusted information aid, not a gate authority.
4. Record per-step source hashes, run ID, provider/profile, attempted calls,
   token usage **when available**, wall time, tool turns, reviewer result
   and any extra raw evidence reads. Unknown usage is **unknown, not zero**.

This is real use of read-only llmtool, **not** native self-development and
**not** a blanket claim of token savings. Company VPN/auth checks can run only
on the actual authorized Mac environment; no secrets or internal endpoints
are copied into external model contexts.

## Priority 1 — first small *llmtool* feature with measurable savings

**Deliverable C1: deterministic real-test-output excerpt selector**, an
explicit opt-in read-only helper used by the existing llmtool company-provider
workflow, *not* another LLM agent and *not* an ai-loop-only script.

Inputs: captured stdout/stderr from an explicit allowed test run (or an
explicit file), trusted run/exit metadata and a max-byte bound. It MUST NOT
run tests, scan the repo implicitly, touch Git state or infer that a model
response authorizes anything.

Output: stable failing-test/error identifiers, bounded anchored excerpts,
original byte ranges, source SHA-256, omission/truncation counts, unknown
markers, and a safe reference to the complete raw evidence. Do not fabricate
an error/root cause if coverage cannot be established. Reject malformed,
oversized, binary, symlinked, changing or sensitive inputs according to the
existing trust boundary; fail closed or return explicit INCOMPLETE. Never
call a provider simply to parse deterministic IDs.

**Acceptance C1a (pure deterministic):** unittest/pytest/JUnit-style
fixtures including mixed and repeated failures, Unicode, ANSI escapes,
truncated traceback, no failures, missing IDs, long output, path/byte
drift, symlinks, secret-like lines and false-excerpt/false-PASS negatives.
Pre-register failure-ID coverage and exact raw-range checks; preserve
original exit/failures. A partial excerpt is not grounds to mark a failed
test PASS.

**Acceptance C1b (company-only Phase 0):** on real-format 48 KiB+
representative fixtures, the selected company profile must produce
bounded, validated triage across pre-registered repeated trials. Confirm
task ID/error identifier precision and recall, source references,
hallucination rate, retry/failure usage accounting, latency and privacy.
Do **not** spend a paired Codex benchmark until company-only reliability
and usage pass. If Phase 0 fails, leave routing disabled; the deterministic
selector can still save prompt/log re-reading without an LLM.

**Acceptance C1c (premium-token savings):** compare no-delegation vs
excerpt/company lane on identical frozen evidence, same model/reasoning
and quality rules, fresh sessions, pre-registered counterbalanced runs.
Measure **Codex and Claude separately** when runtime accounting exists;
capture company tokens, total paid cost *only if reliable pricing exists*,
extra tool calls, cache, wall time and acceptance quality. All company
attempts/retries count. Require quality parity and positive measured
premium-token benefit before marketing this route as saving tokens. Do not
multiply two model tokenizers into a fictitious common currency.

Why first? The pilot `reports/llmtool_token_savings_pilot_review_20261006.md`
found small-input dogfood made Codex token use **104.46% higher** across
three quality-equivalent paired fixtures (54,849→112,144, n=1 per pair):
a second model/tool turn repeatedly resent a large fixed context. In
contrast, the *specific* L01 research pipeline on ~48 KiB raw output
recorded Codex reductions of **39.82% and 37.55%** under opposite run order
when a deterministic full failure-ID selector sent only compact excerpts.
The L01 evidence does **not** validate ordinary `llmtool ask --skill
test-triage`, arbitrary `pytest/unittest` output or `log-triage`/
`context-pack`. Never use company-first indiscriminately.

## Priority 2 — first native **self-development** execution vertical slice

**Deliverable C2: one authorized repository-bound development workflow.**
Use explicit separation of `Provider` (model transport), `Role` (job),
`Action` (typed side effect), `Policy/Approval`, `Executor` (host)
and `Verifier`. Do not loosen existing text-only `codex-cli` provider
or silently grant A9 roles shell/Git authority. A distinct, reviewed
`codex-cli` development-execution adapter is needed, or another
equivalently sandboxed implementation through A11b; Claude may consume a
bounded frozen diff/evidence as a read-only reviewer.

Minimum execution contract:

- Fixed canonical repository identity, exact HEAD, branch, task/version,
  allowed file paths, forbidden paths, accepted tools/action types,
  tests, token/tool/time budgets, required Security/QA/Mac gates.
- Explicit policy and user authorization before every permitted side
  effect, including edits/tests/Git. No arbitrary model shell strings,
  arbitrary Git operations, network, credential inheritance, privilege
  escalation, extra checkout/worktree, auto-push/merge or provider model
  output treated as approval.
- Required A11b-safe sandbox, pinned runtime/executable, process-group
  descendants cleanup, timeout/interrupt handling, PID reuse protection,
  audit/trace, original failure retention, no unsafe post-reap signaling.
  Cover negative cases before live execution.
- Bounded loop: Codex proposes/edits only allowed files → deterministic
  tests → independent Claude review of frozen diff and actual evidence →
  targeted repair if reproducible and within budget → final full/required
  Mac tests → scoped Git/PR publication **only when separately authorized**
  → stop at independent final gates and user merge decision.
- A failed or inconclusive Security/QA result, unverified Mac dependency,
  changing SHA, unknown process identity, failed source hash, exhausted
  budget or permission conflict MUST stop without inferred PASS.
- Before real product integration fix known PR #49 low defects: S49-3
  raw-directory-FD cleanup and a permanent natural-leader-exit native
  regression; isolate fixture-only fault hooks (P49-L1); document
  supported interpreter/runtime/host F4 conditions.

This vertical slice is **not yet implemented**. C2 is narrower than a
fully general-purpose autonomous executor but **not** permission to bypass
the A11b safety/approval/containment requirements. A11b.2.2.2 real Git
ADD, A11b.3–6 policy/repair and A11c integration remain gated separately;
a reviewed slice contract must enumerate its exact dependencies.

## Priority 3 — cutover: llmtool replaces ai-loop

Run llmtool on actual canonical-Mac, authorized, isolated *real* repository
tasks: at least one scoped documentation change, one small bug fix with
reproducible failing test, and one forced failed-review/repair case.
Independent Security and Functional QA must inspect true behavior, logs
and false-PASS negatives. Include interrupted children and dirty-tree STOP.
External reviewers do not depend on developer memory.

**Switch only when** normal user flow is task→single llmtool command→
Codex/Claude/test/repair→audited Draft PR or explicit NEEDS_DECISION,
no manual prompt relaying, acceptable latency, no unresolved critical/high
security findings, quality parity and measured savings on at least one
representative route. If the cost route loses tokens, disable it without
blocking an otherwise safe native executor; do not claim ROI.

Leave `ai-loop` usable as an emergency fallback during cutover, then
freeze/deprecate its duplicated orchestration. Do not demand completion
of optional A2.3 Anthropic HTTP API, A12 Telegram or a general-purpose
dashboard before this vertical slice.

## Decisions and sequencing — current A11 authority (2026-10-09)

This updates **ordering and ownership only**, not the historical C0–C3
acceptance designs above. See the sole A↔C mapping in
[ROADMAP.md](ROADMAP.md).

1. **A11b product critical path:** PR #49 synthetic-only containment is DONE
   (`eb464628dd6096811c659cfdf663f364107a6ef4`). Next: write the
   A11b.2.2.2 B5 §14.1 product-integration **design spec** and obtain
   independent Security review ([Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)).
   A separate authorization, test/evidence contract and approval gate must
   precede real Git ADD implementation. A11b.3–6 remain required for actual
   Codex-driven execution, retries and Git side effects.
2. **A11c output-safety task (legacy C1b.0):** [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66)
   may run as a bounded, independently reviewed parallel fix; it gates C1
   excerpts **before they leave the local process for any provider**.
   It does not block local A11b design and does not sanitize arbitrary diff,
   source code, other logs or all reviewer payloads. Evaluate actual
   external-data exports separately before using Claude/company/Codex.
3. **A11c company route and savings acceptance (legacy C1b Phase 0/C1c):**
   retain the pre-registered company-only reliability, quality, usage
   accounting and paired premium-token checks for permitted routes.
   Run when there is a realistic usable route; **not** as a blanket
   prerequisite for starting the local native executor. Do not claim
   generic cost savings from the existing limited pilots.
4. **A11c native self-development & cutover (legacy C2/C3 outcomes):**
   prove canonical Mac scoped documentation and bug/failed-review cases,
   controlled Codex development, independent Claude review, tests,
   bounded repair, approved PR publication and fail-closed STOP. Only
   when llmtool meets equivalent Security/Functional QA and operational
   evidence can it replace the **currently active temporary ai-loop**
   collaboration workflow. Until then, maintain the existing operator
   SHA-approved launcher and only fix its essential security/stability
   faults; do not build a second controller product.
5. Do not renumber A milestones or rewrite prior validation reports,
   PR evidence or this proposal's original requirement paragraphs.
   General delegated merge authority is **not** Mac controller-SHA
   approval, model provider export authorization or permission to skip
   the required independent/Mac gates.

**Not a deployment timeline:** phase gates, not dates. Code/CI/natively
observed behaviors determine launch availability; the planned future
components do not yet have tested completion estimates.
