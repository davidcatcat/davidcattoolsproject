---
name: ai-loop-handoff
description: Coordinate llmtool development and recovery through Git-pinned ai-loop tasks until native llmtool A11b/A11c is independently accepted. Use for helper faults, CI failures, interrupted runs, local Mac handoff, dirty Git trees and Codex-Claude orchestration.
---

# AI-loop handoff — one Mac script, no prompt courier

## Mandatory user contract

Until the native llmtool self-development workflow passes A11b/A11c real-task acceptance, **ai-loop is the sole standard Mac operator interface**. The user only starts a reviewed, validated ai-loop script and makes material authorization/design decisions. **Never ask the user to copy/paste Codex prompts, Claude prompts, long ad hoc shell commands, or shuttle logs between agents.**

The coordinator writes and publishes the exact Git-pinned `automation/ai-loop/task.md` and `task.json`; they must describe the same authorized live task. Codex CLI implements within the allowlist. Claude CLI independently reviews read-only. The runner coordinates both, runs deterministic tests, executes bounded repairs, preserves evidence, creates a Draft PR and verifies exact-head CI. Security Review and Functional QA are distinct gates. Never auto-merge or auto-start the next task.

## Normal operation — only after readiness

Verify the latest Git control SHA, task, branch/base, remote PR/CI, actual canonical Mac status, dependencies and independent gates. Then the **single standard launcher** is:

`bash scripts/ai-loop.sh --check && bash scripts/ai-loop.sh`

The `--check` is a preflight, not a new model prompt. `--resume RUN_ID` may be used only for a specifically pinned and supported frozen recovery contract, never for an unrelated historical run. The canonical Mac repository is `/Users/davidsun/git/davidcataiporject/davidcattoolsproject`. Do not make an alternate clone/worktree.

## Priority 0: script or system failure means maintenance-first

On runner/launcher/CI errors, failed tests, macOS temp/symlink problems, process conflict, dirty tree or interruption:

1. Freeze exact STOP phase, branch, HEAD, allowed files, candidate/evidence digests and state; preserve all tracked/untracked user work, stashes and private logs. No `git clean`, forced reset, blanket kill, automatic stash or blind retry.
2. Determine whether this is a valid safety STOP or helper bug. A locally dirty `scripts/test_ai_loop.py` under live C1's product-only allowlist is a **correct STOP**; never weaken the guard to get through.
3. Coordinator publishes a **separate frozen helper-maintenance Markdown + machine contract**, not a modification of the live C1 `task.json` and not a recycled 76644 `--resume`. Maintenance mode must explicitly bind approved dirty paths and digests, exact Git head/branch/remote PR, exclusion of unrelated/staged modifications, exclusive writer lock, safe local snapshot, bounded model and retry budgets, and preserved logs.
4. Use a **reviewed, tested single maintenance script entrypoint** to adopt the frozen candidate, run deterministic tests first, invoke read-only Claude for a cumulative-diff review, have Codex implement narrowly if needed, re-test and verify Python 3.9/3.14 exact-head CI before any Draft PR publication. Re-entry uses immutable checkpoints and authorization; never auto-merge. Treat system-specific fixes as runner/bootstrap scope, not llmtool product scope.
5. **If the separate maintenance executable does not yet exist, STOP with `AUTOMATED_MAINTENANCE_NOT_READY`.** Coordinator must implement and validate that bootstrap through authorized Git/CI before asking the operator to execute a vetted script. A document or Git issue is not an executable. Do **not** fall back to manual Codex/Claude prompt relay.
6. For macOS helper synthetic tests, validate a real (not symlinked) temporary root such as `/private/var/tmp` without dropping existing symlink/path security checks. Distinguish local Python 3.13 test PASS from CI Python 3.9/3.14 success. Stop safely on insufficient identity, evidence, permissions or authorization.

## Current gate / cost / future cutover (2026-10-09)

- Issue #52: the Mac has an uncommitted `scripts/test_ai_loop.py` Codex patch; helper suite **reported 210/210 PASS** with a physical TMPDIR. Claude independent review, publication and helper CI 3.9/3.14 have **not** been established.
- Issue #53: a separate helper self-maintenance/adopt-dirty/resume script is **NOT IMPLEMENTED**. The active `task.md/task.json` still targets C1 Issue #51, which remains NOT STARTED. See `automation/ai-loop/MAINTENANCE_HANDOFF.md`.
- Spend zero model tokens on deterministic diagnostics where possible; send bounded logs/diffs to premium models and enforce budgets. Never trade away safety, independent review or exact-head checks to reduce cost.
- Cut over only when llmtool-native execution passes approved actions, real code/test/review/repair/recovery/CI, required Mac/Security/QA evidence, and measured tokens/cost/quality. Keep ai-loop a fallback until that acceptance; do not build a second permanent orchestration platform.

## Completion criteria

The user gets only a vetted named script invocation or a genuine design/authorization decision—not routine manual agent prompts. Required tests, reviews, CI and their precise tested revisions are recorded. A blocked or not-yet-implemented maintenance mode is reported truthfully.
