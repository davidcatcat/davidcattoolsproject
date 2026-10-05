# llmtool A11b.1 Typed Action & Approval Contract validation — 2026-10-05

## Result and pinned Task Contract v1

The implemented slice is an immutable, bounded, offline proposal/receipt contract.
It adds no execution or project mutation capability. Matching structural approval
bindings remain **NOT_AUTHORIZED**, including when a trusted caller supplies an
attestation context. A11a is DONE; QG-S1 and QG-QA1 are PASS/CLOSED; A11b is IN
PROGRESS with A11b.1 current; A11c is NOT STARTED; A12 is future optional.

The accepted goal is strict version-1 typed Actions, exact immutable Approval
bindings, deterministic canonical digests, synthetic stale-state/expiry/nonce
checks, bounded safe audit metadata and public offline validation commands.
Allowed repository edits are the contract/API, offline CLI adapter and minimal CLI
registration, permanent tests, current-state documentation and this report.
Acceptance requires all relevant field mutations to change the digest or fail
closed, old receipts to fail after valid mutations, no model/JSON approval authority,
no implicit config/auth/provider access, and full offline plus installed-package checks.

Forbidden product scope includes command execution, shells/subprocesses, file/Git
mutation, patch/test execution, apply/execute/yes, approval issuance/authentication,
automatic confirmation, UI, remote Telegram approval, providers, sandbox execution,
recovery/retry/rollback/resume, persistent replay/audit and A11b.2. Development tests,
builds and root-owned Git publication are separate authorized repository work.

| Provenance | Evidence |
| --- | --- |
| Canonical repository | `/workspace/davidcattoolsproject`; no clone/worktree |
| Feature branch | `feat/a11b1-action-approval-contract-20261005` |
| Exact starting main | `7be031c2f8004143d56116717ada5c8599d47d3f` |
| Documentation-first checkpoint | `0a112dbe3da35f44e0b5a0eef9e1984ea39ffecd`, six current-state docs only |
| Original implementation review SHA | `50273c4ac0bbf22dce7113c7eaf5dfad7ece5d6b`; historical round-1 object |
| Repair implementation review SHA | PENDING root-owned repair freeze commit; no own-SHA bookkeeping loop |
| Independent review | Round 1 REQUEST CHANGES (R1 only); repair re-review PENDING; maximum two repair rounds |
| Publication / PR / feature-head CI | PENDING; final publication SHA belongs in PR/final handoff |
| Historical baseline CI only | [37317208697](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37317208697), main `7be031c`, Python 3.9/3.14 SUCCESS |

The historical CI above is not evidence for the new implementation. Baseline PR
#31 merged at 2026-10-05T13:29:31Z. Current-state updates follow the merged
[QG-S1 security baseline](llmtool_qg_s1_security_baseline_20261005.md) and
[QG-QA1 round-2 APPROVE](llmtool_qg_qa1_targeted_rereview_round2_20261005.md).
Historical reports were not edited. Future QG-S1 R-EXEC/R-FS/R-GIT/R-APR/R-INJ/
R-EXP/R-AUD controls remain required before a real executor is designed.

## Threat model and authority boundary

Provider != Role != Agent != Skill != Action != Approval. Provider capability,
agent role and skill guidance do not grant Action or Approval authority.

Assets are exact proposal identity, pinned task/run/repository/scope/state bindings,
host filesystem and credentials, and sanitized validation/audit output. Untrusted
sources include model/provider text, supplied JSON and claimed USER receipts.
Threats tested include malformed/ambiguous JSON, unsafe paths and shell fragments,
coercion/unsupported versions, mutable nested data, digest omissions, stale state,
receipt substitution, expired or wrong nonces, source spoofing, implicit input
discovery, credential/provider access and output leakage.

