# Mac AI-loop development handoff

This standalone development helper lives on the persistent `automation/ai-loop`
control branch, available without merging its draft helper PR #45. It is independent
of llmtool product execution, approval and A11b/A11c acceptance.

Current task: **A11b.2.2.1 — private supervisor/fake-child containment harness**.
PR #44 and PR #46 are merged; the pinned main/start is
`585caf7d4c9dad20afd3beb38e5140d6dadf89e0`. Only fake children are authorized;
real llmtool Git ADD and A11b.2.2.2 remain prohibited.

## Run on the actual Mac

Use the launcher David already downloaded to the canonical repo's parent:

```sh
cd /Users/davidsun/git/davidcataiporject
bash ai-loop.sh
```

No repeated download is needed. Each invocation fetches the control branch, pins its
SHA, downloads the matching runner/task.md/task.json and executes from the canonical
repository. There is no manual prompt transfer to Codex/Claude. A stable alternative
after first launch is the canonical repo's `.git/ai-loop/run.sh`.

```sh
bash ai-loop.sh --check    # optional preflight; no model/code/branch creation
bash ai-loop.sh --related  # default: related checks during implementation/repair
bash ai-loop.sh --full     # also run full checks on the initial candidate
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
4. Tell David to execute his existing `bash ai-loop.sh`. This Work conversation has
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
