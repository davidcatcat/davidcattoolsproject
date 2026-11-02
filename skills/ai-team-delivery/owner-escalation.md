# Owner escalation reference — authority matrix (2026-10-27)

This is the operational decision boundary for the Integrator/PM. [AGENTS.md](../../AGENTS.md) owns roles and immutable safety gates; [Owner outcome reporting §6](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md#6-decision-authority-and-escalation-boundary) owns material product/release disposition; [PM Continuation Contract](pm-continuation.md) owns next-step behavior. This file reconciles those sources rather than granting new authority.

## PM may decide without Owner interruption

| Within already-approved task, budget and data boundary | PM action |
| --- | --- |
| Ordinary defect, CI/test failure, Reviewer FAIL | Diagnose, bounded fix, relevant retest and exact-HEAD rereview. Do not weaken a gate. |
| Worker quota, login, VPN, availability or temporary tool failure | Consult current Owner dispatch policy and real availability. Retry safely within task budget, isolate affected Task, or reassign only to an already-authorized qualified route after D6 Writer release. Keep independent work moving. |
| Task splitting, review sequencing, compliant low-risk change, nonblocking LOW issue | Make and record the decision and next responsible Agent. |
| Reviewer quota exhausted | Preserve cross-family and independent-review requirements; mark only that formal-review gate WAITING, continue unrelated work. Do not substitute a Reviewer, self-approve or merge while required PASS is missing. |
| Existing eligible reviewed PR | May merge only after all review, exact-head CI, runtime and scope checks are satisfied. |

## Owner must decide before the dependent action

| Reserved decision | Required handling |
| --- | --- |
| Incorrect or changed product requirement; material feature, architecture, scope, priority or hosting change; major unresolved release trade-off | Provide a short evidence-based decision brief and ask a concrete question. |
| New spend, billing/provider tier, paid fallback, export/egress beyond existing permission, credentials or dangerous permission, accepted material security risk | STOP **only the dependent action**; no response is not consent. |
| Root/sudo or macOS core/system changes | Ask for explicit approval before execution, even if a technical fix would be convenient. |
| Formal GO / LIMITED GO / NO-GO and major deployment or release decision | Owner decides based on evidence; technical PASS alone is not release approval. |
| Change of formal Reviewer pairing due to quota or other unavailability | Owner authorization is required for the particular eligible cross-family pairing. No same-family or self-review; absence of a qualified Reviewer leaves the affected review waiting. |
| Assignment of `OWNER_ASSIGNMENT_ONLY` Workers | Require the specific Owner Task assignment and exact policy binding before dispatch; a chat suggestion or healthy provider alone does not authorize work. AGY is currently `ALLOWED` for PM-selected dispatch under its worker-dispatch entry. |

### macOS core/system boundary

Includes changes to `/System`, `/Library`, `/usr` except `/usr/local`, system LaunchDaemons, kernel extensions, TCC privacy/security permissions, system firewall/network settings, and system-wide software installs. User-level `~/Library/LaunchAgents`, repository files and `.git/` changes are not by themselves macOS-core changes. The machine owner may still need to perform a physical command where access is unavailable; a command request is not blanket approval for changing policy.

A remote controller or persistent background agent is a separate technical threat boundary. It requires source and permission review, exact-SHA CI, applicable native tests and fail-closed operation before installation; not every such change creates a new Owner-escalation category.

## Owner channel

The Owner never edits anything on GitHub (Owner 2026-11-02). Never ask the Owner to post, comment, label, approve, review, merge or edit on GitHub. Ask in the PM chat. Before asking, post a `waiting_owner` marker (`actor=PM target=Owner status=WAITING`) on the Issue/PR so the monitor shows the open decision. Record the answer as a PM-relayed comment (`actor=PM`) that quotes the Owner's words and says it is relayed. Never create an Owner-authored GitHub event, and never treat a PM summary as Owner words. An Owner assignment reference or authorization link in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) or a Task points to that relayed record.

## Decision record and continuation

For every reserved choice, PM presents the exact decision, alternatives, user impact, estimated cost/risk (or UNKNOWN), recommendation and HOLD-until-approved disposition. Record the Owner authorization on the Task/Issue; for dispatch switches, update [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) with the binding under its strict schema. Do not treat a comment, proposal or silence as implicit approval. Only the blocked dependency waits. Unrelated already-authorized work must continue under [PM Continuation](pm-continuation.md).

A Reviewer/Worker quota event by itself is **not** permission to buy tokens, silently select another formal Reviewer, expand egress or change dispatch switches. No new escalation classes for routine fees or exports are introduced: any action **outside existing authority** remains a STOP for that action.

Historical detailed wording remains in the immutable [source snapshot](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#p0--sole-owner-escalation-rule-owner-decision-2026-10-10).