`source="USER"` is a structural claim. `validate_approval` does not authenticate
issuance. A trusted embedding host must authenticate an actual direct user decision
for the exact receipt before supplying `TrustedApprovalContext(approval_digest)`.
This context is a caller-supplied attestation prerequisite, with no JSON parser,
CLI switch, issuer, signature verifier or authenticated issuance feature. Constructing
it from model/JSON data violates the API trust precondition. MODEL source and model
text saying approved cannot attest or authorize. `authorized` is always false.

Repository identity is caller-supplied: repository_id SHA-256, declared canonical
physical root, device/inode and expected HEAD. The module never resolves that root,
probes a repository or proves the declaration. An independently trusted host must
establish repository identity and state. A root string/scope restriction is neither
a sandbox nor authentication. Task/run identifiers bind caller-owned contracts;
they do not implement a Task Contract engine or approval authority.

## Exact schemas, kinds and bounds

Action fields are exactly schema_version, action_id, created_at, kind, task, repository,
scope and payload. schema_version is strict integer 1. Identifiers are 1..64 ASCII
letters/digits/dot/underscore/hyphen, starting with a letter/digit. Task fields are
task_id, run_id (32 lowercase hex), contract_version (1..2^31-1) and contract_digest.
Repository fields are repository_id, canonical_root, root_device, root_inode and
expected_head (40/64 lowercase hex). All SHA-256 fields use 64 lowercase hex.

| Supported kind | Fixed typed payload | Expected precondition |
| --- | --- | --- |
| FILE_CREATE | target, new_content_sha256, new_size, new_mode | ABSENT target |
| FILE_WRITE | target, new_content_sha256, new_size, new_mode | Complete REGULAR target |
| FILE_DELETE | target | Complete REGULAR target |
| GIT_OPERATION | operation=ADD, files, expected_index_sha256 | Complete REGULAR state per path and index digest |

COMMAND and COMMIT are deferred. Empty argv/executable, shell/command/tool/env
payloads and arbitrary Git subcommands are rejection cases, not fictional command
execution coverage. Future commands need fixed allowlisted executables and typed
argv, no credential inheritance/expansion, timeout/output bounds and explicit
network/sandbox controls. PUSH/RESET/CLEAN/checkout/config/hooks/helpers are unsupported.

Scope and file collections detach into immutable sorted unique tuples and contain
at most 32 exact relative file paths. Every payload path must be in scope. File
expectation fields are path and state; state fields are exists, file_type,
content_sha256 and metadata. ABSENT requires false/null/null. REGULAR requires
true/digest and all metadata fields: device, inode, mode, uid, gid, size, mtime_ns,
nlink=1. Symlink/device/hardlink/special-mode states are unsupported. New content
is digest-only: no content bytes or generic payload dictionary are stored.
RepositoryState fields are version, repository, files and index_sha256 (null for
file proposals, required digest for ADD). Frozen nested models and detached
serializers prevent later input-list/dictionary mutation from changing an action.

| Hard bound | Enforced limit |
| --- | --- |
| JSON / explicit input | 64 KiB UTF-8, nesting <=6, values <=1024 |
| Paths and root | <=1024 UTF-8 bytes, <=32 components |
| Expected files / scope | <=32; each and sum of expected sizes <=1 MiB |
| Proposed new size | <=1 MiB, digest-only |
| Numeric metadata | strict integers 0..2^63-1; inode positive; mode <=0777 |
| Epoch seconds | 0..253402300799 |
| Approval validity interval | 1..3600 seconds |
| Supplied consumed nonces | <=128 unique SHA-256-form strings |

Unknown/missing/duplicate keys, nonfinite/floating numbers, boolean/int coercion,
future versions, invalid UTF-8 and C0/C1/DEL/Cf controls fail closed. Literal path
components allow letters/digits/dot/underscore/hyphen; absolute targets, parent/dot
traversal, empty/leading-dash components, backslashes, globs, shell punctuation,
expansions and obvious credential/Git storage paths are rejected. These restrictions
are data checks, not filesystem containment proof or comprehensive secret detection.

## Canonicalization, receipt and synthetic state checks

