# 2026-11-02 llmtool governance and AI-team decisions

- **Participants:** the Owner and the ChatGPT Coordinator. The decisions were relayed to the Claude PM (claude2 session `8a809544`) in the PM chat on 2026-11-02.
- **Context:** the governance audit in [#785](https://github.com/davidcatcat/davidcattoolsproject/issues/785) (60 findings, decisions D1–D7) and the PM's parallel-work question (D8).
- **Status:** this file is the PM's written relay of the Owner's decisions. It is not an Owner-signed GitHub event, and it creates no authorization beyond what the Owner stated. Implementation happens in separate Tasks and PRs, each with its own review, CI and evidence.

## D1: unified AI role and account configuration

- Claude currently defaults to the `claude2` account. `claude2` is a default, not a mandatory account.
- The Owner may name another account at any time with an explicit command.
- The Owner judges token quota by hand. Automatic account rotation is not authorized.
- Adopt central configuration **option B**:
  - One user-level config file manages AI roles, providers, models, CLI accounts and per-role defaults.
  - Every program reads it through one shared interface; no program hard-codes its own default.
  - Prefer extending the existing `~/.config/llmtool/config.yaml`; do not add a second config system.
- An explicit one-shot command flag or environment variable may override a value. It must not silently change the stored default.
- The Developer proposes the minimal compatible design: config fields, override precedence and how an account's identity is verified.
- Owner dispatch authority and the safety rules in `worker-dispatch.json` stay separate. Switching accounts must not bypass them.
- The design must also work for future A13 multi-user installs: no hard-coded Owner Mac paths or personal accounts.

## D2: management hierarchy

Owner/User → ChatGPT Coordinator (technical adviser and technical director) → Claude Integrator/PM → sub-agent PMs, Developers and Reviewers.

- **Coordinator:** technical direction, architecture review, technical management advice, and helping the Owner decide.
- **Claude PM:** day-to-day planning, dispatch, acceptance, tracking and integration.
- AGENTS.md, CLAUDE.md, the team roster and related governance documents must be corrected so they stop using the outdated Coordinator/PM duty descriptions.

## D3: no fixed weekly report

- The Owner does not need a fixed weekly report.
- Kept: progress on request, notices of major events, necessary Owner decisions, and internal execution records.
- Outdated fixed weekly-report requirements are removed from the governance documents.

## D4: ai-loop becomes a standalone tool

- ai-loop is currently disabled, but the work already invested in it must be kept.
- Target: a standalone tool at `tools/ai-loop/`.
- Steps:
  1. Check existing plans first, to avoid duplicate Issues.
  2. Inventory the code, tests, configuration, launchers and dependencies.
  3. Create the standalone tool directory.
  4. Do the necessary standalone launch and functional verification.
  5. Remove ai-loop code, flows and launch dependencies from llmtool.
  6. Clean up outdated rules and documents.
  7. Keep the historical evidence that is still needed.
- Order: build, verify, then remove. Moving text to `docs/history/` does not count as making it a standalone tool.
- ai-loop does not become llmtool's main controller again.

## D5: Codex Cloud

- The existing Codex Cloud independent development route stays for now.
- Do not remove its valid operating instructions as part of document cleanup.

## D6: dynamic Reviewer selection

The Owner authorizes the Claude PM to choose a qualified, independent formal Reviewer itself. No permanent one-to-one Developer→Reviewer pairing is required.

Required conditions:
- a different model family from the Developer;
- allowed by the current Worker dispatch authorization;
- able to do a formal review of that kind of work;
- an independent review environment;
- complete evidence of the Reviewer, Task, PR, HEAD and acceptance;
- no relaxation of the existing security and CI requirements.

If the usual Reviewer is unavailable, the PM finds another qualified, authorized Reviewer on its own. Only if none is available does the PM ask the Owner.

Check `review_dispatch.py`, `review_result.py` and `task_completion.py`. The AGY Reviewer setting and the code's behavior currently disagree. Create the code work packages needed so dynamic Reviewer selection can complete a formal acceptance. Until that code is accepted, the docs must not claim support.

## D7: old AGY owner assignments

- First investigate the four old AGY `owner_assignments` in `worker-dispatch.json`: each Task's status, dependencies and whether it is still needed.
- Do not delete them before that is confirmed.
- Report the findings and a recommendation back to the Owner for a decision.

## D8: parallel work and PM sub-agents

- A11c/WP3 and the #785 governance fixes proceed in parallel.
- The Claude PM uses sub-agent PMs proactively, without the Owner having to remind it.
- Two modes:
  - **A, analysis sub-agent PM:** investigation, analysis, work breakdown, planning and reports.
  - **B, execution sub-agent PM:** inside an already authorized, explicit work package, it arranges Developers, Reviewers, tests and repairs itself, then returns the result to the main PM.
- The main PM decides between A and B.
- No two PMs may dispatch the same work or manage the same Task, and no two may edit conflicting files at the same time.
- The main PM keeps overall management, integration and delivery responsibility.

## Execution rules restated by the Owner

- Investigate existing features first, then split work packages. Do not open many document PRs at once.
- A11c/WP3 stays the top priority. The #763 sweep rules (scheduled sweeps, item-by-item checks, dispatch records, no unjustified zero dispatch) stay in force.
- All code changes follow Owner dispatch authority, the Task Contract, D6 single writer, EC2 remote-test, cross-family independent formal review, exact-head CI, Mac native acceptance where required, and the security and egress limits.
- Document PRs use the docs CI and a formal review, not a full Mac CI run.
- A document fix, a test PASS, a Reviewer PASS, a deployment and Owner acceptance are different things. Each needs its own evidence.
