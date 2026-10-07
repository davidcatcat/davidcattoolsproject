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
