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
| Repair implementation review SHA | `546d0187c5aca0c78081edfc3fc80aab8da05f32`; exact independently reviewed implementation |
| Independent review | FINAL PASS at `546d0187`; R1 CLOSED; one repair round used |
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
values. All existing security tests are retained. R1 disposition is CLOSED by final
independent PASS at `546d0187c5aca0c78081edfc3fc80aab8da05f32`. No executor or
approval authentication changes were made.

Historical local evidence on original `50273c4`: 33 new tests PASS (2.211 s),
196 security tests PASS (2.791 s), 1014 canonical tests PASS (50.808 s, no skips),
fresh installed 196 PASS (2.964 s), five console cases PASS and original wheel
SHA-256 `6d32f0dcc1abb96d5fb4629c5e605d983621647d56391a2197689b617d3dcb7d`.
Those results do not validate the repaired schema bytes.

## Final repair-round-1 local validation evidence

All results below follow the R1 schema repair and bind the repair freeze manifest;
the root-owned repair commit is `546d0187c5aca0c78081edfc3fc80aab8da05f32`.
Test fixture receipt helpers are
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
| Diff / docs / freeze manifest | PASS; root verified frozen bytes before implementation commit |

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

## Final independent re-review

Reviewer `/root/a11b1_reviewer` returned FINAL PASS on exact
`546d0187c5aca0c78081edfc3fc80aab8da05f32`. Ordinary review, scoped security,
public CLI QA and negative bug hunt PASS; confirmed new bugs: 0; remaining blockers:
0; R1 CLOSED. The reviewer independently ran 192 focused regressions, PASS in
6.775 s with no skips, plus 46 public source/fresh-installed checks. The public checks
confirmed schema_version acceptance, legacy/missing/duplicate/bool/string/float/
future rejection, stale receipt failure after relevant mutations, sanitized output,
unchanged fixtures and authorized=false. Fresh wheel production identity, manifest
and diff checks PASS. The 1016 canonical result remains developer-run evidence;
the reviewer checked its log and attribution rather than claiming an independent rerun.

Independent regression command, run from the canonical repository with permission
for existing local socket regressions:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/llmtool:tools/llmtool/tests python3 -m unittest -q \
  test_action_contract test_action_contract_cli test_qg_qa1_error_context \
  test_qg_qa1_file_context test_entrypoint_interrupt test_validator test_diagnostics test_post_a9_review
```

The independent 46-check inline Python harness used `subprocess.run`, timeout=5,
captured stdout/stderr and isolated HOME/PATH/LLMTOOL_HOME. It invoked both
`[sys.executable, 'tools/llmtool/llmtool.py', *args]` and
`['/tmp/a11b1-r1-installed-venv/bin/llmtool', *args]`. Args were `action validate
--file action.json --json` and `approval validate --action action.json --approval
approval.json --state state.json --nonce HEX --now 15 --json`, using explicit files
in `/tmp/a11b1-r1-review-final-dhbc8l8v` and a synthetic nonce of 64 repetitions of 1.
Each launcher ran two valid cases, ten rejection variants for each Action/Approval
(legacy, both keys, missing, duplicate, true, string, float, future 2, zero and null),
and one pre-repair stale-digest receipt, totaling 23 each. The harness independently
computed canonical Action SHA-256, checked fixed outputs/authorized=false, unchanged
fixture contents and absent store. Independent outputs/harness are recorded in the
tool transcript; no separate saved script or regression log is claimed. Fixture
JSON remains at the stated directory. The checked fresh wheel SHA-256 is
`2fad5d67b5254030aad25414324e54096cc309bfe2a32fd6dd77f08cfd443fc6`.

An initial independent probe used the old installed venv; that invalid artifact
probe was discarded and the checks rerun against the correctly rebuilt fresh wheel.
It is not included in the final passing evidence. This final report update changes
documentation only after review; production/tests/README remain frozen at `546d0187`.
The eventual report-only commit is not a second code-review SHA. Its publication
HEAD/PR/CI will be recorded once by the coordinator in PR/final handoff, avoiding
a self-referential report SHA loop.

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

Independent repair frozen-SHA re-review is PASS; publication and new-head CI remain
pending at this authoring cutoff. This report does not claim a merge. Executor, sandbox, trusted
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
INDEPENDENT REVIEW: FINAL PASS; R1 CLOSED
FEATURE-HEAD CI: PENDING
MERGED: NO
```

