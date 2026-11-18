# PM Operator Quick Reference

An index for the Claude Integrator/PM, not a rule source (#1502 WP-H). Each item is one sentence and a link to its current source, and that source wins if this page differs. Caps, sizes and models are never copied here: read them in the linked code or config.

## Dispatch

- **Authority:** reread [worker-dispatch.json](worker-dispatch.json) through the dispatch gate before every dispatch, retry, repair, recovery and Reviewer hand-off; `PAUSED` refuses whatever the live health and `OWNER_ASSIGNMENT_ONLY` needs an explicit Owner assignment ([CLAUDE.md Worker dispatch](../../CLAUDE.md#worker-dispatch)).
- **Owner PM rules:** write only problem, observable acceptance, constraints, scopes and evidence; no solutions, code, edits, exact test values, or PM technical inferences ([PM role card](../../.claude/skills/role-cards/pm.md#owner-pm-working-rules)).
- **Route and model:** take each Developer route's executable, model and timeout, and each Reviewer and Debug role model, from [developer-routes.json](developer-routes.json), never from memory or chat.
- **Reviewer pairing:** the formal Reviewer comes from the other model family than the Developer ([CLAUDE.md Review](../../CLAUDE.md#review)).
- **Spec preflight:** run [spec_preflight.py](scripts/spec_preflight.py) on every hand-made spec, and record or dispatch it only after a `SPEC_PREFLIGHT_OK` result ([dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Scope:** list every write path, and every test module to run on EC2, in the read paths ([WORKFLOW.md](WORKFLOW.md), [dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Fresh source:** fast-forward the canonical checkout to `origin/main` before dispatch, or llmtool refuses `LLMTOOL_SOURCE_STALE` ([dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Task IDs:** use one Task ID per work package, and never reuse the ID of a retired D6 (`D6_TASK_ALREADY_EXISTS`) ([dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Background runs:** never wrap `llmtool task delegate` in `timeout`; run it in the background and poll ([dispatch hygiene](../../.claude/skills/ai-team-delivery/pm-continuation.md#dispatch-hygiene)).
- **Test data:** every spec follows the four-point test data rule ([Owner #763 A](../../.claude/skills/ai-team-delivery/SKILL.md#test-data-rule-owner-763-a-2026-11-08)).
- **Delivery:** a dispatch counts as delivered only after an observed start receipt ([Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record)).

## Normal path

- **One flow:** `llmtool task advance` runs one step at a time: push, EC2 before the PR, PR, Linux CI, formal review, PASS, `mac-ci`, Mac CI, then merge without a main sync ([WORKFLOW.md](WORKFLOW.md#normal-delivery-flow); flags: `llmtool task advance --help`).
- **Before review:** a Developer result is ready for review only after its EC2 `task remote-test` receipt ([validate before review](../../.claude/skills/ai-team-delivery/pm-continuation.md#validate-a-developer-result-before-review-owner-2026-11-02)).
- **Every result:** record the next decision for each PASS, FAIL or BLOCKED before turning to other work ([PM Continuation Contract](../../.claude/skills/ai-team-delivery/pm-continuation.md#every-result-gets-a-next-decision)).
- **Review FAIL:** the repair through `llmtool task repair TASK --executor-task D6 --pr N` lists every finding as fix, defer (Low or advisory only) or dispute ([PM Continuation Contract](../../.claude/skills/ai-team-delivery/pm-continuation.md#every-result-gets-a-next-decision)).
- **Test or CI failure:** `task advance` returns it to the original Developer on the test-failure count, and an environment conclusion waits uncounted ([WORKFLOW.md](WORKFLOW.md#normal-delivery-flow)).
- **AI Debug:** after the first failed correction in a confirmed family, run `llmtool task high-debug` before the second repair ([High Debug](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#high-debug)).
- **Merge:** a code PR merges only through `llmtool task advance --merge` or `llmtool task train`, never `gh pr merge` ([Transition record](../../.claude/skills/ai-team-delivery/pm-continuation.md#transition-record)).
- **Close-out:** after a merge train, run `llmtool task advance <task> --pr <N> --integration-pr <train>` for each original Task and confirm `step: CLOSED` before retiring D6s ([session close-out](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#session-close-out)).

## Failure recovery

Legal recovery commands (the manual flow only when `task advance` cannot run):

- **Manual flow:** commit in the D6, run `d6_worktree_lifecycle.py --record-result-task-id <d6>`, run `llmtool task remote-test`, then request review ([manual recovery flow](../../.claude/skills/ai-team-delivery/pm-continuation.md#manual-recovery-flow)).
- **UNKNOWN repair claim:** `llmtool task repair TASK --executor-task D6 --pr N --recover-claim REPAIR_TASK_ID` settles it after its evidence checks, and the TaskStore is never edited by hand ([WORKFLOW.md](WORKFLOW.md#normal-delivery-flow)).
- **Worker back after quota:** relay the Owner's words, then run `llmtool worker-quota-recovered <worker> --owner-relay <URL>` ([quota continuation](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#quota-and-reviewer-continuation)).
- **High Debug screen block:** record hits with `python -m llmtool.high_debug screen-disposition`, or use `screen-recover` when hits were omitted ([sensitive-screen refusals](../../.claude/skills/ai-team-delivery/pm-continuation.md#sensitive-screen-refusals-owner-2026-11-05-981)).
- **BLOCKED:** confirm the cause with evidence, classify it, then retry (transient only), reassign or isolate ([BLOCKED](../../.claude/skills/ai-team-delivery/pm-continuation.md#blocked-diagnose-then-retry-reassign-or-isolate)).

Refusals not to retry unchanged:

- **Sensitive screen** (for example `DEVELOPER_OUTPUT_SENSITIVE`): stop, record metadata only, and never reword, reshape or resend ([sensitive-screen refusals](../../.claude/skills/ai-team-delivery/pm-continuation.md#sensitive-screen-refusals-owner-2026-11-05-981)).
- **Confirmed safety refusal:** it is never routed around, and reassignment resets no count ([confirmed refusals](../../.claude/skills/ai-team-delivery/pm-continuation.md#confirmed-refusals-owner-o5-1943)).
- **`WORKER_QUOTA_UNAVAILABLE`:** wait for expiry or the recorded recovery, and never take a paid fallback ([quota continuation](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#quota-and-reviewer-continuation)).
- **`TEST_FAIL_LIMIT_REACHED` or a spent repair cap:** no further repair on that chain; follow the Debug and split rule ([repair cap](../../.claude/skills/ai-team-delivery/pm-continuation-reference.md#repair-cap)).
- **`CONTINUES_*` and `CONTINUATION_LINK_REQUIRED`:** the chain shape is refused with no override ([lineage_counts.py](llmtool/lineage_counts.py)).
- **`MODULE_NOT_ADMITTED`, `RECEIPT_HEAD_UNRECORDED`, `RECEIPT_HEAD_MISMATCH`:** fix the Task read paths or the recorded result head first, since an unchanged rerun refuses again ([remote_test.py](llmtool/remote_test.py)).
- **`MERGE_ACCEPTANCE_CARRY_*`:** ask the Owner again for the current head ([escalation](../../.claude/skills/ai-team-delivery/pm-continuation.md#escalate-only-when-continuing-is-not-allowed)).
- **`REPAIR_CODEX_DEVELOPER_RETIRED`, `COMPANY_GW_DIRECT_RETIRED`:** the route is retired, so choose an `ALLOWED` Developer route ([CLAUDE.md Worker dispatch](../../CLAUDE.md#worker-dispatch)).

## Limits

Read each value in its source; this page copies none.

- **Dispatch switches:** [worker-dispatch.json](worker-dispatch.json).
- **Route models, effort, timeouts and diff size; Reviewer and Debug models:** [developer-routes.json](developer-routes.json).
- **Repair, Debug, child split, replan, test-fail, review-fail, Owner-notice and timeout-continuation caps:** [policy.json](policy.json).
- **Continuation chain length:** `MAX_CHAIN` in [lineage_counts.py](llmtool/lineage_counts.py).
- **EC2 module admission and modules per run:** `_task_path_modules` and `_MAX_REMOTE_MODULES` in [remote_test.py](llmtool/remote_test.py).
- **Spec text, acceptance count, edits and ladder tiers:** `check`, `REPAIR_ROOM_MAX_ACCEPTANCE`, `check_edits` and `LADDER_SIZES` in [spec_preflight.py](scripts/spec_preflight.py).

## Known cases

One prevention line each.

- **WP-5 (test module missing from read, EC2 refused):** list every test module to run on EC2 in the Task read paths before dispatch, because remote-test admits only modules declared in the Task paths ([#1943 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1943#issuecomment-6911682241)).
- **#2194 (spec refused custom families, 55 tests broke):** before a spec makes code refuse a kind of value, search the existing tests and callers that use it ([#2190 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/2190#issuecomment-6909409750)).
- **#2202 (comment-only gate forgeable):** never accept a gate that a GitHub comment alone can satisfy, since every agent posts as one login ([#2202](https://github.com/davidcatcat/davidcattoolsproject/issues/2202)).
- **Dispatch after failed preflight:** a `SPEC_PREFLIGHT_FAIL` or `SPEC_PREFLIGHT_INVALID` result means fix the spec and rerun preflight, never dispatch ([#1817 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6911866400)).
- **Wrong High Debug arguments:** take `task high-debug` arguments from `llmtool task high-debug --help` and the recorded failed-correction family, never from memory ([#1817 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6911866400)).
- **Status from intent:** report a state only from observed evidence (a start receipt, a verdict comment, a check conclusion), never from a plan or a sent command ([#1817 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6911866400)).
- **Drop before split (#1817):** create the replacement split child before dropping, because dropping the last open child of a split root ends the lineage `FAILED_FINAL` ([#1817 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6929490710)).
- **Start failure or no work (#2188):** a delegate that failed before it ran is excluded from the repair cap only when llmtool classifies it as a known pre-launch refusal or a proven no-work run; any other start failure still counts; check [#2188](https://github.com/davidcatcat/davidcattoolsproject/issues/2188) and [PR #2235](https://github.com/davidcatcat/davidcattoolsproject/pull/2235) before planning repairs.
- **Route switch after a Claude quota stop (#1817):** plan a fresh D6 and a new High Debug, since the moved repair loses the original binding ([#1817 comment](https://github.com/davidcatcat/davidcattoolsproject/issues/1817#issuecomment-6932763555)).
- **Reviewer choice while `claude-reviewer` is paused:** dispatch every formal review with `task review-dispatch --reviewer codex-reviewer`, never letting drive or advance pick ([PR #2223](https://github.com/davidcatcat/davidcattoolsproject/pull/2223)).
- **Read scope misses touched test modules:** grep the test tree first and list every test module that patches or asserts a symbol the change touches ([PR #2232](https://github.com/davidcatcat/davidcattoolsproject/pull/2232), [PR #2240](https://github.com/davidcatcat/davidcattoolsproject/pull/2240)).