Each digest is SHA-256 over exactly UTF-8 encoded sorted-key compact JSON with
`ensure_ascii=False`, `separators=(',', ':')` and `allow_nan=False`:
`{"domain":DOMAIN,"value":VALUE,"version":1}`. Domain is `llmtool.action`,
`llmtool.action-state` or `llmtool.approval`. VALUE contains every exact serialized
field, including schema_version for Action/Approval or version for RepositoryState;
the digest envelope also retains version. Action/Approval reject the legacy version
key without aliases or compatibility coercion. There are no omitted execution-relevant fields,
coercions or authoritative display text. Input whitespace/key order do not matter.
Tests independently build these exact bytes, including Unicode and domain separation.

Approval fields are exactly schema_version, source, action_digest, action_kind, task,
repository, scope, state_digest, issued_at, expires_at and nonce. Validation requires
exact Action/kind/task/run/repository/HEAD/scope/state binding, issued_at >= created_at,
issued_at <= supplied now < expires_at, exact expected nonce and optional supplied
consumed-set exclusion. Action has no separate expiry; the receipt supplies its
bounded validity window and changing created_at invalidates its bound Action digest.

`current_state` is mandatory caller-supplied synthetic state. Exact equality includes
repository identity/root/device/inode/HEAD, each file's existence/type/digest and
every metadata field, and ADD index digest. Missing/extra/mismatching state fails
closed. No actual filesystem/Git execution state is read. This demonstrates binding
and mismatch behavior, not fresh execution preflight or TOCTOU protection.

A nonce alone is **not anti-replay**. The consumed-set check has no persistence,
atomic update or trusted issuance. Repeating validation with unchanged unconsumed
inputs succeeds again. Durable nonce issuance/ledger/consumption and fresh state
checks remain deferred executor requirements.

Audit types are PROPOSED, GRANTED, REJECTED, EXPIRED and INVALID_CONTRACT, with
fixed version/enums and appropriately required action/approval digests. Malformed
audit combinations fail closed. No raw identifier, root/path, nonce, secret/API key,
provider stderr or reasoning is included. Helpers are data-only bookkeeping and
carry no authentication/authority. GRANTED denotes caller-attested metadata, never
permission to execute; CLI never emits GRANTED. Audit is not persisted, durable or
authenticated and has no fabricated EXECUTED/recovery flow.

## Public offline CLI and safe input reads

`llmtool action validate --file action.json [--json]` returns PROPOSED, authorized=false.
`llmtool approval validate --action action.json --approval approval.json --state state.json
--nonce HEX --now EPOCH [--consumed-nonce HEX] [--json]` always returns NOT_AUTHORIZED.
Matching bindings return 0; mismatches return 1 with fixed safe codes. Usage returns
2. Malformed contract/input errors have empty stdout and fixed sanitized stderr.
Normal output includes only fixed labels/kinds/digests, not arbitrary input tokens.

Only explicitly named .json inputs are read, with POSIX directory-relative anchored
O_NOFOLLOW ancestor/leaf opens, nonblocking leaf access, single-link regular-file
checks, 64 KiB limit and before/after descriptor/path identity comparison. Symlinks,
hardlinks, FIFO/device paths, traversal and changing inputs fail closed. Unsupported
reader platforms fail closed. No implicit discovery, config/credential lookup,
provider/network/Git factory, subprocess, project write or approval prompt occurs.
Reads may update OS atime; no kernel-level unchanged-atime claim is made.

## Independent round 1 and R1 repair

Read-only reviewer `/root/a11b1_reviewer` reviewed exact
`50273c4ac0bbf22dce7113c7eaf5dfad7ece5d6b` against base `7be031c` and returned
REQUEST CHANGES for one acceptance blocker, R1. Action and Approval used `version`
instead of the requested `schema_version`. Independent public console fixtures
`action-schema.json` and `approval-schema.json` both returned 1 with empty stdout
and fixed sanitized error, rejecting the required valid spelling. The affected
contract declarations/validators and parser lookup are `action_contract.py` lines
325/336, 368/382 and 685/693 (original source); this was a schema interoperability
defect, not an execution or authority escalation. Reviewer reported no other ordinary
blocker or confirmed security bug: 59 independent QA probes PASS plus these two R1
failures, and 190 independent regressions PASS in 6.009 s after the authorized
network rerun, no skips. Historical installed packaging matched the original source.