TOCTOU PRECONDITION CONTRACT PASS means supplied synthetic snapshots are bound
and mismatches reject; actual fresh-state/TOCTOU protection is not implemented.

## PR #32 R-1/R-2 repair round 2 — pinned Task Contract

Task `A11b1-PR32-R1-R2-20261005`, version 1, narrows the authorized repair to the two findings
in the preserved [Claude review](llmtool_a11b1_action_approval_contract_review_20261005.md).
Starting implementation is `34383043b76cded9348a4587aa264286f7670f43` on
`feat/a11b1-action-approval-contract-20261005`, base
`7be031c2f8004143d56116717ada5c8599d47d3f`, in the clean configured Cloud checkout
`/workspace/davidcattoolsproject`, origin `davidcatcat/davidcattoolsproject`.

Allowed changes: the offline CLI adapter, permanent CLI regressions, this report
and minimal input-path documentation. R-1 must flush stdout and silence a real
closed OS pipe, returning 0 without shutdown diagnostics for action/approval,
human/JSON. R-2 must separate bounded literal POSIX input spelling from conservative
Action target rules, accepting ordinary legal components (including spaces, `+`,
`@`, `~`, leading `-`, sensitive target names and `./action.json`) without expansion.
Reject explicit `..` before normalization, controls/invisible formats/invalid path
UTF-8, non-JSON suffix, special filesystem roots, links, special/oversized/changing
inputs; preserve anchored no-follow reads and 64 KiB single-link regular-file bounds.
Path/filesystem/read UTF-8 errors must produce ACTION_INPUT_INVALID; malformed
JSON/schema/content must remain ACTION_CONTRACT_INVALID, both with empty stdout
and sanitized stderr. Invalid read UTF-8 is checked at the reader boundary.

Schema, Action kinds/targets, digests, state, nonce/expiry, trusted-context rules,
authorized=false and audit semantics are unchanged. No executor, mutation,
provider, replay/sandbox/recovery or A11b.2 work is authorized. Preserve all earlier
evidence and the historical Claude report. Required Cloud evidence: permanent
failing regressions before production edits, log/patch digest, focused CLI/API and
public OS-pipe/input matrix, canonical check, fresh installed offline smoke,
diff/documentation checks and frozen-file manifest for independent review, followed
by coordinator-owned publication and exact-head Python 3.9/3.14 CI.
MAC REQUIRED: NONE; live provider/auth/VPN and real executor checks: NOT REQUIRED.
Requested Developer model/effort: gpt-6.1-sol/high; actual runtime attestation:
UNVERIFIED. Developer writes source/tests/docs only; coordinator owns Git writes.

The contract text above is preserved in `/tmp/a11b1-r12-task-contract.txt` with
SHA-256 `075b845d0cba0a808bb1154cdc7d4c772ec6843f7fe8655d8b2f016da1ee88bb`.
This round concerns external **R-1** (broken pipe) and **R-2** (input paths/error
classification); the earlier internal **R1** schema_version repair remains historical.

### Fail-first and repaired evidence

