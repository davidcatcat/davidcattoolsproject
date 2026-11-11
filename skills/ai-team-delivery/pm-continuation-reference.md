# PM Continuation Reference

Reference detail for the normative [PM Continuation Contract](pm-continuation.md).

## D8 Host/Worker admission and review

For OpenCode Host/Worker jobs, D8 authorizes the PM to admit qualifying tasks without Owner-per-call approval. Confirm the actually-free, enabled route at admission (never infer current price from a historical `$0.00` record), D8-authorized non-sensitive data class, provider cap, retry stop and D6 write scope. The PM admits the task and screens each file set for secrets/restricted data; a file-set change within the authorized class needs no repeated per-file Owner approval ([Issue #120 direction](https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6289116138)). Do not generalize D8 to another provider, paid route or data class. Any OpenCode-authored Host or Worker update requires a distinct review before use. Admission or containment changes require formal Security review before the updated Host admits another Worker. These Host/Worker controls do not apply to normal direct Developer sessions; see the routing paragraph below.

## High Debug

For substantial uncertainty or root-cause work beyond the ordinary Developer route, the PM should proactively select a suitable high-capability Debug Developer when an authorized Worker route and the existing budget permit, subject to evidence/egress permission and D6 availability. Do not wait for the Owner to name a model. A model identity grants no dispatch, data, write or approval authority. PM analysis, a generic sub-agent or a formal Reviewer is not a Debug Developer assignment; bind the selected model and provider to a separate Debug Task. Diagnosis is read-only by default and grants no write authority, dispatch key, model-default change, provider fallback or acceptance.

The repair cap is 2 repairs in total, the 2nd after an AI Debug with a confirmed cause (D1/O2); a 3rd valid FAIL splits into at most 3 child Tasks (D3) 待實作（WP-D）. When an ordinary repair fails in the same confirmed root-cause/finding family, assign a separate bounded Codex CLI `gpt-6.1-sol` Debug Developer Task through `codex-developer` before attempting the 2nd repair, if all gates permit. Severe security/data-integrity, architecture or difficult root-cause uncertainty may justify earlier use with recorded evidence. Do not reset the failure-family count by renaming findings or changing Worker/Task; a Reviewer independent of the author adjudicates equivalence.

Before dispatch, reread the current `codex-developer` switch through the Python dispatch gate and confirm actual CLI/model availability, identity, permitted evidence and data egress, budget, and a verified read-only D6. Record the Debug Task ID, failed-correction count/family, actual Worker/model/provider, source SHA, diagnostic target, evidence and data classes, scope, budget and limits, usage/cost or `UNKNOWN`, outcome and report URL. Do not claim a live invocation without runtime evidence.

The Debug Task must return the [mandatory High Debug report](high-debug-report.md): evidence-supported Root Cause and confidence, exact repository paths/functions and source references at the analyzed SHA, precise changes and focused verification, or `RCA_UNCONFIRMED`/`BLOCKED` with the smallest next probe. A diagnosis is not a PASS or merge gate. Implementation requires a separate authorized Developer Task, verified D6 single-writer handoff, and the usual deterministic checks, independent review, exact-head CI and applicable runtime gates.

## Retry and reassignment

Before every retry or reassignment, reread and validate `worker-dispatch.json`, record the decision, and confirm task scope, privacy/data permission, budget, current technical availability and independent-review gates. A missing/unreadable or malformed file, `schema_version` other than `1`, missing Worker entry, or absent/unrecognized exact-case `dispatch` value means refuse and record why. Only `PAUSED`, `OWNER_ASSIGNMENT_ONLY` and `ALLOWED` are valid: `PAUSED` refuses regardless of provider health; `OWNER_ASSIGNMENT_ONLY` requires an explicit Owner assignment; `ALLOWED` proceeds only through all remaining gates.

Retry only for a transient cause and within the existing Task budget. Never replay UNKNOWN or non-replayable operations. Reassign only to a permitted, freshly available Worker after the previous writer has verifiably released the D6. Do not use a paid fallback unless already authorized. These checks do not expand authority or change the [Owner escalation rule](owner-escalation.md).

## Closing process gaps

When an AI finds that it missed something, whether the AI or the Owner noticed it first:

1. Open a GitHub Issue that states what was missed, the root cause and the fix.
2. Fix the immediate gap.
3. Change the rule, skill or tool so the same miss cannot recur.
4. Close the Issue only when the change is merged.

## Quota and Reviewer continuation

Quota exhaustion is a technical availability observation, not a dispatch-authority change. An `OWNER_ASSIGNMENT_ONLY` Worker cannot be assigned to a different Task, retried or used as fallback without the exact Owner binding. For quota-limited Developer Tasks, persist the exact Task/Session, checked-out SHA, partial artifacts, changed-file scope, tests and the last confirmed action; mark UNKNOWN operations as non-replayable. Confirm the previous Writer released its D6 lease before any reassignment. Never broaden a Task or silently buy a fallback.

If a formal Reviewer is unavailable, do **not** replace it with an unapproved model or treat Developer pre-review as acceptance. Mark only the affected Reviewer handoff WAITING with recovery condition, let other independent work continue, and request an Owner decision only if an exception to the [cross-family Reviewer pairing](owner-escalation.md) is actually needed. No merged PR may rely on a missing formal verdict.

## Session close-out

A Reviewer or Developer session is done once its result is recorded (a verdict on GitHub, or a Developer outcome in llmtool). A Reviewer's own report that it posted does not count. The PM confirms that a `Verdict: … | HEAD: <dispatched head>` comment exists on the PR. If it is missing, resume the Reviewer to post it (#528). A Reviewer request asks it to run only tests relevant to the change, leave the full suite to exact-head CI, and end the session after posting.

After recording the next decision, check `claude agents` for that session. If still busy, stop it with `claude stop <id>`. Send `TERM` only to leftover processes whose working directory is inside that session's own D6 root; do not touch other processes or use a stronger signal first. Record close-out on the PR or Issue and retire the D6 after merge under [safe-git](../safe-git/worktree-lifecycle.md).

After a merge train lands, run `llmtool task advance <task> --pr <N> --integration-pr <train>` for each original Task and confirm `step: CLOSED` before retiring D6s for original PRs, their Reviewer D6s and the train. A Task not CLOSED keeps its D6 and is listed on the train PR (#547). List any D6 the lifecycle tool refuses to retire on the train PR (#529). Leftover full-suite runs compete with Mac CI runners and change no decision.