Authorized repair round 1 renames only Action/Approval's version field to
`schema_version`, strict integer 1, without legacy aliases. State/audit/envelope
`version` remains unchanged. Updated fixed schema inventory/mutation checks and
two permanent API/public CLI methods independently assert both contracts accept
schema_version and reject legacy/both/missing/duplicate/bool/string/float/future
values. All existing security tests are retained. R1 disposition is FIXED LOCALLY;
independent re-review is PENDING the root-owned repair commit. No executor or
approval authentication changes were made.

Historical local evidence on original `50273c4`: 33 new tests PASS (2.211 s),
196 security tests PASS (2.791 s), 1014 canonical tests PASS (50.808 s, no skips),
fresh installed 196 PASS (2.964 s), five console cases PASS and original wheel
SHA-256 `6d32f0dcc1abb96d5fb4629c5e605d983621647d56391a2197689b617d3dcb7d`.
Those results do not validate the repaired schema bytes.

## Final repair-round-1 local validation evidence

All results below follow the R1 schema repair and bind the repair freeze manifest;
the root-owned repair commit SHA is pending. Test fixture receipt helpers are
development data constructors, not product issuance APIs. No tests were weakened.

| Check | Final result |
| --- | --- |
| New contract/API + public CLI | **35 tests PASS**, 2.325 s |
| New + security/context/feedback regressions | **198 tests PASS**, 3.021 s |
| Canonical `make -C tools/llmtool check` with venv Python | **1016 tests PASS**, 52.265 s; compileall PASS, no skips |
| Fresh installed wheel same security regressions | **198 tests PASS**, 2.917 s |
| Installed console | Five cases PASS: version/help/proposal/matching receipt/expired receipt; expected exits 0/0/0/0/1 |
| Wheel production-source identity | All three production module bytes equal tested repository bytes |
| Wheel SHA-256 | `2fad5d67b5254030aad25414324e54096cc309bfe2a32fd6dd77f08cfd443fc6` |
| Diff / docs / freeze manifest | Final checks recorded at root-owned freeze handoff |

Focused invocation: `PYTHONPATH=tools/llmtool/tests:tools/llmtool tools/llmtool/.venv/bin/python
-m unittest -v test_action_contract test_action_contract_cli`. Combined security modules:
test_action_contract, test_action_contract_cli, test_validator, test_post_a9_review,
test_post_a9_hardening, test_context and test_feedback. Canonical invocation:
`make -C tools/llmtool check PYTHON=/workspace/davidcattoolsproject/tools/llmtool/.venv/bin/python`.
Wheel build: `tools/llmtool/.venv/bin/python -m pip wheel --no-index --no-build-isolation
--no-deps --wheel-dir /tmp/a11b1-wheels ./tools/llmtool`; installed into a fresh
runtime-only venv with local PyYAML 6.0.3 wheel. Installed tests import site-packages,
not the source package. Console tests use an isolated empty HOME/absent product store.

The independent fixed-schema leaf inventory covers repository_id/root/device/inode/
HEAD, task contract version/digest/IDs/run, action ID/time/scope, target path and every
state digest/type/existence/metadata field, new digest/size/mode and Git index digest.
Valid mutations change digest and invalidate the old receipt; fixed enum/version/type
mutations reject. Public CLI repeats the complete mutation/state matrices.

| Action mutation kind | Cases | Valid changed digest + stale receipt rejected | Strict rejection |
| --- | ---: | ---: | ---: |
| FILE_CREATE | 23 | 15 | 8 |
| FILE_WRITE | 30 | 23 | 7 |
| FILE_DELETE | 27 | 20 | 7 |
| GIT_OPERATION ADD | 29 | 21 | 8 |
| Total | **109** | **79** | **30** |

