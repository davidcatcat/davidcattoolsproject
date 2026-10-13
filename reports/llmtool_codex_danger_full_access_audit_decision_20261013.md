# Codex Developer unrestricted-sandbox exception and approval-equivalent audit

Decision recorded: **2026-10-13**. Owner request: allow the **existing named Codex Developer CLI** to use `danger-full-access` because repeated approvals are disruptive, while retaining a Git-searchable record of actions that previously would have needed owner approval.

**Status: PROPOSED POLICY / NOT ACTIVATED.** This document and its Draft PR record the owner's authorization in principle. They are not evidence of a configured/audited runtime. Existing [safe-git C2](../skills/safe-git/SKILL.md) remains the effective execution guard until the separate native hook/test and independent security activation gates below pass. No one may treat these words as license to ignore a current C2 STOP.

## Exact scope and limits

- Applies only to the **original Codex Developer** assigned to `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`, including its verified existing session identity, task branch and independently accepted safe-git G7 C2-C4 preflight. It does **not** change the canonical integrator, Claude root, other assistants, Codex Cloud, ai-loop controller or any shell outside that task session.
- Requested Codex settings after activation: `sandbox_mode = "danger-full-access"`, `approval_policy = "never"`; these are **permission-profile choices, not a task authorization**. They remove an OS write/network sandbox and approval prompts, not merely their UI. This enlarges the blast radius if the agent makes a mistake or follows untrusted instructions.
- Still prohibited without their **own separate explicit scope and gate**: changes outside task file/path allowlist; force/reset/clean/rebase/stash/prune or unowned Git refs; canonical/peer/other worktree edits; credentials/SSH/keychain/secret stores; unreviewed package installs and arbitrary network/provider data egress; spending; production Host/ledger/F2 real-repository executor effects; root/`sudo`, system settings and system-wide services; background agents, new clones/worktrees and destructive OS operations. A blanket owner approval to reduce routine prompts does not waive these restrictions.
- Especially, **no** retrospective approval of T1 Helper execution, Issue #120 HOLD, Issue #66 content egress, product F2 mutation, unreviewed Mac program modifications or provider spending. Unexpected actions must STOP and be flagged, even if the operating system would allow them.
- Never publish private command arguments, prompts, environment variables, stdout/stderr, tokens, passwords, secret-bearing file paths or raw transcripts to GitHub.

## What can actually be recorded

With `approval_policy=never`, Codex generally does **not** generate `PermissionRequest` prompts that can be logged. Therefore a prior command's `would_have_required_owner_approval` classification is **a policy reconstruction, not an actual historical approval event or proof of owner consent**.

Implement **pre- and post-tool observation** for the installed Codex version, preferably using supported `PreToolUse`, `PostToolUse`, `SessionStart/End` hooks. Cover `Bash` / `exec_command`, `apply_patch`, and applicable local MCP actions; inventory tools not covered and do not claim 100% completeness. Record actual tool commands/arguments safely **locally**, and only independently sanitized summaries in the task's Git evidence. A `PermissionRequest` hook alone is insufficient under `never`.

Required event fields (use `null` / `UNKNOWN` rather than inventing facts):

| Field | Requirement |
|---|---|
| `schema_version`, UTC timestamp, ordered local sequence | distinguish sessions, permit gap detection |
| `session_id`, `turn_id`, `tool_use_id` | correlate pre/post event |
| Task Issue/PR, role, verified root, branch, pre/post HEAD | state and provenance |
| Tool class (`Bash`, `apply_patch`, MCP name), policy/rule ID | which action and why flagged |
| Safe command synopsis / argv **after redaction**, SHA-256 of original private event | useful searchable action without raw secret disclosure |
| `approval_equivalent` | `ROUTINE`, `WOULD_PROMPT_UNDER_OLD_PROFILE`, `SEPARATE_APPROVAL_REQUIRED`, or `UNKNOWN` |
| `authorization_basis` | owner profile decision vs task contract vs special separate grant; never `OWNER_APPROVED_THIS_COMMAND` without evidence |
| `pre_seen`, `post_seen`, status/exit/error/blocked/unknown | show actual execution vs proposed command; missing post-event = `OUTCOME_UNKNOWN` |
| `gap_detected`, tool coverage, reviewer, evidence link | show completeness limitations honestly |

