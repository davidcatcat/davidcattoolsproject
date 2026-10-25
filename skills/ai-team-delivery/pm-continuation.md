# PM Continuation Contract (normative)

This is the single rule for what the Integrator/PM does after each work result. Other guidance links here instead of restating it. [AGENTS.md](../../AGENTS.md) owns authority and roles. The [delivery workflow](SKILL.md) owns the Task Contract and review gates. The [Owner escalation reference](owner-escalation.md) owns escalation. This contract adds no Git, provider-spend, data-egress or security authority.

## Every result gets a next decision

When the PM receives PASS, FAIL or BLOCKED for a Task, it records the next decision before it stops or turns to other work. "Waiting for the Owner" is valid only under the escalation rule below.

| Result | Required PM decision |
| --- | --- |
| Developer PASS | Send the exact-SHA candidate to the independent Reviewer for that Task. |
| Reviewer FAIL | Return the bounded findings to the original Developer inside the same Task Contract, then request rereview of the affected scope. A Reviewer FAIL is a repair step for that Task. It does not stop the whole flow. |
| Reviewer PASS | Hand off to the Integrator for exact-head CI, any required Mac/runtime gate and the merge decision under [safe-git](../safe-git/SKILL.md). |
| Test or CI FAIL | Make a bounded repair and rerun the check inside the original Task Contract, within the [repair bounds](defect-triage.md). |
| BLOCKED | Diagnose the block first (next section). Then retry, reassign or isolate the Task. |
| Work package complete | Check the queue and pick the next executable, authorized work package (see "Queue and IDLE"). |

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

## Queue and IDLE

After a work package completes, or after a Task is isolated, the PM checks:

- the Roadmap;
- open A11c Issues and PRs;
- registered D6 Tasks.

It then picks the next work package that is executable and authorized, and has a Worker permitted by worker-dispatch.json with freshly confirmed live availability. The PM reports **IDLE** only when no such work package exists. The IDLE report lists every remaining item and the reason it cannot run.

## Transition record

Record every state transition on the Task's PR or tracking Issue, or in the llmtool TaskStore when the runtime records it. Each record contains:

- Task ID;
- from-state and to-state;
- the reason;
- the evidence, such as an exact SHA, a run ID or a check;
- the next responsible Agent.

For every review conclusion (including a rereview), publish a **new** GitHub comment on the Task's PR or tracking Issue. Begin the comment with the monitor-recognized, whole-line `<!-- activity: type=review_verdict actor=… target=… status=… -->` marker(s), using permitted field values. The **first line after those marker(s)** must be `Verdict: PASS | Version: vN | HEAD: <full-commit-sha>`, `Verdict: FAIL | Version: vN | HEAD: <full-commit-sha>`, or `Verdict: BLOCKED | Version: vN | HEAD: <full-commit-sha>`. Increment `vN` each review round, bind `HEAD` to the exact reviewed revision, and identify formal versus advisory review in the body; **never edit or replace an older verdict comment**.

For other GitHub activity posts, begin the post with whole-line `<!-- activity: type=… actor=… target=… status=… -->` markers when the monitor needs them. The [monitor source](../../scripts/llmtool_monitor.py) defines allowed marker values: for a `BLOCKED` review verdict, its current `review_verdict` marker supports `status=UNKNOWN` (not `status=BLOCKED`); the visible verdict line still reads `Verdict: BLOCKED`.

**Publishing a GitHub comment is not a wake-up.** A dispatch is delivered only when an observed receipt exists: the Worker run, an ACK or the target Session actually starting. Without one, record the dispatch as `DISPATCHED` with delivery `UNKNOWN`. Never record it as delivered or as started.

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

Editing documents does not deliver a runtime capability. The [Roadmap](../../tools/llmtool/ROADMAP.md#a11c-work-packages-and-continuation-runtime-gaps) tracks which runtime handoffs are implemented and which are still gaps. Until a handoff is implemented and verified, the PM performs that step manually. The PM never reports a manual step as automatic.
