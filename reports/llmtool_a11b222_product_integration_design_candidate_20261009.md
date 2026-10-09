# A11b.2.2.2 — Real Git ADD product integration: design delta and review contract

> **DRAFT DESIGN / NOT INDEPENDENTLY SECURITY-REVIEWED / NO REAL MUTATION AUTHORIZATION**
> Coordinator-authored specification candidate, 2026-10-09.
> Source baseline: `main@4b8bf511f4b52245b877b80a0d8d509bff1f95d9`.
> Work item: [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70).
> Only this design document is added; no new tool, runtime, tests, Mac action or control-branch publication.

## 1. Purpose and existing authority

Deliver a **narrow design delta**, not another process-controller framework.
The authoritative prior normative sources remain:

- [B5 containment + failure semantics (§§3–22, especially §14.1)](llmtool_a11b2_2_b5_containment_failure_spec_20261007.md)
- [B5 self-owned-supervisor topology amendment](llmtool_pr49_b5_self_anchor_amendment_20261008.md), which supersedes historical topology only for the approved synthetic harness
- [Independent PR #49 re-review](llmtool_pr49_independent_rereview_20261009.md)
- [A11b ROADMAP](../tools/llmtool/ROADMAP.md), [A11b.2.1 PR #43](https://github.com/davidcatcat/davidcattoolsproject/pull/43), [A11b.2.2.1 PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49).

**Current delivered boundary:** A11b.1 typed contracts and approvals data structures are merged; A11b.2.1 read-only Git ADD preflight is merged; A11b.2.2.1 *synthetic-only* containment is merged with Mac 14/14 and 20+20 parent SIGKILL evidence. The production execution adapter, actual Git ADD authorization, durable-before-action audit writer, approval issuer and fully integrated A11b.3–6 controls **are not delivered**. Do not present successful synthetic process probes as production mutation evidence.

## 2. Proposed bounded first product slice (subject to independent review)

The first code implementation **after this design passes independent Security review** should be the smallest feasible *internal/private* Git ADD adapter, in a disposable, verified repository fixture, with exact path/HEAD/branch/pre-index identity and process-containment semantics.

**Two distinct scopes must not be conflated:**

- **Internal fixture integration** may exercise a private mutation engine in disposable test repositories, using synthetic/test-controlled authority only under the B5 §16.1 exception; it must remain inaccessible from CLI, public orchestration, providers, agent tools, debug paths and live user repositories. No model-generated text is executable authority.
- **Product runtime capability** is a later, separately reviewed/authorized scope requiring complete A11b.3 policy/approval, R-AUD-4 durable intent-before-action and terminal audit, R-EXEC/R-GIT/R-FS controls, host sandbox and real-Mac validation. This document cannot activate that path.

If the first fixture slice cannot safely isolate its execution authority, **do not implement Git mutation even in the fixture**; return NEEDS_DECISION with the exact violated premise.

## 3. Action & authority boundary

Proposed data flow, **not yet an API or executable implementation**:

1. Read trusted typed Task Contract + Action with exact repo/root, action/version digest, allowlisted paths, bound HEAD OID/ref and expected pre/post semantic index digests. Reject inconsistent/missing fields.
2. Produce **only** A11b.2.1 existing nonmutating preflight evidence with sanitized Git argv/environment. Verify same canonical repo identity, clean or explicitly scoped expected staging state, index and branch/HEAD/ref conditions; refuse unsafe/symlink/directory escape, conflicts, unknown state or untracked unrelated work in scope.
3. For eventual runtime: check policy/required authorization bound to exact action/run/target **then** write and fsync/confirm durable mutation intent **before any possible Git ADD**; failure STOP, no exec. **Fixture-only integration** must not be reachable through this product route.
4. Before spawn and at the trusted handoff, recheck exact source/manifest, filesystem provenance, index/ref identity and allowed paths to mitigate TOCTOU. Existing preflight PASS cannot be replayed after any drift. Keep Git config, optional Git hooks/filters/attrs, inherited descriptors, env, pager and network escapes constrained per A5 Git-hardening contract.
5. Run a single bounded, reviewed executable within a supported OS/process sandbox and the reviewed parent → owned direct-supervisor session/group → child/descendants topology. Only the owned, unreaped supervisor may anchor group cleanup; never kill a remembered unrelated process group. The supervisor's verified self-group last act may use current-group signaling; parent kill must be preceded by strict fresh-owned identity verification.
6. On normal exit, timeout, interrupt, supervisor loss or host/control failure, acquire reliable **containment evidence first**. If uncertain, retain responsibility / operator-wait and emit `STATE_UNKNOWN / USER_ACTION_REQUIRED` without guessed cleanup, return, rollback or automatic retry.
7. Only after confirmed containment, read the index and HEAD/ref from an independently bounded, safe post-state verifier; apply §14.1 prioritized semantic classification. Record a sanitized lifecycle and semantic outcome separately.
8. Runtime-only path must persist terminal/recovery audit. Missing terminal event means restart-time **read-only recovery** pending separate design, never automatic retry/restore/lock deletion.

At **no** point may Claude/Codex prompt content, model self-reported PASS, or the owner typing a shell command substitute for typed authorization, independently reviewed code, exact source-binding or runtime verification.

## 4. Normative state/containment matrix inherited from B5 §14.1

Precondition: if group/owned-child containment cannot be proved, outcome is **`STATE_UNKNOWN / USER_ACTION_REQUIRED`**, `containment_confirmed=false`; observed index content is only provisional. Never label APPLIED while Git may still run.

Once containment **is confirmed**, evaluate first-match priority:

| Priority | Condition | Required semantic result |
| --- | --- | --- |
| 1 | Index lock present; index unreadable, missing, invalid, unstable, inaccessible or unsafe | `USER_ACTION_REQUIRED` |
| 2 | Root/layout identity unsafe or changed; HEAD/ref name/OID unreadable or differs from bound state | `USER_ACTION_REQUIRED` |
| 3 | Full semantic index equals **exactly expected authorized post-state** | clean exit 0, no interruption: `APPLIED`; otherwise: `APPLIED_BUT_CONTROL_INTERRUPTED` |
| 4 | Full semantic index equals pre-state **and pre-state != expected** | `NOT_APPLIED` even if subprocess nominally returned 0 |
| 5 | Any different semantic index, unknown verifier error or unmodeled state | `USER_ACTION_REQUIRED` plus stable mutation-state-unknown reason |

No-op (`expected == pre`) is evaluated by row 3 before row 4, never erroneously NOT_APPLIED. Compare ordered path/mode/OID/stage/flags, **not** raw index SHA alone; cache/stat refresh can change bytes without semantic staging change. Unexpected paths, mode/oid/stage changes, hidden index.lock, concurrent checkout or different ref **must never PASS**. Preservation rules: never delete lock, never `reset --hard`, `clean`, checkout/restore unrelated files, guess provenance, auto-retry unknown actions or call an uncertain stop a success.

**Action outcome != lifecycle outcome != independent QA:** `APPLIED_BUT_CONTROL_INTERRUPTED` confirms a postcondition under appropriate proof but still requires STOP and further review, not DONE. A model cannot promote either type.

## 5. Specific PR #49 deferred findings: integration disposition

| Finding | Required new specification / implementation evidence | Failure rule |
| --- | --- | --- |
| **P49-L1** fixture fault hooks | Design the real engine with **no production import, environment flag or callable hook** that can trigger fixture-only injection. Keep synthetic injections in test-only fixture wiring; add a source/import reachability test and no-public-CLI/agent entrypoint probe | Any hook reachable in product path: **BLOCK** |
| **S49-3 / QA49-N2** raw directory-FD leak | Audit all open directory/descriptor paths used in hashing, path ancestry and rejection, including exceptions, early returns, timeouts/cancellation and simultaneous failures. Centralize FD ownership; guarantee closure with nested `finally` and test process FD inventory before/after repeated rejection in real Mac supported runtime | Leak or uncertain ownership: **BLOCK production integration**, preserve typed error |
| **QA49-N1** natural leader exit | Add permanent non-synthetic leader-exit cleanup regression independent of SIGKILL stress loops, with no residual descendants and verified ownership boundaries | Residual child/unknown cleanup: STOP |
| **P49-L2 / B5 F4** host premises | Pin supported Python/platform, single-thread synchronous host, required `ctypes`/FD identity capabilities, supervisor behavior and safe fallback. On unsupported host/runtime or ambiguous process identity, fail closed; document Windows as unsupported for this narrow mechanism unless separately reviewed | Do not silently downgrade to uncontained subprocess |
| **P49-L3** post-reap numeric check | Numeric absence is passive forensic signal after reap and may be conservatively false-negative. Never use post-reap PID/PGID numbers to authorize a second signal or to claim unsafe state is resolved | Uncertain final identity/outcome: retain conservative STOP |
| **B5 §14.1 & QG-S1** index/authority | Implement total first-match state table and durable-before-action intent for runtime path only; disallow any side-effect before approval/audit | No verified audit, approval or state binding: STOP |

For each finding, Claude should decide whether it is (a) hard *pre-first-fixture* gate, (b) hard *pre-runtime-integration* gate, or (c) a nonblocking, evidenced residual. Do **not** silently mark these all resolved because their requirements are written here.

## 6. Required negative and platform-specific acceptance matrix

**Cloud/offline on exact implementation commit (future, not run here):**
- dirty worktree; index.lock; symlink/UNC/path escapes where supported; fake Git repository/foreign origin; swapped refs/HEAD/branch/index between preflight and spawn
- incomplete path allowlist, staged unrelated file, semantically changed mode/oid/stage, no-op, manipulated pre/post raw index cache, partial Git failure, nominal exit0 with wrong index
- descriptor leak on repeated rejected paths (and forced exception paths); injection hook reachability; malformed/oversized framed supervisor messages, early EOF/EPIPE, stalled child identity delivery
- payload/contract mismatch, stale or replayed approval, audit-write failure **before spawn**, terminal-audit failure, provider-prompt injection that claims authorization
- Timeout/interrupt/supervisor crash/host-SIGKILL leading to contained, uncontained, unknown identity states; never manufacture APPLIED after unresolved cleanup.

**Native Mac required on exact source / compatible supported Python:**
- supported Python 3.9 system + installed venv Python version, separately; real process trees with plain `Popen` and other supported launch forms
- parent SIGKILL **at least 20 repetitions per required form** from verified product harness, with observed process identities, absence/cleanup evidence and honest latency; reuse PR #49's prior 14 scenarios as baseline where still relevant but **rerun on integrated bytes**
- natural leader-exit and descendants, supervisor unavailable and failure-to-report, wrong-group rejection, PID/PGID/SID ownership mismatch, fd-closure and interruption handler restore ordering
- unsupported Linux/macOS sandbox capability is **NOT TESTED/BLOCKED**, not silently replaced with synthetic Mac PASS. Cloud CI Python 3.9/3.14 does not prove real Mac behavior.

Record scenario-level PASS/FAIL/INCOMPLETE/NOT-TESTED, exact git commit, artifact content hashes, environment, command, exit code and privacy-safe diagnostics; no raw provider secrets or fake Mac evidence.

## 7. Publication, review, and release-stop contract

- This *documentation* design candidate is **not** independent Security PASS. The coordinator may author/organize it, but a **distinct** Claude specialist must review the frozen candidate against B5 source, PR #49 evidence, concrete code-safety constraints and proposed tests.
- Require Claude to answer: exact fixture vs product boundary; post-state totality/no-op; child ownership races and Mac host premises; FD/fault-hook isolation; durable audit/approval ordering; protection from unsanitized Git/environment/network; safe stop/unknown; what can be safely built first.
- Review outcomes: `APPROVE` (design gate only), `NEEDS_CHANGE` or `BLOCK`; provide path/section/evidence. No self-review treated as independent proof.
- Only after design APPROVE may coordinator create a new **bounded Codex implementation contract** with explicit file allowlist, source SHA, pre-registration of test scenarios, provider-export limits, permitted synthetic fixtures, Mac/integration/CI gates and a separate PR. Actual Git ADD in production remains NOT AUTHORIZED until all relevant A11b.3–6 and security requirements are independently met.
- The existing Git-provided temporary `ai-loop` coordinates Codex/Claude until native llmtool passes A11c real-Mac acceptance. Controller review/release is handled by AI; **no per-version owner technical approval** is required. Do not push unreviewed executable control bytes to `automation/ai-loop` to run this design task.
- [Legacy C1 excerpt privacy Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) stays separate for specific provider export paths and is not an offline A11b design precondition. Exports of arbitrary source, diff or test logs need their own data-boundary review.

**Current exit state:** `DESIGN_CANDIDATE_READY_FOR_INDEPENDENT_REVIEW`, not `APPROVED`, `IMPLEMENTED`, `MAC_PASS` or `PRODUCTION_READY`.
