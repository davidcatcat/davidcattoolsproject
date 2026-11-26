# llmtool README history — 2026-11-26

This archive preserves the milestone and design history moved out of [the llmtool README](../../tools/llmtool/README.md). The archived text is unchanged; only relative link targets were rewritten so they resolve from `docs/history/`. The current user guide remains in the README.

## Accepted collaboration and mobile Codex Cloud development lane

[Architecture decisions Issue #172](https://github.com/davidcatcat/davidcattoolsproject/issues/172)
and [Draft PR #173](https://github.com/davidcatcat/davidcattoolsproject/pull/173)
record the owner's D-001, D-002a, D-003a/b/c, D-005a and D-007 decisions:
**ChatGPT Coordinator** discusses product requirements; for complex work the
**existing Codex Integrator** is PM + Integration Manager and decides how to
split and assign agents; **llmtool** performs authorized deterministic
Provider/Worker invocation, policy, state, evidence and events, **not**
complex AI-driven task planning. The local one-time owner handoff -> Worker
completion -> same Integrator wake is an unverified M2 product target.
The home LLM is used only through the API Agent Gateway route `gw-agent`;
the direct and proposal `company-gw` routes are retired and refused
(Owner 2026-11-11, #120). Unknown cost and usage remain UNKNOWN.

**D-007 mobile parallel development:** while away from the Mac, the owner
can start a separate Codex Cloud task from ChatGPT mobile after the
Cloud environment was configured/published on web/desktop. Coordinator or
Integrator/PM decides which independent, nonoverlapping work package suits
Cloud. Cloud works in its own checkout, implements, tests, **self-pre-reviews**
and, when GitHub permissions allow, publishes its own branch/Draft PR.
**Under the 2026-10-18 handoff disposition, the existing Integrator picks
up the finished PR through actually available, authorized CLI/GitHub
workflows**, verifies the change and obtains independent non-author
Security/Functional/QA plus Mac-only checks as applicable. The
Cloud completion may use optional owner link sharing as a fallback; no
owner completion relay is required. If remote launch is unavailable, record
the concrete task and limitation; a GitHub comment does not prove that Cloud
has started. This handoff is not an llmtool Worker, an automated
original-session callback, or evidence of M2/F2 Live acceptance. Self-review
does not replace independent review. Do not auto-merge main.

Cloud needs repository and push authorization; it does not automatically
inherit Mac checkout, VPN/Keychain or unpushed files. GitHub refs remain
shared between independent Cloud and Mac branches. See
[Workflow](https://github.com/davidcatcat/davidcattoolsproject/blob/cf3a1c4a306b3be9b058d32b0bf7047d33727f1c/tools/llmtool/WORKFLOW.md#d-007-independent-mobile-codex-cloud-delivery),
[Roadmap](llmtool-roadmap-archive-20261028.md#owner-accepted-collaboration-architecture-and-mobile-codex-cloud-lane-2026-10-17)
and [official Codex Cloud guide](https://help.openai.com/en/articles/20001545-using-codex-cloud).

## Roadmap status

- **A0 — Model Validation:** DONE
- **A1 — Generic LLMTool Core:** DONE and merged to `main`
- **A1.1 — Configure Wizard + Live Smoke:** DONE and merged to `main`
- **A2.1 — Provider Capability Foundation:** DONE
- **A2.2 — OpenAI API Provider:** DONE; Responses API, pure text; live API NOT-TESTED
- **A2.3 — Anthropic API Provider:** planned
- **A2.4 — Claude CLI Provider:** DONE; local CLI, pure text; historical pre-hardening live E2E PASS; current status below
- **A2.5 — Codex CLI Provider:** DONE and merged to `main`; external host validation and direct/live E2E PASS
- **A2.6 — Provider Integration / Smoke Tests:** DONE and merged to `main`; host canonical validation 189/189 PASS
- **A3 — Skill Framework:** DONE and merged to `main` (`c9159f2`); external review PASS
- **A3.5 — Interactive Chat Shell:** DONE and merged to `main` (`021456b`); external review PASS
- **A4 — Evidence-first Context Packet:** DONE and merged to `main` (`38b1a67`); external review PASS
- **A5 — Git Intelligence:** DONE and merged to `main` (`7de6a99`); external review PASS
- **A6 — Safety & Validator Layer:** DONE and merged to `main` (`63b5bae`); external review PASS
- **A0–A6:** DONE and merged; A0–A7 bugfix/hardening merged to main (`643caa2`).
- **A7 — Skill Trainer / Feedback Loop:** DONE and merged to main (`d61ca6a`); post-hardening validation and external ChatGPT review PASS
- **A8 — Regression / Benchmark System:** DONE and merged to `main` via PR #9 (`d968161`); external ChatGPT review PASS; A8-R1 FIXED. Claude live model safety benchmark remains FAIL as measured.
- **A9 — Agent Orchestration:** DONE and merged to `main` via PR #10 (`8776476`); A9-R1 FIXED; updated external ChatGPT review PASS; final-head Python 3.9/3.14 CI PASS.
- **A10 — Dynamic Context Retrieval:** DONE and merged to `main` via PR #12 (`21b9757`); independent Claude re-review APPROVE, external ChatGPT final review PASS, final VPN live acceptance PASS, final-head Python 3.9/3.14 CI 605/605 PASS.
- **A11 — Production Workflow & Diagnostics:** **A11a DONE and merged** through PR #26 (`2761ce10`), including final acceptance and B1–B7 post-acceptance regression fixes. **QG-S1 and QG-QA1 are PASS/CLOSED**. A11b is **IN PROGRESS**: **A11b.1 Typed Action & Approval Contract is DONE/merged via PR #32 (`e5525f18`)**; **A11b.2.1 deterministic Git ADD preflight/state verifier is DONE/merged via PR #43 (`780970ab`)** with no `git add` execution or public mutation path. **A11b.2.2.1 synthetic containment is DONE/merged via PR #49 (`eb464628`); A11b.2.2.2 F1 private disposable-fixture-only Git ADD is DONE/merged via PR #84 (`46b17b13`) after independent Security/Functional QA. F2 real-repository mutation remains NOT AUTHORIZED.** **P1 failure-usage preservation is DONE/merged via PR #39 (`56799527`)**. A11c.1 S1a offline inspection is DONE/MERGED via PR #128; S1b Product A fake-only is merged via PR #131; B1 is a candidate with real-provider handoff BLOCKED pending B; full production Integration/Real-world Acceptance remains NOT STARTED and must use route-by-task evidence: only the specific L01 test-triage benchmark pipeline has positive token-saving evidence; ordinary test-triage usage and log-triage/context-pack are not validated production savings routes.
- **A12 — Remote Frontends / Telegram Agent:** future optional frontend; must reuse A11 task/run/event/error/approval semantics and must not create a second execution/logging/error system.

A1.1 code review and live smoke test are complete. It delivers interactive `llmtool configure`,
automatic user config path handling, hidden API
key input, `api_key`／`api_key_env` support, saving incomplete settings as null, atomic writes,
0600 permissions, existing-config protection, profile preservation, and no automatic provider
request during configuration. The live gateway result is recorded in the
[A1.1 smoke test report](../../reports/llmtool_a1_1_live_smoke_20260929.md).

## A11 native self-development — single product roadmap

The product goal is **native llmtool-directed Codex CLI development,
independent Claude CLI review, real tests, bounded repair and safe PR
handoff**, with measured and quality-preserving Token savings when those
routes are genuinely eligible. **A11b/A11c are the only active product
milestones for that goal.** Historical C0–C3 are consolidated back into
[the single A roadmap](../../tools/llmtool/ROADMAP.md) and documented only as legacy labels
in [the original proposal](../../tools/llmtool/NATIVE_SELF_DEV_MVP.md). Do not implement
a second C executor, QA framework or release line.

The standalone transitional helper is documented in
[tools/ai-loop/README.md](../../tools/ai-loop/README.md); it is not an llmtool route.
The requirements owner supplies goals and product acceptance; the AI
team owns technical management, development and evidence-backed review.

**Current native milestone:** A11b.2.2.1 synthetic containment and
A11b.2.2.2 F1 private disposable-fixture-only Git ADD are merged (PR #49,
PR #84 respectively). **F2 user-repository Git mutation remains NOT AUTHORIZED**.
Design the next separate action/approval/containment/rollback contract with
independent Security review first ([Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)).
A11c later performs real Mac Codex/Claude self-development acceptance
and cutover. The merged local-only test-excerpt helper (legacy C1a,
[PR #55](https://github.com/davidcatcat/davidcattoolsproject/pull/55))
stays available. Its final-output privacy follow-up
([Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66))
must pass before those excerpts go to **any provider**. Company-only
reliability/Token benchmarks belong in A11c; full benchmarks do not
block local A11b design, and other model-bound source/log/diff exports
require their own independent data-boundary review and authorization.

Cost evidence is route-specific: small PR #36 pilot was token-negative
(Codex usage increased by 104.46%); one large-log L01 research pipeline
had two quality-approved Codex savings observations (39.82% / 37.55%).
Do not advertise broad savings before representative tests and accounting.

## A11 delivery checkpoint

A11 is deliberately split into implementation milestones and independent quality
gates so that "code reviewed", "bugs searched", "security assessed" and "QA passed"
are never treated as the same claim.

| Stage | State | Purpose |
| --- | --- | --- |
| A11a — Observability & Diagnostics | **DONE** | Structured diagnostics, opt-in safe metadata store, runs/trace/doctor, failure handling |
| QG-S1 — Security Baseline / Threat Model | **PASS/CLOSED** | Assess current attack surface and pin security requirements before executor work |
| QG-QA1 — Formal A11a QA | **PASS/CLOSED** | Exercise public CLI behavior, failures, concurrency/state and supported environments |
| A11b — Execution, Approval & Recovery | **IN PROGRESS — A11b.1 PR #32, A11b.2.1 PR #43, A11b.2.2.1 PR #49 and sealed F1 PR #84 DONE/merged** | F2 real user-repo mutation NOT AUTHORIZED; next separate Security-reviewed F2 and A11b.3 approval design |
| A11c — Integration & Real-world Acceptance | **S1a DONE/MERGED; S1b A MERGED; B1 candidate** | S1a product accepted via PR #128; S1b contract merged via PR #129; B production attestation/grant/route gates remain required; full production integration/real task acceptance NOT STARTED; no blanket company-first policy |

### Home LLM routing evidence after Round 3

Round 3 is closed as an experiment record; PR #38 is merged. P1 from PR #39 is also
merged and preserves a validated `prompt_tokens/completion_tokens/total_tokens` triplet
on parseable provider failures without changing failure semantics.

Current routing evidence:

| Route | Evidence state | Current policy |
| --- | --- | --- |
| `test-triage` | **BENCHMARK PIPELINE VALIDATED; ordinary skill use UNVALIDATED for savings** | Positive evidence requires the frozen L01-style pipeline: ~48 KiB canonical raw output, complete FAILURE-ID coverage, deterministic ±2-line excerpt selector capped at `min(32 KiB, 20%)`, inline selected raw, and no full-raw re-read by Codex |
| `log-triage` | **EXPERIMENTAL** | May assist bounded triage, but do not claim token savings or authorize A11c routing from current evidence |
| `context-pack` | **EXPERIMENTAL** | May assist bounded evidence normalization, but Round-3 3/7 target recall and instruction/gate mismatch must be redesigned before routing claims |

The two quality-approved L01 observations reduced Codex usage by 39.82% and 37.55%
under opposite run order on the same synthetic fixture. They are not pooled into a
generalized savings rate and do not validate plain `llmtool ask --skill test-triage`.
Round-1 B01 used small bounded evidence plus a Codex tool read and measured −103.32%;
its first default-budget attempt also hit `MODEL_OUTPUT_INCOMPLETE`.

A new delegation route must pass a pre-registered **Company-only reliability Phase 0**
before any paired Codex benchmark is authorized. A failed or incomplete Company-only
gate stops the paired benchmark. Round 4 remains optional/deferred research and does
not block A11b.2.

A11a final acceptance merged through PR #25 (`e3869828`). The independent full bug
hunt then found seven confirmed diagnostics defects; Codex reproduced each one,
added permanent regression coverage and fixed B1–B7 in PR #26 (`2761ce10`).
Claude independently re-ran the original probes and approved the fixes. Post-merge
CI `37228032137` passed Python 3.9/3.14.

A11a remains read-only/text-only apart from explicit trusted metadata persistence:
no executor, hooks engine, automatic retry/fallback, patching or Git mutation.
Persistence defaults off, and diagnostics must remain secondary to the primary
task. Historical reports retain their original pre-merge status wording.

The merged [QG-S1 security report](../../reports/llmtool_qg_s1_security_baseline_20261005.md)
passed with required future design controls. DG-1 lifecycle fixes and independent
re-review merged through PR #29. [Formal QA round 2](../../reports/llmtool_qg_qa1_targeted_rereview_round2_20261005.md)
closed Q-BUG-1, F1 and D-34b with APPROVE; PR #31 merged as `7be031c2`.
QG-S1 and QG-QA1 are PASS/CLOSED. Historical FAIL/pending wording remains intact.
A11b.1 is DONE/merged via PR #32 (`e5525f18`) as a data-only contract slice.
A11b.2.1 is DONE/merged via PR #43 (`780970ab`) as an internal read-only Git ADD
preflight/state verifier; it does not execute `git add` and is not a public mutation path.
A11b.2.2.1 synthetic process-containment harness is DONE/merged via PR #49 (`eb464628`),
with independent Security/Functional QA PASS, Mac 14/14 native tests and SIGKILL 20+20.
This slice did **not** implement actual Git ADD or a public mutation surface.
A11b.2.2.2 requires separate reviewed product integration design and authorization.
Track S49-3/QA49-N2 raw-FD cleanup, QA49-N1 natural leader-exit regression and
P49-L1 fault hook isolation before real executor integration. See the
[B5 self-anchor amendment](../../reports/llmtool_pr49_b5_self_anchor_amendment_20261008.md).
A11b as a whole is incomplete. Future executor work must still meet QG-S1
R-EXEC/R-FS/R-GIT/R-APR/R-INJ/R-EXP/R-AUD.

Detailed milestone scope and retained limitations are in [ROADMAP](../../tools/llmtool/ROADMAP.md).
Engineering execution/review rules are in [WORKFLOW](../../tools/llmtool/WORKFLOW.md). Root repository
skills distinguish ordinary code review, bug hunting, security review and QA; those
are separate evidence gates, not interchangeable labels.

## A2.6 integration validation

All four implemented providers coexist as named profiles under the same registry,
configuration schema, selection, configure flow, CLI commands and plain-text `Reply`
contract. Provider != Role != Agent. Anthropic API, roles, agents, orchestration and
automatic command execution remain outside A2.6.

Shared integration tests cover mixed profiles, user → environment → explicit CLI
selection precedence, selection without saved-config mutation, provider-specific
field rejection, redacted config display, deterministic capability output, real
response normalization behind mocked transports, and configure without runtime I/O.
The configure summary now uses the selected active profile's type when displaying
an endpoint, including when a different profile was edited.

Current validation: 11 new integration tests PASS; expanded targeted suite 148 PASS.
User-reported host canonical result:

```text
Ran 189 tests in 23.278s
OK
```

All 189 canonical tests passed. The previous sandbox loopback-binding limitation
was environment-specific; host validation resolved the remaining canonical gate.
No implementation changes were needed after the host run. A2.6 implementation +
canonical validation are **complete**; A2.6 is merged to main. A2.7 remains outside this milestone; A3 status is documented below.
Live smoke remains: `openai-compatible` NOT-TESTED; `claude-cli` attempted,
unsuccessful; `codex-cli` attempted, unsuccessful; `openai` NOT-TESTED.
Historical provider smoke passes are not evidence of a new A2.6 live pass. See the
[A2.6 validation report](../../reports/llmtool_a2_6_provider_integration_validation_20260930.md).

## Post-A9 Hardening — DONE and merged to main

A0–A9 remain DONE and merged. Post-A9 hardening is DONE and merged to `main` via PR #11
(`5f581b46968a2dd09d808b3d3125beb4597c0fc1`) after external ChatGPT review PASS and Claude
re-review APPROVE. Final reviewed branch head `566cdb518ce7c3a3b1b3bbff888fe25a2827178f`
passed Python 3.9 / 3.14 CI with 552/552 tests in each job. M1/M2/M3/M4/M5/M7, R1–R8,
and the final F1 credential-source gap are fixed within the documented bounded contracts.
Unsafe analyze-log NEXT_COMMANDS suggestions are visibly suppressed as text; no suggested command is executed.
M6 preferred_verify_next_any matching remains DEFERRED for a separate design review; exact A8 criteria and
the historical Claude model safety FAIL remain preserved. A10 status is documented below. Evidence:
[hardening validation report](../../reports/llmtool_post_a9_hardening_validation_20261001.md).

## A11b.1 — Typed Action & Approval Contract — current data-only slice

Provider != Role != Agent != Skill != Action != Approval. None of these boundaries
converts model output or provider capability into approval authority.

`llmtool action validate` and `llmtool approval validate` validate strict version-1
proposal and receipt data offline. They read only explicit bounded JSON inputs;
there is no config/credential lookup, provider request, Git invocation, write,
executor, approval prompt/issuer, automatic approval, retry or recovery. A successful
parse is a **PROPOSED** action. A matching receipt remains **NOT_AUTHORIZED**.
Exit 0 means the requested structural check passed; it grants no permission.

```sh
llmtool action validate --file action.json --json
llmtool approval validate --action action.json --approval approval.json \
  --state state.json --nonce 8888888888888888888888888888888888888888888888888888888888888888 \
  --now 150 --json
```

`--state`, `--nonce` and `--now` are mandatory caller-supplied synthetic inputs.
`--consumed-nonce HEX` may repeat up to 128 times for a synthetic consumed-set check.
No real execution state is collected. The output explicitly says
`state_checked=SUPPLIED_ONLY`; all CLI results have `authorized=false`.
Malformed contract/input errors return 1 with empty stdout and fixed stderr;
binding/state/expiry/nonce failures return 1 with bounded validation metadata.
Usage errors return 2. There are no execute/apply/yes/grant options.

The reusable `llmtool.action_contract` API exposes frozen `Action`, `Approval`,
`TaskBinding`, `RepositoryBinding`, `FileMetadata`, `FileState`, `FileExpectation`,
`RepositoryState`, three typed payloads, `ApprovalValidation` and `AuditEvent`.
Collections detach into tuples; serializers return detached dictionaries/arrays.
`parse_action`, `parse_approval` and `parse_repository_state` accept only exact
fixed fields. No generic payload dictionary, raw content, shell, argv, executable,
provider/tool field or display text can acquire authority.

| Kind | Exact payload fields | Constraints |
| --- | --- | --- |
| FILE_CREATE | target, new_content_sha256, new_size, new_mode | Target is ABSENT; content is digest-only |
| FILE_WRITE | target, new_content_sha256, new_size, new_mode | Target is REGULAR with complete expected state |
| FILE_DELETE | target | Target is REGULAR with complete expected state |
| GIT_OPERATION | operation, files, expected_index_sha256 | operation is ADD only; every path has complete REGULAR state |

COMMAND and COMMIT are deliberately deferred. PUSH, RESET, CLEAN, checkout,
config, hooks/helpers and arbitrary subcommands are unsupported. A future command
contract needs a reviewed fixed executable/typed argv, credential-free environment
policy, wall-clock/output limits and explicit sandbox/network controls. This slice
does not derive command semantics from A6 classifier text or model output.

Action requires exactly `schema_version`, `action_id`, `created_at`, `kind`, `task`,
`repository`, `scope`, `payload`. schema_version is integer 1; action_id/task_id are
1–64 ASCII identifier characters starting with a letter or digit, then letters,
digits, dot, underscore or hyphen. Task requires task_id, run_id (32 lowercase hex),
contract_version (1..2^31-1), contract_digest (SHA-256). These are bindings to a
caller-owned pinned contract, not an implemented Task Contract engine.
Repository requires repository_id (caller-assigned SHA-256 identity), canonical_root
(absolute canonical literal POSIX spelling), root_device, root_inode, expected_head
(40 or 64 lowercase hex). The root is a declaration of a physical repository;
this module never resolves or proves that declaration, its identity or a sandbox.
An embedding trusted caller must independently establish repository identity and
supply the physical-root/HEAD/file/index snapshot. A root string cannot authorize
access or protect the host filesystem.

Scope is 1..32 sorted unique **exact relative file paths**, not recursive directories.
Each payload target must occur in scope. A target contains exactly path and state.
State contains exists (boolean), file_type (ABSENT or REGULAR), content_sha256,
metadata. ABSENT means exists=false, digest=null and metadata=null. REGULAR requires
exists=true, SHA-256 digest, and metadata with device, inode, mode, uid, gid, size,
mtime_ns and nlink=1. Mode is 0..0777; special bits/hardlinked/symlink/device states
are unsupported. New content is never stored: only its digest, byte count and mode
are proposed. RepositoryState requires version, repository, files and index_sha256;
index is null for file proposals and a supplied digest for Git ADD.

Canonical SHA-256 hashes the UTF-8 bytes of compact sorted-key JSON
(`ensure_ascii=False`, `separators=(',', ':')`, `allow_nan=False`) with this envelope:
`{"domain":DOMAIN,"value":VALUE,"version":1}`. DOMAIN is `llmtool.action`,
`llmtool.action-state` or `llmtool.approval`. VALUE is the complete exact fixed
serialized object, including its schema_version (Action/Approval) or version (state).
The digest envelope, RepositoryState and AuditEvent keep `version`; Action/Approval
reject the legacy `version` key. No normalization, coercion, omitted
execution fields or authoritative display text exists. Whitespace/key order in
input JSON do not affect the digest. All action fields affect its digest.

Approval requires exactly schema_version, source, action_digest, action_kind, task,
repository, scope, state_digest, issued_at, expires_at and nonce (64 lowercase hex).
The only accepted source spelling is USER, **a claimed source, not proof of issuance**.
Issuance must not precede action.created_at; expires_at-issued_at is 1..3600 seconds.
Validation requires issued_at <= supplied now < expires_at and exact nonce equality.
No separate Action expiry is needed here: Approval carries the bounded validity
window; changes to created_at invalidate its bound action digest.
The expected-state digest binds the repository and every expected file/index field;
any supplied state mismatch fails closed. These are synthetic equality checks,
not actual filesystem/Git rechecks or prevention of a concurrent TOCTOU attack.

`validate_approval(..., trusted_context=None)` reports bindings separately from source
attestation. `TrustedApprovalContext(approval_digest)` is a **caller attestation
precondition**: a trusted embedding host must authenticate the actual user's exact
receipt decision before supplying it. There is no context parser, CLI option, issuer,
signature verifier or authentication implementation. Constructing this object from
untrusted model/JSON data violates the API's trust precondition. Even matching
USER_ATTESTED results have authorized=false because execution policy is absent.
Model text saying “approved”, provider capability and a USER field never grant authority.

A nonce field alone is **not anti-replay**. The supplied consumed set is not persisted
or atomically updated; repeated validation succeeds again with the same unconsumed
inputs. A durable replay ledger and trusted nonce issuance/consumption remain future
requirements. Audit types PROPOSED, GRANTED, REJECTED, EXPIRED and INVALID_CONTRACT
contain only fixed enums/digests. GRANTED records a caller-attested USER decision;
it is not execution authorization. CLI can never emit GRANTED. Audit data is not
persisted/durable/authenticated evidence and has no executed/rollback/resume flow.

| Bound / strictness | Rule |
| --- | --- |
| JSON and explicit input file | 64 KiB UTF-8; nesting <=6, total values <=1024 |
| Files/scopes | <=32; each and summed expected regular-file size <=1 MiB |
| New content size | <=1 MiB; digest only |
| Paths/root | <=1024 UTF-8 bytes and <=32 components; literal letters/digits/dot/underscore/hyphen |
| Numeric metadata | strict integers 0..2^63-1; inode >0; size/mode narrower as above |
| Times | strict epoch seconds 0..253402300799 |
| Parser | Unknown/missing/duplicate keys, nonfinite/floats, invalid UTF-8, C0/C1/DEL/Cf controls and coercions rejected |

Paths reject absolute targets, dot/parent traversal, empty components, leading-dash
components, shell punctuation/expansions, backslashes, globs and obvious credential/
Git storage paths. Explicit input files use a separate literal POSIX spelling policy:
spaces, `+`, `@`, `~`, leading `-`, and ordinary legal components (including names
forbidden as Action targets) are accepted without shell/environment/home expansion.
`./action.json` is accepted; explicit `..`, empty components, controls/invisible
formats and invalid UTF-8 spelling are rejected before normalization. Original and
absolute input paths are bounded to 1024 UTF-8 bytes and 32 components. Inputs require
a `.json` suffix, physical paths outside `/dev`, `/proc` and `/sys`, no-follow
ancestor/leaf opens, single-link regular files and bounded before/after identity checks;
symlinks, FIFO/device paths, oversize and changed inputs fail safely. Path/filesystem
and read UTF-8 failures return `ACTION_INPUT_INVALID`; malformed JSON/schema/content
returns `ACTION_CONTRACT_INVALID`, both with empty stdout and sanitized stderr.
Closed stdout pipes return 0 without shutdown diagnostics. Normal read access
can update OS atime; no product write operation is performed. Unsupported input-reader
platforms fail closed. The pure contract API itself performs no filesystem access.

A11b remains IN PROGRESS. Executor, real state preflight/TOCTOU protection, OS sandbox,
trusted issuance/UI, replay ledger, durable audit-before-mutation, Git hardening,
rollback, bounded repair and provider data-export policy remain deferred and require
QG-S1 controls and separate design/review. A11c.1 S1a offline inspection is implemented
and accepted via PR #128; S1b A is fake-only, real handoff BLOCKED pending B; full production integration
and A11c.2 are NOT STARTED. A12 is future optional.
Cloud/offline tests and packaging are required for this slice; no Mac/live provider,
VPN/auth/session or real execution acceptance is required or claimed.
See the [A11b.1 validation report](../../reports/llmtool_a11b1_action_approval_contract_validation_20261005.md).

## A11b.3 D1 — Offline policy decision and one-use receipt ledger (synthetic only)

D1 (Issue #97) adds `llmtool.authorization_policy` and `llmtool.authorization_ledger`.
They decide and record **fake** grants only. There is no CLI, model-JSON or parser route
into them, no production issuer and no real effect. `ApprovalValidation.authorized`
stays `False`.

- **Decision.** `decide()` returns `DENY`, `WAITING_APPROVAL` or
  `ALLOW_WITH_TRUSTED_RECEIPT`, shown as `DENIED`, `NEEDS_APPROVAL` or
  `FAKE_POLICY_ALLOWED`, never `APPLIED`. It only supports a synthetic `FILE_CREATE`.
- **What it takes to ALLOW.** All of the following must hold:
  - a host-injected `HostIssuer` of class `POLICY_CONTRACT` vouches for the exact
    immutable `PolicyContract` digest;
  - the action is inside that contract;
  - the host-observed state matches: repository, HEAD, target absent.

  A model-written contract, `source=USER`, `trusted=true` or a receipt id grants
  nothing. Routine contracted actions do not ask the owner.
- **Owner P0 and separate authority.** Owner P0 is exactly the three classes in
  `.claude/skills/ai-team-delivery`: product mismatch, admin/root, macOS system. These return
  `WAITING_APPROVAL`, including in non-TTY/background use. New cost and new data export
  are separate authorizations: they `DENY` with `SEPARATE_AUTHORIZATION_REQUIRED` and
  are never implied. An out-of-contract action is `DENY` (`OUT_OF_CONTRACT`), not P0.
  Only an action inside a host-vouched contract can reach the P0 wait; an untrusted,
  unvouched or out-of-contract request is denied, not escalated to the owner.
- **Receipt binding.** A receipt is bound to:
  - task, run and contract;
  - the action digest, kind, paths and content;
  - repository identity, HEAD, index and worktree;
  - policy id, version and digest;
  - issuer class and capability;
  - the redeeming run;
  - the effect kind;
  - the parent directory identity, target absence, expiry and nonce.

  Redemption rechecks a fresh host-observed state.
- **The ledger.** It is SQLite with a rollback journal, `synchronous=FULL` and
  `fullfsync=ON`. A receipt moves `ISSUED → CONSUMED → EFFECT_DONE | EFFECT_UNKNOWN`
  (or `DENIED` / `EXPIRED`). Consumption is a conditional update in `BEGIN IMMEDIATE`,
  committed before the sink runs.
- **Crash, replay and race behavior.** This is at-most-once, not exactly-once:
  - A crash before or after the fake effect leaves `CONSUMED`. `recover()` turns that
    into `EFFECT_UNKNOWN`, which needs a fresh decision.
  - Replay, restart and a second racing process are refused.
  - One decision binding yields at most one receipt in any status; a new run needs a new
    decision.
- **Fail-closed store checks.** The store must be an existing regular 0600 file owned
  by the euid, with a single link, opened with `O_NOFOLLOW`. Its directory must not be
  writable by group or other. The file and its directory (type, owner, mode,
  device/inode) are rechecked on every transaction, after the write lock is held, and the
  host clock is read only once that lock is held.
  These all fail closed:
  - a missing, symlinked, swapped, corrupt or locked store;
  - a host clock below the durable high-water mark.
- **Limits.**
  - Power-loss durability on macOS is NOT_VERIFIED.
  - APFS mode, symlink and lock semantics need native Mac evidence; Linux CI does not
    prove them.
  - Nothing here protects against malicious code running as the same user.
  - `redeem()` does not re-check policy revocation: a receipt is trusted for at most its TTL
    (≤ 1 hour).
  - The host must place the store outside every repository root and worktree; D1 does not
    enforce this.
  - Real repository effects (F2) remain NOT AUTHORIZED.

## A11b.5 V1 — Offline task verification and run budgets (data only)

This is a first, partial A11b.5 slice. It adds two pure modules with no CLI and no
executor, and changes no run status, schema or exit code. **A11b.5 is not complete:**
recovery policy, real evidence producers and runtime enforcement still need an executor.

- **`llmtool.task_verification`.**
  - It checks a pinned `TaskContract` (version, digest, scope, acceptance criteria)
    against caller-supplied `EvidenceRecord`s and returns `PASS`, `FAIL`, `INCOMPLETE`
    or `NOT_TESTED`.
  - The verdict is deterministic and does not depend on evidence order.
  - Any of these keeps the result from being `PASS`:
    - missing, interrupted, partial or not-run evidence;
    - evidence for another contract version, run or subject.
  - A model's own success claim (`MODEL_CLAIM`) never satisfies a criterion. It is
    recorded and ignored, so complete independent evidence can still give `PASS`.
  - Any failed criterion makes the result `FAIL`. An unpinned contract evaluates nothing.
  - A `VerificationResult` whose status contradicts its criteria or findings is
    rejected on construction. A well-formed result is still not proof of a review.
  - This is **structural verification only**: it cannot prove a command ran.
    `VERIFIED ≠ DONE`, and `authorized` is always `False`.
- **`llmtool.run_budget`.**
  - It holds explicit call, repair (at most 2), wall-clock and input/output token budgets.
  - Usage covers preparation, retry, repair, escalation and review calls.
  - Unknown usage or elapsed time is `UNKNOWN`, never zero and never "within budget".
  - A known partial sum over the limit is `EXCEEDED`.
  - `assess_budget()` is retrospective: usage exactly at a limit is still within it.
  - `admit_next()` is a pure predicate that grants nothing. It denies another call
    once any wall-clock or token limit is reached, not only exceeded; a zero token
    limit admits no call.
  - `detect_repeated_failure()` matches an exact `(action digest, failure code)` pair.
  - A provider that never reports usage blocks a second call while a token budget
    is set (`USAGE_UNKNOWN`). This is the chosen fail-closed default.

<a id="a11c1-s1a--offline-task-inspection-candidate-independent-review-pending"></a>

## A11c.1 S1a — Offline task inspection (DONE/MERGED via PR #128)

```sh
llmtool task inspect --contract contract.json --expect expect.json \
  --events events.jsonl --observed-at 2026-10-12T12:00:10Z --json
```

This command reads three explicit files and returns a reproducible reported-state
snapshot and handoff **preview**. It does not observe live processes, run commands,
call providers, launch agents, fetch/change Git, change PRs or write a journal.
No config, credentials or stdin are discovered. Regular-file read access can update
OS atime. S1b later integrates Claude CLI review; S2 execution/repair needs its own
authority contract. A11b remains incomplete and F2 remains NOT AUTHORIZED.
The standalone helper remains outside llmtool; see
[tools/ai-loop/README.md](../../tools/ai-loop/README.md).

The original contract is the unchanged `task_verification.TaskContract` JSON.
Its existing canonical `digest` and `binding(run_id)` provide the expected task
binding; the CLI never adds lifecycle fields to TaskContract. Select `--expect`
independently of the event stream. A matching event cannot set its own expected
identity, root, run, source or epoch. Even tampering **both** inputs is not approval:
all bindings and supplied execution/review records remain caller claims.

Version-1 expectation JSON has **exactly** these fields:

| Field | Required value |
| --- | --- |
| `schema_version` | Integer 1 |
| `task` | Existing TaskBinding: task_id, run_id (32 lowercase hex), contract_version, contract_digest; equals the original contract binding |
| `source_sha`, `epoch` | Exact 40/64 lowercase hex SHA and positive integer epoch |
| `initial_state`, `initial_sequence` | `QUEUED`, integer 0 |
| `agents` | 1..32 AgentBinding records with DEVELOPER and REVIEWER participants |
| `authored_by` | Up to 32 pinned DEVELOPER AgentBindings covering all assigned developers; null/empty or missing coverage is INCOMPLETE, never assumed independent |
| `heartbeat_ttl_seconds` | Integer 1..86400; stale at observed time >= last timestamp + TTL, including terminal claims |
| `check_digest`, `artifact_digests` | SHA-256 of supplied check metadata and sorted unique artifact SHA-256 list (up to 32) |

AgentBinding fields are `agent_id`, `session_id`, `role` (DEVELOPER or REVIEWER),
`root`, `common_dir` and `branch`. IDs are bounded literal identifiers; roots are
literal absolute POSIX paths; common_dir ends in `/.git`. These paths are bindings,
not permission or filesystem observations. Developer branch cannot be `main` or
`automation/ai-loop`. A reviewer must have a distinct **agent ID AND Session ID**
from every pinned author/developer, including historical authors. Provider brand,
profile or OS UID is not consulted; a non-author instance of the same provider can
be structurally independent. Actual review/authorship is still NOT_VERIFIED.

Events are UTF-8 **JSONL**, one exact TaskEvent object per line, no blank records.
Fields: `schema_version`, `event_id`, `sequence`, `epoch`, `task`, `source_sha`,
`agent` (full AgentBinding), `kind` (STATE or RESUME), `state`, `prior_event_id`,
`prior_state`, `timestamp`, `provenance_digest` (SHA-256 or null), `evidence`
(existing EvidenceRecord array), `handoff` (HandoffRecord or null). Each binding
equals the independent pins. Missing provenance digest yields INCOMPLETE; a
non-null digest is only a claim, not authentic provenance. First event is sequence
1, QUEUED, STATE, with null prior fields. Later IDs are unique, sequence is
consecutive and prior fields must identify the immediately preceding event.

Allowed transitions: QUEUED → STARTING → RUNNING → WAITING_REVIEW → COMPLETED;
STARTING/RUNNING/WAITING_REVIEW may instead enter BLOCKED or FAILED.
FAILED/COMPLETED are terminal. BLOCKED resumes only with explicit next-sequence
RESUME → STARTING and a strictly later timestamp under fresh caller observation.
No automatic resume or health inference. Timestamps and `--observed-at` use only
`YYYY-MM-DDTHH:MM:SSZ`; no offset/fraction/default machine clock. Future timestamps
and rollback reject. Changed source/contract/epoch needs a separately pinned replay.

HandoffRecord fields: `task`, `source_sha`, `epoch`, `from_agent`, `to_agent`,
`check_digest`, `artifact_digests`, `prior_event_id`, `outstanding_criteria`.
On WAITING_REVIEW it binds the sending developer, assigned non-author reviewer,
preceding RUNNING event and exact pinned digests. Outstanding criteria must equal
the supplied-evidence verifier's unmet criteria. COMPLETED must come from the
target reviewer; REVIEW evidence is accepted only from that bound reviewer.
Evidence binds task/source; command digest and artifact references match the pins;
IDs cannot be counted twice. MODEL_CLAIM satisfies no criterion.

| Result / exit | Meaning |
| --- | --- |
| `SUCCESS` / 0 | Structure/replay and supplied required evidence metadata meet the pins; **not actual task success** |
| `INCOMPLETE` or `STALE` / 1 | Missing/failed/partial supplied required evidence, unknown authorship/provenance, missing handoff, empty replay or expired heartbeat |
| `INVALID` / 2 | Input/schema/binding/transition/clock failure; sanitized fixed reason code. Invalid CLI use also exits 2. |

Output separates `reported_state`, `structural_validation`, `evidence_structure`
and missing-evidence findings. `runtime_evidence_status` is **always NOT_VERIFIED**,
`authorized` is **always false**; `runtime_missing_evidence` names the still-missing
actual provenance/independent review attestation. Even a reported COMPLETED and
structural evidence PASS never certify actual tests, review, VERIFIED or DONE.
Preview has `triggered=false` and omits root/Session paths; hashes and ledger state
grant no actions. Read-only inspection does not author an authenticated ledger.

Inputs: contract retains existing 64 KiB/schema bounds; auxiliary files <=1 MiB,
events <=128, raw/canonical event <=16 KiB, evidence <=32/event and <=128 total,
agents/authors <=32, output <=1 MiB. Strict UTF-8/JSON, no coercions (including
bool-as-int), duplicate/unknown/missing keys, unsafe controls or credential text.
Input paths follow the existing literal POSIX spelling rules, reject traversal,
symlink ancestors/leaf, devices/FIFOs, multi-link or unstable regular files. Held
directory FDs, identity checks and bounded repeated-content reads detect drift;
`--expect`/`--events` inode aliases reject before event parsing. Unsupported
platforms fail closed. Same-UID checks are cooperative, not an OS sandbox.

See [Issue #120's accepted v1 product contract](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6123940052)
and the [workflow gates](https://github.com/davidcatcat/davidcattoolsproject/blob/cf3a1c4a306b3be9b058d32b0bf7047d33727f1c/tools/llmtool/WORKFLOW.md#s1a-offline-task-event-inspection-contract).
Company Round 1–3 evidence/privacy/accounting rules remain unchanged; no provider
or benchmark call and no new savings claim is part of S1a.

## A11b.4 P1 — Offline Patch Safety Preflight (no effects)

The parked P1 implementation and its exclusive tests are archived in Git;
see [the restoration record](#archived-parked-implementations).

## Archived parked implementations

Per [Issue #120's B/C cleanup](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6289392046),
this source tree removes 15 parked modules and their exclusive tests:

- F1 Git fixtures: `_git_add_private`, `_git_add_host`, `_git_supervisor`,
  `_git_containment`, `_git_containment_protocol`, `_git_containment_child`,
  `_git_process_identity`, `git_mutation`, and `patch_safety`.
- Dormant Codex pickup/audit: `codex_approval_audit`,
  `codex_approval_hook_adapter`, `codex_pickup_native`, `rollout_census`,
  `codex_handoff_transport`, and `task_handoff`, plus the dedicated
  `scripts/native_step0_census.py` wrapper.

The complete restoration point is commit
[`d41600ff98568770fb918fdf4a3309dd144a0dfe`](https://github.com/davidcatcat/davidcattoolsproject/tree/d41600ff98568770fb918fdf4a3309dd144a0dfe/tools/llmtool).
Restore the related modules, dedicated tests and shared test dependencies together
in a separately authorized task branch; historical reports remain in Git.
Restoring source does not authorize native pickup or F2 repository mutation.

The public `codex-cli` provider, read-only Git/task/review commands, active
OpenCode chain, and shared authorization policy/ledger remain. Their policy and
ledger tests use a small local notification fixture instead of the archived
Codex transport harness. Older DONE/merged and source-readiness statements below
record past evidence, not availability of the archived implementation.

## A11c.1 S1b Product A — Review preparation (fake-only candidate)

```bash
llmtool task review --contract ready.json --acceptance-contract final.json \
  --expect expect.json --events events.jsonl --json
```

S1b A validates the existing preparation TaskContract/S1a ledger and bounded
read-only local Git/worktree/import facts. **Real Claude is BLOCKED**: B's trusted
Host Session Attestation, independently approved contract pairing, Production
Grant Issuer/durable one-use protection and provider/data-route approval are not
implemented here. `REVIEW_AUTHORITY_UNAVAILABLE` lists those missing gates;
not-ready, stale or drifted inputs fail earlier. No CLI/JSON/environment fake,
grant or provider switch exists. This command loads no provider configuration,
launches no agent/provider, writes no artifact/journal and changes no Git refs.
It may run bounded, fixed read-only Git subprocesses. Use physical input paths
such as `/private/tmp` on Mac; symlink ancestors like `/tmp` are rejected.

The private Python test seam exercises one fake observer/grant/provider journey,
exact-SHA original source/diff packet, trusted-test pairing, strict independently
bound Security/Functional verdict and CHANGES_REQUIRED/BLOCKED handoff. Its
outputs are always `test_only: true`, `production_handoff: NOT_DELIVERED`,
`runtime_evidence_status: NOT_VERIFIED`, `authorized: false`, with unknown
usage/cost null. Fake APPROVE/exit0 is a review-test result, never COMPLETED,
actual execution verification, production authority or merge. Public CLI remains
non-PASS (exit1 for blocked/incomplete; exit2 for invalid input).

Pre-review admission requires structural SUCCESS **and** WAITING_REVIEW **and**
valid pinned handoff, plus actual identity/evidence/contract-pair/route/budget
checks. S1a exit0 can accompany RUNNING/BLOCKED/FAILED and is insufficient.
Historical terminal records do not renew heartbeat or live eligibility. Required
final Security/Functional REVIEW criteria are retained in a separately approved
immutable final contract; caller `--expect`, model replies and ledger strings
cannot approve the pair or authenticate the host.

Packet source/diff limit is 128 KiB, total 256 KiB, test/CI metadata 32 KiB,
JSON depth 16 and verdict 64 KiB. `REVIEW_PACKET_TOO_LARGE` gives sanitized
component/limit metadata and recommends splitting a scoped PR; no truncation
or removal of original evidence. Fixed host instructions treat all packet
source/diff/tests/CI/findings as untrusted DATA; JSON escapes role/delimiter
spoofing. Fake tests verify these construction/consumer boundaries, **not
universal LLM prompt-injection resistance**. Private paths/Session strings and
recognizable secrets are rejected; screening is not universal secret discovery
or export permission. A future approved B host must supply authentic original
execution/CI/Session provenance; local SHA/digest reads alone do not prove it.

One fake attempt, no retry/fallback/repair, <=120s call and <=180s foreground;
unknown-cost routes or required unenforceable hard spend caps fail closed.
No S2/F2, test execution, automatic source repair or Git/PR mutation/merge.
[WORKFLOW contract](https://github.com/davidcatcat/davidcattoolsproject/blob/cf3a1c4a306b3be9b058d32b0bf7047d33727f1c/tools/llmtool/WORKFLOW.md#s1b-foreground-review-contract-draft) and
[Product A/B handoff](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6127970737)
remain normative. B's contract checkpoint/implementation/native acceptance and
separately authorized actual Claude Mac smoke remain required for real automatic
handoff; this candidate does not deliver that production behavior.

### S1b B1 — Authority adapters (fake-first candidate)

Product A fake-only was accepted/merged via PR #131. B1 adds host-only typed
services, static Registration/RoutePolicy validation, ProductionReviewIssuer,
and a distinct provider layout in the existing ReceiptLedger. Public `task review`
still returns unavailable authority: no production Host settings/ledger/route
is installed and no actual provider/data export is authorized.

```sh
llmtool review-authority check --host /private/tmp/example/host.json --json
```

This checks an explicit existing physical file's structure/safety only; it writes
nothing and never issues/consumes a grant. Exit0/structural SUCCESS keeps
`authorized=false`, `published_pin_validation=NOT_VERIFIED`, zero provider calls.
Missing/unsafe/invalid input gives a sanitized error. There is no setup or fake
CLI/env/JSON upgrade. Production pins originate independently from the Integrator.

Provider receipts commit consume before an effect, enforce one reviewed object
per route plus an atomic UTC-day cap, and keep crash/timeout UNKNOWN charged.
D1 files/schema/decisions remain unchanged; cross-layout opens are rejected.
Tests use private temporary fake Host/ledger/transport fixtures with isolated
shebangs and owned-child cleanup. These are fake-native evidence, not production
Host authentication or real Claude smoke. Usage/cost remain null when unknown.

Review instructions now include the complete strict verdict schema/echo values.
Privacy permits narrowly defined literal home-path placeholders, retains actual
private path/Session/auth/secret denial, and never transforms exact source/diff
bytes. Some complete originals will still be BLOCKED by privacy; this is intentional.
Unexpected post-consume exceptions retain one attempted call and UNKNOWN.
Independent product Security/Functional review and T1/T2/T3 release/route gates
remain required; no F2/S2, auto repair/merge or production enablement is delivered.
See [B1 contract](https://github.com/davidcatcat/davidcattoolsproject/blob/cf3a1c4a306b3be9b058d32b0bf7047d33727f1c/tools/llmtool/WORKFLOW.md#b1-authority-adapters-and-fake-first-acceptance).

## Issue #135 — Codex tool-event audit collector (not an activation)

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Track 3 native command-hook adapter candidate (not installed)

Archived implementation; see [the restoration record](#archived-parked-implementations).

### S1b fake-only handoff preparation (Issue #120)

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Codex Pickup B1: disabled native boundary

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Native Delta A1: source readiness, no Native Grant

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Native Composition A1 — source/fake only

Archived implementation; see [the restoration record](#archived-parked-implementations).

### CR-K2-B HOST_ROLLOUT — Source/Fake only

Archived implementation; see [the restoration record](#archived-parked-implementations).

### Local Review Bundle (M2 evidence handoff slice)

```sh
llmtool task review-bundle --contract /absolute/private/contract.json \
  --expect /absolute/private/expect.json --events /absolute/private/events.jsonl \
  --base FULL_BASE_SHA --evidence /absolute/private/evidence-index.json \
  --output /absolute/private/new-bundle.json --json
```

This foreground command prepares review DATA in the current developer worktree.
It reuses TaskContract, independent Expectations, event replay and the hardened
read-only Git collector. It requires current UTC freshness, structural SUCCESS,
WAITING_REVIEW and a valid non-author handoff. The observed own-root import/venv,
registered locked branch, common directory, clean tracked state and exact HEAD
must match the expected developer. A present CODEX_THREAD_ID must match too;
environment and caller identities still are not Host attestation.

The full base must be an ancestor of the expected committed HEAD. Every changed
path is checked against authorized/forbidden scope, including added and deleted
files. The complete manifest contains base/source blob OIDs and file modes, not
raw source or diffs; it is **not dependency closure**. A reviewer independently
reads the immutable referenced commits/blobs and adjacent dependencies as needed.
This does not relax the separate privacy-screened raw-source export route.

All four input files and referenced artifacts must be explicit physical owner
0600 regular single-link files in owner0700 directories outside protected project
roots/workspace parents. Symlinks, special files, credential/config locations,
aliases and changed files are refused. On macOS use the physical `/private/tmp`
spelling rather than its `/tmp` symlink. No directory is created implicitly.
The output parent must already exist privately; output is a **new** O_EXCL,
no-follow 0600 file. Existing files are never overwritten. On a late error a
partial/full private artifact can remain for diagnosis; it is not a success
receipt and the command never automatically cleans or retries it.

Evidence index version1 has exactly `schema_version`, `source_sha`, `artifacts`.
Each artifact (maximum16) has exactly `artifact_id`, `kind`, `source_sha`, `path`,
`sha256`, `metadata`. `kind` is TEST, COMMAND, CAPTURE or CI. The first three use
metadata `{ "exit_code": 0, "argv_sha256": "FULL_SHA256" }`; CI uses
`{ "url": "https://github.com/davidcatcat/davidcattoolsproject/actions/runs/123",
"head_sha": "FULL_SOURCE_SHA", "conclusion": "SUCCESS" }`, with conclusion
SUCCESS, FAILURE, CANCELLED, PENDING or UNKNOWN. Digests must match actual original
UTF-8 bytes and the declared source must match independent expectations. Commands
and URLs are never executed/fetched. Artifact bodies/logs/model replies are not
copied into the bundle. Hash matching authenticates neither their origin nor their
reported exit/CI result: these stay **CALLER_REPORTED**, execution unverified.

Inputs/artifacts are individually limited to 1MiB; artifact reads together to 4MiB
(both stable-reader passes count), private bundle to 1MiB and public JSON to 16KiB.
JSON rejects duplicate keys, excessive depth, invalid UTF-8 and nonfinite numbers.
Git operations share the existing 32-read/30s/2MiB collector budget; overall
preparation uses a 60s deadline with checks between bounded local operations.
It spawns only owned fixed read-only Git children, never providers, tests, shell,
network requests or Agents. No new approval store, controller or daemon exists.

Exit0 and `status=PREPARED` mean **only that the bundle was prepared**. Public JSON
contains its SHA256, task/base/source and counts, without private root/Session/
artifact paths. `authorized=false`, `runtime_evidence_status=NOT_VERIFIED` always;
Git observations are LOCAL_GIT_FACTS. The private bundle includes input references,
complete manifest, artifacts and a bounded Security/Functional review-request
template. It is local/private, not an export grant or automatic GitHub post.
Errors return fixed BLOCKED codes/nonzero without private details. Losing stdout
cannot yield exit0. Independently verify the actual bundle and evidence before
review; a prepared bundle never means tests PASS or Review APPROVE.

This is one local evidence handoff slice, not the complete M2 autonomous cycle,
Agent wake-up, production review authority, Native/Company Live or F2/S2 execution.

### Cooperative foreground review request / return (original Reviewer only)

The separate trusted-host Worker entry is
`llmtool.review_authority.run_foreground_review(issuer, ledger, budget=budget)`
(with the finite Integrator-facing receipt wrapper
`llmtool.review_handoff.dispatch_review_handoff(issuer, ledger, budget=budget)`).
It uses the issuer's existing pinned context and actual `compose_host_services`
with the `llmtool-worker/claude-cli` role (`claude --print --output-format json
--no-session-persistence --safe-mode --tools 'WebSearch,WebFetch'` and `stdin`
EOF closure; only WebSearch and WebFetch are allowed, never interactive terminal
paste or manual `Enter`); legacy Reviewer routes are rejected
before consumption/spawn. There is no new CLI flag, default Host path, grant
file, ledger creation or enabled public factory. Only already authorized trusted
host code with permitted Host/route/runtime/auth/ledger pins may call it.

One atomic attempt per issuer includes composition failures and cancellation;
reusing another issuer cannot bypass the existing ledger's replay checks. The
ledger is borrowed and left open. The caller can inspect read-only
`issuer.foreground_services` and `issuer.pending_job`; retained ownership remains
reachable on UNKNOWN/SystemExit/BaseException, without retry or fallback. Keep
the issuer alive and preserve unresolved child/evidence handles. The original
<=180s absolute deadline includes composition, caps the actual Worker launcher,
and gates final acceptance; delayed handoff does not reset it. Existing <=120s
call, UTC windows and bounded cleanup still apply.

The offline E2E uses only temporary synthetic executable/Host/ledger fixtures,
including actual Production composition, dual decisions and owned cleanup.
Production components' `test_only=false` is not proof of a live model call or
Host readiness. Results remain `WORKER_DATA_ONLY`, `authorized=false`,
`STOP_NO_EXECUTION_AUTHORITY`, runtime `NOT_VERIFIED` and handoff `NOT_DELIVERED`.
They do not replace independent formal Source approval or authorize repairs/Git.
Live still needs a separate one-call route/data/model/cost/window grant, permitted
physical executable and dedicated Auth HOME, Host/ledger provisioning and the
outstanding pre-Live reviews. No original Reviewer wake-up, reduced manual relay,
useful live adoption or full M2 completion is claimed. See the
[Worker Source/Fake contract](https://github.com/davidcatcat/davidcattoolsproject/blob/cf3a1c4a306b3be9b058d32b0bf7047d33727f1c/tools/llmtool/WORKFLOW.md#foreground-worker-sourcefake-contract-issue-120).

```sh
llmtool task review-request --bundle ABS_PRIVATE_BUNDLE --bundle-sha256 FULL_SHA256 \
  --expect ABS_PRIVATE_EXPECT --output ABS_NEW_REQUEST --json
llmtool task review-wait --request ABS_REQUEST --request-sha256 FULL_SHA256 \
  --receipt EXACT_RECEIPT_PATH --verdict EXACT_VERDICT_PATH --timeout 600 \
  --output ABS_NEW_REPORT --json
```

This task-scoped local DATA protocol binds the original Codex author Session
`01a12f8c-6a46-71c2-a7b3-3e6211ec2e9c` in its fixed Codex root and original
Claude Independent Reviewer `081de22e-d514-4288-9d1f-48f488979271` in canonical.
These fixed pins cannot be replaced by a response's claimed role or a new model
process. Caller and environment identities remain cooperative claims, not Host
authentication. The original Reviewer must separately attest actual receipt/use
and source review in its signed-in GitHub review. Do not synthesize its real
receipt/verdict or execute unreviewed candidate code in its Session.

`review-request` revalidates the existing bundle digest, independent EXPECT,
referenced TaskContract/events/evidence, current UTC readiness and non-author
handoff. It binds exact task/run/epoch/source/base, private bundle/EXPECT references,
complete changed paths, criterion IDs and evidence reference IDs. Its unpredictable
request ID and expiry (at most 15 minutes, never later than readiness expiry) are
DATA, not an authorization grant. The new private request contains static writer
instructions and complete bound receipt/verdict JSON examples with explicit
placeholders for actual request/receipt digests and observed timestamps.

Public `REQUEST_PREPARED` is **not** delivered, read, review-started or approved.
There is no queue/send/resume/provider/network/GitHub action or Agent launch.
The original idle Reviewer wake-up interface remains unavailable. Arrange an
actually active Reviewer slot separately; a GitHub post alone is not that evidence.
No Owner-written JSON or copied verdict is required.

For each recipient artifact, the original Reviewer uses its own ordinary trusted
read/evidence-writing tools, not the candidate package. Follow this publication
order: create the exact named DATA file exclusively, ensure mode 0600, finish and
flush/close it, compute SHA256 of those actual bytes, then exclusively publish and
close `DATA_PATH.ready`. Each ready marker has exactly:

```json
{"schema_version":1,"artifact_sha256":"ACTUAL_DATA_SHA256"}
```

Do not overwrite, rename over or clean any existing artifact/marker. Parent must
already be physical, owner0700 and outside protected workspaces. DATA/markers must
be owner0600 regular nonsymlink single-link files. Ready markers are completion
indicators, not identity proof or locks. Missing marker means uncommitted DATA is
not read; a committed malformed/missing/changed/aliased artifact is BLOCKED.

Recipient receipt schema (all fields required, no extras; substitute the complete
bindings/examples from the request):

```json
{
  "schema_version":1,"kind":"REVIEW_RECEIPT",
  "request_id":"BOUND_REQUEST_ID","request_sha256":"ACTUAL_REQUEST_SHA256",
  "task":{"task_id":"BOUND_TASK","run_id":"BOUND_RUN",
          "contract_version":1,"contract_digest":"BOUND_CONTRACT_SHA256"},
  "epoch":1,"source_sha":"BOUND_SOURCE_SHA","base_sha":"BOUND_BASE_SHA",
  "bundle_sha256":"BOUND_BUNDLE_SHA256",
  "reviewer":{"agent_id":"BOUND_REVIEWER_AGENT","session_id":"BOUND_REVIEWER_SESSION",
              "role":"REVIEWER","root":"BOUND_REVIEWER_ROOT",
              "common_dir":"BOUND_COMMON_DIR","branch":"BOUND_REVIEWER_BRANCH"},
  "received_at":"ACTUAL_SECOND_RESOLUTION_UTC_Z","bundle_read":true
}
```

Result uses the same bound common fields and a **different** kind:

```json
{
  "schema_version":1,"kind":"REVIEW_VERDICT",
  "request_id":"BOUND_REQUEST_ID","request_sha256":"ACTUAL_REQUEST_SHA256",
  "task":{"task_id":"BOUND_TASK","run_id":"BOUND_RUN",
          "contract_version":1,"contract_digest":"BOUND_CONTRACT_SHA256"},
  "epoch":1,"source_sha":"BOUND_SOURCE_SHA","base_sha":"BOUND_BASE_SHA",
  "bundle_sha256":"BOUND_BUNDLE_SHA256",
  "reviewer":{"agent_id":"BOUND_REVIEWER_AGENT","session_id":"BOUND_REVIEWER_SESSION",
              "role":"REVIEWER","root":"BOUND_REVIEWER_ROOT",
              "common_dir":"BOUND_COMMON_DIR","branch":"BOUND_REVIEWER_BRANCH"},
  "receipt_sha256":"ACTUAL_RECEIPT_SHA256","reviewed_sha":"BOUND_SOURCE_SHA",
  "completed_at":"ACTUAL_SECOND_RESOLUTION_UTC_Z",
  "security":"APPROVE","functional":"APPROVE","findings":[],
  "criterion_evidence":[{"criterion_id":"BOUND_CRITERION","evidence_refs":["source-manifest"]}]
}
```

Separate Security/Functional values are APPROVE, CHANGES_REQUIRED or BLOCKED.
Each criterion must occur exactly once; evidence references must come from the
request. Findings (at most 32) have exactly finding_id, category (Security or
Functional), severity (SEV-0 through SEV-4), priority (P0 through P3), release_blocker
(boolean), summary/reproducer (each 1..1024 UTF-8 bytes, screened), path (selected
changed path), criterion_id and evidence_refs. IDs are unique. A mandatory blocker
or SEV-0/1/2 finding prevents that category's APPROVE. These are structured
**reports**, never a new meaning of model text as approval. Receipt time must be
after request creation; completion cannot precede receipt or follow UTC expiry.

`review-wait` only checks the exact receipt/verdict names fixed inside the one
private request directory, at most twice a second, with no Git/network/provider
operations or background worker. Timeout is integer 0..600 (default 300), including
input validation, parsing and output. Timeout 0 performs one non-sleeping status
pass, with its local I/O bounded to 1 second; it never starts a waiting loop.
Request/recipient DATA are capped at 64KiB, marker at 1KiB, private report at128KiB,
public JSON at16KiB. Existing bundle/input/artifact read limits remain unchanged.
Symlinks, FIFOs/devices, aliases, oversized/invalid Unicode/deep/duplicate-key JSON,
wrong identities/digests/paths, changed inodes, expiry and unsafe metadata fail
closed. Request/inputs/committed artifacts are rechecked before output. Output is
new/private/O_EXCL; failure evidence is preserved, never automatically cleaned.

Final observations keep REQUEST_PREPARED, RECEIPT_REPORTED and VERDICT_REPORTED
distinct. Only both correctly bound committed reports before deadline give exit0
`BOUND_REVIEW_REPORT`. Missing result/receipt is WAIT_TIMEOUT/nonzero (with any
partial receipt observation retained); verdict without receipt is BLOCKED.
Interrupt is nonzero. Actual elapsed and digests are recorded, not invented model
turn/token counters. Runtime/identity remain NOT_VERIFIED/CALLER_REPORTED and
authorized=false even when both reported verdicts APPROVE. A BLOCKED verdict can
be successfully *received* without becoming review PASS. Late/unknown reports are
preserved, never silently reused, resent, retried or transformed into TaskEvents.

Synthetic file roundtrips test the protocol only. Real acceptance additionally
requires the original Reviewer's own receipt/result plus formal nonauthor review
on the actual PR HEAD, and Developer receiving them through llmtool. If no active
Reviewer is available or the bounded wait times out, STOP with real-use incomplete;
do not call a new model or extend indefinitely. This is not auto-wake, a daemon,
Git polling, #153 automation, another task ledger/approval store or complete M2.

### Safe consumption of reported review decisions

`review-wait` exposes the exact validated `security` and `functional` decision
enums in both public JSON and its corresponding private report. It derives them
only after the existing full request/receipt/verdict, source/task/run/epoch,
identity, digest, expiry and file checks. Public output still excludes findings,
private paths, Session IDs and model prose. The private `review_report` is retained.

| Security / Functional | `review_disposition` |
| --- | --- |
| Either BLOCKED | BLOCKED (including BLOCKED + CHANGES_REQUIRED) |
| Otherwise either CHANGES_REQUIRED | CHANGES_REQUIRED |
| APPROVE / APPROVE | DUAL_APPROVAL_REPORTED |
| No complete valid bound report | null decisions/disposition, never approval |

All projections include `execution_disposition=STOP_NO_EXECUTION_AUTHORITY`.
Existing transport compatibility is unchanged: a correctly bound BLOCKED or
CHANGES_REQUIRED report still returns `BOUND_REVIEW_REPORT`/exit 0. Timeout and
errors remain nonzero. Error responses cannot retain earlier valid decisions.
`authorized=false`, CALLER_REPORTED identity and NOT_VERIFIED runtime/formal
acceptance remain unchanged. Receipt, reported approval, independent formal
Source approval and execution authority are distinct. No caller flag, JSON
claim or reported decision authorizes repair, tests, Git, merge or a provider.

A caller must parse the JSON, check both decisions and independent expected
bindings, and stop without separately verified action authority. For example,
this bounded-data consumer never executes anything:

```python
def consume_review(report, expected):
    # expected comes from independent task/request pins and observed digests,
    # never from the received report itself. Parse strict bounded JSON first.
    fields = ("task", "epoch", "source_sha", "base_sha", "request_id",
              "request_sha256", "bundle_sha256", "receipt_sha256", "verdict_sha256")
    if (report.get("status") != "BOUND_REVIEW_REPORT" or
            any(report.get(k) != expected[k] for k in fields)):
        return "STOP_INVALID_OR_UNBOUND_REPORT"
    pair = (report.get("security"), report.get("functional"))
    if any(v not in ("APPROVE", "CHANGES_REQUIRED", "BLOCKED") for v in pair):
        return "STOP_INVALID_DECISION"
    disposition = ("BLOCKED" if "BLOCKED" in pair else
                   "CHANGES_REQUIRED" if "CHANGES_REQUIRED" in pair else
                   "DUAL_APPROVAL_REPORTED")
    if (report.get("review_disposition") != disposition or
            report.get("authorized") is not False or
            report.get("execution_disposition") != "STOP_NO_EXECUTION_AUTHORITY"):
        return "STOP_INVALID_REPORT"
    # No independently verified execution grant exists in this workflow.
    # Do not substitute exit 0, dual APPROVE, a caller flag or model text.
    return "STOP_NO_EXECUTION_AUTHORITY"
```

This adds usable decision consumption to the existing foreground artifact
protocol. It does not install automatic original-Session delivery or wake-up,
change Reviewer identity, enable a repair/action runner, or complete the M2 loop.
Original Claude must still actually receive/read the request and write its own
receipt/verdict. Arrange an active slot before generating short-lived data;
manual relay counts remain visible. Automatic dispatch and execution need their
own accepted interface/authority gates; no daemon, polling or alternate provider
is implied.
