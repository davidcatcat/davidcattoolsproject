# Owner escalation reference — authority matrix (2026-10-27)

This is the operational decision boundary for the Integrator/PM. [AGENTS.md](../../AGENTS.md) owns roles and immutable safety gates; [Owner outcome reporting §6](../../docs/governance/llmtool_owner_outcome_reporting_v1_1_20261019.md#6-decision-authority-and-escalation-boundary) owns material product/release disposition; [PM Continuation Contract](pm-continuation.md) owns next-step behavior. This file reconciles those sources rather than granting new authority.

## PM may decide without Owner interruption

| Within already-approved task, budget and data boundary | PM action |
| --- | --- |
| Ordinary defect, CI/test failure, Reviewer FAIL | Diagnose, bounded fix, relevant retest and exact-HEAD rereview. Do not weaken a gate. |
| Worker quota, login, VPN, availability or temporary tool failure | Consult current Owner dispatch policy and real availability. Retry safely within task budget, isolate affected Task, or reassign only to an already-authorized qualified route after D6 Writer release. Keep independent work moving. |
| Task splitting, review sequencing, compliant low-risk change, nonblocking LOW issue | Make and record the decision and next responsible Agent. |
| Reviewer route unavailable or pairing cannot be staffed | Under [Owner D6 (#846)](https://github.com/davidcatcat/davidcattoolsproject/issues/846), select any already-authorized, qualified formal Reviewer from a different model family. If none is qualified and authorized, mark only that review WAITING and ask the Owner to authorize an eligible pairing. Continue unrelated work; never use a same-family Reviewer or self-review. |
| Existing eligible reviewed PR | May merge only after all review, exact-head CI, runtime and scope checks are satisfied. |

## Owner must decide before the dependent action

| Reserved decision | Required handling |
| --- | --- |
| Incorrect or changed product requirement; material feature, architecture, scope, priority or hosting change; major unresolved release trade-off | Provide a short evidence-based decision brief and ask a concrete question. |
| New spend, billing/provider tier, paid fallback, export/egress beyond existing permission, credentials or dangerous permission, accepted material security risk | STOP **only the dependent action**; no response is not consent. |
| `sudo`, `su` or root | AI never uses these, including on EC2. Stop and report if a task needs them. |
| Accounts, passwords, credentials or keys | Ask for explicit Owner authorization before any change or use. |
| macOS core/system changes | Ask for explicit approval before execution, even if a technical fix would be convenient. |
| Formal GO / LIMITED GO / NO-GO and major deployment or release decision | Owner decides based on evidence; technical PASS alone is not release approval. |
| No qualified and authorized cross-family formal Reviewer is available | Ask the Owner to authorize an eligible pairing; keep the affected review WAITING until one is available. Under [Owner D6 (#846)](https://github.com/davidcatcat/davidcattoolsproject/issues/846), the PM selects among already-authorized qualified Reviewers from a different model family without seeking separate Owner authorization for each pairing. No same-family or self-review. |
| Assignment of `OWNER_ASSIGNMENT_ONLY` Workers | Require the specific Owner Task assignment and exact policy binding before dispatch; a chat suggestion or healthy provider alone does not authorize work. AGY is currently `ALLOWED` for PM-selected dispatch under its worker-dispatch entry. |

### macOS core/system boundary

Includes changes to `/System`, `/Library`, `/usr` except `/usr/local`, system LaunchDaemons, kernel extensions, TCC privacy/security permissions, system firewall/network settings, and system-wide software installs. User-level `~/Library/LaunchAgents`, repository files and `.git/` changes are not by themselves macOS-core changes. The machine owner may still need to perform a physical command where access is unavailable; a command request is not blanket approval for changing policy.

A remote controller or persistent background agent is a separate technical threat boundary. It requires source and permission review, exact-SHA CI, applicable native tests and fail-closed operation before installation; not every such change creates a new Owner-escalation category.

## Owner ask gate (Owner O1, #1943)

Every Owner decision request names exactly one reserved row from this table. The IDs map one to one onto the reserved decisions above and the Owner rules they cite; they add no reserved decision and remove none ([Owner O1, relayed](https://github.com/davidcatcat/davidcattoolsproject/issues/1943#issuecomment-6879415056)).

| ID | Reserved decision | Required evidence (metadata only, never a value) |
| --- | --- | --- |
| R-SCOPE | Changed or incorrect requirement; material feature, architecture, scope, priority or hosting change | The original requirement and the fact it conflicts with |
| R-RELEASE | Formal GO / LIMITED GO / NO-GO; major deployment, release decision or release trade-off | The release item and its evidence |
| R-SPEND | New spend, billing or provider tier, paid fallback | The action class |
| R-EGRESS | Export or egress beyond existing permission | The action class and data class |
| R-SECURITY | Dangerous permission or acceptance of a material security risk | The action class or refusal code |
| R-CRED | Accounts, passwords, credentials or keys | The action class |
| R-ROOT | A task that needs `sudo`, `su` or root | The action class |
| R-MACOS | macOS core/system change | The action class |
| R-REVIEWER | No qualified, authorized cross-family formal Reviewer | Each Reviewer checked and its state |
| R-DISPATCH | Assignment of a `PAUSED` or `OWNER_ASSIGNMENT_ONLY` Worker | The `dispatch-check` code `OWNER_PAUSED` or `OWNER_ASSIGNMENT_REQUIRED` |
| R-ACCEPT | Owner product acceptance | `MERGE_OWNER_ACCEPTANCE_MANUAL` with the PR and HEAD |
| R-BUDGET | More attempts after a lineage ends `FAILED_FINAL`, beyond a standing Owner rule | The lineage ID and its counts |
| R-STOP | Conflict with an Owner stop, pause or report-back command | The link to the relayed Owner command |
| R-OTHER | No row fits, but continuing looks unsafe or unauthorized | Why no row fits |

Each ask carries one structured record with these fields:

- `reserved_row`: one ID from the table.
- `blocked_action`: the single blocked action and its Task or PR. Only that action waits.
- `authority_check`: what was checked and the result: the `dispatch-check` code when dispatch is involved; the PM-may-decide row considered and why it does not apply; the standing Owner decisions checked; the refusal code and run or Task ID when a gate stopped the action.
- `evidence`: the evidence the row requires.
- `not_defect_not_harness`: why this is not a tool defect (that is a defect record under [defect triage](defect-triage.md)) and not a harness permission refusal (that is a `harness_permission` record, below).

`R-OTHER` is the safety valve: never skip a real safety concern because no row fits, and never pick a row just to ask. If `R-OTHER` exceeds 10% of asks, the rules are reviewed. Stopping a dangerous action never needs an Owner question first.
[NOT LIVE] (#1943): `pm_owner_marker.py ask` refusing an incomplete record, the monitor flag `UNGATED_OWNER_ASK` and the weekly cross-family audit. Until they are live, the PM applies this gate by hand.

## Standing PM cleanup (Owner O2, #1943)

The PM may do this routine cleanup without asking, and records each action:

- close a PR, Issue or Task that the PM or llmtool created and that is merged, superseded or abandoned and meets its closing conditions;
- cancel a CI run that a newer HEAD of the same PR or branch superseded and that is no longer needed;
- stop a session the PM created that is confirmed idle or stuck;
- retire a D6 through the lifecycle tool and clean AI work data that can be rebuilt.

Never: delete a branch with unique unmerged work; delete Owner-created work; change system services, runner registration, workflow enable or disable state or macOS system directories; work around a Claude Code harness permission refusal.

**`harness_permission` record.** When the harness refuses an action this grant allows, do not ask the Owner and do not work around it. Record it in one daily summary on the PM sweep Issue, beginning with `<!-- activity: type=pm_progress actor=PM target=PM status=HARNESS_PERMISSION -->`: action class, tool and time, no values.
Only the Owner changes harness permission settings; this grant lets no AI change them.

## Owner channel

The Owner never edits anything on GitHub (Owner 2026-11-02). Never ask the Owner to post, comment, label, approve, review, merge or edit on GitHub. Ask in the PM chat. Before asking, post a `waiting_owner` marker (`actor=PM target=Owner status=WAITING`) on the Issue/PR so the monitor shows the open decision. Record the answer as a PM-relayed comment that quotes the Owner's words, says it is relayed, and begins with `<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->`; this one comment also settles the monitor's open row. Never create an Owner-authored GitHub event, and never treat a PM summary as Owner words. An Owner assignment reference or authorization link in [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) or a Task points to that relayed record.

This applies to every Owner decision on every Issue/PR, not to one topic (#812):

- Before any Owner question appears in chat, the `waiting_owner` marker is on the relevant Issue/PR. A decision sheet never stays only in a local scratchpad.
- Every Owner answer, including a decision the Owner raises first in chat, is recorded by one comment that quotes the Owner and carries the `ANSWERED` marker, on each Issue/PR the answer touches.
- Every scheduled sweep lists all open Owner rows in the repository and either settles each one or confirms it is still pending.

Before asking, apply the [Owner ask gate](#owner-ask-gate-owner-o1-1943). Use `python3 tools/llmtool/scripts/pm_owner_marker.py ask N --question Q [--sheet FILE]` to post the waiting marker before asking in chat, `answer N --owner-words W --pm-reading R` to record the relayed answer, and `open-rows` for a read-only sweep of pending rows. The script takes the repository slug from the `origin` remote and uses `gh` for GitHub access.

## Decision record and continuation

For every reserved choice, PM presents the exact decision, alternatives, user impact, estimated cost/risk (or UNKNOWN), recommendation and HOLD-until-approved disposition. Record the Owner authorization on the Task/Issue; for dispatch switches, update [worker-dispatch.json](../../tools/llmtool/worker-dispatch.json) with the binding under its strict schema. Do not treat a comment, proposal or silence as implicit approval. Only the blocked dependency waits. Unrelated already-authorized work must continue under [PM Continuation](pm-continuation.md).

A Reviewer/Worker quota event by itself is **not** permission to buy tokens, silently select another formal Reviewer, expand egress or change dispatch switches. No new escalation classes for routine fees or exports are introduced: any action **outside existing authority** remains a STOP for that action.

Historical detailed wording remains in the immutable [source snapshot](https://github.com/davidcatcat/davidcattoolsproject/blob/d41600ff98568770fb918fdf4a3309dd144a0dfe/skills/ai-team-delivery/SKILL.md#p0--sole-owner-escalation-rule-owner-decision-2026-10-10).
