# ACTIVE — C1 Issue #51 test-excerpt parser (2026-10-09)

## ACTIVE (2026-10-09): PR #55 Security FAIL — Medium/blocking RESOURCE_EXHAUSTION fix

The post-first-fix independent Security gate **FAILED** on PR #55 exact HEAD `d5ecfce9f7e28da2983affb235dd942d51388723`; Functional QA did not start. [Frozen Issue #56 assessment](https://github.com/davidcatcat/davidcattoolsproject/issues/56#issuecomment-6074021352) confirms **Medium, blocking, RESOURCE_EXHAUSTION, FIX_BEFORE_MERGE, HIGH confidence**, `test_output_excerpts.py:42`, source finding SHA `44460d77ccd8bb3224db90dfa71920653d9a6d64b908aa22d79dc1b12c0996e1`. This is in the JWT-sensitive regex branch; the precise input exploit stays in Mac-private security findings. PR #55 stays Draft with unchanged HEAD until a new reviewed candidate is published.

Previous successful/failed post-fix QA task is archived under `automation/ai-loop/history/20261009-C1-PR55-PostFix-Security-FAIL-task.md/json`. Live `task.md/task.json` now authorize **one-candidate same-PR repair** `C1-PR55-SecurityFail-ResourceFix-20261009` via pinned `qa_security_fail_repair`. Runner first verifies exact local Security FAIL (role record, task, run, diff SHA, Medium/true blocking flag, finding hash) and the independent risk assessment, then supplies the bounded original private rationale to Codex as *untrusted evidence*, never to GitHub. Codex can only change **both** `tools/llmtool/llmtool/test_output_excerpts.py` and `tools/llmtool/tests/test_test_output_excerpts.py`; the broader seven-path PR inventory is **not** new write permission. `max_repairs=0`; no automatic retry or second PR.

Fresh related tests, both full Mac suites, ordinary separate Claude code review and exact-new-head GitHub CI remain mandatory. The **next** independent Security and Functional QA require another explicitly authorized task on the changed HEAD: previous FAIL cannot become PASS by inference. No merge, external-provider egress, C1b company-only run or C1c savings benchmark.

**Operator command only after exact-current-control-head helper CI (3.9 and 3.14) succeeds and coordinator approves:** `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh`. Precheck is no-model and fail-closed on missing private findings, unexpected branch/HEAD or modified files. Do not manually pass Codex/Claude prompts, rerun old `--resume`, or start ordinary QA.


## ACTIVE: PR #55 post-fix Security FAIL — frozen single-finding triage (2026-10-09)

The fresh post-Medium-fix independent Security review on PR #55 exact HEAD `d5ecfce9f7e28da2983affb235dd942d51388723` **FAILED with one actionable finding**. The affected path is `tools/llmtool/llmtool/test_output_excerpts.py`; [Issue #56](https://github.com/davidcatcat/davidcattoolsproject/issues/56#issuecomment-6070341161) pins QA run `20261008T221753Z-44339` and cumulative-diff SHA256 `6662b084538b44f3ad9f7ee873f5c08d23d60cef2b4c49e849c6d0de0c1613f2`. Fresh related + both Mac full tests PASS and GitHub CI 3.9/3.14 PASS, but Security **FAIL** overrides those technical gates. The runner skipped Functional QA as intended, and Draft PR #55 must **NOT MERGE**.

The original untrusted Security detail remains private in Mac `.ai-loop/20261008T221753Z-44339/claude-201.json`; do not manually upload, paste or relay it. A narrowly scoped `--qa-security-fail 20261008T221753Z-44339` mode verifies original task/QA/provenance/role evidence, pinned file and cumulative Git diff, clean PR/head and the previously published Issue #56 FAIL **before a model call**. One bounded **read-only Claude** pass categorizes the actual source-grounded blocker into fixed category/action/confidence and allowed file/line metadata. Public GitHub Issue #56 receives **only enumerated metadata**; raw findings, source excerpts and private reasoning stay in a protected ignored local directory. A retry with existing raw/review evidence never silently calls the model again. No related/full tests or new PR work are replayed. This is categorization, **not permission to override the Security FAIL**, merge, route external data, or accept the risk.

The live `task.md/task.json` remain the **frozen post-fix review contract** at HEAD `d5ecfce9...`, not a new Codex repair contract. After exact latest controller helper CI green on Python 3.9 and 3.14 and coordinator approval, the Mac operator's **one named script entrypoint** is `bash .git/ai-loop/run.sh --qa-security-fail 20261008T221753Z-44339`. On STOP, preserve all evidence and avoid manual model CLI. Only after reviewing sanitized classification may the coordinator prepare a separate exact-finding same-PR repair contract with new tests, independent specialist review and exact-head CI.


## ACTIVE (2026-10-09): PR #55 post-fix new-HEAD dual specialist QA

The exact Medium `INPUT_VALIDATION` repair was committed to the **existing** PR #55; current publication HEAD `d5ecfce9f7e28da2983affb235dd942d51388723`, still OPEN/Draft. [Exact-head llmtool CI #37849174251](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37849174251) is SUCCESS for Python 3.9 and 3.14 (1,292 tests, 1 skip each); the runner report records both full Mac `make check` suites and ordinary Claude code review PASS. Only the two previously approved Codex source/test paths changed, plus one runner report. The previous Medium risk was marked FIX_BEFORE_MERGE and the **old** Security/Functional QA model PASS must NOT be inherited on changed source.

