# Codex Developer unrestricted-sandbox exception and approval-equivalent audit

Decision recorded: **2026-10-13**. Owner request: allow the **existing named Codex Developer CLI** to use `danger-full-access` because repeated approvals are disruptive, while retaining a Git-searchable record of actions that previously would have needed owner approval.

**Status (corrected 2026-10-14 per [owner direction](https://github.com/davidcatcat/davidcattoolsproject/issues/135#issuecomment-6152425440)):**
- `OWNER_AUTHORIZED_FULL_ACCESS`. The permission mode is the owner's decision. AI review and coordination may find, fix and disclose risks, but may not veto, revoke, defer or ask for re-authorization of that choice.
- `EFFECTIVE_MAC_MODE_NOT_VERIFIED`. This document does not configure or prove any local session's actual mode.
- `NATIVE_AUDIT_NOT_VERIFIED`. The observe-only collector from PR #136 is merged, but native hook coverage, deny and fail-open behaviour are unproven. Hooks are cooperative and are not an OS sandbox.

[safe-git C2](../skills/safe-git/SKILL.md) keeps every non-permission guard: identity, root, task scope, ownership, preflight, Git, secret and egress limits. The full-access mode alone is not a C2 STOP reason.

## Exact scope and limits

- Applies only to the **original Codex Developer** assigned to `/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject/codex`, including its verified existing session identity, task branch and independently accepted safe-git G7 C2-C4 preflight. It does **not** change the canonical integrator, Claude root, other assistants, Codex Cloud, ai-loop controller or any shell outside that task session.
- Owner-authorized Codex settings: `sandbox_mode = "danger-full-access"`, `approval_policy = "never"`; these are **permission-profile choices, not a task authorization**. They remove an OS write/network sandbox and approval prompts, not merely their UI. This enlarges the blast radius if the agent makes a mistake or follows untrusted instructions.
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

## Verification and hardening (evidence, not an AI permission gate)

These steps establish what is actually true and make the owner-authorized mode as safe as practicable. Their results are reported as evidence. They do not grant, withhold or revoke the owner's permission choice. An incomplete step stays labelled `NOT_VERIFIED` or `AUDIT_INCOMPLETE`; it is never reported as verified.

1. **Effective mode:** inspect the named session's actual Codex flags and configuration precedence on the Mac. Report `EFFECTIVE_MAC_MODE` truthfully. Changing another agent's session configuration requires that session's own explicit direction.
2. **Hooks:** confirm that the installed Codex CLI supports the hooks/events. Install or configure hooks only through a separately reviewed, narrowly scoped change: no untrusted global auto-start and no new daemon. Report coverage gaps and options to the owner.
3. **Security review:** independently review hook source, event redaction, local-file access controls, deny/STOP behaviour and available counterexamples. **A hook failure/timeout may fail open; do not claim hooks enforce sandbox isolation.** Disclose this residual risk to the owner.
4. **Mac-local synthetic-only tests:**
   - routine read;
   - previously prompted candidate (no risky side effects);
   - denied privilege/destructive action;
   - apply_patch;
   - supported MCP;
   - failed/timeout/interrupt;
   - missing pre/post;
   - redaction of a canary token;
   - source/hash/branch mismatch.

   Git summaries must contain no secret-like output and be traceable to the correct task; otherwise publish only a safe STOP summary for that audit.
5. **Record:** the exact source SHA, independent Security+Functional review of audit code, effective profile, audit version/hash, limits, remaining gaps and rollback options, all in this document's PR/Issue.
6. **Session:** use only the **same verified Developer session in its assigned root**. Safe-git C2/C3 identity, root, own branch/baseline and non-conflicting-writer checks still apply; failing them is a STOP for that task, independent of the permission mode. Changing text in Git does not adjust a running session.

### Rollback

Reverting the session to `workspace-write` (and the prescribed approval mode) is the owner's decision. Reviewers should promptly recommend rollback, with evidence, if:
- audit hooks are unavailable;
- pre/post events are incomplete;
- secret redaction fails;
- an action crosses the task/permission boundary.

A task-level STOP for a concrete boundary violation still applies immediately. Do not delete recorded evidence. This permission-profile exception is revocable by the owner and must not become a universal default for other projects.

Related: [Issue #66](https://github.com/davidcatcat/davidcattoolsproject/issues/66), [Issue #120](https://github.com/davidcatcat/davidcattoolsproject/issues/120), [safe-git](../skills/safe-git/SKILL.md), [Codex Hooks docs](https://developers.openai.com/codex/hooks), [Codex configuration docs](https://developers.openai.com/codex/config-basic).
