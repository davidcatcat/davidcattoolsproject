# Owner escalation reference

Current escalation authority is limited to a wrong product requirement, root/sudo, or a macOS core/system change. Ask a short yes/no question only when one applies; ordinary implementation and risk decisions remain with the authorized AI team.

## macOS core/system changes

This includes changes to `/System`, `/Library`, `/usr` except `/usr/local`, system LaunchDaemons, kernel extensions, TCC privacy/security permissions, system firewall/network settings, and system-wide software installs. User-level `~/Library/LaunchAgents`, repository files and `.git/` changes are not by themselves macOS-core changes.

A remote controller or persistent background agent is a separate technical threat boundary. It requires source and permission review, exact-SHA CI, applicable native tests and fail-closed operation before installation; this is not by itself a new Owner-escalation category.

## Scope limits

Do not expand escalation to unconfirmed proposals such as new fees/subscriptions or data exports that remain within an already authorized route and data class. Spending or provider export outside current authority is STOP under [AGENTS.md](../../AGENTS.md). Keep product acceptance distinct from technical review, task admission, and Git publication.

Historical detailed wording remains in the immutable [source snapshot](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#p0--sole-owner-escalation-rule-owner-decision-2026-10-10); this reference states the current scope under D8.
