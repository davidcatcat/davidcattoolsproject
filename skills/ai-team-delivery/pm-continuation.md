# PM Continuation Contract (normative)

This is the single rule for what the Integrator/PM does after each work result. Other guidance links here instead of restating it. [AGENTS.md](../../AGENTS.md) owns authority and roles. The [delivery workflow](SKILL.md) owns the Task Contract and review gates. The [Owner escalation reference](owner-escalation.md) owns escalation. This contract adds no Git, provider-spend, data-egress or security authority.

## Every result gets a next decision

When the PM receives PASS, FAIL or BLOCKED for a Task, it records the next decision before it stops or turns to other work. "Waiting for the Owner" is valid only under the escalation rule below.

| Result | Required PM decision |
| --- | --- |
| Developer PASS | Send the exact-SHA candidate to the independent Reviewer for that Task. |
| Reviewer FAIL | Return the bounded findings to the original Developer inside the same Task Contract, then request rereview of the affected scope. The repair dispatch lists **every** finding of the FAIL verdict, advisory and Low included. Mark each one as fix, defer with a reason, or dispute with evidence. Reviewer disputes follow the O3 procedure (dispute with concrete technical evidence posted on the PR; see [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502)); the PM may defer only Low/advisory findings (E10: defer only Low or advisory findings with recorded reasons, never Medium, High or blocking defects; see [#1502](https://github.com/davidcatcat/davidcattoolsproject/issues/1502)). Post deferred and disputed findings on the PR so the next Reviewer sees them (#521). PM repairs of a reviewed PR go through `llmtool task repair <task> --executor-task <d6> --pr <N>` so each repair is a recorded claim that `task high-debug --failed-corrections` can count (#521). A Reviewer FAIL is a repair step for that Task. It does not stop the whole flow. |
| Reviewer PASS | Hand off to the Integrator for exact-head CI, any required Mac/runtime gate and the merge decision under [safe-git](../safe-git/SKILL.md). Before a PASS merges or joins a merge train, the PM triages every advisory and Low finding. Any finding about process-global state (signal handlers, atexit, environment, cwd, locks) is fixed before the merge, not deferred (#521; e.g. #524 broke train 13's Mac CI). |
| Test or CI FAIL | Make a bounded repair and rerun the check inside the original Task Contract, within the [repair bounds](defect-triage.md). |
| BLOCKED | Diagnose the block first (next section). Then retry, reassign or isolate the Task. |
| Work package complete | Check the queue and pick the next executable, authorized work package (see "Queue and IDLE"). |

After recording the next decision, close out completed sessions and retire eligible D6 worktrees using the [session close-out procedure](pm-continuation-reference.md#session-close-out).

### Validate a Developer result before review (Owner 2026-11-02)

`task advance` is the only normal flow: push -> EC2 before PR -> PR -> Linux CI -> review -> PASS -> mac-ci -> Mac CI -> merge with no main sync (M1, #1317); manual commands move to a recovery section.

Codex direct, AGY, OpenCode and home LLM `gw-agent` Developers do not run the Task's tests themselves. "Remote testing is handled by PM" in their reply means validating on EC2 before the PR; it does not mean the PM runs the tests locally. A Developer result is not ready for review until the PM has validated it on EC2 before the PR.

An EC2 remote-test failure is environment-specific only when exact-head CI passed the same module at the same SHA. Do not start a repair from test evidence alone while review of that head is running: wait for its verdict and carry every finding into the repair.

Run tests locally on the Mac only for what EC2 cannot run: interactive TUI/PTY tests, and code outside `tools/llmtool/tests` (for example a new top-level tool). Use `PYTHONDONTWRITEBYTECODE=1`, set `PYTHONPATH` to the worktree, and do it in a registered test D6 created with `d6_worktree_lifecycle.py --agent-id`, not in the Developer's D6. A local run never replaces the remote-test receipt.

#### Manual recovery flow

When `task advance` cannot run, manual commands are recovery only:

1. **Commit the result in its D6** without running tests inside it first. Local runs leave `__pycache__`, and ENV-2 then refuses the D6 (`D6_UNREGISTERED_LOCAL_STATE`, shown as `ENV_BOOTSTRAP_REJECTED`, #725). #775 fixed the env-probe `__pycache__` issue, but running tests in a D6 still creates `__pycache__`, so the no-tests-in-D6 rule remains. If anything stray appears, move it out; never delete Developer work.
2. **Record the result head:** `d6_worktree_lifecycle.py --record-result-task-id <d6>`. Without this, remote-test refuses with `RECEIPT_HEAD_UNRECORDED` (or `RECEIPT_HEAD_MISMATCH` when the recorded head differs from `--sha`).
3. **Run the Task's tests on EC2:** `llmtool task remote-test <task> --executor-task <d6> --sha <head> --ref refs/heads/<branch> --pr <N>`. Omit `--module` to use the admitted modules. Post the receipt on the PR: result, module, test count, head and receipt digest.
4. **Then request the formal review** of that exact head.

### Dispatch hygiene

These checks prevent refusals that cost a dispatch round (2026-11-02):

- **One Task ID per work package.** A Task ID names exactly one D6. Once a D6 is retired, its Task ID cannot be reused (`D6_TASK_ALREADY_EXISTS`). A standing Owner assignment that covers several slices, such as the TUI slices, needs one registered Task ID per slice in `worker-dispatch.json`.
- **PM-written Task text is screened.** The goal must be ≤ 512 bytes (`PM_RESPONSE_GOAL_TOO_LONG`); each acceptance item must be ≤ 512 bytes; acceptance must contain 1–16 items (`PM_RESPONSE_ACCEPTANCE_COUNT`). Limits count UTF-8 bytes, so a CJK character is typically about 3 bytes. Request, response and acceptance text must not contain credential-shaped text (a credential keyword followed by an assignment or a value), even when quoting a finding; describe it in words instead. Every write path must also be listed as a read path. Before `record-pm`, run `tools/llmtool/scripts/spec_preflight.py` on a hand-made spec; it prints field names only.
- **Sync the canonical checkout before dispatch.** Run `git merge --ff-only origin/main` in the canonical root first; a stale install refuses with `LLMTOOL_SOURCE_STALE`. The install stays automatic: the local repo fast-forwards to `main`, with no Owner step and no self-update tool ([Owner](https://github.com/davidcatcat/davidcattoolsproject/issues/1019#issuecomment-6669350210)).
- **A branch update needs a new review.** After `gh pr update-branch`, or any merge of main into a PR, the verdict no longer covers the head. Request a merge-delta re-review of the new head before merging, unless the new head equals the reviewed head.
- **Never wrap dispatch or repair in `timeout`.** It can kill Codex with signal 9. Run `llmtool task delegate` and any helper that runs it in the background, then poll. Set `ANTHROPIC_MODEL` explicitly for a background Claude dispatch until central role config (D1) lands.
- Stopping a waiting shell kills background work that shell started; account for that before stopping it.

### Sensitive-screen refusals (Owner 2026-11-05, #981)

Stop and read the refusal first. Record the refusal first as an Issue comment containing metadata only (code, rule names and line numbers), so it can serve as `--ref`. This is the only control on paths with no code gate, such as `DEVELOPER_OUTPUT_SENSITIVE`; refusal codes and rule names are safe metadata.
The cause is confirmed only when both the blocked content and triggering rule are established. An independent Reviewer or the PM establishes both locally, in memory, printing metadata only; blocked content must never reach GitHub, logs or a provider. If this is not possible, leave the item unconfirmed and stopped.
No one tells a model how to word output to pass the screen, before or after the cause is confirmed.
Until confirmation, do not reword evidence, reports or fixtures, or retry. After confirmation, removing a real value or changing test design is a different action.
For `task high-debug`, reruns on the same Task and family are refused until `python -m llmtool.high_debug screen-disposition` records the hits. Pass `--task-id`, `--family`, `--hit rule:line` (repeat for every hit), `--classification` and `--ref`. The local record is the JSON file for that Task and family in the `high-debug-screen` directory beside the task store. It stores at most 32 hit locations and counts any further hits in `omitted`. Refusal detail can be shortened with `more=N`, and that count can cover two different things: stored hits the display left out, which you can read in the record, and permanently omitted entries, which no record lists. The stored locations are every hit only when `omitted` is 0. When `omitted` is above 0, `screen-disposition` refuses with `HIGH_DEBUG_SCREEN_HITS_OMITTED` and reruns stay refused. Recover overflow with `python -m llmtool.high_debug screen-recover` (#1097). Pass `--task-id`, `--family` and `--d6` (the refused D6, which is the executor Task). For refused evidence or a refused report, also pass `--source` with the path of the original file on every command. Developer output takes no `--source`, because recovery rebuilds it in memory from the D6. With no other options, it prints the next batch as `rule:line` metadata. Confirm that batch by adding `--batch N`, one `--hit rule:line` for each listed hit, `--classification` and `--ref`. A batch holds at most 32 hits, and batches go in order. A partial confirmation clears nothing. Progress persists between commands. A wrong, repeated or out-of-range batch refuses (`HIGH_DEBUG_SCREEN_BATCH_WRONG`, `_REPEATED`, `_OUT_OF_RANGE`). The block clears only when the last batch is confirmed. Recovery binds to the refusal: the record keeps the total hit count, a digest of the complete hit list and a full SHA-256 digest of the screened source, and no refused text is kept anywhere (Owner decision on PR 1110). Each command rereads the original source, checks that digest and rescreens it, so never make a copy of refused text to satisfy recovery. A missing source refuses with `HIGH_DEBUG_SCREEN_SOURCE_MISSING`. This includes a report, because its temporary file is deleted after the run, and a text-route Developer reply, because it was never a D6 diff. Both stay blocked unless the identical bytes still exist in an original file or the D6. A changed or unreadable source refuses with `HIGH_DEBUG_SCREEN_SOURCE_MISMATCH`, and changed hits refuse with `HIGH_DEBUG_SCREEN_HITS_MISMATCH`. A changed history refuses with `HIGH_DEBUG_SCREEN_HISTORY_INVALID`, and a different D6 refuses with `HIGH_DEBUG_SCREEN_RECOVERY_BINDING`. A record with no digests (legacy) refuses with `HIGH_DEBUG_SCREEN_RECOVERY_LEGACY` and stays blocked. The refusal and every batch stay in the `high-debug-screen-history` directory after clearing, and a cleared record whose history is incomplete is invalid. Never recover by changing D6 or family, deleting records or history, or adding a disposition by hand. A stored disposition beside omitted hits is rejected as `HIGH_DEBUG_SCREEN_RECORD_INVALID`, and a deleted record keeps the block. `--ref` must be an Issue comment URL of form `issues/N#issuecomment-M`, not a PR comment. If detail says `screen_record=UNRECORDED`, no record exists and the rule holds by hand. This gate covers task high-debug only so far.
No real credentials may be used. The older hygiene guidance to describe credential-shaped text in words applies to PM-written Task text before dispatch, not advice after a refusal.
Joined-string credential-shaped fixtures are allowed only in screen-tool tests as synthetic data labelled `# synthetic-fixture: screen-tool test`, confirmed by an independent Reviewer; once merged, check with `tools/llmtool/scripts/fixture_check.py`. This does not permit bypassing a formal security check. [Owner decision](https://github.com/davidcatcat/davidcattoolsproject/issues/763#issuecomment-6651110328).
A refused local commit must not reach origin, including in history. This is a PM rule under #981, not part of the Owner decision.

**On every refusal (Owner #763 A):** Stop the Task. Keep the audit record and the original D6. Report only the rule name, file, line, Task ID and refusal type, never values. Classify the refusal as a real block, a test-design problem or a detector false positive, and complete the #981 disposition record before any new flow. Never pass refused content to a new Developer, never resend the old Task and never hint how to pass the screen. A false positive gets a bounded fix package with RCA and a regression test; rotating Developers is not a fix. Start at most one new independent Developer Task per blocked item, from latest main. If that Task is refused again, stop and create no third; investigate the detector-defect family and report the RCA and a proposed fix to the PM. The existing defect-family repair cap stays, and a new Task or a new D6 never resets it. High Debug, Reviewer and security rules are unchanged. The joined-fixture sentence above stays the only exception to the [test data rule](SKILL.md#test-data-rule-owner-763-a-2026-11-08). The PM keeps an efficiency log on the work-package Issue: refusal count, confirmed false positives, test-design stops, extra Developer and Reviewer runs, recurring rules and root causes, with UNKNOWN where data is missing and no refused values. [Owner decision, relayed](https://github.com/davidcatcat/davidcattoolsproject/issues/763#issuecomment-6717848625).

### Rule-file PRs (Owner 2026-11-05, #1019)

A PR that changes a path in `tools/llmtool/sensitive-paths.json` is a rule-file PR. It needs formal review and exact-head CI like any other PR and no per-PR Owner approval. Check the list as installed, not the PR head, and count a rename out of a listed path. If the list cannot be read, treat the PR as a rule-file PR. `CLAUDE.md`, `AGENTS.md` and `skills/` are not listed for now.

Each merge of a rule-file PR raises an alert at the top of the monitor. It stays until the Owner asks the PM to clear it; the PM then runs `scripts/llmtool_monitor.py --clear-rule-alert <PR>`. The PM never clears an alert on its own. The Owner does not need to act before the merge.

This is an accident guard, not a security boundary (S2): a same-user AI can still run `gh pr merge` or edit the alert record. Report that plainly and never claim the alert prevents it. AI never uses `sudo`, `su` or root. Accounts, passwords, credentials and keys need an explicit Owner authorization first.

### Sub-agent PMs (Owner D8 2026-11-02)

The main PM may use sub-agent PMs proactively. Mode A is read-only and returns text only. Mode B owns exactly one named work package (WP) and dispatches its Developer, formal Reviewer, EC2 remote-test and repairs through the normal gates: cross-family review, one writer per D6 and required remote tests.

Before Mode B starts, post a `subpm-lease` claim comment on the WP Issue with `wp`, `holder`, `pm`, `paths` and `status=HELD`. Lease paths must not overlap any in-flight Task's `write_paths`. Register the sub-agent PM's D6 with `--lease-ref subpm:<wp>:<agent>`. After hand-back, the main PM marks the lease `RELEASED`.

Only the main PM merges, adopts Tasks, edits the #763 inventory or asks the Owner. A Mode B hand-back records: WP; Task IDs; D6 and lease-ref; Developer route and start receipt; PR; HEAD SHA; verdict comment URL; CI conclusion; open findings; lease state.

### Large work packages (Owner 2026-11-07)

The PM plans work as packages of 3 to 6 related items with one shared goal, known dependencies and shared acceptance ([Owner instruction, relayed](https://github.com/davidcatcat/davidcattoolsproject/issues/763#issuecomment-6704957386)). A package may hold several bounded PRs. Each PR keeps its own Task scope, acceptance and D6, with one writer per D6 and no conflicting writers.

Packaging changes no gate. The per-D6 [repair cap](pm-continuation-reference.md#repair-cap), [High Debug rule](pm-continuation-reference.md#high-debug), safety, authorization, formal review, EC2 and exact-head CI apply to each PR as before. Splitting or merging work to get around them is not allowed.

Independent packages run in parallel through Mode B sub-agent PMs with non-overlapping leases. Request formal review only for a finished PR head. On unfinished work, run only the checks that [Validate a Developer result before review](#validate-a-developer-result-before-review-owner-2026-11-02) allows, never tests inside a D6; final EC2, formal review and exact-head CI still decide.

Owner-reserved decisions, new permissions and security boundaries still stop the dependent work for the Owner.

## BLOCKED: diagnose, then retry, reassign or isolate

1. **Confirm the cause with evidence:** the exact error code, run/Task ID, observed time and the check that produced it. A stale comment, an assumption or a single indirect signal does not confirm a cause.
2. **Classify the cause:**
   - transient (timeout, rate limit, interrupted process);
   - Worker unavailable (quota, login, provider or VPN layer): first read the Owner switch in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json), then confirm live technical state through the [local monitor](../../scripts/llmtool_monitor.py); these are separate evidence.
   - a defect in the Task itself;
   - missing authority.
3. **Choose the next action using engineering judgment and record it:** The PM identifies whether the remaining obstacle is primarily missing information/evidence, capability, tooling, safety, authority, feasibility or route availability. Compare the useful next options—gather more evidence, use a qualified Developer or model, try a different technical route, make a bounded repair, or isolate the affected Task—against current Worker availability, task-specific quality evidence, data/privacy permission, D6 ownership, remaining budget and likely total cost/time. A `BLOCKED` result describes the current Task or route under those conditions; it does not establish that all routes or other work are blocked.

   Before parking a difficult unresolved Task, record a Debug escalation disposition in its existing Issue/Task record: the qualified model/Worker route considered and, if selected, its result; or the concrete gate/refusal reason (dispatch, availability, data permission or budget) and recovery condition. This record is required for continuation, but it does not require a high-model call for every `BLOCKED` Task.

   For substantial technical uncertainty, or after the first failed correction in the same confirmed root-cause/finding family before the 2nd repair 待實作（WP-D）, follow the [High Debug procedure and repair cap](pm-continuation-reference.md#high-debug) and its [High Debug report](high-debug-report.md).

   If the preferred route is unavailable or not authorized, choose another safe option if one exists. Do not increase spending, use a paid fallback, expand data permission or weaken D8. If the next useful action requires reserved authority, new spending/data permission, or no safe route remains, record the exact gate evidence and recovery condition and follow the Owner escalation rule below. Otherwise isolate only the affected Task and keep independent work moving.
   - **Retry or reassign:** reread and validate `worker-dispatch.json` before each action, record the gate decision and confirm fresh availability and all task, privacy, budget and review gates. Retry only transient failures within budget; never replay UNKNOWN or non-replayable operations. Reassign only after the prior writer releases the D6. Follow the [dispatch and handoff checklist](pm-continuation-reference.md#retry-and-reassignment); paid fallback is prohibited unless already authorized.
   - **Isolate:** mark only that Task BLOCKED, with the cause and the condition for resuming it.
4. **Keep other work moving.** One unavailable Worker or one BLOCKED Task does not block other independent, authorized Tasks. The PM continues dispatching them.

## Escalate only when continuing is not allowed

Escalate to the Owner only when one of these applies:

- the work needs authority the team does not have;
- the work cannot continue safely;
- a Task that does not end successfully notifies the Owner (D4);
- the decision belongs to the Owner under [owner-escalation](owner-escalation.md).

Everything else is a PM decision: routine failures, Reviewer FAIL, a quota pause, a VPN outage and repair choices. Record the PM decision and continue. While the escalation is open, only the dependent action waits.

Every Owner decision request must appear in the monitor (Owner 2026-10-28, #515):

- Ask in chat with the options in plain language and a recommendation.
- In the same step, post a comment on the relevant Issue or PR. It begins with `<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->`.
- When the Owner answers in chat, post a new comment that quotes the answer verbatim and says the PM is relaying it. It begins with `<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->`. The Owner never posts on GitHub, so the PM never writes an `actor=Owner` marker; the monitor settles the open row from this `target=Owner status=ANSWERED` marker.

A chat question alone does not count as asking the Owner.

Carried Owner acceptance ([Owner standing rule](https://github.com/davidcatcat/davidcattoolsproject/issues/763#issuecomment-6723320384)): if the relay comment passed to `task advance --merge --owner-acceptance URL` names an older head H0 of the same PR, the merge may go on at the current head H1 only when the PR's own changes since H0 are edits to test files already in the PR that add or delete no file, remove no assertion, skip or test method and add no skip. Carry mode carries only Owner product acceptance. H1 still needs its own bound PASS and exact-head CI. Any `MERGE_ACCEPTANCE_CARRY_*` refusal means the PM asks the Owner again for the current head.

For the required remediation after an AI misses something, see [closing process gaps](pm-continuation-reference.md#closing-process-gaps).

### Reviewer and Developer quota continuation (Owner 2026-10-27)

Follow the [quota and Reviewer continuation rules](pm-continuation-reference.md#quota-and-reviewer-continuation).

## PM takeover

1. Before any dispatch or merge, read [AGENTS.md](../../AGENTS.md), [ai-team-delivery](SKILL.md), this contract and the [continuation reference](pm-continuation-reference.md).
2. Check that the schedule prompt says to reread [Scheduled sweep](#scheduled-sweep) from `main` on every sweep.
3. List in-flight Tasks, open PRs and open Owner questions before choosing the next step; follow [Every result gets a next decision](#every-result-gets-a-next-decision) and [Queue and IDLE](#queue-and-idle).
4. Before retiring a parked D6, record its Debug escalation disposition under [BLOCKED](#blocked-diagnose-then-retry-reassign-or-isolate).
5. A handoff note or memory never replaces these documents. High Debug means a recorded `task high-debug` run, not a PM comment; follow [High Debug](pm-continuation-reference.md#high-debug).

## Queue and IDLE

Authorized PM work scope covers open Issues already authorized, Roadmap work, in-flight Tasks, and unfinished PRs, reviews and CI (Owner 2026-11-02, #763). The single work source is the #763 backlog inventory (plus GitHub deltas) within this authorized scope. The PM may split work and choose Developers under current gates, but never expand product scope, permissions, data egress or paid use.

After a work package completes, or after a Task is isolated, the PM runs the [Scheduled sweep](#scheduled-sweep) to advance in-flight work and pick the next executable, authorized item from the #763 inventory that has a Worker permitted by [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) with freshly confirmed live availability. The PM reports **IDLE** only when no in-flight step can move and no backlog item can run. The IDLE report lists every remaining item with its required undispatched-reason class and evidence (see below).

## Scheduled sweep

This single recurring schedule (about every 5 minutes in the PM session, for example `/loop 5m`, and after every result) (Owner 2026-11-02, #763). The PM does not wait on CI, a review or a single Worker while other authorized work in the #763 inventory can move. The schedule never authorizes work outside the authorized #763 scope, a Worker the policy does not allow, or a merge without its gates. Every sweep runs in this order:

1. **Continue in-flight work (uncapped):** advance every active Task, formal review, bounded repair, CI check and merge that can move. The per-sweep cap of 2 applies only to new Developer Tasks in step 3; reviews, repairs, CI checks and merges have no per-sweep cap.
2. **Check the backlog list:** reconcile the #763 inventory with GitHub deltas (new, reopened and unblocked items) within authorized scope. Never decide from only a few Issues, and never invent low-value Issues.
3. **Dispatch executable work (1–2 new Tasks):** when executable authorized work and a free Developer exist below the concurrency cap, dispatch at least 1 and at most 2 new Developer Tasks per sweep; a new Task may share a file with an in-flight Task, and only a real conflict on the same lines blocks it, with the later merge resolving any overlap. At the concurrency cap, make no new Developer Task dispatch. The concurrency cap is the maximum concurrent in-flight Developer Tasks set on tracking Issue #763 (bounded by distinct authorized, live-available Developer routes in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) and one writer per D6 Task under [AGENTS.md](../../AGENTS.md)). An Owner wait blocks only its dependent item and never stops other work.

**Board:** every sweep writes `~/.local/state/llmtool/pm-board.json`. Check each row against its real Issue, PR or Task and update its text, not only `updated_at`. A closed Issue leaves the board. A row nobody works on is not `running`: find its next step, dispatch it or move it (Owner 2026-11-07, #763).

**Main FULL Mac:** every sweep runs `llmtool ci full-mac-due` from canonical `main` and puts its JSON `code` in the sweep record. Only this command or a finished merge train starts a main FULL (Owner #1485). Exit 1 is a failed FULL or an alert: triage it at once. Exit 2 is a GitHub read or dispatch error: record it as an error, never as queued or passed. A manual FULL rerun stays traceable by its run and never replaces a PR's exact-head check.

**Row audit:** each sweep record lists every running and queue row as ref, who is on it now (Task, PR check or sub-agent, with ID) and next step. Nobody is allowed for one sweep only: by the next sweep the row is dispatched, moved to queue or blocked with a real reason, or raised to the Owner. A short no-change record is not allowed while any row says Nobody (Owner 2026-11-07, #763).

<a id="sweep-record"></a>
**Sweep record:** post a sweep comment on tracking Issue #763 only when there is an unworked row, an Owner item, a dispatch, a state change or an anomaly (E4) (no new Issue, no raw logs), starting with the exact whole-line marker:

`<!-- activity: type=pm_progress actor=PM target=PM status=SWEEP -->`

Sweep records must never use `target=Owner`, so [llmtool_monitor.py](../../scripts/llmtool_monitor.py) star rows for Owner decisions are neither raised nor settled by sweep comments. Each record holds:

- timestamp, sweep run ID, PM session and trigger (`schedule` or `Owner message`);
- backlog, in-flight and dispatchable counts;
- new Task IDs with Developer and Issue, plus verified Developer start evidence (per [Transition record](#transition-record));
- for each undispatched item (or by reference to the previous sweep comment when the undispatched list is unchanged), one allowed reason class with its evidence—`running` (`PR #N` or `Task <id>`), `waiting_owner` (link to the open `type=waiting_owner` comment), `blocked` (`blocked by #N` or overlapping writer `Task <id>`), `unauthorized` (specific scope/policy/budget/availability clause), or `closed` (`PR/Issue #N`)—forbidding vague free text such as `not suitable`, `later` or `low priority`;
- `running` applies only to the row it names and must not stand for the rest of the queue. Owner 2026-11-06: 「同檔案可派」 (“sharing a file with an in-flight Task does not block a dispatch; only a real conflict on the same lines does”).
- next steps.

**Anomalies, notifications and schedule control:**

- Zero new dispatch while executable work and a free Developer are available below the concurrency cap is an anomaly, not **IDLE**.
- Normal sweeps are silent in chat (send no chat message) and post a sweep comment on tracking Issue #763 only under the [Sweep record condition](#sweep-record); anomalies, sweep failures, Developer start failures and real Owner decisions also get dedicated GitHub records.
- Owner asks use `<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->`; a relayed Owner answer uses `<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->` (never an `actor=Owner` marker; the Owner does not use GitHub); verify both with `llmtool_monitor.py --once`.
- An Owner ask is made only when its `type=waiting_owner` marker is posted and shows in `llmtool_monitor.py --once`. Post it before asking in chat; a chat-only ask does not count, because the Owner does not scroll back (Owner 2026-11-07, #763).
- Status answers give numbers first (`dispatched`, `merged`, `remaining`).
- The schedule prompt must instruct the PM to re-read this `Scheduled sweep` section from canonical `main` on every sweep. The PM may stop and re-enable the schedule, recording both on the tracking Issue.
- The schedule interval is an Owner decision. No AI changes it, including on takeover or when rebuilding the schedule (Owner 2026-11-07, #763).

## Transition record

Task records are evidence: disk cleanup never deletes a TaskStore record that names a published PR (`published_pr`); it compresses and archives it instead (#1208 3b).

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
- Codex direct: the `task <32-hex>` ID and a running `llmtool task delegate` process, followed by the Codex session ID and first event (or a result line);
- OpenCode or AGY direct: Task developer state `DEVELOPER_DISPATCHING` with provider `opencode-cli` or `agy-cli`, plus a running delegate process, or a result;
- home LLM `gw-agent`: the recorded result.

Confirm the receipt within 2 minutes of dispatch. Until then record only `DISPATCHED` / `UNKNOWN`, and never report the Worker as delivered, started or working. While waiting for a result, watch Worker state (`claude_bg_dispatch.py watch --name <worker-role>` for Claude, using the same role as dispatch); a blocked, waiting or gone Worker is handled under [BLOCKED](#blocked-diagnose-then-retry-reassign-or-isolate).

Formal verdicts are posted with `gh pr comment`, never `gh pr review`, because Task advance scans issue comments. A skipped required check is NOT_RUN, never PASS. Merge only when every exact-head required check is `SUCCESS`, with macOS `SUCCESS` where required; `CANCELLED`, `NOT_RUN` or pending checks block the merge.

**Code PRs merge only through llmtool (#1208).** A PR that changes any path not classified as documentation merges only through `llmtool task advance --merge` or a merge train (`llmtool task train`). Both refuse a missing, skipped or red required check. The PM never runs `gh pr merge` on such a PR, including Owner-direct and monitor PRs. Reason: audit #1174 found 60 hand-merged PRs with a required check missing ([#1208](https://github.com/davidcatcat/davidcattoolsproject/issues/1208)). This is a rule, not a technical block: a same-user AI can still run `gh pr merge` (S2), so never claim it prevents that. A documentation-only PR keeps its current gates: formal review and exact-head `check (docs)`. If the llmtool merge path is broken, the PM records the defect and waits for the fix or an Owner decision; it does not fall back to `gh pr merge`.

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

Editing documents does not deliver a runtime capability. Current implementation status is tracked in the [Roadmap](../../tools/llmtool/ROADMAP.md) or the relevant Task/Issue. Until a handoff is implemented and verified, the PM performs that step manually. The PM never reports a manual step as automatic.
