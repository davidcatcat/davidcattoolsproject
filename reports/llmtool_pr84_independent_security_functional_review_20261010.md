# PR #84 — independent security and functional QA review

Review date: 2026-10-10 (Asia/Taipei)  
Repository: [davidcatcat/davidcattoolsproject](https://github.com/davidcatcat/davidcattoolsproject)  
Source PR: [#84](https://github.com/davidcatcat/davidcattoolsproject/pull/84)  
Requirement: [Issue #70](https://github.com/davidcatcat/davidcattoolsproject/issues/70)  
Decision: **REQUEST_CHANGES**

This report records an independent review of the complete F1-r3 diff, including the original implementation and Claude's test-only repair. Previous implementation reports and repair approvals were treated as submitted evidence, not independent proof. No real user-repository Git ADD was performed. No demonstrated escape from the disposable fixture into the canonical repository was found; the failures and evidence gaps below prevent approval.

Publication is a separate documentation change. It does not modify PR #84, product code, tests, the ai-loop controller, or the reviewed branch; it does not approve or merge that PR. Private native evidence and raw diagnostics are not included.

## 1. Exact identity and review boundary

| Item | Verified value |
| --- | --- |
| Environment | Actual local Mac; canonical repository; shell sandbox restrictions described below |
| Canonical root | /Users/davidsun/git/davidcataiporject/davidcattoolsproject |
| Repository | davidcatcat/davidcattoolsproject |
| Origin | git@github.com:davidcatcat/davidcattoolsproject.git |
| Reviewed branch | feat/a11b222-f1-private-git-add-20261010-r3 |
| PR HEAD / local HEAD | 36f972e07465d0b5a8e984b8b766d06142178cfa |
| PR base branch | main |
| PR base SHA | 0b679537c487ae3eaa21702d64c96ec89ff88145 |
| Actual merge-base | 0b679537c487ae3eaa21702d64c96ec89ff88145 |
| Original Codex implementation | 9a8f9c3cd5be5318cea22e3088cd48e16e7dad10 |
| Claude repair | 36f972e07465d0b5a8e984b8b766d06142178cfa |
| Complete PR scope | 2 commits; 12 files; +1,983 / -14 |
| Working tree during review | Clean; canonical index unchanged |
| PR disposition | Open, draft; no approval or merge |

The diff was reviewed against the actual merge-base, not merely the last repair commit. GitHub metadata and local immutable objects were checked; HEAD remained the reviewed SHA during review and the publication precheck. This report applies only to these bytes. A different source PR HEAD makes it **REVIEW_STALE** and requires a new review of the changed scope.

Repository guidance read included AGENTS.md, relevant inspection/review/security/QA/runtime skills, WORKFLOW.md, ROADMAP.md, the merged F1 implementation contract, the B5 specification, the self-anchor amendment and design delta, and Issues #70 and #83. The contract's private-fixture-only authority and the requirement for a distinct final gate remain in force.

## 2. Full scope reviewed

All twelve changed files were included:

| File | Scope |
| --- | --- |
| [reports/llmtool_a11b222_f1_r3_mac_acceptance_20261010.md](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/reports/llmtool_a11b222_f1_r3_mac_acceptance_20261010.md) | Submitted acceptance report and its pending status |
| [tools/llmtool/llmtool/_git_add_host.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_add_host.py) | Private Host admission and ADD lifecycle |
| [tools/llmtool/llmtool/_git_add_private.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_add_private.py) | Launcher, fixture provenance, runtime binding, supervision |
| [tools/llmtool/llmtool/_git_containment_protocol.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_containment_protocol.py) | Framing and file-hash descriptor handling |
| [tools/llmtool/llmtool/_git_process_identity.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_process_identity.py) | Product Git process inventory and identity |
| [tools/llmtool/llmtool/_git_supervisor.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_supervisor.py) | Separate product domain and ownership |
| [tools/llmtool/llmtool/git_mutation.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/git_mutation.py) | Preflight and deterministic post-state classification |
| [tools/llmtool/tests/run_containment_acceptance.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/run_containment_acceptance.py) | Native evidence runner |
| [tools/llmtool/tests/test_git_add_authority.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_add_authority.py) | Authority and negative tests; Claude FD repair |
| [tools/llmtool/tests/test_git_add_private.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_add_private.py) | Private ADD tests; Claude conditional skip |
| [tools/llmtool/tests/test_git_containment.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_containment.py) | Baseline containment harness |
| [tools/llmtool/tests/test_git_mutation.py](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_mutation.py) | Classification assertions |

Related unchanged code, fixture construction, public CLI/import boundaries and the containment probe harness were inspected where necessary to trace the complete execution path.

## 3. Ranked findings

No CRITICAL or HIGH finding was demonstrated. Three **MEDIUM** findings block the requested gate.

### M1 — Required Python 3.14 exact-head CI fails

**Status:** Confirmed functional/acceptance failure.  
**Locations:** [test_git_add_authority.py:46](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_add_authority.py#L46), [_git_add_private.py:241](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_add_private.py#L241), [_git_containment_protocol.py:348](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_containment_protocol.py#L348).

The product-manifest test calls runtime(), which hashes the interpreter using wire.file_hash(). The hash helper rejects metadata that is not a regular file, permits group/other writes, has an unexpected owner, or exceeds its 64 MiB bound. Both exact-head GitHub Actions runs fail in this path on Python 3.14 with ProtocolFailure.

**Direct remote evidence:**

- [Push run 37971683740](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37971683740): Python 3.9 succeeds; [Python 3.14 job 113959617131](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37971683740/job/113959617131) fails, 1,332 tests, one error, skipped=16.
- [Pull-request run 37971724314](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37971724314): Python 3.9 succeeds; [Python 3.14 job 113959753997](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37971724314/job/113959753997) fails with the same test/error pattern.
- Both runs identify source HEAD 36f972e07465d0b5a8e984b8b766d06142178cfa.

The available traceback identifies the rejecting metadata block; it does **not** prove which predicate rejected that interpreter. In particular, the binary-size hypothesis must not be presented as an established root cause.

**Impact:** The required supported-version acceptance gate is red, and this runtime-binding path is not demonstrated to work on Python 3.14. This is not evidence of an authorization bypass.

**Suggested correction:** Reproduce with the failing interpreter, inspect its metadata without exporting sensitive diagnostics, identify the exact predicate, and make a bounded correction consistent with trusted-runtime integrity. Preserve no-follow, ownership, permissions and hashing protections. Do not suppress the manifest test or classify the failure as an acceptable skip. Require successful exact-head Python 3.9 and 3.14 CI after repair.

### M2 — Top-level mismatch negative test rejects before reaching its intended guard

**Status:** Confirmed false-positive negative coverage.  
**Locations:** [test_git_add_authority.py:303](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_add_authority.py#L303) and the production top-level guard at [_git_add_private.py:311](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_add_private.py#L311).

test_marker_symlink_owner_ambiguity_and_toplevel_mismatch renames the bound marker, replaces it with a symlink, checks rejection, and then renames the original marker back before patching the Git top-level query to return /wrong. The rename changes the marker fingerprint, including ctime. Restoring the pathname does not restore that binding.

**Independent reproduction:** Using a freshly generated disposable fixture, the same sequence produced:

| Observation | Result |
| --- | --- |
| Marker fingerprint changed | true |
| Final fixture_check outcome | AUTHORITY_REJECTED |
| Patched top-level query calls | 0 |

The second assertion passes because the earlier provenance check rejects. It would still pass if the intended top-level guard were removed. The production guard is present; this finding is about the test's claimed evidence, not a demonstrated product bypass.

**Impact:** A required private-repository boundary has false-positive coverage, which can conceal a regression.

**Suggested correction:** Use a separate fresh valid fixture for the top-level mismatch case. First establish that its unmodified fixture_check succeeds. Then return the wrong top-level, assert that the query was reached, and assert rejection. Do not rebind the restored marker to bypass the creation-order invariant. This defect is in the original implementation commit, not introduced by Claude's repair.

### M3 — Native acceptance does not exercise failure containment in the new product domain

**Status:** Confirmed coverage/design-evidence gap; no exploit demonstrated.  
**Locations:** [_git_add_host.py:66](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_add_host.py#L66), [_git_supervisor.py:232](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/llmtool/_git_supervisor.py#L232), [run_containment_acceptance.py:14](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/run_containment_acceptance.py#L14), [test_git_containment.py:1428](https://github.com/davidcatcat/davidcattoolsproject/blob/36f972e07465d0b5a8e984b8b766d06142178cfa/tools/llmtool/tests/test_git_containment.py#L1428).

The fourteen native scenarios continue to use the baseline probe/fake containment harness. Adding F1 product files to the evidence manifest establishes which integrated bytes were present; it does not make those scenarios execute the separate new product supervision domain.

The real private ADD happy path is exercised. Existing tests also exercise helpers, mock selected admission/resource outcomes and run synthetic containment. They do not independently demonstrate the full new owned product path after READY under timeout, cancellation, status-pipe loss, uncertain Git inventory or escaped descendant conditions.

**Proof:** Tracing the acceptance scenarios reaches the probe parent and fake domain; tracing the new product path reaches _product_main. Those are distinct execution paths. The SIGKILL repetition evidence belongs to the former.

**Impact:** Required product failure containment, audit classification, operator-wait retention and cleanup cannot be signed off from baseline PASS counts alone.

**Suggested correction:** Add a bounded test-only harness that runs the actual product ownership path with deterministic controlled failure conditions. Demonstrate Git is not spawned after preflight refusal, and demonstrate cancellation, timeout, status/inventory uncertainty and containment retention after READY, including signal handling and descendant ownership. Avoid production fault flags or broader mutation authority. Re-run native Mac acceptance on the corrected HEAD. Treat this as an unresolved specialist acceptance requirement rather than a proven source-code escape.

## 4. Security checklist

PASS below is scoped to the evidence stated in its row; it is not blanket execution approval.

| Control | Result | Evidence and limitation |
| --- | --- | --- |
| Private Host admission; fixed inherited FD; FIFO modes and exact inventory | PASS | Source traced; real descriptor tests and native private ADD happy path. Generic helper tests use actual allocated descriptors; native launch covers the fixed production descriptor. |
| Canonical framing, UTF-8, size/depth/atom bounds, deadlines, EOF and broken pipes | PASS | Parsing checks and real pipe tests reject malformed/oversized input; framing covers a stream larger than pipe capacity. |
| Fresh nonce, replay rejection and parent continuity across complete Host admission | NOT_VERIFIED | Source binds nonce and process continuity; complete unmocked negative product admission coverage is insufficient for an end-to-end PASS. |
| Manifest/module identity and trusted interpreter binding across supported CI versions | FAIL | M1; Python 3.14 manifest test fails on the exact HEAD. |
| Disposable provenance, realpath, root identity, marker owner/mode/link count and creation order | PASS | Fixture-only launcher, canonical overlap rejection, marker fingerprint/nonce and pre-Git creation checks traced; targeted real rejection tests. |
| Symlink and root-inode drift rejection | PASS | Targeted fixture checks; no arbitrary caller-supplied production repository interface found. |
| Git top-level mismatch negative evidence | FAIL | M2; intended query is never reached in the named test. |
| Git preflight, action digest, HEAD/branch/path/index scope | PASS | Scoped immutable action and pre/post observations traced; targeted happy and rejection paths exercised. |
| Atomic TOCTOU protection against a concurrently hostile same-UID actor | NOT_VERIFIED | Checks are repeated observations, not an atomic OS security boundary; trusted same-UID installation/launcher premise remains necessary. |
| Git hooks, attributes, filters, fsmonitor, external commands, config/environment/network surfaces | PASS | Closed configuration admission, attributes rejection, disabled hooks and fsmonitor, sanitized environment and restricted protocol/config invocation traced; post-index-change hook sentinel remains unexecuted in fixture tests. No network/external-helper escape was demonstrated. |
| Supervisor ownership, child identity, PGID/SID, natural leader exit and descendants | PASS | Ownership/identity source and actual private ADD happy path; baseline native lifecycle checks pass. Failure product domain remains M3. |
| Actual product-domain timeout, SIGINT/SIGTERM/SIGKILL and uncertain post-state | NOT_VERIFIED | Baseline native results do not close M3. |
| UNKNOWN fail-closed operator-wait and audit behavior | PASS | Retention/emit-failure helper behavior and classification checks support the intended rule; complete real product failure lifecycle remains NOT_VERIFIED. |
| File-hash FD ownership and cleanup on wrapping failure | PASS | Exception cleanup and preservation of original failure exercised repeatedly (100 repetitions). |
| All product failure-path descriptor/process cleanup | NOT_VERIFIED | M3; selected helpers and successful path do not prove every failure path. |
| F1 authority boundaries and public exposure | PASS | No production CLI, provider execution capability or broader ADD interface added; no expansion to F2 or real user-repository mutation found. |

The design's trusted same-UID installation premise matters. A nonce, module hash and marker continuity are useful admission controls, but are not independent authentication against an arbitrary hostile process sharing that user's authority. The launcher constructs its fixture and does not accept a caller-selected repository or execution callback. Source review found repeated independent fixture/root checks before the actual ADD.

## 5. Functional QA

| Behavior | Result | Evidence and limitation |
| --- | --- | --- |
| Valid private tracked edit and newly added file | PASS | Real Git ADD only in launcher-created disposable fixtures. |
| No-op classification | PASS | Expected post-state matching the pre-state classifies APPLIED under the specified first-match rule. |
| Cross-request replay/idempotency across one persistent fixture | NOT_VERIFIED | Separate launcher calls create fresh fixtures; they do not prove persistent-fixture replay semantics. |
| Deterministic post-state/error precedence | PASS | Expected state, control interruption, index-lock/ref/layout user-action conditions and unknown-state precedence checked. |
| Malformed and unauthorized framing | PASS | Real parsers/pipes and targeted rejection cases; not merely all mocked behavior. |
| Complete native unauthorized Host admission | NOT_VERIFIED | Some complete admission cases rely on patched identity/ownership outcomes. |
| Malicious config/helpers/attributes and fixture drift | PASS | Targeted actual guards reject; top-level mismatch coverage is separately FAIL under M2. |
| Product failure-path cleanup and final audit result | NOT_VERIFIED | M3. |
| Public CLI / general Git mutation exposure | PASS | Static interface/import review found no unintended production execution interface. |
| Required exact-head Python 3.9/3.14 acceptance | FAIL | 3.9 CI succeeds, 3.14 CI fails under M1. |
| Clean complete Mac suite independently reproduced | NOT_VERIFIED | Submitted 1,332-test PASS claims were not independently reproduced; sandbox-limited reruns are recorded below. |

Focused independent tests ran 35 tests with the venv interpreter, zero skips, PASS; the system interpreter also ran 35, PASS with one explicit skip. These tests choose the venv subprocess interpreter when available, so the system-parent run is not proof that every positive Host subprocess executed under system Python 3.9.

## 6. Claude's two test-only repairs

Git inspection of commit 36f972e07465d0b5a8e984b8b766d06142178cfa confirmed **only** these two files changed:

1. tools/llmtool/tests/test_git_add_authority.py.
2. tools/llmtool/tests/test_git_add_private.py.

No product safety check was weakened.

### Descriptor isolation

The repaired FIFO/inventory test runs endpoints() in a fresh child with close_fds and an explicit pass_fds set. It accurately isolates the inherited descriptors relevant to admission instead of accidentally relying on directory descriptors retained by prior unrelated tests.

The test checks legitimate read/write inventory, access direction and the non-inheritable post-admission state. It adds a genuine unexpected directory descriptor opened on / with O_DIRECTORY and confirms rejection. It also retains rejection of unsuitable regular/socket descriptors and wrong inventory. The unexpected directory case exercises the real inventory rejection, not a mocked result.

The helper uses actual allocated read/write descriptor numbers; it does not assume the allocator always gives descriptor 3. Production fixed-FD inheritance is checked by the actual native launcher path. The descriptors are allocated and passed synchronously; no inherited-descriptor race was demonstrated in this test. This supports the repair, while leaving the separate Issue #83 accumulation investigation open.

### System-Python importability skip

The added condition checks whether sys.executable -I can import llmtool. A failed import causes an explicit unittest skip. The independent focused system-Python run observed that skip; the venv case executed.

This is a reasonable way to avoid treating an unavailable isolated package as containment evidence. However, unittest's overall OK with skips does not establish mandatory Mac acceptance. The native acceptance runner must still produce fourteen substantive scenarios with no empty/skip substitute, and the natural-leader-exit case must execute with the available installed interpreter. The independent native run did so.

The skip therefore cannot be counted as a containment PASS. It does not itself enforce the separate required acceptance gate; that enforcement and evidence must remain explicit. No product bypass or relaxed ownership rule was introduced.

## 7. Evidence and CI verification

### Submitted Mac evidence that was directly inspected

The Mac-private native repair JSON was available locally and was inspected rather than assumed from the PR body. Its SHA-256 is:

99d3b69f82d8d2f457e8b4e3b9c7032dfc5c82fad7450d1c835b0f7f4b9ab57a

It identifies Darwin/macOS 15.7.9 and Python 3.13.3, fourteen successful substantive scenarios, no failure entry, and twenty plain plus twenty Popen SIGKILL repetitions. All reported repetition containment/absence checks were true. The twelve tested file digests matched the reviewed bytes, including both repaired tests.

The exact maximum submitted SIGKILL latency was **0.012420938001014292 seconds**. The PR's 0.012-second statement is rounded, not a separate precise measurement.

This verifies the contents and byte binding of that submitted artifact. It is distinct from independent execution and does not convert baseline harness coverage into product-domain coverage. The private JSON and raw logs were not exported.

### Independent execution

All review test commands were launched from the canonical repository. An OS sandbox denied file writes under that repository and Python -B prevented bytecode writes. Temporary fixtures belonged to test/runtime creation only, not another project checkout. No worktree or clone was created.

| Check | Direct result |
| --- | --- |
| Focused authority/private tests, venv Python 3.13.3 | 35 tests, PASS, zero skips; 14.630 s |
| Focused authority/private tests, /usr/bin/python3 3.9.6 | 35 tests, PASS, one skip; 14.628 s |
| Native containment scenarios, installed venv | 14/14 substantive PASS |
| Native parent SIGKILL, plain ownership | 20 repetitions; all containment/absence assertions true |
| Native parent SIGKILL, Popen ownership | 20 repetitions; all containment/absence assertions true |
| Independent maximum SIGKILL latency | 0.006379797006957233 s |
| Native retained process check | Zero retained processes |
| M2 fresh-fixture reproducer | AUTHORITY_REJECTED; top-level query calls=0 |
| Changed Python AST parsing | 11 files parsed successfully |
| Whitespace check against actual PR range | PASS |

Native scenarios were fd_inheritance, sigint, sigterm, sighup, hung_supervisor, leader_exit_descendant, identity_reuse, macos_start_time, kqueue_non_reaping, protocol_framing, code_runtime_binding, public_import_boundary, parent_sigkill_plain and parent_sigkill_popen. The first twelve produced substantive checks; each SIGKILL case produced twenty repetitions.

The native runner's scenario methods were invoked directly without writing its report output into the repository. Those results validate the existing acceptance harness on these bytes, subject to M3.

### Complete suite limitations — not a clean PASS

| Independent attempt | Result |
| --- | --- |
| Venv custom full-suite harness | 1,332 tests, skipped=3, failures=0, errors=54, exit 1; 239.534 s |
| System-Python custom full-suite harness | 1,332 tests, skipped=4, failures=5, errors=54, exit 1; 369.170 s |
| Corrected package-cwd rerun of affected existing diagnostic methods | Three methods PASS; 8.778 s |

These were custom read-only review harnesses, **not** successful canonical make check runs. The 54 errors encountered sandbox-denied /bin/ps in existing lifecycle tests. The five system-Python failures came from the review harness's repository-root cwd: existing diagnostic subprocesses could not import llmtool. That avoidable harness mistake was corrected by running those three existing methods from tools/llmtool; the three methods and their subcases then passed.

These environment/harness failures are not evidence of five or 54 new F1 defects. They also are not a basis to report the complete suite PASS. No independently verified clean full Mac suite is claimed.

The PR's submitted final claims of **1,332 tests, skipped=3** in the venv and **1,332 tests, skipped=4** in system Python remain **NOT_VERIFIED** as clean final-run results. The available full-run log inspected was from an earlier failing run, not a complete final repaired PASS log. Test counts alone prove neither execution safety nor cleanup.

### Exact-head CI

Both remote workflow runs were verified at 36f972e07465d0b5a8e984b8b766d06142178cfa; results and links are in M1. Python 3.9 succeeded on both triggers. Python 3.14 failed on both. The PR body's earlier “pending on this push” wording is superseded by those directly checked outcomes.

CI success on one interpreter does not close native Mac requirements, and native baseline success does not resolve the failing supported interpreter or M3.

### Reproduction approach

Representative focused review invocation used the following repository-write-denying wrapper, from the canonical root. The system-Python run substituted /usr/bin/python3 for the venv binary.

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny file-write* (subpath "/Users/davidsun/git/davidcataiporject/davidcattoolsproject"))' \
  tools/llmtool/.venv/bin/python -B -c '
import sys, unittest
sys.path.insert(0, "tools/llmtool")
sys.path.insert(0, "tools/llmtool/tests")
suite = unittest.defaultTestLoader.loadTestsFromNames(
    ["test_git_add_private", "test_git_add_authority"])
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
'
```

The M2 reproducer used only a new disposable fixture: create the fixture; save the marker fingerprint; rename marker to saved-marker; replace marker with a symlink; confirm refusal; remove the symlink and rename the original marker back; patch _Reads.query to return /wrong; invoke fixture_check and inspect the query call count. The binding was deliberately not changed. Expected observed call count is zero, proving the false-positive coverage.

## 8. Separate concerns and remaining risks

[Issue #83](https://github.com/davidcatcat/davidcattoolsproject/issues/83) tracks suspected pre-existing directory-FD accumulation. The repaired inventory test now distinguishes a legitimate isolated descriptor set from a genuine injected directory descriptor. The product launcher closes unrelated inherited descriptors, and the file-hash wrapping-failure cleanup was separately verified. The broader source of accumulation was not independently attributed. It should remain a separate bounded investigation rather than an invented F1 escape finding.

A broader read-only process scan found twenty-one older test-model processes in seven groups, with seven reparented leaders, dating from October 8. The initial and final scans had the same PID set and no new processes of that older population. They were left untouched. The PR's earlier six-process observation and this broader scan are different inventories; neither by itself attributes a failure to F1.

No new F1 Host, supervisor or synthetic acceptance child was observed remaining after the review's completed acceptance runs. “No leftovers from the final acceptance run” is a scoped statement; it does not mean the entire machine had no older orphaned test processes.

Remaining risks are the unclosed supported-version runtime-binding failure, false-positive boundary coverage, incomplete product-domain failure containment evidence, and absence of independently reproduced clean final Mac full-suite logs. Trusted same-UID runtime and repeated-observation TOCTOU assumptions must remain explicit. F2 and real user-repository mutation remain unauthorized.

## 9. Required handoff and final decision

**Decision: REQUEST_CHANGES.** PR #84 is **not ready** for a distinct final Security/Functional QA gate. These blockers must be addressed first:

1. Diagnose and fix M1 without weakening runtime trust controls; obtain green exact-head Python 3.9 and 3.14 CI.
2. Repair M2 using a fresh valid fixture and proof that the intended top-level rejection path was reached.
3. Close M3 with actual product-domain native failure/lifecycle evidence, including uncertainty retention and owned-descendant cleanup.
4. Supply or rerun the complete final Mac suites in a suitable authorized environment, retaining clean logs and explicit skip accounting. Do not promote the independent sandbox-limited attempts to PASS.
5. Pin the repaired PR HEAD, repeat affected validation, and request the distinct independent final Security/Functional QA gate. A changed HEAD makes this report stale.

The implementer and subsequent reviewer can consume this report directly from GitHub; the owner does not need to relay prompts or copy private evidence. The receiving AI should read the pinned source diff, contracts and relevant repository skills independently, reproduce each finding, and distinguish test corrections from product changes. This report is a handoff artifact, not repair approval, automatic execution authority, or a merge decision.
