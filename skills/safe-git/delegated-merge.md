# Safe Git reference: delegated PR management and audit attribution

Normative reference for [safe-git](SKILL.md). Moved verbatim from `SKILL.md`
(main `cd7ee64`); delegation scope and limits are unchanged.

## Delegated PR management and audit attribution

The requirements owner has **delegated routine technical PR/merge decisions**
to the AI coordinator within accepted product goals; do not ask them to
act as PM, review each Low finding or hand-carry Codex/Claude prompts.
Follow [ai-team-delivery](../ai-team-delivery/SKILL.md). Delegation
does not waive independent gates, Mac validation, product/provider
permissions, or the AI-owned controller **pre-publication** review
and exact-revision release gate. Routine controller technical
acceptance is delegated to AI, not the owner. The standard Git
`run.sh` shortcut executes the control branch's tracked
`scripts/ai-loop.sh` and matching cached Python controller; any
control-tip update may affect the next Mac execution. Validate
immutable candidate bytes on a non-executable staging reference:
independent Security review where applicable, exact-candidate
helper CI and required Mac acceptance **before promoting the
same revision to the executable tip**. Record the executed commit
and privacy-safe outcome through actually observed evidence,
not by assuming a custom local logging mechanism exists.

After verifying all contract-required evidence, the coordinator can
merge an eligible exact-head PR without repeatedly asking "may I merge?".
Keep blocker/unfinished PRs Draft or Open, close genuinely obsolete PRs
with a reason, and retain immutable evidence. Do not merge a failing
controller helper just because Linux CI is green. Never treat the
Mac operator's ability to type a command as permission for that command
to run arbitrary new Git-fetched code.

In the audit trail record **technical disposition author, reviewer,
product scope confirmation, delegated merge authority, Git executor,**
and exact refs separately. Do not attribute the coordinator's detailed
risk analysis to the owner. Do not equate "delegated technical choice"
with new powers to export credentials/data, spend unapproved funds,
bypass branch protection or lower a required security gate.
