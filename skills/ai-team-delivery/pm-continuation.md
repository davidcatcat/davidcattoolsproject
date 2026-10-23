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
3. **Choose one action and record it:**
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

When the monitor needs it, begin the GitHub post with whole-line `<!-- activity: type=… actor=… target=… status=… -->` markers. The [monitor source](../../scripts/llmtool_monitor.py) defines the marker format.

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
