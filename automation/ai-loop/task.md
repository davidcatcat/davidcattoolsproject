# CURRENT exact FAIL-review recovery — 2026-10-08 / run 60914

Run `20261008T052643Z-60914` stopped after all five related suites PASS at exact
unpublished candidate `35ad4be92f6492fa86a8bc2fe961a22cf9c84a49`,
base `ceae425eacaa8729a9eb8d796e43304918a8c908` (merged PR #48).

Claude's ordinary read-only independent review completed as **FAIL**, not formal
security approval. Two exact blocking test-only findings are in:
- `tools/llmtool/tests/run_containment_acceptance.py`
- `tools/llmtool/tests/test_git_containment.py`

Both still pin `63e232e22a769f409e381e34dfe0ef1add766d8e`. The current
runner pins exact base `ceae425eacaa8729a9eb8d796e43304918a8c908`.
No test or production containment behavior is approved as PASS; required Mac
native acceptance will fail before the first scenario without this fix.

The subsequent failure-classification Claude CLI call exited 1 and both stderr
files were empty. This **does not establish quota/auth as the cause**.
The exact ordinary FAIL review is already a sufficient bounded repair basis;
do not retry or silently substitute the failed classification. Preserve local
raw model output and provider logs.

Control runner now supports a dedicated `review_failure_recovery` contract:
- original control `2a919119459a4042e45115bc4934a7319f461740`;
- exact stopped HEAD `35ad4be92f6492fa86a8bc2fe961a22cf9c84a49`;
- stopped summary SHA-256
  `921919abad240e84a5b79f6e3c9a35706bbfa939b7b161e87d0c693d96b57d24`;
- original local `failure-0.json` SHA-256
  `5340bb23e53cf76107f9999fbbdee94fa136d3ccdfbb6fba6e4e8f0dce0244de`;
- exact same unchanged base, task scope and mandatory gate commands;
- exact blocking review finding file set = the two test paths above.

The runner checks the original task/summary/provenance/failure hashes,
candidate SHA-256 for all ten allowlisted source/test/report files, an
unpublished clean branch, and no active ai-loop Codex process. It snapshots
the candidate before any model call; it may then call Codex **only** for a
two-file strict-base repair and an optional truthful pending-report note.
The first repair cannot touch production modules, other tests, task contracts,
signal authority, real containment thresholds, fourteen scenario names or
exact PASS requirements. Preserve all historical evidence, no retroactive PASS.

Update both old-base literals to the **exact complete**
`ceae425eacaa8729a9eb8d796e43304918a8c908` SHA. Keep strict equality;
do not derive main SHA dynamically, accept both bases, or skip preflight.
Rerun related checks, read-only ordinary review, both full Python suites and
all fourteen mandatory native Mac scenarios on changed bytes. Native PASS,
independent security/QA acceptance, PR publication and merge are pending.

First Mac operator action, only after control helper CI success:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T052643Z-60914 --check
```

`--check` only verifies exact recovery contract; no model, test or source
changes. A PASS is required before the same command without `--check`.
Do not manually change candidate, rebase, reset, cherry-pick, publish the
feature branch or rerun older 43467 recovery.

# CURRENT post-PR48 out-of-scope TEST_BUG recovery — 2026-10-08

Run `20261008T035727Z-43467` stopped on unpublished A11b candidate
`0dc05b75844f9caa4c4ccf9c28c8b21972fd25f9`.

Round 0 reached real Mac containment after the stale-base harness repair. The
candidate passed fd_inheritance, SIGINT, SIGTERM and SIGHUP, then
`hung_supervisor/unresolved` hit a Darwin EPERM in the test controller's passive
disappearance poll. Claude classified that exact native failure
`REPAIR / TEST_BUG`; Codex repaired only the allowlisted controller test while
retaining the same deadlines, signal authority and unresolved-on-no-proof rules.

On the changed round-1 bytes, related checks, developer review and ordinary
review passed. The venv full suite passed. Mac system Python 3.9 full-final then
failed only in
`test_entrypoint_interrupt.EntrypointInterruptTests.test_repeated_real_subprocess_startup_import_and_parser_interrupts`:
after the child had written its product-controlled ready marker and the parent had
sent SIGINT, the test controller's `communicate(timeout=5)` expired once during
the 1242-test full suite. The same exact targeted Mac Python 3.9 test subsequently
passed 10/10, each complete 9-subprocess invocation taking about 3.9-4.35 seconds.

Claude's original exact-evidence classification was
`NEEDS_DECISION / TEST_BUG`: the blocking root-cause file
`tools/llmtool/tests/test_entrypoint_interrupt.py` is outside the A11b allowlist.
That pairing is semantically valid: category describes root cause; decision
describes whether the current task is authorized to repair it. The control helper
now encodes that distinction instead of rejecting this result as
`CATEGORY_DECISION_MISMATCH`.

Independent Draft PR #48 changed only
`tools/llmtool/tests/test_entrypoint_interrupt.py`: it retained the exact exit
130 / empty stdout / exact interrupted stderr assertions and changed only the
post-SIGINT safety wait from 5 seconds to 10 seconds, matching the existing
real-subprocess startup budget. No retry, skip, production entrypoint/CLI change,
signal policy change or assertion weakening was introduced. Exact-head CI passed
on Python 3.9 and 3.14. PR #48 was squash-merged to main as
`ceae425eacaa8729a9eb8d796e43304918a8c908`.

Current stopped-failure recovery pins:
- run `20261008T035727Z-43467`;
- original control `a831946fc75823e2a97296a4d5f33bc375f3dc8e`;
- stopped candidate `0dc05b75844f9caa4c4ccf9c28c8b21972fd25f9`;
- old base `63e232e22a769f409e381e34dfe0ef1add766d8e`;
- new main `ceae425eacaa8729a9eb8d796e43304918a8c908`;
- round-1 failure SHA-256
  `26d51d0a067e7b408998a2fd633645765d3f7f85a51fcb069689903bf1c94dd1`;
- original Claude raw envelope SHA-256
  `fcea8633beb151df4248a07e6749864a0f7ac7b38bd662ce1c4db73ef807ac5b`;
- prior round-0 Mac evidence SHA-256
  `9baed935a39d56b6a0c8dd10e3a47da317b0ac972039d117c97881fdae411b83`;
- sole approved upstream path
  `tools/llmtool/tests/test_entrypoint_interrupt.py`.

Recovery revalidates the raw Claude envelope privately and requires every blocking
finding to equal the approved upstream path set. The raw provider envelope is
hash-checked but is not copied into exportable recovery evidence. It also performs
a fresh read-only absence proof for the retained round-0 Mac owned PID inventory;
saved IDs are never signaled.

If all exact pins hold, the runner snapshots the stopped A11b bytes, creates a
two-parent merge with exact new main, proves all A11b candidate bytes are unchanged
and the upstream test file equals main, then starts with deterministic validation.
No prior PASS is inherited. Both full suites and all fourteen Mac native scenarios
must pass again before publication.

First operator action after helper CI is green:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T035727Z-43467 --check
```

Do not rerun the old 35739/7365 recoveries and do not manually rebase,
cherry-pick, reset, stash or publish the feature branch.

# CURRENT Mac preflight harness recovery — 2026-10-08

Run `20261008T033528Z-35739` successfully integrated the separately merged
PR #47 test-infrastructure fix into the unpublished A11b.2.2.1 candidate. On
candidate `8bada855a99ce4660c66537922f3ad833b8669fb`, related checks,
ordinary Claude review, the venv full suite and the Mac system-Python full suite
all passed. Mandatory Mac containment then stopped in 0.3 seconds at harness
preflight before any native scenario ran.

The exact failure is a bounded **TEST_BUG**, not a containment result:
`tools/llmtool/tests/run_containment_acceptance.py` still pins the old base
`585caf7d4c9dad20afd3beb38e5140d6dadf89e0`, while the current task is
strictly pinned to post-PR47 main
`63e232e22a769f409e381e34dfe0ef1add766d8e`. A regression fixture in
`tools/llmtool/tests/test_git_containment.py` carries the same old literal.

Claude's published evidence-bound review for round 0 returned
`REPAIR / TEST_BUG` and named only those two blocking test files. The runner
previously stopped with `MAC_EVIDENCE_INCOMPLETE` because the preflight failure
correctly had zero scenarios and zero tested native hashes. Control-plane logic
now distinguishes this narrow case from a real native failure: a preflight repair
is permitted only when the native JSON is Darwin/schema-v1, scenario
`preflight`, scenarios `{}`, tested_sha256 `{}`, owned_processes `[]`,
Claude classifies `TEST_BUG`, and every blocking finding is under
`tools/llmtool/tests/`. No missing native evidence becomes PASS.

Current exact recovery pins in `task.json`:
- run: `20261008T033528Z-35739`
- original control: `b65b31712abdb4f2809047911ba136944f320385`
- stopped HEAD: `8bada855a99ce4660c66537922f3ad833b8669fb`
- summary SHA-256:
  `7a10a6b35bf7a297d2a974ada90cb77cd33221eea05dfcac24551a4865b5a573`
- Mac preflight JSON SHA-256:
  `d12a698dd959efb93164fe3174db7e473ca3e889a3b99057c92a127abdce2c9a`
- failure evidence SHA-256:
  `7ea19918439e64bfb488697a76dfc1f5dcad45b193cf39c1295635039f78facf`
- authorized repair files only:
  `tools/llmtool/tests/run_containment_acceptance.py` and
  `tools/llmtool/tests/test_git_containment.py`.

Bounded repair requirements:
1. Update the acceptance harness exact base pin from `585caf7...` to exactly
   `63e232e...`. Keep the equality check; do not accept both bases, derive it
   dynamically, or delete the pin.
2. Update the synthetic regression contract in `test_git_containment.py` to the
   same new base so it still reaches the intended failure-export assertions.
3. Preserve all existing assertions, identity checks, scenario list, timeout and
   disappearance thresholds, no-skip rules, PASS schema, nonzero failure exit,
   fixture cleanup and source-hash binding.
4. Historical report text remains historical. The runner may append truthful
   current evidence later; do not rewrite old evidence as if it originally used
   the new base.
5. After changed bytes, all related checks, both full suites and all fourteen Mac
   containment scenarios run again. No PASS is inherited from run 35739.

First operator action after control CI passes is read-only preflight:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T033528Z-35739 --check
```

Do not manually edit, rebase, cherry-pick, reset, publish the feature branch, or
rerun the older 7365 recovery.

# CURRENT post-PR47 stopped-failure recovery — 2026-10-08

Run `20261007T183516Z-7365` stopped on candidate
`474679ecda36da610a96e7ec84da39a5dd3ae33b` after round-2
`/usr/bin/python3` full-final failed in
`test_diagnostics_failure_matrix.py`. The same targeted Mac Python 3.9 test then
passed 3/3. Independent PR #47 isolated the test-only timing issue, retained the
real timeout case at 0.2s, moved non-timeout CLI fixtures to a bounded 2s budget,
passed Cloud targeted/full validation and exact-head CI, and was squash-merged to
main as `63e232e22a769f409e381e34dfe0ef1add766d8e`.

The old run's Claude failure classification was evidence-bound and otherwise
schema-valid but was rejected because its summary was 2136 characters versus the
2000-character contract. The helper now exposes that exact reason and constrains
future classification output in the request schema. No failed gate was waived.

The current recovery is pinned to:
- old base `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`;
- stopped HEAD `474679ecda36da610a96e7ec84da39a5dd3ae33b`;
- original control `98b1871be51128da8dcdcf6eb32c27e2af89fa14`;
- exact stopped summary/failure/Mac-cleanup hashes in `task.json`;
- new main `63e232e22a769f409e381e34dfe0ef1add766d8e`;
- the sole approved upstream path
  `tools/llmtool/tests/test_diagnostics_failure_matrix.py`.

The runner must prove those pins, prove prior synthetic Mac fixtures are absent,
snapshot the stopped candidate, and create a two-parent merge of the exact new main
without changing any stopped A11b source/test/report byte. After integration the
diff against new main must contain only the existing A11b allowlist. Recovery starts
with deterministic validation, not a Codex call. No historical PASS is inherited;
both full suites and all fourteen Mac containment scenarios run again on the
integrated bytes before publication.

First operator action is read-only preflight only:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T183516Z-7365 --check
```

`--check` spends no model tokens and performs no merge/source/test change. Only
after this exact preflight passes is the same resume without `--check` permitted.
Do not rebase, cherry-pick, reset, stash, manually merge main, publish the feature
branch, or restart from main. Previous 96025/77009/87533 operator commands are
historical.

# CURRENT autonomous failure coordination — David authorization 2026-10-08

David authorized the Mac script to coordinate ordinary failures/bugs without using
him as a report/review/repair messenger. This CURRENT policy supersedes older
instructions requiring coordinator intervention after EVERY native failure, the
old two-call bound and initial wording below. It does not weaken acceptance, expand
allowed files, alter roadmap/milestones, authorize real Git ADD or approve merge.

## Failure and repair workflow

The same invocation handles:

1. Freeze failed candidate hashes, phase/commands/exit status, exact native JSON and
   evidence references. Publish a bounded immutable failure report to reports-only
   automation/ai-loop-diagnostics; no failed feature source publication.
2. Invoke a separate read-only Claude failure classification call. Bind its result
   to exact HEAD plus failure-evidence SHA256, including uncommitted candidate
   hashes. Read original local test logs/source. Do not classify by CLI exit zero.
3. Publish Claude's structured diagnosis/repair recommendation and disposition to
   Git BEFORE passing them to Codex. REPAIR requires concrete blocking evidence,
   CODE_BUG/TEST_BUG and named files from the existing allowlist. Scope/design/
   function/roadmap/authorization changes are NEEDS_DECISION. Auth/quota/environment
   or insufficient evidence are BLOCKED; no blind provider retry.
4. Codex repairs within the unchanged allowlist. A real source/test-byte change is
   required before another test attempt. Re-run related tests and ordinary review;
   run both full suites and ALL native gates on changed bytes before completion.
5. Exact-head CI failures also go through classification. After an accepted local
   repair, update the SAME Draft PR/branch using non-force Git and an exact-head
   lease, then check the new exact CI HEAD; no duplicate PR or automatic merge.

At most FOUR Codex candidate calls including this continuation's first call, at
most FOUR failure classification calls. Two failures in the same phase with the
same blocking file/command/native scenario signature stop as REPAIR_STALLED. Zero
source/test progress, exhausted bounds or unreproducible/insufficient evidence
stops as blocked/incomplete with published recommendations. Optional Low/Note
findings are deferred; no zero-bug pursuit. A required failing test never becomes
PASS/DONE merely because the AI elects to stop fixing.

This is the script plus Claude coordinating the same task on David's Mac. Work
cannot itself launch Mac processes or receive an automatic callback. David only
needs to start the command, act on a genuine decision/blocked prerequisite or bring
the completed stage to a ChatGPT functional review. Routine in-scope bug cycles
need no repeated 'next step', 'review repo' or manual diagnostic upload.

## Required test-controller fixture cleanup

Implement this within existing allowed test files in this candidate BEFORE native
acceptance, alongside the closed_status transition repair below. The acceptance
controller must handle ordinary failed-probe fixtures itself while it is still
the real parent and retains their owned Popen handles. Preserve the original
assertion/primary error, source digests and exact failing subcase before cleanup.

- Runtime production F1 retains unresolved ownership and cannot return normally;
  do not weaken F1 just to terminate a test. The external test controller may
  resolve its OWN synthetic host fixture by SIGKILL, as the earlier fixed F1 probe
  already does. It holds the unreaped real child PID pin, verifies launch/current
  PID/start identity/UID/parent relationship under the no-reaper premise, and kills
  only that owned host. No remembered PID/PGID signaling after release.
- Reap the controller-owned host after its cleanup decision. EOF must trigger the
  supervisor's owned-group containment path where applicable. Preserve all group,
  leader and descendant disappearance checks; do not signal a guessed group or
  unrelated orphan. Actual hung/unresponsive/lost-group ambiguity remains blocked.
- Keep a bounded `original_owned_processes` snapshot with host identity/supervisor
  entries BEFORE cleanup. After actual checks establish cleanup, failed JSON may
  record `failure.owned_processes: []` and `failure.fixture_cleanup` containing
  `status: RESOLVED`, `original_owned_processes` and truthful cleanup evidence.
  Nonempty owned_processes instead records unresolved/preserved fixtures. Do not
  erase the inventory or fabricate RESOLVED to permit another native attempt.
- The outer runner independently performs a read-only PID-absence check on the
  retained/original inventory before accepting a Mac repair continuation. Missing
  inventory/cleanup proof, retained PIDs, permission or inspection errors block
  the next candidate; the runner never signals saved IDs. Operator cleanup of old
  orphan fixtures is not a production containment PASS.
- Add regression and native failure-path tests verifying assertion retention,
  actual owned-host pin/identity, safe cleanup/reap order, no unrelated signal,
  bounded cleanup, original inventory, absence proof and blocked ambiguous cases.
  If safe cleanup requires a different containment architecture, Claude must return
  NEEDS_DECISION. Do not guess, reap the production anchor early or lower deadlines.

## Delivery and stopping states

Published per-round reports: failure.json, review.json and disposition.json in
immutable round subdirectories, with exact evidence hash/source hashes. Provider
raw streams, source snapshots and original deterministic logs stay local; Claude's
structured recommendations are intended Git reports. Upload failure preserves
local evidence and stops, rather than silently proceeding without the handoff.

Success still requires every pinned test/full/native/final ordinary review/CI gate
and ends READY_FOR_FINAL_REVIEW with one Draft PR. Independent security review,
coordinator/functional acceptance and merge remain pending. NEEDS_DECISION hands
back the concrete requested change; BLOCKED/stalled/exhausted hands back an honest
uncompleted report. Nothing starts 2.2.2 or changes the roadmap automatically.

## Existing pinned recovery and containment requirements

The following candidate/evidence pins and underlying containment restrictions
remain authoritative, subject only to the CURRENT autonomous policy above.

# A11b.2.2.1 — bounded hung-supervisor recovery

## CURRENT authorization — run 96025

Repair the existing unpublished containment candidate on the same branch. Do not
start a fresh implementation or create a branch from main. This CURRENT section
supersedes old SIGINT/libproc recovery instructions and initial-implementation
language in the retained authoritative requirements below. Historical failures
remain in the assessment and the pinned previous control contracts; no old PASS
is inherited. Only A11b.2.2.1 is authorized.

- Canonical Mac repository: `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`.
- Main/base: `585caf7d4c9dad20afd3beb38e5140d6dadf89e0`.
- Existing unpublished branch: `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007`.
- Exact stopped candidate: `a00eda49aa0f0c3486d27589148361d67e7bdb3e`.
- Assessed run: `20261007T162747Z-96025`; original control:
  `8c96e7ad60fd5c39f13f0a563afcc6cc26f84753`.
- Original summary SHA256:
  `28961634d78941ea3f80f81e457896bca8e4c8be8f23a2e75afe42301be8dce3`.
- Original native JSON SHA256:
  `0bbb5aa95e3887bf2756fe275e1028c7dae1e1dd01f160f2ddd92c02ad3cf5a1`.
- Published diagnostic index at immutable commit:
  https://github.com/davidcatcat/davidcattoolsproject/blob/3da0c0c25d0695fcb3792a225d503d372fc52572/reports/ai-loop/diagnostics/20261007T162747Z-96025/index.md
- Coordinator assessment:
  `reports/ai-loop/checkpoints/A11b.2.2.1-sigint-failure-assessment-20261007.md`,
  latest CURRENT hung-supervisor repair authorization section.

The runner requires exact stopped tracked/index/source/test/report bytes, the
original evidence digests and Git-pinned contract, unchanged main/scope/tests,
absence of a published feature branch/PR, no active runner Codex writer and no
retained failed probe host/supervisor PID. It preserves unrelated untracked
artifacts and snapshots the failed evidence/candidate before the one-use recovery.
A failed preflight does not consume the recovery. Never reset, stash, clean,
force-push or bypass a PID guard. At most FOUR Codex repair calls total for this
continuation, including the initial continuation call. Each provider has a three-
hour limit. Native failures enter the CURRENT evidence-bound Claude classification policy below; no blind retry.

## Observed facts and limits

Mac 15.7.9 / venv Python 3.13.3: related tests, preliminary ordinary Claude review
and both full suites passed on the old candidate. Native FD inheritance and
SIGINT/SIGTERM/SIGHUP passed. `hung_supervisor` failed and nine later native
scenarios DID NOT RUN. These results are historical exact-byte evidence only.

The failed parent received READY and CHILD_IDENTITY, then status EOF without a
TERMINAL row. Its primary outcome was supervisor-lost. Cleanup caught getsid
ESRCH and read two fresh BSD rows matching launch PID/PGID/UID/PPID/start-time,
both with status 2 rather than required SZOMB 5. `Anchor.verify` rejected with
`owned zombie continuity unconfirmed`. Later fixture-cleanup timeout retained
the owned host; it did not replace the primary failure. The prior arg=1 BSD-query
fix now returns rows and must be preserved.

Apple XNU tag xnu-11417.140.69 posts NOTE_EXIT before final SZOMB assignment and
allows exited-process references while p_stat still reflects an earlier state.
Fresh operator ps identifies the retained host as `--case closed_status`.
`AcceptanceScenarios.hung_supervisor` first completes the hung probe, then runs
closed_status before the other fault subcases. `_git_supervisor` intentionally
closes the status FD and self-kills its owned group in closed_status. Thus EOF
without TERMINAL is expected fault injection here, not proof of an unexplained
supervisor crash. The source order plus matching retained-host command localizes
this failure to closed_status; no separate completed-subcase evidence was captured.
The entire hung_supervisor scenario is still FAIL and its later subcases remain
unverified. Apple source supports observing the exit transition, not certainty
about its duration in this invocation. Do not label status 2 a zombie or accept
it as continuity proof. Read the latest assessment correction before editing.

## Bounded repair requirements

1. **Non-reaping exit transition.** Inspect and repair the immediate-two-SZOMB
   assumption. A legitimate owned exit transition may be observed/rechecked
   within the EXISTING shared monotonic cleanup deadline, with bounded polling
   and no consuming wait. A NOTE_EXIT indication must be latched if reused; an
   empty subsequent kqueue read must not undo known exit. Preserve the original
   live SID check and the narrow getsid ESRCH branch. Before any group signal,
   require the full trusted host/no-reaper premise, real owned unreaped Popen,
   PID==PGID==SID launch continuity, UID/PPID/kernel start identity, and fresh
   matching kernel rows establishing actual SZOMB 5 when using zombie proof.
   Recheck ownership/identity after transition observation. Never signal a
   remembered PGID based on NOTE_EXIT, ESRCH, status 2, or a stale snapshot alone.
   Deadline expiry, drift, missing/short rows, permissions, external reap or
   ambiguity remains containment/state unknown with F1 ownership retention.
   No poll/wait/communicate/consuming waitpid before the group-cleanup decision.
   Do not add threads, processes or a second anchor to the critical window.

2. **Preserve the closed-status fault injection.** The failure is localized to
   `closed_status`, a deliberate close(status_fd) followed by self-group SIGKILL.
   Do not repair it by keeping the FD open, sending TERMINAL, disabling group
   self-kill, or waiting for a normal completion message. The parent must retain
   primary supervisor-lost/status-EOF, safely observe the owned exit transition,
   decide group cleanup before any supervisor reap, then reap/confirm absence
   within the existing bound. Unexpected exceptions are not established here;
   do not add broad supervisor exception diagnostics or change protocol/version
   merely to investigate the expected EOF. Preserve framing/binding/FD boundaries.

3. **Identify the exact acceptance subcase.** Add compact bounded subcase evidence
   to the existing test diagnostics so the next failure names hung, closed_status,
   malformed_status, lost_before_identity, registration_failure, drain_failure,
   missing_ACK or the other fixed subcase actually executing. Preserve actual
   completed checks without calling a partly completed scenario PASS. Retain the
   genuine hung-stage, missing ACK, unexpected supervisor-loss and all later
   acceptance assertions. Test-only diagnostic labeling must not add a production
   FD/message/CLI, change the protocol flow or fabricate stage entry. The previous
   first-hung probe progressed before closed_status according to source order,
   but the full scenario must rerun on changed bytes. If further native evidence
   exposes another bug within the same allowed containment scope, the runner sends
   it through evidence-bound Claude classification before another bounded Codex
   repair. Scope/design/protocol expansion remains NEEDS_DECISION.

4. **Regression evidence.** Add deterministic tests for non-reaping status 2 to
   SZOMB 5 transition, already-zombie, latched NOTE_EXIT, shared deadline expiry,
   identity/start-time drift during transition, absent/short/permission rows,
   external reap and no-signal rejection. Test bounded subcase diagnostic labeling
   and primary-error preservation; retain existing strict protocol version/schema,
   unknown-field and EPIPE rejection coverage. Add native transition observations
   while preserving genuine hung-stage evidence and intentional closed-status EOF.
   Preserve existing identity reuse, verification-to-signal race, FD, import/CLI
   boundaries, code-binding, negative and descendant containment assertions.
   Do not increase timeouts/cleanup budgets, skip assertions, catch-and-PASS,
   accept status 2 as zombie, fake protocol evidence or adjust thresholds to
   obtain PASS. Source/test bytes must change before a new native attempt.

5. **Runner gates and reporting.** Codex edits only the existing ten-file allowlist;
   the outer runner owns deterministic tests and Git. No Codex-run full suites or
   native probes. Preserve all historical report evidence; mark new gates pending
   until the runner executes them on changed bytes. Run all related checks,
   independent read-only Claude review, full current-venv and Mac system-Python
   suites, then ALL fourteen native scenarios including >=20 plain and >=20
   Popen parent-SIGKILL repetitions with <=2s group disappearance. No skipped
   native scenario is PASS. A mandatory failure is published and classified by Claude under CURRENT policy.
   Repeated failure/stagnation, exhausted bounds or unresolved fixture ownership
   stops with curated diagnostics on the separate reports-only branch.
   Publication does not authorize recovery. No feature push/Draft PR until all
   required gates pass; final coordinator/security review and merge remain pending.

## Operator handoff

Failed probe host PID 5769 and supervisor PID 5770 are saved evidence, not current
ownership proof. Require fresh read-only inspection and resolution of retained
fixtures before resume; do not blindly kill these IDs or require a reboot. The
runner PID guard is read-only and must remain in place. A coordinator must verify
any proposed operator fixture cleanup against fresh path/state/parent evidence.

After fixture absence is established, David runs only:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T162747Z-96025
```

The existing launcher downloads the new control contract automatically; no
reinstall is needed. Previous 77009/87533 resumes are historical and consumed.
This Work session performs no Mac cleanup/tests/application implementation.

## Retained authoritative implementation contract

The original requirements below remain mandatory subject to this CURRENT bounded
continuation and the approved supervisor-anchor ordering in section 8. Read them
fully; do not reimplement the existing candidate from main.

## Read before implementation

Read these authoritative requirements and evidence in full:

- AGENTS.md, CLAUDE.md, tools/llmtool/WORKFLOW.md, tools/llmtool/ROADMAP.md.
- skills/implement-feature/SKILL.md, skills/review-change/SKILL.md,
  skills/security-review/SKILL.md and skills/safe-git/SKILL.md.
- reports/llmtool_a11b2_2_b5_containment_failure_spec_20261007.md (especially
  sections 3/4/8/9/11/18/19/21 and remediation section 26).
- reports/llmtool_a11b2_2_b5_containment_spec_review_20261007.md
  (historical BLOCKED findings).
- reports/llmtool_a11b2_2_b5_containment_spec_remediation_20261007.md.
- reports/llmtool_a11b2_2_b5_containment_spec_targeted_rereview_20261007.md.
- reports/llmtool_a11b2_2_0_cleanup_validation_20261007.md.
- reports/ai-loop/A11b.2.2.0-pr46-review-20261007-20261007T050236Z-59259.md.
- reports/ai-loop/checkpoints/A11b.2.2.1-containment-20261007-20261007T064116Z-68730.md (stopped F3 checkpoint; not a PASS).
- tools/llmtool/llmtool/git_mutation.py, git_intel.py, action_contract.py, and the existing process runner/error definitions
  located by inspection (do not assume a process_runner.py/errors.py filename).

## Scope and file contract

Implement only private supervisor + deterministic fake-child process containment.
Use the explicit allowed_files and required_files in task.json. The private module
names are reserved for this handoff; do not add an arbitrary command runner. If the
reviewed architecture requires another file, return NEEDS_DECISION with the exact
reason/path rather than expanding scope. Existing git_mutation production behavior
and Action/approval semantics remain unchanged. Extend its import-boundary tests
only as necessary to distinguish reviewed private code from forbidden public paths;
preserve all existing rejection assertions.

Never execute real product `git add`, mutate the user repository/index, implement
real ADD/post-state mutation, add a public mutation CLI or model path, issue approval,
bypass durable audit, retry/rollback, delete locks, or start 2.2.2/.3, A11b.3, A11c or
A12. Fake-child/probe processes may create disposable runtime fixtures, not project
checkouts. Their signals target only identity-bound owned synthetic processes.

The outer development runner owns Git staging/commit/push/PR creation. Its ordinary
publication staging is distinct from the prohibited llmtool Git ADD implementation.
Codex must not stage/commit/push, install globally, invoke sudo, bypass permissions,
run expensive suites/probes itself, or change the task/criteria. Return PASS for an
implemented candidate, not as a claim that pending runner tests/security gates passed.

## 1. Supervisor and trusted runtime

Separate process, never a thread; fixed exec-style launch equivalent to
`[sys.executable, '-I', '-m', '<private supervisor module>']`, shell=False, distinct
session, trusted neutral cwd outside the repository, fresh PATH/LC_ALL allowlist.
No PYTHON*, DYLD_*, LD_*, user-site, credentials, proxies, agents or ASKPASS inheritance.
Verify interpreter/install identity. Isolated module-resolution failure rejects;
no PYTHONPATH/repository-cwd workaround or silent alternate interpreter. If the
specified Mac Python environment cannot resolve the trusted installation, report
NOT SUPPORTED/NEEDS_DECISION and preserve evidence instead of changing global setup.
Without a legitimate internal FD/launch/code handshake, direct import, runpy,
wrapper or subprocess -m invocation must fail closed without spawning any child.
There is no hidden/public supervisor CLI.

## 2. FD topology and host premise

Parent uniquely owns control/liveness writer and status reader; supervisor owns
control/liveness reader and status writer; fake child owns neither. CLOEXEC and
non-inheritable endpoints, close_fds=True, pass_fds limited to the two required
supervisor endpoints; close unused ends immediately. Supervisor sets inherited
endpoints non-inheritable before spawning its child. No control/liveness/status/audit
FD reaches fake children; verify enumeration in tests.

Install signal scope before pipe creation. The production critical window permits
only the one supervisor launch, no extra fork/spawn/thread or spawning callback.
Support only reviewed main-thread/single-thread hosts. threading.active_count()==1
alone does not prove absence of native threads/fork; explain and enforce the trusted
host premise. Unsupported/unprovable conditions reject before spawn. The extra
Popen(close_fds=True) inheritance stress variant is harness-only, never a production
permission.

## 3. Bounded protocol

Versioned strict JSON, 4-byte big-endian length prefix, max 128 KiB; reject zero/
oversize, duplicate/unknown keys, non-finite values, excessive depth/count, unknown
version/type and mid-frame EOF before unsafe allocation. Nonblocking partial
reads/writes with monotonic deadlines; EPIPE/BrokenPipeError triggers cleanup.
No pickle/eval, arbitrary argv/shell or action replacement.
START -> READY -> fixed fake-child spawn -> CHILD_IDENTITY -> optional CANCEL_ACK
-> terminal status. After START only CANCEL is accepted from parent.

## 4. Code/runtime binding

Bind protocol, action/preflight digests and caller-pinned reviewed artifact manifest,
interpreter/install identity; manifest includes supervisor, protocol/identity helpers,
mutation/preflight, action contract, errors, package init and all containment/error
path modules. Parent and supervisor independently verify origins and hashes, before
READY and again before child spawn. A self-consistent Action-supplied manifest is
not authority. Mismatch must reject with zero child spawn. This is drift detection
under trusted installation, not a same-UID isolation claim. Explain the fake-harness
internal binding without introducing fake product approval/audit authorization.

## 5. Fake child only

Fixed deterministic synthetic cases in private code; no arbitrary command seam or
Git ADD. F3 changes the topology: the supervisor itself is the session leader and
process-group leader (`PID == PGID == SID`), and every fake child/controlled descendant
stays in that supervisor-owned group. The child must not create a new session/group or
escape it. A controlled descendant case must remain after its direct leader exits, so
the test proves whole-group descendant containment rather than merely leader reaping.

## 6. Non-reaping lifecycle

On macOS implement kqueue KQ_FILTER_PROC/NOTE_EXIT or a separately reviewed equivalent.
Notification -> owned-group cleanup decision -> leader reap, never poll/wait/
communicate/consuming waitpid/context-manager reap first. Handle SIGCHLD auto-reap
and custom reapers according to B5. Test alive and zombie-at-registration NOTE_EXIT,
registration failure, fast exit and leader exit with live descendant. Never signal
remembered released IDs after reap.

## 7. Parent death, signals and supervisor failure

Real host parent-SIGKILL -> writer closure -> supervisor EOF -> fake group SIGKILL
-> leader reap -> eventual group disappearance. Run >=20 repetitions EACH for plain
and harness-only Popen(close_fds=True) variants, with a real controlled descendant.
Every repetition has <=2s monotonic latency to confirmed group disappearance;
use kill(pid,0)/killpg(pgid,0) with identity evidence, not ps. EPERM is inconclusive,
not absence. Distinguish zombies from executing descendants and safely clean owned
processes on assertion failures. Record macOS/Python/code digest. Probe FAIL,
NOT SUPPORTED or instability means STOP, not automatic retries or weaker thresholds.

Test real SIGINT/SIGTERM/SIGHUP including pre-pipe, spawn and cleanup timing; minimal
handlers, repeated signals cannot bypass cleanup/deadline; restore only after verified
containment and reap. EOF is the death safety mechanism, handlers provide typed outcome.
For hung/lost supervisor, missing ACK, bounded wait, malformed/closed status or missing
group-gone proof: follow section 8's supervisor-anchor ordering. Freshly verify the
still-owned, unreaped supervisor, signal its owned group while that supervisor pins
the PID/PGID, and make the group-cleanup decision before any supervisor reap. Then
reap the supervisor and verify eventual group/descendant disappearance. Never reap
first and subsequently signal the remembered group number. No normal return/restoration while an owned process may live. Explicitly
implement unresolved-containment exit/handler behavior (F1), preserve its evidence,
and classify as state unknown / user action required without guessing ownership.
Also inject supervisor death before CHILD_IDENTITY delivery (B5 residual window).

## 8. Identity, reuse and coordinator-approved F3 remediation

Bind PID/PGID/SID/UID and kernel start-time identity. The accepted remediation is:

- supervisor starts a new session, so its `PID == PGID == SID`; parent is its real parent;
- fake child and descendants remain in the supervisor's process group;
- parent observes supervisor exit without reaping it and never calls poll/wait/communicate before the group-cleanup decision;
- for timeout, hung supervisor, cancellation or unexpected supervisor death, parent may `killpg(supervisor_pid, SIGKILL)` only while the owned supervisor is live or an unreaped zombie and bound PID/PGID/SID/UID/start-time evidence matches;
- because the parent has not reaped that child, the supervisor PID cannot be reused; because PGID equals that reserved PID, an unrelated new process group cannot acquire that PGID before reap;
- only after group cleanup/absence is decided may parent reap the supervisor; never signal the remembered group after reap;
- on normal fake-child termination, supervisor may make the cleanup decision and reap its direct child for status while the still-live supervisor pins the group, then send terminal evidence and wait for parent finalization;
- on parent-liveness EOF/SIGKILL, supervisor performs a last-act `killpg(own_pgid, SIGKILL)`; self-termination is intentional and kills the fake group together;
- if supervisor identity is ambiguous, already reaped, or no longer `PID == PGID == SID`, do not signal a numeric PGID; classify containment/state unknown.

This replaces the stopped run's snapshot-only fallback and the earlier separate-child-process-group assumption. For this recovery, section 8 also supersedes B5 section 11.2's old supervisor-kill/reap-before-group-fallback ordering: the supervisor-owned group cleanup decision MUST precede supervisor reap. All identity, disappearance, bounded cleanup, typed failure and independent review requirements remain mandatory. Do not add a separate anchor process and do not depend on Apple private audit-token signaling. Independent security review after implementation must explicitly verify this remediation.

Test live and zombie supervisor-anchor cases, mismatch/reuse, attempted signal after reap, unexpected supervisor death, hung supervisor, parent SIGKILL and verification-to-signal timing. Actual Mac probes remain mandatory.

## 9. Typed outcomes and F1–F4

Internal start-failed, timeout, interrupted, parent-lost, supervisor-lost, output-limit,
protocol-failure and containment/state-unknown outcomes; no real Git APPLIED/NOT_APPLIED
semantic flow in this slice. Implement F1 unresolved exit, F2 kqueue cases, F3 kernel
identity/continuity and F4 host premise within 2.2.1, without new milestones. Preserve
primary failure despite cleanup/protocol faults. BLOCKER/HIGH/correctness defects
block; optional Low/Note do not cause an unlimited repair loop or a zero-bug pursuit.

## 10. Boundary and tests

No CLI/router/provider/orchestrator/model/debug mutation path, approval issuer or audit
bypass. Cover AST/string/static and dynamic import, direct invocation, runpy, -m and
wrapper paths; invalid internal handshake cannot spawn a fake child. Keep A5 and
A11b.1/2.1 regressions intact. New unit tests must discover actual nonzero test cases.
CI on other platforms may report Mac-only capabilities NOT SUPPORTED/skipped; those
results never count as Mac containment PASS. Actual Mac mandatory acceptance permits
no skipped scenarios. Do not weaken tests or change required policy for a green run.

## 11. Runner test order and evidence interface

The runner calls Codex once for the initial implementation. After each candidate or
bounded repair it runs syntax, new containment tests and existing Git/A5/Action tests,
then read-only Claude ordinary review of the complete base-to-HEAD diff. On preliminary
review PASS, it runs final full checks under both tools/llmtool/.venv/bin/python and
/usr/bin/python3, plus required actual Mac acceptance. Full checks are reused only on
identical source/test bytes. --related selects repair coverage, not permission to skip
these mandatory final gates. --full also runs full checks on the initial candidate.

Implement tests/run_containment_acceptance.py with exactly the fixed `--output PATH`
evidence interface used by task.json, launched from canonical root by the outer runner
outside the Codex sandbox. It must execute real required tests/probes, require nonzero
checks and zero skips, retain failures, exit nonzero on any failure/unsupported case,
and produce fresh JSON only for that invocation. No network/global install/user repo
mutation. The output path is in the runner-owned local logs directory. Do not fabricate
JSON PASS to satisfy the validator. Independently reviewed source and raw evidence
are required; schema validation alone cannot prove semantic correctness.

JSON shape (strict keys; max 128 KiB):

- schema_version: integer 1; platform: 'darwin'; os_version: platform.mac_ver()[0];
  python_version: platform.python_version() for the invoked venv interpreter.
- tested_sha256: SHA-256 map of ALL existing allowed_files excluding reports/;
  read task.json's pinned contract copied in the output directory's task.json.
- scenarios: EXACT names fd_inheritance, sigint, sigterm, sighup, hung_supervisor,
  leader_exit_descendant, identity_reuse, macos_start_time, kqueue_non_reaping,
  protocol_framing, code_runtime_binding, public_import_boundary,
  parent_sigkill_plain, parent_sigkill_popen.
- Every scenario row: EXACT keys status ('PASS'), checks (positive int), skipped (0),
  repetitions (empty array except for SIGKILL variants).
- Each SIGKILL row: >=20 actual repetitions, checks >= repetition count. Each record
  has EXACT keys leader_pid (positive int), pgid (positive int), descendant_pids
  (nonempty positive-int array), latency_seconds (finite number 0..2), identity_verified,
  leader_absent, descendants_absent, group_absent, containment_confirmed (all true).

The heartbeat is not a repeated command: [Running] prints every
20 seconds while a command is active. Tests show position, PASS/FAIL and duration.
Any mandatory Mac failure or invalid evidence stops with files/logs preserved. No
blind provider retry, permission bypass or automatic probe repair. Ordinary unit/full
regression failures can use the fixed two-repair budget when reproducible/in scope.

## 12. Report, review and publication

Create the specified validation report with truthful provenance and initial pending
runner gates. Preserve before-fix evidence and do not claim old results for changed
bytes. Include every requested disposition: harness/supervisor, FD, x20 plain/x20
Popen parent-SIGKILL, SIGINT/TERM/HUP, hung escalation, leader/descendant cleanup,
PID/PGID reuse, kernel identity, non-reaping kqueue, framing, code/runtime binding,
both Python full suites, BLOCKER/HIGH, no real Git ADD, no public mutation, no 2.2.2
authorization and readiness for independent security review.

After actual gates PASS, the runner appends its final results and hashes to that report,
commits report evidence, and Claude re-reviews the complete final diff. It publishes
report/implementation to the new branch, creates one Draft PR, verifies exact final
HEAD llmtool CI, appends the handoff to the PR and stops at READY_FOR_FINAL_REVIEW.
No formal security PASS or merge approval is implied. Keep raw prompts/test/model
streams local in .ai-loop/<run-id>; curated evidence alone goes to Git. Provider auth,
quota, CLI, stale state, invalid structured output, missing files or exhausted repair
bounds stop with work preserved and require coordinator recovery, not a blind rerun.


Preflight permits normal ancestry or a verified squash handoff: exact merged PR
HEAD/branch/repository, identical tracked tree to pinned main and PR merge commit
already in pinned main. It does not reset/change the old branch or accept equal
content as proof for unpublished commits. Existing dirty/untracked protections apply.
