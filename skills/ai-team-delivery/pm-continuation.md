# PM Continuation Contract (normative)

This is the single rule for what the Integrator/PM does after each work result. Other guidance links here instead of restating it. [AGENTS.md](../../AGENTS.md) owns authority and roles. The [delivery workflow](SKILL.md) owns the Task Contract and review gates. The [Owner escalation reference](owner-escalation.md) owns escalation. This contract adds no Git, provider-spend, data-egress or security authority.

## Every result gets a next decision

When the PM receives PASS, FAIL or BLOCKED for a Task, it records the next decision before it stops or turns to other work. "Waiting for the Owner" is valid only under the escalation rule below.

| Result | Required PM decision |
| --- | --- |
| Developer PASS | Send the exact-SHA candidate to the independent Reviewer for that Task. |
| Reviewer FAIL | Return the bounded findings to the original Developer inside the same Task Contract, then request rereview of the affected scope. The repair dispatch lists **every** finding of the FAIL verdict, advisory and Low included. Mark each one as fix, defer with a reason, or dispute with evidence. Post deferred and disputed findings on the PR so the next Reviewer sees them (#521). PM repairs of a reviewed PR go through `llmtool task repair <task> --executor-task <d6> --pr <N>` so each repair is a recorded claim that `task high-debug --failed-corrections` can count (#521). A Reviewer FAIL is a repair step for that Task. It does not stop the whole flow. |
| Reviewer PASS | Hand off to the Integrator for exact-head CI, any required Mac/runtime gate and the merge decision under [safe-git](../safe-git/SKILL.md). Before a PASS merges or joins a merge train, the PM triages every advisory and Low finding. Any finding about process-global state (signal handlers, atexit, environment, cwd, locks) is fixed before the merge, not deferred (#521; e.g. #524 broke train 13's Mac CI). |
| Test or CI FAIL | Make a bounded repair and rerun the check inside the original Task Contract, within the [repair bounds](defect-triage.md). |
| BLOCKED | Diagnose the block first (next section). Then retry, reassign or isolate the Task. |
| Work package complete | Check the queue and pick the next executable, authorized work package (see "Queue and IDLE"). |

### Close out sessions that finished their part

A Reviewer or Developer session is done once its result is recorded (a verdict on GitHub, or a Developer outcome in llmtool). A Reviewer's own report that it posted does not count. The PM confirms that a `Verdict: … | HEAD: <dispatched head>` comment exists on the PR. If it is missing, the PM resumes the Reviewer to post it (#528). A Reviewer request asks the Reviewer to run only tests relevant to the change, leave the full suite to exact-head CI, and end the session after posting. After recording the next decision, the PM checks `claude agents` for that session. If it is still busy, the PM stops it with `claude stop <id>`. It then sends `TERM` only to leftover processes whose working directory is inside that session's own D6 root; it never touches other processes and never uses a stronger signal first. The PM notes the close-out on the PR or Issue, and retires the D6 after merge under [safe-git](../safe-git/worktree-lifecycle.md). After a merge train lands, the PM first runs `llmtool task advance <task> --pr <N> --integration-pr <train>` for each original Task and confirms `step: CLOSED`; only then does it retire the D6 of every original PR in the train, every Reviewer D6 for those PRs, and the train's own D6. A Task that does not reach CLOSED keeps its D6 and is listed on the train PR (#547). A D6 that the lifecycle tool refuses is listed on the train PR; it is never left silently (#529). Leftover full-suite runs compete with the Mac CI runners and change no decision.

### Validate a Developer result before review (Owner 2026-11-02)

A Developer result is not ready for review until the PM has validated it on EC2. Codex direct, AGY, OpenCode and company `gw` Developers do not run the Task's tests themselves. "Remote testing is handled by PM" in their reply means this step; it does not mean the PM runs the tests locally.

1. **Commit the result in its D6** without running tests inside it first. Local runs leave `__pycache__`, and ENV-2 then refuses the D6 (`D6_UNREGISTERED_LOCAL_STATE`, shown as `ENV_BOOTSTRAP_REJECTED`, #725). If anything stray appears, move it out; never delete Developer work.
2. **Record the result head:** `d6_worktree_lifecycle.py --record-result-task-id <d6>`. Without this, remote-test refuses with `RECEIPT_HEAD_UNRECORDED` (or `RECEIPT_HEAD_MISMATCH` when the recorded head differs from `--sha`).
3. **Run the Task's tests on EC2:** `llmtool task remote-test <task> --executor-task <d6> --sha <head> --ref refs/heads/<branch> --pr <N>`. Omit `--module` to use the admitted modules. Post the receipt on the PR: result, module, test count, head and receipt digest.
4. **Then request the formal review** of that exact head.

Run tests locally on the Mac only for what EC2 cannot run: interactive TUI/PTY tests, and code outside `tools/llmtool/tests` (for example a new top-level tool). Use `PYTHONDONTWRITEBYTECODE=1`, set `PYTHONPATH` to the worktree, and do it in a separate checkout, not in the Developer's D6. A local run never replaces the remote-test receipt.

### Dispatch hygiene

These checks prevent refusals that cost a dispatch round (2026-11-02):

- **One Task ID per work package.** A Task ID names exactly one D6. Once a D6 is retired, its Task ID cannot be reused (`D6_TASK_ALREADY_EXISTS`). A standing Owner assignment that covers several slices, such as the TUI slices, needs one registered Task ID per slice in `worker-dispatch.json`.
- **PM-written Task text is screened.** Each acceptance item must be ≤ 512 bytes. Request, response and acceptance text must not contain credential-shaped text (a credential keyword followed by an assignment or a value), even when quoting a finding; describe it in words instead. Every write path must also be listed as a read path.
- **Sync the canonical checkout before dispatch.** Run `git merge --ff-only origin/main` in the canonical root first; a stale install refuses with `LLMTOOL_SOURCE_STALE`.
- **A branch update needs a new review.** After `gh pr update-branch`, or any merge of main into a PR, the verdict no longer covers the head. Request a merge-delta re-review of the new head before merging, unless the new head equals the reviewed head.

## BLOCKED: diagnose, then retry, reassign or isolate

1. **Confirm the cause with evidence:** the exact error code, run/Task ID, observed time and the check that produced it. A stale comment, an assumption or a single indirect signal does not confirm a cause.
2. **Classify the cause:**
   - transient (timeout, rate limit, interrupted process);
   - Worker unavailable (quota, login, provider or VPN layer): first read the Owner switch in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json), then confirm live technical state through the [local monitor](../../scripts/llmtool_monitor.py); these are separate evidence.
   - a defect in the Task itself;
   - missing authority.
3. **Choose the next action using engineering judgment and record it:** The PM identifies whether the remaining obstacle is primarily missing information/evidence, capability, tooling, safety, authority, feasibility or route availability. Compare the useful next options—gather more evidence, use a qualified Developer or model, try a different technical route, make a bounded repair, or isolate the affected Task—against current Worker availability, task-specific quality evidence, data/privacy permission, D6 ownership, remaining budget and likely total cost/time. A `BLOCKED` result describes the current Task or route under those conditions; it does not establish that all routes or other work are blocked.

   Before parking a difficult unresolved Task, record a Debug escalation disposition in its existing Issue/Task record: the qualified model/Worker route considered and, if selected, its result; or the concrete gate/refusal reason (dispatch, availability, data permission or budget) and recovery condition. This record is required for continuation, but it does not require a high-model call for every `BLOCKED` Task.

   For substantial technical uncertainty or root-cause work beyond the ordinary Developer route, the PM should proactively select a suitable high-capability Debug Developer when an authorized Worker route and the existing budget permit. Do not wait for the Owner to name a model. A generic Subagent, PM analysis or formal Claude Reviewer does not count as this Developer assignment unless explicitly selected and bound to the Debug task as a Developer with its model/provider recorded; formal review remains a separate gate. Record the Debug Task ID, actual Worker and exact model/provider, source or candidate SHA, diagnostic target, evidence/context and data classes, scope, budget and limits, and the result with usage/cost or `UNKNOWN`. Re-read `worker-dispatch.json` and confirm live availability, data-egress permission and budget before dispatch; a model identity grants no dispatch, data, write or approval authority. Debug advice is not acceptance, and any code change requires an authorized Task Contract, a verified D6 single-writer handoff and the usual deterministic, formal-review, CI and runtime gates.

   **Mandatory High Debug after two failed corrections (Owner 2026-10-27):** When two corrections fail in the same confirmed root-cause/finding family, stop ordinary repeated repairs and assign a separate, bounded Codex CLI `gpt-6.1-sol` Debug Developer Task, if all existing authorization, availability, data/D6 and budget gates permit. Use the existing `codex-developer` route; this does not change the normal Codex Developer model or grant another repair attempt. Severe security/data-integrity, architecture or difficult root-cause uncertainty may justify earlier use with recorded evidence. Never reset the family counter by renaming findings or changing Worker/Task; a Reviewer independent of the author adjudicates root-cause equivalence.

   High Debug is read-only by default and must return the [mandatory High Debug report](high-debug-report.md). The report gives the evidence-supported Root Cause, exact repository-relative paths and functions/classes to change, source references at the analyzed SHA, precise required changes and focused verification. If the evidence does not establish a cause or fix, mark it `RCA_UNCONFIRMED` or `BLOCKED` and name the smallest next probe. The report and diagnosis grant no write authority, model-default change, dispatch key, extra repair attempt, provider fallback or formal Reviewer acceptance.

   Before dispatch, validate the current `codex-developer` switch through the Python dispatch gate and confirm actual CLI/model availability, identity, permitted evidence and data egress, remaining budget, and a verified read-only D6 context. Record the High Debug Task ID, failed-correction count/family, actual Worker/model/provider, source SHA, diagnostic target, evidence and data classes, scope, budget and limits, usage/cost or `UNKNOWN`, outcome and report URL. A diagnosis is not a PASS or merge gate; implementation requires a separate authorized Developer Task, verified D6 Writer handoff and the usual independent review, exact-head CI and applicable runtime gates.

   If the preferred route is unavailable or not authorized, choose another safe option if one exists. Do not increase spending, use a paid fallback, expand data permission or weaken D8. If the next useful action requires reserved authority, new spending/data permission, or no safe route remains, record the exact gate evidence and recovery condition and follow the Owner escalation rule below. Otherwise isolate only the affected Task and keep independent work moving.
   - **Retry:** before every attempt, reread `worker-dispatch.json` and record the decision. A missing or unreadable file, malformed data, `schema_version` other than `1`, a missing Worker entry, or a missing or unrecognized exact-case value refuses the retry and records the reason. Only `PAUSED`, `OWNER_ASSIGNMENT_ONLY` and `ALLOWED` are valid. `PAUSED` refuses; `OWNER_ASSIGNMENT_ONLY` requires an explicit Owner assignment; `ALLOWED` still requires all existing task, privacy, budget, live-availability and independent-review gates. Retry only when the cause is transient and the Task budget allows it. Never replay an outcome that the runtime recorded as UNKNOWN or non-replayable.
   - **Reassign:** before every reassignment, reread `worker-dispatch.json` and record the decision. A missing or unreadable file, malformed data, `schema_version` other than `1`, a missing Worker entry, or a missing or unrecognized exact-case value means refuse and record the reason. Only `PAUSED`, `OWNER_ASSIGNMENT_ONLY` and `ALLOWED` are valid. Move the Task only to a Worker whose switch permits assignment and whose live technical availability is freshly confirmed. PAUSED refuses regardless of provider health; OWNER_ASSIGNMENT_ONLY requires an explicit Owner assignment; ALLOWED retains all task, privacy, budget, live-availability and independent-review gates. Keep D6 single-writer ownership: hand over only after the previous writer has verifiably released the Task. Never use a paid fallback that is not already authorized.
   - **Isolate:** mark only that Task BLOCKED, with the cause and the condition for resuming it.
4. **Keep other work moving.** One unavailable Worker or one BLOCKED Task does not block other independent, authorized Tasks. The PM continues dispatching them.

## Escalate only when continuing is not allowed

Escalate to the Owner only when one of these applies:

- the work needs authority the team does not have;
- the work cannot continue safely;
- the decision belongs to the Owner under [owner-escalation](owner-escalation.md).

Everything else is a PM decision: routine failures, Reviewer FAIL, a quota pause, a VPN outage and repair choices. Record the PM decision and continue. While the escalation is open, only the dependent action waits.

Every Owner decision request must appear in the monitor (Owner 2026-10-28, #515):

- Ask in chat with the options in plain language and a recommendation.
- In the same step, post a comment on the relevant Issue or PR. It begins with `<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->`.
- When the Owner answers, post a new comment that quotes the answer verbatim. It begins with `<!-- activity: type=waiting_owner actor=Owner target=PM status=ANSWERED -->`.

A chat question alone does not count as asking the Owner.

### Close your own gaps (Owner 2026-10-28, #515)

When an AI finds that it missed something, whether the AI or the Owner noticed it first:

1. Open a GitHub Issue that states what was missed, the root cause and the fix.
2. Fix the immediate gap.
3. Change the rule, skill or tool so the same miss cannot recur.
4. Close the Issue only when the change is merged.

### Reviewer and Developer quota continuation (Owner 2026-10-27)

Quota exhaustion is a technical availability observation, not a dispatch-authority change. An `OWNER_ASSIGNMENT_ONLY` Worker (including AGY) cannot be assigned to a different Task, retried or used as fallback without the exact Owner binding. For quota-limited Developer Tasks, persist the exact Task/Session, checked-out SHA, partial artifacts, changed-file scope, tests and the last confirmed action; mark UNKNOWN operations as non-replayable. Confirm the previous Writer released its D6 lease before any reassignment. Never broaden a Task or silently buy a fallback.

If a formal Reviewer is unavailable, do **not** replace it with an unapproved model or treat Developer pre-review as acceptance. Mark only the affected Reviewer handoff WAITING with recovery condition, let other independent work continue, and request an Owner decision only if an exception to the [cross-family Reviewer pairing](owner-escalation.md) is actually needed. No merged PR may rely on a missing formal verdict.

## Queue and IDLE

Work packages come only from the Owner's instructions (Owner 2026-10-27: 「工作範圍都以我給你的提示為準,不要自己去repo找工作」). The PM keeps a written list of the Owner's work packages for the session, adds each new Owner instruction to it, and does not add work it found on its own in the Roadmap, Issues or the repository. The Roadmap, open A11c Issues and PRs and registered D6 Tasks are read only to find the state and next step of a listed package.

After a work package completes, or after a Task is isolated, the PM picks the next listed package or step that is executable and authorized, and has a Worker permitted by worker-dispatch.json with freshly confirmed live availability. The PM reports **IDLE** only when no such package or step exists. The IDLE report lists every remaining item and the reason it cannot run.

### Parallel dispatch check (Owner 2026-10-27)

The PM does not wait on CI, a review or a single Worker while other listed work can move. About every 10 minutes (a recurring schedule in the PM session, for example `/loop 10m`), and after every result, the PM re-reads the work-package list and the state of each in-flight Task, then dispatches every package or next step (formal review, repair, merge, the next slice after a dependency lands) that is unblocked and writes no file another in-flight Task writes. It repeats until nothing more can run in parallel, then reports what is running and what each waiting item waits for. A check that finds nothing reports "no new dispatch". The schedule never authorizes work outside the list, a Worker the policy does not allow, or a merge without its gates.

## Transition record

Record every state transition on the Task's PR or tracking Issue, or in the llmtool TaskStore when the runtime records it. Each record contains:

- Task ID;
- from-state and to-state;
- the reason;
- the evidence, such as an exact SHA, a run ID or a check;
- the next responsible Agent.

For every review conclusion (including a rereview), publish a **new** GitHub comment on the Task's PR or tracking Issue. Begin the comment with the monitor-recognized, whole-line `<!-- activity: type=review_verdict actor=… target=… status=… -->` marker(s), using permitted field values. The **first line after those marker(s)** must be `Verdict: PASS | Version: vN | HEAD: <full-commit-sha>`, `Verdict: FAIL | Version: vN | HEAD: <full-commit-sha>`, or `Verdict: BLOCKED | Version: vN | HEAD: <full-commit-sha>`. Increment `vN` each review round, bind `HEAD` to the exact reviewed revision, and identify formal versus advisory review in the body; **never edit or replace an older verdict comment**.

For other GitHub activity posts, begin the post with whole-line `<!-- activity: type=… actor=… target=… status=… -->` markers when the monitor needs them. The [monitor source](../../scripts/llmtool_monitor.py) defines allowed marker values: for a `BLOCKED` review verdict, its current `review_verdict` marker supports `status=UNKNOWN` (not `status=BLOCKED`); the visible verdict line still reads `Verdict: BLOCKED`.

**Sending is not delivery.** A CLI exit code (including `claude --bg` exiting 0 or printing `backgrounded`), a GitHub comment and a SendMessage `success` show only that a dispatch was sent. A dispatch is delivered only when an observed start receipt exists:

- a background Claude session: `claude agents --json` shows it running (`busy` or `idle`) or finished (`state` `done`), and its transcript holds a first action stamped at or after the dispatch time (history carried over by `--resume` does not count); [claude_bg_dispatch.py](../../tools/llmtool/scripts/claude_bg_dispatch.py) checks this and prints the receipt;
- a Codex session: its session ID plus its first event;
- any other Worker: the Worker run or an ACK.

Confirm the receipt within 2 minutes of dispatch. Until then record the dispatch as `DISPATCHED` with delivery `UNKNOWN`, and never record or tell the Owner that the Worker is delivered, started or working. While waiting for the result, also watch the Worker's state (`claude_bg_dispatch.py watch` for a Claude session); a `blocked` or `waiting` Worker, or one that is gone, is handled at once under [BLOCKED](#blocked-diagnose-then-retry-reassign-or-isolate).

## What the skills own and what the runtime owns

**Skills** (this contract and the files it links) own:

- PM decision duty;
- role boundaries;
- dispatch rules;
- BLOCKED diagnosis and reassignment;
- the escalation conditions.

**The llmtool runtime** owns:

- completion events;
- resuming the original PM;
- persisting Task state;
- notification deduplication and replay protection;
- confirming Worker status.

Editing documents does not deliver a runtime capability. The [Roadmap](../../docs/history/llmtool-roadmap-archive-20261028.md#a11c-work-packages-and-continuation-runtime-gaps) tracks which runtime handoffs are implemented and which are still gaps. Until a handoff is implemented and verified, the PM performs that step manually. The PM never reports a manual step as automatic.