Additionally: 69 synthetic-state leaf mutations (12 CREATE; 19 each other kind),
19 receipt leaf mutations, valid kind/path/existence alternatives, fixed independent
schema enumeration and two-file ADD per-path preconditions. Method totals and
subcase totals are separate; they are not additive suite test counts. Initial test
harness expected 110 instead of the actual enumerated 109; explicit inventories
confirmed all leaves, the count assertion was corrected and no cases removed.

Permanent hostile-input/zero-capability tests cover exact unknown/missing/duplicate
fields, versions/kinds/types/limits/controls/NaN/UTF-8, nested immutability, shell/path
injection, stale repository/HEAD/files/metadata, wrong/expired/consumed nonce, source
spoofing and arbitrary authority fields. Public entrypoint tests tripwire actual input
read allowlists, all write flags/writers, config/auth paths/environment, providers,
sockets/subprocesses and Git factories. Module/direct launcher tests install hostile
Git/provider/shell executable markers and verify none run. Parser display may read
standard locale/terminal-size environment only; no product config or credentials.

Initial local existing-socket tests required authorized network permissions; reruns
used local socket/loopback mocks only, with no live provider. A standalone focused
invocation missing tests in PYTHONPATH failed import and was corrected to the command
above; final repaired 35-method execution passed. Test/dev fixtures do not grant product rights.

## Environment, review boundary and remaining limitations

Cloud Linux x86_64 kernel 6.18.44, Python 3.12.14 venv, pip 25.0.1, setuptools 84,
wheel 0.48 and PyYAML 6.0.3. Runtime has no configured credentials/identities/secrets;
no auth/session/private paths were inspected. Developer `/root/a11b1_developer`
and independent reviewer `/root/a11b1_reviewer` were requested as gpt-6.1-sol/high;
actual model/effort are **UNVERIFIED** where runtime attestation is not exposed.
Requested Git model gpt-5.6-luna was UNAVAILABLE; coordinator `/root` performs
Git mutation as the documented fallback, with no Git agent created. Source/test/doc
author performs no Git mutation.
Authorized local fixture permissions include empty `/var/tmp` runtime fixtures and
loopback mocks; `/tmp/.git` guard was preserved. No live provider/VPN was used.

**CLOUD CAPABLE:** all contract tests, public CLI, canonical regressions, packaging
and CI. **MAC REQUIRED:** NONE. **NOT REQUIRED:** real provider/auth/VPN, remote
approval or real executor acceptance. Fresh input-reader behavior is POSIX; unsupported
platforms fail closed, while pure contract validation is filesystem-independent.

Independent repair frozen-SHA re-review and new-head CI remain pending at this authoring
cutoff. There is no merge recommendation before review. Executor, sandbox, trusted
issuance, durable replay/audit, fresh actual state/TOCTOU controls, Git mutation and
provider export policy need their own future design. Final publication SHA/PR/CI
will be recorded once by the coordinator without self-referential report commits.

```text
REAL MUTATION CAPABILITY ADDED: NO
ACTION EXECUTION IMPLEMENTED: NO
MODEL CAN GRANT APPROVAL: NO
ACTION DIGEST BINDING: PASS
APPROVAL EXACT-BINDING: PASS
TOCTOU PRECONDITION CONTRACT: PASS
OFFLINE / ZERO-PROVIDER VALIDATION: PASS
READY FOR INDEPENDENT REVIEW: YES
INDEPENDENT REVIEW: ROUND 1 REQUEST CHANGES; RE-REVIEW PENDING
FEATURE-HEAD CI: PENDING
MERGED: NO
```

TOCTOU PRECONDITION CONTRACT PASS means supplied synthetic snapshots are bound
and mismatches reject; actual fresh-state/TOCTOU protection is not implemented.