Classification rules must be independently reviewed; do not assume every `Bash` call previously prompted. Examples of `WOULD_PROMPT_UNDER_OLD_PROFILE` candidates include writes outside workspace, sandbox escape/network access, protected Git metadata and permission-elevated shell commands **under the old baseline**. Commands forbidden by the task, credentials/real-provider egress, non-owned ref changes, root/system changes, destructive operations or unclear origin are `SEPARATE_APPROVAL_REQUIRED` / STOP, **not** silently approved merely by this exception. Mark any mapping uncertainty `UNKNOWN`.

## Where evidence lives

1. **Private raw observation:** one directory per run, 0700, files 0600, outside all Git repositories (e.g., under the current user's private app-state directory), no auto-upload. Record full event envelope only after secret-handling assessment. Guard against symlinks and concurrent writers; preserve errors/gaps. Local same-UID files and self-reported hooks are **not tamper-proof or an OS boundary**.
2. **Git searchable summary:** within the **authorized task branch/PR**, publish a sanitized file at `reports/codex-approval-audit/YYYYMMDD-issueNN-runID.md`, linking the exact reviewed task/commit/Issue. Include the fields above, safe command descriptions, rule/reason, outcome, counts by category, missing events and reviewer verdict. Use a verified safe publisher; no direct commit from an unreviewed `PreToolUse` hook, no auto-push from hooks.
3. **Review gate:** an independent reviewer checks summary against permitted local provenance, source/worktree state and known tool coverage before feature merge. Secrets/unsafe data stay local; when sanitization or completeness fails, publish only a **safe STOP summary** and mark audit `INCOMPLETE` (never fabricate a blank clean log).

Example **invented format only, NOT execution evidence**:

| Tool / redacted command | Old-profile classification | Actual outcome |
|---|---|---|
| `Bash: git status --short` | ROUTINE | example: exit 0 |
| `Bash: network operation [destination redacted]` | WOULD_PROMPT_UNDER_OLD_PROFILE / REVIEW | example: NOT EXECUTED |
| `Bash: sudo [arguments redacted]` | SEPARATE_APPROVAL_REQUIRED | example: BLOCKED |

## Activation / test acceptance

**No live full-access switch from this document alone.** Before anyone restarts the Codex Developer task in danger-full-access:

1. Confirm current Codex CLI supports the proposed hooks/events on this Mac version; install/configure hooks only within a separately reviewed narrowly scoped change, not untrusted global auto-start or new daemon. Inspect Codex configuration precedence and report actual effective permission mode. If coverage is incomplete, enumerate alternatives or keep `workspace-write`.
2. Independently Security-review hook source, event redaction, local-file access controls, deny/STOP behavior and available counterexamples. **A hook failure/timeout may fail open; do not claim hooks enforce sandbox isolation.** Activation must explicitly accept this residual risk or use a separately reviewed external process/OS-level enforcement layer.
3. Mac-local **synthetic-only** tests: routine read, previously prompted candidate (no risky side effects), denied privilege/destructive action, apply_patch, supported MCP, failed/timeout/interrupt, missing pre/post, redaction of canary token and source/hash/branch mismatch. Prove Git summaries have no secret-like output and are traceable to the correct task, or STOP.
4. Exact source SHA, independent Security+Functional review and verified task-bound activation record in this document's PR/Issue. Mark effective profile, audit version/hash, limits, explicit remaining gaps and rollback. If this is not accepted, `workspace-write` is still required and Issue #66 C2 STOP remains correct.
5. Only after activation, open/resume the **same verified Developer session in its assigned root** with the explicitly accepted profile. Require audited STOP if the task cannot prove its root, own branch/baseline, pre/post command records or non-conflicting writer. Do not attempt to adjust a running session by simply changing text in Git.

### Rollback

Revert the session to `workspace-write` (and the prescribed approval mode) if audit hooks are unavailable, pre/post events are incomplete, secret redaction fails, any action crosses the task/permission boundary or the security reviewer rejects the exception. Do not delete recorded evidence. This permission-profile exception is revocable and must not become a universal default for other projects.

Related: [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66), [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120), [safe-git](../skills/safe-git/SKILL.md), [Codex Hooks docs](https://developers.openai.com/codex/hooks), [Codex configuration docs](https://developers.openai.com/codex/config-basic).
