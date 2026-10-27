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

### Close out sessions that finished their part

A Reviewer or Developer session is done once its result is recorded (a verdict on GitHub, or a Developer outcome in llmtool). A Reviewer request asks the Reviewer to run only tests relevant to the change, leave the full suite to exact-head CI, and end the session after posting. After recording the next decision, the PM checks `claude agents` for that session. If it is still busy, the PM stops it with `claude stop <id>`. It then sends `TERM` only to leftover processes whose working directory is inside that session's own D6 root; it never touches other processes and never uses a stronger signal first. The PM notes the close-out on the PR or Issue, and retires the D6 after merge under [safe-git](../safe-git/worktree-lifecycle.md). Leftover full-suite runs compete with the Mac CI runners and change no decision.

## BLOCKED: diagnose, then retry, reassign or isolate

1. **Confirm the cause with evidence:** the exact error code, run/Task ID, observed time and the check that produced it. A stale comment, an assumption or a single indirect signal does not confirm a cause.
2. **Classify the cause:**
   - transient (timeout, rate limit, interrupted process);
   - Worker unavailable (quota, login, provider or VPN layer): first read the Owner switch in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json), then confirm live technical state through the [local monitor](../../scripts/llmtool_monitor.py); these are separate evidence.
   - a defect in the Task itself;
   - missing authority.
3. **Choose the next action using engineering judgment and record it:** The PM identifies whether the remaining obstacle is primarily missing information/evidence, capability, tooling, safety, authority, feasibility or route availability. Compare the useful next options—gather more evidence, use a qualified Developer or model, try a different technical route, make a bounded repair, or isolate the affected Task—against current Worker availability, task-specific quality evidence, data/privacy permission, D6 ownership, remaining budget and likely total cost/time. A `BLOCKED` result describes the current Task or route under those conditions; it does not establish that all routes or other work are blocked.

   **Automatic High Debug trigger (Owner 2026-10-27):** after **two unsuccessful corrections in the same confirmed root-cause/finding family**, stop ordinary repeated repairs and, when independently authorized and within budget, assign a separate bounded **Codex CLI `gpt-6.1-sol` Debug Developer Task** (high reasoning when supported). The selected Worker route is `codex-developer`; this does **not** change the normal Codex Developer model or raise the existing per-assignment repair cap. For severe security/data-integrity, architecture or genuinely difficult root-cause work, PM may invoke it sooner with evidence. Never reset family counters by renaming findings or switching Worker/Task; a non-author reviewer/coordinator adjudicates ambiguous family grouping.

   The High Debug Developer is **diagnostic/read-only by default** and must return the [mandatory report](high-debug-report.md). It states the specific evidence-supported **Root Cause**, affected repository-relative file paths, functions/classes and line or section references on the analyzed SHA, **exact modifications required** (before → intended after), and focused reproducer/tests, regressions and residual risks. If facts do not support a cause or exact fix, mark `RCA_UNCONFIRMED` or `BLOCKED` and name the smallest next probe; never invent certainty. PM must reject generic advice with no concrete file/function/change instructions.

   Before launch, verify the current `codex-developer` switch via the Python dispatch gate, actual Codex CLI `gpt-6.1-sol` availability, model/reasoning identity, task-scoped non-sensitive evidence, data-egress authority, available budget and verified read-only D6 context. Record High Debug Task ID, prior failure-family attempts, actual Worker/model/provider, source SHA, bounded diagnostic target, evidence/permissions, usage/cost or `UNKNOWN`, outcome and GitHub report URL. Generic PM analysis, another model silently substituted, or formal Reviewer work does not count as this Developer assignment. The diagnostic result is not a PASS/merge gate; implementing changes requires a separate authorized Developer step and verified D6 Writer handoff, independent formal review, exact-head CI and applicable native tests. If preferred model cannot run, record specific denial and recovery condition, isolate only the affected Task and keep unrelated work moving; no paid fallback without Owner authority.
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

### Reviewer and Developer quota continuation (Owner 2026-10-27)

Quota exhaustion is a technical availability observation, not a dispatch-authority change. An `OWNER_ASSIGNMENT_ONLY` Worker (including AGY) cannot be assigned to a different Task, retried or used as fallback without the exact Owner binding. For quota-limited Developer Tasks, persist the exact Task/Session, checked-out SHA, partial artifacts, changed-file scope, tests and the last confirmed action; mark UNKNOWN operations as non-replayable. Confirm the previous Writer released its D6 lease before any reassignment. Never broaden a Task or silently buy a fallback.

If a formal Reviewer is unavailable, do **not** replace it with an unapproved model or treat Developer pre-review as acceptance. Mark only the affected Reviewer handoff WAITING with recovery condition, let other independent work continue, and request an Owner decision only if an exception to the [cross-family Reviewer pairing](owner-escalation.md) is actually needed. No merged PR may rely on a missing formal verdict.

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

Editing documents does not deliver a runtime capability. The [Roadmap](../../tools/llmtool/ROADMAP.md#a11c-work-packages-and-continuation-runtime-gaps) tracks which runtime handoffs are implemented and which are still gaps. Until a handoff is implemented and verified, the PM performs that step manually. The PM never reports a manual step as automatic.
