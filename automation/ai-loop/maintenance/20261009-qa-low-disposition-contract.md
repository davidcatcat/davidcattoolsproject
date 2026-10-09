# MAINTENANCE TASK (NOT EXECUTABLE YET): PR #55 six Low finding disposition
Date: 2026-10-09
Status: PENDING IMPLEMENTATION AND HELPER CI. This document is a Git-pinned task contract, NOT evidence of an implemented runner action or authorization to invoke the existing default launcher.

## Authority and immutable references
- Source Issue: https://github.com/davidcatcat/davidcattoolsproject/issues/56#issuecomment-6074934613
- Product PR: https://github.com/davidcatcat/davidcattoolsproject/pull/55
- Expected PR HEAD: ee8180bc3ad5c455b63e493fee9f2955a90c5463
- Expected base HEAD: 8e29024656f873431f6ad83e45015b57bfe381a9
- Existing completed QA run on Mac: 20261009T050558Z-60868
- Cumulative diff SHA256: e65bf139f62439be7803981b855ca96add17778eb2daff4c9fb329b69b0c1311
- Frozen QA: Security PASS with 2 Low, Functional QA PASS with 4 Low. The six detailed findings are MAC-PRIVATE.
- Source product CI: GitHub Actions run 37885712383, Python 3.9 and 3.14 success (1297 tests each, 1 skip each).

## Git control + operator contract
Read AGENTS.md, skills/ai-loop-handoff/SKILL.md, automation/ai-loop/README.md, scripts/ai_loop.py, tools/llmtool/WORKFLOW.md, and task.md/task.json before changing anything. Use the canonical Mac checkout only. No clone, no worktree, no /tmp checkout. Inspect clean index/worktree and all current remote SHA identities before mutation.

This is CONTROL-RUNNER maintenance, not the current product QA task. Preserve the existing automation/ai-loop branch and its live task.md/task.json. Do not overwrite or run the old PR #55 post-JWT QA task, do not recycle --maintenance-issue52, and do not reclassify historical Codex FAIL or original security findings. No automatic merge or provider egress.

## Goal
Implement a narrow standalone fail-closed ai-loop maintenance path (working name: qa_low_disposition), integrated with the trusted .git/ai-loop/run.sh launcher after its own tests and exact-head helper CI. It must:
1. Bind expected product PR/head/base, QA run ID, original immutable local QA role/provenance records and hashes, original 2+4 counts, diff SHA, exact PR changed file inventory and prior successful CI. Reject wrong branch/HEAD, missing role payloads, changed QA bytes, symlink/path escape, dirty unexpected source, wrong count, finding ambiguity and mismatched signed status.
2. Extract six original finding identities, severities, blocking flags and allowed source locations without exposing raw reviewer texts. For each, bounded independent local read-only assessment must produce stable identity/hash and one classification: ACCEPT_WITH_RATIONALE, DEFER_FOLLOW_UP with owner/condition, or FIX_BEFORE_MERGE. Private assessment must retain rationale and source evidence. Findings with missing/ambiguous rationale, Medium+, blocking or missing evidence -> STOP, not PASS.
3. Public Issue #56 comment may include only fixed-schema role, finding SHA/ID, allowed source path/line, fixed disposition enum/count and immutable provenance. Never publish raw findings, traces, credentials, model envelopes, local private paths or source excerpts.
4. Be idempotent, bounded and no replay of paid review if a complete source-bound private result already exists. No Codex writer to PR #55, no PR edits/transition, no forced Git mutations or automatic merge. Fail closed if current product HEAD differs.
5. Include positive/negative regression tests for tampering, privacy redaction, stale HEAD, missing evidence, wrong finding number, role substitution, blocked finding, model protocol failure, Git state changes, repeated invocation and non-publication on STOP. Run exact-control-head helper CI Python 3.9 and 3.14.
6. Once CI is green, the coordinator approves one specific launcher command; until then do NOT tell the Mac operator to run the existing default launcher.

## Delivery
The implementation may change only separate controller maintenance implementation, tests, approved launcher/help/docs, and task history. Product PR #55 stays unchanged. Publish controller commit and test evidence. Independent read-only Claude review and CI must pass before any Mac operator invocation. The coordinator then inspects six sanitized outcomes and separately decides signoff. Only a subsequent independent merge authorization may merge PR #55.

## Current status
- Task contract published to a separate maintenance branch.
- Runner implementation: NOT STARTED.
- Tests/CI for this new feature: NOT RUN.
- No command has yet been verified safe to execute on Mac.