Before any production edit, six permanent CLI regression methods ran against
`34383043` and failed in 15.188 s, exit 1, with 141 failed subcases. The preserved
`/tmp/a11b1-r12-beforefix.log` has SHA-256
`16160a0c88f1101fcd3afb4daf56ebc354ca47692e7db96936f7baa5efcc81ff`;
the test/report patch `/tmp/a11b1-r12-beforefix.patch` has SHA-256
`4988e275d11a40c1e19b8faa71e3507ab610133921d62ee00df171a1c861d2e3`.
The pipe assertions were then reordered to preserve the observed return code too,
still before production edits: one method/four cases failed in 0.420 s, each with
rc120 and `Exception ignored`/BrokenPipeError shutdown output. That log is
`/tmp/a11b1-r12-beforefix-pipe.log`, SHA-256
`e555bae1c027219b1aa59c60d1ca0b71a7f9451731c0463ea180b088b15ffcb5`;
its patch is `/tmp/a11b1-r12-beforefix-pipe.patch`, SHA-256
`4c09c51c8a05e9a45a9f1f1bc86be7c69263fe78b3c3ba3542e63b5be34775f0`.
Confirmed bugs in the authorized repair scope: **2**.

R-1 now flushes after stdout write and calls the existing `silence_broken_pipe`
helper before returning 0 on BrokenPipeError. R-2 replaces target `_path` reuse
with an input-only bounded spelling policy and uses reader exceptions classified
as ACTION_INPUT_INVALID. UTF-8 decoding remains at the read boundary; the pure
parser and conservative Action target policy are unchanged. Anchored no-follow
ancestor/leaf opens, nonblocking reads, regular/single-link/64 KiB checks and
before/after descriptor/leaf identity comparisons are preserved.

| Round-2 check | Developer result / artifact |
| --- | --- |
| Contract API/public CLI focused suite | **39 tests PASS**, 16.344 s, no skips; `/tmp/a11b1-r12-contract.log` |
| Canonical syntax + regression suite | **1020 tests PASS**, 68.916 s, no skips; `/tmp/a11b1-r12-canonical.log` |
| R-1 public real OS pipes | **4 PASS**: action/approval × human/JSON; reader closed before child starts; rc0 and empty stderr |
| Ordinary input spelling | **45 public child cases PASS**: 11 directory spellings × action/approval × human/JSON, plus `./action.json`; all four file switches exercised |
| Rejection/error boundary | **68 public child cases PASS**: 17 invalid inputs × four switches; **36 cli.main cases PASS**: nine unsafe spellings × four switches |
| Malformed content / unchanged target policy | **9 public child cases PASS**: legal spaced input with malformed JSON through four switches; five forbidden Action targets |
| Existing filesystem constraints | **PASS**: leaf/ancestor symlink, hardlink (including original alias), directory/FIFO/device, oversized/invalid UTF-8, exact 64 KiB and changing-file regressions retained; reader error assertions tightened |
| Fresh wheel/installed console | **40 cases PASS**, including five requested directory spellings, all four switches, malformed content, invalid read UTF-8, five forbidden Action targets and four real pipes; `/tmp/a11b1-r12-installed-smoke.log` |
| Wheel/installed production identity | **PASS**: action_contract.py, action_contract_cli.py, cli.py and terminal.py equal repository bytes |
| Diff/docs/unchanged historical evidence | **PASS**, scoped to four files; freeze manifest `/tmp/a11b1-r12-freeze.json` |

Subcase totals are separate coverage measures, not additive unittest method counts.
All new subprocess tests run the public module launcher with bounded timeouts,
synthetic inputs and isolated HOME/absent product state. Pipes are real OS pipes,
not mocked streams. Accepted input names additionally cover `.git`, `.aws`,
`key.pem`, literal `$HOME`/`;` and Unicode; no expansion or execution occurs.
Rejected spellings include explicit traversal before normalization, NUL/C0/C1/DEL/Cf,
surrogate invalid UTF-8, byte/depth bounds and non-JSON suffixes. Special-root probes
fail before opening `/dev`, `/proc` or `/sys` files. `a b.py`, `$HOME/file`,
`file;touch`, `.git/config` and `key.pem` remain invalid Action targets.

