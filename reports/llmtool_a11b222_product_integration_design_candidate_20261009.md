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

## 6A. Corrective integration decisions for Claude NEEDS_CHANGE at `8361f78`

**Priority of interpretation:** This section clarifies/supersedes the ambiguous *proposed* host, session and handoff language in §§1–3, §5 and §6. It requests independent re-review and has **NO execution authority**. [Claude's first Security/design findings](https://github.com/davidcatcat/davidcattoolsproject/pull/73#issuecomment-6083013832): B1–B3 BLOCKING; M1–M2 Medium; L1–L6 precision requests.

### B1 — Seal a dedicated product Host (not the CLI and not the synthetic harness)

**Choice:** a new, reviewed private standalone parent process provisionally named `llmtool._git_add_host`, using a trusted isolated launch (`python -I -m llmtool._git_add_host`) from an installed, validated product path and neutral environment. It is **not** the normal llmtool CLI/provider/diagnostic process, which can have extra threads; it is **not** `tests/containment_probe_parent.py`. The trusted internal caller-to-host entry/channel must bind fixed module origins, interpreter, reviewed code manifest, action/task digest, fresh nonce and parent identity. An unsupported caller or direct `-m`/runpy/importlib/CLI/provider/debug entry lacking that private handshake must fail **before any supervisor or Git spawn**. This private capability/transport requires a separate concrete implementation design and independent approval; a self-consistent model-supplied manifest cannot authorize entry.

A separate product host validator (not relaxing fixture-only `_git_containment._validate_host` at its current lines 86–92) enforces `sys.argv[0]`, `__main__.__file__`, hashes, isolated interpreter, no tracer/profiler, main thread, `threading.active_count()==1`, native OS thread count exactly 1, `SIGCHLD == SIG_DFL`, no extra spawn, fork callbacks or foreign reaper (B5 §3.3; **F4 from the A11b.2.2.1 independent review**, not B5). Unsupported/unknown premise -> fail closed before spawn.

**Retained operator-wait owner:** the **sealed host** holds the only liveness writer, direct supervisor `Popen`/unreaped anchor, FDs, scoped handlers, non-reaping monitor, original failure and runtime ownership throughout containment. On `ContainmentUnresolved`, it does not return normally, restore handlers, reap, close critical FDs, signal unverified numeric IDs or claim success; it retains **operator-wait** until independent external resolution. The outer CLI/coordinator may get a bounded `STATE_UNKNOWN / USER_ACTION_REQUIRED` notification but must not retry, release ownership, or equate terminating the host with confirmed descendant cleanup. Reviewer must examine host lifetime vs task budgets and trusted channel anti-replay; unresolved means **B1 BLOCKED**.

### B2 — Explicitly request the topology extension for real Git ADD

**Proposed product topology, NOT yet approved:**

```text
sealed synchronous host (owns unique parent liveness-writer)
  -> owned direct supervisor (start_new_session=True; PID=PGID=SID)
     -> deterministic git add + descendants (start_new_session=False;
                                             inherit supervisor PGID/SID)
```

The [PR #49 self-anchor amendment](llmtool_pr49_b5_self_anchor_amendment_20261008.md) approved this layout for **synthetic** children only. For A11b.2.2.2 the coordinator now explicitly requests **independent Security approval** to supersede B5 §§3.2, **6** (existing Git child `start_new_session=True`) and 11.1–11.3 (old grandchild fallback) *for this product slice only*. Supervisor current-self-group `killpg(0,SIGKILL)` last act is allowed only after its live self leadership check; host group signals must first verify its own unreaped direct supervisor using PPID/UID/start-time/PID/PGID/SID, **signal before reap**, never re-signal numeric IDs after reaping. Git inherits **no** control/status/liveness/audit FDs. If independent reviewer rejects extension, retain B5 §6 and **STOP real Git integration** until an alternative coherent child-group design is separately accepted. Do not mix mutually exclusive session topologies.

### B3 — Supervisor-owned TOCTOU gate and separate product binding

The existing `_git_containment_protocol.runtime_binding`, `_git_supervisor.py` at its fixture check, and `ARTIFACTS` manifest are **fake-only**: `fixture={case,timeout}`, domains `A11b.2.2.1/fake-only/action/v1` / `...preflight/v1`, and `../tests/containment_probe_parent.py` is included in the manifest. A real Git ADD requires a **new reviewed product domain**, e.g. `A11b.2.2.2/git-add/internal/v1`, binding canonical Action/preflight digests, exact source/manifest+interpreter identity, root/HEAD/ref/index/pre/post/authorized path/expected blob fields, nonce, parent/supervisor identity and timeout. **Product manifest may not include any test fixture file.** Reject cross-domain binding/replay, old fixture-only schema, mismatched trusted identity, unreviewed modules or inconsistent provenance.

**B5 §5 is mandatory:** the **supervisor itself** must parse/revalidate the canonical Action, rerun `preflight_git_add(action)` and require field-for-field equality with the trusted parent's pinned result for Action digest; canonical root/device/inode; HEAD OID, attached branch ref name/OID; Git object format; pre-index SHA; expected state digest; authorized ordered paths; semantic baseline; bound blob OIDs/modes and attributes/config. Independently reverify reviewed installed code hashes, source manifest, nonce, owner and parent liveness **immediately before spawning Git**. A parent-only previous preflight does **not** satisfy this requirement. Any drift, uncertain identity or mismatch -> no Git spawn. Recheck reduces but does not make the check/spawn interval atomic; exact TOCTOU race bounds require independent review.

### M1/M2 and Low finding precision

- **P49-L1/M1:** isolate the existing `_git_containment._run(..., hold_terminal, inheritance_stress, timing)` knobs, fixed fault cases in `_git_supervisor.py` and F1 test injection from all product host/handshake/CLI/importlib/runpy/`-m` call paths. Separate product/fixture manifests; never hash `../tests/containment_probe_parent.py` as product authority (B5 §21).
- **S49-3 / QA49-N2 / L5:** reproduce `_git_containment_protocol.file_hash` lines 334–335: `os.open(...)` obtains a raw FD, and failing `os.fdopen(fd,'rb')` can leak it when wrapping a directory FD. Introduce independent regression tests forcing fdopen failure, proving closure/FD-count stability in repeated exceptional and early-exit paths. A written spec does **not** fix this bug.
- **M2 / B5 §§20,26 N1:** test a `post-index-change` hook does **not** run, an authorized **new untracked file** stages correctly, unrelated work is left untouched, an index lock is preserved and classified, and unresolved containment forbids APPLIED even with a provisional expected index. Native Mac parent-death plain/Popen probes require >=20 repeats **per form**, group/descendant disappearance within **2 seconds** and explicit `kill(0)`/identity/absence evidence; hosted CI is not automatic Mac acceptance.
- **L2–L4:** B5 **§16** already enumerates intent fields: contract/action digests, approval ID, repo identity, HEAD, pre-index SHA, attached branch name/OID, semantic pre/expected digests, source manifest digest and allowed paths/scope. Runtime durable-before-action audit is mandatory; fixture uses **no approval object** (B5 §§16.1/21). B5 **§17** already specifies crash-recovery semantics; only the startup implementation is deferred, not design of classification.
- **L6 / B5 §7:** the first private disposable-fixture ADD may use the existing config/hook/filter/credential/protocol neutralization *without adding a new OS sandbox* **only if** independently shown to remove **every** repository-controlled executable path and preserve user data boundaries. If not, STOP until a reviewed OS sandbox or blocked code path. Do not imply a general product sandbox exemption.
- **F4 citation:** F4 is in [A11b.2.2.1 independent review](llmtool_a11b2_2_1_containment_independent_review_20261008.md), not B5. Host compatibility/ctypes and P49-L3 passive post-reap check remain subject to future tests.

**Result:** `DESIGN_REVISED_REVIEW_PENDING`. **This is not a new Claude PASS, Codex implementation contract, test evidence or Git mutation authorization.** Distinct Security reviewer must challenge this frozen new HEAD and identify any still-unresolved host authentication, topology, supervisor revalidation, runtime and Mac safety conditions.

## 7. Publication, review, and release-stop contract

- This *documentation* design candidate is **not** independent Security PASS. The coordinator may author/organize it, but a **distinct** Claude specialist must review the frozen candidate against B5 source, PR #49 evidence, concrete code-safety constraints and proposed tests.
- Require Claude to answer: exact fixture vs product boundary; post-state totality/no-op; child ownership races and Mac host premises; FD/fault-hook isolation; durable audit/approval ordering; protection from unsanitized Git/environment/network; safe stop/unknown; what can be safely built first.
- Review outcomes: `APPROVE` (design gate only), `NEEDS_CHANGE` or `BLOCK`; provide path/section/evidence. No self-review treated as independent proof.
- Only after design APPROVE may coordinator create a new **bounded Codex implementation contract** with explicit file allowlist, source SHA, pre-registration of test scenarios, provider-export limits, permitted synthetic fixtures, Mac/integration/CI gates and a separate PR. Actual Git ADD in production remains NOT AUTHORIZED until all relevant A11b.3–6 and security requirements are independently met.
- The existing Git-provided temporary `ai-loop` coordinates Codex/Claude until native llmtool passes A11c real-Mac acceptance. Controller review/release is handled by AI; **no per-version owner technical approval** is required. Do not push unreviewed executable control bytes to `automation/ai-loop` to run this design task.
- [Legacy C1 excerpt privacy Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66) stays separate for specific provider export paths and is not an offline A11b design precondition. Exports of arbitrary source, diff or test logs need their own data-boundary review.

**Current exit state:** `DESIGN_CANDIDATE_READY_FOR_INDEPENDENT_REVIEW`, not `APPROVED`, `IMPLEMENTED`, `MAC_PASS` or `PRODUCTION_READY`.