The completed repair contract is archived under `automation/ai-loop/history/20261009-C1-PR55-Medium-InputValidation-Fix-task.md/json`. **Current live `task.md/task.json` is now `C1-PR55-PostFix-Security-Functional-QA-20261009`: read-only PR review, new exact HEAD, seven changed files, `qa_gates=[security,functional_qa]`, zero writer calls, fresh related and both full Mac gates, fresh two independent role-isolated Claude sessions.** Focus explicitly on prior catastrophic URL-regex backtracking, safe URL credentials detection, near-limit timeouts and false-positive behavior. **Any Medium+ or blocking finding (even when reviewer status says PASS) makes the overall QA STOPPED and prevents starting the second expensive role.** Results are published to [Issue #56](https://github.com/davidcatcat/davidcattoolsproject/issues/56) as sanitized counts/allowlisted paths only. Private model text stays on Mac; no provider egress or auto-merge.

Only after current exact-control HEAD helper CI 3.9/3.14 PASS, the one standard Mac command is `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh`. Fail closed on any mismatched revision, dirty Git, tests, model error or uncertain review; no manual provider prompts. A dual model PASS still requires separate coordinator merge decision, and company-only Phase 0 + cost/token benchmarks remain separately authorized.


## ACTIVE (2026-10-09): PR #55 Medium INPUT_VALIDATION fix, before merge

Issue #56's single Medium Security finding has now been independently triaged on **unchanged PR #55** `67d02be6bc8cb3f237c5060edda3321f6d885bf3`: `INPUT_VALIDATION`, `FIX_BEFORE_MERGE`, confidence `HIGH`, `test_output_excerpts.py:46`. Prior dual specialist QA PASS and all seven Low findings remain frozen as **historical evidence**, not a waiver or post-fix PASS. PR stays Draft/unmerged.

**Live task has changed.** Archived the completed dual-review task under `automation/ai-loop/history/20261009-C1-PR55-Issue56-dual-QA-task.md/json`. Live `task.md/task.json` now authorize a *single* targeted **same-PR Codex fix**, `C1-PR55-Medium-InputValidation-Fix-20261009`, with `qa_medium_repair` binding original Medium finding SHA and local frozen private risk assessment. The runner verifies the original risk evidence before even allowing a precheck PASS. Only the original `test_output_excerpts.py` module and its `test_test_output_excerpts.py` regressions may change; report publication remains runner-owned. No second PR, no broad refactor, no automatic merge, zero automatic Codex repair retries, no new product provider calls other than necessary implementation/review. Both related and **both Mac full test suites**, ordinary independent read-only Claude review, exact-new-HEAD GitHub CI are mandatory. Repeat formal dual specialist QA on the **new** HEAD after repair; the earlier specialist QA is stale on changed bytes.

**Operator step, only after latest control-head Helper CI Python 3.9/3.14 PASS**: `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh`. User never relays prompts, manipulates Git or runs separate models. On STOP, preserve evidence and repair script/contract first.


## Current PR #55 merge hold — one Medium Security finding (2026-10-09)

The previous **read-only** C1 QA gate PASS and the later frozen severity-count report are both genuine, but do **not** grant merge authority. [Issue #56](https://github.com/davidcatcat/davidcattoolsproject/issues/56#issuecomment-6069088872) proves **Security: 2 Low, 1 Medium; Functional QA: 5 Low, 0 Medium; no High/Critical** on original HEAD `67d02be6bc8cb3f237c5060edda3321f6d885bf3`. The Medium finding is flagged non-blocking by its model, but the coordinator imposes a conservative **merge HOLD** until independently categorized and dispositioned. Do not auto-mark READY, merge, or claim security fully accepted.

A **single use, separately validated** `--qa-medium 20261008T202910Z-31100` mode has been added to ai-loop to confirm one frozen Medium finding with one bounded fresh **read-only** Claude call. It verifies the unchanged PR HEAD, private original QA records and SHA first, then publishes only validated enums (risk category, action, confidence, allowlisted file/line) to Issue #56. All rationale, review details, provider output and original test data remain local; no source, Git index, PR or merge mutations and no rerunning the previously PASS full tests. The operation is idempotent if the private assessment already passed, avoiding repeated premium calls. Treat its output as **additional evidence**, not permission to merge. If it classifies FIX_BEFORE_MERGE, the coordinator prepares a separate tightly scoped same-PR ai-loop/Codex repair and exact-new-HEAD tests/reviews. If ambiguous, preserve STOP and escalate only a real decision. Do not forward prompts.

**Only after exact-controller HEAD helper CI Python 3.9/3.14 PASS:** authorized Mac command `bash .git/ai-loop/run.sh --qa-medium 20261008T202910Z-31100`. Do not run normal C1/QA again.


## Active: PR #55 exact-head independent QA (Issue #56, 2026-10-09)

The live Git task is now **`C1-PR55-Security-Functional-QA-20261009`**, replacing the completed C1 source-implementation task, which is frozen under `automation/ai-loop/history/20261009-C1-issue51-completed-task.md/json`. **Do not rerun new C1 or old `--resume`**. Target is unchanged Draft PR #55 at `67d02be6bc8cb3f237c5060edda3321f6d885bf3`; current task `mode=review_existing_pr`, `qa_gates=[security,functional_qa]`, zero repairs/zero Codex writers. All six changed files (five product authorized paths plus runner report) are pinned.

The QA-specific runner path checks existing PR base/head/diff and current working tree, requires exact-head GitHub CI and fresh related + both local full suites, then invokes **two separate read-only Claude review sessions** (Security, Functional QA), preserving original PR source/HEAD. On a genuine first-gate blocker, it stops before spending the second model's tokens. Only fixed role-status/test/file-name metadata is sent to [Issue #56](https://github.com/davidcatcat/davidcattoolsproject/issues/56); raw provider and detailed review content stays under local `.ai-loop` and **no merge/model-egress authorization** is granted.

The single Mac operator entrypoint, **only after exact control HEAD helper CI Python 3.9/3.14 PASS and coordinator approval**, is `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh`. If `--check` STOPs, it cannot run the second command. Never manually relay prompts or invoke bare Codex/Claude. This automated two-role gate is not an external human security certification; coordinator must assess findings and explicit remaining approvals before changing Draft or merging.


## 2026-10-09 C1 decision resolved as an ai-loop handoff defect — frozen recovery

The **actual private Codex decision** from frozen run `20261008T185112Z-21061` was that `automation/ai-loop/task.json` and `scripts/ai-loop.sh` do not exist in the C1 main-based checkout. This is **expected architecture**: the runner fetches both authoritative task files from the `automation/ai-loop` Git control revision and supplies exact `TASK`/`CONTRACT` in Codex's prompt. There was no human authorization issue; Codex made 0 source edits, no tests ran, Claude's error envelope was invalid, and no PASS was inherited.

**Correct repair implemented:** Runner prepends an explicit authority/provenance notice to Codex; it does not copy control files into main or expand the five-file C1 allowlist. The current `task.json` has exactly one `initial_decision_recovery` pinned to old control `9d6bbc3c578a3dca0c4d29026b1c64c89cb200bb`, run ID and public `codex-0.json` SHA-256 `39d066a116f2ea169fc76176afa0beeca2055e7a4770250c1e8f1fa785c76927`. The script checks frozen task/provenance, original `NEEDS_DECISION` output, no changed candidate, original Claude failure envelope, clean branch/HEAD, no C1 PR/remote branch, and uses an exclusive single-use recovery marker. It does NOT accept stale test/review PASS, create other files, auto-merge, or call Claude just to reinterpret a zero-edit Codex decision.

**Next vetted Mac operator entrypoint** (only after current exact-head helper CI Python 3.9/3.14 success):
`bash .git/ai-loop/run.sh --resume 20261008T185112Z-21061 --check && bash .git/ai-loop/run.sh --resume 20261008T185112Z-21061`.
The `--check` starts no models; only on PASS should actual recovery run. Do not run fresh C1, don't reuse older 76644 recovery or manually invoke Codex/Claude. All ordinary local related tests, final full tests, independent ordinary Claude review and exact-head CI are required anew. [Issue #54](https://github.com/davidcatcat/davidcattoolsproject/issues/54) tracks the incident; general Issue #53 maintenance hardening remains open.


## Live C1 STOP: Codex needs a human decision (2026-10-09)

The only actual C1 attempt, run `20261008T185112Z-21061`, **STOPPED before tests**. [Source-free Codex diagnostic](https://github.com/davidcatcat/davidcattoolsproject/blob/05928d578ee29605dd994e99b1a0dd646eaf0b75/reports/ai-loop/diagnostics/20261008T185112Z-21061/round-0-codex/codex.json) confirms `developer_status=NEEDS_DECISION`, **0 files modified**, and no repair authorization. [Claude triage](https://github.com/davidcatcat/davidcattoolsproject/blob/17ff4bbdb60a798c4ae20fecefb192f3450083e3/reports/ai-loop/diagnostics/20261008T185112Z-21061/round-0-triage/triage.json) ended with `ERROR_ENVELOPE`, not a valid decision. Do **not** run ordinary C1 a second time, claim CODE_BUG, or infer a decision rationale not contained in public evidence.

Use a **single helper invocation** `bash .git/ai-loop/run.sh --inspect-decision 20261008T185112Z-21061` only after helper CI on the updated control head passes. This checks the original frozen task, candidate branch/head, unchanged allowed files, and SHA-256 of the exact private Codex JSON; then displays only the decision summary on the operator's own terminal, JSON-escaped. It makes **no model calls or Git publication and grants no approval**. The operator assesses whether any *genuine material human decision* is necessary; never manually paste Codex/Claude prompts or leak secrets. Any follow-on C1 recovery must be separately bound to the frozen run/evidence and user-approved scope. Track [Issue #54](https://github.com/davidcatcat/davidcattoolsproject/issues/54).


## Operator contract — one launcher and repair-first recovery (2026-10-09)

Until llmtool-native A11b/A11c development workflow passes independent real-task acceptance, **the operator only runs a validated ai-loop script**. Coordinator authors matching Git `task.md` + `task.json` contracts; runner invokes Codex, independent read-only Claude, tests, bounded repair and Draft PR/CI. Never instruct the user to manually relay prompts or assemble many Mac commands. See [ai-loop-handoff Skill](../../skills/ai-loop-handoff/SKILL.md) and [MAINTENANCE_HANDOFF](MAINTENANCE_HANDOFF.md).

**CURRENT (2026-10-09): Issue #52 RESOLVED on control `ab1f396b00ba11704f0b370a70bf1a31f85f2ee9`.** One-use `--maintenance-issue52` completed end-to-end: Mac 210/210 helper tests, independent Claude review PASS as reported by the runner, and [exact-head GitHub CI](https://github.com/davidcatcat/davidcattoolsproject/actions/runs/37826584204) Python 3.9 and 3.14 both PASS (210 helper + 15 maintenance safety cases each). The Mac operator reported the local branch reconciled to **clean main**; C1 Issue #51 remains NOT STARTED but its pinned main `8e29024656f873431f6ad83e45015b57bfe381a9` still matches upstream and no remote C1 branch/PR exists. Coordinator now authorizes the ordinary C1 precheck and, only on its PASS, execution. **Since `scripts/ai-loop.sh` is absent from tracked `main`, use the stable `.git/ai-loop/run.sh` script created by the launcher:** `bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh` from the canonical repo. No manual model prompts. Issue #53 remains OPEN for general reusable maintenance/resume hardening; the successful one-use bootstrap is not general self-repair. No auto-merge.

**The current control branch is now assigned to `C1-test-excerpts-20261009` on pinned
`main@8e29024656f873431f6ad83e45015b57bfe381a9`.** Exact task contract and requirements:
[task.json](task.json), [task.md](task.md). This task is **implementation_new_pr**,
not a resumed A11b cleanup. Codex writes only the five allowlisted C1
product/test/docs files; Claude performs read-only ordinary review;
the Mac runner controls tests/branch/commit/CI and Draft PR. The user
never needs to relay prompts manually. Security and formal QA remain
independent gates; no auto-merge, no real product Git ADD.

**Historical 76644 recovery is superseded by merged PR #49.**
The former `task.json` and `task.md` are archived byte-for-byte in
[history](history/) and remain retrievable from prior immutable
control SHA `f09202c208871a60cc2f81adac3e7542cbd6e455`. Original evidence and runner state are
preserved; no inference of a successful 76644 resume is made.
Do **not** run `--resume 20261008T100821Z-76644` or edit/reset old
candidate data. If the Mac checkout has unrelated/unpublished work,
`--check` must STOP; do not force switch, stash or clean it.

**Operator normal next command** (only once latest helper CI succeeds):
```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash .git/ai-loop/run.sh --check && bash .git/ai-loop/run.sh
```
A failing check means STOP and report, not manual prompt relay or
switching to another checkout. The runner is a temporary bootstrap,
not llmtool's permanent orchestrator. llmtool C1 remains a read-only
function; its company-only token-saving benchmark is not part of this
implementation run.

---

# ACTIVE COORDINATOR GUIDANCE — final acceptance and no manual prompt relay (2026-10-09)

**Design is now recorded, not yet implemented.** Read
[FINAL_ACCEPTANCE_DESIGN.md](FINAL_ACCEPTANCE_DESIGN.md) before authorizing the
next control task. This addresses the user's requirement: **the Mac operator
must not manually shuttle ordinary prompts between Codex and Claude**. The
existing runner already coordinates Codex implementation, Claude ordinary
code review, tests and bounded repair, but **does not yet implement** separate
formal independent Security Review and Functional QA sign-off. Do not mislabel
ordinary review as either required acceptance gate.

[PR #49](https://github.com/davidcatcat/davidcattoolsproject/pull/49):
remediation Security re-review PASS and independent Functional QA PASS have
both been reported; the QA itself was challenged by another independent
reviewer. The code/test-bound Mac evidence recorded 14/14 scenarios, 20+20
parent SIGKILL and two 1,247-test Mac suites (each with 3 filesystem skips).
This status is not merge authorization. Confirm the **latest** remote HEAD,
GitHub CI and required coordinator acceptance before merging. These reviews
used manual/special-purpose execution: they are **not evidence that a new
formal-QA automation mode already exists**.

The control `task.md`/`task.json` below remains the frozen, single-use
`76644` sandbox-decision recovery for an **old** unpublished candidate.
It is **not** PR #49's latest HEAD and is not a generic review task. Do NOT
run the historical `--resume 20261008T100821Z-76644` command to perform
PR #49 QA or any new task, and do not silently repoint its SHA. A future
fresh control task requires the explicit migration/provenance gates in the
design, tests, and independent PR #45 security review. The historical
instructions below are retained as forensic evidence, not active guidance.

When a supported, correctly pinned, **fresh** control task is authorized,
the operator's normal interface is preflight `bash scripts/ai-loop.sh --check`
(no models/edits), then `bash scripts/ai-loop.sh` for the exact task. Routine
Codex/Claude prompts come from the runner; a blocked gate returns one
structured human decision request. No automatic source-merge, PR approval,
real Git ADD, product execution or next-slice escalation.

---

# CURRENT — 76644 sandbox-decision recovery, 2026-10-08

Run `20261008T100821Z-76644` returned Codex developer_status
`NEEDS_DECISION`, with **zero modified allowed files**.
The operator read the private Codex decision reason on Mac:
"No files changed. Candidate, contract and evidence hashes match, but the
sandbox denied the required fresh read-only process-absence check. Recovery
rules block edits on inspection errors. The outer runner must establish
absence proof outside the sandbox before repair."

This is a separation-of-authority issue, not Mac containment PASS, not a
developer patch and not permission to escalate sandbox or skip PID validation.
Original A11b candidate HEAD remains
`59381d49a0f3992f1edd6ac5c8e1fd5a87d3cdad`.
The 76644 immutable Codex decision JSON SHA-256 is
`3e0778155ce211f1da82171af432b3a6e8fbb7212c7d8ee50bef1d5a6801a2f3`,
source-free Git projection:
`https://github.com/davidcatcat/davidcattoolsproject/blob/59f1f7a0033e440bcb306c58680be87f7c934c61/reports/ai-loop/diagnostics/20261008T100821Z-76644/round-0-codex/codex.json`.

Control contract has one new `sandbox_decision_recovery` tied to:
- original `76644` control SHA `2534621046491780dcbf7062856f148317edea2b`;
- exact original candidate HEAD above and main
  `ceae425eacaa8729a9eb8d796e43304918a8c908`;
- `76644` summary hash
  `4ba4ab9696ca0020e50eb0d951d18fb60d5b21ac163d936af22923390b5ae4ce`;
- exact Codex raw output SHA-256 above;
- original `64433` Mac native failure hash
  `c2b223ec0350917cb925ddbd28cb7b6d96ab4deff81c463d6113b165a254d9dc`;
- exactly one initially repairable test:
  `tools/llmtool/tests/test_git_containment.py`, optionally append a truthful
  pending note to the original validation report.

The **Mac outer runner**, not Codex sandbox, must reperform the
read-only host-only process absence check on the frozen cleanup inventory:
once at `--check`, AGAIN at formal `--resume`, both before calling any model.
It must check exact task/provenance/candidate, all ten original file digests,
original recovery bytes and original Codex decision. If PID proof fails, STOP;
do not signal previous PIDs, relax process permissions or fabricate evidence.

On actual resume, `recovery.json` records
`outer_absence_check.status=ABSENT_AT_PRECHECK` with
`authority=MAC_RUNNER` and original native inventory/evidence digests.
This is only a PRECONDITION at the check time, **not** a Mac native containment
PASS. Codex must read that handoff and must NOT rerun host `ps`/lsof/kill in
its restricted sandbox as a duplicate gate. If it has a NEW unmet
process-ownership precondition, return NEEDS_DECISION. Codex is authorized
only to change the already approved kqueue test in the one file and keep all
non-reaping/identity requirements; do not touch production modules or signal
code. The outer runner must rerun related checks, both full Python suites,
and all fourteen native Mac scenarios on changed bytes, then ordinary
independent review and exact-head CI before any Draft PR. No previous tests
or native scenarios are inherited; no auto-merge.

Operator command (only after matching control helper CI SUCCESS):

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T100821Z-76644 --check && \
bash scripts/ai-loop.sh --resume 20261008T100821Z-76644
```

Do not rerun used `64433` recovery, manually reset/change the stopped
candidate, create worktrees outside the canonical repo, or upload private
Codex/free-form/source logs. This is a one-use authorization, not an
unbounded retry loop.

# CURRENT — 76644 Codex decision gate confirmed (2026-10-08)

The Mac operator ran `bash scripts/ai-loop.sh --diagnose 20261008T100821Z-76644`.
Immutable curated report:
`https://github.com/davidcatcat/davidcattoolsproject/blob/59f1f7a0033e440bcb306c58680be87f7c934c61/reports/ai-loop/diagnostics/20261008T100821Z-76644/round-0-codex/codex.json`.

Frozen exact outcome: developer_status=`NEEDS_DECISION`; original JSON SHA-256
`3e0778155ce211f1da82171af432b3a6e8fbb7212c7d8ee50bef1d5a6801a2f3`;
changed_allowed_files=[]; repair_files_changed=[]; test_gates_reexecuted=false.
No Codex repair took effect, no new Mac native proof exists, and no PR/merge is authorized.
This is NOT a scope violation; previous runner STOP combined no-change with
forbidden-change and model-decision scenarios. The updated guard handles those
separately in future runs.

The model's human-facing decision rationale remains only in
`.ai-loop/20261008T100821Z-76644/codex-0.json` on the Mac. The existing
privacy contract explicitly does NOT publish its free-form `summary`.
Do not invent the rationale or automatically convert `NEEDS_DECISION`
to `PASS`/REPAIR. Until the rationale is available for coordinator review,
A11b.2.2.1 is BLOCKED_FOR_DECISION. The one-use 64433 recovery is consumed;
do NOT rerun `--resume 64433`, manually modify/reset/publish the candidate,
weaken the fourteen native cases, or signal old PIDs.

Read-only local rationale retrieval (nothing is published or executed):

```sh
python3 - <<'PY'
import json
from pathlib import Path
p = Path('.ai-loop/20261008T100821Z-76644/codex-0.json')
v = json.loads(p.read_text(encoding='utf-8'))
print('status:', v.get('status'))
print('decision reason:', v.get('summary'))
PY
```

The operator should redact any private credentials before sharing that summary.
If it identifies an approval/design conflict, obtain explicit bounded authority;
if it identifies a technical misconception, explain and re-evaluate it.
Only afterward consider one new evidence-pinned recovery (not an old run retry).
All related/full/native/security/QA gates still require fresh PASS on final bytes.

# CURRENT — 76644 stopped without any allowed-file changes (2026-10-08)

Last attempted Mac recovery: `20261008T100821Z-76644`, control
`2534621046491780dcbf7062856f148317edea2b`. It was the
first bounded Codex repair of kqueue-only failure `64433`. The
operator's recovery precheck PASSED, but the runner stopped immediately
after Codex, before related tests or ordinary Claude review:
`bounded recovery Codex edited outside authorized test files or made no
first-round test-harness repair`.

Published diagnostic
`https://github.com/davidcatcat/davidcattoolsproject/blob/665754111934ca6a510dc1345614dd6cdca41e1e/reports/ai-loop/diagnostics/20261008T100821Z-76644/index.md`
shows original HEAD `59381d49a0f3992f1edd6ac5c8e1fd5a87d3cdad`.
All ten allowed-file SHA-256 values, including the test and report,
are IDENTICAL to the frozen `64433` candidate. Therefore no
successful test-harness source fix is evidenced. No testing, native
acceptance, new Claude review, feature publication or merge happened.

The existing check was *correct to STOP*, but its error merged distinct
causes (forbidden path / zero source edits / provider NEEDS_DECISION).
The runner now reports these states separately and does not treat a
PASS claim with no changed test file as proof of repair.

**Do not rerun `--resume 64433` (one-use consumed), signal old
PIDs, manually clean/alter the unpublished branch, or start full/native
checks.** The original Codex developer JSON remains on the Mac, not on
Git. It is unknown at this point whether it returned PASS, FAIL or
NEEDS_DECISION. No diagnosis or source fix can be asserted from the
structured JSON until it is examined.

After helper CI success, use the no-model diagnostic action:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --diagnose 20261008T100821Z-76644
```

It verifies exact original control and current stopped HEAD, reads
local `codex-0.json`, compares the ten candidate files against the
original `recovery-inputs` snapshots, and publishes *only* status,
Codex-output SHA-256 and allowlisted changed-file names as a
source-free `round-0-codex` Git diagnostic. Provider summary, stdout,
stderr, private source and candidate internals remain on the Mac.
There are no model/test calls or changes to source, index or candidate.
An original `round-0-triage` is published separately.

Once the exact Codex status is confirmed, build the next single-use
bounded recovery from the 76644 evidence if and only if its semantics
support a safe repair; do not automatically authorize another provider
call against unchanged bytes. All fourteen Mac native cases, both
full suites, security/QA assessment and Draft PR publication are
still pending.

# CURRENT — A11b kqueue-only native failure recovery (64433), 2026-10-08

The operator ran `bash scripts/ai-loop.sh --diagnose 20261008T064055Z-64433`
successfully. Immutable Git report:
`https://github.com/davidcatcat/davidcattoolsproject/blob/c0db1d9195040212d23d5020876b5d5602419b16/reports/ai-loop/diagnostics/20261008T064055Z-64433/round-0-triage/index.md`.
It confirms `kqueue_non_reaping` / `ProcessLookupError` / `ESRCH` at
`test_git_containment.py:2334` (`ExitNote(pid)` registration) and
`_git_process_identity.py:148`, cleanup `RESOLVED` with no currently
retained owned processes, normal Claude review `SUCCESS_ENVELOPE` and
failure-coordination Claude `ERROR_ENVELOPE`. No classifier authorization is
claimed from the latter.

Current unpublished A11b candidate:
`59381d49a0f3992f1edd6ac5c8e1fd5a87d3cdad`.
Current pinned main:
`ceae425eacaa8729a9eb8d796e43304918a8c908`.
Exactly five related tests, read-only ordinary review and both full Python
suites passed on this stopped HEAD, but ONLY eight of fourteen Mac native
scenarios ran and PASS. Native acceptance is still FAIL. Original native error
is an untrusted test failure to investigate, not proof of product safety.

The bounded `acceptance_failure_recovery` contract now pins:
- run `20261008T064055Z-64433`, control
  `3c08dba54e6ac59db8385816ad51f2faaeacabee`;
- candidate HEAD `59381d49a0f3992f1edd6ac5c8e1fd5a87d3cdad`;
- local summary SHA-256
  `05f13f97a570ed41b70b69e9e2be246689d35c539eba020f7668d18ad804efb5`;
- Mac native evidence SHA-256
  `c2b223ec0350917cb925ddbd28cb7b6d96ab4deff81c463d6113b165a254d9dc`;
- round-0 raw failure evidence SHA-256
  `fd6b86c56af6836c08226ac762e5cf3fffa181eaab441d8b17762388848c08f5`;
- sole permitted initial test repair
  `tools/llmtool/tests/test_git_containment.py` (and optional truthful PENDING
  note in existing acceptance report). No production module edits authorized.

Preflight checks original Git-pinned task/provenance/HEAD, raw failing evidence
and hashes, exact eight prior scenario names and zero skips, native traceback and
ESRCH, expected failure-line source, and that Claude's failure classification
was an **error envelope**, not a successful classification. It requires a
**fresh read-only absence check** of the originally owned native process
inventory. Saved PIDs may NEVER be signaled by the recovery coordinator.
Any discrepancy must STOP before a provider call or source change.

Bounded repair instruction to Codex:
- Keep the before-exit `kqueue NOTE_EXIT` registration and event behavior
  intact, including proof of unreaped ownership/identity.
- For `zombie_first=True`, after verifying the same owned child is a zombie
  without reaping it, acknowledge that Darwin may refuse a *new*
  `EVFILT_PROC` registration with ESRCH. Test this expected platform behavior
  without accepting arbitrary ESRCH as success. Alternate successful
  registration/event behavior must remain valid only with equivalent proof.
- No skip, xfail, new filter fallback masquerading as exit notification,
  time/threshold weakening, extra signals, source/product module change,
  acceptance-script change, or deleted negative tests.
- All **fourteen** native scenarios remain mandatory, including both
  parent-SIGKILL ×20 configurations with the unchanged <=2s requirement.
- All related checks, both full suites, ordinary independent review, and
  final exact-HEAD CI must rerun on changed bytes. Prior PASS is not inherited.
- Independent security/QA and merge authorization remain pending.

First Mac action after helper CI turns green (zero-model precheck):

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T064055Z-64433 --check
```

Only if precheck PASS, execute the same command without `--check`.
Do NOT resume 60914 or any earlier run, cherry-pick, manually reset, signal
saved PIDs, publish the feature branch, skip native cases, or merge.

# CURRENT — A11b native failure, deterministic diagnosis, 2026-10-08

Run `20261008T064055Z-64433` STOPPED on unpublished candidate
`59381d49a0f3992f1edd6ac5c8e1fd5a87d3cdad`, still based on
`ceae425eacaa8729a9eb8d796e43304918a8c908`.
Five related checks, preliminary ordinary Claude review, and both complete
Python suites passed on these bytes. This is NOT native acceptance or security PASS.

Mac native suite passed 8 of 14 scenarios and failed in
`kqueue_non_reaping` with `ProcessLookupError / ESRCH` when
`ExitNote(pid)` registered an EVFILT_PROC NOTE_EXIT notification.
The native test has two branches: register-before-exit and zombie-first.
The log already proves register-before-exit passed and retained an unreaped
owned-zombie anchor. The failed second branch explicitly waited for status 5
before attempting registration. Existing Darwin implementations document
ESRCH when registering EVFILT_PROC after a process has already exited.
Do not infer generic retry/flakiness; determine the intended acceptance
contract and safe expected outcome without weakening the before-exit path,
the identity/unreaped checks, or all fourteen native scenarios.

Fixture cleanup was recorded RESOLVED, owned_processes empty. A fresh read-only
absence check of original PID inventory is still required before native testing.
A subsequent Claude failure coordinator CLI returned exit 1. Its JSON envelope
had subtype=success and is_error=true; empty stderr does NOT establish
auth/quota cause. No classifier PASS or CODE_BUG judgment is inherited.
There is no authorization to blindly retry native probes, signal old PIDs,
merge/publish the candidate, or weaken acceptance requirements.

The control runner now automatically extracts bounded source line context and
Claude envelope metadata when a future run stops. Private source excerpts remain
Mac-only, under `.ai-loop/<run>/triage-local.json`; only schema-validated
file/line/exception/provider-state metadata is eligible for Git diagnostics.
Raw provider output and secret/auth logs are never published.

For this ALREADY stopped run, use the separately authorized diagnostic-only
mode (no Codex/Claude, no tests, no candidate/index edits):

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --diagnose 20261008T064055Z-64433
```

This enforces original task/control SHA, exact stopped candidate HEAD and source
hashes against the native evidence, then publishes an immutable
`round-0-triage/index.md` containing source-free metadata on the dedicated
diagnostics branch. The command does not authorize recovery. Do not run old
`--resume 60914` or unapproved `--resume 64433`.

# CURRENT 60914 blocked ordinary review recovery — 2026-10-08

The unpublished A11b candidate `35ad4be...` passed five related suites on
post-PR48 main `ceae425...`, but Claude ordinary review correctly found two
old-base test-harness pins and returned FAIL. The subsequent read-only failure
classification CLI exited 1, with empty stderr. Do not infer quota/auth or
rerun blindly. The exact signed-off source review has two blocking test paths,
so a constrained first Codex repair is authorized without model reclassification.

The `review_failure_recovery` task contract pins the local stopped failure
SHA-256, original control and HEAD. The runner snapshots old bytes and permits
only two test harness edits plus pending validation-report note; no production
code changes or historical PASS. Both full suites and fourteen Mac gates remain
mandatory. See CURRENT task.md for SHA pins.

No-token preflight (after helper CI is green):

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T052643Z-60914 --check
```

# CURRENT A11b recovery after independent PR #48 — 2026-10-08

Run `20261008T035727Z-43467` stopped at full-final because an unrelated
real-subprocess interrupt test outside the A11b allowlist timed out once under the
Mac Python 3.9 full suite. The exact targeted test then passed 10/10. Independent
PR #48 retained all exact interrupt assertions, changed only the post-SIGINT
bounded controller wait from 5s to 10s, passed exact-head 3.9/3.14 CI, and merged
to main as `ceae425eacaa8729a9eb8d796e43304918a8c908`.

The current recovery treats Claude's original
`NEEDS_DECISION / TEST_BUG` as valid evidence that the blocking root-cause file
was outside task scope. It will resume only if the locally retained raw envelope
hash matches and its complete blocking file set is exactly the sole upstream #48
path. Previous round-0 native owned PIDs must also be proven absent read-only.
No historical test/native PASS is inherited.

No-token preflight, only after helper CI is green:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T035727Z-43467 --check
```

# CURRENT A11b Mac-preflight repair handoff — 2026-10-08

The post-PR47 candidate reached both full suites successfully, then stopped before
the first native containment scenario because the acceptance harness still pinned
the old main SHA. Current authorized recovery is run
`20261008T033528Z-35739`, exact candidate
`8bada855a99ce4660c66537922f3ad833b8669fb`.

Only two test-harness files are authorized for this repair:
`tools/llmtool/tests/run_containment_acceptance.py` and
`tools/llmtool/tests/test_git_containment.py`. Production containment files are
not authorized by this failure. The repair keeps strict base equality and changes
the stale base literal to post-PR47 main `63e232e...`; all native gates remain
non-PASS until rerun.

The helper now treats a zero-scenario Mac `preflight` failure as auto-repairable
only for an evidence-bound Claude `TEST_BUG` whose blocking files are all test
harness paths. It still refuses to infer any native PASS and reruns all validation
after changed bytes.

Current no-token preflight, only after helper CI is green:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261008T033528Z-35739 --check
```

The older 7365 recovery is consumed/historical.

# CURRENT A11b recovery after merged flaky-test fix — 2026-10-08

PR #47 was squash-merged to main at
`63e232e22a769f409e381e34dfe0ef1add766d8e`. The stopped A11b candidate remains
local and unpublished at `474679ecda36da610a96e7ec84da39a5dd3ae33b` from run
`20261007T183516Z-7365`.

The helper now supports this exact stopped-failure recovery across one separately
merged upstream fix. It requires the old/new base SHAs, candidate HEAD, original
control/evidence hashes, `SUMMARY_TOO_LONG` classifier rejection, resolved Mac
fixture cleanup, and an exact upstream path set. It refuses overlapping or additional
main changes. It snapshots the candidate, merges the pinned new main with a
two-parent merge, verifies every stopped A11b byte is unchanged, then validates
against the new base. The first recovery round calls no Codex: related/full/native
gates are rerun first. Any new blocking failure re-enters the bounded
failure->Claude classification->Git recommendation->Codex repair loop.

Current no-token preflight:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T183516Z-7365 --check
```

Do not use the older 96025 command or manually rebase/cherry-pick the candidate.
See CURRENT task.md for exact pins and stopping rules.

# CURRENT autonomous collaboration — 2026-10-08

David starts one canonical Mac invocation. The script publishes ordinary failures,
asks read-only Claude to classify and recommend repairs, publishes that recommendation,
then calls Codex and revalidates. David does not relay routine reports/review prompts.

| Role | Responsibility |
| --- | --- |
| Runner | Pins scope/bytes, runs tests, publishes reports, applies budget/progress/ownership checks, commits/pushes and checks CI |
| Claude | Read-only ordinary review and separate failure classification; concrete in-scope repair or decision/blocked disposition |
| Codex | Sole source/test writer in allowed_files; cannot stage/push, waive gates, expand scope or alter roadmap |
| David / functional coordinator | Feature/roadmap/design/authorization decisions, blocked physical prerequisites, completed-stage functional review |

Current run: `--resume 20261007T162747Z-96025`. The original candidate/failure hash
pins remain unchanged. Existing launcher fetches the new runner/task; no reinstall.
Resolve the previously identified old host 5769 fixture before this first resume
as already instructed; the read-only old-PID guard stays in place. New candidate
work must add failure cleanup in the owning test controller so ordinary subsequent
probe failures do not routinely become orphan fixtures/manual cleanup handoffs.

Related/full/native/developer/review/final-review/CI failures can enter Claude
classification. REPAIR is accepted only for blocking CODE_BUG/TEST_BUG findings
within allowed files, bound to exact HEAD and candidate/evidence hashes. Claude's
recommendation goes to Git before Codex edits. No code/test change means no retry.
Mac continuation requires a bound failure JSON and independently checked absence
of all recorded old probe PIDs; the runner never kills by saved IDs. CI repairs
update the same Draft PR with non-force Git and exact HEAD verification.

Current budget: four Codex calls including the first continuation; four failure
reviews; repeated same failure signature twice is stalled. Low/Note is deferred.
Stalled/exhausted/unsafe/missing-evidence remains uncompleted, never waived PASS.
Only a genuine scope/design/function/roadmap decision needs David's judgment;
auth/quota/network/missing Mac capabilities can still block the physical workflow.
No automatic login, privilege bypass, merge or next milestone.

Per-round failure/recommendation/disposition reports are immutable on
`automation/ai-loop-diagnostics`. Raw provider streams, candidate source and
original deterministic logs stay local; structured Claude repair guidance is an
intended published report. A final STOP still exports the visible ZIP and attempts
its curated publication. Results reach Git automatically; Work has no automatic
Mac callback and can read them when the completed/decision handoff is brought here.

See [CURRENT task](task.md) and [autonomous-helper validation](../../reports/ai-loop/checkpoints/ai-loop-autonomous-coordination-validation-20261008.md).
Earlier operator instructions below are historical except for original evidence,
canonical path and the current 96025 command. CURRENT policy supersedes their
old two-call/every-Mac-failure-stop rules.

# Mac AI-loop development handoff

## CURRENT operator step — assess fixtures, then bounded hung-supervisor repair

The diagnostic-only upload for `20261007T162747Z-96025` succeeded on David's Mac.
The coordinator verified the immutable Git evidence against the original captured
ZIP. Native FD inheritance and SIGINT/SIGTERM/SIGHUP passed on the old bytes;
`hung_supervisor` failed and nine later scenarios did not run.

A new bounded continuation is authorized in [task.md](task.md) and [the assessment](../../reports/ai-loop/checkpoints/A11b.2.2.1-sigint-failure-assessment-20261007.md).
It pins candidate `a00eda49aa0f0c3486d27589148361d67e7bdb3e` and the exact failure
hashes. Fresh operator ps localizes the fault to the intentional `closed_status` subcase
inside hung_supervisor. The new task addresses strict non-reaping exit-transition
observation and exact bounded subcase diagnostics; it preserves intentional EOF
and does not treat it as an unexplained crash. No old PASS is inherited and acceptance thresholds stay intact.

Before resuming, inspect possible retained fixtures and show the coordinator:

```sh
ps -ww -p 5769,5770 -o pid,ppid,pgid,state,etime,args
```

Saved PID numbers do not authorize signals. No reboot is required by the contract.
After fresh inspection/fixture absence, the only new continuation command is:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T162747Z-96025
```

The launcher downloads updates; no reinstall. One-use continuation, at most two
Codex calls total. All related/review/full/native gates run on changed bytes. A
new mandatory native failure stops without automatic retry; feature Draft PR and
security/coordinator review remain gated. Old 77009/87533 invocations are consumed.

## Diagnostic publication

The upload-only command remains available for a frozen stopped run:

```sh
bash scripts/ai-loop.sh --publish-diagnostics 20261007T162747Z-96025
```

It verifies the frozen ZIP manifest and original Git-pinned contract, then publishes
curated immutable files under `reports/ai-loop/diagnostics/<run-id>/` on reports-only
`automation/ai-loop-diagnostics`. Identical repeats are read-only; conflicts or
concurrent remote moves fail without force/retry. It changes no local branch,
HEAD, tree or index, starts no model/test/probe and makes no recovery claim.
After model/test STOP the runner exports a visible ZIP and attempts this upload.
Upload/auth/network failure preserves the original STOP and prints a補傳 command.

Published material: projected statuses, bounded provenance/capture/source hashes,
validation report, native JSON and mandatory logs. Raw provider streams, free-form
summaries, credentials, candidate source snapshots and unrelated files stay local.
Limits: 128 KiB/file and 512 KiB total. Structured API request files are used.
Work reads published evidence when David next messages; no automatic callback,
Mac executor, containment/security approval or recovery authorization is implied.

## Historical first native-failure recovery — superseded invocation

The older current-command labels below are historical. Use the CURRENT operator step above.

## Historical command — first native SIGINT failure recovery

The Mac run `20261007T123142Z-77009` stopped at SIGINT native acceptance FAIL,
at local candidate `ce29d4a6a7764e62dc993b76b33996db2fb3455b`. The coordinator
assessed David's supplied diagnostics and authorized a bounded repair continuation
on the same unpublished branch. See [the assessment](../../reports/ai-loop/checkpoints/A11b.2.2.1-sigint-failure-assessment-20261007.md)
and the CURRENT section of [task.md](task.md).

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T123142Z-77009
```

The installed launcher downloads the new runner/task automatically. No repeated
launcher installation is needed. Older quota/timeout resume commands below are
historical and are not the current command. `--check` can be appended for preflight
only; it spends no model tokens and does not consume the one-use authorization.

Recovery requires unchanged stopped commit/source hashes, Git-pinned old task and
failure records, a clean tracked/index state and no feature publication. It snapshots
candidate files and failure JSON before models and preserves unrelated artifacts.
If old probe PIDs still exist or inspection is ambiguous, it stops without signaling.
If retained processes are reported, inspect fresh state/arguments/parent-child
relationships and obtain coordinator resolution for the confirmed test parent.
Reboot is not required merely because a PID exists. A zombie is already exited;
operator handling concerns its confirmed stuck parent, not a zombie signal or an
unverified remembered process group. Do not delete recovery markers. Preflight failure
does not consume the one-use continuation.

At most two Codex repair calls; diagnostic preservation first, zombie identity cause
still unconfirmed. Related/Claude/two full suites/all fourteen Mac acceptance gates
run afresh on changed bytes. A repeat native failure stops without automatic retry.
Required security review, real ADD prohibition and merge/later-slice boundaries stay.

On a stopped model/test run, the runner exports a curated diagnostic ZIP to the
visible `ai-loop-diagnostics/` directory and prints its absolute path. It contains
candidate files, summary/contract/provenance and bounded native evidence/logs;
raw Codex/Claude streams and authentication files are excluded.
Generated ZIPs are locally excluded from Git staging, while `.ai-loop` remains the
internal run store. Existing unrelated files in the visible directory stay protected.
ZIP collisions, unsafe paths and export failures preserve the original STOP reason.

## Historical setup and earlier continuations

This standalone development helper lives on the persistent `automation/ai-loop`
control branch, available without merging its draft helper PR #45. It is independent
of llmtool product execution, approval and A11b/A11c acceptance.

Current task: **A11b.2.2.1 — private supervisor/fake-child containment harness**.
PR #44 and PR #46 are merged; the pinned main/start is
`585caf7d4c9dad20afd3beb38e5140d6dadf89e0`. Only fake children are authorized;
real llmtool Git ADD and A11b.2.2.2 remain prohibited.

## Run on the actual Mac

Preferred entry point is `scripts/ai-loop.sh` inside the canonical repository.
The launcher is already versioned on this control branch. To install it on David's
existing Mac checkout using the parent launcher he already has, run once:

```sh
cd /Users/davidsun/git/davidcataiporject
bash ai-loop.sh --install-launcher
```

This fetches the pinned control runner and installs only the launcher. It does not
load/start/resume the task, invoke Codex/Claude/gh, run suites/probes, stage/commit,
switch a branch or discard the stopped report. It needs Git/Bash/Python only. An
identical existing launcher is accepted; a different file or symlink is preserved
and installation stops. `--install-launcher` cannot be combined with task options.
The launcher may remain untracked on a feature branch until the helper is integrated;
installation does not imply it has been merged into main.

After the coordinator prepares the F3 decision and safe recovery contract, use:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh
```

No repeated manual download is needed. Each invocation fetches the control branch,
pins its SHA, downloads the matching runner/task.md/task.json and executes from the
canonical repository. There is no manual prompt transfer to Codex/Claude. The old
parent launcher remains compatible; `.git/ai-loop/run.sh` is another stable entry.
Installation is allowed now; it does not authorize rerunning the current stopped task.

```sh
bash scripts/ai-loop.sh --check    # optional preflight; no model/code/branch creation
bash scripts/ai-loop.sh --related  # related checks during implementation/repair
bash scripts/ai-loop.sh --full     # also full checks on the initial candidate
```

`--test-mode related|full` is equivalent. Mandatory task gates cannot be disabled by
`--related`: this containment task still requires final full local suites on BOTH
specified Mac Python interpreters and actual Mac acceptance. Each parent-SIGKILL
variant runs >=20 times with a <=2-second per-repetition disappearance bound. The
runner rejects missing/skipped/failed/stale Mac evidence. That is separate from
Linux/Python 3.9/3.14 exact-head CI. Related mode is not a containment shortcut.

After changes, related tests run first. Full and Mac checks are reused only when
source/test bytes are identical; report-only evidence changes do not rerun expensive
checks. Test phases display command position, PASS/FAIL and duration. [Running] is a
20-second heartbeat, not a restart.

## Handoff from another Chat/Work conversation

When David chooses script execution, the coordinating conversation must:

1. Read current remote main/PR/CI/review state, repo guidance and the originating spec.
2. Write the concrete authorized prompt to `automation/ai-loop/task.md` and matching
   executable `task.json` on this control branch. Pin base/start, branch, task ID,
   explicit allowed/required files, test commands/mode, mandatory gates, budgets and
   stop state. Do not merely tell David to paste a prompt into Codex.
3. Verify the runner supports the task. Add tested support before publication if its
   mode/gates are new. Verify the published remote files. Keep one coordinator/Git
   writer, and do not create a second competing implementation from another chat.
4. Tell David to execute `bash scripts/ai-loop.sh` from the canonical repo. This Work conversation has
   no connected Mac executor or automatic callback/approval-resume service.
5. Review the resulting Draft PR, validation evidence, exact SHA and CI here. Keep
   required independent security/QA/runtime gates distinct from ordinary Claude
   review. Merge/next slices require their applicable authorization.

This same flow is documented in tools/llmtool/WORKFLOW.md on the control branch.
Other conversations should explicitly read this branch until the helper PR is merged.
A new milestone task gets a new task ID. Completed tasks are not restarted to do new
work. A stopped run needs diagnosis/recovery before rerun; files and commits are
preserved. Never reset/stash/force-push/discard user work to restart blindly.

## Supported execution flows

- `implement_new_pr`: verify branch/PR absence and base ancestry, create a fresh
  feature branch from pinned origin/main, invoke Codex for the initial candidate,
  test/review, allow at most two repairs, run required final gates, append truthful
  validation evidence, re-review, publish and create one new Draft PR.
- `review_existing_pr`: verify an open unmerged PR at the pinned branch/base/head,
  safely reuse/fast-forward it, run selected tests and independent Claude review,
  skip Codex when already passing; repair only reproducible blockers, publish to
  the same PR and preserve its original description.

Both modes verify applicable llmtool CI on the exact final publication SHA before
completion. Missing/old runs are not PASS. The current implementation stops at
READY_FOR_FINAL_REVIEW with independent security review pending. No automatic merge
or next slice. A failed/unsupported Mac containment probe stops for coordinator
assessment, without automatic retries or lowering thresholds.

## Environment, authority and evidence

Only `/Users/davidsun/git/davidcataiporject/davidcattoolsproject` is the project root.
No alternate clone/worktree/checkout. Git/GitHub auth, Codex CLI, Claude Code, make,
Python >=3.9, tools/llmtool/.venv/bin/python and Mac /usr/bin/python3 are prerequisites.
The script preserves tracked/index cleanliness and unrelated untracked artifacts,
rejects changed main, branch collisions and unpublished/divergent work. Existing
isolated package installation must support the B5 -I launch; no global-install,
PYTHONPATH/cwd bypass or silent alternate interpreter is authorized for that launch.

GitHub authentication is checked in preflight. CLI installation/version does not
prove provider login; usable auth is checked when that provider is called. This new
implementation task calls Codex. Auth/quota/CLI errors and invalid structured outputs
stop with evidence; there is no permission bypass or blind provider retry.

Codex writes only allowed files; the outer runner is the sole Git writer. Claude has
Read/Glob/Grep only, no hooks/MCP, and cannot run tests or Git. JSON schema, exact
reviewed_head and blocking findings are checked; exit zero alone is not review PASS.
Mac evidence schema/hashes/counts are deterministic gates, not a proof of test-design
correctness. Independent source/security review still assesses that evidence.

Raw logs/prompts/model streams live locally in `.ai-loop/<run-id>/`, excluded through
.git/info/exclude. Curated report/provenance/check summaries go to Git; do not publish
secrets. Completion/bootstrap state lives in .git/ai-loop. Mac must remain awake and
online. Run only one writer; an active invocation retains its pinned runner/task.

The helper's process-group timeout handling is not the future llmtool B5 supervisor
and does not claim parent-SIGKILL or full descendant containment for model CLIs.
Authoring uses standalone helper fixtures, not an alternate project checkout. The
new implementation/gates have not run on David's Mac yet. See VALIDATION.md.

## Squash-merged branch handoff

A current feature HEAD need not be an ancestor of main after a squash merge. The
runner accepts that handoff only when its tracked tree exactly equals pinned main,
GitHub identifies the same branch/repository and exact HEAD as a merged PR, and
that PR's merge commit is an ancestor of pinned main. Equal content alone cannot
approve an unpublished or empty local commit. Different trees, unmerged PRs,
unknown merge ancestry and branch/PR collisions still stop with files preserved.

## Decision checkpoints on Git

When enabled by `publish_decision_checkpoint: true`, a valid initial Codex NEEDS_DECISION in a new task publishes a curated Markdown checkpoint under `reports/ai-loop/checkpoints/` on the control branch. The runner requires unchanged base HEAD and report-only edits, bounds the report, checks scope and remote path/control identity, uses a non-force API update, and verifies the exact remote blob. It never stages, commits or pushes the Mac feature branch in this path. Tests, probes, acceptance, approval and resume remain pending; no new feature PR is created. The displayed Git URL lets the coordinator read evidence without asking David to cat it.

Publication errors preserve the original NEEDS_DECISION and local report. Raw provider streams, prompts and private logs stay on the Mac. Source changes, later repair decisions, invalid results, auth/CLI/scope/test errors are outside this narrow automatic publication path. They are not silently uploaded. A stopped task still requires explicit coordinator recovery.

The current run already stopped using the older runner. Its report was imported from David's attachment: [F3 stop checkpoint](../../reports/ai-loop/checkpoints/A11b.2.2.1-containment-20261007-20261007T064116Z-68730.md). Do not rerun the current task until the continuity decision and recovery contract are ready.


## Current F3 recovery handoff — 2026-10-07

The first A11b.2.2.1 Mac run stopped before implementation on the F3 process-group continuity question. The coordinator accepted that stop and issued a recovery contract. The old local branch `feat/llmtool-a11b2-2-1-containment-20261007` and its local decision report are preserved as checkpoint evidence; do not delete/reset/reuse them. The current task uses the fresh branch `feat/llmtool-a11b2-2-1-containment-f3-recovery-20261007` from the same pinned main.

The accepted F3 topology uses the supervisor itself as the owned process-group anchor: supervisor starts a new session (`PID=PGID=SID`), fake children stay in that group, and the parent never reaps its owned supervisor before the group-cleanup decision. This removes the snapshot-only PGID reuse gap while keeping the task inside A11b.2.2.1. Mandatory Mac probes, both Python full suites and later independent security review remain unchanged. Real product Git ADD and A11b.2.2.2 remain prohibited.


## Continue the initial Codex quota stop — 2026-10-07

The explicit recovery contract accepts only run `20261007T075141Z-70725` on the
unpublished A11b.2.2.1 F3 recovery branch at the pinned base. From the canonical Mac:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T075141Z-70725
```

The installed launcher fetches the new runner/contract; no launcher replacement is
needed. `--resume RUN_ID --check` performs recovery preflight only. Plain invocation
still stops on partial-file collisions. The runner verifies the old Git-pinned
contract, saved provenance/STOPPED summary and quota error; it rejects any published
branch/PR or changed base/HEAD. Only initial Codex exit 1 before tests/commits is
supported, not arbitrary errors or post-publication recovery.

Before another model call, allowed partial files are copied into the new local run's
`recovery-inputs/` and hashes recorded in `recovery.json`. Existing old logs remain.
Current unrelated untracked files are fingerprinted and protected. Historical
unrelated integrity is UNVERIFIED because the old runner did not record its original
inventory. No old test/review results are reused. Codex continues the candidate,
then the same bounded tests/Claude/full-suite/native-Mac/Draft-PR/CI gates run.
A second interruption stops; this is not an automatic retry or session-resume service.

A one-use recovery claim is recorded before Codex starts. Repeating this recovery
after another stop is rejected; do not delete its marker to bypass the guard.


## Current timeout continuation — 2026-10-07

The latest explicit recovery replaces the quota run above. Run from the canonical
Mac repository:

```sh
cd /Users/davidsun/git/davidcataiporject/davidcattoolsproject
bash scripts/ai-loop.sh --resume 20261007T113658Z-74880
```

Codex now has a 3600-second limit; tests and Claude retain their existing limits.
The task, branch/base, implementation scope and mandatory final gates stay pinned.
Existing launcher fetches everything; no new download/install is needed. The new
one-use claim leaves the prior quota claim intact. Old recovery fingerprints protect
unrelated files; new out-of-scope files, an active ai-loop Codex exec for this repo, index/staging changes,
changed base/branch/PR or unrelated tracked edits stop before model work. Allowed
tracked edits and untracked candidate files are snapshotted and continued. All tests
and review run on resulting bytes, with no inherited PASS or automatic merge.


## Process guard correction — 2026-10-07

The timeout continuation initially rejected every process whose name included
Codex. David's process listing exposed false matches: VS Code's backend, app-server
daemons, code-mode hosts and a plain interactive terminal. Do not terminate these
services merely to satisfy the recovery preflight.

The guard now reads full arguments with `ps -ww` and identifies only `codex exec`
using this canonical repository's ai-loop schema/output log paths. A matching active
runner stops with its PID; ambiguous matching arguments or inspection errors still
stop. Background services, plain interactive terminals and other repositories'
exec calls are not evidence that this ai-loop's writer is active. This check does
not establish the activity or safety of unrelated sessions; keep one writer for
the task. No process is terminated by the preflight. The failed preflight did not
consume the one-use claim: repeat the same current timeout resume command after
fetching this update. All branch/file/acceptance gates remain intact.


## Descriptive progress and three-hour AI calls — 2026-10-07

Codex and Claude each have a three-hour single-call timeout. Test command timeouts
remain unchanged; auth/quota/errors still stop, repairs remain bounded and no
acceptance gate is weakened. A running process keeps its pinned settings; updates
apply at the next launch. Do not restart a current review just for these changes.

Every 20 seconds, a still-running command prints phase/purpose, elapsed time, limit
and observed log activity, for example:

```text
[Claude／獨立審查] 檢查需求、完整程式差異與測試證據（唯讀）｜已執行 00:04:20／上限 03:00:00｜暫無新增日誌；等待結果
[Codex／實作修正] 依 task.md 產生或補完候選程式｜已執行 00:12:20／上限 03:00:00｜日誌有更新
[測試 3/5] Git 前置檢查與錯誤清理回歸測試｜已執行 00:00:20／上限 00:30:00｜日誌有更新
```

These are workflow descriptions plus observed time/log-size changes, not model
completion percentages or claims about a specific live internal action. No raw
log content or private reasoning is copied to the console. Claude can buffer its
JSON until completion, so unchanged logs alone do not establish a hang.

## Classification rejection diagnostics — 2026-10-08

Failure classification remains strict: exact reviewed HEAD/evidence SHA256, allowed
files, bounded strings/findings and decision/category agreement. The Claude request
schema now states the existing 2000-character string and 16-finding limits and pins
the expected HEAD/digest. The runner never truncates, rewrites a mismatched identity,
or retries a rejected classification to turn it into an accepted repair.

A rejected classification automatically publishes `round-N-rejection/rejection.json`
with specific fixed reason codes, hashes, match booleans and string lengths. No raw
provider output, model summaries/findings, credentials, session metadata or private
log text is exported by this projection. Failed tests still block delivery.

For an older stopped run whose terminal diagnostics are already published, run:

```sh
bash scripts/ai-loop.sh --publish-diagnostics RUN_ID
```

This preserves the original ZIP/report paths and additionally reads original local
failure/classifier artifacts under the run's original Git-pinned contract. Only
rejected classifications receive a separate immutable rejection batch; valid reviews
are not relabeled. It does not invoke models/tests, signal processes, alter the
feature branch/index, authorize recovery or reset the existing repair budget.
Missing/unsafe/oversized provenance or changed inputs stop the upload. Identical
publication is idempotent; a different report at the same path is never overwritten.
