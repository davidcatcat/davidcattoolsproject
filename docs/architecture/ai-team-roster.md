# AI team roster and execution resources

Status: Owner-approved **role / resource inventory**, 2026-10-27. This is not live attendance, provider availability, billing authority or proof of completed integration.

## Management hierarchy (Owner decision D2, 2026-11-02)

Owner → ChatGPT Coordinator (technical adviser and technical director) → Claude PM → sub-agent PMs, Developers and Reviewers. The Coordinator provides technical direction, architecture review and advice to help the Owner decide. The Claude PM owns day-to-day planning, dispatch, acceptance, tracking and integration.

## Office roles: who can do what

| Role | Worker / actor | Authority source | Operation / limit |
| --- | --- | --- | --- |
| Owner | Human product owner | [Owner authority matrix](../../skills/ai-team-delivery/owner-escalation.md) | Product, priority, material cost/egress/risk, selected Developer tasks and Reviewer-pairing exceptions. |
| Integrator / PM | Claude PM (role, not a dispatch-map Worker key) | [AGENTS.md](../../AGENTS.md) | Plan, orchestrate, continue/recover, report, integrate; cannot bypass review or approve its own change. |
| Developer | `codex-developer` | [Worker dispatch](../../tools/llmtool/worker-dispatch.json) | `ALLOWED` in the dispatch file, but per the Owner on 2026-11-06 ([#289](https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6685246395)) it gets no new Developer work for now; it still runs bounded High Debug diagnostic Tasks. |
| Developer | `opencode-developer` | [Worker dispatch](../../tools/llmtool/worker-dispatch.json) | **ALLOWED** (Owner 2026-11-02); PM-selected direct route, free model only; registered D6, task-local permissions and independent review required; no paid fallback. |
| Developer | `agy` | [Worker dispatch](../../tools/llmtool/worker-dispatch.json) | **ALLOWED** (Owner 2026-10-31); PM-selected direct route, no individual Owner assignment or Host D4/G3/M2; verified D6 and auth/model/session/permission/quota checks, independent cross-family review and no automatic retry or paid fallback. |
| Developer | `claude-developer` | [Worker dispatch](../../tools/llmtool/worker-dispatch.json) | **ALLOWED** (Owner 2026-11-06, [#289](https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6685246395)); PM-selected Developer work; formal Reviewer is `codex-reviewer`. |
| Developer agent route (home LLM) | `company-gw-agent` (`gw-agent`) | Worker dispatch + [company delegation](../../skills/company-llm-delegation/SKILL.md) | Only through the API Agent Gateway; line-range reads and Task write paths; no shell or Git; the `company-gw` direct/proposal route is retired (Owner 2026-11-11, #120). |
| Formal reviewer | `claude-reviewer` | Worker dispatch + [formal verdict](../../skills/ai-team-delivery/formal-verdict.md) | Separate read-only D6 reviewer; PM selects when qualified, authorized and cross-family for the Task. |
| Formal reviewer | `codex-reviewer` | Worker dispatch | Separate read-only D6 reviewer; PM may select for any task when qualified and cross-family. |

An `ALLOWED` dispatch switch is authorization, **not** evidence that a Worker is reachable or qualified for the Task. `OWNER_ASSIGNMENT_ONLY` requires an exact authorized Task entry. Developer self-review and advisory pre-review never become formal acceptance merely by changing a name.

## Execution hosts and CI runners

Machine-readable **informational** inventory: [execution-resources.json](../../tools/llmtool/execution-resources.json). It is deliberately separate from strict v1 `worker-dispatch.json`. A CI runner is not an AI Worker; an execution host is not an approved SSH Developer.

| Host | Reported hardware / OS | Registered runners (name) | Existing purpose |
| --- | --- | --- | --- |
| Local Mac | Intel macOS, limited 8 GiB memory | None; self-hosted CI runners retired ([#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253)) | Mac CI now runs on GitHub-hosted `macos-15-intel`; the Mac still gives native Mac runtime evidence. |
| AWS EC2 | CentOS Stream 9, x86_64, 4 vCPU / 8 GiB | None; self-hosted CI runners retired ([#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253)) | Linux CI now runs on GitHub-hosted `ubuntu-24.04`; EC2 serves `llmtool task remote-test`. |

See [execution-resources.json](../../tools/llmtool/execution-resources.json) for the current host and runner inventory; its runner entries describe the retired self-hosted setup. Runner names, installation accounts and platform capabilities reflect committed documentation, not a live online/busy probe. [Mac runner guide](../../tools/mac-runner/README.md) and [EC2 runner guide](../../tools/ec2-runner/README.md) contain isolation and admission requirements.

## Routing and operational boundaries

- PM picks an authorized model/Worker for a bounded Task, consults live technical availability and uses the Python dispatch gate at every handoff. The gate returns a compact permit/deny reason and policy digest; do not paste the entire JSON to an LLM every time.
- PM may use a direct CLI D6 route for OpenCode or AGY under their current worker-dispatch entries without waiting on Host D4/G3/M2. Both still require independent review, exact-head CI and actual per-provider execution receipts.
- When free quotas expire, record confirmed progress and unfinished steps. Protect uncommitted changes and single Writer lease; do not replay an UNKNOWN mutation. PM continues unrelated tasks and uses only authorized qualified fallback routes.
- EC2 no longer runs CI ([#1253](https://github.com/davidcatcat/davidcattoolsproject/issues/1253)); it serves `llmtool task remote-test`, a PM pre-review tool on private infrastructure, **not** blanket remote-SSH code execution authorization. General SSH execution, dirty-worktree transfer and live Token savings require separate design, source screening and evidence; [EC2 work inventory](../ci/ec2-deterministic-work-inventory-20261027.md).
- One GitHub login may post Developer and Reviewer records; a login alone is **not** independent review evidence. Require distinct read-only Reviewer Session, D6 record, exact-SHA verdict and merge gate. A second personal GitHub account is **not required** by current process; GitHub App integration is optional future work, not approved implementation.

## Where authority lives

`AGENTS.md` → permanent role and safety boundaries; `worker-dispatch.json` → Owner AI Worker permission; local monitor → technical availability; `execution-resources.json` → informational static host/CI roster; PM Continuation → Task lifecycle; formal verdict and CI → independent merge gates. A status dashboard should consume these separate sources without treating static inventory as live truth.