The tested production/test diff `/tmp/a11b1-r12-tested-code.patch` has SHA-256
`b7fde3e72072b29ea25929925ac2d3e4a375260e1b9b08d68debbb17685f251f`.
Focused and canonical log SHA-256 are respectively
`f8551aeab6bf712337588f3f843063fcc0da35d9fe27148e41dc80b257ffae85` and
`0b71dc70f08efd92b312b48e3f8b010213c380ff1f3b630078f4508566abeeb4`.
Fresh wheel SHA-256 is
`d4d7016b27c7add3666e5908d394c5576e94524539972cc62572871ff60929cc`.
The starting Action contract source remains byte-identical, SHA-256
`1e38d2acfd68b3d237c58f0b425410125ff2f22283a962579b1f2311d0676f9e`.

Commands run from the configured checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/llmtool/tests:tools/llmtool \
  tools/llmtool/.venv/bin/python -m unittest -v test_action_contract test_action_contract_cli
make -C tools/llmtool check PYTHON=/workspace/davidcattoolsproject/tools/llmtool/.venv/bin/python
tools/llmtool/.venv/bin/python -m pip wheel --no-index --no-build-isolation \
  --no-deps --wheel-dir /tmp/a11b1-r12-wheels ./tools/llmtool
tools/llmtool/.venv/bin/python -m venv /tmp/a11b1-r12-installed-venv
/tmp/a11b1-r12-installed-venv/bin/python -m pip install --no-index --no-deps \
  /tmp/a11b1-wheels/pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl \
  /tmp/a11b1-r12-wheels/llmtool-0.1.0-py3-none-any.whl
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/llmtool/tests:tools/llmtool \
  tools/llmtool/.venv/bin/python /tmp/a11b1-r12-installed-smoke.py
git diff --check
```

All repaired checks above exited 0. Canonical tests used authorized network access
for existing local loopback mocks and `/var/tmp` empty fake-Codex runtime fixtures;
no live provider was used. The installed smoke invokes only the fresh console
script, clears PYTHONPATH in its child environment, and uses synthetic temporary
fixtures; no project checkout is copied. Cloud Python is 3.12.14. All required
runtime checks for this repair are CLOUD CAPABLE; MAC REQUIRED: NONE.

### Deferred execution design and handoff status

- **D-1 — case-insensitive path identity: DEFERRED.** No target canonicalization or
  case-folding redesign is introduced in this data-only slice.
- **D-2 — Git metadata / .gitattributes: DEFERRED TO A11b.2.** Input filename
  acceptance grants no Git execution authority; future Git operations need their
  own reviewed metadata, hooks/config/helpers and attribute controls.
- **D-3 — actual fresh state / replay / audit: DEFERRED.** Supplied synthetic state
  checks are unchanged; there is no real preflight, durable consumed ledger or
  audit-before-mutation implementation.
- **D-4 — parent-directory identity: REQUIRED BEFORE REAL FILE EXECUTOR.** Before
  any real filesystem executor is introduced, fresh execution preflight must
  bind/verify parent-directory identity or hold anchored directory descriptors so
  an approved path cannot be redirected by ancestor replacement.

The existing input-reader checks cover their bounded descriptor/leaf matrix;
neither those checks nor synthetic approval binding establish full TOCTOU safety
for a future executor. No real filesystem executor is introduced here.

```text
R-1: FIXED; DEVELOPER REGRESSION PASS; INDEPENDENT REVIEW PENDING
R-2: FIXED; DEVELOPER REGRESSION PASS; INDEPENDENT REVIEW PENDING
SCHEMA: UNCHANGED
ACTION TARGET POLICY: PASS; BYTE-IDENTICAL
REAL MUTATION CAPABILITY ADDED: NO
AUTHORIZED: ALWAYS FALSE
READY FOR INDEPENDENT REVIEW: YES
INDEPENDENT R-1/R-2 REVIEW: PENDING
EXACT-HEAD PYTHON 3.9/3.14 CI: PENDING
MERGED: NO
```

The coordinator will record the exact independently reviewed implementation SHA
once after the implementation commit and review. Publication SHA and current CI
belong in the PR/final handoff. Earlier PASS/CI tables remain historical evidence,
not substituted proof for this round. Developer stops writing at the frozen-file
handoff; coordinator is the sole Git writer. No publication or merge is claimed.
